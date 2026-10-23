"""Único H1 del dashboard: sin fecha/modelo del cliente, BD y proveedores bloqueados."""
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy as np
from app import app
from routes import admin_ml as routes
from ml.services import retorno_h1_experimental as candidate


class TestDashboardRetornoExperimental(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = candidate._profile()  # Verificación y carga real, sin BD.

    def setUp(self):
        self.client = app.test_client()
        self.session('SuperAdmin')
        for name in ('pyodbc.connect', 'pymssql.connect', 'requests.sessions.Session.request'):
            p = patch(name, side_effect=AssertionError('Servicio real bloqueado'))
            p.start(); self.addCleanup(p.stop)
        p = patch('app.get_db_connection', return_value=None)
        p.start(); self.addCleanup(p.stop)
        self.clock = patch.object(candidate, '_now', return_value=datetime(2026,10,22,10,tzinfo=ZoneInfo('America/Lima')))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value
        self.cursor.fetchone.return_value = (123,)
        self.rows = [(date(2026,10,d),2) for d in (14,15,16,20)]
        self.cursor.fetchall.return_value = self.rows
        p = patch.object(candidate, 'get_db_connection', return_value=self.conn)
        self.db = p.start(); self.addCleanup(p.stop)
        self.model = MagicMock()
        self.model.classes_ = np.array([0,1])
        self.model.predict_proba.return_value = np.array([[.3,.7]])
        profile = dict(self.profile); profile['model'] = self.model
        p = patch.object(candidate, '_profile', return_value=profile)
        self.loader = p.start(); self.addCleanup(p.stop)
        p = patch.object(routes, 'predecir_retorno_alumno', side_effect=AssertionError('No usar el modelo anterior'))
        self.old = p.start(); self.addCleanup(p.stop)
        p = patch.object(routes.PredictorRetornoAlumno, 'obtener_instancia', side_effect=AssertionError('No cargar el modelo anterior'))
        self.old_loader = p.start(); self.addCleanup(p.stop)

    def session(self, role, sede='Central'):
        with self.client.session_transaction() as s:
            s.update(admin_user='operador-ficticio', admin_rol=role, admin_sede=sede, csrf_token='csrf-ficticio')

    def post(self, payload=None, csrf=True):
        return self.client.post('/admin/api/ml/predecir-retorno', json=payload if payload is not None else {'codigo_matricula':'FICTICIO1234'},
                                headers={'X-CSRFToken':'csrf-ficticio'} if csrf else {})

    def html(self):
        with patch.object(candidate, '_profile', return_value=self.profile), patch.object(routes, 'obtener_informacion_modelo', return_value={}):
            response = self.client.get('/admin/ml')
        self.assertEqual(response.status_code,200)
        self.old_loader.assert_not_called()
        return response.get_data(as_text=True)

    def test_unico_h1_automatico_constructor_exacto(self):
        result = self.post()
        self.assertEqual(result.status_code,200)
        data = result.get_json()
        self.assertEqual(data['status'],'success')
        self.assertEqual(data['codigo_consultado'],'FICTICIO1234')
        self.assertEqual(data['fecha_referencia'],'2026-10-21')
        self.assertEqual(data['fecha_objetivo'],'2026-10-22')
        self.assertEqual(data['corte_variables'],'2026-10-20')
        self.assertEqual(data['probabilidad_h1'],.7)
        self.model.predict_proba.assert_called_once()
        expected = self.profile['recipe'].feature_row(dict(self.rows),date(2026,10,21),date(2026,10,22),self.profile['confirmed'])
        frame = self.model.predict_proba.call_args.args[0]
        self.assertEqual(list(frame), self.profile['recipe'].FEATURES)
        for key,value in expected.items():
            self.assertAlmostEqual(frame.iloc[0][key],value,places=10)
        self.old.assert_not_called()
        self.conn.commit.assert_not_called()
        self.conn.close.assert_called_once()

    def test_fechas_y_modelos_cliente_rechazados_sin_bd(self):
        for extra in ({'fecha_referencia':'2026-10-23'},{'modelo':'actual'},{'modelo':candidate.SELECTOR},{'alumno_id':123},{'rol':'SuperAdmin'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.post({'codigo_matricula':'FICTICIO1234',**extra}).status_code,400)
        self.db.assert_not_called(); self.old.assert_not_called()

    def test_referencia_nunca_futura_ni_jornada_en_curso(self):
        for now, expected in [(datetime(2026,10,21,10,tzinfo=ZoneInfo('America/Lima')),'2026-10-20'),
                              (datetime(2026,10,22,8,tzinfo=ZoneInfo('America/Lima')),'2026-10-21')]:
            with patch.object(candidate,'_now',return_value=now):
                self.assertEqual(candidate._referencia_automatica(self.profile,now).isoformat(),expected)

    def test_captura_no_confirmada_no_disfraza_referencia_antigua(self):
        for now in [datetime(2026,10,23,21,tzinfo=ZoneInfo('America/Lima')),datetime(2026,10,24,10,tzinfo=ZoneInfo('America/Lima'))]:
            with self.subTest(now=now), patch.object(candidate,'_now',return_value=now):
                data=self.post().get_json()
                self.assertEqual(data['status'],'no_calculable')
                self.assertIsNone(data['probabilidad_h1'])
                self.assertTrue(data['diagnostico']['fechas_cobertura_faltante'])
        self.db.assert_not_called(); self.old.assert_not_called()

    def test_confirmacion_responsable_solo_22_no_certificacion_tecnica(self):
        source=json.loads(candidate.COBERTURA_INFERENCIA.read_text(encoding='utf-8'))
        self.assertEqual(source['sede'],'Central')
        self.assertEqual([r['fecha'] for r in source['jornadas']],['2026-10-22'])
        row=source['jornadas'][0]
        self.assertEqual((row['hora_inicio'],row['hora_fin']),('08:00:00','20:45:00'))
        self.assertEqual(row['tipo_evidencia'],'confirmacion_operativa_del_responsable')
        self.assertIs(row['certificacion_tecnica_independiente'],False)
        p=candidate._perfil_inferencia(self.profile)
        self.assertEqual(p['confirmed']-self.profile['confirmed'],{date(2026,10,22)})
        self.assertEqual(p['cut'],date(2026,10,21))
        self.assertEqual(p['inference_cut'],date(2026,10,22))
        self.assertNotIn(date(2026,10,23),p['confirmed'])

    def test_referencia_22_objetivo_23_cortes_separados_sin_fuga(self):
        paths=list(candidate.DIRECTORY.iterdir())
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
        self.cursor.fetchall.return_value=self.rows+[(date(2026,10,21),3)]
        now=datetime(2026,10,22,21,tzinfo=ZoneInfo('America/Lima'))
        with patch.object(candidate,'_now',return_value=now):
            data=self.post().get_json()
        self.assertEqual(data['status'],'success')
        self.assertEqual(data['fecha_referencia'],'2026-10-22')
        self.assertEqual(data['fecha_objetivo'],'2026-10-23')
        self.assertEqual(data['corte_variables'],'2026-10-21')
        self.assertEqual(data['corte_datos_confirmados'],'2026-10-22')
        self.assertEqual(data['corte_entrenamiento'],'2026-10-21')
        self.assertEqual(data['metadatos_modelo']['ultima_referencia_entrenamiento'],'2026-10-20')
        self.assertEqual(data['metadatos_modelo']['ultimo_objetivo_entrenamiento'],'2026-10-21')
        self.assertIs(data['evidencia_cobertura_inferencia'][0]['certificacion_tecnica_independiente'],False)
        sql,params=self.cursor.execute.call_args.args
        self.assertIn('FechaHora < ?',sql)
        self.assertEqual(params[2:4],('2026-09-22','2026-10-22'))
        expected=self.profile['recipe'].feature_row(dict(self.cursor.fetchall.return_value),date(2026,10,22),date(2026,10,23),self.profile['confirmed'])
        frame=self.model.predict_proba.call_args.args[0]
        for name,value in expected.items():
            self.assertAlmostEqual(frame.iloc[0][name],value,places=10)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()})
        self.conn.commit.assert_not_called();self.old.assert_not_called()

    def test_datos_de_referencia_22_y_objetivo_23_no_entran_en_variables(self):
        now=datetime(2026,10,22,21,tzinfo=ZoneInfo('America/Lima'))
        for day in (date(2026,10,22),date(2026,10,23)):
            self.cursor.fetchall.return_value=self.rows+[(day,9999)]
            with patch.object(candidate,'_now',return_value=now):
                self.assertEqual(self.post().get_json()['status'],'no_calculable')
        self.model.predict_proba.assert_not_called()

    def test_fuente_cobertura_invalida_bloquea_sin_bd(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cobertura.json'
            source=json.loads(candidate.COBERTURA_INFERENCIA.read_text(encoding='utf-8'))
            for change in ({'sede':'Tarma'}, {'jornadas':[dict(source['jornadas'][0],hora_fin='20:00:00')]},
                           {'jornadas':[dict(source['jornadas'][0],certificacion_tecnica_independiente=True)]}):
                path.write_text(json.dumps({**source,**change}),encoding='utf-8')
                with patch.object(candidate,'COBERTURA_INFERENCIA',path):
                    self.assertEqual(self.post().get_json()['status'],'no_calculable')
        self.db.assert_not_called();self.old.assert_not_called()

    def test_objetivo_salta_fin_de_semana_calendario_congelado(self):
        p=dict(self.profile); p['cut']=date(2026,10,23)
        p['confirmed']=self.profile['confirmed']|{date(2026,10,22),date(2026,10,23)}
        p['states']={**p['states'],date(2026,10,22):'atencion_segun_calendario',date(2026,10,23):'atencion_segun_calendario'}
        now=datetime(2026,10,24,10,tzinfo=ZoneInfo('America/Lima'))
        ref=candidate._referencia_automatica(p,now)
        self.assertEqual(ref,date(2026,10,23))
        self.assertEqual(candidate._dates(p,ref,now)[1],date(2026,10,26))

    def test_cobertura_desconocida_en_ventana_no_infiere(self):
        p=dict(self.profile);p['states']=dict(p['states']);p['states'].pop(date(2026,10,19))
        self.loader.return_value=p
        self.assertEqual(self.post().get_json()['status'],'no_calculable')
        self.db.assert_not_called();self.model.predict_proba.assert_not_called()

    def test_historial_insuficiente_y_sin_ficha_no_son_negativos(self):
        self.cursor.fetchall.return_value=self.rows[:2]
        data=self.post().get_json()
        self.assertEqual(data['status'],'no_calculable')
        self.assertEqual(data['diagnostico']['dias_activos_validos']['30'],2)
        self.assertIsNone(data['probabilidad_h1'])
        self.cursor.fetchone.return_value=None
        data=self.post().get_json()
        self.assertEqual(data['status'],'no_calculable')
        self.assertIn('No se encontró',data['mensaje'])
        self.model.predict_proba.assert_not_called();self.old.assert_not_called()

    def test_fuente_futura_y_referencia_fuera_de_variables(self):
        for day in [date(2026,10,21),date(2026,10,22),date(2026,10,23)]:
            self.cursor.fetchall.return_value=self.rows+[(day,999)]
            self.assertEqual(self.post().get_json()['status'],'no_calculable')
        self.model.predict_proba.assert_not_called()

    def test_filtros_sql_parametrizados_y_ausencia_de_escrituras(self):
        self.post()
        sql,params=self.cursor.execute.call_args.args
        self.assertIn('ISNULL(EsPrueba, 0) = 0',sql)
        self.assertIn('FechaHora < ?',sql)
        self.assertEqual(params[1:4],('Central','2026-09-21','2026-10-21'))
        self.assertNotIn('FICTICIO',sql)
        for call in self.cursor.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().startswith('SELECT'))
            self.assertNotIn('DNI',call.args[0])
        self.conn.commit.assert_not_called()

    def test_integridad_no_disponible_no_fallback_ni_bd(self):
        self.loader.side_effect=candidate.CandidatoNoDisponible('No exponer detalles')
        data=self.post().get_json()
        self.assertEqual(data['status'],'no_calculable')
        self.assertIsNone(data['probabilidad_h1'])
        self.assertNotIn('No exponer',str(data))
        self.db.assert_not_called();self.old.assert_not_called()

    def test_probabilidades_invalidas_no_se_presentan(self):
        for probs in ([[float('nan'),.7]],[[.8,.7]],[[-.2,1.2]]):
            self.model.predict_proba.return_value=np.array(probs)
            data=self.post().get_json()
            self.assertEqual(data['status'],'no_calculable')
            self.assertIsNone(data['probabilidad_h1'])
            self.old.assert_not_called()

    def test_error_bd_y_inesperado_no_filtra_datos(self):
        for error in (ConnectionError('FICTICIO-SECRETO'),RuntimeError('FICTICIO-SECRETO')):
            with patch.object(routes,'predecir_h1_actual',side_effect=error):
                data=self.post().get_json()
            self.assertEqual(data['status'],'no_calculable')
            self.assertNotIn('FICTICIO-SECRETO',str(data));self.assertIsNone(data['probabilidad_h1'])

    def test_roles_csrf_y_sede_siguen_protegidos(self):
        for role,sede in [('Consultor','Central'),('Supervisor','Tarma'),('Supervisor',''),('Operador','Central')]:
            self.session(role,sede)
            self.assertEqual(self.post().status_code,403)
        self.session('SuperAdmin');self.assertEqual(self.post(csrf=False).status_code,403)
        self.db.assert_not_called()

    def test_supervisor_codigo_enmascarado_y_administrador_completo(self):
        self.session('Supervisor')
        self.assertNotIn('FICTICIO1234',str(self.post().get_json()))
        self.session('Administrador')
        self.assertEqual(self.post().get_json()['codigo_consultado'],'FICTICIO1234')

    def test_metadatos_reales_sin_cargar_base(self):
        with patch.object(candidate,'_profile',return_value=self.profile):
            data=self.client.get('/admin/api/ml/retorno-informacion').get_json()['metadatos']
        self.assertEqual(data['algoritmo'],'HistGradientBoostingClassifier')
        self.assertEqual(data['corte_entrenamiento'],'2026-10-21')
        self.assertEqual(data['ultima_referencia_entrenamiento'],'2026-10-20')
        self.assertEqual(data['ultimo_objetivo_entrenamiento'],'2026-10-21')
        self.assertEqual(data['artefacto_sha256'],hashlib.sha256((candidate.DIRECTORY/'modelo_retorno_h1_ventanas_experimental.joblib').read_bytes()).hexdigest())
        self.old_loader.assert_not_called()

    def test_html_real_unico_flujo_sin_fechas_ni_selectores(self):
        html=self.html();section=html.split('id="seccion-retorno"',1)[1].split('{% endblock %}',1)[0]
        for removed in ('retorno-modelo','retorno-fecha-ref','Ambos selectores','Próximos','Archivo histórico del modelo base','GradientBoostingClassifier · Experimental local'):
            self.assertNotIn(removed,html)
        for present in ('Calcular retorno','HistGradientBoostingClassifier','2026-10-20','2026-10-21','retorno-detalles-tecnicos','aria-live="polite"','Experimental · pendiente de validación prospectiva'):
            self.assertIn(present,section)
        self.assertIn('sm:flex-row',section);self.assertIn('min-w-0',section)

    def test_html_integridad_fallida_no_oculta_estado(self):
        with patch.object(routes,'informacion_h1_actual',return_value={'disponible':False}),patch.object(routes,'obtener_informacion_modelo',return_value={}):
            response=self.client.get('/admin/ml')
        self.assertIn('No calculable',response.get_data(as_text=True));self.old_loader.assert_not_called()

    def test_javascript_renderizado_tarjeta_accesible_y_peticion_real(self):
        node=shutil.which('node');self.assertIsNotNone(node)
        scripts='\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>',self.html(),re.S))
        result=subprocess.run([node,'--check'],input=scripts,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        functions='function mostrarRetornoH1'+scripts.split('function mostrarRetornoH1',1)[1].split('    function escaparHTML',1)[0]
        harness=r'''
const assert=require('node:assert/strict');
const elements=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,{value:'FICTICIO1234',disabled:false,innerHTML:'',textContent:'',attrs:{},classList:{remove(){},add(){}},setAttribute(k,v){this.attrs[k]=v}});return elements.get(id)},querySelector(){return {getAttribute(){return 'csrf-ficticio'}}}};
function escaparHTML(v){return String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
let cancel,cleared=0,mode='success';
function setTimeout(cb,ms){assert.equal(ms,30000);cancel=cb;return 1;}
function clearTimeout(){cleared++;}
async function fetch(url,options){
 assert.equal(url,'/admin/api/ml/predecir-retorno');assert.equal(options.method,'POST');
 assert.deepEqual(JSON.parse(options.body),{codigo_matricula:'FICTICIO1234'});
 assert.equal(options.headers['X-CSRFToken'],'csrf-ficticio');
 assert.equal(document.getElementById('resultado-retorno-box').attrs['aria-busy'],'true');
 assert.ok(document.getElementById('retorno-contenido').textContent.includes('Verificando'));
 if(mode==='timeout'){cancel();assert.ok(options.signal.aborted);throw Object.assign(new Error(),{name:'AbortError'});}
 if(mode==='error')return {ok:false,json:async()=>({mensaje:'Acceso denegado'})};
 return {ok:true,json:async()=>({status:'success',codigo_consultado:'FICTICIO1234',probabilidad_h1:.7,fecha_referencia:'2026-10-21',fecha_objetivo:'2026-10-22'})};
}
(async()=>{
 await ejecutarPrediccionRetorno({preventDefault(){}});
 let out=document.getElementById('retorno-contenido').innerHTML;
 assert.ok(out.includes('70.0'));assert.ok(out.includes('width:70.0%'));assert.ok(out.includes('aria-valuenow="70.0"'));assert.ok(out.includes('22/10/2026'));
 for(const prob of [null,NaN,1.2]){mostrarRetornoH1({status:'success',probabilidad_h1:prob});assert.ok(document.getElementById('retorno-contenido').innerHTML.includes('No calculable'));assert.ok(!document.getElementById('retorno-contenido').innerHTML.includes('role="progressbar"'));}
 mostrarRetornoH1({status:'no_calculable',mensaje:'<img src=x onerror=alert(1)>'});assert.ok(!document.getElementById('retorno-contenido').innerHTML.includes('<img'));
 mode='timeout';await ejecutarPrediccionRetorno({preventDefault(){}});assert.ok(document.getElementById('retorno-contenido').innerHTML.includes('reintentar'));
 mode='error';await ejecutarPrediccionRetorno({preventDefault(){}});assert.ok(document.getElementById('retorno-contenido').innerHTML.includes('Acceso denegado'));
 assert.equal(document.getElementById('btn-evaluar-retorno').disabled,false);assert.equal(document.getElementById('resultado-retorno-box').attrs['aria-busy'],'false');assert.equal(cleared,3);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run([node],input=functions+harness,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
