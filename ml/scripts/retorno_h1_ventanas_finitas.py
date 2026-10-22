"""Experimento H1 separado: --prepare (SELECT) y --evaluate (solo archivos).

AlumnoID se usa solo en memoria; no se guarda ni como pseudónimo/hash por alumno.
Ventanas estrictas [FechaRef-W, FechaRef); H1 es el siguiente día operativo.
"""
import argparse
import ast
import json
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import ExtraTreesClassifier, GradientBoostingClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score, brier_score_loss,
    confusion_matrix, f1_score, log_loss, precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml.scripts.evaluacion_afluencia_reproducible import ROOT, calendar, historical_coverage, model_hashes, sha, write_csv, write_json

START, SOURCE_START, CUT = date(2026,6,1), date(2026,5,2), date(2026,10,21)
OUT = ROOT / "ml/experiments/retorno_h1_ventanas_finitas_20261021"
FEATURES = [f"{kind}_{w}d" for w in (7,14,30) for kind in ("dias_activos","visitas","tasa_asistencia")] + [
    "recencia_30d", "afinidad_dia_objetivo_30d", "dias_apertura_30d"]
SQL = """SELECT AlumnoID, CAST(FechaHora AS DATE) AS Fecha, COUNT_BIG(*) AS Visitas
FROM dbo.RegistroIngresos
WHERE FechaHora >= ? AND FechaHora < ? AND FechaHora <= ? AND Sede = ?
 AND AlumnoID IS NOT NULL AND ISNULL(EsPrueba, 0) = 0
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)
GROUP BY AlumnoID, CAST(FechaHora AS DATE)
ORDER BY Fecha, AlumnoID"""


def coverage():
    confirmed = historical_coverage(pd.read_csv(ROOT/"ml/data/resumen_cobertura_por_fecha.csv"))
    confirmed.update(d.date().isoformat() for d in pd.date_range("2026-09-29",CUT))
    return {date.fromisoformat(d) for d in confirmed}


def status(day, confirmed):
    state = calendar(day)
    if state == "atencion_segun_calendario" and day not in confirmed:
        return "desconocido_captura"
    return state


def target_h1(ref, confirmed):
    day = ref + timedelta(days=1)
    while day <= CUT:
        state = status(day,confirmed)
        if state.startswith("desconocido"):
            return None,"objetivo_no_confirmable"
        if state == "atencion_segun_calendario":
            return day,None
        day += timedelta(days=1)
    return None,"etiqueta_no_madura"


def window_days(ref, width, confirmed):
    dates = [ref-timedelta(days=i) for i in range(width,0,-1)]
    if dates[0] < SOURCE_START or any(status(d,confirmed).startswith("desconocido") for d in dates):
        return None
    return [d for d in dates if status(d,confirmed)=="atencion_segun_calendario"]


def feature_row(visits, ref, target, confirmed):
    """visits: diccionario fecha->escaneos, ya filtrado; nunca lee ref ni target."""
    windows = {w:window_days(ref,w,confirmed) for w in (7,14,30)}
    if any(v is None or len(v)==0 for v in windows.values()):
        return None
    recent = [d for d in windows[30] if visits.get(d,0)>0]
    if len(recent)<3:
        return None
    row={}
    for width,days in windows.items():
        active=sum(visits.get(d,0)>0 for d in days)
        row[f"dias_activos_{width}d"]=active
        row[f"visitas_{width}d"]=sum(visits.get(d,0) for d in days)
        row[f"tasa_asistencia_{width}d"]=active/len(days) if days else 0.0
    row.update({"recencia_30d":(ref-max(recent)).days,
        "afinidad_dia_objetivo_30d":sum(d.weekday()==target.weekday() for d in recent)/len(recent),
        "dias_apertura_30d":len(windows[30])})
    return row


def fetch(asof):
    from db import get_db_connection
    conn=get_db_connection()
    if conn is None:
        raise RuntimeError("No se pudo abrir la BD configurada")
    cursor=None
    try:
        cursor=conn.cursor()
        cursor.execute(SQL,(SOURCE_START.isoformat(),(CUT+timedelta(days=1)).isoformat(),asof.replace(tzinfo=None),"Central"))
        return pd.DataFrame([(r[0],str(r[1])[:10],int(r[2])) for r in cursor.fetchall()],columns=["AlumnoID","Fecha","Visitas"])
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            conn.close()


def part_for(ref,target):
    if START<=ref<=date(2026,7,31) and target<date(2026,9,1):
        return "train"
    if date(2026,9,1)<=ref<=date(2026,9,29) and target<date(2026,10,1):
        return "val"
    if date(2026,10,1)<=ref<=date(2026,10,20) and target<=CUT:
        return "octubre_retrospectivo"
    return "solo_ajuste_final"


def build(raw,confirmed):
    if raw.empty or raw.Fecha.max()>CUT.isoformat() or raw.Fecha.min()<SOURCE_START.isoformat():
        raise ValueError("Fuente vacía o fuera del corte")
    if not raw.Fecha.eq(CUT.isoformat()).any():
        raise ValueError("Faltan registros del 21/10; no se inventan etiquetas")
    if raw.duplicated(["AlumnoID","Fecha"]).any() or (raw.Visitas<=0).any():
        raise ValueError("Agregados no válidos")
    daily=raw.groupby("Fecha",as_index=False).agg(Visitas=("Visitas","sum"),FilasAlumnoDia=("Visitas","size"))
    daily["Estado"]=daily.Fecha.map(lambda s:status(date.fromisoformat(s),confirmed))
    allowed=raw.Fecha.map(lambda s:status(date.fromisoformat(s),confirmed)=="atencion_segun_calendario")
    clean=raw.loc[allowed].copy()
    clean["Fecha"]=clean.Fecha.map(date.fromisoformat)
    # Estas claves NO salen del proceso: se descartan al devolver estadísticas agregadas.
    by_student={s:dict(zip(g.Fecha,g.Visitas)) for s,g in clean.groupby("AlumnoID",sort=False)}
    groups={k:set() for k in ["train","val","octubre_retrospectivo","solo_ajuste_final","todos"]}
    rows,dates_audit=[],[]
    for day in pd.date_range(START,CUT):
        ref=day.date()
        state=status(ref,confirmed)
        target,reason=target_h1(ref,confirmed) if state=="atencion_segun_calendario" else (None,"referencia_no_confirmada_o_cerrada")
        if target is not None and window_days(ref,30,confirmed) is None:
            reason="ventana_30d_contiene_desconocido"
        accepted=positive=0
        if not reason:
            for student,visits in by_student.items():
                features=feature_row(visits,ref,target,confirmed)
                if features is None:
                    continue
                y=int(visits.get(target,0)>0)
                part=part_for(ref,target)
                rows.append({"FechaRef":ref.isoformat(),"FechaObjetivo":target.isoformat(),
                    "InicioVentana30":(ref-timedelta(days=30)).isoformat(),
                    "FinVentana":(ref-timedelta(days=1)).isoformat(),"Particion":part,**features,"target_h1":y})
                groups[part].add(student)
                groups["todos"].add(student)
                accepted+=1
                positive+=y
        dates_audit.append({"FechaRef":ref.isoformat(),"FechaObjetivo":target.isoformat() if target else None,
            "EstadoReferencia":state,"MotivoExclusion":reason,"ObservacionesValidas":accepted,"Positivas":positive})
    columns=["FechaRef","FechaObjetivo","InicioVentana30","FinVentana","Particion"]+FEATURES+["target_h1"]
    df=pd.DataFrame(rows,columns=columns)
    summaries={}
    for name in ["train","val","octubre_retrospectivo","solo_ajuste_final"]:
        f=df[df.Particion.eq(name)]
        summaries[name]={"observaciones":len(f),"alumnos":len(groups[name]),"fechas":int(f.FechaRef.nunique()),
            "inicio":f.FechaRef.min() if len(f) else None,"fin":f.FechaRef.max() if len(f) else None,
            "clases":{str(int(k)):int(v) for k,v in f.target_h1.value_counts().items()}}
    overlap={f"{a}_{b}":len(groups[a]&groups[b]) for a,b in [("train","val"),("train","octubre_retrospectivo"),("val","octubre_retrospectivo")]}
    stats={"particiones":summaries,"alumnos_totales":len(groups["todos"]),"alumnos_compartidos":overlap}
    return df,pd.DataFrame(dates_audit),daily,stats


def sufficient(parts):
    # El protocolo exige >=3 fechas para seleccionar en validación. Octubre ya
    # examinado solo requiere clases/soporte; sus pocas fechas se declaran como límite.
    errors=[]
    for name in ["train","val","octubre_retrospectivo"]:
        p=parts[name]
        min_dates=1 if name=="octubre_retrospectivo" else 3
        if p["fechas"]<min_dates or set(p["clases"])!={"0","1"} or min(p["clases"].values(),default=0)<10:
            errors.append(name)
    return errors


def prepare():
    now=datetime.now(ZoneInfo("America/Lima"))
    if now.date()<CUT or (now.date()==CUT and now.hour<21):
        raise ValueError("Corte todavía incompleto")
    OUT.mkdir(exist_ok=False)
    before=model_hashes()
    confirmed=coverage()
    rules={"estado":"preparando","corte_captura":CUT.isoformat(),"inicio":now.isoformat(),
        "definicion_ventana":"[FechaRef-W, FechaRef), W=7,14,30 días calendario; excluye FechaRef y objetivo",
        "conteos":"días activos únicos y número de escaneos válidos; sumas solo en días abiertos con captura confirmada",
        "tasas":"días activos / días abiertos confirmados de cada ventana; si una ventana no tiene apertura, se excluye el ejemplo en vez de inventar una tasa",
        "suficiencia_alumno":">=3 días activos en la ventana previa de 30 días, sin acumulación de por vida",
        "desconocidos":"Se excluye el ejemplo si cualquier ventana contiene un desconocido o su siguiente día objetivo no es confirmable. No se salta el 24/07 como si fuera un cierre.",
        "cerrados":"Fines de semana, feriados y receso 01/08–23/08 no son días de ausencia: no entran en conteos, tasas ni objetivos. Las ventanas son calendario, no se extienden para recuperar días cerrados.",
        "cobertura":"Fechas abiertas del resumen histórico, más 29/09–21/10 por confirmación operativa del responsable; no verificación técnica independiente",
        "unknown_days":[d.date().isoformat() for d in pd.date_range(SOURCE_START,CUT) if status(d.date(),confirmed).startswith("desconocido")],
        "protocolo_alumno":"longitudinal temporal, no alumnos disjuntos; grupos usados solo en memoria para conteos/solapamientos",
        "seleccion":"menor Brier septiembre, desempate log-loss, mayor PR-AUC(AP), nombre; solo modelos sin ConvergenceWarning",
        "umbral":"Macro F1 máximo en septiembre en rejilla 0.15..0.80 paso0.05; empate umbral menor; no usa octubre",
        "octubre":"retrospectivo ya examinado; no prueba independiente ni criterio de selección",
        "variables":FEATURES,"modelos_antes":before,
        "fuentes_sha256":{str(p.relative_to(ROOT)):sha(p) for p in [ROOT/"ml/data/resumen_cobertura_por_fecha.csv",ROOT/"ml/scripts/experimento_comparativo_retorno.py",Path(__file__)]}}
    try:
        raw=fetch(now)
        rules["filas_agregadas_solo_en_memoria"]=len(raw)
        rules["alumnos_fuente"]=int(raw.AlumnoID.nunique())
        df,dates,daily,stats=build(raw,confirmed)
        del raw
        for name,frame in [("dataset_sin_identificadores.csv",df),("preflight_por_fecha.csv",dates),("evidencia_diaria.csv",daily)]:
            if "AlumnoID" in frame:
                raise ValueError("No se exportan identificadores")
            write_csv(frame,OUT/name)
        rules.update(stats)
        errors=sufficient(stats["particiones"])
        rules["estado"]="bloqueado" if errors else "listo_para_evaluar"
        rules["bloques_insuficientes"]=errors
        cal=pd.DataFrame([{"Fecha":d.date().isoformat(),"Estado":status(d.date(),confirmed)} for d in pd.date_range(SOURCE_START,CUT)])
        write_csv(cal,OUT/"calendario_aplicado.csv")
        print(json.dumps({"estado":rules["estado"],"particiones":stats["particiones"],"alumnos_totales":stats["alumnos_totales"]},ensure_ascii=False))
    except Exception as exc:
        rules.update({"estado":"bloqueado","error_tipo":type(exc).__name__})
        raise
    finally:
        rules["modelos_despues"]=model_hashes()
        rules["modelos_intactos"]=before==rules["modelos_despues"]
        (OUT/"consulta.sql").write_text(SQL+"\n",encoding="utf-8")
        (OUT/"receta.py").write_bytes(Path(__file__).read_bytes())
        rules["archivos"]={p.name:sha(p) for p in OUT.iterdir() if p.is_file() and p.name!="preflight.json"}
        write_json(OUT/"preflight.json",rules)


def model_factories():
    source=ROOT/"ml/scripts/experimento_comparativo_retorno.py"
    function=next(n for n in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(n,ast.FunctionDef) and n.name=="obtener_fabricas_modelos")
    names=["DummyClassifier","Pipeline","StandardScaler","LogisticRegression","RandomForestClassifier","ExtraTreesClassifier","GradientBoostingClassifier","HistGradientBoostingClassifier"]
    namespace={n:globals()[n] for n in names}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),"exec"),namespace)
    return namespace["obtener_fabricas_modelos"]()


def score(y,p,threshold):
    pred=(p>=threshold).astype(int)
    return {"brier":float(brier_score_loss(y,p)),"log_loss":float(log_loss(y,p,labels=[0,1])),
        "roc_auc":float(roc_auc_score(y,p)),"pr_auc_ap":float(average_precision_score(y,p)),
        "macro_f1":float(f1_score(y,pred,average="macro",zero_division=0)),
        "precision_positivo":float(precision_score(y,pred,zero_division=0)),"recall_positivo":float(recall_score(y,pred,zero_division=0)),
        "accuracy":float(accuracy_score(y,pred)),"balanced_accuracy":float(balanced_accuracy_score(y,pred)),
        "umbral":threshold,"matriz_confusion":confusion_matrix(y,pred,labels=[0,1]).tolist()}


def convergence(model,caught):
    estimator=model.named_steps["clf"] if isinstance(model,Pipeline) else model
    warned=any(issubclass(w.category,ConvergenceWarning) for w in caught)
    n=getattr(estimator,"n_iter_",None)
    state="No aplica (presupuesto fijo o baseline)"
    if isinstance(estimator,LogisticRegression):
        state="Sí" if not warned and int(np.max(n))<estimator.max_iter else "No"
    if isinstance(estimator,HistGradientBoostingClassifier):
        state="Parada interna anticipada" if n<estimator.max_iter else "Presupuesto agotado; estabilidad no demostrada"
    return {"estado":state,"sin_ConvergenceWarning":not warned,"n_iter":np.asarray(n).tolist() if n is not None else None,
            "advertencias":[w.category.__name__+": "+str(w.message) for w in caught]}


def evaluate():
    pre=json.loads((OUT/"preflight.json").read_text(encoding="utf-8"))
    if pre["estado"]!="listo_para_evaluar" or sufficient(pre["particiones"]):
        raise ValueError("Preflight no válido")
    if (OUT/"inicio_evaluacion.json").exists():
        raise FileExistsError("La evaluación ya fue iniciada; no se repite automáticamente")
    if sha(OUT/"dataset_sin_identificadores.csv")!=pre["archivos"]["dataset_sin_identificadores.csv"]:
        raise ValueError("Cambió el dataset")
    source=ROOT/"ml/scripts/experimento_comparativo_retorno.py"
    if sha(source)!=pre["fuentes_sha256"][str(source.relative_to(ROOT))]:
        raise ValueError("Cambió la receta de algoritmos")
    before=model_hashes()
    write_json(OUT/"inicio_evaluacion.json",{"seleccion":pre["seleccion"],"umbral":pre["umbral"],"fecha":datetime.now(ZoneInfo("America/Lima")).isoformat()})
    df=pd.read_csv(OUT/"dataset_sin_identificadores.csv")
    tr=df[df.Particion.eq("train")]; val=df[df.Particion.eq("val")]; octo=df[df.Particion.eq("octubre_retrospectivo")]
    factories=model_factories()
    results={"validacion_septiembre":{},"convergencia":{},"octubre_retrospectivo_ya_examinado":{}}
    trained={}; configs={}
    for name,info in factories.items():
        model=info["factory"]()
        configs[name]={k:repr(v) for k,v in model.get_params(deep=True).items()}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            model.fit(tr[FEATURES],tr.target_h1)
        p=model.predict_proba(val[FEATURES])[:,list(model.classes_).index(1)]
        thresholds=[round(float(x),2) for x in np.arange(.15,.85,.05)]
        threshold=max(thresholds,key=lambda u:f1_score(val.target_h1,p>=u,average="macro",zero_division=0)) if not info["es_linea_base"] else .5
        results["validacion_septiembre"][name]=score(val.target_h1,p,threshold)
        results["convergencia"][name]=convergence(model,caught)
        trained[name]=model
    write_json(OUT/"configuracion_modelos.json",configs)
    eligible=[n for n in trained if not factories[n]["es_linea_base"] and results["convergencia"][n]["sin_ConvergenceWarning"] and results["convergencia"][n]["estado"]!="No"]
    if not eligible:
        raise ValueError("No hay candidato con ajuste aceptable")
    v=results["validacion_septiembre"]
    winner=min(eligible,key=lambda n:(v[n]["brier"],v[n]["log_loss"],-v[n]["pr_auc_ap"],n))
    results["seleccionado_solo_por_septiembre"]=winner
    # Selección fijada antes de mirar octubre.
    write_json(OUT/"seleccion_septiembre.json",{"candidato":winner,"criterio":pre["seleccion"],"validacion":v,"convergencia":results["convergencia"]})
    for name,model in trained.items():
        p=model.predict_proba(octo[FEATURES])[:,list(model.classes_).index(1)]
        results["octubre_retrospectivo_ya_examinado"][name]=score(octo.target_h1,p,v[name]["umbral"])
    write_json(OUT/"resultados.json",results)
    final=clone(trained[winner])
    with warnings.catch_warnings(record=True) as caught_final:
        warnings.simplefilter("always")
        final.fit(df[FEATURES],df.target_h1)
    final_convergence=convergence(final,caught_final)
    if not final_convergence["sin_ConvergenceWarning"] or final_convergence["estado"]=="No":
        raise ValueError("El ajuste final no convergió; no se guarda candidato")
    candidate=OUT/"modelo_retorno_h1_ventanas_experimental.joblib"
    if candidate.exists():
        raise FileExistsError("No se reemplaza candidato")
    package={"modelo":final,"variables":FEATURES,"horizonte":"H1",
        "version":"retorno-h1-ventanas-7-14-30-corte-20261021-v1",
        "umbral_decision":v[winner]["umbral"],"experimental":True,"no_conectado_a_interfaz":True}
    joblib.dump(package,candidate,compress=3)
    meta={"algoritmo":winner,"version":package["version"],"artefacto_sha256":sha(candidate),
        "dataset_sha256":sha(OUT/"dataset_sin_identificadores.csv"),"corte_captura":CUT.isoformat(),
        "max_fecha_referencia":df.FechaRef.max(),"max_fecha_objetivo":df.FechaObjetivo.max(),
        "observaciones_ajuste_final":len(df),"alumnos_ajuste_final":pre["alumnos_totales"],
        "parametros":configs[winner],"variables":FEATURES,"convergencia_ajuste_final":final_convergence,
        "seleccion_septiembre":v[winner],"nota_metricas":"Selección histórica, no evaluación independiente del reajuste final; octubre ya examinado y también incorporado al ajuste final",
        "particiones":pre["particiones"],"alumnos_compartidos":pre["alumnos_compartidos"],
        "reglas_preflight_sha256":sha(OUT/"preflight.json"),"produccion_reemplazada":False,
        "entorno":{"sklearn":sklearn.__version__,"numpy":np.__version__,"pandas":pd.__version__}}
    restored=joblib.load(candidate)
    if list(restored["modelo"].feature_names_in_)!=FEATURES:
        raise ValueError("Artefacto no preservó variables")
    write_json(OUT/"metadatos_candidato.json",meta)
    lines=["# Retorno H1 experimental con ventanas finitas", "",f"Corte de captura: {CUT}. Dataset sin identificadores. Candidato: **{winner}**.",
        "Las métricas seleccionan solo por septiembre. Octubre ya fue examinado: evaluación retrospectiva, no prueba independiente.",
        "", "## Definición y exclusiones", "",pre["definicion_ventana"],pre["conteos"],pre["tasas"],pre["suficiencia_alumno"],pre["desconocidos"],pre["cerrados"],
        "No se modificó el constructor ni los parámetros de producción. Las nuevas 12 variables son exclusivas de este candidato.",
        "Se conservan los cinco algoritmos/configuraciones previos y escalado de logística ajustado solo al train. HistGB conserva early_stopping=auto: su parada interna usa únicamente train, no septiembre/octubre; no es una validación temporal adicional.",
        "", "## Particiones maduras tras exclusiones y purga", "", "| Bloque | Fechas | Alumnos | Observaciones | Negativas | Positivas |", "|---|---|---:|---:|---:|---:|"]
    for name,p in pre["particiones"].items():
        lines.append(f"| {name} | {p['inicio']} a {p['fin']} ({p['fechas']}) | {p['alumnos']} | {p['observaciones']} | {p['clases'].get('0',0)} | {p['clases'].get('1',0)} |")
    lines += ["", "## Validación de septiembre", "", "| Algoritmo | Brier ↓ | Log-loss ↓ | PR-AUC(AP) ↑ | ROC-AUC ↑ | Macro F1 | Umbral | Convergencia |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for name in trained:
        m=v[name]
        lines.append(f"| {name} | {m['brier']:.4f} | {m['log_loss']:.4f} | {m['pr_auc_ap']:.4f} | {m['roc_auc']:.4f} | {m['macro_f1']:.4f} | {m['umbral']:.2f} | {results['convergencia'][name]['estado']} |")
    lines += ["", "## Límites", "",pre["protocolo_alumno"],f"Alumnos compartidos (solo conteos): {pre['alumnos_compartidos']}.",
        "La falta de confirmación del 18/09 censura también sus ventanas de 30 días: octubre puede quedar con muy pocas fechas, insuficientes para conclusiones de robustez temporal. Su resultado no decide la selección.",
        "Conservar ventanas calendario implica menos días observables cerca del receso; no se rellena el cierre como inasistencia ni se alarga la ventana.",
        "El mínimo de tres días activos en 30 días cambia la población elegible respecto del historial de por vida; las métricas antiguas no son una comparación directa.",
        "El umbral se ajustó en la misma validación utilizada para seleccionar; F1 de validación es optimista. No se aplicó calibración adicional ni se demostró calibración perfecta.",
        "El candidato final incorpora todas las filas válidas maduras, incluso septiembre/octubre; no tiene una prueba independiente nueva.",
        f"Convergencia/ajuste final: {final_convergence['estado']}. Producción intacta y candidato no conectado a interfaz."]
    (OUT/"informe.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if before!=model_hashes():
        raise RuntimeError("Se detectó cambio de modelos existentes")
    write_json(OUT/"manifest_evaluacion.json",{"estado":"completada","produccion_y_candidatos_existentes_intactos":True,
        "archivos":{p.name:sha(p) for p in OUT.iterdir() if p.is_file() and p.name!="manifest_evaluacion.json"}})
    print(json.dumps({"candidato":winner,"validacion":v[winner],"convergencia_final":final_convergence,"dataset":len(df)},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare",action="store_true")
    parser.add_argument("--evaluate",action="store_true")
    args=parser.parse_args()
    try:
        if args.prepare==args.evaluate:
            raise ValueError("Elegir solo --prepare o --evaluate")
        prepare() if args.prepare else evaluate()
    except Exception as exc:
        print("Experimento detenido:",type(exc).__name__,"(sin identificadores ni detalles de conexión)",file=sys.stderr)
        sys.exit(1)
