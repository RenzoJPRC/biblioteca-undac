"""Regresiones de aislamiento de la demo y auditoría con registros sintéticos."""
from contextlib import contextmanager
from datetime import datetime
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch


class TestCorreccionesRevisionLLM(unittest.TestCase):
    def test_demo_agentes_no_comparte_cookie_clave_ni_fallbacks(self):
        codigo = r'''
import os
os.environ.update(SECRET_KEY='clave-habitual-ficticia', FLASK_ENV='testing',
                  DB_SERVER='blocked.invalid', DB_DATABASE='test_db',
                  DB_USERNAME='test', DB_PASSWORD='test', LLM_ACTIVADO='false')
import pyodbc,pymssql,requests,smtplib,socket
from unittest.mock import patch
from itsdangerous import BadSignature
def deny(*a,**k): raise AssertionError('Conexion externa bloqueada')
pyodbc.connect=pymssql.connect=requests.sessions.Session.request=deny
smtplib.SMTP=smtplib.SMTP_SSL=socket.socket.connect=deny
import app as modulo
anterior=modulo.app.secret_key
nombre=modulo.app.config.get('SESSION_COOKIE_NAME','session')
firmante=modulo.app.session_interface.get_signing_serializer(modulo.app)
modulo.app.config['SECRET_KEY_FALLBACKS']=[anterior]
from llm.vista_local_agentes import main
with patch('flask.Flask.run') as run: main()
assert modulo.app.secret_key != anterior
assert modulo.app.config['SESSION_COOKIE_NAME'] != nombre
assert not modulo.app.config.get('SECRET_KEY_FALLBACKS')
run.assert_called_once_with(host='127.0.0.1',port=8798,debug=False,use_reloader=False)
client=modulo.app.test_client()
cookie_habitual=firmante.dumps({'admin_user':'usuario-habitual-ficticio'})
client.set_cookie(nombre,cookie_habitual)
response=client.get('/admin/asistente')
assert response.status_code == 200
assert client.get_cookie(nombre).value == cookie_habitual
assert not any(c.startswith(nombre+'=') for c in response.headers.getlist('Set-Cookie'))
cookie_demo=client.get_cookie(modulo.app.config['SESSION_COOKIE_NAME'])
assert cookie_demo is not None
nuevo=modulo.app.session_interface.get_signing_serializer(modulo.app)
assert nuevo.loads(cookie_demo.value)['admin_user']=='operador-ficticio'
for signer,value in ((firmante,cookie_demo.value),(nuevo,cookie_habitual)):
    try: signer.loads(value)
    except BadSignature: pass
    else: raise AssertionError('Cookie aceptada fuera de su aplicacion')
print('AISLAMIENTO_AGENTES_OK')
'''
        result = subprocess.run([sys.executable, '-B', '-c', codigo], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertIn('AISLAMIENTO_AGENTES_OK', result.stdout)

    def _consultar_con_cobertura_sintetica(self, filas):
        from llm.services import consultas_biblioteca as queries
        # SQLite únicamente en memoria ejecuta la agregación; ISNULL de SQL Server
        # se traduce a IFNULL. No hay conexión a SQL Server ni registros de personas.
        with sqlite3.connect(':memory:') as fixture:
            fixture.execute('CREATE TABLE RegistroIngresos (FechaHora TEXT, EsPrueba INTEGER, Sede TEXT)')
            fixture.executemany('INSERT INTO RegistroIngresos VALUES (?, ?, ?)', filas)
            cursor = MagicMock()
            respuestas = []
            def ejecutar(sql, params):
                self.assertTrue(sql.lstrip().startswith('SELECT'))
                if 'AS total_registros' in sql:
                    fila = fixture.execute(sql.replace('ISNULL(', 'IFNULL('), params).fetchone()
                    respuestas.append(tuple(datetime.fromisoformat(v) if i < 2 and v else v for i,v in enumerate(fila)))
                else:
                    self.assertIn('ISNULL(R.EsPrueba, 0) = 0', sql)
                    self.assertIn('R.Sede = ?', sql)
                    respuestas.append((0, None, None, 0, ''))
            cursor.execute.side_effect = ejecutar
            cursor.fetchone.side_effect = lambda: respuestas.pop(0)
            @contextmanager
            def fuente(**kwargs):
                yield None, cursor
            with patch.object(queries, 'get_db_cursor', fuente), \
                 patch('pyodbc.connect', side_effect=AssertionError('BD real bloqueada')), \
                 patch('pymssql.connect', side_effect=AssertionError('BD real bloqueada')), \
                 patch('requests.sessions.Session.request', side_effect=AssertionError('Red bloqueada')):
                resultado = queries.contar_ingresos_alumno_por_codigo('9900000001', rol='Supervisor', sede_permitida='Tarma')
            self.assertEqual(cursor.execute.call_count, 2)
            return resultado

    def test_auditoria_cuenta_pruebas_sin_ampliar_corte_ni_mezclar_sedes(self):
        resultado = self._consultar_con_cobertura_sintetica([
            ('2026-10-21T08:00:00', None, 'Tarma'),
            ('2026-10-22T09:00:00', 0, 'Tarma'),
            ('2026-10-23T10:00:00', 1, 'Tarma'),
            ('2026-10-24T10:00:00', 1, 'Central'),
        ])
        cobertura = resultado['cobertura_sistema']
        self.assertEqual(cobertura['total_pruebas_excluidas'], 1)
        self.assertEqual(cobertura['total_visitas_sistema'], 3)
        self.assertEqual(cobertura['total_visitas_reales'], 2)
        self.assertEqual(cobertura['fecha_minima'], '2026-10-21')
        self.assertEqual(cobertura['fecha_maxima'], '2026-10-22')
        self.assertEqual(resultado['total_ingresos'], 0)
        self.assertIn('excluyeron 1 registros', resultado['mensaje'])

    def test_solo_pruebas_no_inventa_cobertura_real(self):
        resultado = self._consultar_con_cobertura_sintetica([('2026-10-23T10:00:00', 1, 'Tarma')])
        cobertura = resultado['cobertura_sistema']
        self.assertEqual(cobertura['total_pruebas_excluidas'], 1)
        self.assertEqual(cobertura['total_visitas_reales'], 0)
        self.assertIsNone(cobertura['fecha_minima'])
        self.assertIsNone(cobertura['fecha_maxima'])

    def test_fuente_vacia_no_inventa_visitas_ni_pruebas(self):
        resultado = self._consultar_con_cobertura_sintetica([])
        cobertura = resultado['cobertura_sistema']
        self.assertEqual(cobertura['total_pruebas_excluidas'], 0)
        self.assertEqual(cobertura['total_visitas_reales'], 0)
        self.assertIsNone(cobertura['fecha_minima'])
        self.assertIsNone(cobertura['fecha_maxima'])
