"""Regresiones aisladas: conteos agrupados, errores recuperables y referencia H1."""
import copy
import logging
import re
import shutil
import subprocess
import time
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time as hora
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app import app
from ml.services import analisis_individual as service
from ml.services import retorno_h1_experimental as candidate
from routes import admin_ml as routes


class TestRevisionIndividualH1Local(unittest.TestCase):
    def setUp(self):
        service.limpiar_cache_analisis_descriptivo()
        for name in ('pyodbc.connect', 'pymssql.connect', 'requests.sessions.Session.request'):
            p = patch(name, side_effect=AssertionError('Servicio real bloqueado'))
            p.start(); self.addCleanup(p.stop)
        p = patch('app.get_db_connection', return_value=None)
        p.start(); self.addCleanup(p.stop)
        self.client = app.test_client()
        self.session('SuperAdmin')

    def session(self, rol, sede='Central'):
        with self.client.session_transaction() as s:
            s.update(admin_user='operador-ficticio', admin_rol=rol, admin_sede=sede, csrf_token='csrf-ficticio')

    def source(self, rows, observe=None):
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        if observe:
            cursor.execute.side_effect = observe
        @contextmanager
        def context(**kwargs):
            yield MagicMock(), cursor
        return patch.object(service, 'get_db_cursor', context), cursor

    def test_equivalencia_conteos_agrupados_limites_y_privacidad(self):
        rows = []
        for i, (day, stamp, alumno) in enumerate([
            (19, '10:01:00', 1), (19, '10:02:00', 1), (19, '10:03:00', 1),
            (20, '20:45:00', 1), (20, '20:45:00.001', 1), (20, '20:46:00', 1),
            (21, '07:59:00', 2), (21, '08:00:00', 2), (21, '08:00:01', 2),
        ]):
            stamp = datetime.fromisoformat(f'2026-10-{day}T{stamp}')
            rows.append((i, alumno, 'Escuela ficticia', 'Sala ficticia', 1, stamp,
                         stamp.hour, '2026-10', f'FICTICIO-{alumno}'))
        grouped = {}
        for r in rows:
            normal = hora(8) <= r[5].time() <= hora(20, 45)
            key = (r[1], r[5].date(), r[6], normal, r[3], r[4])
            if key not in grouped:
                grouped[key] = list(r) + [0]
            grouped[key][9] += 1
        clock = datetime(2026, 10, 22, 10, tzinfo=ZoneInfo('America/Lima'))
        for completo in (False, True):
            with patch.object(service, '_ahora_descriptivo', return_value=clock):
                source, _ = self.source(rows)
                with source:
                    expected = service.obtener_analisis_descriptivo_ingresos(forzar_refresco=True, mostrar_codigo_completo=completo)
                source, cursor = self.source(list(grouped.values()))
                with source:
                    actual = service.obtener_analisis_descriptivo_ingresos(forzar_refresco=True, mostrar_codigo_completo=completo)
            for data in (expected, actual):
                data.pop('rendimiento')
            self.assertEqual(actual, expected)
            self.assertEqual(actual['total_visitas'], 9)
            self.assertEqual(actual['total_visitas_horario_atipico'], 3)
            sql, params = cursor.execute.call_args.args
            self.assertIn('COUNT_BIG(*)', sql)
            self.assertIn('GROUP BY R.AlumnoID', sql)
            self.assertIn('ISNULL(R.EsPrueba, 0) = 0', sql)
            self.assertEqual(params, ('2026-06-01', '2026-10-22', 'Central'))
            self.assertNotIn('DNI', sql)

    def test_endpoint_mide_http_y_duracion_sin_datos_personales(self):
        data = {'status': 'success', 'total_visitas': 9}
        with patch.object(routes, 'obtener_analisis_descriptivo_ingresos', return_value=data), \
             self.assertLogs('routes.admin_ml', level=logging.INFO) as logs:
            start = time.perf_counter()
            response = self.client.get('/admin/api/ml/analisis-individual')
            elapsed = (time.perf_counter() - start) * 1000
        self.assertEqual(response.status_code, 200)
        self.assertIn('individual;dur=', response.headers['Server-Timing'])
        self.assertIn('estado_http=200', str(logs.output))
        self.assertNotIn('operador-ficticio', str(logs.output))
        print(f'ENDPOINT_SIMULADO estado_http=200 duracion_cliente_ms={elapsed:.2f}')

    def test_medicion_transferencia_sin_datos_personales(self):
        row = (None, 1, 'Escuela ficticia', 'Sala ficticia', 1,
               datetime(2026, 10, 19, 10), 10, '2026-10', 'FICTICIO-1')
        results = []
        for rows in ([row] * 18000, [row + (18000,)]):
            source, _ = self.source(rows)
            start = time.perf_counter()
            with source:
                result = service.obtener_analisis_descriptivo_ingresos(forzar_refresco=True)
            results.append(result)
            print(f'CALCULO_SIMULADO grupos={len(rows)} visitas={result["total_visitas"]} duracion_ms={(time.perf_counter()-start)*1000:.2f}')
        for result in results:
            result.pop('rendimiento')
            result.pop('actualizado_en')
        self.assertEqual(*results)

    def test_javascript_renderizado_timeout_error_reintento_y_exito(self):
        node = shutil.which('node')
        self.assertIsNotNone(node)
        getter = MagicMock(); getter.metadatos = {}
        with patch.object(routes.PredictorRetornoAlumno, 'obtener_instancia', return_value=getter), \
             patch.object(routes, 'obtener_informacion_modelo', return_value={}):
            response = self.client.get('/admin/ml')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="error-individual"', html)
        self.assertIn('Referencia del historial', html)
        scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S))
        checked = subprocess.run([node, '--check'], input=scripts, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        function = 'async function cargarAnalisisIndividual' + scripts.split('async function cargarAnalisisIndividual', 1)[1].split('    function renderizarAnalisisIndividual', 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
let analisisIndividualCargado=false, rendered=0, cancel, clearCount=0;
const elements = new Map();
const document={getElementById(id) {
  if(!elements.has(id)) elements.set(id,{value:'Central', disabled:false,textContent:'',classList:{add(){},remove(){}}});
  return elements.get(id);
}};
function setTimeout(cb,ms){assert.equal(ms,30000);cancel=cb;return 1;}
function clearTimeout(){clearCount++;}
function renderizarAnalisisIndividual(){rendered++;}
let mode='http-error';
async function fetch(url,options) {
  assert.ok(url.startsWith('/admin/api/ml/analisis-individual?sede=Central'));
  assert.ok(options.signal);
  if(mode==='timeout') {cancel();assert.ok(options.signal.aborted);throw Object.assign(new Error(),{name:'AbortError'});}
  return {ok:mode==='success',async json(){return mode==='success'?{status:'success',analisis:{},rol:'SuperAdmin'}:{status:'error'};}};
}
(async()=>{
 await cargarAnalisisIndividual();
 assert.equal(rendered,0);assert.equal(analisisIndividualCargado,false);
 assert.ok(document.getElementById('error-individual').textContent.includes('reintentar'));
 assert.equal(document.getElementById('btn-actualizar-individual').disabled,false);
 mode='timeout';await cargarAnalisisIndividual(true);
 assert.ok(document.getElementById('error-individual').textContent.includes('30 segundos'));
 assert.equal(document.getElementById('btn-actualizar-individual').disabled,false);
 mode='success';await cargarAnalisisIndividual(true);
 assert.equal(rendered,1);assert.equal(analisisIndividualCargado,true);assert.equal(clearCount,3);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([node], input=function + harness, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_error_recuperable_no_filtra_excepcion(self):
        with patch.object(routes, 'obtener_analisis_descriptivo_ingresos', side_effect=RuntimeError('NO-EXPONER-FICTICIO')):
            response = self.client.get('/admin/api/ml/analisis-individual')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('NO-EXPONER', response.get_data(as_text=True))
        self.assertIn('reintentar', response.get_data(as_text=True))
        self.assertIn('Server-Timing', response.headers)

    def test_permisos_sede_y_codigos_sin_ampliarlos(self):
        for rol in ('Consultor', 'Supervisor', 'Administrador', 'SuperAdmin'):
            self.session(rol, 'Tarma')
            data = {'ranking_alumnos_por_visitas': [{'codigo_matricula': 'FICTICIO-PRIVADO'}]}
            with patch.object(routes, 'obtener_analisis_descriptivo_ingresos', return_value=copy.deepcopy(data)) as query:
                response = self.client.get('/admin/api/ml/analisis-individual?sede=Central')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(query.call_args.kwargs['sede'], 'Tarma' if rol == 'Supervisor' else 'Central')
            self.assertEqual(query.call_args.kwargs['mostrar_codigo_completo'], rol in ('Administrador', 'SuperAdmin'))
            if rol in ('Consultor', 'Supervisor'):
                self.assertNotIn('FICTICIO-PRIVADO', response.get_data(as_text=True))
        self.session('Supervisor', '')
        with patch.object(routes, 'obtener_analisis_descriptivo_ingresos') as query:
            self.assertEqual(self.client.get('/admin/api/ml/analisis-individual').status_code, 403)
            query.assert_not_called()
        with self.client.session_transaction() as sesion:
            sesion.clear()
        with patch.object(routes, 'obtener_analisis_descriptivo_ingresos') as query:
            self.assertEqual(self.client.get('/admin/api/ml/analisis-individual').status_code, 302)
            query.assert_not_called()

    def test_base_referencia_22_objetivo_23_y_rechazo_referencia_23(self):
        model = service.PredictorRetornoAlumno()
        cursor = MagicMock()
        def execute(sql, params=None):
            if 'MAX(CAST' in sql:
                cursor.fetchone.return_value = (date(2026, 10, 22),)
            elif 'FROM dbo.Alumnos' in sql:
                cursor.fetchone.return_value = (1, 'FICTICIO-1', 'Escuela ficticia')
            elif 'SELECT R.FechaHora' in sql:
                cursor.fetchall.return_value = [(datetime(2026, 10, d, 10),) for d in (19, 20, 21, 22)]
            else:
                cursor.fetchall.return_value = [(date(2026, 10, d),) for d in (19, 20, 21, 22)]
        cursor.execute.side_effect = execute
        @contextmanager
        def source(**kwargs):
            yield MagicMock(), cursor
        with patch.object(service, 'get_db_cursor', source):
            accepted = model.predecir_retorno(codigo_matricula='FICTICIO-1', fecha_referencia='2026-10-22')
            rejected = model.predecir_retorno(codigo_matricula='FICTICIO-1', fecha_referencia='2026-10-23')
        self.assertEqual(accepted['status'], 'success')
        self.assertEqual(accepted['proximo_dia_operativo'], '2026-10-23')
        self.assertEqual(rejected['status'], 'no_calculable')
        self.assertIsNone(rejected['probabilidad_retorno'])

    def test_candidato_dos_dias_explica_ventana_no_relaja_minimo(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = (1,)
        # Dos días válidos; el feriado no crea un tercer día elegible.
        conn.cursor.return_value.fetchall.return_value = [(date(2026, 10, d), 2) for d in (8, 19, 20)]
        clock = datetime(2026, 10, 22, 22, tzinfo=ZoneInfo('America/Lima'))
        with patch.object(candidate, 'get_db_connection', return_value=conn), patch.object(candidate, '_now', return_value=clock):
            result = candidate.predecir_candidato('FICTICIO-1', fecha_referencia='2026-10-22')
            with self.assertRaises(candidate.CoberturaNoConfirmada):
                candidate.predecir_candidato('FICTICIO-1', fecha_referencia='2026-10-23')
        self.assertEqual(result['status'], 'historial_insuficiente')
        self.assertEqual(result['fecha_objetivo'], '2026-10-23')
        self.assertEqual(result['diagnostico']['dias_activos_validos']['30'], 2)
        self.assertEqual(result['detalle_historial']['minimo_dias_activos_30'], 3)
        self.assertEqual(result['detalle_historial']['ventana_hasta'], '2026-10-21')
        self.assertIsNone(result['probabilidad_h1'])
        self.assertNotIn('FICTICIO-1', str(result))
        conn.commit.assert_not_called()
