"""
Script de auditoría y extracción de datos reales de afluencia (Solo Lectura)
Biblioteca UNDAC.

Este script realiza análisis exploratorio y extracción de datos reales
exclusivamente mediante consultas SELECT sobre la base de datos académica configurada.
- No inserta, actualiza ni borra datos (modo estricto de solo lectura).
- No expone credenciales, nombres, DNI, códigos de matrícula ni identificadores personales.
- Consolida y deduplica registros entre la tabla actual (RegistroIngresos)
  y la histórica (RegistroIngresos_Historico).
- Analiza cobertura temporal, sedes, días de semana, horas y carrera/escuela.
- Prepara el dataset agregado para Machine Learning agrupado por Fecha, Hora y Sede.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

# Cargar variables de entorno desde .env en la raíz del proyecto
load_dotenv(dotenv_path=ROOT_DIR / ".env")


HORAS_OPERATIVAS = list(range(8, 21))  # 08:00 a 20:59
DIAS_SEMANA_ESP = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo"
]
MESES_ESP = [
    "",
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

PALABRAS_PROHIBIDAS_ESCRITURA = [
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bDELETE\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bTRUNCATE\b",
    r"\bCREATE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bMERGE\b"
]


class AuditoriaSeguridadError(Exception):
    """Excepción al detectar intentos de escritura o entorno no verificado."""
    pass


def validar_entorno_academico(confirmar_cli: bool = False) -> Tuple[bool, str, Dict[str, str]]:
    """
    Verifica que la configuración apunte a la base de datos académica autorizada.

    Requisitos:
    1. Existencia de configuración de base de datos en entorno o valores por defecto.
    2. Confirmación explícita mediante flag CLI (--confirmar-academica)
       o variable de entorno (CONFIRMAR_BASE_ACADEMICA=1 o ENTORNO_ACADEMICO=1).
    3. No estar en un entorno explícitamente productivo sin confirmación.

    Retorna: (es_valido, mensaje_explicacion, info_conexion_segura)
    """
    database = os.getenv("DB_DATABASE", "BibliotecaUNDAC")
    server = os.getenv("DB_SERVER", r"RENZO\SQL2025")
    confirmacion_env = os.getenv("CONFIRMAR_BASE_ACADEMICA", "").lower() in ("1", "true", "si", "yes") or \
                       os.getenv("ENTORNO_ACADEMICO", "").lower() in ("1", "true", "si", "yes")

    confirmado = confirmar_cli or confirmacion_env

    info_segura = {
        "database": str(database).strip(),
        "server": str(server).strip()
    }

    if not database:
        return (
            False,
            "Falta la configuración de la base de datos (DB_DATABASE no está configurada). "
            "Asegúrese de definir DB_DATABASE en el archivo .env o en el entorno.",
            info_segura
        )

    if not confirmado:
        return (
            False,
            "Seguridad preventiva: No se detectó la confirmación explícita de uso de la base académica.\n"
            "Para autorizar la ejecución de solo lectura sobre la base académica, debe:\n"
            "  1. Ejecutar el script agregando el parámetro: --confirmar-academica\n"
            "  2. O definir la variable de entorno: $env:CONFIRMAR_BASE_ACADEMICA='1'\n"
            f"Base objetivo detectada: '{info_segura['database']}' en servidor '{info_segura['server']}'.",
            info_segura
        )

    return (
        True,
        f"Entorno académico validado correctamente. Base: '{info_segura['database']}', Servidor: '{info_segura['server']}'.",
        info_segura
    )


def validar_consulta_solo_lectura(sql: str) -> None:
    """
    Asegura que la sentencia SQL sea estrictamente de solo lectura (SELECT / WITH).
    Lanza AuditoriaSeguridadError si detecta cualquier intento de modificación.
    """
    if not sql or not isinstance(sql, str):
        raise AuditoriaSeguridadError("La consulta SQL no puede estar vacía.")

    sql_limpia = sql.strip().upper()

    # Debe iniciar con SELECT o WITH
    if not (sql_limpia.startswith("SELECT") or sql_limpia.startswith("WITH")):
        raise AuditoriaSeguridadError(
            f"Consulta no permitida. Solo se autorizan sentencias que inicien con SELECT o WITH. Sentencia: '{sql[:40]}...'"
        )

    # Verificar ausencia de palabras de escritura
    for patron in PALABRAS_PROHIBIDAS_ESCRITURA:
        if re.search(patron, sql_limpia):
            raise AuditoriaSeguridadError(
                f"Consulta rechazada por seguridad: contiene término de modificación no autorizado ({patron})."
            )


def ejecutar_select_seguro(cursor: Any, sql: str, params: Optional[Tuple[Any, ...]] = None) -> Tuple[List[str], List[Any]]:
    """
    Ejecuta una consulta SQL de solo lectura verificada y retorna (columnas, filas).
    """
    validar_consulta_solo_lectura(sql)
    if params:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)

    columnas = [d[0] for d in cursor.description] if cursor.description else []
    filas = cursor.fetchall() if cursor.description else []
    return columnas, filas


def _tabla_existe(cursor: Any, nombre_tabla: str) -> bool:
    """Verifica si una tabla existe en el catálogo de SQL Server sin lanzar excepciones."""
    sql = "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?"
    try:
        validar_consulta_solo_lectura(sql)
        cursor.execute(sql, (nombre_tabla,))
        fila = cursor.fetchone()
        return bool(fila and fila[0] > 0)
    except Exception:
        return False


def auditar_tablas_y_deduplicacion(cursor: Any) -> Dict[str, Any]:
    """
    Audita la cantidad de registros en las tablas de ingresos actuales e históricas,
    detecta solapamientos por RegistroID y determina el total neto consolidado.
    """
    res: Dict[str, Any] = {
        "registro_ingresos_disponible": False,
        "historico_disponible": False,
        "filas_registro_ingresos": 0,
        "filas_historico": 0,
        "duplicados_registro_id_cruzados": 0,
        "ingresos_netos_consolidados": 0
    }

    # 1. Tabla actual RegistroIngresos
    if _tabla_existe(cursor, "RegistroIngresos"):
        res["registro_ingresos_disponible"] = True
        _, filas = ejecutar_select_seguro(cursor, "SELECT COUNT(*) FROM RegistroIngresos")
        res["filas_registro_ingresos"] = int(filas[0][0]) if filas else 0

    # 2. Tabla histórica RegistroIngresos_Historico
    if _tabla_existe(cursor, "RegistroIngresos_Historico"):
        res["historico_disponible"] = True
        _, filas = ejecutar_select_seguro(cursor, "SELECT COUNT(*) FROM RegistroIngresos_Historico")
        res["filas_historico"] = int(filas[0][0]) if filas else 0

    # 3. Detección de solapamiento por RegistroID
    if res["registro_ingresos_disponible"] and res["historico_disponible"]:
        sql_duplicados = """
            SELECT COUNT(*)
            FROM RegistroIngresos R
            INNER JOIN RegistroIngresos_Historico H ON R.RegistroID = H.RegistroID
        """
        _, filas = ejecutar_select_seguro(cursor, sql_duplicados)
        res["duplicados_registro_id_cruzados"] = int(filas[0][0]) if filas else 0
        res["ingresos_netos_consolidados"] = (
            res["filas_registro_ingresos"] + res["filas_historico"] - res["duplicados_registro_id_cruzados"]
        )
    elif res["registro_ingresos_disponible"]:
        res["ingresos_netos_consolidados"] = res["filas_registro_ingresos"]
    elif res["historico_disponible"]:
        res["ingresos_netos_consolidados"] = res["filas_historico"]

    return res


def auditar_periodo_y_temporalidad(cursor: Any, historico_activo: bool = True) -> Dict[str, Any]:
    """
    Determina fechas inicial y final, y agrupaciones por mes, sede, día de semana y hora.
    """
    # Consulta unificada deduplicando por RegistroID
    if historico_activo and _tabla_existe(cursor, "RegistroIngresos_Historico"):
        cte_unificada = """
            WITH IngresosUnificados AS (
                SELECT
                    RegistroID,
                    FechaHora,
                    ISNULL(Sede, 'Central') AS Sede
                FROM RegistroIngresos
                UNION ALL
                SELECT
                    H.RegistroID,
                    H.FechaHora,
                    ISNULL(H.Sede, 'Central') AS Sede
                FROM RegistroIngresos_Historico H
                WHERE NOT EXISTS (
                    SELECT 1 FROM RegistroIngresos R WHERE R.RegistroID = H.RegistroID
                )
            )
        """
    else:
        cte_unificada = """
            WITH IngresosUnificados AS (
                SELECT
                    RegistroID,
                    FechaHora,
                    ISNULL(Sede, 'Central') AS Sede
                FROM RegistroIngresos
            )
        """

    # 1. Periodo global
    sql_periodo = f"""
        {cte_unificada}
        SELECT
            MIN(FechaHora) AS FechaInicio,
            MAX(FechaHora) AS FechaFin,
            COUNT(DISTINCT CAST(FechaHora AS DATE)) AS DiasConRegistros,
            COUNT(*) AS TotalIngresos
        FROM IngresosUnificados
        WHERE FechaHora IS NOT NULL
    """
    _, filas_p = ejecutar_select_seguro(cursor, sql_periodo)
    fecha_min = filas_p[0][0] if filas_p and len(filas_p[0]) > 0 and filas_p[0][0] else None
    fecha_max = filas_p[0][1] if filas_p and len(filas_p[0]) > 1 and filas_p[0][1] else None
    dias_unicos = int(filas_p[0][2]) if filas_p and len(filas_p[0]) > 2 and filas_p[0][2] is not None else 0
    total_periodo = int(filas_p[0][3]) if filas_p and len(filas_p[0]) > 3 and filas_p[0][3] is not None else 0

    # 2. Distribución por Mes
    sql_mes = f"""
        {cte_unificada}
        SELECT
            DATEPART(YEAR, FechaHora) AS Anio,
            DATEPART(MONTH, FechaHora) AS Mes,
            COUNT(*) AS Cantidad
        FROM IngresosUnificados
        WHERE FechaHora IS NOT NULL
        GROUP BY DATEPART(YEAR, FechaHora), DATEPART(MONTH, FechaHora)
        ORDER BY Anio ASC, Mes ASC
    """
    _, filas_mes = ejecutar_select_seguro(cursor, sql_mes)
    dist_mes = [
        {
            "anio": int(f[0]),
            "mes_numero": int(f[1]),
            "mes_nombre": MESES_ESP[int(f[1])] if 1 <= int(f[1]) <= 12 else str(f[1]),
            "cantidad": int(f[2])
        }
        for f in filas_mes
    ]

    # 3. Distribución por Sede
    sql_sede = f"""
        {cte_unificada}
        SELECT
            Sede,
            COUNT(*) AS Cantidad
        FROM IngresosUnificados
        GROUP BY Sede
        ORDER BY Cantidad DESC
    """
    _, filas_sede = ejecutar_select_seguro(cursor, sql_sede)
    dist_sede = {str(f[0]): int(f[1]) for f in filas_sede}

    # 4. Distribución por Día de Semana (SQL Server DATEPART(WEEKDAY): 1=Domingo o según @@DATEFIRST)
    sql_dia = f"""
        {cte_unificada}
        SELECT
            DATEPART(WEEKDAY, FechaHora) AS NumDia,
            DATENAME(WEEKDAY, FechaHora) AS NombreDia,
            COUNT(*) AS Cantidad
        FROM IngresosUnificados
        WHERE FechaHora IS NOT NULL
        GROUP BY DATEPART(WEEKDAY, FechaHora), DATENAME(WEEKDAY, FechaHora)
        ORDER BY NumDia ASC
    """
    _, filas_dia = ejecutar_select_seguro(cursor, sql_dia)
    dist_dia = [
        {
            "numero_dia_sql": int(f[0]),
            "nombre_dia": str(f[1]).capitalize(),
            "cantidad": int(f[2])
        }
        for f in filas_dia
    ]

    # 5. Distribución por Hora (0 a 23)
    sql_hora = f"""
        {cte_unificada}
        SELECT
            DATEPART(HOUR, FechaHora) AS Hora,
            COUNT(*) AS Cantidad
        FROM IngresosUnificados
        WHERE FechaHora IS NOT NULL
        GROUP BY DATEPART(HOUR, FechaHora)
        ORDER BY Hora ASC
    """
    _, filas_hora = ejecutar_select_seguro(cursor, sql_hora)
    dist_hora = {int(f[0]): int(f[1]) for f in filas_hora}

    return {
        "fecha_inicial": fecha_min.isoformat() if isinstance(fecha_min, (datetime, date)) else str(fecha_min),
        "fecha_final": fecha_max.isoformat() if isinstance(fecha_max, (datetime, date)) else str(fecha_max),
        "dias_con_registros": dias_unicos,
        "total_registros_validos": total_periodo,
        "distribucion_mes": dist_mes,
        "distribucion_sede": dist_sede,
        "distribucion_dia_semana": dist_dia,
        "distribucion_hora": dist_hora
    }


def auditar_cobertura_carrera_y_usuarios(cursor: Any) -> Dict[str, Any]:
    """
    Analiza la cobertura por tipo de usuario y por carrera/escuela profesional.
    Evalúa la presencia de datos en Alumnos y Egresados vs Docentes, Personal y Visitantes.
    """
    res: Dict[str, Any] = {
        "cobertura_tipo_usuario": {},
        "total_ingresos_analizados": 0,
        "ingresos_alumnos": 0,
        "ingresos_egresados": 0,
        "ingresos_con_escuela": 0,
        "ingresos_sin_escuela": 0,
        "porcentaje_cobertura_escuela_total": 0.0,
        "porcentaje_cobertura_escuela_alumnos": 0.0,
        "top_escuelas": [],
        "evaluacion_ml_carrera": ""
    }

    if not _tabla_existe(cursor, "RegistroIngresos"):
        return res

    # 1. Distribución por tipo de persona en RegistroIngresos
    sql_tipos = """
        SELECT
            COUNT(*) AS Total,
            SUM(CASE WHEN R.AlumnoID IS NOT NULL THEN 1 ELSE 0 END) AS Alumnos,
            SUM(CASE WHEN R.EgresadoID IS NOT NULL THEN 1 ELSE 0 END) AS Egresados,
            SUM(CASE WHEN R.DocenteID IS NOT NULL THEN 1 ELSE 0 END) AS Docentes,
            SUM(CASE WHEN R.PersonalID IS NOT NULL THEN 1 ELSE 0 END) AS Personal,
            SUM(CASE WHEN R.VisitanteID IS NOT NULL THEN 1 ELSE 0 END) AS Visitantes,
            SUM(CASE WHEN R.AlumnoID IS NULL AND R.EgresadoID IS NULL AND R.DocenteID IS NULL
                          AND R.PersonalID IS NULL AND R.VisitanteID IS NULL THEN 1 ELSE 0 END) AS SinPersona
        FROM RegistroIngresos R
    """
    _, filas_t = ejecutar_select_seguro(cursor, sql_tipos)
    if filas_t:
        total = int(filas_t[0][0])
        alumnos = int(filas_t[0][1])
        egresados = int(filas_t[0][2])
        docentes = int(filas_t[0][3])
        personal = int(filas_t[0][4])
        visitantes = int(filas_t[0][5])
        sin_persona = int(filas_t[0][6])

        res["total_ingresos_analizados"] = total
        res["ingresos_alumnos"] = alumnos
        res["ingresos_egresados"] = egresados
        res["cobertura_tipo_usuario"] = {
            "Alumno": alumnos,
            "Egresado": egresados,
            "Docente": docentes,
            "PersonalAdministrativo": personal,
            "Visitante": visitantes,
            "SinPersonaVinculada": sin_persona
        }

    # 2. Cobertura de Carrera/Escuela profesional
    # Para Alumnos: A.Escuela o Escuelas.NombreEscuela
    tiene_alumnos = _tabla_existe(cursor, "Alumnos")
    tiene_escuelas = _tabla_existe(cursor, "Escuelas")

    if tiene_alumnos:
        join_escuelas = "LEFT JOIN Escuelas E ON A.EscuelaID = E.EscuelaID" if tiene_escuelas else ""
        col_escuela = "COALESCE(NULLIF(E.NombreEscuela, ''), NULLIF(A.Escuela, ''), '')" if tiene_escuelas else "ISNULL(A.Escuela, '')"

        sql_escuelas_alumnos = f"""
            SELECT
                COUNT(*) AS TotalAlumnos,
                SUM(CASE WHEN {col_escuela} <> '' THEN 1 ELSE 0 END) AS ConEscuela
            FROM RegistroIngresos R
            INNER JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
            {join_escuelas}
        """
        _, filas_ea = ejecutar_select_seguro(cursor, sql_escuelas_alumnos)
        con_escuela_alumnos = int(filas_ea[0][1]) if filas_ea and filas_ea[0][1] is not None else 0

        # Top 10 Escuelas por afluencia agregada (sin exponer datos personales)
        sql_top_escuelas = f"""
            SELECT TOP 10
                {col_escuela} AS Escuela,
                COUNT(*) AS Cantidad
            FROM RegistroIngresos R
            INNER JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
            {join_escuelas}
            WHERE {col_escuela} <> ''
            GROUP BY {col_escuela}
            ORDER BY Cantidad DESC
        """
        _, filas_top = ejecutar_select_seguro(cursor, sql_top_escuelas)
        res["top_escuelas"] = [{"escuela": str(f[0]), "cantidad": int(f[1])} for f in filas_top]

        res["ingresos_con_escuela"] = con_escuela_alumnos
        res["ingresos_sin_escuela"] = res["total_ingresos_analizados"] - con_escuela_alumnos

        if res["total_ingresos_analizados"] > 0:
            res["porcentaje_cobertura_escuela_total"] = round(
                (con_escuela_alumnos / res["total_ingresos_analizados"]) * 100, 2
            )
        if res["ingresos_alumnos"] > 0:
            res["porcentaje_cobertura_escuela_alumnos"] = round(
                (con_escuela_alumnos / res["ingresos_alumnos"]) * 100, 2
            )

    # Justificación técnica respecto al modelo de afluencia
    justificacion = (
        "EVALUACIÓN DE CARRERA/ESCUELA PARA ML: "
        "Aunque existe cobertura de carrera en los registros de alumnos, la variable carrera/escuela "
        "NO debe incluirse en el dataset agregado horario de afluencia de biblioteca. "
        "Justificación técnica: "
        "1. La finalidad del modelo es predecir la afluencia total de la biblioteca por hora y sede "
        "(nivel de ocupación del recinto), el cual es la suma agregada de todas las carreras y tipos de usuarios. "
        "2. Al momento de la inferencia (consulta predictiva), el administrador consulta por una fecha/hora/sede "
        "futura, sin conocer a priori qué carrera asistirá. "
        "3. Segmentar por carrera fragmentaría la muestra horaria en cientos de micro-registros casi vacíos "
        "(1 a 3 alumnos), destruyendo la señal temporal del flujo general del recinto. "
        "4. La distribución por carrera es altamente valiosa para reportes descriptivos y preguntas del asistente "
        "(ej. '¿Qué carreras ingresaron más?'), pero no como dimensión de predicción horaria del modelo."
    )
    res["evaluacion_ml_carrera"] = justificacion

    return res


def auditar_datos_faltantes_y_anomalias(cursor: Any) -> Dict[str, Any]:
    """
    Detecta posibles datos faltantes, fechas nulas, sedes sin especificar
    e ingresos fuera del horario operativo oficial (8:00 a 20:59).
    """
    if not _tabla_existe(cursor, "RegistroIngresos"):
        return {}

    sql = """
        SELECT
            COUNT(*) AS Total,
            SUM(CASE WHEN FechaHora IS NULL THEN 1 ELSE 0 END) AS FechaHoraNula,
            SUM(CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 1 ELSE 0 END) AS SedeNula,
            SUM(CASE WHEN FechaHora IS NOT NULL AND DATEPART(HOUR, FechaHora) NOT BETWEEN 8 AND 20 THEN 1 ELSE 0 END) AS FueraHorarioOperativo,
            SUM(CASE WHEN FechaHora IS NOT NULL AND DATEPART(WEEKDAY, FechaHora) IN (1, 7) THEN 1 ELSE 0 END) AS FinDeSemana
        FROM RegistroIngresos
    """
    _, filas = ejecutar_select_seguro(cursor, sql)
    if not filas:
        return {}

    f = filas[0]
    total = int(f[0])
    fecha_nula = int(f[1] or 0)
    sede_nula = int(f[2] or 0)
    fuera_horario = int(f[3] or 0)
    fin_semana = int(f[4] or 0)

    return {
        "total_registros_evaluados": total,
        "fechahora_nula": fecha_nula,
        "sede_nula_o_vacia": sede_nula,
        "imputacion_sede_nula": "Se imputa por defecto a 'Central' según la regla de negocio del sistema",
        "ingresos_fuera_horario_8_a_20": fuera_horario,
        "ingresos_fin_de_semana": fin_semana
    }


def extraer_dataset_ml_agregado(
    cursor: Any,
    exportar_ruta: Optional[Path] = None,
    incluir_historico: bool = True
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Extrae el dataset agregado por (Fecha, Hora, Sede).
    Aplica deduplicación entre RegistroIngresos y RegistroIngresos_Historico.
    Rellena la cuadrícula horaria de 8 a 20 para los días operativos con 0 ingresos cuando corresponda.
    Garantiza que NO contenga nombres, DNI, matrículas ni identificadores personales.

    Retorna: (filas_dataset, resumen_extraccion)
    """
    # 1. Consulta agregada segura deduplicada
    if incluir_historico and _tabla_existe(cursor, "RegistroIngresos_Historico"):
        sql_agregado = """
            WITH IngresosUnificados AS (
                SELECT
                    RegistroID,
                    CAST(FechaHora AS DATE) AS Fecha,
                    DATEPART(HOUR, FechaHora) AS Hora,
                    ISNULL(Sede, 'Central') AS Sede
                FROM RegistroIngresos
                WHERE FechaHora IS NOT NULL
                UNION ALL
                SELECT
                    H.RegistroID,
                    CAST(H.FechaHora AS DATE) AS Fecha,
                    DATEPART(HOUR, H.FechaHora) AS Hora,
                    ISNULL(H.Sede, 'Central') AS Sede
                FROM RegistroIngresos_Historico H
                WHERE H.FechaHora IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM RegistroIngresos R WHERE R.RegistroID = H.RegistroID
                  )
            )
            SELECT
                Fecha,
                Hora,
                Sede,
                COUNT(*) AS CantidadIngresos
            FROM IngresosUnificados
            WHERE Hora BETWEEN 8 AND 20
            GROUP BY Fecha, Hora, Sede
            ORDER BY Fecha ASC, Hora ASC, Sede ASC
        """
    else:
        sql_agregado = """
            WITH IngresosUnificados AS (
                SELECT
                    RegistroID,
                    CAST(FechaHora AS DATE) AS Fecha,
                    DATEPART(HOUR, FechaHora) AS Hora,
                    ISNULL(Sede, 'Central') AS Sede
                FROM RegistroIngresos
                WHERE FechaHora IS NOT NULL
            )
            SELECT
                Fecha,
                Hora,
                Sede,
                COUNT(*) AS CantidadIngresos
            FROM IngresosUnificados
            WHERE Hora BETWEEN 8 AND 20
            GROUP BY Fecha, Hora, Sede
            ORDER BY Fecha ASC, Hora ASC, Sede ASC
        """

    _, filas_raw = ejecutar_select_seguro(cursor, sql_agregado)

    # Mapear conteos existentes en diccionario {(fecha, hora, sede): cantidad}
    conteos_existentes: Dict[Tuple[str, int, str], int] = {}
    fechas_unicas: set[str] = set()
    sedes_encontradas: set[str] = set()

    for f in filas_raw:
        fecha_str = f[0].isoformat() if isinstance(f[0], (datetime, date)) else str(f[0])
        hora_int = int(f[1])
        sede_str = str(f[2]).strip()
        cantidad = int(f[3])

        conteos_existentes[(fecha_str, hora_int, sede_str)] = cantidad
        fechas_unicas.add(fecha_str)
        sedes_encontradas.add(sede_str)

    if not sedes_encontradas:
        sedes_encontradas.add("Central")

    # Si hay fechas, construir cuadrícula regular para los días hábiles presentes (8 a 20)
    fechas_ordenadas = sorted(fechas_unicas)
    filas_finales: List[Dict[str, Any]] = []

    for f_str in fechas_ordenadas:
        f_obj = datetime.strptime(f_str, "%Y-%m-%d").date()
        dia_sem = f_obj.weekday()  # 0=Lunes, 4=Viernes
        es_lunes = 1 if dia_sem == 0 else 0
        es_viernes = 1 if dia_sem == 4 else 0
        mes = f_obj.month
        dia_mes = f_obj.day
        semana_anio = f_obj.isocalendar()[1]

        for s_nombre in sorted(sedes_encontradas):
            for h in HORAS_OPERATIVAS:
                cant = conteos_existentes.get((f_str, h, s_nombre), 0)

                # Definir turno
                if 8 <= h < 12:
                    turno = "Mañana"
                elif 12 <= h < 18:
                    turno = "Tarde"
                else:
                    turno = "Noche"

                filas_finales.append({
                    "Fecha": f_str,
                    "Hora": h,
                    "Sede": s_nombre,
                    "CantidadIngresos": cant,
                    "DiaSemana": dia_sem,
                    "Mes": mes,
                    "DiaMes": dia_mes,
                    "SemanaAnio": semana_anio,
                    "EsLunes": es_lunes,
                    "EsViernes": es_viernes,
                    "Turno": turno
                })

    resumen = {
        "fechas_distintas": len(fechas_unicas),
        "sedes_incluidas": list(sorted(sedes_encontradas)),
        "horas_cubiertas_por_dia": len(HORAS_OPERATIVAS),
        "total_ejemplos_ml": len(filas_finales),
        "total_ingresos_acumulados": sum(f["CantidadIngresos"] for f in filas_finales),
        "slots_con_ingresos_positivos": sum(1 for f in filas_finales if f["CantidadIngresos"] > 0),
        "slots_con_cero_ingresos": sum(1 for f in filas_finales if f["CantidadIngresos"] == 0)
    }

    # Exportar si se solicitó ruta
    if exportar_ruta and filas_finales:
        exportar_ruta = Path(exportar_ruta)
        exportar_ruta.parent.mkdir(parents=True, exist_ok=True)
        campos = list(filas_finales[0].keys())

        # Doble verificación contra datos personales en el archivo exportado
        prohibidos = {"nombre", "dni", "codigo", "matricula", "persona_id", "alumnoid", "docenteid"}
        for c in campos:
            if c.lower() in prohibidos:
                raise AuditoriaSeguridadError(f"Se detectó columna personal prohibida en exportación: '{c}'")

        with open(exportar_ruta, "w", newline="", encoding="utf-8-sig") as arch:
            writer = csv.DictWriter(arch, fieldnames=campos)
            writer.writeheader()
            writer.writerows(filas_finales)

        resumen["archivo_exportado"] = str(exportar_ruta.resolve())

    return filas_finales, resumen


def ejecutar_auditoria_completa(
    cursor: Any,
    exportar_dataset: bool = False,
    ruta_salida: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Ejecuta el pipeline completo de auditoría y extracción en modo solo lectura.
    """
    tablas = auditar_tablas_y_deduplicacion(cursor)
    temporalidad = auditar_periodo_y_temporalidad(cursor, historico_activo=tablas.get("historico_disponible", False))
    cobertura = auditar_cobertura_carrera_y_usuarios(cursor)
    anomalias = auditar_datos_faltantes_y_anomalias(cursor)
    _, resumen_ml = extraer_dataset_ml_agregado(
        cursor,
        exportar_ruta=ruta_salida if exportar_dataset else None,
        incluir_historico=tablas.get("historico_disponible", False)
    )

    reporte = {
        "timestamp": datetime.now().isoformat(),
        "modo": "SOLO_LECTURA_SELECT",
        "tablas_y_deduplicacion": tablas,
        "temporalidad": temporalidad,
        "cobertura_carrera_y_usuarios": cobertura,
        "anomalias_y_faltantes": anomalias,
        "resumen_dataset_ml": resumen_ml
    }
    return reporte


def imprimir_reporte_consola(reporte: Dict[str, Any], info_bd: Dict[str, str]) -> None:
    """Imprime el resumen de auditoría de forma clara y estructurada en consola."""
    print("=" * 78)
    print("  AUDITORÍA Y PREPARACIÓN DE DATOS REALES - BIBLIOTECA UNDAC (SOLO LECTURA)")
    print("=" * 78)
    print(f"Base de datos consultada: {info_bd.get('database')} | Servidor: {info_bd.get('server')}")
    print("Modo de seguridad: ESTRICTO SOLO LECTURA (únicamente SELECT; sin escrituras).")
    print("-" * 78)

    t = reporte["tablas_y_deduplicacion"]
    print("\n1. VOLUMEN DE REGISTROS Y DEDUPLICACIÓN DE FUENTES")
    print(f"  • Registros en 'RegistroIngresos' (actual):     {t.get('filas_registro_ingresos', 0):,}")
    print(f"  • Registros en 'RegistroIngresos_Historico':     {t.get('filas_historico', 0):,}")
    print(f"  • Duplicados por RegistroID entre ambas tablas:  {t.get('duplicados_registro_id_cruzados', 0):,}")
    print(f"  • Total neto consolidado sin doble conteo:       {t.get('ingresos_netos_consolidados', 0):,}")

    temp = reporte["temporalidad"]
    print("\n2. PERIODO TEMPORAL Y COBERTURA DE FECHAS")
    print(f"  • Fecha inicial registrada:  {temp.get('fecha_inicial')}")
    print(f"  • Fecha final registrada:    {temp.get('fecha_final')}")
    print(f"  • Días con registros:        {temp.get('dias_con_registros')} días")
    print(f"  • Total de ingresos válidos: {temp.get('total_registros_validos', 0):,}")

    print("\n3. DISTRIBUCIÓN POR SEDE")
    for s_nom, cant in temp.get("distribucion_sede", {}).items():
        print(f"  • Sede {s_nom:<15}: {cant:>8,} ingresos")

    print("\n4. DISTRIBUCIÓN POR MES")
    for m in temp.get("distribucion_mes", []):
        print(f"  • {m['mes_nombre']} {m['anio']}: {m['cantidad']:>8,} ingresos")

    print("\n5. DISTRIBUCIÓN POR DÍA DE SEMANA")
    for d in temp.get("distribucion_dia_semana", []):
        print(f"  • {d['nombre_dia']:<12}: {d['cantidad']:>8,} ingresos")

    print("\n6. DISTRIBUCIÓN POR HORA (08:00 a 20:00)")
    dist_h = temp.get("distribucion_hora", {})
    for h in HORAS_OPERATIVAS:
        c_h = dist_h.get(h, 0)
        barra = "■" * min(40, max(0, c_h // 100))
        print(f"  • {h:02d}:00 - {h+1:02d}:00: {c_h:>6,} ingresos {barra}")

    cob = reporte["cobertura_carrera_y_usuarios"]
    print("\n7. COBERTURA POR TIPO DE PERSONA Y CARRERA / ESCUELA")
    for tp, cant in cob.get("cobertura_tipo_usuario", {}).items():
        print(f"  • Tipo {tp:<24}: {cant:>8,} ingresos")
    print(f"  • Ingresos de alumnos con carrera identificada: {cob.get('ingresos_con_escuela', 0):,} ({cob.get('porcentaje_cobertura_escuela_alumnos', 0)}% de alumnos)")
    print(f"  • Cobertura sobre el total general:             {cob.get('porcentaje_cobertura_escuela_total', 0)}%")

    top_esc = cob.get("top_escuelas", [])
    if top_esc:
        print("\n  Top carreras/escuelas con mayor asistencia (datos agregados):")
        for i, esc in enumerate(top_esc, 1):
            print(f"    {i:2d}. {esc['escuela']:<45}: {esc['cantidad']:>6,} ingresos")

    anom = reporte["anomalias_y_faltantes"]
    print("\n8. CALIDAD DE DATOS, FALTANTES Y ANOMALÍAS")
    print(f"  • Fechas nulas:                  {anom.get('fechahora_nula', 0)}")
    print(f"  • Sedes vacías o nulas:          {anom.get('sede_nula_o_vacia', 0)} ({anom.get('imputacion_sede_nula')})")
    print(f"  • Ingresos fuera de 08:00-20:00: {anom.get('ingresos_fuera_horario_8_a_20', 0)}")
    print(f"  • Ingresos en fin de semana:     {anom.get('ingresos_fin_de_semana', 0)}")

    ml = reporte["resumen_dataset_ml"]
    print("\n9. DATASET AGREGADO PARA MACHINE LEARNING")
    print(f"  • Cuadrícula horaria completa:   8:00 a 20:00 (13 slots diarios)")
    print(f"  • Total de ejemplos resultantes: {ml.get('total_ejemplos_ml', 0):,} filas agregadas por (Fecha, Hora, Sede)")
    print(f"  • Slots con ingresos positivos:  {ml.get('slots_con_ingresos_positivos', 0):,}")
    print(f"  • Slots con 0 ingresos:          {ml.get('slots_con_cero_ingresos', 0):,}")
    if "archivo_exportado" in ml:
        print(f"  • Archivo exportado (ignorado por Git): {ml['archivo_exportado']}")

    print("\n10. JUSTIFICACIÓN TÉCNICA DE VARIABLES PARA PREDECIR AFLUENCIA")
    print(f"  {cob.get('evaluacion_ml_carrera')}")
    print("=" * 78)


def obtener_get_db_cursor() -> Any:
    """
    Importa get_db_cursor desde db.py asegurando que la raíz del proyecto
    esté en sys.path. Distingue errores de ruta de importación de problemas
    de dependencias o ejecución interna, sin mostrar credenciales.
    """
    root_dir = Path(__file__).resolve().parents[2]
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    ruta_db = root_dir / "db.py"
    if not ruta_db.is_file():
        raise ImportError(
            f"No se encontró el archivo db.py en la raíz esperada del proyecto: '{root_dir}'. "
            "Asegúrese de ejecutar el script dentro de la estructura de Biblioteca UNDAC."
        )

    try:
        from db import get_db_cursor
        return get_db_cursor
    except ModuleNotFoundError as e:
        if e.name == "db":
            raise ImportError(
                f"No se pudo resolver el módulo 'db' en sys.path (raíz verificada: '{root_dir}'). "
                "Verifique que la raíz del proyecto contenga db.py."
            ) from e
        raise ImportError(
            f"El módulo db.py requiere la dependencia '{e.name}', la cual no está disponible en el entorno virtual. "
            "Verifique que el entorno virtual esté activo."
        ) from e
    except Exception as e:
        raise ImportError(
            f"Error al inicializar el módulo db.py ({type(e).__name__}). "
            "Verifique la sintaxis y configuración general en .env."
        ) from e


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auditoría y extracción de datos reales de afluencia (Solo Lectura) - Biblioteca UNDAC"
    )
    parser.add_argument(
        "--confirmar-academica",
        action="store_true",
        help="Confirmación explícita de que se está consultando la base de datos académica autorizada."
    )
    parser.add_argument(
        "--exportar",
        action="store_true",
        help="Exporta el dataset agregado de afluencia para ML a una ruta local ignorada por Git."
    )
    parser.add_argument(
        "--ruta-salida",
        type=str,
        default="ml/data/dataset_afluencia_real_agregado.csv",
        help="Ruta de destino para el dataset exportado (por defecto ml/data/dataset_afluencia_real_agregado.csv)."
    )

    args = parser.parse_args()

    # 1. Validación de seguridad de entorno académico
    valido, mensaje_val, info_bd = validar_entorno_academico(confirmar_cli=args.confirmar_academica)
    if not valido:
        print(f"\n[ALERTA DE SEGURIDAD] {mensaje_val}\n", file=sys.stderr)
        sys.exit(1)

    # 2. Conexión segura a SQL Server usando get_db_cursor
    try:
        get_db_cursor = obtener_get_db_cursor()
    except ImportError as e_imp:
        print(f"\n[ERROR DE IMPORTACIÓN / RUTA] {e_imp}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e_gen:
        print(f"\n[ERROR INESPERADO AL CARGAR BD] No se pudo inicializar la conexión ({type(e_gen).__name__}).\n", file=sys.stderr)
        sys.exit(1)

    ruta_csv = Path(args.ruta_salida) if args.exportar else None

    try:
        with get_db_cursor(autocommit=True) as (_, cursor):
            if cursor is None:
                print(
                    "\n[ERROR DE CONEXIÓN] No fue posible conectar con SQL Server.\n"
                    "Verifique que el servicio SQL Server esté en ejecución y los parámetros en .env sean correctos.",
                    file=sys.stderr
                )
                sys.exit(1)

            reporte = ejecutar_auditoria_completa(
                cursor=cursor,
                exportar_dataset=args.exportar,
                ruta_salida=ruta_csv
            )

        imprimir_reporte_consola(reporte, info_bd)

    except Exception as e:
        print(f"\n[ERROR EN AUDITORÍA] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
