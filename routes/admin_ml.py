from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session

from ml.services.servicio_afluencia import (
    obtener_informacion_modelo,
    predecir_afluencia_desde_bd
)


admin_ml_bp = Blueprint(
    "admin_ml",
    __name__,
    url_prefix="/admin"
)


def respuesta_error(mensaje, codigo):
    return jsonify({
        "status": "error",
        "mensaje": mensaje
    }), codigo


@admin_ml_bp.route("/ml", methods=["GET"])
def vista_machine_learning():
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

    # El supervisor solamente trabajará con su sede.
    if rol == "Supervisor":
        sedes = [sede_asignada]

    return render_template(
        "admin_ml.html",
        sedes=sedes,
        sede_asignada=sede_asignada,
        fecha_hoy=datetime.now().date().isoformat()
    )


@admin_ml_bp.route("/api/ml/informacion", methods=["GET"])
def api_informacion_modelo():
    """
    Devuelve la información general del modelo cargado.
    Esta ruta está protegida por la sesión administrativa de app.py.
    """
    try:
        informacion = obtener_informacion_modelo()

        return jsonify({
            "status": "success",
            "modelo": informacion
        })

    except Exception as error:
        print(f"Error obteniendo información del modelo ML: {error}")
        return respuesta_error(
            "No se pudo obtener la información del modelo.",
            500
        )


@admin_ml_bp.route("/api/ml/predecir-afluencia", methods=["POST"])
def api_predecir_afluencia():
    """
    Recibe:
    {
        "fecha": "2026-09-29",
        "hora": 10,
        "sede": "Central"
    }
    """
    datos = request.get_json(silent=True)

    if not datos:
        return respuesta_error(
            "Debe enviar los datos en formato JSON.",
            400
        )

    fecha = str(datos.get("fecha", "")).strip()
    sede_solicitada = str(datos.get("sede", "")).strip()
    hora = datos.get("hora")

    if not fecha:
        return respuesta_error("La fecha es obligatoria.", 400)

    try:
        fecha_objeto = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return respuesta_error(
            "La fecha debe tener el formato AAAA-MM-DD.",
            400
        )

    try:
        hora = int(hora)
    except (TypeError, ValueError):
        return respuesta_error(
            "La hora debe ser un número entero.",
            400
        )

    if hora < 8 or hora > 20:
        return respuesta_error(
            "La hora debe estar comprendida entre las 8 y las 20.",
            400
        )

    rol = session.get("admin_rol")
    sede_administrador = str(
        session.get("admin_sede", "")
    ).strip()

    # Un supervisor solo puede consultar su sede asignada.
    if rol == "Supervisor":
        if not sede_administrador:
            return respuesta_error(
                "El usuario supervisor no tiene una sede asignada.",
                403
            )

        sede = sede_administrador
    else:
        sede = sede_solicitada

    if not sede:
        return respuesta_error("La sede es obligatoria.", 400)

    try:
        resultado = predecir_afluencia_desde_bd(
            fecha=fecha_objeto,
            hora=hora,
            sede=sede
        )

        return jsonify(resultado)

    except ValueError as error:
        return respuesta_error(str(error), 400)

    except ConnectionError as error:
        print(f"Error de conexión durante la predicción ML: {error}")
        return respuesta_error(
            "No se pudo conectar con la base de datos.",
            503
        )

    except Exception as error:
        print(f"Error inesperado durante la predicción ML: {error}")
        return respuesta_error(
            "No fue posible realizar la predicción.",
            500
        )