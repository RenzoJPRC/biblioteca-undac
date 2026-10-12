from ml.services.datos_afluencia import (
    construir_variables_afluencia,
    obtener_historial_afluencia_periodo
)
from ml.services.predictor_afluencia import (
    obtener_predictor_afluencia
)


def predecir_afluencia_desde_bd(
    fecha,
    hora,
    sede="Central"
):
    """
    Ejecuta el flujo completo:

    1. Consulta el historial de SQL Server.
    2. Calcula las 12 variables.
    3. Ejecuta el modelo de Machine Learning.
    4. Devuelve predicción, probabilidades y contexto.
    """
    variables, contexto = (
        construir_variables_afluencia(
            fecha_objetivo=fecha,
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

    advertencia_base = resultado_modelo.get("advertencia", "")
    if modo_demo:
        dias_ant = contexto.get("dias_antiguedad_ultimo_registro", 0)
        fechas_cnt = contexto.get("fechas_historicas", 0)
        prefijo_advertencia = (
            f"[MODO DEMOSTRACIÓN - NO CONFIABLE] Historial insuficiente o desactualizado "
            f"({fechas_cnt} fechas registradas, último registro con {dias_ant} días de desfase). "
            "La estimación tiene carácter estrictamente ilustrativo y no debe utilizarse para decisiones operativas. "
        )
        advertencia_final = prefijo_advertencia + advertencia_base
    else:
        prefijo_calidad = (
            "El historial cumple los criterios mínimos de calidad operativa de entrada (cobertura temporal y rezagos), "
            "pero los umbrales no constituyen una prueba formal de certeza; la estimación es únicamente de apoyo referencial. "
        )
        advertencia_final = prefijo_calidad + advertencia_base

    return {
        "status": "success",
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
            "modo_demostracion": modo_demo
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