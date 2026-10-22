"""Experimento nuevo: al menos un ingreso en los siguientes siete días operativos.

--prepare consulta solo SELECT; --evaluate usa exclusivamente la instantánea local.
Los identificadores se usan en memoria y nunca se exportan.
"""
import argparse
import hashlib
import json
import warnings
from datetime import date, datetime, time, timedelta
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
from sklearn.metrics import average_precision_score, brier_score_loss, confusion_matrix, f1_score, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.scripts.evaluacion_afluencia_reproducible import ROOT, calendar, model_hashes, sha, write_csv, write_json

SOURCE_START=date(2026,5,2)
START=date(2026,6,1)
FEATURES=[f'{kind}_{w}d' for w in (7,14,30) for kind in ('dias_activos','visitas','tasa_asistencia')]+['recencia_30d','dias_apertura_30d']
COVERAGE=ROOT/'ml/experiments/retorno_h1_ventanas_finitas_20261021/calendario_aplicado.csv'
SQL="""SELECT AlumnoID, CAST(FechaHora AS DATE), COUNT_BIG(*)
FROM dbo.RegistroIngresos
WHERE FechaHora >= ? AND FechaHora < ? AND FechaHora <= ? AND Sede = ?
 AND AlumnoID IS NOT NULL AND ISNULL(EsPrueba,0)=0
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)
GROUP BY AlumnoID, CAST(FechaHora AS DATE) ORDER BY CAST(FechaHora AS DATE), AlumnoID"""
MAX_SQL="""SELECT MAX(FechaHora) FROM dbo.RegistroIngresos
WHERE Sede = ? AND AlumnoID IS NOT NULL AND ISNULL(EsPrueba,0)=0 AND FechaHora <= ?
 AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME)"""


def state(day, confirmed):
    s=calendar(day)
    return 'desconocido_captura' if s=='atencion_segun_calendario' and day not in confirmed else s


def targets(ref,cut,confirmed):
    """No salta fechas desconocidas ni madura etiquetas parcialmente observadas."""
    days=[]
    d=ref
    while len(days)<7:
        d+=timedelta(days=1)
        if d>cut:
            return None,'etiqueta_no_madura'
        s=state(d,confirmed)
        if s.startswith('desconocido'):
            return None,'objetivo_cobertura_desconocida'
        if s=='atencion_segun_calendario':
            days.append(d)
    return days,None


def features(visits,ref,confirmed):
    """Ventanas previas finitas; no exige visitas en los últimos siete días."""
    windows={w:[ref-timedelta(days=i) for i in range(w,0,-1)] for w in (7,14,30)}
    if windows[30][0]<SOURCE_START or any(state(d,confirmed).startswith('desconocido') for d in windows[30]):
        return None,'historial_cobertura_desconocida'
    windows={w:[d for d in days if state(d,confirmed)=='atencion_segun_calendario'] for w,days in windows.items()}
    if any(not days for days in windows.values()):
        return None,'ventana_sin_dias_operativos'
    active=[d for d in windows[30] if visits.get(d,0)>0]
    if len(active)<3:
        return None,'historial_insuficiente'
    row={}
    for w,days in windows.items():
        n=sum(visits.get(d,0)>0 for d in days)
        row.update({f'dias_activos_{w}d':n,f'visitas_{w}d':sum(visits.get(d,0) for d in days),f'tasa_asistencia_{w}d':n/len(days)})
    row.update(recencia_30d=(ref-max(active)).days,dias_apertura_30d=len(windows[30]))
    return row,None


def partition(ref,end):
    # Purga: todos los objetivos de train terminan antes de validación.
    if ref<date(2026,9,1) and end<date(2026,9,1):
        return 'entrenamiento'
    if date(2026,9,1)<=ref<date(2026,10,1) and end<date(2026,10,1):
        return 'validacion_septiembre'
    if ref>=date(2026,10,1):
        return 'octubre_retrospectivo'
    return 'solo_ajuste_final'


def build(raw,cut,confirmed):
    if raw.empty or raw.duplicated(['AlumnoID','Fecha']).any() or (raw.Visitas<=0).any():
        raise ValueError('Fuente agregada vacía o inválida')
    raw=raw.copy()
    raw['Fecha']=pd.to_datetime(raw.Fecha).dt.date
    if raw.Fecha.min()<SOURCE_START or raw.Fecha.max()>cut:
        raise ValueError('Fuente fuera del corte')
    daily=raw.groupby('Fecha').agg(Visitas=('Visitas','sum'),FilasAlumnoDia=('Visitas','size')).reset_index()
    daily['Estado']=daily.Fecha.map(lambda d:state(d,confirmed))
    clean=raw[raw.Fecha.map(lambda d:state(d,confirmed)=='atencion_segun_calendario')]
    students={k:dict(zip(g.Fecha,g.Visitas)) for k,g in clean.groupby('AlumnoID',sort=False)}
    groups={k:set() for k in ('entrenamiento','validacion_septiembre','octubre_retrospectivo','solo_ajuste_final','todos')}
    rows=[]; audit=[]; latest=None
    for dt in pd.date_range(START,cut):
        ref=dt.date(); days=None; reason='referencia_no_confirmada_o_cerrada'; counts={}
        if state(ref,confirmed)=='atencion_segun_calendario':
            days,reason=targets(ref,cut,confirmed)
        n=pos=0
        if days:
            latest=ref
            for student,visits in students.items():
                row,excluded=features(visits,ref,confirmed)
                if excluded:
                    counts[excluded]=counts.get(excluded,0)+1
                    continue
                label=int(any(visits.get(d,0)>0 for d in days))
                part=partition(ref,days[-1])
                rows.append({'FechaRef':ref.isoformat(),'InicioObjetivo':days[0].isoformat(),
                    'FinObjetivo':days[-1].isoformat(),'FinVariables':(ref-timedelta(days=1)).isoformat(),
                    'Particion':part,**row,'retorno_7_operativos':label})
                groups[part].add(student); groups['todos'].add(student)
                n+=1; pos+=label
        audit.append({'FechaRef':ref.isoformat(),'Motivo':reason,'FinObjetivo':days[-1].isoformat() if days else None,
                      'Observaciones':n,'Positivas':pos,**counts})
    df=pd.DataFrame(rows,columns=['FechaRef','InicioObjetivo','FinObjetivo','FinVariables','Particion']+FEATURES+['retorno_7_operativos'])
    summaries={}
    for name in groups:
        if name=='todos': continue
        part=df[df.Particion.eq(name)]
        summaries[name]={'observaciones':len(part),'alumnos':len(groups[name]),'fechas':int(part.FechaRef.nunique()),
            'inicio':part.FechaRef.min() if len(part) else None,'fin':part.FechaRef.max() if len(part) else None,
            'fin_objetivos':part.FinObjetivo.max() if len(part) else None,
            'clases':{str(int(k)):int(v) for k,v in part.retorno_7_operativos.value_counts().items()}}
    return df,pd.DataFrame(audit),daily,{'particiones':summaries,'alumnos_totales':len(groups['todos']),
        'alumnos_compartidos_train_val':len(groups['entrenamiento']&groups['validacion_septiembre']),
        'ultima_referencia_madura_calendario':latest.isoformat() if latest else None}


def sufficient(stats):
    return [name for name in ('entrenamiento','validacion_septiembre')
            if stats['particiones'][name]['fechas']<3 or
            min(stats['particiones'][name]['clases'].get('0',0),stats['particiones'][name]['clases'].get('1',0))<10]


def prepare(out):
    from db import get_db_connection
    now=datetime.now(ZoneInfo('America/Lima'))
    completed=now.date() if now.time()>time(20,45) else now.date()-timedelta(days=1)
    cal=pd.read_csv(COVERAGE)
    confirmed={date.fromisoformat(r.Fecha) for r in cal.itertuples() if r.Estado=='atencion_segun_calendario'}
    available=max(d for d in confirmed if d<=completed)
    before=model_hashes()
    conn=get_db_connection()
    if conn is None: raise RuntimeError('No se pudo consultar la BD configurada')
    cursor=None
    try:
        cursor=conn.cursor()
        cursor.execute(MAX_SQL,('Central',now.replace(tzinfo=None)))
        maximum=cursor.fetchone()[0]
        if maximum is None: raise ValueError('No hay captura agregada')
        maxday=maximum.date() if isinstance(maximum,datetime) else date.fromisoformat(str(maximum)[:10])
        cut=max(d for d in confirmed if d<=min(available,maxday))
        cursor.execute(SQL,(SOURCE_START.isoformat(),(cut+timedelta(days=1)).isoformat(),now.replace(tzinfo=None),'Central'))
        raw=pd.DataFrame([(r[0],str(r[1])[:10],int(r[2])) for r in cursor.fetchall()],columns=['AlumnoID','Fecha','Visitas'])
    finally:
        try:
            if cursor is not None: cursor.close()
        finally: conn.close()
    df,audit,daily,stats=build(raw,cut,confirmed)
    del raw  # Ningún identificador se guarda, ni se transforma en hash/pseudónimo.
    out.mkdir(parents=True,exist_ok=False)
    write_csv(df,out/'dataset_sin_identificadores.csv')
    write_csv(audit,out/'auditoria_fechas.csv')
    write_csv(daily,out/'evidencia_diaria_agregada.csv')
    write_csv(cal,out/'calendario_aplicado.csv')
    rules={**stats,'estado':'listo_para_evaluar' if not sufficient(stats) else 'bloqueado',
        'bloqueos':sufficient(stats),'generado':now.isoformat(),'sede':'Central',
        'max_fecha_registro_observada':maxday.isoformat(),'corte_captura_completa_confirmada':cut.isoformat(),
        'definicion':'Al menos un ingreso válido durante los siguientes siete días operativos, estrictamente posteriores a FechaRef.',
        'calendario':'Lunes-viernes 08:00–20:45; feriados y receso 01/08–23/08 cerrados. 24/07 desconocido, nunca saltado como cierre.',
        'cobertura':'Calendario congelado del proyecto. Histórico declarado; 29/09–21/10 confirmación operativa del responsable, no verificación técnica independiente. No se infiere continuidad a partir de MAX(FechaHora).',
        'variables':'Ventanas [FechaRef-W,FechaRef), 7/14/30 días calendario; solo apertura confirmada; ventanas con desconocidos excluidas.',
        'elegibilidad':'>=3 días activos válidos en 30 días anteriores. Sin requisito de visita en los últimos 7 días; insuficiencia no equivale a clase negativa.',
        'purga':'Entrenamiento anterior a septiembre y todos sus objetivos anteriores a septiembre; validación de septiembre con objetivos completos antes de octubre.',
        'protocolo_alumnos':'Longitudinal temporal; pueden repetirse alumnos entre bloques. Sin IDs como variable; no mide generalización a alumnos nuevos. Conteos de solapamiento solo en memoria.',
        'seleccion':'Menor Brier de validación septiembre; desempate menor log-loss, mayor AP, nombre. Sin ConvergenceWarning. Umbral fijo 0.5 para todos.',
        'octubre':'Retrospectivo ya examinado en el proyecto; no prueba independiente ni criterio de selección. Puede quedar vacío por cobertura histórica desconocida.',
        'suficiencia':'Mínimo 3 referencias y 10 observaciones por clase en train y validación; no garantiza independencia entre etiquetas superpuestas ni robustez poblacional.',
        'modelos_antes':before,'dataset_sha256':sha(out/'dataset_sin_identificadores.csv'),
        'fuentes_sha256':{'constructor':sha(__file__),'calendario':sha(COVERAGE),
            'reglas_calendario':sha(ROOT/'ml/scripts/evaluacion_afluencia_reproducible.py')},'sql_select':[MAX_SQL,SQL]}
    write_json(out/'preflight.json',rules)
    if before!=model_hashes(): raise RuntimeError('Cambió un modelo existente')
    print(json.dumps({k:rules[k] for k in ('estado','corte_captura_completa_confirmada','max_fecha_registro_observada','ultima_referencia_madura_calendario','particiones')},ensure_ascii=False))


def factories():
    return {
        'LogisticRegression':lambda:make_pipeline(StandardScaler(),LogisticRegression(class_weight='balanced',max_iter=1000,random_state=42)),
        'RandomForestClassifier':lambda:RandomForestClassifier(n_estimators=100,max_depth=8,class_weight='balanced',random_state=42,n_jobs=1),
        'ExtraTreesClassifier':lambda:ExtraTreesClassifier(n_estimators=100,max_depth=8,class_weight='balanced',random_state=42,n_jobs=1),
        'GradientBoostingClassifier':lambda:GradientBoostingClassifier(n_estimators=100,max_depth=4,learning_rate=.08,random_state=42),
        'HistGradientBoostingClassifier':lambda:HistGradientBoostingClassifier(max_iter=100,learning_rate=.08,early_stopping=False,random_state=42)}


def metrics(y,p):
    return {'brier':float(brier_score_loss(y,p)),'log_loss':float(log_loss(y,p,labels=[0,1])),
        'roc_auc':float(roc_auc_score(y,p)),'pr_auc_ap':float(average_precision_score(y,p)),
        'macro_f1':float(f1_score(y,p>=.5,average='macro',zero_division=0)),
        'matriz_confusion':confusion_matrix(y,p>=.5,labels=[0,1]).tolist(),'umbral':.5}


def evaluate(out):
    pre=json.loads((out/'preflight.json').read_text(encoding='utf-8'))
    if pre['estado']!='listo_para_evaluar' or sufficient(pre): raise ValueError('Particiones insuficientes')
    if (out/'inicio_evaluacion.json').exists(): raise FileExistsError('No se repite evaluación iniciada')
    if sha(out/'dataset_sin_identificadores.csv')!=pre['dataset_sha256'] or sha(__file__)!=pre['fuentes_sha256']['constructor']:
        raise ValueError('Cambió la instantánea o su constructor')
    before=model_hashes()
    write_json(out/'inicio_evaluacion.json',{'fecha':datetime.now(ZoneInfo('America/Lima')).isoformat(),'criterio':pre['seleccion']})
    df=pd.read_csv(out/'dataset_sin_identificadores.csv')
    train=df[df.Particion.eq('entrenamiento')]; val=df[df.Particion.eq('validacion_septiembre')]
    results={}; fitted={}; configs={}
    for name,factory in factories().items():
        model=factory(); configs[name]={k:repr(v) for k,v in model.get_params(deep=True).items()}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always'); model.fit(train[FEATURES],train.retorno_7_operativos)
        warned=any(issubclass(w.category,ConvergenceWarning) for w in caught)
        estimator=model.steps[-1][1] if hasattr(model,'steps') else model
        convergence='No aplica: presupuesto fijo' if name!='LogisticRegression' else ('No' if warned or int(np.max(estimator.n_iter_))>=estimator.max_iter else 'Sí')
        p=model.predict_proba(val[FEATURES])[:,list(model.classes_).index(1)]
        results[name]={**metrics(val.retorno_7_operativos,p),'convergencia':convergence,'sin_ConvergenceWarning':not warned}
        fitted[name]=model
    eligible=[n for n in fitted if results[n]['sin_ConvergenceWarning'] and results[n]['convergencia']!='No']
    if not eligible: raise ValueError('No hay candidato convergido')
    winner=min(eligible,key=lambda n:(results[n]['brier'],results[n]['log_loss'],-results[n]['pr_auc_ap'],n))
    baseline=DummyClassifier(strategy='prior').fit(train[FEATURES],train.retorno_7_operativos)
    base=metrics(val.retorno_7_operativos,baseline.predict_proba(val[FEATURES])[:,1])
    write_json(out/'seleccion_validacion.json',{'ganador':winner,'resultados':results,'baseline_prevalencia_train':base})
    final=clone(fitted[winner])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always'); final.fit(df[FEATURES],df.retorno_7_operativos)
    if any(issubclass(w.category,ConvergenceWarning) for w in caught): raise ValueError('Ajuste final sin convergencia')
    artifact=out/'modelo_retorno_7_operativos_experimental.joblib'
    if artifact.exists(): raise FileExistsError('No reemplazar artefacto')
    version=f'retorno-7-operativos-{winner}-corte-{pre["corte_captura_completa_confirmada"]}-v1'
    joblib.dump({'modelo':final,'variables':FEATURES,'horizonte_dias_operativos':7,'version':version,
                 'umbral_decision':.5,'experimental':True,'algoritmo':winner},artifact,compress=3)
    meta={'version':version,'algoritmo':winner,'fecha_entrenamiento':datetime.now(ZoneInfo('America/Lima')).isoformat(),
        'corte_captura':pre['corte_captura_completa_confirmada'],'ultima_referencia_madura_calendario':pre['ultima_referencia_madura_calendario'],
        'ultima_referencia_usada':df.FechaRef.max(),'ultimo_objetivo_usado':df.FinObjetivo.max(),
        'observaciones_ajuste_final':len(df),'alumnos_ajuste_final':pre['alumnos_totales'],
        'dataset_sha256':pre['dataset_sha256'],'artefacto_sha256':sha(artifact),'preflight_sha256':sha(out/'preflight.json'),
        'parametros':configs[winner],'variables':FEATURES,'particiones':pre['particiones'],
        'metricas_validacion':results[winner],'baseline':base,'sin_prueba_independiente':True,
        'no_conectado_a_produccion':True,'entorno':{'sklearn':sklearn.__version__,'numpy':np.__version__,'pandas':pd.__version__}}
    write_json(out/'configuraciones.json',configs); write_json(out/'metadatos_candidato.json',meta)
    lines=['# Retorno en los próximos siete días operativos — candidato experimental','',f'Algoritmo: **{winner}**. Corte completo confirmado: {meta["corte_captura"]}.',
        f'Última referencia madura según calendario: {meta["ultima_referencia_madura_calendario"]}; última elegible usada: {meta["ultima_referencia_usada"]}.',
        '',pre['definicion'],pre['cobertura'],pre['variables'],pre['elegibilidad'],pre['purga'],pre['protocolo_alumnos'],pre['seleccion'],pre['octubre'],
        '', '| Partición | Referencias | Fin de objetivos | Alumnos | Observaciones | Negativas | Positivas |', '|---|---|---|---:|---:|---:|---:|']
    for name,s in pre['particiones'].items():
        lines.append(f'| {name} | {s["inicio"]} a {s["fin"]} ({s["fechas"]} fechas) | {s["fin_objetivos"]} | {s["alumnos"]} | {s["observaciones"]} | {s["clases"].get("0",0)} | {s["clases"].get("1",0)} |')
    lines+=['','| Algoritmo | Brier ↓ | Log-loss ↓ | ROC-AUC | AP | Macro F1 | Convergencia |','|---|---:|---:|---:|---:|---:|---|']
    for name,m in results.items():
        lines.append(f'| {name} | {m["brier"]:.6f} | {m["log_loss"]:.6f} | {m["roc_auc"]:.6f} | {m["pr_auc_ap"]:.6f} | {m["macro_f1"]:.6f} | {m["convergencia"]} |')
    lines+=['',f'Baseline de prevalencia de entrenamiento: Brier {base["brier"]:.6f}.',
        'Las etiquetas de referencias cercanas se superponen: observaciones y alumnos no son muestras independientes. La selección es histórica y limitada, sin intervalos de confianza ni prueba prospectiva.',
        'El ajuste final incluye toda observación madura elegible, incluida validación; sus métricas no evalúan ese reajuste independientemente. No se reutilizan métricas del otro horizonte.',
        'No se exigieron visitas en los siete días previos. La insuficiencia de historial excluye ejemplos; no crea negativos.',
        'Se preservan únicamente agregados diarios de auditoría, variables y etiquetas sin identificadores. No hay tabla de equivalencias ni tokens.',
        f'Dataset SHA256: {pre["dataset_sha256"]}. Artefacto SHA256: {meta["artefacto_sha256"]}.']
    (out/'informe.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    if before!=model_hashes(): raise RuntimeError('Cambió producción')
    write_json(out/'manifest.json',{'archivos':{p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='manifest.json'},'modelos_existentes_intactos':True})
    print(json.dumps({'algoritmo':winner,'validacion':results[winner],'baseline':base,'observaciones_finales':len(df)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true'); parser.add_argument('--evaluate',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.prepare==args.evaluate: raise ValueError('Seleccione una sola fase')
        prepare(args.output) if args.prepare else evaluate(args.output)
    except Exception as exc:
        print('Experimento detenido: '+type(exc).__name__+' (sin identificadores ni detalles de conexión).')
        raise SystemExit(1)
