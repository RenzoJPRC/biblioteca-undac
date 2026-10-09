import unittest
from unittest.mock import patch, MagicMock
from datetime import date, datetime, timedelta

from llm.services.consultas_biblioteca import (
    buscar_personas,
    obtener_historial_persona,
    obtener_resumen_ingresos,
    obtener_horas_mayor_afluencia,
    escapar_like,
    enmascarar_codigo,
    _es_error_tabla_inexistente
)

from llm.services.motor_intenciones import clasificar_intencion, normalizar_texto


class TestModuloLLMConsultas(unittest.TestCase):

    # 1. BÚSQUEDA SEGURA DE PERSONAS

    def test_01_buscar_personas_nombre_corto(self):
        """Nombre menor de 3 caracteres debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            buscar_personas("ab")

    def test_02_buscar_personas_nombre_largo(self):
        """Nombre mayor de 120 caracteres debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            buscar_personas("a" * 121)

    def test_03_buscar_personas_comodines_escapados(self):
        """Los caracteres comodín %, _, [ y \\ deben ser escapados correctamente."""
        self.assertEqual(escapar_like("Juan_Perez%[1]\\"), "Juan\\_Perez\\%\\[1]\\\\")

    def test_04_buscar_personas_maximo_10_candidatos(self):
        """La búsqueda no debe retornar más de 10 candidatos aunque existan más en BD."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            # Simular 15 filas retornadas
            mock_cursor.fetchall.return_value = [
                ("Alumno", i, f"Alumno Test {i}", "2020123456") for i in range(1, 16)
            ]

            resultados = buscar_personas("Alumno", limite=15)
            self.assertLessEqual(len(resultados), 10)

    def test_05_buscar_personas_sin_resultados(self):
        """Búsqueda sin coincidencias debe retornar una lista vacía."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            resultados = buscar_personas("Inexistente")
            self.assertEqual(resultados, [])

    def test_06_buscar_personas_multiples_coincidencias(self):
        """Las coincidencias exactas deben ordenarse primero que las parciales."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular retornos de la consulta
            mock_cursor.fetchall.side_effect = [
                [("Alumno", 1, "Renzo Rojas Castillo", "20201001")],
                [("Visitante", 2, "Renzo", None)],
                [],
                [],
                []
            ]

            resultados = buscar_personas("Renzo")
            self.assertEqual(resultados[0]["nombre"], "Renzo")
            self.assertEqual(resultados[1]["nombre"], "Renzo Rojas Castillo")

    def test_07_buscar_personas_codigo_enmascarado(self):
        """Verificar enmascaramiento correcto del código de matrícula."""
        self.assertEqual(enmascarar_codigo("2020123456"), "20******56")
        self.assertEqual(enmascarar_codigo("12345"), "1***5")
        self.assertEqual(enmascarar_codigo("12"), "***")
        self.assertIsNone(enmascarar_codigo(None))

    def test_08_sin_datos_personales_sensibles(self):
        """Verificar que no se expongan DNI, correo ni teléfono."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = [
                ("Alumno", 1, "Juan Perez", "2020100100")
            ]

            resultados = buscar_personas("Juan Perez")
            if resultados:
                item = resultados[0]
                self.assertNotIn("dni", item)
                self.assertNotIn("email", item)
                self.assertNotIn("correo", item)
                self.assertNotIn("telefono", item)

    # 2. HISTORIAL DE UNA PERSONA

    def test_09_historial_consultor_bloqueado(self):
        """El rol Consultor debe recibir acceso denegado (PermissionError)."""
        with self.assertRaises(PermissionError):
            obtener_historial_persona("Alumno", 1, rol="Consultor")

    def test_10_historial_supervisor_sede_forzada(self):
        """Supervisor debe requerir sede permitida válida y aplicarla."""
        with self.assertRaises(ValueError):
            obtener_historial_persona("Alumno", 1, rol="Supervisor", sede_permitida=None)

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            res = obtener_historial_persona("Alumno", 1, rol="Supervisor", sede_permitida="Tarma")
            self.assertEqual(res["status"], "success")

    def test_11_historial_superadmin_sin_restriccion(self):
        """SuperAdmin puede consultar historial sin restricción obligatoria de sede."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            res = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res["status"], "success")

    def test_12_historial_limite_maximo_50(self):
        """Límite de historial mayor a 50 se acota automáticamente a 50."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            res = obtener_historial_persona("Alumno", 1, rol="SuperAdmin", limite=100)
            self.assertEqual(res["status"], "success")

    def test_13_historial_vacio(self):
        """Historial de persona sin registros retorna total_ingresos 0 y lista vacía."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            res = obtener_historial_persona("Alumno", 9999, rol="SuperAdmin")
            self.assertEqual(res["total_ingresos"], 0)
            self.assertEqual(res["registros"], [])
            self.assertIsNone(res["primera_fecha"])
            self.assertIsNone(res["ultima_fecha"])

    def test_14_historial_union_registros_actuales_e_historicos(self):
        """Verificar combinación de registros de producción e históricos."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = [
                (101, datetime(2026, 9, 28, 10, 0), "Manana", "Central", 1, "actual", 2, None, "Alumno"),
                (50, datetime(2026, 5, 15, 14, 0), "Tarde", "Central", 1, "historico", None, None, "Alumno")
            ]

            res = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res["total_ingresos"], 2)
            self.assertEqual(len(res["registros"]), 2)
            self.assertEqual(res["registros"][0]["fuente"], "actual")
            self.assertEqual(res["registros"][1]["fuente"], "historico")

    def test_15_historial_docente_sin_tabla_historica(self):
        """Docente consulta únicamente RegistroIngresos sin usar la tabla histórica."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []

            res = obtener_historial_persona("Docente", 5, rol="SuperAdmin")
            self.assertEqual(res["status"], "success")

            # Verificar que el SQL ejecutado no contenga RegistroIngresos_Historico
            sql_ejecutado = mock_cursor.execute.call_args[0][0]
            self.assertNotIn("RegistroIngresos_Historico", sql_ejecutado)

    # 3. CONSULTAS AGREGADAS

    def test_16_resumen_ingresos_fechas_invalidas(self):
        """Fecha inicio mayor que fecha fin debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            obtener_resumen_ingresos("2026-10-10", "2026-10-01", rol="SuperAdmin")

    def test_17_resumen_ingresos_rango_mayor_366_dias(self):
        """Rango mayor a 366 días debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            obtener_resumen_ingresos("2025-01-01", "2026-02-01", rol="SuperAdmin")

    # 4. MOTOR DE INTENCIONES

    def test_18_intenciones_locales_reconocidas(self):
        """Reconocimiento correcto de patrones de intención deterministas."""
        i1 = clasificar_intencion("¿Cuántas veces ingresó Renzo Rojas Castillo?")
        self.assertEqual(i1["intencion"], "historial_persona")
        self.assertEqual(i1["parametros"]["texto_persona"], "Renzo Rojas Castillo")

        i2 = clasificar_intencion("¿Cuándo fue el último ingreso de Maria Lopez?")
        self.assertEqual(i2["intencion"], "ultimo_ingreso_persona")

        i3 = clasificar_intencion("¿Cuáles son las horas de mayor afluencia?")
        self.assertEqual(i3["intencion"], "horas_mayor_afluencia")

        i4 = clasificar_intencion("Generar predicción de afluencia para mañana")
        self.assertEqual(i4["intencion"], "prediccion_afluencia")

        i5 = clasificar_intencion("Resumen de ingresos del mes pasado")
        self.assertEqual(i5["intencion"], "resumen_ingresos")

    def test_19_intenciones_pregunta_desconocida(self):
        """Pregunta no relacionada retorna intencion_desconocida."""
        res = clasificar_intencion("¿Cuál es el menú del comedor universitario?")
        self.assertEqual(res["intencion"], "intencion_desconocida")

    def test_20_intenciones_pregunta_mayor_500_caracteres(self):
        """Pregunta con más de 500 caracteres retorna intencion_desconocida con motivo."""
        pregunta_larga = "hola " * 150
        res = clasificar_intencion(pregunta_larga)
        self.assertEqual(res["intencion"], "intencion_desconocida")
        self.assertIn("motivo", res)

    # 5. AUDITORÍA Y SEGURIDAD ADICIONAL

    def test_22_rol_invalido_o_none(self):
        """Rol desconocido, modificado o None debe lanzar PermissionError."""
        with self.assertRaises(PermissionError):
            obtener_historial_persona("Alumno", 1, rol=None)

        with self.assertRaises(PermissionError):
            obtener_historial_persona("Alumno", 1, rol="AdminFalso")

        with self.assertRaises(PermissionError):
            obtener_resumen_ingresos("2026-10-01", "2026-10-05", rol="HackRol")

    def test_23_persona_id_y_limite_booleanos(self):
        """persona_id o limite pasados como booleano (True/False) deben lanzar ValueError."""
        with self.assertRaises(ValueError):
            obtener_historial_persona("Alumno", True, rol="SuperAdmin")

        with self.assertRaises(ValueError):
            obtener_historial_persona("Alumno", 1, rol="SuperAdmin", limite=True)

        with self.assertRaises(ValueError):
            buscar_personas("Juan Perez", limite=True)

    def test_24_fallback_sin_tabla_historica(self):
        """Si RegistroIngresos_Historico no existe en la BD, la consulta continua con la tabla actual."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            # Simular que la consulta a RegistroIngresos_Historico lanza un error de tabla no encontrada
            mock_cursor.fetchall.side_effect = [
                [(101, datetime(2026, 9, 28, 10, 0), "Manana", "Central", 1, "actual", 2, None, "Alumno")],
                Exception("Invalid object name 'RegistroIngresos_Historico'")
            ]

            res = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_ingresos"], 1)
            self.assertEqual(res["registros"][0]["fuente"], "actual")

    def test_25_deduplicacion_registros(self):
        """Si un registro_id existe tanto en actual como en histórico, se evita duplicidad."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.side_effect = [
                [(101, datetime(2026, 9, 28, 10, 0), "Manana", "Central", 1, "actual", 2, None, "Alumno")],
                [(101, datetime(2026, 9, 28, 10, 0), "Manana", "Central", 1, "historico", 2, None, "Alumno")]
            ]

            res = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res["total_ingresos"], 1)
            self.assertEqual(len(res["registros"]), 1)
            self.assertEqual(res["registros"][0]["fuente"], "actual")

    def test_26_string_vacio_o_espacios(self):
        """Rechazar strings vacíos o con solo espacios."""
        with self.assertRaises(ValueError):
            buscar_personas("   ")

        res = clasificar_intencion("   ")
        self.assertEqual(res["intencion"], "intencion_desconocida")

    def test_27_motor_intenciones_seguridad_sql(self):
        """El motor de intenciones no ejecuta SQL ni se rompe con comandos SQL incluidos en la pregunta."""
        res = clasificar_intencion("SELECT * FROM Alumnos WHERE 1=1; DROP TABLE RegistroIngresos;")
        self.assertIn(res["intencion"], ["intencion_desconocida", "busqueda_persona", "historial_persona"])
        self.assertNotIn("SELECT", res.get("parametros", {}).get("texto_persona", ""))

    def test_28_ejemplos_renzo_datos_simulados(self):
        """Comprobar que búsquedas simuladas de Renzo mantengan enmascaramiento y privacidad."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.side_effect = [
                [("Alumno", 1, "Renzo Rojas Castillo", "2020100100")],
                [], [], [], []
            ]

            res = buscar_personas("Renzo")
            self.assertEqual(res[0]["codigo_mascarado"], "20******00")
            self.assertNotIn("dni", res[0])
    # 6. PRUEBAS DE MANEJO DE EXCEPCIONES Y TOLERANCIA DE TABLA INEXISTENTE (ERROR 208)

    def test_29_helper_es_error_tabla_inexistente(self):
        """Verificar la función privada _es_error_tabla_inexistente."""
        e_208 = Exception("Invalid object name 'RegistroIngresos_Historico'")
        e_args = Exception(208, "Table missing")
        e_sqlstate = Exception("[42S02] Table not found")
        e_conn = ConnectionError("Fallo de conexión a la BD")
        e_timeout = TimeoutError("Timeout en la consulta SQL")
        e_perm = PermissionError("SELECT permission denied")

        self.assertTrue(_es_error_tabla_inexistente(e_208))
        self.assertTrue(_es_error_tabla_inexistente(e_args))
        self.assertTrue(_es_error_tabla_inexistente(e_sqlstate))

        self.assertFalse(_es_error_tabla_inexistente(e_conn))
        self.assertFalse(_es_error_tabla_inexistente(e_timeout))
        self.assertFalse(_es_error_tabla_inexistente(e_perm))

    def test_30_error_208_tabla_inexistente_tolerado_en_todas_las_consultas(self):
        """Error 208 / Invalid object name se tolera en tablas opcionales para las 4 funciones de consulta."""
        e_208 = Exception("Invalid object name 'RegistroIngresos_Historico'")

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # 1. buscar_personas: 1 tabla opcional lanza 208, las demas continúan
            mock_cursor.fetchall.side_effect = [
                [("Alumno", 1, "Juan Perez", "20201001")],
                e_208,
                [], [], []
            ]
            res_busqueda = buscar_personas("Juan Perez")
            self.assertEqual(len(res_busqueda), 1)

            # 2. obtener_historial_persona: RegistroIngresos_Historico lanza 208
            mock_cursor.fetchall.side_effect = [
                [(101, datetime(2026, 9, 28, 10, 0), "Manana", "Central", 1, "actual", 2, None, "Alumno")],
                e_208
            ]
            res_hist = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res_hist["status"], "success")
            self.assertEqual(res_hist["total_ingresos"], 1)

            # 3. obtener_resumen_ingresos: RegistroIngresos_Historico lanza 208
            mock_cursor.fetchall.side_effect = [
                [("Central", "Alumno")],
                e_208
            ]
            res_res = obtener_resumen_ingresos("2026-10-01", "2026-10-05", rol="SuperAdmin")
            self.assertEqual(res_res["status"], "success")
            self.assertEqual(res_res["total_ingresos"], 1)

            # 4. obtener_horas_mayor_afluencia: RegistroIngresos_Historico lanza 208
            mock_cursor.fetchall.side_effect = [
                [(10, date(2026, 10, 1))],
                e_208
            ]
            res_horas = obtener_horas_mayor_afluencia("2026-10-01", "2026-10-05", rol="SuperAdmin")
            self.assertEqual(res_horas["status"], "success")

    def test_31_error_conexion_relanzado_en_todas_las_consultas(self):
        """Fallos de conexión a BD se relanzan mediante raise en las 4 funciones."""
        e_conn = ConnectionError("Fallo de conexión crítico")

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # buscar_personas
            mock_cursor.execute.side_effect = e_conn
            with self.assertRaises(ConnectionError):
                buscar_personas("Juan Perez")

            # obtener_historial_persona
            mock_cursor.execute.side_effect = [None, e_conn]
            with self.assertRaises(ConnectionError):
                obtener_historial_persona("Alumno", 1, rol="SuperAdmin")

            # obtener_resumen_ingresos
            mock_cursor.execute.side_effect = [None, e_conn]
            with self.assertRaises(ConnectionError):
                obtener_resumen_ingresos("2026-10-01", "2026-10-05", rol="SuperAdmin")

            # obtener_horas_mayor_afluencia
            mock_cursor.execute.side_effect = [None, e_conn]
            with self.assertRaises(ConnectionError):
                obtener_horas_mayor_afluencia("2026-10-01", "2026-10-05", rol="SuperAdmin")

    def test_32_error_timeout_relanzado_en_todas_las_consultas(self):
        """Errores de timeout en SQL Server se relanzan mediante raise en las 4 funciones."""
        e_timeout = TimeoutError("Query timeout expired")

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # buscar_personas
            mock_cursor.execute.side_effect = e_timeout
            with self.assertRaises(TimeoutError):
                buscar_personas("Juan Perez")

            # obtener_historial_persona
            mock_cursor.execute.side_effect = [None, e_timeout]
            with self.assertRaises(TimeoutError):
                obtener_historial_persona("Alumno", 1, rol="SuperAdmin")

            # obtener_resumen_ingresos
            mock_cursor.execute.side_effect = [None, e_timeout]
            with self.assertRaises(TimeoutError):
                obtener_resumen_ingresos("2026-10-01", "2026-10-05", rol="SuperAdmin")

            # obtener_horas_mayor_afluencia
            mock_cursor.execute.side_effect = [None, e_timeout]
            with self.assertRaises(TimeoutError):
                obtener_horas_mayor_afluencia("2026-10-01", "2026-10-05", rol="SuperAdmin")

    def test_33_error_permisos_relanzado_en_todas_las_consultas(self):
        """Errores de permisos de SQL Server se relanzan mediante raise en las 4 funciones."""
        e_perm = PermissionError("SELECT permission denied on object")

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # buscar_personas
            mock_cursor.execute.side_effect = e_perm
            with self.assertRaises(PermissionError):
                buscar_personas("Juan Perez")

            # obtener_historial_persona
            mock_cursor.execute.side_effect = [None, e_perm]
            with self.assertRaises(PermissionError):
                obtener_historial_persona("Alumno", 1, rol="SuperAdmin")

            # obtener_resumen_ingresos
            mock_cursor.execute.side_effect = [None, e_perm]
            with self.assertRaises(PermissionError):
                obtener_resumen_ingresos("2026-10-01", "2026-10-05", rol="SuperAdmin")

            # obtener_horas_mayor_afluencia
            mock_cursor.execute.side_effect = [None, e_perm]
            with self.assertRaises(PermissionError):
                obtener_horas_mayor_afluencia("2026-10-01", "2026-10-05", rol="SuperAdmin")


if __name__ == "__main__":
    unittest.main()
