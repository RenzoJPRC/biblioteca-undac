"""Reproduce causas de cobertura con agregados ficticios, sin BD ni predicciones reales."""
from contextlib import contextmanager
from datetime import date, datetime
import re
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app import app
from ml.services import datos_afluencia as data
from routes import admin_ml as routes


def historial(hasta):
    return pd.DataFrame([{'Fecha':d.date(),'Hora':10,'CantidadIngresos':12}
                         for d in pd.bdate_range('2026-06-01',hasta)])


class TestDiagnosticoAfluencia(unittest.TestCase):
    def test_refresco_y_predictor_consultan_ingresos_recientes_sin_depender_de_cache(self):
        """AHA antigua ya no impide incorporar la captura confirmada reciente."""
        refresco = MagicMock()
        reciente = historial('2026-10-21')
        refresco.fetchall.side_effect = [list(reciente.itertuples(index=False, name=None)), []]
        refresco.fetchone.return_value = (datetime(2026, 10, 21, 20, 30), 1000)
        compartida = MagicMock()
        compartida.description = [('Fecha',), ('Hora',), ('CantidadIngresos',)]

        @contextmanager
        def fuente(**kwargs):
            yield None, compartida

        with patch.dict(data._HISTORIAL_REFRESCADO_MEMORIA, {}, clear=True), patch.object(data, 'get_db_cursor', fuente):
            data.refrescar_analisis_afluencia_memoria(
                'Central', cursor_externo=refresco,
                ahora_referencia=datetime(2026, 10, 22, 9))
            self.assertEqual(data._HISTORIAL_REFRESCADO_MEMORIA['Central']['df'].Fecha.max(), date(2026, 10, 21))
            for corte in ['2026-09-28', '2026-10-21']:
                with self.subTest(corte_compartido=corte):
                    compartida.fetchall.side_effect = [
                        list(historial(corte).itertuples(index=False, name=None)),
                        list(reciente.itertuples(index=False, name=None))]
                    _, contexto = data.construir_variables_afluencia(date(2026, 10, 22), 10, 'Central')
                    self.assertEqual(contexto['ultima_fecha_disponible'], '2026-10-21')
                    self.assertFalse(contexto['modo_demostracion'])
        for cursor in (refresco, compartida):
            for llamada in cursor.execute.call_args_list:
                self.assertTrue(llamada.args[0].lstrip().startswith('SELECT'))
            cursor.connection.commit.assert_not_called()

    def test_historial_anterior_y_reciente_producen_distinto_aviso(self):
        for hasta,expected,age in [('2026-09-28',True,24),('2026-10-21',False,1)]:
            with self.subTest(hasta=hasta),patch.object(data,'obtener_historial_agrupado',return_value=historial(hasta)):
                _,context=data.construir_variables_afluencia(date(2026,10,22),10,'Central')
                self.assertEqual(context['modo_demostracion'],expected)
                self.assertEqual(context['dias_antiguedad_ultimo_registro'],age)

    def test_menos_de_quince_fechas_tambien_activa_aviso(self):
        with patch.object(data,'obtener_historial_agrupado',return_value=historial('2026-10-21').tail(8)):
            _,context=data.construir_variables_afluencia(date(2026,10,22),10,'Central')
            self.assertTrue(context['modo_demostracion'])
            self.assertTrue(context['historial_insuficiente'])

    def test_cache_vacia_vuelve_a_consultar_tabla_agregada(self):
        cursor=MagicMock()
        old=historial('2026-09-28')
        cursor.fetchall.return_value=list(old.itertuples(index=False,name=None))
        cursor.description=[('Fecha',),('Hora',),('CantidadIngresos',)]
        @contextmanager
        def fake_cursor(**kwargs): yield None,cursor
        cache=data._HISTORIAL_REFRESCADO_MEMORIA
        with patch.dict(cache,{'Central':{'df':historial('2026-10-21'),'es_dia_en_curso':False}},clear=True),patch.object(data,'get_db_cursor',fake_cursor):
            recent=data.obtener_historial_agrupado(date(2026,10,22),'Central')
            self.assertEqual(recent.Fecha.max(),date(2026,10,21))
            cursor.execute.assert_not_called()
        with patch.dict(cache,{},clear=True),patch.object(data,'get_db_cursor',fake_cursor):
            older=data.obtener_historial_agrupado(date(2026,10,22),'Central')
            self.assertEqual(older.Fecha.max(),date(2026,9,28))
            self.assertIn('AfluenciaHistoricaAgregada',cursor.execute.call_args.args[0])

    def test_opcion_h1_disabled_depende_de_disponible(self):
        client=app.test_client()
        with client.session_transaction() as s:
            s.update(admin_user='revision-local',admin_rol='SuperAdmin',admin_sede='Central')
        getter=MagicMock(); getter.metadatos={}
        with patch('app.get_db_connection',side_effect=AssertionError('BD bloqueada')),patch('pyodbc.connect',side_effect=AssertionError('BD bloqueada')),patch('pymssql.connect',side_effect=AssertionError('BD bloqueada')),patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter),patch.object(routes,'obtener_informacion_modelo',return_value={}):
            for available in [False,True]:
                with patch.object(routes,'informacion_retorno_experimental',return_value={'disponible':available}):
                    html=client.get('/admin/ml').get_data(as_text=True)
                option=re.search(r'<option value="experimental_h1_ventanas"([^>]*)>',html).group(1)
                self.assertEqual('disabled' in option,not available)

    def test_prediccion_es_identica_con_worker_refrescado_o_sin_cache(self):
        cursor=MagicMock()
        cursor.fetchall.return_value=list(historial('2026-09-28').itertuples(index=False,name=None))
        cursor.description=[('Fecha',),('Hora',),('CantidadIngresos',)]
        @contextmanager
        def source(**kwargs): yield None,cursor
        results=[]
        for cache in [{},{'Central':{'df':historial('2026-10-21'),'es_dia_en_curso':False}}]:
            with patch.dict(data._HISTORIAL_REFRESCADO_MEMORIA,cache,clear=True),patch.object(data,'get_db_cursor',source):
                results.append(data.construir_variables_afluencia(date(2026,10,22),10,'Central'))
        self.assertEqual(results[0],results[1])
        self.assertFalse(results[0][1]['modo_demostracion'])
        self.assertEqual(results[0][1]['ultima_fecha_disponible'],'2026-10-21')
        self.assertIn('fuente compartida',results[0][1]['fuente_historial'])
        sql,params=cursor.execute.call_args_list[0].args
        self.assertTrue(sql.lstrip().startswith('SELECT'))
        self.assertIn('Fecha < ?',sql); self.assertIn('Sede = ?',sql)
        self.assertEqual(params,('2026-10-22','Central'))

    def test_union_reemplaza_claves_filtra_cierres_y_no_extiende_cobertura(self):
        cursor = MagicMock()
        cursor.description = [('Fecha',), ('Hora',), ('CantidadIngresos',)]
        base = list(historial('2026-09-28').itertuples(index=False, name=None))
        recientes = [(date(2026, 9, 28), 10, 999),  # sin cobertura: conservar AHA
                     (date(2026, 9, 29), 10, 31),
                     (date(2026, 10, 8), 10, 88),  # feriado
                     (date(2026, 10, 21), 10, 42),
                     (date(2026, 10, 22), 10, 777)]  # desconocido / día en curso
        @contextmanager
        def fuente(**kwargs):
            cursor.fetchall.side_effect = [base, recientes]
            yield None, cursor
        with patch.object(data, 'get_db_cursor', fuente):
            frame = data.obtener_historial_agrupado(date(2026,10,22), fuente_compartida=True)
            self.assertFalse(frame.duplicated(['Fecha','Hora']).any())
            self.assertEqual(frame.loc[(frame.Fecha == date(2026,9,28)) & (frame.Hora == 10), 'CantidadIngresos'].item(), 12)
            self.assertEqual(frame.loc[(frame.Fecha == date(2026,10,21)) & (frame.Hora == 10), 'CantidadIngresos'].item(), 42)
            self.assertNotIn(date(2026,10,8), set(frame.Fecha))
            self.assertNotIn(date(2026,10,22), set(frame.Fecha))
            completo = data.completar_horas_sin_ingresos(frame)
            self.assertTrue(completo[(completo.Fecha == date(2026,9,28)) & (completo.Hora == 9)].empty)
            self.assertEqual(completo.loc[(completo.Fecha == date(2026,10,21)) & (completo.Hora == 9), 'CantidadIngresos'].item(), 0)
            variables, contexto = data.construir_variables_afluencia(date(2026,10,22), 10)
            self.assertEqual(variables['IngresosDiaAnterior'], 42)
            self.assertFalse(contexto['modo_demostracion'])
            _, contexto = data.construir_variables_afluencia(date(2026,10,23), 10)
            self.assertTrue(contexto['modo_demostracion'])
            self.assertIn('2026-10-22', contexto['fechas_sin_cobertura'])
        sql, parametros = cursor.execute.call_args.args
        self.assertIn('ISNULL(EsPrueba, 0) = 0', sql)
        self.assertIn('Sede = ?', sql)
        self.assertIn("20:45:00.000", sql)
        self.assertIn('FechaHora < ?', sql)
        self.assertEqual(parametros[0], '2026-09-28')
        self.assertEqual(parametros[2], 'Central')

    def test_selector_afluencia_respeta_disponibilidad_y_conserva_random_forest(self):
        client=app.test_client()
        with client.session_transaction() as s:
            s.update(admin_user='revision-local',admin_rol='SuperAdmin',admin_sede='Central')
        getter=MagicMock(); getter.metadatos={}
        for available in [False,True]:
            info={'es_candidato':False,'modelo_candidato_disponible':available,
                  'motivo_candidato_no_disponible':'Integridad no verificada' if not available else None}
            with patch('app.get_db_connection',side_effect=AssertionError('BD bloqueada')),patch.object(routes.PredictorRetornoAlumno,'obtener_instancia',return_value=getter),patch.object(routes,'obtener_informacion_modelo',return_value=info):
                html=client.get('/admin/ml').get_data(as_text=True)
            selector=html.split('id="selector-modelo"',1)[1].split('</select>',1)[0]
            candidate=re.search(r'<option value="candidato"([^>]*)>',selector).group(1)
            production=re.search(r'<option value="produccion"([^>]*)>',selector).group(1)
            self.assertEqual('disabled' in candidate,not available)
            self.assertIn('selected',production)
            self.assertNotIn('selected',candidate)
            if not available:
                self.assertIn('Candidato no disponible: Integridad no verificada',html)


if __name__=='__main__': unittest.main()
