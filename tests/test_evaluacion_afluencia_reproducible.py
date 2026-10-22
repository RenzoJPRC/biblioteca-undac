"""Pruebas sintéticas: no conexiones ni lecturas de registros personales."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from ml.scripts import evaluacion_afluencia_reproducible as ev


def raw(rows):
    return pd.DataFrame(rows, columns=["Fecha", "Hora", "CantidadOriginal", "CantidadHorario"])


class TestEvaluacionAfluenciaReproducible(unittest.TestCase):
    def test_calendario(self):
        for day in ["2026-08-01", "2026-08-14", "2026-08-23"]:
            self.assertEqual(ev.calendar(date.fromisoformat(day)), "cerrado_receso")
        self.assertEqual(ev.calendar(date(2026, 8, 24)), "atencion_segun_calendario")
        self.assertTrue(ev.calendar(date(2026, 10, 8)).startswith("cerrado"))
        self.assertTrue(ev.calendar(date(2026, 10, 3)).startswith("cerrado"))
        self.assertEqual(ev.calendar(date(2026, 7, 24)), "desconocido_24_julio")

    def test_ceros_solo_captura_confirmada_incluso_dia_sin_filas(self):
        _, evidence, _ = ev.build_dataset(raw([]), {"2026-09-29", "2026-10-03", "2026-08-12", "2026-07-24"})
        confirmed = evidence[evidence.Fecha.eq("2026-09-29")]
        self.assertTrue(confirmed.CeroGenerado.all())
        self.assertEqual(confirmed.CantidadIngresos.sum(), 0)
        self.assertEqual(int(evidence.CeroGenerado.sum()), 13)
        self.assertTrue(evidence.loc[evidence.Fecha.eq("2026-09-30"), "CantidadIngresos"].isna().all())

    def test_desconocidos_y_cierres_no_alimentan_rezagos(self):
        days = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-08-24"]
        frame = raw([(d, 8, i + 1, i + 1) for i, d in enumerate(days)] +
                    [("2026-08-12", 8, 9999, 9999), ("2026-07-24", 8, 8888, 8888)])
        df, evidence, _ = ev.build_dataset(frame, set(days) | {"2026-08-12", "2026-07-24"})
        row = df[(df.Fecha == "2026-08-24") & (df.Hora == 8)].iloc[0]
        self.assertEqual(row.IngresosUltimaFranjaConfirmada, 5)
        self.assertEqual(row.PromedioHistoricoHora, 3)
        self.assertEqual(row.IngresosQuintaFranjaConfirmada, 1)
        self.assertEqual(evidence.loc[(evidence.Fecha == "2026-08-12") & (evidence.Hora == 8), "CantidadObservada"].iloc[0], 9999)
        self.assertFalse(df.Fecha.isin(["2026-08-12", "2026-07-24"]).any())

    def test_duracion_parcial_y_postcierre(self):
        _, evidence, _ = ev.build_dataset(raw([("2026-09-29", 20, 5, 2)]), {"2026-09-29"})
        row = evidence[(evidence.Fecha == "2026-09-29") & (evidence.Hora == 20)].iloc[0]
        self.assertEqual(row.CantidadIngresos, 2)
        self.assertEqual(row.DuracionMinutos, 45)
        self.assertEqual(row.EsFranjaParcial, 1)
        self.assertIn("DuracionMinutos", ev.FEATURES)
        self.assertIn("<= CAST('20:45:00' AS TIME)", ev.SQL)

    def test_cobertura_historica_no_extiende_dias_ausentes(self):
        source = pd.DataFrame({"Fecha": ["2026-05-12", "2026-05-05"],
                               "EstadoOperativo": ["abierto_regular", "desconocido"]})
        self.assertEqual(ev.historical_coverage(source), {"2026-05-12"})

    def test_duplicados_invalidos_rechazados(self):
        with self.assertRaises(ValueError):
            ev.build_dataset(raw([("2026-09-29", 8, 1, 1)] * 2), set())
        with self.assertRaises(ValueError):
            ev.build_dataset(raw([("2026-09-29", 8, 1, 2)]), set())

    def test_seleccion_solo_septiembre(self):
        val = {"A": {"f1_macro": .6, "balanced_accuracy": .6},
               "B": {"f1_macro": .5, "balanced_accuracy": .9}}
        self.assertEqual(ev.select_candidate(val), "A")
        self.assertEqual(ev.select_candidate(dict(reversed(list(val.items())))), "A")

    def test_particiones_comunes_y_clases_insuficientes(self):
        frame = pd.DataFrame([(d, c) for d in ["2026-08-31", "2026-09-01", "2026-10-20"]
                              for c in ev.CLASSES for _ in range(5)], columns=["Fecha", "NivelAfluencia"])
        parts = ev.partitions(frame)
        self.assertEqual([len(f) for f in parts.values()], [15, 15, 15])
        with self.assertRaises(ValueError):
            ev.partitions(frame[frame.NivelAfluencia != "Alto"])

    def test_produccion_no_es_salida_permitida(self):
        before = ev.model_hashes()
        with self.assertRaises(ValueError):
            ev.run(ev.ROOT / "ml/models")
        self.assertEqual(before, ev.model_hashes())

    def test_extraccion_solo_select_agregado_cierra_sin_commit(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchall.return_value = [(date(2026, 9, 29), 8, 3, 3)]
        module = MagicMock()
        module.get_db_connection.return_value = conn
        with patch.dict("sys.modules", {"db": module}):
            frame = ev.extract()
        query = conn.cursor.return_value.execute.call_args.args[0]
        self.assertTrue(query.startswith("SELECT"))
        self.assertIn("GROUP BY", query)
        self.assertNotIn("CodigoMatricula", query)
        self.assertEqual(list(frame.columns), ["Fecha", "Hora", "CantidadOriginal", "CantidadHorario"])
        conn.commit.assert_not_called()
        conn.close.assert_called_once()
        conn.cursor.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
