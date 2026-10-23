"""Lógica del protocolo histórico con fixtures artificiales, sin datos ni entrenamientos.

No reproduce métricas históricas ni certifica cobertura. El calendario operativo
vigente y el H1 del dashboard se verifican en sus pruebas propias.
"""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from ml.scripts import experimento_comparativo_retorno as experimento
from ml.scripts import backtest_robustez_retorno as backtest
from ml.services import retorno_h1_experimental as h1


class TestProtocoloRetornoSintetico(unittest.TestCase):
    def test_particiones_cronologicas_purgan_limites_con_fixture_minimo(self):
        # Etiquetas artificiales para comprobar filtros, no observaciones reales.
        fechas = [date(2026, 6, 1), date(2026, 7, 31),
                  date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 29), date(2026, 9, 30),
                  date(2026, 10, 1), date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 20)]
        panel = pd.DataFrame({'FechaRef': fechas, 'target_h1': [0, 1]*5+[np.nan],
                              'target_h7': [1, 0]*5+[np.nan]})
        cal = sum(backtest.construir_calendarios_por_semestre(), [])
        for horizon in (1, 7):
            with self.subTest(horizon=horizon):
                parts = experimento.obtener_particiones_purgadas(panel, cal, horizonte_dias=horizon)
                self.assertEqual(len(parts['train']), 2)
                self.assertEqual(len(parts['val']), 3 if horizon == 1 else 1)
                self.assertEqual(len(parts['test']), 4 if horizon == 1 else 2)
                self.assertNotIn(date(2026, 9, 30), set(parts['val'].FechaRef))
                last_train = max(parts['train'].FechaRef)
                last_val = max(parts['val'].FechaRef)
                self.assertLess(cal[cal.index(last_train)+horizon], min(parts['val'].FechaRef))
                self.assertLess(cal[cal.index(last_val)+horizon], min(parts['test'].FechaRef))
                self.assertFalse(parts['test'][parts['col_target']].isna().any())

    def test_constructor_legacy_censura_etiquetas_sin_leer_csv_personales(self):
        fechas = ['2026-10-09', '2026-10-19', '2026-10-20']
        panel = pd.DataFrame({'AlumnoID': [1]*3, 'FechaRef': fechas})
        visitas = pd.DataFrame({'AlumnoID': [1], 'Fecha': ['2026-10-20']})
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(experimento.pd, 'read_csv', side_effect=[panel, visitas]) as read:
            result, _ = experimento.preparar_datos_panel_y_visitas()
        self.assertEqual(read.call_count, 2)
        by_day = result.set_index('FechaRef')
        self.assertEqual(by_day.loc[date(2026, 10, 19), 'target_h1'], 1)
        self.assertTrue(pd.isna(by_day.loc[date(2026, 10, 20), 'target_h1']))
        self.assertTrue(pd.isna(by_day.loc[date(2026, 10, 19), 'target_h7']))

    def test_ausencia_de_csv_sigue_siendo_error_explicito_del_constructor(self):
        with patch.object(Path, 'exists', return_value=False), \
             patch.object(experimento.pd, 'read_csv') as read:
            with self.assertRaisesRegex(FileNotFoundError, 'datasets preparados'):
                experimento.preparar_datos_panel_y_visitas()
        read.assert_not_called()

    def test_estadisticas_excluyen_etiquetas_desconocidas_y_no_inventan_ceros(self):
        calendar = [date(2026, 10, d) for d in (1, 2, 5)]
        frame = pd.DataFrame({'FechaRef': calendar, 'target_h1': [1, np.nan, 0]})
        stats = backtest.extraer_estadisticas_particion(frame, calendar, 'target_h1')
        self.assertEqual(stats['num_obs'], 2)
        self.assertEqual(stats['fechas_con_obs'], 2)
        self.assertEqual(stats['fechas_calendario'], 3)
        empty = backtest.extraer_estadisticas_particion(frame.iloc[1:2], calendar, 'target_h1')
        self.assertEqual(empty['num_obs'], 0)
        self.assertEqual(empty['fecha_min'], 'N/A')

    def test_validacion_insuficiente_no_entrena_ni_selecciona_umbral(self):
        factory = MagicMock(side_effect=AssertionError('No entrenar'))
        frame = pd.DataFrame({'variable': [1, 2]})
        labels = pd.Series([0, 1])
        self.assertEqual(backtest.evaluar_umbral_validacion_con_suficiencia(
            factory, frame, labels, frame, labels, n_fechas_val=2),
            (.5, False, 'insuficiente_por_purga'))
        factory.assert_not_called()

    def test_cobertura_h1_desconocida_no_se_interpreta_como_ausencia(self):
        profile = h1._profile(load_model=False)
        ref = date(2026, 10, 22)
        unknown = date(2026, 10, 19)
        confirmed = profile['confirmed'] - {unknown}
        recipe = profile['recipe']
        self.assertEqual(recipe.status(unknown, confirmed), 'desconocido_captura')
        self.assertIsNone(recipe.feature_row({date(2026, 10, d): 1 for d in (14, 15, 16)},
                                             ref, date(2026, 10, 23), confirmed))
