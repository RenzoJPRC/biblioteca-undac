"""Chat con servicios sustituidos y datos ficticios; no requiere red ni BD."""
from copy import deepcopy
from types import SimpleNamespace
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from app import app
from llm.services import chat_conversacional as chat
from llm.services import coordinador_asistente as coor
from llm.services import proveedor_llm as provider


class TestChatConversacional(unittest.TestCase):
    def setUp(self):
        for target in ('pyodbc.connect','pymssql.connect','requests.sessions.Session.request','smtplib.SMTP'):
            p=patch(target,side_effect=AssertionError('Servicio real bloqueado'));p.start();self.addCleanup(p.stop)
        for target,kwargs in [('app.get_db_connection',{'return_value':None}),
                              ('llm.services.chat_conversacional.obtener_gestor_metricas',{}),
                              ('llm.services.chat_conversacional.obtener_configuracion',{'return_value':SimpleNamespace(esta_disponible=True,proveedor='gemini',modelo='modelo-ficticio',api_key='ficticia')}),
                              ('llm.services.chat_conversacional.ProveedorLLM',{})]:
            p=patch(target,**kwargs);obj=p.start();self.addCleanup(p.stop)
            if target.endswith('ProveedorLLM'):self.llm=obj
            if target.endswith('obtener_configuracion'):self.config=obj
            if target.endswith('obtener_gestor_metricas'):self.metrics=obj
        self.client=app.test_client()
        with self.client.session_transaction() as s:
            s.update(admin_user='ficticio',admin_rol='SuperAdmin',admin_sede='Central',csrf_token='ficticio')
        self.llm.return_value.responder_chat.return_value={'mensaje':'Los resultados reflejan únicamente el periodo consultado.','usage':{'total_tokens':7}}

    def enviar(self,texto):
        return self.client.post('/admin/api/asistente/consultar',json={'pregunta':texto},headers={'X-CSRFToken':'ficticio'})

    def test_secuencia_captura_cambio_de_tema_no_recicla_horas(self):
        for modo in ('activo','error','desactivado'):
            for siguiente in ('¿Qué libros fueron más prestados?', 'necesito saber sobre', '¿Y qué libros fueron más prestados?'):
                with self.subTest(modo=modo,siguiente=siguiente):
                    with self.client.session_transaction() as ses: ses.pop('asistente_contexto',None)
                    self.config.return_value.esta_disponible = modo != 'desactivado'
                    self.llm.return_value.responder_chat.side_effect = provider.LLMTimeoutError() if modo == 'error' else None
                    datos=dict(fecha_inicio='2026-09-01',fecha_fin='2026-09-30',horas_top=[dict(hora=13,total_ingresos=17)])
                    with patch.object(coor,'obtener_horas_mayor_afluencia',return_value=datos) as tool:
                        primero=self.enviar('Horas de mayor afluencia de Central en septiembre de 2026').get_json()
                        self.assertEqual(primero['status'],'success')
                        self.assertEqual(primero['datos']['horas_top'][0]['total_ingresos'],17)
                        self.llm.reset_mock()
                        segundo=self.enviar(siguiente).get_json()
                        self.assertEqual(tool.call_count,1,'Una intención nueva volvió a ejecutar la consulta anterior')
                        self.assertEqual(segundo['datos'],{})
                        self.assertNotIn('horas_top',json.dumps(segundo))
                        self.assertNotIn('2026-09',json.dumps(segundo))
                        if 'libros' in siguiente:
                            self.assertIn('no está disponible',segundo['mensaje'])
                        else:
                            self.assertEqual(segundo['status'],'requires_input')
                            self.assertIn('qué',segundo['mensaje'].lower())
                        self.llm.assert_not_called()
                        with self.client.session_transaction() as ses:
                            self.assertEqual(ses.get('asistente_contexto'),{})

    def test_secuencia_captura_setiembre_y_octubre_heredan_solo_continuacion(self):
        for frase in ('De setiembre de 2026','setiembre 2026','septiembre de 2026','DE SETIEMBRE DE 2026'):
            for continuacion in ('¿y en octubre?','¿Y EN OCTUBRE?','y en octubre'):
                with self.subTest(frase=frase,continuacion=continuacion):
                    with self.client.session_transaction() as ses: ses.pop('asistente_contexto',None)
                    def resultado(**kwargs):
                        return dict(fecha_inicio=kwargs['fecha_inicio'],fecha_fin=kwargs['fecha_fin'],horas_top=[])
                    with patch.object(coor,'obtener_horas_mayor_afluencia',side_effect=resultado) as tool:
                        self.assertEqual(self.enviar('Horas de mayor afluencia de Central').get_json()['status'],'requires_input')
                        septiembre=self.enviar(frase).get_json()
                        self.assertEqual(septiembre['status'],'success')
                        self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-09-01')
                        self.assertEqual(tool.call_args.kwargs['fecha_fin'],'2026-09-30')
                        octubre=self.enviar(continuacion).get_json()
                        self.assertEqual(octubre['status'],'success')
                        self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-10-01')
                        self.assertEqual(tool.call_args.kwargs['fecha_fin'],'2026-10-31')
                        self.assertEqual(tool.call_args.kwargs['sede_permitida'],'Central')
                        self.assertEqual(tool.call_count,2)
                        reinicio=self.enviar('Nueva consulta').get_json()
                        self.assertEqual(reinicio['datos'],{})
                        with self.client.session_transaction() as ses: self.assertEqual(ses.get('asistente_contexto'),{})
                        sin_contexto=self.enviar('¿y en octubre?').get_json()
                        self.assertEqual(sin_contexto['status'],'requires_input')
                        self.assertEqual(tool.call_count,2)

    def test_frase_exacta_captura_afluencia_y_cobertura_sin_resultado_anterior(self):
        def resultado(**kwargs):
            return dict(periodo=dict(fecha_inicio=kwargs['fecha_inicio'],fecha_fin=kwargs['fecha_fin']),sede=kwargs['sede'],fuente='Fuente simulada',limites_cobertura='Sin garantía de captura completa',mensaje='Resumen agregado simulado',total_ingresos=4)
        with patch.object(coor,'consultar_afluencia_historica',side_effect=resultado) as tool:
            primero=self.enviar('Quiero consultar la afluencia de sede Central').get_json()
            self.assertEqual(primero['status'],'requires_input');tool.assert_not_called()
            septiembre=self.enviar('De setiembre de 2026').get_json()
            self.assertEqual(septiembre['status'],'success')
            self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-09-01')
            tool.side_effect=ValueError('Datos insuficientes en el periodo solicitado')
            octubre=self.enviar('¿Y en octubre?').get_json()
            self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-10-01')
            self.assertEqual(tool.call_args.kwargs['sede'],'Central')
            self.assertEqual(octubre['status'],'error')
            self.assertNotIn('total_ingresos',octubre['datos'])
            self.assertNotIn('Resumen agregado simulado',octubre['mensaje'])

    def test_ayuda_no_publica_capacidades_inventadas_por_gemini(self):
        self.llm.return_value.responder_chat.return_value={'mensaje':'Hola. Puedo informar los títulos más prestados, disponibilidad de ejemplares y movimiento bibliográfico.'}
        for rol in ('SuperAdmin','Consultor'):
            with self.subTest(rol=rol):
                with self.client.session_transaction() as ses: ses['admin_rol']=rol
                respuesta=self.enviar('Hola').get_json()
                self.assertEqual(respuesta['origen'],'llm_externo')
                self.assertIn('ingresos',respuesta['mensaje'])
                for promesa in ('prestados','ejemplares','bibliográfico'):
                    self.assertNotIn(promesa,respuesta['mensaje'])
                if rol == 'Consultor': self.assertNotIn('matrícula',respuesta['mensaje'])
                else: self.assertIn('matrícula',respuesta['mensaje'])

    def test_nueva_intencion_reemplaza_periodo_anterior(self):
        with patch.object(coor,'obtener_horas_mayor_afluencia',return_value=dict(fecha_inicio='2026-09-01',fecha_fin='2026-09-30',horas_top=[])) as horas:
            self.enviar('Horas de mayor afluencia de Central en septiembre de 2026')
            with patch.object(coor,'obtener_resumen_ingresos') as resumen:
                respuesta=self.enviar('Quiero un resumen de ingresos').get_json()
                self.assertEqual(respuesta['status'],'requires_input')
                self.assertIn('sede',respuesta['parametros_faltantes'])
                self.assertIn('periodo',respuesta['parametros_faltantes'])
                resumen.assert_not_called();self.assertEqual(horas.call_count,1)

    def test_cabecera_configurada_no_equivale_a_respuesta_exitosa(self):
        cfg=SimpleNamespace(esta_disponible=True,proveedor='gemini',modelo='modelo-ficticio',api_key='secreto-ficticio',activado=True)
        with patch('llm.services.proveedor_llm.obtener_configuracion',return_value=cfg):
            html=self.client.get('/admin/asistente').get_data(as_text=True)
        self.assertIn('Proveedor LLM configurado (gemini)',html)
        self.assertIn('modelo-ficticio',html)
        self.assertIn('la disponibilidad se verifica en cada solicitud',html)
        self.assertNotIn('secreto-ficticio',html)

    def test_cabecera_informa_variable_faltante_sin_credenciales(self):
        cfg=SimpleNamespace(esta_disponible=False,proveedor='gemini',modelo='modelo-ficticio',api_key='',activado=True)
        with patch('llm.services.proveedor_llm.obtener_configuracion',return_value=cfg):
            html=self.client.get('/admin/asistente').get_data(as_text=True)
        self.assertIn('Proveedor no configurado o desactivado',html)
        self.assertIn('Revisar: LLM_API_KEY',html)
        self.assertNotIn('Respondió el motor local',html)

    def test_tarjetas_genericas_y_aclaracion_de_tres_turnos(self):
        html=self.client.get('/admin/asistente').get_data(as_text=True)
        for prohibido in ('2026-03-01', '2026-03-15', 'Juan Perez', 'Juan Pérez', 'Quispe'):
            self.assertNotIn(prohibido,html)
        for pregunta in ('Quiero consultar la afluencia de una sede', '¿Qué horas tuvieron más ingresos?', 'Quiero consultar el historial de visitas de un alumno', '¿Qué consultas puedes resolver?'):
            self.assertIn(pregunta,html)
        with patch.object(coor,'obtener_horas_mayor_afluencia') as tool:
            a=self.enviar('¿Qué horas tuvieron más ingresos?').get_json()
            self.assertEqual(a['status'],'requires_input')
            b=self.enviar('Central').get_json()
            self.assertNotIn('sede',b.get('parametros_faltantes',[]))
            c=self.enviar('del 1 al 2 de octubre').get_json()
            self.assertIn('año',c['mensaje']);tool.assert_not_called()
            tool.return_value=dict(fecha_inicio='2026-10-01',fecha_fin='2026-10-02',horas_top=[])
            d=self.enviar('2026').get_json()
            self.assertEqual(d['status'],'success')
            self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-10-01')
            self.assertEqual(tool.call_args.kwargs['fecha_fin'],'2026-10-02')
            self.assertEqual(tool.call_args.kwargs['sede_permitida'],'Central')

    def test_sede_durante_aclaracion_de_anio_no_pierde_mes(self):
        with patch.object(coor,'obtener_resumen_ingresos') as tool:
            self.enviar('Resumen de ingresos en junio')
            respuesta=self.enviar('Tarma').get_json()
            self.assertIn('año',respuesta['mensaje']);tool.assert_not_called()
            tool.return_value=dict(fecha_inicio='2026-06-01',fecha_fin='2026-06-30',total_ingresos=3)
            self.assertEqual(self.enviar('2026').get_json()['status'],'success')
            self.assertEqual(tool.call_args.kwargs['sede_permitida'],'Tarma')
            self.assertEqual(tool.call_args.kwargs['fecha_fin'],'2026-06-30')

    def test_personal_ambigua_con_codigo_solo_en_memoria_cliente(self):
        with patch.object(coor,'contar_ingresos_alumno_por_codigo',return_value=dict(total_ingresos=2,estado='con_ingresos_verificables',mensaje='Dos visitas verificadas en la fuente simulada.')) as tool:
            original='Informe del alumno 9900000001 del 1 al 2 de octubre'
            primero=self.enviar(original).get_json()
            self.assertEqual(primero['status'],'requires_input');tool.assert_not_called()
            with self.client.session_transaction() as ses:
                self.assertNotIn('9900000001',str(dict(ses)))
            segundo=self.client.post('/admin/api/asistente/consultar',json={'pregunta':'2026','pregunta_personal_pendiente':original},headers={'X-CSRFToken':'ficticio'}).get_json()
            self.assertEqual(segundo['status'],'success')
            self.assertEqual(tool.call_args.kwargs['codigo_matricula'],'9900000001')
            self.llm.assert_not_called();self.metrics.assert_not_called()

    def test_saludo_gemini_sin_herramientas_y_origen(self):
        self.llm.return_value.responder_chat.return_value={'mensaje':'Hola. Puedo orientarle sobre consultas de biblioteca.'}
        response=self.enviar('Hola')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['origen'],'llm_externo')
        self.assertEqual(response.get_json()['proveedor'],'gemini')
        self.assertNotIn('tools',self.llm.return_value.responder_chat.call_args.kwargs)

    def test_aclaracion_periodo_sede_y_seguimiento(self):
        with patch.object(coor,'obtener_horas_mayor_afluencia') as tool:
            first=self.enviar('¿Cuáles fueron las horas de mayor afluencia?').get_json()
            self.assertEqual(first['status'],'requires_input')
            self.assertIn('periodo',first['mensaje']);self.assertIn('sede',first['mensaje'])
            tool.assert_not_called();self.llm.assert_not_called()
            tool.return_value=dict(fecha_inicio='2026-10-01',fecha_fin='2026-10-02',horas_top=[dict(hora=10,total_ingresos=3,promedio_diario=1.5)])
            second=self.enviar('Central del 1 al 2 de octubre de 2026').get_json()
        self.assertEqual(second['status'],'success')
        self.assertEqual(tool.call_args.kwargs['sede_permitida'],'Central')
        self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-10-01')

    def test_agregados_preservados_y_pregunta_no_enviada(self):
        valores=dict(fecha_inicio='2026-10-01',fecha_fin='2026-10-02',total_ingresos=3,ingresos_por_sede={'Central':3})
        with patch.object(coor,'obtener_resumen_ingresos',return_value=deepcopy(valores)):
            result=self.enviar('Resumen de ingresos de Central del 1 al 2 de octubre de 2026').get_json()
        self.assertEqual(result['datos'],valores)
        self.assertIn('3',result['mensaje'])
        self.assertEqual(result['origen_detalle'],'biblioteca_explicada')
        self.assertTrue(result['consulta_sql_realizada'])
        enviados=json.dumps(self.llm.return_value.responder_chat.call_args.args)
        self.assertIn('total_ingresos',enviados)
        self.assertNotIn('Resumen de ingresos de Central',enviados)

    def test_aclaracion_de_anio_sin_repetir_mes(self):
        with patch.object(coor,'obtener_resumen_ingresos') as tool:
            first=self.enviar('Resumen de ingresos de Central en junio').get_json()
            self.assertEqual(first['status'],'requires_input');tool.assert_not_called()
            tool.return_value=dict(fecha_inicio='2026-06-01',fecha_fin='2026-06-30',total_ingresos=3)
            second=self.enviar('2026').get_json()
        self.assertEqual(second['status'],'success')
        self.assertEqual(tool.call_args.kwargs['fecha_inicio'],'2026-06-01')
        self.assertEqual(tool.call_args.kwargs['fecha_fin'],'2026-06-30')

    def test_explicacion_con_cifras_inventadas_rechazada(self):
        self.llm.return_value.responder_chat.return_value={'mensaje':'Se contabilizaron 999 visitas.'}
        with patch.object(coor,'obtener_resumen_ingresos',return_value=dict(fecha_inicio='2026-10-01',fecha_fin='2026-10-02',total_ingresos=3)):
            result=self.enviar('Resumen de ingresos de Central del 1 al 2 de octubre de 2026').get_json()
        self.assertNotIn('999',json.dumps(result))
        self.assertEqual(result['datos']['total_ingresos'],3)
        self.assertEqual(result['error_proveedor'],'respuesta_no_verificable')

    def test_fallback_clasificado_y_proveedor_desactivado(self):
        self.llm.return_value.responder_chat.side_effect=provider.LLMAPIError('error ficticio',429)
        result=self.enviar('Hola').get_json()
        self.assertEqual(result['error_proveedor'],'cuota')
        self.assertEqual(result['aviso_origen'],'Respuesta de respaldo; Gemini no respondió')
        self.assertNotEqual(result['origen'],'llm_externo')
        self.config.return_value.esta_disponible=False
        self.llm.reset_mock()
        self.enviar('Hola');self.llm.assert_not_called()

    def test_personal_pide_codigo_y_nunca_sale_a_proveedor_ni_metricas(self):
        with patch.object(coor,'contar_ingresos_alumno_por_codigo',return_value={'mensaje':'Informe ficticio','total_ingresos':2}) as tool:
            first=self.enviar('Dame un informe del alumno por su código de matrícula').get_json()
            self.assertEqual(first['status'],'requires_input');tool.assert_not_called()
            result=self.enviar('9900000001').get_json()
            self.assertEqual(result['origen'],'consulta_segura')
            self.assertTrue(result['consulta_confidencial'])
            self.assertFalse(result['consulta_sql_realizada'])
        self.llm.assert_not_called();self.metrics.assert_not_called()
        with self.client.session_transaction() as s:self.assertNotIn('9900000001',json.dumps(dict(s)))

    def test_permisos_y_sede_no_proceden_del_cliente(self):
        with self.client.session_transaction() as s:s.update(admin_rol='Consultor')
        with patch.object(coor,'contar_ingresos_alumno_por_codigo') as tool:
            response=self.enviar('Informe del alumno 9900000001')
            self.assertEqual(response.status_code,403);tool.assert_not_called()
        with self.client.session_transaction() as s:s.update(admin_rol='Supervisor',admin_sede='Tarma')
        with patch.object(coor,'contar_ingresos_alumno_por_codigo',return_value={'mensaje':'ficticio'}) as tool:
            response=self.client.post('/admin/api/asistente/consultar',json={'pregunta':'Informe del alumno 9900000001 en Central','rol':'SuperAdmin','sede':'Central'},headers={'X-CSRFToken':'ficticio'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(tool.call_args.kwargs['sede_permitida'],'Tarma')
        self.llm.assert_not_called();self.metrics.assert_not_called()

    def test_orientacion_no_envia_el_texto_original(self):
        self.enviar('Hola, soy una persona ficticia, ¿qué puedes hacer?')
        if self.llm.return_value.responder_chat.called:
            payload=json.dumps(self.llm.return_value.responder_chat.call_args.args)
            self.assertNotIn('persona ficticia',payload)
            self.assertNotIn('Hola, soy',payload)

    def test_errores_seguro_sin_cuerpo_sensible(self):
        for status,mensaje,esperado in [(400,'API key invalid: secreto','autenticacion'),(429,'quota exceeded secreto','cuota'),(404,'model not found secreto','modelo'),(500,'secreto','servidor')]:
            response=MagicMock(status_code=status);response.json.return_value={'error':{'message':mensaje}}
            error=provider.error_api_seguro(response)
            self.assertEqual(provider.clasificar_error(error),esperado)
            self.assertNotIn('secreto',str(error))
        self.assertEqual(provider.clasificar_error(provider.LLMTimeoutError()),'timeout')
        self.assertEqual(provider.clasificar_error(provider.LLMNetworkError()),'conexion')
        self.assertEqual(provider.clasificar_error(provider.LLMError()),'respuesta_invalida')

    def test_proveedor_chat_no_usa_tools_y_rechaza_respuesta_vacia(self):
        config=SimpleNamespace(esta_disponible=True,api_base='https://example.invalid',api_key='ficticia',modelo='ficticio',timeout=1)
        response=MagicMock(status_code=200);response.json.return_value={'choices':[{'message':{'content':''}}]}
        with patch('requests.post',return_value=response) as post:
            with self.assertRaises(provider.LLMError): provider.ProveedorLLM(config).responder_chat([{'role':'user','content':'Hola'}])
        self.assertNotIn('tools',post.call_args.kwargs['json'])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_html_y_javascript_conversacion_y_envio_real(self):
        html=self.client.get('/admin/asistente').get_data(as_text=True)
        self.assertIn('id="conversacion-chat"',html)
        self.assertIn('Ideas para empezar la conversación',html)
        self.assertIn('grid-cols-1 sm:grid-cols-2',html)
        self.assertIn('ml-2 sm:ml-8',html)
        script=next(s for s in re.findall(r'<script\b[^>]*>(.*?)</script>',html,re.S) if 'async function ejecutarConsulta' in s)
        harness=r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const script=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements=new Map();let seq=0; const ready=[];
function el(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',children:[],className:'',disabled:false,
 classList:{add(){},remove(){},toggle(){},contains(){return false;}},appendChild(x){this.children.push(x);},replaceChildren(){this.children=[];},remove(){},focus(){},scrollIntoView(){},events:{},addEventListener(event,fn){this.events[event]=fn;},querySelector(){return el('nested-'+seq++);}});return elements.get(id);}
let calls=[];
const examples=['Quiero consultar la afluencia de una sede','¿Qué horas tuvieron más ingresos?','Quiero consultar el historial de visitas de un alumno','¿Qué consultas puedes resolver?'].map(text=>({querySelector(){return {textContent:text};},addEventListener(event,fn){this.click=fn;}}));
const ctx={document:{getElementById:el,querySelectorAll(selector){return selector==='.btn-ejemplo'?examples:[];},addEventListener(event,fn){ready.push(fn);},querySelector(){return {content:'ficticio'};},createElement(){return el('new-'+seq++);},createTextNode(t){return {textContent:t};}},
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({status:'success',intencion:'consulta_general',mensaje:'Hola. Puedo ayudarle.',chat:true,datos:{horas_top:[{hora:10,total_ingresos:3}]},origen:'regla_local',error_proveedor:'configuracion',aviso_origen:'Respuesta de respaldo; Gemini no respondió'})};}};
vm.runInNewContext(script,ctx);
assert.equal(ctx.etiquetaOrigenChat({status:'success',origen:'llm_externo',proveedor:'gemini',origen_detalle:'biblioteca_explicada',explicacion_llm:'Explicación',consulta_sql_realizada:true}), 'Datos consultados en SQL Server · Explicación redactada por Gemini');
assert.equal(ctx.etiquetaOrigenChat({error_proveedor:'timeout'}), 'Respuesta de respaldo; Gemini no respondió');
assert.equal(ctx.etiquetaOrigenChat({error_proveedor:'configuracion'}), 'Respuesta de respaldo; Gemini no respondió');
assert.equal(ctx.etiquetaOrigenChat({consulta_confidencial:true}), 'Consulta confidencial procesada en el servidor; no se envía a Gemini');
assert.equal(ctx.etiquetaOrigenChat({intencion:'consulta_general',origen:'regla_local'}), 'Ayuda del asistente');
assert.equal(ctx.etiquetaOrigenChat({status:'requires_input'}), 'Aclaración del asistente');
assert.ok(!ctx.etiquetaOrigenChat({status:'success',origen:'llm_externo',proveedor:'gemini',origen_detalle:'ayuda_verificada'}).includes('SQL'));

ready.forEach(fn=>fn());
examples.forEach(example=>{example.click();assert.equal(el('pregunta').value,example.querySelector().textContent);});
el('pregunta').value='Hola, ¿qué puedes hacer?';
(async()=>{await ctx.ejecutarConsulta();
 assert.equal(calls[0].url,'/admin/api/asistente/consultar');
 assert.equal(JSON.parse(calls[0].options.body).pregunta,'Hola, ¿qué puedes hacer?');
 const bubbles=el('conversacion-chat').children;
 const plain=n=>n.textContent+(n.children||[]).map(plain).join('');
 assert.ok(plain(bubbles[0].children[0]).includes('Hola')); 
 assert.ok(plain(bubbles[bubbles.length-1]).includes('10:00: 3 ingresos'));
 const safe=el('safe');ctx.renderizarTextoConFormatoSeguro(safe,'**Texto** <img src=x onerror=alert(1)>');
 assert.equal(safe.children[0].textContent,'Texto');
 assert.ok(!safe.children.some(n=>n.tagName==='IMG'));
 assert.ok(bubbles[bubbles.length-1].children[1].textContent.includes('Respuesta de respaldo; Gemini no respondió'));
 el('param-sede').value='Central';el('param-fecha-inicio').value='2026-09-01';
 vm.runInNewContext("preguntaPersonalPendiente='Informe ficticio';",ctx);
 el('btn-nueva-consulta').events.click();
 assert.equal(el('conversacion-chat').children.length,0);
 assert.equal(el('param-sede').value,'');
 assert.equal(vm.runInNewContext('preguntaPersonalPendiente',ctx),'');
 el('pregunta').value='¿y en octubre?';await ctx.ejecutarConsulta();
 const nueva=JSON.parse(calls[1].options.body);assert.equal(nueva.reset_contexto,true);
 assert.equal(nueva.sede,undefined);assert.equal(nueva.fecha_inicio,undefined);
 assert.equal(nueva.pregunta_personal_pendiente,undefined);
const presentacion=ctx.agregarMensajeChat('asistente','Central: resumen breve','Respuesta de respaldo; Gemini no respondió',{}, {causa_proveedor_visible:'Cuota del proveedor.',metodologia_visible:'Metodología secundaria.'});
assert.equal(presentacion.children[2].textContent,'Cuota del proveedor.');
assert.equal(presentacion.children[3].children[0].textContent,'Fuente y metodología');
assert.equal(presentacion.children[3].children[1].textContent,'Metodología secundaria.');


})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result=subprocess.run([shutil.which('node'),'-e',harness],input=json.dumps(script),encoding='utf-8',capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
