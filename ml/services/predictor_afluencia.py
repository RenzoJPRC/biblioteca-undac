import json
import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

logger = logging.getLogger(__name__)


class PredictorAfluencia:
    """
    Carga y ejecuta el modelo de predicción de afluencia para Sede Central.

    Soporta dos artefactos:
    1. Modelo de Producción: RandomForestClassifier (modelo_afluencia_undac.joblib).
    2. Modelo Candidato: GradientBoostingClassifier (modelo_afluencia_candidato_gb.joblib),
       ganador de la evaluación comparativa.

    Resiliencia y Conmutación:
    - Por defecto conserva Random Forest; el candidato requiere selección explícita.
    - Si el artefacto candidato no existe o falla en ejecución, conmuta automáticamente
      al Random Forest de producción sin lanzar error al usuario ni a la API.
    - Permite conmutar explícitamente entre 'candidato' y 'produccion' vía parámetro,
      método o variable de entorno MODELO_AFLUENCIA_ACTIVO.
    """

    def __init__(self):
        self.ruta_ml = Path(__file__).resolve().parents[1]
        self.ruta_modelo_prod = self.ruta_ml / "models" / "modelo_afluencia_undac.joblib"
        self.ruta_modelo_candidato = self.ruta_ml / "models" / "modelo_afluencia_candidato_gb.joblib"

        # 1. Cargar obligatoriamente el modelo de producción (Random Forest)
        if not self.ruta_modelo_prod.exists():
            raise FileNotFoundError(
                f"No se encontró el modelo de producción en: {self.ruta_modelo_prod}"
            )

        paquete_prod = joblib.load(self.ruta_modelo_prod)
        if not isinstance(paquete_prod, dict) or "modelo" not in paquete_prod or "metadatos" not in paquete_prod:
            raise ValueError("El paquete del modelo de producción no contiene una estructura válida.")

        self.modelo_prod = paquete_prod["modelo"]
        self.metadatos_prod = dict(paquete_prod["metadatos"])

        # Cargar metadatos JSON de producción si existen
        ruta_meta_prod_json = self.ruta_ml / "models" / "metadatos_modelo_afluencia.json"
        if ruta_meta_prod_json.exists():
            try:
                with open(ruta_meta_prod_json, "r", encoding="utf-8") as f_json:
                    self.metadatos_prod.update(json.load(f_json))
            except Exception as e_json:
                logger.warning("No se pudo cargar metadatos JSON de producción: %s", e_json)

        # 2. Intentar cargar el modelo candidato (Gradient Boosting)
        self.modelo_candidato = None
        self.metadatos_candidato = None
        self.motivo_candidato_no_disponible = 'Artefacto candidato no instalado.'

        if self.ruta_modelo_candidato.exists():
            try:
                manifest_path=self.ruta_ml/'models'/'manifest_afluencia_candidato_gb.json'
                meta_path=self.ruta_ml/'models'/'metadatos_modelo_afluencia_candidato_gb.json'
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                if (hashlib.sha256(self.ruta_modelo_candidato.read_bytes()).hexdigest()!=manifest['modelo_sha256']
                    or hashlib.sha256(meta_path.read_bytes()).hexdigest()!=manifest['metadatos_sha256']):
                    raise ValueError('Integridad del candidato no confirmada')
                paquete_cand = joblib.load(self.ruta_modelo_candidato)
                if isinstance(paquete_cand, dict) and "modelo" in paquete_cand and "metadatos" in paquete_cand:
                    model=paquete_cand['modelo']
                    if (type(model).__name__!='GradientBoostingClassifier'
                        or list(model.feature_names_in_)!=manifest['variables']
                        or manifest['variables']!=self.metadatos_prod.get('variables')
                        or sorted(model.classes_)!=['Alto','Bajo','Medio']):
                        raise ValueError('Candidato incompatible')
                    self.modelo_candidato = paquete_cand["modelo"]
                    self.metadatos_candidato = dict(paquete_cand["metadatos"])
                    self.motivo_candidato_no_disponible = None

                    ruta_meta_cand_json = self.ruta_ml / "models" / "metadatos_modelo_afluencia_candidato_gb.json"
                    if ruta_meta_cand_json.exists():
                        try:
                            with open(ruta_meta_cand_json, "r", encoding="utf-8") as f_cand_json:
                                self.metadatos_candidato.update(json.load(f_cand_json))
                        except Exception:
                            pass
                else:
                    logger.warning("El archivo candidato no contiene un diccionario con 'modelo' y 'metadatos'.")
            except Exception as e_cand:
                logger.warning("No se pudo verificar/cargar el candidato (%s); se conserva Random Forest.", type(e_cand).__name__)
                self.modelo_candidato = None
                self.metadatos_candidato = None
                self.motivo_candidato_no_disponible = 'No se pudo verificar la integridad o compatibilidad del candidato.'

        # 3. Definir modelo activo por defecto
        # Incorporar un artefacto no promueve el candidato automáticamente.
        preferencia_env = os.getenv("MODELO_AFLUENCIA_ACTIVO", "produccion").strip().lower()
        if preferencia_env in ("candidato", "gradient_boosting", "gb") and self.modelo_candidato is not None:
            self.tipo_modelo_activo = "candidato"
        else:
            self.tipo_modelo_activo = "produccion"

        # Variables predictoras (idénticas en ambos modelos y en mismo orden)
        self.variables = list(self.metadatos_prod.get("variables", []))

    def establecer_modelo_activo(self, tipo: str):
        """
        Cambia el modelo activo por defecto para futuras predicciones.
        Acepta 'candidato' o 'produccion' (también 'random_forest' o 'gradient_boosting').
        """
        tipo_normalizado = str(tipo).strip().lower()
        if tipo_normalizado in ("candidato", "gradient_boosting", "gb"):
            if self.modelo_candidato is not None:
                self.tipo_modelo_activo = "candidato"
            else:
                logger.warning("Modelo candidato no disponible. Se mantiene en producción.")
                self.tipo_modelo_activo = "produccion"
        elif tipo_normalizado in ("produccion", "random_forest", "rf", "default"):
            self.tipo_modelo_activo = "produccion"
        else:
            raise ValueError(f"Tipo de modelo no reconocido: '{tipo}'. Use 'candidato' o 'produccion'.")

    def _resolver_modelo_y_metadatos(self, modelo_solicitado=None):
        """Resuelve qué modelo y metadatos usar, con fallback automático a producción."""
        if modelo_solicitado:
            solicitud = str(modelo_solicitado).strip().lower()
            if solicitud in ("candidato", "gradient_boosting", "gb"):
                tipo_deseado = "candidato"
            else:
                tipo_deseado = "produccion"
        else:
            tipo_deseado = self.tipo_modelo_activo

        fallback_activado = False
        if tipo_deseado == "candidato":
            if self.modelo_candidato is not None:
                return self.modelo_candidato, self.metadatos_candidato, "candidato", False
            else:
                fallback_activado = True

        return self.modelo_prod, self.metadatos_prod, "produccion", fallback_activado

    def obtener_informacion(self, modelo=None):
        """
        Retorna la información y metadatos del modelo activo o solicitado.
        """
        modelo_obj, metadatos, tipo_modelo, _ = self._resolver_modelo_y_metadatos(modelo)

        umbrales_defecto = {
            "bajo_maximo": 7,
            "medio_minimo": 8,
            "medio_maximo": 35,
            "alto_minimo": 36,
            "metodo": "Percentiles 33 y 66 del conjunto real de entrenamiento"
        }
        umbrales = metadatos.get("umbrales", umbrales_defecto)

        periodo_entrenamiento = metadatos.get("periodo_entrenamiento", {})
        if isinstance(periodo_entrenamiento, dict):
            periodo_txt = f"{periodo_entrenamiento.get('inicio', '2026-05-13')} al {periodo_entrenamiento.get('fin', '2026-08-31')}"
        else:
            periodo_txt = str(periodo_entrenamiento)

        fecha_entrenamiento = metadatos.get("fecha_entrenamiento") or metadatos.get("fecha_exportacion", "2026-10-05")

        metricas = metadatos.get("metricas_validacion_septiembre") or metadatos.get("metricas_prueba_septiembre", {})

        return {
            "nombre": metadatos.get("nombre", "Modelo de predicción de afluencia UNDAC"),
            "version": metadatos.get("version", "1.0.0"),
            "algoritmo": metadatos.get("algoritmo", "RandomForestClassifier"),
            "tipo_modelo": tipo_modelo,
            "es_candidato": (tipo_modelo == "candidato"),
            "modelo_candidato_disponible": (self.modelo_candidato is not None),
            "motivo_candidato_no_disponible": self.motivo_candidato_no_disponible,
            "modelo_produccion_disponible": True,
            "fecha_entrenamiento": fecha_entrenamiento,
            "periodo_entrenamiento": periodo_entrenamiento,
            "periodo_entrenamiento_texto": periodo_txt,
            "version_sklearn": metadatos.get("version_sklearn", "1.6.1"),
            "variables": self.variables,
            "clases": [str(c) for c in modelo_obj.classes_],
            "metricas": metricas,
            "umbrales": umbrales,
            "advertencia": metadatos.get("advertencia", "Modelo de apoyo referencial.")
        }

    def validar_variables(self, datos):
        if not isinstance(datos, dict):
            raise ValueError("Los datos deben enviarse como diccionario.")

        faltantes = [v for v in self.variables if v not in datos]
        if faltantes:
            raise ValueError(f"Faltan variables requeridas: {', '.join(faltantes)}")

        datos_limpios = {}
        for variable in self.variables:
            try:
                valor = float(datos[variable])
            except (TypeError, ValueError):
                raise ValueError(f"La variable '{variable}' debe ser numérica.")
            datos_limpios[variable] = valor

        return datos_limpios

    def predecir(self, datos, modelo=None):
        """
        Ejecuta la predicción del nivel de afluencia.

        Si se solicita el candidato y ocurre cualquier falla interna, conmuta de inmediato
        al modelo de producción (Random Forest) garantizando la continuidad del servicio.
        """
        datos_limpios = self.validar_variables(datos)
        entrada = pd.DataFrame(
            [[datos_limpios[var] for var in self.variables]],
            columns=self.variables
        )

        modelo_obj, metadatos, tipo_modelo, fallback_activado = self._resolver_modelo_y_metadatos(modelo)

        # Si el modelo elegido es el candidato, intentar predecir con él; si falla, fallback a producción
        if tipo_modelo == "candidato":
            try:
                nivel_estimado = str(modelo_obj.predict(entrada)[0])
                probabilidades_raw = modelo_obj.predict_proba(entrada)[0]
            except Exception as e_pred_cand:
                logger.warning(
                    "Error ejecutando predicción con modelo candidato: %s. Activando fallback a Random Forest.",
                    e_pred_cand
                )
                modelo_obj = self.modelo_prod
                metadatos = self.metadatos_prod
                tipo_modelo = "produccion"
                fallback_activado = True
                nivel_estimado = str(modelo_obj.predict(entrada)[0])
                probabilidades_raw = modelo_obj.predict_proba(entrada)[0]
        else:
            nivel_estimado = str(modelo_obj.predict(entrada)[0])
            probabilidades_raw = modelo_obj.predict_proba(entrada)[0]

        probabilidades = {
            str(clase): round(float(prob), 4)
            for clase, prob in zip(modelo_obj.classes_, probabilidades_raw)
        }

        advertencia = metadatos.get("advertencia", "Modelo de apoyo referencial.")
        if fallback_activado:
            advertencia = (
                "[FALLBACK AUTOMÁTICO] El modelo candidato no estuvo disponible; "
                "la predicción se generó exitosamente con el modelo de producción Random Forest. "
                + advertencia
            )

        fecha_entrenamiento = metadatos.get("fecha_entrenamiento") or metadatos.get("fecha_exportacion", "2026-10-05")

        return {
            "nivel_estimado": nivel_estimado,
            "probabilidades": probabilidades,
            "version_modelo": metadatos.get("version", "1.0.0"),
            "algoritmo": metadatos.get("algoritmo", "RandomForestClassifier"),
            "modelo_utilizado": metadatos.get("nombre", "Modelo de predicción de afluencia UNDAC"),
            "tipo_modelo": tipo_modelo,
            "es_candidato": (tipo_modelo == "candidato"),
            "fecha_entrenamiento": fecha_entrenamiento,
            "periodo_entrenamiento": metadatos.get("periodo_entrenamiento", {}),
            "fallback_activado": fallback_activado,
            "advertencia": advertencia
        }


@lru_cache(maxsize=1)
def obtener_predictor_afluencia():
    """Devuelve la instancia singleton del predictor de afluencia."""
    return PredictorAfluencia()


def limpiar_cache_predictor():
    """Limpia el caché singleton del predictor para pruebas o recargas."""
    obtener_predictor_afluencia.cache_clear()


def establecer_modelo_afluencia_activo(tipo: str):
    """Permite cambiar dinámicamente el modelo activo por defecto."""
    predictor = obtener_predictor_afluencia()
    predictor.establecer_modelo_activo(tipo)


if __name__ == "__main__":
    predictor = obtener_predictor_afluencia()
    print("Información del modelo activo:")
    print(predictor.obtener_informacion())
