from datetime import date, datetime, timedelta

import pandas as pd

from db import get_db_cursor


HORAS_PERMITIDAS = list(range(8, 21))

# Calendario institucional confirmado 2026 (excluido de entrenamiento)
FERIADOS_CONFIRMADOS_2026 = {
    "2026-04-02",  # Jueves Santo
    "2026-04-03",  # Viernes Santo
    "2026-05-01",  # Día del Trabajo
    "2026-06-07",  # Batalla de Arica y Día de la Bandera
    "2026-06-29",  # San Pedro y San Pablo
    "2026-07-23",  # Día de la Fuerza Aérea del Perú
    "2026-07-28",  # Fiestas Patrias
    "2026-07-29",  # Fiestas Patrias
    "2026-08-06",  # Batalla de Junín
    "2026-08-30",  # Santa Rosa de Lima
    "2026-10-08",  # Combate de Angamos
    "2026-11-01",  # Todos los Santos
    "2026-12-08",  # Inmaculada Concepción
    "2026-12-09",  # Batalla de Ayacucho
    "2026-12-25",  # Navidad
}
CIERRE_CONFIRMADO_27_JULIO = "2026-07-27"


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

    Utiliza exclusivamente la tabla analítica separada AfluenciaHistoricaAgregada,
    excluyendo filas en horario atípico (EsHorarioAtipico = 0) y filas durante el
    cierre de agosto (EsCierreAgosto = 0).

    NO completa sedes sin historial suficiente utilizando RegistroIngresos
    (las filas actuales son de prueba manual). Si la sede no tiene historial suficiente,
    devuelve un estado explícito de datos insuficientes (ValueError).
    Esta función solo ejecuta consultas SELECT.
    """
    fecha_objetivo = convertir_fecha(
        fecha_objetivo
    )

    sede = str(sede).strip()
    if not sede:
        raise ValueError("Debe especificar una sede válida.")

    consulta_analitica = """
        SELECT
            Fecha,
            Hora,
            CantidadIngresos
        FROM dbo.AfluenciaHistoricaAgregada
        WHERE Fecha < ?
          AND Sede = ?
          AND EsHorarioAtipico = 0
          AND EsCierreAgosto = 0
          AND EsCierre27Julio = 0
          AND EsFeriado = 0
        ORDER BY
            Fecha ASC,
            Hora ASC
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError(
                "No fue posible conectarse a SQL Server."
            )

        filas = []
        try:
            cursor.execute(consulta_analitica, (fecha_objetivo.isoformat(), sede))
            filas = cursor.fetchall()
        except Exception:
            filas = []

        if not filas:
            raise ValueError(
                f"Historial insuficiente: La sede '{sede}' no cuenta con datos "
                "históricos suficientes para generar un pronóstico confiable "
                "(requiere al menos 15 fechas operativas registradas). "
                "No se completa con registros de prueba de RegistroIngresos."
            )

        columnas = [
            descripcion[0]
            for descripcion in cursor.description
        ]

    historial = pd.DataFrame(
        [list(fila) for fila in filas],
        columns=columnas
    )

    # Filtrar rigurosamente feriados y cierre del 27 de julio si vienen de mocks o datos sin procesar
    historial["_fecha_str"] = historial["Fecha"].astype(str)
    historial = historial[
        (~historial["_fecha_str"].isin(FERIADOS_CONFIRMADOS_2026)) &
        (historial["_fecha_str"] != CIERRE_CONFIRMADO_27_JULIO)
    ].drop(columns=["_fecha_str"]).reset_index(drop=True)

    if historial.empty:
        raise ValueError(
            f"Historial insuficiente: Tras excluir feriados y cierres, la sede '{sede}' "
            "no cuenta con datos operativos válidos para generar un pronóstico confiable."
        )

    historial["Fecha"] = pd.to_datetime(
        historial["Fecha"]
    ).dt.date

    fechas_operativas = historial["Fecha"].nunique()
    if sede != "Central" and fechas_operativas < 15:
        raise ValueError(
            f"Historial insuficiente: La sede '{sede}' cuenta con {fechas_operativas} fecha(s) "
            "operativa(s) registrada(s) en la base histórica analítica, no alcanzando el mínimo "
            "configurado de 15 fechas operativas para generar un pronóstico confiable. "
            "No se completa con registros de prueba de RegistroIngresos."
        )

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
            "Historial insuficiente: Se necesitan al menos cinco fechas "
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

    es_parcial = bool(hora_objetivo == 20)
    duracion_min = 45 if es_parcial else 60
    nota_parcial = (
        "La franja 20:00 corresponde a operación parcial (45 minutos, con cierre de atención "
        "y apagado a las 20:45). No debe compararse directamente con franjas completas de 60 minutos."
    ) if es_parcial else None

    motivos_advertencia = []
    if es_parcial:
        motivos_advertencia.append(nota_parcial)
    if es_desactualizado:
        f_ult_corta = ultima_fecha_date.strftime("%d/%m")
        f_ult_fmt = ultima_fecha_date.strftime("%d/%m/%Y")
        f_obj_fmt = fecha_objetivo.strftime("%d/%m/%Y")
        motivos_advertencia.append(
            f"El último registro en base de datos es del {f_ult_corta} ({f_ult_fmt}) y está "
            f"{dias_antiguedad} días antes de la fecha objetivo consultada ({f_obj_fmt}), "
            "superando el criterio operativo máximo de 14 días. Las variables de rezago ('día anterior' y 'últimos 5 días') "
            "no reflejan la dinámica reciente del recinto."
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
        "es_franja_parcial": es_parcial,
        "duracion_minutos": duracion_min,
        "nota_franja_parcial": nota_parcial,
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
        SELECT MAX(Fecha) AS UltimaFecha
        FROM dbo.AfluenciaHistoricaAgregada
        WHERE Sede = ?
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
                "total_ingresos_fuera_horario": 0,
                "horas": [],
                "totales": [],
                "promedios": [],
                "unidad": "Ingresos acumulados en molinetes (entradas en torniquetes)",
                "fuente_datos": "Registros consolidados de molinetes",
                "criterio_horario": "Horario habitual de atención (08:00 a 20:00)",
                "registros_fuera_horario": {
                    "total": 0,
                    "criterio_aplicado": "Excluidos del gráfico operativo por encontrarse fuera del horario habitual de atención (08:00 a 20:00).",
                    "detalle": []
                },
                "referencia_horaria": "Hora almacenada en la base de datos (horario habitual de 08:00 a 20:00)"
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
                Fecha,
                Hora,
                SUM(CantidadIngresos) AS CantidadIngresos
            FROM dbo.AfluenciaHistoricaAgregada
            WHERE Sede = ?
              AND Fecha BETWEEN ? AND ?
            GROUP BY
                Fecha,
                Hora
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
            "total_ingresos_fuera_horario": 0,
            "horas": [],
            "totales": [],
            "promedios": [],
            "criterio_horario": "Horario habitual de atención (08:00 a 20:00)",
            "registros_fuera_horario": {
                "total": 0,
                "criterio_aplicado": "Excluidos del gráfico operativo por encontrarse fuera del horario habitual de atención (08:00 a 20:00).",
                "detalle": []
            },
            "referencia_horaria": "Hora almacenada en la base de datos (horario habitual de 08:00 a 20:00)"
        }

    fechas_unicas = set(f[0] for f in filas)
    fechas_con_datos = len(fechas_unicas)

    # El horario configurado para la biblioteca y el modelo predictivo es 08:00 a 20:00 (HORAS_PERMITIDAS).
    # Se excluyen del gráfico operativo los registros atípicos fuera de ese rango (p. ej. 01:00 o 21:00)
    # y se presentan de forma explícita en registros_fuera_horario indicando el criterio aplicado.
    horas = list(HORAS_PERMITIDAS)

    totales = []
    promedios = []

    for h in horas:
        total_h = sum(int(f[2]) for f in filas if int(f[1]) == h)
        promedio_h = round(total_h / fechas_con_datos, 2) if fechas_con_datos > 0 else 0.0
        totales.append(total_h)
        promedios.append(promedio_h)

    total_ingresos_periodo = sum(totales)

    # Identificar y agrupar registros fuera del horario habitual (08:00 a 20:00)
    filas_fuera_horario = [f for f in filas if int(f[1]) not in HORAS_PERMITIDAS]
    horas_fuera = sorted(list(set(int(f[1]) for f in filas_fuera_horario)))
    detalle_fuera = []
    for h_f in horas_fuera:
        cant_hf = sum(int(f[2]) for f in filas_fuera_horario if int(f[1]) == h_f)
        detalle_fuera.append({
            "hora": h_f,
            "etiqueta": f"{h_f:02d}:00",
            "cantidad": cant_hf
        })
    total_fuera = sum(d["cantidad"] for d in detalle_fuera)

    return {
        "status": "success",
        "sede": sede,
        "periodo_dias": dias,
        "fecha_inicio": fecha_inicio.isoformat(),
        "fecha_fin": fecha_fin.isoformat(),
        "fechas_con_datos": fechas_con_datos,
        "total_ingresos_periodo": total_ingresos_periodo,
        "total_ingresos_fuera_horario": total_fuera,
        "horas": horas,
        "totales": totales,
        "unidad": "Ingresos acumulados en molinetes (entradas en torniquetes)",
        "fuente_datos": "Registros consolidados de molinetes",
        "criterio_horario": "Horario habitual de atención (08:00 a 20:00)",
        "registros_fuera_horario": {
            "total": total_fuera,
            "criterio_aplicado": (
                "Excluidos del gráfico operativo por registrarse fuera del horario habitual "
                "de atención de la biblioteca (08:00 a 20:00)."
            ),
            "detalle": detalle_fuera
        },
        "referencia_horaria": "Hora almacenada en la base de datos (horario habitual de 08:00 a 20:00)"
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