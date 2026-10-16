import unittest
from unittest.mock import patch, MagicMock
from datetime import date, datetime, timedelta

from llm.services.consultas_biblioteca import (
    buscar_personas,
    obtener_historial_persona,
    obtener_resumen_ingresos,
    obtener_horas_mayor_afluencia,
    contar_ingresos_alumno_por_codigo,
    consultar_afluencia_historica,
    escapar_like,
    enmascarar_codigo,
    _es_error_tabla_inexistente
)

from llm.services.motor_intenciones import (
    clasificar_intencion,
    normalizar_texto,
    resolver_expresion_temporal,
    extraer_codigo_matricula,
    es_pregunta_seguimiento,
    es_seguimiento_ambiguo,
    sanitizar_contexto
)


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

    # 6. AMPLIACIÓN DE PREDICCIÓN Y PREVENCIÓN DE FALSOS POSITIVOS
    def test_34_ampliacion_prediccion_afluencia_frases_minimas(self):
        """El motor local debe reconocer las 10 frases mínimas requeridas de predicción."""
        frases_prediccion = [
            "Predicción de afluencia",
            "Predecir la afluencia",
            "¿Cuál será la afluencia?",
            "¿Cuál será el nivel de afluencia?",
            "Afluencia esperada",
            "Nivel de afluencia esperado",
            "¿Habrá mucha gente?",
            "¿Estará llena la biblioteca?",
            "Pronóstico de afluencia",
            "Estimar afluencia",
            # Variaciones complementarias
            "¿Cuál es el pronóstico de afluencia para mañana?",
            "¿Habrá bastante gente en la biblioteca?",
            "Afluencia estimada para las 10",
        ]
        for f in frases_prediccion:
            res = clasificar_intencion(f)
            self.assertEqual(
                res["intencion"],
                "prediccion_afluencia",
                f"La frase '{f}' debería clasificarse como 'prediccion_afluencia', se obtuvo '{res['intencion']}'"
            )

    def test_35_evitar_falsos_positivos_expresiones_pasadas(self):
        """Expresiones en pasado o históricas NO deben clasificarse como predicción."""
        casos = [
            ("¿Cuáles fueron las horas de mayor afluencia?", "horas_mayor_afluencia"),
            ("Resumen de afluencia del mes pasado", "resumen_ingresos"),
            ("¿Cuántas personas ingresaron?", "resumen_ingresos"),
            ("Historial de ingresos de Juan Pérez", "historial_persona"),
            ("¿Cuál fue la afluencia de ayer?", "resumen_ingresos"),
            ("¿Ayer hubo afluencia?", "intencion_desconocida"),
        ]
        for frase, intencion_esperada in casos:
            res = clasificar_intencion(frase)
            self.assertNotEqual(
                res["intencion"],
                "prediccion_afluencia",
                f"La frase '{frase}' NO debe clasificarse como 'prediccion_afluencia'"
            )
            self.assertEqual(
                res["intencion"],
                intencion_esperada,
                f"La frase '{frase}' esperaba intención '{intencion_esperada}', se obtuvo '{res['intencion']}'"
            )

    def test_36_promedio_diario_dias_con_registros(self):
        """obtener_horas_mayor_afluencia calcula el promedio dividiendo entre fechas con registros y retorna dias_con_registros."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular 4 registros en 2 fechas distintas (2026-10-01 y 2026-10-03)
            # Ambos a las 10 hrs
            mock_cursor.fetchall.side_effect = [
                [
                    (10, date(2026, 10, 1)),
                    (10, date(2026, 10, 1)),
                    (10, date(2026, 10, 3)),
                    (10, date(2026, 10, 3)),
                ],
                []  # tabla histórica vacía
            ]

            res = obtener_horas_mayor_afluencia("2026-10-01", "2026-10-10", rol="SuperAdmin")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["dias_con_registros"], 2)
            self.assertEqual(len(res["horas_top"]), 1)
            # 4 ingresos entre 2 días con registros = 2.0 de promedio por día con registros
            self.assertEqual(res["horas_top"][0]["total_ingresos"], 4)
            self.assertEqual(res["horas_top"][0]["promedio_diario"], 2.0)

    def test_37_resolver_expresiones_temporales(self):
        """resolver_expresion_temporal resuelve ayer, hoy, anteayer, semana pasada, mes pasado y rangos explícitos."""
        ref = date(2026, 10, 11)  # Domingo

        # Ayer
        t1 = resolver_expresion_temporal("ayer", fecha_referencia=ref)
        self.assertEqual(t1["fecha_inicio"], "2026-10-10")
        self.assertEqual(t1["fecha_fin"], "2026-10-10")
        self.assertEqual(t1["tipo_periodo"], "ayer")

        # Hoy
        t2 = resolver_expresion_temporal("hoy", fecha_referencia=ref)
        self.assertEqual(t2["fecha_inicio"], "2026-10-11")
        self.assertEqual(t2["tipo_periodo"], "hoy")

        # Anteayer
        t3 = resolver_expresion_temporal("anteayer", fecha_referencia=ref)
        self.assertEqual(t3["fecha_inicio"], "2026-10-09")
        self.assertEqual(t3["tipo_periodo"], "anteayer")

        # Semana pasada
        t4 = resolver_expresion_temporal("la semana pasada", fecha_referencia=ref)
        self.assertEqual(t4["tipo_periodo"], "semana_pasada")
        self.assertTrue(t4["fecha_inicio"] < t4["fecha_fin"])

        # Mes pasado
        t5 = resolver_expresion_temporal("el mes pasado", fecha_referencia=ref)
        self.assertEqual(t5["fecha_inicio"], "2026-09-01")
        self.assertEqual(t5["fecha_fin"], "2026-09-30")
        self.assertEqual(t5["tipo_periodo"], "mes_pasado")

        # Rango explícito
        t6 = resolver_expresion_temporal("del 2026-08-01 al 2026-08-15", fecha_referencia=ref)
        self.assertEqual(t6["fecha_inicio"], "2026-08-01")
        self.assertEqual(t6["fecha_fin"], "2026-08-15")

    def test_38_deteccion_preguntas_seguimiento_y_ambiguedad(self):
        """es_pregunta_seguimiento y es_seguimiento_ambiguo clasifican correctamente."""
        # Seguimientos claros
        self.assertTrue(es_pregunta_seguimiento("¿Y ayer?"))
        self.assertTrue(es_pregunta_seguimiento("y ayer?"))
        self.assertTrue(es_pregunta_seguimiento("ayer"))
        self.assertTrue(es_pregunta_seguimiento("¿ayer?"))
        self.assertTrue(es_pregunta_seguimiento("¿Y hoy?"))
        self.assertTrue(es_pregunta_seguimiento("¿Y la semana pasada?"))
        self.assertTrue(es_pregunta_seguimiento("¿Y del 2026-08-01 al 2026-08-15?"))
        self.assertFalse(es_seguimiento_ambiguo("¿Y ayer?"))
        self.assertFalse(es_seguimiento_ambiguo("ayer"))

        # Seguimientos ambiguos
        self.assertTrue(es_pregunta_seguimiento("¿Y entonces?"))
        self.assertTrue(es_seguimiento_ambiguo("¿Y entonces?"))
        self.assertTrue(es_seguimiento_ambiguo("¿Y qué más?"))
        self.assertTrue(es_seguimiento_ambiguo("¿Y?"))
        self.assertTrue(es_seguimiento_ambiguo("¿Cómo?"))

        # Consultas normales independientes (no son seguimientos)
        self.assertFalse(es_pregunta_seguimiento("Resumen de ingresos del 2026-09-01 al 2026-09-30"))
        self.assertFalse(es_pregunta_seguimiento("¿Cuáles son las horas de mayor afluencia?"))
        self.assertFalse(es_pregunta_seguimiento("¿Cuántas veces ingresó Renzo Rojas Castillo?"))

    def test_39_sanitizar_contexto_limpia_datos_sensibles(self):
        """sanitizar_contexto conserva exclusivamente campos mínimos y descarta datos sensibles."""
        ctx_sucio = {
            "intencion": "resumen_ingresos",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "nombre": "Renzo Rojas Castillo",
            "dni": "12345678",
            "persona_id": 99,
            "respuesta": "Texto de respuesta completo",
            "datos": {"raw": 123}
        }
        limpio = sanitizar_contexto(ctx_sucio)
        self.assertEqual(limpio["intencion"], "resumen_ingresos")
        self.assertEqual(limpio["fecha_inicio"], "2026-09-01")
        self.assertEqual(limpio["fecha_fin"], "2026-09-30")
        self.assertNotIn("nombre", limpio)
        self.assertNotIn("dni", limpio)
        self.assertNotIn("persona_id", limpio)
        self.assertNotIn("respuesta", limpio)
        self.assertNotIn("datos", limpio)

    # 7. CONTEO PARAMETRIZADO DE INGRESOS DE ALUMNO POR CÓDIGO

    def test_40_contar_ingresos_alumno_valida_codigo(self):
        """Código de matrícula vacío, no string o muy corto/largo debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            contar_ingresos_alumno_por_codigo("", rol="SuperAdmin")
        with self.assertRaises(ValueError):
            contar_ingresos_alumno_por_codigo("ab", rol="SuperAdmin")
        with self.assertRaises(ValueError):
            contar_ingresos_alumno_por_codigo("a" * 35, rol="SuperAdmin")

    def test_41_contar_ingresos_alumno_bloquea_consultor(self):
        """El rol Consultor no tiene permiso para consultar informes individuales por alumno."""
        with self.assertRaises(PermissionError):
            contar_ingresos_alumno_por_codigo("2020123456", rol="Consultor")

    def test_42_contar_ingresos_alumno_valida_rango_fechas(self):
        """fecha_inicio posterior a fecha_fin debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            contar_ingresos_alumno_por_codigo(
                "2020123456",
                fecha_inicio="2026-09-30",
                fecha_fin="2026-09-01",
                rol="SuperAdmin"
            )

    def test_43_contar_ingresos_alumno_consulta_parametrizada_superadmin(self):
        """Consulta exitosa con SuperAdmin: retorna conteo, código enmascarado y cobertura del sistema."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular primer SELECT (conteo) y segundo SELECT (cobertura global)
            mock_cursor.fetchone.side_effect = [
                (3, datetime(2026, 4, 15, 10, 30), datetime(2026, 5, 20, 16, 45), 2, "Ingeniería de Sistemas"),
                (datetime(2026, 4, 10, 12, 0), datetime(2026, 9, 28, 15, 56), 38)
            ]

            res = contar_ingresos_alumno_por_codigo(
                "2020123456",
                fecha_inicio="2026-04-01",
                fecha_fin="2026-06-30",
                rol="SuperAdmin"
            )

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_ingresos"], 3)
            self.assertEqual(res["dias_con_visita"], 2)
            self.assertEqual(res["codigo_mascarado"], "20******56")
            self.assertIn("2026-04-10", res["mensaje"])
            self.assertIn("2026-09-28", res["mensaje"])
            self.assertIn("38 visitas", res["mensaje"])

            # Verificar que cursor.execute fue llamado con marcadores parametrizados (?)
            llamadas = mock_cursor.execute.call_args_list
            self.assertGreaterEqual(len(llamadas), 2)
            sql_conteo, params_conteo = llamadas[0][0]
            self.assertIn("A.CodigoMatricula = ?", sql_conteo)
            self.assertEqual(params_conteo[0], "2020123456")

    def test_44_contar_ingresos_alumno_supervisor_filtra_sede(self):
        """Supervisor sin sede válida lanza ValueError; con sede válida aplica filtro."""
        with self.assertRaises(ValueError):
            contar_ingresos_alumno_por_codigo("2020123456", rol="Supervisor", sede_permitida=None)

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchone.side_effect = [
                (1, datetime(2026, 4, 15, 10, 0), datetime(2026, 4, 15, 10, 0), 1, "Sistemas"),
                (datetime(2026, 4, 10), datetime(2026, 9, 28), 38)
            ]

            res = contar_ingresos_alumno_por_codigo(
                "2020123456",
                rol="Supervisor",
                sede_permitida="Central"
            )
            self.assertEqual(res["status"], "success")
            sql_conteo, params_conteo = mock_cursor.execute.call_args_list[0][0]
            self.assertIn("ISNULL(R.Sede, 'Central') = ?", sql_conteo)
            self.assertIn("Central", params_conteo)

    def test_45_extraer_codigo_matricula_y_clasificar_intencion(self):
        """extraer_codigo_matricula y clasificar_intencion detectan correctamente conteo_ingresos_alumno."""
        pregunta = "¿Cuántas veces ingresó el alumno con código 2021123456 entre 2026-04-10 y 2026-05-10?"
        cod = extraer_codigo_matricula(pregunta)
        self.assertEqual(cod, "2021123456")

        cl = clasificar_intencion(pregunta)
        self.assertEqual(cl["intencion"], "conteo_ingresos_alumno")
        self.assertEqual(cl["parametros"]["codigo_matricula"], "2021123456")

    def test_46_contar_ingresos_alumno_excluye_filas_prueba(self):
        """contar_ingresos_alumno_por_codigo filtra EsPrueba = 1 y reporta exclusión de pruebas."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular: 0 visitas reales encontradas para el alumno; cobertura global: 38 registros, los 38 son prueba
            mock_cursor.fetchone.side_effect = [
                (0, None, None, 0, "Ingeniería de Minas"),
                (datetime(2026, 4, 10), datetime(2026, 9, 28), 38, 38, 0)
            ]

            res = contar_ingresos_alumno_por_codigo("2021123456", rol="SuperAdmin")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_ingresos"], 0)
            self.assertIn("No se encontraron ingresos reales", res["mensaje"])
            self.assertIn("excluyeron 38 registros manuales de prueba", res["mensaje"])

            sql_conteo = mock_cursor.execute.call_args_list[0][0][0]
            self.assertIn("ISNULL(R.EsPrueba, 0) = 0", sql_conteo)

    def test_47_distingue_visitas_reales_de_pruebas(self):
        """Las visitas reales futuras (EsPrueba = 0) sí se contabilizan, distinguiéndolas de las pruebas."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular: 5 visitas reales para el alumno; cobertura global: 43 registros (38 pruebas, 5 reales)
            mock_cursor.fetchone.side_effect = [
                (5, datetime(2026, 10, 15, 9, 0), datetime(2026, 10, 20, 14, 0), 3, "Educación"),
                (datetime(2026, 4, 10), datetime(2026, 10, 20), 43, 38, 5)
            ]

            res = contar_ingresos_alumno_por_codigo("2022987654", rol="SuperAdmin")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_ingresos"], 5)
            self.assertEqual(res["dias_con_visita"], 3)
            self.assertIn("registra 5 ingreso(s) real(es)", res["mensaje"])
            self.assertIn("El sistema contiene 5 visitas reales", res["mensaje"])
            self.assertIn("excluyeron 38 registros manuales de prueba", res["mensaje"])

    # 48. CONSULTA DESCRIPTIVA DE AFLUENCIA HISTÓRICA: SEDE CENTRAL ÉXITO
    def test_48_consultar_afluencia_historica_central_exito(self):
        """Sede Central consulta historial disponible; reporta periodo, sede, fuente, límites y no asume cero."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular: suficiencia -> 82 fechas, 1024 franjas, 32779 ingresos, min=2026-04-10, max=2026-09-28
            mock_cursor.fetchone.return_value = (82, 1024, 32779, date(2026, 4, 10), date(2026, 9, 28))

            # Simular: filas rango -> 3 franjas observadas (una a las 10h, una a las 14h, una a las 20h con franja parcial)
            mock_cursor.fetchall.return_value = [
                (date(2026, 5, 4), 10, 45, 0),
                (date(2026, 5, 4), 14, 60, 0),
                (date(2026, 5, 4), 20, 15, 1)  # Franja parcial a las 20h
            ]

            res = consultar_afluencia_historica(
                fecha_inicio="2026-05-01",
                fecha_fin="2026-05-31",
                sede="Central",
                rol="SuperAdmin"
            )

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["sede"], "Central")
            self.assertEqual(res["periodo"]["fecha_inicio"], "2026-05-01")
            self.assertEqual(res["periodo"]["fecha_fin"], "2026-05-31")
            self.assertIn("AfluenciaHistoricaAgregada", res["fuente"])
            self.assertIn("02_ingresos_reales_actual.csv", res["fuente"])

            # Límites de cobertura
            limites = res["limites_cobertura"]
            self.assertEqual(limites["sedes_con_historial_suficiente"], ["Central"])
            self.assertIn("Tarma", limites["sedes_con_datos_insuficientes"])
            self.assertIn("45 minutos", limites["franja_20h_parcial"])

            # Métricas calculadas
            metricas = res["metricas"]
            self.assertEqual(metricas["total_ingresos"], 120)
            self.assertEqual(metricas["franjas_observadas"], 3)
            self.assertEqual(metricas["dias_con_registros"], 1)
            self.assertEqual(metricas["franjas_parciales_20h"], 1)
            self.assertEqual(metricas["ingresos_franjas_parciales_20h"], 15)

            # Verificación de que las franjas no observadas NO se interpretan como cero
            no_obs = res["interpretacion_franjas_no_observadas"]
            self.assertFalse(no_obs["asume_cero"])
            self.assertIn("NO se asumen como cero visitas", no_obs["aviso"])
            self.assertIn("NO se interpretan automáticamente como cero ingresos", res["mensaje"])

    # 49. CONSULTA HISTÓRICA: TARMA DEVUELVE DATOS INSUFICIENTES
    def test_49_consultar_afluencia_historica_tarma_datos_insuficientes(self):
        """Sede Tarma cuenta con 1 fecha operativa (< 15) y debe lanzar ValueError de datos insuficientes."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Tarma solo tiene 1 fecha en la tabla histórica
            mock_cursor.fetchone.return_value = (1, 1, 1, date(2026, 4, 10), date(2026, 4, 10))

            with self.assertRaises(ValueError) as ctx_err:
                consultar_afluencia_historica(
                    fecha_inicio="2026-05-01",
                    fecha_fin="2026-05-31",
                    sede="Tarma",
                    rol="SuperAdmin"
                )

            msg = str(ctx_err.exception)
            self.assertIn("Historial insuficiente", msg)
            self.assertIn("Tarma", msg)
            self.assertIn("1 fecha(s)", msg)
            self.assertIn("mínimo configurado de 15 fechas", msg)
            self.assertIn("No se completa con registros de prueba", msg)

    # 50. CONSULTA HISTÓRICA: OTRAS SEDES DEVUELVEN DATOS INSUFICIENTES
    def test_50_consultar_afluencia_historica_otras_sedes_datos_insuficientes(self):
        """Sedes sin datos (La Merced, Oxapampa, Paucartambo, Yanahuanca) lanzan ValueError de datos insuficientes."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # 0 fechas para La Merced
            mock_cursor.fetchone.return_value = (0, 0, 0, None, None)

            with self.assertRaises(ValueError) as ctx_err:
                consultar_afluencia_historica(
                    fecha_inicio="2026-05-01",
                    fecha_fin="2026-05-31",
                    sede="La Merced",
                    rol="SuperAdmin"
                )

            msg = str(ctx_err.exception)
            self.assertIn("Historial insuficiente", msg)
            self.assertIn("La Merced", msg)
            self.assertIn("0 fecha(s)", msg)

    # 51. CONSULTA HISTÓRICA: VALIDACIONES DE PARÁMETROS
    def test_51_consultar_afluencia_historica_validaciones_parametros(self):
        """Valida fechas invertidas, formato incorrecto, rango excesivo y sede inválida."""
        # Fechas invertidas
        with self.assertRaises(ValueError) as err_inv:
            consultar_afluencia_historica("2026-06-01", "2026-05-01", sede="Central", rol="SuperAdmin")
        self.assertIn("anterior o igual", str(err_inv.exception))

        # Formato de fecha inválido
        with self.assertRaises(ValueError):
            consultar_afluencia_historica("fecha-erronea", "2026-05-01", sede="Central", rol="SuperAdmin")

        # Rango mayor a 366 días
        with self.assertRaises(ValueError) as err_rango:
            consultar_afluencia_historica("2025-01-01", "2026-06-01", sede="Central", rol="SuperAdmin")
        self.assertIn("366 días", str(err_rango.exception))

        # Sede inválida
        with self.assertRaises(ValueError) as err_sede:
            consultar_afluencia_historica("2026-05-01", "2026-05-31", sede="SedeInexistente", rol="SuperAdmin")
        self.assertIn("no es válida", str(err_sede.exception))

    # 52. CONSULTA HISTÓRICA: SUPERVISOR FORZADO A SU SEDE PERMITIDA
    def test_52_consultar_afluencia_historica_supervisor_sede_forzada(self):
        """Supervisor con sede_permitida='Central' se fuerza a Central aunque intente consultar otra."""
        # Supervisor sin sede_permitida debe lanzar ValueError
        with self.assertRaises(ValueError):
            consultar_afluencia_historica("2026-05-01", "2026-05-31", sede="Tarma", rol="Supervisor", sede_permitida=None)

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchone.return_value = (82, 1024, 32779, date(2026, 4, 10), date(2026, 9, 28))
            mock_cursor.fetchall.return_value = []

            res = consultar_afluencia_historica(
                "2026-05-01", "2026-05-31",
                sede="Tarma",
                rol="Supervisor",
                sede_permitida="Central"
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["sede"], "Central")
            sql_ejecutado, params = mock_cursor.execute.call_args_list[0][0]
            self.assertIn("Central", params)

    # 53. CONTEO DE ALUMNO: EXCLUYE PRUEBAS Y NO ATRIBUYE ALUMNOS A CONTEOS AGREGADOS
    def test_53_contar_ingresos_alumno_sin_visitas_reales_excluye_pruebas_y_no_atribuye_agregados(self):
        """Cuando total_reales=0, comunica que aún no hay visitas reales, excluye 38 pruebas y no atribuye agregados."""
        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular: 0 visitas reales para el alumno; sistema contiene 38 pruebas manuales y 0 reales
            mock_cursor.fetchone.side_effect = [
                (0, None, None, 0, "Ingeniería de Sistemas"),
                (datetime(2026, 4, 10), datetime(2026, 9, 28), 38, 38, 0)
            ]

            res = contar_ingresos_alumno_por_codigo("2021123456", rol="SuperAdmin")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_ingresos"], 0)
            self.assertIn("aún no hay visitas individuales reales registradas en el sistema", res["mensaje"])
            self.assertIn("excluyeron 38 registros manuales de prueba", res["mensaje"])
            self.assertIn("No se generan registros ni se atribuyen alumnos a los conteos agregados", res["mensaje"])
            self.assertIn("RegistroIngresos", res["fuente"])
            self.assertEqual(res["cobertura_sistema"]["total_pruebas_excluidas"], 38)
            self.assertEqual(res["cobertura_sistema"]["total_visitas_reales"], 0)

    # 54. DETECCIÓN Y VALIDACIÓN: RANGO EN ESPAÑOL (ENTRE EL 1 DE JUNIO Y EL 31 DE JULIO DE 2026)
    def test_54_extraer_fechas_texto_espanol_rango_completo(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol, resolver_expresion_temporal
        texto = "¿Cuántos ingresos de alumnos hubo entre el 1 de junio y el 31 de julio de 2026?"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res["fecha_inicio"], "2026-06-01")
        self.assertEqual(res["fecha_fin"], "2026-07-31")
        self.assertEqual(res["tipo_periodo"], "rango")

        # Confirmar también a través de resolver_expresion_temporal
        res_temp = resolver_expresion_temporal(texto)
        self.assertEqual(res_temp["fecha_inicio"], "2026-06-01")
        self.assertEqual(res_temp["fecha_fin"], "2026-07-31")

    # 55. DETECCIÓN Y VALIDACIÓN: RANGO DENTRO DEL MISMO MES
    def test_55_extraer_fechas_texto_espanol_mismo_mes(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "del 1 al 15 de julio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res["fecha_inicio"], "2026-07-01")
        self.assertEqual(res["fecha_fin"], "2026-07-15")

    # 56. DETECCIÓN Y VALIDACIÓN: MES COMPLETO EN ESPAÑOL
    def test_56_extraer_fechas_texto_espanol_mes_completo(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "en junio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res["fecha_inicio"], "2026-06-01")
        self.assertEqual(res["fecha_fin"], "2026-06-30")
        self.assertEqual(res["tipo_periodo"], "mes_completo")

    # 57. DETECCIÓN Y VALIDACIÓN: RANGO DE MESES COMPLETOS
    def test_57_extraer_fechas_texto_espanol_rango_meses(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "entre junio y julio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res["fecha_inicio"], "2026-06-01")
        self.assertEqual(res["fecha_fin"], "2026-07-31")

    # 58. DETECCIÓN Y VALIDACIÓN: FECHA PUNTUAL EN ESPAÑOL
    def test_58_extraer_fechas_texto_espanol_fecha_puntual(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "el 15 de julio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res["fecha"], "2026-07-15")
        self.assertEqual(res["fecha_inicio"], "2026-07-15")
        self.assertEqual(res["fecha_fin"], "2026-07-15")

    # 59. DETECCIÓN Y VALIDACIÓN: FECHA INVÁLIDA (31 DE JUNIO) RECHAZADA
    def test_59_extraer_fechas_texto_espanol_fecha_invalida(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "entre el 1 de junio y el 31 de junio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("error_temporal"), "fecha_invalida")
        self.assertIn("31 de junio", res.get("mensaje_error"))

    # 60. DETECCIÓN Y VALIDACIÓN: FECHA AMBIGUA (MES SIN AÑO)
    def test_60_extraer_fechas_texto_espanol_fecha_ambigua(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        res_mes = extraer_fechas_texto_espanol("¿Cuántos ingresos hubo en junio?")
        self.assertIsNotNone(res_mes)
        self.assertEqual(res_mes.get("error_temporal"), "fecha_ambigua")
        self.assertIn("junio", res_mes.get("mensaje_error"))

        res_rango = extraer_fechas_texto_espanol("del 1 al 15 de julio")
        self.assertIsNotNone(res_rango)
        self.assertEqual(res_rango.get("error_temporal"), "fecha_ambigua")

    # 61. DETECCIÓN Y VALIDACIÓN: RANGO INVERTIDO RECHAZADO
    def test_61_extraer_fechas_texto_espanol_rango_invertido(self):
        from llm.services.motor_intenciones import extraer_fechas_texto_espanol
        texto = "del 31 de julio al 1 de junio de 2026"
        res = extraer_fechas_texto_espanol(texto)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("error_temporal"), "rango_invalido")


if __name__ == "__main__":
    unittest.main()
