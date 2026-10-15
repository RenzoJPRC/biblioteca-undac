from flask import Blueprint, render_template, request, session, jsonify
from llm.services.coordinador_asistente import procesar_pregunta
from llm.services.motor_intenciones import sanitizar_contexto

admin_asistente_bp = Blueprint(
    "admin_asistente",
    __name__,
    url_prefix="/admin"
)


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
    Vista administrativa principal del Asistente Inteligente (Modo Local).
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
    sede_asignada = session.get("admin_sede", "Central")

    from llm.services.proveedor_llm import obtener_configuracion
    config_llm = obtener_configuracion()
    llm_activo = config_llm.esta_disponible
    llm_proveedor = config_llm.proveedor if llm_activo else "local"
    llm_modelo = config_llm.modelo if llm_activo else "reglas_locales"

    return render_template(
        "admin_asistente.html",
        sedes=sedes,
        sede_asignada=sede_asignada,
        llm_activo=llm_activo,
        llm_proveedor=llm_proveedor,
        llm_modelo=llm_modelo
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

    return render_template(
        "admin_asistente_metricas.html",
        metricas=resumen,
        llm_activo=config_llm.esta_disponible,
        llm_proveedor=config_llm.proveedor if config_llm.esta_disponible else "local",
        llm_modelo=config_llm.modelo if config_llm.esta_disponible else "reglas_locales",
        llm_api_base=config_llm.api_base,
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

    return jsonify({
        "status": "success",
        "datos": resumen
    }), 200


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
            contexto=contexto_limpio
        )

        # 6. Actualizar sesión con el contexto mínimo devuelto (garantizando ausencia de datos personales)
        ctx_actualizado = resultado.get("contexto")
        if isinstance(ctx_actualizado, dict):
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
