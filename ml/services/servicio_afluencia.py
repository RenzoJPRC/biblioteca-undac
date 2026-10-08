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
            ]
        },
        "consulta": contexto,
        "variables_modelo": variables,
        "advertencia": resultado_modelo[
            "advertencia"
        ]
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