"""HTML de rutas reales y ejecución del clic del JS renderizado, con herramientas simuladas."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import template_rendered
from app import app
from llm.services import agentes_asistente as agentes
from llm.services.metricas_asistente import GestorMetricasAsistente


class TestInterfazAgentesRenderizada(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        for target in ('pyodbc.connect', 'pymssql.connect', 'requests.sessions.Session.request', 'smtplib.SMTP', 'smtplib.SMTP_SSL'):
            p = patch(target, side_effect=AssertionError('Servicio real bloqueado'))
            p.start(); self.addCleanup(p.stop)
        for target, value in [
            ('app.get_db_connection', lambda: None),
            ('llm.services.agentes_asistente.telemetria', agentes.TelemetriaAgentes()),
            ('llm.services.metricas_asistente.obtener_gestor_metricas', lambda: GestorMetricasAsistente(Path(self.temp.name)/'metricas.json'))]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        self.client = app.test_client()
        self.rol('SuperAdmin')

    def rol(self, rol):
        with self.client.session_transaction() as s:
            s.update(admin_user='operador-ficticio', admin_rol=rol, admin_sede='Tarma', csrf_token='token-ficticio')

    def renderizar(self, ruta, plantilla):
        capturadas = []
        def capturar(sender, template, context, **extra): capturadas.append(template.name)
        with template_rendered.connected_to(capturar, app):
            response = self.client.get(ruta)
        self.assertEqual(response.status_code, 200)
        self.assertIn(plantilla, capturadas)
        self.assertEqual(response.headers['X-Asistente-UI'], 'agentes-local-v3')
        self.assertIn('no-store', response.headers['Cache-Control'])
        return response.get_data(as_text=True)

    def test_boton_real_visible_para_roles_autorizados(self):
        for rol in ('SuperAdmin', 'Supervisor'):
            self.rol(rol)
            html = self.renderizar('/admin/asistente', 'admin_asistente.html')
            boton = re.search(r'<button\b[^>]*id="btn-reporte-auditado"[^>]*>', html)
            self.assertIsNotNone(boton)
            self.assertNotIn('hidden', boton.group())
            self.assertNotIn('disabled', boton.group().split('class=')[0])
            self.assertIn('data-endpoint="/admin/api/asistente/reportes/preparar"', boton.group())
            self.assertLess(html.index('id="btn-consultar"'), html.index('id="btn-reporte-auditado"'))
            self.assertLess(html.index('id="btn-reporte-auditado"'), html.index('</form>'))

    def test_consultor_no_recibe_boton(self):
        self.rol('Consultor')
        html = self.renderizar('/admin/asistente', 'admin_asistente.html')
        self.assertNotRegex(html, r'<button[^>]+id="btn-reporte-auditado"')

    def test_click_js_usa_endpoint_agentes_y_panel_muestra_ejecuciones(self):
        html = self.renderizar('/admin/asistente', 'admin_asistente.html')
        pregunta = 'Resumen de ingresos del 2026-10-01 al 2026-10-02'
        resultado_herramienta = dict(status='success', fecha_inicio='2026-10-01', fecha_fin='2026-10-02',
                                    total_ingresos=3, ingresos_por_sede={'Tarma': 3}, ingresos_por_tipo_persona={'Alumno': 3})
        with patch('llm.services.coordinador_asistente.obtener_resumen_ingresos', return_value=resultado_herramienta), patch('llm.services.coordinador_asistente.ProveedorLLM') as externo:
            respuesta = self.client.post('/admin/api/asistente/reportes/preparar', json={'pregunta': pregunta}, headers={'X-CSRFToken': 'token-ficticio'})
        externo.assert_not_called()
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.get_json()
        self.assertEqual(datos['auditoria'], 'aprobado')
        incorrecto = {**resultado_herramienta, 'total_ingresos': 10}
        with patch('llm.services.coordinador_asistente.obtener_resumen_ingresos', return_value=incorrecto):
            rechazo = self.client.post('/admin/api/asistente/reportes/preparar', json={'pregunta': pregunta}, headers={'X-CSRFToken': 'token-ficticio'})
        self.assertEqual(rechazo.status_code, 422)
        aclaracion = self.client.post('/admin/api/asistente/reportes/preparar', json={'pregunta': 'Resumen de ingresos'}, headers={'X-CSRFToken': 'token-ficticio'})
        self.assertEqual(aclaracion.get_json()['status'], 'requires_input')
        script = next(s for s in re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.S) if "btn-reporte-auditado" in s)
        endpoint = re.search(r'<button[^>]*id="btn-reporte-auditado"[^>]*data-endpoint="([^"]+)"', html).group(1)
        harness = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const spec = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const elements = new Map(); let ready;
function element(id) {
  if (!elements.has(id)) elements.set(id, {value:'', disabled:false, textContent:'', dataset:{}, handlers:{},
    children:[],replaceChildren(){this.children=[];},appendChild(child){this.children.push(child);},classList:{remove(){},add(){},contains(){return false},toggle(){}}, addEventListener(type,handler){this.handlers[type]=handler;}});
  return elements.get(id);
}
element('pregunta').value = spec.question;
element('btn-reporte-auditado').dataset.endpoint = spec.endpoint;
const calls = [];
const context = {document:{getElementById:element,querySelector(){return {content:'token-ficticio'};},querySelectorAll(){return [];},createElement(){return element('generated-'+elements.size);},addEventListener(type,handler){if(type==='DOMContentLoaded') ready=handler;}},
fetch:async (url,options)=>{calls.push({url,options});return {ok:true,headers:{get(){return 'application/json'}},json:async()=>spec.response};}};
vm.runInNewContext(spec.script, context);
ready();
(async()=>{
  const button = element('btn-reporte-auditado');
  await button.handlers.click.call(button);
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'/admin/api/asistente/reportes/preparar');
  assert.notEqual(calls[0].url,'/admin/api/asistente/consultar');
  assert.equal(calls[0].options.method,'POST');
  assert.equal(JSON.parse(calls[0].options.body).pregunta,spec.question);
  assert.equal(calls[0].options.headers['X-CSRFToken'],'token-ficticio');
  const text = element('reporte-agentes').children.map(item=>item.textContent).join(' ');
  for(const name of ['orquestador','afluencia','auditor','reportes_automatizacion']) assert.ok(text.includes(name));
  assert.equal(element('reporte-auditoria-estado').textContent,'Auditoría: aprobado');
  assert.equal(element('reporte-total').textContent,3);
  assert.ok(element('reporte-periodo').textContent.includes('2026-10-01'));
  assert.equal(button.disabled,false);
  spec.response = spec.rejection;
  await button.handlers.click.call(button);
  assert.equal(element('reporte-auditoria-estado').textContent,'Auditoría: rechazado');
  assert.equal(element('reporte-estado').textContent,'No preparado');
  assert.ok(element('reporte-mensaje').textContent.includes('auditor'));
  spec.response = spec.clarification;
  await button.handlers.click.call(button);
  assert.equal(element('reporte-estado').textContent,'Aclaración necesaria');
  assert.equal(element('reporte-auditoria-estado').textContent,'Auditoría: no ejecutada');
  context.renderizarHistorialPersona({coincidencias:[]});
  const empty = element('contenedor-historial-persona').children;
  assert.equal(empty.length,1);
  assert.ok(empty[0].textContent.includes('No se identificó una persona'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        node = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps(dict(script=script, endpoint=endpoint, response=datos, question=pregunta, rejection=rechazo.get_json(), clarification=aclaracion.get_json())), encoding='utf-8', capture_output=True)
        self.assertEqual(node.returncode, 0, node.stderr)
        metricas = self.renderizar('/admin/asistente/metricas', 'admin_asistente_metricas.html')
        self.assertIn('id="metricas-agentes"', metricas)
        self.assertIn('worker actual', metricas)
        for nombre in ('orquestador', 'afluencia', 'analisis_individual', 'auditor', 'reportes_automatizacion'):
            self.assertIn('data-agent="' + nombre + '"', metricas)
        stats = self.client.get('/admin/api/asistente/metricas').get_json()['datos']['agentes']
        self.assertEqual(stats['reportes_generados'], 1)
        self.assertEqual(stats['agentes']['afluencia']['ejecuciones'], 3)
        self.assertEqual(stats['agentes']['afluencia']['fallos'], 0)
        self.assertGreaterEqual(stats['agentes']['afluencia']['tiempo_ms'], 0)
        self.assertEqual(stats['tareas']['rechazadas'], 1)
        self.assertEqual(stats['tareas']['pendientes'], 1)
        self.assertNotIn('id="borrador-recurrente"', metricas)
        self.assertIn('id="diagnostico-proveedor"', metricas)


if __name__ == '__main__': unittest.main()
