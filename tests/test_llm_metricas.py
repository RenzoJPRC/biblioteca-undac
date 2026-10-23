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

    def test_error_clasificado_sin_textos_arbitrarios(self):
        r=self.gestor.registrar_interaccion('gemini','ficticio','consulta_general',12,exito=False,es_fallback=True,categoria_error='cuota',codigo_http=429)
        self.assertEqual(r['categoria_error'],'cuota')
        self.assertEqual(r['codigo_http'],429)
        r=self.gestor.registrar_interaccion('gemini','ficticio','consulta_general',12,categoria_error='texto privado ficticio',codigo_http=999)
        self.assertNotIn('categoria_error',r)
        self.assertNotIn('codigo_http',r)

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

    # =========================================================================
    # 5. PRUEBAS ENFOCADAS: TOKENS SIN DESGLOSE Y CASO RENDER GEMINI
    # =========================================================================

    @patch("requests.post")
    def test_10_llamada_directa_con_tokens_sin_desglose(self, mock_post):
        """Llamada directa donde el proveedor reporta total mayor a la suma de prompt y completion registra tokens_sin_desglose."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "La biblioteca abre de lunes a viernes."}}],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 40,
                "total_tokens": 600  # 60 tokens sin desglose en el usage
            }
        }
        mock_post.return_value = mock_resp

        resultado = proveedor.procesar_consulta("¿Cuál es el horario?", rol="SuperAdmin")
        tokens = resultado["metricas"]["tokens"]

        self.assertTrue(tokens["disponible"])
        self.assertEqual(tokens["prompt_tokens"], 500)
        self.assertEqual(tokens["completion_tokens"], 40)
        self.assertEqual(tokens["tokens_sin_desglose"], 60)
        self.assertEqual(tokens["total_tokens"], 600)
        self.assertEqual(tokens["prompt_tokens"] + tokens["completion_tokens"] + tokens["tokens_sin_desglose"], tokens["total_tokens"])
        self.assertEqual(tokens["llamadas_api"], 1)

    @patch("requests.post")
    def test_11_llamada_directa_coincidencia_aritmetica_estricta(self, mock_post):
        """Llamada directa con desglose exacto reporta tokens_sin_desglose en 0."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Respuesta directa estándar."}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 30,
                "total_tokens": 230
            }
        }
        mock_post.return_value = mock_resp

        resultado = proveedor.procesar_consulta("Consulta directa", rol="SuperAdmin")
        tokens = resultado["metricas"]["tokens"]

        self.assertTrue(tokens["disponible"])
        self.assertEqual(tokens["prompt_tokens"], 200)
        self.assertEqual(tokens["completion_tokens"], 30)
        self.assertEqual(tokens["tokens_sin_desglose"], 0)
        self.assertEqual(tokens["total_tokens"], 230)
        self.assertEqual(tokens["prompt_tokens"] + tokens["completion_tokens"] + tokens["tokens_sin_desglose"], tokens["total_tokens"])
        self.assertEqual(tokens["llamadas_api"], 1)

    @patch("requests.post")
    def test_12_ciclo_dos_llamadas_herramienta_caso_render_con_tokens_sin_desglose(self, mock_post):
        """
        Caso exacto de Render verificado:
        - 1 solicitud, 2 llamadas al LLM (tool call + síntesis), 0 fallback.
        - Llamada 1: prompt=550, completion=0, total=650.
        - Llamada 2: prompt=587, completion=49, total=650.
        - Preserva exactamente los valores reportados: prompt=1,137, completion=49, total=1,300.
        - Registra la diferencia aritmética de 114 como tokens_sin_desglose (1,137 + 49 + 114 = 1,300).
        - Presupuesto calculado estrictamente con total_tokens reportado (1,300).
        """
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        # Llamada 1: Gemini decide invocar obtener_resumen_ingresos
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_rend_1",
                        "type": "function",
                        "function": {
                            "name": "obtener_resumen_ingresos",
                            "arguments": json.dumps({"fecha_inicio": "2026-06-01", "fecha_fin": "2026-07-31"})
                        }
                    }]
                }
            }],
            "usage": {
                "prompt_tokens": 550,
                "completion_tokens": 0,
                "total_tokens": 650
            }
        }

        # Llamada 2: Gemini recibe los datos agregados y sintetiza la respuesta final
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Entre el 1 de junio y el 31 de julio de 2026 hubo 18,168 ingresos registrados."
                }
            }],
            "usage": {
                "prompt_tokens": 587,
                "completion_tokens": 49,
                "total_tokens": 650
            }
        }
        mock_post.side_effect = [resp1, resp2]

        with patch("llm.services.proveedor_llm.obtener_resumen_ingresos") as mock_tool:
            mock_tool.return_value = {
                "total_ingresos": 18168,
                "fecha_inicio": "2026-06-01",
                "fecha_fin": "2026-07-31",
                "sede": None,
                "status": "success"
            }

            res = proveedor.procesar_consulta(
                "¿Cuántos ingresos de alumnos hubo entre el 1 de junio y el 31 de julio de 2026?",
                rol="SuperAdmin"
            )

            tokens = res["metricas"]["tokens"]
            self.assertTrue(tokens["disponible"])
            self.assertEqual(tokens["prompt_tokens"], 1137)       # 550 + 587
            self.assertEqual(tokens["completion_tokens"], 49)     # 0 + 49 reportado por proveedor
            self.assertEqual(tokens["tokens_sin_desglose"], 114)  # 1300 - (1137 + 49)
            self.assertEqual(tokens["total_tokens"], 1300)        # 650 + 650
            self.assertEqual(tokens["llamadas_api"], 2)
            # Verificación aritmética estricta
            self.assertEqual(
                tokens["prompt_tokens"] + tokens["completion_tokens"] + tokens["tokens_sin_desglose"],
                tokens["total_tokens"]
            )

            # Probar registro en el gestor de métricas y cálculo de presupuesto configurable
            with patch.dict(os.environ, {"LLM_PRESUPUESTO_TOKENS": "100000"}):
                reg = self.gestor.registrar_interaccion(
                    proveedor="gemini",
                    modelo="gemini-2.0-flash",
                    intencion=res["intencion"],
                    tiempo_ms=res["metricas"]["tiempo_ms"],
                    tokens=tokens,
                    exito=True,
                    es_fallback=False
                )

                self.assertEqual(reg["prompt_tokens"], 1137)
                self.assertEqual(reg["completion_tokens"], 49)
                self.assertEqual(reg["tokens_sin_desglose"], 114)
                self.assertEqual(reg["total_tokens"], 1300)
                self.assertEqual(
                    reg["prompt_tokens"] + reg["completion_tokens"] + reg["tokens_sin_desglose"],
                    reg["total_tokens"]
                )

                resumen = self.gestor.obtener_resumen()
                tok_resumen = resumen["tokens"]
                self.assertEqual(tok_resumen["prompt_tokens"], 1137)
                self.assertEqual(tok_resumen["completion_tokens"], 49)
                self.assertEqual(tok_resumen["tokens_sin_desglose"], 114)
                self.assertEqual(tok_resumen["total_tokens"], 1300)
                self.assertEqual(
                    tok_resumen["prompt_tokens"] + tok_resumen["completion_tokens"] + tok_resumen["tokens_sin_desglose"],
                    tok_resumen["total_tokens"]
                )

                # Verificación de cálculo del porcentaje del presupuesto usando total_tokens reportado
                presupuesto = resumen["presupuesto"]
                self.assertTrue(presupuesto["configurado"])
                self.assertEqual(presupuesto["limite_tokens"], 100000)
                # 1,300 / 100,000 * 100 = 1.3%
                self.assertEqual(presupuesto["porcentaje"], 1.3)
                self.assertEqual(presupuesto["etiqueta"], "Porcentaje del presupuesto configurado")

    def test_13_normalizacion_de_registros_previos_con_discrepancia_al_cargar(self):
        """Registros previos en archivo con discrepancia histórica (1137, 49, 1300) preservan valores y calculan tokens_sin_desglose."""
        datos_previos = [{
            "id": "met_prev_01",
            "timestamp": "2026-10-16T00:00:00",
            "proveedor": "gemini",
            "modelo": "gemini-2.0-flash",
            "intencion": "resumen_ingresos",
            "tiempo_ms": 650,
            "prompt_tokens": 1137,
            "completion_tokens": 49,
            "total_tokens": 1300,
            "tokens_disponibles": True,
            "llamadas_api": 2,
            "exito": True,
            "es_fallback": False
        }]

        # Escribir en archivo temporal
        with open(self.archivo_tmp, "w", encoding="utf-8") as f:
            json.dump(datos_previos, f)

        # Crear gestor apuntando a ese archivo
        gestor_recuperado = GestorMetricasAsistente(archivo_persistencia=self.archivo_tmp)
        resumen = gestor_recuperado.obtener_resumen()

        tok = resumen["tokens"]
        self.assertTrue(tok["disponible"])
        self.assertEqual(tok["prompt_tokens"], 1137)
        self.assertEqual(tok["completion_tokens"], 49)
        self.assertEqual(tok["tokens_sin_desglose"], 114)
        self.assertEqual(tok["total_tokens"], 1300)
        self.assertEqual(tok["prompt_tokens"] + tok["completion_tokens"] + tok["tokens_sin_desglose"], tok["total_tokens"])

    def test_14_panel_metricas_renderiza_tokens_sin_desglose(self):
        """El panel HTML de métricas renderiza visualmente el campo 'Tokens sin desglose' y su valor."""
        with patch("llm.services.metricas_asistente.obtener_gestor_metricas", return_value=self.gestor):
            self.gestor.registrar_interaccion(
                proveedor="gemini",
                modelo="gemini-2.0-flash",
                intencion="resumen_ingresos",
                tiempo_ms=750,
                tokens={
                    "disponible": True,
                    "prompt_tokens": 1137,
                    "completion_tokens": 49,
                    "tokens_sin_desglose": 114,
                    "total_tokens": 1300,
                    "llamadas_api": 2
                },
                exito=True,
                es_fallback=False
            )

            with self.client.session_transaction() as sess:
                sess["admin_user"] = "superadmin"
                sess["admin_rol"] = "SuperAdmin"

            resp_html = self.client.get("/admin/asistente/metricas")
            self.assertEqual(resp_html.status_code, 200)
            self.assertIn(b"Tokens sin desglose:", resp_html.data)
            self.assertIn(b"114", resp_html.data)
            self.assertIn(b"(+114 sin desglose)", resp_html.data)
            self.assertIn(b"1,300", resp_html.data)


if __name__ == "__main__":
    unittest.main()

