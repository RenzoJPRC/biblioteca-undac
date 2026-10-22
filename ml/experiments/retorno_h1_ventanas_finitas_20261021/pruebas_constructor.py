"""Pruebas sintéticas, sin BD ni entrenamiento."""
import unittest
from datetime import date,timedelta

import pandas as pd

from ml.scripts import retorno_h1_ventanas_finitas as exp


def confirmed():
    return {d.date() for d in pd.date_range(exp.SOURCE_START,exp.CUT)}


class TestVentanasRetornoH1(unittest.TestCase):
    def test_24_julio_censura_objetivo_y_30_dias(self):
        c=confirmed()
        self.assertEqual(exp.target_h1(date(2026,7,22),c),(None,"objetivo_no_confirmable"))
        self.assertIsNone(exp.window_days(date(2026,7,31),30,c))
        self.assertIsNotNone(exp.window_days(date(2026,8,24),30,c))

    def test_desconocido_captura_no_se_trata_como_cierre_o_cero(self):
        c=confirmed()-{date(2026,9,18)}
        self.assertIsNone(exp.window_days(date(2026,10,18),30,c))
        self.assertIsNotNone(exp.window_days(date(2026,10,19),30,c))
        self.assertEqual(exp.target_h1(date(2026,9,17),c),(None,"objetivo_no_confirmable"))

    def test_receso_no_aporta_visitas_ni_denominador(self):
        c=confirmed(); ref=date(2026,8,31); target=date(2026,9,1)
        visits={date(2026,8,24):1,date(2026,8,25):2,date(2026,8,26):3}
        base=exp.feature_row(visits,ref,target,c)
        visits[date(2026,8,3)]=999999
        perturbed=exp.feature_row(visits,ref,target,c)
        self.assertEqual(base,perturbed)
        self.assertEqual(base['dias_apertura_30d'],5)
        self.assertEqual(base['tasa_asistencia_30d'],3/5)
        self.assertEqual(base['visitas_30d'],6)
        self.assertEqual(exp.target_h1(date(2026,7,31),c),(date(2026,8,24),None))

    def test_ref_objetivo_y_posteriores_no_alteran_variables(self):
        c=confirmed(); ref=date(2026,10,20); target=date(2026,10,21)
        visits={date(2026,10,14):1,date(2026,10,15):2,date(2026,10,16):3}
        base=exp.feature_row(visits,ref,target,c)
        visits.update({ref:999,target:99999,target+timedelta(days=1):999999})
        self.assertEqual(base,exp.feature_row(visits,ref,target,c))
        self.assertEqual(base['tasa_asistencia_7d'],3/5)
        self.assertEqual(base['recencia_30d'],4)
        days=exp.window_days(ref,7,c)
        self.assertTrue(all(ref-timedelta(days=7)<=d<ref for d in days))

    def test_etiquetas_maduras_y_exportacion_sin_identificadores(self):
        c=confirmed()-{date(2026,9,18)}
        raw=pd.DataFrame([(123,d,1) for d in ['2026-10-05','2026-10-06','2026-10-07','2026-10-20','2026-10-21']],columns=['AlumnoID','Fecha','Visitas'])
        df,audit,daily,stats=exp.build(raw,c)
        row=df[df.FechaRef.eq('2026-10-20')].iloc[0]
        self.assertEqual(row.FechaObjetivo,'2026-10-21')
        self.assertEqual(row.target_h1,1)
        self.assertTrue(df.FinVentana.lt(df.FechaRef).all())
        self.assertTrue(df.FechaRef.lt(df.FechaObjetivo).all())
        self.assertFalse(df.FechaRef.eq('2026-10-21').any())
        for frame in [df,audit,daily]:
            self.assertNotIn('AlumnoID',frame.columns)
        with self.assertRaises(ValueError):
            exp.build(raw[raw.Fecha.ne('2026-10-21')],c)

    def test_purga_no_usa_objetivo_de_octubre_en_validacion(self):
        self.assertEqual(exp.part_for(date(2026,9,30),date(2026,10,1)),'solo_ajuste_final')
        self.assertEqual(exp.part_for(date(2026,9,29),date(2026,9,30)),'val')


if __name__=='__main__':
    unittest.main()
