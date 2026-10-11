from flask import Blueprint, render_template, request, session, jsonify
from llm.services.coordinador_asistente import procesar_pregunta

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

    if rol == "Supervisor":
        sedes = [sede_asignada]

    return render_template(
        "admin_asistente.html",
        sedes=sedes,
        sede_asignada=sede_asignada
    )


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
            limite=limite
        )

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
