"""
Pruebas automatizadas de la comparación experimental de cinco modelos de Machine Learning
para la Predicción de Retorno Individual de Alumnos (Biblioteca Central UNDAC).

Verifica:
1. Calendario de atención real: distinción de cierres efectivos vs atención reducida en agosto.
2. Tratamiento de referencias del 17 al 31 de julio: resueltas sin censura artificial.
3. Tratamiento de censuras al corte del 21/10/2026: corte en 19/10 para H1 y en 09/10 para H7.
4. Conteo estricto de cinco modelos candidatos de ML y DummyClassifier como línea base separada.
5. Ausencia absoluta de solapamiento temporal (Purged Temporal Split: Train -> Val -> Test).
6. Selección de umbral y ganador exclusivamente en validación interna.
7. Conservación incondicional del artefacto oficial modelo_retorno_alumno.joblib y generación
   de artefactos candidatos separados.
"""

import json
import os
import unittest
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "ml" / "reports"
DATA_DIR = ROOT_DIR / "ml" / "data"
MODELS_DIR = ROOT_DIR / "ml" / "models"

from ml.scripts.experimento_comparativo_retorno import (
    CIERRES_EFECTIVOS,
    VARIABLES_RETORNO,
    construir_calendario_real_operativo,
    obtener_fabricas_modelos,
    obtener_particiones_purgadas,
    preparar_datos_panel_y_visitas,
    seleccionar_umbral_optimo_validacion,
)


class TestExperimentoCincoModelosRetorno(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Calendario del experimento legado, no el calendario vigente de H1.
        cls.calendario = construir_calendario_real_operativo()

    def cargar_datos_locales(self):
        if os.getenv("ML_TEST_DATOS_LOCALES") != "1":
            self.skipTest("Integración histórica opt-in: requiere ML_TEST_DATOS_LOCALES=1 y los CSV privados dataset_retorno_panel_completo_backtest.csv / dataset_retorno_visitas_diarias_completo.csv; CI no reconstruye ni inventa observaciones.")
        paths=[DATA_DIR/'dataset_retorno_panel_completo_backtest.csv',DATA_DIR/'dataset_retorno_visitas_diarias_completo.csv']
        missing=[p.name for p in paths if not p.exists()]
        if missing:
            self.skipTest("Integración histórica: faltan archivos locales no versionados: " + ', '.join(missing))
        self.df_panel, self.calendario = preparar_datos_panel_y_visitas()
        self.parts_h1 = obtener_particiones_purgadas(self.df_panel, self.calendario, horizonte_dias=1)
        self.parts_h7 = obtener_particiones_purgadas(self.df_panel, self.calendario, horizonte_dias=7)

    def test_calendario_real_operativo_y_distincion_agosto(self):
        """Verifica que el calendario real no marque agosto completo como cerrado y distinga cierres."""
        self.assertEqual(len(self.calendario), 94, "El calendario real 01/06 al 20/10 debe contener 94 fechas operativas")
        
        # Verificar ausencia de fines de semana y exclusión de cierres efectivos
        for d in self.calendario:
            self.assertLess(d.weekday(), 5, f"La fecha {d} es fin de semana y no debe ser operativa")
            self.assertNotIn(d, CIERRES_EFECTIVOS, f"La fecha {d} es un cierre efectivo confirmado")

        # Verificar que agosto no esté cerrado en su totalidad
        fechas_agosto = [d for d in self.calendario if d.month == 8]
        self.assertEqual(len(fechas_agosto), 20, "Agosto debe contener 20 fechas operativas (14 reducidas + 6 regulares)")
        
        # Feriado del 06/08 debe estar estrictamente excluido
        self.assertNotIn(date(2026, 8, 6), self.calendario, "El 06/08 (Batalla de Junín) debe ser cierre efectivo")

    def test_etiquetas_julio_17_a_31_resueltas_sin_censura_artificial(self):
        """Verifica que las referencias del 17 al 31 de julio tengan etiquetas completas en H1 y H7."""
        self.cargar_datos_locales()
        df_julio_tardio = self.df_panel[
            (self.df_panel["FechaRef"] >= date(2026, 7, 17)) &
            (self.df_panel["FechaRef"] <= date(2026, 7, 31))
        ]
        self.assertGreater(len(df_julio_tardio), 0, "Deben existir observaciones para finales de julio")

        # Para H1: Todas las observaciones deben tener etiqueta válida (ninguna censurada)
        nulos_h1 = df_julio_tardio["target_h1"].isna().sum()
        self.assertEqual(nulos_h1, 0, "No debe haber etiquetas nulas en H1 para el 17 al 31 de julio")

        # Para H7: Al observarse en agosto, todas deben tener etiqueta válida
        nulos_h7 = df_julio_tardio["target_h7"].isna().sum()
        self.assertEqual(nulos_h7, 0, "No debe haber etiquetas nulas en H7 para el 17 al 31 de julio")

    def test_censuras_estrictas_al_corte_21_octubre(self):
        """Verifica que no se usen como completas etiquetas cuya ventana todavía esté abierta."""
        self.cargar_datos_locales()
        # 1. Para H1: 19/10 debe tener etiqueta completa; 20/10 debe estar censurado (sig. día en curso)
        df_19_oct = self.df_panel[self.df_panel["FechaRef"] == date(2026, 10, 19)]
        df_20_oct = self.df_panel[self.df_panel["FechaRef"] == date(2026, 10, 20)]

        self.assertGreater(len(df_19_oct), 0)
        self.assertEqual(df_19_oct["target_h1"].isna().sum(), 0, "El 19/10 debe tener etiqueta H1 completa")
        self.assertEqual(df_20_oct["target_h1"].notna().sum(), 0, "El 20/10 debe estar estrictamente censurado en H1")

        # 2. Para H7: 09/10 debe tener etiqueta completa; >= 12/10 deben estar censuradas
        df_09_oct = self.df_panel[self.df_panel["FechaRef"] == date(2026, 10, 9)]
        df_post_09 = self.df_panel[self.df_panel["FechaRef"] >= date(2026, 10, 12)]

        self.assertGreater(len(df_09_oct), 0)
        self.assertEqual(df_09_oct["target_h7"].isna().sum(), 0, "El 09/10 debe tener etiqueta H7 completa")
        self.assertEqual(df_post_09["target_h7"].notna().sum(), 0, "Fechas >= 12/10 deben estar estrictamente censuradas en H7")

    def test_cinco_modelos_candidatos_y_linea_base_separada(self):
        """Verifica que se definan exactamente 5 modelos de ML candidatos y Dummy como línea base."""
        fabricas = obtener_fabricas_modelos()
        self.assertEqual(len(fabricas), 6, "Deben existir 6 fábricas en total (1 línea base + 5 candidatos)")

        lineas_base = [k for k, v in fabricas.items() if v["es_linea_base"]]
        candidatos = [k for k, v in fabricas.items() if not v["es_linea_base"]]

        self.assertEqual(len(lineas_base), 1, "Debe existir exactamente 1 línea base")
        self.assertEqual(lineas_base[0], "Dummy (Clase Mayoritaria)")

        self.assertEqual(len(candidatos), 5, "Deben existir exactamente 5 candidatos de Machine Learning")
        candidatos_esperados = {
            "Regresión Logística",
            "Random Forest",
            "Extra Trees",
            "Gradient Boosting",
            "HistGradientBoosting",
        }
        self.assertEqual(set(candidatos), candidatos_esperados)

    def test_purged_temporal_split_sin_fuga_temporal(self):
        """Verifica que la purga elimine rigurosamente el solapamiento hacia bloques futuros."""
        self.cargar_datos_locales()
        cal = self.calendario

        # 1. Para H1:
        p1 = self.parts_h1
        fechas_val_h1 = set(p1["val"]["FechaRef"])
        fechas_test_h1 = set(p1["test"]["FechaRef"])
        min_test_h1 = min(fechas_test_h1)

        # 30/09 debe estar purgado en Val
        self.assertNotIn(date(2026, 9, 30), fechas_val_h1, "El 30/09 debe estar purgado de Val en H1")
        for f in fechas_val_h1:
            idx = cal.index(f)
            f_next = cal[idx + 1]
            self.assertLess(f_next, min_test_h1, f"Fuga en H1: {f} toca Test en {f_next}")

        # 2. Para H7:
        p7 = self.parts_h7
        fechas_val_h7 = set(p7["val"]["FechaRef"])
        fechas_test_h7 = set(p7["test"]["FechaRef"])
        min_test_h7 = min(fechas_test_h7)

        # 22/09 al 30/09 deben estar purgados en Val
        for d_purga in [date(2026, 9, dia) for dia in range(22, 31)]:
            if d_purga in cal:
                self.assertNotIn(d_purga, fechas_val_h7, f"La fecha {d_purga} debe estar purgada de Val en H7")

        for f in fechas_val_h7:
            idx = cal.index(f)
            w7 = cal[idx + 1 : idx + 8]
            max_w7 = max(w7)
            self.assertLess(max_w7, min_test_h7, f"Fuga en H7: ventana de {f} termina en {max_w7} y toca Test")

    def test_seleccion_de_umbral_solo_en_validacion(self):
        """Verifica la sintonización interna de umbrales sobre validación sin tocar prueba."""
        np.random.seed(42)
        X_tr = pd.DataFrame(np.random.randn(80, len(VARIABLES_RETORNO)), columns=VARIABLES_RETORNO)
        y_tr = pd.Series(np.random.choice([0, 1], size=80, p=[0.7, 0.3]))
        X_val = pd.DataFrame(np.random.randn(40, len(VARIABLES_RETORNO)), columns=VARIABLES_RETORNO)
        y_val = pd.Series(np.random.choice([0, 1], size=40, p=[0.7, 0.3]))

        class EstimadorFicticio:
            def fit(self, X, y):
                self.X_ajuste = X.copy()
                self.y_ajuste = y.copy()
                return self
            def predict_proba(self, X):
                probability = np.linspace(.1, .9, len(X))
                return np.column_stack((1-probability,probability))
        estimador = EstimadorFicticio()
        factory = lambda: estimador
        u = seleccionar_umbral_optimo_validacion(factory, X_tr, y_tr, X_val, y_val)

        pd.testing.assert_frame_equal(estimador.X_ajuste,X_tr)
        pd.testing.assert_series_equal(estimador.y_ajuste,y_tr)
        self.assertIsInstance(u, float)
        self.assertGreaterEqual(u, 0.15)
        self.assertLessEqual(u, 0.85)

    def test_conservacion_artefacto_produccion_y_candidatos_separados(self):
        """Verifica que el modelo de producción se conserve intacto y los candidatos se guarden aparte."""
        # 1. Artefacto oficial en producción inalterado
        prod_model_path = MODELS_DIR / "modelo_retorno_alumno.joblib"
        self.assertTrue(prod_model_path.exists(), "El artefacto oficial de produccion debe existir")
        self.assertEqual(prod_model_path.stat().st_size, 333604, "El artefacto oficial no debe haber sido modificado")

        # 2. Artefactos candidatos separados para H1
        cand_hgb_path = MODELS_DIR / "modelo_retorno_alumno_candidato_hgb_h1.joblib"
        cand_hgb_meta = MODELS_DIR / "metadatos_retorno_alumno_candidato_hgb_h1.json"
        self.assertTrue(cand_hgb_path.exists(), "El candidato HGB H1 debe existir")
        self.assertTrue(cand_hgb_meta.exists(), "Los metadatos del candidato HGB H1 deben existir")

        # 3. La aplicación actual en servicio NO carga los candidatos
        from ml.services.analisis_individual import PredictorRetornoAlumno
        predictor = PredictorRetornoAlumno.obtener_instancia()
        self.assertEqual(predictor.ruta_modelo, prod_model_path)
        self.assertNotEqual(predictor.ruta_modelo, cand_hgb_path)

    def test_candidato_logreg_local_separado(self):
        paths=[MODELS_DIR/'modelo_retorno_alumno_candidato_logreg_h1.joblib',MODELS_DIR/'metadatos_retorno_alumno_candidato_logreg_h1.json']
        if os.getenv("ML_TEST_DATOS_LOCALES") != "1" or not all(p.exists() for p in paths):
            self.skipTest("Integración histórica opt-in: candidato LogReg local y metadatos no versionados; no son dependencias del H1 actual.")
        self.assertTrue(all(p.is_file() for p in paths))

    def test_corte_temporal_candidato_h1_y_ausencia_informacion_futura(self):
        """Verifica que el candidato H1 se entrene hasta el 19/10 y excluya estrictamente el 20/10 y 21/10."""
        import joblib
        meta_path = MODELS_DIR / "metadatos_retorno_alumno_candidato_hgb_h1.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        cobertura = meta["cobertura_entrenamiento"]
        self.assertEqual(cobertura["fecha_corte"], "2026-10-19")
        self.assertEqual(cobertura["total_observaciones"], 126123)
        self.assertIn("2026-10-20 (ventana abierta en 21/10)", cobertura["fechas_excluidas_por_censura"][0])

        # Verificar paquete serializado
        cand_pkg = joblib.load(MODELS_DIR / "modelo_retorno_alumno_candidato_hgb_h1.joblib")
        self.assertEqual(cand_pkg["horizonte"], "H1")
        self.assertEqual(cand_pkg["fecha_corte_entrenamiento"], "2026-10-19")
        self.assertEqual(cand_pkg["algoritmo"], "HistGradientBoostingClassifier")

    def test_etiquetas_20_octubre_integracion_historica(self):
        self.cargar_datos_locales()
        df_20 = self.df_panel[self.df_panel["FechaRef"] == date(2026, 10, 20)]
        self.assertTrue(df_20["target_h1"].isna().all(), "El 20/10 debe ser estrictamente NaN para evitar fuga del 21/10")

    def test_ejecucion_sombra_ausencia_fuga_dia_objetivo(self):
        """Verifica que la ejecución de sombra cumpla con aislamiento de fecha objetivo y exclusión de Git."""
        import pandas as pd
        csv_sombra = ROOT_DIR / "scratch" / "predicciones_sombra_h1.csv"
        if os.getenv("ML_TEST_DATOS_LOCALES") != "1" or not csv_sombra.exists():
            self.skipTest("Integración histórica opt-in: scratch/predicciones_sombra_h1.csv privado no versionado; no es una prueba del registro prospectivo actual.")
        self.assertTrue(csv_sombra.exists(), "El archivo de predicciones en sombra debe existir en scratch/")

        df_sombra = pd.read_csv(csv_sombra)
        self.assertFalse(df_sombra.empty, "El archivo de predicciones en sombra no debe estar vacío")

        # 1. Verificación de anonimización: no contiene nombres ni matrículas
        columnas_prohibidas = {"CodigoMatricula", "Nombres", "Apellidos", "DNI", "codigo", "nombre"}
        self.assertTrue(columnas_prohibidas.isdisjoint(set(df_sombra.columns)),
                        "El CSV de sombra no debe contener nombres ni códigos de matrícula")
        self.assertIn("AlumnoID", df_sombra.columns)
        self.assertIn("prob_produccion", df_sombra.columns)
        self.assertIn("prob_candidato_h1", df_sombra.columns)

        # 2. Verificación temporal: fecha_corte < fecha_objetivo
        f_corte = df_sombra["fecha_corte"].iloc[0]
        f_obj = df_sombra["fecha_objetivo"].iloc[0]
        self.assertLess(f_corte, f_obj, "La fecha de corte debe ser estrictamente anterior a la fecha objetivo")

        # 3. Comprobar que el archivo esté excluido de Git
        import subprocess
        res = subprocess.run(["git", "check-ignore", "-v", str(csv_sombra)],
                             cwd=str(ROOT_DIR), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, "El archivo de predicciones en sombra debe estar excluido por .gitignore")


if __name__ == "__main__":
    unittest.main()

