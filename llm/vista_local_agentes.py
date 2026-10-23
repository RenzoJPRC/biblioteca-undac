"""Vista de prueba SOLO loopback, con sesión y datos ficticios; sin servicios reales.

Ejecutar: .venv/Scripts/python.exe -m llm.vista_local_agentes
No importar desde app.py ni desplegar este lanzador de demostración.
"""
import tempfile
import secrets
from pathlib import Path
from types import SimpleNamespace


def main():
    import pyodbc
    import pymssql
    import requests
    import smtplib
    def bloquear(*args, **kwargs):
        raise RuntimeError('Servicio real deshabilitado en esta vista simulada.')
    pyodbc.connect = pymssql.connect = bloquear
    requests.sessions.Session.request = bloquear
    smtplib.SMTP = smtplib.SMTP_SSL = bloquear

    import app as modulo
    from flask import request, session, jsonify
    from llm.services import coordinador_asistente, proveedor_llm, metricas_asistente
    modulo.get_db_connection = lambda: None
    # Las cookies comparten host entre puertos: usar otro nombre y una clave efímera.
    # No aceptar claves anteriores de la aplicación habitual como fallback.
    modulo.app.config.update(
        SESSION_COOKIE_NAME="asistente_agentes_demo_aislado",
        SECRET_KEY=secrets.token_hex(32),
        SECRET_KEY_FALLBACKS=None,
        SESSION_COOKIE_DOMAIN=None,
    )
    proveedor_llm.obtener_configuracion = lambda: SimpleNamespace(
        esta_disponible=False, proveedor='local', modelo='simulado', api_base='', timeout=0)
    coordinador_asistente.obtener_configuracion = proveedor_llm.obtener_configuracion

    def resumen_simulado(fecha_inicio, fecha_fin, rol, sede_permitida=None):
        return dict(status='success', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                    total_ingresos=3, ingresos_por_sede={'Tarma': 3},
                    ingresos_por_tipo_persona={'Alumno': 3},
                    demostracion='DATOS SIMULADOS. No consultados en una BD.')
    coordinador_asistente.obtener_resumen_ingresos = resumen_simulado

    def informe_simulado(codigo_matricula, **kwargs):
        if codigo_matricula != '9900000001':
            return dict(estado='sin_ingresos_verificables', mensaje='Sin ingresos verificables en la fuente SIMULADA.')
        return dict(status='success', estado='verificado_con_ingresos', codigo_mascarado='******0001',
                    fuente='DATOS SIMULADOS; ninguna consulta a la BD', fecha_corte='2026-10-20',
                    total_ingresos=2, dias_con_visita=1, primer_ingreso='2026-10-20T09:00:00', ultimo_ingreso='2026-10-20T10:00:00',
                    mensaje='Informe SIMULADO: 2 ingresos ficticios en 1 día. No describe a una persona real.',
                    mensual=[dict(grupo='2026-10', visitas=2)], dias_semana=[dict(grupo=1, visitas=2)],
                    horarios=[dict(grupo=9, visitas=1), dict(grupo=10, visitas=1)], salas=[dict(grupo=1, visitas=2)])
    coordinador_asistente.contar_ingresos_alumno_por_codigo = informe_simulado

    def sesion_ficticia():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return 'Solo loopback', 403
        permitidas = {'/admin/asistente', '/admin/asistente/metricas', '/admin/api/asistente/reportes/preparar', '/admin/api/asistente/metricas', '/admin/api/asistente/consultar'}
        if request.path not in permitidas and not request.path.startswith('/static/'):
            return jsonify(status='error', mensaje='Esta vista solo permite probar agentes con datos simulados.'), 403
        session.update(admin_user='operador-ficticio', admin_rol='Supervisor', admin_sede='Tarma')
    # Solo en este proceso de prueba: ejecutar antes del guard normal de sesión.
    modulo.app.before_request_funcs.setdefault(None, []).insert(0, sesion_ficticia)

    @modulo.app.after_request
    def identificar_simulacion(response):
        if response.mimetype == 'text/html':
            import re
            banner = '<aside style="background:#fff3cd;padding:16px;font-weight:bold">VISTA LOCAL SIMULADA · sesión ficticia Supervisor / Tarma · sin BD, proveedor, correo ni programación. Ejemplo: Resumen de ingresos del 2026-10-01 al 2026-10-02</aside>'
            response.set_data(re.sub(r'(<body[^>]*>)', lambda m: m.group(1) + banner, response.get_data(as_text=True), count=1))
        return response

    with tempfile.TemporaryDirectory(prefix='asistente-vista-simulada-') as temporal:
        gestor = metricas_asistente.GestorMetricasAsistente(Path(temporal)/'metricas.json')
        metricas_asistente.obtener_gestor_metricas = lambda: gestor
        coordinador_asistente.obtener_gestor_metricas = lambda: gestor
        print('Vista SIMULADA: http://127.0.0.1:8798/admin/asistente')
        modulo.app.run(host='127.0.0.1', port=8798, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
