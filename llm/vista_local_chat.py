"""Chat de prueba loopback con fuentes ficticias y todos los servicios bloqueados.

Ejecutar desde la raíz: .venv/Scripts/python.exe -m llm.vista_local_chat
No importar desde app.py ni desplegar este lanzador.
"""
from types import SimpleNamespace


def main():
    import pyodbc, pymssql, requests, smtplib
    def bloquear(*args, **kwargs): raise RuntimeError('Servicio real bloqueado en la vista de chat simulada.')
    pyodbc.connect = pymssql.connect = bloquear
    requests.sessions.Session.request = bloquear
    smtplib.SMTP = smtplib.SMTP_SSL = bloquear
    import app as modulo
    from flask import request, session, jsonify
    from llm.services import proveedor_llm, chat_conversacional, coordinador_asistente as coor
    modulo.get_db_connection = lambda: None
    # El navegador comparte cookies entre puertos: nunca firmar sesiones ficticias con la clave real.
    import secrets
    from itsdangerous import BadSignature
    serializador_anterior = modulo.app.session_interface.get_signing_serializer(modulo.app)
    nombre_cookie_anterior = modulo.app.config.get('SESSION_COOKIE_NAME', 'session')
    modulo.app.config.update(SESSION_COOKIE_NAME='asistente_demo_aislado', SECRET_KEY=secrets.token_hex(32))
    config = SimpleNamespace(esta_disponible=False, proveedor='gemini', activado=False, api_key='', modelo='', api_base='', timeout=1)
    proveedor_llm.obtener_configuracion = chat_conversacional.obtener_configuracion = lambda: config
    def resumen(fecha_inicio, fecha_fin, rol, sede_permitida=None):
        return dict(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, total_ingresos=3,
                    ingresos_por_sede={sede_permitida or 'Central':3}, ingresos_por_tipo_persona={'Alumno':3},
                    fuente='DATOS FICTICIOS de demostración, no una consulta a la BD')
    def horas(fecha_inicio, fecha_fin, rol, sede_permitida=None, limite=5):
        return dict(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, dias_con_registros=2,
                    fuente='DATOS FICTICIOS', horas_top=[dict(hora=10,hora_formato='10:00',total_ingresos=3,promedio_diario=1.5)])
    def afluencia(fecha_inicio, fecha_fin, sede, **kwargs):
        if fecha_inicio >= '2026-10-01':
            raise ValueError('Datos insuficientes: la fuente ficticia no tiene cobertura de octubre. No se infieren ceros.')
        return dict(periodo=dict(fecha_inicio=fecha_inicio,fecha_fin=fecha_fin),sede=sede,
                    fuente='DATOS FICTICIOS; nunca se consulta la BD',limites_cobertura='Solo evidencia simulada',
                    datos_simulados=True, mensaje='En el periodo simulado se observaron 3 ingresos. Fuente: datos ficticios.',total_ingresos=3)
    def alumno(codigo_matricula, **kwargs):
        if codigo_matricula != '9900000001':
            return dict(mensaje='No hay coincidencia en la fuente ficticia.',estado='sin_ingresos_verificables')
        return dict(mensaje='Informe FICTICIO: 2 visitas en 1 día. No corresponde a un alumno real.',
                    total_ingresos=2, dias_con_visita=1, fecha_corte='2026-10-20', fuente='DATOS FICTICIOS',
                    mensual=[dict(grupo='2026-10',visitas=2)], dias_semana=[dict(grupo=1,visitas=2)],
                    horarios=[dict(grupo=10,visitas=2)], salas=[dict(grupo=1,visitas=2)])
    coor.consultar_afluencia_historica = afluencia
    coor.obtener_resumen_ingresos = resumen
    coor.obtener_horas_mayor_afluencia = horas
    coor.contar_ingresos_alumno_por_codigo = alumno
    def sesion_simulada():
        if request.remote_addr not in ('127.0.0.1','::1'): return 'Solo loopback',403
        if request.path not in {'/admin/asistente','/admin/api/asistente/consultar'} and not request.path.startswith('/static/'):
            return jsonify(status='error',mensaje='Vista limitada al chat simulado, sin agentes ni tareas.'),403
        session.update(admin_user='operador-ficticio', admin_rol='SuperAdmin', admin_sede='Central')
    modulo.app.before_request_funcs.setdefault(None,[]).insert(0,sesion_simulada)
    @modulo.app.after_request
    def banner(response):
        antigua = request.cookies.get(nombre_cookie_anterior)
        if antigua and serializador_anterior:
            try:
                previa = serializador_anterior.loads(antigua)
                if previa.get('admin_user') == 'operador-ficticio':
                    response.delete_cookie(nombre_cookie_anterior, path='/')
            except BadSignature:
                pass
        if response.mimetype == 'text/html':
            import re
            response.set_data(re.sub(r'(<main[^>]*>)',lambda m:m.group(1)+'<aside style="flex-shrink:0;padding:16px;background:#fff3cd;font-weight:bold">CHAT SIMULADO · datos ficticios · Gemini desactivado · sin BD, correo ni tareas. Matrícula ficticia: 9900000001.</aside>',response.get_data(as_text=True),count=1))
        return response
    print('Chat SIMULADO: http://127.0.0.1:8799/admin/asistente')
    modulo.app.run(host='127.0.0.1',port=8799,debug=False,use_reloader=False)


if __name__ == '__main__': main()
