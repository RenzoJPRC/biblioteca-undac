"""Ruta completa con resumen autorizado y proveedor sustituido; nunca llama a Internet."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch,MagicMock
from tests import test_chat_conversacional as base
from llm.services import coordinador_asistente as coor
from llm.services import proveedor_llm as provider

RESUMEN=dict(sede='Central',periodo=dict(fecha_inicio='2026-10-01',fecha_fin='2026-10-31'),fuente='Resumen agregado ficticio de prueba',limites_cobertura='Periodo parcial; no certificado técnicamente',cobertura=dict(estado='parcial',ultimo_dia_observado_en_periodo='2026-10-21'),metricas=dict(total_ingresos=7056,dias_con_registros=14),mensaje='Texto técnico extenso de prueba con nombre_interno.csv')

class TestGeminiChatIntegrado(unittest.TestCase):
    setUp=base.TestChatConversacional.setUp
    enviar=base.TestChatConversacional.enviar

    def test_regresion_aclaracion_ingresos_completa_afluencia_pendiente(self):
        pregunta='Resume la afluencia de octubre de 2026 en Central e indica hasta qué fecha hay datos.'
        with patch.object(coor,'consultar_afluencia_historica',return_value=deepcopy(RESUMEN)) as sql:
            primero=self.enviar(pregunta).get_json()
            self.assertEqual(primero['status'],'requires_input')
            sql.assert_not_called();self.llm.assert_not_called()
            with self.client.session_transaction() as ses:
                contexto=ses.get('asistente_contexto',{})
                self.assertEqual(contexto.get('fecha_inicio'),'2026-10-01')
                self.assertEqual(contexto.get('fecha_fin'),'2026-10-31')
                self.assertEqual(contexto.get('sede'),'Central')
            segundo=self.enviar('ingresos').get_json()
            self.assertEqual(segundo['status'],'success')
            self.assertEqual(segundo['intencion'],'afluencia_historica')
            self.assertNotEqual(primero['mensaje'],segundo['mensaje'])
            self.assertIn('21/10/2026',segundo['resumen_visible'])
            sql.assert_called_once_with(fecha_inicio='2026-10-01',fecha_fin='2026-10-31',sede='Central',rol='SuperAdmin',sede_permitida=None)
            self.assertEqual(self.llm.return_value.responder_chat.call_count,1)
            evidencia=json.loads(self.llm.return_value.responder_chat.call_args.args[0][1]['content'])
            self.assertEqual(evidencia['periodo_solicitado'],RESUMEN['periodo'])
            self.assertEqual(evidencia['sede'],'Central')
            self.assertEqual(evidencia['ingresos_observados'],7056)
            self.assertNotIn('pregunta',evidencia)
            with self.client.session_transaction() as ses:
                self.assertNotIn('tema_pendiente',ses.get('asistente_contexto',{}))

    def test_variaciones_respuesta_ingresos_resuelven_aclaracion(self):
        for frase in ('ingresos.', 'solo ingresos', 'resumen de ingresos', 'los ingresos'):
            with self.subTest(frase=frase):
                self.llm.reset_mock()
                with patch.object(coor,'consultar_afluencia_historica',return_value=deepcopy(RESUMEN)) as sql:
                    self.enviar('Nueva consulta')
                    self.llm.reset_mock()
                    p1 = self.enviar('Resume la afluencia de octubre de 2026 en Central e indica hasta qué fecha hay datos.').get_json()
                    self.assertEqual(p1['status'], 'requires_input')
                    sql.assert_not_called()
                    self.llm.assert_not_called()
                    p2 = self.enviar(frase).get_json()
                    self.assertEqual(p2['status'], 'success')
                    self.assertEqual(p2['intencion'], 'afluencia_historica')
                    self.assertIn('21/10/2026', p2['resumen_visible'])
                    sql.assert_called_once()
                    self.assertEqual(self.llm.return_value.responder_chat.call_count, 1)

    def test_aclaracion_pendiente_con_fallo_gemini_no_repite_pregunta(self):
        with patch.object(coor,'consultar_afluencia_historica',return_value=deepcopy(RESUMEN)) as sql:
            primero=self.enviar('Resume la afluencia de octubre de 2026 en Central e indica hasta qué fecha hay datos.').get_json()
            self.llm.assert_not_called();sql.assert_not_called()
            self.llm.return_value.responder_chat.side_effect=provider.LLMAPIError('fallo ficticio',429)
            segundo=self.enviar('INGRESOS').get_json()
            self.assertEqual(segundo['status'],'success')
            self.assertNotEqual(primero['mensaje'],segundo['mensaje'])
            self.assertEqual(segundo['error_proveedor'],'cuota')
            self.assertIn('21/10/2026',segundo['resumen_visible'])
            self.assertEqual(sql.call_count,1)
            self.assertEqual(sql.call_args.kwargs['sede'],'Central')
            self.assertEqual(sql.call_args.kwargs['fecha_inicio'],'2026-10-01')
            self.assertEqual(sql.call_args.kwargs['fecha_fin'],'2026-10-31')
            self.assertEqual(self.llm.return_value.responder_chat.call_count,1)

    def test_cambio_de_tema_y_nueva_consulta_borran_eleccion_pendiente(self):
        for cambio in ('Necesito saber sobre','Nueva consulta','¿Qué libros fueron más prestados?'):
            with self.subTest(cambio=cambio):
                self.enviar('Resume la afluencia de octubre de 2026 en Central e indica hasta qué fecha hay datos.')
                respuesta=self.enviar(cambio).get_json()
                self.assertEqual(respuesta['contexto'],{})
                with self.client.session_transaction() as ses:
                    self.assertNotIn('tema_pendiente',ses.get('asistente_contexto',{}))
                    self.assertNotIn('fecha_inicio',ses.get('asistente_contexto',{}))
        self.llm.assert_not_called()

    def test_exito_no_modifica_cifras_y_envia_solo_hechos_permitidos(self):
        self.llm.return_value.responder_chat.return_value={'mensaje':'El periodo es parcial y la captura no está certificada técnicamente. No permite concluir ausencia de visitas fuera de lo observado.'}
        with patch.object(coor,'consultar_afluencia_historica',return_value=deepcopy(RESUMEN)):
            r=self.enviar('Resume la afluencia de la sede Central en octubre de 2026').get_json()
        self.assertEqual(r['origen'],'llm_externo');self.assertEqual(r['proveedor'],'gemini')
        self.assertEqual(r['datos']['metricas'],RESUMEN['metricas'])
        self.assertIn('7.056',r['resumen_visible']);self.assertIn('14 días',r['resumen_visible']);self.assertIn('21/10/2026',r['resumen_visible'])
        self.assertNotIn('nombre_interno.csv',r['resumen_visible'])
        self.assertIn('metodologia_visible',r)
        evidencia=json.loads(self.llm.return_value.responder_chat.call_args.args[0][1]['content'])
        self.assertEqual(set(evidencia),{'sede','periodo_solicitado','ingresos_observados','dias_con_registros','ultimo_dia_observado','periodo_parcial','captura_certificada_tecnicamente'})
        self.assertFalse(evidencia['captura_certificada_tecnicamente'])
        self.assertNotIn('nombre_interno.csv',json.dumps(evidencia))

    def test_timeout_cuota_y_respuesta_invalida_conservan_respaldo(self):
        casos=[(provider.LLMTimeoutError(),'timeout'),(provider.LLMAPIError('error ficticio',429),'cuota'),({'mensaje':''},'respuesta_invalida'),({'mensaje':'Se observaron 999 ingresos.'},'respuesta_no_verificable'),({'mensaje':'x'*601},'respuesta_no_verificable')]
        for valor,categoria in casos:
            with self.subTest(categoria=categoria,valor=type(valor).__name__):
                self.llm.return_value.responder_chat.side_effect=valor if isinstance(valor,Exception) else None
                self.llm.return_value.responder_chat.return_value=valor if isinstance(valor,dict) else {}
                with patch.object(coor,'consultar_afluencia_historica',return_value=deepcopy(RESUMEN)):
                    r=self.enviar('Resume la afluencia de la sede Central en octubre de 2026').get_json()
                self.assertEqual(r['error_proveedor'],categoria)
                self.assertNotEqual(r['origen'],'llm_externo')
                self.assertNotIn('explicacion_llm',r)
                self.assertNotIn('proveedor',r)
                self.assertEqual(r['datos']['metricas']['total_ingresos'],7056)
                self.assertIn('7.056',r['resumen_visible'])
                self.assertTrue(r['causa_proveedor_visible'])
                self.assertNotIn('error ficticio',r['causa_proveedor_visible'])

    def test_adaptador_rechaza_respuesta_truncada_vacia_y_formato_invalido(self):
        cfg=base.SimpleNamespace(esta_disponible=True,api_base='https://example.invalid',api_key='ficticia',modelo='modelo-conservado',timeout=10)
        for cuerpo in ({'choices':[{'finish_reason':'length','message':{'content':'Texto parcial'}}]}, {'choices':[{'message':{'content':''}}]}, {'choices':[]}):
            with self.subTest(formato=list(cuerpo)):
                respuesta=MagicMock(status_code=200);respuesta.json.return_value=cuerpo
                with patch('requests.post',return_value=respuesta) as post:
                    with self.assertRaises(provider.LLMError): provider.ProveedorLLM(cfg).responder_chat([{'role':'user','content':'Resumen agregado ficticio'}])
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.kwargs['json']['model'],'modelo-conservado')
                self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_transicion_agregada_a_individual_conserva_periodo_y_sede_en_sql(self):
        pregunta1 = 'Resume la afluencia de octubre de 2026 en Central e indica hasta qué fecha hay datos.'
        with patch.object(coor, 'consultar_afluencia_historica', return_value=deepcopy(RESUMEN)) as sql, \
             patch.object(coor, 'contar_ingresos_alumno_por_codigo') as tool_alumno:
            tool_alumno.return_value = {
                'status': 'success',
                'codigo_mascarado': '99****01',
                'total_ingresos': 5,
                'dias_con_visita': 3,
                'primer_ingreso': '2026-10-05T09:15:00',
                'ultimo_ingreso': '2026-10-20T14:30:00',
                'escuela': 'Ingeniería de Sistemas',
                'periodo_consultado': {'fecha_inicio': '2026-10-01', 'fecha_fin': '2026-10-31'},
                'fuente': 'RegistroIngresos + Alumnos',
                'fecha_corte': '2026-10-22',
                'mensaje': 'Informe local del alumno 99****01 (del 2026-10-01 al 2026-10-31): 5 visitas en 3 días distintos. Periodo consultado: del 2026-10-01 al 2026-10-31. Sede: Central. Primera visita en periodo: 05/10/2026. Última visita en periodo: 20/10/2026. Corte observado de la fuente autorizada: 22/10/2026.'
            }

            # Turno 1: Aclaración
            p1 = self.enviar(pregunta1).get_json()
            self.assertEqual(p1['status'], 'requires_input')

            # Turno 2: Respuesta "ingresos" completa consulta agregada
            p2 = self.enviar('ingresos').get_json()
            self.assertEqual(p2['status'], 'success')
            sql.assert_called_once()
            self.assertEqual(self.llm.return_value.responder_chat.call_count, 1)

            # Turno 3: Consulta al alumno "en ese periodo"
            p3 = self.enviar('Informe del alumno 9900000001 en ese periodo').get_json()
            self.assertEqual(p3['status'], 'success')
            self.assertEqual(p3['intencion'], 'conteo_ingresos_alumno')
            self.assertTrue(p3.get('consulta_confidencial'))

            # Se verifica que SQL fue invocado con el rango y sede heredados del contexto
            tool_alumno.assert_called_once()
            call_kwargs = tool_alumno.call_args.kwargs
            self.assertEqual(call_kwargs.get('fecha_inicio'), '2026-10-01')
            self.assertEqual(call_kwargs.get('fecha_fin'), '2026-10-31')
            self.assertEqual(call_kwargs.get('sede_permitida'), 'Central')
            self.assertEqual(call_kwargs.get('codigo_matricula'), '9900000001')

            # Se verifica que el mensaje o resumen_visible muestre explícitamente el periodo y distinga el corte
            texto_visible = (p3.get('resumen_visible') or '') + ' ' + (p3.get('mensaje') or '')
            self.assertIn('2026-10-01', texto_visible)
            self.assertIn('2026-10-31', texto_visible)
            self.assertIn('99****01', texto_visible)
            self.assertNotIn('9900000001', texto_visible)

            # Gemini NO debe haberse llamado para el alumno (call_count sigue siendo 1 del Turno 2)
            self.assertEqual(self.llm.return_value.responder_chat.call_count, 1)

    def test_consulta_alumno_en_ese_periodo_sin_fechas_previas_pide_aclaracion(self):
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo') as tool_alumno:
            self.enviar('Nueva consulta')
            p = self.enviar('Informe del alumno 9900000001 en ese periodo').get_json()
            self.assertEqual(p['status'], 'requires_input')
            self.assertIn('periodo', p['mensaje'].lower())
            tool_alumno.assert_not_called()
            self.llm.assert_not_called()

    def test_regresion_secuencia_cuatro_pasos_resumen_afluencia_octubre_2026_central(self):
        """
        Prueba de regresión de 4 pasos (datos simulados en mock local):
        1. Usuario: 'Resumen de afluencia de octubre de 2026 en Central'.
        2. El asistente no debe preguntar el año (ya indicado: 2026) y debe conservar
           octubre de 2026 y sede Central; pregunta solo el tema (aclaración).
        3. Usuario: 'ingresos'.
        4. El asistente resuelve la consulta agregada para Central (01/10/2026 al 31/10/2026),
           responde con periodo y último día observado (21/10/2026), no repite aclaración
           y llama a Gemini como máximo 1 vez solo para explicar resultados SQL.
        """
        with patch.object(coor, 'consultar_afluencia_historica', return_value=deepcopy(RESUMEN)) as sql:
            self.enviar('Nueva consulta')
            self.llm.reset_mock()

            # Paso 1: Usuario solicita resumen de afluencia con año, mes y sede
            p1 = self.enviar('Resumen de afluencia de octubre de 2026 en Central').get_json()

            # Paso 2: Asistente pide aclaración de tema; NO debe preguntar el año
            self.assertEqual(p1['status'], 'requires_input')
            self.assertNotIn('año', p1['mensaje'].lower())
            self.assertNotIn('ano', p1['mensaje'].lower())
            self.assertTrue(any(w in p1['mensaje'].lower() for w in ('ingresos', 'afluencia')))
            sql.assert_not_called()
            self.llm.assert_not_called()

            # Se verifica que no se perdieron año, mes ni sede en contexto
            with self.client.session_transaction() as ses:
                contexto = ses.get('asistente_contexto', {})
                self.assertEqual(contexto.get('fecha_inicio'), '2026-10-01')
                self.assertEqual(contexto.get('fecha_fin'), '2026-10-31')
                self.assertEqual(contexto.get('sede'), 'Central')
                self.assertEqual(contexto.get('tema_pendiente'), 'afluencia_agregada')

            # Paso 3: Usuario aclara el tema: 'ingresos'
            p2 = self.enviar('ingresos').get_json()

            # Paso 4: Asistente resuelve consulta agregada y no repite pregunta
            self.assertEqual(p2['status'], 'success')
            self.assertEqual(p2['intencion'], 'afluencia_historica')
            self.assertFalse(p2.get('requiere_aclaracion', False))
            self.assertNotEqual(p1['mensaje'], p2['mensaje'])

            # Verifica presencia del periodo y último día observado
            resumen_vis = p2.get('resumen_visible', '')
            self.assertIn('21/10/2026', resumen_vis)
            self.assertIn('Central', resumen_vis)

            # Consulta SQL ejecutada exactamente una vez con parámetros preservados
            sql.assert_called_once_with(
                fecha_inicio='2026-10-01',
                fecha_fin='2026-10-31',
                sede='Central',
                rol='SuperAdmin',
                sede_permitida=None
            )

            # Gemini llamado como máximo una vez (exactamente 1 llamada mockeada)
            self.assertLessEqual(self.llm.return_value.responder_chat.call_count, 1)
            self.assertEqual(self.llm.return_value.responder_chat.call_count, 1)

            # Verificación de privacidad: sin datos personales enviados a Gemini
            evidencia = json.loads(self.llm.return_value.responder_chat.call_args.args[0][1]['content'])
            self.assertEqual(evidencia['sede'], 'Central')
            self.assertEqual(evidencia['periodo_solicitado'], RESUMEN['periodo'])
            self.assertEqual(evidencia['ingresos_observados'], 7056)
            self.assertNotIn('pregunta', evidencia)
            self.assertNotIn('alumno', json.dumps(evidencia))
            self.assertNotIn('matricula', json.dumps(evidencia))

            # Contexto limpio de tema_pendiente
            with self.client.session_transaction() as ses:
                self.assertNotIn('tema_pendiente', ses.get('asistente_contexto', {}))

    def test_secuencia_consulta_individual_mismo_alumno_rango_junio_octubre(self):
        """
        Prueba de regresión con matrícula ficticia 9900000001:
        1. Se consulta a un alumno con código ficticio y periodo (septiembre de 2026 en Central).
        2. Usuario: 'y el mismo alumno, desde junio hasta octubre, ¿cuántos ingresos tiene?'
           - Conserva año (2026), sede (Central) y resuelve el rango solicitado (01/06/2026 al 31/10/2026).
           - No vuelve a preguntar el año porque ya está en contexto.
           - No reutiliza ni guarda la matrícula por privacidad; solicita el código explícitamente.
           - No ejecuta SQL en el paso 2.
        3. Usuario: '9900000001'
           - Ejecuta SQL aplicando el rango completo (01/06/2026 al 31/10/2026) y sede Central.
           - Muestra rango aplicado y corte observado por separado.
           - 0 llamadas a Gemini (sin envío de datos personales ni matrícula al LLM).
        """
        tool_retorno = {
            'status': 'success',
            'codigo_mascarado': '99****01',
            'total_ingresos': 8,
            'dias_con_visita': 4,
            'primer_ingreso': '2026-06-10T10:00:00',
            'ultimo_ingreso': '2026-10-15T16:00:00',
            'escuela': 'Ingeniería',
            'periodo_consultado': {'fecha_inicio': '2026-06-01', 'fecha_fin': '2026-10-31'},
            'fuente': 'RegistroIngresos + Alumnos',
            'fecha_corte': '2026-10-21',
            'mensaje': 'Informe del alumno 99****01: 8 visitas en 4 días distintos · Periodo consultado: del 2026-06-01 al 2026-10-31 (Central) · Corte observado: 21/10/2026.',
            'datos': {
                'total_ingresos': 8,
                'dias_con_visita': 4,
                'codigo_mascarado': '99****01',
                'periodo_consultado': {'fecha_inicio': '2026-06-01', 'fecha_fin': '2026-10-31'},
                'sede': 'Central',
                'fecha_corte': '2026-10-21'
            }
        }
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo', return_value=tool_retorno) as tool_alumno:
            self.enviar('Nueva consulta')

            # Paso 1: Consulta inicial alumno ficticio en septiembre 2026 en Central
            p1 = self.enviar('Informe del alumno 9900000001 en septiembre de 2026 en Central').get_json()
            self.assertEqual(p1['status'], 'success')
            self.assertEqual(tool_alumno.call_count, 1)
            # Verificar que la matrícula NO se guardó en la sesión
            with self.client.session_transaction() as ses:
                ctx_ses = ses.get('asistente_contexto', {})
                self.assertNotIn('codigo', ctx_ses)
                self.assertNotIn('codigo_matricula', ctx_ses)
                self.assertNotIn('9900000001', str(ctx_ses))

            # Paso 2: Usuario dice 'y el mismo alumno, desde junio hasta octubre, ¿cuántos ingresos tiene?'
            p2 = self.enviar('y el mismo alumno, desde junio hasta octubre, ¿cuántos ingresos tiene?').get_json()
            # No debe pedir el año (ya que 2026 está en contexto)
            self.assertNotIn('año', p2.get('mensaje', '').lower())
            self.assertEqual(p2['status'], 'requires_input')
            self.assertEqual(p2['intencion'], 'conteo_ingresos_alumno')
            # Debe solicitar el código explícitamente por privacidad
            self.assertIn('privacidad', p2['mensaje'].lower())
            self.assertIn('código', p2['mensaje'].lower())
            # Debe mencionar el rango resuelto del 01/06/2026 al 31/10/2026
            self.assertIn('01/06/2026', p2['mensaje'])
            self.assertIn('31/10/2026', p2['mensaje'])
            # No debe ejecutar SQL en este paso porque falta el código
            self.assertEqual(tool_alumno.call_count, 1)

            # Verificar que el contexto preserva el rango y la sede pero NO la matrícula
            with self.client.session_transaction() as ses:
                ctx_ses = ses.get('asistente_contexto', {})
                self.assertEqual(ctx_ses.get('fecha_inicio'), '2026-06-01')
                self.assertEqual(ctx_ses.get('fecha_fin'), '2026-10-31')
                self.assertEqual(ctx_ses.get('sede'), 'Central')
                self.assertNotIn('codigo', ctx_ses)
                self.assertNotIn('codigo_matricula', ctx_ses)
                self.assertNotIn('9900000001', str(ctx_ses))

            # Paso 3: Usuario envía el código
            p3 = self.enviar('9900000001').get_json()
            self.assertEqual(p3['status'], 'success')
            self.assertEqual(p3['intencion'], 'conteo_ingresos_alumno')
            # SQL ejecutado por segunda vez con el rango completo solicitado y la sede
            self.assertEqual(tool_alumno.call_count, 2)
            call2_kwargs = tool_alumno.call_args.kwargs
            self.assertEqual(call2_kwargs.get('codigo_matricula'), '9900000001')
            self.assertEqual(call2_kwargs.get('fecha_inicio'), '2026-06-01')
            self.assertEqual(call2_kwargs.get('fecha_fin'), '2026-10-31')
            self.assertEqual(call2_kwargs.get('sede_permitida'), 'Central')

            # Muestra rango realmente aplicado y corte observado por separado
            resumen_vis = p3.get('resumen_visible', '')
            self.assertIn('del 2026-06-01 al 2026-10-31', resumen_vis)
            self.assertIn('Corte observado: 21/10/2026', resumen_vis)
            self.assertIn('99****01', resumen_vis)
            self.assertNotIn('9900000001', resumen_vis)

            # Privacidad LLM: Cero llamadas a Gemini
            self.assertEqual(self.llm.return_value.responder_chat.call_count, 0)

    def test_esperando_anio_interpreta_2026_como_anio_no_como_matricula_y_aplica_rango_completo(self):
        """
        Si está esperando el año con mes_pendiente (6) y mes_fin_pendiente (10):
        - '2026' se interpreta como año, NUNCA como matrícula.
        - Aplica el rango completo (01/06/2026 al 31/10/2026), no solo junio.
        - Si falta el código, lo solicita explícitamente antes de consultar SQL.
        """
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo') as tool_alumno:
            self.enviar('Nueva consulta')
            with self.client.session_transaction() as ses:
                ses['asistente_contexto'] = {
                    'intencion': 'conteo_ingresos_alumno',
                    'mes_pendiente': '6',
                    'mes_fin_pendiente': '10',
                    'sede': 'Central',
                    'aclaracion_pendiente': 'anio'
                }

            p = self.enviar('2026').get_json()
            # Debe interpretar 2026 como año, no como código de alumno
            self.assertEqual(p['status'], 'requires_input')
            self.assertEqual(p['intencion'], 'conteo_ingresos_alumno')
            self.assertIn('código', p['mensaje'].lower())
            self.assertIn('01/06/2026', p['mensaje'])
            self.assertIn('31/10/2026', p['mensaje'])
            # No ejecuta SQL
            tool_alumno.assert_not_called()

            # En el contexto se guardó el rango completo
            with self.client.session_transaction() as ses:
                ctx_ses = ses.get('asistente_contexto', {})
                self.assertEqual(ctx_ses.get('fecha_inicio'), '2026-06-01')
                self.assertEqual(ctx_ses.get('fecha_fin'), '2026-10-31')
                self.assertEqual(ctx_ses.get('sede'), 'Central')
                self.assertNotIn('codigo', ctx_ses)
                self.assertNotIn('codigo_matricula', ctx_ses)

    def test_prevencion_consulta_alumno_con_rango_incompleto(self):
        """
        Prevención de consulta con rango incompleto:
        Si se consulta un alumno con código pero con mes sin año y sin contexto,
        debe requerir aclaración del año y NO ejecutar SQL.
        """
        with patch.object(coor, 'contar_ingresos_alumno_por_codigo') as tool_alumno:
            self.enviar('Nueva consulta')
            p = self.enviar('Informe del alumno 9900000001 desde junio hasta octubre').get_json()
            self.assertEqual(p['status'], 'requires_input')
            self.assertIn('año', p['mensaje'].lower())
            # Prevención: SQL no debe ejecutarse con rango incompleto
            tool_alumno.assert_not_called()
            # Privacidad: Matrícula no debe guardarse en contexto
            with self.client.session_transaction() as ses:
                ctx_ses = ses.get('asistente_contexto', {})
                self.assertNotIn('codigo', ctx_ses)
                self.assertNotIn('codigo_matricula', ctx_ses)
                self.assertNotIn('9900000001', str(ctx_ses))
                self.assertEqual(ctx_ses.get('mes_pendiente'), '6')
                self.assertEqual(ctx_ses.get('mes_fin_pendiente'), '10')

