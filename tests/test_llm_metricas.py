import os
import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

from llm.services.proveedor_llm import ConfiguracionLLM, ProveedorLLM
from llm.services.metricas_asistente import GestorMetricasAsistente
from llm.services.coordinador_asistente import procesar_pregunta
from app import app


class TestMetricasAsistente(unittest.TestCase):

    def setUp(self):
        # Crear un archivo temporal para persistencia aislada de pruebas
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.archivo_tmp = Path(self.tmp_dir.name) / "test_metricas.json"
        self.gestor = GestorMetricasAsistente(archivo_persistencia=self.archivo_tmp)
        self.client = app.test_client()

    def tearDown(self):
        self.tmp_dir.cleanup()

    # =========================================================================
    # 1. EXTRACCIÓN Y SUMA DE TOKENS REALES (CAMPO USAGE)
    # =========================================================================

    @patch("requests.post")
    def test_01_extraccion_tokens_presentes_llamada_unica(self, mock_post):
        """Llamada directa sin herramientas extrae exactamente el campo usage real."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Respuesta directa"}}],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 45,
                "total_tokens": 195
            }
        }
        mock_post.return_value = mock_resp

        resultado = proveedor.procesar_consulta("Hola", rol="SuperAdmin")

        self.assertEqual(resultado["status"], "success")
        tokens = resultado["metricas"]["tokens"]
        self.assertTrue(tokens["disponible"])
        self.assertEqual(tokens["prompt_tokens"], 150)
        self.assertEqual(tokens["completion_tokens"], 45)
        self.assertEqual(tokens["total_tokens"], 195)
        self.assertEqual(tokens["llamadas_api"], 1)

    @patch("requests.post")
    def test_02_suma_tokens_multiples_llamadas_herramientas(self, mock_post):
        """En interacción con herramientas, suma los tokens de todas las llamadas realizadas."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        # 1ra llamada: Modelo solicita herramienta
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "obtener_resumen_ingresos",
                            "arguments": json.dumps({"fecha_inicio": "2026-05-01", "fecha_fin": "2026-05-15"})
                        }
                    }]
                }
            }],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 25,
                "total_tokens": 145
            }
        }

        # 2da llamada: Modelo recibe datos agregados y genera síntesis
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Entre el 1 y el 15 de mayo se registraron **18,168** ingresos."
                }
            }],
            "usage": {
                "prompt_tokens": 230,
                "completion_tokens": 50,
                "total_tokens": 280
            }
        }
        mock_post.side_effect = [resp1, resp2]

        with patch("llm.services.proveedor_llm.obtener_resumen_ingresos") as mock_tool:
            mock_tool.return_value = {"total_ingresos": 18168, "status": "success"}

            res = proveedor.procesar_consulta("¿Cuántos ingresos hubo en mayo?", rol="SuperAdmin")

            tokens = res["metricas"]["tokens"]
            self.assertTrue(tokens["disponible"])
            self.assertEqual(tokens["prompt_tokens"], 350)  # 120 + 230
            self.assertEqual(tokens["completion_tokens"], 75)   # 25 + 50
            self.assertEqual(tokens["total_tokens"], 425)       # 145 + 280
            self.assertEqual(tokens["llamadas_api"], 2)

    @patch("requests.post")
    def test_03_tokens_ausentes_reporta_no_disponible_sin_inventar(self, mock_post):
        """Si la API no devuelve el campo usage, reporta disponible=False y tokens=None."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Respuesta sin usage"}}]
            # Sin campo 'usage'
        }
        mock_post.return_value = mock_resp

        resultado = proveedor.procesar_consulta("Hola", rol="SuperAdmin")
        tokens = resultado["metricas"]["tokens"]

        self.assertFalse(tokens["disponible"])
        self.assertIsNone(tokens["prompt_tokens"])
        self.assertIsNone(tokens["completion_tokens"])
        self.assertIsNone(tokens["total_tokens"])

    # =========================================================================
    # 2. GESTOR DE MÉTRICAS, PRESUPUESTO Y PERSISTENCIA SEGURA
    # =========================================================================

    def test_04_privacidad_estricta_sin_datos_personales_ni_consultas(self):
        """El registro de métricas nunca almacena textos de preguntas, respuestas, nombres ni DNIs."""
        reg = self.gestor.registrar_interaccion(
            proveedor="gemini",
            modelo="gemini-2.0-flash",
            intencion="resumen_ingresos",
            tiempo_ms=450,
            tokens={"disponible": True, "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            exito=True,
            es_fallback=False
        )

        campos_prohibidos = ["pregunta", "mensaje", "datos", "alumno", "dni", "nombre", "codigo", "api_key", "respuesta"]
        for campo in campos_prohibidos:
            self.assertNotIn(campo, reg)

        # Verificar también en el archivo persistido
        with open(self.archivo_tmp, "r", encoding="utf-8") as f:
            contenido = f.read()
            for campo in campos_prohibidos:
                self.assertNotIn(f'"{campo}"', contenido)

    def test_05_presupuesto_configurable_con_porcentaje_y_etiqueta_correcta(self):
        """Con LLM_PRESUPUESTO_TOKENS calcula porcentaje rotulado exclusivamente como presupuesto."""
        with patch.dict(os.environ, {"LLM_PRESUPUESTO_TOKENS": "500000"}):
            self.gestor.registrar_interaccion(
                proveedor="gemini",
                modelo="gemini-2.0-flash",
                intencion="resumen_ingresos",
                tiempo_ms=500,
                tokens={"disponible": True, "prompt_tokens": 20000, "completion_tokens": 5000, "total_tokens": 25000}
            )

            resumen = self.gestor.obtener_resumen()
            presupuesto = resumen["presupuesto"]

            self.assertTrue(presupuesto["configurado"])
            self.assertEqual(presupuesto["limite_tokens"], 500000)
            self.assertEqual(presupuesto["porcentaje"], 5.0)  # 25,000 / 500,000 = 5%
            self.assertEqual(presupuesto["etiqueta"], "Porcentaje del presupuesto configurado")
            self.assertNotIn("Google", presupuesto["etiqueta"])
            self.assertNotIn("gratuita", presupuesto["etiqueta"])

    def test_06_presupuesto_no_configurado_muestra_no_configurado_sin_porcentaje(self):
        """Sin LLM_PRESUPUESTO_TOKENS, el panel indica 'No configurado' y porcentaje=None."""
        with patch.dict(os.environ, {}, clear=True):
            self.gestor.registrar_interaccion(
                proveedor="gemini",
                modelo="gemini-2.0-flash",
                intencion="resumen_ingresos",
                tiempo_ms=400,
                tokens={"disponible": True, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
            )

            resumen = self.gestor.obtener_resumen()
            presupuesto = resumen["presupuesto"]

            self.assertFalse(presupuesto["configurado"])
            self.assertIsNone(presupuesto["limite_tokens"])
            self.assertIsNone(presupuesto["porcentaje"])
            self.assertEqual(presupuesto["etiqueta"], "No configurado")

    # =========================================================================
    # 3. COORDINADOR Y REGISTRO AUTOMÁTICO DE FALLBACK
    # =========================================================================

    @patch("requests.post")
    def test_07_coordinador_registra_evento_fallback_ante_error(self, mock_post):
        """Cuando la API del LLM falla, el coordinador registra el evento de fallback en las métricas."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection error to LLM")

        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyFakeTestKey",
            "LLM_MODELO": "gemini-2.0-flash",
            "LLM_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai/"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("llm.services.coordinador_asistente.obtener_gestor_metricas") as mock_get_gestor:
                mock_gestor_inst = MagicMock()
                mock_get_gestor.return_value = mock_gestor_inst

                with patch("llm.services.coordinador_asistente.obtener_resumen_ingresos") as mock_resumen:
                    mock_resumen.return_value = {
                        "total_ingresos": 100,
                        "fecha_inicio": "2026-05-01",
                        "fecha_fin": "2026-05-15",
                        "status": "success"
                    }

                    res = procesar_pregunta("Resumen de ingresos del 2026-05-01 al 2026-05-15", rol="SuperAdmin")

                    self.assertEqual(res["status"], "success")
                    self.assertEqual(res["origen"], "consulta_segura")

                    # Verificar que se registró el evento de fallback
                    mock_gestor_inst.registrar_interaccion.assert_called_once()
                    kwargs = mock_gestor_inst.registrar_interaccion.call_args[1]
                    self.assertFalse(kwargs["exito"])
                    self.assertTrue(kwargs["es_fallback"])
                    self.assertIsNone(kwargs["tokens"])

    # =========================================================================
    # 4. CONTROL DE ACCESO AL PANEL DE MÉTRICAS SEGÚN ROL
    # =========================================================================

    def test_08_superadmin_puede_acceder_al_panel_y_api_de_metricas(self):
        """Rol SuperAdmin tiene acceso autorizado a la vista HTML y a la API JSON de métricas."""
        with self.client.session_transaction() as sess:
            sess["admin_user"] = "superadmin"
            sess["admin_rol"] = "SuperAdmin"

        # Vista HTML
        resp_html = self.client.get("/admin/asistente/metricas")
        self.assertEqual(resp_html.status_code, 200)
        self.assertIn(b"Uso del asistente", resp_html.data)
        self.assertIn(b"Tokens Totales", resp_html.data)

        # API JSON
        resp_api = self.client.get("/admin/api/asistente/metricas")
        self.assertEqual(resp_api.status_code, 200)
        data = resp_api.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("solicitudes", data["datos"])
        self.assertIn("tokens", data["datos"])

    def test_09_consultor_bloqueado_en_panel_y_api_de_metricas(self):
        """Rol Consultor recibe HTTP 403 al intentar acceder al panel o a la API de métricas."""
        with self.client.session_transaction() as sess:
            sess["admin_user"] = "consultor1"
            sess["admin_rol"] = "Consultor"

        resp_html = self.client.get("/admin/asistente/metricas")
        self.assertEqual(resp_html.status_code, 403)

        resp_api = self.client.get("/admin/api/asistente/metricas")
        self.assertEqual(resp_api.status_code, 403)
        data = resp_api.get_json()
        self.assertEqual(data["categoria_error"], "permiso")


if __name__ == "__main__":
    unittest.main()
