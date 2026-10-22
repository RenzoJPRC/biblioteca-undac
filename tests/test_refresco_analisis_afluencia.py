import unittest
from unittest.mock import MagicMock, patch
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ml.services.datos_afluencia import (
    refrescar_analisis_afluencia_memoria,
    limpiar_cache_refresco_memoria,
    obtener_estado_refresco_memoria,
    obtener_historial_agrupado,
    obtener_historial_afluencia_periodo,
    construir_variables_afluencia
)
from ml.services.servicio_afluencia import predecir_afluencia_desde_bd
from ml.services.predictor_afluencia import obtener_predictor_afluencia


class TestRefrescoAnalisisAfluencia(unittest.TestCase):

    def setUp(self):
        limpiar_cache_refresco_memoria()

    def tearDown(self):
        limpiar_cache_refresco_memoria()

    def test_01_actualizacion_franja_5_luego_10_refleja_15_sin_duplicar(self):
        """
        Simulación:
        1. Primera actualización ve una franja horaria (ej. 2026-10-19 14:00) con 5 ingresos.
        2. Luego ingresan 10 registros adicionales en esa misma franja.
        3. Al actualizar de nuevo, el resultado refleja 15 sin duplicar el total.
        """
        # Estado 1: 5 ingresos en franja 14:00
        mock_cursor_1 = MagicMock()
        # consulta_ri: [(Fecha, Hora, CantidadIngresos, MaxFechaHora)]
        # consulta_meta: (UltimoRegistro, TotalIngresos)
        # consulta_base_central: [(Fecha, Hora, CantidadIngresos)]
        mock_cursor_1.fetchall.side_effect = [
            [(date(2026, 10, 19), 14, 5, datetime(2026, 10, 19, 14, 15, 0))], # consulta_ri
            [(date(2026, 5, 15), 14, 20)] # consulta_base_central
        ]
        mock_cursor_1.fetchone.return_value = (datetime(2026, 10, 19, 14, 15, 0), 5) # consulta_meta

        res_1 = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cursor_1)
        self.assertEqual(res_1["status"], "success")
        self.assertEqual(res_1["total_ingresos"], 5)

        # Verificar en el historial agrupado
        hist_1 = obtener_historial_agrupado(fecha_objetivo="2026-10-20", sede="Central")
        franja_1 = hist_1[(hist_1["Fecha"] == date(2026, 10, 19)) & (hist_1["Hora"] == 14)]
        self.assertEqual(len(franja_1), 1)
        self.assertEqual(int(franja_1["CantidadIngresos"].iloc[0]), 5)

        # Estado 2: Entran 10 ingresos más en esa misma franja (total acumulado = 15)
        mock_cursor_2 = MagicMock()
        mock_cursor_2.fetchall.side_effect = [
            [(date(2026, 10, 19), 14, 15, datetime(2026, 10, 19, 14, 45, 0))], # consulta_ri
            [(date(2026, 5, 15), 14, 20)] # consulta_base_central
        ]
        mock_cursor_2.fetchone.return_value = (datetime(2026, 10, 19, 14, 45, 0), 15) # consulta_meta

        res_2 = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cursor_2)
        self.assertEqual(res_2["status"], "success")
        self.assertEqual(res_2["total_ingresos"], 15)

        # Al actualizar de nuevo, el resultado refleja 15 exactamente, sin duplicar
        hist_2 = obtener_historial_agrupado(fecha_objetivo="2026-10-20", sede="Central")
        franja_2 = hist_2[(hist_2["Fecha"] == date(2026, 10, 19)) & (hist_2["Hora"] == 14)]
        self.assertEqual(len(franja_2), 1)
        self.assertEqual(int(franja_2["CantidadIngresos"].iloc[0]), 15)
        self.assertNotEqual(int(franja_2["CantidadIngresos"].iloc[0]), 20)

    def test_02_aislamiento_por_sede_y_exclusion_null(self):
        """
        Verifica que cada sede se consulte estrictamente aislada:
        - Central no incluye registros de Tarma ni La Merced.
        - Tarma y La Merced no se mezclan con Central.
        - Registros con Sede NULL son excluidos.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [(date(2026, 10, 15), 10, 8, datetime(2026, 10, 15, 10, 30, 0))],
            [] # base_central vacía para este test
        ]
        mock_cursor.fetchone.return_value = (datetime(2026, 10, 15, 10, 30, 0), 8)

        refrescar_analisis_afluencia_memoria(sede="Tarma", cursor_externo=mock_cursor)

        # Se llamó pasando exactamente el parámetro sede='Tarma'
        llamadas = mock_cursor.execute.call_args_list
        # La consulta a RegistroIngresos debe incluir el parámetro 'Tarma'
        params_ri = llamadas[0][0][1]
        self.assertEqual(params_ri, ("Tarma",))

        # Y la consulta de metadatos también
        params_meta = llamadas[1][0][1]
        self.assertEqual(params_meta, ("Tarma",))

        # Y para Tarma NO se consulta consulta_base_central
        self.assertEqual(len(llamadas), 2)

    def test_03_exclusion_es_prueba_1_en_consulta_sql(self):
        """
        Verifica que la consulta SQL a dbo.RegistroIngresos filtre rigurosamente ISNULL(EsPrueba, 0) = 0.
        """
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [[], []]
        mock_cursor.fetchone.return_value = (None, 0)

        refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cursor)

        sql_ri = mock_cursor.execute.call_args_list[0][0][0]
        self.assertIn("ISNULL(EsPrueba, 0) = 0", sql_ri)

        sql_meta = mock_cursor.execute.call_args_list[1][0][0]
        self.assertIn("ISNULL(EsPrueba, 0) = 0", sql_meta)

    def test_04_etiquetado_dia_en_curso_vs_cerrado(self):
        """
        Verifica que si el último registro es de hoy y la jornada está activa, se etiquete como 'día en curso'.
        Si es de un día pasado o posterior a las 20:45:00, se etiquete como 'cerrado'.
        """
        ahora_peru = datetime.now(ZoneInfo("America/Lima"))
        hoy_peru = ahora_peru.date()

        # Caso A: Registro hoy a las 11:30 (jornada en curso)
        mock_cursor_a = MagicMock()
        mock_cursor_a.fetchall.side_effect = [
            [(hoy_peru, 11, 4, datetime(hoy_peru.year, hoy_peru.month, hoy_peru.day, 11, 30, 0))],
            []
        ]
        mock_cursor_a.fetchone.return_value = (
            datetime(hoy_peru.year, hoy_peru.month, hoy_peru.day, 11, 30, 0),
            4
        )

        res_a = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cursor_a)
        # Si la prueba se ejecuta antes de las 20:45 o después de las 20:45
        if ahora_peru.time() < time(20, 45, 0):
            self.assertEqual(res_a["estado_dia"], "día en curso")
            self.assertTrue(res_a["es_dia_en_curso"])
        self.assertIn(str(hoy_peru), res_a["ultimo_registro_incluido"])

        # Caso B: Registro de ayer (día cerrado)
        ayer = date(2026, 10, 18)
        mock_cursor_b = MagicMock()
        mock_cursor_b.fetchall.side_effect = [
            [(ayer, 16, 25, datetime(2026, 10, 18, 16, 45, 0))],
            []
        ]
        mock_cursor_b.fetchone.return_value = (datetime(2026, 10, 18, 16, 45, 0), 25)

        res_b = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cursor_b)
        self.assertEqual(res_b["estado_dia"], "cerrado")
        self.assertFalse(res_b["es_dia_en_curso"])

    def test_05_prohibicion_estricta_de_prediccion_para_filiales_con_modelo_central(self):
        """
        No se deben presentar predicciones para filiales (Tarma, La Merced) con el modelo Central.
        Debe lanzar ValueError explícito.
        """
        with self.assertRaises(ValueError) as ctx:
            predecir_afluencia_desde_bd("2026-10-20", 10, sede="Tarma")
        self.assertIn("modelo predictivo actual corresponde exclusivamente a la Sede Central", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            predecir_afluencia_desde_bd("2026-10-20", 10, sede="La Merced")
        self.assertIn("modelo predictivo actual corresponde exclusivamente a la Sede Central", str(ctx2.exception))

    def test_06_compatibilidad_artefacto_con_historial_refrescado_en_memoria(self):
        """
        Verifica que el modelo vigente RandomForestClassifier acepte de forma coherente
        las 12 variables calculadas con los nuevos registros en memoria y emita su predicción.
        """
        # Crear 20 fechas operativas simuladas en octubre para Central
        filas_simuladas = []
        for d in range(1, 21):
            fecha = date(2026, 10, d)
            if fecha.weekday() < 5: # Solo lunes a viernes
                for h in range(8, 21):
                    filas_simuladas.append((fecha, h, 15 + (h % 5), datetime(2026, 10, d, h, 30, 0)))

        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [filas_simuladas, []]
        mock_cur.fetchone.return_value = (datetime(2026, 10, 20, 20, 30, 0), len(filas_simuladas) * 15)

        res_refresco = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur)
        self.assertEqual(res_refresco["status"], "success")

        # Ahora invocar la predicción para 2026-10-21 a las 10:00 (fecha posterior a las fechas en memoria)
        res_pred = predecir_afluencia_desde_bd(fecha="2026-10-21", hora=10, sede="Central")
        self.assertEqual(res_pred["status"], "success")
        self.assertIn("prediccion", res_pred)
        self.assertIn(res_pred["prediccion"]["nivel"], ["Bajo", "Medio", "Alto"])
        self.assertIn("probabilidades", res_pred["prediccion"])
        self.assertEqual(len(res_pred["variables_modelo"]), 12)
        # El desfase debe ser de 1 día (no más de 14), por lo que es confiable y no desactualizado
        self.assertFalse(res_pred["consulta"]["es_desactualizado"])

    def test_07_endpoint_actualizar_analisis_sin_sesion_redirige_login_302(self):
        """El endpoint requiere sesión administrativa activa; sin sesión redirige a /admin/login."""
        from app import app
        client = app.test_client()
        res = client.post("/admin/api/ml/actualizar-analisis")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.headers.get("Location", ""))

    def test_08_endpoint_actualizar_analisis_consultor_rechazado(self):
        """El rol Consultor está denegado para actualizar análisis ML (403 en GET y bloqueado en POST)."""
        from app import app
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["admin_user"] = "consultor_test"
            sess["admin_rol"] = "Consultor"

        # GET es rechazado directamente con 403 en el endpoint
        res_get = client.get("/admin/api/ml/actualizar-analisis")
        self.assertEqual(res_get.status_code, 403)
        self.assertEqual(res_get.get_json()["status"], "error")

        # POST es bloqueado por middleware de rol solo lectura
        res_post = client.post("/admin/api/ml/actualizar-analisis")
        datos_post = res_post.get_json()
        self.assertEqual(datos_post["status"], "error")

    @patch("routes.admin_ml.validar_sede_activa")
    @patch("routes.admin_ml.refrescar_analisis_afluencia_memoria")
    def test_09_endpoint_actualizar_analisis_supervisor_sede_forzada(self, mock_refrescar, mock_validar):
        """Supervisor con sede 'Tarma' solo puede refrescar 'Tarma', sin importar lo que envíe en el body."""
        from app import app
        mock_validar.return_value = (True, "Tarma", "sede_activa")
        mock_refrescar.return_value = {
            "status": "success",
            "sede": "Tarma",
            "hora_actualizacion": "2026-10-19 18:00:00",
            "ultimo_registro_incluido": "2026-10-19 17:30:00",
            "estado_dia": "día en curso",
            "es_dia_en_curso": True,
            "total_ingresos": 30,
            "franjas_horarias": 9,
            "fechas_operativas": 3
        }

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["admin_user"] = "supervisor_tarma"
            sess["admin_rol"] = "Supervisor"
            sess["admin_sede"] = "Tarma"

        res = client.post("/admin/api/ml/actualizar-analisis", json={"sede": "Central"})
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["sede"], "Tarma")
        mock_refrescar.assert_called_with(sede="Tarma")

    @patch("routes.admin_ml.validar_sede_activa")
    @patch("routes.admin_ml.refrescar_analisis_afluencia_memoria")
    def test_10_endpoint_actualizar_analisis_admin_exito(self, mock_refrescar, mock_validar):
        """SuperAdmin puede actualizar el análisis indicando la sede deseada."""
        from app import app
        mock_validar.return_value = (True, "Central", "sede_activa")
        mock_refrescar.return_value = {
            "status": "success",
            "sede": "Central",
            "hora_actualizacion": "2026-10-19 18:00:00",
            "ultimo_registro_incluido": "2026-10-19 14:32:00",
            "estado_dia": "día en curso",
            "es_dia_en_curso": True,
            "total_ingresos": 39751,
            "franjas_horarias": 1005,
            "fechas_operativas": 81
        }

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["admin_user"] = "superadmin_test"
            sess["admin_rol"] = "SuperAdmin"

        res = client.post("/admin/api/ml/actualizar-analisis", json={"sede": "Central"})
        self.assertEqual(res.status_code, 200)
        datos = res.get_json()
        self.assertEqual(datos["status"], "success")
        self.assertEqual(datos["estado_dia"], "día en curso")
        self.assertTrue(datos["es_dia_en_curso"])

    def test_11_exclusion_24_julio_agosto_27_julio_feriados(self):
        """
        Verifica que el refresco en memoria excluya rigurosamente:
        - El cierre no laborable del 24/07/2026 (puente FAP).
        - El cierre institucional del 27/07/2026 (puente de Fiestas Patrias).
        - Las fechas del receso de agosto (01/08/2026 al 23/08/2026).
        - Los feriados confirmados (ej. 23/07/2026, 28/07/2026, 29/07/2026, 06/08/2026).
        - Los fines de semana (sábados y domingos).
        """
        filas_prueba = [
            (date(2026, 7, 23), 10, 5, datetime(2026, 7, 23, 10, 30)), # Feriado FAP (excluido)
            (date(2026, 7, 24), 10, 5, datetime(2026, 7, 24, 10, 30)), # Cierre puente FAP (excluido)
            (date(2026, 7, 27), 10, 5, datetime(2026, 7, 27, 10, 30)), # Cierre puente patrias (excluido)
            (date(2026, 7, 28), 10, 5, datetime(2026, 7, 28, 10, 30)), # Feriado Fiestas Patrias (excluido)
            (date(2026, 8, 10), 10, 5, datetime(2026, 8, 10, 10, 30)), # Receso de agosto (excluido)
            (date(2026, 10, 17), 10, 5, datetime(2026, 10, 17, 10, 30)), # Sábado (excluido)
            (date(2026, 10, 19), 10, 12, datetime(2026, 10, 19, 10, 30)), # Lunes hábil lectivo regular (incluido)
        ]
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [filas_prueba, []]
        mock_cur.fetchone.return_value = (datetime(2026, 10, 19, 10, 30), 42)

        res = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur)
        self.assertEqual(res["status"], "success")

        hist = obtener_historial_agrupado(fecha_objetivo="2026-10-20", sede="Central")
        fechas_incluidas = set(hist["Fecha"].unique())
        self.assertIn(date(2026, 10, 19), fechas_incluidas)
        self.assertNotIn(date(2026, 7, 23), fechas_incluidas)
        self.assertNotIn(date(2026, 7, 24), fechas_incluidas)
        self.assertNotIn(date(2026, 7, 27), fechas_incluidas)
        self.assertNotIn(date(2026, 7, 28), fechas_incluidas)
        self.assertNotIn(date(2026, 8, 10), fechas_incluidas)
        self.assertNotIn(date(2026, 10, 17), fechas_incluidas)

    def test_12_determinacion_ultimo_dia_operativo_completo_y_fechas_corte(self):
        """
        Verifica que el refresco determine explícitamente el último día operativo completo:
        - Si el día está en curso, el último día completo es el día previo cerrado.
        - Si el día está cerrado, el último día completo es la fecha del registro.
        - Expone fecha_corte_datos y metadatos de entrenamiento del modelo.
        """
        # Caso A: Día cerrado (lunes 2026-10-19 consultado el martes 2026-10-20)
        ahora_a = datetime(2026, 10, 20, 10, 0, 0, tzinfo=ZoneInfo("America/Lima"))
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [
            [(date(2026, 10, 19), 15, 20, datetime(2026, 10, 19, 15, 30))],
            []
        ]
        mock_cur.fetchone.return_value = (datetime(2026, 10, 19, 15, 30), 20)
        res_a = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur, ahora_referencia=ahora_a)

        self.assertEqual(res_a["status"], "success")
        self.assertFalse(res_a["es_dia_en_curso"])
        self.assertEqual(res_a["ultimo_dia_completo"], "2026-10-19")
        self.assertEqual(res_a["fecha_corte_datos"], "2026-10-19")
        self.assertIn("fecha_entrenamiento_modelo", res_a)
        self.assertIn("modelo_vigente", res_a)

        # Caso B: Día en curso (martes 2026-10-20 a las 11:30 AM con datos parciales de hoy)
        ahora_b = datetime(2026, 10, 20, 11, 30, 0, tzinfo=ZoneInfo("America/Lima"))
        mock_cur_b = MagicMock()
        mock_cur_b.fetchall.side_effect = [
            [
                (date(2026, 10, 19), 15, 20, datetime(2026, 10, 19, 15, 30)),
                (date(2026, 10, 20), 11, 5, datetime(2026, 10, 20, 11, 30))
            ],
            []
        ]
        mock_cur_b.fetchone.return_value = (datetime(2026, 10, 20, 11, 30), 25)
        res_b = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur_b, ahora_referencia=ahora_b)

        self.assertTrue(res_b["es_dia_en_curso"])
        self.assertEqual(res_b["ultimo_dia_completo"], "2026-10-19")
        self.assertEqual(res_b["fecha_corte_datos"], "2026-10-19")

    def test_13_filial_presenta_analisis_descriptivo_sin_ml(self):
        """
        Verifica que al consultar filiales (Tarma, La Merced) con permitir_descriptivo=True,
        se retorne análisis descriptivo directo de molinetes sin emitir predicción con el modelo de Central.
        """
        # Con permitir_descriptivo=True
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            (55, 2, datetime(2026, 10, 20, 15, 30)),
            (8.5,)
        ]
        from ml.services.datos_afluencia import obtener_analisis_descriptivo_filial
        res_tarma = obtener_analisis_descriptivo_filial(
            fecha_objetivo="2026-10-21",
            hora_objetivo=10,
            sede="Tarma",
            cursor_externo=mock_cur
        )
        self.assertEqual(res_tarma["status"], "filial_descriptivo")
        self.assertTrue(res_tarma["es_filial"])
        self.assertFalse(res_tarma["presenta_prediccion_ml"])
        self.assertEqual(res_tarma["descriptivo"]["total_ingresos"], 55)
        self.assertEqual(res_tarma["descriptivo"]["fechas_operativas"], 2)
        self.assertEqual(res_tarma["descriptivo"]["promedio_hora_consultada"], 8.5)

        # A través de predecir_afluencia_desde_bd con permitir_descriptivo=True
        with patch("ml.services.servicio_afluencia.obtener_analisis_descriptivo_filial") as mock_desc:
            mock_desc.return_value = res_tarma
            res_servicio = predecir_afluencia_desde_bd(
                fecha="2026-10-21",
                hora=10,
                sede="Tarma",
                permitir_descriptivo=True
            )
            self.assertEqual(res_servicio["status"], "filial_descriptivo")
            self.assertFalse(res_servicio["presenta_prediccion_ml"])
            mock_desc.assert_called_once()

    def test_14_recalculo_prediccion_con_datos_refrescados(self):
        """
        Verifica que tras actualizar en memoria, la predicción use inmediatamente el historial refrescado.
        """
        filas = []
        for d in range(1, 20):
            f = date(2026, 10, d)
            if f.weekday() < 5:
                for h in range(8, 21):
                    filas.append((f, h, 25, datetime(2026, 10, d, h, 30)))

        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [filas, []]
        mock_cur.fetchone.return_value = (datetime(2026, 10, 19, 20, 30), len(filas) * 25)

        refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur)

        # Predicción posterior al refresco
        res_pred = predecir_afluencia_desde_bd(fecha="2026-10-20", hora=10, sede="Central")
        self.assertEqual(res_pred["status"], "success")
        self.assertIn("prediccion", res_pred)
        # Última fecha disponible debe ser 2026-10-19
        self.assertEqual(res_pred["consulta"]["ultima_fecha_disponible"], "2026-10-19")

    def test_15_ausencia_total_escrituras_y_reentrenamiento(self):
        """
        Verifica que el botón de refresco y su endpoint sean estrictamente de solo lectura:
        - Consultas en SQL Server exclusivamente SELECT.
        - Los artefactos modelo_afluencia_undac.joblib y modelo_afluencia_candidato_gb.joblib
          conservan su tamaño e integridad física sin reentrenamiento.
        """
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        prod_model = root / "ml" / "models" / "modelo_afluencia_undac.joblib"
        cand_model = root / "ml" / "models" / "modelo_afluencia_candidato_gb.joblib"

        mtime_prod_antes = prod_model.stat().st_mtime if prod_model.exists() else None
        mtime_cand_antes = cand_model.stat().st_mtime if cand_model.exists() else None

        consultas = []
        mock_cur = MagicMock()
        def reg_exec(sql, params=None):
            consultas.append(sql.strip().upper())
        mock_cur.execute.side_effect = reg_exec
        mock_cur.fetchall.side_effect = [[], []]
        mock_cur.fetchone.return_value = (None, 0)

        res = refrescar_analisis_afluencia_memoria(sede="Central", cursor_externo=mock_cur)
        self.assertEqual(res["status"], "success")

        # Cero escrituras
        for sql in consultas:
            self.assertTrue(sql.startswith("SELECT"), f"Debe ser SELECT: {sql}")
            for dml in ["INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER"]:
                self.assertNotIn(dml, sql)

        # Cero reentrenamiento
        if prod_model.exists():
            self.assertEqual(prod_model.stat().st_mtime, mtime_prod_antes)
        if cand_model.exists():
            self.assertEqual(cand_model.stat().st_mtime, mtime_cand_antes)


if __name__ == "__main__":
    unittest.main()
