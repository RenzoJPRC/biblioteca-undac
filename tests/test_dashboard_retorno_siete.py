"""Dashboard de siete días: artefacto real de solo lectura, BD sustituida."""
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date,datetime
from pathlib import Path
from unittest.mock import MagicMock,patch
from zoneinfo import ZoneInfo

import numpy as np

from app import app
from routes import admin_ml as routes
from ml.services import retorno_7_operativos_experimental as service


class TestDashboardSieteOperativos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=service._profile()

    def setUp(self):
        self.client=app.test_client()
        with self.client.session_transaction() as s:
            s.update(admin_user='operador-prueba',admin_rol='SuperAdmin',admin_sede='Central',csrf_token='csrf-prueba')
        for target in ['db.get_db_connection','pyodbc.connect','pymssql.connect']:
            p=patch(target,side_effect=AssertionError('BD real bloqueada'))
            p.start(); self.addCleanup(p.stop)
        p=patch('app.get_db_connection',side_effect=AssertionError('Auditoría BD bloqueada'))
        self.audit=p.start(); self.addCleanup(p.stop)
        p=patch.object(service,'_now',return_value=datetime(2026,10,22,10,0,tzinfo=ZoneInfo('America/Lima')))
        p.start(); self.addCleanup(p.stop)
        self.conn=MagicMock(); self.cursor=self.conn.cursor.return_value
        self.cursor.fetchone.return_value=(987654321,)
        self.rows=[(date(2026,9,22),1),(date(2026,9,23),2),(date(2026,9,24),3)]
        self.cursor.fetchall.return_value=self.rows
        p=patch.object(service,'get_db_connection',return_value=self.conn)
        self.db=p.start(); self.addCleanup(p.stop)
        self.model=MagicMock(); self.model.classes_=np.array([0,1]); self.model.predict_proba.return_value=np.array([[.25,.75]])
        profile={**self.profile,'model':self.model}
        p=patch.object(service,'_profile',return_value=profile)
        self.loader=p.start(); self.addCleanup(p.stop)

    def post(self,fecha=None,csrf=True,**extra):
        return self.client.post('/admin/api/ml/retorno-7-operativos',
            json={'codigo_matricula':'CODIGO-PRUEBA','fecha_referencia':fecha,**extra},
            headers={'X-CSRFToken':'csrf-prueba'} if csrf else {})

    def test_probabilidad_sin_clase_corte_y_constructor_exacto(self):
        response=self.post(sede='Tarma',sala_id=9999)
        self.assertEqual(response.status_code,200)
        data=response.get_json()
        self.assertEqual(data['algoritmo'],'GradientBoostingClassifier')
        self.assertEqual(data['probabilidad'],.75)
        for key in ['clase_predicha','umbral','probabilidad_h1','codigo_mascarado','AlumnoID']:
            self.assertNotIn(key,data)
        self.assertNotIn('CODIGO-PRUEBA',str(data)); self.assertNotIn('987654321',str(data))
        self.assertEqual(data['ultima_referencia_entrenamiento'],'2026-09-08')
        self.assertEqual(data['ultimo_objetivo_entrenamiento'],'2026-09-17')
        self.assertEqual(data['corte_variables'],'2026-10-21')
        self.assertEqual(data['fecha_referencia'],'2026-10-22')
        self.assertEqual(data['fechas_objetivo'],['2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02'])
        sql,params=self.cursor.execute.call_args.args
        self.assertTrue(sql.startswith('SELECT')); self.assertIn('ISNULL(EsPrueba,0)=0',sql)
        self.assertEqual(params[1:4],('Central','2026-09-22','2026-10-22'))
        frame=self.model.predict_proba.call_args.args[0]
        expected,_=self.profile['recipe'].features(dict(self.rows),date(2026,10,22),self.profile['confirmed'])
        self.assertEqual(list(frame.columns),self.profile['recipe'].FEATURES)
        for k,v in expected.items(): self.assertAlmostEqual(frame.iloc[0][k],v,places=10)
        self.assertEqual(frame.iloc[0]['dias_activos_7d'],0)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.conn.commit.assert_not_called(); self.conn.close.assert_called_once()
        self.audit.assert_not_called()

    def test_historial_insuficiente_no_infiere(self):
        self.cursor.fetchall.return_value=self.rows[:2]
        data=self.post().get_json()
        self.assertEqual(data['status'],'historial_insuficiente')
        self.assertIsNone(data['probabilidad'])
        self.assertEqual(data['dias_activos_validos'],{'7':0,'14':0,'30':2})
        self.model.predict_proba.assert_not_called()

    def test_alumno_sin_ficha_no_es_negativo(self):
        self.cursor.fetchone.return_value=None
        data=self.post().get_json()
        self.assertEqual(data['status'],'historial_insuficiente'); self.assertIsNone(data['probabilidad'])
        self.model.predict_proba.assert_not_called()

    def test_cobertura_desconocida_y_posterior_corte_sin_consulta(self):
        for ref,missing in [('2026-10-12','2026-09-18'),('2026-10-21','2026-10-20')]:
            if ref=='2026-10-21':
                self.loader.return_value={**self.profile,'cut':date(2026,10,19),'model':self.model}
            data=self.post(ref).get_json()
            self.assertEqual(data['status'],'cobertura_desconocida')
            self.assertIn(missing,data['fechas_cobertura_faltante']); self.assertIsNone(data['probabilidad'])
        self.db.assert_not_called(); self.model.predict_proba.assert_not_called()

    def test_fuente_fuera_ventana_no_infiere(self):
        for forbidden in [date(2026,10,22),date(2026,10,23),date(2026,10,30)]:
            self.cursor.fetchall.return_value=self.rows+[(forbidden,999)]
            self.assertEqual(self.post().status_code,400)
        self.model.predict_proba.assert_not_called()

    def test_ref_futura_y_cerrada_rechazadas(self):
        for ref in ['2026-10-23','2026-10-18','no-fecha',123]:
            self.assertEqual(self.post(ref).status_code,400)
        self.db.assert_not_called()

    def test_roles_sesion_y_csrf(self):
        for role,sede,expected in [('Consultor','Central',403),('Invitado','Central',403),
                                   ('Supervisor','Tarma',403),('Supervisor','',403),
                                   ('Supervisor','Central',200),('Administrador','Central',200)]:
            with self.client.session_transaction() as s: s.update(admin_rol=role,admin_sede=sede)
            self.assertEqual(self.post().status_code,expected)
        self.assertEqual(self.post(csrf=False).status_code,403)
        self.audit.assert_not_called()
        self.model.predict_proba.reset_mock()
        with self.client.session_transaction() as s: s.clear()
        self.assertIn(self.post().status_code,[302,401,403])
        self.model.predict_proba.assert_not_called()

    def test_carga_invalida_no_fallback_ni_bd(self):
        self.loader.side_effect=service.CandidatoNoDisponible('No disponible')
        with patch.object(routes,'predecir_retorno_alumno') as original:
            self.assertEqual(self.post().status_code,503)
            original.assert_not_called()
        self.db.assert_not_called()

    def test_error_bd_sin_datos_personales_ni_auditoria(self):
        self.cursor.fetchall.side_effect=RuntimeError('CODIGO-PRUEBA 987654321')
        with redirect_stdout(io.StringIO()) as output:
            response=self.post()
        self.assertEqual(response.status_code,503)
        for secret in ['CODIGO-PRUEBA','987654321']:
            self.assertNotIn(secret,response.get_data(as_text=True)+output.getvalue())
        self.cursor.close.assert_called_once(); self.conn.close.assert_called_once()
        self.audit.assert_not_called(); self.conn.commit.assert_not_called()

    def test_carga_real_e_integridad_archivos_requeridos(self):
        # El setUpClass cargó y comprobó el artefacto real antes de sustituir el predictor.
        self.assertEqual(type(self.profile['model']).__name__,'GradientBoostingClassifier')
        source=service.DIRECTORY
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)
            for name in ['manifest.json',service.ARTIFACT,'metadatos_candidato.json','preflight.json',
                         'constructor_congelado.py','calendario_aplicado.csv','reglas_calendario_fuente.py']:
                shutil.copyfile(service.DIRECTORY/name,dest/name)
            (dest/'calendario_aplicado.csv').write_text('alterado',encoding='utf-8')
            # Recuperar la función real de carga, no el mock de la instancia.
            with patch.object(service,'DIRECTORY',dest):
                with self.assertRaises(service.CandidatoNoDisponible):
                    self.real_loader()
                shutil.copyfile(source/'calendario_aplicado.csv',dest/'calendario_aplicado.csv')
                (dest/service.ARTIFACT).unlink()
                with self.assertRaises(service.CandidatoNoDisponible):
                    self.real_loader()

    real_loader=staticmethod(service._profile)

    def test_consulta_nueva_preserva_respuesta_modelo_actual(self):
        original={'status':'success','probabilidad_retorno':.25,'campos_originales':[1,2]}
        with patch.object(routes,'predecir_retorno_alumno',return_value=original), \
             patch('app.get_db_connection',return_value=None):
            def current():
                return self.client.post('/admin/api/ml/predecir-retorno',json={'codigo_matricula':'CODIGO-PRUEBA'},headers={'X-CSRFToken':'csrf-prueba'}).get_json()
            self.assertEqual(current(),original)
            self.assertEqual(self.post().get_json()['probabilidad'],.75)
            self.assertEqual(current(),original)

    def test_pantalla_y_javascript_sin_clasificacion_binaria(self):
        getter=MagicMock(); getter.metadatos={}
        with patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter), \
             patch.object(routes,'obtener_informacion_modelo',return_value={}):
            html=self.client.get('/admin/ml').get_data(as_text=True)
        self.assertNotIn('id="retorno-siete-experimental"',html)
        self.assertNotIn('form-retorno-siete',html)
        self.assertNotIn('consultarRetornoSiete',html)
        self.assertNotIn('/admin/api/ml/retorno-7-operativos',html)
        self.assertIn('<option value="actual" selected>',html)
        scripts='\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>',html,re.S))
        node=shutil.which('node'); self.assertIsNotNone(node)
        checked=subprocess.run([node,'--check'],input=scripts,text=True,capture_output=True)
        self.assertEqual(checked.returncode,0,checked.stderr)



if __name__=='__main__': unittest.main()
