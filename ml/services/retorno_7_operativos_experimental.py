"""Inferencia aislada del candidato existente; SELECT únicamente, sin registros prospectivos."""
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
DIRECTORY=ROOT/'ml/experiments/retorno_7_operativos_20261021'
ARTIFACT='modelo_retorno_7_operativos_experimental.joblib'


class CandidatoNoDisponible(RuntimeError):
    pass


def _now():
    return datetime.now(ZoneInfo('America/Lima'))


def _profile():
    """Comprueba artefacto, metadatos, constructor y calendario antes de habilitarlo."""
    try:
        manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))['archivos']
        names=[ARTIFACT,'metadatos_candidato.json','preflight.json','constructor_congelado.py',
               'calendario_aplicado.csv','reglas_calendario_fuente.py']
        content={n:(DIRECTORY/n).read_bytes() for n in names}
        if any(hashlib.sha256(b).hexdigest()!=manifest[n] for n,b in content.items()):
            raise ValueError('Integridad')
        meta=json.loads(content['metadatos_candidato.json']); pre=json.loads(content['preflight.json'])
        if hashlib.sha256(content[ARTIFACT]).hexdigest()!=meta['artefacto_sha256']:
            raise ValueError('Artefacto')
        if hashlib.sha256(content['preflight.json']).hexdigest()!=meta['preflight_sha256']:
            raise ValueError('Reglas')
        if hashlib.sha256(content['constructor_congelado.py']).hexdigest()!=pre['fuentes_sha256']['constructor']:
            raise ValueError('Constructor')
        helper=ROOT/'ml/scripts/evaluacion_afluencia_reproducible.py'
        if any(hashlib.sha256(b).hexdigest()!=pre['fuentes_sha256']['reglas_calendario']
               for b in [content['reglas_calendario_fuente.py'],helper.read_bytes()]):
            raise ValueError('Calendario')
        if sklearn.__version__!=meta['entorno']['sklearn']:
            raise ValueError('Entorno')
        recipe=types.ModuleType('_retorno_siete_congelado')
        recipe.__file__=str(DIRECTORY/'constructor_congelado.py')
        exec(compile(content['constructor_congelado.py'],recipe.__file__,'exec'),recipe.__dict__)
        package=joblib.load(io.BytesIO(content[ARTIFACT]))
        model=package['modelo']
        if (type(model).__name__!='GradientBoostingClassifier' or meta['algoritmo']!=type(model).__name__
            or package['algoritmo']!=meta['algoritmo'] or package['horizonte_dias_operativos']!=7
            or package['version']!=meta['version'] or package['variables']!=recipe.FEATURES
            or meta['variables']!=recipe.FEATURES or list(model.feature_names_in_)!=recipe.FEATURES
            or any(repr(model.get_params()[k])!=v for k,v in meta['parametros'].items())):
            raise ValueError('Esquema')
        cal=pd.read_csv(io.BytesIO(content['calendario_aplicado.csv']))
        states={date.fromisoformat(r.Fecha):r.Estado for r in cal.itertuples()}
        return {'metadata':meta,'recipe':recipe,'model':model,'states':states,
                'confirmed':{d for d,s in states.items() if s=='atencion_segun_calendario'},
                'cut':date.fromisoformat(meta['corte_captura'])}
    except Exception:
        raise CandidatoNoDisponible('No se pudo verificar o cargar el candidato de siete días operativos.') from None


def _public(p):
    m=p['metadata']
    return {'algoritmo':m['algoritmo'],'version':m['version'],'fecha_entrenamiento':m['fecha_entrenamiento'],
            'ultima_referencia_entrenamiento':m['ultima_referencia_usada'],
            'ultimo_objetivo_entrenamiento':m['ultimo_objetivo_usado'],'corte_captura':m['corte_captura'],
            'referencia_presentacion':date.fromisoformat(m['ultima_referencia_usada']).strftime('%d/%m/%Y'),
            'objetivo_presentacion':date.fromisoformat(m['ultimo_objetivo_usado']).strftime('%d/%m/%Y')}


def informacion_candidato():
    try:
        return {'disponible':True,**_public(_profile())}
    except CandidatoNoDisponible:
        return {'disponible':False}


def predecir(codigo_matricula,fecha_referencia=None):
    p=_profile(); now=_now(); recipe=p['recipe']
    if not isinstance(codigo_matricula,str) or not codigo_matricula.strip() or len(codigo_matricula)>64:
        raise ValueError('Indique un código de matrícula válido.')
    try:
        ref=now.date() if fecha_referencia is None else date.fromisoformat(fecha_referencia)
    except (TypeError,ValueError):
        raise ValueError('Indique la referencia como AAAA-MM-DD.') from None
    if ref>now.date():
        raise ValueError('No se permiten referencias futuras.')
    if recipe.calendar(ref)!='atencion_segun_calendario':
        raise ValueError('La referencia es una fecha cerrada o desconocida.')
    completed=now.date() if now.time()>time(20,45) else now.date()-timedelta(days=1)
    cut=min(p['cut'],completed)
    base={**_public(p),'probabilidad':None,'experimental':True,'fecha_referencia':ref.isoformat(),
          'corte_variables':(ref-timedelta(days=1)).isoformat(),'corte_captura_confirmada':cut.isoformat(),
          'fecha_generacion':now.isoformat(),
          'advertencia':'Probabilidad experimental, pendiente de validación prospectiva. No emite una clasificación binaria ni registra un pronóstico congelado.'}
    missing=[]
    for i in range(30,0,-1):
        d=ref-timedelta(days=i)
        s=recipe.state(d,p['confirmed'])
        if d<recipe.SOURCE_START or d>cut or d not in p['states'] or s.startswith('desconocido') or s!=p['states'][d]:
            missing.append(d.isoformat())
    if missing:
        return {**base,'status':'cobertura_desconocida',
                'mensaje':'La ventana requiere fechas sin captura completa confirmada; no se calcula.',
                'fechas_cobertura_faltante':missing}
    # Para inferir se necesita calendario futuro, no etiquetas futuras observadas.
    # Nunca se consultan esos días ni se afirman maduros.
    targets=[]; d=ref
    while len(targets)<7:
        d+=timedelta(days=1)
        if d.year!=2026 or recipe.calendar(d).startswith('desconocido'):
            return {**base,'status':'calendario_desconocido','mensaje':'Falta calendario operativo para completar los siete días objetivo.'}
        if recipe.calendar(d)=='atencion_segun_calendario': targets.append(d.isoformat())
    base.update(fechas_objetivo=targets,inicio_objetivo=targets[0],fin_objetivo=targets[-1],
                consulta_retrospectiva=date.fromisoformat(targets[-1])<=p['cut'])
    conn=get_db_connection()
    if conn is None: raise ConnectionError('Fuente no disponible.')
    cursor=None
    try:
        cursor=conn.cursor()
        cursor.execute('SELECT AlumnoID FROM dbo.Alumnos WHERE CodigoMatricula = ?',(codigo_matricula.strip(),))
        found=cursor.fetchone()
        if not found:
            return {**base,'status':'historial_insuficiente','mensaje':'No hay historial local suficiente para calcular esta probabilidad.'}
        cursor.execute("""SELECT CAST(FechaHora AS DATE), COUNT_BIG(*) FROM dbo.RegistroIngresos
WHERE AlumnoID = ? AND Sede = ? AND ISNULL(EsPrueba,0)=0
 AND FechaHora >= ? AND FechaHora < ? AND FechaHora <= ?
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)
GROUP BY CAST(FechaHora AS DATE) ORDER BY CAST(FechaHora AS DATE)""",
            (int(found[0]),'Central',(ref-timedelta(days=30)).isoformat(),ref.isoformat(),now.replace(tzinfo=None)))
        rows=cursor.fetchall()
    except Exception:
        raise ConnectionError('No se pudo leer el historial agregado.') from None
    finally:
        try:
            if cursor is not None: cursor.close()
        finally: conn.close()
    visits={}
    try:
        for day,count in rows:
            day=day.date() if isinstance(day,datetime) else day if isinstance(day,date) else date.fromisoformat(str(day))
            if not ref-timedelta(days=30)<=day<ref or day>cut or int(count)!=count or count<=0:
                raise ValueError('Rango')
            if recipe.state(day,p['confirmed'])=='atencion_segun_calendario': visits[day]=int(count)
    except Exception:
        raise ValueError('La fuente devolvió agregados fuera de la ventana temporal permitida.') from None
    row,reason=recipe.features(visits,ref,p['confirmed'])
    if row is None:
        active={str(w):sum(v>0 for d,v in visits.items() if ref-timedelta(days=w)<=d<ref) for w in (7,14,30)}
        return {**base,'status':'historial_insuficiente' if reason=='historial_insuficiente' else 'cobertura_desconocida',
                'mensaje':'Se requieren tres días activos en los últimos 30 días y apertura confirmada en las ventanas; no se exige una visita en los últimos siete días.',
                'dias_activos_validos':active,'motivo':reason}
    serialized=pd.DataFrame([row],columns=recipe.FEATURES).to_csv(index=False,float_format='%.12g')
    frame=pd.read_csv(io.StringIO(serialized))[recipe.FEATURES]
    probs=p['model'].predict_proba(frame)[0]
    mapping={int(c):float(v) for c,v in zip(p['model'].classes_,probs)}
    if set(mapping)!={0,1} or not all(np.isfinite(v) and 0<=v<=1 for v in mapping.values()) or not np.isclose(sum(mapping.values()),1):
        raise CandidatoNoDisponible('El candidato produjo probabilidades inválidas.')
    return {**base,'status':'success','probabilidad':round(mapping[1],6)}
