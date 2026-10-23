"""Secuencia de la captura y anomalías del informe, con datos exclusivamente ficticios."""
import json
import re
import sqlite3
import shutil
import subprocess
import unittest
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from datetime import datetime, time
from tests import test_chat_conversacional as base
from llm.services import coordinador_asistente as coor, consultas_biblioteca as queries
from llm.services.motor_intenciones import clasificar_intencion


class TestAclaracionIngresosAlumno(unittest.TestCase):
    setUp = base.TestChatConversacional.setUp
    enviar = base.TestChatConversacional.enviar

    def test_secuencia_captura_conserva_periodo_y_no_repite_tema(self):
        pregunta = 'quiero saber los ingresos de un alumno desde junio del 2026 hasta octubre del 2026'
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo', return_value={'mensaje':'Informe ficticio', 'total_ingresos':2}) as fuente:
            primera = self.enviar(pregunta).get_json()
            self.assertEqual(primera['intencion'], 'conteo_ingresos_alumno')
            self.assertEqual(primera['parametros_faltantes'], ['codigo_matricula'])
            for texto in ('ingresos', 'INGRESOS', 'los ingresos'):
                respuesta = self.enviar(texto).get_json()
                self.assertEqual(respuesta['intencion'], 'conteo_ingresos_alumno')
                self.assertIn('matrícula', respuesta['mensaje'])
                self.assertNotIn('¿Sobre qué', respuesta['mensaje'])
                self.assertEqual(respuesta['contexto']['fecha_inicio'], '2026-06-01')
                self.assertEqual(respuesta['contexto']['fecha_fin'], '2026-10-31')
            fuente.assert_not_called()
            final = self.enviar('9900000001').get_json()
            self.assertEqual(final['status'], 'success')
            self.assertTrue(final['consulta_confidencial'])
            self.assertEqual(fuente.call_args.kwargs['fecha_inicio'], '2026-06-01')
            self.assertEqual(fuente.call_args.kwargs['fecha_fin'], '2026-10-31')
        self.llm.assert_not_called(); self.metrics.assert_not_called()
        with self.client.session_transaction() as ses:
            self.assertNotIn('9900000001', json.dumps(dict(ses)))

    def test_orden_de_palabras_y_cambio_de_tema(self):
        for frase in ('ingresos de un alumno', 'visitas del estudiante', 'el alumno y sus ingresos'):
            self.assertEqual(clasificar_intencion(frase)['intencion'], 'conteo_ingresos_alumno')
        self.enviar('ingresos de un alumno')
        self.assertIn('no está disponible', self.enviar('¿Qué libros fueron más prestados?').get_json()['mensaje'])
        self.assertEqual(self.enviar('Nueva consulta').get_json()['contexto'], {})
        self.llm.assert_not_called()

    def test_consultor_no_accede_a_esta_redaccion(self):
        with self.client.session_transaction() as ses:ses['admin_rol']='Consultor'
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo') as fuente:
            response=self.enviar('quiero saber los ingresos de un alumno desde junio del 2026 hasta octubre del 2026')
            self.assertEqual(response.status_code,403)
            fuente.assert_not_called()
        self.llm.assert_not_called()


    def test_html_y_javascript_muestran_atipicos_sin_ocultar_filas(self):
        html = self.client.get('/admin/asistente').get_data(as_text=True)
        script = next(s for s in re.findall(r'<script\b[^>]*>(.*?)</script>',html,re.S) if 'async function ejecutarConsulta' in s)
        harness = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const script=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const element=()=>({textContent:'',children:[],appendChild(n){this.children.push(n);}});
const context={document:{addEventListener(){},createElement:element}};
vm.runInNewContext(script,context);
const holder=element();
context.renderizarDatosChat(holder,{aviso_atipicos:'Atípicos sintéticos: <img src=x> se conservan.',
 dias_semana:[{grupo:5,visitas:1},{grupo:6,visitas:1}],horarios:[{grupo:20,visitas:2},{grupo:22,visitas:1}]});
const plain=n=>n.textContent+n.children.map(plain).join(' ');
assert.ok(plain(holder).includes('Atípicos sintéticos: <img src=x> se conservan.'));
assert.ok(plain(holder).includes('Sábado · atípico según horario habitual: 1 visitas'));
assert.ok(plain(holder).includes('Domingo · atípico según horario habitual: 1 visitas'));
assert.ok(plain(holder).includes('22:00 · fuera del horario habitual: 1 visitas'));
assert.ok(plain(holder).includes('20:00: 2 visitas'));
assert.ok(!plain(holder).includes('20:00 · fuera'));
'''
        node=shutil.which('node')
        self.assertIsNotNone(node, 'Node es necesario para validar el JavaScript renderizado')
        result=subprocess.run([node,'-e',harness],input=json.dumps(script),text=True,encoding='utf-8',capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_aclaracion_personal_pendiente_no_se_convierte_en_consulta_agregada(self):
        with self.client.session_transaction() as ses:
            ses['asistente_contexto']={'intencion':'conteo_ingresos_alumno','sede':'Tarma',
                'fecha_inicio':'2026-06-01','fecha_fin':'2026-10-31','aclaracion_pendiente':'1'}
        with patch.object(coor,'contar_ingresos_alumno_por_codigo') as personal, patch.object(coor,'obtener_resumen_ingresos') as agregado:
            respuesta=self.enviar('ingresos').get_json()
            self.assertEqual(respuesta['intencion'],'conteo_ingresos_alumno')
            self.assertEqual(respuesta['parametros_faltantes'],['codigo_matricula'])
            self.assertEqual(respuesta['contexto']['sede'],'Tarma')
            self.assertEqual(respuesta['contexto']['fecha_inicio'],'2026-06-01')
            personal.assert_not_called();agregado.assert_not_called()
        self.llm.assert_not_called()


    def _informe_octubre(self, total=2, ultima='2026-10-21T22:00:00', corte='2026-10-23'):
        datos = {
            'codigo_mascarado':'99******01', 'total_ingresos':total, 'dias_con_visita':1 if total else 0,
            'periodo_consultado':{'fecha_inicio':'2026-10-01','fecha_fin':'2026-10-31'},
            'fecha_corte':corte, 'ultimo_ingreso':ultima, 'mensaje':'Informe ficticio',
            'cobertura_sistema':{'fecha_minima':'2026-06-01','fecha_maxima':corte},
            'estado':'verificado_con_ingresos' if total else 'sin_ingresos_verificables',
            'atipicos':{'total_sin_duplicar':1 if total else 0,'registros_conservados':True},
            'aviso_atipicos':'Las visitas atípicas se conservan.',
        }
        with patch.object(coor,'contar_ingresos_alumno_por_codigo',return_value=datos) as fuente:
            respuesta=self.enviar('Informe del alumno 9900000001 en octubre de 2026 en Central')
        self.assertEqual(respuesta.status_code,200)
        self.assertEqual(fuente.call_args.kwargs['fecha_inicio'],'2026-10-01')
        self.assertEqual(fuente.call_args.kwargs['fecha_fin'],'2026-10-31')
        self.assertEqual(fuente.call_args.kwargs['sede_permitida'],'Central')
        self.llm.assert_not_called();self.metrics.assert_not_called()
        return respuesta.get_json()

    def test_octubre_parcial_separa_rango_solicitado_corte_y_ultima_visita(self):
        respuesta=self._informe_octubre()
        texto=respuesta['resumen_visible']
        self.assertIn('Rango solicitado: del 2026-10-01 al 2026-10-31',texto)
        self.assertIn('Intervalo limitado a los datos disponibles: del 01/10/2026 al 23/10/2026',texto)
        self.assertIn('Periodo parcial; datos observados hasta el 23/10/2026',texto)
        self.assertIn('Última visita individual dentro del periodo y hasta el corte: 21/10/2026',texto)
        self.assertEqual(respuesta['periodo_individual']['ultima_visita_individual'],'2026-10-21')
        self.assertEqual(respuesta['periodo_individual']['intervalo_hasta_corte']['fin'],'2026-10-23')
        self.assertTrue(respuesta['periodo_individual']['parcial_por_corte'])
        self.assertTrue(respuesta['datos']['atipicos']['registros_conservados'])
        self.assertEqual(respuesta['datos']['total_ingresos'],2)
        self.assertEqual(respuesta['datos']['ultimo_ingreso'],'2026-10-21T22:00:00')
        self.assertNotIn('9900000001',texto)

    def test_octubre_sin_visitas_no_inventa_fecha_individual(self):
        respuesta=self._informe_octubre(total=0,ultima=None)
        texto=respuesta['resumen_visible']
        self.assertIn('Periodo parcial; datos observados hasta el 23/10/2026',texto)
        self.assertIn('Sin visitas individuales registradas',texto)
        self.assertIsNone(respuesta['periodo_individual']['ultima_visita_individual'])
        self.assertNotIn('Última visita individual dentro del periodo y hasta el corte: 23/10',texto)

    def test_ultima_visita_fuera_del_periodo_o_corte_no_se_presenta_como_valida(self):
        for ultima in ('2026-09-30T10:00:00','2026-10-24T10:00:00','2026-11-01T10:00:00',None):
            with self.subTest(ultima=ultima):
                respuesta=self._informe_octubre(ultima=ultima)
                self.assertIsNone(respuesta['periodo_individual']['ultima_visita_individual'])
                self.assertIn('Fecha de última visita individual no disponible o no verificable',respuesta['resumen_visible'])

    def test_corte_desconocido_no_inventa_disponibilidad(self):
        respuesta=self._informe_octubre(corte=None)
        self.assertIsNone(respuesta['periodo_individual']['corte_observado'])
        self.assertIsNone(respuesta['periodo_individual']['intervalo_hasta_corte'])
        self.assertIsNone(respuesta['periodo_individual']['parcial_por_corte'])
        self.assertIsNone(respuesta['periodo_individual']['ultima_visita_individual'])
        self.assertIn('Disponibilidad no verificable',respuesta['resumen_visible'])

    def test_periodo_anterior_al_corte_no_se_declara_completo(self):
        from llm.services.chat_conversacional import _preparar_resumen_visible_alumno
        resultado={'sede_aplicada':'Central','datos':{
            'codigo_mascarado':'99******01','total_ingresos':1,'dias_con_visita':1,
            'periodo_consultado':{'fecha_inicio':'2026-09-01','fecha_fin':'2026-09-30'},
            'fecha_corte':'2026-10-23','ultimo_ingreso':'2026-09-29T10:00:00',
            'cobertura_sistema':{'fecha_minima':'2026-06-01'}}}
        _preparar_resumen_visible_alumno(resultado)
        self.assertFalse(resultado['periodo_individual']['parcial_por_corte'])
        self.assertEqual(resultado['periodo_individual']['intervalo_hasta_corte']['fin'],'2026-09-30')
        self.assertIn('no acredita captura continua ni cobertura completa',resultado['resumen_visible'])
        self.assertIn('29/09/2026',resultado['resumen_visible'])


class TestAtipicosInformeAlumno(unittest.TestCase):
    def test_registros_atipicos_se_conservan_y_no_se_afirma_zona_o_autenticidad(self):
        stamp=datetime(2026,10,19,10)
        cursor=MagicMock()
        cursor.fetchone.side_effect=[(5,stamp,stamp,3,'',2,3,4),(stamp,stamp,6,1,5)]
        cursor.fetchall.side_effect=[[('2026-10',5)],[(0,3),(5,1),(6,1)],[(0,1),(10,2),(20,1),(22,1)],[('Sala ficticia',5)]]
        @contextmanager
        def fuente(**kwargs):yield None,cursor
        with patch.object(queries,'get_db_cursor',fuente):
            result=queries.contar_ingresos_alumno_por_codigo('9900000001',rol='Supervisor',sede_permitida='Tarma',informe=True)
        self.assertEqual(result['total_ingresos'],5)
        self.assertEqual(sum(f['visitas'] for f in result['horarios']),5)
        self.assertEqual(result['atipicos']['fin_de_semana'],2)
        self.assertEqual(result['atipicos']['fuera_horario'],3)
        self.assertEqual(result['atipicos']['total_sin_duplicar'],4)
        self.assertIn('atípicos',result['aviso_atipicos'])
        self.assertIn('zona horaria',result['aviso_atipicos'])
        sql,params=cursor.execute.call_args_list[0].args
        self.assertIn('ISNULL(R.EsPrueba, 0) = 0',sql)
        self.assertIn('R.Sede = ?',sql)
        self.assertIn("'20:45:00.000'",sql)
        self.assertNotIn('AT TIME ZONE',sql)
        where=sql.split('WHERE',1)[1]
        self.assertNotIn('DATEPART',where)
        self.assertNotIn('AS TIME',where)
        self.assertIn('Tarma',params)

    def test_agregacion_sql_sintetica_limite_exacto_solapamiento_y_pruebas(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('DATEDIFF', 3, lambda unidad, inicio, fin:
                (datetime.fromisoformat(fin) - datetime.strptime(inicio, '%Y%m%d')).days)
            def hora(valor):
                valor = datetime.fromisoformat(valor).time() if 'T' in valor else time.fromisoformat(valor)
                return valor.isoformat(timespec='microseconds')
            db.create_function('hora', 1, hora)
            db.execute('CREATE TABLE Alumnos (AlumnoID INTEGER, CodigoMatricula TEXT, Escuela TEXT)')
            db.execute('CREATE TABLE Salas (SalaID INTEGER, Sede TEXT, NombreSala TEXT)')
            db.execute('CREATE TABLE RegistroIngresos (AlumnoID INTEGER, FechaHora TEXT, EsPrueba INTEGER, Sede TEXT, SalaID INTEGER)')
            db.execute("INSERT INTO Alumnos VALUES (1, '9900000001', 'Escuela ficticia')")
            db.execute("INSERT INTO Salas VALUES (1, 'Central', 'Sala ficticia')")
            filas = [
                ('2026-10-16T20:45:00.000', 0, 'Central'),
                ('2026-10-16T20:45:00.001', 0, 'Central'),
                ('2026-10-17T10:00:00', 0, 'Central'),
                ('2026-10-18T00:00:00', 0, 'Central'),
                ('2026-10-19T08:00:00', None, 'Central'),
                ('2026-10-19T07:59:59.999', 0, 'Central'),
                ('2026-10-19T06:00:00', 1, 'Central'),
                ('2026-10-19T06:00:00', 0, 'Tarma'),
            ]
            db.executemany('INSERT INTO RegistroIngresos VALUES (1, ?, ?, ?, 1)', filas)
            class Cursor:
                def execute(self, sql, params):
                    sql = sql.replace('ISNULL(', 'IFNULL(').replace("DATEDIFF(day,", "DATEDIFF('day',")
                    sql = re.sub(r'CAST\(R.FechaHora AS DATE\)', 'date(R.FechaHora)', sql, flags=re.I)
                    sql = re.sub(r"CAST\((R.FechaHora|'[^']+') AS TIME\)", r'hora(\1)', sql, flags=re.I)
                    sql = sql.replace('CONVERT(char(7), R.FechaHora, 120)', 'substr(R.FechaHora,1,7)')
                    sql = sql.replace('DATEPART(hour, R.FechaHora)', "CAST(strftime('%H',R.FechaHora) AS INTEGER)")
                    self.individual = 'AS total_ingresos' in sql
                    self.coverage = 'AS total_registros' in sql
                    self.cursor = db.execute(sql, params)
                def fetchone(self):
                    row = list(self.cursor.fetchone())
                    for i in ((1,2) if self.individual else (0,1)):
                        if row[i]: row[i] = datetime.fromisoformat(row[i])
                    return row
                def fetchall(self): return self.cursor.fetchall()
            @contextmanager
            def fuente(**kwargs): yield None, Cursor()
            with patch.object(queries,'get_db_cursor',fuente):
                result=queries.contar_ingresos_alumno_por_codigo('9900000001', rol='Supervisor', sede_permitida='Central', informe=True)
        self.assertEqual(result['total_ingresos'],6)
        self.assertEqual(result['cobertura_sistema']['total_pruebas_excluidas'],1)
        self.assertEqual(result['atipicos']['fin_de_semana'],2)
        self.assertEqual(result['atipicos']['fuera_horario'],3)
        self.assertEqual(result['atipicos']['total_sin_duplicar'],4)
        for grupo in ('mensual','horarios','dias_semana','salas'):
            self.assertEqual(sum(f['visitas'] for f in result[grupo]),6)
        self.assertEqual(dict((f['grupo'],f['visitas']) for f in result['dias_semana'])[6],1)
