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
    cut=min(profile["cut"],completed)
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


def predecir_candidato(codigo_matricula=None,alumno_id=None,fecha_referencia=None):
    p=_profile()
    now=_now()
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
                "mensaje":"Historial reciente insuficiente: se requieren tres días activos en los 30 días anteriores, con ventanas confirmadas.",
                "diagnostico":{"motivo":"historial_reciente_insuficiente",
                    "dias_activos_validos":active,"fechas_cobertura_faltante":[]}}
    # Mismos bytes/precisión que la evaluación guardada; nunca añade datos de ref/target.
    raw=pd.DataFrame([features],columns=p["recipe"].FEATURES).to_csv(index=False,float_format="%.12g")
    frame=pd.read_csv(io.StringIO(raw))[p["recipe"].FEATURES]
    probs=p["model"].predict_proba(frame)[0]
    mapping={int(c):float(v) for c,v in zip(p["model"].classes_,probs)}
    if set(mapping)!={0,1} or not all(np.isfinite(list(mapping.values()))) or abs(sum(mapping.values())-1)>1e-10:
        raise CandidatoNoDisponible("El candidato produjo probabilidades inválidas.")
    prob=mapping[1]
    return {**base,"status":"success","probabilidad_h1":round(prob,6),"probabilidad_retorno":round(prob,6),
        "probabilidad_porcentaje":f"{prob*100:.1f}%","umbral":p["threshold"],
        "clase_predicha":int(prob>=p["threshold"]),"dias_activos_30d":features["dias_activos_30d"]}
