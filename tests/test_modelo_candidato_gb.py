"""
Pruebas automatizadas unitarias y de integración para la integración del modelo
candidato Gradient Boosting (modelo_afluencia_candidato_gb.joblib) junto al modelo
de producción Random Forest (modelo_afluencia_undac.joblib).

Verifica:
1. Existencia de artefactos separados y compatibilidad estricta de las 12 variables y su orden.
2. Preservación intacta del modelo Random Forest de producción.
3. Generación exitosa de predicciones con ambos modelos usando el historial actual de datos.
4. Conmutación sencilla entre candidato y producción.
5. Resiliencia y fallback automático a Random Forest en caso de falla o ausencia del candidato.
6. Identificación clara del modelo y fecha de entrenamiento en metadatos y respuestas.
7. Compatibilidad con el refresco en memoria de afluencia.
8. Respuesta del endpoint /admin/api/ml/predecir-afluencia.
"""

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import joblib

from app import app
from ml.services.predictor_afluencia import (
    PredictorAfluencia,
    establecer_modelo_afluencia_activo,
    limpiar_cache_predictor,
    obtener_predictor_afluencia,
)
from ml.services.servicio_afluencia import (
    obtener_informacion_modelo,
    predecir_afluencia_desde_bd,
    refrescar_analisis_afluencia_memoria,
    limpiar_cache_refresco_memoria,
)


class TestModeloCandidatoGB(unittest.TestCase):
    """Batería de pruebas para la integración del modelo candidato Gradient Boosting."""

    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parents[1]
        cls.ruta_prod = cls.root_dir / "ml" / "models" / "modelo_afluencia_undac.joblib"
        cls.ruta_candidato = cls.root_dir / "ml" / "models" / "modelo_afluencia_candidato_gb.joblib"
        cls.ruta_meta_candidato = cls.root_dir / "ml" / "models" / "metadatos_modelo_afluencia_candidato_gb.json"
        cls.client = app.test_client()

    def setUp(self):
        limpiar_cache_predictor()
        limpiar_cache_refresco_memoria()
        # Asegurar estado por defecto como candidato
        establecer_modelo_afluencia_activo("candidato")

    def tearDown(self):
        limpiar_cache_predictor()
        limpiar_cache_refresco_memoria()

    def test_01_artefactos_separados_y_compatibilidad_variables(self):
        """Verifica que ambos modelos existan en archivos separados y compartan las 12 variables en orden."""
        self.assertTrue(self.ruta_prod.exists(), "El modelo de producción .joblib debe existir")
        self.assertTrue(self.ruta_candidato.exists(), "El modelo candidato .joblib debe existir en artefacto separado")
        self.assertTrue(self.ruta_meta_candidato.exists(), "Los metadatos del candidato deben existir")

        paquete_prod = joblib.load(self.ruta_prod)
        paquete_cand = joblib.load(self.ruta_candidato)

        vars_prod = paquete_prod["metadatos"]["variables"]
        vars_cand = paquete_cand["metadatos"]["variables"]

        self.assertEqual(vars_prod, vars_cand, "Ambos modelos deben compartir exactamente las 12 variables y su orden")
        self.assertEqual(len(vars_cand), 12, "Deben ser exactamente 12 variables")

        # Verificar clases
        clases_prod = sorted(list(paquete_prod["modelo"].classes_))
        clases_cand = sorted(list(paquete_cand["modelo"].classes_))
        self.assertEqual(clases_prod, ["Alto", "Bajo", "Medio"])
        self.assertEqual(clases_cand, ["Alto", "Bajo", "Medio"])

        # Verificar tipos de algoritmo distintos
        self.assertEqual(paquete_prod["metadatos"]["algoritmo"], "RandomForestClassifier")
        self.assertEqual(paquete_cand["metadatos"]["algoritmo"], "GradientBoostingClassifier")
        self.assertTrue(paquete_cand["metadatos"].get("es_candidato"))

    @patch("ml.services.servicio_afluencia.construir_variables_afluencia")
    def test_02_prediccion_con_ambos_modelos(self, mock_construir):
        """Prueba que tanto el candidato como la producción emitan predicciones válidas sin consultar la BD real."""
        mock_construir.return_value = (
            {
                "Hora": 10,
                "DiaSemana": 1,
                "Mes": 10,
                "DiaMes": 20,
                "SemanaAnio": 43,
                "EsLunes": 0,
                "EsViernes": 0,
                "IngresosDiaAnterior": 25.0,
                "IngresosCincoDiasAntes": 20.0,
                "PromedioMovil5": 22.0,
                "PromedioHistoricoHora": 18.0,
                "TendenciaReciente": 3.0
            },
            {
                "fecha": "2026-10-20",
                "hora": 10,
                "sede": "Central",
                "calidad_minima_historial": True,
                "modo_demostracion": False,
                "ultima_fecha_disponible": "2026-10-19",
                "fechas_historicas": 80
            }
        )

        # Predicción con modelo candidato
        res_cand = predecir_afluencia_desde_bd(
            fecha="2026-10-20",
            hora=10,
            sede="Central",
            modelo="candidato"
        )
        self.assertEqual(res_cand["status"], "success")
        pred_c = res_cand["prediccion"]
        self.assertIn(pred_c["nivel"], ["Bajo", "Medio", "Alto"])
        self.assertEqual(pred_c["algoritmo"], "GradientBoostingClassifier")
        self.assertTrue(pred_c["es_candidato"])
        self.assertEqual(pred_c["tipo_modelo"], "candidato")
        self.assertIn("fecha_entrenamiento", pred_c)
        self.assertIn("periodo_entrenamiento", pred_c)
        for c in ["Alto", "Bajo", "Medio"]:
            self.assertIn(c, pred_c["probabilidades"])

        # Predicción con modelo de producción
        res_prod = predecir_afluencia_desde_bd(
            fecha="2026-10-20",
            hora=10,
            sede="Central",
            modelo="produccion"
        )
        self.assertEqual(res_prod["status"], "success")
        pred_p = res_prod["prediccion"]
        self.assertIn(pred_p["nivel"], ["Bajo", "Medio", "Alto"])
        self.assertEqual(pred_p["algoritmo"], "RandomForestClassifier")
        self.assertFalse(pred_p["es_candidato"])
        self.assertEqual(pred_p["tipo_modelo"], "produccion")
        self.assertIn("fecha_entrenamiento", pred_p)

    def test_03_conmutacion_sencilla_de_modelo(self):
        """Verifica que se pueda conmutar fácilmente entre candidato y producción."""
        predictor = obtener_predictor_afluencia()

        # Cambiar a producción
        predictor.establecer_modelo_activo("produccion")
        self.assertEqual(predictor.tipo_modelo_activo, "produccion")
        info_prod = predictor.obtener_informacion()
        self.assertEqual(info_prod["algoritmo"], "RandomForestClassifier")
        self.assertFalse(info_prod["es_candidato"])

        # Cambiar a candidato
        predictor.establecer_modelo_activo("candidato")
        self.assertEqual(predictor.tipo_modelo_activo, "candidato")
        info_cand = predictor.obtener_informacion()
        self.assertEqual(info_cand["algoritmo"], "GradientBoostingClassifier")
        self.assertTrue(info_cand["es_candidato"])

    def test_04_fallback_automatico_por_ausencia_de_candidato(self):
        """Verifica que si el candidato no está disponible, el sistema conmuta automáticamente a Random Forest."""
        predictor = PredictorAfluencia()
        predictor.modelo_candidato = None  # Simular candidato no disponible
        predictor.tipo_modelo_activo = "candidato"

        datos_ejemplo = {v: 10.0 for v in predictor.variables}
        res = predictor.predecir(datos_ejemplo, modelo="candidato")

        self.assertEqual(res["algoritmo"], "RandomForestClassifier")
        self.assertFalse(res["es_candidato"])
        self.assertTrue(res["fallback_activado"])
        self.assertIn("FALLBACK AUTOMÁTICO", res["advertencia"])

    def test_05_fallback_automatico_por_excepcion_en_candidato(self):
        """Verifica que si el candidato lanza un error durante predict, se activa el fallback sin error para el usuario."""
        predictor = PredictorAfluencia()
        mock_cand = MagicMock()
        mock_cand.predict.side_effect = RuntimeError("Fallo simulado en inferencia del candidato")
        predictor.modelo_candidato = mock_cand

        datos_ejemplo = {v: 15.0 for v in predictor.variables}
        # No debe lanzar excepción
        res = predictor.predecir(datos_ejemplo, modelo="candidato")

        self.assertEqual(res["algoritmo"], "RandomForestClassifier")
        self.assertTrue(res["fallback_activado"])
        self.assertIn(res["nivel_estimado"], ["Bajo", "Medio", "Alto"])

    def test_06_identificacion_en_informacion_modelo(self):
        """Verifica que la función obtener_informacion_modelo reporte algoritmo, rol y fecha de entrenamiento."""
        info_cand = obtener_informacion_modelo(modelo="candidato")
        self.assertEqual(info_cand["algoritmo"], "GradientBoostingClassifier")
        self.assertTrue(info_cand["es_candidato"])
        self.assertIn("fecha_entrenamiento", info_cand)
        self.assertIn("periodo_entrenamiento", info_cand)

        info_prod = obtener_informacion_modelo(modelo="produccion")
        self.assertEqual(info_prod["algoritmo"], "RandomForestClassifier")
        self.assertFalse(info_prod["es_candidato"])
        self.assertIn("fecha_entrenamiento", info_prod)

    @patch("ml.services.servicio_afluencia.construir_variables_afluencia")
    def test_07_compatibilidad_con_refresco_de_afluencia(self, mock_construir):
        """Verifica que tras refrescar el análisis de afluencia en memoria, ambos modelos funcionen con los datos actualizados."""
        mock_construir.return_value = (
            {v: 12.0 for v in obtener_predictor_afluencia().variables},
            {"fecha": "2026-10-21", "hora": 11, "sede": "Central", "calidad_minima_historial": True, "modo_demostracion": False, "ultima_fecha_disponible": "2026-10-20"}
        )
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [
            [(date(2026, 10, 20), 10, 15, datetime(2026, 10, 20, 10, 30, 0))],
            [(date(2026, 5, 15), 10, 20)]
        ]
        mock_cur.fetchone.return_value = (datetime(2026, 10, 20, 10, 30, 0), 15)

        refresco = refrescar_analisis_afluencia_memoria("Central", cursor_externo=mock_cur)
        self.assertEqual(refresco["status"], "success")

        # Predicción posterior al refresco
        res_cand = predecir_afluencia_desde_bd("2026-10-21", 11, "Central", modelo="candidato")
        self.assertEqual(res_cand["status"], "success")
        self.assertEqual(res_cand["prediccion"]["algoritmo"], "GradientBoostingClassifier")

        res_prod = predecir_afluencia_desde_bd("2026-10-21", 11, "Central", modelo="produccion")
        self.assertEqual(res_prod["status"], "success")
        self.assertEqual(res_prod["prediccion"]["algoritmo"], "RandomForestClassifier")

    @patch("routes.admin_ml.predecir_afluencia_desde_bd")
    def test_08_endpoint_api_predecir_afluencia_soporta_ambos_modelos(self, mock_predecir):
        """Verifica que el endpoint /admin/api/ml/predecir-afluencia responda correctamente para ambos modelos."""
        def mock_side_effect(fecha, hora, sede, modelo=None):
            alg = "GradientBoostingClassifier" if modelo == "candidato" else "RandomForestClassifier"
            is_cand = (modelo == "candidato")
            return {
                "status": "success",
                "prediccion": {
                    "nivel": "Medio",
                    "algoritmo": alg,
                    "es_candidato": is_cand,
                    "tipo_modelo": modelo or "candidato",
                    "probabilidades": {"Alto": 0.1, "Bajo": 0.2, "Medio": 0.7}
                },
                "consulta": {"fecha": str(fecha), "hora": hora, "sede": sede}
            }
        mock_predecir.side_effect = mock_side_effect

        with self.client.session_transaction() as sess:
            sess["admin_user"] = "admin_test"
            sess["admin_rol"] = "Administrador"
            sess["admin_sede"] = "Central"
            sess["csrf_token"] = "csrf_token_de_prueba_123"

        headers = {"X-CSRFToken": "csrf_token_de_prueba_123"}

        # Petición solicitando candidato
        resp_cand = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json={
                "fecha": "2026-10-20",
                "hora": 10,
                "sede": "Central",
                "modelo": "candidato"
            },
            headers=headers
        )
        self.assertEqual(resp_cand.status_code, 200)
        data_cand = resp_cand.get_json()
        self.assertEqual(data_cand["status"], "success")
        self.assertEqual(data_cand["prediccion"]["algoritmo"], "GradientBoostingClassifier")
        self.assertTrue(data_cand["prediccion"]["es_candidato"])

        # Petición solicitando producción
        resp_prod = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json={
                "fecha": "2026-10-20",
                "hora": 10,
                "sede": "Central",
                "modelo": "produccion"
            },
            headers=headers
        )
        self.assertEqual(resp_prod.status_code, 200)
        data_prod = resp_prod.get_json()
        self.assertEqual(data_prod["status"], "success")
        self.assertEqual(data_prod["prediccion"]["algoritmo"], "RandomForestClassifier")
        self.assertFalse(data_prod["prediccion"]["es_candidato"])


if __name__ == "__main__":
    unittest.main()
