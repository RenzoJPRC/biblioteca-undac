import re

SEDES_VALIDAS = {
    "central": "Central",
    "tarma": "Tarma",
    "la merced": "La Merced",
    "oxapampa": "Oxapampa",
    "paucartambo": "Paucartambo",
    "yanahuanca": "Yanahuanca"
}


def normalizar_texto(texto):
    """
    Normaliza el texto removiendo acentos, convirtiendo a minúsculas y eliminando espacios adicionales.
    """
    if not texto:
        return ""
    t = str(texto).lower().strip()
    remplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u",
        "ü": "u"
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


def extraer_parametros_texto(pregunta_original, norm):
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

    # 3. Hora: 'a las 10:00', 'a las 10', 'a la 1'
    m_hora = re.search(r"\b(?:a\s+las\s+|a\s+la\s+|hora\s+)(\d{1,2})(?::00)?\b", norm)
    if m_hora:
        try:
            h = int(m_hora.group(1))
            if 0 <= h <= 23:
                params["hora"] = h
        except (ValueError, TypeError):
            pass

    # 4. Sede
    for sede_clave, sede_nom in SEDES_VALIDAS.items():
        if re.search(r"\b(?:sede\s+)?" + re.escape(sede_clave) + r"\b", norm):
            params["sede"] = sede_nom
            break

    return params


def clasificar_intencion(pregunta):
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
    params_extra = extraer_parametros_texto(pregunta_limpia, norm)

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
