from datetime import datetime, date

from llm.services.consultas_biblioteca import (
    validar_rol,
    buscar_personas,
    obtener_historial_persona,
    obtener_resumen_ingresos,
    obtener_horas_mayor_afluencia,
    SEDES_PERMITIDAS
)
from llm.services.motor_intenciones import clasificar_intencion
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
    limite=None
):
    """
    Coordinador local del Asistente LLM de Biblioteca.

    Evalúa la pregunta enviada por el usuario en lenguaje natural,
    clasifica la intención mediante el motor local y la redirige de forma
    segura a las funciones de consulta SQL Server o al servicio de Machine Learning.
    """
    # 1. Validar la pregunta
    if not pregunta or not isinstance(pregunta, str):
        return {
            "status": "error",
            "intencion": "intencion_desconocida",
            "mensaje": "La pregunta es obligatoria y debe ser un texto válido.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "origen": "regla_local"
        }

    pregunta_limpia = pregunta.strip()
    if not pregunta_limpia:
        return {
            "status": "error",
            "intencion": "intencion_desconocida",
            "mensaje": "La pregunta no puede estar vacía o contener solo espacios.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "origen": "regla_local"
        }

    if len(pregunta_limpia) > 500:
        return {
            "status": "error",
            "intencion": "intencion_desconocida",
            "mensaje": "La pregunta excede el límite máximo permitido de 500 caracteres.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "origen": "regla_local"
        }

    # 2. Validar el rol del usuario
    try:
        rol_valido = validar_rol(rol)
    except PermissionError as e:
        return {
            "status": "error",
            "intencion": "intencion_desconocida",
            "mensaje": str(e),
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "origen": "regla_local"
        }

    # 3. Clasificar intención mediante motor local
    clasificacion = clasificar_intencion(pregunta_limpia)
    intencion = clasificacion["intencion"]
    params_intencion = clasificacion.get("parametros", {})

    # 4. Enrutamiento seguro por intención

    # --- INTENCIÓN: HISTORIAL_PERSONA O ULTIMO_INGRESO_PERSONA ---
    if intencion in ("historial_persona", "ultimo_ingreso_persona"):
        if rol_valido == "Consultor":
            return {
                "status": "error",
                "intencion": intencion,
                "mensaje": "Acceso denegado. El rol Consultor no tiene permiso para consultar historiales personales.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }

        texto_persona = params_intencion.get("texto_persona", "").strip()
        if not texto_persona:
            return {
                "status": "requires_input",
                "intencion": intencion,
                "mensaje": "No se especificó el nombre de la persona en la consulta. Por favor indique el nombre a consultar.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["texto_persona"],
                "opciones": [],
                "origen": "regla_local"
            }

        try:
            coincidencias_raw = buscar_personas(texto_persona)

            # Filtrar candidatos por sede permitida si el usuario es Supervisor
            if rol_valido == "Supervisor":
                if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
                    return {
                        "status": "error",
                        "intencion": intencion,
                        "mensaje": "El usuario supervisor debe tener una sede válida permitida.",
                        "datos": {},
                        "requiere_aclaracion": False,
                        "requiere_parametros": False,
                        "opciones": [],
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
                "intencion": intencion,
                "mensaje": "Ocurrió un error al buscar la persona en la base de datos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }

        if len(coincidencias) == 0:
            return {
                "status": "success",
                "intencion": intencion,
                "mensaje": f"No se encontró ninguna persona registrada que coincida con '{texto_persona}'.",
                "datos": {"coincidencias": []},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
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
                    "intencion": intencion,
                    "mensaje": "La persona seleccionada no pertenece a los candidatos válidos encontrados para esta búsqueda.",
                    "datos": {},
                    "requiere_aclaracion": True,
                    "requiere_parametros": False,
                    "opciones": coincidencias,
                    "origen": "consulta_segura"
                }
        else:
            if len(coincidencias) == 1:
                candidato_seleccionado = coincidencias[0]
            else:
                return {
                    "status": "requires_input",
                    "intencion": intencion,
                    "mensaje": f"Se encontraron {len(coincidencias)} personas que coinciden con '{texto_persona}'. Por favor seleccione una opción.",
                    "datos": {},
                    "requiere_aclaracion": True,
                    "requiere_parametros": False,
                    "opciones": coincidencias,
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
                "intencion": intencion,
                "mensaje": "Ocurrió un error al consultar el historial de la persona en la base de datos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }


        nombre_p = candidato_seleccionado["nombre"]

        if intencion == "historial_persona":
            total = historial["total_ingresos"]
            msg = f"Se encontraron {total} ingresos para {nombre_p}." if total > 0 else f"{nombre_p} no registra ningún ingreso a la biblioteca."
            return {
                "status": "success",
                "intencion": "historial_persona",
                "mensaje": msg,
                "datos": {
                    "persona": candidato_seleccionado,
                    "historial": historial
                },
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }
        else:  # ultimo_ingreso_persona
            registros = historial.get("registros", [])
            if not registros:
                return {
                    "status": "success",
                    "intencion": "ultimo_ingreso_persona",
                    "mensaje": f"{nombre_p} no registra ningún ingreso a la biblioteca.",
                    "datos": {
                        "persona": candidato_seleccionado,
                        "ultimo_registro": None
                    },
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "origen": "consulta_segura"
                }
            else:
                ultimo = registros[0]
                msg = f"El último ingreso registrado de {nombre_p} fue el {ultimo['fecha_hora']} en la sede {ultimo['sede']}."
                return {
                    "status": "success",
                    "intencion": "ultimo_ingreso_persona",
                    "mensaje": msg,
                    "datos": {
                        "persona": candidato_seleccionado,
                        "ultimo_registro": ultimo
                    },
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "origen": "consulta_segura"
                }

    # --- INTENCIÓN: RESUMEN_INGRESOS ---
    elif intencion == "resumen_ingresos":
        if not fecha_inicio or not fecha_fin:
            return {
                "status": "requires_input",
                "intencion": "resumen_ingresos",
                "mensaje": "Para consultar el resumen de ingresos debe especificar la fecha de inicio y la fecha de fin (formato YYYY-MM-DD).",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["fecha_inicio", "fecha_fin"],
                "opciones": [],
                "origen": "consulta_segura"
            }

        try:
            resumen = obtener_resumen_ingresos(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                rol=rol_valido,
                sede_permitida=sede_permitida
            )
            return {
                "status": "success",
                "intencion": "resumen_ingresos",
                "mensaje": f"Resumen de ingresos del {resumen['fecha_inicio']} al {resumen['fecha_fin']}: Total de {resumen['total_ingresos']} ingresos registrados.",
                "datos": resumen,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }
        except Exception:
            return {
                "status": "error",
                "intencion": "resumen_ingresos",
                "mensaje": "Ocurrió un error al generar el resumen de ingresos.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }

    # --- INTENCIÓN: HORAS_MAYOR_AFLUENCIA ---
    elif intencion == "horas_mayor_afluencia":
        if not fecha_inicio or not fecha_fin:
            return {
                "status": "requires_input",
                "intencion": "horas_mayor_afluencia",
                "mensaje": "Para consultar las horas de mayor afluencia debe especificar la fecha de inicio y la fecha de fin (formato YYYY-MM-DD).",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["fecha_inicio", "fecha_fin"],
                "opciones": [],
                "origen": "consulta_segura"
            }

        limite_val = limite if limite is not None else 5

        try:
            horas = obtener_horas_mayor_afluencia(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                rol=rol_valido,
                sede_permitida=sede_permitida,
                limite=limite_val
            )
            return {
                "status": "success",
                "intencion": "horas_mayor_afluencia",
                "mensaje": f"Horas de mayor afluencia del {horas['fecha_inicio']} al {horas['fecha_fin']}.",
                "datos": horas,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }
        except Exception:
            return {
                "status": "error",
                "intencion": "horas_mayor_afluencia",
                "mensaje": "Ocurrió un error al consultar las horas de mayor afluencia.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "consulta_segura"
            }

    # --- INTENCIÓN: PREDICCION_AFLUENCIA ---
    elif intencion == "prediccion_afluencia":
        # Determinar sede efectiva según rol
        if rol_valido == "Supervisor":
            if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
                return {
                    "status": "error",
                    "intencion": "prediccion_afluencia",
                    "mensaje": "El usuario supervisor debe tener una sede válida permitida.",
                    "datos": {},
                    "requiere_aclaracion": False,
                    "requiere_parametros": False,
                    "opciones": [],
                    "origen": "machine_learning"
                }
            sede_efectiva = str(sede_permitida).strip()
        else:
            sede_efectiva = str(sede).strip() if sede and str(sede).strip() in SEDES_PERMITIDAS else (str(sede_permitida).strip() if sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS else "Central")

        # Comprobar parámetros requeridos para predicción ML
        faltantes = []
        if not fecha:
            faltantes.append("fecha")
        if hora is None:
            faltantes.append("hora")
        if not sede_efectiva:
            faltantes.append("sede")

        if faltantes:
            return {
                "status": "requires_input",
                "intencion": "prediccion_afluencia",
                "mensaje": "Para generar la predicción de afluencia debe especificar la fecha, la hora (8-20) y la sede.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": faltantes,
                "opciones": [],
                "origen": "machine_learning"
            }

        # Validar tipo y rango de hora (8 a 20)
        try:
            hora_int = int(hora)
            if hora_int < 8 or hora_int > 20:
                raise ValueError("Hora fuera de rango")
        except (TypeError, ValueError):
            return {
                "status": "error",
                "intencion": "prediccion_afluencia",
                "mensaje": "La hora para la predicción debe ser un número entero entre 8 y 20.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": True,
                "parametros_faltantes": ["hora"],
                "opciones": [],
                "origen": "machine_learning"
            }

        try:
            pred_res = predecir_afluencia_desde_bd(
                fecha=fecha,
                hora=hora_int,
                sede=sede_efectiva
            )
            nivel = pred_res["prediccion"]["nivel"]
            msg = f"Predicción de afluencia para el {fecha} a las {hora_int}:00 en la sede {sede_efectiva}: Nivel {nivel}."

            return {
                "status": "success",
                "intencion": "prediccion_afluencia",
                "mensaje": msg,
                "datos": pred_res,
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "machine_learning"
            }
        except Exception:
            return {
                "status": "error",
                "intencion": "prediccion_afluencia",
                "mensaje": "Ocurrió un error al generar la predicción de afluencia.",
                "datos": {},
                "requiere_aclaracion": False,
                "requiere_parametros": False,
                "opciones": [],
                "origen": "machine_learning"
            }


    # --- INTENCIÓN: INTENCION_DESCONOCIDA ---
    else:
        return {
            "status": "requires_input",
            "intencion": "intencion_desconocida",
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
            "origen": "regla_local"
        }


if __name__ == "__main__":
    print("=== PRUEBA MANUAL LOCAL DE COORDINADOR (DATOS MOCK) ===")
    res_prueba = procesar_pregunta("¿Cuál es la hora con más gente?", rol="SuperAdmin")
    print(res_prueba)
