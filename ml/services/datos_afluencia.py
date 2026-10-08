from datetime import date, datetime

import pandas as pd

from db import get_db_cursor


HORAS_PERMITIDAS = list(range(8, 21))


def convertir_fecha(fecha_objetivo):
    """
    Convierte una fecha de texto YYYY-MM-DD,
    datetime o date en un objeto date.
    """
    if isinstance(fecha_objetivo, datetime):
        return fecha_objetivo.date()

    if isinstance(fecha_objetivo, date):
        return fecha_objetivo

    try:
        return datetime.strptime(
            str(fecha_objetivo),
            "%Y-%m-%d"
        ).date()
    except (TypeError, ValueError):
        raise ValueError(
            "La fecha debe utilizar el formato YYYY-MM-DD."
        )


def obtener_historial_agrupado(
    fecha_objetivo,
    sede="Central"
):
    """
    Obtiene la cantidad de ingresos por fecha y hora
    anteriores a la fecha que se desea predecir.

    Esta función solo ejecuta una consulta SELECT.
    """
    fecha_objetivo = convertir_fecha(
        fecha_objetivo
    )

    consulta = """
        SELECT
            CAST(R.FechaHora AS DATE) AS Fecha,
            DATEPART(HOUR, R.FechaHora) AS Hora,
            COUNT(*) AS CantidadIngresos
        FROM RegistroIngresos R
        WHERE R.FechaHora < ?
          AND ISNULL(R.Sede, 'Central') = ?
        GROUP BY
            CAST(R.FechaHora AS DATE),
            DATEPART(HOUR, R.FechaHora)
        ORDER BY
            Fecha ASC,
            Hora ASC
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError(
                "No fue posible conectarse a SQL Server."
            )

        cursor.execute(
            consulta,
            (
                datetime.combine(
                    fecha_objetivo,
                    datetime.min.time()
                ),
                sede
            )
        )

        filas = cursor.fetchall()

        if not filas:
            raise ValueError(
                "No existen datos históricos para "
                f"la sede '{sede}' antes de "
                f"{fecha_objetivo}."
            )

        columnas = [
            descripcion[0]
            for descripcion in cursor.description
        ]

    historial = pd.DataFrame(
        [list(fila) for fila in filas],
        columns=columnas
    )

    historial["Fecha"] = pd.to_datetime(
        historial["Fecha"]
    ).dt.date

    historial["Hora"] = (
        historial["Hora"].astype(int)
    )

    historial["CantidadIngresos"] = (
        historial["CantidadIngresos"].astype(int)
    )

    return historial


def completar_horas_sin_ingresos(historial):
    """
    Genera las combinaciones de fechas activas
    y horas entre 08:00 y 20:00.

    Cuando no hubo ingresos en una hora, asigna cero.
    Esta operación reproduce la preparación utilizada
    durante el entrenamiento.
    """
    fechas_activas = sorted(
        historial["Fecha"].unique()
    )

    indice_completo = pd.MultiIndex.from_product(
        [
            fechas_activas,
            HORAS_PERMITIDAS
        ],
        names=["Fecha", "Hora"]
    )

    historial_completo = (
        historial
        .set_index(["Fecha", "Hora"])
        .reindex(
            indice_completo,
            fill_value=0
        )
        .reset_index()
    )

    return historial_completo


def construir_variables_afluencia(
    fecha_objetivo,
    hora_objetivo,
    sede="Central"
):
    """
    Calcula las 12 variables utilizadas por el modelo
    para una fecha, hora y sede determinadas.
    """
    fecha_objetivo = convertir_fecha(
        fecha_objetivo
    )

    try:
        hora_objetivo = int(hora_objetivo)
    except (TypeError, ValueError):
        raise ValueError(
            "La hora debe ser un número entero."
        )

    if hora_objetivo not in HORAS_PERMITIDAS:
        raise ValueError(
            "La hora debe encontrarse entre "
            "08:00 y 20:00."
        )

    sede = str(sede).strip()

    if not sede:
        raise ValueError(
            "Debe especificar una sede."
        )

    historial = obtener_historial_agrupado(
        fecha_objetivo,
        sede
    )

    historial_completo = (
        completar_horas_sin_ingresos(historial)
    )

    historial_hora = (
        historial_completo[
            historial_completo["Hora"]
            == hora_objetivo
        ]
        .sort_values("Fecha")
        .reset_index(drop=True)
    )

    if len(historial_hora) < 5:
        raise ValueError(
            "Se necesitan al menos cinco fechas "
            "históricas para predecir esta sede y hora. "
            f"Actualmente existen {len(historial_hora)}."
        )

    ingresos_anteriores = (
        historial_hora["CantidadIngresos"]
        .astype(float)
    )

    ingresos_dia_anterior = float(
        ingresos_anteriores.iloc[-1]
    )

    ingresos_cinco_dias_antes = float(
        ingresos_anteriores.iloc[-5]
    )

    promedio_movil_5 = float(
        ingresos_anteriores.tail(5).mean()
    )

    promedio_historico_hora = float(
        ingresos_anteriores.mean()
    )

    tendencia_reciente = (
        ingresos_dia_anterior
        - promedio_movil_5
    )

    dia_semana = fecha_objetivo.weekday()
    semana_anio = int(
        fecha_objetivo.isocalendar().week
    )

    variables = {
        "Hora": hora_objetivo,
        "DiaSemana": dia_semana,
        "Mes": fecha_objetivo.month,
        "DiaMes": fecha_objetivo.day,
        "SemanaAnio": semana_anio,
        "EsLunes": int(dia_semana == 0),
        "EsViernes": int(dia_semana == 4),
        "IngresosDiaAnterior":
            ingresos_dia_anterior,
        "IngresosCincoDiasAntes":
            ingresos_cinco_dias_antes,
        "PromedioMovil5":
            promedio_movil_5,
        "PromedioHistoricoHora":
            promedio_historico_hora,
        "TendenciaReciente":
            tendencia_reciente
    }

    contexto = {
        "fecha": fecha_objetivo.isoformat(),
        "hora": hora_objetivo,
        "sede": sede,
        "fechas_historicas": int(
            len(historial_hora)
        ),
        "ultima_fecha_disponible": (
            historial_hora["Fecha"]
            .iloc[-1]
            .isoformat()
        )
    }

    return variables, contexto


if __name__ == "__main__":
    variables_prueba, contexto_prueba = (
        construir_variables_afluencia(
            fecha_objetivo="2026-09-29",
            hora_objetivo=10,
            sede="Central"
        )
    )

    print("Contexto:")
    print(contexto_prueba)

    print("\nVariables:")
    for nombre, valor in variables_prueba.items():
        print(f"{nombre}: {valor}")