"""
Pruebas unitarias y de integración de la tabla analítica AfluenciaHistoricaAgregada
y de separación de funciones analíticas vs informes de alumnos.
Biblioteca UNDAC.
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import date, datetime
import pandas as pd
from pathlib import Path

from ml.scripts.cargar_afluencia_historica import (
    leer_y_preparar_csv,
    obtener_ruta_csv_fuente,
    cargar_afluencia_agregada_idempotente,
    validar_metricas_tabla,
    auditar_cruce_con_registro_ingresos,
    NOMBRE_TABLA_ANALITICA,
    PROCEDENCIA_DEFECTO
)
from ml.services.datos_afluencia import (
    obtener_historial_agrupado,
    construir_variables_afluencia
)
from ml.services.servicio_afluencia import predecir_afluencia_desde_bd


class TestIntegracionAfluenciaHistorica(unittest.TestCase):

    def setUp(self):
        self.ruta_csv = obtener_ruta_csv_fuente()
        self.df_csv = leer_y_preparar_csv(self.ruta_csv)

    def test_01_csv_fuente_metricas_exactas(self):
        """El CSV fuente contiene exactamente 1,029 filas, 98 fechas únicas y 32,788 ingresos."""
        self.assertEqual(len(self.df_csv), 1029)
        self.assertEqual(self.df_csv["Fecha"].nunique(), 98)
        self.assertEqual(int(self.df_csv["CantidadIngresos"].sum()), 32788)
        self.assertEqual(self.df_csv["CantidadIngresos"].min(), 1)
        self.assertFalse(self.df_csv.duplicated(subset=["Fecha", "Hora", "Sede"]).any())

    def test_02_marcado_horario_atipico(self):
        """Las 4 filas fuera del horario habitual (08:00 a 20:00 o fines de semana) quedan marcadas."""
        atipicos = self.df_csv[self.df_csv["EsHorarioAtipico"] == 1]
        self.assertEqual(len(atipicos), 4)
        self.assertEqual(int(atipicos["CantidadIngresos"].sum()), 8)

        # Verificar horas de los 4 atípicos (0h, 7h, 7h y 22h)
        horas_atipicas = sorted(atipicos["Hora"].tolist())
        self.assertEqual(horas_atipicas, [0, 7, 7, 22])

    def test_03_marcado_cierre_agosto(self):
        """Las 65 filas durante el cierre de agosto (01/08/2026 al 23/08/2026) quedan marcadas."""
        cierre = self.df_csv[self.df_csv["EsCierreAgosto"] == 1]
        self.assertEqual(len(cierre), 65)
        self.assertEqual(int(cierre["CantidadIngresos"].sum()), 214)

        # Verificar que no hay registros fuera de ese rango marcado
        fechas_cierre = cierre["Fecha"].unique()
        for f in fechas_cierre:
            self.assertTrue("2026-08-01" <= f <= "2026-08-23")

    def test_04_idempotencia_de_carga(self):
        """La carga no debe insertar duplicados si las filas ya existen en la base de datos."""
        mock_cursor = MagicMock()
        mock_conn = MagicMock()

        # Simular que la tabla ya tiene 1,029 filas
        mock_cursor.fetchone.side_effect = [
            (1029,),  # conteo antes
            (1029,)   # conteo después
        ]

        resultado = cargar_afluencia_agregada_idempotente(
            cursor=mock_cursor,
            conn=mock_conn,
            df=self.df_csv,
            batch_size=500
        )

        self.assertEqual(resultado["filas_procesadas"], 1029)
        self.assertEqual(resultado["filas_antes"], 1029)
        self.assertEqual(resultado["filas_despues"], 1029)
        self.assertEqual(resultado["filas_nuevas_insertadas"], 0)
        self.assertEqual(resultado["filas_omitidas_duplicadas"], 1029)

    def test_05_auditoria_cruce_registro_ingresos_y_prevencion_doble_conteo(self):
        """Cruce con RegistroIngresos documenta 18 grupos, 14 coincidentes y 4 no incluidos en CSV."""
        mock_cursor = MagicMock()

        # Simular los 18 grupos agrupados de las 38 filas de RegistroIngresos
        filas_simuladas = [
            (date(2026, 4, 10), 12, "Central", 2),
            (date(2026, 4, 10), 13, "Central", 2),
            (date(2026, 4, 10), 15, "Tarma", 1),
            (date(2026, 4, 29), 14, "Central", 3),
            (date(2026, 5, 4), 15, "Central", 1),
            (date(2026, 5, 5), 11, "Central", 8),
            (date(2026, 5, 5), 12, "Central", 8),
            (date(2026, 5, 5), 17, "Central", 2),
            (date(2026, 7, 7), 16, "Central", 1),
            (date(2026, 9, 7), 1, "Central", 1),
            (date(2026, 9, 7), 9, "Central", 1),
            (date(2026, 9, 7), 11, "Central", 1),
            (date(2026, 9, 13), 10, "Central", 1),
            (date(2026, 9, 16), 13, "Central", 1),
            (date(2026, 9, 22), 21, "Central", 1),
            (date(2026, 9, 24), 18, "Central", 2),
            (date(2026, 9, 24), 18, "La Merced", 1),
            (date(2026, 9, 28), 15, "Central", 1),
        ]
        mock_cursor.fetchall.return_value = filas_simuladas

        cruce = auditar_cruce_con_registro_ingresos(mock_cursor, self.df_csv)

        self.assertEqual(cruce["total_grupos_bd"], 18)
        self.assertEqual(cruce["total_visitas_bd"], 38)
        self.assertEqual(cruce["grupos_en_csv"], 14)
        self.assertEqual(cruce["grupos_no_en_csv"], 4)
        self.assertEqual(cruce["grupos_conteo_identico"], 7)
        self.assertEqual(cruce["visitas_conteo_identico"], 25)
        self.assertEqual(cruce["grupos_csv_mayor"], 7)
        self.assertEqual(cruce["visitas_no_en_csv"], 4)

        # Comprobar las 4 franjas que no están en el CSV
        detalles_no_csv = [(d["fecha"], d["hora"], d["sede"]) for d in cruce["detalle_no_en_csv"]]
        self.assertIn(("2026-09-07", 1, "Central"), detalles_no_csv)
        self.assertIn(("2026-09-13", 10, "Central"), detalles_no_csv)
        self.assertIn(("2026-09-22", 21, "Central"), detalles_no_csv)
        self.assertIn(("2026-09-24", 18, "La Merced"), detalles_no_csv)

    def test_06_separacion_funciones_ml_no_usa_dni(self):
        """obtener_historial_agrupado prioriza AfluenciaHistoricaAgregada sin campos personales."""
        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # Simular retorno desde AfluenciaHistoricaAgregada
            mock_cursor.description = [("Fecha",), ("Hora",), ("CantidadIngresos",)]
            mock_cursor.fetchall.return_value = [
                (date(2026, 4, 10), 12, 2),
                (date(2026, 4, 10), 13, 2),
                (date(2026, 4, 11), 8, 15),
                (date(2026, 4, 12), 9, 20),
                (date(2026, 4, 13), 10, 25),
            ]

            df_hist = obtener_historial_agrupado(fecha_objetivo="2026-04-14", sede="Central")

            # Verificar que no hay columnas personales ni DNI
            self.assertIn("Fecha", df_hist.columns)
            self.assertIn("Hora", df_hist.columns)
            self.assertIn("CantidadIngresos", df_hist.columns)
            self.assertNotIn("DNI", df_hist.columns)
            self.assertNotIn("AlumnoID", df_hist.columns)
            self.assertNotIn("NombreCompleto", df_hist.columns)

            # Verificar que se consultó AfluenciaHistoricaAgregada con filtro EsHorarioAtipico = 0
            llamada = mock_cursor.execute.call_args_list[0][0]
            sql = llamada[0]
            self.assertIn("AfluenciaHistoricaAgregada", sql)
            self.assertIn("EsHorarioAtipico = 0", sql)

    def test_07_exclusion_cierre_agosto_y_atipicos_del_entrenamiento(self):
        """Las 65 filas del cierre de agosto y las 4 atípicas se conservan en histórico pero se excluyen del entrenamiento."""
        # 1. Verificar sobre el DataFrame completo del CSV (1,029 filas)
        self.assertEqual(len(self.df_csv), 1029)
        filas_agosto = self.df_csv[self.df_csv["EsCierreAgosto"] == 1]
        filas_atipicas = self.df_csv[self.df_csv["EsHorarioAtipico"] == 1]
        self.assertEqual(len(filas_agosto), 65)
        self.assertEqual(len(filas_atipicas), 4)

        # Verificar que no se solapan
        solapadas = self.df_csv[(self.df_csv["EsCierreAgosto"] == 1) & (self.df_csv["EsHorarioAtipico"] == 1)]
        self.assertEqual(len(solapadas), 0)

        # Filtrar exactamente como la consulta de entrenamiento ML
        filas_entrenamiento = self.df_csv[
            (self.df_csv["EsCierreAgosto"] == 0) & (self.df_csv["EsHorarioAtipico"] == 0)
        ]
        self.assertEqual(len(filas_entrenamiento), 1029 - 65 - 4)  # 960 filas netas
        self.assertEqual(int(filas_entrenamiento["CantidadIngresos"].sum()), 32566)
        self.assertEqual(filas_entrenamiento["Fecha"].nunique(), 82)

        # 2. Verificar que obtener_historial_agrupado incluye ambos filtros en la consulta SQL
        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.description = [("Fecha",), ("Hora",), ("CantidadIngresos",)]
            mock_cursor.fetchall.return_value = [
                (date(2026, 4, 10), 12, 2)
            ]
            obtener_historial_agrupado(fecha_objetivo="2026-04-14", sede="Central")
            sql_ejecutado = mock_cursor.execute.call_args[0][0]
            self.assertIn("EsHorarioAtipico = 0", sql_ejecutado)
            self.assertIn("EsCierreAgosto = 0", sql_ejecutado)
            self.assertIn("EsCierre27Julio = 0", sql_ejecutado)
            self.assertIn("EsFeriado = 0", sql_ejecutado)

    def test_08_tratamiento_franja_parcial_20h(self):
        """La franja 20:00 se marca como parcial (45 min) en variables y servicio de predicción."""
        # 1. En el CSV/tabla histórica, las 41 filas de las 20:00 están marcadas como EsFranjaParcial = 1
        filas_20h = self.df_csv[self.df_csv["Hora"] == 20]
        self.assertEqual(len(filas_20h), 41)
        self.assertTrue((filas_20h["EsFranjaParcial"] == 1).all())

        # 2. En construir_variables_afluencia, hora=20 activa es_franja_parcial=True y duracion=45 min
        with patch("ml.services.datos_afluencia.obtener_historial_agrupado") as mock_hist:
            # Simular historial suficiente
            fechas_simuladas = [date(2026, 4, d) for d in range(10, 26)]
            filas = []
            for f in fechas_simuladas:
                for h in range(8, 21):
                    filas.append({"Fecha": f, "Hora": h, "CantidadIngresos": 10})
            mock_hist.return_value = pd.DataFrame(filas)

            # Para las 20:00
            vars_20, ctx_20 = construir_variables_afluencia("2026-04-27", 20, sede="Central")
            self.assertTrue(ctx_20["es_franja_parcial"])
            self.assertEqual(ctx_20["duracion_minutos"], 45)
            self.assertIn("45 minutos", ctx_20["nota_franja_parcial"])

            # Para una franja regular (ej. 10:00)
            vars_10, ctx_10 = construir_variables_afluencia("2026-04-27", 10, sede="Central")
            self.assertFalse(ctx_10["es_franja_parcial"])
            self.assertEqual(ctx_10["duracion_minutos"], 60)
            self.assertIsNone(ctx_10["nota_franja_parcial"])

        # 3. En predecir_afluencia_desde_bd, se incluye la advertencia de franja parcial
        with patch("ml.services.servicio_afluencia.construir_variables_afluencia") as mock_vars, \
             patch("ml.services.servicio_afluencia.obtener_predictor_afluencia") as mock_pred:
            mock_vars.return_value = (
                {"Hora": 20},
                {
                    "es_franja_parcial": True,
                    "duracion_minutos": 45,
                    "calidad_minima_historial": True,
                    "modo_demostracion": False,
                    "fecha": "2026-04-27",
                    "hora": 20,
                    "sede": "Central"
                }
            )
            mock_modelo = MagicMock()
            mock_modelo.predecir.return_value = {
                "nivel_estimado": "Medio",
                "probabilidades": {"Bajo": 0.1, "Medio": 0.8, "Alto": 0.1},
                "version_modelo": "1.0",
                "advertencia": ""
            }
            mock_pred.return_value = mock_modelo

            res_pred = predecir_afluencia_desde_bd("2026-04-27", 20, "Central")
            self.assertTrue(res_pred["prediccion"]["es_franja_parcial"])
            self.assertEqual(res_pred["prediccion"]["duracion_minutos"], 45)
            self.assertIn("[FRANJA PARCIAL: 45 MINUTOS]", res_pred["advertencia"])

    def test_09_falta_pronostico_confiable_sedes_datos_insuficientes(self):
        """Sedes sin historial suficiente no usan RegistroIngresos y devuelven estado explícito de datos insuficientes."""
        # 1. En obtener_historial_agrupado, una sede sin datos (ej. La Merced) lanza ValueError
        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.fetchall.return_value = []  # Sin datos en AfluenciaHistoricaAgregada

            with self.assertRaises(ValueError) as ctx:
                obtener_historial_agrupado("2026-10-15", sede="La Merced")
            self.assertIn("Historial insuficiente", str(ctx.exception))
            self.assertIn("No se completa con registros de prueba", str(ctx.exception))

            # Verificar que NUNCA intentó consultar RegistroIngresos como reemplazo
            sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
            for s in sqls:
                self.assertNotIn("RegistroIngresos", s)

        # 2. En el coordinador asistente, se traduce a error con categoría datos_insuficientes y código 400
        from llm.services.coordinador_asistente import procesar_pregunta
        with patch("llm.services.coordinador_asistente.predecir_afluencia_desde_bd") as mock_ml:
            mock_ml.side_effect = ValueError(
                "Historial insuficiente: La sede 'La Merced' no cuenta con datos históricos suficientes."
            )
            res_asistente = procesar_pregunta(
                "¿Cuál será el nivel de afluencia?",
                rol="SuperAdmin",
                fecha="2026-10-15",
                hora=10,
                sede="La Merced"
            )
            self.assertEqual(res_asistente["status"], "error")
            self.assertEqual(res_asistente["categoria_error"], "datos_insuficientes")
            self.assertEqual(res_asistente["codigo_http"], 400)
            self.assertEqual(res_asistente["datos"]["estado"], "datos_insuficientes")
            self.assertIn("Historial insuficiente", res_asistente["mensaje"])

    def test_10_exclusion_38_filas_prueba_en_consultas_reales(self):
        """Las 38 filas de prueba en RegistroIngresos (EsPrueba = 1) quedan excluidas de consultas reales."""
        from llm.services.consultas_biblioteca import (
            contar_ingresos_alumno_por_codigo,
            obtener_historial_persona,
            obtener_resumen_ingresos
        )

        with patch("llm.services.consultas_biblioteca.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)

            # 1. Probar contar_ingresos_alumno_por_codigo
            # fila_conteo: 0 ingresos reales (porque las pruebas fueron filtradas)
            # fila_cob: fecha_min, fecha_max, total=38, total_pruebas=38, total_reales=0
            mock_cursor.fetchone.side_effect = [
                (0, None, None, 0, "Ingeniería"),
                (datetime(2026, 4, 10), datetime(2026, 9, 28), 38, 38, 0)
            ]
            res_conteo = contar_ingresos_alumno_por_codigo("2020123456", rol="SuperAdmin")
            self.assertEqual(res_conteo["status"], "success")
            self.assertEqual(res_conteo["total_ingresos"], 0)
            self.assertIn("No se encontraron ingresos reales", res_conteo["mensaje"])
            self.assertIn("excluyeron 38 registros manuales de prueba", res_conteo["mensaje"])

            sql_conteo = mock_cursor.execute.call_args_list[0][0][0]
            self.assertIn("ISNULL(R.EsPrueba, 0) = 0", sql_conteo)

            # 2. Probar obtener_historial_persona
            mock_cursor.fetchall.side_effect = [
                [],  # Actuales (filtrados con EsPrueba = 0)
                []   # Históricos
            ]
            res_hist = obtener_historial_persona("Alumno", 1, rol="SuperAdmin")
            self.assertEqual(res_hist["total_ingresos"], 0)
            sql_actual = mock_cursor.execute.call_args_list[2][0][0]
            self.assertIn("ISNULL(R.EsPrueba, 0) = 0", sql_actual)

            # 3. Probar obtener_resumen_ingresos
            mock_cursor.fetchall.side_effect = [
                [],  # Actuales filtrados
                []   # Históricos
            ]
            res_resumen = obtener_resumen_ingresos("2026-04-10", "2026-09-28", rol="SuperAdmin")
            self.assertEqual(res_resumen["total_ingresos"], 0)
            sql_resumen = mock_cursor.execute.call_args_list[4][0][0]
            self.assertIn("ISNULL(R.EsPrueba, 0) = 0", sql_resumen)

    def test_11_exclusion_feriados_y_cierre_27_julio_del_entrenamiento(self):
        """El calendario acordado de feriados y el cierre del 27/07/2026 están marcados y excluidos del entrenamiento."""
        from ml.scripts.cargar_afluencia_historica import (
            FERIADOS_CONFIRMADOS_2026,
            CIERRE_CONFIRMADO_27_JULIO
        )

        # 1. Comprobar que en el CSV fuente (1,029 filas) hay exactamente 0 filas y 0 ingresos para 27/07 y feriados
        filas_cierre_27 = self.df_csv[self.df_csv["Fecha"] == CIERRE_CONFIRMADO_27_JULIO]
        self.assertEqual(len(filas_cierre_27), 0)
        self.assertEqual(int(filas_cierre_27["CantidadIngresos"].sum()) if len(filas_cierre_27) > 0 else 0, 0)
        self.assertEqual(len(self.df_csv[self.df_csv["EsCierre27Julio"] == 1]), 0)

        filas_feriados = self.df_csv[self.df_csv["Fecha"].isin(FERIADOS_CONFIRMADOS_2026)]
        self.assertEqual(len(filas_feriados), 0)
        self.assertEqual(int(filas_feriados["CantidadIngresos"].sum()) if len(filas_feriados) > 0 else 0, 0)
        self.assertEqual(len(self.df_csv[self.df_csv["EsFeriado"] == 1]), 0)

        # 2. Comprobar que obtener_historial_agrupado incluye los filtros SQL de exclusión
        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.description = [("Fecha",), ("Hora",), ("CantidadIngresos",)]
            mock_cursor.fetchall.return_value = [
                (date(2026, 4, 10), 12, 2)
            ]
            obtener_historial_agrupado(fecha_objetivo="2026-10-15", sede="Central")
            sql_ejecutado = mock_cursor.execute.call_args[0][0]
            self.assertIn("EsCierre27Julio = 0", sql_ejecutado)
            self.assertIn("EsFeriado = 0", sql_ejecutado)

        # 3. Comprobar que si hipotéticamente existieran registros para 27/07 o feriados en la tabla histórica,
        #    quedan 100% filtrados y NUNCA entrenan el modelo ni alimentan las variables predictivas
        with patch("ml.services.datos_afluencia.get_db_cursor") as mock_db:
            mock_cursor = MagicMock()
            mock_db.return_value.__enter__.return_value = (MagicMock(), mock_cursor)
            mock_cursor.description = [("Fecha",), ("Hora",), ("CantidadIngresos",)]
            # Simular retorno que incluye filas de fechas de cierre/feriados junto a filas operativas válidas
            mock_cursor.fetchall.return_value = [
                (date(2026, 4, 10), 12, 2),
                (date(2026, 5, 1), 10, 50),     # Feriado: Día del Trabajo
                (date(2026, 7, 27), 11, 40),    # Cierre confirmado puente 27 de julio
                (date(2026, 7, 28), 12, 35),    # Feriado: Fiestas Patrias
                (date(2026, 4, 11), 8, 15),
                (date(2026, 4, 12), 9, 20),
            ]
            df_filtrado = obtener_historial_agrupado(fecha_objetivo="2026-10-15", sede="Central")

            fechas_resultantes = set(df_filtrado["Fecha"].astype(str))
            self.assertNotIn("2026-07-27", fechas_resultantes)
            self.assertNotIn("2026-05-01", fechas_resultantes)
            self.assertNotIn("2026-07-28", fechas_resultantes)
            self.assertIn("2026-04-10", fechas_resultantes)
            self.assertIn("2026-04-11", fechas_resultantes)


if __name__ == "__main__":
    unittest.main()
