import re
import calendar
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, Tuple

SEDES_VALIDAS = {
    "central": "Central",
    "tarma": "Tarma",
    "la merced": "La Merced",
    "oxapampa": "Oxapampa",
    "paucartambo": "Paucartambo",
    "yanahuanca": "Yanahuanca"
}

CAMPOS_CONTEXTO_PERMITIDOS = {"intencion", "fecha_inicio", "fecha_fin", "fecha", "sede", "codigo_matricula"}
INTENCIONES_VALIDAS_CONTEXTO = {"resumen_ingresos", "horas_mayor_afluencia", "prediccion_afluencia", "conteo_ingresos_alumno", "afluencia_historica"}
CAMPOS_SENSIBLES_PROHIBIDOS = {
    "nombre", "texto_persona", "dni", "persona_id", "id", "persona",
    "candidato", "opciones", "historial", "registros", "ultimo_registro",
    "respuesta", "mensaje", "datos"
}

MESES_ESPANOL = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12
}


def obtener_fecha_referencia():
    """
    Obtiene la fecha actual de referencia en la zona horaria America/Lima
    o en la hora local del sistema como alternativa segura.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Lima")).date()
    except Exception:
        return datetime.now().date()


def validar_fecha_espanol(dia: int, mes: int, anio: int) -> Optional[date]:
    """
    Valida y construye un objeto date verificando límites de mes y bisiestos.
    Retorna None si el día o mes es inválido para el año especificado (ej. 31 de junio).
    """
    if not (1 <= mes <= 12):
        return None
    if not (1900 <= anio <= 2100):
        return None
    _, max_dias = calendar.monthrange(anio, mes)
    if not (1 <= dia <= max_dias):
        return None
    try:
        return date(anio, mes, dia)
    except (ValueError, TypeError):
        return None


def extraer_fechas_texto_espanol(texto: str, fecha_referencia=None) -> Optional[Dict[str, Any]]:
    """
    Extrae e interpreta expresiones de fechas y rangos en español y formato numérico/ISO.
    Valida días por mes, rangos válidos (inicio <= fin) y límites de 366 días.
    Detecta también fechas ambiguas o incompletas (ej. mes sin año).
    """
    if not texto or not isinstance(texto, str):
        return None

    # 1. Rango explícito en formato ISO: del YYYY-MM-DD al YYYY-MM-DD / entre YYYY-MM-DD y YYYY-MM-DD
    m_rango_iso = re.search(
        r"\b(?:del?\s+|entre\s+(?:el\s+)?|desde\s+(?:el\s+)?)"
        r"(\d{4}-\d{2}-\d{2})"
        r"\s+(?:al?\s+|y\s+(?:el\s+)?|hasta\s+(?:el\s+)?)"
        r"(\d{4}-\d{2}-\d{2})\b",
        texto,
        re.IGNORECASE
    )
    if m_rango_iso:
        try:
            f1 = date.fromisoformat(m_rango_iso.group(1))
            f2 = date.fromisoformat(m_rango_iso.group(2))
            if f1 > f2:
                return {
                    "error_temporal": "rango_invalido",
                    "mensaje_error": "La fecha de inicio debe ser anterior o igual a la fecha de fin."
                }
            if (f2 - f1).days > 366:
                return {
                    "error_temporal": "rango_excesivo",
                    "mensaje_error": "El rango consultado excede el límite máximo permitido de 366 días."
                }
            return {
                "fecha_inicio": f1.isoformat(),
                "fecha_fin": f2.isoformat(),
                "tipo_periodo": "rango"
            }
        except ValueError:
            return {
                "error_temporal": "fecha_invalida",
                "mensaje_error": "El formato de fecha no es válido."
            }

    # 2. Rango explícito en español (dos fechas completas o entre distintos meses):
    # Ej: 'entre el 1 de junio y el 31 de julio de 2026', 'del 1 de junio al 31 de julio de 2026',
    #     'desde el 1 de junio hasta el 31 de julio de 2026', 'de 1 de junio a 31 de julio de 2026'
    m_rango_meses = re.search(
        r"\b(?:entre\s+(?:el\s+)?|del?\s+(?:el\s+)?|desde\s+(?:el\s+)?)"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)(?:\s+de\s+(\d{4}))?"
        r"\s+(?:y\s+(?:el\s+)?|al?\s+(?:el\s+)?|hasta\s+(?:el\s+)?)"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_rango_meses:
        m1_nom = normalizar_texto(m_rango_meses.group(2))
        m2_nom = normalizar_texto(m_rango_meses.group(5))
        if m1_nom in MESES_ESPANOL and m2_nom in MESES_ESPANOL:
            d1 = int(m_rango_meses.group(1))
            m1 = MESES_ESPANOL[m1_nom]
            y2 = int(m_rango_meses.group(6))
            y1 = int(m_rango_meses.group(3)) if m_rango_meses.group(3) else y2
            d2 = int(m_rango_meses.group(4))
            m2 = MESES_ESPANOL[m2_nom]

            f1 = validar_fecha_espanol(d1, m1, y1)
            if not f1:
                return {
                    "error_temporal": "fecha_invalida",
                    "mensaje_error": f"La fecha {d1} de {m1_nom} de {y1} no es válida."
                }
            f2 = validar_fecha_espanol(d2, m2, y2)
            if not f2:
                return {
                    "error_temporal": "fecha_invalida",
                    "mensaje_error": f"La fecha {d2} de {m2_nom} de {y2} no es válida."
                }
            if f1 > f2:
                return {
                    "error_temporal": "rango_invalido",
                    "mensaje_error": "La fecha de inicio debe ser anterior o igual a la fecha de fin."
                }
            if (f2 - f1).days > 366:
                return {
                    "error_temporal": "rango_excesivo",
                    "mensaje_error": "El rango consultado excede el límite máximo permitido de 366 días."
                }
            return {
                "fecha_inicio": f1.isoformat(),
                "fecha_fin": f2.isoformat(),
                "tipo_periodo": "rango"
            }

    # 3. Rango explícito en español dentro del mismo mes:
    # Ej: 'del 1 al 15 de julio de 2026', 'entre el 1 y el 15 de julio de 2026'
    m_rango_mismo_mes = re.search(
        r"\b(?:entre\s+(?:el\s+)?|del?\s+(?:el\s+)?|desde\s+(?:el\s+)?)"
        r"(\d{1,2})"
        r"\s+(?:y\s+(?:el\s+)?|al?\s+(?:el\s+)?|hasta\s+(?:el\s+)?)"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_rango_mismo_mes:
        m_nom = normalizar_texto(m_rango_mismo_mes.group(3))
        if m_nom in MESES_ESPANOL:
            d1 = int(m_rango_mismo_mes.group(1))
            d2 = int(m_rango_mismo_mes.group(2))
            m = MESES_ESPANOL[m_nom]
            y = int(m_rango_mismo_mes.group(4))

            f1 = validar_fecha_espanol(d1, m, y)
            if not f1:
                return {
                    "error_temporal": "fecha_invalida",
                    "mensaje_error": f"La fecha {d1} de {m_nom} de {y} no es válida."
                }
            f2 = validar_fecha_espanol(d2, m, y)
            if not f2:
                return {
                    "error_temporal": "fecha_invalida",
                    "mensaje_error": f"La fecha {d2} de {m_nom} de {y} no es válida."
                }
            if f1 > f2:
                return {
                    "error_temporal": "rango_invalido",
                    "mensaje_error": "La fecha de inicio debe ser anterior o igual a la fecha de fin."
                }
            return {
                "fecha_inicio": f1.isoformat(),
                "fecha_fin": f2.isoformat(),
                "tipo_periodo": "rango"
            }

    # 4. Rango de meses completos: 'entre junio y julio de 2026', 'de junio a julio de 2026'
    m_rango_meses_comp = re.search(
        r"\b(?:entre\s+(?:el\s+mes\s+de\s+)?|del?\s+(?:mes\s+de\s+)?|desde\s+(?:el\s+mes\s+de\s+)?)"
        r"([a-záéíóúñ]+)"
        r"\s+(?:y\s+(?:el\s+mes\s+de\s+)?|al?\s+(?:mes\s+de\s+)?|hasta\s+(?:el\s+mes\s+de\s+)?)"
        r"([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_rango_meses_comp:
        m1_nom = normalizar_texto(m_rango_meses_comp.group(1))
        m2_nom = normalizar_texto(m_rango_meses_comp.group(2))
        if m1_nom in MESES_ESPANOL and m2_nom in MESES_ESPANOL:
            y = int(m_rango_meses_comp.group(3))
            m1 = MESES_ESPANOL[m1_nom]
            m2 = MESES_ESPANOL[m2_nom]
            _, last_day = calendar.monthrange(y, m2)
            f1 = date(y, m1, 1)
            f2 = date(y, m2, last_day)
            if f1 > f2:
                return {
                    "error_temporal": "rango_invalido",
                    "mensaje_error": "La fecha de inicio debe ser anterior o igual a la fecha de fin."
                }
            if (f2 - f1).days > 366:
                return {
                    "error_temporal": "rango_excesivo",
                    "mensaje_error": "El rango consultado excede el límite máximo permitido de 366 días."
                }
            return {
                "fecha_inicio": f1.isoformat(),
                "fecha_fin": f2.isoformat(),
                "tipo_periodo": "rango"
            }

    # 5. Fecha puntual en español: 'el 15 de julio de 2026', '15 de julio de 2026'
    m_fecha_esp = re.search(
        r"\b(?:el\s+|para\s+el\s+|del?\s+)?"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_fecha_esp:
        m_nom = normalizar_texto(m_fecha_esp.group(2))
        if m_nom in MESES_ESPANOL:
            d = int(m_fecha_esp.group(1))
            m = MESES_ESPANOL[m_nom]
            y = int(m_fecha_esp.group(3))
            f = validar_fecha_espanol(d, m, y)
            if not f:
                return {
                    "error_temporal": "fecha_invalida",
                    "mensaje_error": f"La fecha {d} de {m_nom} de {y} no es válida."
                }
            f_str = f.isoformat()
            return {
                "fecha_inicio": f_str,
                "fecha_fin": f_str,
                "fecha": f_str,
                "tipo_periodo": "fecha_fija"
            }

    # 6. Mes completo: 'en junio de 2026', 'durante junio de 2026', 'en el mes de junio de 2026'
    m_mes_comp = re.search(
        r"\b(?:(?:en|durante|para)\s+(?:el\s+mes\s+de\s+)?|el\s+mes\s+de\s+|mes\s+de\s+)"
        r"([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_mes_comp:
        m_nom = normalizar_texto(m_mes_comp.group(1))
        if m_nom in MESES_ESPANOL:
            y = int(m_mes_comp.group(2))
            m = MESES_ESPANOL[m_nom]
            _, last_day = calendar.monthrange(y, m)
            f1 = date(y, m, 1)
            f2 = date(y, m, last_day)
            return {
                "fecha_inicio": f1.isoformat(),
                "fecha_fin": f2.isoformat(),
                "tipo_periodo": "mes_completo"
            }

    # 7. Rango numérico DD/MM/YYYY o DD-MM-YYYY: 'del 01/06/2026 al 31/07/2026'
    m_rango_num = re.search(
        r"\b(?:del?\s+|entre\s+(?:el\s+)?|desde\s+(?:el\s+)?)"
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"
        r"\s+(?:al?\s+|y\s+(?:el\s+)?|hasta\s+(?:el\s+)?)"
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        texto,
        re.IGNORECASE
    )
    if m_rango_num:
        d1, m1, y1 = int(m_rango_num.group(1)), int(m_rango_num.group(2)), int(m_rango_num.group(3))
        d2, m2, y2 = int(m_rango_num.group(4)), int(m_rango_num.group(5)), int(m_rango_num.group(6))
        f1 = validar_fecha_espanol(d1, m1, y1)
        f2 = validar_fecha_espanol(d2, m2, y2)
        if not f1 or not f2:
            return {
                "error_temporal": "fecha_invalida",
                "mensaje_error": "Una de las fechas indicadas en el rango no es válida."
            }
        if f1 > f2:
            return {
                "error_temporal": "rango_invalido",
                "mensaje_error": "La fecha de inicio debe ser anterior o igual a la fecha de fin."
            }
        if (f2 - f1).days > 366:
            return {
                "error_temporal": "rango_excesivo",
                "mensaje_error": "El rango consultado excede el límite máximo permitido de 366 días."
            }
        return {
            "fecha_inicio": f1.isoformat(),
            "fecha_fin": f2.isoformat(),
            "tipo_periodo": "rango"
        }

    # 8. Fecha puntual numérica DD/MM/YYYY o YYYY-MM-DD
    m_fecha_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", texto)
    if m_fecha_iso:
        try:
            f = date.fromisoformat(m_fecha_iso.group(1))
            f_str = f.isoformat()
            return {
                "fecha_inicio": f_str,
                "fecha_fin": f_str,
                "fecha": f_str,
                "tipo_periodo": "fecha_fija"
            }
        except ValueError:
            return {
                "error_temporal": "fecha_invalida",
                "mensaje_error": "La fecha ISO no es válida."
            }

    m_fecha_num = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", texto)
    if m_fecha_num:
        d, m, y = int(m_fecha_num.group(1)), int(m_fecha_num.group(2)), int(m_fecha_num.group(3))
        f = validar_fecha_espanol(d, m, y)
        if not f:
            return {
                "error_temporal": "fecha_invalida",
                "mensaje_error": "La fecha numérica indicada no es válida."
            }
        f_str = f.isoformat()
        return {
            "fecha_inicio": f_str,
            "fecha_fin": f_str,
            "fecha": f_str,
            "tipo_periodo": "fecha_fija"
        }

    # 9. Detección de ambigüedad: Rango de días sin año ('del 1 al 15 de julio')
    for m_rango_sin_anio in re.finditer(
        r"\b(?:entre\s+(?:el\s+)?|del?\s+(?:el\s+)?|desde\s+(?:el\s+)?)"
        r"(\d{1,2})"
        r"\s+(?:y\s+(?:el\s+)?|al?\s+(?:el\s+)?|hasta\s+(?:el\s+)?)"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\b",
        texto,
        re.IGNORECASE
    ):
        m_nom = normalizar_texto(m_rango_sin_anio.group(3))
        if m_nom in MESES_ESPANOL:
            return {
                "error_temporal": "fecha_ambigua",
                "mensaje_error": f"¿A qué año corresponde el periodo del {m_rango_sin_anio.group(1)} al {m_rango_sin_anio.group(2)} de {m_nom}? Por favor indique el año (por ejemplo: 'del {m_rango_sin_anio.group(1)} al {m_rango_sin_anio.group(2)} de {m_nom} de 2026')."
            }

    # 10. Detección de ambigüedad: Fecha puntual sin año ('el 15 de julio')
    for m_puntual_sin_anio in re.finditer(
        r"\b(?:el\s+|para\s+el\s+|del?\s+)?"
        r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\b",
        texto,
        re.IGNORECASE
    ):
        m_nom = normalizar_texto(m_puntual_sin_anio.group(2))
        if m_nom in MESES_ESPANOL and m_nom not in ("hoy", "ayer"):
            return {
                "error_temporal": "fecha_ambigua",
                "mensaje_error": f"¿A qué año corresponde la fecha {m_puntual_sin_anio.group(1)} de {m_nom}? Por favor indique el año (por ejemplo: '{m_puntual_sin_anio.group(1)} de {m_nom} de 2026')."
            }

    # 11. Detección de ambigüedad: Mes mencionado sin año ('en junio', 'durante julio')
    for m_mes in re.finditer(r"\b([a-záéíóúñ]+)\b", texto, re.IGNORECASE):
        m_nom = normalizar_texto(m_mes.group(1))
        if m_nom in MESES_ESPANOL and m_nom not in ("hoy", "ayer"):
            return {
                "error_temporal": "fecha_ambigua",
                "mensaje_error": f"¿A qué año corresponde el mes de {m_nom}? Por favor especifique el año (por ejemplo: '{m_nom} de 2026')."
            }

    return None


def resolver_expresion_temporal(texto, fecha_referencia=None):
    """
    Resuelve expresiones temporales relativas y absolutas a fechas ISO (YYYY-MM-DD).
    Soporta:
    - Fechas y rangos explícitos en español (vía extraer_fechas_texto_espanol)
    - 'ayer', 'de ayer', 'para ayer' -> día anterior
    - 'hoy', 'de hoy', 'para hoy' -> día actual
    - 'anteayer', 'antier' -> dos días anteriores
    - 'la semana pasada' -> lunes a domingo de la semana calendario anterior
    - 'el mes pasado' -> primer y último día del mes calendario anterior
    """
    if not texto or not isinstance(texto, str):
        return None

    # Primero intentar resolver expresiones explícitas en español o numéricas
    res_esp = extraer_fechas_texto_espanol(texto, fecha_referencia)
    if res_esp:
        return res_esp

    ref = fecha_referencia if isinstance(fecha_referencia, (date, datetime)) else obtener_fecha_referencia()
    if isinstance(ref, datetime):
        ref = ref.date()

    norm = normalizar_texto(texto)

    # 3. Anteayer / Antier (evaluado antes de 'ayer')
    if re.search(r"\b(?:anteayer|antier)\b", norm):
        d = ref - timedelta(days=2)
        d_str = d.isoformat()
        return {
            "fecha_inicio": d_str,
            "fecha_fin": d_str,
            "fecha": d_str,
            "tipo_periodo": "anteayer"
        }

    # 4. Ayer
    if re.search(r"\bayer\b", norm):
        d = ref - timedelta(days=1)
        d_str = d.isoformat()
        return {
            "fecha_inicio": d_str,
            "fecha_fin": d_str,
            "fecha": d_str,
            "tipo_periodo": "ayer"
        }

    # 5. Hoy
    if re.search(r"\bhoy\b", norm):
        d_str = ref.isoformat()
        return {
            "fecha_inicio": d_str,
            "fecha_fin": d_str,
            "fecha": d_str,
            "tipo_periodo": "hoy"
        }

    # 6. Semana pasada (lunes a domingo de la semana calendario previa)
    if re.search(r"\b(?:la\s+)?semana\s+pasada\b", norm):
        dias_desde_lunes = ref.weekday()  # Lunes = 0, Domingo = 6
        fin_semana_pasada = ref - timedelta(days=dias_desde_lunes + 1)
        inicio_semana_pasada = fin_semana_pasada - timedelta(days=6)
        return {
            "fecha_inicio": inicio_semana_pasada.isoformat(),
            "fecha_fin": fin_semana_pasada.isoformat(),
            "tipo_periodo": "semana_pasada"
        }

    # 7. Mes pasado (primer y último día del mes calendario previo)
    if re.search(r"\b(?:el\s+)?mes\s+pasado\b", norm):
        if ref.month == 1:
            inicio_mes_pasado = date(ref.year - 1, 12, 1)
            fin_mes_pasado = date(ref.year, 1, 1) - timedelta(days=1)
        else:
            inicio_mes_pasado = date(ref.year, ref.month - 1, 1)
            fin_mes_pasado = date(ref.year, ref.month, 1) - timedelta(days=1)
        return {
            "fecha_inicio": inicio_mes_pasado.isoformat(),
            "fecha_fin": fin_mes_pasado.isoformat(),
            "tipo_periodo": "mes_pasado"
        }

    return None


def es_pregunta_seguimiento(pregunta):
    """
    Determina si una pregunta es una continuación o seguimiento conversacional.
    Detecta patrones como:
    - '¿Y ayer?', 'y ayer?', '¿ayer?', 'ayer'
    - '¿Y hoy?', 'y hoy', 'hoy'
    - '¿Y anteayer?', '¿Y antier?'
    - '¿Y la semana pasada?', '¿Y el mes pasado?'
    - '¿Y del YYYY-MM-DD al YYYY-MM-DD?'
    - '¿Y el YYYY-MM-DD?'
    - Preguntas de seguimiento cortas o ambiguas: '¿Y entonces?', '¿Y qué más?', '¿Y?', etc.
    """
    if not pregunta or not isinstance(pregunta, str):
        return False
    norm = normalizar_texto(pregunta)
    if not norm:
        return False

    # 1. Empieza con conjunción de seguimiento 'y' o 'e'
    if re.match(r"^(?:y|e)\s+", norm) or norm in ("y", "e"):
        return True

    # 2. Expresiones temporales o de fechas como consulta corta o aislada
    patrones_cortos = [
        r"^(?:de\s+|para\s+)?(?:ayer|hoy|anteayer|antier)\??$",
        r"^(?:de\s+|para\s+)?(?:la\s+)?semana\s+pasada\??$",
        r"^(?:de\s+|para\s+)?(?:el\s+)?mes\s+pasado\??$",
        r"^(?:que\s+hay\s+de|que\s+tal)\s+(?:ayer|hoy|anteayer|antier|la\s+semana\s+pasada|el\s+mes\s+pasado)\??$",
        r"^\d{4}-\d{2}-\d{2}\??$",
        r"^del\s+\d{4}-\d{2}-\d{2}\s+al\s+\d{4}-\d{2}-\d{2}\??$"
    ]
    for p in patrones_cortos:
        if re.search(p, norm):
            return True

    # 3. Preguntas de seguimiento ambiguas sin intención definida
    patrones_ambiguos = [
        r"^(?:y\s+)?entonces\??$",
        r"^(?:y\s+)?que\s+mas\??$",
        r"^(?:y\s+)?ahora\??$",
        r"^(?:y\s+)?que\s+sigue\??$",
        r"^(?:y\s+)?luego\??$",
        r"^como\??$"
    ]
    for p in patrones_ambiguos:
        if re.search(p, norm):
            return True

    return False


def es_seguimiento_ambiguo(pregunta):
    """
    Determina si una pregunta de seguimiento es ambigua por carecer de
    parámetros temporales, filtros o intención específica.
    """
    if not pregunta or not isinstance(pregunta, str):
        return False
    norm = normalizar_texto(pregunta)
    if not norm:
        return False

    # Frases claramente ambiguas
    patrones_ambiguos = [
        r"^(?:y|e)\s*$",
        r"^(?:y\s+)?entonces\??$",
        r"^(?:y\s+)?que\s+mas\??$",
        r"^(?:y\s+)?ahora\??$",
        r"^(?:y\s+)?que\s+sigue\??$",
        r"^(?:y\s+)?luego\??$",
        r"^como\??$"
    ]
    for p in patrones_ambiguos:
        if re.search(p, norm):
            return True

    # Si es pregunta de seguimiento pero no contiene periodo temporal ni sede
    if es_pregunta_seguimiento(pregunta):
        temp = resolver_expresion_temporal(pregunta)
        tiene_sede = any(re.search(r"\b" + re.escape(s) + r"\b", norm) for s in SEDES_VALIDAS)
        if not temp and not tiene_sede:
            return True

    return False


def sanitizar_contexto(contexto):
    """
    Filtra y conserva exclusivamente el contexto mínimo necesario:
    - intención y filtros de fecha (y sede si aplica).
    - NUNCA almacena nombres, DNI, IDs personales, respuestas completas ni datos crudos.
    """
    if not isinstance(contexto, dict):
        return {}

    limpio = {}
    for k in CAMPOS_CONTEXTO_PERMITIDOS:
        val = contexto.get(k)
        if val is not None:
            s_val = str(val).strip()
            if s_val:
                limpio[k] = s_val

    # Si la intención no es una de las intenciones agregadas/predictivas permitidas,
    # se descarta para evitar reusar búsquedas personales sin nombre.
    intencion = limpio.get("intencion")
    if intencion and intencion not in INTENCIONES_VALIDAS_CONTEXTO:
        limpio.pop("intencion", None)

    # Doble verificación estricta: asegurar que ningún campo sensible o prohibido permanezca
    for sensible in CAMPOS_SENSIBLES_PROHIBIDOS:
        limpio.pop(sensible, None)

    return limpio


def normalizar_texto(texto):
    """
    Normaliza el texto removiendo acentos, signos de interrogación/exclamación,
    convirtiendo a minúsculas y eliminando espacios adicionales.
    """
    if not texto:
        return ""
    t = str(texto).lower().strip()
    remplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u",
        "ü": "u",
        "¿": "", "?": "", "¡": "", "!": ""
    }
    for orig, dest in remplazos.items():
        t = t.replace(orig, dest)
    return " ".join(t.split())


def extraer_nombre_persona(pregunta_original):
    """
    Extrae el nombre de la persona mencionada en la pregunta.
    """
    patrones = [
        r"(?:historial|registro)\s+(?:de\s+)?(?:ingresos\s+(?:de\s+)?)?([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
        r"(?:[uú]ltimo\s+ingreso|[uú]ltima\s+visita)\s+(?:de\s+)?([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
        r"(?:cu[aá]ntas\s+veces\s+ingres[oó]|cu[aá]ntas\s+veces\s+entr[oó]|ingres[oó]|entr[oó])\s+(?:a\s+|de\s+)?([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
        r"(?:buscar\s+a)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
        r"(?:visitas|accesos)\s+(?:de\s+)?([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
    ]
    for p in patrones:
        m = re.search(p, pregunta_original, re.IGNORECASE)
        if m:
            nombre = m.group(1).strip()
            nombre = re.sub(
                r"\b(hoy|ayer|entre|del|desde|en|el|la|los|las|de|por|para|esta|semana|mes|pasado|biblioteca|sede)\b.*$",
                "",
                nombre,
                flags=re.IGNORECASE
            ).strip()
            if len(nombre) >= 3 and not re.match(r"^(ingresos|resumen|afluencia|biblioteca)$", nombre, re.IGNORECASE):
                return nombre
    return ""


def extraer_codigo_matricula(pregunta_original):
    """
    Extrae un código de matrícula mencionado en la pregunta.
    Formatos soportados: 'código 2020123456', 'código de matrícula 2020123456', etc.
    """
    if not pregunta_original:
        return ""
    m = re.search(r"\bc[oó]digo(?:\s+de\s+matr[ií]cula)?\s*[:#]?\s*([0-9a-zA-Z]{4,25})\b", pregunta_original, re.IGNORECASE)
    if m:
        c = m.group(1).strip()
        if not re.match(r"^(de|del|para|por|en|un|una|el|la|los|las|alumno|alumna)$", c, re.IGNORECASE):
            return c
    m2 = re.search(r"\balumno\s+(?:con\s+c[oó]digo\s+)?([0-9]{4,20})\b", pregunta_original, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    return ""


def extraer_parametros_texto(pregunta_original, norm, fecha_referencia=None):
    """
    Extrae parámetros auxiliares (fechas, hora, sede) directamente del texto si están presentes.
    """
    params = {}

    # 1. Extracción y validación temporal centralizada (fechas en español, ISO, relativas o errores)
    temp = resolver_expresion_temporal(pregunta_original, fecha_referencia)
    if temp:
        if "error_temporal" in temp:
            params["error_temporal"] = temp["error_temporal"]
            params["mensaje_error_temporal"] = temp.get("mensaje_error")
        if "fecha_inicio" in temp:
            params["fecha_inicio"] = temp["fecha_inicio"]
        if "fecha_fin" in temp:
            params["fecha_fin"] = temp["fecha_fin"]
        if "fecha" in temp:
            params["fecha"] = temp["fecha"]
        if "tipo_periodo" in temp:
            params["tipo_periodo"] = temp["tipo_periodo"]

    # 2. Hora: 'a las 10:00', 'a las 10', 'a la 1'
    m_hora = re.search(r"\b(?:a\s+las\s+|a\s+la\s+|hora\s+)(\d{1,2})(?::00)?\b", norm)
    if m_hora:
        try:
            h = int(m_hora.group(1))
            if 0 <= h <= 23:
                params["hora"] = h
        except (ValueError, TypeError):
            pass

    # 3. Sede
    for sede_clave, sede_nom in SEDES_VALIDAS.items():
        if re.search(r"\b(?:sede\s+)?" + re.escape(sede_clave) + r"\b", norm):
            params["sede"] = sede_nom
            break

    return params


def clasificar_intencion(pregunta, fecha_referencia=None):
    """
    Motor local determinista de intenciones sin APIs externas ni SQL.

    Prioridad de intenciones:
    1. Historial personal / Último ingreso: cuando hay nombre o persona identificada.
    2. Horas de mayor afluencia: preguntas por horas / horarios pico.
    3. Resumen de ingresos / estadísticas históricas: totales, resúmenes o afluencia pasada.
    4. Predicción de afluencia: preguntas sobre futuro, estimación, pronóstico o nivel esperado (excluyendo pasado).
    5. Intención desconocida.
    """
    if not pregunta or not isinstance(pregunta, str):
        return {
            "intencion": "intencion_desconocida",
            "parametros": {},
            "origen": "regla_local"
        }

    pregunta_limpia = pregunta.strip()

    if len(pregunta_limpia) > 500:
        return {
            "intencion": "intencion_desconocida",
            "parametros": {},
            "origen": "regla_local",
            "motivo": "pregunta_excede_limite_caracteres"
        }

    norm = normalizar_texto(pregunta_limpia)
    params_extra = extraer_parametros_texto(pregunta_limpia, norm, fecha_referencia=fecha_referencia)

    # Detectar si la pregunta contiene marcadores explícitos de tiempo pasado
    es_pasado = bool(re.search(
        r"\b(fue|fueron|ayer|antier|pasad[oa]s?|anterior(?:es)?|hist[oó]ric[oa]s?|registrad[oa]s?|ingresaron|entraron)\b",
        norm
    ))

    # =========================================================================
    # 0. PRIORIDAD 0: CONTEO DE INGRESOS DE ALUMNO POR CÓDIGO DE MATRÍCULA
    # =========================================================================
    codigo_mat = extraer_codigo_matricula(pregunta_limpia)
    if codigo_mat:
        if re.search(r"\b(ingres[oó]|ingresos|veces|visitas?|conteo|cu[aá]ntas?|accesos?|asistencia)\b", norm):
            params = dict(params_extra)
            params["codigo_matricula"] = codigo_mat
            return {
                "intencion": "conteo_ingresos_alumno",
                "parametros": params,
                "origen": "regla_local"
            }

    # =========================================================================
    # 1. PRIORIDAD 1: HISTORIAL PERSONAL O ÚLTIMO INGRESO
    # =========================================================================
    patrones_ultimo = [
        r"[uú]ltimo.*ingreso",
        r"[uú]ltima.*visita",
        r"cu[aá]ndo.*fue.*el.*[uú]ltimo",
        r"cu[aá]ndo.*ingres[oó].*por.*[uú]ltima"
    ]
    for p in patrones_ultimo:
        if re.search(p, norm):
            texto_persona = extraer_nombre_persona(pregunta_limpia)
            params = dict(params_extra)
            params["texto_persona"] = texto_persona
            return {
                "intencion": "ultimo_ingreso_persona",
                "parametros": params,
                "origen": "regla_local"
            }

    patrones_historial = [
        r"cu[aá]ntas.*veces.*ingres[oó]",
        r"cu[aá]ntas.*veces.*entr[oó]",
        r"cu[aá]ntas.*visitas",
        r"historial.*de",
        r"registro.*de.*ingresos.*de",
        r"buscar\s+a\b",
        r"accesos.*de\s+[a-z]",
        r"a.*qu[eé].*horas.*ingres[oó]"
    ]
    for p in patrones_historial:
        if re.search(p, norm):
            texto_persona = extraer_nombre_persona(pregunta_limpia)
            params = dict(params_extra)
            params["texto_persona"] = texto_persona
            return {
                "intencion": "historial_persona",
                "parametros": params,
                "origen": "regla_local"
            }

    # =========================================================================
    # 2. PRIORIDAD 2: HORAS DE MAYOR AFLUENCIA (preguntas por horas / pico)
    # =========================================================================
    patrones_horas = [
        r"horas?.*(?:mayor|mas).*afluencia",
        r"horas?.*pico",
        r"horarios?.*pico",
        r"a.*qu[eé].*horas?.*(?:hay|ingresan|vienen).*m[aá]s",
        r"horarios?.*con.*m[aá]s.*(?:ingresos|afluencia|gente)",
        r"horas?.*con.*m[aá]s.*(?:ingresos|afluencia|gente)",
        r"hora.*con.*m[aá]s.*gente",
        r"afluencia.*por.*hora"
    ]
    for p in patrones_horas:
        if re.search(p, norm):
            return {
                "intencion": "horas_mayor_afluencia",
                "parametros": params_extra,
                "origen": "regla_local"
            }

    # =========================================================================
    # 2.5. PRIORIDAD 2.5: AFLUENCIA HISTÓRICA DESCRIPTIVA (consultas agregadas analíticas)
    # =========================================================================
    patrones_afluencia_hist = [
        r"afluencia.*hist[oó]rica",
        r"consulta.*descriptiva.*afluencia",
        r"hist[oó]rico.*(?:de\s+)?afluencia",
        r"comportamiento.*hist[oó]rico.*afluencia",
        r"comportamiento.*hist[oó]rico",
        r"afluencia.*en\s+(?:la\s+)?sede",
        r"afluencia.*de\s+(?:la\s+)?sede"
    ]
    for p in patrones_afluencia_hist:
        if re.search(p, norm):
            return {
                "intencion": "afluencia_historica",
                "parametros": params_extra,
                "origen": "regla_local"
            }

    # =========================================================================
    # 3. PRIORIDAD 3: RESUMEN DE INGRESOS (cantidad histórica / resumen / ayer / mes pasado)
    # =========================================================================
    patrones_resumen = [
        r"resumen.*ingresos",
        r"resumen.*de.*ingresos",
        r"total.*ingresos",
        r"total.*de.*ingresos",
        r"cu[aá]ntos.*ingresos",
        r"cu[aá]ntas.*personas.*ingresaron",
        r"cu[aá]ntos.*alumnos.*ingresaron",
        r"estad[ií]sticas.*(?:de\s+)?ingresos",
        r"reporte.*(?:de\s+)?ingresos",
        r"resumen.*(?:de\s+)?afluencia",
        r"afluencia.*(?:ayer|pasad[oa]|historica|registrada|anterior)",
        r"(?:cu[aá]l|c[oó]mo)\s+fue.*(?:la\s+)?afluencia",
        r"cu[aá]nta.*gente.*(?:ingres[oó]|entr[oó]|vino|hubo)",
        r"cu[aá]ntas.*personas.*(?:entraron|visitaron|ingresaron)"
    ]
    for p in patrones_resumen:
        if re.search(p, norm):
            return {
                "intencion": "resumen_ingresos",
                "parametros": params_extra,
                "origen": "regla_local"
            }

    # =========================================================================
    # 4. PRIORIDAD 4: PREDICCIÓN DE AFLUENCIA (futuro, estimación, pronóstico, nivel esperado)
    # Solo cuando NO se exprese en tiempo pasado
    # =========================================================================
    if not es_pasado:
        patrones_prediccion = [
            r"\bpredicci[oó]n\b",
            r"\bpredecir\b",
            r"\bpron[oó]stico\b",
            r"\bestimaci[oó]n.*afluencia",
            r"\bestimar.*afluencia",
            r"afluencia.*estimada",
            r"(?:cu[aá]l|qu[eé]|c[oó]mo)\s+ser[aá]\b.*afluencia",
            r"\bser[aá]\b.*(?:el\s+)?(?:nivel\s+de\s+)?afluencia",
            r"afluencia.*esperada",
            r"(?:nivel\s+de\s+)?afluencia.*esperad[oa]",
            r"\bhabr[aá]\b.*(?:mucha|bastante|alta|baja|poca|demasiada|gran)?\s*(?:gente|personas|afluencia|concurrencia)",
            r"\bestar[aá]\b.*(?:llena|concurrida|vacia)",
            r"\bcu[aá]nta\b.*gente.*\bhabr[aá]\b",
            r"afluencia.*ma[nñ]ana",
            r"afluencia.*futura",
            r"proyecci[oó]n.*afluencia",
            r"afluencia.*proyectada"
        ]
        for p in patrones_prediccion:
            if re.search(p, norm):
                return {
                    "intencion": "prediccion_afluencia",
                    "parametros": params_extra,
                    "origen": "regla_local"
                }

    # =========================================================================
    # 5. INTENCIÓN DESCONOCIDA
    # =========================================================================
    return {
        "intencion": "intencion_desconocida",
        "parametros": params_extra,
        "origen": "regla_local"
    }


if __name__ == "__main__":
    preguntas_prueba = [
        "¿Cuál será el nivel de afluencia?",
        "Predicción de afluencia",
        "Predecir la afluencia",
        "¿Cuál será la afluencia?",
        "Afluencia esperada",
        "Nivel de afluencia esperado",
        "¿Habrá mucha gente?",
        "¿Estará llena la biblioteca?",
        "Pronóstico de afluencia",
        "Estimar afluencia",
        "¿Cuáles fueron las horas de mayor afluencia?",
        "Resumen de afluencia del mes pasado",
        "¿Cuántas personas ingresaron?",
        "Historial de ingresos de Juan Pérez",
        "¿Cuál fue la afluencia de ayer?"
    ]

    print("=== MOTOR LOCAL DE INTENCIONES (VERIFICACIÓN) ===")
    for preg in preguntas_prueba:
        res = clasificar_intencion(preg)
        print(f"Pregunta: {preg}")
        print(f"Intención: {res['intencion']} | Params: {res.get('parametros')}\n")
