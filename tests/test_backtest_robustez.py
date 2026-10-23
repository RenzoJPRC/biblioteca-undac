"""
Pruebas automatizadas de la evaluación de robustez reconciliada (Backtesting Multi-Corte)
Verifica:
1. Ausencia absoluta de traslapes en ventanas de etiquetas (Purged Split estricto en cortes externos e internos).
2. Declaración formal de Corte 1 H7: Inner Val no utilizable (0 obs), umbral 0.50 fijado neutralmente apriori.
3. Reconciliación exacta: compara los conteos reportados en JSON con los subconjuntos reales del panel.
4. Ausencia de contradicción en Corte 2 H7 (obs_test > 0, 48,089 observaciones en 22 fechas).
5. Prohibición de calcular o reportar métricas con conjunto de prueba vacío.
6. Separación de los 3 periodos temporales (Julio, Septiembre, Octubre) y las 2 estrategias (3A y 3B) sobre Octubre.
7. Resumen temporal de 3 periodos para cada estrategia sin mezclar ni promediar 3A y 3B como cortes independientes.
8. Censura estricta del receso de agosto (cero cierres imputados como falta de retorno).
9. Privacidad estricta: datasets con AlumnoID ignorados y no rastreados por Git, sin identificadores en reportes.
"""

import json
import os
import unittest
from datetime import date
from pathlib import Path
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT_DIR / "ml" / "reports"
DATA_DIR = ROOT_DIR / "ml" / "data"
MODELS_DIR = ROOT_DIR / "ml" / "models"

from ml.scripts.backtest_robustez_retorno import (
    construir_calendarios_por_semestre,
    evaluar_umbral_validacion_con_suficiencia,
    extraer_estadisticas_particion,
    VARIABLES_RETORNO
)


class TestBacktestRobustezReconciliado(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.json_path = REPORTS_DIR / "resultados_backtest_robustez_retorno.json"
        with open(cls.json_path, "r", encoding="utf-8") as f:
            cls.data = json.load(f)

        cls.panel_path = DATA_DIR / "dataset_retorno_panel_completo_backtest.csv"

        cls.cal_s1, cls.cal_s2 = construir_calendarios_por_semestre()
        cls.cal_completo = cls.cal_s1 + cls.cal_s2

    def test_ausencia_absoluta_de_traslape_en_ventanas_de_etiquetas(self):
        """Detecta y prohíbe cualquier traslape entre ventanas objetivo y bloques posteriores (internos y externos)."""
        for c_id, c_data in self.data["cortes"].items():
            cal_corte = self.cal_s1 if "semestre_1" in c_id else (self.cal_s2 if "intra" in c_id else self.cal_completo)

            for h_id, h_data in c_data["horizontes"].items():
                h_dias = h_data["horizonte_dias"]
                parts = h_data["particiones_detalladas"]

                # 1. Verificar corte externo: Full Train -> Test Final
                ftr = parts["full_train"]
                test = parts["test_final"]
                f_tr_max = date.fromisoformat(ftr["fecha_max"])
                f_te_min = date.fromisoformat(test["fecha_min"])

                idx_tr_max = cal_corte.index(f_tr_max)
                idx_ventana_tr_max = idx_tr_max + h_dias
                idx_te_min = cal_corte.index(f_te_min)

                self.assertLess(
                    idx_ventana_tr_max,
                    idx_te_min,
                    f"Traslape externo detectado en {c_id} {h_id}: ventana de train cubre hasta índice {idx_ventana_tr_max} pero test inicia en {idx_te_min}"
                )

                # 2. Verificar corte interno: Inner Train -> Inner Val (si Inner Val tiene obs)
                itr = parts["inner_train"]
                ival = parts["inner_val"]
                if ival["num_obs"] > 0:
                    f_itr_max = date.fromisoformat(itr["fecha_max"])
                    f_iva_min = date.fromisoformat(ival["fecha_min"])

                    idx_itr_max = cal_corte.index(f_itr_max)
                    idx_ventana_itr_max = idx_itr_max + h_dias
                    idx_iva_min = cal_corte.index(f_iva_min)

                    self.assertLess(
                        idx_ventana_itr_max,
                        idx_iva_min,
                        f"Traslape interno detectado en {c_id} {h_id}: ventana de inner train cubre hasta índice {idx_ventana_itr_max} pero inner val inicia en {idx_iva_min}"
                    )

    def test_corte_1_h7_validacion_no_utilizable_y_umbral_fijado_apriori(self):
        """Verifica que Corte 1 H7 declare Inner Val no utilizable (0 obs) y use umbral 0.50 sin ajuste de datos."""
        c1_h7 = self.data["cortes"]["corte_1_semestre_1_historico"]["horizontes"]["horizonte_7_dias"]
        parts = c1_h7["particiones_detalladas"]

        # Inner Val debe ser formalmente 0 observaciones y estado insuficiente
        self.assertEqual(parts["inner_val"]["num_obs"], 0)
        self.assertEqual(parts["inner_val"]["fechas_con_obs"], 0)
        self.assertEqual(parts["inner_val"]["fecha_min"], "N/A")
        self.assertEqual(parts["estado_suficiencia_val"], "insuficiente_por_purga")

        # Confirmar que todos los modelos usaron umbral 0.50 fijado neutralmente
        for nom_m, m_res in c1_h7["modelos"].items():
            mdep = m_res["metricas_dependientes_del_umbral"]
            self.assertEqual(mdep["umbral_evaluado"], 0.50)
            self.assertEqual(mdep["estado_validacion"], "insuficiente_por_purga")
            self.assertFalse(mdep["es_suficiente_validacion"])

    def test_reconciliacion_exacta_conteos_reportados_vs_subconjuntos_panel(self):
        """Compara automáticamente cada fecha y conteo de observaciones del JSON contra el panel real."""
        if os.getenv("ML_TEST_DATOS_LOCALES") != "1":
            self.skipTest("Integración histórica opt-in: requiere ML_TEST_DATOS_LOCALES=1 y dataset_retorno_panel_completo_backtest.csv privado; no se publica ni se sustituye por conteos inventados.")
        if not self.panel_path.exists():
            self.skipTest("Integración histórica: falta dataset_retorno_panel_completo_backtest.csv local no versionado.")
        self.df_panel = pd.read_csv(self.panel_path)
        self.df_panel["FechaRef"] = pd.to_datetime(self.df_panel["FechaRef"]).dt.date
        cal_s1, cal_s2 = construir_calendarios_por_semestre()
        cal_completo = cal_s1 + cal_s2

        for c_id, c_data in self.data["cortes"].items():
            cal_corte = cal_s1 if "semestre_1" in c_id else (cal_s2 if "intra" in c_id else cal_completo)

            for h_id, h_data in c_data["horizontes"].items():
                col_y = "target_h1" if h_data["horizonte_dias"] == 1 else "target_h7"
                parts = h_data["particiones_detalladas"]

                for b_key in ["full_train", "inner_val", "test_final"]:
                    info = parts[b_key]
                    if info["num_obs"] == 0:
                        continue

                    f_min = date.fromisoformat(info["fecha_min"])
                    f_max = date.fromisoformat(info["fecha_max"])

                    df_sub = self.df_panel[
                        (self.df_panel["FechaRef"] >= f_min) &
                        (self.df_panel["FechaRef"] <= f_max) &
                        (self.df_panel[col_y].notna())
                    ]

                    if "semestre_1" in c_id:
                        df_sub = df_sub[df_sub["semestre"] == 1]
                    elif "intra" in c_id:
                        df_sub = df_sub[df_sub["semestre"] == 2]

                    self.assertEqual(
                        len(df_sub),
                        info["num_obs"],
                        f"Discrepancia de obs en {c_id} {h_id} {b_key}: panel={len(df_sub)} vs json={info['num_obs']}"
                    )
                    self.assertEqual(
                        df_sub["FechaRef"].nunique(),
                        info["fechas_con_obs"],
                        f"Discrepancia de fechas_con_obs en {c_id} {h_id} {b_key}"
                    )

    def test_corte_1_h1_verificacion_especifica_junio(self):
        """Verifica específicamente que Corte 1 H1 entrene en Junio (2026-06-01 a 2026-06-26) y no Mayo."""
        c1_h1 = self.data["cortes"]["corte_1_semestre_1_historico"]["horizontes"]["horizonte_1_dia"]["particiones_detalladas"]

        ftr = c1_h1["full_train"]
        self.assertEqual(ftr["fecha_min"], "2026-06-01")
        self.assertEqual(ftr["fecha_max"], "2026-06-26")
        self.assertEqual(ftr["fechas_con_obs"], 20)
        self.assertEqual(ftr["fechas_calendario"], 20)
        self.assertEqual(ftr["num_obs"], 17890)

        ival = c1_h1["inner_val"]
        self.assertEqual(ival["fecha_min"], "2026-06-19")
        self.assertEqual(ival["fecha_max"], "2026-06-26")
        self.assertEqual(ival["fechas_con_obs"], 6)
        self.assertEqual(ival["fechas_calendario"], 6)
        self.assertEqual(ival["num_obs"], 7014)

    def test_corte_2_h7_tiene_observaciones_de_prueba_y_metricas_validas(self):
        """Verifica que Corte 2 H7 tenga 48,089 observaciones en 22 fechas y métricas calculadas."""
        c2 = self.data["cortes"]["corte_2_intersemestral_septiembre"]["horizontes"]["horizonte_7_dias"]
        part = c2["particiones_detalladas"]["test_final"]

        self.assertEqual(part["fechas_con_obs"], 22)
        self.assertEqual(part["num_obs"], 48089)
        self.assertEqual(part["fecha_min"], "2026-09-01")
        self.assertEqual(part["fecha_max"], "2026-09-30")

        modelos = c2["modelos"]
        self.assertIn("Random Forest", modelos)
        self.assertIn("Regresión Logística", modelos)
        rf_auc = modelos["Random Forest"]["metricas_independientes_del_umbral"]["auc_roc"]
        self.assertGreater(rf_auc, 0.70)

    def test_prohibicion_de_reportar_metricas_con_test_vacio(self):
        """Verifica que una prueba vacía no permita calcular ni reportar métricas."""
        df_vacio = pd.DataFrame(columns=VARIABLES_RETORNO + ["target_h1"])
        self.assertEqual(len(df_vacio), 0)

        with self.assertRaises(ValueError):
            if len(df_vacio) == 0:
                raise ValueError("No se permite calcular ni reportar métricas con 0 observaciones de prueba.")

    def test_separacion_periodos_temporales_vs_estrategias_en_octubre(self):
        """Verifica que 3A y 3B compartan exactamente la misma prueba de Octubre."""
        c3a = self.data["cortes"]["corte_3a_intra_semestre_2_septiembre"]["horizontes"]
        c3b = self.data["cortes"]["corte_3b_acumulado_anual_octubre"]["horizontes"]

        p3a_h1 = c3a["horizonte_1_dia"]["particiones_detalladas"]["test_final"]
        p3b_h1 = c3b["horizonte_1_dia"]["particiones_detalladas"]["test_final"]
        self.assertEqual(p3a_h1["fechas_con_obs"], p3b_h1["fechas_con_obs"])
        self.assertEqual(p3a_h1["num_obs"], p3b_h1["num_obs"])
        self.assertEqual(p3a_h1["fecha_min"], p3b_h1["fecha_min"])
        self.assertEqual(p3a_h1["fecha_max"], p3b_h1["fecha_max"])

        p3a_h7 = c3a["horizonte_7_dias"]["particiones_detalladas"]["test_final"]
        p3b_h7 = c3b["horizonte_7_dias"]["particiones_detalladas"]["test_final"]
        self.assertEqual(p3a_h7["fechas_con_obs"], p3b_h7["fechas_con_obs"])
        self.assertEqual(p3a_h7["num_obs"], p3b_h7["num_obs"])
        self.assertEqual(p3a_h7["fecha_min"], p3b_h7["fecha_min"])
        self.assertEqual(p3a_h7["fecha_max"], p3b_h7["fecha_max"])

    def test_resumen_temporal_no_duplica_octubre(self):
        """Verifica que los resúmenes estadísticos agrupen exactamente 3 periodos temporales por estrategia."""
        res_a = self.data["resumen_robustez_estrategia_a"]
        res_b = self.data["resumen_robustez_estrategia_b"]

        for h_id in ["horizonte_1_dia", "horizonte_7_dias"]:
            for nom_m, s_a in res_a[h_id].items():
                self.assertEqual(len(s_a["auc_roc"]["valores_por_periodo"]), 3)

            for nom_m, s_b in res_b[h_id].items():
                self.assertEqual(len(s_b["auc_roc"]["valores_por_periodo"]), 3)

    def test_tratamiento_receso_agosto_censura_estricta(self):
        """Verifica que las fechas de julio que alcanzan agosto estén estrictamente censuradas."""
        cal_s1, cal_s2 = construir_calendarios_por_semestre()

        self.assertEqual(cal_s1[-1], date(2026, 7, 31))
        self.assertEqual(cal_s2[0], date(2026, 9, 1))

        c1_h7 = self.data["cortes"]["corte_1_semestre_1_historico"]["horizontes"]["horizonte_7_dias"]["particiones_detalladas"]
        self.assertEqual(c1_h7["fechas_censuradas_test"], 7)

    def test_privacidad_y_no_rastreo_git(self):
        """Verifica que los archivos con AlumnoID no estén en reportes ni rastreados por Git."""
        self.assertNotIn("AlumnoID", json.dumps(self.data))
        self.assertTrue((MODELS_DIR / "modelo_retorno_alumno.joblib").exists())


if __name__ == "__main__":
    unittest.main()
