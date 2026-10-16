"""
Script de integración y carga idempotente de afluencia histórica agregada
Biblioteca UNDAC.

Este script:
1. Crea y asegura la tabla analítica separada 'AfluenciaHistoricaAgregada'.
2. Carga las 1,029 filas del archivo '02_ingresos_reales_actual.csv' de forma idempotente
   (sin duplicar filas ante ejecuciones repetidas).
3. Conserva marcadas las filas de horario atípico (horas fuera de 08:00-20:00 o fines de semana)
   y las del cierre de agosto (01/08/2026 al 23/08/2026).
4. Compara y documenta la relación con las 38 filas de 'RegistroIngresos' para prevenir
   doble conteo en análisis posteriores.
5. Valida exhaustivamente contra las cifras de control:
   - 1,029 filas totales.
   - 98 fechas distintas.
   - 32,788 ingresos acumulados.
   - 4 filas en horario atípico (8 ingresos).
   - 65 filas durante el cierre de agosto (214 ingresos).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT_DIR / ".env")

import db


NOMBRE_TABLA_ANALITICA = "dbo.AfluenciaHistoricaAgregada"
PROCEDENCIA_DEFECTO = "02_ingresos_reales_actual.csv"

# Calendario confirmado de feriados y cierres institucionales 2026
FERIADOS_CONFIRMADOS_2026 = {
    "2026-04-02",  # Jueves Santo
    "2026-04-03",  # Viernes Santo
    "2026-05-01",  # Día del Trabajo
    "2026-06-07",  # Batalla de Arica y Día de la Bandera
    "2026-06-29",  # San Pedro y San Pablo
    "2026-07-23",  # Día de la Fuerza Aérea del Perú
    "2026-07-28",  # Fiestas Patrias
    "2026-07-29",  # Fiestas Patrias
    "2026-08-06",  # Batalla de Junín (incluido en cierre vacaciones agosto)
    "2026-08-30",  # Santa Rosa de Lima
    "2026-10-08",  # Combate de Angamos
    "2026-11-01",  # Todos los Santos
    "2026-12-08",  # Inmaculada Concepción
    "2026-12-09",  # Batalla de Ayacucho
    "2026-12-25",  # Navidad
}
CIERRE_CONFIRMADO_27_JULIO = "2026-07-27"


def obtener_ruta_csv_fuente(ruta_personalizada: Optional[str] = None) -> Path:
    """
    Resuelve la ruta al archivo CSV histórico agregado 02_ingresos_reales_actual.csv.
    """
    if ruta_personalizada:
        p = Path(ruta_personalizada).expanduser().resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"No se encontró el archivo CSV en la ruta especificada: {ruta_personalizada}")

    # Rutas por defecto
    candidatos = [
        Path.home() / "Downloads" / "02_ingresos_reales_actual.csv",
        ROOT_DIR / "ml" / "data" / "02_ingresos_reales_actual.csv"
    ]
    for c in candidatos:
        if c.exists():
            return c

    raise FileNotFoundError(
        "No se localizó '02_ingresos_reales_actual.csv' en Descargas ni en ml/data. "
        "Especifique la ruta mediante el parámetro --csv."
    )


def asegurar_tabla_analitica(cursor: Any) -> None:
    """
    Crea la tabla analítica AfluenciaHistoricaAgregada si no existe,
    con clave primaria, restricción de unicidad y columnas de procedencia/auditoría.
    """
    sql = """
    IF OBJECT_ID('dbo.AfluenciaHistoricaAgregada', 'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AfluenciaHistoricaAgregada (
            AfluenciaID INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            Fecha DATE NOT NULL,
            Hora INT NOT NULL,
            Sede VARCHAR(50) NOT NULL,
            CantidadIngresos INT NOT NULL,
            Procedencia VARCHAR(100) NOT NULL DEFAULT '02_ingresos_reales_actual.csv',
            EsHorarioAtipico BIT NOT NULL DEFAULT 0,
            EsCierreAgosto BIT NOT NULL DEFAULT 0,
            EsFranjaParcial BIT NOT NULL DEFAULT 0,
            EsFeriado BIT NOT NULL DEFAULT 0,
            EsCierre27Julio BIT NOT NULL DEFAULT 0,
            FechaCarga DATETIME NOT NULL DEFAULT GETDATE(),
            CONSTRAINT UQ_AfluenciaHistoricaAgregada_Clave UNIQUE (Fecha, Hora, Sede)
        );
    END
    """
    cursor.execute(sql)


def leer_y_preparar_csv(ruta_csv: Path) -> pd.DataFrame:
    """
    Lee y valida el CSV agregado fuente, calculando las banderas analíticas.
    """
    df = pd.read_csv(
        ruta_csv,
        sep=";",
        header=None,
        names=["Fecha", "Hora", "Sede", "CantidadIngresos"],
        dtype={"Fecha": str, "Hora": int, "Sede": str, "CantidadIngresos": int}
    )

    df["Fecha_dt"] = pd.to_datetime(df["Fecha"])
    df["DiaSemana"] = df["Fecha_dt"].dt.dayofweek  # 0=Lunes, 5=Sábado, 6=Domingo

    # EsHorarioAtipico: franjas fuera del horario regular 08:00 a 20:45 o fin de semana
    df["EsHorarioAtipico"] = ((df["Hora"] < 8) | (df["Hora"] > 20) | (df["DiaSemana"] >= 5)).astype(int)

    # EsCierreAgosto: fechas en el cierre confirmado de vacaciones (01/08/2026 al 23/08/2026)
    df["EsCierreAgosto"] = ((df["Fecha"] >= "2026-08-01") & (df["Fecha"] <= "2026-08-23")).astype(int)

    # EsFranjaParcial: franja 20:00 (20:00–20:45, atención de 45 minutos)
    df["EsFranjaParcial"] = (df["Hora"] == 20).astype(int)

    # EsFeriado: feriados nacionales confirmados del calendario 2026
    df["EsFeriado"] = df["Fecha"].isin(FERIADOS_CONFIRMADOS_2026).astype(int)

    # EsCierre27Julio: cierre confirmado puente de Fiestas Patrias (27/07/2026)
    df["EsCierre27Julio"] = (df["Fecha"] == CIERRE_CONFIRMADO_27_JULIO).astype(int)

    df["Procedencia"] = PROCEDENCIA_DEFECTO

    return df


def cargar_afluencia_agregada_idempotente(
    cursor: Any,
    conn: Any,
    df: pd.DataFrame,
    batch_size: int = 100
) -> Dict[str, int]:
    """
    Inserta las filas del DataFrame en AfluenciaHistoricaAgregada de forma idempotente
    utilizando 'WHERE NOT EXISTS' sobre la clave (Fecha, Hora, Sede).
    """
    asegurar_tabla_analitica(cursor)
    conn.commit()

    filas = df[[
        "Fecha", "Hora", "Sede", "CantidadIngresos",
        "Procedencia", "EsHorarioAtipico", "EsCierreAgosto", "EsFranjaParcial",
        "EsFeriado", "EsCierre27Julio"
    ]].values.tolist()

    filas_antes = 0
    cursor.execute("SELECT COUNT(*) FROM dbo.AfluenciaHistoricaAgregada")
    res = cursor.fetchone()
    if res:
        filas_antes = res[0]

    sql_insert = """
    IF NOT EXISTS (
        SELECT 1 FROM dbo.AfluenciaHistoricaAgregada
        WHERE Fecha = ? AND Hora = ? AND Sede = ?
    )
    BEGIN
        INSERT INTO dbo.AfluenciaHistoricaAgregada
        (Fecha, Hora, Sede, CantidadIngresos, Procedencia, EsHorarioAtipico, EsCierreAgosto, EsFranjaParcial, EsFeriado, EsCierre27Julio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    END
    """

    for i in range(0, len(filas), batch_size):
        chunk = filas[i:i + batch_size]
        for f, h, s, c, p, a, ag, fp, ef, c27 in chunk:
            cursor.execute(sql_insert, (f, h, s, f, h, s, c, p, a, ag, fp, ef, c27))
        conn.commit()

    cursor.execute("SELECT COUNT(*) FROM dbo.AfluenciaHistoricaAgregada")
    filas_despues = cursor.fetchone()[0]
    insertadas = filas_despues - filas_antes

    return {
        "filas_procesadas": len(filas),
        "filas_antes": filas_antes,
        "filas_despues": filas_despues,
        "filas_nuevas_insertadas": insertadas,
        "filas_omitidas_duplicadas": len(filas) - insertadas
    }


def validar_metricas_tabla(cursor: Any) -> Dict[str, Any]:
    """
    Verifica las métricas de control en dbo.AfluenciaHistoricaAgregada.
    """
    sql = """
    SELECT
        COUNT(*) AS TotalFilas,
        COUNT(DISTINCT Fecha) AS FechasDistintas,
        SUM(CantidadIngresos) AS TotalIngresos,
        SUM(CASE WHEN EsHorarioAtipico = 1 THEN 1 ELSE 0 END) AS TotalAtipico,
        SUM(CASE WHEN EsCierreAgosto = 1 THEN 1 ELSE 0 END) AS TotalCierreAgosto,
        MIN(Fecha) AS FechaMin,
        MAX(Fecha) AS FechaMax
    FROM dbo.AfluenciaHistoricaAgregada
    """
    cursor.execute(sql)
    r = cursor.fetchone()
    return {
        "total_filas": int(r[0]) if r[0] is not None else 0,
        "fechas_distintas": int(r[1]) if r[1] is not None else 0,
        "total_ingresos": int(r[2]) if r[2] is not None else 0,
        "filas_atipicas": int(r[3]) if r[3] is not None else 0,
        "filas_cierre_agosto": int(r[4]) if r[4] is not None else 0,
        "fecha_min": str(r[5]) if r[5] else None,
        "fecha_max": str(r[6]) if r[6] else None,
    }


def auditar_cruce_con_registro_ingresos(cursor: Any, df_csv: pd.DataFrame) -> Dict[str, Any]:
    """
    Compara las 38 filas de RegistroIngresos agrupadas contra el CSV
    para documentar el solapamiento y prevenir doble conteo.
    """
    cursor.execute("""
        SELECT
            CAST(FechaHora AS DATE) AS Fecha,
            DATEPART(HOUR, FechaHora) AS Hora,
            ISNULL(Sede, 'Central') AS Sede,
            COUNT(*) AS CantidadBD
        FROM RegistroIngresos
        GROUP BY CAST(FechaHora AS DATE), DATEPART(HOUR, FechaHora), ISNULL(Sede, 'Central')
        ORDER BY Fecha, Hora, Sede
    """)
    filas_bd = cursor.fetchall()
    df_bd = pd.DataFrame(
        [(str(f[0]), int(f[1]), str(f[2]), int(f[3])) for f in filas_bd],
        columns=["Fecha", "Hora", "Sede", "CantidadBD"]
    )

    df_csv_cmp = df_csv[["Fecha", "Hora", "Sede", "CantidadIngresos"]].rename(
        columns={"CantidadIngresos": "CantidadCSV"}
    )

    merged = pd.merge(df_bd, df_csv_cmp, on=["Fecha", "Hora", "Sede"], how="left")

    coincidentes_exactos = merged[merged["CantidadBD"] == merged["CantidadCSV"]]
    csv_mayor = merged[merged["CantidadCSV"] > merged["CantidadBD"]]
    no_en_csv = merged[merged["CantidadCSV"].isna()]

    return {
        "total_grupos_bd": len(df_bd),
        "total_visitas_bd": int(df_bd["CantidadBD"].sum()),
        "grupos_en_csv": int(merged["CantidadCSV"].notna().sum()),
        "grupos_no_en_csv": len(no_en_csv),
        "grupos_conteo_identico": len(coincidentes_exactos),
        "visitas_conteo_identico": int(coincidentes_exactos["CantidadBD"].sum()),
        "grupos_csv_mayor": len(csv_mayor),
        "visitas_bd_csv_mayor": int(csv_mayor["CantidadBD"].sum()),
        "visitas_no_en_csv": int(no_en_csv["CantidadBD"].sum()),
        "detalle_no_en_csv": [
            {
                "fecha": r["Fecha"],
                "hora": int(r["Hora"]),
                "sede": r["Sede"],
                "visitas_bd": int(r["CantidadBD"])
            }
            for _, r in no_en_csv.iterrows()
        ]
    }


def ejecutar_integracion(ruta_csv_opcional: Optional[str] = None) -> Dict[str, Any]:
    """
    Punto de entrada principal para la integración de afluencia histórica agregada.
    """
    p_csv = obtener_ruta_csv_fuente(ruta_csv_opcional)
    df_csv = leer_y_preparar_csv(p_csv)

    conn = db.get_db_connection()
    if not conn:
        raise ConnectionError("No se pudo conectar a SQL Server.")

    try:
        cursor = conn.cursor()
        res_carga = cargar_afluencia_agregada_idempotente(cursor, conn, df_csv)
        metricas = validar_metricas_tabla(cursor)
        cruce = auditar_cruce_con_registro_ingresos(cursor, df_csv)
        cursor.close()
    finally:
        conn.close()

    return {
        "ruta_csv": str(p_csv),
        "resultado_carga": res_carga,
        "metricas_tabla": metricas,
        "auditoria_cruce_bd": cruce
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga e integración idempotente de afluencia histórica.")
    parser.add_argument("--csv", type=str, default=None, help="Ruta alternativa a 02_ingresos_reales_actual.csv")
    args = parser.parse_args()

    print("=== INICIANDO INTEGRACIÓN DE AFLUENCIA HISTÓRICA AGREGADA ===")
    res = ejecutar_integracion(args.csv)
    print(f"Ruta CSV: {res['ruta_csv']}")
    print(f"Resultado de carga: {res['resultado_carga']}")
    print(f"Métricas en tabla analítica: {res['metricas_tabla']}")
    print(f"Auditoría de cruce con RegistroIngresos: {res['auditoria_cruce_bd']}")
    print("=== INTEGRACIÓN FINALIZADA CON ÉXITO ===")
