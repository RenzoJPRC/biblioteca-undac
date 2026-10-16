from datetime import date, datetime

from ml.services.datos_afluencia import (
    construir_variables_afluencia,
    obtener_historial_afluencia_periodo,
    FERIADOS_CONFIRMADOS_2026,
    CIERRE_CONFIRMADO_27_JULIO
)
from ml.services.predictor_afluencia import (
    obtener_predictor_afluencia
)

# Cierres institucionales adicionales confirmados en el calendario de biblioteca 2026
CIERRES_ADICIONALES_2026 = {
    date(2026, 7, 24),  # Cierre no laborable puente FAP
}


def predecir_afluencia_desde_bd(
    fecha,
    hora,
    sede="Central"
):
    """
    Ejecuta el flujo completo:

    1. Verifica si la fecha consultada corresponde a un día de cierre o feriado.
    2. Consulta el historial de SQL Server.
    3. Calcula las 12 variables.
    4. Ejecuta el modelo de Machine Learning.
    5. Devuelve predicción, probabilidades y contexto.
    """
    if isinstance(fecha, datetime):
        fecha_obj = fecha.date()
    elif isinstance(fecha, date):
        fecha_obj = fecha
    elif isinstance(fecha, str):
        fecha_obj = datetime.strptime(fecha.strip(), "%Y-%m-%d").date()
    else:
        fecha_obj = fecha

    fecha_str = fecha_obj.isoformat() if hasattr(fecha_obj, "isoformat") else str(fecha_obj)

    # 1. Verificación contra el calendario de cierres y feriados confirmado en el proyecto
    es_cerrada = False
    motivo_cierre = None

    if hasattr(fecha_obj, "weekday") and fecha_obj.weekday() >= 5:
        es_cerrada = True
        motivo_cierre = "Fin de semana (sábado/domingo): la biblioteca no opera en estos días."
    elif fecha_str in FERIADOS_CONFIRMADOS_2026:
        es_cerrada = True
        motivo_cierre = "Feriado nacional oficial confirmado en el calendario institucional."
    elif fecha_str == CIERRE_CONFIRMADO_27_JULIO or fecha_obj in CIERRES_ADICIONALES_2026:
        es_cerrada = True
        motivo_cierre = "Cierre institucional programado en el calendario de biblioteca."
    elif isinstance(fecha_obj, date) and date(2026, 8, 1) <= fecha_obj <= date(2026, 8, 23):
        es_cerrada = True
        motivo_cierre = "Receso institucional programado (vacaciones del 01 al 23 de agosto de 2026)."

    if es_cerrada:
        return {
            "status": "biblioteca_cerrada",
            "mensaje": "Biblioteca cerrada; no corresponde emitir un pronóstico operativo.",
            "es_cerrada": True,
            "motivo_cierre": motivo_cierre,
            "periodo": {
                "fecha": fecha_str,
                "hora": int(hora)
            },
            "sede": sede,
            "prediccion": {
                "nivel": "Cerrado",
                "probabilidades": {
                    "Bajo": 0.0,
                    "Medio": 0.0,
                    "Alto": 0.0
                },
                "es_biblioteca_cerrada": True
            },
            "advertencia": "Biblioteca cerrada; no corresponde emitir un pronóstico operativo."
        }

    variables, contexto = (
        construir_variables_afluencia(
            fecha_objetivo=fecha_obj,
            hora_objetivo=hora,
            sede=sede
        )
    )

    predictor = obtener_predictor_afluencia()

    resultado_modelo = predictor.predecir(
        variables
    )

    calidad_minima = bool(contexto.get("calidad_minima_historial", contexto.get("confiable", True)))
    modo_demo = bool(contexto.get("modo_demostracion", False))

    es_parcial = bool(contexto.get("es_franja_parcial", False))
    duracion_min = int(contexto.get("duracion_minutos", 45 if es_parcial else 60))

    advertencia_base = resultado_modelo.get("advertencia", "")
    if modo_demo:
        dias_ant = contexto.get("dias_antiguedad_ultimo_registro", 0)
        fechas_cnt = contexto.get("fechas_historicas", 0)
        ult_fecha_txt = contexto.get("ultima_fecha_disponible", "")
        fecha_obj_txt = contexto.get("fecha", "")
        try:
            from datetime import datetime as dt
            ult_f_corta = dt.strptime(ult_fecha_txt, "%Y-%m-%d").strftime("%d/%m")
        except Exception:
            ult_f_corta = ult_fecha_txt
        prefijo_advertencia = (
            f"[MODO DEMOSTRACIÓN - NO CONFIABLE] Historial insuficiente o desactualizado "
            f"({fechas_cnt} fechas registradas; el último registro es del {ult_f_corta} y está "
            f"{dias_ant} días antes de la fecha objetivo {fecha_obj_txt}). "
            "La estimación tiene carácter estrictamente ilustrativo y no debe utilizarse para decisiones operativas. "
        )
        advertencia_final = prefijo_advertencia + advertencia_base
    else:
        prefijo_calidad = (
            "El historial cumple los criterios mínimos de calidad operativa de entrada (cobertura temporal y rezagos), "
            "pero los umbrales no constituyen una prueba formal de certeza; la estimación es únicamente de apoyo referencial. "
        )
        advertencia_final = prefijo_calidad + advertencia_base

    if es_parcial:
        prefijo_parcial = (
            "[FRANJA PARCIAL: 45 MINUTOS] La franja 20:00–20:45 contiene únicamente 45 minutos de operación efectiva "
            "(el servidor se apagaba a las 20:45); no debe compararse directamente con franjas completas de 60 minutos. "
        )
        advertencia_final = prefijo_parcial + advertencia_final

    return {
        "status": "success",
        "periodo": {
            "fecha": str(fecha),
            "hora": int(hora)
        },
        "sede": sede,
        "fuente": "Registros consolidados de molinetes y modelo predictivo RandomForestClassifier",
        "limites_cobertura": {
            "fecha_minima_sistema": "2026-04-10",
            "fecha_maxima_sistema": contexto.get("ultima_fecha_disponible", "2026-09-28"),
            "horario_operativo": "08:00 a 20:45 (lunes a viernes)",
            "franja_20h_parcial": "20:00 a 20:45 (45 minutos por apagado del servidor)",
            "sedes_con_historial_suficiente": ["Central"],
            "sedes_con_datos_insuficientes": ["Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
        },
        "prediccion": {
            "nivel": resultado_modelo[
                "nivel_estimado"
            ],
            "probabilidades": resultado_modelo[
                "probabilidades"
            ],
            "version_modelo": resultado_modelo[
                "version_modelo"
            ],
            "calidad_minima_historial": calidad_minima,
            "confiable": calidad_minima,
            "modo_demostracion": modo_demo,
            "es_franja_parcial": es_parcial,
            "duracion_minutos": duracion_min
        },
        "consulta": contexto,
        "variables_modelo": variables,
        "advertencia": advertencia_final
    }


def obtener_informacion_modelo():
    predictor = obtener_predictor_afluencia()
    return predictor.obtener_informacion()


if __name__ == "__main__":
    resultado = predecir_afluencia_desde_bd(
        fecha="2026-09-29",
        hora=10,
        sede="Central"
    )

    print("Predicción completa:")
    print(resultado)