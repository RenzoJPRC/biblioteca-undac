"""Opción local aislada. No cambia PredictorRetornoAlumno ni usa registros prospectivos."""
import hashlib
import io
import json
import types
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import sklearn

from db import get_db_connection

ROOT=Path(__file__).resolve().parents[2]
DIRECTORY=ROOT/"ml/experiments/retorno_h1_ventanas_finitas_20261021"
COBERTURA_INFERENCIA=ROOT/"ml/config/cobertura_inferencia_h1.json"
SELECTOR="experimental_h1_ventanas"
LABEL="Experimental, pendiente de validación prospectiva"


class CandidatoNoDisponible(RuntimeError):
    pass


class CoberturaNoConfirmada(ValueError):
    def __init__(self, mensaje, categoria, fechas, profile, cut):
        super().__init__(mensaje)
        self.respuesta={
            "status":"cobertura_no_confirmada", "modelo_selector":SELECTOR,
            "mensaje":mensaje, "probabilidad_h1":None,
            "version_modelo":profile["metadata"]["version"],
            "fecha_entrenamiento":profile["trained_date"].isoformat(),
            "corte_entrenamiento":profile["cut"].isoformat(),
            "corte_datos_confirmados":cut.isoformat(),
            "diagnostico":{"motivo":categoria,
                "fechas_cobertura_faltante":sorted({d.isoformat() for d in fechas}),
                # No consultar ni presentar ausencia de captura como cero actividad.
                "dias_activos_validos":{str(w):None for w in (7,14,30)}}}


def _now():
    return datetime.now(ZoneInfo("America/Lima"))


def _hash_json_o_bytes(payload, expected, name):
    """Git normaliza los JSON de Windows a LF: aceptar solo ese cambio de EOL.

    Mantiene la comprobación exacta para binarios, código y calendario; para JSON
    no se ignoran espacios, valores ni ningún otro cambio de contenido.
    """
    if hashlib.sha256(payload).hexdigest()==expected:
        return True
    if name not in {'metadatos_candidato.json','preflight.json','inicio_evaluacion.json'}:
        return False
    lf=payload.replace(b'\r\n',b'\n')
    return any(hashlib.sha256(value).hexdigest()==expected for value in (lf,lf.replace(b'\n',b'\r\n')))


def _profile(load_model=True):
    """Verifica los bytes antes de cargar el modelo y el constructor congelados."""
    try:
        manifest=json.loads((DIRECTORY/"manifest_evaluacion.json").read_text(encoding="utf-8"))
        names=["metadatos_candidato.json","preflight.json","receta.py","calendario_aplicado.csv",
               "inicio_evaluacion.json","modelo_retorno_h1_ventanas_experimental.joblib"]
        content={}
        for name in names:
            payload=(DIRECTORY/name).read_bytes()
            if not _hash_json_o_bytes(payload,manifest["archivos"][name],name):
                raise ValueError("Hash no coincidente")
            content[name]=payload
        meta=json.loads(content["metadatos_candidato.json"])
        pre=json.loads(content["preflight.json"])
        source_hashes={key.replace("\\", "/"):value for key,value in pre["fuentes_sha256"].items()}
        if hashlib.sha256(content["receta.py"]).hexdigest()!=source_hashes["ml/scripts/retorno_h1_ventanas_finitas.py"]:
            raise ValueError("Constructor no identificado")
        if hashlib.sha256(content["modelo_retorno_h1_ventanas_experimental.joblib"]).hexdigest()!=meta["artefacto_sha256"]:
            raise ValueError("Artefacto no identificado")
        recipe=types.ModuleType("_retorno_h1_dashboard_congelado")
        recipe.__file__=str(DIRECTORY/"receta.py")
        exec(compile(content["receta.py"],recipe.__file__,"exec"),recipe.__dict__)
        cal=pd.read_csv(io.BytesIO(content["calendario_aplicado.csv"]))
        states={date.fromisoformat(r.Fecha):r.Estado for r in cal.itertuples()}
        confirmed={d for d,s in states.items() if s=="atencion_segun_calendario"}
        if recipe.FEATURES!=meta["variables"]:
            raise ValueError("Variables no coincidentes")
        trained=datetime.fromisoformat(json.loads(content["inicio_evaluacion.json"])["fecha"]).date()
        profile={"metadata":meta,"recipe":recipe,"states":states,"confirmed":confirmed,
                 "trained_date":trained,"cut":date.fromisoformat(meta["corte_captura"])}
        if load_model:
            if sklearn.__version__!=meta["entorno"]["sklearn"]:
                raise ValueError("Versión de sklearn diferente")
            package=joblib.load(io.BytesIO(content["modelo_retorno_h1_ventanas_experimental.joblib"]))
            model=package["modelo"]
            if package["variables"]!=recipe.FEATURES or list(model.feature_names_in_)!=recipe.FEATURES:
                raise ValueError("Variables del artefacto diferentes")
            if package["version"]!=meta["version"] or package["umbral_decision"]!=meta["seleccion_septiembre"]["umbral"]:
                raise ValueError("Versión o umbral diferente")
            profile.update({"model":model,"threshold":package["umbral_decision"]})
        return profile
    except Exception:
        raise CandidatoNoDisponible("Candidato experimental no disponible: falló la verificación local del artefacto o sus reglas.") from None


def informacion_candidato():
    try:
        p=_profile(load_model=False)
        return {"disponible":True,"version":p["metadata"]["version"],"fecha_entrenamiento":p["trained_date"].isoformat(),
                "corte_entrenamiento":p["cut"].isoformat(),"etiqueta":LABEL,"selector":SELECTOR}
    except CandidatoNoDisponible:
        return {"disponible":False,"etiqueta":LABEL,"selector":SELECTOR}


def _dates(profile,requested,now):
    if requested is None:
        ref=profile["cut"]
    elif isinstance(requested,datetime):
        ref=requested.date()
    elif isinstance(requested,date):
        ref=requested
    elif isinstance(requested,str):
        try:
            ref=date.fromisoformat(requested)
        except ValueError:
            raise ValueError("Para el candidato, indique la fecha como AAAA-MM-DD.") from None
    else:
        raise ValueError("Fecha de referencia inválida.")
    # Nunca inferir captura nueva a partir de la existencia de ingresos en BD.
    completed=now.date() if now.time()>time(20,45) else now.date()-timedelta(days=1)
    cut=min(profile.get("inference_cut",profile["cut"]),completed)
    if ref-timedelta(days=1)>cut:
        missing=[(ref-timedelta(days=i)) for i in range(30,0,-1)
                 if ref-timedelta(days=i)>cut]
        raise CoberturaNoConfirmada("La ventana requiere cobertura posterior al corte confirmado; no se calcula.",
                                   "posterior_al_corte",missing,profile,cut)
    recipe=profile["recipe"]
    reference_state=recipe.calendar(ref)
    if reference_state.startswith("desconocido"):
        raise CoberturaNoConfirmada("La fecha de referencia tiene cobertura desconocida; no se calcula.",
                                   "cobertura_desconocida",[ref],profile,cut)
    if reference_state!="atencion_segun_calendario":
        raise ValueError("La referencia es un día cerrado.")
    target=ref+timedelta(days=1)
    while True:
        if target.year!=2026:
            raise CoberturaNoConfirmada("Falta calendario confirmado para el objetivo.",
                                       "cobertura_desconocida",[target],profile,cut)
        state=profile["states"].get(target,recipe.calendar(target))
        if state.startswith("desconocido"):
            raise CoberturaNoConfirmada("No se puede confirmar el siguiente día operativo.",
                                       "cobertura_desconocida",[target],profile,cut)
        if state=="atencion_segun_calendario":
            break
        target+=timedelta(days=1)
    start=ref-timedelta(days=30)
    missing=[]
    for day in pd.date_range(start,ref-timedelta(days=1)):
        d=day.date()
        state=recipe.status(d,profile["confirmed"])
        if d not in profile["states"] or state!=profile["states"][d] or state.startswith("desconocido") or d<recipe.SOURCE_START:
            missing.append(d)
    if missing:
        raise CoberturaNoConfirmada("La ventana incluye fechas sin cobertura diaria confirmada; no se calcula.",
                                   "cobertura_desconocida",missing,profile,cut)
    return ref,target,cut


def _metadatos_publicos(p):
    return {"disponible":True,"algoritmo":type(p["model"]).__name__,
            "version":p["metadata"]["version"],"fecha_entrenamiento":p["trained_date"].isoformat(),
            "corte_entrenamiento":p["cut"].isoformat(),
            "ultima_referencia_entrenamiento":p["metadata"]["max_fecha_referencia"],
            "ultimo_objetivo_entrenamiento":p["metadata"]["max_fecha_objetivo"],
            "artefacto_sha256":p["metadata"]["artefacto_sha256"],"etiqueta":LABEL}


def informacion_h1_actual():
    """Carga real e íntegra del único H1 elegido; nunca carga un modelo anterior."""
    try:
        return _metadatos_publicos(_profile())
    except CandidatoNoDisponible:
        return {"disponible":False,"etiqueta":LABEL,
                "mensaje":"No calculable: no se pudo verificar o cargar el artefacto H1 elegido."}


def _perfil_inferencia(p):
    """Añade jornadas explícitas, sin modificar el calendario ni el corte del ajuste.

    La ausencia de esta fuente no confirma ningún día adicional. Una fuente
    inválida bloquea el cálculo; no se usa el último ingreso como evidencia.
    """
    states = dict(p["states"])
    confirmed = set(p["confirmed"])
    evidence = []
    try:
        if COBERTURA_INFERENCIA.exists():
            source = json.loads(COBERTURA_INFERENCIA.read_text(encoding="utf-8"))
            if source["sede"] != "Central" or not isinstance(source["jornadas"],list):
                raise ValueError("Fuente inválida")
            seen = set()
            for row in source["jornadas"]:
                day = date.fromisoformat(row["fecha"])
                if (day in seen or row["hora_inicio"] != "08:00:00" or row["hora_fin"] != "20:45:00"
                        or row["tipo_evidencia"] != "confirmacion_operativa_del_responsable"
                        or row["certificacion_tecnica_independiente"] is not False
                        or not isinstance(row["origen"],str) or not row["origen"].strip()
                        or p["recipe"].calendar(day) != "atencion_segun_calendario"):
                    raise ValueError("Jornada no confirmable")
                seen.add(day)
                states[day] = "atencion_segun_calendario"
                confirmed.add(day)
                evidence.append(dict(row))
    except Exception:
        raise CoberturaNoConfirmada("No calculable: no se pudo validar la fuente de cobertura de inferencia.",
                                   "cobertura_desconocida",[],p,p["cut"]) from None
    return {**p,"states":states,"confirmed":confirmed,
            "inference_cut":max([p["cut"]]+[date.fromisoformat(r["fecha"]) for r in evidence]),
            "evidencia_inferencia":evidence}


def _referencia_automatica(p, now):
    # Un ingreso no demuestra cobertura. Usar exclusivamente el calendario verificado.
    terminado = now.date() if now.time() > time(20,45) else now.date()-timedelta(days=1)
    if terminado.year != 2026:
        raise CoberturaNoConfirmada("No calculable: falta calendario confirmado para la fecha actual.",
                                   "cobertura_desconocida",[terminado],p,p["cut"])
    ref = terminado
    while p["recipe"].calendar(ref) != "atencion_segun_calendario":
        if p["recipe"].calendar(ref).startswith("desconocido"):
            raise CoberturaNoConfirmada("No calculable: la última jornada tiene cobertura desconocida.",
                                       "cobertura_desconocida",[ref],p,p["cut"])
        ref -= timedelta(days=1)
        if ref < p["recipe"].SOURCE_START:
            raise CoberturaNoConfirmada("No calculable: no hay una jornada completa confirmada.",
                                       "cobertura_desconocida",[ref],p,p["cut"])
    if ref not in p["confirmed"] or ref > p.get("inference_cut",p["cut"]):
        raise CoberturaNoConfirmada("No calculable: falta confirmar la captura de la última jornada terminada; no se usa una referencia antigua como actual.",
                                   "posterior_al_corte",[ref],p,p.get("inference_cut",p["cut"]))
    return ref


def predecir_h1_actual(codigo_matricula=None, alumno_id=None):
    """Flujo automático del dashboard. No acepta fecha o modelo del cliente."""
    p = _perfil_inferencia(_profile())  # Paquete del ajuste íntegro + cobertura operativa separada.
    now = _now()
    ref = _referencia_automatica(p, now)
    result = _predecir_con_perfil(p, now, codigo_matricula, alumno_id, ref)
    result["metadatos_modelo"] = _metadatos_publicos(p)
    result["evidencia_cobertura_inferencia"] = p["evidencia_inferencia"]
    if result.get("status") != "success":
        result["estado_calculo"] = result.get("status")
        result["status"] = "no_calculable"
    return result


def predecir_candidato(codigo_matricula=None,alumno_id=None,fecha_referencia=None):
    # API interna histórica para pruebas de ventanas; el dashboard no admite fecha manual.
    return _predecir_con_perfil(_profile(), _now(), codigo_matricula, alumno_id, fecha_referencia)


def _predecir_con_perfil(p, now, codigo_matricula=None, alumno_id=None, fecha_referencia=None):
    ref,target,cut=_dates(p,fecha_referencia,now)
    base={"modelo_selector":SELECTOR,"es_experimental":True,"estado_validacion":LABEL,
        "version_modelo":p["metadata"]["version"],"algoritmo":p["metadata"]["algoritmo"],
        "fecha_entrenamiento":p["trained_date"].isoformat(),"corte_entrenamiento":p["cut"].isoformat(),
        "corte_datos_confirmados":cut.isoformat(),"corte_variables":(ref-timedelta(days=1)).isoformat(),
        "fecha_referencia":ref.isoformat(),"fecha_objetivo":target.isoformat(),"fecha_generacion":now.isoformat(),
        "probabilidad_h1":None,"probabilidad_retorno":None,
        "advertencia":"Experimental, pendiente de validación prospectiva. Esta consulta interactiva no crea ni sustituye un registro prospectivo.",
        "consulta_retrospectiva_con_modelo_reajustado":target<=p["cut"]}
    if not codigo_matricula and not alumno_id:
        raise ValueError("Indique un código de matrícula o identificador del alumno.")
    conn=get_db_connection()
    if conn is None:
        raise ConnectionError("No se pudo abrir la fuente de ingresos.")
    cursor=None
    try:
        cursor=conn.cursor()
        if codigo_matricula:
            cursor.execute("SELECT AlumnoID FROM dbo.Alumnos WHERE CodigoMatricula = ?",(str(codigo_matricula).strip(),))
        else:
            if isinstance(alumno_id,bool) or not str(alumno_id).isdigit() or int(alumno_id)<=0:
                raise ValueError("Identificador de alumno inválido.")
            cursor.execute("SELECT AlumnoID FROM dbo.Alumnos WHERE AlumnoID = ?",(int(alumno_id),))
        found=cursor.fetchone()
        if not found:
            return {**base,"status":"error","mensaje":"No se encontró el alumno solicitado."}
        cursor.execute("""SELECT CAST(FechaHora AS DATE) AS Fecha, COUNT_BIG(*) AS Visitas
FROM dbo.RegistroIngresos
WHERE AlumnoID = ? AND Sede = ? AND ISNULL(EsPrueba, 0) = 0
 AND FechaHora >= ? AND FechaHora < ? AND FechaHora <= ?
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)
GROUP BY CAST(FechaHora AS DATE) ORDER BY Fecha""",
            (int(found[0]),"Central",(ref-timedelta(days=30)).isoformat(),ref.isoformat(),now.replace(tzinfo=None)))
        rows=cursor.fetchall()
    except ValueError:
        raise
    except Exception:
        raise ConnectionError("No fue posible leer el historial requerido por el candidato.") from None
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            conn.close()  # SELECT exclusivamente; no commit ni registro de predicciones.
    visits={}
    for day,count in rows:
        d=day.date() if isinstance(day,datetime) else day if isinstance(day,date) else date.fromisoformat(str(day))
        if not ref-timedelta(days=30)<=d<ref or d>cut or int(count)<0:
            raise ValueError("La fuente devolvió datos fuera de la ventana temporal permitida.")
        if p["recipe"].status(d,p["confirmed"])=="atencion_segun_calendario":
            visits[d]=int(count)
    features=p["recipe"].feature_row(visits,ref,target,p["confirmed"])
    if features is None:
        active={str(w):sum(visits.get(d,0)>0 for d in p["recipe"].window_days(ref,w,p["confirmed"])) for w in (7,14,30)}
        return {**base,"status":"historial_insuficiente",
                "mensaje":f"No calculable: {active['30']} día(s) activo(s) válido(s) en los 30 días anteriores; se requieren al menos tres. Solo Central, ingresos no marcados como prueba y horario 08:00–20:45; se excluyen la referencia, cierres y días desconocidos.",
                "diagnostico":{"motivo":"historial_reciente_insuficiente",
                    "dias_activos_validos":active,"fechas_cobertura_faltante":[]},
                "detalle_historial":{"minimo_dias_activos_30":3,
                    "ventana_desde":(ref-timedelta(days=30)).isoformat(),
                    "ventana_hasta":(ref-timedelta(days=1)).isoformat(),
                    "criterio":"Solo Central, 08:00–20:45, sin pruebas; días distintos con captura confirmada. La fecha de referencia queda fuera de las variables del candidato."}}
    # Mismos bytes/precisión que la evaluación guardada; nunca añade datos de ref/target.
    raw=pd.DataFrame([features],columns=p["recipe"].FEATURES).to_csv(index=False,float_format="%.12g")
    frame=pd.read_csv(io.StringIO(raw))[p["recipe"].FEATURES]
    probs=p["model"].predict_proba(frame)[0]
    mapping={int(c):float(v) for c,v in zip(p["model"].classes_,probs)}
    if set(mapping)!={0,1} or not all(np.isfinite(list(mapping.values()))) or any(v < 0 or v > 1 for v in mapping.values()) or abs(sum(mapping.values())-1)>1e-10:
        raise CandidatoNoDisponible("El candidato produjo probabilidades inválidas.")
    prob=mapping[1]
    return {**base,"status":"success","probabilidad_h1":round(prob,6),"probabilidad_retorno":round(prob,6),
        "probabilidad_porcentaje":f"{prob*100:.1f}%","umbral":p["threshold"],
        "clase_predicha":int(prob>=p["threshold"]),"dias_activos_30d":features["dias_activos_30d"]}
