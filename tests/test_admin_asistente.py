import os
import unittest
from unittest.mock import patch
import json
from app import app


class TestAdminAsistente(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self._env_patch = patch.dict(os.environ, {"LLM_ACTIVADO": "false"})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def _obtener_cliente_con_sesion(self, user="admin", rol="SuperAdmin", sede="Central"):
        with self.client.session_transaction() as sess:
            sess["admin_user"] = user
            sess["admin_rol"] = rol
            sess["admin_sede"] = sede
            sess["csrf_token"] = "token_csrf_de_prueba_12345"
        return self.client

    # 1. Sin sesión redirige a login
    def test_01_sin_sesion_redirige_a_login(self):
        res = self.client.get("/admin/asistente")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.location)

    # 2. SuperAdmin accede a vista
    def test_02_superadmin_accede_vista(self):
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res = self.client.get("/admin/asistente")
        self.assertEqual(res.status_code, 200)
        contenido = res.get_data(as_text=True)
        self.assertIn("Asistente inteligente de biblioteca", contenido)
        self.assertIn("Asistente inteligente — modo local", contenido)

    # 3. Supervisor accede a vista
    def test_03_supervisor_accede_vista(self):
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Tarma")
        res = self.client.get("/admin/asistente")
        self.assertEqual(res.status_code, 200)
        contenido = res.get_data(as_text=True)
        self.assertIn("Asistente inteligente — modo local", contenido)

    # 4. Consultor accede a vista
    def test_04_consultor_accede_vista(self):
        self._obtener_cliente_con_sesion(rol="Consultor")
        res = self.client.get("/admin/asistente")
        self.assertEqual(res.status_code, 200)
        contenido = res.get_data(as_text=True)
        self.assertIn("Asistente inteligente — modo local", contenido)

    # 5. POST sin CSRF devuelve 403 y no llama al coordinador
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_05_post_sin_csrf_devuelve_403(self, mock_procesar):
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        self.assertEqual(res.status_code, 403)
        datos = res.get_json()
        self.assertEqual(datos["status"], "error")
        self.assertIn("CSRF", datos["mensaje"])
        mock_procesar.assert_not_called()

    # 6. POST con CSRF inválido devuelve 403 y no llama al coordinador
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_06_post_con_csrf_invalido_devuelve_403(self, mock_procesar):
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_invalido_xyz"},
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        self.assertEqual(res.status_code, 403)
        datos = res.get_json()
        self.assertEqual(datos["status"], "error")
        mock_procesar.assert_not_called()

    # 7. POST con CSRF válido funciona
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_07_post_con_csrf_valido_funciona(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "success",
            "intencion": "resumen_ingresos",
            "mensaje": "Hoy se registraron 15 ingresos.",
            "sede": "Central"
        }
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["mensaje"], "Hoy se registraron 15 ingresos.")

    # 8. Solo acepta JSON y rechaza variaciones malformadas/no-dict con 400
    def test_08_solo_acepta_json_y_valida_variaciones(self):
        self._obtener_cliente_con_sesion()

        # Content-Type form urlencoded
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            data="pregunta=cuantos+ingresos",
            content_type="application/x-www-form-urlencoded"
        )
        self.assertEqual(res.status_code, 400)

        # Content-Type text/plain
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            data='{"pregunta": "hola"}',
            content_type="text/plain"
        )
        self.assertEqual(res.status_code, 400)

        # JSON array []
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json=[]
        )
        self.assertEqual(res.status_code, 400)

        # JSON null
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json=None
        )
        self.assertEqual(res.status_code, 400)

        # JSON string "texto"
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json="texto"
        )
        self.assertEqual(res.status_code, 400)

        # JSON number 123
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json=123
        )
        self.assertEqual(res.status_code, 400)

    # 9. Pregunta ausente, null, no-string, vacía o mayor de 500 devuelve 400
    def test_09_pregunta_invalidas_devuelven_400(self):
        self._obtener_cliente_con_sesion()

        # Objeto sin pregunta
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={}
        )
        self.assertEqual(res.status_code, 400)

        # Pregunta null
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": None}
        )
        self.assertEqual(res.status_code, 400)

        # Pregunta número 123
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": 123}
        )
        self.assertEqual(res.status_code, 400)

        # Pregunta vacía
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "   "}
        )
        self.assertEqual(res.status_code, 400)

        # Pregunta > 500 caracteres
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "a" * 501}
        )
        self.assertEqual(res.status_code, 400)

    # 10. Rol cliente es ignorado
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_10_rol_cliente_es_ignorado(self, mock_procesar):
        mock_procesar.return_value = {"status": "success", "mensaje": "OK"}
        self._obtener_cliente_con_sesion(rol="Consultor")

        self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "¿Cuántos ingresos hubo hoy?",
                "rol": "SuperAdmin"
            }
        )
        mock_procesar.assert_called_once()
        kwargs = mock_procesar.call_args[1]
        self.assertEqual(kwargs["rol"], "Consultor")

    # 11. sede_permitida cliente es ignorada
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_11_sede_permitida_cliente_es_ignorada(self, mock_procesar):
        mock_procesar.return_value = {"status": "success", "mensaje": "OK"}
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Tarma")

        self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "¿Cuántos ingresos hubo hoy?",
                "sede_permitida": "Central"
            }
        )
        mock_procesar.assert_called_once()
        kwargs = mock_procesar.call_args[1]
        self.assertEqual(kwargs["sede_permitida"], "Tarma")

    # 12. Supervisor utiliza su sede de sesión
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_12_supervisor_utiliza_su_sede_de_sesion(self, mock_procesar):
        mock_procesar.return_value = {"status": "success", "mensaje": "OK"}
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Oxapampa")

        self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        kwargs = mock_procesar.call_args[1]
        self.assertEqual(kwargs["rol"], "Supervisor")
        self.assertEqual(kwargs["sede_permitida"], "Oxapampa")

    # 13. Consultor bloqueado en consulta personal
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_13_consultor_bloqueado_en_consulta_personal(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "error",
            "categoria_error": "permiso",
            "codigo_http": 403,
            "mensaje": "Acceso denegado. El rol Consultor no tiene permiso para consultar historiales personales."
        }
        self._obtener_cliente_con_sesion(rol="Consultor")

        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "historial de Juan Perez"}
        )
        self.assertEqual(res.status_code, 403)
        datos = res.get_json()
        self.assertEqual(datos["status"], "error")
        self.assertEqual(datos["categoria_error"], "permiso")

    # 14. requires_input devuelve respuesta controlada (200 OK)
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_14_requires_input_devuelve_respuesta_controlada(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "requires_input",
            "requires_input": True,
            "mensaje": "Se encontraron 2 personas con el nombre 'Quispe'. Por favor seleccione una:",
            "opciones": [
                {"persona_id": 10, "nombre": "Juan Quispe", "tipo_persona": "Alumno", "codigo_mascarado": "20***1"},
                {"persona_id": 20, "nombre": "Maria Quispe", "tipo_persona": "Docente", "codigo_mascarado": "10***5"}
            ]
        }
        self._obtener_cliente_con_sesion(rol="SuperAdmin")

        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "historial de Quispe"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "requires_input")
        self.assertTrue(datos["requires_input"])

    # 15. Selección de persona se reenvía correctamente
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_15_seleccion_persona_se_reenvia_correctamente(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "success",
            "mensaje": "Historial de Juan Quispe: 3 ingresos registrados."
        }
        self._obtener_cliente_con_sesion(rol="SuperAdmin")

        seleccion = {"persona_id": 10, "tipo_persona": "Alumno"}
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "historial de Quispe",
                "seleccion_persona": seleccion
            }
        )
        self.assertEqual(res.status_code, 200)
        kwargs = mock_procesar.call_args[1]
        self.assertEqual(kwargs["seleccion_persona"], seleccion)

    # 16. Error técnico devuelve respuesta genérica sin SQL
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_16_error_tecnico_devuelve_respuesta_generica_sin_sql(self, mock_procesar):
        mock_procesar.side_effect = Exception("pyodbc.Error: (208) Invalid object name 'dbo.TablaInexistente'")
        self._obtener_cliente_con_sesion()

        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        self.assertEqual(res.status_code, 503)
        datos = res.get_json()
        self.assertEqual(datos["status"], "error")
        self.assertEqual(datos["categoria_error"], "servicio")
        self.assertNotIn("dbo.TablaInexistente", datos["mensaje"])

    # 17. Ruta no expone traceback para ConnectionError, TimeoutError o Exception
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_17_ruta_no_expone_traceback_connection_timeout(self, mock_procesar):
        self._obtener_cliente_con_sesion()

        for exc in [ConnectionError("BD Caida"), TimeoutError("Timeout BD"), RuntimeError("Crash")]:
            mock_procesar.side_effect = exc
            res = self.client.post(
                "/admin/api/asistente/consultar",
                headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
                json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
            )
            self.assertEqual(res.status_code, 503)
            datos = res.get_json()
            self.assertEqual(datos["status"], "error")
            self.assertEqual(datos["categoria_error"], "servicio")
            self.assertNotIn("Traceback", res.get_data(as_text=True))

    # 18. Blueprint registrado
    def test_18_blueprint_registrado(self):
        self.assertIn("admin_asistente", self.app.blueprints)
        rutas_asistente = [str(r) for r in self.app.url_map.iter_rules() if "asistente" in str(r)]
        self.assertIn("/admin/asistente", rutas_asistente)
        self.assertIn("/admin/api/asistente/consultar", rutas_asistente)

    # 19. Plantilla Jinja válida
    def test_19_plantilla_jinja_valida(self):
        tmpl = self.app.jinja_env.get_template("admin_asistente.html")
        self.assertIsNotNone(tmpl)

    # 20. Sidebar contiene enlace
    def test_20_sidebar_contiene_enlace(self):
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res = self.client.get("/admin/asistente")
        contenido = res.get_data(as_text=True)
        self.assertIn('/admin/asistente', contenido)
        self.assertIn('Asistente inteligente', contenido)

    # 21. Respuesta no contiene DNI, correo ni teléfono
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_21_respuesta_no_contiene_dni_correo_ni_telefono(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "success",
            "mensaje": "Se encontraron 3 ingresos de Juan Perez en la sede Central.",
            "registros": [{"fecha": "2026-03-10 10:00:00", "sede": "Central"}]
        }
        self._obtener_cliente_con_sesion()

        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "historial de Juan Perez"}
        )
        datos_str = json.dumps(res.get_json())
        self.assertNotIn("dni", datos_str.lower())
        self.assertNotIn("correo", datos_str.lower())
        self.assertNotIn("telefono", datos_str.lower())

    # 22. El HTML no usa localStorage, sessionStorage ni eval
    def test_22_el_html_no_usa_localstorage_sessionstorage_ni_eval(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("eval(", html)
        self.assertNotIn("new Function", html)

    # 23. Auditoria XSS: Datos dinámicos se presentan con textContent y sin métodos peligrosos
    def test_23_auditoria_xss_estatica_plantilla(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("textContent", html)
        self.assertIn("addEventListener", html)

        # Prohibición de métodos/atributos XSS peligrosos
        self.assertNotIn("insertAdjacentHTML", html)
        self.assertNotIn("document.write", html)
        self.assertNotIn("onclick=", html)
        self.assertNotIn("innerHTML =", html)

    # 24. No hay llamadas a proveedor externo ni clasificación por texto en rutas
    def test_24_no_hay_llamadas_a_proveedor_externo_ni_busqueda_texto(self):
        with open("routes/admin_asistente.py", "r", encoding="utf-8") as f:
            codigo_route = f.read()
        with open("llm/services/coordinador_asistente.py", "r", encoding="utf-8") as f:
            codigo_coord = f.read()

        for term in ["openai", "generativeai", "groq", "anthropic", "requests.post"]:
            self.assertNotIn(term, codigo_route.lower())
            self.assertNotIn(term, codigo_coord.lower())

        # Confirmar ausencia de clasificación HTTP basada en búsqueda por palabras
        self.assertNotIn('"permiso" in mensaje', codigo_route)
        self.assertNotIn('"acceso" in mensaje', codigo_route)
        self.assertNotIn('"base de datos" in mensaje', codigo_route)

    # 25. Las rutas ML anteriores continúan registradas
    def test_25_rutas_ml_anteriores_continuan_registradas(self):
        rutas = [str(r) for r in self.app.url_map.iter_rules()]
        self.assertIn("/admin/ml", rutas)
        self.assertIn("/admin/api/ml/informacion", rutas)
        self.assertIn("/admin/api/ml/historial-afluencia", rutas)
        self.assertIn("/admin/api/ml/predecir-afluencia", rutas)

    # 26. Sesión con rol no válido o ausente es rechazada antes de llamar al coordinador
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_26_rol_sesion_invalido_o_ausente_rechazado(self, mock_procesar):
        self._obtener_cliente_con_sesion(rol=None)
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuántos ingresos hubo hoy?"}
        )
        self.assertEqual(res.status_code, 403)
        mock_procesar.assert_not_called()

    # 27. Opciones requires_input para parámetros faltantes e intención desconocida usan HTTP 200
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_27_requires_input_intencion_desconocida_y_parametros_usa_200(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "requires_input",
            "intencion": "intencion_desconocida",
            "sede_aplicada": "Todas las sedes",
            "mensaje": "No se reconoció la intención.",
            "opciones": ["Ejemplo 1", "Ejemplo 2"]
        }
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "algo raro"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "requires_input")

    # 28. J.1 y J.2: intencion_desconocida no muestra candidatos y opciones de texto no se convierten en "Sin nombre"
    def test_28_intencion_desconocida_no_muestra_candidatos_ni_sin_nombre(self):
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "Hola, ¿qué puedes hacer?"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["intencion"], "intencion_desconocida")
        self.assertFalse(datos.get("requiere_aclaracion", False))
        self.assertIsInstance(datos.get("opciones"), list)
        # Las opciones son strings de ejemplos, no objetos de personas
        for opt in datos["opciones"]:
            self.assertIsInstance(opt, str)

        # En la plantilla HTML se comprueba la función de validación de candidatos
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("esListaDeCandidatosValida", html)
        self.assertIn('"persona_id" in opt', html)
        self.assertIn('"nombre" in opt', html)
        self.assertIn('"tipo_persona" in opt', html)
        self.assertIn("CONSULTA NO RECONOCIDA", html)

    # 29. J.3: panel de candidatos se limpia en la siguiente consulta y con nueva consulta
    def test_29_limpiar_resultado_anterior_usa_replace_children(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("function limpiarResultadoAnterior()", html)
        self.assertIn("replaceChildren()", html)
        self.assertIn("contenedor-candidatos", html)
        self.assertIn("panel-desambiguacion", html)

        # Confirmar invocación en las 3 fases requeridas:
        # 1. Antes de enviar nueva consulta en ejecutarConsulta
        self.assertIn("limpiarResultadoAnterior();", html)
        # 2. Al recibir cualquier nueva respuesta en procesarRespuestaExitoso y mostrarEstadoError
        self.assertIn("procesarRespuestaExitoso", html)
        # 3. Al presionar 'Nueva consulta'
        self.assertIn("btnNuevaConsulta.addEventListener", html)

    # 30. J.4: requiere_parametros no se muestra como desambiguación
    @patch("routes.admin_asistente.procesar_pregunta")
    def test_30_requiere_parametros_no_se_muestra_como_desambiguacion(self, mock_procesar):
        mock_procesar.return_value = {
            "status": "requires_input",
            "intencion": "resumen_ingresos",
            "sede_aplicada": "Todas las sedes",
            "mensaje": "Para consultar el resumen de ingresos debe especificar fechas.",
            "datos": {},
            "requiere_aclaracion": False,
            "requiere_parametros": True,
            "parametros_faltantes": ["fecha_inicio", "fecha_fin"],
            "opciones": []
        }
        self._obtener_cliente_con_sesion()
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertTrue(datos["requiere_parametros"])
        self.assertFalse(datos["requiere_aclaracion"])
        self.assertEqual(datos["parametros_faltantes"], ["fecha_inicio", "fecha_fin"])

        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("renderizarParametrosFaltantes", html)
        self.assertIn("Parámetros requeridos", html)
        self.assertIn("PARÁMETROS REQUERIDOS", html)

    # 31. J.5: horas_top se renderiza con hora_formato, total_ingresos y promedio_diario
    def test_31_horas_top_se_renderiza(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("renderizarHorasAfluencia", html)
        self.assertIn("hora_formato", html)
        self.assertIn("total_ingresos", html)
        self.assertIn("promedio_diario", html)
        self.assertIn("Promedio por día con registros", html)
        self.assertIn("Días con registros:", html)
        self.assertIn("No existen ingresos registrados en el periodo seleccionado.", html)

    # 32. J.6: resumen agregado se renderiza con campos requeridos
    def test_32_resumen_agregado_se_renderiza(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("renderizarResumenIngresos", html)
        self.assertIn("Total Ingresos", html)
        self.assertIn("Fecha Inicio", html)
        self.assertIn("Fecha Fin", html)
        self.assertIn("Días del Rango", html)
        self.assertIn("Ingresos por Sede", html)
        self.assertIn("Ingresos por Tipo de Persona", html)

    # 33. J.7: predicción y probabilidades se renderizan con advertencia exacta
    def test_33_prediccion_y_probabilidades_se_renderizan(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("renderizarPrediccion", html)
        self.assertIn("Nivel Estimado", html)
        self.assertIn("Bajo", html)
        self.assertIn("Medio", html)
        self.assertIn("Alto", html)
        self.assertIn("mostrarAdvertenciaML", html)
        self.assertIn("panel-advertencia-ml", html)

    # 34. J.8: historial personal se renderiza sin persona_id
    def test_34_historial_personal_se_renderiza_sin_persona_id(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("renderizarHistorialPersona", html)
        self.assertIn("Nombre", html)
        self.assertIn("Tipo de Persona", html)
        self.assertIn("Código Enmascarado", html)
        self.assertIn("Total Ingresos", html)
        self.assertIn("Primer ingreso:", html)
        self.assertIn("Último ingreso:", html)

        # Confirmar ausencia de persona_id, DNI, correo y teléfono en renderizado
        bloque_historial = html[html.find("function renderizarHistorialPersona"):html.find("function mostrarAdvertenciaML")]
        self.assertNotIn("persona_id", bloque_historial)
        self.assertNotIn("dni", bloque_historial.lower())
        self.assertNotIn("correo", bloque_historial.lower())
        self.assertNotIn("telefono", bloque_historial.lower())

    # 35. J.9: sede_aplicada se devuelve correctamente según rol
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_35_sede_aplicada_devuelta_correctamente_segun_rol(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-03-01",
            "fecha_fin": "2026-03-10",
            "dias_rango": 10,
            "total_ingresos": 100,
            "ingresos_por_sede": {"Central": 100},
            "ingresos_por_tipo_persona": {}
        }

        # 1. SuperAdmin con sede seleccionada
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res1 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos", "fecha_inicio": "2026-03-01", "fecha_fin": "2026-03-10", "sede": "Central"}
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json()["sede_aplicada"], "Central")

        # 2. SuperAdmin sin sede seleccionada -> 'Todas las sedes'
        res2 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos", "fecha_inicio": "2026-03-01", "fecha_fin": "2026-03-10"}
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["sede_aplicada"], "Todas las sedes")

        # 3. Supervisor -> siempre su sede de sesion
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Tarma")
        res3 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos", "fecha_inicio": "2026-03-01", "fecha_fin": "2026-03-10"}
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.get_json()["sede_aplicada"], "Tarma")

        # 4. Consultor con sede seleccionada
        self._obtener_cliente_con_sesion(rol="Consultor")
        res4 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos", "fecha_inicio": "2026-03-01", "fecha_fin": "2026-03-10", "sede": "Oxapampa"}
        )
        self.assertEqual(res4.status_code, 200)
        self.assertEqual(res4.get_json()["sede_aplicada"], "Oxapampa")

        # 5. Consultor sin sede seleccionada -> 'Todas las sedes'
        res5 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "resumen de ingresos", "fecha_inicio": "2026-03-01", "fecha_fin": "2026-03-10"}
        )
        self.assertEqual(res5.status_code, 200)
        self.assertEqual(res5.get_json()["sede_aplicada"], "Todas las sedes")

    # 36. J.10: Supervisor no puede cambiar sede_aplicada
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_36_supervisor_no_puede_cambiar_sede_aplicada(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-03-01",
            "fecha_fin": "2026-03-10",
            "total_ingresos": 20
        }
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Oxapampa")

        # Intento de Supervisor enviando sede 'Central' y sede_permitida 'Central' en el payload
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "resumen de ingresos",
                "fecha_inicio": "2026-03-01",
                "fecha_fin": "2026-03-10",
                "sede": "Central",
                "sede_permitida": "Central"
            }
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["sede_aplicada"], "Oxapampa")
        # Verificar que a la consulta interna se le pasó Oxapampa
        mock_resumen.assert_called_once_with(
            fecha_inicio="2026-03-01",
            fecha_fin="2026-03-10",
            rol="Supervisor",
            sede_permitida="Oxapampa"
        )

    # 37. J.11: XSS continúa mostrándose solo como texto y texto aclaratorio actualizado
    def test_37_xss_y_texto_aclaratorio_actualizado(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        # Texto aclaratorio en parámetros complementarios
        self.assertIn("Parámetros complementarios (opcional)", html)
        self.assertIn("Complete solamente los parámetros necesarios para el tipo de consulta.", html)

        # textContent y createElement en todos los renderizadores
        self.assertIn("document.createElement", html)
        self.assertIn("textContent", html)

    # 38. J.12: Auditoría estricta contra innerHTML, insertAdjacentHTML, document.write, eval, new Function
    def test_38_auditoria_estricta_sin_innerhtml_ni_eval(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        prohibidos = [
            "innerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
            "new Function"
        ]
        for p in prohibidos:
            self.assertNotIn(p, html, f"Se encontró método prohibido '{p}' en admin_asistente.html")

    # 39. Badges de estado requeridos
    def test_39_badges_de_estado_segun_reglas(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()

        badges_requeridos = [
            "CONSULTA COMPLETADA",
            "SELECCIÓN REQUERIDA",
            "PARÁMETROS REQUERIDOS",
            "CONSULTA NO RECONOCIDA",
            "NO SE PUDO COMPLETAR"
        ]
        for b in badges_requeridos:
            self.assertIn(b, html, f"Badge requerido '{b}' no encontrado en plantilla")

    # 40. Ejemplo visible de predicción utiliza "¿Cuál será el nivel de afluencia?"
    def test_40_ejemplo_prediccion_usa_cual_sera_el_nivel_de_afluencia(self):
        with open("templates/admin_asistente.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("¿Cuál será el nivel de afluencia?", html)

    # 41. Endpoint reconoce "¿Cuál será el nivel de afluencia?" como predicción y solicita parámetros faltantes
    def test_41_cual_sera_el_nivel_de_afluencia_retorna_requires_input_con_parametros_faltantes(self):
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuál será el nivel de afluencia?"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "requires_input")
        self.assertEqual(datos["intencion"], "prediccion_afluencia")
        self.assertTrue(datos["requiere_parametros"])
        self.assertFalse(datos["requiere_aclaracion"])
        self.assertIn("fecha", datos["parametros_faltantes"])
        self.assertIn("hora", datos["parametros_faltantes"])
        self.assertIn("sede", datos["parametros_faltantes"])

    # 42. Seguimiento en sesión: después de consultar resumen histórico, "¿Y ayer?" conserva intención
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_42_seguimiento_en_sesion_aplica_ayer_y_conserva_intencion(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "total_ingresos": 120
        }
        self._obtener_cliente_con_sesion(rol="SuperAdmin")

        # Turno 1: Consulta de resumen histórico
        res1 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "Resumen de ingresos del 2026-09-01 al 2026-09-30"
            }
        )
        self.assertEqual(res1.status_code, 200)
        datos1 = res1.get_json()
        self.assertEqual(datos1["status"], "success")
        self.assertEqual(datos1["intencion"], "resumen_ingresos")
        self.assertIn("contexto", datos1)
        self.assertEqual(datos1["contexto"]["intencion"], "resumen_ingresos")

        # Turno 2: Seguimiento "¿Y ayer?" en la misma sesión
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-10-10",
            "fecha_fin": "2026-10-10",
            "total_ingresos": 35
        }
        res2 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "¿Y ayer?"
            }
        )
        self.assertEqual(res2.status_code, 200)
        datos2 = res2.get_json()
        self.assertEqual(datos2["status"], "success")
        self.assertEqual(datos2["intencion"], "resumen_ingresos")
        self.assertIn("35 ingresos", datos2["mensaje"])
        self.assertEqual(datos2["contexto"]["intencion"], "resumen_ingresos")

    # 43. Seguimiento sin contexto previo en sesión solicita aclaración
    def test_43_seguimiento_sin_contexto_previo_retorna_requires_input(self):
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "¿Y ayer?"
            }
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "requires_input")
        self.assertEqual(datos["intencion"], "intencion_desconocida")
        self.assertTrue(datos["requiere_aclaracion"])
        self.assertIn("No se encontró una consulta previa", datos["mensaje"])
        self.assertTrue(len(datos["opciones"]) > 0)

    # 44. Seguimiento ambiguo en sesión solicita aclaración
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_44_seguimiento_ambiguo_retorna_requires_input(self, mock_resumen):
        mock_resumen.return_value = {"status": "success", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30", "total_ingresos": 50}
        self._obtener_cliente_con_sesion(rol="SuperAdmin")

        # Turno 1: Establecer contexto
        self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "Resumen de ingresos del 2026-09-01 al 2026-09-30"}
        )

        # Turno 2: Pregunta ambigua
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Y entonces?"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "requires_input")
        self.assertTrue(datos["requiere_aclaracion"])
        self.assertIn("ambigua", datos["mensaje"].lower())

    # 45. Supervisor: aislamiento estricto de sede en seguimiento a través de sesión
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_45_supervisor_aislamiento_de_sede_en_seguimiento_en_sesion(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "total_ingresos": 10
        }
        # Supervisor con sede Tarma
        self._obtener_cliente_con_sesion(rol="Supervisor", sede="Tarma")

        # Turno 1
        res1 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "Resumen de ingresos del 2026-09-01 al 2026-09-30"}
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json()["sede_aplicada"], "Tarma")

        # Turno 2: Intenta forzar otra sede en la pregunta y en el contexto
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-10-10",
            "fecha_fin": "2026-10-10",
            "total_ingresos": 8
        }
        res2 = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={
                "pregunta": "¿Y ayer en Central?",
                "sede": "Central",
                "sede_permitida": "Central",
                "contexto": {"sede": "Central"}
            }
        )
        self.assertEqual(res2.status_code, 200)
        datos2 = res2.get_json()
        # Sede aplicada DEBE ser estrictamente 'Tarma'
        self.assertEqual(datos2["sede_aplicada"], "Tarma")
        self.assertEqual(datos2["contexto"]["sede"], "Tarma")
        # Verificar que a obtener_resumen_ingresos se le pasó Tarma
        mock_resumen.assert_called_with(
            fecha_inicio=unittest.mock.ANY,
            fecha_fin=unittest.mock.ANY,
            rol="Supervisor",
            sede_permitida="Tarma"
        )

    # 46. Privacidad: contexto en sesión no almacena datos sensibles
    @patch("llm.services.coordinador_asistente.buscar_personas")
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    def test_46_contexto_sesion_no_expone_datos_sensibles(self, mock_hist, mock_busq):
        mock_busq.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 101, "nombre": "Renzo Rojas Castillo", "codigo_mascarado": "20******56"}
        ]
        mock_hist.return_value = {
            "status": "success",
            "total_ingresos": 3,
            "registros": [{"registro_id": 1, "fecha_hora": "2026-10-01T10:00:00", "sede": "Central"}]
        }
        self._obtener_cliente_con_sesion(rol="SuperAdmin")
        res = self.client.post(
            "/admin/api/asistente/consultar",
            headers={"X-CSRFToken": "token_csrf_de_prueba_12345"},
            json={"pregunta": "¿Cuántas veces ingresó Renzo Rojas Castillo?"}
        )
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        ctx = datos.get("contexto", {})

        campos_prohibidos = [
            "nombre", "texto_persona", "dni", "persona_id", "id", "persona",
            "candidato", "opciones", "historial", "registros", "ultimo_registro",
            "respuesta", "mensaje", "datos"
        ]
        for c in campos_prohibidos:
            self.assertNotIn(c, ctx, f"Campo prohibido '{c}' encontrado en el contexto de respuesta")

        with self.client.session_transaction() as sess:
            ctx_sess = sess.get("asistente_contexto", {})
            for c in campos_prohibidos:
                self.assertNotIn(c, ctx_sess, f"Campo prohibido '{c}' encontrado en asistente_contexto de sesión")


if __name__ == "__main__":
    unittest.main()
