import os
import json
import uuid
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Directorio de persistencia local desacoplada
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
METRICAS_FILE = DATA_DIR / "metricas_asistente.json"
MAX_REGISTROS_HISTORIAL = 1000


class GestorMetricasAsistente:
    """
    Gestor en memoria con persistencia local rotativa en JSON para telemetría
    y consumo de tokens del Asistente LLM.

    Principios de diseño y privacidad:
    - No almacena preguntas, respuestas, nombres, códigos, DNIs ni claves de API.
    - Es thread-safe y tolerante a fallos de I/O.
    - No requiere modificar la base de datos de producción.
    """
    def __init__(self, archivo_persistencia: Optional[Path] = None):
        self.archivo_persistencia = archivo_persistencia or METRICAS_FILE
        self._lock = threading.Lock()
        self._registros: List[Dict[str, Any]] = []
        self._cargar_desde_archivo()

    def _cargar_desde_archivo(self) -> None:
        """Carga el historial previo desde archivo JSON si existe y preserva el desglose de tokens."""
        if not self.archivo_persistencia.exists():
            return
        try:
            with open(self.archivo_persistencia, "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list):
                    # Validar formato y calcular tokens_sin_desglose si existen registros previos
                    registros_limpios = []
                    for r in datos:
                        if not isinstance(r, dict):
                            continue
                        if r.get("tokens_disponibles"):
                            p = r.get("prompt_tokens")
                            c = r.get("completion_tokens")
                            t = r.get("total_tokens")
                            sin_d = r.get("tokens_sin_desglose")
                            if t is not None:
                                suma = (int(p) if p is not None else 0) + (int(c) if c is not None else 0)
                                if sin_d is None:
                                    r["tokens_sin_desglose"] = max(0, int(t) - suma)
                                else:
                                    r["tokens_sin_desglose"] = max(0, int(sin_d))
                        registros_limpios.append(r)
                    self._registros = registros_limpios[-MAX_REGISTROS_HISTORIAL:]
        except Exception as e:
            logger.warning("No se pudo cargar el archivo de métricas previo: %s", str(e))
            self._registros = []

    def _guardar_en_archivo(self) -> None:
        """Persiste los registros actuales en disco de forma segura."""
        try:
            self.archivo_persistencia.parent.mkdir(parents=True, exist_ok=True)
            with open(self.archivo_persistencia, "w", encoding="utf-8") as f:
                json.dump(self._registros[-MAX_REGISTROS_HISTORIAL:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Error al guardar métricas en disco: %s", str(e))

    def registrar_interaccion(
        self,
        proveedor: str,
        modelo: Optional[str],
        intencion: str,
        tiempo_ms: int,
        tokens: Optional[Dict[str, Any]] = None,
        exito: bool = True,
        es_fallback: bool = False
    ) -> Dict[str, Any]:
        """
        Registra una interacción exclusivamente con metadatos operativos agregados.
        Garantiza que nunca se almacenen textos de usuario, respuestas ni datos personales.
        Conserva los valores exactos reportados por el proveedor (prompt_tokens, completion_tokens y total_tokens),
        registrando cualquier diferencia como tokens_sin_desglose.
        """
        prompt_tokens = None
        completion_tokens = None
        tokens_sin_desglose = 0
        total_tokens = None
        tokens_disponibles = False
        llamadas_api = 1

        if isinstance(tokens, dict):
            llamadas_api = tokens.get("llamadas_api", 1)
            if tokens.get("disponible") is True:
                p = tokens.get("prompt_tokens")
                c = tokens.get("completion_tokens")
                t = tokens.get("total_tokens")
                sin_d = tokens.get("tokens_sin_desglose")
                if t is not None:
                    prompt_tokens = int(p) if p is not None else None
                    completion_tokens = int(c) if c is not None else None
                    total_tokens = int(t)
                    if sin_d is not None:
                        tokens_sin_desglose = max(0, int(sin_d))
                    else:
                        suma = (prompt_tokens or 0) + (completion_tokens or 0)
                        tokens_sin_desglose = max(0, total_tokens - suma)
                    tokens_disponibles = True

        registro = {
            "id": f"met_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "proveedor": str(proveedor or "local").lower(),
            "modelo": str(modelo or "desconocido"),
            "intencion": str(intencion or "general"),
            "tiempo_ms": max(0, int(tiempo_ms)),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tokens_sin_desglose": tokens_sin_desglose,
            "total_tokens": total_tokens,
            "tokens_disponibles": tokens_disponibles,
            "llamadas_api": llamadas_api,
            "exito": bool(exito),
            "es_fallback": bool(es_fallback)
        }

        with self._lock:
            self._registros.append(registro)
            if len(self._registros) > MAX_REGISTROS_HISTORIAL:
                self._registros = self._registros[-MAX_REGISTROS_HISTORIAL:]
            self._guardar_en_archivo()

        return registro

    def obtener_presupuesto_configurado(self) -> Optional[int]:
        """Obtiene el presupuesto explícito en tokens desde la variable LLM_PRESUPUESTO_TOKENS."""
        val = os.environ.get("LLM_PRESUPUESTO_TOKENS", "").strip()
        if not val:
            return None
        try:
            limite = int(val)
            return limite if limite > 0 else None
        except (ValueError, TypeError):
            return None

    def obtener_resumen(self) -> Dict[str, Any]:
        """
        Calcula estadísticas acumuladas exactas sobre las interacciones registradas.
        Si no hay consumo de tokens reportado, reporta 'No disponible' sin inventar cifras.
        Preserva prompt_tokens, completion_tokens y total_tokens reportados,
        reflejando tokens_sin_desglose si total_tokens excede la suma desglosada.
        """
        with self._lock:
            registros = list(self._registros)

        total_solicitudes = len(registros)
        solicitudes_exitosas = sum(1 for r in registros if r.get("exito") and not r.get("es_fallback"))
        solicitudes_fallback = sum(1 for r in registros if r.get("es_fallback"))

        # Tiempo de respuesta promedio
        if total_solicitudes > 0:
            tiempo_promedio_ms = round(sum(r.get("tiempo_ms", 0) for r in registros) / total_solicitudes, 1)
            tasa_exito_pct = round((solicitudes_exitosas / total_solicitudes) * 100, 1)
            tasa_fallback_pct = round((solicitudes_fallback / total_solicitudes) * 100, 1)
        else:
            tiempo_promedio_ms = 0
            tasa_exito_pct = 100.0
            tasa_fallback_pct = 0.0

        # Consumo de tokens
        registros_con_tokens = [r for r in registros if r.get("tokens_disponibles") and r.get("total_tokens") is not None]
        conteo_con_tokens = len(registros_con_tokens)

        if conteo_con_tokens > 0:
            prompt_acumulado = sum(r.get("prompt_tokens") or 0 for r in registros_con_tokens)
            completion_acumulado = sum(r.get("completion_tokens") or 0 for r in registros_con_tokens)
            total_acumulado = sum(r.get("total_tokens") or 0 for r in registros_con_tokens)
            tokens_sin_desglose_acumulado = max(0, total_acumulado - (prompt_acumulado + completion_acumulado))
            tokens_disponibles = True
        else:
            prompt_acumulado = None
            completion_acumulado = None
            tokens_sin_desglose_acumulado = 0
            total_acumulado = None
            tokens_disponibles = False

        # Presupuesto configurable (calculado estrictamente con el total_tokens reportado)
        presupuesto_limite = self.obtener_presupuesto_configurado()
        if presupuesto_limite is not None:
            presupuesto_configurado = True
            etiqueta_presupuesto = "Porcentaje del presupuesto configurado"
            if total_acumulado is not None and total_acumulado >= 0:
                porcentaje_presupuesto = round((total_acumulado / presupuesto_limite) * 100, 2)
            else:
                porcentaje_presupuesto = 0.0
        else:
            presupuesto_configurado = False
            presupuesto_limite = None
            etiqueta_presupuesto = "No configurado"
            porcentaje_presupuesto = None

        # Historial reciente en orden descendente (máx 50)
        historial_reciente = []
        for r in reversed(registros[-50:]):
            historial_reciente.append({
                "id": r.get("id"),
                "timestamp": r.get("timestamp"),
                "proveedor": r.get("proveedor"),
                "modelo": r.get("modelo"),
                "intencion": r.get("intencion"),
                "tiempo_ms": r.get("tiempo_ms"),
                "prompt_tokens": r.get("prompt_tokens") if r.get("tokens_disponibles") else None,
                "completion_tokens": r.get("completion_tokens") if r.get("tokens_disponibles") else None,
                "tokens_sin_desglose": r.get("tokens_sin_desglose", 0) if r.get("tokens_disponibles") else 0,
                "total_tokens": r.get("total_tokens") if r.get("tokens_disponibles") else None,
                "tokens_disponibles": r.get("tokens_disponibles"),
                "llamadas_api": r.get("llamadas_api", 1),
                "exito": r.get("exito"),
                "es_fallback": r.get("es_fallback")
            })

        return {
            "solicitudes": {
                "total": total_solicitudes,
                "exitosas": solicitudes_exitosas,
                "fallbacks": solicitudes_fallback,
                "tasa_exito_pct": tasa_exito_pct,
                "tasa_fallback_pct": tasa_fallback_pct
            },
            "tiempo_respuesta": {
                "promedio_ms": tiempo_promedio_ms
            },
            "tokens": {
                "disponible": tokens_disponibles,
                "prompt_tokens": prompt_acumulado,
                "completion_tokens": completion_acumulado,
                "tokens_sin_desglose": tokens_sin_desglose_acumulado,
                "total_tokens": total_acumulado,
                "interacciones_con_tokens": conteo_con_tokens
            },
            "presupuesto": {
                "configurado": presupuesto_configurado,
                "limite_tokens": presupuesto_limite,
                "porcentaje": porcentaje_presupuesto,
                "etiqueta": etiqueta_presupuesto
            },
            "historial_reciente": historial_reciente
        }

    def reiniciar_metricas(self) -> None:
        """Limpia en memoria y archivo los registros (útil para pruebas)."""
        with self._lock:
            self._registros = []
            if self.archivo_persistencia.exists():
                try:
                    self.archivo_persistencia.unlink()
                except Exception:
                    pass


# Instancia única compartida del gestor de métricas
_instancia_gestor: Optional[GestorMetricasAsistente] = None
_instancia_lock = threading.Lock()


def obtener_gestor_metricas() -> GestorMetricasAsistente:
    global _instancia_gestor
    with _instancia_lock:
        if _instancia_gestor is None:
            _instancia_gestor = GestorMetricasAsistente()
        return _instancia_gestor
