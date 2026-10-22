"""
Servicio de Vista Previa de Solo Lectura para la Reconciliación del Historial de Afluencia
Biblioteca UNDAC.

Reglas y Salvaguardas:
1. Solo lectura: Ejecuta únicamente consultas SELECT parametrizadas.
2. Exclusión estricta de pruebas: Aplica ISNULL(EsPrueba, 0) = 0.
3. Segregación de Sede Desconocida (Sede NULL):
   - Los registros con Sede NULL o en blanco se clasifican de forma separada como "Desconocida".
   - Se excluyen estrictamente de los totales de cualquier sede, incluida Central.
   - Mismo criterio en ambas consultas (dbo.RegistroIngresos y dbo.AfluenciaHistoricaAgregada) y en todos los resultados.
4. Validación de Sede contra el Catálogo Real:
   - Valida la sede contra dbo.Salas (fuente real de sedes en el esquema del proyecto).
   - Rechaza sedes ausentes, vacías, inexistentes o inactivas.
5. Cierre Operativo Exacto a las 20:45:00:
   - Las marcas temporales mayores a 20:45:00 (incluidas 20:45:00.001 y 20:45:59.999) son atípicas/post-cierre.
   - Conserva por separado los ingresos válidos y los posteriores al cierre cuando una hora tenga ambos.
6. Cierre del 24/07/2026:
   - Identificado como regla configurada en el sistema, pendiente de confirmación documental institucional.
7. Privacidad: No expone nombres, DNIs, códigos ni marcas temporales exactas individuales.
"""

import os
import sys
from pathlib import Path
from datetime import date, datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import get_db_cursor

# Etiqueta estándar para registros sin sede asignada
SEDE_DESCONOCIDA = "Desconocida"

# Calendario institucional confirmado 2026 (feriados oficiales nacionales)
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

# Cierre puente FAP: regla configurada en código, pendiente de confirmación documental institucional
CIERRE_CONFIGURADO_24_JULIO = "2026-07-24"
CIERRE_CONFIRMADO_24_JULIO = CIERRE_CONFIGURADO_24_JULIO  # Alias de compatibilidad

# Cierre institucional confirmado previo a Fiestas Patrias
CIERRE_CONFIRMADO_27_JULIO = "2026-07-27"
INICIO_VACACIONES_AGOSTO = date(2026, 8, 1)
FIN_VACACIONES_AGOSTO = date(2026, 8, 23)

# Cierre operativo exacto (20:45:00)
HORA_CIERRE_OPERATIVO = dt_time(20, 45, 0, 0)

FECHA_CORTE_HISTORICO_DEFECTO = "2026-09-28"


def validar_sede_activa(
    sede: Optional[str],
    cursor: Any = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Valida la sede contra la fuente real de sedes activas del proyecto (dbo.Salas).
    Identificada en el esquema del proyecto: dbo.Salas (SalaID, NombreSala, Sede, Activo).

    Retorna: (es_valida, sede_canonico, motivo_rechazo)
    Posibles motivos de rechazo:
    - 'sede_ausente_o_vacia': Sede None, vacía o compuesta solo por espacios.
    - 'sede_inexistente': No existe ninguna sala registrada bajo esta sede en dbo.Salas.
    - 'sede_inactiva': La sede existe en dbo.Salas pero todas sus salas están inactivas (Activo = 0).
    """
    if sede is None or not str(sede).strip():
        return False, None, "sede_ausente_o_vacia"

    sede_limpia = str(sede).strip()

    def _consultar_catalogo(c: Any) -> Tuple[bool, Optional[str], Optional[str]]:
        sql = """
            SELECT 
                Sede,
                MAX(CASE WHEN Activo = 1 THEN 1 ELSE 0 END) AS TieneSalasActivas
            FROM dbo.Salas
            WHERE Sede IS NOT NULL AND LTRIM(RTRIM(Sede)) <> ''
              AND LOWER(LTRIM(RTRIM(Sede))) = LOWER(?)
            GROUP BY Sede
        """
        c.execute(sql, (sede_limpia,))
        fila = c.fetchone()
        if not fila:
            return False, None, "sede_inexistente"

        sede_db = fila[0]
        tiene_activas = int(fila[1]) if fila[1] is not None else 0
        if tiene_activas == 0:
            return False, None, "sede_inactiva"

        return True, sede_db, None

    if cursor is not None:
        return _consultar_catalogo(cursor)

    with get_db_cursor(autocommit=True) as (_, cursor_db):
        if cursor_db is None:
            raise ConnectionError("No fue posible conectarse a SQL Server para validar la sede.")
        return _consultar_catalogo(cursor_db)


def clasificar_marca_temporal(
    timestamp_val: Any
) -> Dict[str, Any]:
    """
    Clasifica una marca temporal respecto al horario de cierre de las 20:45:00.
    Acepta objetos datetime, time, o cadenas de texto con hora o timestamp ISO.

    Límite estricto: 20:45:00.000.
    Cualquier valor mayor (ej. 20:45:00.001, 20:45:59.999) es posterior al cierre.
    """
    t = None
    if isinstance(timestamp_val, datetime):
        t = timestamp_val.time()
    elif isinstance(timestamp_val, dt_time):
        t = timestamp_val
    elif isinstance(timestamp_val, str):
        s = timestamp_val.strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%H:%M:%S.%f",
            "%H:%M:%S"
        ):
            try:
                t = datetime.strptime(s, fmt).time()
                break
            except ValueError:
                pass
        if t is None:
            raise ValueError(f"No fue posible parsear la marca temporal: '{timestamp_val}'")
    else:
        raise TypeError(f"Tipo no admitido para marca temporal: {type(timestamp_val)}")

    cierre_exacto = HORA_CIERRE_OPERATIVO
    es_hora_20 = (t.hour == 20)
    es_valido_20 = bool(es_hora_20 and t <= cierre_exacto)
    es_post_cierre_20 = bool(es_hora_20 and t > cierre_exacto)
    es_post_cierre_general = bool((t > cierre_exacto if t.hour >= 20 else False) or (t.hour > 20))

    return {
        "tiempo": t.strftime("%H:%M:%S.%f") if t.microsecond else t.strftime("%H:%M:%S"),
        "hora": t.hour,
        "es_hora_20": es_hora_20,
        "es_valido_hora20": es_valido_20,
        "es_post_cierre_hora20": es_post_cierre_20,
        "es_post_cierre_general": es_post_cierre_general
    }


def calcular_banderas_analiticas(
    fecha_str: str,
    hora_int: int,
    cantidad_hora20_valida: int = 0,
    cantidad_hora20_post_cierre: int = 0
) -> Dict[str, Any]:
    """
    Calcula las banderas analíticas conforme a la metodología demostrada en el dataset
    y en cargar_afluencia_historica.py.

    Metodología de banderas ortogonales:
    - EsHorarioAtipico: Evalúa estrictamente la franja horaria y fin de semana.
      (Hora < 8 | Hora > 20 | DiaSemana >= 5 | Ingresos hora 20 posteriores a las 20:45:00).
      Los cierres por vacaciones (agosto), feriados y puentes se registran exclusivamente
      en sus propias banderas y NO se mezclan en EsHorarioAtipico.
    - EsCierreAgosto: 1 durante el periodo de vacaciones (01/08/2026 al 23/08/2026).
    - EsFranjaParcial: 1 para la hora 20 (atención de 45 min).
    - EsFeriado: 1 en feriados nacionales confirmados 2026.
    - EsCierre27Julio: 1 el 27/07/2026.
    - EsCierre24Julio: 1 el 24/07/2026 (regla configurada en sistema).
    """
    dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    dia_semana = dt.weekday()
    es_fin_de_semana = bool(dia_semana >= 5)

    es_feriado = 1 if fecha_str in FERIADOS_CONFIRMADOS_2026 else 0
    es_cierre_24_julio = 1 if fecha_str == CIERRE_CONFIGURADO_24_JULIO else 0
    es_cierre_27_julio = 1 if fecha_str == CIERRE_CONFIRMADO_27_JULIO else 0
    es_cierre_agosto = 1 if (INICIO_VACACIONES_AGOSTO <= dt <= FIN_VACACIONES_AGOSTO) else 0

    # Evaluación de horario atípico: estrictamente horario y fin de semana
    if hora_int == 20:
        if cantidad_hora20_post_cierre > 0 and cantidad_hora20_valida == 0:
            # Todos los ingresos fueron estrictamente posteriores a las 20:45:00
            es_franja_parcial = 0
            es_atipico = 1
            estado_franja_20h = "posterior_al_cierre"
        elif cantidad_hora20_valida > 0 and cantidad_hora20_post_cierre > 0:
            # Mixta: hubo ingresos operativos válidos (<= 20:45:00) y también posteriores (> 20:45:00)
            es_franja_parcial = 1
            es_atipico = 1
            estado_franja_20h = "mixta_con_registros_post_cierre"
        else:
            # Franja parcial válida (20:00:00 a 20:45:00)
            es_franja_parcial = 1
            es_atipico = 1 if es_fin_de_semana else 0
            estado_franja_20h = "franja_parcial_valida_45m"
    else:
        es_franja_parcial = 0
        estado_franja_20h = "no_aplica"
        es_atipico = 1 if (hora_int < 8 or hora_int > 20 or es_fin_de_semana) else 0

    return {
        "EsHorarioAtipico": es_atipico,
        "EsCierreAgosto": es_cierre_agosto,
        "EsFranjaParcial": es_franja_parcial,
        "EsFeriado": es_feriado,
        "EsCierre27Julio": es_cierre_27_julio,
        "EsCierre24Julio": es_cierre_24_julio,
        "EstadoFranja20h": estado_franja_20h
    }


def obtener_vista_previa_historial_afluencia(
    fecha_corte: str = FECHA_CORTE_HISTORICO_DEFECTO,
    sede_filtro: Optional[str] = None,
    cursor_externo: Any = None
) -> Dict[str, Any]:
    """
    Ejecuta la vista previa comparativa de solo lectura aplicando aislamiento por sede
    desde la consulta inicial y segregando estrictamente los registros con Sede NULL.
    """
    sede_limpia = str(sede_filtro).strip() if sede_filtro and str(sede_filtro).strip() else None

    if cursor_externo is not None:
        return _ejecutar_vista_previa(cursor_externo, fecha_corte, sede_limpia)

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")
        return _ejecutar_vista_previa(cursor, fecha_corte, sede_limpia)


def _ejecutar_vista_previa(
    cursor: Any,
    fecha_corte: str,
    sede_filtro: Optional[str]
) -> Dict[str, Any]:
    timestamp_ejecucion = datetime.now().isoformat()

    # 1. Agregación desde dbo.RegistroIngresos
    # Regla: NO atribuir Sede NULL a Central. Clasificar como 'Desconocida' de forma separada.
    # Excluir de totales de cualquier sede, incluida Central.
    params_ri = []
    filtro_ri_sede = ""
    if sede_filtro:
        filtro_ri_sede = " AND (CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 'Desconocida' ELSE Sede END) = ? "
        params_ri.append(sede_filtro)

    sql_ri = f"""
        SELECT 
            CAST(FechaHora AS DATE) AS Fecha,
            DATEPART(HOUR, FechaHora) AS Hora,
            CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 'Desconocida' ELSE Sede END AS Sede,
            COUNT(*) AS CantidadIngresos,
            SUM(CASE WHEN DATEPART(HOUR, FechaHora) = 20 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME) THEN 1 ELSE 0 END) AS CantidadHora20Valida,
            SUM(CASE WHEN DATEPART(HOUR, FechaHora) = 20 AND CAST(FechaHora AS TIME) > CAST('20:45:00' AS TIME) THEN 1 ELSE 0 END) AS CantidadHora20PostCierre
        FROM dbo.RegistroIngresos
        WHERE ISNULL(EsPrueba, 0) = 0 {filtro_ri_sede}
        GROUP BY 
            CAST(FechaHora AS DATE), 
            DATEPART(HOUR, FechaHora), 
            CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 'Desconocida' ELSE Sede END
        ORDER BY Fecha ASC, Hora ASC, Sede ASC
    """
    cursor.execute(sql_ri, tuple(params_ri))
    filas_ri_raw = cursor.fetchall()

    mapa_ri: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    total_ingresos_ri = 0
    for r in filas_ri_raw:
        f_str = str(r[0])
        h_int = int(r[1])
        s_str = str(r[2])
        cant = int(r[3])
        cnt_valida_20 = int(r[4]) if r[4] is not None else 0
        cnt_post_20 = int(r[5]) if r[5] is not None else 0

        total_ingresos_ri += cant

        # Clasificación de ingresos válidos vs posteriores al cierre
        if h_int == 20:
            ingresos_validos = cnt_valida_20
            ingresos_post_cierre = cnt_post_20
        elif h_int < 8:
            ingresos_validos = 0
            ingresos_post_cierre = 0
        elif h_int > 20:
            ingresos_validos = 0
            ingresos_post_cierre = cant
        else:
            ingresos_validos = cant
            ingresos_post_cierre = 0

        banderas = calcular_banderas_analiticas(
            f_str,
            h_int,
            cantidad_hora20_valida=cnt_valida_20,
            cantidad_hora20_post_cierre=cnt_post_20
        )

        mapa_ri[(f_str, h_int, s_str)] = {
            "Fecha": f_str,
            "Hora": h_int,
            "Sede": s_str,
            "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
            "CantidadIngresos": cant,
            "IngresosValidos": ingresos_validos,
            "IngresosPostCierre": ingresos_post_cierre,
            "CantidadHora20Valida": cnt_valida_20,
            "CantidadHora20PostCierre": cnt_post_20,
            **banderas
        }

    # 2. Filas desde dbo.AfluenciaHistoricaAgregada
    # Aplicar el mismo criterio de Sede NULL/Desconocida y filtro de sede antes de conteos
    params_aha = []
    filtro_aha_sede = ""
    if sede_filtro:
        filtro_aha_sede = " WHERE (CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 'Desconocida' ELSE Sede END) = ? "
        params_aha.append(sede_filtro)

    sql_aha = f"""
        SELECT 
            Fecha,
            Hora,
            CASE WHEN Sede IS NULL OR LTRIM(RTRIM(Sede)) = '' THEN 'Desconocida' ELSE Sede END AS Sede,
            CantidadIngresos,
            EsHorarioAtipico,
            EsCierreAgosto,
            EsFranjaParcial,
            EsFeriado,
            EsCierre27Julio
        FROM dbo.AfluenciaHistoricaAgregada
        {filtro_aha_sede}
        ORDER BY Fecha ASC, Hora ASC, Sede ASC
    """
    cursor.execute(sql_aha, tuple(params_aha))
    filas_aha_raw = cursor.fetchall()

    mapa_aha: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    total_ingresos_aha = 0
    for r in filas_aha_raw:
        f_str = str(r[0])
        h_int = int(r[1])
        s_str = str(r[2])
        cant = int(r[3])
        total_ingresos_aha += cant

        mapa_aha[(f_str, h_int, s_str)] = {
            "Fecha": f_str,
            "Hora": h_int,
            "Sede": s_str,
            "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
            "CantidadIngresos": cant,
            "EsHorarioAtipico": int(r[4]),
            "EsCierreAgosto": int(r[5]),
            "EsFranjaParcial": int(r[6]),
            "EsFeriado": int(r[7]),
            "EsCierre27Julio": int(r[8])
        }

    # 3. Conciliación y Comparación detallada de Cantidades y Banderas
    todas_las_claves = sorted(list(set(mapa_ri.keys()) | set(mapa_aha.keys())))

    filas_historicas_coincidentes = []
    filas_historicas_discrepantes = []
    filas_solo_en_aha_historicas = []
    filas_solo_en_ri_historicas = []

    filas_nuevas_post_corte = []
    colisiones_duplicados_post_corte = []
    filas_solo_en_aha_post_corte = []

    for clave in todas_las_claves:
        f_str, h_int, s_str = clave
        en_ri = clave in mapa_ri
        en_aha = clave in mapa_aha
        es_posterior = f_str > fecha_corte

        if en_ri and en_aha:
            cant_ri = mapa_ri[clave]["CantidadIngresos"]
            cant_aha = mapa_aha[clave]["CantidadIngresos"]

            flags_esp = {
                "EsHorarioAtipico": mapa_ri[clave]["EsHorarioAtipico"],
                "EsCierreAgosto": mapa_ri[clave]["EsCierreAgosto"],
                "EsFranjaParcial": mapa_ri[clave]["EsFranjaParcial"],
                "EsFeriado": mapa_ri[clave]["EsFeriado"],
                "EsCierre27Julio": mapa_ri[clave]["EsCierre27Julio"]
            }
            flags_alm = {
                "EsHorarioAtipico": mapa_aha[clave]["EsHorarioAtipico"],
                "EsCierreAgosto": mapa_aha[clave]["EsCierreAgosto"],
                "EsFranjaParcial": mapa_aha[clave]["EsFranjaParcial"],
                "EsFeriado": mapa_aha[clave]["EsFeriado"],
                "EsCierre27Julio": mapa_aha[clave]["EsCierre27Julio"]
            }

            diff_flags = {
                k: {"esperado": flags_esp[k], "almacenado": flags_alm[k]}
                for k in flags_esp if flags_esp[k] != flags_alm[k]
            }

            diff_cant = (cant_ri != cant_aha)
            hay_discrepancia = diff_cant or bool(diff_flags)

            detalle_item = {
                "Fecha": f_str,
                "Hora": h_int,
                "Sede": s_str,
                "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
                "CantidadEnRI": cant_ri,
                "CantidadEnAHA": cant_aha,
                "DiferenciaCantidad": cant_ri - cant_aha,
                "IngresosValidos": mapa_ri[clave]["IngresosValidos"],
                "IngresosPostCierre": mapa_ri[clave]["IngresosPostCierre"],
                "CantidadHora20Valida": mapa_ri[clave]["CantidadHora20Valida"],
                "CantidadHora20PostCierre": mapa_ri[clave]["CantidadHora20PostCierre"],
                "TieneDiscrepanciaCantidad": diff_cant,
                "TieneDiscrepanciaBanderas": bool(diff_flags),
                "DiscrepanciasBanderas": diff_flags,
                "BanderasEsperadas": flags_esp,
                "BanderasAlmacenadas": flags_alm
            }

            if not es_posterior:
                if not hay_discrepancia:
                    filas_historicas_coincidentes.append({
                        "Fecha": f_str,
                        "Hora": h_int,
                        "Sede": s_str,
                        "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
                        "Cantidad": cant_ri,
                        "IngresosValidos": mapa_ri[clave]["IngresosValidos"],
                        "IngresosPostCierre": mapa_ri[clave]["IngresosPostCierre"],
                        "Banderas": flags_esp
                    })
                else:
                    filas_historicas_discrepantes.append(detalle_item)
            else:
                colisiones_duplicados_post_corte.append(detalle_item)

        elif en_aha and not en_ri:
            cant_aha = mapa_aha[clave]["CantidadIngresos"]
            flags_alm = {
                "EsHorarioAtipico": mapa_aha[clave]["EsHorarioAtipico"],
                "EsCierreAgosto": mapa_aha[clave]["EsCierreAgosto"],
                "EsFranjaParcial": mapa_aha[clave]["EsFranjaParcial"],
                "EsFeriado": mapa_aha[clave]["EsFeriado"],
                "EsCierre27Julio": mapa_aha[clave]["EsCierre27Julio"]
            }
            flags_esp_calculadas = calcular_banderas_analiticas(f_str, h_int)
            flags_esp = {
                "EsHorarioAtipico": flags_esp_calculadas["EsHorarioAtipico"],
                "EsCierreAgosto": flags_esp_calculadas["EsCierreAgosto"],
                "EsFranjaParcial": flags_esp_calculadas["EsFranjaParcial"],
                "EsFeriado": flags_esp_calculadas["EsFeriado"],
                "EsCierre27Julio": flags_esp_calculadas["EsCierre27Julio"]
            }
            diff_flags = {
                k: {"esperado": flags_esp[k], "almacenado": flags_alm[k]}
                for k in flags_esp if flags_esp[k] != flags_alm[k]
            }

            item_solo_aha = {
                "Fecha": f_str,
                "Hora": h_int,
                "Sede": s_str,
                "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
                "CantidadEnAHA": cant_aha,
                "BanderasAlmacenadas": flags_alm,
                "BanderasEsperadas": flags_esp,
                "DiscrepanciasBanderas": diff_flags
            }

            if not es_posterior:
                filas_solo_en_aha_historicas.append(item_solo_aha)
            else:
                filas_solo_en_aha_post_corte.append(item_solo_aha)

        elif en_ri and not en_aha:
            if not es_posterior:
                filas_solo_en_ri_historicas.append({
                    "Fecha": f_str,
                    "Hora": h_int,
                    "Sede": s_str,
                    "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
                    "CantidadEnRI": mapa_ri[clave]["CantidadIngresos"],
                    "IngresosValidos": mapa_ri[clave]["IngresosValidos"],
                    "IngresosPostCierre": mapa_ri[clave]["IngresosPostCierre"],
                    "Banderas": {
                        "EsHorarioAtipico": mapa_ri[clave]["EsHorarioAtipico"],
                        "EsCierreAgosto": mapa_ri[clave]["EsCierreAgosto"],
                        "EsFranjaParcial": mapa_ri[clave]["EsFranjaParcial"],
                        "EsFeriado": mapa_ri[clave]["EsFeriado"],
                        "EsCierre27Julio": mapa_ri[clave]["EsCierre27Julio"],
                        "EsCierre24Julio": mapa_ri[clave]["EsCierre24Julio"]
                    }
                })
            else:
                # Omitir marcas temporales exactas individuales por privacidad
                filas_nuevas_post_corte.append({
                    "Fecha": f_str,
                    "Hora": h_int,
                    "Sede": s_str,
                    "EsSedeDesconocida": bool(s_str == SEDE_DESCONOCIDA),
                    "CantidadIngresos": mapa_ri[clave]["CantidadIngresos"],
                    "IngresosValidos": mapa_ri[clave]["IngresosValidos"],
                    "IngresosPostCierre": mapa_ri[clave]["IngresosPostCierre"],
                    "CantidadHora20Valida": mapa_ri[clave]["CantidadHora20Valida"],
                    "CantidadHora20PostCierre": mapa_ri[clave]["CantidadHora20PostCierre"],
                    "EsHorarioAtipico": mapa_ri[clave]["EsHorarioAtipico"],
                    "EsCierreAgosto": mapa_ri[clave]["EsCierreAgosto"],
                    "EsFranjaParcial": mapa_ri[clave]["EsFranjaParcial"],
                    "EsFeriado": mapa_ri[clave]["EsFeriado"],
                    "EsCierre27Julio": mapa_ri[clave]["EsCierre27Julio"],
                    "EsCierre24Julio": mapa_ri[clave]["EsCierre24Julio"],
                    "EstadoFranja20h": mapa_ri[clave]["EstadoFranja20h"]
                })

    # Resumen cuantitativo por Sede para filas nuevas post-corte
    resumen_sedes_nuevas: Dict[str, Dict[str, int]] = {}
    filas_desconocidas_post = 0
    ingresos_desconocidos_post = 0

    for f in filas_nuevas_post_corte:
        s = f["Sede"]
        if s not in resumen_sedes_nuevas:
            resumen_sedes_nuevas[s] = {
                "filas": 0,
                "ingresos": 0,
                "ingresos_validos": 0,
                "ingresos_post_cierre": 0
            }
        resumen_sedes_nuevas[s]["filas"] += 1
        resumen_sedes_nuevas[s]["ingresos"] += f["CantidadIngresos"]
        resumen_sedes_nuevas[s]["ingresos_validos"] += f["IngresosValidos"]
        resumen_sedes_nuevas[s]["ingresos_post_cierre"] += f["IngresosPostCierre"]

        if s == SEDE_DESCONOCIDA:
            filas_desconocidas_post += 1
            ingresos_desconocidos_post += f["CantidadIngresos"]

    total_ingresos_nuevos = sum(f["CantidadIngresos"] for f in filas_nuevas_post_corte)
    total_validos_nuevos = sum(f["IngresosValidos"] for f in filas_nuevas_post_corte)
    total_post_cierre_nuevos = sum(f["IngresosPostCierre"] for f in filas_nuevas_post_corte)

    cnt_atipicas = sum(1 for f in filas_nuevas_post_corte if f["EsHorarioAtipico"] == 1)
    cnt_parciales = sum(1 for f in filas_nuevas_post_corte if f["EsFranjaParcial"] == 1)
    cnt_feriados = sum(1 for f in filas_nuevas_post_corte if f["EsFeriado"] == 1)

    min_fecha_post = min((f["Fecha"] for f in filas_nuevas_post_corte), default=None)
    max_fecha_post = max((f["Fecha"] for f in filas_nuevas_post_corte), default=None)

    # Separación analítica de discrepancias: cantidad vs banderas
    disc_cantidad = [d for d in filas_historicas_discrepantes if d["TieneDiscrepanciaCantidad"]]
    disc_banderas = [d for d in filas_historicas_discrepantes if d["TieneDiscrepanciaBanderas"]]
    disc_solo_cantidad = [d for d in filas_historicas_discrepantes if d["TieneDiscrepanciaCantidad"] and not d["TieneDiscrepanciaBanderas"]]
    disc_solo_banderas = [d for d in filas_historicas_discrepantes if d["TieneDiscrepanciaBanderas"] and not d["TieneDiscrepanciaCantidad"]]
    disc_mixtas = [d for d in filas_historicas_discrepantes if d["TieneDiscrepanciaCantidad"] and d["TieneDiscrepanciaBanderas"]]

    # Diagnósticos dinámicos calculados desde la consulta
    if not filas_historicas_discrepantes and not filas_solo_en_aha_historicas and not filas_solo_en_ri_historicas:
        diag_historico = f"Todas las {len(filas_historicas_coincidentes)} franjas evaluadas en el periodo histórico coinciden exactamente en cantidad y banderas."
    else:
        partes = [f"El periodo histórico presenta {len(filas_historicas_coincidentes)} coincidencias exactas"]
        if filas_historicas_discrepantes:
            partes.append(
                f"{len(filas_historicas_discrepantes)} discrepancias ({len(disc_cantidad)} de cantidad, {len(disc_banderas)} de banderas)"
            )
        if filas_solo_en_aha_historicas:
            partes.append(f"{len(filas_solo_en_aha_historicas)} filas presentes únicamente en AfluenciaHistoricaAgregada")
        if filas_solo_en_ri_historicas:
            partes.append(f"{len(filas_solo_en_ri_historicas)} filas presentes únicamente en RegistroIngresos")
        diag_historico = ", ".join(partes) + "."

    if not colisiones_duplicados_post_corte and not filas_solo_en_aha_post_corte:
        diag_post = (
            f"Esta lectura detectó {len(filas_nuevas_post_corte)} filas nuevas en RegistroIngresos posteriores al corte. "
            "No se detectaron claves preexistentes en AfluenciaHistoricaAgregada para dicho periodo."
        )
    else:
        diag_post = (
            f"Esta lectura detectó {len(filas_nuevas_post_corte)} filas nuevas en RegistroIngresos, "
            f"{len(colisiones_duplicados_post_corte)} claves ya existentes en AfluenciaHistoricaAgregada que colisionan con los registros, y "
            f"{len(filas_solo_en_aha_post_corte)} filas presentes exclusivamente en AfluenciaHistoricaAgregada posteriores al corte."
        )

    # Conteo segregado de Sede Desconocida
    ri_ingresos_desconocida = sum(v["CantidadIngresos"] for v in mapa_ri.values() if v["Sede"] == SEDE_DESCONOCIDA)
    ri_grupos_desconocida = sum(1 for v in mapa_ri.values() if v["Sede"] == SEDE_DESCONOCIDA)
    aha_ingresos_desconocida = sum(v["CantidadIngresos"] for v in mapa_aha.values() if v["Sede"] == SEDE_DESCONOCIDA)
    aha_filas_desconocida = sum(1 for v in mapa_aha.values() if v["Sede"] == SEDE_DESCONOCIDA)

    return {
        "status": "success",
        "tipo_operacion": "vista_previa_solo_lectura",
        "timestamp_consulta": timestamp_ejecucion,
        "fecha_corte_referencia": fecha_corte,
        "filtro_sede_aplicado": sede_filtro if sede_filtro else "Todas",
        "reglas_aplicadas": {
            "exclusion_pruebas": "ISNULL(EsPrueba, 0) = 0",
            "criterio_sede_desconocida": "Los registros con Sede NULL o en blanco se clasifican de forma separada como 'Desconocida' y se excluyen de los totales de cualquier sede, incluida Central.",
            "nivel_agregacion": "Fecha, Hora, Sede (consolidación de salas por sede)",
            "justificacion_sala": "La tabla AfluenciaHistoricaAgregada no contiene columna SalaID; las consultas agregan los ingresos a nivel sede (Fecha, Hora, Sede).",
            "horario_regular": "08:00:00 a 20:45:00 (lunes a viernes)",
            "cierre_operativo_limite": "20:45:00 exacto. Ingresos con marcas temporales > 20:45:00 (incluidos 20:45:00.001 y 20:45:59.999) son atípicos (EsHorarioAtipico = 1).",
            "franja_parcial": "Hora 20 (20:00:00 a 20:45:00) con EsFranjaParcial = 1. Si coexisten con ingresos > 20:45:00 se conservan ambos conteos por separado.",
            "horario_atipico": "Horas < 8 o > 20, fines de semana (sábado/domingo) o ingresos pasadas las 20:45:00. Las vacaciones (agosto), feriados y puentes se evalúan de forma ortogonal en sus propias banderas.",
            "feriados_confirmados": list(sorted(FERIADOS_CONFIRMADOS_2026)),
            "cierre_24_julio": {
                "fecha": CIERRE_CONFIGURADO_24_JULIO,
                "estado": "regla_configurada_pendiente_confirmacion_documental",
                "descripcion": "Puente FAP configurado en el sistema; pendiente de confirmación documental institucional."
            },
            "cierre_27_julio": {
                "fecha": CIERRE_CONFIRMADO_27_JULIO,
                "estado": "cierre_institucional_confirmado",
                "descripcion": "Cierre institucional puente Fiestas Patrias."
            }
        },
        "resumen_conteos_base": {
            "sede_consultada": sede_filtro if sede_filtro else "Todas",
            "RegistroIngresos_total_grupos": len(mapa_ri),
            "RegistroIngresos_total_ingresos_reales": total_ingresos_ri,
            "AfluenciaHistoricaAgregada_total_filas": len(mapa_aha),
            "AfluenciaHistoricaAgregada_total_ingresos": total_ingresos_aha,
            "sede_desconocida_segregada": {
                "etiqueta": SEDE_DESCONOCIDA,
                "criterio": "Registros con Sede NULL clasificados por separado y excluidos de totales de sedes con nombre.",
                "RegistroIngresos_grupos": ri_grupos_desconocida,
                "RegistroIngresos_ingresos": ri_ingresos_desconocida,
                "AfluenciaHistoricaAgregada_filas": aha_filas_desconocida,
                "AfluenciaHistoricaAgregada_ingresos": aha_ingresos_desconocida
            }
        },
        "reconciliacion_periodo_historico": {
            "periodo": f"Inicio hasta {fecha_corte}",
            "coincidencias_exactas_filas": len(filas_historicas_coincidentes),
            "discrepancias_filas": len(filas_historicas_discrepantes),
            "resumen_discrepancias": {
                "total_discrepancias": len(filas_historicas_discrepantes),
                "discrepancias_cantidad": len(disc_cantidad),
                "discrepancias_solo_cantidad": len(disc_solo_cantidad),
                "discrepancias_banderas": len(disc_banderas),
                "discrepancias_solo_banderas": len(disc_solo_banderas),
                "discrepancias_mixtas_cantidad_y_banderas": len(disc_mixtas)
            },
            "detalle_discrepancias": filas_historicas_discrepantes,
            "detalle_discrepancias_cantidad": disc_cantidad,
            "detalle_discrepancias_banderas": disc_banderas,
            "filas_solo_en_AfluenciaHistoricaAgregada": len(filas_solo_en_aha_historicas),
            "detalle_solo_en_AHA": filas_solo_en_aha_historicas,
            "filas_solo_en_RegistroIngresos": len(filas_solo_en_ri_historicas),
            "detalle_solo_en_RI": filas_solo_en_ri_historicas,
            "diagnostico_historico": diag_historico
        },
        "proyeccion_periodo_posterior": {
            "periodo": f"Posterior a {fecha_corte} hasta {max_fecha_post or 'N/D'}",
            "filas_nuevas_esperadas": len(filas_nuevas_post_corte),
            "detalle_filas_nuevas": filas_nuevas_post_corte,
            "total_ingresos_nuevos_esperados": total_ingresos_nuevos,
            "total_ingresos_nuevos_validos": total_validos_nuevos,
            "total_ingresos_nuevos_post_cierre": total_post_cierre_nuevos,
            "rango_fechas": {
                "fecha_inicio": min_fecha_post,
                "fecha_fin": max_fecha_post
            },
            "desglose_por_sede": resumen_sedes_nuevas,
            "sede_desconocida": {
                "filas_nuevas": filas_desconocidas_post,
                "ingresos_nuevos": ingresos_desconocidos_post
            },
            "clasificacion_banderas": {
                "filas_horario_estandar": len(filas_nuevas_post_corte) - cnt_atipicas,
                "filas_horario_atipico": cnt_atipicas,
                "filas_franja_parcial_20h": cnt_parciales,
                "filas_feriado": cnt_feriados
            },
            "analisis_claves_existentes": {
                "claves_existentes_en_aha_post_corte": len(colisiones_duplicados_post_corte),
                "detalle_claves_existentes": colisiones_duplicados_post_corte,
                "filas_solo_en_aha_post_corte": len(filas_solo_en_aha_post_corte),
                "detalle_solo_en_aha_post_corte": filas_solo_en_aha_post_corte,
                "diagnostico_claves_post": diag_post
            }
        },
        "muestra_filas_nuevas_primeras_y_ultimas": {
            "primeras_5": filas_nuevas_post_corte[:5],
            "ultimas_5": filas_nuevas_post_corte[-5:]
        }
    }


if __name__ == "__main__":
    resultado = obtener_vista_previa_historial_afluencia()
    import json
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
