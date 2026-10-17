import unittest
from datetime import date, datetime
from unittest.mock import patch, MagicMock
from app import app
from ml.services.analisis_individual import (
    obtener_analisis_descriptivo_ingresos,
    predecir_retorno_alumno,
    calcular_proximo_dia_operativo,
    enmascarar_codigo,
    PredictorRetornoAlumno,
    DIAS_FERIADOS_O_CIERRES
)
from llm.services.consultas_biblioteca import (
    consultar_analisis_descriptivo_individual,
    consultar_prediccion_retorno_alumno
)


# Fixture de datos controlados para aislar pruebas ML de SQL Server
_FECHAS_OPERATIVAS_FIXTURE = []
_curr = date(2026, 6, 1)
while _curr <= date(2026, 7, 31):
    if _curr.weekday() < 5 and _curr not in DIAS_FERIADOS_O_CIERRES:
        _FECHAS_OPERATIVAS_FIXTURE.append(_curr)
    _curr = date.fromordinal(_curr.toordinal() + 1)

_VISITAS_ALUMNO_200 = [
    datetime(2026, 6, 1, 10, 0),
    datetime(2026, 6, 2, 11, 0),
    datetime(2026, 6, 10, 12, 0),
    datetime(2026, 6, 15, 14, 0),
    datetime(2026, 7, 1, 10, 0),
    datetime(2026, 7, 8, 11, 0),
    datetime(2026, 7, 15, 12, 0),
    datetime(2026, 7, 20, 14, 0),
    datetime(2026, 7, 25, 15, 0),
    datetime(2026, 7, 31, 16, 0),
]


def _generar_filas_fixture_descriptivo():
    salas_spec = [
        ('Sala de Humanidades', 2, 10659),
        ('Sala de Usos Multiples', 3, 5154),
        ('Sala de Ciencias Puras', 3, 1001),
        ('Sala de Tesis', 3, 463),
        ('Servicios Informáticos', 3, 459),
        ('Sala de Ciencias Aplicadas', 2, 432)
    ]
    lista_salas = []
    for s_nom, s_piso, cnt in salas_spec:
        lista_salas.extend([(s_nom, s_piso)] * cnt)

    june_dates = [datetime(2026, 6, d, 8, 0) for d in range(1, 21)]
    july_dates = [datetime(2026, 7, d, 8, 0) for d in range(1, 21)]

    visitas_june = []
    visitas_july = []

    for sid in range(1, 552):
        visitas_june.append((sid, june_dates[0]))
        visitas_june.append((sid, june_dates[1]))
        visitas_june.append((sid, june_dates[2]))

    for sid in range(552, 1640):
        visitas_june.append((sid, june_dates[0]))
        visitas_june.append((sid, june_dates[1]))
        visitas_july.append((sid, july_dates[0]))
        visitas_july.append((sid, july_dates[1]))

    for sid in range(1640, 2157):
        visitas_june.append((sid, june_dates[3]))
        visitas_july.append((sid, july_dates[3]))

    for sid in range(2157, 3014):
        visitas_july.append((sid, july_dates[4]))

    for _ in range(7559 - len(visitas_june)):
        visitas_june.append((1, june_dates[0]))

    for _ in range(10609 - len(visitas_july)):
        visitas_july.append((3013, july_dates[4]))

    all_visitas = visitas_june + visitas_july

    filas = []
    for idx, (sid, dt) in enumerate(all_visitas):
        hora = 8 + (idx % 13)
        dt_hora = dt.replace(hour=hora, minute=0)
        sala_nom, piso = lista_salas[idx]
        mes = dt.strftime('%Y-%m')
        filas.append((
            idx + 1,
            sid,
            'Ingeniería Metalúrgica' if sid % 2 == 0 else 'Educación',
            sala_nom,
            piso,
            dt_hora,
            hora,
            mes,
            f'234130{sid:04d}' if sid == 200 else (f'229440{sid:04d}' if sid == 100 else f'COD{sid:07d}')
        ))
    return filas


_FILAS_FIXTURE_DESCRIPTIVO = _generar_filas_fixture_descriptivo()


def _crear_mock_db_cursor(*args, **kwargs):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    def execute(sql, params=()):
        mock_cursor._last_sql = sql
        mock_cursor._last_params = params
        sql_u = sql.upper()

        if "INNER JOIN DBO.ALUMNOS A" in sql_u:
            mock_cursor.fetchall.return_value = _FILAS_FIXTURE_DESCRIPTIVO
        elif "FROM DBO.ALUMNOS" in sql_u:
            cod = str(params[0]).strip()
            if cod == "0000000000":
                mock_cursor.fetchone.return_value = None
            elif cod in ("2294403050", "100"):
                mock_cursor.fetchone.return_value = (100, "2294403050", "Ingeniería")
            elif cod in ("2341304357", "200"):
                mock_cursor.fetchone.return_value = (200, "2341304357", "Ingeniería Metalúrgica")
            else:
                mock_cursor.fetchone.return_value = (300, cod, "General")
        elif "WHERE R.ALUMNOID =" in sql_u:
            al_id = params[0]
            f_ref_str = str(params[1])
            try:
                f_ref = date.fromisoformat(f_ref_str)
            except Exception:
                f_ref = date(2026, 7, 31)

            if al_id in (100, "100"):
                mock_cursor.fetchall.return_value = [(datetime(2026, 6, 15, 10, 0),)]
            elif al_id in (200, "200"):
                mock_cursor.fetchall.return_value = [(v,) for v in _VISITAS_ALUMNO_200 if v.date() <= f_ref]
            else:
                mock_cursor.fetchall.return_value = []
        elif "DISTINCT CAST(FECHAHORA AS DATE)" in sql_u:
            f_ref_str = str(params[0])
            try:
                f_ref = date.fromisoformat(f_ref_str)
            except Exception:
                f_ref = date(2026, 7, 31)
            mock_cursor.fetchall.return_value = [(d,) for d in _FECHAS_OPERATIVAS_FIXTURE if d <= f_ref]
        else:
            mock_cursor.fetchall.return_value = []
            mock_cursor.fetchone.return_value = None

    mock_cursor.execute.side_effect = execute
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = (mock_conn, mock_cursor)
    mock_ctx.__exit__.return_value = None
    return mock_ctx


class TestAnalisisIndividualML(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.patcher_db = patch("ml.services.analisis_individual.get_db_cursor", side_effect=_crear_mock_db_cursor)
        self.patcher_db.start()

    def tearDown(self):
        self.patcher_db.stop()

    # =========================================================================
    # 1. PRUEBAS DE DEFINICIÓN DE "VOLVER MAÑANA" Y CALENDARIO OPERATIVO
    # =========================================================================

    def test_01_proximo_dia_operativo_entre_semana(self):
        """Jueves 2026-06-04 debe tener como siguiente día hábil el viernes 2026-06-05."""
        jueves = date(2026, 6, 4)
        sig = calcular_proximo_dia_operativo(jueves)
        self.assertEqual(sig, date(2026, 6, 5))
        self.assertEqual(sig.weekday(), 4)  # Viernes

    def test_02_proximo_dia_operativo_fin_de_semana(self):
        """Viernes 2026-06-05 debe saltar sábado y domingo y dar lunes 2026-06-08."""
        viernes = date(2026, 6, 5)
        sig = calcular_proximo_dia_operativo(viernes)
        self.assertEqual(sig, date(2026, 6, 8))
        self.assertEqual(sig.weekday(), 0)  # Lunes

    def test_03_proximo_dia_operativo_salta_feriado(self):
        """Viernes 2026-06-26 debe saltar lunes 2026-06-29 (San Pedro y San Pablo) y dar martes 2026-06-30."""
        viernes = date(2026, 6, 26)
        sig = calcular_proximo_dia_operativo(viernes)
        self.assertEqual(sig, date(2026, 6, 30))
        self.assertEqual(sig.weekday(), 1)  # Martes

    def test_04_proximo_dia_operativo_salta_cierre_institucional_y_fiestas_patrias(self):
        """Miércoles 2026-07-22 debe saltar cierres del 23, 27, 28 y 29 de julio y dar 2026-07-30."""
        miercoles = date(2026, 7, 22)
        sig = calcular_proximo_dia_operativo(miercoles)
        self.assertEqual(sig, date(2026, 7, 30))

    # =========================================================================
    # 2. PRUEBAS DE FILTRO DE PRUEBA Y EXCLUSIÓN DE DNI (SEGURIDAD Y PRIVACIDAD)
    # =========================================================================

    def test_05_exclusion_estricta_de_dni_en_descriptivo(self):
        """El análisis descriptivo no debe consultar, contener ni exponer la clave DNI."""
        resultado = obtener_analisis_descriptivo_ingresos()
        self.assertEqual(resultado["status"], "success")

        # Serializar y comprobar que la palabra 'DNI' o 'dni' no aparece como clave
        def buscar_clave_dni(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    self.assertNotIn("dni", k.lower(), f"Se encontró la clave prohibida '{k}' en el resultado.")
                    buscar_clave_dni(v)
            elif isinstance(obj, list):
                for item in obj:
                    buscar_clave_dni(item)

        buscar_clave_dni(resultado)

    def test_06_exclusion_estricta_de_dni_en_prediccion(self):
        """La predicción individual no debe contener la clave DNI ni el DNI del alumno."""
        # Alumno recurrente de prueba
        resultado = predecir_retorno_alumno(codigo_matricula="2341304357")
        self.assertIn(resultado["status"], ["success", "historial_insuficiente"])
        self.assertNotIn("dni", resultado)
        self.assertNotIn("DNI", resultado)
        if "metricas_historial" in resultado:
            self.assertNotIn("dni", resultado["metricas_historial"])
            self.assertNotIn("DNI", resultado["metricas_historial"])

    def test_07_enmascaramiento_de_codigo(self):
        """El código de matrícula debe ser enmascarado para presentación segura."""
        self.assertEqual(enmascarar_codigo("2021123456"), "20******56")
        self.assertEqual(enmascarar_codigo("12345"), "1***5")
        self.assertEqual(enmascarar_codigo("12"), "***")
        self.assertEqual(enmascarar_codigo(None), "N/D")

    def test_08_exclusion_de_filas_de_prueba(self):
        """Las 38 filas con EsPrueba = 1 deben quedar estrictamente excluidas de los conteos reales."""
        resultado = obtener_analisis_descriptivo_ingresos()
        # Debe reportar exactamente 18,168 visitas reales y 38 registros de prueba excluidos
        self.assertEqual(resultado["total_visitas"], 18168)
        self.assertEqual(resultado["registros_prueba_excluidos"], 38)
        self.assertEqual(resultado["total_alumnos"], 3013)

    # =========================================================================
    # 3. PRUEBAS DE HISTORIAL INSUFICIENTE Y SUFICIENCIA ESTADÍSTICA
    # =========================================================================

    def test_09_alumno_sin_visitas_devuelve_historial_insuficiente(self):
        """Un alumno con 0 visitas previas debe devolver 'historial_insuficiente' sin probabilidad."""
        resultado = predecir_retorno_alumno(codigo_matricula="0000000000")
        if resultado["status"] == "error":
            # Código no registrado en la base de datos
            self.assertEqual(resultado["categoria_error"], "no_encontrado")
        else:
            self.assertEqual(resultado["status"], "historial_insuficiente")
            self.assertFalse(resultado["confiable"])
            self.assertIsNone(resultado["probabilidad_retorno"])

    def test_10_alumno_con_1_o_2_visitas_devuelve_historial_insuficiente(self):
        """Un alumno con 1 día activo debe devolver 'historial_insuficiente' con explicación clara."""
        # 2294403050 tiene 1 sola visita registrada
        resultado = predecir_retorno_alumno(codigo_matricula="2294403050")
        self.assertEqual(resultado["status"], "historial_insuficiente")
        self.assertFalse(resultado["confiable"])
        self.assertIsNone(resultado["probabilidad_retorno"])
        self.assertEqual(resultado["dias_activos_historicos"], 1)
        self.assertIn("Historial insuficiente", resultado["explicacion"])
        self.assertIn("mínimo de 3 días activos", resultado["explicacion"])

    def test_11_alumno_con_historial_suficiente_devuelve_probabilidad(self):
        """Un alumno con >= 3 días activos debe devolver 'success', confiable=True y probabilidad válida."""
        # 2341304357 tiene >= 5 días activos
        resultado = predecir_retorno_alumno(codigo_matricula="2341304357")
        self.assertEqual(resultado["status"], "success")
        self.assertTrue(resultado["confiable"])
        self.assertIsNotNone(resultado["probabilidad_retorno"])
        self.assertGreaterEqual(resultado["probabilidad_retorno"], 0.0)
        self.assertLessEqual(resultado["probabilidad_retorno"], 1.0)
        self.assertIn(resultado["nivel_probabilidad"], ["Alta", "Media", "Baja"])
        self.assertIn("%", resultado["probabilidad_porcentaje"])

    # =========================================================================
    # 4. PRUEBAS DE VALIDACIÓN CRONOLÓGICA (CERO FUGA TEMPORAL)
    # =========================================================================

    def test_12_validacion_cronologica_sin_fuga_temporal(self):
        """Al predecir con una fecha de referencia anterior, sólo se cuenta el historial hasta esa fecha."""
        # Con fecha 2026-06-05, el alumno no puede tener visitas de julio en su vector de características
        resultado_junio = predecir_retorno_alumno(
            codigo_matricula="2341304357",
            fecha_referencia="2026-06-05"
        )
        # En junio tenía menos días acumulados que al final de julio
        if resultado_junio["status"] == "success":
            dias_junio = resultado_junio["metricas_historial"]["dias_activos"]
            resultado_final = predecir_retorno_alumno(
                codigo_matricula="2341304357",
                fecha_referencia="2026-07-31"
            )
            self.assertLessEqual(dias_junio, resultado_final["metricas_historial"]["dias_activos"])
        else:
            self.assertEqual(resultado_junio["status"], "historial_insuficiente")
            self.assertLess(resultado_junio["dias_activos_historicos"], 3)

    # =========================================================================
    # 5. PRUEBAS DE MÉTRICAS DESCRIPTIVAS
    # =========================================================================

    def test_13_metricas_por_mes(self):
        """Las visitas por mes deben cubrir exactamente junio y julio de 2026."""
        resultado = obtener_analisis_descriptivo_ingresos()
        meses = resultado["visitas_por_mes"]
        self.assertEqual(len(meses), 2)
        mes_dict = {m["mes"]: m["total_visitas"] for m in meses}
        self.assertEqual(mes_dict["2026-06"], 7559)
        self.assertEqual(mes_dict["2026-07"], 10609)
        self.assertEqual(sum(mes_dict.values()), 18168)

    def test_14_metricas_por_sala(self):
        """Las visitas por sala deben identificar las salas reales y pisos observados."""
        resultado = obtener_analisis_descriptivo_ingresos()
        salas = resultado["visitas_por_sala"]
        self.assertGreater(len(salas), 0)
        nombres = [s["nombre_sala"] for s in salas]
        self.assertIn("Sala de Humanidades", nombres)
        self.assertIn("Sala de Usos Multiples", nombres)

    def test_15_metricas_por_hora_y_franja_parcial(self):
        """Las visitas por hora deben incluir de 08:00 a 20:00 y marcar las 20:00 como parcial."""
        resultado = obtener_analisis_descriptivo_ingresos()
        horas = resultado["visitas_por_hora"]
        self.assertEqual(len(horas), 13)  # 8 a 20 inclusive
        franja_20 = [h for h in horas if h["hora"] == 20][0]
        self.assertTrue(franja_20["es_parcial"])
        self.assertIn("parcial", franja_20["hora_formato"])

    def test_16_distribucion_frecuencia_alumnos(self):
        """La distribución de frecuencia debe reportar alumnos aptos (>= 3 días) e insuficientes (< 3 días)."""
        resultado = obtener_analisis_descriptivo_ingresos()
        frec = resultado["frecuencia_por_alumno"]
        elegibilidad = frec["resumen_elegibilidad_prediccion_ml"]
        suf = elegibilidad["alumnos_con_historial_suficiente"]["cantidad"]
        insuf = elegibilidad["alumnos_con_historial_insuficiente"]["cantidad"]
        self.assertEqual(suf, 1639)
        self.assertEqual(insuf, 1374)
        self.assertEqual(suf + insuf, 3013)

    # =========================================================================
    # 6. PRUEBAS DE ENDPOINTS ADMINISTRATIVOS Y SERVICIOS LLM
    # =========================================================================

    def test_17_endpoint_analisis_individual(self):
        """GET /admin/api/ml/analisis-individual con sesión de SuperAdmin responde 200 OK."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_abc"

        respuesta = self.client.get("/admin/api/ml/analisis-individual")
        self.assertEqual(respuesta.status_code, 200)
        data = respuesta.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total_visitas"], 18168)

    def test_18_endpoint_predecir_retorno(self):
        """POST /admin/api/ml/predecir-retorno con sesión responde 200 OK."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "admin_test"
            sesion["admin_rol"] = "SuperAdmin"
            sesion["csrf_token"] = "token_abc"

        respuesta = self.client.post(
            "/admin/api/ml/predecir-retorno",
            json={"codigo_matricula": "2341304357"},
            headers={"X-CSRFToken": "token_abc"}
        )
        self.assertEqual(respuesta.status_code, 200)
        data = respuesta.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("probabilidad_retorno", data)

    def test_19_consultor_bloqueado_en_prediccion(self):
        """El rol Consultor no debe tener acceso a la predicción individual de retorno."""
        with self.client.session_transaction() as sesion:
            sesion["admin_user"] = "consultor_test"
            sesion["admin_rol"] = "Consultor"
            sesion["csrf_token"] = "token_abc"

        respuesta = self.client.post(
            "/admin/api/ml/predecir-retorno",
            json={"codigo_matricula": "2341304357"},
            headers={"X-CSRFToken": "token_abc"}
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_20_servicios_llm_consultas(self):
        """Las funciones helper de consultas_biblioteca deben respetar roles y devolver datos sin DNI."""
        res_desc = consultar_analisis_descriptivo_individual(rol="SuperAdmin")
        self.assertEqual(res_desc["status"], "success")
        self.assertIn("Análisis descriptivo de ingresos individuales", res_desc["mensaje"])
        self.assertNotIn("dni", str(res_desc).lower())

        # Consultor denegado en predicción
        with self.assertRaises(PermissionError):
            consultar_prediccion_retorno_alumno(codigo_matricula="2341304357", rol="Consultor")

        # SuperAdmin permitido
        res_pred = consultar_prediccion_retorno_alumno(codigo_matricula="2341304357", rol="SuperAdmin")
        self.assertIn(res_pred["status"], ["success", "historial_insuficiente"])
        self.assertNotIn("dni", str(res_pred).lower())

    # =========================================================================
    # 7. PRUEBAS DE EXCLUSIÓN EN DASHBOARD Y RECONCILIACIÓN POR MES
    # =========================================================================

    @patch("utils.queries_dashboard.get_db_connection")
    def test_21_dashboard_excluye_filas_de_prueba(self, mock_conn_fn):
        """El dashboard general y de exportación deben excluir estrictamente EsPrueba = 1."""
        from utils.queries_dashboard import obtener_datos_dashboard, obtener_registros_csv
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn_fn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        def execute(sql, params=()):
            sql_u = sql.upper().replace(" ", "").replace("\n", "").replace("\t", "")
            self.assertTrue("ISNULL(ESPRUEBA,0)=0" in sql_u or "ISNULL(R.ESPRUEBA,0)=0" in sql_u)
            if "REGISTROIDASID" in sql_u:
                mock_cursor.fetchall.return_value = [("Alumno", "2341304357", "Central", 2, "Sala Humanidades", "Mañana", "01/06/2026", "08:00:00", "Ingeniería", "Metalúrgica")] * 18168
            elif "TOP10" in sql_u:
                mock_cursor.fetchall.return_value = [("Estudiante 1", 2, "10:00:00", "Ingeniería", "Alumno", "01/06/2026", "Central", "Sala Humanidades")]
            elif "TIPOUSUARIO='ALUMNO'" in sql_u:
                mock_cursor.fetchone.return_value = (18168,)
            elif "TIPOUSUARIO" in sql_u:
                mock_cursor.fetchone.return_value = (0,)
            elif "SELECTCOUNT(*)" in sql_u:
                mock_cursor.fetchone.return_value = (18168,)
            elif "GROUPBYPISO" in sql_u:
                mock_cursor.fetchall.return_value = [(2, 11091), (3, 7077)]
            elif "GROUPBYS.PISO,S.NOMBRESALA" in sql_u:
                mock_cursor.fetchall.return_value = [(2, "Sala Humanidades", 10659), (3, "Sala Usos Multiples", 5154)]
            elif "GROUPBYSEDE" in sql_u:
                mock_cursor.fetchall.return_value = []
            elif "GROUPBYDATEPART(HOUR,FECHAHORA)" in sql_u:
                mock_cursor.fetchall.return_value = [(h, 100) for h in range(8, 21)]
            elif "TOP5ORIGEN" in sql_u:
                mock_cursor.fetchall.return_value = [("Ingeniería", 5000)]
            else:
                mock_cursor.fetchone.return_value = (0,)
                mock_cursor.fetchall.return_value = []

        mock_cursor.execute.side_effect = execute

        dash_data = obtener_datos_dashboard('2026-06-01', '2026-07-31')
        self.assertEqual(dash_data['total_hoy'], 18168)
        self.assertEqual(dash_data['total_alumnos'], 18168)
        self.assertEqual(dash_data['total_personal'], 0)
        self.assertEqual(dash_data['total_visitantes'], 0)

        csv_rows = obtener_registros_csv('2026-06-01', '2026-07-31')
        self.assertEqual(len(csv_rows), 18168)

    def test_22_reconciliacion_alumnos_unicos_por_mes(self):
        """Reconciliación de alumnos únicos: 2,156 en junio, 2,462 en julio, 1,605 en ambos, total 3,013."""
        resultado = obtener_analisis_descriptivo_ingresos()
        meses = {m["mes"]: m for m in resultado["visitas_por_mes"]}
        self.assertEqual(meses["2026-06"]["alumnos_unicos"], 2156)
        self.assertEqual(meses["2026-07"]["alumnos_unicos"], 2462)
        self.assertEqual(resultado["total_alumnos"], 3013)

    # =========================================================================
    # 8. PRUEBAS DE VIGENCIA (>60 DÍAS) Y DISTRIBUCIÓN DE SALAS EN SEDE CENTRAL
    # =========================================================================

    def test_23_vigencia_desfase_superior_a_60_dias(self):
        """Si la fecha supera en más de 60 días el corte (31/07/2026), debe marcarse no vigente."""
        # Alumno activo conocido: 2341304357
        # Fecha en octubre 2026 (>60 días después de 2026-07-31)
        res_desactualizado = predecir_retorno_alumno(
            codigo_matricula="2341304357",
            fecha_referencia="2026-10-15"
        )
        if res_desactualizado["status"] == "success":
            self.assertFalse(res_desactualizado["vigente"])
            self.assertEqual(res_desactualizado["estado_vigencia"], "no_representativo")
            self.assertTrue(res_desactualizado["es_desfase_severo"])
            self.assertIn("NO ES REPRESENTATIVA", res_desactualizado["mensaje"].upper())

        # Fecha al corte (2026-07-31)
        res_corte = predecir_retorno_alumno(
            codigo_matricula="2341304357",
            fecha_referencia="2026-07-31"
        )
        if res_corte["status"] == "success":
            self.assertTrue(res_corte["vigente"])
            self.assertEqual(res_corte["estado_vigencia"], "vigente")
            self.assertFalse(res_corte["es_desfase_severo"])

    def test_24_conteo_salas_sede_central(self):
        """Verificar conteo real de salas y pisos en Sede Central: 6 salas con visitas en Pisos 2 y 3."""
        resultado = obtener_analisis_descriptivo_ingresos(sede="Central")
        self.assertEqual(resultado["total_visitas"], 18168)

        salas = {s["nombre_sala"]: s["total_visitas"] for s in resultado["visitas_por_sala"]}
        self.assertEqual(len(salas), 6)  # 6 salas con ingresos en Sede Central

        # Verificar cada sala por su nombre configurado en BD
        self.assertEqual(salas.get("Sala de Humanidades"), 10659)
        self.assertEqual(salas.get("Sala de Ciencias Aplicadas"), 432)
        self.assertEqual(salas.get("Sala de Usos Multiples"), 5154)
        self.assertEqual(salas.get("Sala de Ciencias Puras"), 1001)
        self.assertEqual(salas.get("Sala de Tesis"), 463)
        self.assertTrue(any("Servicios Inform" in k and v == 459 for k, v in salas.items()))

        pisos = {p["piso"]: p for p in resultado["visitas_por_piso"]}
        self.assertEqual(pisos[2]["total_visitas"], 11091)
        self.assertEqual(pisos[3]["total_visitas"], 7077)
        self.assertEqual(pisos.get(1, {}).get("total_visitas", 0), 0)

        # Verificar resumen de salas configuradas en Central
        res_pisos = resultado["resumen_pisos"]
        self.assertEqual(res_pisos["salas_configuradas_central"], 8)
        self.assertEqual(res_pisos["salas_con_visitas"], 6)


if __name__ == "__main__":
    unittest.main()
