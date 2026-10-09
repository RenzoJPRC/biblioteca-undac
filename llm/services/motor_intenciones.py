import re


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
        r"(?:historial|ingresos|visitas|registro|ingres[oó]|entr[oó])\s+(?:de\s+)?([A-ZÁÉÍÓÚÑa-záéíóúñ\s]{3,60})",
    ]
    for p in patrones:
        m = re.search(p, pregunta_original, re.IGNORECASE)
        if m:
            nombre = m.group(1).strip()
            nombre = re.sub(r"\b(hoy|ayer|en|el|la|los|las|de|por|para|esta|semana|mes|pasado)\b.*$", "", nombre, flags=re.IGNORECASE).strip()
            if len(nombre) >= 3:
                return nombre
    return ""


def clasificar_intencion(pregunta):
    """
    Motor local determinista de intenciones sin APIs externas ni SQL.

    Evalúa la pregunta en lenguaje natural y devuelve la intención detectada
    y los parámetros extraídos.
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

    # 1. Intención: prediccion_afluencia
    patrones_prediccion = [
        r"predicci[oó]n",
        r"predecir",
        r"pron[oó]stico",
        r"estimaci[oó]n.*afluencia",
        r"cu[aá]nta.*gente.*habr[aá]",
        r"afluencia.*ma[nñ]ana",
        r"afluencia.*futura"
    ]
    for p in patrones_prediccion:
        if re.search(p, norm):
            return {
                "intencion": "prediccion_afluencia",
                "parametros": {},
                "origen": "regla_local"
            }

    # 2. Intención: horas_mayor_afluencia
    patrones_horas = [
        r"horas.*mayor.*afluencia",
        r"horas.*pico",
        r"horarios.*pico",
        r"a.*qu[eé].*horas.*hay.*m[aá]s",
        r"a.*qu[eé].*horas.*ingresan.*m[aá]s",
        r"horarios.*con.*m[aá]s.*ingresos",
        r"horas.*con.*mas.*ingresos"
    ]
    for p in patrones_horas:
        if re.search(p, norm):
            return {
                "intencion": "horas_mayor_afluencia",
                "parametros": {},
                "origen": "regla_local"
            }

    # 3. Intención: resumen_ingresos (Verificar antes de historial para evitar falsos positivos con palabras clave genéricas)
    patrones_resumen = [
        r"resumen.*ingresos",
        r"resumen.*de.*ingresos",
        r"total.*ingresos",
        r"total.*de.*ingresos",
        r"cu[aá]ntos.*ingresos",
        r"cu[aá]ntos.*alumnos.*ingresaron",
        r"estad[ií]sticas.*ingresos",
        r"reporte.*ingresos"
    ]
    for p in patrones_resumen:
        if re.search(p, norm):
            return {
                "intencion": "resumen_ingresos",
                "parametros": {},
                "origen": "regla_local"
            }

    # 4. Intención: ultimo_ingreso_persona
    patrones_ultimo = [
        r"[uú]ltimo.*ingreso",
        r"[uú]ltima.*visita",
        r"cu[aá]ndo.*fue.*el.*[uú]ltimo",
        r"cu[aá]ndo.*ingres[oó].*por.*[uú]ltima"
    ]
    for p in patrones_ultimo:
        if re.search(p, norm):
            texto_persona = extraer_nombre_persona(pregunta_limpia)
            return {
                "intencion": "ultimo_ingreso_persona",
                "parametros": {
                    "texto_persona": texto_persona
                },
                "origen": "regla_local"
            }

    # 5. Intención: historial_persona
    patrones_historial = [
        r"cu[aá]ntas.*veces.*ingres[oó]",
        r"cu[aá]ntas.*veces.*entr[oó]",
        r"cu[aá]ntas.*visitas",
        r"historial.*de",
        r"registro.*de.*ingresos.*de",
        r"a.*qu[eé].*horas.*ingres[oó]"
    ]
    for p in patrones_historial:
        if re.search(p, norm):
            texto_persona = extraer_nombre_persona(pregunta_limpia)
            return {
                "intencion": "historial_persona",
                "parametros": {
                    "texto_persona": texto_persona
                },
                "origen": "regla_local"
            }

    return {
        "intencion": "intencion_desconocida",
        "parametros": {},
        "origen": "regla_local"
    }


if __name__ == "__main__":
    preguntas_prueba = [
        "¿Cuántas veces ingresó Renzo Rojas Castillo?",
        "¿Cuándo fue el último ingreso de Maria Lopez?",
        "¿Cuáles son las horas con mayor afluencia?",
        "Generar una predicción de afluencia para mañana",
        "Resumen de ingresos del mes pasado",
        "¿Cuál es el clima de hoy?"
    ]

    print("=== MOTOR LOCAL DE INTENCIONES (PRUEBAS) ===")
    for preg in preguntas_prueba:
        res = clasificar_intencion(preg)
        print(f"Pregunta: {preg}")
        print(f"Resultado: {res}\n")
