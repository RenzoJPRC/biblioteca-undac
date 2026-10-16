import unittest
from unittest.mock import patch, MagicMock
import os
import json
import requests

from llm.services.proveedor_llm import (
    ConfiguracionLLM,
    obtener_configuracion,
    ProveedorLLM,
    ejecutar_herramienta_backend,
    HERRAMIENTAS_BIBLIOTECA,
    LLMError,
    LLMTimeoutError,
    LLMNetworkError,
    LLMAPIError
)
from llm.services.filtro_privacidad import (
    es_consulta_personal_o_identificable,
    sanitizar_texto_para_llm
)
from llm.services.coordinador_asistente import procesar_pregunta


class TestProveedorLLM(unittest.TestCase):

    # =========================================================================
    # 1. CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
    # =========================================================================

    def test_01_configuracion_por_defecto(self):
        """Valores por defecto cuando no hay variables de entorno configuradas."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = ConfiguracionLLM()
            self.assertEqual(cfg.proveedor, "openai")
            self.assertFalse(cfg.activado)
            self.assertEqual(cfg.api_key, "")
            self.assertEqual(cfg.modelo, "gpt-4o-mini")
            self.assertEqual(cfg.api_base, "https://api.openai.com/v1")
            self.assertEqual(cfg.timeout, 10)
            self.assertFalse(cfg.esta_disponible)

    def test_02_configuracion_personalizada_y_disponibilidad(self):
        """Lectura correcta de variables de entorno y disponibilidad."""
        env_vars = {
            "LLM_PROVEEDOR": "openai",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "sk-test-secret-key-123",
            "LLM_MODELO": "gpt-4o",
            "LLM_API_BASE": "https://api.custom.com/v1/",
            "LLM_TIMEOUT": "15"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ConfiguracionLLM()
            self.assertTrue(cfg.activado)
            self.assertTrue(cfg.esta_disponible)
            self.assertEqual(cfg.api_key, "sk-test-secret-key-123")
            self.assertEqual(cfg.modelo, "gpt-4o")
            self.assertEqual(cfg.api_base, "https://api.custom.com/v1")
            self.assertEqual(cfg.timeout, 15)

            # Verificar que to_dict no expone la api_key
            dic = cfg.to_dict()
            self.assertNotIn("api_key", dic)
            self.assertNotIn("sk-test-secret-key-123", str(dic))

    def test_03_no_disponible_si_falta_api_key_aunque_este_activado(self):
        """Si LLM_ACTIVADO=true pero no hay API_KEY, debe considerarse no disponible."""
        env_vars = {
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": ""
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ConfiguracionLLM()
            self.assertTrue(cfg.activado)
            self.assertFalse(cfg.esta_disponible)

    # =========================================================================
    # 2. FILTRO DE PRIVACIDAD ESTRICTO (CONSULTAS PERSONALES)
    # =========================================================================

    def test_04_filtro_privacidad_detecta_nombres_personales(self):
        """Las consultas con nombres de personas deben marcarse como personales."""
        preguntas_personales = [
            "Historial de ingresos de Juan Perez",
            "Último ingreso de Maria Quispe",
            "¿Cuántas veces ingresó Carlos Gomez?",
            "buscar a Gomez",
            "registro de ingresos de Pedro Ramirez"
        ]
        for p in preguntas_personales:
            es_pers, motivo = es_consulta_personal_o_identificable(p)
            self.assertTrue(es_pers, f"Fallo al detectar consulta personal: {p}")

    def test_05_filtro_privacidad_detecta_dni_o_codigos(self):
        """Detección obligatoria de DNIs de 8 dígitos y códigos de matrícula."""
        es_pers_dni, mot_dni = es_consulta_personal_o_identificable("¿Ingresó el DNI 45891234 hoy?")
        self.assertTrue(es_pers_dni)
        self.assertEqual(mot_dni, "dni_detectado")

        es_pers_cod, mot_cod = es_consulta_personal_o_identificable("conteo de visitas del código 2020123456")
        self.assertTrue(es_pers_cod)
        self.assertEqual(mot_cod, "codigo_matricula_detectado")

        es_pers_sel, mot_sel = es_consulta_personal_o_identificable("confirmar", seleccion_persona={"persona_id": 1})
        self.assertTrue(es_pers_sel)
        self.assertEqual(mot_sel, "seleccion_persona_presente")

    def test_06_filtro_privacidad_permite_consultas_agregadas_o_predictivas(self):
        """Consultas analíticas generales no deben marcarse como personales."""
        preguntas_agregadas = [
            "Resumen de ingresos del 2026-03-01 al 2026-03-15",
            "¿Cuáles fueron las horas de mayor afluencia?",
            "Consulta descriptiva de afluencia histórica en Central",
            "¿Cuál será la afluencia esperada mañana a las 10:00 en Central?",
            "Hola, ¿qué puedes hacer?"
        ]
        for p in preguntas_agregadas:
            es_pers, _ = es_consulta_personal_o_identificable(p)
            self.assertFalse(es_pers, f"Marcó erróneamente como personal: {p}")

    def test_07_sanitizador_de_texto_oculta_numeros_identificables(self):
        """El sanitizador oculta secuencias de 8 dígitos antes de enviarse al exterior."""
        texto_crudo = "Verificar si el usuario con 45891234 ingresó"
        texto_limpio = sanitizar_texto_para_llm(texto_crudo)
        self.assertNotIn("45891234", texto_limpio)
        self.assertIn("[DOCUMENTO_RESERVADO]", texto_limpio)

    # =========================================================================
    # 3. EJECUCIÓN SEGURA DE HERRAMIENTAS DE BACKEND (NO SQL)
    # =========================================================================

    @patch("llm.services.proveedor_llm.obtener_resumen_ingresos")
    def test_08_ejecutar_herramienta_resumen_ingresos(self, mock_resumen):
        """La herramienta resumen ejecuta la función segura con control de sede y rol."""
        mock_resumen.return_value = {"total_ingresos": 42, "status": "success"}
        args = {"fecha_inicio": "2026-05-01", "fecha_fin": "2026-05-15", "sede": "Central"}

        datos, intencion = ejecutar_herramienta_backend("obtener_resumen_ingresos", args, "SuperAdmin", None)
        self.assertEqual(intencion, "resumen_ingresos")
        self.assertEqual(datos["total_ingresos"], 42)
        mock_resumen.assert_called_once_with(
            fecha_inicio="2026-05-01",
            fecha_fin="2026-05-15",
            rol="SuperAdmin",
            sede_permitida="Central"
        )

    @patch("llm.services.proveedor_llm.obtener_horas_mayor_afluencia")
    def test_09_supervisor_forzado_a_su_sede_en_herramientas(self, mock_horas):
        """Un Supervisor no puede consultar otra sede mediante tools; el servidor lo fuerza."""
        mock_horas.return_value = {"horas_top": [], "status": "success"}
        # Intenta engañar pasando sede "Central"
        args = {"fecha_inicio": "2026-05-01", "fecha_fin": "2026-05-15", "sede": "Central", "limite": 3}

        datos, intencion = ejecutar_herramienta_backend("obtener_horas_mayor_afluencia", args, "Supervisor", "Tarma")
        self.assertEqual(intencion, "horas_mayor_afluencia")
        mock_horas.assert_called_once_with(
            fecha_inicio="2026-05-01",
            fecha_fin="2026-05-15",
            rol="Supervisor",
            sede_permitida="Tarma",
            limite=3
        )

    @patch("llm.services.proveedor_llm.predecir_afluencia_desde_bd")
    def test_10_ejecutar_herramienta_predecir_ml(self, mock_ml):
        """La herramienta ML invoca exclusivamente predecir_afluencia_desde_bd."""
        mock_ml.return_value = {"prediccion": {"nivel": "Media"}, "status": "success"}
        args = {"fecha": "2026-10-20", "hora": 11, "sede": "Central"}

        datos, intencion = ejecutar_herramienta_backend("predecir_afluencia_ml", args, "SuperAdmin", None)
        self.assertEqual(intencion, "prediccion_afluencia")
        mock_ml.assert_called_once_with(fecha="2026-10-20", hora=11, sede="Central")

    def test_11_herramienta_desconocida_rechazada(self):
        """Cualquier intento de invocar herramientas no autorizadas debe ser rechazado."""
        with self.assertRaises(ValueError):
            ejecutar_herramienta_backend("ejecutar_sql_arbitrario", {"query": "SELECT * FROM Alumnos"}, "SuperAdmin", None)

    # =========================================================================
    # 4. PROCESAMIENTO CON EL PROVEEDOR LLM (MOCK DE API EXTERNA)
    # =========================================================================

    @patch("requests.post")
    def test_12_procesamiento_exitoso_con_tool_calling(self, mock_post):
        """Ciclo completo: el LLM llama a una herramienta y genera la síntesis final."""
        cfg = ConfiguracionLLM()
        cfg.activado = True
        cfg.api_key = "sk-fake-key"
        cfg.modelo = "gpt-4o-mini"
        proveedor = ProveedorLLM(config=cfg)

        # Simular primera respuesta con Tool Call
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "obtener_resumen_ingresos",
                            "arguments": json.dumps({"fecha_inicio": "2026-04-01", "fecha_fin": "2026-04-15", "sede": "Central"})
                        }
                    }]
                }
            }]
        }

        # Simular segunda respuesta con síntesis final
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Entre el 1 y el 15 de abril de 2026 se registraron un total de 120 ingresos en Central."
                }
            }]
        }
        mock_post.side_effect = [resp1, resp2]

        with patch("llm.services.proveedor_llm.obtener_resumen_ingresos") as mock_resumen:
            mock_resumen.return_value = {"total_ingresos": 120, "fecha_inicio": "2026-04-01", "fecha_fin": "2026-04-15"}
            res = proveedor.procesar_consulta(
                pregunta="Resumen de ingresos del 2026-04-01 al 2026-04-15 en Central",
                rol="SuperAdmin"
            )

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["origen"], "llm_externo")
            self.assertEqual(res["intencion"], "resumen_ingresos")
            self.assertIn("120 ingresos", res["mensaje"])
            self.assertEqual(res["datos"]["total_ingresos"], 120)

    @patch("requests.post")
    def test_13_manejo_de_timeout_en_proveedor_llm(self, mock_post):
        """Tiempo de espera agotado debe lanzar LLMTimeoutError."""
        mock_post.side_effect = requests.exceptions.Timeout("Read timed out")
        cfg = ConfiguracionLLM()
        cfg.activado = True
        cfg.api_key = "sk-fake-key"
        cfg.modelo = "gpt-4o-mini"
        proveedor = ProveedorLLM(config=cfg)

        with self.assertRaises(LLMTimeoutError):
            proveedor.procesar_consulta("Hola", "SuperAdmin")

    @patch("requests.post")
    def test_14_manejo_de_error_http_api_llm(self, mock_post):
        """Errores HTTP 401 o 429 deben lanzar LLMAPIError con status code."""
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized: Invalid API key"
        mock_post.return_value = resp

        cfg = ConfiguracionLLM()
        cfg.activado = True
        cfg.api_key = "sk-invalid-key"
        cfg.modelo = "gpt-4o-mini"
        proveedor = ProveedorLLM(config=cfg)

        with self.assertRaises(LLMAPIError) as ctx:
            proveedor.procesar_consulta("Hola", "SuperAdmin")
        self.assertEqual(ctx.exception.status_code, 401)

    # =========================================================================
    # 5. INTEGRACIÓN EN COORDINADOR Y MECANISMO DE FALLBACK
    # =========================================================================

    def test_15_coordinador_usa_motor_local_cuando_llm_desactivado(self):
        """Si LLM_ACTIVADO=false, procesar_pregunta usa el motor local sin llamar al LLM."""
        with patch.dict(os.environ, {"LLM_ACTIVADO": "false"}, clear=True):
            with patch("llm.services.coordinador_asistente.ProveedorLLM") as mock_llm_cls:
                res = procesar_pregunta("¿Cuáles fueron las horas de mayor afluencia?", rol="SuperAdmin")
                mock_llm_cls.assert_not_called()
                # Debe responder a través del motor local (pide fechas o responde)
                self.assertIn(res["status"], ("requires_input", "success"))
                self.assertIn(res.get("origen"), ("regla_local", "consulta_segura"))

    def test_16_consultas_personales_nunca_van_al_llm_incluso_con_llm_activado(self):
        """Una consulta sobre una persona específica NUNCA invoca al proveedor externo."""
        env_vars = {
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "sk-real-looking-key"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("llm.services.coordinador_asistente.ProveedorLLM") as mock_llm_cls:
                with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
                    mock_cursor = MagicMock()
                    mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
                    mock_cursor.fetchall.return_value = []

                    res = procesar_pregunta("Historial de Juan Perez", rol="SuperAdmin")
                    # El LLM no debe ser instanciado ni llamado
                    mock_llm_cls.assert_not_called()
                    self.assertNotEqual(res.get("origen"), "llm_externo")

    @patch("llm.services.coordinador_asistente.ProveedorLLM")
    def test_17_fallback_automatico_al_motor_local_ante_falla_del_llm(self, mock_llm_cls):
        """Si el LLM externo falla o agota el tiempo de espera, se activa el fallback local transparente."""
        env_vars = {
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "sk-test-key"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            mock_inst = MagicMock()
            mock_inst.procesar_consulta.side_effect = LLMTimeoutError("Read timed out")
            mock_llm_cls.return_value = mock_inst

            # Consulta analítica con fechas explícitas para permitir resolución local
            with patch("llm.services.coordinador_asistente.obtener_resumen_ingresos") as mock_resumen:
                mock_resumen.return_value = {
                    "total_ingresos": 50,
                    "fecha_inicio": "2026-05-01",
                    "fecha_fin": "2026-05-15",
                    "status": "success"
                }

                res = procesar_pregunta(
                    "Resumen de ingresos del 2026-05-01 al 2026-05-15",
                    rol="SuperAdmin"
                )

                # Debe haber intentado llamar al LLM
                mock_inst.procesar_consulta.assert_called_once()
                # Pero ante el fallo, resolvió con el respaldo local
                self.assertEqual(res["status"], "success")
                self.assertEqual(res["intencion"], "resumen_ingresos")
                self.assertEqual(res.get("origen"), "consulta_segura")
                self.assertIn("50 ingresos", res["mensaje"])

    def test_18_rol_consultor_bloqueado_localmente_en_consulta_personal(self):
        """El rol Consultor es bloqueado con 403 sin consultar base ni LLM."""
        env_vars = {
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "sk-test-key"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("llm.services.coordinador_asistente.ProveedorLLM") as mock_llm_cls:
                res = procesar_pregunta("Historial de Juan Perez", rol="Consultor")
                mock_llm_cls.assert_not_called()
                self.assertEqual(res["status"], "error")
                self.assertEqual(res["codigo_http"], 403)
                self.assertEqual(res["categoria_error"], "permiso")

    # =========================================================================
    # 6. PRUEBAS PARA RIESGOS CONCRETOS DESCUBIERTOS EN LA REVISIÓN
    # =========================================================================

    def test_19_preguntas_con_nombres_o_verbos_personales_derivadas_a_motor_local(self):
        """No depender solo de regex de nombres: cualquier verbo o término individual deriva a local."""
        from llm.services.filtro_privacidad import es_consulta_permitida_para_llm

        preguntas_dudosas = [
            ("Carlos vino ayer?", "intencion_desconocida"),
            ("Dime si asistio Mendoza", "intencion_desconocida"),
            ("Estuvo Pamela en la biblioteca?", "intencion_desconocida"),
            ("Falto el alumno a clases?", "intencion_desconocida"),
            ("Quien entro hoy a las 10?", "intencion_desconocida")
        ]

        for preg, intent in preguntas_dudosas:
            permitida, motivo = es_consulta_permitida_para_llm(preg, intent, es_seguimiento=False)
            self.assertFalse(permitida, f"Permitió erróneamente enviar al LLM: '{preg}' (motivo: {motivo})")

    def test_20_seguimientos_y_preguntas_ambiguas_no_van_al_llm(self):
        """Preguntas de seguimiento y preguntas ambiguas se resuelven exclusivamente de forma local."""
        from llm.services.filtro_privacidad import es_consulta_permitida_para_llm

        # Caso seguimiento
        perm_seg, mot_seg = es_consulta_permitida_para_llm("¿Y ayer?", "resumen_ingresos", es_seguimiento=True)
        self.assertFalse(perm_seg)
        self.assertEqual(mot_seg, "seguimiento_conversacional_local")

        # Caso intención no permitida / ambigua
        perm_amb, mot_amb = es_consulta_permitida_para_llm("¿Y entonces?", "intencion_desconocida", es_seguimiento=False)
        self.assertFalse(perm_amb)

    def test_21_sanitizacion_datos_herramienta_hacia_llm(self):
        """Garantiza que cualquier clave individual o sensible sea depurada antes de enviarse al LLM."""
        from llm.services.filtro_privacidad import sanitizar_datos_herramienta_para_llm

        datos_con_fuga = {
            "total_ingresos": 100,
            "sede": "Central",
            "detalles_alumnos": [
                {"nombre": "Alumno Ficticio", "dni": "12345678", "codigo_mat": "20201234", "hora": "10:00"},
                {"nombre": "Otro Alumno", "dni": "87654321", "codigo_mat": "20205678", "hora": "11:00"}
            ]
        }

        datos_limpios = sanitizar_datos_herramienta_para_llm(datos_con_fuga)
        self.assertEqual(datos_limpios["total_ingresos"], 100)
        self.assertEqual(datos_limpios["sede"], "Central")
        # En los detalles, las claves sensibles 'nombre', 'dni' y 'codigo_mat' deben haber sido purgadas
        for item in datos_limpios["detalles_alumnos"]:
            self.assertNotIn("nombre", item)
            self.assertNotIn("dni", item)
            self.assertNotIn("codigo_mat", item)
            self.assertIn("hora", item)

    def test_22_validador_salida_bloquea_sentencias_sql(self):
        """Si la respuesta del LLM contiene sentencias o código SQL, debe ser rechazada."""
        from llm.services.filtro_privacidad import validar_y_sanitizar_respuesta_llm

        mensajes_peligrosos = [
            "Puedes consultar la tabla ejecutando: SELECT * FROM RegistroIngresos WHERE EsPrueba = 0",
            "DELETE FROM Alumnos WHERE AlumnoID = 1",
            "UPDATE RegistroIngresos SET Sede = 'Central'",
            "UNION SELECT username, password FROM Usuarios"
        ]

        for m in mensajes_peligrosos:
            valido, _, motivo = validar_y_sanitizar_respuesta_llm(m)
            self.assertFalse(valido, f"Permitió erróneamente SQL en salida: {m}")
            self.assertEqual(motivo, "deteccion_sentencia_sql_en_salida")

    def test_23_validador_salida_enmascara_dni_alucinado(self):
        """Si la respuesta del LLM contiene un número de 8 dígitos, este debe ser enmascarado."""
        from llm.services.filtro_privacidad import validar_y_sanitizar_respuesta_llm

        mensaje = "El total de ingresos registrados para el usuario con documento 74859612 fue de 5."
        valido, limpio, _ = validar_y_sanitizar_respuesta_llm(mensaje)
        self.assertTrue(valido)
        self.assertNotIn("74859612", limpio)
        self.assertIn("[DOCUMENTO_RESERVADO]", limpio)

    def test_24_validador_salida_bloquea_script_tags(self):
        """Si la respuesta del LLM contiene inyecciones de script, debe ser rechazada."""
        from llm.services.filtro_privacidad import validar_y_sanitizar_respuesta_llm

        mensaje_xss = "Aquí está el resultado: <script>alert('xss')</script>"
        valido, _, motivo = validar_y_sanitizar_respuesta_llm(mensaje_xss)
        self.assertFalse(valido)
        self.assertEqual(motivo, "deteccion_script_en_salida")

    def test_25_dni_formatos_espacios_guiones_y_fechas_iso(self):
        """Detección de DNI con espacios y guiones, sin confundir fechas ISO YYYY-MM-DD."""
        from llm.services.filtro_privacidad import es_consulta_personal_o_identificable

        # Formatos de DNI que deben ser bloqueados
        dnis_variados = [
            "¿Ingresó el DNI 45 89 12 34 hoy?",
            "Verificar documento 4589-1234",
            "Consulta para 45-89-12-34",
            "asistencia de 4589 1234"
        ]
        for preg in dnis_variados:
            es_pers, motivo = es_consulta_personal_o_identificable(preg)
            self.assertTrue(es_pers, f"No detectó DNI con separadores en: {preg}")
            self.assertEqual(motivo, "dni_detectado")

        # Fechas ISO válidas NO deben ser confundidas con DNI
        es_pers_fecha, _ = es_consulta_personal_o_identificable("Resumen de ingresos del 2026-03-01 al 2026-03-15")
        self.assertFalse(es_pers_fecha, "Confundió una fecha ISO con un DNI")

    def test_26_preguntas_indirectas_bloqueadas_antes_del_llm(self):
        """Preguntas indirectas sobre asistencia de personas deben bloquearse antes de llamar al LLM."""
        from llm.services.filtro_privacidad import es_consulta_permitida_para_llm

        preguntas_indirectas = [
            "Quisiera averiguar si esa persona vino hoy",
            "Podrías fijarte si vino alguien con documento a la sede",
            "Me puedes decir a qué hora asistió el muchacho",
            "Hubo algún registro del alumno en la tarde?",
            "Confirmar si estuvo el docente en sala"
        ]

        for preg in preguntas_indirectas:
            permitida, motivo = es_consulta_permitida_para_llm(preg, "intencion_desconocida", es_seguimiento=False)
            self.assertFalse(permitida, f"Permitió pregunta indirecta: '{preg}' (motivo: {motivo})")

    def test_27_salida_enmascara_codigos_y_dni_con_guiones(self):
        """El validador de salida enmascara códigos de alumno y DNIs con guión."""
        from llm.services.filtro_privacidad import validar_y_sanitizar_respuesta_llm

        texto = "El alumno con código 2020-123456 y documento 4589-1234 registró 3 ingresos."
        valido, limpio, _ = validar_y_sanitizar_respuesta_llm(texto)
        self.assertTrue(valido)
        self.assertNotIn("4589-1234", limpio)
        self.assertNotIn("2020-123456", limpio)
        self.assertIn("[DOCUMENTO_RESERVADO]", limpio)
        self.assertIn("[CÓDIGO_RESERVADO]", limpio)

    # =========================================================================
    # 8. ADAPTACIÓN Y PRUEBAS PARA GEMINI DEVELOPER API (OPENAI COMPATIBLE)
    # =========================================================================

    def test_28_configuracion_gemini_valores_por_defecto(self):
        """Valores por defecto para Gemini: modelo vacío (requiere configuración) y base URL de Google AI Studio."""
        env_vars = {
            "LLM_PROVEEDOR": "gemini"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ConfiguracionLLM()
            self.assertEqual(cfg.proveedor, "gemini")
            self.assertEqual(cfg.modelo, "")
            self.assertEqual(cfg.api_base, "https://generativelanguage.googleapis.com/v1beta/openai")
            self.assertFalse(cfg.activado)
            self.assertFalse(cfg.esta_disponible)

    def test_29_configuracion_gemini_con_url_con_barra_final_y_mascaras(self):
        """Manejo de URL con barra final, variables personalizadas y protección de credencial."""
        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyTestSecretGeminiKey123",
            "LLM_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "LLM_MODELO": "gemini-2.0-flash"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ConfiguracionLLM()
            self.assertTrue(cfg.activado)
            self.assertTrue(cfg.esta_disponible)
            self.assertEqual(cfg.api_base, "https://generativelanguage.googleapis.com/v1beta/openai")
            self.assertEqual(cfg.modelo, "gemini-2.0-flash")

            # La representación pública nunca debe revelar la clave
            dic = cfg.to_dict()
            self.assertNotIn("api_key", dic)
            self.assertNotIn("AIzaSyTestSecretGeminiKey123", str(dic))

    @patch("requests.post")
    def test_30_gemini_openai_endpoint_chat_completions_y_tool_calling(self, mock_post):
        """Simulación de ciclo Chat Completions y Function Calling con Gemini OpenAI endpoint."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeKeyForTestingOnly"
        cfg.modelo = "gemini-2.0-flash"
        cfg.api_base = "https://generativelanguage.googleapis.com/v1beta/openai"

        proveedor = ProveedorLLM(config=cfg)

        # 1. Simulación respuesta de Gemini solicitando tool call
        resp_tool_call = MagicMock()
        resp_tool_call.status_code = 200
        resp_tool_call.json.return_value = {
            "id": "chatcmpl-gemini-123",
            "object": "chat.completion",
            "model": "gemini-2.0-flash",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_gemini_456",
                        "type": "function",
                        "function": {
                            "name": "obtener_horas_mayor_afluencia",
                            "arguments": json.dumps({"fecha_inicio": "2026-05-01", "fecha_fin": "2026-05-15", "limite": 3})
                        }
                    }]
                },
                "finish_reason": "tool_calls"
            }]
        }

        # 2. Simulación respuesta de síntesis de Gemini tras recibir datos de la herramienta
        resp_sintesis = MagicMock()
        resp_sintesis.status_code = 200
        resp_sintesis.json.return_value = {
            "id": "chatcmpl-gemini-789",
            "object": "chat.completion",
            "model": "gemini-2.0-flash",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Las horas de mayor concurrencia registradas en el periodo son las 10:00, 11:00 y 16:00."
                },
                "finish_reason": "stop"
            }]
        }

        mock_post.side_effect = [resp_tool_call, resp_sintesis]

        with patch("llm.services.proveedor_llm.obtener_horas_mayor_afluencia") as mock_horas:
            mock_horas.return_value = {
                "horas_top": [
                    {"hora": 10, "cantidad": 85},
                    {"hora": 11, "cantidad": 78},
                    {"hora": 16, "cantidad": 72}
                ],
                "status": "success"
            }

            resultado = proveedor.procesar_consulta(
                pregunta="¿Cuáles son las horas con mayor concurrencia en la biblioteca?",
                rol="SuperAdmin"
            )

            # Verificaciones del resultado final
            self.assertEqual(resultado["status"], "success")
            self.assertEqual(resultado["origen"], "llm_externo")
            self.assertEqual(resultado["proveedor"], "gemini")
            self.assertEqual(resultado["modelo"], "gemini-2.0-flash")
            self.assertEqual(resultado["intencion"], "horas_mayor_afluencia")
            self.assertIn("10:00, 11:00 y 16:00", resultado["mensaje"])

            # Verificaciones de la primera llamada HTTP a Gemini
            llamada_1 = mock_post.call_args_list[0]
            url_1, kwargs_1 = llamada_1[0][0], llamada_1[1]
            self.assertEqual(url_1, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
            self.assertEqual(kwargs_1["headers"]["Authorization"], "Bearer AIzaSyFakeKeyForTestingOnly")
            self.assertEqual(kwargs_1["json"]["model"], "gemini-2.0-flash")
            self.assertEqual(kwargs_1["json"]["tool_choice"], "auto")
            self.assertEqual(len(kwargs_1["json"]["tools"]), 4)

            # Verificaciones de la segunda llamada HTTP (síntesis) a Gemini
            llamada_2 = mock_post.call_args_list[1]
            url_2, kwargs_2 = llamada_2[0][0], llamada_2[1]
            self.assertEqual(url_2, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
            mensajes_sintesis = kwargs_2["json"]["messages"]
            # Debe contener el mensaje del asistente con content no-nulo para compatibilidad estricta
            msg_asistente = [m for m in mensajes_sintesis if m["role"] == "assistant"][0]
            self.assertIsNotNone(msg_asistente["content"])
            self.assertEqual(msg_asistente["content"], "")
            self.assertEqual(msg_asistente["tool_calls"][0]["id"], "call_gemini_456")

            # Y el mensaje tool
            msg_tool = [m for m in mensajes_sintesis if m["role"] == "tool"][0]
            self.assertEqual(msg_tool["tool_call_id"], "call_gemini_456")
            self.assertEqual(msg_tool["name"], "obtener_horas_mayor_afluencia")

    @patch("requests.post")
    def test_31_gemini_consultas_personales_nunca_invocan_api(self, mock_post):
        """Con Gemini configurado y activo, consultas personales se resuelven 100% local sin tocar la API."""
        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyFakeKeyForTestingOnly",
            "LLM_MODELO": "gemini-2.0-flash",
            "LLM_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai/"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("llm.services.coordinador_asistente.contar_ingresos_alumno_por_codigo") as mock_conteo:
                mock_conteo.return_value = {
                    "total_ingresos": 5,
                    "mensaje": "El alumno con código 2020123456 registró 5 ingresos.",
                    "status": "success"
                }

                res = procesar_pregunta("¿Cuántas veces ingresó el alumno 2020123456?", rol="SuperAdmin")

                self.assertEqual(res["status"], "success")
                self.assertEqual(res["origen"], "consulta_segura")
                # Verificación estricta: Gemini API externa NUNCA fue contactada
                self.assertEqual(mock_post.call_count, 0)

    @patch("requests.post")
    def test_32_gemini_fallback_local_ante_error_api(self, mock_post):
        """Si la API de Gemini devuelve error 500, se activa de inmediato el fallback local."""
        resp_err = MagicMock()
        resp_err.status_code = 500
        resp_err.text = "Internal Gemini Service Error"
        mock_post.return_value = resp_err

        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyFakeKeyForTestingOnly",
            "LLM_MODELO": "gemini-2.0-flash",
            "LLM_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai/"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            with patch("llm.services.coordinador_asistente.obtener_resumen_ingresos") as mock_resumen:
                mock_resumen.return_value = {
                    "total_ingresos": 250,
                    "fecha_inicio": "2026-05-01",
                    "fecha_fin": "2026-05-15",
                    "status": "success"
                }

                # Consulta agregada válida que intentará ir a Gemini pero sufrirá fallback
                res = procesar_pregunta(
                    "Resumen de ingresos del 2026-05-01 al 2026-05-15",
                    rol="SuperAdmin"
                )

                self.assertEqual(res["status"], "success")
                # Origen fue el motor local debido al fallback
                self.assertEqual(res["origen"], "consulta_segura")
                self.assertIn("250", res["mensaje"])

    def test_33_gemini_no_disponible_si_modelo_esta_vacio(self):
        """Si LLM_MODELO está vacío, esta_disponible es False aunque LLM_ACTIVADO=true y haya API_KEY."""
        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyFakeKeyForTestingOnly",
            "LLM_MODELO": ""
        }
        with patch.dict(os.environ, env_vars, clear=True):
            cfg = ConfiguracionLLM()
            self.assertTrue(cfg.activado)
            self.assertFalse(cfg.esta_disponible)

    @patch("requests.post")
    def test_34_gemini_consulta_agregada_texto_rango_fechas_espanol_invoca_tool_segura(self, mock_post):
        """Caso clave: Consulta analítica con fechas en español resuelve mediante tool segura sin SQL."""
        env_vars = {
            "LLM_PROVEEDOR": "gemini",
            "LLM_ACTIVADO": "true",
            "LLM_API_KEY": "AIzaSyFakeKeyForTestingOnly",
            "LLM_MODELO": "gemini-2.0-flash",
            "LLM_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai"
        }
        with patch.dict(os.environ, env_vars, clear=True):
            # 1. Simulación respuesta de Gemini solicitando tool call para obtener_resumen_ingresos
            resp_tool_call = MagicMock()
            resp_tool_call.status_code = 200
            resp_tool_call.json.return_value = {
                "id": "chatcmpl-gemini-34-1",
                "object": "chat.completion",
                "model": "gemini-2.0-flash",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_gemini_resumen",
                            "type": "function",
                            "function": {
                                "name": "obtener_resumen_ingresos",
                                "arguments": json.dumps({"fecha_inicio": "2026-06-01", "fecha_fin": "2026-07-31"})
                            }
                        }]
                    },
                    "finish_reason": "tool_calls"
                }],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
            }

            # 2. Simulación respuesta de síntesis de Gemini tras recibir datos de la herramienta segura
            resp_sintesis = MagicMock()
            resp_sintesis.status_code = 200
            resp_sintesis.json.return_value = {
                "id": "chatcmpl-gemini-34-2",
                "object": "chat.completion",
                "model": "gemini-2.0-flash",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Entre el 1 de junio y el 31 de julio de 2026 se registraron un total de 18,168 ingresos de alumnos en la biblioteca."
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120}
            }

            mock_post.side_effect = [resp_tool_call, resp_sintesis]

            with patch("llm.services.proveedor_llm.obtener_resumen_ingresos") as mock_resumen:
                mock_resumen.return_value = {
                    "status": "success",
                    "fecha_inicio": "2026-06-01",
                    "fecha_fin": "2026-07-31",
                    "total_ingresos": 18168,
                    "ingresos_por_tipo_persona": {"Alumno": 18168}
                }

                from llm.services.coordinador_asistente import procesar_pregunta
                resultado = procesar_pregunta(
                    "¿Cuántos ingresos de alumnos hubo entre el 1 de junio y el 31 de julio de 2026?",
                    rol="SuperAdmin"
                )

                self.assertEqual(resultado["status"], "success")
                self.assertEqual(resultado["origen"], "llm_externo")
                self.assertIn("18,168", resultado["mensaje"])
                mock_resumen.assert_called_once_with(
                    fecha_inicio="2026-06-01",
                    fecha_fin="2026-07-31",
                    rol="SuperAdmin",
                    sede_permitida=None
                )

                # Confirmar que en los mensajes enviados no hay generación de SQL
                prompt_enviado = mock_post.call_args_list[0][1]["json"]["messages"][1]["content"]
                self.assertIn("fecha_inicio=2026-06-01", prompt_enviado)
                self.assertIn("fecha_fin=2026-07-31", prompt_enviado)

    @patch("requests.post")
    def test_35_coherencia_tokens_dos_llamadas_herramienta_caso_render(self, mock_post):
        """Valida que en 2 llamadas con herramienta (caso Render) se conserven prompt y completion, registrando tokens_sin_desglose."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeRenderTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        # Llamada 1: Tool call de Gemini (completion_tokens=0 en capa compatible, total=650, prompt=550)
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_rend_tools",
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

        # Llamada 2: Síntesis de Gemini (completion_tokens=49, total=650, prompt=587)
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

            tok = res["metricas"]["tokens"]
            self.assertTrue(tok["disponible"])
            self.assertEqual(tok["llamadas_api"], 2)
            self.assertEqual(tok["prompt_tokens"], 1137)
            self.assertEqual(tok["completion_tokens"], 49)
            self.assertEqual(tok["tokens_sin_desglose"], 114)
            self.assertEqual(tok["total_tokens"], 1300)
            self.assertEqual(tok["prompt_tokens"] + tok["completion_tokens"] + tok["tokens_sin_desglose"], tok["total_tokens"])

    @patch("requests.post")
    def test_36_coherencia_tokens_llamada_directa_con_pensamiento(self, mock_post):
        """Valida que en llamada directa sin herramientas se registre tokens_sin_desglose cuando total excede prompt + completion."""
        cfg = ConfiguracionLLM()
        cfg.proveedor = "gemini"
        cfg.activado = True
        cfg.api_key = "AIzaSyFakeDirectTestKey"
        cfg.modelo = "gemini-2.0-flash"
        proveedor = ProveedorLLM(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "La biblioteca atiende de 08:00 a 20:00."
                }
            }],
            "usage": {
                "prompt_tokens": 400,
                "completion_tokens": 35,
                "total_tokens": 485  # 50 tokens sin desglose
            }
        }
        mock_post.return_value = mock_resp

        res = proveedor.procesar_consulta("¿Cuál es el horario de atención?", rol="SuperAdmin")
        tok = res["metricas"]["tokens"]

        self.assertTrue(tok["disponible"])
        self.assertEqual(tok["llamadas_api"], 1)
        self.assertEqual(tok["prompt_tokens"], 400)
        self.assertEqual(tok["completion_tokens"], 35)
        self.assertEqual(tok["tokens_sin_desglose"], 50)
        self.assertEqual(tok["total_tokens"], 485)
        self.assertEqual(tok["prompt_tokens"] + tok["completion_tokens"] + tok["tokens_sin_desglose"], tok["total_tokens"])


if __name__ == "__main__":
    unittest.main()
