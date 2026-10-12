import re
from datetime import date, datetime, timedelta

SEDES_VALIDAS = {
    "central": "Central",
    "tarma": "Tarma",
    "la merced": "La Merced",
    "oxapampa": "Oxapampa",
    "paucartambo": "Paucartambo",
    "yanahuanca": "Yanahuanca"
}

CAMPOS_CONTEXTO_PERMITIDOS = {"intencion", "fecha_inicio", "fecha_fin", "fecha", "sede"}
INTENCIONES_VALIDAS_CONTEXTO = {"resumen_ingresos", "horas_mayor_afluencia", "prediccion_afluencia"}
CAMPOS_SENSIBLES_PROHIBIDOS = {
    "nombre", "texto_persona", "dni", "persona_id", "id", "persona",
    "candidato", "opciones", "historial", "registros", "ultimo_registro",
    "respuesta", "mensaje", "datos"
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


def resolver_expresion_temporal(texto, fecha_referencia=None):
    """
    Resuelve expresiones temporales relativas y absolutas a fechas ISO (YYYY-MM-DD).
    Soporta:
    - 'ayer', 'de ayer', 'para ayer' -> día anterior
    - 'hoy', 'de hoy', 'para hoy' -> día actual
    - 'anteayer', 'antier' -> dos días anteriores
    - 'la semana pasada' -> lunes a domingo de la semana calendario anterior
    - 'el mes pasado' -> primer y último día del mes calendario anterior
    - 'del YYYY-MM-DD al YYYY-MM-DD' -> rango explícito
    - 'YYYY-MM-DD' -> fecha puntual explícita
    """
    if not texto or not isinstance(texto, str):
        return None

    ref = fecha_referencia if isinstance(fecha_referencia, (date, datetime)) else obtener_fecha_referencia()
    if isinstance(ref, datetime):
        ref = ref.date()

    # 1. Rango explícito: del YYYY-MM-DD al YYYY-MM-DD
    m_rango = re.search(r"\bdel\s+(\d{4}-\d{2}-\d{2})\s+al\s+(\d{4}-\d{2}-\d{2})\b", texto, re.IGNORECASE)
    if m_rango:
        return {
            "fecha_inicio": m_rango.group(1),
            "fecha_fin": m_rango.group(2),
            "tipo_periodo": "rango"
        }

    # 2. Fecha explícita única: YYYY-MM-DD
    m_fecha = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", texto)
    if m_fecha:
        f_str = m_fecha.group(1)
        return {
            "fecha_inicio": f_str,
            "fecha_fin": f_str,
            "fecha": f_str,
            "tipo_periodo": "fecha_fija"
        }

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
                r"\b(hoy|ayer|en|el|la|los|las|de|por|para|esta|semana|mes|pasado|biblioteca|sede)\b.*$",
                "",
                nombre,
                flags=re.IGNORECASE
            ).strip()
            if len(nombre) >= 3 and not re.match(r"^(ingresos|resumen|afluencia|biblioteca)$", nombre, re.IGNORECASE):
                return nombre
    return ""


def extraer_parametros_texto(pregunta_original, norm, fecha_referencia=None):
    """
    Extrae parámetros auxiliares (fechas, hora, sede) directamente del texto si están presentes.
    """
    params = {}

    # 1. Rango de fechas: 'del YYYY-MM-DD al YYYY-MM-DD'
    m_rango = re.search(r"\bdel\s+(\d{4}-\d{2}-\d{2})\s+al\s+(\d{4}-\d{2}-\d{2})\b", pregunta_original, re.IGNORECASE)
    if m_rango:
        params["fecha_inicio"] = m_rango.group(1)
        params["fecha_fin"] = m_rango.group(2)

    # 2. Fecha única: YYYY-MM-DD
    if "fecha_inicio" not in params:
        m_fecha = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", pregunta_original)
        if m_fecha:
            params["fecha"] = m_fecha.group(1)

    # 3. Expresiones temporales relativas si no hay fechas explícitas
    if "fecha_inicio" not in params and "fecha" not in params:
        temp = resolver_expresion_temporal(pregunta_original, fecha_referencia)
        if temp:
            if "fecha_inicio" in temp:
                params["fecha_inicio"] = temp["fecha_inicio"]
            if "fecha_fin" in temp:
                params["fecha_fin"] = temp["fecha_fin"]
            if "fecha" in temp:
                params["fecha"] = temp["fecha"]

    # 4. Hora: 'a las 10:00', 'a las 10', 'a la 1'
    m_hora = re.search(r"\b(?:a\s+las\s+|a\s+la\s+|hora\s+)(\d{1,2})(?::00)?\b", norm)
    if m_hora:
        try:
            h = int(m_hora.group(1))
            if 0 <= h <= 23:
                params["hora"] = h
        except (ValueError, TypeError):
            pass

    # 5. Sede
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
