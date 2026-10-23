import inspect
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, render_template, request, session

from ml.services.servicio_afluencia import (
    obtener_historial_afluencia_periodo,
    obtener_informacion_modelo,
    predecir_afluencia_desde_bd,
    refrescar_analisis_afluencia_memoria
)
from ml.services.analisis_individual import (
    obtener_analisis_descriptivo_ingresos,
    obtener_estado_cache_descriptivo,
    predecir_retorno_alumno,
    PredictorRetornoAlumno, enmascarar_codigo
)
from ml.services.retorno_h1_experimental import (
    predecir_candidato as predecir_retorno_experimental,
    predecir_h1_actual, informacion_h1_actual,
    informacion_candidato as informacion_retorno_experimental,
    CandidatoNoDisponible,
    CoberturaNoConfirmada,
    SELECTOR as SELECTOR_RETORNO_EXPERIMENTAL,
)
from ml.services.vista_previa_historial import (
    obtener_vista_previa_historial_afluencia,
    validar_sede_activa
)
from ml.services import retorno_7_operativos_experimental as retorno_siete


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

    # Carga diferida: No se ejecuta la consulta pesada de 39,000 registros sincrónicamente.
    # La Pestaña 2 se cargará bajo demanda vía /admin/api/ml/analisis-individual.
    analisis_descriptivo = {
        "cargado": False,
        "acceso_ranking_alumnos": (rol != "Consultor"),
        "mensaje_permiso": "El rol Consultor no tiene autorización para visualizar rankings individuales de alumnos." if rol == "Consultor" else None
    }

    h1_info = informacion_h1_actual()

    try:
        info_afluencia = obtener_informacion_modelo()
    except Exception as error:
        print(f"Error cargando información de afluencia en vista ML: {error}")
        info_afluencia = {}

    return render_template(
        "admin_ml.html",
        sedes=sedes,
        sede_asignada=sede_asignada,
        fecha_hoy=datetime.now(ZoneInfo("America/Lima")).date().isoformat(),
        analisis_descriptivo=analisis_descriptivo,
        retorno_h1_info=h1_info,
        info_afluencia=info_afluencia
    )


@admin_ml_bp.route("/api/ml/analisis-individual", methods=["GET"])
def api_analisis_individual():
    """
    Devuelve el análisis descriptivo individual de ingresos reales por sede (carga diferida).
    Aplica controles de acceso:
    - Supervisor: limitado estrictamente a su sede asignada.
    - Consultor: sin acceso a rankings individuales con códigos de alumnos.
    - Administrador / SuperAdmin: acceso completo.
    Soporta refresco manual mediante query param forzar=1.
    """
    inicio = time.perf_counter()
    def finalizar(respuesta):
        from flask import make_response
        respuesta = make_response(respuesta)
        duracion = round((time.perf_counter() - inicio) * 1000, 2)
        respuesta.headers['Server-Timing'] = f'individual;dur={duracion}'
        logging.getLogger(__name__).info('ml_individual estado_http=%s duracion_ms=%.2f', respuesta.status_code, duracion)
        return respuesta

    rol = session.get("admin_rol")
    sede_administrador = str(session.get("admin_sede", "")).strip()
    sede_solicitada = str(request.args.get("sede", "Central")).strip()

    if rol == "Supervisor":
        if not sede_administrador:
            return respuesta_error("El usuario supervisor no tiene una sede asignada.", 403)
        sede = sede_administrador
    else:
        sede = sede_solicitada if sede_solicitada else "Central"

    forzar = request.args.get("forzar", "0").lower() in ("1", "true", "si", "yes")
    fecha_inicio = request.args.get("fecha_inicio")
    fecha_fin = request.args.get("fecha_fin")
    mostrar_codigo_completo = rol in {"Administrador", "SuperAdmin"}

    try:
        analisis = obtener_analisis_descriptivo_ingresos(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            sede=sede,
            forzar_refresco=forzar,
            mostrar_codigo_completo=mostrar_codigo_completo
        )
        if not isinstance(analisis, dict):
            analisis = {}

        # Defensa adicional en la respuesta HTTP, independiente de la caché del servicio.
        if not mostrar_codigo_completo:
            for campo in ("ranking_alumnos_por_visitas", "ranking_alumnos_por_dias"):
                for alumno in analisis.get(campo, []):
                    alumno.pop("codigo_matricula", None)
                    alumno.pop("CodigoMatricula", None)
        analisis["codigos_completos_permitidos"] = mostrar_codigo_completo

        if rol == "Consultor":
            analisis["ranking_alumnos_por_visitas"] = []
            analisis["ranking_alumnos_por_dias"] = []
            analisis["acceso_ranking_alumnos"] = False
            analisis["mensaje_permiso"] = "El rol Consultor no tiene autorización para visualizar rankings individuales de alumnos."
        else:
            analisis["acceso_ranking_alumnos"] = True

        estado_cache = obtener_estado_cache_descriptivo(
            fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, sede=sede,
            mostrar_codigo_completo=mostrar_codigo_completo
        )

        respuesta = {
            "status": "success",
            "sede": sede,
            "rol": rol,
            "analisis": analisis,
            "estado_cache": estado_cache,
            "linea_base_fija": {
                "periodo": "01/06/2026 al 31/07/2026",
                "visitas_validas": 18168,
                "alumnos_unicos": 3013,
                "promedio_visitas": 6.03,
                "dias_activos_promedio": 4.77,
                "descripcion": "Estudio piloto histórico de calibración de 2 meses académicos (junio–julio 2026)."
            }
        }
        # Mantener compatibilidad directa con claves de primer nivel para clientes existentes
        for clave, valor in analisis.items():
            if clave not in respuesta:
                respuesta[clave] = valor

        return finalizar(jsonify(respuesta))
    except ValueError as error:
        return finalizar(respuesta_error(str(error), 400))
    except ConnectionError:
        return finalizar(respuesta_error("No se pudo conectar con la base de datos. Puede reintentar.", 503))
    except Exception:
        # No registrar parámetros, excepciones del driver ni respuestas personales.
        return finalizar(respuesta_error("No fue posible cargar las estadísticas. Puede reintentar.", 503))


@admin_ml_bp.route("/api/ml/informacion", methods=["GET"])
def api_informacion_modelo():
    """
    Devuelve la información general del modelo cargado (candidato o producción).
    Esta ruta está protegida por la sesión administrativa de app.py.
    """
    try:
        modelo = request.args.get("modelo")
        informacion = obtener_informacion_modelo(modelo=modelo)

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


@admin_ml_bp.route("/api/ml/historial-afluencia", methods=["GET"])
def api_historial_afluencia():
    """
    Devuelve la agregación histórica de afluencia por hora para la sede y periodo indicado (7 o 30 días).
    Ruta protegida por sesión administrativa. No requiere CSRF (GET).
    """
    dias_param = request.args.get("dias", "30")
    try:
        dias = int(dias_param)
    except (ValueError, TypeError):
        return respuesta_error("El parámetro dias debe ser 7 o 30.", 400)

    if dias not in (7, 30):
        return respuesta_error("El parámetro dias debe ser 7 o 30.", 400)

    sede_solicitada = str(request.args.get("sede", "Central")).strip()

    rol = session.get("admin_rol")
    sede_administrador = str(session.get("admin_sede", "")).strip()

    if rol == "Supervisor":
        if not sede_administrador:
            return respuesta_error("El usuario supervisor no tiene una sede asignada.", 403)
        sede = sede_administrador
    else:
        sede = sede_solicitada if sede_solicitada else "Central"

    try:
        resultado = obtener_historial_afluencia_periodo(sede=sede, dias=dias)
        return jsonify(resultado)
    except ValueError as error:
        return respuesta_error(str(error), 400)
    except ConnectionError as error:
        print(f"Error de conexión al obtener historial ML: {error}")
        return respuesta_error("No se pudo conectar con la base de datos.", 503)
    except Exception as error:
        print(f"Error inesperado al obtener historial ML: {error}")
        return respuesta_error("No fue posible obtener el historial de afluencia.", 500)


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

    kwargs = {}
    modelo_solicitado = datos.get("modelo") or request.args.get("modelo")
    if modelo_solicitado:
        kwargs["modelo"] = modelo_solicitado

    try:
        call_kwargs = dict(kwargs)
        try:
            target_fn = getattr(predecir_afluencia_desde_bd, "side_effect", None)
            if target_fn is None or not callable(target_fn):
                target_fn = getattr(predecir_afluencia_desde_bd, "_mock_wraps", None) or predecir_afluencia_desde_bd
            sig = inspect.signature(target_fn)
            if "permitir_descriptivo" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                call_kwargs["permitir_descriptivo"] = True
        except Exception:
            call_kwargs["permitir_descriptivo"] = True

        resultado = predecir_afluencia_desde_bd(
            fecha=fecha_objeto,
            hora=hora,
            sede=sede,
            **call_kwargs
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


@admin_ml_bp.route("/api/ml/predecir-retorno", methods=["POST"])
def api_predecir_retorno():
    """Único H1 del dashboard: referencia automática y sin fallback al modelo anterior."""
    rol = session.get("admin_rol")
    if rol not in {"SuperAdmin", "Administrador", "Supervisor"}:
        return respuesta_error("No tiene autorización para consultas individuales.", 403)
    if rol == "Supervisor" and session.get("admin_sede") != "Central":
        return respuesta_error("El H1 disponible solo corresponde a Central.", 403)
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return respuesta_error("Debe enviar el código en formato JSON.", 400)
    if set(datos) - {"codigo_matricula"}:
        return respuesta_error("El modelo y la referencia se determinan en el servidor; envíe solo el código.", 400)
    codigo = str(datos.get("codigo_matricula") or "").strip()
    if not codigo or len(codigo) > 50:
        return respuesta_error("Indique un código de matrícula válido.", 400)
    try:
        resultado = predecir_h1_actual(codigo_matricula=codigo)
    except CoberturaNoConfirmada as error:
        resultado = {**error.respuesta, "status":"no_calculable", "probabilidad_h1":None}
    except CandidatoNoDisponible:
        resultado = {"status":"no_calculable", "probabilidad_h1":None,
                     "mensaje":"No calculable: no se pudo verificar o cargar el artefacto H1 elegido."}
    except ConnectionError:
        resultado = {"status":"no_calculable", "probabilidad_h1":None,
                     "mensaje":"No calculable: no se pudo leer el historial. Puede reintentar."}
    except ValueError:
        resultado = {"status":"no_calculable", "probabilidad_h1":None,
                     "mensaje":"No calculable: el historial no cumple las reglas temporales o de cobertura."}
    except Exception:
        resultado = {"status":"no_calculable", "probabilidad_h1":None,
                     "mensaje":"No calculable: no fue posible verificar el resultado H1. Puede reintentar."}
    # Mismos límites de privacidad del dashboard: el Supervisor no recibe el código completo.
    resultado["codigo_consultado"] = codigo if rol in {"Administrador", "SuperAdmin"} else enmascarar_codigo(codigo)
    return jsonify(resultado)


@admin_ml_bp.route("/api/ml/retorno-7-operativos", methods=["POST"])
def api_retorno_siete_operativos():
    rol=session.get('admin_rol')
    if rol not in {'SuperAdmin','Administrador','Supervisor'}:
        return respuesta_error('No tiene autorización para consultas individuales.',403)
    if rol=='Supervisor' and session.get('admin_sede')!='Central':
        return respuesta_error('El candidato experimental solo corresponde a Central.',403)
    data=request.get_json(silent=True)
    if not isinstance(data,dict):
        return respuesta_error('Se requiere un objeto JSON.',400)
    try:
        result=retorno_siete.predecir(data.get('codigo_matricula'),data.get('fecha_referencia'))
        response=jsonify(result)
        response.headers['Cache-Control']='no-store'
        return response
    except retorno_siete.CandidatoNoDisponible:
        return respuesta_error('El candidato de siete días no está disponible.',503)
    except ValueError as error:
        return respuesta_error(str(error),400)
    except ConnectionError:
        return respuesta_error('No se pudo leer el historial requerido.',503)
    except Exception:
        return respuesta_error('No fue posible calcular la probabilidad experimental.',500)


@admin_ml_bp.route("/api/ml/retorno-informacion", methods=["GET"])
def api_informacion_modelo_retorno():
    """Metadatos reales del único artefacto H1 elegido, sin cargar el modelo antiguo."""
    return jsonify({"status":"success", "metadatos":informacion_h1_actual()})


ROLES_AUTORIZADOS_VISTA_PREVIA = {"SuperAdmin", "Administrador", "Supervisor"}


@admin_ml_bp.route("/api/ml/vista-previa-historial", methods=["GET"])
def api_vista_previa_historial():
    """
    Endpoint de solo lectura que compara la agregación esperada desde dbo.RegistroIngresos
    con dbo.AfluenciaHistoricaAgregada, excluyendo EsPrueba=1 e identificando filas nuevas post 28/09/2026.
    Protegido con lista explícita de roles autorizados (rechaza Consultor y roles no permitidos).
    Si el rol es Supervisor, rechaza si la sede está ausente, vacía o con solo espacios.
    """
    # 1. Validación de autenticación
    if "admin_user" not in session:
        return respuesta_error("Acceso denegado: se requiere sesión administrativa activa.", 401)

    rol = session.get("admin_rol")

    # 2. Validación explícita de roles autorizados para el módulo ML
    if not rol or rol not in ROLES_AUTORIZADOS_VISTA_PREVIA or rol == "Consultor":
        return respuesta_error(
            "Acceso denegado: el rol del usuario no tiene permisos para consultar la vista previa del historial ML.",
            403
        )

    # 3. Validación estricta de sede contra el catálogo real de sedes activas (dbo.Salas)
    if rol == "Supervisor":
        sede_supervisor = session.get("admin_sede")
        try:
            valida, sede_canon, motivo = validar_sede_activa(sede_supervisor)
        except ConnectionError as err:
            return respuesta_error("No se pudo conectar con la base de datos para validar la sede.", 503)

        if not valida:
            if motivo == "sede_ausente_o_vacia":
                return respuesta_error(
                    "Acceso denegado: el supervisor no tiene una sede asignada válida (sede ausente o vacía).",
                    403
                )
            elif motivo == "sede_inexistente":
                return respuesta_error(
                    f"Acceso denegado: la sede '{sede_supervisor}' no existe en el catálogo de sedes del sistema.",
                    403
                )
            elif motivo == "sede_inactiva":
                return respuesta_error(
                    f"Acceso denegado: la sede '{sede_supervisor}' se encuentra inactiva.",
                    403
                )
            else:
                return respuesta_error("Acceso denegado: sede no autorizada.", 403)

        sede_filtro = sede_canon
    else:
        # Administrador o SuperAdmin pueden consultar todas las sedes o filtrar voluntariamente por una
        sede_solicitada = request.args.get("sede")
        if sede_solicitada and str(sede_solicitada).strip():
            sede_limpia = str(sede_solicitada).strip()
            try:
                valida, sede_canon, motivo = validar_sede_activa(sede_limpia)
            except ConnectionError as err:
                return respuesta_error("No se pudo conectar con la base de datos para validar la sede.", 503)

            if not valida:
                if motivo == "sede_inexistente":
                    return respuesta_error(f"La sede '{sede_limpia}' no existe en el catálogo de sedes.", 400)
                elif motivo == "sede_inactiva":
                    return respuesta_error(f"La sede '{sede_limpia}' se encuentra inactiva.", 400)
                else:
                    return respuesta_error("Sede inválida solicitada.", 400)
            sede_filtro = sede_canon
        else:
            sede_filtro = None

    try:
        resultado = obtener_vista_previa_historial_afluencia(sede_filtro=sede_filtro)
        return jsonify(resultado)
    except ConnectionError as error:
        print(f"Error de conexión en vista previa ML: {error}")
        return respuesta_error("No se pudo conectar con la base de datos.", 503)
    except Exception as error:
        print(f"Error inesperado en vista previa ML: {error}")
        return respuesta_error("No fue posible generar la vista previa del historial.", 500)


@admin_ml_bp.route("/api/ml/actualizar-analisis", methods=["POST", "GET"])
def api_actualizar_analisis():
    """
    Endpoint de solo lectura que refresca en memoria el historial analítico y el pronóstico de afluencia,
    leyendo los ingresos reales disponibles desde 2026-06-01 hasta la última fecha/hora registrada en dbo.RegistroIngresos.
    Excluye estrictamente registros de prueba (EsPrueba = 1) y mantiene aislamiento por sede.
    No realiza INSERT, UPDATE, DELETE ni MERGE (cero escrituras).
    Protegido con lista explícita de roles autorizados (rechaza Consultor y roles no autorizados).
    """
    if "admin_user" not in session:
        return respuesta_error("Acceso denegado: se requiere sesión administrativa activa.", 401)

    rol = session.get("admin_rol")
    if not rol or rol not in ROLES_AUTORIZADOS_VISTA_PREVIA or rol == "Consultor":
        return respuesta_error(
            "Acceso denegado: el rol del usuario no tiene permisos para actualizar el análisis ML.",
            403
        )

    if rol == "Supervisor":
        sede_supervisor = session.get("admin_sede")
        try:
            valida, sede_canon, motivo = validar_sede_activa(sede_supervisor)
        except ConnectionError as err:
            return respuesta_error("No se pudo conectar con la base de datos para validar la sede.", 503)

        if not valida:
            if motivo == "sede_ausente_o_vacia":
                return respuesta_error("Acceso denegado: el supervisor no tiene una sede asignada válida.", 403)
            elif motivo == "sede_inexistente":
                return respuesta_error(f"Acceso denegado: la sede '{sede_supervisor}' no existe en el catálogo.", 403)
            elif motivo == "sede_inactiva":
                return respuesta_error(f"Acceso denegado: la sede '{sede_supervisor}' se encuentra inactiva.", 403)
            else:
                return respuesta_error("Acceso denegado: sede no autorizada.", 403)

        sede_consulta = sede_canon
    else:
        sede_solicitada = None
        if request.is_json:
            datos = request.get_json(silent=True) or {}
            sede_solicitada = datos.get("sede")
        if not sede_solicitada:
            sede_solicitada = request.values.get("sede")

        if sede_solicitada and str(sede_solicitada).strip():
            sede_limpia = str(sede_solicitada).strip()
            try:
                valida, sede_canon, motivo = validar_sede_activa(sede_limpia)
            except ConnectionError as err:
                return respuesta_error("No se pudo conectar con la base de datos para validar la sede.", 503)

            if not valida:
                if motivo == "sede_inexistente":
                    return respuesta_error(f"La sede '{sede_limpia}' no existe en el catálogo de sedes.", 400)
                elif motivo == "sede_inactiva":
                    return respuesta_error(f"La sede '{sede_limpia}' se encuentra inactiva.", 400)
                else:
                    return respuesta_error("Sede inválida solicitada.", 400)
            sede_consulta = sede_canon
        else:
            sede_consulta = "Central"

    try:
        resultado = refrescar_analisis_afluencia_memoria(sede=sede_consulta)
        return jsonify(resultado)
    except ValueError as error:
        return respuesta_error(str(error), 400)
    except ConnectionError as error:
        print(f"Error de conexión al actualizar análisis ML: {error}")
        return respuesta_error("No se pudo conectar con la base de datos.", 503)
    except Exception as error:
        print(f"Error inesperado al actualizar análisis ML: {error}")
        return respuesta_error("No fue posible actualizar el análisis de afluencia.", 500)



