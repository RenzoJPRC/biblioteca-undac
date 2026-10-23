"""Fuentes agregadas del asistente: BD simulada, sin conexiones ni servicios."""
from contextlib import contextmanager
from datetime import date,datetime,time
from collections import Counter
import unittest
from unittest.mock import MagicMock,patch
from llm.services import consultas_biblioteca as q
from llm.services.fuente_afluencia_asistente import unir_recientes
from tests import test_chat_conversacional as base

class TestUnionAfluencia(unittest.TestCase):
    def setUp(self):
        self.bloqueos=[]
        for nombre in ('pyodbc.connect','pymssql.connect','requests.sessions.Session.request'):
            p=patch(nombre,side_effect=AssertionError('Servicio real bloqueado'));p.start();self.addCleanup(p.stop)

    def test_septiembre_29_30_y_claves_prioritarias_aha(self):
        c=MagicMock();c.fetchall.return_value=[(date(2026,9,28),10,999,'Central'),(date(2026,9,29),10,4,'Central'),(date(2026,9,30),20,2,'Central')]
        filas,cob=unir_recientes(c,[(date(2026,9,28),10,7,0)],date(2026,9,28),'Central',date(2026,9,1),date(2026,9,30),date(2026,10,22))
        self.assertEqual(sum(f[2] for f in filas),13)
        self.assertEqual(len(filas),3)
        self.assertEqual(cob['franjas_recientes_incorporadas'],2)
        self.assertEqual(cob['ultimo_dia_observado_en_periodo'],'2026-09-30')
        self.assertEqual(cob['estado'],'parcial') # Tener filas históricas no confirma captura continua.
        self.assertFalse(cob['ausencias_se_interpretan_como_cero'])

    def test_octubre_con_ingresos_recientes_sin_ceros_sinteticos(self):
        c=MagicMock();c.fetchall.return_value=[(date(2026,10,1),8,3,'Central'),(date(2026,10,21),20,5,'Central')]
        filas,cob=unir_recientes(c,[],date(2026,9,28),'Central',date(2026,10,1),date(2026,10,31),date(2026,10,22))
        self.assertEqual(len(filas),2);self.assertEqual(sum(f[2] for f in filas),8)
        self.assertEqual(cob['ultimo_dia_con_datos'],'2026-10-21')
        self.assertEqual(cob['estado'],'parcial');self.assertIn('2026-10-22',cob['fechas_captura_no_confirmada'])

    def test_sql_sede_pruebas_limite_horario_y_exclusiones_calendario(self):
        c=MagicMock()
        c.fetchall.return_value=[(date(2026,10,1),10,2,None),(date(2026,10,1),10,2,''),(date(2026,10,1),10,2,'Tarma'),(date(2026,10,8),10,9,'Central'),(date(2026,10,10),10,9,'Central'),(date(2026,10,22),10,9,'Central'),(date(2026,10,1),21,9,'Central'),(date(2026,10,1),10,3,'Central')]
        filas,_=unir_recientes(c,[],date(2026,9,28),'Central',date(2026,10,1),date(2026,10,31),date(2026,10,22))
        self.assertEqual([f[2] for f in filas],[3])
        sql,args=c.execute.call_args.args
        self.assertTrue(sql.lstrip().startswith('SELECT'))
        self.assertIn('ISNULL(EsPrueba, 0) = 0',sql)
        self.assertIn("'20:45:00.000'",sql);self.assertIn("'08:00:00'",sql)
        self.assertIn('Sede = ?',sql);self.assertNotIn("ISNULL(Sede",sql)
        self.assertEqual(args,('2026-09-28','2026-10-22','Central'))
        for campo in ('AlumnoID','CodigoMatricula','DNI','NombreCompleto'):self.assertNotIn(campo,sql)

    def test_sin_cobertura_no_equivale_a_cero_y_error_no_se_oculta(self):
        c=MagicMock();c.fetchall.return_value=[]
        filas,cob=unir_recientes(c,[],date(2026,9,28),'Tarma',date(2026,10,1),date(2026,10,31),date(2026,10,22))
        self.assertEqual(filas,[]);self.assertEqual(cob['estado'],'parcial')
        self.assertFalse(cob['captura_completa_confirmada'])
        c.execute.side_effect=RuntimeError('fallo ficticio')
        with self.assertRaises(RuntimeError):unir_recientes(c,[],date(2026,9,28),'Central',date(2026,10,1),date(2026,10,31))

    def test_servicio_conserva_periodo_y_advierte_parcial(self):
        c=MagicMock();c.fetchone.return_value=(82,100,300,date(2026,4,10),date(2026,9,28))
        c.fetchall.side_effect=[[(date(2026,9,28),10,7,0)],[(date(2026,9,29),10,4,'Central'),(date(2026,9,30),20,2,'Central')]]
        @contextmanager
        def db(**kwargs):yield None,c
        with patch.object(q,'get_db_cursor',db):r=q.consultar_afluencia_historica('2026-09-01','2026-09-30',rol='SuperAdmin')
        self.assertEqual(r['metricas']['total_ingresos'],13)
        self.assertTrue(r['mensaje'].startswith('PERIODO PARCIAL'))
        self.assertIn('RegistroIngresos',r['fuente'])
        self.assertEqual(r['limites_cobertura']['fecha_maxima_sistema'],'2026-09-30')
        self.assertEqual(r['periodo']['fecha_fin'],'2026-09-30')

class TestRutaConFuentesRecientes(unittest.TestCase):
    setUp=base.TestChatConversacional.setUp
    enviar=base.TestChatConversacional.enviar

    def test_octubre_llega_a_la_union_y_gemini_falla_sin_perder_cifras(self):
        c=MagicMock();c.fetchone.return_value=(82,100,300,date(2026,4,10),date(2026,9,28))
        c.fetchall.side_effect=[[],[(date(2026,10,1),10,4,'Central')]]
        @contextmanager
        def db(**kwargs):yield None,c
        self.llm.return_value.responder_chat.side_effect=RuntimeError('fallo ficticio')
        with patch.object(q,'get_db_cursor',db):r=self.enviar('Resume la afluencia de la sede Central en octubre de 2026').get_json()
        self.assertEqual(r['status'],'success')
        self.assertEqual(r['datos']['metricas']['total_ingresos'],4)
        self.assertEqual(r['aviso_origen'],'Respuesta de respaldo; Gemini no respondió')
        self.assertTrue(r['datos']['mensaje'].startswith('PERIODO PARCIAL'))
        self.assertNotIn('No se registraron observaciones',r['mensaje'])

    def test_fallo_fuente_reciente_no_presenta_reporte_viejo_como_actual(self):
        c=MagicMock();c.fetchone.return_value=(82,100,300,date(2026,4,10),date(2026,9,28))
        c.fetchall.side_effect=[[],RuntimeError('Fallo ficticio al leer agregados recientes')]
        @contextmanager
        def db(**kwargs):yield None,c
        with patch.object(q,'get_db_cursor',db):
            r=self.enviar('Resume la afluencia de la sede Central en octubre de 2026')
        self.assertEqual(r.status_code,503)
        self.assertEqual(r.get_json()['status'],'error')
        self.llm.assert_not_called()
