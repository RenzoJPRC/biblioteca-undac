import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, date

from llm.services.coordinador_asistente import procesar_pregunta


class TestCoordinadorAsistente(unittest.TestCase):

    def setUp(self):
        self._env_patch = patch.dict(os.environ, {"LLM_ACTIVADO": "false"})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    # 1. PREGUNTA VACÍA
    def test_01_pregunta_vacia(self):
        res = procesar_pregunta("", rol="SuperAdmin")
        self.assertEqual(res["status"], "error")
        self.assertIn("obligatoria", res["mensaje"])

        res_spaces = procesar_pregunta("   ", rol="SuperAdmin")
        self.assertEqual(res_spaces["status"], "error")

    # 2. PREGUNTA MAYOR DE 500 CARACTERES
    def test_02_pregunta_mayor_500_caracteres(self):
        pregunta_larga = "hola " * 120
        res = procesar_pregunta(pregunta_larga, rol="SuperAdmin")
        self.assertEqual(res["status"], "error")
        self.assertIn("500", res["mensaje"])

    # 3. ROL NONE O DESCONOCIDO
    def test_03_rol_none_o_desconocido(self):
        res1 = procesar_pregunta("Resumen de ingresos", rol=None)
        self.assertEqual(res1["status"], "error")
        self.assertIn("denegado", res1["mensaje"].lower())

        res2 = procesar_pregunta("Resumen de ingresos", rol="AdministradorFalso")
        self.assertEqual(res2["status"], "error")
        self.assertIn("denegado", res2["mensaje"].lower())

    # 4. CONSULTOR BLOQUEADO EN CONSULTA PERSONAL
    @patch("llm.services.coordinador_asistente.clasificar_intencion")
    def test_04_consultor_bloqueado_en_consulta_personal(self, mock_intencion):
        mock_intencion.return_value = {
            "intencion": "historial_persona",
            "parametros": {"texto_persona": "Juan Perez"},
            "origen": "regla_local"
        }
        res = procesar_pregunta("Historial de Juan Perez", rol="Consultor")
        self.assertEqual(res["status"], "error")
        self.assertIn("Consultor", res["mensaje"])

    # 5. PERSONA SIN COINCIDENCIAS
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_05_persona_sin_coincidencias(self, mock_buscar):
        mock_buscar.return_value = []
        res = procesar_pregunta("¿Cuántas veces ingresó Inexistente Persona?", rol="SuperAdmin")
        self.assertEqual(res["status"], "success")
        self.assertIn("No se encontró", res["mensaje"])
        self.assertEqual(res["datos"]["coincidencias"], [])

    # 6. PERSONA CON UNA COINCIDENCIA
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_06_persona_con_una_coincidencia(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Perez", "codigo_mascarado": "20******00"}
        ]
        mock_historial.return_value = {
            "status": "success",
            "tipo_persona": "Alumno",
            "persona_id": 1,
            "total_ingresos": 2,
            "registros": [
                {"registro_id": 10, "fecha_hora": "2026-10-01T10:00:00", "sede": "Central"}
            ]
        }
        res = procesar_pregunta("¿Cuántas veces ingresó Juan Perez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "success")
        self.assertIn("2 ingresos", res["mensaje"])
        self.assertFalse(res["requiere_aclaracion"])

    # 7. PERSONA CON VARIAS COINCIDENCIAS REQUIERE ACLARACIÓN
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_07_persona_varias_coincidencias_requiere_aclaracion(self, mock_buscar):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Perez Ramos", "codigo_mascarado": "20******01"},
            {"tipo_persona": "Docente", "persona_id": 5, "nombre": "Juan Perez Gomez", "codigo_mascarado": None}
        ]
        res = procesar_pregunta("¿Cuántas veces ingresó Juan Perez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "requires_input")
        self.assertTrue(res["requiere_aclaracion"])
        self.assertEqual(len(res["opciones"]), 2)

    # 8. SELECCIÓN VÁLIDA DE CANDIDATO
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_08_seleccion_valida_candidato(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Perez Ramos", "codigo_mascarado": "20******01"},
            {"tipo_persona": "Docente", "persona_id": 5, "nombre": "Juan Perez Gomez", "codigo_mascarado": None}
        ]
        mock_historial.return_value = {
            "status": "success",
            "total_ingresos": 1,
            "registros": [{"registro_id": 12, "fecha_hora": "2026-10-02T11:00:00", "sede": "Central"}]
        }
        seleccion = {"tipo_persona": "Docente", "persona_id": 5}
        res = procesar_pregunta("¿Cuántas veces ingresó Juan Perez?", rol="SuperAdmin", seleccion_persona=seleccion)
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["requiere_aclaracion"])
        mock_historial.assert_called_once_with(tipo_persona="Docente", persona_id=5, rol="SuperAdmin", sede_permitida=None)

    # 9. SELECCIÓN MANIPULADA O INEXISTENTE RECHAZADA
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_09_seleccion_manipulada_rechazada(self, mock_buscar):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Perez Ramos", "codigo_mascarado": "20******01"}
        ]
        seleccion_falsa = {"tipo_persona": "SuperAdmin", "persona_id": 9999}
        res = procesar_pregunta("¿Cuántas veces ingresó Juan Perez?", rol="SuperAdmin", seleccion_persona=seleccion_falsa)
        self.assertEqual(res["status"], "error")
        self.assertIn("no pertenece a los candidatos válidos", res["mensaje"])

    # 10. HISTORIAL VACÍO
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_10_historial_vacio(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Maria Lopez", "codigo_mascarado": "20******02"}
        ]
        mock_historial.return_value = {
            "status": "success",
            "total_ingresos": 0,
            "registros": []
        }
        res = procesar_pregunta("¿Cuántas veces ingresó Maria Lopez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "success")
        self.assertIn("no registra ningún ingreso", res["mensaje"])

    # 11. ÚLTIMO INGRESO CORRECTO
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_11_ultimo_ingreso_correcto(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Maria Lopez", "codigo_mascarado": "20******02"}
        ]
        mock_historial.return_value = {
            "status": "success",
            "total_ingresos": 3,
            "registros": [
                {"registro_id": 99, "fecha_hora": "2026-10-05T14:30:00", "sede": "Central"}
            ]
        }
        res = procesar_pregunta("¿Cuándo fue el último ingreso de Maria Lopez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "success")
        self.assertIn("2026-10-05T14:30:00", res["mensaje"])

    # 12. RESUMEN AGREGADO PARA CONSULTOR
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_12_resumen_agregado_para_consultor(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "total_ingresos": 150
        }
        res = procesar_pregunta("Resumen de ingresos del mes pasado", rol="Consultor", fecha_inicio="2026-09-01", fecha_fin="2026-09-30")
        self.assertEqual(res["status"], "success")
        self.assertIn("150 ingresos", res["mensaje"])

    # 13. SUPERVISOR FORZADO A SU SEDE EN RESUMEN
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_13_supervisor_forzado_sede_en_resumen(self, mock_resumen):
        mock_resumen.return_value = {"status": "success", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30", "total_ingresos": 40}
        res = procesar_pregunta("Resumen de ingresos", rol="Supervisor", sede_permitida="Tarma", fecha_inicio="2026-09-01", fecha_fin="2026-09-30")
        self.assertEqual(res["status"], "success")
        mock_resumen.assert_called_once_with(fecha_inicio="2026-09-01", fecha_fin="2026-09-30", rol="Supervisor", sede_permitida="Tarma")

    # 14. HORAS DE MAYOR AFLUENCIA
    @patch("llm.services.coordinador_asistente.obtener_horas_mayor_afluencia")
    def test_14_horas_mayor_afluencia(self, mock_horas):
        mock_horas.return_value = {
            "status": "success",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "horas_top": [{"hora": 10, "hora_formato": "10:00 - 11:00", "total_ingresos": 50}]
        }
        res = procesar_pregunta("Horas de mayor afluencia", rol="SuperAdmin", fecha_inicio="2026-09-01", fecha_fin="2026-09-30")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["origen"], "consulta_segura")

    # 15. PREDICCIÓN CONECTADA CON SERVICIO ML USANDO MOCKS
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_15_prediccion_conectada_ml_mocks(self, mock_ml):
        mock_ml.return_value = {
            "status": "success",
            "prediccion": {"nivel": "Medio", "probabilidades": {"Bajo": 0.2, "Medio": 0.7, "Alto": 0.1}},
            "advertencia": "Modelo entrenado con datos históricos"
        }
        res = procesar_pregunta("Generar predicción de afluencia", rol="SuperAdmin", fecha="2026-10-15", hora=10, sede="Central")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["origen"], "machine_learning")
        self.assertIn("Medio", res["mensaje"])

    # 16. SUPERVISOR FORZADO A SU SEDE EN PREDICCIÓN
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_16_supervisor_forzado_sede_en_prediccion(self, mock_ml):
        mock_ml.return_value = {
            "status": "success",
            "prediccion": {"nivel": "Alto"},
            "advertencia": "Advertencia simulada"
        }
        res = procesar_pregunta("Predicción de afluencia para mañana", rol="Supervisor", sede_permitida="Tarma", fecha="2026-10-15", hora=11, sede="Central")
        self.assertEqual(res["status"], "success")
        mock_ml.assert_called_once_with(fecha="2026-10-15", hora=11, sede="Tarma")

    # 17. PARÁMETROS FALTANTES SIN EJECUTAR BD NI ML
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_17_parametros_faltantes_sin_ejecutar_bd_ni_ml(self, mock_resumen, mock_ml):
        res = procesar_pregunta("Generar predicción de afluencia", rol="SuperAdmin")
        self.assertEqual(res["status"], "requires_input")
        self.assertTrue(res["requiere_parametros"])
        mock_ml.assert_not_called()
        mock_resumen.assert_not_called()

    # 18. INTENCIÓN DESCONOCIDA SIN EJECUTAR BD
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_18_intencion_desconocida_sin_ejecutar_bd(self, mock_buscar):
        res = procesar_pregunta("¿Cuál es la receta del pastel de manzana?", rol="SuperAdmin")
        self.assertEqual(res["status"], "requires_input")
        self.assertEqual(res["intencion"], "intencion_desconocida")
        self.assertTrue(len(res["opciones"]) > 0)
        mock_buscar.assert_not_called()

    # 19. AUSENCIA DE DNI, CORREO Y TELÉFONO EN TODAS LAS RESPUESTAS
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_19_ausencia_datos_sensibles(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Test User", "codigo_mascarado": "20******99"}
        ]
        mock_historial.return_value = {
            "status": "success",
            "total_ingresos": 1,
            "registros": [{"registro_id": 1, "fecha_hora": "2026-10-01T10:00:00", "sede": "Central"}]
        }
        res = procesar_pregunta("¿Cuántas veces ingresó Test User?", rol="SuperAdmin")
        texto_res = str(res)
        self.assertNotIn("dni", texto_res.lower())
        self.assertNotIn("email", texto_res.lower())
        self.assertNotIn("telefono", texto_res.lower())

    # 20. ERROR DE CONEXIÓN CONVERTIDO EN RESPUESTA SEGURA
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_20_error_conexion_respuesta_segura(self, mock_buscar):
        mock_buscar.side_effect = ConnectionError("No se pudo conectar a SQL Server")
        res = procesar_pregunta("¿Cuántas veces ingresó Juan Perez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "error")
        self.assertIn("error", res["mensaje"].lower())
        self.assertNotIn("pyodbc", res["mensaje"].lower())

    # 21. CONFIRMAR QUE EL COORDINADOR NO CONTIENE SQL
    def test_21_coordinador_sin_sql(self):
        import inspect
        import llm.services.coordinador_asistente as mod_coord
        src = inspect.getsource(mod_coord)
        self.assertNotIn("SELECT", src)
        self.assertNotIn("INSERT", src)
        self.assertNotIn("UPDATE", src)
        self.assertNotIn("DELETE", src)
        self.assertNotIn("FROM RegistroIngresos", src)

    # 22. CONFIRMAR QUE NO REALIZA LLAMADAS HTTP EXTERNAS
    def test_22_sin_llamadas_http_externas(self):
        import inspect
        import llm.services.coordinador_asistente as mod_coord
        src = inspect.getsource(mod_coord)
        self.assertNotIn("requests.", src)
        self.assertNotIn("urllib.", src)
        self.assertNotIn("httpx.", src)

    # 23. SUPERVISOR NO RECIBE CANDIDATO SIN INGRESOS EN SU SEDE
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_23_supervisor_no_recibe_candidato_sin_ingresos_en_su_sede(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan ConIngresos", "codigo_mascarado": "20******01"},
            {"tipo_persona": "Alumno", "persona_id": 2, "nombre": "Juan SinIngresos", "codigo_mascarado": "20******02"}
        ]

        def side_effect_hist(tipo_persona, persona_id, rol, sede_permitida, limite=1):
            if persona_id == 1:
                return {"status": "success", "total_ingresos": 3, "registros": [{"registro_id": 1, "fecha_hora": "2026-10-01", "sede": "Tarma"}]}
            return {"status": "success", "total_ingresos": 0, "registros": []}

        mock_historial.side_effect = side_effect_hist

        res = procesar_pregunta("¿Cuántas veces ingresó Juan?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "success")
        self.assertIn("Juan ConIngresos", res["mensaje"])
        self.assertNotIn("Juan SinIngresos", str(res))

    # 24. SUPERVISOR SÍ RECIBE CANDIDATO CON INGRESOS EN SU SEDE
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_24_supervisor_recibe_candidato_con_ingresos_en_su_sede(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 10, "nombre": "Pedro Tarma", "codigo_mascarado": "20******10"}
        ]
        mock_historial.return_value = {"status": "success", "total_ingresos": 5, "registros": [{"registro_id": 5, "fecha_hora": "2026-10-02", "sede": "Tarma"}]}

        res = procesar_pregunta("¿Cuántas veces ingresó Pedro?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "success")
        self.assertIn("Pedro Tarma", res["mensaje"])

    # 25. SUPERADMIN CONSERVA BÚSQUEDA GLOBAL
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_25_superadmin_conserva_busqueda_global(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Uno", "codigo_mascarado": "20******01"},
            {"tipo_persona": "Docente", "persona_id": 2, "nombre": "Juan Dos", "codigo_mascarado": None}
        ]
        res = procesar_pregunta("¿Cuántas veces ingresó Juan?", rol="SuperAdmin")
        self.assertEqual(res["status"], "requires_input")
        self.assertEqual(len(res["opciones"]), 2)

    # 26. PERSONA_ID NO APARECE EN MENSAJE
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_26_persona_id_no_aparece_en_mensaje(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 987654, "nombre": "Carlos Perez", "codigo_mascarado": "20******55"}
        ]
        mock_historial.return_value = {"status": "success", "total_ingresos": 1, "registros": [{"registro_id": 1, "fecha_hora": "2026-10-01", "sede": "Central"}]}

        res = procesar_pregunta("¿Cuántas veces ingresó Carlos Perez?", rol="SuperAdmin")
        self.assertNotIn("987654", res["mensaje"])

    # 27. ERRORES TÉCNICOS NO SE CONFUNDEN CON AUSENCIA DE PERSONA
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_27_errores_tecnicos_no_se_confunden_con_ausencia_persona(self, mock_buscar):
        mock_buscar.side_effect = TimeoutError("Timeout en la consulta SQL")

        res = procesar_pregunta("¿Cuántas veces ingresó Carlos Perez?", rol="SuperAdmin")
        self.assertEqual(res["status"], "error")
        self.assertNotIn("No se encontró ninguna persona", res["mensaje"])

    # 28. ADVERTENCIA ML SE CONSERVA EXACTAMENTE
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_28_advertencia_ml_se_conserva_exactamente(self, mock_ml):
        mock_ml.return_value = {
            "status": "success",
            "prediccion": {"nivel": "Bajo", "probabilidades": {"Bajo": 0.9}},
            "advertencia": "ADVERTENCIA_OFICIAL_MODELO_ML_VERBATIM"
        }
        res = procesar_pregunta("Generar predicción de afluencia", rol="SuperAdmin", fecha="2026-10-15", hora=10, sede="Central")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["datos"]["advertencia"], "ADVERTENCIA_OFICIAL_MODELO_ML_VERBATIM")

    # 29. SUPERVISOR ERROR DURANTE FILTRADO NO SE CONVIERTE EN PERSONA NO ENCONTRADA
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_29_supervisor_error_durante_filtrado_no_se_convierte_en_persona_no_encontrada(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Valido", "codigo_mascarado": "20******01"}
        ]
        mock_historial.side_effect = ConnectionError("detalle sensible simulado")

        res = procesar_pregunta("¿Cuántas veces ingresó Juan?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "error")
        self.assertNotIn("no se encontró", res["mensaje"].lower())
        self.assertNotIn("detalle sensible simulado", res["mensaje"])

    # 30. SUPERVISOR ERROR TIMEOUT DURANTE FILTRADO
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_30_supervisor_error_timeout_durante_filtrado(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Valido", "codigo_mascarado": "20******01"}
        ]
        mock_historial.side_effect = TimeoutError("Query timeout expired")

        res = procesar_pregunta("¿Cuántas veces ingresó Juan?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "error")
        self.assertNotIn("no se encontró", res["mensaje"].lower())
        self.assertNotIn("query timeout expired", res["mensaje"].lower())

    # 31. SUPERVISOR ERROR PERMISOS DURANTE FILTRADO
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    @patch("llm.services.coordinador_asistente.buscar_personas")
    def test_31_supervisor_error_permisos_durante_filtrado(self, mock_buscar, mock_historial):
        mock_buscar.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 1, "nombre": "Juan Valido", "codigo_mascarado": "20******01"}
        ]
        mock_historial.side_effect = PermissionError("SELECT permission denied")

        res = procesar_pregunta("¿Cuántas veces ingresó Juan?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "error")
        self.assertNotIn("no se encontró", res["mensaje"].lower())
        self.assertNotIn("permission denied", res["mensaje"].lower())

    # 32. PREGUNTA "¿CUÁL SERÁ EL NIVEL DE AFLUENCIA?" SE RECONOCE COMO PREDICCIÓN Y PIDE PARÁMETROS
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_32_cual_sera_el_nivel_de_afluencia_requiere_parametros_superadmin(self, mock_ml):
        res = procesar_pregunta("¿Cuál será el nivel de afluencia?", rol="SuperAdmin")
        self.assertEqual(res["status"], "requires_input")
        self.assertEqual(res["intencion"], "prediccion_afluencia")
        self.assertTrue(res["requiere_parametros"])
        self.assertIn("fecha", res["parametros_faltantes"])
        self.assertIn("hora", res["parametros_faltantes"])
        self.assertIn("sede", res["parametros_faltantes"])
        mock_ml.assert_not_called()

    # 33. SUPERVISOR TIENE SEDE FIJA, SOLO LE FALTAN FECHA Y HORA
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_33_cual_sera_el_nivel_de_afluencia_supervisor_sin_sede_en_faltantes(self, mock_ml):
        res = procesar_pregunta("¿Cuál será el nivel de afluencia?", rol="Supervisor", sede_permitida="Tarma")
        self.assertEqual(res["status"], "requires_input")
        self.assertEqual(res["intencion"], "prediccion_afluencia")
        self.assertTrue(res["requiere_parametros"])
        self.assertEqual(res["sede_aplicada"], "Tarma")
        self.assertIn("fecha", res["parametros_faltantes"])
        self.assertIn("hora", res["parametros_faltantes"])
        self.assertNotIn("sede", res["parametros_faltantes"])
        mock_ml.assert_not_called()

    # 34. PREDICCIÓN CON PARÁMETROS COMPLETOS LLAMA A ML Y RETORNA SUCCESS
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_34_prediccion_con_parametros_completos_llama_ml(self, mock_ml):
        mock_ml.return_value = {
            "status": "success",
            "prediccion": {"nivel": "Medio", "probabilidades": {"Bajo": 0.2, "Medio": 0.7, "Alto": 0.1}},
            "advertencia": "Advertencia oficial del modelo"
        }
        res = procesar_pregunta(
            "¿Cuál será el nivel de afluencia?",
            rol="SuperAdmin",
            fecha="2026-10-15",
            hora=10,
            sede="Central"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "prediccion_afluencia")
        self.assertEqual(res["sede_aplicada"], "Central")
        self.assertIn("Nivel Medio", res["mensaje"])
        mock_ml.assert_called_once_with(fecha="2026-10-15", hora=10, sede="Central")

    # 35. CONSULTAS EN TIEMPO PASADO NO ACTIVAN PREDICCIÓN ML
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    @patch("llm.services.coordinador_asistente.obtener_horas_mayor_afluencia")
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_35_consultas_pasadas_no_activan_ml(self, mock_resumen, mock_horas, mock_ml):
        mock_horas.return_value = {"status": "success", "horas_top": []}
        mock_resumen.return_value = {"status": "success", "total_ingresos": 10}

        # 1. ¿Cuáles fueron las horas de mayor afluencia?
        r1 = procesar_pregunta("¿Cuáles fueron las horas de mayor afluencia?", rol="SuperAdmin", fecha_inicio="2026-03-01", fecha_fin="2026-03-10")
        self.assertEqual(r1["intencion"], "horas_mayor_afluencia")
        mock_horas.assert_called_once()
        mock_ml.assert_not_called()

        # 2. Resumen de afluencia del mes pasado
        r2 = procesar_pregunta("Resumen de afluencia del mes pasado", rol="SuperAdmin", fecha_inicio="2026-03-01", fecha_fin="2026-03-10")
        self.assertEqual(r2["intencion"], "resumen_ingresos")
        mock_resumen.assert_called_once()
        mock_ml.assert_not_called()

    # 36. SEGUIMIENTO CONVERSACIONAL: "¿Y ayer?" CONSERVA INTENCIÓN Y APLICA AYER
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_36_seguimiento_despues_de_resumen_aplica_ayer(self, mock_resumen):
        ref = date(2026, 10, 11)
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-10-10",
            "fecha_fin": "2026-10-10",
            "total_ingresos": 75
        }
        ctx_previo = {
            "intencion": "resumen_ingresos",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30"
        }
        res = procesar_pregunta(
            "¿Y ayer?",
            rol="SuperAdmin",
            contexto=ctx_previo,
            fecha_referencia=ref
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "resumen_ingresos")
        self.assertIn("75 ingresos", res["mensaje"])
        self.assertEqual(res["contexto"]["intencion"], "resumen_ingresos")
        self.assertEqual(res["contexto"]["fecha_inicio"], "2026-10-10")
        self.assertEqual(res["contexto"]["fecha_fin"], "2026-10-10")
        mock_resumen.assert_called_once_with(
            fecha_inicio="2026-10-10",
            fecha_fin="2026-10-10",
            rol="SuperAdmin",
            sede_permitida=None
        )

    # 37. VARIACIONES DE SEGUIMIENTO TEMPORAL (ayer, la semana pasada, el mes pasado, rango)
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_37_seguimiento_variaciones_temporales(self, mock_resumen):
        ref = date(2026, 10, 11)
        mock_resumen.return_value = {"status": "success", "total_ingresos": 20, "fecha_inicio": "2026-10-10", "fecha_fin": "2026-10-10"}
        ctx = {"intencion": "resumen_ingresos", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30"}

        # "ayer" a secas
        r1 = procesar_pregunta("ayer", rol="SuperAdmin", contexto=ctx, fecha_referencia=ref)
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r1["intencion"], "resumen_ingresos")

        # "¿ayer?"
        r2 = procesar_pregunta("¿ayer?", rol="SuperAdmin", contexto=ctx, fecha_referencia=ref)
        self.assertEqual(r2["status"], "success")

        # "¿Y hoy?"
        mock_resumen.return_value = {"status": "success", "total_ingresos": 15, "fecha_inicio": "2026-10-11", "fecha_fin": "2026-10-11"}
        r3 = procesar_pregunta("¿Y hoy?", rol="SuperAdmin", contexto=ctx, fecha_referencia=ref)
        self.assertEqual(r3["status"], "success")
        self.assertEqual(r3["contexto"]["fecha_inicio"], "2026-10-11")

        # "¿Y anteayer?"
        mock_resumen.return_value = {"status": "success", "total_ingresos": 30, "fecha_inicio": "2026-10-09", "fecha_fin": "2026-10-09"}
        r4 = procesar_pregunta("¿Y anteayer?", rol="SuperAdmin", contexto=ctx, fecha_referencia=ref)
        self.assertEqual(r4["status"], "success")
        self.assertEqual(r4["contexto"]["fecha_inicio"], "2026-10-09")

        # "¿Y del 2026-08-01 al 2026-08-15?"
        mock_resumen.return_value = {"status": "success", "total_ingresos": 80, "fecha_inicio": "2026-08-01", "fecha_fin": "2026-08-15"}
        r5 = procesar_pregunta("¿Y del 2026-08-01 al 2026-08-15?", rol="SuperAdmin", contexto=ctx, fecha_referencia=ref)
        self.assertEqual(r5["status"], "success")
        self.assertEqual(r5["contexto"]["fecha_inicio"], "2026-08-01")
        self.assertEqual(r5["contexto"]["fecha_fin"], "2026-08-15")

    # 38. SEGUIMIENTO TRAS HORAS DE MAYOR AFLUENCIA HEREDA HORAS_MAYOR_AFLUENCIA
    @patch("llm.services.coordinador_asistente.obtener_horas_mayor_afluencia")
    def test_38_seguimiento_despues_de_horas_mayor_afluencia(self, mock_horas):
        ref = date(2026, 10, 11)
        mock_horas.return_value = {
            "status": "success",
            "fecha_inicio": "2026-10-10",
            "fecha_fin": "2026-10-10",
            "horas_top": [{"hora": 11, "hora_formato": "11:00 - 12:00", "total_ingresos": 25}]
        }
        ctx_horas = {
            "intencion": "horas_mayor_afluencia",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30"
        }
        res = procesar_pregunta("¿Y ayer?", rol="SuperAdmin", contexto=ctx_horas, fecha_referencia=ref)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "horas_mayor_afluencia")
        self.assertEqual(res["contexto"]["intencion"], "horas_mayor_afluencia")
        self.assertEqual(res["contexto"]["fecha_inicio"], "2026-10-10")
        mock_horas.assert_called_once_with(
            fecha_inicio="2026-10-10",
            fecha_fin="2026-10-10",
            rol="SuperAdmin",
            sede_permitida=None,
            limite=5
        )

    # 39. SEGUIMIENTO SIN CONTEXTO PREVIO SOLICITA ACLARACIÓN
    def test_39_seguimiento_sin_contexto_previo_solicita_aclaracion(self):
        # Pregunta "¿Y ayer?" con contexto None
        r1 = procesar_pregunta("¿Y ayer?", rol="SuperAdmin", contexto=None)
        self.assertEqual(r1["status"], "requires_input")
        self.assertEqual(r1["intencion"], "intencion_desconocida")
        self.assertTrue(r1["requiere_aclaracion"])
        self.assertIn("No se encontró una consulta previa", r1["mensaje"])
        self.assertTrue(len(r1["opciones"]) > 0)
        self.assertEqual(r1["contexto"], {})

        # Pregunta "¿ayer?" con contexto vacío
        r2 = procesar_pregunta("¿ayer?", rol="SuperAdmin", contexto={})
        self.assertEqual(r2["status"], "requires_input")
        self.assertTrue(r2["requiere_aclaracion"])

    # 40. PREGUNTA DE SEGUIMIENTO AMBIGUA SOLICITA ACLARACIÓN
    def test_40_seguimiento_ambiguo_solicita_aclaracion(self):
        ctx = {"intencion": "resumen_ingresos", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-30"}

        casos_ambiguos = ["¿Y entonces?", "¿Y qué más?", "¿Y?", "¿Cómo?", "¿Y ahora?"]
        for preg in casos_ambiguos:
            res = procesar_pregunta(preg, rol="SuperAdmin", contexto=ctx)
            self.assertEqual(res["status"], "requires_input", f"Pregunta '{preg}' debía retornar requires_input")
            self.assertTrue(res["requiere_aclaracion"], f"Pregunta '{preg}' debía requerir aclaración")
            self.assertIn("ambigua", res["mensaje"].lower())
            self.assertTrue(len(res["opciones"]) > 0)

    # 41. AISLAMIENTO POR ROL Y SEDE: SUPERVISOR NUNCA PUEDE CONSULTAR OTRA SEDE
    @patch("llm.services.coordinador_asistente.obtener_resumen_ingresos")
    def test_41_supervisor_aislamiento_sede_en_seguimiento(self, mock_resumen):
        mock_resumen.return_value = {
            "status": "success",
            "fecha_inicio": "2026-10-10",
            "fecha_fin": "2026-10-10",
            "total_ingresos": 18
        }
        # Supervisor asignado a 'Tarma'
        # Contexto previo intenta inyectar sede 'Central'
        ctx_manipulado = {
            "intencion": "resumen_ingresos",
            "fecha_inicio": "2026-09-01",
            "fecha_fin": "2026-09-30",
            "sede": "Central"
        }

        # Supervisor pregunta "¿Y ayer en Yanahuanca?" o envía sede="Central"
        res = procesar_pregunta(
            "¿Y ayer en Yanahuanca?",
            rol="Supervisor",
            sede_permitida="Tarma",
            sede="Central",
            contexto=ctx_manipulado,
            fecha_referencia=date(2026, 10, 11)
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["sede_aplicada"], "Tarma")
        self.assertEqual(res["contexto"]["sede"], "Tarma")
        mock_resumen.assert_called_once_with(
            fecha_inicio="2026-10-10",
            fecha_fin="2026-10-10",
            rol="Supervisor",
            sede_permitida="Tarma"
        )

    # 42. PRIVACIDAD: CONTEXTO NO ALMACENA DATOS PERSONALES NI RESPUESTAS COMPLETAS
    @patch("llm.services.coordinador_asistente.buscar_personas")
    @patch("llm.services.coordinador_asistente.obtener_historial_persona")
    def test_42_contexto_no_almacena_datos_personales_ni_respuestas_completas(self, mock_hist, mock_busq):
        mock_busq.return_value = [
            {"tipo_persona": "Alumno", "persona_id": 101, "nombre": "Renzo Rojas Castillo", "codigo_mascarado": "20******56"}
        ]
        mock_hist.return_value = {
            "status": "success",
            "total_ingresos": 5,
            "registros": [{"registro_id": 1, "fecha_hora": "2026-10-01T10:00:00", "sede": "Central"}]
        }
        res = procesar_pregunta("¿Cuántas veces ingresó Renzo Rojas Castillo?", rol="SuperAdmin")
        self.assertEqual(res["status"], "success")
        ctx = res.get("contexto", {})

        # El contexto retornado debe estar estrictamente limpio de datos personales
        campos_prohibidos = [
            "nombre", "texto_persona", "dni", "persona_id", "id", "persona",
            "candidato", "opciones", "historial", "registros", "ultimo_registro",
            "respuesta", "mensaje", "datos"
        ]
        for c in campos_prohibidos:
            self.assertNotIn(c, ctx, f"Campo prohibido '{c}' encontrado en el contexto")

    # 43. CONTEO DE INGRESOS DE ALUMNO POR CÓDIGO
    @patch("llm.services.coordinador_asistente.contar_ingresos_alumno_por_codigo")
    def test_43_procesar_pregunta_conteo_ingresos_alumno(self, mock_conteo):
        mock_conteo.return_value = {
            "status": "success",
            "codigo_mascarado": "20******56",
            "total_ingresos": 4,
            "dias_con_visita": 3,
            "primer_ingreso": "2026-04-15T10:30:00",
            "ultimo_ingreso": "2026-05-20T16:45:00",
            "cobertura_sistema": {
                "fecha_minima": "2026-04-10",
                "fecha_maxima": "2026-09-28",
                "total_visitas_sistema": 38
            },
            "mensaje": "El alumno con código 20******56 registra 4 ingreso(s). Nota de cobertura: sistema cubre 2026-04-10 a 2026-09-28."
        }

        res = procesar_pregunta(
            "¿Cuántas veces ingresó el alumno con código 2021123456 del 2026-04-10 al 2026-05-30?",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "conteo_ingresos_alumno")
        self.assertIn("4 ingreso(s)", res["mensaje"])
        self.assertIn("2026-04-10 a 2026-09-28", res["mensaje"])
        mock_conteo.assert_called_once()

    # 44. CONTEO DE INGRESOS DE ALUMNO BLOQUEA ROL CONSULTOR
    def test_44_procesar_pregunta_conteo_ingresos_alumno_bloquea_consultor(self):
        res = procesar_pregunta(
            "¿Cuántas veces ingresó el alumno con código 2021123456?",
            rol="Consultor"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["codigo_http"], 403)
        self.assertEqual(res["categoria_error"], "permiso")
        self.assertIn("Acceso denegado", res["mensaje"])

    # 45. PREGUNTA REPRESENTATIVA: AFLUENCIA HISTÓRICA EN SEDE CENTRAL CON RANGO
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_45_pregunta_representativa_afluencia_historica_central(self, mock_afluencia):
        mock_afluencia.return_value = {
            "status": "success",
            "herramienta": "consulta_afluencia_historica",
            "periodo": {"fecha_inicio": "2026-05-01", "fecha_fin": "2026-05-31", "dias_calendario": 31},
            "sede": "Central",
            "fuente": "Tabla analítica AfluenciaHistoricaAgregada (procedencia: 02_ingresos_reales_actual.csv)",
            "limites_cobertura": {
                "fecha_minima_sistema": "2026-04-10",
                "fecha_maxima_sistema": "2026-09-28",
                "horario_operativo": "08:00 a 20:45 (lunes a viernes)",
                "franja_20h_parcial": "20:00 a 20:45 (45 minutos por apagado del servidor)",
                "sedes_con_historial_suficiente": ["Central"],
                "sedes_con_datos_insuficientes": ["Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
            },
            "metricas": {
                "total_ingresos": 7820,
                "franjas_observadas": 240,
                "dias_con_registros": 20,
                "promedio_por_dia_activo": 391.0,
                "promedio_por_franja_observada": 32.58,
                "franjas_parciales_20h": 20,
                "ingresos_franjas_parciales_20h": 410,
                "horas_top": []
            },
            "interpretacion_franjas_no_observadas": {
                "asume_cero": False,
                "aviso": "Las franjas no observadas corresponden a intervalos no registrados en el archivo fuente y NO se asumen como cero visitas."
            },
            "mensaje": "Consulta descriptiva de afluencia histórica para la sede Central del 2026-05-01 al 2026-05-31: Total de 7820 ingresos. Fuente: Tabla analítica AfluenciaHistoricaAgregada. Límites de cobertura: Registros disponibles entre 2026-04-10 y 2026-09-28. Aviso: Las franjas no observadas NO se interpretan automáticamente como cero ingresos."
        }

        res = procesar_pregunta(
            "Consulta descriptiva de afluencia histórica del 2026-05-01 al 2026-05-31 en sede Central",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "afluencia_historica")
        self.assertEqual(res["sede_aplicada"], "Central")
        self.assertEqual(res["periodo"]["fecha_inicio"], "2026-05-01")
        self.assertEqual(res["periodo"]["fecha_fin"], "2026-05-31")
        self.assertIn("AfluenciaHistoricaAgregada", res["fuente"])
        self.assertEqual(res["limites_cobertura"]["sedes_con_historial_suficiente"], ["Central"])
        self.assertFalse(res["datos"]["interpretacion_franjas_no_observadas"]["asume_cero"])
        mock_afluencia.assert_called_once_with(
            fecha_inicio="2026-05-01",
            fecha_fin="2026-05-31",
            sede="Central",
            rol="SuperAdmin",
            sede_permitida=None
        )

    # 46. PREGUNTA REPRESENTATIVA: AFLUENCIA HISTÓRICA EN TARMA DEVUELVE DATOS INSUFICIENTES
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_46_pregunta_representativa_afluencia_historica_tarma_insuficiente(self, mock_afluencia):
        mock_afluencia.side_effect = ValueError(
            "Historial insuficiente: La sede 'Tarma' cuenta con 1 fecha(s) operativa(s) registrada(s) "
            "en la base histórica analítica, no alcanzando el mínimo configurado de 15 fechas operativas "
            "para generar un análisis confiable. No se completa con registros de prueba de RegistroIngresos."
        )

        res = procesar_pregunta(
            "Consulta descriptiva de afluencia histórica en Tarma del 2026-05-01 al 2026-05-31",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "datos_insuficientes")
        self.assertEqual(res["codigo_http"], 400)
        self.assertEqual(res["datos"]["estado"], "datos_insuficientes")
        self.assertEqual(res["datos"]["sede"], "Tarma")
        self.assertIn("Historial insuficiente", res["mensaje"])
        self.assertIn("Tarma", res["mensaje"])

    # 47. PREGUNTA REPRESENTATIVA: AFLUENCIA HISTÓRICA EN LA MERCED DEVUELVE DATOS INSUFICIENTES
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_47_pregunta_representativa_afluencia_historica_la_merced_insuficiente(self, mock_afluencia):
        mock_afluencia.side_effect = ValueError(
            "Historial insuficiente: La sede 'La Merced' cuenta con 0 fecha(s) operativa(s) registrada(s) "
            "en la base histórica analítica, no alcanzando el mínimo configurado de 15 fechas operativas."
        )

        res = procesar_pregunta(
            "Consulta descriptiva de afluencia histórica en sede La Merced",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "datos_insuficientes")
        self.assertEqual(res["codigo_http"], 400)
        self.assertEqual(res["datos"]["estado"], "datos_insuficientes")

    # 48. PREGUNTA REPRESENTATIVA: COMPORTAMIENTO HISTÓRICO SIN FECHAS ASIGNA COBERTURA COMPLETA
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_48_pregunta_representativa_comportamiento_historico_default_fechas(self, mock_afluencia):
        mock_afluencia.return_value = {
            "status": "success",
            "periodo": {"fecha_inicio": "2026-04-10", "fecha_fin": "2026-09-28", "dias_calendario": 172},
            "sede": "Central",
            "fuente": "Tabla analítica AfluenciaHistoricaAgregada",
            "limites_cobertura": {"sedes_con_historial_suficiente": ["Central"]},
            "metricas": {"total_ingresos": 32779, "franjas_observadas": 1024},
            "interpretacion_franjas_no_observadas": {"asume_cero": False},
            "mensaje": "Comportamiento histórico sede Central."
        }

        res = procesar_pregunta(
            "¿Cuál es el comportamiento histórico de afluencia en Central?",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "afluencia_historica")
        self.assertEqual(res["periodo"]["fecha_inicio"], "2026-04-10")
        self.assertEqual(res["periodo"]["fecha_fin"], "2026-09-28")
        mock_afluencia.assert_called_once_with(
            fecha_inicio="2026-04-10",
            fecha_fin="2026-09-28",
            sede="Central",
            rol="SuperAdmin",
            sede_permitida=None
        )

    # 49. VALIDACIÓN DE FECHAS EN AFLUENCIA HISTÓRICA (FECHAS INVERTIDAS)
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_49_afluencia_historica_fechas_invertidas_error_validacion(self, mock_afluencia):
        mock_afluencia.side_effect = ValueError("La fecha de inicio debe ser anterior o igual a la fecha de fin.")

        res = procesar_pregunta(
            "Consulta descriptiva de afluencia histórica del 2026-06-01 al 2026-05-01 en sede Central",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "validacion")
        self.assertEqual(res["codigo_http"], 400)
        self.assertIn("anterior o igual", res["mensaje"])

    # 50. PREGUNTA REPRESENTATIVA: PRONÓSTICO DE AFLUENCIA EN CENTRAL ÉXITO
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_50_pregunta_representativa_pronostico_central_exito(self, mock_predecir):
        mock_predecir.return_value = {
            "status": "success",
            "periodo": {"fecha": "2026-10-15", "hora": 10},
            "sede": "Central",
            "fuente": "Tabla analítica AfluenciaHistoricaAgregada y modelo ML HistGradientBoosting (metadatos_modelo_afluencia.json)",
            "limites_cobertura": {
                "fecha_minima_sistema": "2026-04-10",
                "fecha_maxima_sistema": "2026-09-28",
                "horario_operativo": "08:00 a 20:45 (lunes a viernes)",
                "franja_20h_parcial": "20:00 a 20:45 (45 minutos por apagado del servidor)",
                "sedes_con_historial_suficiente": ["Central"],
                "sedes_con_datos_insuficientes": ["Tarma", "La Merced", "Oxapampa", "Paucartambo", "Yanahuanca"]
            },
            "prediccion": {
                "nivel": "Medio",
                "probabilidades": {"Alto": 0.1, "Bajo": 0.2, "Medio": 0.7},
                "version_modelo": "1.0.0",
                "calidad_minima_historial": True,
                "confiable": True,
                "modo_demostracion": False,
                "es_franja_parcial": False,
                "duracion_minutos": 60
            },
            "consulta": {"fecha": "2026-10-15", "hora": 10, "sede": "Central", "ultima_fecha_disponible": "2026-09-28"},
            "variables_modelo": {},
            "advertencia": ""
        }

        res = procesar_pregunta(
            "¿Cuál será el nivel de afluencia en Central para el 2026-10-15 a las 10:00?",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "prediccion_afluencia")
        self.assertEqual(res["sede_aplicada"], "Central")
        self.assertEqual(res["periodo"]["fecha"], "2026-10-15")
        self.assertEqual(res["periodo"]["hora"], 10)
        self.assertIn("AfluenciaHistoricaAgregada", res["fuente"])
        self.assertEqual(res["limites_cobertura"]["sedes_con_historial_suficiente"], ["Central"])
        self.assertIn("Nivel Medio", res["mensaje"])
        self.assertIn("Fuente:", res["mensaje"])
        self.assertIn("Límites de cobertura:", res["mensaje"])

    # 51. PREGUNTA REPRESENTATIVA: PRONÓSTICO EN TARMA DEVUELVE DATOS INSUFICIENTES
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_51_pregunta_representativa_pronostico_tarma_datos_insuficientes(self, mock_predecir):
        mock_predecir.side_effect = ValueError(
            "Historial insuficiente: La sede 'Tarma' cuenta con 1 fecha(s) operativa(s) registrada(s) "
            "en la base histórica analítica, no alcanzando el mínimo configurado de 15 fechas operativas "
            "para generar un pronóstico confiable. No se completa con registros de prueba de RegistroIngresos."
        )

        res = procesar_pregunta(
            "Pronóstico de afluencia en Tarma para el 2026-10-15 a las 10:00",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "datos_insuficientes")
        self.assertEqual(res["codigo_http"], 400)
        self.assertEqual(res["datos"]["estado"], "datos_insuficientes")
        self.assertEqual(res["datos"]["sede"], "Tarma")
        self.assertIn("Historial insuficiente", res["mensaje"])

    # 52. PREGUNTA REPRESENTATIVA: PRONÓSTICO EN OTRA SEDE (OXAPAMPA) DEVUELVE DATOS INSUFICIENTES
    @patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd")
    def test_52_pregunta_representativa_pronostico_oxapampa_datos_insuficientes(self, mock_predecir):
        mock_predecir.side_effect = ValueError(
            "Historial insuficiente: La sede 'Oxapampa' no cuenta con datos históricos suficientes "
            "para generar un pronóstico confiable (requiere al menos 15 fechas operativas registradas)."
        )

        res = procesar_pregunta(
            "¿Cuál será el pronóstico de afluencia en Oxapampa para el 2026-10-15 a las 14:00?",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "datos_insuficientes")
        self.assertEqual(res["codigo_http"], 400)
        self.assertEqual(res["datos"]["estado"], "datos_insuficientes")
        self.assertEqual(res["datos"]["sede"], "Oxapampa")

    # 53. VALIDACIÓN DE SEDE INVÁLIDA EN PRONÓSTICO
    def test_53_pronostico_sede_invalida_error_validacion(self):
        res = procesar_pregunta(
            "¿Cuál será el nivel de afluencia?",
            rol="SuperAdmin",
            sede="SedeInexistente",
            fecha="2026-10-15",
            hora=10
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "validacion")
        self.assertEqual(res["codigo_http"], 400)
        self.assertIn("no es válida", res["mensaje"])

    # 54. PREGUNTA REPRESENTATIVA: CONSULTA POR ALUMNO EXCLUYE 38 FILAS DE PRUEBA Y NO ATRIBUYE AGREGADOS
    @patch("llm.services.coordinador_asistente.contar_ingresos_alumno_por_codigo")
    def test_54_pregunta_representativa_alumno_excluye_pruebas_y_comunica_cero_reales(self, mock_conteo):
        mock_conteo.return_value = {
            "status": "success",
            "codigo_mascarado": "20******56",
            "total_ingresos": 0,
            "dias_con_visita": 0,
            "primer_ingreso": None,
            "ultimo_ingreso": None,
            "escuela": "",
            "periodo_consultado": {"fecha_inicio": None, "fecha_fin": None},
            "fuente": "RegistroIngresos (registros individuales de acceso por carnet/DNI)",
            "cobertura_sistema": {
                "fecha_minima": "2026-04-10",
                "fecha_maxima": "2026-09-28",
                "total_visitas_sistema": 38,
                "total_pruebas_excluidas": 38,
                "total_visitas_reales": 0,
                "aviso": "Los registros individuales del sistema corresponden al periodo 2026-04-10 a 2026-09-28."
            },
            "mensaje": (
                "No se encontraron ingresos reales registrados para el alumno con código 20******56 en el historial disponible. "
                "Nota de auditoría: El sistema contiene 0 visitas reales registradas (aún no hay visitas individuales reales registradas en el sistema). "
                "Se identificaron y excluyeron 38 registros manuales de prueba para garantizar la veracidad del conteo. "
                "No se generan registros ni se atribuyen alumnos a los conteos agregados de molinetes."
            )
        }

        res = procesar_pregunta(
            "¿Cuántas veces ingresó el alumno 2021123456?",
            rol="SuperAdmin"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["intencion"], "conteo_ingresos_alumno")
        self.assertEqual(res["datos"]["total_ingresos"], 0)
        self.assertIn("aún no hay visitas individuales reales registradas en el sistema", res["mensaje"])
        self.assertIn("excluyeron 38 registros manuales de prueba", res["mensaje"])
        self.assertIn("No se generan registros ni se atribuyen alumnos a los conteos agregados", res["mensaje"])
        self.assertIn("RegistroIngresos", res["fuente"])
        self.assertEqual(res["limites_cobertura"]["total_pruebas_excluidas"], 38)
        self.assertEqual(res["limites_cobertura"]["total_visitas_reales"], 0)

    # 55. SUPERVISOR ASIGNADO A TARMA EN AFLUENCIA HISTÓRICA FORZADO Y DEVUELVE DATOS INSUFICIENTES
    @patch("llm.services.coordinador_asistente.consultar_afluencia_historica")
    def test_55_supervisor_tarma_afluencia_historica_devuelve_datos_insuficientes(self, mock_afluencia):
        mock_afluencia.side_effect = ValueError(
            "Historial insuficiente: La sede 'Tarma' cuenta con 1 fecha(s) operativa(s) registrada(s)..."
        )

        res = procesar_pregunta(
            "Consulta descriptiva de afluencia histórica del 2026-05-01 al 2026-05-31",
            rol="Supervisor",
            sede_permitida="Tarma"
        )
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["categoria_error"], "datos_insuficientes")
        self.assertEqual(res["codigo_http"], 400)
        self.assertEqual(res["sede_aplicada"], "Tarma")
        mock_afluencia.assert_called_once_with(
            fecha_inicio="2026-05-01",
            fecha_fin="2026-05-31",
            sede="Tarma",
            rol="Supervisor",
            sede_permitida="Tarma"
        )


if __name__ == "__main__":
    unittest.main()
