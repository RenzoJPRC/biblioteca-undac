"""
Módulo de Análisis Individual y Predicción de Retorno de Alumnos (ML)
Biblioteca Central UNDAC

Proporciona:
1. Análisis descriptivo de visitas por mes, escuela/carrera, sala, hora y frecuencia de uso por alumno.
2. Predicción inferencial de retorno ("volver mañana" / siguiente día hábil operativo)
   con validación cronológica estricta (sin fuga de datos futuros) y umbral de suficiencia
   estadística (mínimo 3 días activos). Si no alcanza el umbral, devuelve 'historial insuficiente'.
3. Garantías de integridad y privacidad:
   - Filtra estrictamente ISNULL(EsPrueba, 0) = 0 (38 registros de prueba excluidos).
   - NUNCA consulta, procesa ni expone el DNI ni nombres completos de los alumnos.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import copy
import json
import os
import time
import joblib
import pandas as pd
import numpy as np

from db import get_db_cursor

# =============================================================================
# CALENDARIO INSTITUCIONAL Y HORARIOS DE OPERACIÓN CONFIRMADOS (UNDAC)
# =============================================================================
# Horario oficial de atención: Lunes a viernes de 08:00 a 20:45 horas.
# Sábados y domingos: Biblioteca cerrada.
# Feriados nacionales oficiales (D. Leg. 713 y modificatorias) y cierres confirmados:
FERIADOS_CONFIRMADOS_2026 = {
    date(2026, 1, 1),   # Año Nuevo
    date(2026, 4, 2),   # Jueves Santo
    date(2026, 4, 3),   # Viernes Santo
    date(2026, 5, 1),   # Día del Trabajo
    date(2026, 6, 7),   # Batalla de Arica y Día de la Bandera (domingo)
    date(2026, 6, 29),  # San Pedro y San Pablo (lunes)
    date(2026, 7, 23),  # Día de la Fuerza Aérea del Perú (jueves)
    date(2026, 7, 24),  # Cierre no laborable puente FAP (viernes)
    date(2026, 7, 27),  # Cierre institucional 27 de julio (lunes)
    date(2026, 7, 28),  # Fiestas Patrias (martes)
    date(2026, 7, 29),  # Fiestas Patrias (miércoles)
    date(2026, 8, 6),   # Batalla de Junín (jueves, dentro de receso de agosto)
    date(2026, 8, 30),  # Santa Rosa de Lima (domingo)
    date(2026, 10, 8),  # Combate de Angamos (jueves)
    date(2026, 11, 1),  # Todos los Santos (domingo)
    date(2026, 12, 8),  # Inmaculada Concepción (martes)
    date(2026, 12, 9),  # Batalla de Ayacucho (miércoles)
    date(2026, 12, 25), # Navidad (viernes)
}

# Receso institucional de medio año (vacaciones intersemestrales):
# Desde el 01/08/2026 hasta el 23/08/2026 inclusive. Retorno a la atención: lunes 24/08/2026.
RECESO_AGOSTO_INICIO = date(2026, 8, 1)
RECESO_AGOSTO_FIN = date(2026, 8, 23)
RECESO_AGOSTO_RETORNO = date(2026, 8, 24)

DIAS_RECESO_AGOSTO = {
    date(2026, 8, dia) for dia in range(1, 24)
}

# Conjunto unificado de días no operativos confirmados (feriados oficiales + receso intersemestral)
DIAS_FERIADOS_O_CIERRES = FERIADOS_CONFIRMADOS_2026 | DIAS_RECESO_AGOSTO

# =============================================================================
# DEFINICIÓN INMUTABLE DEL ARTEFACTO DE PRODUCCIÓN (HistGradientBoosting v1.0.0)
# =============================================================================
# Fecha máxima de entrenamiento y evaluación con la que fue generado el artefacto activo:
FECHA_MINIMA_DATOS = date(2026, 6, 1)
FECHA_ENTRENAMIENTO_ARTEFACTO = date(2026, 7, 31)
FECHA_MAXIMA_ENTRENAMIENTO = FECHA_ENTRENAMIENTO_ARTEFACTO

# Alias de compatibilidad hacia atrás:
FECHA_MAXIMA_DATOS = FECHA_ENTRENAMIENTO_ARTEFACTO


def enmascarar_codigo(codigo):
    """
    Enmascara el código de matrícula para presentación en reportes o LLM
    sin revelar identificadores en texto plano (ej: 2021123456 -> 20******56).
    """
    if not codigo:
        return "N/D"
    c = str(codigo).strip()
    if len(c) <= 3:
        return "***"
    elif len(c) <= 6:
        return c[0] + "*" * (len(c) - 2) + c[-1]
    else:
        return c[:2] + "*" * (len(c) - 4) + c[-2:]


def calcular_proximo_dia_operativo(fecha_referencia, calendario_operativo=None):
    """
    Define operativamente qué significa "volver mañana":
    En la biblioteca central que opera de lunes a viernes (08:00 a 20:45), "volver mañana"
    corresponde al siguiente día hábil operativo disponible:
    - Si hoy es jueves -> viernes.
    - Si hoy es viernes -> lunes siguiente (salta sábado y domingo).
    - Salta feriados confirmados (ej: 29/06, 23/07, 24/07, 27-29/07, 08/10 Combate de Angamos, etc.).
    - Salta el receso intersemestral de agosto (01/08 al 23/08 inclusive), retornando el 24/08.
    """
    if isinstance(fecha_referencia, str):
        fecha_referencia = datetime.strptime(fecha_referencia.strip(), "%Y-%m-%d").date()
    elif isinstance(fecha_referencia, datetime):
        fecha_referencia = fecha_referencia.date()

    if calendario_operativo:
        fechas_futuras = sorted([
            d for d in calendario_operativo
            if d > fecha_referencia and d not in DIAS_FERIADOS_O_CIERRES and d.weekday() < 5
        ])
        if fechas_futuras:
            return fechas_futuras[0]

    candidato = fecha_referencia + timedelta(days=1)
    while True:
        # 5: sábado, 6: domingo
        if candidato.weekday() < 5 and candidato not in DIAS_FERIADOS_O_CIERRES:
            return candidato
        candidato += timedelta(days=1)


TTL_CACHE_DESCRIPTIVO_SEGUNDOS = int(os.getenv("ML_CACHE_TTL_DESCRIPTIVO", "300"))
_CACHE_ANALISIS_DESCRIPTIVO = {}


def _ahora_descriptivo():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Lima"))


def _periodo_descriptivo(fecha_inicio=None, fecha_fin=None):
    """Días calendario terminados; no presupone que la carga de datos sea completa."""
    def convertir(valor):
        if isinstance(valor, datetime):
            return valor.date()
        return date.fromisoformat(valor.strip()) if isinstance(valor, str) else valor

    inicio = max(FECHA_MINIMA_DATOS, convertir(fecha_inicio)) if fecha_inicio else FECHA_MINIMA_DATOS
    limite = _ahora_descriptivo().date() - timedelta(days=1)
    fin = min(limite, convertir(fecha_fin)) if fecha_fin else limite
    if inicio > fin:
        raise ValueError("El inicio del periodo debe ser anterior o igual al último día completo permitido.")
    return inicio, fin


def _clave_cache_descriptivo(fecha_inicio, fecha_fin, sede, mostrar_codigo_completo=False):
    inicio, fin = _periodo_descriptivo(fecha_inicio, fecha_fin)
    return (inicio.isoformat(), fin.isoformat(), str(sede) if sede else "Central", bool(mostrar_codigo_completo))


def limpiar_cache_analisis_descriptivo(sede=None):
    """
    Limpia la caché en memoria de análisis descriptivo.
    Si se especifica sede, invalida solo las entradas de esa sede.
    Si sede es None, invalida toda la caché.
    """
    global _CACHE_ANALISIS_DESCRIPTIVO
    if sede is None:
        _CACHE_ANALISIS_DESCRIPTIVO.clear()
    else:
        sede_str = str(sede).strip()
        claves_a_borrar = [k for k in _CACHE_ANALISIS_DESCRIPTIVO if k[2] == sede_str]
        for k in claves_a_borrar:
            _CACHE_ANALISIS_DESCRIPTIVO.pop(k, None)


def obtener_estado_cache_descriptivo(fecha_inicio=None, fecha_fin=None, sede="Central", mostrar_codigo_completo=False):
    """
    Devuelve metadatos de diagnóstico sobre una entrada en la caché de análisis descriptivo.
    """
    clave = _clave_cache_descriptivo(fecha_inicio, fecha_fin, sede, mostrar_codigo_completo)
    entrada = _CACHE_ANALISIS_DESCRIPTIVO.get(clave)
    if not entrada:
        return {
            "sede": sede,
            "en_cache": False,
            "edad_segundos": None,
            "segundos_restantes": 0,
            "ttl_segundos": TTL_CACHE_DESCRIPTIVO_SEGUNDOS,
            "expirada": True
        }
    edad = time.time() - entrada.get("timestamp", 0)
    segundos_restantes = max(0.0, round(TTL_CACHE_DESCRIPTIVO_SEGUNDOS - edad, 2))
    return {
        "sede": sede,
        "en_cache": True,
        "edad_segundos": round(edad, 2),
        "segundos_restantes": segundos_restantes,
        "ttl_segundos": TTL_CACHE_DESCRIPTIVO_SEGUNDOS,
        "expirada": edad >= TTL_CACHE_DESCRIPTIVO_SEGUNDOS
    }


def obtener_analisis_descriptivo_ingresos(fecha_inicio=None, fecha_fin=None, sede="Central", forzar_refresco=False, mostrar_codigo_completo=False):
    """
    Genera el análisis descriptivo integral de ingresos reales por alumno:
    - Visitas por mes desde junio de 2026 hasta el último día terminado disponible.
    - Visitas por escuela académica / carrera profesional.
    - Visitas por sala y piso de biblioteca.
    - Visitas por hora de atención (08:00 a 20:45, marcando la franja parcial de las 20:00).
    - Frecuencia y recurrencia por alumno (media, mediana, percentiles, alumnos recurrentes vs ocasionales).

    Reglas de seguridad y datos:
    - Excluye siempre registros de prueba: ISNULL(EsPrueba, 0) = 0.
    - NUNCA consulta la columna DNI.
    """
    f_ini, f_fin = _periodo_descriptivo(fecha_inicio, fecha_fin)
    # No compartir una respuesta con códigos completos con los consumidores enmascarados.
    clave_cache = (f_ini.isoformat(), f_fin.isoformat(), str(sede) if sede else "Central", bool(mostrar_codigo_completo))

    ahora = time.time()
    if not forzar_refresco and clave_cache in _CACHE_ANALISIS_DESCRIPTIVO:
        entrada = _CACHE_ANALISIS_DESCRIPTIVO[clave_cache]
        if ahora - entrada.get("timestamp", 0) < TTL_CACHE_DESCRIPTIVO_SEGUNDOS:
            return copy.deepcopy(entrada.get("datos", {}))

    filtros = [
        "ISNULL(R.EsPrueba, 0) = 0",
        "R.AlumnoID IS NOT NULL",
        "R.FechaHora >= ?",
        "R.FechaHora < ?"
    ]
    params = [f_ini.isoformat(), (f_fin + timedelta(days=1)).isoformat()]

    if sede and sede != "Todas":
        filtros.append("ISNULL(R.Sede, 'Central') = ?")
        params.append(sede)

    where_sql = " AND ".join(filtros)

    sql = f"""
        SELECT
            R.RegistroID,
            R.AlumnoID,
            ISNULL(A.Escuela, 'Sin Escuela') AS Escuela,
            ISNULL(S.NombreSala, 'Sin Sala') AS NombreSala,
            ISNULL(S.Piso, R.Piso) AS Piso,
            R.FechaHora,
            DATEPART(HOUR, R.FechaHora) AS Hora,
            CONVERT(VARCHAR(7), R.FechaHora, 120) AS Mes,
            ISNULL(A.CodigoMatricula, 'N/D') AS CodigoMatricula
        FROM dbo.RegistroIngresos R
        INNER JOIN dbo.Alumnos A ON R.AlumnoID = A.AlumnoID
        LEFT JOIN dbo.Salas S ON R.SalaID = S.SalaID
        WHERE {where_sql}
        ORDER BY R.FechaHora ASC
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")
        cursor.execute(sql, tuple(params))
        filas = cursor.fetchall()

    ultima_fecha = max((r[5].date() for r in filas), default=None)
    metadatos = {
        "sede": sede,
        "tipo_analisis": "estadisticas_descriptivas",
        "actualizado_en": _ahora_descriptivo().isoformat(timespec="seconds"),
        "fecha_ultimo_dato_completo": ultima_fecha.isoformat() if ultima_fecha else None,
        "periodo_analizado": {
            "fecha_inicio": f_ini.isoformat(),
            "fecha_fin": ultima_fecha.isoformat() if ultima_fecha else None,
            "fecha_limite_consulta": f_fin.isoformat(),
            "total_fechas_operativas": len({r[5].date() for r in filas}),
        },
        "criterio_dia_completo": "Día calendario terminado en America/Lima con ingresos válidos disponibles; no certifica completitud de importaciones.",
    }

    if not filas:
        res_vacio = {
            **metadatos,
            "status": "success",
            "total_visitas": 0,
            "total_alumnos": 0,
            "mensaje": "No se encontraron registros de visitas reales en el rango solicitado.",
            "visitas_por_mes": [],
            "visitas_por_escuela": [],
            "visitas_por_sala": [],
            "visitas_por_piso": [],
            "total_dias_distintos": 0,
            "visitas_por_hora": [],
            "visitas_por_hora_atipica": [],
            "total_visitas_horario_operativo": 0,
            "total_visitas_horario_atipico": 0,
            "frecuencia_por_alumno": {},
            "ranking_alumnos_por_visitas": [],
            "ranking_alumnos_por_dias": []
        }
        _CACHE_ANALISIS_DESCRIPTIVO[clave_cache] = {
            "timestamp": ahora,
            "datos": res_vacio
        }
        return copy.deepcopy(res_vacio)

    total_visitas = len(filas)

    # 1. Visitas por Mes
    conteo_mes = {}
    alumnos_mes = {}
    for r in filas:
        mes = r[7]
        conteo_mes[mes] = conteo_mes.get(mes, 0) + 1
        if mes not in alumnos_mes:
            alumnos_mes[mes] = set()
        alumnos_mes[mes].add(r[1])

    visitas_por_mes = [
        {
            "mes": mes,
            "total_visitas": cnt,
            "alumnos_unicos": len(alumnos_mes[mes]),
            "porcentaje": round((cnt / total_visitas) * 100, 2)
        }
        for mes, cnt in sorted(conteo_mes.items())
    ]

    # 2. Visitas por Escuela / Carrera
    conteo_escuela = {}
    alumnos_escuela = {}
    for r in filas:
        esc = r[2]
        conteo_escuela[esc] = conteo_escuela.get(esc, 0) + 1
        if esc not in alumnos_escuela:
            alumnos_escuela[esc] = set()
        alumnos_escuela[esc].add(r[1])

    visitas_por_escuela = sorted(
        [
            {
                "escuela": esc,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_escuela[esc]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for esc, cnt in conteo_escuela.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    # 3. Visitas por Sala y Piso
    conteo_sala = {}
    alumnos_sala = {}
    conteo_piso = {}
    alumnos_piso = {}
    for r in filas:
        clave_sala = (r[3], r[4])
        conteo_sala[clave_sala] = conteo_sala.get(clave_sala, 0) + 1
        if clave_sala not in alumnos_sala:
            alumnos_sala[clave_sala] = set()
        alumnos_sala[clave_sala].add(r[1])

        piso = r[4]
        conteo_piso[piso] = conteo_piso.get(piso, 0) + 1
        if piso not in alumnos_piso:
            alumnos_piso[piso] = set()
        alumnos_piso[piso].add(r[1])

    visitas_por_sala = sorted(
        [
            {
                "nombre_sala": s_nombre,
                "piso": s_piso,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_sala[(s_nombre, s_piso)]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for (s_nombre, s_piso), cnt in conteo_sala.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    visitas_por_piso = sorted(
        [
            {
                "piso": p,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_piso[p]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for p, cnt in conteo_piso.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    resumen_pisos_configurados = {
        "pisos_con_visitas": list(conteo_piso),
        "salas_con_visitas": len(visitas_por_sala),
        "aclaracion": "Distribución observada en los ingresos del periodo seleccionado; no representa el inventario de salas configuradas."
    }

    # 4. Visitas por Hora (08:00 a 20:00)
    conteo_hora = {}
    conteo_hora_atipica = {}
    for r in filas:
        h = r[6]
        # Comparar la hora completa, incluidos segundos y fracciones, sin eliminar visitas.
        hora_real = r[5].time()
        if datetime.min.replace(hour=8).time() <= hora_real <= datetime.min.replace(hour=20, minute=45).time():
            conteo_hora[h] = conteo_hora.get(h, 0) + 1
        else:
            conteo_hora_atipica[h] = conteo_hora_atipica.get(h, 0) + 1

    visitas_por_hora = [
        {
            "hora": h,
            "hora_formato": f"{h:02d}:00 - {(h+1) % 24:02d}:00" if h != 20 else "20:00 - 20:45 (parcial)",
            "es_parcial": (h == 20),
            "es_atipica": False,
            "total_visitas": conteo_hora.get(h, 0),
            "porcentaje": round((conteo_hora.get(h, 0) / total_visitas) * 100, 2)
        }
        for h in range(8, 21)
    ]
    visitas_por_hora_atipica = [
        {
            "hora": h,
            "hora_formato": f"{h:02d}:00 - {(h+1) % 24:02d}:00" if h != 20 else "Después de 20:45 hasta 21:00",
            "es_atipica": True,
            "motivo": "Fuera del horario operativo de 08:00 a 20:45",
            "total_visitas": cantidad,
            "porcentaje": round(cantidad * 100 / total_visitas, 2),
        }
        for h, cantidad in sorted(conteo_hora_atipica.items())
    ]

    # 5. Frecuencia y Recurrencia por Alumno
    alumno_visitas = {}
    alumno_dias = {}
    alumno_escuelas = {}
    alumno_codigos = {}
    alumno_codigos_completos = {}
    for r in filas:
        al_id = r[1]
        f_dia = r[5].date()
        alumno_visitas[al_id] = alumno_visitas.get(al_id, 0) + 1
        if al_id not in alumno_dias:
            alumno_dias[al_id] = set()
        alumno_dias[al_id].add(f_dia)
        alumno_escuelas[al_id] = r[2]
        if al_id not in alumno_codigos:
            alumno_codigos[al_id] = enmascarar_codigo(r[8])
            if mostrar_codigo_completo:
                alumno_codigos_completos[al_id] = str(r[8])

    ranking_alumnos_por_visitas = sorted(
        [
            {
                "codigo_mascarado": alumno_codigos[al_id],
                **({"codigo_matricula": alumno_codigos_completos[al_id]} if mostrar_codigo_completo else {}),
                "escuela": alumno_escuelas[al_id],
                "total_visitas": alumno_visitas[al_id],
                "dias_distintos": len(alumno_dias[al_id]),
                "dias_activos": len(alumno_dias[al_id])
            }
            for al_id in alumno_visitas
        ],
        key=lambda x: (x["total_visitas"], x["dias_distintos"]),
        reverse=True
    )[:10]
    for idx, r_item in enumerate(ranking_alumnos_por_visitas, 1):
        r_item["ranking"] = idx

    ranking_alumnos_por_dias = sorted(
        [
            {
                "codigo_mascarado": alumno_codigos[al_id],
                **({"codigo_matricula": alumno_codigos_completos[al_id]} if mostrar_codigo_completo else {}),
                "escuela": alumno_escuelas[al_id],
                "dias_distintos": len(alumno_dias[al_id]),
                "dias_activos": len(alumno_dias[al_id]),
                "total_visitas": alumno_visitas[al_id]
            }
            for al_id in alumno_visitas
        ],
        key=lambda x: (x["dias_distintos"], x["total_visitas"]),
        reverse=True
    )[:10]
    for idx, r_item in enumerate(ranking_alumnos_por_dias, 1):
        r_item["ranking"] = idx

    total_alumnos_unicos = len(alumno_visitas)
    arr_visitas = list(alumno_visitas.values())
    arr_dias = [len(s) for s in alumno_dias.values()]

    cnt_1_dia = sum(1 for d in arr_dias if d == 1)
    cnt_2_dias = sum(1 for d in arr_dias if d == 2)
    cnt_3_a_5_dias = sum(1 for d in arr_dias if 3 <= d <= 5)
    cnt_6_a_10_dias = sum(1 for d in arr_dias if 6 <= d <= 10)
    cnt_mas_10_dias = sum(1 for d in arr_dias if d > 10)

    alumnos_suficientes = sum(1 for d in arr_dias if d >= 3)
    alumnos_insuficientes = sum(1 for d in arr_dias if d < 3)

    frecuencia_por_alumno = {
        "total_alumnos_unicos": total_alumnos_unicos,
        "promedio_visitas": round(float(np.mean(arr_visitas)), 2),
        "mediana_visitas": round(float(np.median(arr_visitas)), 2),
        "max_visitas": int(max(arr_visitas)),
        "min_visitas": int(min(arr_visitas)),
        "promedio_dias_activos": round(float(np.mean(arr_dias)), 2),
        "distribucion_dias_activos": {
            "1_dia": {"cantidad": cnt_1_dia, "porcentaje": round(cnt_1_dia * 100 / total_alumnos_unicos, 2)},
            "2_dias": {"cantidad": cnt_2_dias, "porcentaje": round(cnt_2_dias * 100 / total_alumnos_unicos, 2)},
            "3_a_5_dias": {"cantidad": cnt_3_a_5_dias, "porcentaje": round(cnt_3_a_5_dias * 100 / total_alumnos_unicos, 2)},
            "6_a_10_dias": {"cantidad": cnt_6_a_10_dias, "porcentaje": round(cnt_6_a_10_dias * 100 / total_alumnos_unicos, 2)},
            "mas_de_10_dias": {"cantidad": cnt_mas_10_dias, "porcentaje": round(cnt_mas_10_dias * 100 / total_alumnos_unicos, 2)}
        },
        "resumen_elegibilidad_prediccion_ml": {
            "criterio_minimo": "Mínimo 3 días activos en el periodo para emitir probabilidad",
            "alumnos_con_historial_suficiente": {
                "cantidad": alumnos_suficientes,
                "porcentaje": round(alumnos_suficientes * 100 / total_alumnos_unicos, 2),
                "estado": "Aptos para estimación inferencial de probabilidad de retorno"
            },
            "alumnos_con_historial_insuficiente": {
                "cantidad": alumnos_insuficientes,
                "porcentaje": round(alumnos_insuficientes * 100 / total_alumnos_unicos, 2),
                "estado": "Clasificados como 'historial insuficiente' (visitas esporádicas de 1 o 2 días)"
            }
        }
    }

    resultado = {
        **metadatos,
        "status": "success",
        "fuente": "dbo.RegistroIngresos (EsPrueba = 0), dbo.Alumnos, dbo.Salas",
        "total_visitas": total_visitas,
        "total_alumnos": total_alumnos_unicos,
        "total_dias_distintos": len({r[5].date() for r in filas}),
        "visitas_por_mes": visitas_por_mes,
        "visitas_por_escuela": visitas_por_escuela,
        "visitas_por_sala": visitas_por_sala,
        "visitas_por_piso": visitas_por_piso,
        "resumen_pisos": resumen_pisos_configurados,
        "visitas_por_hora": visitas_por_hora,
        "visitas_por_hora_atipica": visitas_por_hora_atipica,
        "total_visitas_horario_operativo": sum(conteo_hora.values()),
        "total_visitas_horario_atipico": sum(conteo_hora_atipica.values()),
        "frecuencia_por_alumno": frecuencia_por_alumno,
        "ranking_alumnos_por_visitas": ranking_alumnos_por_visitas,
        "ranking_alumnos_por_dias": ranking_alumnos_por_dias
    }

    _CACHE_ANALISIS_DESCRIPTIVO[clave_cache] = {
        "timestamp": ahora,
        "datos": resultado
    }
    return copy.deepcopy(resultado)


class PredictorRetornoAlumno:
    """
    Servicio de predicción de retorno de un alumno con validación cronológica.
    Carga el modelo HistGradientBoosting entrenado sin fuga de información futura.
    """

    _instancia = None

    @classmethod
    def obtener_instancia(cls):
        if cls._instancia is None:
            cls._instancia = cls()
        return cls._instancia

    def __init__(self):
        ruta_ml = Path(__file__).resolve().parents[1]
        self.ruta_modelo = ruta_ml / "models" / "modelo_retorno_alumno.joblib"
        self.ruta_metadatos = ruta_ml / "models" / "metadatos_retorno_alumno.json"

        if not self.ruta_modelo.exists():
            raise FileNotFoundError(f"No se encontró el modelo de retorno en: {self.ruta_modelo}")

        paquete = joblib.load(self.ruta_modelo)
        self.modelo = paquete["modelo"]
        self.fecha_creacion_artefacto = paquete.get("fecha_entrenamiento")
        self.variables = paquete.get("variables", [
            'dias_activos', 'total_visitas', 'recencia_dias',
            'visitas_ultimos_7d', 'visitas_ultimos_14d',
            'afinidad_dia_semana', 'visito_hoy', 'tasa_asistencia'
        ])
        self.umbral_minimo = paquete.get("umbral_minimo_dias_activos", 3)

        self.metadatos = {}
        if self.ruta_metadatos.exists():
            try:
                with open(self.ruta_metadatos, "r", encoding="utf-8") as f:
                    self.metadatos = json.load(f)
            except Exception:
                self.metadatos = {}

    def predecir_retorno(self, codigo_matricula=None, alumno_id=None, fecha_referencia=None):
        """
        Calcula la probabilidad de retorno del alumno ("volver mañana" / siguiente día hábil):
        - Aplica validación cronológica estricta: sólo considera datos hasta fecha_referencia.
        - Excluye estrictamente registros de prueba (EsPrueba = 0).
        - NUNCA consulta, evalúa ni retorna el DNI.
        - Si el alumno tiene menos de 3 días activos previos, devuelve 'historial insuficiente'.
        """
        if not codigo_matricula and not alumno_id:
            raise ValueError("Debe proporcionar el código de matrícula o el identificador del alumno.")

        # Resolver fecha de referencia provista explícitamente por el usuario
        fecha_referencia_provista = False
        if fecha_referencia:
            if isinstance(fecha_referencia, (date, datetime)):
                f_ref = fecha_referencia.date() if isinstance(fecha_referencia, datetime) else fecha_referencia
                fecha_referencia_provista = True
            elif isinstance(fecha_referencia, str):
                fecha_str = fecha_referencia.strip()
                if fecha_str:
                    formatos = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]
                    f_ref = None
                    for fmt in formatos:
                        try:
                            f_ref = datetime.strptime(fecha_str, fmt).date()
                            fecha_referencia_provista = True
                            break
                        except ValueError:
                            continue
                    if f_ref is None:
                        raise ValueError(
                            f"La fecha de referencia '{fecha_str}' no tiene un formato válido. "
                            "Utilice el formato AAAA-MM-DD (ejemplo: 2026-07-31) o DD/MM/AAAA."
                        )
                else:
                    f_ref = None
            else:
                raise ValueError("La fecha de referencia proporcionada no es válida.")
        else:
            f_ref = None

        # 1. Identificar al alumno en la BD sin consultar DNI
        with get_db_cursor(autocommit=True) as (_, cursor):
            if cursor is None:
                raise ConnectionError("No fue posible conectarse a SQL Server.")

            # Consultar la última fecha de ingresos registrada en BD (excluyendo pruebas) una sola vez
            cursor.execute("""
                SELECT MAX(CAST(FechaHora AS DATE))
                FROM dbo.RegistroIngresos
                WHERE ISNULL(EsPrueba, 0) = 0
            """)
            row_max_bd = cursor.fetchone()
            ultima_fecha_bd = row_max_bd[0] if (row_max_bd and row_max_bd[0]) else None
            ultima_fecha_bd_str = ultima_fecha_bd.isoformat() if ultima_fecha_bd else None
            origen_fecha_ref = "seleccionada_por_usuario" if fecha_referencia_provista else "predeterminada_ultima_bd"

            # Si el usuario no seleccionó fecha, usar la última fecha real disponible en BD
            if not fecha_referencia_provista:
                if ultima_fecha_bd is None:
                    # Sin datos en BD y sin fecha seleccionada: devolver estado explícito sin inventar fecha
                    cod_mascarado = enmascarar_codigo(str(codigo_matricula or alumno_id))
                    return {
                        "status": "sin_datos_en_bd",
                        "confiable": False,
                        "codigo_mascarado": cod_mascarado,
                        "mensaje": (
                            "No se encontraron registros de ingresos en la base de datos para determinar "
                            "la fecha de referencia por defecto. Seleccione una fecha de referencia manualmente."
                        ),
                        "fecha_referencia": None,
                        "ultima_fecha_bd": None,
                        "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                        "es_experimental": False
                    }
                f_ref = ultima_fecha_bd

            if codigo_matricula:
                cursor.execute("""
                    SELECT AlumnoID, CodigoMatricula, ISNULL(Escuela, 'Sin Escuela') AS Escuela
                    FROM dbo.Alumnos
                    WHERE CodigoMatricula = ?
                """, (str(codigo_matricula).strip(),))
            else:
                cursor.execute("""
                    SELECT AlumnoID, CodigoMatricula, ISNULL(Escuela, 'Sin Escuela') AS Escuela
                    FROM dbo.Alumnos
                    WHERE AlumnoID = ?
                """, (int(alumno_id),))

            alumno_row = cursor.fetchone()
            if not alumno_row:
                return {
                    "status": "error",
                    "categoria_error": "no_encontrado",
                    "mensaje": "No se encontró ningún alumno con el identificador proporcionado."
                }

            id_alumno = int(alumno_row[0])
            cod_alumno = str(alumno_row[1]) if alumno_row[1] else "N/D"
            escuela_alumno = str(alumno_row[2])
            cod_mascarado = enmascarar_codigo(cod_alumno)

            # Si la fecha consultada es posterior al último registro en BD, no se puede calcular
            # una predicción porque faltan fechas intermedias con cobertura confirmada.
            if ultima_fecha_bd and f_ref > ultima_fecha_bd:
                dias_faltantes = (f_ref - ultima_fecha_bd).days
                return {
                    "status": "no_calculable",
                    "confiable": False,
                    "codigo_mascarado": cod_mascarado,
                    "escuela": escuela_alumno,
                    "fecha_referencia": f_ref.isoformat(),
                    "origen_fecha_referencia": origen_fecha_ref,
                    "fecha_corte_bd": ultima_fecha_bd_str,
                    "ultima_fecha_bd": ultima_fecha_bd_str,
                    "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                    "fecha_corte_entrenamiento": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                    "proximo_dia_operativo": None,
                    "horizonte_prediccion": "H1",
                    "es_experimental": True,
                    "tipo_prediccion": "no_calculable_sin_cobertura",
                    "estado_validacion": "sin_cobertura_confirmada",
                    "probabilidad_retorno": None,
                    "nivel_probabilidad": "No calculable",
                    "motivo": "cobertura_no_confirmada",
                    "mensaje": (
                        f"No calculable: La fecha de referencia consultada ({f_ref.strftime('%d/%m/%Y')}) es posterior "
                        f"al último día con registros en base de datos ({ultima_fecha_bd.strftime('%d/%m/%Y')}). "
                        f"Faltan {dias_faltantes} día(s) intermedio(s) sin cobertura confirmada."
                    ),
                    "explicacion": (
                        f"La base de datos cuenta con registros confirmados únicamente hasta el {ultima_fecha_bd.strftime('%d/%m/%Y')}. "
                        "No se puede calcular una predicción para una fecha posterior porque faltan registros de ingresos intermedios."
                    ),
                    "desfase_temporal": {
                        "es_desactualizado": True,
                        "dias_desfase_bd": dias_faltantes,
                        "dias_desfase_corte": dias_faltantes,
                        "vigente": False,
                        "desfase_severo": True,
                        "estado_vigencia": "posterior_al_corte_bd"
                    }
                }

            # 2. Consultar historial cronológico estricto del alumno hasta f_ref (EsPrueba = 0)
            cursor.execute("""
                SELECT R.FechaHora
                FROM dbo.RegistroIngresos R
                WHERE R.AlumnoID = ?
                  AND ISNULL(R.EsPrueba, 0) = 0
                  AND CAST(R.FechaHora AS DATE) <= ?
                ORDER BY R.FechaHora ASC
            """, (id_alumno, f_ref.isoformat()))
            visitas_rows = cursor.fetchall()

            # 3. Consultar fechas operativas globales hasta f_ref para calcular tasas
            cursor.execute("""
                SELECT DISTINCT CAST(FechaHora AS DATE) AS Fecha
                FROM dbo.RegistroIngresos
                WHERE ISNULL(EsPrueba, 0) = 0
                  AND CAST(FechaHora AS DATE) <= ?
                ORDER BY Fecha ASC
            """, (f_ref.isoformat(),))
            fechas_operativas_globales = [r[0] for r in cursor.fetchall()]

        cod_mascarado = enmascarar_codigo(cod_alumno)
        proximo_dia_operativo = calcular_proximo_dia_operativo(f_ref, fechas_operativas_globales)
        dias_operativos_transcurridos = max(1, len(fechas_operativas_globales))

        ultima_fecha_bd_str = ultima_fecha_bd.isoformat() if ultima_fecha_bd else None
        origen_fecha_ref = "seleccionada_por_usuario" if fecha_referencia_provista else "predeterminada_ultima_bd"
        es_exp_general = bool(f_ref > FECHA_ENTRENAMIENTO_ARTEFACTO)
        tipo_pred_general = "experimental_post_entrenamiento" if es_exp_general else "vigente_en_periodo_entrenamiento"
        estado_val_general = "pendiente_validacion_prospectiva" if es_exp_general else "validada_en_muestra"
        adv_exp_general = (
            "Predicción experimental: El modelo en producción fue entrenado y evaluado con datos "
            f"hasta el {FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')}. La fecha consultada utiliza "
            "datos del historial de la base de datos pero corresponde a un periodo posterior en régimen experimental "
            "pendiente de validación prospectiva en sombra; no debe interpretarse como una predicción validada en producción."
        ) if es_exp_general else None

        # Caso A: Cero visitas en el historial
        if not visitas_rows:
            return {
                "status": "historial_insuficiente",
                "confiable": False,
                "codigo_mascarado": cod_mascarado,
                "escuela": escuela_alumno,
                "fecha_referencia": f_ref.isoformat(),
                "origen_fecha_referencia": origen_fecha_ref,
                "fecha_corte_bd": ultima_fecha_bd_str,
                "ultima_fecha_bd": ultima_fecha_bd_str,
                "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                "fecha_corte_entrenamiento": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
                "horizonte_prediccion": "H1",
                "es_experimental": es_exp_general,
                "tipo_prediccion": tipo_pred_general,
                "estado_validacion": estado_val_general,
                "advertencia_experimental": adv_exp_general,
                "dias_activos_historicos": 0,
                "total_visitas_historicas": 0,
                "probabilidad_retorno": None,
                "nivel_probabilidad": "Indeterminado",
                "definicion_retorno": (
                    "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                    f"día hábil operativo de biblioteca ({proximo_dia_operativo.isoformat()}). "
                    "Horizonte H1 operativo (lunes a viernes, 08:00 a 20:45)."
                ),
                "explicacion": (
                    f"El alumno con código {cod_mascarado} no registra ningún ingreso previo en el sistema "
                    f"hasta el {f_ref.isoformat()}. No se dispone de datos para estimar su retorno."
                ),
                "advertencia": (
                    "Estimación orientativa de propensión de asistencia. "
                    "No representa certeza matemática ni debe utilizarse para calificar abandono, deserción "
                    "ni para calcular directamente afluencia agregada total."
                ),
                "limites_cobertura": {
                    "corte_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                    "ultima_fecha_ingresos_bd": ultima_fecha_bd_str,
                    "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat()}",
                    "registros_prueba_excluidos": 38
                }
            }

        fechas_visitas = [r[0].date() for r in visitas_rows]
        fechas_unicas = sorted(list(set(fechas_visitas)))
        dias_activos = len(fechas_unicas)
        total_visitas = len(visitas_rows)

        # Caso B: Historial insuficiente (< 3 días activos de visita)
        if dias_activos < self.umbral_minimo:
            return {
                "status": "historial_insuficiente",
                "confiable": False,
                "codigo_mascarado": cod_mascarado,
                "escuela": escuela_alumno,
                "fecha_referencia": f_ref.isoformat(),
                "origen_fecha_referencia": origen_fecha_ref,
                "fecha_corte_bd": ultima_fecha_bd_str,
                "ultima_fecha_bd": ultima_fecha_bd_str,
                "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                "fecha_corte_entrenamiento": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
                "horizonte_prediccion": "H1",
                "es_experimental": es_exp_general,
                "tipo_prediccion": tipo_pred_general,
                "estado_validacion": estado_val_general,
                "advertencia_experimental": adv_exp_general,
                "dias_activos_historicos": dias_activos,
                "total_visitas_historicas": total_visitas,
                "probabilidad_retorno": None,
                "nivel_probabilidad": "Indeterminado (historial insuficiente)",
                "definicion_retorno": (
                    "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                    f"día hábil operativo de biblioteca ({proximo_dia_operativo.isoformat()}). "
                    "Horizonte H1 operativo (lunes a viernes, 08:00 a 20:45)."
                ),
                "explicacion": (
                    f"Historial insuficiente: El alumno registra únicamente {dias_activos} día(s) con visitas "
                    f"en el historial previo disponible (total de {total_visitas} visitas). Se requiere un mínimo "
                    "de 3 días activos para identificar patrones de asistencia y calcular una estimación de retorno confiable. "
                    "Emitir una probabilidad con 1 o 2 visitas resultaría estadísticamente espurio e impreciso."
                ),
                "advertencia": (
                    "Estimación orientativa de propensión de asistencia. "
                    "No representa certeza matemática ni debe utilizarse para calificar abandono, deserción "
                    "ni para calcular directamente afluencia agregada total."
                ),
                "limites_cobertura": {
                    "criterio_minimo": "Mínimo 3 días activos en el historial previo",
                    "corte_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                    "ultima_fecha_ingresos_bd": ultima_fecha_bd_str,
                    "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat()}",
                    "registros_prueba_excluidos": 38
                }
            }

        # Caso C: Historial suficiente (>= 3 días activos) -> Inferencia ML
        ultima_fecha_visita = max(fechas_unicas)
        recencia_dias = (f_ref - ultima_fecha_visita).days

        # Visitas en ventana reciente
        visitas_7d = len([d for d in fechas_unicas if d >= (f_ref - timedelta(days=7))])
        visitas_14d = len([d for d in fechas_unicas if d >= (f_ref - timedelta(days=14))])

        # Afinidad al día de la semana del próximo día operativo
        proximo_weekday = proximo_dia_operativo.weekday()
        visitas_mismo_weekday = len([d for d in fechas_unicas if d.weekday() == proximo_weekday])
        afinidad_dia = float(visitas_mismo_weekday / dias_activos)

        # ¿Visitó en la fecha de referencia?
        visito_hoy = 1 if f_ref in fechas_unicas else 0

        # Tasa de asistencia general
        tasa_asistencia = float(dias_activos / dias_operativos_transcurridos)

        # Construir vector de características
        vector_variables = pd.DataFrame([{
            'dias_activos': dias_activos,
            'total_visitas': total_visitas,
            'recencia_dias': recencia_dias,
            'visitas_ultimos_7d': visitas_7d,
            'visitas_ultimos_14d': visitas_14d,
            'afinidad_dia_semana': afinidad_dia,
            'visito_hoy': visito_hoy,
            'tasa_asistencia': tasa_asistencia
        }])[self.variables]

        prob = float(self.modelo.predict_proba(vector_variables)[0, 1])

        if prob >= 0.60:
            nivel = "Alta"
        elif prob >= 0.30:
            nivel = "Media"
        else:
            nivel = "Baja"

        dia_nombre_es = {
            0: "lunes", 1: "martes", 2: "miércoles",
            3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
        }
        nombre_proximo_dia = dia_nombre_es.get(proximo_dia_operativo.weekday(), "día hábil")

        # ---------------------------------------------------------------------
        # Evaluación rigurosa de desfase temporal y régimen metodológico
        # ---------------------------------------------------------------------
        # 1. Desfase respecto a los registros disponibles en base de datos:
        dias_desfase_bd = (f_ref - ultima_fecha_bd).days if ultima_fecha_bd else 0
        es_desactualizado_bd = dias_desfase_bd > 0
        es_desfase_critico_bd = dias_desfase_bd > 30
        es_desfase_severo_bd = dias_desfase_bd > 60

        # 2. Relación respecto a la fecha de entrenamiento del artefacto (corte: 31/07/2026):
        dias_desfase_entrenamiento = (f_ref - FECHA_ENTRENAMIENTO_ARTEFACTO).days
        es_posterior_entrenamiento = dias_desfase_entrenamiento > 0

        # Régimen de validación metodológica
        if es_posterior_entrenamiento:
            es_experimental = True
            tipo_prediccion = "experimental_post_entrenamiento"
            estado_validacion = "pendiente_validacion_prospectiva"
            advertencia_experimental = (
                "Predicción experimental: El modelo en producción fue entrenado y evaluado con datos "
                f"hasta el {FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')}. La fecha consultada utiliza "
                "datos del historial de la base de datos pero corresponde a un periodo posterior en régimen experimental "
                "pendiente de validación prospectiva en sombra; no debe interpretarse como una predicción validada en producción."
            )
        else:
            es_experimental = False
            tipo_prediccion = "vigente_en_periodo_entrenamiento"
            estado_validacion = "validada_en_muestra"
            advertencia_experimental = None

        # Evaluación de vigencia técnica:
        # Si la fecha consultada sobrepasa en >60 días la última fecha cargada en BD, los datos faltantes
        # deprimen espuriamente la probabilidad (inactividad artificial por atraso de ingesta en BD).
        if es_desfase_severo_bd:
            es_vigente = False
            estado_vigencia = "no_representativo"
            es_desfase_severo = True
            es_desfase_critico = True
        elif es_desfase_critico_bd:
            es_vigente = True
            estado_vigencia = "desactualizado_moderado"
            es_desfase_severo = False
            es_desfase_critico = True
        else:
            es_vigente = True
            estado_vigencia = "experimental_pendiente_validacion" if es_experimental else "vigente"
            es_desfase_severo = False
            es_desfase_critico = False

        fecha_corte_bd_fmt = ultima_fecha_bd.strftime('%d/%m/%Y') if ultima_fecha_bd else 'N/D'

        info_desfase = {
            "es_desactualizado": es_desactualizado_bd,
            "dias_desfase_bd": max(0, dias_desfase_bd),
            "dias_desfase_corte": max(0, dias_desfase_bd),
            "dias_desfase_entrenamiento": max(0, dias_desfase_entrenamiento),
            "fecha_consultada": f_ref.isoformat(),
            "origen_fecha_referencia": origen_fecha_ref,
            "fecha_corte_bd": ultima_fecha_bd_str,
            "ultima_fecha_bd": ultima_fecha_bd_str,
            "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
            "fecha_corte_entrenamiento": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
            "desfase_critico": es_desfase_critico,
            "desfase_severo": es_desfase_severo,
            "vigente": es_vigente,
            "es_experimental": es_experimental,
            "tipo_prediccion": tipo_prediccion,
            "estado_validacion": estado_validacion,
            "aviso": (
                (
                    f"Aviso de datos desactualizados en BD: La base de datos contiene registros únicamente "
                    f"hasta el {fecha_corte_bd_fmt}. La fecha consultada ({f_ref.strftime('%d/%m/%Y')}) "
                    f"presenta un desfase de {dias_desfase_bd} días sin nuevos ingresos registrados en el sistema. "
                    + (
                        "Al superar los 60 días de desfase, esta estimación no debe considerarse como una predicción vigente "
                        "del comportamiento actual del estudiante. Para consultar la propensión real en su periodo activo, "
                        f"consulte a la fecha de corte en BD ({fecha_corte_bd_fmt}) o a la de entrenamiento ({FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')})."
                        if es_desfase_severo else
                        "La inactividad calculada deprime la probabilidad a valores basales."
                    )
                ) if es_desactualizado_bd else (
                    (
                        f"Aviso de régimen experimental: El artefacto activo fue entrenado hasta el {FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')}. "
                        f"La fecha consultada ({f_ref.strftime('%d/%m/%Y')}) cuenta con datos en BD pero corresponde a un periodo "
                        "posterior pendiente de validación prospectiva en sombra."
                    ) if es_experimental else None
                )
            )
        }

        if es_desfase_severo:
            mensaje = (
                f"[DATOS DESACTUALIZADOS EN BD >60 DÍAS] El alumno con código {cod_mascarado} ({escuela_alumno}) presenta "
                f"un desfase de {dias_desfase_bd} días respecto a la última fecha en BD ({fecha_corte_bd_fmt}). "
                "La predicción no es representativa como probabilidad vigente para la fecha actual. "
                f"Consulte la estimación histórica a la fecha de corte ({FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')}) para observar su propensión en periodo validado."
            )
        elif es_experimental:
            mensaje = (
                f"[EXPERIMENTAL - PENDIENTE VALIDACIÓN PROSPECTIVA] El alumno con código {cod_mascarado} ({escuela_alumno}) "
                f"presenta una probabilidad {nivel.lower()} ({round(prob * 100, 1)}%) de volver a la biblioteca el siguiente día hábil operativo "
                f"({nombre_proximo_dia} {proximo_dia_operativo.isoformat()}). "
                f"Historial previo al {f_ref.isoformat()}: {dias_activos} días activos ({total_visitas} visitas totales, última visita hace {recencia_dias} días). "
                f"Modelo entrenado al {FECHA_ENTRENAMIENTO_ARTEFACTO.strftime('%d/%m/%Y')}; inferencia pendiente de validación prospectiva."
            )
        elif es_desfase_critico:
            mensaje = (
                f"[AVISO: DATOS DESACTUALIZADOS EN BD] El alumno con código {cod_mascarado} ({escuela_alumno}) presenta "
                f"un desfase de {dias_desfase_bd} días respecto al corte en BD ({fecha_corte_bd_fmt}). "
                f"La estimación calculada ({round(prob * 100, 1)}%) está deprimida por falta de datos recientes en el sistema."
            )
        else:
            mensaje = (
                f"El alumno con código {cod_mascarado} ({escuela_alumno}) presenta una probabilidad {nivel.lower()} "
                f"({round(prob * 100, 1)}%) de volver a la biblioteca el siguiente día hábil operativo "
                f"({nombre_proximo_dia} {proximo_dia_operativo.isoformat()}). "
                f"Historial previo: {dias_activos} días activos ({total_visitas} visitas totales, última visita hace {recencia_dias} días)."
            )

        return {
            "status": "success",
            "confiable": not es_desfase_severo,
            "vigente": es_vigente,
            "estado_vigencia": estado_vigencia,
            "codigo_mascarado": cod_mascarado,
            "escuela": escuela_alumno,
            "fecha_referencia": f_ref.isoformat(),
            "origen_fecha_referencia": origen_fecha_ref,
            "fecha_corte_bd": ultima_fecha_bd_str,
            "ultima_fecha_bd": ultima_fecha_bd_str,
            "fecha_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
            "fecha_corte_entrenamiento": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
            "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
            "horizonte_prediccion": "H1",
            "definicion_retorno": (
                "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                f"día hábil operativo de biblioteca ({nombre_proximo_dia} {proximo_dia_operativo.isoformat()}). "
                "Horizonte H1 operativo (lunes a viernes, 08:00 a 20:45)."
            ),
            "probabilidad_retorno": round(prob, 4),
            "probabilidad_porcentaje": f"{round(prob * 100, 1)}%",
            "nivel_probabilidad": nivel,
            "es_experimental": es_experimental,
            "tipo_prediccion": tipo_prediccion,
            "estado_validacion": estado_validacion,
            "advertencia_experimental": advertencia_experimental,
            "dias_desfase_bd": max(0, dias_desfase_bd),
            "dias_desfase_corte": max(0, dias_desfase_bd),
            "dias_desfase_entrenamiento": max(0, dias_desfase_entrenamiento),
            "desfase_temporal": info_desfase,
            "aviso_desactualizado": info_desfase["aviso"],
            "es_desfase_critico": es_desfase_critico,
            "es_desfase_severo": es_desfase_severo,
            "metricas_historial": {
                "dias_activos": dias_activos,
                "total_visitas": total_visitas,
                "recencia_dias": recencia_dias,
                "visitas_ultimos_7d": visitas_7d,
                "visitas_ultimos_14d": visitas_14d,
                "afinidad_dia_semana": round(afinidad_dia, 4),
                "visito_dia_referencia": bool(visito_hoy),
                "tasa_asistencia_periodo": round(tasa_asistencia, 4)
            },
            "advertencia": (
                "Estimación orientativa de propensión de asistencia. "
                "No representa certeza matemática ni debe utilizarse para calificar abandono, deserción "
                "ni para calcular directamente afluencia agregada total."
            ),
            "limites_cobertura": {
                "periodo_entrenamiento_modelo": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat()}",
                "corte_entrenamiento_artefacto": FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat(),
                "ultima_fecha_ingresos_bd": ultima_fecha_bd_str,
                "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_ENTRENAMIENTO_ARTEFACTO.isoformat()}",
                "registros_prueba_excluidos": 38
            },
            "mensaje": mensaje
        }


def predecir_retorno_alumno(codigo_matricula=None, alumno_id=None, fecha_referencia=None):
    """
    Función de acceso directo para predecir el retorno de un alumno.
    """
    predictor = PredictorRetornoAlumno.obtener_instancia()
    return predictor.predecir_retorno(
        codigo_matricula=codigo_matricula,
        alumno_id=alumno_id,
        fecha_referencia=fecha_referencia
    )
