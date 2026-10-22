"""Dashboard H1: BD sustituida, reloj fijo y ningún servicio externo."""
import unittest
import io
import re
import shutil
import subprocess
from contextlib import redirect_stdout
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
        # Solo lee el artefacto local y el constructor congelado, sin BD.
        cls.profile=candidate._profile()

    def setUp(self):
        self.client=app.test_client()
        with self.client.session_transaction() as s:
            s['admin_user']='operador-prueba'
            s['admin_rol']='SuperAdmin'
            s['admin_sede']='Central'
            s['csrf_token']='csrf-prueba'
        self.clock=patch.object(candidate,'_now',return_value=datetime(2026,10,21,22,30,tzinfo=ZoneInfo('America/Lima')))
        self.clock.start(); self.addCleanup(self.clock.stop)
        # Todo acceso real a BD es un fallo de prueba.
        blocked=patch('db.get_db_connection',side_effect=AssertionError('BD real bloqueada'))
        blocked.start(); self.addCleanup(blocked.stop)
        # app.py captura su propio alias y audita POST en after_request.
        # Anularlo explícitamente: ningún INSERT de auditoría durante las pruebas.
        audit=patch('app.get_db_connection',return_value=None)
        audit.start(); self.addCleanup(audit.stop)
        for target in ['pyodbc.connect','pymssql.connect']:
            driver=patch(target,side_effect=AssertionError('Conector real bloqueado'))
            driver.start(); self.addCleanup(driver.stop)
        self.conn=MagicMock()
        self.cursor=self.conn.cursor.return_value
        self.cursor.fetchone.return_value=(123,)
        self.rows=[(date(2026,10,14),2),(date(2026,10,15),3),(date(2026,10,16),4),(date(2026,10,20),5)]
        self.cursor.fetchall.return_value=self.rows
        dbpatch=patch.object(candidate,'get_db_connection',return_value=self.conn)
        dbpatch.start(); self.addCleanup(dbpatch.stop)
        self.model=MagicMock()
        self.model.classes_=np.array([0,1])
        self.model.predict_proba.return_value=np.array([[.3,.7]])
        profile=dict(self.profile)
        profile['model']=self.model
        loader=patch.object(candidate,'_profile',return_value=profile)
        loader.start(); self.addCleanup(loader.stop)

    def post(self,model=None,fecha=None,csrf=True):
        payload={'codigo_matricula':'PRUEBA1234'}
        if model is not None:
            payload['modelo']=model
        if fecha is not None:
            payload['fecha_referencia']=fecha
        return self.client.post('/admin/api/ml/predecir-retorno',json=payload,
            headers={'X-CSRFToken':'csrf-prueba'} if csrf else {})

    def test_actual_implicito_explicito_y_tras_candidato_son_identicos(self):
        expected={'status':'success','probabilidad_retorno':.25,'es_experimental':False,'extra_original':{'valor':[1,2]}}
        with patch.object(routes,'predecir_retorno_alumno',return_value=expected) as current:
            self.assertEqual(self.post().get_json(),expected)
            self.assertEqual(self.post('actual').get_json(),expected)
            result=self.post(candidate.SELECTOR)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.get_json()['probabilidad_h1'],.7)
            self.assertEqual(self.post('actual').get_json(),expected)
            self.assertEqual(current.call_count,3)
            for call in current.call_args_list:
                self.assertEqual(call.kwargs,{'codigo_matricula':'PRUEBA1234','alumno_id':None,'fecha_referencia':None})

    def test_constructor_exacto_corte_y_privacidad(self):
        response=self.post(candidate.SELECTOR,'2026-10-21')
        self.assertEqual(response.status_code,200)
        data=response.get_json()
        self.assertEqual(data['corte_variables'],'2026-10-20')
        self.assertEqual(data['corte_entrenamiento'],'2026-10-21')
        self.assertEqual(data['fecha_entrenamiento'],'2026-10-21')
        self.assertEqual(data['fecha_objetivo'],'2026-10-22')
        self.assertEqual(data['estado_validacion'],candidate.LABEL)
        self.assertTrue(data['es_experimental'])
        self.assertNotIn('AlumnoID',data)
        self.assertNotIn('PRUEBA1234',str(data))
        sql,params=self.cursor.execute.call_args.args
        self.assertTrue(sql.startswith('SELECT'))
        self.assertIn('FechaHora < ?',sql)
        self.assertIn('ISNULL(EsPrueba, 0) = 0',sql)
        self.assertEqual(params[1:4],('Central','2026-09-21','2026-10-21'))
        self.assertEqual(len(self.cursor.execute.call_args_list),2)
        expected=self.profile['recipe'].feature_row(dict(self.rows),date(2026,10,21),date(2026,10,22),self.profile['confirmed'])
        passed=self.model.predict_proba.call_args.args[0]
        self.assertEqual(list(passed.columns),self.profile['recipe'].FEATURES)
        for key,value in expected.items():
            self.assertAlmostEqual(passed.iloc[0][key],value,places=10)
        self.conn.commit.assert_not_called()
        self.conn.close.assert_called_once()

    def test_no_usa_fecha_referencia_objetivo_ni_futuras(self):
        for forbidden in [date(2026,10,21),date(2026,10,22),date(2026,10,23)]:
            with self.subTest(day=forbidden):
                self.cursor.fetchall.return_value=self.rows+[(forbidden,999999)]
                self.model.predict_proba.reset_mock()
                self.assertEqual(self.post(candidate.SELECTOR).status_code,400)
                self.model.predict_proba.assert_not_called()

    def test_desconocidos_corte_futuro_y_cerrados_no_infiere(self):
        for ref in ['2026-07-22','2026-07-31','2026-10-18','2026-10-23','2026-08-03']:
            with self.subTest(ref=ref):
                self.model.predict_proba.reset_mock()
                self.assertEqual(self.post(candidate.SELECTOR,ref).status_code,400)
                self.model.predict_proba.assert_not_called()

    def test_cierre_en_ventana_no_aporta_visitas(self):
        self.cursor.fetchall.return_value=self.rows+[(date(2026,10,8),999999)]
        self.assertEqual(self.post(candidate.SELECTOR).status_code,200)
        used=self.model.predict_proba.call_args.args[0]
        self.assertEqual(used.iloc[0]['visitas_30d'],14)

    def test_candidato_insuficiente_y_alumno_inexistente_no_cambian_actual(self):
        self.cursor.fetchall.return_value=self.rows[:2]
        data=self.post(candidate.SELECTOR).get_json()
        self.assertEqual(data['status'],'historial_insuficiente')
        self.assertEqual(data['diagnostico'],{
            'motivo':'historial_reciente_insuficiente',
            'dias_activos_validos':{'7':2,'14':2,'30':2},
            'fechas_cobertura_faltante':[]})
        self.assertNotIn('codigo_mascarado',data)
        self.assertIsNone(data['probabilidad_h1'])
        self.model.predict_proba.assert_not_called()
        self.cursor.fetchone.return_value=None
        self.assertEqual(self.post(candidate.SELECTOR).get_json()['status'],'error')

    def test_cobertura_desconocida_y_posterior_corte_sin_consultar_ni_identificadores(self):
        for ref,motivo,missing in [('2026-07-24','cobertura_desconocida','2026-07-24'),
                                   ('2026-10-01','cobertura_desconocida','2026-09-18'),
                                   ('2026-10-23','posterior_al_corte','2026-10-22')]:
            with self.subTest(ref=ref), redirect_stdout(io.StringIO()) as output:
                response=self.post(candidate.SELECTOR,ref)
                self.assertEqual(response.status_code,400)
                data=response.get_json()
                self.assertEqual(data['modelo_selector'],candidate.SELECTOR)
                self.assertEqual(data['diagnostico']['motivo'],motivo)
                self.assertIn(missing,data['diagnostico']['fechas_cobertura_faltante'])
                self.assertEqual(data['diagnostico']['dias_activos_validos'],{'7':None,'14':None,'30':None})
                self.assertIsNone(data['probabilidad_h1'])
                for secret in ['PRUEBA1234','codigo_mascarado','AlumnoID','DNI','nombre']:
                    self.assertNotIn(secret,response.get_data(as_text=True)+output.getvalue())
        self.cursor.execute.assert_not_called()
        self.model.predict_proba.assert_not_called()

    def test_diagnostico_historial_no_imprime_identificadores(self):
        self.cursor.fetchall.return_value=self.rows[:2]
        with redirect_stdout(io.StringIO()) as output:
            response=self.post(candidate.SELECTOR)
        self.assertNotIn('PRUEBA1234',response.get_data(as_text=True)+output.getvalue())
        self.assertNotIn('codigo_mascarado',response.get_json())

    def test_excepcion_inesperada_candidato_no_registra_detalles(self):
        self.conn.close.side_effect=RuntimeError('PRUEBA1234 identificador privado')
        with redirect_stdout(io.StringIO()) as output:
            response=self.post(candidate.SELECTOR)
        self.assertEqual(response.status_code,500)
        self.assertNotIn('PRUEBA1234',response.get_data(as_text=True)+output.getvalue())

    def test_fallo_candidato_no_hace_fallback(self):
        with patch.object(routes,'predecir_retorno_experimental',side_effect=candidate.CandidatoNoDisponible('Candidato no disponible')), \
             patch.object(routes,'predecir_retorno_alumno') as current:
            self.assertEqual(self.post(candidate.SELECTOR).status_code,503)
            current.assert_not_called()

    def test_consultor_y_csrf_siguen_bloqueando_ambos_selectores(self):
        with self.client.session_transaction() as s:
            s['admin_rol']='Consultor'
        with patch.object(routes,'predecir_retorno_alumno') as current:
            for model in ['actual',candidate.SELECTOR]:
                self.assertEqual(self.post(model).status_code,403)
                self.assertEqual(self.post(model,csrf=False).status_code,403)
            current.assert_not_called()
            self.model.predict_proba.assert_not_called()

    def test_supervisor_candidato_solo_central(self):
        with self.client.session_transaction() as s:
            s['admin_rol']='Supervisor'; s['admin_sede']='Tarma'
        self.assertEqual(self.post(candidate.SELECTOR).status_code,403)
        with self.client.session_transaction() as s:
            s['admin_sede']='Central'
        self.assertEqual(self.post(candidate.SELECTOR).status_code,200)

    def test_selector_invalido_no_invoca_servicios(self):
        with patch.object(routes,'predecir_retorno_alumno') as current:
            self.assertEqual(self.post('otro').status_code,400)
            current.assert_not_called()
            self.model.predict_proba.assert_not_called()

    def test_error_bd_cierra_sin_commit(self):
        self.cursor.fetchall.side_effect=RuntimeError('detalle no debe filtrarse')
        response=self.post(candidate.SELECTOR)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('detalle',response.get_data(as_text=True))
        self.cursor.close.assert_called_once(); self.conn.close.assert_called_once()
        self.conn.commit.assert_not_called()

    def test_informacion_actual_conservada(self):
        getter=MagicMock(); getter.metadatos={'version':'actual-original'}
        with patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter):
            result=self.client.get('/admin/api/ml/retorno-informacion')
        self.assertEqual(result.get_json(),{'status':'success','metadatos':getter.metadatos})

    def test_dashboard_select_actual_y_etiqueta_candidato(self):
        getter=MagicMock(); getter.metadatos={}
        with patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter), \
             patch.object(routes,'obtener_informacion_modelo',return_value={}):
            result=self.client.get('/admin/ml')
        self.assertEqual(result.status_code,200)
        html=result.get_data(as_text=True)
        self.assertIn('<option value="actual" selected>',html)
        self.assertIn('HistGradientBoostingClassifier · Próximo día', html)
        self.assertIn('HistGradientBoostingClassifier · Próximos', html)
        self.assertIn('Experimental · pendiente de validación prospectiva',html)
        self.assertIn('mostrarRetornoExperimental(datos, box, cont)',html)
        self.assertNotIn('id="retorno-historico"',html)
        self.assertNotIn('Archivo histórico del modelo base',html)
        self.assertNotIn('retorno-info-experimental',html)
        self.assertNotIn('Metadatos reales del candidato H1',html)
        self.assertNotIn('[EXPERIMENTAL',html)

    def test_javascript_renderizado_sintaxis_y_diagnostico(self):
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node es necesario para verificar el JavaScript')
        getter=MagicMock(); getter.metadatos={}
        with patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter), \
             patch.object(routes,'obtener_informacion_modelo',return_value={}):
            html=self.client.get('/admin/ml').get_data(as_text=True)
        scripts='\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>',html,re.S))
        check=subprocess.run([node,'--check'],input=scripts,text=True,capture_output=True)
        self.assertEqual(check.returncode,0,check.stderr)
        renderer=scripts.split('    function mostrarRetornoExperimental',1)[1].split('    // Predicción interactiva de retorno',1)[0]
        renderer='function mostrarRetornoExperimental'+renderer
        harness=r'''
const assert = require('node:assert/strict');
function escaparHTML(x) { return String(x).replaceAll('<', '&lt;'); }
const box={}, cont={};

// Caso 1: Historial insuficiente (No calculable, sin porcentajes inventados)
mostrarRetornoExperimental({status:'historial_insuficiente',diagnostico:{
    motivo:'historial_reciente_insuficiente',dias_activos_validos:{7:0,14:1,30:2},
    fechas_cobertura_faltante:[]}},box,cont);
assert.ok(cont.innerHTML.includes('No calculable'));
assert.ok(cont.innerHTML.includes('Historial reciente insuficiente'));
assert.ok(!cont.innerHTML.includes('bg-amber-'));
assert.ok(cont.innerHTML.includes('7 días: 0'));
assert.ok(cont.innerHTML.includes('30 días: 2'));
assert.ok(!cont.innerHTML.includes('%'), 'No debe mostrar porcentajes cuando es no calculable');

// Caso 2: Cobertura no confirmada (No calculable, sin porcentajes)
mostrarRetornoExperimental({status:'cobertura_no_confirmada',diagnostico:{
    motivo:'posterior_al_corte',dias_activos_validos:{7:null,14:null,30:null},
    fechas_cobertura_faltante:['2026-10-22','<no ejecutar>']}},box,cont);
assert.ok(cont.innerHTML.includes('Cobertura posterior al corte confirmado'));
assert.ok(cont.innerHTML.includes('no calculable sin cobertura'));
assert.ok(cont.innerHTML.includes('2026-10-22'));
assert.ok(!cont.innerHTML.includes('<no ejecutar>'));
assert.ok(!cont.innerHTML.includes('%'));

// Caso 3: Éxito calculable (Porcentaje destacado 5xl, fecha objetivo, explicación breve, aviso prospectivo)
mostrarRetornoExperimental({
    status: 'success',
    probabilidad_h1: 0.742,
    probabilidad_porcentaje: '74.2%',
    fecha_objetivo: '2026-10-22',
    fecha_referencia: '2026-10-21',
    corte_datos_confirmados: '2026-10-21',
    corte_entrenamiento: '2026-10-21',
    corte_variables: '2026-10-20',
    version_modelo: 'candidato_h1_logreg_v1',
    fecha_entrenamiento: '2026-10-21',
    dias_activos_30d: 5,
    algoritmo: 'LogisticRegression'
}, box, cont);
assert.ok(cont.innerHTML.includes('text-5xl font-black'), 'Debe incluir porcentaje destacado con clase 5xl bold');
assert.ok(cont.innerHTML.includes('74.2%'));
assert.ok(cont.innerHTML.includes('2026-10-22'));
assert.ok(cont.innerHTML.includes('Probabilidad de al menos un ingreso en el siguiente día operativo'));
assert.ok(cont.innerHTML.includes('Experimental · pendiente de validación prospectiva'));
assert.ok(!cont.innerHTML.includes('[EXPERIMENTAL'));
assert.ok(!cont.innerHTML.includes('bg-amber-'));
assert.ok(!cont.innerHTML.includes('Versión'));
assert.ok(!cont.innerHTML.includes('Fecha de entrenamiento'));
assert.equal((cont.innerHTML.match(/2026-10-22/g) || []).length,1);
assert.ok(cont.innerHTML.includes('Último día utilizado en variables'));
'''
        run=subprocess.run([node],input=renderer+harness,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_rotulos_historicos_ausencia_h7_y_aislamiento_secciones(self):
        getter=MagicMock(); getter.metadatos={}
        with patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter), \
             patch.object(routes,'obtener_informacion_modelo',return_value={}):
            html=self.client.get('/admin/ml').get_data(as_text=True)

        # 1. Ausencia del bloque H7 (7 días) en la interfaz
        self.assertNotIn('retorno-7-operativos', html)
        self.assertNotIn('retorno-siete-experimental', html)
        self.assertNotIn('form-retorno-siete', html)
        self.assertNotIn('consultarRetornoSiete', html)
        self.assertNotIn('GradientBoostingClassifier · Experimental local', html)

        # 2. Secciones aisladas: seccion-afluencia y seccion-individual no contienen la auditoría fija de junio-julio
        self.assertIn('id="seccion-afluencia"', html)
        self.assertIn('id="seccion-individual"', html)
        self.assertIn('id="seccion-retorno"', html)

        sec_afluencia = html.split('id="seccion-afluencia"')[1].split('id="seccion-individual"')[0]
        self.assertNotIn('Auditoría histórica de junio–julio 2026 · cifras de referencia', sec_afluencia)
        self.assertNotIn('18,168', sec_afluencia)
        # En Afluencia, el periodo histórico documentado está explícitamente rotulado
        self.assertIn('Periodo histórico documentado (no es el corte actual)', sec_afluencia)

        sec_individual = html.split('id="seccion-individual"')[1].split('id="seccion-retorno"')[0]
        self.assertNotIn('Auditoría histórica de junio–julio 2026 · cifras de referencia', sec_individual)
        # En Análisis Individual, la línea base histórica está en su propio desplegable con rótulo claro
        self.assertIn('Línea base histórica de referencia · Central · junio–julio 2026', sec_individual)

        sec_retorno = html.split('id="seccion-retorno"')[1]
        self.assertNotIn('id="retorno-historico"',sec_retorno)
        self.assertNotIn('18,168',sec_retorno)
        self.assertNotIn('Auditoría histórica de junio–julio',sec_retorno)



if __name__=='__main__':
    unittest.main()

