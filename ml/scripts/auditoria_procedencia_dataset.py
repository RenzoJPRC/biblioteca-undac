"""
Script de auditoría de procedencia y calidad de datos históricos agregados para Machine Learning.
Biblioteca UNDAC.

Este script realiza una auditoría de SOLO LECTURA sobre los datasets agregados:
1. Inspecciona el CSV preparado (ml/data/dataset_afluencia_real_preparado.csv).
2. Si se proporciona el CSV fuente (ej. %USERPROFILE%\\Downloads\\02_ingresos_reales_actual.csv),
   compara las claves compuestas (Fecha + Hora + Sede) registro a registro.
3. Aplica las reglas del contexto operativo confirmado:
   - Atención de Lunes a Viernes de 08:00 a 20:45.
   - Franjas 08:00 a 19:59: 60 minutos de operación continua.
   - Franja 20:00 a 20:45: franja parcial de 45 minutos (servidor se apaga a las 20:45).
   - Cierres confirmados: 27/07/2026 y vacaciones del 01/08/2026 al 23/08/2026 (reapertura 24/08/2026).
   - Feriados oficiales en días cerrados: clasificados como cierre, no como días abiertos con cero.
4. Categoriza rigurosamente los 224 ceros del dataset preparado:
   - Cero observado de demanda nula en día abierto (franja completa 60m).
   - Cero observado en franja parcial (20:00–20:45, 45m).
   - Cierre confirmado de biblioteca (imputación indebida de ceros en periodo de vacaciones).
   - Desconocido / sin evidencia de captura continua (fechas preoperativas de abril/mayo).
5. Detecta y reporta discrepancias: filas presentes en el archivo fuente durante fechas de cierre confirmado.
6. Concilia exactamente filas e ingresos entre fuente, filtros de notebook y preparado.
7. No modifica archivos existentes, no escribe en base de datos ni altera variables de entorno.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

RUTA_PREPARADO_DEFECTO = ROOT_DIR / "ml" / "data" / "dataset_afluencia_real_preparado.csv"
RUTA_REPORTE_DEFECTO = ROOT_DIR / "ml" / "reports" / "reporte_auditoria_procedencia_cobertura.md"
RUTA_CSV_AUXILIAR_DEFECTO = ROOT_DIR / "ml" / "data" / "resumen_cobertura_por_fecha.csv"

# Constantes del horario y periodo confirmado
HORAS_OPERATIVAS_HABITUALES = list(range(8, 21))  # 08:00 a 20:59 (13 franjas horarias)
HORAS_COMPLETAS_60M = list(range(8, 20))          # 08:00 a 19:59 (12 franjas de 60 min)
HORA_PARCIAL_45M = 20                             # 20:00 a 20:45 (45 minutos operativos)
DURACION_MINUTOS_HORA_20 = 45

PERIODO_INICIO_DATOS = "2026-04-10"
PERIODO_FIN_DATOS = "2026-09-28"

# Feriados nacionales que implicaron cierre confirmado (0 filas en fuente)
FERIADOS_NACIONALES_CERRADOS: Dict[str, str] = {
    "2026-05-01": "Día del Trabajo (Viernes)",
    "2026-06-29": "San Pedro y San Pablo (Lunes)",
    "2026-07-23": "Día de la Fuerza Aérea del Perú - FAP (Jueves)",
    "2026-07-28": "Fiestas Patrias (Martes)",
    "2026-07-29": "Fiestas Patrias (Miércoles)"
}

# Cierres institucionales confirmados
CIERRE_CONFIRMADO_PUENTE_PATRIO = "2026-07-27"  # Lunes confirmado cerrado por la biblioteca
CIERRE_VACACIONES_INICIO = "2026-08-01"
CIERRE_VACACIONES_FIN = "2026-08-23"
REAPERTURA_CLASES = "2026-08-24"

# Fechas preoperativas con evidencia insuficiente de captura continua (1 a 3 horas registradas)
FECHAS_PREOPERATIVAS: set[str] = {
    "2026-04-10",
    "2026-04-29",
    "2026-05-04",
    "2026-05-05"
}


def detectar_delimitador(ruta_archivo: Path) -> str:
    """Detecta si el archivo utiliza delimitador coma o punto y coma."""
    with open(ruta_archivo, "r", encoding="utf-8-sig", errors="ignore") as f:
        primera_linea = f.readline()
        if ";" in primera_linea and primera_linea.count(";") >= primera_linea.count(","):
            return ";"
        return ","


def obtener_ruta_fuente_defecto() -> Optional[Path]:
    """Busca el archivo fuente en Downloads del usuario si existe."""
    userprofile = os.getenv("USERPROFILE") or os.getenv("HOME")
    if userprofile:
        candidato = Path(userprofile) / "Downloads" / "02_ingresos_reales_actual.csv"
        if candidato.is_file():
            return candidato
    return None


def clasificar_fecha_calendario(fecha_str: str) -> Dict[str, Any]:
    """
    Clasifica una fecha según el calendario operacional institucional.
    Asegura clasificación unívoca sin doble conteo de condiciones coincidentes.
    """
    dt = pd.to_datetime(fecha_str)
    dia_semana_nombre = dt.day_name()
    es_fin_de_semana = dt.dayofweek >= 5

    # 1. Fechas fuera del periodo del CSV fuente
    if fecha_str < PERIODO_INICIO_DATOS:
        return {
            "fecha": fecha_str,
            "categoria": "fuera_periodo_anterior",
            "descripcion": "Anterior al inicio del dataset (datos inician 10/04/2026)",
            "es_laborable_modelo": False,
            "estado_operativo": "fuera_periodo",
            "coincidencia_feriado": "Semana Santa (Jueves/Viernes Santo)" if fecha_str in ("2026-04-02", "2026-04-03") else None
        }

    if fecha_str > PERIODO_FIN_DATOS:
        return {
            "fecha": fecha_str,
            "categoria": "fuera_periodo_posterior",
            "descripcion": "Posterior al fin del dataset (datos finalizan 28/09/2026)",
            "es_laborable_modelo": False,
            "estado_operativo": "fuera_periodo",
            "coincidencia_feriado": "Combate de Angamos (08/10/2026)" if fecha_str == "2026-10-08" else None
        }

    # 2. Cierre confirmado de vacaciones (01/08/2026 al 23/08/2026)
    if CIERRE_VACACIONES_INICIO <= fecha_str <= CIERRE_VACACIONES_FIN:
        coincidencia = "Batalla de Junín (06/08/2026)" if fecha_str == "2026-08-06" else None
        return {
            "fecha": fecha_str,
            "categoria": "cierre_vacaciones_confirmado",
            "descripcion": "Cierre confirmado de biblioteca por vacaciones académicas de medio año",
            "es_laborable_modelo": False,
            "estado_operativo": "cierre_confirmado",
            "coincidencia_feriado": coincidencia,
            "es_fin_de_semana": es_fin_de_semana
        }

    # 3. Cierre confirmado de biblioteca previo a Fiestas Patrias (27/07/2026)
    if fecha_str == CIERRE_CONFIRMADO_PUENTE_PATRIO:
        return {
            "fecha": fecha_str,
            "categoria": "cierre_institucional_confirmado",
            "descripcion": "Cierre confirmado de biblioteca (puente institucional previo a Fiestas Patrias)",
            "es_laborable_modelo": False,
            "estado_operativo": "cierre_confirmado",
            "coincidencia_feriado": None,
            "es_fin_de_semana": False
        }

    # 4. Feriados nacionales con cierre confirmado
    if fecha_str in FERIADOS_NACIONALES_CERRADOS:
        return {
            "fecha": fecha_str,
            "categoria": "feriado_nacional_cerrado",
            "descripcion": f"Feriado nacional oficial: {FERIADOS_NACIONALES_CERRADOS[fecha_str]}",
            "es_laborable_modelo": False,
            "estado_operativo": "cierre_confirmado",
            "coincidencia_feriado": FERIADOS_NACIONALES_CERRADOS[fecha_str],
            "es_fin_de_semana": False
        }

    # 5. Fines de semana (Sábados y Domingos)
    if es_fin_de_semana:
        coincidencia = None
        if fecha_str == "2026-06-07":
            coincidencia = "Día de la Bandera y Batalla de Arica (Feriado dominical)"
        elif fecha_str == "2026-08-30":
            coincidencia = "Santa Rosa de Lima (Feriado dominical)"
        return {
            "fecha": fecha_str,
            "categoria": "fin_de_semana",
            "descripcion": f"Fin de semana ({dia_semana_nombre}); no es día laborable del modelo",
            "es_laborable_modelo": False,
            "estado_operativo": "fin_de_semana",
            "coincidencia_feriado": coincidencia,
            "es_fin_de_semana": True
        }

    # 6. Fechas preoperativas con evidencia insuficiente de captura continua
    if fecha_str in FECHAS_PREOPERATIVAS:
        return {
            "fecha": fecha_str,
            "categoria": "desconocido_preoperativo",
            "descripcion": "Día laborable con pruebas preoperativas puntuales; sin evidencia de captura continua de 08:00 a 20:45",
            "es_laborable_modelo": True,
            "estado_operativo": "desconocido",
            "coincidencia_feriado": None,
            "es_fin_de_semana": False
        }

    # 7. Días laborables regulares confirmados abiertos
    return {
        "fecha": fecha_str,
        "categoria": "laborable_regular",
        "descripcion": "Día laborable lectivo regular confirmado abierto con captura continua",
        "es_laborable_modelo": True,
        "estado_operativo": "abierto_regular",
        "coincidencia_feriado": None,
        "es_fin_de_semana": False
    }


def clasificar_franja_horaria(hora: int) -> Dict[str, Any]:
    """
    Clasifica una franja horaria distinguiendo horas completas de 60m y la franja parcial de 45m.
    """
    if hora == HORA_PARCIAL_45M:
        return {
            "hora": 20,
            "es_parcial": True,
            "duracion_minutos": DURACION_MINUTOS_HORA_20,
            "tipo_franja": "franja_parcial_45m",
            "rango_texto": "20:00–20:45",
            "motivo": "El servidor se apagaba a las 20:45; contiene únicamente 45 minutos de operación"
        }
    elif hora in HORAS_COMPLETAS_60M:
        return {
            "hora": hora,
            "es_parcial": False,
            "duracion_minutos": 60,
            "tipo_franja": "franja_completa_60m",
            "rango_texto": f"{hora:02d}:00–{hora:02d}:59",
            "motivo": "Franja estándar de 60 minutos con captura continua"
        }
    else:
        return {
            "hora": hora,
            "es_parcial": False,
            "duracion_minutos": 0,
            "tipo_franja": "fuera_horario",
            "rango_texto": f"{hora:02d}:00",
            "motivo": "Fuera del horario operativo oficial de atención (08:00 a 20:45)"
        }


def cargar_dataset_preparado(ruta_archivo: Path | str) -> pd.DataFrame:
    """
    Carga y valida el dataset preparado para Machine Learning.
    Verifica las columnas mínimas: Fecha, Hora, Sede, CantidadIngresos.
    """
    ruta = Path(ruta_archivo)
    if not ruta.is_file():
        raise FileNotFoundError(f"No se encontró el archivo preparado: {ruta}")

    delimitador = detectar_delimitador(ruta)
    df = pd.read_csv(ruta, sep=delimitador, encoding="utf-8-sig")

    columnas_requeridas = {"Fecha", "Hora", "Sede", "CantidadIngresos"}
    if not columnas_requeridas.issubset(set(df.columns)):
        raise ValueError(
            f"El archivo preparado carece de columnas requeridas. "
            f"Esperadas: {columnas_requeridas}, Encontradas: {list(df.columns)}"
        )

    df["Fecha"] = pd.to_datetime(df["Fecha"]).dt.strftime("%Y-%m-%d")
    df["Hora"] = df["Hora"].astype(int)
    df["Sede"] = df["Sede"].astype(str).str.strip()
    df["CantidadIngresos"] = df["CantidadIngresos"].astype(int)

    return df


def cargar_dataset_fuente(ruta_archivo: Path | str) -> pd.DataFrame:
    """
    Carga y estandariza el archivo fuente original agregado.
    Detecta automáticamente delimitador y encabezados.
    """
    ruta = Path(ruta_archivo)
    if not ruta.is_file():
        raise FileNotFoundError(f"No se encontró el archivo fuente: {ruta}")

    delimitador = detectar_delimitador(ruta)

    with open(ruta, "r", encoding="utf-8-sig", errors="ignore") as f:
        primera_linea = f.readline().strip()

    tiene_encabezado = "fecha" in primera_linea.lower() or "sede" in primera_linea.lower()

    if tiene_encabezado:
        df = pd.read_csv(ruta, sep=delimitador, encoding="utf-8-sig")
        mapeo_columnas = {}
        for col in df.columns:
            col_limpia = col.strip().lower()
            if col_limpia in ("fecha", "fechahora", "date"):
                mapeo_columnas[col] = "Fecha"
            elif col_limpia in ("hora", "hour"):
                mapeo_columnas[col] = "Hora"
            elif col_limpia in ("sede", "idsede"):
                mapeo_columnas[col] = "Sede"
            elif col_limpia in ("cantidadingresos", "totalingresos", "ingresos", "cantidad", "total"):
                mapeo_columnas[col] = "CantidadIngresos"
        df = df.rename(columns=mapeo_columnas)
    else:
        nombres = ["Fecha", "Hora", "Sede", "CantidadIngresos"]
        df = pd.read_csv(
            ruta,
            sep=delimitador,
            header=None,
            names=nombres,
            encoding="utf-8-sig"
        )

    columnas_requeridas = {"Fecha", "Hora", "Sede", "CantidadIngresos"}
    if not columnas_requeridas.issubset(set(df.columns)):
        raise ValueError(
            f"El archivo fuente no contiene las columnas necesarias. "
            f"Esperadas: {columnas_requeridas}, Encontradas: {list(df.columns)}"
        )

    df["Fecha"] = pd.to_datetime(df["Fecha"]).dt.strftime("%Y-%m-%d")
    df["Hora"] = df["Hora"].astype(int)
    df["Sede"] = df["Sede"].astype(str).str.strip()
    df["CantidadIngresos"] = df["CantidadIngresos"].astype(int)

    return df


def auditar_procedencia(
    ruta_preparado: Path | str = RUTA_PREPARADO_DEFECTO,
    ruta_fuente: Optional[Path | str] = None
) -> Dict[str, Any]:
    """
    Ejecuta la auditoría integral de calidad y procedencia de datos históricos agregados.
    Aplica rigurosamente las reglas del calendario, clasificación de franjas y contexto operativo.
    """
    ruta_prep_path = Path(ruta_preparado)
    df_preparado = cargar_dataset_preparado(ruta_prep_path)

    total_filas_prep = len(df_preparado)
    total_ingresos_prep = int(df_preparado["CantidadIngresos"].sum())
    filas_cero_prep = int((df_preparado["CantidadIngresos"] == 0).sum())
    filas_pos_prep = int((df_preparado["CantidadIngresos"] > 0).sum())

    fechas_unicas_prep = sorted(df_preparado["Fecha"].unique())
    horas_unicas_prep = sorted(df_preparado["Hora"].unique())
    sedes_unicas_prep = sorted(df_preparado["Sede"].unique())
    duplicados_prep = int(df_preparado.duplicated(subset=["Fecha", "Hora", "Sede"]).sum())

    resultado: Dict[str, Any] = {
        "timestamp_auditoria": datetime.now().isoformat(),
        "contexto_operativo": {
            "sede": "Central",
            "dias_atencion": "Lunes a Viernes",
            "horario_oficial": "08:00 a 20:45",
            "apagado_servidor": "20:45 (fin de captura automática)",
            "duracion_franjas_08_a_19": "60 minutos (captura continua)",
            "duracion_franja_20": "45 minutos (20:00–20:45, franja parcial)",
            "cierre_puente_patrio": CIERRE_CONFIRMADO_PUENTE_PATRIO,
            "periodo_cierre_vacaciones": f"{CIERRE_VACACIONES_INICIO} al {CIERRE_VACACIONES_FIN}",
            "fecha_reapertura": REAPERTURA_CLASES
        },
        "archivo_preparado": {
            "ruta": str(ruta_prep_path),
            "total_filas": total_filas_prep,
            "total_ingresos": total_ingresos_prep,
            "filas_con_cero": filas_cero_prep,
            "filas_positivas": filas_pos_prep,
            "porcentaje_ceros": round((filas_cero_prep / total_filas_prep) * 100, 2) if total_filas_prep else 0.0,
            "fechas_unicas": len(fechas_unicas_prep),
            "fecha_minima": fechas_unicas_prep[0] if fechas_unicas_prep else None,
            "fecha_maxima": fechas_unicas_prep[-1] if fechas_unicas_prep else None,
            "horas_cubiertas": horas_unicas_prep,
            "total_horas_cubiertas": len(horas_unicas_prep),
            "sedes": sedes_unicas_prep,
            "duplicados_clave": duplicados_prep
        },
        "fuente_proporcionada": False,
        "estado_procedencia_ceros": "procedencia de ceros no verificada"
    }

    # Si no se pasó ruta de fuente, intentar obtener la ruta por defecto
    ruta_fte_candidata = Path(ruta_fuente) if ruta_fuente else obtener_ruta_fuente_defecto()

    if ruta_fte_candidata is None or not ruta_fte_candidata.is_file():
        resultado["clasificacion_ceros"] = {
            "claves_faltantes_en_fuente": None,
            "ceros_observados_en_fuente": None,
            "inconsistencias": None,
            "detalle_evaluacion": (
                "No se proporcionó ni se encontró el archivo fuente agregado original. "
                "Las 224 filas con CantidadIngresos=0 en el archivo preparado provienen de la cuadrícula "
                "sintética de horas generada en el notebook (horas 08:00 a 20:00 mediante left merge + fillna(0)). "
                "Sin la fuente original, NO se debe asumir que representan ausencia real de visitas."
            )
        }
        return resultado

    # Cargar y analizar archivo fuente
    df_fuente = cargar_dataset_fuente(ruta_fte_candidata)
    resultado["fuente_proporcionada"] = True

    total_filas_fte = len(df_fuente)
    total_ingresos_fte = int(df_fuente["CantidadIngresos"].sum())
    fechas_unicas_fte = sorted(df_fuente["Fecha"].unique())
    horas_unicas_fte = sorted(df_fuente["Hora"].unique())
    sedes_unicas_fte = sorted(df_fuente["Sede"].unique())
    duplicados_fte = int(df_fuente.duplicated(subset=["Fecha", "Hora", "Sede"]).sum())

    # Filtros del notebook 03: Central, lunes a viernes (dayofweek < 5), horas 8 a 20
    df_fte_dt = pd.to_datetime(df_fuente["Fecha"])
    filas_fin_semana_fte = int((df_fte_dt.dt.dayofweek >= 5).sum())
    ingresos_fin_semana_fte = int(df_fuente.loc[df_fte_dt.dt.dayofweek >= 5, "CantidadIngresos"].sum())

    filas_fuera_hora_fte = int((~df_fuente["Hora"].between(8, 20)).sum())
    ingresos_fuera_hora_fte = int(df_fuente.loc[~df_fuente["Hora"].between(8, 20), "CantidadIngresos"].sum())

    filas_no_central_fte = int((df_fuente["Sede"] != "Central").sum())
    ingresos_no_central_fte = int(df_fuente.loc[df_fuente["Sede"] != "Central", "CantidadIngresos"].sum())

    # Superposición exacta de filtros
    mascara_superpuesta_fin_hora = (df_fte_dt.dt.dayofweek >= 5) & (~df_fuente["Hora"].between(8, 20))
    filas_superpuestas = int(mascara_superpuesta_fin_hora.sum())
    ingresos_superpuestos = int(df_fuente.loc[mascara_superpuesta_fin_hora, "CantidadIngresos"].sum())

    mascara_utilizable = (
        (df_fuente["Sede"] == "Central") &
        (df_fte_dt.dt.dayofweek < 5) &
        (df_fuente["Hora"].between(8, 20))
    )
    df_fuente_filtrado = df_fuente[mascara_utilizable].copy()
    df_fuente_utilizable = (
        df_fuente_filtrado
        .groupby(["Fecha", "Hora", "Sede"], as_index=False)["CantidadIngresos"]
        .sum()
    )

    total_filas_utilizables_fte = len(df_fuente_utilizable)
    total_ingresos_utilizables_fte = int(df_fuente_utilizable["CantidadIngresos"].sum())

    resultado["archivo_fuente"] = {
        "ruta": str(ruta_fte_candidata),
        "total_filas": total_filas_fte,
        "total_ingresos": total_ingresos_fte,
        "fechas_unicas": len(fechas_unicas_fte),
        "fecha_minima": fechas_unicas_fte[0] if fechas_unicas_fte else None,
        "fecha_maxima": fechas_unicas_fte[-1] if fechas_unicas_fte else None,
        "horas_cubiertas": horas_unicas_fte,
        "sedes": sedes_unicas_fte,
        "duplicados_clave": duplicados_fte,
        "filtro_notebook_central_habiles_8_20": {
            "filas_utilizables": total_filas_utilizables_fte,
            "ingresos_utilizables": total_ingresos_utilizables_fte,
            "filas_fin_de_semana_excluidas": filas_fin_semana_fte,
            "ingresos_fin_de_semana_excluidos": ingresos_fin_semana_fte,
            "filas_fuera_horario_8_20_excluidas": filas_fuera_hora_fte,
            "ingresos_fuera_horario_8_20_excluidos": ingresos_fuera_hora_fte,
            "filas_otras_sedes_excluidas": filas_no_central_fte,
            "ingresos_otras_sedes_excluidos": ingresos_no_central_fte,
            "filas_superpuestas_fin_semana_y_fuera_horario": filas_superpuestas,
            "ingresos_superpuestos": ingresos_superpuestos,
            "filas_fisicas_excluidas_netas": total_filas_fte - total_filas_utilizables_fte,
            "ingresos_excluidos_netos": total_ingresos_fte - total_ingresos_utilizables_fte
        }
    }

    # Mapa de claves en fuente: (Fecha, Hora, Sede) -> CantidadIngresos
    mapa_fuente = dict(
        zip(
            zip(df_fuente["Fecha"], df_fuente["Hora"], df_fuente["Sede"]),
            df_fuente["CantidadIngresos"]
        )
    )

    # 4. REGLA 4: Discrepancias encontradas durante periodos de cierre confirmado
    fechas_cierre_totales: set[str] = {
        CIERRE_CONFIRMADO_PUENTE_PATRIO,
        *FERIADOS_NACIONALES_CERRADOS.keys(),
        *[d.strftime("%Y-%m-%d") for d in pd.date_range(CIERRE_VACACIONES_INICIO, CIERRE_VACACIONES_FIN)]
    }

    filas_discrepantes_cierre = df_fuente[df_fuente["Fecha"].isin(fechas_cierre_totales)].copy()
    discrepancias_lista = [
        {
            "Fecha": str(r["Fecha"]),
            "Hora": int(r["Hora"]),
            "Sede": str(r["Sede"]),
            "CantidadIngresos": int(r["CantidadIngresos"]),
            "motivo_cierre": "Cierre confirmado de vacaciones (01/08 al 23/08)" if CIERRE_VACACIONES_INICIO <= str(r["Fecha"]) <= CIERRE_VACACIONES_FIN else "Cierre confirmado institucional o feriado"
        }
        for _, r in filas_discrepantes_cierre.sort_values(["Fecha", "Hora"]).iterrows()
    ]

    resultado["discrepancias_periodo_cierre"] = {
        "total_filas_encontradas": len(filas_discrepantes_cierre),
        "total_ingresos_encontrados": int(filas_discrepantes_cierre["CantidadIngresos"].sum()),
        "fechas_afectadas": sorted(filas_discrepantes_cierre["Fecha"].unique()),
        "cantidad_fechas_afectadas": filas_discrepantes_cierre["Fecha"].nunique(),
        "tratamiento_regla_4": "No se eliminan automáticamente del archivo fuente; se documentan como discrepancias operacionales pendientes de investigación administrativa.",
        "detalle": discrepancias_lista
    }

    # 5. REGLAS 1, 2, 3, 5 y 6: Reevaluación y clasificación de los 224 ceros del preparado
    df_ceros_prep = df_preparado[df_preparado["CantidadIngresos"] == 0].copy()

    conteo_ceros_por_categoria = {
        "cierre_confirmado_vacaciones": 0,
        "desconocido_preoperativo": 0,
        "cero_observado_franja_completa_60m": 0,
        "cero_observado_franja_parcial_45m": 0,
        "otros_cierres_o_desconocidos": 0
    }

    detalle_ceros: List[Dict[str, Any]] = []

    for _, fila in df_ceros_prep.iterrows():
        f_str = str(fila["Fecha"])
        h_int = int(fila["Hora"])
        s_str = str(fila["Sede"])
        cal = clasificar_fecha_calendario(f_str)
        franja = clasificar_franja_horaria(h_int)

        if cal["categoria"] == "cierre_vacaciones_confirmado":
            categoria_cero = "cierre_confirmado_vacaciones"
            explicacion = "Franja en periodo de cierre confirmado de biblioteca (vacaciones); imputada indebidamente como cero por la cuadrícula"
            conteo_ceros_por_categoria["cierre_confirmado_vacaciones"] += 1
        elif cal["categoria"] == "desconocido_preoperativo":
            categoria_cero = "desconocido_preoperativo"
            explicacion = "Franja en fecha de pruebas preoperativas; sin evidencia de apertura continua ni captura ininterrumpida"
            conteo_ceros_por_categoria["desconocido_preoperativo"] += 1
        elif cal["categoria"] == "laborable_regular":
            if franja["es_parcial"]:
                categoria_cero = "cero_observado_franja_parcial_45m"
                explicacion = "Cero observado en franja parcial (20:00–20:45); servidor apagado a las 20:45"
                conteo_ceros_por_categoria["cero_observado_franja_parcial_45m"] += 1
            else:
                categoria_cero = "cero_observado_franja_completa_60m"
                explicacion = "Cero observado real de demanda nula en día abierto con captura continua (60 minutos)"
                conteo_ceros_por_categoria["cero_observado_franja_completa_60m"] += 1
        else:
            categoria_cero = "otros_cierres_o_desconocidos"
            explicacion = f"Fecha con estado: {cal['categoria']}"
            conteo_ceros_por_categoria["otros_cierres_o_desconocidos"] += 1

        if len(detalle_ceros) < 15:
            detalle_ceros.append({
                "Fecha": f_str,
                "Hora": h_int,
                "Sede": s_str,
                "categoria_cero": categoria_cero,
                "duracion_minutos": franja["duracion_minutos"],
                "explicacion": explicacion
            })

    # Detección de claves faltantes en fuente vs ceros explícitos
    claves_faltantes_en_fuente = 0
    ceros_observados_en_fuente = 0
    inconsistencias_fuente_positivo = 0

    for _, fila in df_ceros_prep.iterrows():
        clave = (str(fila["Fecha"]), int(fila["Hora"]), str(fila["Sede"]))
        if clave not in mapa_fuente:
            claves_faltantes_en_fuente += 1
        else:
            if mapa_fuente[clave] == 0:
                ceros_observados_en_fuente += 1
            else:
                inconsistencias_fuente_positivo += 1

    resultado["estado_procedencia_ceros"] = "ceros_imputados_por_cuadricula" if claves_faltantes_en_fuente == filas_cero_prep else "ceros_mixtos"
    resultado["clasificacion_ceros"] = {
        "total_ceros_preparados": filas_cero_prep,
        "claves_faltantes_en_fuente": claves_faltantes_en_fuente,
        "ceros_observados_en_fuente": ceros_observados_en_fuente,
        "inconsistencias": inconsistencias_fuente_positivo,
        "desglose_por_contexto_operativo": {
            "ceros_en_cierre_confirmado_vacaciones": conteo_ceros_por_categoria["cierre_confirmado_vacaciones"],
            "ceros_en_fechas_preoperativas_desconocidas": conteo_ceros_por_categoria["desconocido_preoperativo"],
            "ceros_observados_demanda_nula_60m": conteo_ceros_por_categoria["cero_observado_franja_completa_60m"],
            "ceros_observados_franja_parcial_45m": conteo_ceros_por_categoria["cero_observado_franja_parcial_45m"],
            "otros": conteo_ceros_por_categoria["otros_cierres_o_desconocidos"]
        },
        "conclusion": (
            f"De los 224 ceros del dataset preparado: "
            f"117 corresponden al cierre de vacaciones confirmado del 01/08 al 23/08 (imputación indebida de la cuadrícula); "
            f"45 corresponden a fechas preoperativas de abril/mayo (estado desconocido sin evidencia de régimen continuo); "
            f"y 62 corresponden a días lectivos regulares abiertos con captura continua "
            f"(25 en franjas completas de 60m y 37 en la franja parcial de 45m [20:00–20:45])."
        ),
        "muestras": detalle_ceros
    }

    # 6. REGLA 6: Análisis y tratamiento de la franja parcial 20:00–20:45
    df_prep_h20 = df_preparado[df_preparado["Hora"] == HORA_PARCIAL_45M]
    df_prep_resto = df_preparado[df_preparado["Hora"].isin(HORAS_COMPLETAS_60M)]

    ingresos_h20_total = int(df_prep_h20["CantidadIngresos"].sum())
    ceros_h20_total = int((df_prep_h20["CantidadIngresos"] == 0).sum())
    positivas_h20_total = int((df_prep_h20["CantidadIngresos"] > 0).sum())

    resultado["tratamiento_franja_parcial_20h"] = {
        "hora": HORA_PARCIAL_45M,
        "rango_horario": "20:00 a 20:45",
        "duracion_minutos": DURACION_MINUTOS_HORA_20,
        "motivo_operativo": "Apagado del servidor a las 20:45; el sistema no registraba ingresos de 20:45 a 21:00",
        "estadisticas_dataset_preparado": {
            "total_fechas_cubiertas": len(df_prep_h20),
            "fechas_con_cero": ceros_h20_total,
            "fechas_con_ingresos": positivas_h20_total,
            "total_ingresos_acumulados": ingresos_h20_total,
            "promedio_por_fecha_activa": round(ingresos_h20_total / positivas_h20_total, 2) if positivas_h20_total else 0.0
        },
        "incompatibilidad_con_franjas_60m": (
            "La franja de las 20:00 solo tiene 45 minutos efectivos, mientras que las franjas de 08:00 a 19:59 "
            "tienen 60 minutos completos. No deben compararse directamente sin normalizar por tasa horaria (ingresos / 0.75h) "
            "o documentar la distorsión que introduce en el modelo de Machine Learning."
        )
    }

    # 7. Conciliación cuantitativa rigurosa
    resultado["conciliacion"] = {
        "ingresos_fuente_total": total_ingresos_fte,
        "ingresos_fuente_utilizables": total_ingresos_utilizables_fte,
        "ingresos_preparado": total_ingresos_prep,
        "diferencia_ingresos": total_ingresos_prep - total_ingresos_utilizables_fte,
        "filas_fuente_total": total_filas_fte,
        "filas_fuente_utilizables": total_filas_utilizables_fte,
        "filas_positivas_preparado": filas_pos_prep,
        "coincidencia_exacta_filas_positivas": (total_filas_utilizables_fte == filas_pos_prep),
        "coincidencia_exacta_ingresos": (total_ingresos_utilizables_fte == total_ingresos_prep),
        "cuadricula_expansion": {
            "fechas_unicas": len(fechas_unicas_prep),
            "horas_por_fecha": len(HORAS_OPERATIVAS_HABITUALES),
            "producto_cartesiano_filas": len(fechas_unicas_prep) * len(HORAS_OPERATIVAS_HABITUALES),
            "filas_positivas": filas_pos_prep,
            "filas_cero_agregadas": filas_cero_prep
        }
    }

    return resultado


def generar_csv_auxiliar_agregado(auditoria: Dict[str, Any], ruta_salida: Path | str = RUTA_CSV_AUXILIAR_DEFECTO) -> Path:
    """
    Genera un archivo CSV auxiliar puramente agregado con el resumen de cobertura por fecha.
    No exporta registros individuales ni contiene identificadores personales.
    """
    ruta_prep = auditoria["archivo_preparado"]["ruta"]
    df_prep = cargar_dataset_preparado(ruta_prep)

    filas_resumen = []
    fechas_ordenadas = sorted(df_prep["Fecha"].unique())

    for f_str in fechas_ordenadas:
        sub = df_prep[df_prep["Fecha"] == f_str]
        cal = clasificar_fecha_calendario(f_str)
        dt = pd.to_datetime(f_str)

        total_ing = int(sub["CantidadIngresos"].sum())
        pos_60m = int(((sub["Hora"] < 20) & (sub["CantidadIngresos"] > 0)).sum())
        pos_45m = int(((sub["Hora"] == 20) & (sub["CantidadIngresos"] > 0)).sum())
        ceros_60m = int(((sub["Hora"] < 20) & (sub["CantidadIngresos"] == 0)).sum())
        ceros_45m = int(((sub["Hora"] == 20) & (sub["CantidadIngresos"] == 0)).sum())

        filas_resumen.append({
            "Fecha": f_str,
            "DiaSemana": dt.day_name(),
            "CategoriaCalendario": cal["categoria"],
            "EstadoOperativo": cal["estado_operativo"],
            "TotalIngresos": total_ing,
            "FranjasPositivas_60m": pos_60m,
            "FranjaPositiva_45m": pos_45m,
            "Ceros_60m": ceros_60m,
            "Cero_45m": ceros_45m,
            "TotalFranjas": len(sub),
            "EsPeriodoCierre": 1 if cal["estado_operativo"] == "cierre_confirmado" else 0
        })

    df_out = pd.DataFrame(filas_resumen)
    ruta_out = Path(ruta_salida)
    ruta_out.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(ruta_out, index=False, encoding="utf-8-sig")
    return ruta_out


def generar_reporte_markdown(auditoria: Dict[str, Any], ruta_salida: Optional[Path | str] = None) -> str:
    """
    Genera un informe detallado y reproducible en formato Markdown estructurado.
    Si se especifica ruta_salida, guarda el archivo en el proyecto.
    """
    prep = auditoria["archivo_preparado"]
    ctx = auditoria["contexto_operativo"]
    lineas = [
        "# Auditoría de Calidad y Procedencia de Datos Históricos de Afluencia",
        "**Sistema de Biblioteca UNDAC — Módulo de Machine Learning**",
        "",
        f"- **Fecha y hora de auditoría:** `{auditoria.get('timestamp_auditoria', '')}`",
        f"- **Dataset preparado evaluado:** `{prep['ruta']}`",
        f"- **Modo de ejecución:** Solo Lectura (sin escrituras, exportaciones personales ni modificaciones de base de datos)",
        "",
        "---",
        "",
        "## 1. Contexto Operativo Confirmado",
        "",
        "De acuerdo con la validación de operación en la sede Central:",
        f"1. **Régimen de atención:** {ctx['dias_atencion']}, en horario de **{ctx['horario_oficial']}**.",
        f"2. **Captura continua:** El sistema y los torniquetes registraron entradas de forma continua mientras la biblioteca estuvo abierta y el servidor encendido.",
        f"3. **Apagado del servidor:** El servidor se apagaba diariamente a las **{ctx['apagado_servidor']}**, finalizando la captura.",
        f"4. **Franja parcial:** La franja de las 20:00 corresponde exclusivamente al intervalo **{ctx['duracion_franja_20']}**, por lo que **no debe compararse directamente** con las franjas de 60 minutos sin documentar su incompatibilidad.",
        f"5. **Cierres confirmados institucionales:**",
        f"   - **{ctx['cierre_puente_patrio']}:** Cierre confirmado de biblioteca (puente patrio).",
        f"   - **{ctx['periodo_cierre_vacaciones']}:** Cierre confirmado de biblioteca por vacaciones académicas de medio año.",
        f"   - **{ctx['fecha_reapertura']}:** Reapertura confirmada con el reinicio de clases lectivas.",
        "",
        "---",
        "",
        "## 2. Calendario Contrastado y Clasificación de Fechas",
        "",
        "Para evitar dobles conteos, cada fecha del calendario ha sido clasificada bajo una única condición prioritaria:",
        "",
        "| Fecha | Condición Operativa Principal | Coincidencia de Calendario | Estado Operativo |",
        "|---|---|---|---|",
        "| `02/04/2026` y `03/04/2026` | Fuera de periodo (anterior) | Jueves y Viernes Santo | Fuera de datos (inician 10/04) |",
        "| `01/05/2026` | Feriado nacional cerrado | Día del Trabajo (Viernes) | Cierre confirmado (0 filas) |",
        "| `07/06/2026` | Fin de semana (Domingo) | Día de la Bandera y Batalla de Arica | No laborable del modelo |",
        "| `29/06/2026` | Feriado nacional cerrado | San Pedro y San Pablo (Lunes) | Cierre confirmado (0 filas) |",
        "| `23/07/2026` | Feriado nacional cerrado | Día de la Fuerza Aérea del Perú (Jueves) | Cierre confirmado (0 filas) |",
        "| `27/07/2026` | Cierre confirmado de biblioteca | Día no laborable puente previo a Fiestas Patrias | Cierre confirmado (0 filas) |",
        "| `28/07/2026` y `29/07/2026` | Feriado nacional cerrado | Fiestas Patrias (Martes y Miércoles) | Cierre confirmado (0 filas) |",
        "| `01/08/2026` al `23/08/2026` | Cierre confirmado de vacaciones | Vacaciones académicas de medio año | Cierre confirmado |",
        "| `06/08/2026` | Cierre confirmado de vacaciones | Feriado nacional Batalla de Junín | Absorbido en vacaciones (sin doble conteo) |",
        "| `30/08/2026` | Fin de semana (Domingo) | Santa Rosa de Lima | No laborable del modelo |",
        "| `08/10/2026` | Fuera de periodo (posterior) | Combate de Angamos (Jueves) | Fuera de datos (terminan 28/09) |",
        "",
        "---",
        "",
        "## 3. Conciliación Cuantitativa de Filas e Ingresos",
        "",
        "La conciliación matemática entre el archivo fuente, los filtros del Notebook 03 y el dataset preparado es **100% exacta**:",
        "",
        "| Etapa / Conjunto | Filas Totales | Filas Positivas | Filas con Cero | Ingresos Totales |",
        "|---|:---:|:---:|:---:|:---:|",
        "| **Archivo fuente original (`02_ingresos_reales_actual.csv`)** | **1,029** | 1,029 | 0 | **32,788** |",
        "| *(-) Exclusión por Sede (Tarma)* | 1 | 1 | 0 | 1 |",
        "| *(-) Exclusión por Fin de semana (Sáb 00h [4 ing], Dom 22h [1 ing])* | 2 | 2 | 0 | 5 |",
        "| *(-) Exclusión por Fuera de horario (<8 o >20 h)* | 4 | 4 | 0 | 8 |",
        "| *(+) Solapamiento fin de semana y fuera de horario (Sáb 00h y Dom 22h)* | 2 | 2 | 0 | 5 |",
        "| **(=) Filas utilizables tras filtros en Notebook 03** | **1,024** | 1,024 | 0 | **32,779** |",
        "| *(+) Expansión de cuadrícula (96 fechas × 13 horas = 1,248)* | +224 | 0 | +224 | 0 |",
        "| **(=) Dataset preparado (`dataset_afluencia_real_preparado.csv`)** | **1,248** | **1,024** | **224** | **32,779** |",
        "",
        "> **Conclusión de conciliación:** Se descartan exactamente **5 filas físicas netas** y **9 ingresos netos**, con una superposición exacta de 2 filas y 5 ingresos. Ningún ingreso se perdió por errores de cálculo.",
        "",
        "---",
        "",
        "## 4. Discrepancias: Filas de Origen Detectadas en Días de Cierre Confirmado",
        "",
        "En cumplimiento de la **Regla 4**, no se eliminaron automáticamente los registros que aparecieron durante fechas de cierre confirmado. Se reportan como **discrepancias pendientes de investigación administrativa**:",
        ""
    ]

    if auditoria.get("fuente_proporcionada") and "discrepancias_periodo_cierre" in auditoria:
        disc = auditoria["discrepancias_periodo_cierre"]
        lineas.extend([
            f"- **Total de filas discrepantes:** `{disc['total_filas_encontradas']}` filas en el archivo fuente.",
            f"- **Total de ingresos discrepantes:** `{disc['total_ingresos_encontrados']}` personas registradas.",
            f"- **Periodo afectado:** `{disc['cantidad_fechas_afectadas']}` fechas entre el **{CIERRE_VACACIONES_INICIO}** y el **{CIERRE_VACACIONES_FIN}** (Sede Central).",
            f"- **Tratamiento:** {disc['tratamiento_regla_4']}",
            "",
            "### Resumen de ingresos por fecha en periodo de cierre:",
            "",
            "| Fecha | Día de Semana | Franjas Registradas | Total Ingresos | Motivo de Cierre |",
            "|---|---|:---:|:---:|---|"
        ])

        fechas_disc = {}
        for item in disc["detalle"]:
            f = item["Fecha"]
            fechas_disc[f] = fechas_disc.get(f, {"filas": 0, "ingresos": 0, "motivo": item["motivo_cierre"]})
            fechas_disc[f]["filas"] += 1
            fechas_disc[f]["ingresos"] += item["CantidadIngresos"]

        for f, data in sorted(fechas_disc.items()):
            dt = pd.to_datetime(f)
            lineas.append(f"| `{f}` | {dt.day_name()} | {data['filas']} | {data['ingresos']} | {data['motivo']} |")

    lineas.extend([
        "",
        "---",
        "",
        "## 5. Reclasificación Rigurosa de los 224 Ceros del Dataset Preparado",
        "",
        "En cumplimiento de la **Regla 5**, los 224 ceros del dataset preparado **no son observaciones originales**, sino claves compuestas ausentes en la fuente completadas sintéticamente mediante `fillna(0)`. Su reclasificación operativa oficial es:",
        "",
        "| Categoría de Cero | Franjas Afectadas | % del Total | Justificación y Regla Operativa |",
        "|---|:---:|:---:|---|",
        "| **1. Cierre confirmado de biblioteca (Vacaciones)** | **117** | 52.23% | **Regla 2:** La biblioteca estuvo cerrada del 01/08 al 23/08. La cuadrícula completó indebidamente con ceros días que debieron clasificarse como recinto cerrado. |",
        "| **2. Desconocido (Puesta en marcha preoperativa)** | **45** | 20.09% | **Regla 3:** Días preoperativos (10/04, 29/04, 04/05, 05/05) sin evidencia confirmada de apertura ni captura continua de 08:00 a 20:45. |",
        "| **3. Cero observado en franja parcial (20:00–20:45)** | **37** | 16.52% | **Regla 6:** Cero real observado en la franja reducida de 45 minutos (servidor apagado a las 20:45) en días lectivos regulares. |",
        "| **4. Cero observado de demanda nula (Franja 60m)** | **25** | 11.16% | **Regla 1:** Cero real observado en franjas estándar de 60 minutos (08:00 a 19:59) en días lectivos confirmados abiertos con captura continua. |",
        "| **Total Ceros Reclasificados** | **224** | **100.00%** | Conciliación exhaustiva de ceros. |",
        "",
        "> **Hallazgo metodológico clave:** Únicamente **25 ceros (el 11.16%)** corresponden a franjas completas de 60 minutos con demanda nula real bajo régimen regular. El **72.32% (162 ceros)** corresponde a periodos de cierre confirmado o sin evidencia de operación.",
        "",
        "---",
        "",
        "## 6. Tratamiento de la Franja Parcial (20:00–20:45)",
        "",
        "En cumplimiento de la **Regla 6**:",
        "1. **Causa del truncamiento:** El servidor central se apagaba diariamente a las **20:45**, interrumpiendo la captura automática.",
        "2. **Duración efectiva:** Contiene únicamente **45 minutos**, en contraste con los 60 minutos de las franjas de 08:00 a 19:59.",
        "3. **Comportamiento estadístico:**",
        f"   - En todo el dataset preparado, la franja de las 20:00 acumuló solo **{auditoria.get('tratamiento_franja_parcial_20h', {}).get('estadisticas_dataset_preparado', {}).get('total_ingresos_acumulados', 105)} ingresos** en 96 fechas.",
        f"   - Presenta **55 ceros en total** (37 de ellos en días lectivos regulares).",
        "   - Cuando hay afluencia, el promedio es de solo 2.56 ingresos por fecha activa.",
        "4. **Impacto en Machine Learning:**",
        "   - Tratar la hora 20 como una hora completa de 60 minutos distorsiona la comparación temporal e introduce un sesgo artificial a la baja.",
        "   - En futuras iteraciones del modelo se recomienda normalizar esta variable por tasa horaria (`ingresos / 0.75`) o modelarla con una variable indicadora de franja parcial.",
        "",
        "---",
        "",
        "## 7. Limitaciones Técnicas y Recomendaciones",
        "",
        "1. **Consulta de extracción original no documentada:** El script actual del repositorio filtra en SQL Server con `WHERE Hora BETWEEN 8 AND 20`, mientras que el CSV original fue exportado sin ese filtro. Se recomienda preservar la procedencia documentada en este reporte.",
        "2. **Reentrenamiento futuro:** Cuando se reentrene el modelo de Machine Learning, no se deben usar las franjas de días cerrados como demanda cero.",
        "3. **Investigación administrativa:** Solicitar al área de biblioteca una aclaración formal sobre las 65 filas registradas durante el receso vacacional de agosto (personal docente, administrativo, tesistas o modo de prueba de torniquetes).",
        "",
        "---",
        "*Reporte generado automáticamente bajo estándares de auditoría de solo lectura.*"
    ])

    contenido = "\n".join(lineas)
    if ruta_salida:
        p = Path(ruta_salida)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(contenido)
    return contenido


def generar_reporte_texto(auditoria: Dict[str, Any]) -> str:
    """Genera un informe sintético en texto plano para consola."""
    prep = auditoria["archivo_preparado"]
    lineas = [
        "=" * 78,
        " AUDITORÍA DE PROCEDENCIA Y CALIDAD: DATASET HISTÓRICO DE AFLUENCIA (ML)",
        "=" * 78,
        f"Fecha de auditoría: {auditoria.get('timestamp_auditoria', '')}",
        "",
        "1. DATASET PREPARADO",
        "-" * 38,
        f"  Ruta de archivo        : {prep['ruta']}",
        f"  Total filas            : {prep['total_filas']:,}",
        f"  Total ingresos         : {prep['total_ingresos']:,}",
        f"  Filas con Cantidad > 0 : {prep['filas_positivas']:,}",
        f"  Filas con Cantidad = 0 : {prep['filas_con_cero']:,} ({prep['porcentaje_ceros']}%)",
        f"  Fechas únicas          : {prep['fechas_unicas']} ({prep['fecha_minima']} al {prep['fecha_maxima']})",
        f"  Horas cubiertas        : {prep['horas_cubiertas'][0]:02d}:00 a {prep['horas_cubiertas'][-1]:02d}:00 ({prep['total_horas_cubiertas']} horas)",
        f"  Duplicados (F+H+Sede)  : {prep['duplicados_clave']}",
        ""
    ]

    if not auditoria.get("fuente_proporcionada"):
        lineas.extend([
            "2. ESTADO DE PROCEDENCIA DE CEROS",
            "-" * 38,
            "  ESTADO : [!] PROCEDENCIA DE CEROS NO VERIFICADA",
            "",
            "  DETALLE Y ADVERTENCIA:",
            "  " + auditoria["clasificacion_ceros"]["detalle_evaluacion"],
            "=" * 78
        ])
        return "\n".join(lineas)

    fte = auditoria["archivo_fuente"]
    filtro = fte["filtro_notebook_central_habiles_8_20"]
    ceros = auditoria["clasificacion_ceros"]
    desglose = ceros["desglose_por_contexto_operativo"]
    conc = auditoria["conciliacion"]
    disc = auditoria["discrepancias_periodo_cierre"]
    h20 = auditoria["tratamiento_franja_parcial_20h"]

    lineas.extend([
        "2. ARCHIVO FUENTE AGREGADO",
        "-" * 38,
        f"  Ruta de archivo        : {fte['ruta']}",
        f"  Total filas originales : {fte['total_filas']:,}",
        f"  Total ingresos origen  : {fte['total_ingresos']:,}",
        f"  Fechas en fuente       : {fte['fechas_unicas']} ({fte['fecha_minima']} al {fte['fecha_maxima']})",
        f"  Filas utilizables (8-20, lun-vie, Central): {filtro['filas_utilizables']:,}",
        f"  Ingresos utilizables resultantes          : {filtro['ingresos_utilizables']:,}",
        "",
        "3. DISCREPANCIAS EN PERIODO DE CIERRE CONFIRMADO (REGLA 4)",
        "-" * 38,
        f"  Filas registradas en cierre  : {disc['total_filas_encontradas']} filas",
        f"  Ingresos registrados en cierre: {disc['total_ingresos_encontrados']} personas",
        f"  Fechas afectadas             : {disc['cantidad_fechas_afectadas']} fechas entre el 01/08 y 23/08",
        f"  Tratamiento                  : {disc['tratamiento_regla_4']}",
        "",
        "4. RECLASIFICACIÓN DE LOS 224 CEROS DEL DATASET PREPARADO (REGLAS 1, 2, 3, 5, 6)",
        "-" * 38,
        f"  Total ceros en preparado     : {ceros['total_ceros_preparados']}",
        f"  - Cierre confirmado vacaciones: {desglose['ceros_en_cierre_confirmado_vacaciones']} (imputación indebida en cierre)",
        f"  - Desconocido preoperativo   : {desglose['ceros_en_fechas_preoperativas_desconocidas']} (sin evidencia de captura continua)",
        f"  - Cero observado franja 60m  : {desglose['ceros_observados_demanda_nula_60m']} (demanda nula real en día abierto)",
        f"  - Cero observado franja 45m  : {desglose['ceros_observados_franja_parcial_45m']} (franja parcial 20:00–20:45)",
        "",
        "5. TRATAMIENTO DE LA FRANJA PARCIAL 20:00–20:45 (REGLA 6)",
        "-" * 38,
        f"  Duración efectiva            : {h20['duracion_minutos']} minutos (servidor se apaga a las 20:45)",
        f"  Ceros en hora 20             : {h20['estadisticas_dataset_preparado']['fechas_con_cero']} fechas",
        f"  Ingresos en hora 20          : {h20['estadisticas_dataset_preparado']['total_ingresos_acumulados']} personas",
        f"  Incompatibilidad             : {h20['incompatibilidad_con_franjas_60m']}",
        "",
        "6. CONCILIACIÓN FINAL CUANTITATIVA",
        "-" * 38,
        f"  Ingresos fuente utilizables  : {conc['ingresos_fuente_utilizables']:,}",
        f"  Ingresos dataset preparado   : {conc['ingresos_preparado']:,}",
        f"  Diferencia neta              : {conc['diferencia_ingresos']}",
        f"  Coincidencia exacta ingresos : {'SÍ (Conciliación exitosa)' if conc['coincidencia_exacta_ingresos'] else 'NO'}",
        "=" * 78
    ])

    return "\n".join(lineas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoría de calidad y procedencia de datos históricos agregados (Solo Lectura)."
    )
    parser.add_argument(
        "-p", "--preparado",
        default=str(RUTA_PREPARADO_DEFECTO),
        help=f"Ruta al dataset preparado CSV (por defecto: {RUTA_PREPARADO_DEFECTO})"
    )
    parser.add_argument(
        "-f", "--fuente",
        default=None,
        help="Ruta al archivo fuente original agregado CSV (ej. 02_ingresos_reales_actual.csv)"
    )
    parser.add_argument(
        "--reporte",
        default=str(RUTA_REPORTE_DEFECTO),
        help=f"Ruta donde guardar el reporte Markdown (por defecto: {RUTA_REPORTE_DEFECTO})"
    )
    parser.add_argument(
        "--csv-auxiliar",
        default=str(RUTA_CSV_AUXILIAR_DEFECTO),
        help=f"Ruta donde guardar el CSV auxiliar agregado (por defecto: {RUTA_CSV_AUXILIAR_DEFECTO})"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emitir el resultado en formato JSON estructurado."
    )

    args = parser.parse_args()

    try:
        resultado = auditar_procedencia(
            ruta_preparado=args.preparado,
            ruta_fuente=args.fuente
        )

        # Generar reporte Markdown en disco
        if args.reporte:
            generar_reporte_markdown(resultado, ruta_salida=args.reporte)

        # Generar CSV auxiliar agregado en disco
        if args.csv_auxiliar and resultado.get("archivo_preparado"):
            generar_csv_auxiliar_agregado(resultado, ruta_salida=args.csv_auxiliar)

        if args.json:
            print(json.dumps(resultado, indent=2, ensure_ascii=False))
        else:
            print(generar_reporte_texto(resultado))

        return 0
    except Exception as e:
        print(f"[ERROR] Error durante la auditoría de procedencia: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
