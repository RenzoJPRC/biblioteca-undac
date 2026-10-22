import unittest
from unittest.mock import patch, MagicMock
from app import app
from ml.services.predictor_afluencia import obtener_predictor_afluencia


class TestModuloMachineLearning(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        from ml.services.datos_afluencia import limpiar_cache_refresco_memoria
        limpiar_cache_refresco_memoria()

    def tearDown(self):
        from ml.services.datos_afluencia import limpiar_cache_refresco_memoria
        limpiar_cache_refresco_memoria()

    def test_01_usuario_sin_sesion_redirigido(self):
        """Usuario sin sesión accediendo a /admin/ml debe ser redirigido a login (302)."""
        respuesta = self.client.get("/admin/ml")
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("/admin/login", respuesta.headers["Location"])

    def test_02_superadmin_acceso_pantalla(self):
        """SuperAdmin debe poder acceder a /admin/ml (200 OK)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_prueba_123"

        respuesta = self.client.get("/admin/ml")
        self.assertEqual(respuesta.status_code, 200)

    def test_03_supervisor_acceso_pantalla(self):
        """Supervisor debe poder acceder a /admin/ml (200 OK)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "super_test"
            sesion["admin_rol"] = "Supervisor"
            sesion["admin_sede"] = "Tarma"
            sesion["csrf_token"] = "token_prueba_123"

        respuesta = self.client.get("/admin/ml")
        self.assertEqual(respuesta.status_code, 200)

    @patch("routes.admin_ml.predecir_afluencia_desde_bd")
    def test_04_supervisor_sede_forzada(self, mock_predecir):
        """Supervisor de Central enviando 'Tarma' en JSON debe ser forzado a su sede asignada 'Central'."""
        mock_predecir.side_effect = lambda fecha, hora, sede: {
            "status": "success",
            "prediccion": {
                "nivel": "Medio",
                "probabilidades": {"Alto": 0.1, "Bajo": 0.2, "Medio": 0.7},
                "version_modelo": "1.0.0"
            },
            "consulta": {
                "fecha": str(fecha),
                "hora": hora,
                "sede": sede
            },
            "variables_modelo": {},
            "advertencia": "Modelo de apoyo."
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "super_central"
            sesion["admin_rol"] = "Supervisor"
            sesion["admin_sede"] = "Central"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Tarma"}

        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )

        self.assertEqual(respuesta.status_code, 200)
        json_data = respuesta.get_json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(json_data["consulta"]["sede"], "Central")
        mock_predecir.assert_called_once()
        self.assertEqual(mock_predecir.call_args.kwargs["sede"], "Central")

    @patch("routes.admin_ml.predecir_afluencia_desde_bd")
    def test_05_consultor_acceso_solo_lectura(self, mock_predecir):
        """Consultor puede acceder a la vista GET y realizar consultas predictivas en la API."""
        mock_predecir.return_value = {
            "status": "success",
            "prediccion": {
                "nivel": "Medio",
                "probabilidades": {"Alto": 0.1, "Bajo": 0.2, "Medio": 0.7},
                "version_modelo": "1.0.0"
            },
            "consulta": {"fecha": "2026-09-29", "hora": 10, "sede": "Central"},
            "variables_modelo": {},
            "advertencia": "Modelo de apoyo."
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "consultor_test"
            sesion["admin_rol"] = "Consultor"
            sesion["csrf_token"] = "token_valido_999"

        respuesta_get = self.client.get("/admin/ml")
        self.assertEqual(respuesta_get.status_code, 200)

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}
        respuesta_post = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta_post.status_code, 200)

    def test_06_post_sin_csrf_rechazado(self):
        """Petición POST a la API ML sin cabecera CSRF debe ser rechazada (403 Forbidden)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.get_json()["mensaje"], "Token CSRF inválido o expirado.")

    def test_07_post_csrf_invalido_rechazado(self):
        """Petición POST con token CSRF incorrecto debe ser rechazada (403 Forbidden)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_falso_888"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.get_json()["mensaje"], "Token CSRF inválido o expirado.")

    @patch("routes.admin_ml.predecir_afluencia_desde_bd")
    def test_08_post_csrf_valido_exitoso(self, mock_predecir):
        """Petición POST con token CSRF válido debe procesarse correctamente (200 OK)."""
        mock_predecir.return_value = {
            "status": "success",
            "prediccion": {
                "nivel": "Medio",
                "probabilidades": {"Alto": 0.1, "Bajo": 0.2, "Medio": 0.7},
                "version_modelo": "1.0.0"
            },
            "consulta": {"fecha": "2026-09-29", "hora": 10, "sede": "Central"},
            "variables_modelo": {},
            "advertencia": "Modelo de apoyo."
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta.status_code, 200)

    def test_09_fecha_invalida(self):
        """Fecha con formato o fecha inválida debe retornar 400 Bad Request."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-13-99", "hora": 10, "sede": "Central"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_10_hora_fuera_de_rango(self):
        """Hora menor que 8 o mayor que 20 debe retornar 400 Bad Request."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 22, "sede": "Central"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta.status_code, 400)

    @patch("routes.admin_ml.predecir_afluencia_desde_bd")
    def test_11_sede_inexistente(self, mock_predecir):
        """Sede inexistente sin datos históricos debe retornar 400 Bad Request."""
        mock_predecir.side_effect = ValueError(
            "No existen datos históricos para la sede 'SedeInexistente' antes de 2026-09-29."
        )
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "SedeInexistente"}
        respuesta = self.client.post(
            "/admin/api/ml/predecir-afluencia",
            json=datos,
            headers=headers
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_12_error_conexion_bd(self):
        """Manejo controlado cuando no se puede conectar a la BD (503 Service Unavailable)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}

        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_db.side_effect = ConnectionError("Fallo simulado de conexión")
            respuesta = self.client.post(
                "/admin/api/ml/predecir-afluencia",
                json=datos,
                headers=headers
            )
            self.assertEqual(respuesta.status_code, 503)

    def test_13_historial_insuficiente(self):
        """Manejo controlado cuando hay menos de 5 fechas históricas (400 Bad Request)."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_valido_999"

        headers = {"X-CSRFToken": "token_valido_999"}
        datos = {"fecha": "2026-09-29", "hora": 10, "sede": "Central"}

        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            import pandas as pd
            # Simular historial con solo 2 filas
            mock_hist.return_value = pd.DataFrame([
                {"Fecha": "2026-09-01", "Hora": 10, "CantidadIngresos": 5},
                {"Fecha": "2026-09-02", "Hora": 10, "CantidadIngresos": 3}
            ])
            respuesta = self.client.post(
                "/admin/api/ml/predecir-afluencia",
                json=datos,
                headers=headers
            )
            self.assertEqual(respuesta.status_code, 400)

    def test_14_modelo_singleton_lru_cache(self):
        """Comprobar que el predictor se carga en memoria una sola vez vía lru_cache."""
        p1 = obtener_predictor_afluencia()
        p2 = obtener_predictor_afluencia()
        self.assertIs(p1, p2)

    def test_15_rutas_ml_registradas(self):
        """Comprobar que las 3 rutas del módulo ML se encuentran correctamente registradas en Flask."""
        rutas = [str(r) for r in self.app.url_map.iter_rules() if "ml" in str(r)]
        self.assertIn("/admin/ml", rutas)
        self.assertIn("/admin/api/ml/informacion", rutas)
        self.assertIn("/admin/api/ml/predecir-afluencia", rutas)

    def test_16_api_informacion_devuelve_umbrales(self):
        """Comprobar que GET /admin/api/ml/informacion devuelve los umbrales 7, 8, 35 y 36 y datos del modelo."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/informacion")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")

        modelo = datos["modelo"]
        self.assertIn("nombre", modelo)
        self.assertIn("algoritmo", modelo)
        self.assertIn("version", modelo)

        umbrales = modelo.get("umbrales", {})
        self.assertEqual(umbrales.get("bajo_maximo"), 7)
        self.assertEqual(umbrales.get("medio_minimo"), 8)
        self.assertEqual(umbrales.get("medio_maximo"), 35)
        self.assertEqual(umbrales.get("alto_minimo"), 36)

    def test_17_historial_sin_sesion_redirige(self):
        """Acceso a /admin/api/ml/historial-afluencia sin sesión debe redirigir a login (302)."""
        respuesta = self.client.get("/admin/api/ml/historial-afluencia")
        self.assertEqual(respuesta.status_code, 302)

    @patch("routes.admin_ml.obtener_historial_afluencia_periodo")
    def test_18_historial_superadmin_7_dias(self, mock_historial):
        """SuperAdmin consultando 7 días debe obtener status success, periodo 7 y referencia horaria."""
        mock_historial.return_value = {
            "status": "success",
            "sede": "Central",
            "periodo_dias": 7,
            "fecha_inicio": "2026-09-23",
            "fecha_fin": "2026-09-29",
            "fechas_con_datos": 5,
            "total_ingresos_periodo": 120,
            "horas": list(range(8, 21)),
            "totales": [10] * 13,
            "promedios": [2.0] * 13,
            "referencia_horaria": "Hora almacenada en la base de datos"
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=7")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["sede"], "Central")
        self.assertEqual(datos["periodo_dias"], 7)
        self.assertIn("referencia_horaria", datos)
        mock_historial.assert_called_once_with(sede="Central", dias=7)

    @patch("routes.admin_ml.obtener_historial_afluencia_periodo")
    def test_19_historial_superadmin_30_dias(self, mock_historial):
        """SuperAdmin consultando 30 días debe obtener status success y periodo 30."""
        mock_historial.return_value = {
            "status": "success",
            "sede": "Central",
            "periodo_dias": 30,
            "fecha_inicio": "2026-08-31",
            "fecha_fin": "2026-09-29",
            "fechas_con_datos": 15,
            "total_ingresos_periodo": 450,
            "horas": list(range(8, 21)),
            "totales": [35] * 13,
            "promedios": [2.3] * 13,
            "referencia_horaria": "Hora almacenada en la base de datos"
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["periodo_dias"], 30)
        mock_historial.assert_called_once_with(sede="Central", dias=30)

    def test_20_historial_dias_invalido_400(self):
        """Parámetro dias distinto de 7 o 30 debe retornar 400 Bad Request."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=15")
        self.assertEqual(respuesta.status_code, 400)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "error")

    @patch("routes.admin_ml.obtener_historial_afluencia_periodo")
    def test_21_historial_supervisor_sede_forzada(self, mock_historial):
        """Supervisor con sede 'Tarma' enviando 'Central' debe recibir respuesta para su sede 'Tarma'."""
        mock_historial.side_effect = lambda sede, dias: {
            "status": "success",
            "sede": sede,
            "periodo_dias": dias,
            "fecha_inicio": "2026-08-31",
            "fecha_fin": "2026-09-29",
            "fechas_con_datos": 10,
            "total_ingresos_periodo": 200,
            "horas": list(range(8, 21)),
            "totales": [15] * 13,
            "promedios": [1.5] * 13,
            "referencia_horaria": "Hora almacenada en la base de datos"
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "super_tarma"
            sesion["admin_rol"] = "Supervisor"
            sesion["admin_sede"] = "Tarma"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["sede"], "Tarma")
        mock_historial.assert_called_once_with(sede="Tarma", dias=30)

    def test_22_historial_sin_datos_respuesta_exitosa(self):
        """Sede sin registros debe responder 200 OK con arreglos vacíos y fechas con datos en 0."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchone.return_value = None

            respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Yanahuanca&dias=30")
            self.assertEqual(respuesta.status_code, 200)

            datos = respuesta.get_json()
            self.assertEqual(datos["status"], "success")
            self.assertEqual(datos["fechas_con_datos"], 0)
            self.assertEqual(datos["total_ingresos_periodo"], 0)
            self.assertEqual(datos["horas"], [])
            self.assertEqual(datos["totales"], [])
            self.assertEqual(datos["promedios"], [])

    def test_23_historial_error_conexion_bd(self):
        """Error de conexión a la BD durante la consulta del historial debe responder 503."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_db.side_effect = ConnectionError("Error de conexión simulado")

            respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
            self.assertEqual(respuesta.status_code, 503)

            datos = respuesta.get_json()
            self.assertEqual(datos["status"], "error")

    @patch("routes.admin_ml.obtener_historial_afluencia_periodo")
    def test_24_historial_sin_datos_personales(self, mock_historial):
        """Verificar que la respuesta del historial no exponga nombres, DNI, emails o usuarios."""
        mock_historial.return_value = {
            "status": "success",
            "sede": "Central",
            "periodo_dias": 30,
            "fecha_inicio": "2026-08-31",
            "fecha_fin": "2026-09-29",
            "fechas_con_datos": 10,
            "total_ingresos_periodo": 200,
            "horas": list(range(8, 21)),
            "totales": [15] * 13,
            "promedios": [1.5] * 13,
            "referencia_horaria": "Hora almacenada en la base de datos"
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        json_str = respuesta.get_data(as_text=True).lower()
        for campo_pii in ["dni", "nombre_usuario", "email", "usuario", "nombres", "apellidos"]:
            self.assertNotIn(f'"{campo_pii}"', json_str)

    @patch("routes.admin_ml.obtener_historial_afluencia_periodo")
    def test_25_historial_formato_campos(self, mock_historial):
        """Verificar tipos y consistencia de horas, totales y promedios."""
        mock_historial.return_value = {
            "status": "success",
            "sede": "Central",
            "periodo_dias": 30,
            "fecha_inicio": "2026-08-31",
            "fecha_fin": "2026-09-29",
            "fechas_con_datos": 10,
            "total_ingresos_periodo": 200,
            "horas": list(range(8, 21)),
            "totales": [15] * 13,
            "promedios": [1.5] * 13,
            "referencia_horaria": "Hora almacenada en la base de datos"
        }
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertIsInstance(datos["horas"], list)
        self.assertIsInstance(datos["totales"], list)
        self.assertIsInstance(datos["promedios"], list)
        self.assertEqual(len(datos["horas"]), len(datos["totales"]))
        self.assertEqual(len(datos["horas"]), len(datos["promedios"]))

    def test_26_prediccion_desactualizada_marca_modo_demostracion(self):
        """Predicción con último registro de hace más de 14 días debe marcar modo demostración y advertencia explícita."""
        from datetime import date
        from ml.services.datos_afluencia import construir_variables_afluencia
        from ml.services.servicio_afluencia import predecir_afluencia_desde_bd
        import pandas as pd

        # Simular 16 fechas pero con última fecha el 2026-09-28 para una consulta del 2026-10-31 (33 días de desfase)
        fechas_simuladas = [f"2026-09-{dia:02d}" for dia in range(1, 29)]  # 28 fechas
        datos_mock = []
        for f in fechas_simuladas:
            for h in range(8, 21):
                datos_mock.append({"Fecha": f, "Hora": h, "CantidadIngresos": 10})

        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            mock_hist.return_value = pd.DataFrame(datos_mock)
            resultado = predecir_afluencia_desde_bd(
                fecha=date(2026, 10, 30),
                hora=10,
                sede="Central"
            )

            self.assertEqual(resultado["status"], "success")
            self.assertTrue(resultado["prediccion"]["modo_demostracion"])
            self.assertFalse(resultado["prediccion"]["confiable"])
            self.assertTrue(resultado["consulta"]["es_desactualizado"])
            self.assertEqual(resultado["consulta"]["dias_antiguedad_ultimo_registro"], 32)
            self.assertIn("MODO DEMOSTRACIÓN - NO CONFIABLE", resultado["advertencia"])
            self.assertIn("el último registro es del 28/09 y está 32 días antes de la fecha objetivo 2026-10-30", resultado["advertencia"])
            self.assertIn("El último registro en base de datos es del 28/09", resultado["consulta"]["motivos_advertencia"][0])
            self.assertIn("está 32 días antes de la fecha objetivo consultada", resultado["consulta"]["motivos_advertencia"][0])

    def test_27_prediccion_historial_insuficiente_marca_modo_demostracion(self):
        """Predicción con menos de 15 fechas debe marcar historial insuficiente y modo demostración."""
        from datetime import date
        from ml.services.servicio_afluencia import predecir_afluencia_desde_bd
        import pandas as pd

        # Simular 6 fechas consecutivas recientes (desfase de 1 día, pero < 15 fechas)
        fechas_simuladas = [f"2026-10-{dia:02d}" for dia in range(20, 26)]  # 6 fechas
        datos_mock = []
        for f in fechas_simuladas:
            for h in range(8, 21):
                datos_mock.append({"Fecha": f, "Hora": h, "CantidadIngresos": 12})

        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            mock_hist.return_value = pd.DataFrame(datos_mock)
            resultado = predecir_afluencia_desde_bd(
                fecha=date(2026, 10, 26),
                hora=10,
                sede="Central"
            )

            self.assertEqual(resultado["status"], "success")
            self.assertTrue(resultado["prediccion"]["modo_demostracion"])
            self.assertFalse(resultado["prediccion"]["confiable"])
            self.assertTrue(resultado["consulta"]["historial_insuficiente"])
            self.assertFalse(resultado["consulta"]["es_desactualizado"])
            self.assertIn("MODO DEMOSTRACIÓN - NO CONFIABLE", resultado["advertencia"])

    def test_28_prediccion_reciente_y_suficiente_es_confiable(self):
        """Predicción con 15 o más fechas y desfase <= 14 días cumple calidad mínima referencial (no demo)."""
        from datetime import date
        from ml.services.servicio_afluencia import predecir_afluencia_desde_bd
        import pandas as pd

        # Simular 20 fechas recientes consecutivas hasta el día anterior
        fechas_simuladas = [f"2026-10-{dia:02d}" for dia in range(1, 21)]  # 20 fechas
        datos_mock = []
        for f in fechas_simuladas:
            for h in range(8, 21):
                datos_mock.append({"Fecha": f, "Hora": h, "CantidadIngresos": 15})

        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            mock_hist.return_value = pd.DataFrame(datos_mock)
            resultado = predecir_afluencia_desde_bd(
                fecha=date(2026, 10, 21),
                hora=10,
                sede="Central"
            )

            self.assertEqual(resultado["status"], "success")
            self.assertFalse(resultado["prediccion"]["modo_demostracion"])
            self.assertTrue(resultado["prediccion"]["confiable"])
            self.assertTrue(resultado["prediccion"]["calidad_minima_historial"])
            self.assertFalse(resultado["consulta"]["criterio_justificado_por_evaluacion"])
            self.assertFalse(resultado["consulta"]["es_desactualizado"])
            self.assertFalse(resultado["consulta"]["historial_insuficiente"])
            self.assertNotIn("MODO DEMOSTRACIÓN", resultado["advertencia"])
            self.assertIn("criterios mínimos de calidad operativa de entrada", resultado["advertencia"])

    def test_29_formula_tendencia_reciente_consistente_con_entrenamiento(self):
        """Verificar que TendenciaReciente calcule exactamente IngresosDiaAnterior - PromedioMovil5."""
        from datetime import date
        from ml.services.datos_afluencia import construir_variables_afluencia
        import pandas as pd

        # Simular 6 fechas con valores crecientes para hora 10: 10, 20, 30, 40, 50, 100
        valores_hora_10 = [10, 20, 30, 40, 50, 100]
        fechas = [f"2026-10-{d:02d}" for d in range(1, 7)]
        datos_mock = []
        for i, f in enumerate(fechas):
            for h in range(8, 21):
                cant = valores_hora_10[i] if h == 10 else 5
                datos_mock.append({"Fecha": f, "Hora": h, "CantidadIngresos": cant})

        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            mock_hist.return_value = pd.DataFrame(datos_mock)
            variables, contexto = construir_variables_afluencia(
                fecha_objetivo=date(2026, 10, 7),
                hora_objetivo=10,
                sede="Central"
            )

            # Para hora 10:
            # IngresosDiaAnterior = 100 (último día observado)
            # IngresosCincoDiasAntes = 20 (quinto día hacia atrás: 20)
            # PromedioMovil5 = (20 + 30 + 40 + 50 + 100) / 5 = 48.0
            # TendenciaReciente = IngresosDiaAnterior - PromedioMovil5 = 100 - 48 = 52.0
            # Fórmula alternativa errónea: PromedioMovil5 - IngresosCincoDiasAntes = 48 - 20 = 28.0
            self.assertEqual(variables["IngresosDiaAnterior"], 100.0)
            self.assertEqual(variables["IngresosCincoDiasAntes"], 20.0)
            self.assertAlmostEqual(variables["PromedioMovil5"], 48.0, places=4)
            self.assertAlmostEqual(variables["TendenciaReciente"], 52.0, places=4)
            self.assertEqual(
                variables["TendenciaReciente"],
                variables["IngresosDiaAnterior"] - variables["PromedioMovil5"]
            )
            self.assertNotEqual(
                variables["TendenciaReciente"],
                variables["PromedioMovil5"] - variables["IngresosCincoDiasAntes"]
            )

    def test_30_historial_excluye_y_reporta_registros_fuera_horario(self):
        """Verificar que el historial excluya del gráfico operativo (08:00 a 20:00) los ingresos atípicos a las 01:00 y 21:00 y los reporte explícitamente."""
        from datetime import date
        from ml.services.datos_afluencia import obtener_historial_afluencia_periodo

        # Simular registros para el 28/09/2026 con horas habituales (10:00, 14:00) y fuera de horario (01:00, 21:00)
        filas_mock = [
            ("2026-09-28", 1, 1),
            ("2026-09-28", 10, 3),
            ("2026-09-28", 14, 2),
            ("2026-09-28", 21, 1)
        ]

        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchone.return_value = (date(2026, 9, 28),)
            mock_cursor.fetchall.return_value = filas_mock

            resultado = obtener_historial_afluencia_periodo(sede="Central", dias=30)

            self.assertEqual(resultado["status"], "success")
            # El arreglo de horas del gráfico debe contener estrictamente las 13 horas habituales (08:00 a 20:00)
            self.assertEqual(resultado["horas"], list(range(8, 21)))
            self.assertNotIn(1, resultado["horas"])
            self.assertNotIn(21, resultado["horas"])

            # El total en horario habitual debe ser 5 (3 + 2)
            self.assertEqual(resultado["total_ingresos_periodo"], 5)
            # El total fuera de horario debe ser 2 (1 a las 01:00 + 1 a las 21:00)
            self.assertEqual(resultado["total_ingresos_fuera_horario"], 2)

            # Verificar reporte explícito de registros fuera de horario
            fuera = resultado["registros_fuera_horario"]
            self.assertEqual(fuera["total"], 2)
            self.assertIn("Excluidos del gráfico operativo", fuera["criterio_aplicado"])
            self.assertEqual(len(fuera["detalle"]), 2)
            self.assertEqual(fuera["detalle"][0], {"hora": 1, "etiqueta": "01:00", "cantidad": 1})
            self.assertEqual(fuera["detalle"][1], {"hora": 21, "etiqueta": "21:00", "cantidad": 1})


if __name__ == "__main__":
    unittest.main()
