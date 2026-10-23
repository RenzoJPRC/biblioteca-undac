import os
import json
import time
import logging
import requests
from typing import Dict, Any, Optional, Tuple

from llm.services.consultas_biblioteca import (
    validar_rol,
    obtener_resumen_ingresos,
    obtener_horas_mayor_afluencia,
    consultar_afluencia_historica,
    SEDES_PERMITIDAS
)
from ml.services.servicio_afluencia import predecir_afluencia_desde_bd

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Excepción base para errores de comunicación con el LLM."""
    pass


class LLMTimeoutError(LLMError):
    """Tiempo de espera agotado al invocar al LLM."""
    pass


class LLMNetworkError(LLMError):
    """Fallo de conexión o red con el proveedor LLM."""
    pass


class LLMAPIError(LLMError):
    """Error devuelto por la API del proveedor LLM (ej. 401, 429, 500)."""
    def __init__(self, mensaje: str, status_code: int = 500, categoria=None):
        super().__init__(mensaje)
        self.status_code = status_code
        self.categoria = categoria or {401:'autenticacion',403:'autenticacion',429:'cuota',404:'modelo_o_endpoint'}.get(status_code, 'respuesta_api')


def error_api_seguro(respuesta):
    """Inspeccionar el error sin conservar ni imprimir cuerpo, URL o credenciales."""
    categoria = {400:'configuracion',401:'autenticacion',403:'autenticacion',429:'cuota',404:'modelo_o_endpoint'}.get(respuesta.status_code, 'servidor' if respuesta.status_code >= 500 else 'respuesta_api')
    try:
        error = respuesta.json().get('error', {})
        texto = str(error.get('message', '')).lower()
        if 'api key' in texto or 'api_key' in texto or 'unauthenticated' in texto:
            categoria = 'autenticacion'
        elif 'quota' in texto or 'resource_exhausted' in texto:
            categoria = 'cuota'
        elif 'model' in texto and ('not found' in texto or 'not supported' in texto):
            categoria = 'modelo'
    except Exception:
        pass
    return LLMAPIError(f'Proveedor no disponible: {categoria} (HTTP {respuesta.status_code}).', respuesta.status_code, categoria)


def clasificar_error(error):
    if isinstance(error, LLMAPIError): return error.categoria
    if isinstance(error, (LLMTimeoutError, requests.exceptions.Timeout)): return 'timeout'
    if isinstance(error, (LLMNetworkError, requests.exceptions.RequestException)): return 'conexion'
    if isinstance(error, LLMSafetyViolationError): return 'respuesta_no_verificable'
    return 'respuesta_invalida'


class LLMSafetyViolationError(LLMError):
    """La respuesta generada por el LLM violó las reglas de seguridad o filtros de salida."""
    pass


from llm.services.filtro_privacidad import (
    sanitizar_datos_herramienta_para_llm,
    validar_y_sanitizar_respuesta_llm
)


class ConfiguracionLLM:
    """
    Gestiona la configuración del único proveedor LLM activo,
    leída exclusivamente desde variables de entorno del servidor.
    Soporta OpenAI y Gemini Developer API (vía endpoint de compatibilidad OpenAI).
    """
    def __init__(self):
        self.proveedor = os.environ.get("LLM_PROVEEDOR", "openai").strip().lower()
        self.activado = os.environ.get("LLM_ACTIVADO", "false").strip().lower() in ("true", "1", "yes")
        self.api_key = os.environ.get("LLM_API_KEY", "").strip()

        # Valores por defecto inteligentes según el proveedor seleccionado
        if self.proveedor == "gemini":
            # No asumir un modelo fijo para Gemini; el usuario debe configurarlo según disponibilidad en AI Studio
            default_modelo = ""
            default_api_base = "https://generativelanguage.googleapis.com/v1beta/openai"
        else:
            default_modelo = "gpt-4o-mini"
            default_api_base = "https://api.openai.com/v1"

        modelo_env = os.environ.get("LLM_MODELO")
        self.modelo = modelo_env.strip() if (modelo_env is not None and modelo_env.strip()) else default_modelo

        api_base_env = os.environ.get("LLM_API_BASE")
        if api_base_env is not None and api_base_env.strip():
            self.api_base = api_base_env.strip().rstrip("/")
        else:
            self.api_base = default_api_base

        try:
            self.timeout = int(os.environ.get("LLM_TIMEOUT", "10").strip())
        except (ValueError, TypeError):
            self.timeout = 10

        if self.timeout <= 0:
            self.timeout = 10

    @property
    def esta_disponible(self) -> bool:
        """Indica si el proveedor está habilitado y cuenta con credencial y modelo válidos configurados."""
        return self.activado and bool(self.api_key) and bool(self.modelo)

    def to_dict(self) -> Dict[str, Any]:
        """Devuelve representación segura de la configuración (sin exponer la clave)."""
        return {
            "proveedor": self.proveedor,
            "activado": self.activado,
            "disponible": self.esta_disponible,
            "modelo": self.modelo,
            "api_base": self.api_base,
            "timeout": self.timeout
        }


def obtener_configuracion() -> ConfiguracionLLM:
    """Retorna una instancia actualizada de la configuración desde el entorno."""
    return ConfiguracionLLM()


# =========================================================================
# ESQUEMAS FORMALES DE HERRAMIENTAS PERMITIDAS (FUNCTION CALLING)
# =========================================================================
# Restricción fundamental: No se permite generación de SQL por el modelo.
# Solo se exponen funciones específicas de backend con validación interna.

HERRAMIENTAS_BIBLIOTECA = [
    {
        "type": "function",
        "function": {
            "name": "obtener_resumen_ingresos",
            "description": "Obtiene estadísticas y totales agregados de ingresos a la biblioteca en un rango de fechas (máx. 366 días).",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_inicio": {
                        "type": "string",
                        "description": "Fecha de inicio del periodo en formato ISO (YYYY-MM-DD)."
                    },
                    "fecha_fin": {
                        "type": "string",
                        "description": "Fecha de fin del periodo en formato ISO (YYYY-MM-DD)."
                    },
                    "sede": {
                        "type": "string",
                        "description": "Sede a filtrar (Central, Tarma, La Merced, Oxapampa, Paucartambo, Yanahuanca) o vacío para todas.",
                        "enum": ["Central", "Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
                    }
                },
                "required": ["fecha_inicio", "fecha_fin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_horas_mayor_afluencia",
            "description": "Obtiene las horas pico o franjas horarias con mayor concurrencia de ingresos en un periodo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_inicio": {
                        "type": "string",
                        "description": "Fecha inicial en formato YYYY-MM-DD."
                    },
                    "fecha_fin": {
                        "type": "string",
                        "description": "Fecha final en formato YYYY-MM-DD."
                    },
                    "sede": {
                        "type": "string",
                        "description": "Sede de biblioteca a consultar o vacío para todas.",
                        "enum": ["Central", "Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cantidad de horas pico a incluir (por defecto 5, máximo 24)."
                    }
                },
                "required": ["fecha_inicio", "fecha_fin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_afluencia_historica",
            "description": "Consulta descriptiva de afluencia histórica agregada desde AfluenciaHistoricaAgregada. Excluye feriados y cierres. Informa franjas parciales de 20:00 (45 min).",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_inicio": {
                        "type": "string",
                        "description": "Fecha de inicio en formato YYYY-MM-DD."
                    },
                    "fecha_fin": {
                        "type": "string",
                        "description": "Fecha de fin en formato YYYY-MM-DD."
                    },
                    "sede": {
                        "type": "string",
                        "description": "Sede de la biblioteca (Central cuenta con historial suficiente; otras sedes tienen datos insuficientes).",
                        "enum": ["Central", "Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
                    }
                },
                "required": ["fecha_inicio", "fecha_fin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "predecir_afluencia_ml",
            "description": "Invoca el modelo de Machine Learning (HistGradientBoosting) para pronosticar el nivel de concurrencia esperado (Baja, Media, Alta) en una fecha, hora y sede.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha": {
                        "type": "string",
                        "description": "Fecha a pronosticar en formato YYYY-MM-DD."
                    },
                    "hora": {
                        "type": "integer",
                        "description": "Hora a pronosticar (entero entre 8 y 20)."
                    },
                    "sede": {
                        "type": "string",
                        "description": "Sede a pronosticar (ej. Central).",
                        "enum": ["Central", "Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
                    }
                },
                "required": ["fecha", "hora", "sede"]
            }
        }
    }
]


SYSTEM_PROMPT = """Eres el Asistente Inteligente de la Biblioteca Universitaria UNDAC.
Tu función es responder consultas analíticas, resúmenes de afluencia y pronósticos de concurrencia de forma clara, profesional y objetiva.

REGLAS DE SEGURIDAD ESTRICTAS:
1. No tienes permitido generar ni ejecutar sentencias SQL directas. Solo puedes obtener información usando las herramientas autorizadas provistas.
2. NUNCA inventes cifras, fechas, niveles de afluencia ni estadísticas que no provengan del resultado de una herramienta.
3. Si la herramienta indica que los datos son insuficientes o que la biblioteca está cerrada por feriado o receso, comunica esa situación textualmente sin elucubrar.
4. No tienes acceso a datos personales de alumnos, docentes ni personas individuales. Si detectas preguntas sobre personas específicas, indica que esas consultas deben resolverse mediante el módulo local administrativo.
5. Mantén respuestas concisas, profesionales y basadas fielmente en los datos estructurados.
6. Si la consulta del usuario no especifica fechas suficientes o estas son ambiguas, solicita la aclaración con ejemplos de fechas en lenguaje natural; nunca inventes ni asumas un periodo.
"""


def ejecutar_herramienta_backend(
    nombre_tool: str,
    argumentos: Dict[str, Any],
    rol: str,
    sede_permitida: Optional[str]
) -> Tuple[Dict[str, Any], str]:
    """
    Ejecuta con aislamiento y validación de permisos la herramienta solicitada por el LLM.
    Retorna una tupla (datos_resultado, nombre_intencion_asociada).
    """
    rol_valido = validar_rol(rol)

    # Determinar sede de consulta respetando estrictamente el rol
    if rol_valido == "Supervisor":
        sede_efectiva = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
    else:
        sede_arg = argumentos.get("sede")
        if sede_arg and str(sede_arg).strip() in SEDES_PERMITIDAS:
            sede_efectiva = str(sede_arg).strip()
        else:
            sede_efectiva = None

    if nombre_tool == "obtener_resumen_ingresos":
        f_ini = argumentos.get("fecha_inicio")
        f_fin = argumentos.get("fecha_fin")
        resumen = obtener_resumen_ingresos(
            fecha_inicio=f_ini,
            fecha_fin=f_fin,
            rol=rol_valido,
            sede_permitida=sede_efectiva
        )
        return resumen, "resumen_ingresos"

    elif nombre_tool == "obtener_horas_mayor_afluencia":
        f_ini = argumentos.get("fecha_inicio")
        f_fin = argumentos.get("fecha_fin")
        limite = argumentos.get("limite", 5)
        horas = obtener_horas_mayor_afluencia(
            fecha_inicio=f_ini,
            fecha_fin=f_fin,
            rol=rol_valido,
            sede_permitida=sede_efectiva,
            limite=limite
        )
        return horas, "horas_mayor_afluencia"

    elif nombre_tool == "consultar_afluencia_historica":
        f_ini = argumentos.get("fecha_inicio")
        f_fin = argumentos.get("fecha_fin")
        sede_hist = sede_efectiva or "Central"
        afluencia = consultar_afluencia_historica(
            fecha_inicio=f_ini,
            fecha_fin=f_fin,
            sede=sede_hist,
            rol=rol_valido,
            sede_permitida=sede_permitida if rol_valido == "Supervisor" else None
        )
        return afluencia, "afluencia_historica"

    elif nombre_tool == "predecir_afluencia_ml":
        fecha = argumentos.get("fecha")
        hora = int(argumentos.get("hora", 10))
        sede_ml = sede_efectiva or "Central"
        pred = predecir_afluencia_desde_bd(
            fecha=fecha,
            hora=hora,
            sede=sede_ml
        )
        return pred, "prediccion_afluencia"

    else:
        raise ValueError(f"Herramienta no autorizada o desconocida: '{nombre_tool}'.")


class ProveedorLLM:
    """
    Adaptador desacoplado para el proveedor LLM configurable vía servidor.
    Implementa el protocolo OpenAI / OpenAI-compatible mediante requests.
    """
    def __init__(self, config: Optional[ConfiguracionLLM] = None):
        self.config = config or obtener_configuracion()

    def responder_chat(self, mensajes):
        """Texto sin herramientas: no permite al proveedor consultar BD ni elegir SQL."""
        if not self.config.esta_disponible:
            raise LLMAPIError('Falta configuración del proveedor.', 400, 'configuracion')
        try:
            respuesta = requests.post(self.config.api_base + '/chat/completions',
                headers={'Authorization': 'Bearer ' + self.config.api_key},
                json={'model': self.config.modelo, 'messages': mensajes},
                timeout=self.config.timeout, allow_redirects=False)
        except requests.exceptions.Timeout:
            raise LLMTimeoutError('Tiempo de espera agotado.') from None
        except requests.exceptions.RequestException:
            raise LLMNetworkError('No se pudo conectar con el proveedor.') from None
        if respuesta.status_code != 200:
            raise error_api_seguro(respuesta)
        try:
            datos = respuesta.json()
            eleccion = datos['choices'][0]
            if eleccion.get('finish_reason') not in (None,'stop'):
                raise ValueError()
            texto = eleccion['message']['content']
            if not isinstance(texto, str) or not texto.strip():
                raise ValueError()
        except Exception:
            raise LLMError('Respuesta vacía o inválida del proveedor.') from None
        valida, texto, motivo = validar_y_sanitizar_respuesta_llm(texto)
        if not valida:
            raise LLMSafetyViolationError('Respuesta no verificable.')
        return {'mensaje': texto, 'usage': datos.get('usage')}

    def procesar_consulta(
        self,
        pregunta: str,
        rol: str,
        sede_permitida: Optional[str] = None,
        parametros_complementarios: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Envía la consulta despersonalizada al LLM, resuelve tool calls de forma segura
        y retorna la respuesta estructurada compatible con el coordinador y la UI.
        """
        if not self.config.esta_disponible:
            raise LLMError("El proveedor LLM no está activado, carece de API Key o no tiene modelo configurado.")

        url = f"{self.config.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

        # Preparar mensajes de contexto
        prompt_usuario = pregunta.strip()
        if parametros_complementarios:
            partes_comp = [f"{k}={v}" for k, v in parametros_complementarios.items() if v is not None]
            if partes_comp:
                prompt_usuario += f"\n[Parámetros complementarios proporcionados por el usuario: {', '.join(partes_comp)}]"

        mensajes = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_usuario}
        ]

        payload = {
            "model": self.config.modelo,
            "messages": mensajes,
            "tools": HERRAMIENTAS_BIBLIOTECA,
            "tool_choice": "auto",
            "temperature": 0.2
        }

        # Iniciar cronómetro para medir tiempo total de interacción con el proveedor
        t_inicio = time.time()

        # 1. Primera llamada al LLM
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout)
        except requests.exceptions.Timeout as e:
            raise LLMTimeoutError(f"Tiempo de espera agotado ({self.config.timeout}s) al conectar con el LLM.") from e
        except requests.exceptions.RequestException as e:
            raise LLMNetworkError('No se pudo conectar con el proveedor.') from None

        if resp.status_code != 200:
            raise error_api_seguro(resp)

        try:
            datos_resp = resp.json()
            eleccion = datos_resp["choices"][0]["message"]
            usage_1 = datos_resp.get("usage") if isinstance(datos_resp, dict) else None
        except Exception as e:
            raise LLMError(f"Respuesta malformada o inválida del proveedor LLM: {str(e)}") from e

        # 2. Verificar si el modelo solicitó invocar una herramienta (Tool Call)
        tool_calls = eleccion.get("tool_calls")
        datos_herramienta = {}
        intencion_detectada = "consulta_general"
        usage_2 = None
        num_llamadas = 1

        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            num_llamadas = 2
            llamada = tool_calls[0]
            nombre_tool = llamada["function"]["name"]

            try:
                args_tool = json.loads(llamada["function"]["arguments"])
            except Exception:
                args_tool = {}

            # Ejecutar herramienta localmente con control de permisos del servidor
            try:
                datos_herramienta, intencion_detectada = ejecutar_herramienta_backend(
                    nombre_tool=nombre_tool,
                    argumentos=args_tool,
                    rol=rol,
                    sede_permitida=sede_permitida
                )
                # Garantizar que solo viajen datos agregados sin claves sensibles
                datos_seguros_llm = sanitizar_datos_herramienta_para_llm(datos_herramienta)
                resultado_str = json.dumps(datos_seguros_llm, default=str)
            except Exception as err_tool:
                resultado_str = json.dumps({"error": str(err_tool)})

            # 3. Enviar el resultado de la herramienta de vuelta al LLM para la síntesis final
            # Asegurar formato compatible con Gemini y proxies OpenAI (content string en lugar de null)
            mensaje_asistente_historial = {
                "role": "assistant",
                "content": eleccion.get("content") or "",
                "tool_calls": tool_calls
            }
            mensajes.append(mensaje_asistente_historial)
            mensajes.append({
                "role": "tool",
                "tool_call_id": llamada.get("id", "call_1"),
                "name": nombre_tool,
                "content": resultado_str
            })

            payload_sintesis = {
                "model": self.config.modelo,
                "messages": mensajes,
                "temperature": 0.2
            }

            try:
                resp_sintesis = requests.post(url, headers=headers, json=payload_sintesis, timeout=self.config.timeout)
                if resp_sintesis.status_code == 200:
                    datos_sintesis = resp_sintesis.json()
                    usage_2 = datos_sintesis.get("usage") if isinstance(datos_sintesis, dict) else None
                    mensaje_final = datos_sintesis["choices"][0]["message"]["content"].strip()
                else:
                    if nombre_tool == "obtener_resumen_ingresos":
                        t_ing = datos_herramienta.get("total_ingresos", 0)
                        mensaje_final = f"Resumen de ingresos del {datos_herramienta.get('fecha_inicio')} al {datos_herramienta.get('fecha_fin')}: Total de {t_ing:,} ingresos registrados."
                    else:
                        mensaje_final = "Consulta completada a partir de los datos analíticos del sistema."
            except Exception:
                if nombre_tool == "obtener_resumen_ingresos":
                    t_ing = datos_herramienta.get("total_ingresos", 0)
                    mensaje_final = f"Resumen de ingresos del {datos_herramienta.get('fecha_inicio')} al {datos_herramienta.get('fecha_fin')}: Total de {t_ing:,} ingresos registrados."
                else:
                    mensaje_final = "Consulta completada a partir de los datos analíticos del sistema."

            # Validación de salida (Output Guardrail) sobre la síntesis del LLM
            es_valida, mensaje_sanitizado, motivo = validar_y_sanitizar_respuesta_llm(mensaje_final)
            if not es_valida:
                raise LLMSafetyViolationError(f"Respuesta del LLM rechazada por política de seguridad: {motivo}")
            mensaje_final = mensaje_sanitizado

        else:
            # Respuesta textual directa sin herramientas
            mensaje_crudo = eleccion.get("content", "").strip()
            es_valida, mensaje_sanitizado, motivo = validar_y_sanitizar_respuesta_llm(mensaje_crudo)
            if not es_valida:
                raise LLMSafetyViolationError(f"Respuesta directa del LLM rechazada por política de seguridad: {motivo}")
            mensaje_final = mensaje_sanitizado

        # 4. Extracción exacta de consumo de tokens reportado por el proveedor
        def _extraer_enteros_tokens(usage_dict):
            if not isinstance(usage_dict, dict):
                return None, None, None
            p = usage_dict.get("prompt_tokens")
            c = usage_dict.get("completion_tokens")
            t = usage_dict.get("total_tokens")
            p = int(p) if isinstance(p, (int, float)) and not isinstance(p, bool) else None
            c = int(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
            t = int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None

            # Si el proveedor no reporta total_tokens pero sí entrada y salida, inferir suma
            if t is None and p is not None and c is not None:
                t = p + c

            return p, c, t

        if num_llamadas == 2:
            p1, c1, t1 = _extraer_enteros_tokens(usage_1)
            p2, c2, t2 = _extraer_enteros_tokens(usage_2)
            if t1 is not None or t2 is not None:
                p_tot = ((p1 or 0) + (p2 or 0)) if (p1 is not None or p2 is not None) else None
                c_tot = ((c1 or 0) + (c2 or 0)) if (c1 is not None or c2 is not None) else None
                t_tot = (t1 or 0) + (t2 or 0)

                # Si el total no coincide con la suma de entrada y salida,
                # registrar la diferencia como tokens_sin_desglose en vez de sumarla silenciosamente a salida
                suma_desglosada = (p_tot or 0) + (c_tot or 0)
                tokens_sin_desglose = max(0, t_tot - suma_desglosada) if (t_tot is not None and t_tot > suma_desglosada) else 0

                tokens_info = {
                    "disponible": True,
                    "prompt_tokens": p_tot,
                    "completion_tokens": c_tot,
                    "tokens_sin_desglose": tokens_sin_desglose,
                    "total_tokens": t_tot,
                    "llamadas_api": 2
                }
            else:
                tokens_info = {
                    "disponible": False,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "tokens_sin_desglose": 0,
                    "total_tokens": None,
                    "llamadas_api": 2
                }
        else:
            p1, c1, t1 = _extraer_enteros_tokens(usage_1)
            if t1 is not None:
                suma_desglosada = (p1 or 0) + (c1 or 0)
                tokens_sin_desglose = max(0, t1 - suma_desglosada) if (t1 is not None and t1 > suma_desglosada) else 0

                tokens_info = {
                    "disponible": True,
                    "prompt_tokens": p1,
                    "completion_tokens": c1,
                    "tokens_sin_desglose": tokens_sin_desglose,
                    "total_tokens": t1,
                    "llamadas_api": 1
                }
            else:
                tokens_info = {
                    "disponible": False,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "tokens_sin_desglose": 0,
                    "total_tokens": None,
                    "llamadas_api": 1
                }

        tiempo_ms = int((time.time() - t_inicio) * 1000)

        # Determinar sede aplicada efectiva
        if rol == "Supervisor":
            sede_aplicada = str(sede_permitida).strip() if (sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS) else "Central"
        elif parametros_complementarios and parametros_complementarios.get("sede") in SEDES_PERMITIDAS:
            sede_aplicada = parametros_complementarios["sede"]
        elif datos_herramienta.get("sede"):
            sede_aplicada = datos_herramienta["sede"]
        else:
            sede_aplicada = "Todas las sedes"

        return {
            "status": "success",
            "intencion": intencion_detectada,
            "sede_aplicada": sede_aplicada,
            "mensaje": mensaje_final,
            "datos": datos_herramienta,
            "requiere_aclaracion": False,
            "requiere_parametros": False,
            "opciones": [],
            "origen": "llm_externo",
            "proveedor": self.config.proveedor,
            "modelo": self.config.modelo,
            "metricas": {
                "tiempo_ms": tiempo_ms,
                "tokens": tokens_info
            },
            "codigo_http": 200
        }
