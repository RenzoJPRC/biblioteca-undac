"""Informe individual con identificadores ficticios; BD y red bloqueadas."""
import io
import json
import re
import shutil
import subprocess
import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import app
from llm.services import coordinador_asistente as coordinator
from llm.services import consultas_biblioteca as queries
from llm.services.motor_intenciones import sanitizar_contexto

CODE = '9900000001'  # Dato ficticio, no matrícula real.


class TestInformeNatural(unittest.TestCase):
    def setUp(self):
        for target in ('pyodbc.connect', 'pymssql.connect', 'requests.sessions.Session.request'):
            blocker = patch(target, side_effect=AssertionError('Servicio real bloqueado'))
            blocker.start(); self.addCleanup(blocker.stop)
        audit = patch('app.get_db_connection', return_value=None)
        audit.start(); self.addCleanup(audit.stop)
        provider = patch.object(coordinator, 'ProveedorLLM')
        self.provider = provider.start(); self.addCleanup(provider.stop)
        metrics = patch.object(coordinator, 'obtener_gestor_metricas')
        self.metrics = metrics.start(); self.addCleanup(metrics.stop)
        config = patch.object(coordinator, 'obtener_configuracion', return_value=SimpleNamespace(
            esta_disponible=True, proveedor='gemini', modelo='ficticio'))
        self.config = config.start(); self.addCleanup(config.stop)

    def test_informe_no_viaja_a_proveedor_ni_metricas_ni_contexto(self):
        for activo in (False, True):
            self.config.return_value.esta_disponible = activo
            with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value={
                    'mensaje': 'Informe local ficticio', 'total_ingresos': 3}) as query:
                result = coordinator.procesar_pregunta('Dame un informe del alumno ' + CODE, 'SuperAdmin')
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['origen'], 'consulta_segura')
            self.assertTrue(query.call_args.kwargs['informe'])
            self.assertNotIn(CODE, json.dumps(result['contexto']))
        self.provider.assert_not_called(); self.metrics.assert_not_called()
        self.assertNotIn('codigo_matricula', sanitizar_contexto({'codigo_matricula': CODE}))

    def test_consultor_y_supervisor_sin_sede_no_consultan(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo') as query:
            for rol, sede in [('Consultor', 'Central'), ('Supervisor', None), ('Supervisor', 'inventada')]:
                result = coordinator.procesar_pregunta('Informe del alumno ' + CODE, rol, sede)
                self.assertEqual(result['codigo_http'], 403)
            query.assert_not_called()
        self.provider.assert_not_called()

    def test_error_personal_no_registra_identificadores_ni_llama_proveedor(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', side_effect=RuntimeError(CODE)), patch.object(coordinator, 'logger') as logger:
            result = coordinator.procesar_pregunta('Informe del alumno ' + CODE, 'SuperAdmin')
        self.assertEqual(result['codigo_http'], 503)
        self.assertNotIn(CODE, json.dumps(result))
        logger.warning.assert_not_called(); logger.error.assert_not_called()
        self.metrics.assert_not_called(); self.provider.assert_not_called()

    def test_sin_datos_no_inventa_corte_y_cierra_contexto(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(0, None, None, 0, ''), (None, None, 0, 0, 0)]
        cursor.fetchall.return_value = []
        cerrado = []
        @contextmanager
        def source(**kwargs):
            try:
                yield None, cursor
            finally:
                cerrado.append(True)
        with patch.object(queries, 'get_db_cursor', source):
            result = queries.contar_ingresos_alumno_por_codigo(CODE, rol='SuperAdmin', informe=True)
        self.assertEqual(cerrado, [True])
        self.assertIsNone(result['fecha_corte'])
        self.assertIsNone(result['primer_ingreso'])
        self.assertEqual(result['mensual'], [])
        self.assertEqual(result['estado'], 'sin_ingresos_verificables')

    def test_sql_parametrizado_sede_exacta_y_distribuciones(self):
        cursor = MagicMock()
        stamp = datetime(2026, 10, 20, 10)
        cursor.fetchone.side_effect = [(3, stamp, stamp, 1, ''), (stamp, stamp, 3, 0, 3)]
        cursor.fetchall.side_effect = [[('2026-10', 3)], [(1, 3)], [(10, 3)], [('Sala de lectura ficticia', 3)]]
        @contextmanager
        def source(**kwargs):
            yield None, cursor
        with patch.object(queries, 'get_db_cursor', source):
            result = queries.contar_ingresos_alumno_por_codigo(CODE, rol='Supervisor', sede_permitida='Tarma', informe=True)
        self.assertEqual(result['total_ingresos'], 3)
        self.assertEqual(result['salas'], [{'grupo': 'Sala de lectura ficticia', 'visitas': 3}])
        self.assertEqual(result['fecha_corte'], stamp.date().isoformat())
        consulta_salas = cursor.execute.call_args_list[-1].args[0]
        self.assertIn('LEFT JOIN Salas S', consulta_salas)
        self.assertIn('S.NombreSala', consulta_salas)
        self.assertIn('S.Sede = R.Sede', consulta_salas)
        for call in cursor.execute.call_args_list:
            sql, params = call.args
            self.assertTrue(sql.lstrip().startswith('SELECT'))
            self.assertNotIn(CODE, sql)
            self.assertIn('Tarma', params)
            self.assertIn('EsPrueba', sql)
            self.assertIn('Sede = ?', sql)
            self.assertNotIn("ISNULL(R.Sede, 'Central')", sql)
            self.assertNotIn('DNI', sql)
            self.assertNotIn('NombreCompleto', sql)

    def test_sesion_prevalece_sobre_json_y_pregunta(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(admin_user='ficticio', admin_rol='Supervisor', admin_sede='Tarma', csrf_token='test')
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value={'mensaje': 'local'}) as query:
            response = client.post('/admin/api/asistente/consultar', headers={'X-CSRFToken': 'test'}, json={
                'pregunta': 'Informe del alumno ' + CODE + ' en Central',
                'rol': 'SuperAdmin', 'sede_permitida': 'Central', 'sede': 'Central'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(query.call_args.kwargs['rol'], 'Supervisor')
        self.assertEqual(query.call_args.kwargs['sede_permitida'], 'Tarma')
        with client.session_transaction() as session:
            self.assertNotIn(CODE, json.dumps(dict(session)))

    def test_ambiguedad_y_seguimiento_sin_conservar_identificador(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value={'mensaje': 'local'}) as query:
            first = coordinator.procesar_pregunta('Informe del alumno ' + CODE + ' en junio', 'SuperAdmin')
            self.assertEqual(first['status'], 'requires_input'); query.assert_not_called()
            second = coordinator.procesar_pregunta('en junio de 2026', 'SuperAdmin', contexto=first['contexto'])
            self.assertEqual(second['status'], 'requires_input')
            self.assertIn('matrícula', second['mensaje'])
            third = coordinator.procesar_pregunta(CODE, 'SuperAdmin', contexto=second['contexto'])
            self.assertEqual(third['status'], 'success')
            self.assertEqual(query.call_args.kwargs['fecha_inicio'], '2026-06-01')
        self.provider.assert_not_called()

    def test_proveedor_general_activo_desactivado_y_fallback(self):
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value={
                'total_ingresos': 4, 'fecha_inicio': '2026-10-01', 'fecha_fin': '2026-10-02'}):
            self.provider.return_value.procesar_consulta.return_value = {
                'status': 'success', 'origen': 'llm_externo', 'mensaje': 'Agregado ficticio'}
            question = 'Resumen de ingresos del 2026-10-01 al 2026-10-02'
            self.assertEqual(coordinator.procesar_pregunta(question, 'SuperAdmin')['origen'], 'llm_externo')
            self.provider.return_value.procesar_consulta.side_effect = coordinator.LLMTimeoutError('fallo ficticio')
            result = coordinator.procesar_pregunta(question, 'SuperAdmin')
            self.assertNotEqual(result['origen'], 'llm_externo')
            self.config.return_value.esta_disponible = False
            self.provider.reset_mock()
            coordinator.procesar_pregunta(question, 'SuperAdmin')
            self.provider.assert_not_called()

    def test_plantilla_y_sintaxis_javascript(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(admin_user='ficticio', admin_rol='SuperAdmin')
        for activo in (False, True):
            with patch('llm.services.proveedor_llm.obtener_configuracion', return_value=SimpleNamespace(
                    esta_disponible=activo, proveedor='gemini', modelo='ficticio')):
                response = client.get('/admin/asistente')
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn('Procesando su consulta localmente', html)
            self.assertNotIn('Biblioteca — Modo Local', html)
            self.assertIn('Filtros adicionales (opcional, uso secundario)', html)
            scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S))
            checked = subprocess.run([shutil.which('node'), '--check'], input=scripts, text=True, capture_output=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == '__main__':
    unittest.main()
