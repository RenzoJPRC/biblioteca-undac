import re
from typing import Dict, Any, Tuple, Optional
from llm.services.motor_intenciones import (
    extraer_nombre_persona,
    extraer_codigo_matricula,
    normalizar_texto,
    CAMPOS_SENSIBLES_PROHIBIDOS
)

# Patrón para documentos de identidad de 8 dígitos continuos
PATRON_DNI = re.compile(r"\b\d{8}\b")

# Patrones para documentos de identidad con guiones o espacios (4-4 o 2-2-2-2)
PATRON_DNI_CON_SEPARADORES = re.compile(
    r"\b\d{4}[\s\-]\d{4}\b|\b\d{2}[\s\-]\d{2}[\s\-]\d{2}[\s\-]\d{2}\b"
)

# Patrón para menciones explícitas de documento / DNI
PATRON_DNI_EXPLICITO = re.compile(
    r"\b(?:dni|documento|carnet|doc|identidad)\b\s*[:#]?\s*[\d\s\-]{6,14}\b",
    re.IGNORECASE
)

# Patrón para códigos de matrícula con guiones, espacios o prefijos
PATRON_CODIGO_FORMATOS = re.compile(
    r"\b(?:c[oó]digo|matr[ií]cula|carnet)\s*(?:de\s+matr[ií]cula)?\s*[:#]?\s*([0-9a-zA-Z\s\-]{4,20})\b",
    re.IGNORECASE
)

# Patrones explícitos de consultas individuales sobre personas
PATRONES_CONSULTA_INDIVIDUAL = [
    r"\b(?:historial|registro|visitas|accesos)\s+de\b",
    r"\b(?:ultimo|ultima)\s+(?:ingreso|visita|acceso)\b",
    r"\bcuantas\s+veces\s+(?:ingreso|entro|vino|asistio)\b",
    r"\ba\s+que\s+horas\s+(?:ingreso|entro)\b",
    r"\bbuscar\s+a\b",
    r"\balumno\s+(?:con\s+codigo|matricula)\b",
    r"\bcodigo(?:\s+de\s+matricula)?\s*[:#]?\s*[0-9a-zA-Z\s\-]{4,25}\b"
]

# Palabras, verbos y giros de preguntas indirectas que sugieren una consulta sobre individuos o personas
# Ante duda sobre cualquiera de estos términos, la consulta se deriva al motor local.
TERMINOS_SOSPECHOSOS_INDIVIDUALES = [
    r"\b(?:vino|asistio|estuvo|entro|salio|llego|falto)\b",
    r"\b(?:quien|quienes)\b",
    r"\b(?:profesor|profesora|docente|alumno|alumna|estudiante|egresado|egresada|visitante|usuario)\b",
    r"\b(?:buscar|consultar|verificar|saber|averiguar|fijarte|decirme|confirmar)\s+(?:de|a|si)\b",
    r"\b(?:alguien|algun\s+alumno|alguna\s+persona|cierto\s+alumno|esa\s+persona)\b",
    r"\b(?:su\s+asistencia|su\s+ingreso|su\s+historial|sus\s+visitas)\b",
    r"\b(?:registro\s+del\s+alumno|visitas\s+de\s+[a-záéíóúñ]+)\b"
]

# Lista blanca estricta de intenciones analíticas que PUEDEN enviarse al LLM externo
INTENCIONES_PERMITIDAS_LLM = {
    "resumen_ingresos",
    "horas_mayor_afluencia",
    "afluencia_historica",
    "prediccion_afluencia"
}

# Patrón para detectar fugas o alucinaciones de sentencias SQL en la salida
PATRON_SQL = re.compile(
    r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+.+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE|UNION\s+SELECT|EXEC\s+sp_)\b",
    re.IGNORECASE
)

# Patrón de etiquetas de script o inyecciones de marcado
PATRON_SCRIPT = re.compile(r"<\s*script[^>]*>|javascript\s*:", re.IGNORECASE)

# Patrón para detectar afirmaciones personales o nombres propios en la salida agregada
PATRON_AFIRMACION_PERSONAL_SALIDA = re.compile(
    r"\b(?:el\s+alumno|la\s+alumna|el\s+docente|el\s+profesor|el\s+usuario|la\s+persona)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)\b"
)


def detectar_dni_en_texto(texto: str) -> bool:
    """
    Detecta documentos de identidad en formatos continuos (8 dígitos), con guiones
    (4-4 o 2-2-2-2) o con espacios (4 4 o 2 2 2 2), excluyendo fechas ISO (YYYY-MM-DD).
    """
    if not texto or not isinstance(texto, str):
        return False

    # 1. DNI directo continuo de 8 dígitos
    if PATRON_DNI.search(texto):
        return True

    # 2. DNI precedido de palabra clave explícita
    if PATRON_DNI_EXPLICITO.search(texto):
        return True

    # 3. DNI con separadores específicos de 4-4 o 2-2-2-2
    if PATRON_DNI_CON_SEPARADORES.search(texto):
        return True

    # 4. Secuencia general de 8 dígitos con separadores opcionales (excluyendo fechas ISO YYYY-MM-DD)
    coincidencias = re.finditer(r"\b(?:\d[\s\-]?){7}\d\b", texto)
    for c in coincidencias:
        match_str = c.group(0).strip()
        # Si coincide exactamente con el formato de fecha ISO YYYY-MM-DD, no es DNI
        if re.match(r"^\d{4}-\d{2}-\d{2}$", match_str):
            continue
        solo_digitos = re.sub(r"\D", "", match_str)
        if len(solo_digitos) == 8:
            return True

    return False


def detectar_codigo_matricula_en_texto(texto: str) -> bool:
    """
    Detecta códigos de matrícula en diversos formatos (continuo, con guiones, con espacios).
    """
    if not texto or not isinstance(texto, str):
        return False

    # 1. Extractor estándar
    cod = extraer_codigo_matricula(texto)
    if cod:
        return True

    # 2. Formato con guiones o espacios precedido de palabra clave
    m = PATRON_CODIGO_FORMATOS.search(texto)
    if m:
        c = m.group(1).strip()
        solo_alnum = re.sub(r"[\s\-]", "", c)
        if len(solo_alnum) >= 4 and not re.match(r"^(de|del|para|por|en|un|una|el|la|los|las|alumno|alumna)$", solo_alnum, re.IGNORECASE):
            return True

    # 3. Alumno con código separado por guión o espacio
    m2 = re.search(r"\balumno\s+(?:con\s+c[oó]digo\s+)?([0-9]{4}[\s\-][0-9]{4,10})\b", texto, re.IGNORECASE)
    if m2:
        return True

    return False


def es_consulta_personal_o_identificable(pregunta, seleccion_persona=None, contexto=None) -> Tuple[bool, str]:
    """
    Determina si una consulta involucra datos personales, individuales o identificables
    (nombres, DNI, códigos de matrícula con cualquier formato, historiales o preguntas indirectas).
    """
    # 1. Si ya se envió una selección de persona (desambiguación en curso)
    if seleccion_persona and isinstance(seleccion_persona, dict):
        return True, "seleccion_persona_presente"

    if not pregunta or not isinstance(pregunta, str):
        return False, "pregunta_vacia"

    pregunta_limpia = pregunta.strip()
    norm = normalizar_texto(pregunta_limpia)

    # 2. Detección de DNI en cualquier formato (continuo, con guiones o con espacios)
    if detectar_dni_en_texto(pregunta_limpia):
        return True, "dni_detectado"

    # 3. Detección de código de matrícula en cualquier formato
    if detectar_codigo_matricula_en_texto(pregunta_limpia):
        return True, "codigo_matricula_detectado"

    # 4. Detección de nombre de persona por regex
    nombre = extraer_nombre_persona(pregunta_limpia)
    if nombre:
        return True, f"nombre_persona_detectado:{nombre}"

    # 5. Patrones sintácticos de consulta individual sobre personas
    for pat in PATRONES_CONSULTA_INDIVIDUAL:
        if re.search(pat, norm):
            return True, "patron_consulta_individual"

    # 6. Detección de giros y preguntas indirectas sobre individuos
    for term in TERMINOS_SOSPECHOSOS_INDIVIDUALES:
        if re.search(term, norm):
            return True, f"pregunta_indirecta_o_termino_sospechoso:{term}"

    # 7. Inspección del contexto previo por si arrastra referencias a personas
    if isinstance(contexto, dict):
        for campo in CAMPOS_SENSIBLES_PROHIBIDOS:
            if contexto.get(campo):
                return True, f"contexto_sensible:{campo}"
        if contexto.get("codigo_matricula"):
            return True, "codigo_matricula_en_contexto"

    return False, "consulta_agregada_o_general"


def es_consulta_permitida_para_llm(
    pregunta: str,
    intencion: str,
    es_seguimiento: bool,
    seleccion_persona: Optional[Dict[str, Any]] = None,
    contexto: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """
    Política estricta de lista blanca para decidir si una consulta puede enviarse al LLM externo.

    Criterio fundamental de seguridad:
    - NO depende únicamente de expresiones regulares de nombres.
    - Ante cualquier duda, ambigüedad, seguimiento sin contexto previo o intención desconocida,
      la consulta DEBE resolverse en el motor local.
    """
    # Regla 1: Las selecciones individuales de persona se resuelven exclusivamente de forma local
    if seleccion_persona:
        return False, "seleccion_persona_local"

    # Regla 2: En esta etapa, los seguimientos se resuelven localmente para evitar alucinaciones o falta de memoria
    if es_seguimiento:
        return False, "seguimiento_conversacional_local"

    # Regla 3: Si se detecta cualquier entidad personal, DNI en cualquier formato o pregunta indirecta
    es_pers, mot_pers = es_consulta_personal_o_identificable(pregunta, seleccion_persona, contexto)
    if es_pers:
        return False, f"consulta_personal_bloqueada:{mot_pers}"

    # Regla 4: Lista blanca estricta de intenciones permitidas para el LLM
    if intencion not in INTENCIONES_PERMITIDAS_LLM:
        return False, f"intencion_no_autorizada_para_llm:{intencion}"

    return True, "consulta_agregada_autorizada"


def sanitizar_texto_para_llm(texto: str) -> str:
    """
    Asegura que cualquier texto que viaje hacia el LLM esté completamente
    despojado de DNIs en cualquier formato o códigos de matrícula.
    """
    if not texto or not isinstance(texto, str):
        return ""

    limpio = texto
    # Enmascarar números de 8 dígitos continuos
    limpio = PATRON_DNI.sub("[DOCUMENTO_RESERVADO]", limpio)
    # Enmascarar números con guiones o espacios de 4-4 o 2-2-2-2
    limpio = PATRON_DNI_CON_SEPARADORES.sub("[DOCUMENTO_RESERVADO]", limpio)
    return limpio


def sanitizar_datos_herramienta_para_llm(datos: Any) -> Any:
    """
    Garantiza que la información enviada como resultado de una herramienta al LLM
    contenga EXCLUSIVAMENTE métricas agregadas o valores analíticos,
    eliminando cualquier clave o registro individual sensible.
    """
    if isinstance(datos, dict):
        limpio = {}
        for k, v in datos.items():
            k_lower = str(k).lower()
            if any(sensible in k_lower for sensible in ["dni", "persona", "nombre", "apellido", "codigo_mat", "correo", "email", "telefono"]):
                continue
            limpio[k] = sanitizar_datos_herramienta_para_llm(v)
        return limpio
    elif isinstance(datos, list):
        return [sanitizar_datos_herramienta_para_llm(item) for item in datos]
    elif isinstance(datos, str):
        return sanitizar_texto_para_llm(datos)
    else:
        return datos


def validar_y_sanitizar_respuesta_llm(mensaje: str) -> Tuple[bool, str, str]:
    """
    Validador de salida (Output Guardrail) antes de mostrar la respuesta al usuario.

    Verificaciones:
    1. Comprueba si el LLM intentó generar o fugar sentencias SQL.
    2. Comprueba si contiene secuencias numéricas de DNI en cualquier formato y las enmascara.
    3. Enmascara menciones de códigos de matrícula.
    4. Enmascara afirmaciones sobre personas individuales alucinadas.
    5. Neutraliza etiquetas HTML/scripts peligrosas.

    Retorna:
        tuple (es_valida: bool, texto_sanitizado: str, motivo: str)
    """
    if not mensaje or not isinstance(mensaje, str):
        return True, "", "vacio"

    # 1. Detección de sentencias SQL en la salida
    if PATRON_SQL.search(mensaje):
        return False, "", "deteccion_sentencia_sql_en_salida"

    # 2. Detección de inyecciones de script
    if PATRON_SCRIPT.search(mensaje):
        return False, "", "deteccion_script_en_salida"

    # 3. Enmascarar posibles DNIs alucinados (continuos o con separadores)
    mensaje_sanitizado = PATRON_DNI.sub("[DOCUMENTO_RESERVADO]", mensaje)
    mensaje_sanitizado = PATRON_DNI_CON_SEPARADORES.sub("[DOCUMENTO_RESERVADO]", mensaje_sanitizado)

    # 4. Enmascarar posibles códigos de matrícula alucinados
    mensaje_sanitizado = PATRON_CODIGO_FORMATOS.sub("código [CÓDIGO_RESERVADO]", mensaje_sanitizado)

    # 5. Enmascarar atribuciones individuales alucinadas
    mensaje_sanitizado = PATRON_AFIRMACION_PERSONAL_SALIDA.sub(r"\1 [PERSONA_RESERVADA]", mensaje_sanitizado)

    return True, mensaje_sanitizado.strip(), "ok"
