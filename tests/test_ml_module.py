import unittest
from unittest.mock import patch, MagicMock
from app import app
from ml.services.predictor_afluencia import obtener_predictor_afluencia


class TestModuloMachineLearning(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

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

    def test_04_supervisor_sede_forzada(self):
        """Supervisor de Central enviando 'Tarma' en JSON debe ser forzado a su sede asignada 'Central'."""
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

    def test_05_consultor_acceso_solo_lectura(self):
        """Consultor puede acceder a la vista GET y realizar consultas predictivas en la API."""
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

    def test_08_post_csrf_valido_exitoso(self):
        """Petición POST con token CSRF válido debe procesarse correctamente (200 OK)."""
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

    def test_11_sede_inexistente(self):
        """Sede inexistente sin datos históricos debe retornar 400 Bad Request."""
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

    def test_18_historial_superadmin_7_dias(self):
        """SuperAdmin consultando 7 días debe obtener status success, periodo 7 y referencia horaria."""
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

    def test_19_historial_superadmin_30_dias(self):
        """SuperAdmin consultando 30 días debe obtener status success y periodo 30."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["periodo_dias"], 30)

    def test_20_historial_dias_invalido_400(self):
        """Parámetro dias distinto de 7 o 30 debe retornar 400 Bad Request."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=15")
        self.assertEqual(respuesta.status_code, 400)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "error")

    def test_21_historial_supervisor_sede_forzada(self):
        """Supervisor con sede 'Tarma' enviando 'Central' debe recibir respuesta para su sede 'Tarma'."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "super_tarma"
            sesion["admin_rol"] = "Supervisor"
            sesion["admin_sede"] = "Tarma"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        datos = respuesta.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["sede"], "Tarma")

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

    def test_24_historial_sin_datos_personales(self):
        """Verificar que la respuesta del historial no exponga nombres, DNI, emails o usuarios."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"

        respuesta = self.client.get("/admin/api/ml/historial-afluencia?sede=Central&dias=30")
        self.assertEqual(respuesta.status_code, 200)

        json_str = respuesta.get_data(as_text=True).lower()
        for campo_pii in ["dni", "nombre_usuario", "email", "usuario", "nombres", "apellidos"]:
            self.assertNotIn(f'"{campo_pii}"', json_str)

    def test_25_historial_formato_campos(self):
        """Verificar tipos y consistencia de horas, totales y promedios."""
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


if __name__ == "__main__":
    unittest.main()
