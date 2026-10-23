"""Coordinación y correo simulado. Nunca BD, SMTP ni proveedores reales."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from app import app
from llm.services import agentes_asistente as agents
from llm.services import coordinador_asistente as coordinator
from llm.services.metricas_asistente import GestorMetricasAsistente


def resumen():
    return dict(status='success', fecha_inicio='2026-10-01', fecha_fin='2026-10-02',
                total_ingresos=3, ingresos_por_sede={'Tarma': 3}, ingresos_por_tipo_persona={'Alumno': 3})


def individual():
    return dict(mensaje='Informe ficticio', fuente='RegistroIngresos + Alumnos', fecha_corte='2026-10-20',
                total_ingresos=2, dias_con_visita=1, primer_ingreso='2026-10-20T09:00:00',
                ultimo_ingreso='2026-10-20T10:00:00', mensual=[dict(grupo='2026-10', visitas=2)],
                dias_semana=[dict(grupo=1, visitas=2)], horarios=[dict(grupo=10, visitas=2)],
                salas=[dict(grupo=1, visitas=2)])


class TestAgentesAsistente(unittest.TestCase):
    def setUp(self):
        for target in ('pyodbc.connect', 'pymssql.connect', 'requests.sessions.Session.request'):
            blocker = patch(target, side_effect=AssertionError('Servicio real bloqueado'))
            blocker.start(); self.addCleanup(blocker.stop)
        p = patch('app.get_db_connection', return_value=None); p.start(); self.addCleanup(p.stop)
        p = patch.object(agents, 'telemetria', agents.TelemetriaAgentes()); p.start(); self.addCleanup(p.stop)
        self.client = app.test_client()
        self.session('Supervisor', 'Tarma')

    def session(self, rol, sede):
        with self.client.session_transaction() as sesion:
            sesion.update(admin_user='ficticio', admin_rol=rol, admin_sede=sede, csrf_token='token-test')

    def post(self, ruta, datos):
        return self.client.post('/admin/api/asistente/' + ruta, json=datos, headers={'X-CSRFToken': 'token-test'})

    def test_delegacion_real_herramienta_y_auditoria_antes_del_reporte(self):
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value=resumen()) as tool, patch.object(coordinator, 'ProveedorLLM') as externo:
            result = agents.Orquestador().ejecutar('Resumen de ingresos del 2026-10-01 al 2026-10-02', 'Supervisor', 'Tarma')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['agentes'], ['orquestador', 'afluencia', 'auditor', 'reportes_automatizacion'])
        self.assertEqual(result['auditoria'], 'aprobado')
        self.assertEqual(result['reporte']['estado'], 'preparado_no_enviado')
        self.assertEqual(tool.call_args.kwargs['sede_permitida'], 'Tarma')
        externo.assert_not_called()
        self.assertEqual(agents.telemetria.resumen()['tareas']['completadas'], 1)

    def test_personal_local_sin_identificador_en_telemetria(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value=individual()) as tool, patch.object(coordinator, 'ProveedorLLM') as externo:
            result = agents.Orquestador().ejecutar('Informe del alumno 9900000001', 'Supervisor', 'Tarma')
        self.assertEqual(result['auditoria'], 'aprobado')
        self.assertIn('analisis_individual', result['agentes'])
        self.assertTrue(tool.call_args.kwargs['informe'])
        externo.assert_not_called()
        stats = json.dumps(agents.telemetria.resumen())
        for prohibido in ('9900000001', 'Informe ficticio', 'Tarma', 'destinatario', 'mensual'):
            self.assertNotIn(prohibido, stats)

    def test_matricula_sola_y_frases_naturales_no_se_rechazan_como_permiso(self):
        frases = ['9900000001', 'Dame un informe del alumno por su código de matrícula es 9900000001',
                  'El código de matrícula del alumno es 9900000001',
                  'Informe del alumno con matrícula: 9900000001',
                  'Dame un reporte para el alumno cuyo código es 9900000001']
        for rol in ('SuperAdmin', 'Supervisor'):
            self.session(rol, 'Tarma')
            for frase in frases:
                with self.subTest(rol=rol, frase=frase), patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value=individual()) as tool, patch.object(coordinator, 'ProveedorLLM') as externo:
                    result = self.post('reportes/preparar', {'pregunta': frase})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.get_json().get('auditoria'), 'aprobado')
                    self.assertEqual(tool.call_args.kwargs['codigo_matricula'], '9900000001')
                    self.assertTrue(tool.call_args.kwargs['informe'])
                    externo.assert_not_called()

    def test_aclaracion_seguimiento_y_metricas_coherentes(self):
        first = self.post('reportes/preparar', {'pregunta': 'Resumen de ingresos'})
        self.assertEqual(first.get_json()['status'], 'requires_input')
        self.assertIsNone(first.get_json()['reporte'])
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value=resumen()):
            second = self.post('reportes/preparar', {'pregunta': 'del 2026-10-01 al 2026-10-02'})
        self.assertEqual(second.get_json().get('auditoria'), 'aprobado')
        malo = resumen(); malo['total_ingresos'] = 9
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value=malo):
            third = self.post('reportes/preparar', {'pregunta': 'Resumen de ingresos del 2026-10-01 al 2026-10-02'})
        self.assertEqual(third.status_code, 422)
        self.assertEqual(third.get_json()['auditoria'], 'rechazado')
        with patch.object(coordinator, 'obtener_resumen_ingresos', side_effect=RuntimeError('fallo ficticio')):
            fourth = self.post('reportes/preparar', {'pregunta': 'Resumen de ingresos del 2026-10-01 al 2026-10-02'})
        self.assertEqual(fourth.status_code, 503)
        stats = agents.telemetria.resumen()
        self.assertEqual(stats['tareas'], dict(recibidas=4, completadas=1, rechazadas=1, fallidas=1, pendientes=1))
        self.assertEqual(stats['reportes_generados'], 1)
        self.assertEqual(stats['agentes']['afluencia']['fallos'], 1)

    def test_sin_ingresos_no_prepara_informe_validado(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo', return_value={
                'mensaje': 'No hay ingresos verificables.', 'estado': 'sin_ingresos_verificables'}), patch.object(agents.AgenteReportesAutomatizacion, 'componer') as componer:
            result = self.post('reportes/preparar', {'pregunta': 'Informe del alumno 9900000001'})
        self.assertEqual(result.get_json()['status'], 'requires_input')
        self.assertIsNone(result.get_json()['reporte'])
        componer.assert_not_called()
        self.assertEqual(agents.telemetria.resumen()['reportes_generados'], 0)

    def test_auditor_rechaza_total_inconsistente_y_no_compone(self):
        malo = resumen(); malo['total_ingresos'] = 999
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value=malo), patch.object(agents.AgenteReportesAutomatizacion, 'componer') as componer:
            result = agents.Orquestador().ejecutar('Resumen de ingresos del 2026-10-01 al 2026-10-02', 'Supervisor', 'Tarma')
        self.assertEqual(result['codigo_http'], 422); componer.assert_not_called()
        self.assertIsNone(result['reporte'])
        self.assertEqual(agents.telemetria.resumen()['tareas']['rechazadas'], 1)

    def test_auditor_rechaza_sede_y_corte(self):
        response = dict(status='success', origen='consulta_segura', intencion='conteo_ingresos_alumno', sede_aplicada='Tarma', datos=individual())
        response['datos']['fecha_corte'] = '2026-10-01'
        with self.assertRaises(agents.RechazoAuditoria): agents.AgenteAuditor().validar(response, 'Supervisor', 'Tarma')
        response['datos'] = individual(); response['sede_aplicada'] = 'Central'
        with self.assertRaises(agents.RechazoAuditoria): agents.AgenteAuditor().validar(response, 'Supervisor', 'Tarma')

    def test_especialista_afluencia_no_ejecuta_herramientas_personales(self):
        with patch.object(agents, 'procesar_pregunta') as tool:
            result = agents.AgenteAfluencia().ejecutar('Informe del alumno 9900000001', 'SuperAdmin', None)
        self.assertEqual(result['status'], 'requires_input')
        tool.assert_not_called()

    def test_prediccion_sin_cobertura_rechazada(self):
        response = dict(status='success', origen='machine_learning', intencion='prediccion_afluencia', sede_aplicada='Central',
                        datos={'consulta': {'ultima_fecha_disponible': '2026-09-01', 'fecha': '2026-10-22',
                                           'fuente_historial': 'fuente ficticia', 'modo_demostracion': True}})
        with self.assertRaises(agents.RechazoAuditoria):
            agents.AgenteAuditor().validar(response, 'SuperAdmin', None)

    def test_consultor_y_supervisor_sin_sede(self):
        with patch.object(coordinator, 'contar_ingresos_alumno_por_codigo') as tool:
            for rol, sede in [('Consultor', 'Central'), ('Supervisor', None), ('Supervisor', 'invalida')]:
                result = agents.Orquestador().ejecutar('Informe del alumno 9900000001', rol, sede)
                self.assertEqual(result['codigo_http'], 403)
            tool.assert_not_called()

    def test_fallo_de_herramienta_y_telemetria_sin_error_crudo(self):
        with patch.object(agents.AgenteAfluencia, 'ejecutar', side_effect=RuntimeError('9900000001')):
            result = agents.Orquestador().ejecutar('Resumen de ingresos', 'SuperAdmin')
        self.assertEqual(result['codigo_http'], 503)
        self.assertNotIn('9900000001', json.dumps(result))
        self.assertEqual(agents.telemetria.resumen()['agentes']['afluencia']['fallos'], 1)

    def test_endpoint_no_confia_en_rol_sede_o_resultado_del_cliente(self):
        with patch.object(coordinator, 'obtener_resumen_ingresos', return_value=resumen()) as tool:
            result = self.post('reportes/preparar', dict(pregunta='Resumen de ingresos del 2026-10-01 al 2026-10-02 en Central', rol='SuperAdmin', sede='Central', aprobado=True))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(tool.call_args.kwargs['sede_permitida'], 'Tarma')
        sin_csrf = self.client.post('/admin/api/asistente/reportes/preparar', json={'pregunta': 'Resumen'})
        self.assertEqual(sin_csrf.status_code, 403)

    def test_borrador_no_activo_destinatario_autorizado_y_personal_bloqueado(self):
        with patch.dict(os.environ, {'LLM_DESTINATARIOS_AUTORIZADOS': 'reportes@example.invalid'}):
            result = self.post('tareas/borrador', dict(tipo='afluencia', frecuencia='semanal', destinatario='reportes@example.invalid', activar=True))
            self.assertEqual(result.status_code, 200)
            draft = result.get_json()['borrador']
            self.assertFalse(draft['activa']); self.assertIsNone(draft['proxima_ejecucion'])
            for destino, tipo in [('otro@example.invalid', 'afluencia'), ('reportes@example.invalid', 'individual')]:
                self.assertEqual(self.post('tareas/borrador', dict(tipo=tipo, frecuencia='diaria', destinatario=destino)).status_code, 403)
        self.session('Consultor', 'Central')
        with patch.object(agents.AgenteReportesAutomatizacion, 'borrador') as preparar:
            denegada = self.post('tareas/borrador', dict(tipo='afluencia', frecuencia='diaria', destinatario='reportes@example.invalid'))
        # El guard global existente devuelve status:error con HTTP 200 para POST
        # de Consultor; mantener sus permisos y demostrar que no llega al agente.
        self.assertEqual(denegada.get_json()['status'], 'error')
        preparar.assert_not_called()

    def test_smtp_simulado_reintentos_hilos_y_timeout_incierto(self):
        transporte = MagicMock(); transporte.es_simulado = True
        enviados = set()
        with ThreadPoolExecutor(max_workers=4) as pool:
            estados = list(pool.map(lambda _: agents.simular_correo({}, 'reportes@example.invalid', transporte, 'ensayo-1', enviados), range(8)))
        self.assertEqual(estados.count('simulado'), 1); transporte.enviar.assert_called_once()
        transporte.enviar.side_effect = TimeoutError('incierto')
        with self.assertRaises(TimeoutError): agents.simular_correo({}, 'reportes@example.invalid', transporte, 'ensayo-2', enviados)
        self.assertEqual(agents.simular_correo({}, 'reportes@example.invalid', transporte, 'ensayo-2', enviados), 'duplicado_omitido')
        self.assertEqual(transporte.enviar.call_count, 2)
        with self.assertRaises(ValueError): agents.simular_correo({}, 'real@example.org', transporte, 'otra', enviados)

    def test_panel_plantilla_javascript_y_diagnosticos(self):
        # Usar el resumen real vacío con persistencia situada en un temporal.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            gestor = GestorMetricasAsistente(Path(folder)/'metricas.json')
            with patch('llm.services.metricas_asistente.obtener_gestor_metricas', return_value=gestor):
                response = self.client.get('/admin/asistente/metricas')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('no compartida ni permanente', html)
        self.assertNotIn('Endpoint Base (OpenAI Compatible)', html)
        self.assertNotIn('id="borrador-recurrente"', html)
        scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S))
        result = subprocess.run([shutil.which('node'), '--check'], input=scripts, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
