from flask import Blueprint, render_template, request, session, jsonify
from llm.services.coordinador_asistente import procesar_pregunta
from llm.services.motor_intenciones import sanitizar_contexto
from llm.services.chat_conversacional import CHAT_REVISION

admin_asistente_bp = Blueprint(
    "admin_asistente",
    __name__,
    url_prefix="/admin"
)

INTERFAZ_AGENTES = 'agentes-local-v3'


@admin_asistente_bp.after_request
def identificar_interfaz_asistente(response):
    # Evitar reutilizar HTML anterior; esto no recarga un proceso Python antiguo.
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Asistente-UI'] = INTERFAZ_AGENTES
    response.headers['X-Asistente-Chat'] = CHAT_REVISION
    return response


def respuesta_error(mensaje, codigo=400, categoria="validacion"):
    return jsonify({
        "status": "error",
        "mensaje": mensaje,
        "categoria_error": categoria,
        "codigo_http": codigo
    }), codigo


@admin_asistente_bp.route("/asistente", methods=["GET"])
def vista_asistente():
    """
    Vista del asistente con resolución local y proveedor opcional.
    Ruta protegida por sesión en app.py.
    """
    sedes = [
        "Central",
        "Tarma",
        "La Merced",
        "Oxapampa",
        "Paucartambo",
        "Yanahuanca"
    ]

    rol = session.get("admin_rol")
    sede_asignada = session.get("admin_sede")

    from llm.services.proveedor_llm import obtener_configuracion
    config_llm = obtener_configuracion()
    llm_activo = config_llm.esta_disponible
    llm_proveedor = config_llm.proveedor
    llm_modelo = config_llm.modelo
    faltantes = [nombre for nombre, valor in [('LLM_API_KEY',getattr(config_llm,'api_key',None)),('LLM_MODELO',getattr(config_llm,'modelo',None))] if not valor]
    if not getattr(config_llm,'activado',llm_activo): faltantes.append('LLM_ACTIVADO')

    return render_template(
        "admin_asistente.html",
        sedes=sedes,
        sede_asignada=sede_asignada,
        llm_activo=llm_activo,
        llm_proveedor=llm_proveedor,
        llm_modelo=llm_modelo,
        chat_revision=CHAT_REVISION,
        llm_variables_faltantes=faltantes
    )


@admin_asistente_bp.route("/asistente/metricas", methods=["GET"])
def vista_metricas_asistente():
    """
    Panel administrativo independiente para 'Uso del asistente'.
    Exclusivo para roles autorizados (SuperAdmin, Supervisor).
    """
    rol = session.get("admin_rol")
    if not rol or rol not in ["SuperAdmin", "Supervisor"]:
        return respuesta_error("Acceso denegado. Se requiere rol autorizado para acceder a las métricas del asistente.", 403, "permiso")

    from llm.services.proveedor_llm import obtener_configuracion
    from llm.services.metricas_asistente import obtener_gestor_metricas

    config_llm = obtener_configuracion()
    gestor = obtener_gestor_metricas()
    resumen = gestor.obtener_resumen()
    from llm.services.agentes_asistente import telemetria

    return render_template(
        "admin_asistente_metricas.html",
        metricas=resumen,
        agentes=telemetria.resumen(),
        llm_activo=config_llm.esta_disponible,
        llm_proveedor=config_llm.proveedor if config_llm.esta_disponible else "local",
        llm_modelo=config_llm.modelo if config_llm.esta_disponible else "reglas_locales",
        llm_api_base=config_llm.api_base if rol == 'SuperAdmin' else None,
        llm_timeout=config_llm.timeout
    )


@admin_asistente_bp.route("/api/asistente/metricas", methods=["GET"])
def api_metricas_asistente():
    """
    Endpoint JSON para consultar telemetría agregada y consumo de tokens.
    Exclusivo para roles autorizados (SuperAdmin, Supervisor).
    """
    rol = session.get("admin_rol")
    if not rol or rol not in ["SuperAdmin", "Supervisor"]:
        return respuesta_error("Acceso denegado. Se requiere rol autorizado para acceder a las métricas del asistente.", 403, "permiso")

    from llm.services.metricas_asistente import obtener_gestor_metricas
    gestor = obtener_gestor_metricas()
    resumen = gestor.obtener_resumen()
    from llm.services.agentes_asistente import telemetria
    resumen['agentes'] = telemetria.resumen()

    return jsonify({
        "status": "success",
        "datos": resumen
    }), 200


def _validar_peticion_agentes():
    from app import validar_csrf
    if not validar_csrf():
        return respuesta_error('Token CSRF inválido o expirado.', 403, 'permiso')
    from llm.services.agentes_asistente import validar_sesion
    try:
        validar_sesion(session.get('admin_rol'), session.get('admin_sede'))
    except PermissionError:
        return respuesta_error('Rol o sede de sesión no autorizados.', 403, 'permiso')
    if not request.is_json or not isinstance(request.get_json(silent=True), dict):
        return respuesta_error('Debe enviar un objeto JSON.', 400)
    return None


@admin_asistente_bp.route('/api/asistente/reportes/preparar', methods=['POST'])
def preparar_reporte_agentes():
    error = _validar_peticion_agentes()
    if error is not None:
        return error
    pregunta = request.get_json().get('pregunta')
    if not isinstance(pregunta, str) or not pregunta.strip() or len(pregunta) > 500:
        return respuesta_error('Escriba una pregunta de hasta 500 caracteres.', 400)
    from llm.services.agentes_asistente import Orquestador
    resultado = Orquestador().ejecutar(pregunta.strip(), session['admin_rol'], session.get('admin_sede'),
                                    contexto=session.get('asistente_reporte_contexto'))
    session['asistente_reporte_contexto'] = sanitizar_contexto(resultado.get('contexto'))
    return jsonify(resultado), 200 if resultado.get('status') == 'requires_input' else resultado.get('codigo_http', 200)


@admin_asistente_bp.route('/api/asistente/tareas/borrador', methods=['POST'])
def preparar_borrador_recurrente():
    error = _validar_peticion_agentes()
    if error is not None:
        return error
    datos = request.get_json()
    from llm.services.agentes_asistente import AgenteReportesAutomatizacion
    try:
        borrador = AgenteReportesAutomatizacion().borrador(
            datos.get('tipo'), datos.get('frecuencia'), datos.get('destinatario'),
            session['admin_rol'], session.get('admin_sede'))
        return jsonify(status='success', borrador=borrador)
    except PermissionError as error_permiso:
        return respuesta_error(str(error_permiso), 403, 'permiso')
    except (TypeError, ValueError):
        return respuesta_error('Revise tipo, frecuencia y destinatario.', 400)


@admin_asistente_bp.route("/api/asistente/consultar", methods=["POST"])
def api_consultar_asistente():
    """
    Endpoint POST para consultar al Asistente Inteligente Local.

    - Acepta exclusivamente JSON.
    - Obtiene rol y sede_permitida ÚNICAMENTE de session.
    - Rechaza rol o sede_permitida proporcionados por el cliente.
    - Clasificación HTTP determinista vía atributos estructurados sin búsquedas de texto:
      - 200 para 'success' y 'requires_input'.
      - 400 para errores de validación de parámetros (categoria_error: 'validacion').
      - 403 para permisos / accesos denegados (categoria_error: 'permiso').
      - 503 para errores técnicos de servicio (categoria_error: 'servicio').
    """
    # 1. Validar Content-Type
    if not request.is_json:
        return respuesta_error("Solo se aceptan solicitudes JSON.", 400, "validacion")

    # 2. Parsear JSON con manejo explícito de errores
    try:
        datos = request.get_json(silent=True)
    except Exception:
        return respuesta_error("JSON malformado o inválido.", 400, "validacion")

    if datos is None or not isinstance(datos, dict):
        return respuesta_error("Debe enviar los datos en un objeto JSON válido.", 400, "validacion")

    # 3. Validar presencia y tipo de pregunta
    if "pregunta" not in datos or datos["pregunta"] is None or not isinstance(datos["pregunta"], str):
        return respuesta_error("La pregunta es obligatoria y debe ser una cadena de texto.", 400, "validacion")

    pregunta_str = datos["pregunta"].strip()
    if not pregunta_str:
        return respuesta_error("La pregunta no puede estar vacía o contener solo espacios.", 400, "validacion")

    if len(pregunta_str) > 500:
        return respuesta_error("La pregunta no puede exceder los 500 caracteres.", 400, "validacion")

    # 4. Obtener estrictamente rol y sede desde session (Ignorando cliente)
    rol = session.get("admin_rol")
    sede_permitida = session.get("admin_sede")

    if not rol or not isinstance(rol, str) or rol not in ["SuperAdmin", "Supervisor", "Consultor"]:
        return respuesta_error("Acceso denegado. Rol de sesión no válido o no autorizado.", 403, "permiso")

    # 5. Obtener contexto conversacional mínimo desde la sesión segura
    if datos.get("reset_contexto") or datos.get("nueva_consulta"):
        session.pop("asistente_contexto", None)
        contexto_previo = {}
    else:
        contexto_previo = session.get("asistente_contexto")
        if not isinstance(contexto_previo, dict):
            contexto_previo = {}

    contexto_cliente = datos.get("contexto")
    if isinstance(contexto_cliente, dict):
        contexto_combinado = {**contexto_previo, **contexto_cliente}
    else:
        contexto_combinado = contexto_previo

    contexto_limpio = sanitizar_contexto(contexto_combinado)

    # Si el rol es Supervisor, la sede de sesión es estrictamente inmutable
    if rol == "Supervisor":
        contexto_limpio["sede"] = sede_permitida

    # Parámetros opcionales sanitizados
    seleccion_persona = datos.get("seleccion_persona")
    fecha_inicio = datos.get("fecha_inicio")
    fecha_fin = datos.get("fecha_fin")
    fecha = datos.get("fecha")
    hora = datos.get("hora")
    sede = datos.get("sede")
    limite = datos.get("limite")

    try:
        resultado = procesar_pregunta(
            pregunta=pregunta_str,
            rol=rol,
            sede_permitida=sede_permitida,
            seleccion_persona=seleccion_persona,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            fecha=fecha,
            hora=hora,
            sede=sede,
            limite=limite,
            contexto=contexto_limpio,
            pregunta_personal_pendiente=datos.get("pregunta_personal_pendiente"),
            modo_chat=True
        )

        # 6. Actualizar sesión con el contexto mínimo devuelto (garantizando ausencia de datos personales)
        ctx_actualizado = resultado.get("contexto")
        if isinstance(ctx_actualizado, dict):
            if resultado.get('status') == 'requires_input':
                ctx_actualizado = {**ctx_actualizado, 'intencion': resultado.get('intencion')}
            session["asistente_contexto"] = sanitizar_contexto(ctx_actualizado)
            session.modified = True

        status = resultado.get("status")

        if status in ("success", "requires_input"):
            return jsonify(resultado), 200

        # Mapeo estructurado directo sin búsquedas por texto
        codigo_http = resultado.get("codigo_http")
        if not codigo_http:
            categoria = resultado.get("categoria_error")
            if categoria == "permiso":
                codigo_http = 403
            elif categoria == "servicio":
                codigo_http = 503
            else:
                codigo_http = 400

        return jsonify(resultado), codigo_http

    except Exception:
        return respuesta_error("Ocurrió un error al procesar la consulta en el asistente.", 503, "servicio")
