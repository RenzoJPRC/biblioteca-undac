"""Aceptación de la conversación a–g: Flask real con herramientas y proveedor simulados."""
import json
import unittest
from unittest.mock import MagicMock, patch
from tests import test_chat_conversacional as base
from llm.services import chat_conversacional as chat
from llm.services import coordinador_asistente as coor
from llm.services import proveedor_llm as provider

PREGUNTAS = [
    'Quiero consultar la afluencia de sede Central',
    'De septiembre de 2026',
    '¿Y en octubre?',
    '¿Qué libros fueron más prestados?',
    'Necesito saber sobre',
    'Nueva consulta',
    '¿En qué puedes ayudarme?',
]

class TestAceptacionConversacional(unittest.TestCase):
    setUp = base.TestChatConversacional.setUp
    enviar = base.TestChatConversacional.enviar

    def test_ruta_secuencia_completa_con_proveedor_y_fallback(self):
        for fallo in (False, True):
            with self.subTest(fallo_gemini=fallo):
                with self.client.session_transaction() as ses: ses.pop('asistente_contexto',None)
                self.llm.return_value.responder_chat.side_effect = provider.LLMTimeoutError() if fallo else None
                self.llm.reset_mock()
                def fuente(**kwargs):
                    self.assertEqual(kwargs['sede'],'Central')
                    if kwargs['fecha_inicio']=='2026-10-01':
                        self.assertEqual(kwargs['fecha_fin'],'2026-10-31')
                        raise ValueError('Datos insuficientes: fuente ficticia sin cobertura de octubre')
                    self.assertEqual(kwargs['fecha_inicio'],'2026-09-01')
                    self.assertEqual(kwargs['fecha_fin'],'2026-09-30')
                    return dict(periodo=dict(fecha_inicio='2026-09-01',fecha_fin='2026-09-30'),sede='Central',fuente='Fuente ficticia',limites_cobertura='Solo simulación',mensaje='3 ingresos en la fuente ficticia',total_ingresos=3)
                with patch.object(coor,'consultar_afluencia_historica',side_effect=fuente) as tool:
                    respuestas=[]
                    for pregunta in PREGUNTAS:
                        respuesta=self.enviar(pregunta)
                        self.assertEqual(respuesta.headers['X-Asistente-Chat'],chat.CHAT_REVISION)
                        respuestas.append(respuesta.get_json())
                    a,b,c,d,e,f,g=respuestas
                    self.assertEqual(a['parametros_faltantes'],['periodo'])
                    self.assertEqual(b['status'],'success');self.assertEqual(b['datos']['total_ingresos'],3)
                    self.assertNotIn('¿De qué año',b['mensaje'])
                    if fallo: self.assertEqual(b['error_proveedor'],'timeout')
                    self.assertEqual(c['status'],'error');self.assertIn('sin cobertura',c['mensaje'])
                    self.assertIn('no está disponible',d['mensaje'])
                    self.assertIn('¿Sobre qué',e['mensaje'])
                    for respuesta in (d,e,f,g):
                        self.assertEqual(respuesta['datos'],{})
                        self.assertEqual(respuesta['contexto'],{})
                        self.assertNotIn('2026-09',json.dumps(respuesta))
                        self.assertNotIn('3 ingresos',respuesta['mensaje'])
                        self.assertNotIn('¿De qué año',respuesta['mensaje'])
                    self.assertEqual(g['origen'],'regla_local')
                    self.assertEqual(g['intencion'],'consulta_general')
                    self.assertLess(len(g['mensaje']),400)
                    self.assertNotIn('parametros_faltantes',g)
                    self.assertEqual(tool.call_count,2)
                    self.assertEqual(self.llm.return_value.responder_chat.call_count,1)
                    with self.client.session_transaction() as ses: self.assertEqual(ses['asistente_contexto'],{})
                html=self.client.get('/admin/asistente').get_data(as_text=True)
                self.assertIn('content="'+chat.CHAT_REVISION+'"',html)

    def test_servicio_ayuda_local_y_reemplazo_de_aclaracion_obsoleta(self):
        motor=MagicMock(side_effect=AssertionError('No debe reutilizar una herramienta de la conversación previa'))
        pendiente=dict(intencion='afluencia_historica',sede='Central',mes_pendiente='9',aclaracion_pendiente='1')
        self.llm.return_value.responder_chat.side_effect=provider.LLMTimeoutError()
        for pregunta in ('Necesito saber sobre','¿En qué puedes ayudarme?','en que puedes ayudarme','Nueva consulta'):
            with self.subTest(pregunta=pregunta):
                respuesta=chat.procesar_chat(motor,pregunta,'SuperAdmin',None,pendiente)
                self.assertEqual(respuesta['contexto'],{})
                self.assertEqual(respuesta['datos'],{})
                self.assertNotIn('¿De qué año',respuesta['mensaje'])
                self.assertEqual(respuesta['origen'],'regla_local')
        motor.assert_not_called();self.llm.assert_not_called();self.metrics.assert_not_called()

    def test_lanzador_simulado_no_comparte_sesion_real(self):
        import subprocess,sys
        codigo = r"""
import pyodbc,pymssql,requests,smtplib
from unittest.mock import patch
from itsdangerous import BadSignature
def deny(*a,**k):raise AssertionError('Servicio real bloqueado')
pyodbc.connect=deny;pymssql.connect=deny;requests.sessions.Session.request=deny;smtplib.SMTP=deny;smtplib.SMTP_SSL=deny
import app as modulo
clave_anterior=modulo.app.secret_key
nombre_anterior=modulo.app.config.get('SESSION_COOKIE_NAME','session')
firmante_anterior=modulo.app.session_interface.get_signing_serializer(modulo.app)
from llm.vista_local_chat import main
with patch('flask.Flask.run'):main()
assert modulo.app.secret_key != clave_anterior
assert modulo.app.config['SESSION_COOKIE_NAME'] != nombre_anterior
client=modulo.app.test_client()
client.set_cookie(nombre_anterior,firmante_anterior.dumps({'admin_user':'operador-ficticio'}))
respuesta=client.get('/admin/asistente')
assert any(c.startswith(nombre_anterior+'=;') for c in respuesta.headers.getlist('Set-Cookie'))
fixture_cookie=client.get_cookie(modulo.app.config['SESSION_COOKIE_NAME'])
assert fixture_cookie is not None
try:firmante_anterior.loads(fixture_cookie.value)
except BadSignature:pass
else:raise AssertionError('La sesión ficticia era válida con la clave de producción')
print('AISLAMIENTO_OK')
"""
        r=subprocess.run([sys.executable,'-c',codigo],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr[-1000:])
        self.assertIn('AISLAMIENTO_OK',r.stdout)
