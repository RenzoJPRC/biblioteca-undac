from datetime import date, datetime, timedelta

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

    ultima_fecha_disponible = historial_hora["Fecha"].iloc[-1]
    if isinstance(ultima_fecha_disponible, datetime):
        ultima_fecha_date = ultima_fecha_disponible.date()
    elif isinstance(ultima_fecha_disponible, date):
        ultima_fecha_date = ultima_fecha_disponible
    else:
        ultima_fecha_date = datetime.strptime(
            str(ultima_fecha_disponible),
            "%Y-%m-%d"
        ).date()

    # NOTA METODOLÓGICA SOBRE LOS UMBRALES:
    # Los umbrales de 14 días (desfase máximo) y 15 fechas (volumen mínimo)
    # son criterios heurísticos de control de calidad del historial y cobertura
    # de entrada. No provienen de una curva formal de degradación empírica en el
    # entrenamiento. Por tanto, su cumplimiento NO es prueba de certeza predictiva,
    # sino una condición mínima de cobertura temporal para una estimación referencial.
    dias_antiguedad = max(0, (fecha_objetivo - ultima_fecha_date).days)
    es_desactualizado = dias_antiguedad > 14
    historial_insuficiente = len(historial_hora) < 15
    calidad_minima_historial = bool((not es_desactualizado) and (not historial_insuficiente))
    modo_demostracion = not calidad_minima_historial

    motivos_advertencia = []
    if es_desactualizado:
        motivos_advertencia.append(
            f"El último registro disponible tiene {dias_antiguedad} días de desfase "
            f"respecto a la fecha consultada ({ultima_fecha_date.isoformat()}), superando el criterio operativo mínimo de 14 días. "
            "Las variables de rezago ('día anterior' y 'últimos 5 días') no reflejan la dinámica reciente del recinto."
        )
    if historial_insuficiente:
        motivos_advertencia.append(
            f"El historial disponible contiene solo {len(historial_hora)} fechas "
            "(criterio operativo mínimo: 15 fechas para cálculo representativo de rezagos y promedios móviles)."
        )

    contexto = {
        "fecha": fecha_objetivo.isoformat(),
        "hora": hora_objetivo,
        "sede": sede,
        "fechas_historicas": int(
            len(historial_hora)
        ),
        "ultima_fecha_disponible": (
            ultima_fecha_date.isoformat()
        ),
        "dias_antiguedad_ultimo_registro": int(dias_antiguedad),
        "es_desactualizado": es_desactualizado,
        "historial_insuficiente": historial_insuficiente,
        "calidad_minima_historial": calidad_minima_historial,
        "criterio_justificado_por_evaluacion": False,
        "confiable": calidad_minima_historial,
        "modo_demostracion": modo_demostracion,
        "motivos_advertencia": motivos_advertencia
    }

    return variables, contexto


def obtener_historial_afluencia_periodo(sede="Central", dias=30):
    """
    Obtiene el comportamiento histórico agrupado por hora para una sede
    durante los últimos 7 o 30 días basándose en la última fecha registrada.

    No realiza escrituras ni modifica datos.
    Retorna exclusivamente datos agregados sin PII.
    """
    if dias not in (7, 30):
        raise ValueError("El parámetro dias debe ser 7 o 30.")

    SEDES_PERMITIDAS = {
        "Central",
        "Tarma",
        "La Merced",
        "Oxapampa",
        "Paucartambo",
        "Yanahuanca"
    }
    if sede not in SEDES_PERMITIDAS:
        raise ValueError("La sede especificada no es válida.")

    consulta_ultima_fecha = """
        SELECT MAX(CAST(R.FechaHora AS DATE)) AS UltimaFecha
        FROM RegistroIngresos R
        WHERE ISNULL(R.Sede, 'Central') = ?
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        cursor.execute(consulta_ultima_fecha, (sede,))
        fila = cursor.fetchone()
        ultima_fecha = fila[0] if fila and fila[0] else None

        if not ultima_fecha:
            return {
                "status": "success",
                "sede": sede,
                "periodo_dias": dias,
                "fecha_inicio": None,
                "fecha_fin": None,
                "fechas_con_datos": 0,
                "total_ingresos_periodo": 0,
                "horas": [],
                "totales": [],
                "promedios": [],
                "referencia_horaria": "Hora almacenada en la base de datos"
            }

        if isinstance(ultima_fecha, datetime):
            fecha_fin = ultima_fecha.date()
        elif isinstance(ultima_fecha, str):
            fecha_fin = datetime.strptime(ultima_fecha, "%Y-%m-%d").date()
        else:
            fecha_fin = ultima_fecha

        fecha_inicio = fecha_fin - timedelta(days=dias - 1)

        consulta_ingresos = """
            SELECT
                CAST(R.FechaHora AS DATE) AS Fecha,
                DATEPART(HOUR, R.FechaHora) AS Hora,
                COUNT(*) AS CantidadIngresos
            FROM RegistroIngresos R
            WHERE ISNULL(R.Sede, 'Central') = ?
              AND CAST(R.FechaHora AS DATE) BETWEEN ? AND ?
            GROUP BY
                CAST(R.FechaHora AS DATE),
                DATEPART(HOUR, R.FechaHora)
            ORDER BY
                Hora ASC
        """

        cursor.execute(
            consulta_ingresos,
            (sede, fecha_inicio.isoformat(), fecha_fin.isoformat())
        )
        filas = cursor.fetchall()

    if not filas:
        return {
            "status": "success",
            "sede": sede,
            "periodo_dias": dias,
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
            "fechas_con_datos": 0,
            "total_ingresos_periodo": 0,
            "horas": [],
            "totales": [],
            "promedios": [],
            "referencia_horaria": "Hora almacenada en la base de datos"
        }

    fechas_unicas = set(f[0] for f in filas)
    fechas_con_datos = len(fechas_unicas)

    horas_registradas = set(int(f[1]) for f in filas)
    horas = sorted(list(set(range(8, 21)).union(horas_registradas)))

    totales = []
    promedios = []

    for h in horas:
        total_h = sum(int(f[2]) for f in filas if int(f[1]) == h)
        promedio_h = round(total_h / fechas_con_datos, 2) if fechas_con_datos > 0 else 0.0
        totales.append(total_h)
        promedios.append(promedio_h)

    total_ingresos_periodo = sum(totales)

    return {
        "status": "success",
        "sede": sede,
        "periodo_dias": dias,
        "fecha_inicio": fecha_inicio.isoformat(),
        "fecha_fin": fecha_fin.isoformat(),
        "fechas_con_datos": fechas_con_datos,
        "total_ingresos_periodo": total_ingresos_periodo,
        "horas": horas,
        "totales": totales,
        "promedios": promedios,
        "referencia_horaria": "Hora almacenada en la base de datos"
    }



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