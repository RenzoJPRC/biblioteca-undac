from datetime import datetime, date

from llm.services.consultas_biblioteca import (
    validar_rol,
    buscar_personas,
    obtener_historial_persona,
    obtener_resumen_ingresos,
    obtener_horas_mayor_afluencia,
    SEDES_PERMITIDAS
)
from llm.services.motor_intenciones import (
    clasificar_intencion,
    normalizar_texto,
    extraer_parametros_texto,
    resolver_expresion_temporal,
    es_pregunta_seguimiento,
    es_seguimiento_ambiguo,
    sanitizar_contexto
)
from ml.services.servicio_afluencia import predecir_afluencia_desde_bd


def procesar_pregunta(
    pregunta,
    rol,
    sede_permitida=None,
    seleccion_persona=None,
    fecha_inicio=None,
    fecha_fin=None,
    fecha=None,
    hora=None,
    sede=None,
    limite=None,
    contexto=None,
    fecha_referencia=None
):
    """
    Coordinador local del Asistente LLM de Biblioteca.

    Evalúa la pregunta enviada por el usuario en lenguaje natural,
    conserva contexto conversacional mínimo (intención y filtros de fecha)
    sin almacenar datos personales ni respuestas completas,
    re-valida siempre el rol y sede permitida de sesión,
    y clasifica y enruta la consulta de forma determinista y segura.
    """
    # 0. Sanitizar contexto conversacional recibido (mínimo, sin datos personales)
    ctx_sanitizado = sanitizar_contexto(contexto)

    # Determinar sede inicial según rol y parámetros
    if rol == "Supervisor":
        sede_aplicada_ini = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
    elif sede and str(sede).strip() in SEDES_PERMITIDAS:
        sede_aplicada_ini = str(sede).strip()
    else:
        sede_aplicada_ini = "Todas las sedes"

    # 1. Validar la pregunta
    if not pregunta or not isinstance(pregunta, str):
        return {
            "status": "error",
            "categoria_error": "validacion",
            "codigo_http": 400,
            "intencion": "intencion_desconocida",
            "sede_aplicada": sede_aplicada_ini,
            "mensaje": "La pregunta es obligatoria y debe ser un texto válido.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "contexto": ctx_sanitizado,
            "origen": "regla_local"
        }

    pregunta_limpia = pregunta.strip()
    if not pregunta_limpia:
        return {
            "status": "error",
            "categoria_error": "validacion",
            "codigo_http": 400,
            "intencion": "intencion_desconocida",
            "sede_aplicada": sede_aplicada_ini,
            "mensaje": "La pregunta no puede estar vacía o contener solo espacios.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "contexto": ctx_sanitizado,
            "origen": "regla_local"
        }

    if len(pregunta_limpia) > 500:
        return {
            "status": "error",
            "categoria_error": "validacion",
            "codigo_http": 400,
            "intencion": "intencion_desconocida",
            "sede_aplicada": sede_aplicada_ini,
            "mensaje": "La pregunta excede el límite máximo permitido de 500 caracteres.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "contexto": ctx_sanitizado,
            "origen": "regla_local"
        }

    # 2. Validar el rol del usuario (estrictamente desde la sesión)
    try:
        rol_valido = validar_rol(rol)
    except PermissionError as e:
        return {
            "status": "error",
            "categoria_error": "permiso",
            "codigo_http": 403,
            "intencion": "intencion_desconocida",
            "sede_aplicada": sede_aplicada_ini,
            "mensaje": str(e),
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "contexto": {},
            "origen": "regla_local"
        }

    # 3. Determinar sede_aplicada y sede_consulta_efectiva de forma segura
    # En Supervisor, la sede de sesión SIEMPRE manda y no puede ser cambiada por contexto ni cliente
    if rol_valido == "Supervisor":
        sede_aplicada = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
        sede_consulta_efectiva = sede_aplicada
    else:
        sede_candidata = sede if (sede and str(sede).strip() in SEDES_PERMITIDAS) else ctx_sanitizado.get("sede")
        if sede_candidata and str(sede_candidata).strip() in SEDES_PERMITIDAS:
            sede_aplicada = str(sede_candidata).strip()
            sede_consulta_efectiva = sede_aplicada
        else:
            sede_aplicada = "Todas las sedes"
            sede_consulta_efectiva = None

    # 4. Evaluar si la consulta es una pregunta de seguimiento conversacional
    es_seguimiento = es_pregunta_seguimiento(pregunta_limpia)

    if es_seguimiento:
        # Caso A: Pregunta de seguimiento ambigua (ej: "¿Y entonces?", "¿Y qué más?", "¿Y?")
        if es_seguimiento_ambiguo(pregunta_limpia):
            return {
                "status": "requires_input",
                "intencion": ctx_sanitizado.get("intencion") or "intencion_desconocida",
                "sede_aplicada": sede_aplicada,
                "mensaje": "La pregunta de seguimiento es ambigua. Por favor especifique el periodo o dato que desea consultar (por ejemplo: '¿Y ayer?', '¿Y la semana pasada?' o un rango de fechas).",
                "datos": {},
                "requiere_aclaracion": True,
                "requiere_parametros": False,
                "opciones": [
                    "¿Y ayer?",
                    "¿Y hoy?",
                    "¿Y la semana pasada?",
                    "¿Y el mes pasado?"
                ],
                "contexto": ctx_sanitizado,
                "origen": "regla_local"
            }

        # Caso B: Pregunta de seguimiento sin contexto previo válido
        intencion_previa = ctx_sanitizado.get("intencion")
        if not intencion_previa:
            temp_resolv = resolver_expresion_temporal(pregunta_limpia, fecha_referencia)
            tipo_p = temp_resolv.get("tipo_periodo", "") if temp_resolv else ""
            sufijo = f" de {tipo_p}" if tipo_p in ("ayer", "hoy") else (" del mes pasado" if tipo_p == "mes_pasado" else "")
            return {
                "status": "requires_input",
                "intencion": "intencion_desconocida",
                "sede_aplicada": sede_aplicada,
                "mensaje": f"No se encontró una consulta previa en la conversación para continuar con la pregunta de seguimiento. ¿Desea consultar el resumen de ingresos o las horas de mayor afluencia{sufijo}?",
                "datos": {},
                "requiere_aclaracion": True,
                "requiere_parametros": False,
                "opciones": [
                    f"Resumen de ingresos{sufijo}",
                    f"Horas de mayor afluencia{sufijo}"
                ],
                "contexto": {},
                "origen": "regla_local"
            }

        # Caso C: Pregunta de seguimiento con contexto previo válido
        intencion = intencion_previa
        temp_resolv = resolver_expresion_temporal(pregunta_limpia, fecha_referencia)
        norm_txt = normalizar_texto(pregunta_limpia)
        params_texto = extraer_parametros_texto(pregunta_limpia, norm_txt, fecha_referencia=fecha_referencia)

        if temp_resolv:
            f_ini_efectivo = temp_resolv.get("fecha_inicio")
            f_fin_efectivo = temp_resolv.get("fecha_fin")
            fecha_efectiva = temp_resolv.get("fecha")
        else:
            f_ini_efectivo = fecha_inicio or ctx_sanitizado.get("fecha_inicio")
            f_fin_efectivo = fecha_fin or ctx_sanitizado.get("fecha_fin")
            fecha_efectiva = fecha or ctx_sanitizado.get("fecha")

        f_ini = fecha_inicio or f_ini_efectivo
        f_fin = fecha_fin or f_fin_efectivo
        if not fecha and fecha_efectiva:
            fecha = fecha_efectiva

        if rol_valido == "Supervisor":
            sede_aplicada = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
            sede_consulta_efectiva = sede_aplicada
        else:
            sede_candidata = params_texto.get("sede") or sede or ctx_sanitizado.get("sede")
            if sede_candidata and str(sede_candidata).strip() in SEDES_PERMITIDAS:
                sede_aplicada = str(sede_candidata).strip()
                sede_consulta_efectiva = sede_aplicada
            else:
                sede_aplicada = "Todas las sedes"
                sede_consulta_efectiva = None

        params_intencion = {
            "fecha_inicio": f_ini,
            "fecha_fin": f_fin,
            "fecha": fecha,
            "sede": sede_consulta_efectiva
        }

    else:
        # Clasificar intención mediante motor local para consulta normal
        clasificacion = clasificar_intencion(pregunta_limpia, fecha_referencia=fecha_referencia)
        intencion = clasificacion["intencion"]
        params_intencion = clasificacion.get("parametros", {})

        if rol_valido == "Supervisor":
            sede_aplicada = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
            sede_consulta_efectiva = sede_aplicada
        else:
            sede_candidata = sede if (sede and str(sede).strip() in SEDES_PERMITIDAS) else params_intencion.get("sede")
            if sede_candidata and str(sede_candidata).strip() in SEDES_PERMITIDAS:
                sede_aplicada = str(sede_candidata).strip()
                sede_consulta_efectiva = sede_aplicada
            else:
                sede_aplicada = "Todas las sedes"
                sede_consulta_efectiva = None

        f_ini = fecha_inicio or params_intencion.get("fecha_inicio")
        f_fin = fecha_fin or params_intencion.get("fecha_fin")

    # 4. Enrutamiento seguro por intención

    # --- INTENCIÓN: HISTORIAL_PERSONA O ULTIMO_INGRESO_PERSONA ---
    if intencion in ("historial_persona", "ultimo_ingreso_persona"):
        if rol_valido == "Consultor":
            return {
                "status": "error",
                "categoria_error": "permiso",
                "codigo_http": 403,
                "intencion": intencion,
                "sede_aplicada": sede_aplicada,
                "mensaje": "Acceso denegado. El rol Consultor no tiene permiso para consultar historiales personales.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": {},
                "origen": "consulta_segura"
            }

        texto_persona = params_intencion.get("texto_persona", "").strip()
        if not texto_persona:
            return {
                "status": "requires_input",
                "intencion": intencion,
                "sede_aplicada": sede_aplicada,
                "mensaje": "No se especificó el nombre de la persona en la consulta. Por favor indique el nombre a consultar.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["texto_persona"],
                "opciones": [],
                "contexto": {},
                "origen": "regla_local"
            }

        try:
            coincidencias_raw = buscar_personas(texto_persona)

            # Filtrar candidatos por sede permitida si el usuario es Supervisor
            if rol_valido == "Supervisor":
                if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
                    return {
                        "status": "error",
                        "categoria_error": "permiso",
                        "codigo_http": 403,
                        "intencion": intencion,
                        "sede_aplicada": sede_aplicada,
                        "mensaje": "El usuario supervisor debe tener una sede válida permitida.",
                        "datos": {},
                        "requiere_aclaracion": False,
                        "requiere_parametros": False,
                        "opciones": [],
                        "contexto": {},
                        "origen": "consulta_segura"
                    }

                coincidencias_filtradas = []
                for c in coincidencias_raw:
                    h_check = obtener_historial_persona(
                        tipo_persona=c["tipo_persona"],
                        persona_id=c["persona_id"],
                        rol="Supervisor",
                        sede_permitida=sede_permitida,
                        limite=1
                    )
                    if h_check.get("total_ingresos", 0) > 0:
                        coincidencias_filtradas.append(c)
                coincidencias = coincidencias_filtradas
            else:
                coincidencias = coincidencias_raw
        except Exception:
            return {
                "status": "error",
                "categoria_error": "servicio",
                "codigo_http": 503,
                "intencion": intencion,
                "sede_aplicada": sede_aplicada,
                "mensaje": "Ocurrió un error al buscar la persona en la base de datos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": {},
                "origen": "consulta_segura"
            }

        if len(coincidencias) == 0:
            return {
                "status": "success",
                "intencion": intencion,
                "sede_aplicada": sede_aplicada,
                "mensaje": f"No se encontró ninguna persona registrada que coincida con '{texto_persona}'.",
                "datos": {"coincidencias": []},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": {},
                "origen": "consulta_segura"
            }

        candidato_seleccionado = None

        if seleccion_persona:
            # Comprobar que la seleccion enviada corresponda realmente a una de las coincidencias encontradas
            tipo_sel = str(seleccion_persona.get("tipo_persona", "")).strip()
            id_sel = seleccion_persona.get("persona_id")

            for c in coincidencias:
                if c["tipo_persona"] == tipo_sel and int(c["persona_id"]) == int(id_sel):
                    candidato_seleccionado = c
                    break

            if not candidato_seleccionado:
                return {
                    "status": "error",
                    "categoria_error": "validacion",
                    "codigo_http": 400,
                    "intencion": intencion,
                    "sede_aplicada": sede_aplicada,
                    "mensaje": "La persona seleccionada no pertenece a los candidatos válidos encontrados para esta búsqueda.",
                    "datos": {},
                    "requiere_aclaracion": True,
                    "requiere_parametros": False,
                    "opciones": coincidencias,
                    "contexto": {},
                    "origen": "consulta_segura"
                }
        else:
            if len(coincidencias) == 1:
                candidato_seleccionado = coincidencias[0]
            else:
                return {
                    "status": "requires_input",
                    "intencion": intencion,
                    "sede_aplicada": sede_aplicada,
                    "mensaje": f"Se encontraron {len(coincidencias)} personas que coinciden con '{texto_persona}'. Por favor seleccione una opción.",
                    "datos": {},
                    "requiere_aclaracion": True,
                    "requiere_parametros": False,
                    "opciones": coincidencias,
                    "contexto": {},
                    "origen": "consulta_segura"
                }

        # Ejecutar la consulta del historial seguro
        try:
            historial = obtener_historial_persona(
                tipo_persona=candidato_seleccionado["tipo_persona"],
                persona_id=candidato_seleccionado["persona_id"],
                rol=rol_valido,
                sede_permitida=sede_permitida
            )
        except Exception:
            return {
                "status": "error",
                "categoria_error": "servicio",
                "codigo_http": 503,
                "intencion": intencion,
                "sede_aplicada": sede_aplicada,
                "mensaje": "Ocurrió un error al consultar el historial de la persona en la base de datos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": {},
                "origen": "consulta_segura"
            }

        nombre_p = candidato_seleccionado["nombre"]

        if intencion == "historial_persona":
            total = historial["total_ingresos"]
            msg = f"Se encontraron {total} ingresos para {nombre_p}." if total > 0 else f"{nombre_p} no registra ningún ingreso a la biblioteca."
            return {
                "status": "success",
                "intencion": "historial_persona",
                "sede_aplicada": sede_aplicada,
                "mensaje": msg,
                "datos": {
                    "persona": candidato_seleccionado,
                    "historial": historial
                },
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": {},
                "origen": "consulta_segura"
            }
        else:  # ultimo_ingreso_persona
            registros = historial.get("registros", [])
            if not registros:
                return {
                    "status": "success",
                    "intencion": "ultimo_ingreso_persona",
                    "sede_aplicada": sede_aplicada,
                    "mensaje": f"{nombre_p} no registra ningún ingreso a la biblioteca.",
                    "datos": {
                        "persona": candidato_seleccionado,
                        "ultimo_registro": None
                    },
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "contexto": {},
                    "origen": "consulta_segura"
                }
            else:
                ultimo = registros[0]
                msg = f"El último ingreso registrado de {nombre_p} fue el {ultimo['fecha_hora']} en la sede {ultimo['sede']}."
                return {
                    "status": "success",
                    "intencion": "ultimo_ingreso_persona",
                    "sede_aplicada": sede_aplicada,
                    "mensaje": msg,
                    "datos": {
                        "persona": candidato_seleccionado,
                        "ultimo_registro": ultimo
                    },
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "contexto": {},
                    "origen": "consulta_segura"
                }

    # --- INTENCIÓN: RESUMEN_INGRESOS ---
    elif intencion == "resumen_ingresos":
        f_ini = fecha_inicio or params_intencion.get("fecha_inicio")
        f_fin = fecha_fin or params_intencion.get("fecha_fin")
        if not f_ini or not f_fin:
            return {
                "status": "requires_input",
                "intencion": "resumen_ingresos",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Para consultar el resumen de ingresos debe especificar la fecha de inicio y la fecha de fin (formato YYYY-MM-DD).",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["fecha_inicio", "fecha_fin"],
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "consulta_segura"
            }

        try:
            resumen = obtener_resumen_ingresos(
                fecha_inicio=f_ini,
                fecha_fin=f_fin,
                rol=rol_valido,
                sede_permitida=sede_consulta_efectiva
            )
            nuevo_ctx = {
                "intencion": "resumen_ingresos",
                "fecha_inicio": resumen.get("fecha_inicio", f_ini),
                "fecha_fin": resumen.get("fecha_fin", f_fin)
            }
            if sede_consulta_efectiva:
                nuevo_ctx["sede"] = sede_consulta_efectiva

            return {
                "status": "success",
                "intencion": "resumen_ingresos",
                "sede_aplicada": sede_aplicada,
                "mensaje": f"Resumen de ingresos del {resumen['fecha_inicio']} al {resumen['fecha_fin']}: Total de {resumen['total_ingresos']} ingresos registrados.",
                "datos": resumen,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": sanitizar_contexto(nuevo_ctx),
                "origen": "consulta_segura"
            }
        except Exception:
            return {
                "status": "error",
                "categoria_error": "servicio",
                "codigo_http": 503,
                "intencion": "resumen_ingresos",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Ocurrió un error al generar el resumen de ingresos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "consulta_segura"
            }

    # --- INTENCIÓN: HORAS_MAYOR_AFLUENCIA ---
    elif intencion == "horas_mayor_afluencia":
        f_ini = fecha_inicio or params_intencion.get("fecha_inicio")
        f_fin = fecha_fin or params_intencion.get("fecha_fin")
        if not f_ini or not f_fin:
            return {
                "status": "requires_input",
                "intencion": "horas_mayor_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Para consultar las horas de mayor afluencia debe especificar la fecha de inicio y la fecha de fin (formato YYYY-MM-DD).",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["fecha_inicio", "fecha_fin"],
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "consulta_segura"
            }

        limite_val = limite if limite is not None else 5

        try:
            horas = obtener_horas_mayor_afluencia(
                fecha_inicio=f_ini,
                fecha_fin=f_fin,
                rol=rol_valido,
                sede_permitida=sede_consulta_efectiva,
                limite=limite_val
            )
            nuevo_ctx = {
                "intencion": "horas_mayor_afluencia",
                "fecha_inicio": horas.get("fecha_inicio", f_ini),
                "fecha_fin": horas.get("fecha_fin", f_fin)
            }
            if sede_consulta_efectiva:
                nuevo_ctx["sede"] = sede_consulta_efectiva

            return {
                "status": "success",
                "intencion": "horas_mayor_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": f"Horas de mayor afluencia del {horas['fecha_inicio']} al {horas['fecha_fin']}.",
                "datos": horas,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": sanitizar_contexto(nuevo_ctx),
                "origen": "consulta_segura"
            }
        except Exception:
            return {
                "status": "error",
                "categoria_error": "servicio",
                "codigo_http": 503,
                "intencion": "horas_mayor_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Ocurrió un error al consultar las horas de mayor afluencia.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "consulta_segura"
            }

    # --- INTENCIÓN: PREDICCION_AFLUENCIA ---
    elif intencion == "prediccion_afluencia":
        # Determinar sede efectiva según rol
        if rol_valido == "Supervisor":
            if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
                return {
                    "status": "error",
                    "categoria_error": "permiso",
                    "codigo_http": 403,
                    "intencion": "prediccion_afluencia",
                    "sede_aplicada": sede_aplicada,
                    "mensaje": "El usuario supervisor debe tener una sede válida permitida.",
                    "datos": {},
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "contexto": ctx_sanitizado,
                    "origen": "machine_learning"
                }
            sede_efectiva = str(sede_permitida).strip()
            sede_aplicada = sede_efectiva
        else:
            sede_candidata = str(sede).strip() if (sede and str(sede).strip() in SEDES_PERMITIDAS) else params_intencion.get("sede")
            if sede_candidata and str(sede_candidata).strip() in SEDES_PERMITIDAS:
                sede_efectiva = str(sede_candidata).strip()
                sede_aplicada = sede_efectiva
            else:
                sede_efectiva = None
                sede_aplicada = "Todas las sedes"

        fecha_efectiva = fecha or params_intencion.get("fecha")
        hora_efectiva = hora if hora is not None else params_intencion.get("hora")

        # Comprobar parámetros requeridos para predicción ML
        faltantes = []
        if not fecha_efectiva:
            faltantes.append("fecha")
        if hora_efectiva is None:
            faltantes.append("hora")
        if not sede_efectiva:
            faltantes.append("sede")

        if faltantes:
            return {
                "status": "requires_input",
                "intencion": "prediccion_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Para generar la predicción de afluencia debe especificar la fecha, la hora (8-20) y la sede.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": faltantes,
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "machine_learning"
            }

        # Validar tipo y rango de hora (8 a 20)
        try:
            hora_int = int(hora_efectiva)
            if hora_int < 8 or hora_int > 20:
                raise ValueError("Hora fuera de rango")
        except (TypeError, ValueError):
            return {
                "status": "error",
                "categoria_error": "validacion",
                "codigo_http": 400,
                "intencion": "prediccion_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": "La hora para la predicción debe ser un número entero entre 8 y 20.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["hora"],
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "machine_learning"
            }

        try:
            pred_res = predecir_afluencia_desde_bd(
                fecha=fecha_efectiva,
                hora=hora_int,
                sede=sede_efectiva
            )
            nivel = pred_res["prediccion"]["nivel"]
            msg = f"Predicción de afluencia para el {fecha_efectiva} a las {hora_int}:00 en la sede {sede_efectiva}: Nivel {nivel}."

            nuevo_ctx = {
                "intencion": "prediccion_afluencia",
                "fecha": str(fecha_efectiva)
            }
            if sede_efectiva:
                nuevo_ctx["sede"] = sede_efectiva

            return {
                "status": "success",
                "intencion": "prediccion_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": msg,
                "datos": pred_res,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": sanitizar_contexto(nuevo_ctx),
                "origen": "machine_learning"
            }
        except Exception:
            return {
                "status": "error",
                "categoria_error": "servicio",
                "codigo_http": 503,
                "intencion": "prediccion_afluencia",
                "sede_aplicada": sede_aplicada,
                "mensaje": "Ocurrió un error al generar la predicción de afluencia.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "contexto": ctx_sanitizado,
                "origen": "machine_learning"
            }

    # --- INTENCIÓN: INTENCION_DESCONOCIDA ---
    else:
        return {
            "status": "requires_input",
            "intencion": "intencion_desconocida",
            "sede_aplicada": sede_aplicada,
            "mensaje": "No se reconoció la intención de la pregunta. Ejemplos admitidos: '¿Cuántas veces ingresó [Nombre]?', '¿Cuándo fue el último ingreso de [Nombre]?', 'Resumen de ingresos', 'Horas de mayor afluencia' o 'Predicción de afluencia'.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [
                "¿Cuántas veces ingresó [Nombre]?",
                "¿Cuándo fue el último ingreso de [Nombre]?",
                "Resumen de ingresos del 2026-09-01 al 2026-09-30",
                "Horas de mayor afluencia del 2026-09-01 al 2026-09-30",
                "Predicción de afluencia para 2026-10-15 a las 10:00"
            ],
            "contexto": {},
            "origen": "regla_local"
        }


if __name__ == "__main__":
    print("=== PRUEBA MANUAL LOCAL DE COORDINADOR (DATOS MOCK) ===")
    res_prueba = procesar_pregunta("¿Cuál es la hora con más gente?", rol="SuperAdmin")
    print(res_prueba)
