from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd


class PredictorAfluencia:
    """
    Carga y ejecuta el modelo de predicción de afluencia.

    El modelo no consulta directamente la base de datos.
    Recibe las 12 variables ya calculadas y devuelve:
    - nivel estimado;
    - probabilidades por clase;
    - versión y advertencia del modelo.
    """

    def __init__(self):
        ruta_ml = Path(__file__).resolve().parents[1]

        self.ruta_modelo = (
            ruta_ml
            / "models"
            / "modelo_afluencia_undac.joblib"
        )

        if not self.ruta_modelo.exists():
            raise FileNotFoundError(
                "No se encontró el modelo en: "
                f"{self.ruta_modelo}"
            )

        paquete = joblib.load(self.ruta_modelo)

        if not isinstance(paquete, dict):
            raise ValueError(
                "El archivo del modelo no contiene "
                "un paquete válido."
            )

        if "modelo" not in paquete:
            raise ValueError(
                "El paquete no contiene la clave 'modelo'."
            )

        if "metadatos" not in paquete:
            raise ValueError(
                "El paquete no contiene la clave 'metadatos'."
            )

        self.modelo = paquete["modelo"]
        self.metadatos = paquete["metadatos"]

        self.variables = list(
            self.metadatos["variables"]
        )

    def obtener_informacion(self):
        return {
            "nombre": self.metadatos.get("nombre"),
            "version": self.metadatos.get("version"),
            "algoritmo": self.metadatos.get("algoritmo"),
            "version_sklearn": self.metadatos.get(
                "version_sklearn"
            ),
            "variables": self.variables,
            "clases": [
                str(clase)
                for clase in self.modelo.classes_
            ],
            "metricas": self.metadatos.get(
                "metricas_prueba_septiembre",
                {}
            ),
            "advertencia": self.metadatos.get(
                "advertencia"
            )
        }

    def validar_variables(self, datos):
        if not isinstance(datos, dict):
            raise ValueError(
                "Los datos deben enviarse como diccionario."
            )

        faltantes = [
            variable
            for variable in self.variables
            if variable not in datos
        ]

        if faltantes:
            raise ValueError(
                "Faltan variables requeridas: "
                + ", ".join(faltantes)
            )

        datos_limpios = {}

        for variable in self.variables:
            try:
                valor = float(datos[variable])
            except (TypeError, ValueError):
                raise ValueError(
                    f"La variable '{variable}' "
                    "debe ser numérica."
                )

            datos_limpios[variable] = valor

        return datos_limpios

    def predecir(self, datos):
        datos_limpios = self.validar_variables(
            datos
        )

        entrada = pd.DataFrame(
            [[
                datos_limpios[variable]
                for variable in self.variables
            ]],
            columns=self.variables
        )

        nivel_estimado = str(
            self.modelo.predict(entrada)[0]
        )

        probabilidades_modelo = (
            self.modelo.predict_proba(entrada)[0]
        )

        probabilidades = {
            str(clase): round(
                float(probabilidad),
                4
            )
            for clase, probabilidad in zip(
                self.modelo.classes_,
                probabilidades_modelo
            )
        }

        return {
            "nivel_estimado": nivel_estimado,
            "probabilidades": probabilidades,
            "version_modelo": self.metadatos.get(
                "version"
            ),
            "advertencia": self.metadatos.get(
                "advertencia"
            )
        }


@lru_cache(maxsize=1)
def obtener_predictor_afluencia():
    """
    Devuelve una única instancia del predictor durante
    la ejecución del proceso Flask.
    """
    return PredictorAfluencia()


if __name__ == "__main__":
    predictor = obtener_predictor_afluencia()

    print("Modelo cargado correctamente.")
    print(predictor.obtener_informacion())