"""
Pruebas unitarias para el script de auditoría de procedencia y calidad del dataset de ML.
Biblioteca UNDAC.

Valida:
- Análisis del dataset preparado en ausencia de fuente (estado 'procedencia de ceros no verificada').
- Detección precisa de claves faltantes en fuente vs ceros observados con CSVs temporales.
- Filtrado y exclusión de registros de fin de semana, fuera de horario y otras sedes.
- Detección de discrepancias en conteos y totales.
- Carga de datasets delimitados por coma y punto y coma, con y sin encabezado.
- Integridad sobre el archivo preparado real del repositorio (1,248 filas, 32,779 ingresos).
- Clasificación unívoca del calendario: feriados nacionales, días no laborables y cierres confirmados.
- Tratamiento de fechas coincidentes con varias condiciones sin doble conteo (06/08 en vacaciones, 07/06 y 30/08 en domingo).
- Franja horaria parcial 20:00–20:45 (45 min) distinguida de franjas completas de 60 min.
- Detección y reporte de discrepancias en fechas de cierre confirmado (sin eliminación automática).
- Reclasificación rigurosa de los 224 ceros del preparado (117 en cierre, 45 desconocidos, 37 parcial 45m, 25 completos 60m).
- Generación de reporte Markdown estructurado y CSV auxiliar puramente agregado.
"""

import csv
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.scripts.auditoria_procedencia_dataset import (
    RUTA_PREPARADO_DEFECTO,
    auditar_procedencia,
    cargar_dataset_fuente,
    cargar_dataset_preparado,
    clasificar_fecha_calendario,
    clasificar_franja_horaria,
    generar_csv_auxiliar_agregado,
    generar_reporte_markdown,
    generar_reporte_texto,
    obtener_ruta_fuente_defecto,
    main
)


class TestAuditoriaProcedenciaDataset(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _crear_csv_preparado_temporal(self, filas):
        ruta = self.dir_path / "preparado_temp.csv"
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Fecha", "Hora", "Sede", "CantidadIngresos"])
            for fila in filas:
                writer.writerow(fila)
        return ruta

    def _crear_csv_fuente_temporal(self, filas, separador=";", con_encabezado=False):
        ruta = self.dir_path / "fuente_temp.csv"
        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            if con_encabezado:
                f.write(f"Fecha{separador}Hora{separador}Sede{separador}CantidadIngresos\n")
            for fila in filas:
                f.write(f"{fila[0]}{separador}{fila[1]}{separador}{fila[2]}{separador}{fila[3]}\n")
        return ruta

    def test_01_sin_fuente_reporta_procedencia_no_verificada(self):
        """Si no se proporciona el CSV fuente, debe reportar 'procedencia de ceros no verificada'."""
        filas = [
            ("2026-05-18", 8, "Central", 0),
            ("2026-05-18", 9, "Central", 5),
            ("2026-05-18", 10, "Central", 0),
            ("2026-05-18", 11, "Central", 15)
        ]
        ruta_prep = self._crear_csv_preparado_temporal(filas)

        # Forzar ruta_fuente a archivo inexistente para simular ausencia de fuente
        ruta_inexistente = self.dir_path / "inexistente.csv"
        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_inexistente)

        self.assertFalse(resultado["fuente_proporcionada"])
        self.assertEqual(resultado["estado_procedencia_ceros"], "procedencia de ceros no verificada")
        self.assertEqual(resultado["archivo_preparado"]["total_filas"], 4)
        self.assertEqual(resultado["archivo_preparado"]["total_ingresos"], 20)
        self.assertEqual(resultado["archivo_preparado"]["filas_con_cero"], 2)
        self.assertEqual(resultado["archivo_preparado"]["filas_positivas"], 2)
        self.assertIsNone(resultado["clasificacion_ceros"]["claves_faltantes_en_fuente"])
        self.assertIn("No se proporcionó ni se encontró el archivo fuente", resultado["clasificacion_ceros"]["detalle_evaluacion"])

    def test_02_con_fuente_identifica_ceros_imputados_por_cuadricula(self):
        """Las claves ausentes en el fuente deben ser clasificadas como imputadas por cuadrícula."""
        filas_fuente = [
            ("2026-05-18", 9, "Central", 5),
            ("2026-05-18", 11, "Central", 15)
        ]
        filas_prep = [
            ("2026-05-18", 8, "Central", 0),
            ("2026-05-18", 9, "Central", 5),
            ("2026-05-18", 10, "Central", 0),
            ("2026-05-18", 11, "Central", 15)
        ]
        ruta_fte = self._crear_csv_fuente_temporal(filas_fuente, separador=";", con_encabezado=False)
        ruta_prep = self._crear_csv_preparado_temporal(filas_prep)

        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_fte)

        self.assertTrue(resultado["fuente_proporcionada"])
        self.assertEqual(resultado["estado_procedencia_ceros"], "ceros_imputados_por_cuadricula")
        ceros = resultado["clasificacion_ceros"]
        self.assertEqual(ceros["total_ceros_preparados"], 2)
        self.assertEqual(ceros["claves_faltantes_en_fuente"], 2)
        self.assertEqual(ceros["ceros_observados_en_fuente"], 0)
        self.assertEqual(ceros["inconsistencias"], 0)
        self.assertTrue(resultado["conciliacion"]["coincidencia_exacta_ingresos"])
        self.assertTrue(resultado["conciliacion"]["coincidencia_exacta_filas_positivas"])

    def test_03_con_fuente_identifica_ceros_observados_reales(self):
        """Las claves que existen en la fuente con valor cero deben clasificarse como ceros observados."""
        filas_fuente = [
            ("2026-05-18", 8, "Central", 0),
            ("2026-05-18", 9, "Central", 10)
        ]
        filas_prep = [
            ("2026-05-18", 8, "Central", 0),
            ("2026-05-18", 9, "Central", 10)
        ]
        ruta_fte = self._crear_csv_fuente_temporal(filas_fuente, separador=",", con_encabezado=True)
        ruta_prep = self._crear_csv_preparado_temporal(filas_prep)

        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_fte)

        self.assertEqual(resultado["estado_procedencia_ceros"], "ceros_mixtos")
        ceros = resultado["clasificacion_ceros"]
        self.assertEqual(ceros["claves_faltantes_en_fuente"], 0)
        self.assertEqual(ceros["ceros_observados_en_fuente"], 1)

    def test_04_filtro_notebook_detecta_exclusiones_fuente(self):
        """Debe clasificar y reportar filas excluidas por fin de semana, fuera de horario y sede."""
        filas_fuente = [
            ("2026-05-18", 12, "Central", 10),     # Hábil, Central, 12h -> Utilizable
            ("2026-05-18", 15, "Tarma", 1),         # Otra sede -> Excluido
            ("2026-05-09", 10, "Central", 4),       # Sábado (fin de semana, hora 10) -> Excluido
            ("2026-05-15", 7, "Central", 2)         # Viernes, hora 7 (fuera de 8-20) -> Excluido
        ]
        filas_prep = [
            ("2026-05-18", 12, "Central", 10)
        ]
        ruta_fte = self._crear_csv_fuente_temporal(filas_fuente, separador=";")
        ruta_prep = self._crear_csv_preparado_temporal(filas_prep)

        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_fte)

        filtro = resultado["archivo_fuente"]["filtro_notebook_central_habiles_8_20"]
        self.assertEqual(filtro["filas_otras_sedes_excluidas"], 1)
        self.assertEqual(filtro["ingresos_otras_sedes_excluidos"], 1)
        self.assertEqual(filtro["filas_fin_de_semana_excluidas"], 1)
        self.assertEqual(filtro["ingresos_fin_de_semana_excluidos"], 4)
        self.assertEqual(filtro["filas_fuera_horario_8_20_excluidas"], 1)
        self.assertEqual(filtro["ingresos_fuera_horario_8_20_excluidos"], 2)
        self.assertEqual(filtro["filas_utilizables"], 1)
        self.assertEqual(filtro["ingresos_utilizables"], 10)
        self.assertTrue(resultado["conciliacion"]["coincidencia_exacta_ingresos"])

    def test_05_detecta_inconsistencias_y_discrepancias_de_ingresos(self):
        """Debe detectar cuando el preparado tiene 0 pero la fuente tiene positivo, o diferencias en valores."""
        filas_fuente = [
            ("2026-05-18", 12, "Central", 50),
            ("2026-05-18", 13, "Central", 20)  # En fuente es 20, en prep será 0
        ]
        filas_prep = [
            ("2026-05-18", 12, "Central", 40), # Discrepancia 40 vs 50
            ("2026-05-18", 13, "Central", 0)   # Inconsistencia
        ]
        ruta_fte = self._crear_csv_fuente_temporal(filas_fuente, separador=";")
        ruta_prep = self._crear_csv_preparado_temporal(filas_prep)

        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_fte)

        self.assertFalse(resultado["conciliacion"]["coincidencia_exacta_ingresos"])
        self.assertEqual(resultado["clasificacion_ceros"]["inconsistencias"], 1)

    def test_06_dataset_preparado_real_del_repositorio(self):
        """Comprobar los valores consolidados reales del dataset preparado en ml/data/."""
        self.assertTrue(RUTA_PREPARADO_DEFECTO.is_file(), "El dataset preparado debe existir en ml/data/")

        ruta_inexistente = self.dir_path / "sin_fuente.csv"
        resultado = auditar_procedencia(ruta_preparado=RUTA_PREPARADO_DEFECTO, ruta_fuente=ruta_inexistente)

        prep = resultado["archivo_preparado"]
        self.assertEqual(prep["total_filas"], 1248)
        self.assertEqual(prep["total_ingresos"], 32779)
        self.assertEqual(prep["filas_con_cero"], 224)
        self.assertEqual(prep["filas_positivas"], 1024)
        self.assertEqual(prep["fechas_unicas"], 96)
        self.assertEqual(prep["fecha_minima"], "2026-04-10")
        self.assertEqual(prep["fecha_maxima"], "2026-09-28")
        self.assertEqual(prep["total_horas_cubiertas"], 13)
        self.assertEqual(prep["horas_cubiertas"], list(range(8, 21)))
        self.assertEqual(prep["sedes"], ["Central"])
        self.assertEqual(prep["duplicados_clave"], 0)
        self.assertEqual(resultado["estado_procedencia_ceros"], "procedencia de ceros no verificada")

    def test_07_generacion_reporte_texto_y_cli(self):
        """Verificar formateo textual del reporte y ejecución CLI de solo lectura."""
        ruta_inexistente = self.dir_path / "sin_fuente.csv"
        resultado = auditar_procedencia(ruta_preparado=RUTA_PREPARADO_DEFECTO, ruta_fuente=ruta_inexistente)
        reporte_str = generar_reporte_texto(resultado)

        self.assertIn("AUDITORÍA DE PROCEDENCIA", reporte_str)
        self.assertIn("1,248", reporte_str)
        self.assertIn("32,779", reporte_str)
        self.assertIn("PROCEDENCIA DE CEROS NO VERIFICADA", reporte_str)

        # Probar CLI con sys.argv
        with patch("sys.argv", [
            "auditoria_procedencia_dataset.py",
            "-p", str(RUTA_PREPARADO_DEFECTO),
            "-f", str(ruta_inexistente),
            "--reporte", str(self.dir_path / "reporte_cli.md"),
            "--csv-auxiliar", str(self.dir_path / "auxiliar_cli.csv")
        ]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                exit_code = main()
                self.assertEqual(exit_code, 0)
                salida = mock_out.getvalue()
                self.assertIn("AUDITORÍA DE PROCEDENCIA", salida)

    def test_08_cierres_confirmados_clasificacion(self):
        """Verificar que las fechas de cierre confirmado se clasifiquen como tales y no como días abiertos."""
        # 1. Cierre institucional confirmado previo a Fiestas Patrias (27/07/2026)
        cal_27 = clasificar_fecha_calendario("2026-07-27")
        self.assertEqual(cal_27["categoria"], "cierre_institucional_confirmado")
        self.assertEqual(cal_27["estado_operativo"], "cierre_confirmado")
        self.assertFalse(cal_27["es_laborable_modelo"])

        # 2. Cierre de vacaciones de medio año (01/08/2026 al 23/08/2026)
        fechas_vacaciones = ["2026-08-01", "2026-08-03", "2026-08-10", "2026-08-15", "2026-08-21", "2026-08-23"]
        for f in fechas_vacaciones:
            cal_vac = clasificar_fecha_calendario(f)
            self.assertEqual(cal_vac["categoria"], "cierre_vacaciones_confirmado")
            self.assertEqual(cal_vac["estado_operativo"], "cierre_confirmado")
            self.assertFalse(cal_vac["es_laborable_modelo"])

        # 3. Feriados oficiales con cierre confirmado
        feriados_cerrados = ["2026-05-01", "2026-06-29", "2026-07-23", "2026-07-28", "2026-07-29"]
        for f in feriados_cerrados:
            cal_fer = clasificar_fecha_calendario(f)
            self.assertEqual(cal_fer["categoria"], "feriado_nacional_cerrado")
            self.assertEqual(cal_fer["estado_operativo"], "cierre_confirmado")
            self.assertFalse(cal_fer["es_laborable_modelo"])

    def test_09_fechas_coincidentes_con_varias_condiciones_sin_doble_conteo(self):
        """Asegurar que fechas con múltiples condiciones se asignen a una única categoría sin doble conteo."""
        # 1. 06/08/2026: Batalla de Junín (Feriado Nacional) que cae en el cierre de vacaciones
        cal_0608 = clasificar_fecha_calendario("2026-08-06")
        self.assertEqual(cal_0608["categoria"], "cierre_vacaciones_confirmado")
        self.assertEqual(cal_0608["coincidencia_feriado"], "Batalla de Junín (06/08/2026)")
        self.assertEqual(cal_0608["estado_operativo"], "cierre_confirmado")

        # 2. 07/06/2026: Día de la Bandera que cae en Domingo (Fin de semana)
        cal_0706 = clasificar_fecha_calendario("2026-06-07")
        self.assertEqual(cal_0706["categoria"], "fin_de_semana")
        self.assertIn("Día de la Bandera", cal_0706["coincidencia_feriado"])
        self.assertEqual(cal_0706["estado_operativo"], "fin_de_semana")

        # 3. 30/08/2026: Santa Rosa de Lima que cae en Domingo (Fin de semana)
        cal_3008 = clasificar_fecha_calendario("2026-08-30")
        self.assertEqual(cal_3008["categoria"], "fin_de_semana")
        self.assertIn("Santa Rosa de Lima", cal_3008["coincidencia_feriado"])

        # 4. Feriados de Semana Santa anteriores al inicio del dataset (10/04/2026)
        cal_sem_santa = clasificar_fecha_calendario("2026-04-03")
        self.assertEqual(cal_sem_santa["categoria"], "fuera_periodo_anterior")

        # 5. Feriado posterior al fin del dataset (28/09/2026)
        cal_angamos = clasificar_fecha_calendario("2026-10-08")
        self.assertEqual(cal_angamos["categoria"], "fuera_periodo_posterior")

    def test_10_franja_parcial_20h_tratamiento_y_distincion(self):
        """La franja 20:00–20:45 debe clasificarse como parcial de 45 minutos y distinguirse de las de 60m."""
        # Franja 20: 45 minutos
        f20 = clasificar_franja_horaria(20)
        self.assertTrue(f20["es_parcial"])
        self.assertEqual(f20["duracion_minutos"], 45)
        self.assertEqual(f20["tipo_franja"], "franja_parcial_45m")
        self.assertEqual(f20["rango_texto"], "20:00–20:45")

        # Franjas 8 a 19: 60 minutos
        for h in range(8, 20):
            fh = clasificar_franja_horaria(h)
            self.assertFalse(fh["es_parcial"])
            self.assertEqual(fh["duracion_minutos"], 60)
            self.assertEqual(fh["tipo_franja"], "franja_completa_60m")

        # Fuera de horario
        f7 = clasificar_franja_horaria(7)
        self.assertEqual(f7["tipo_franja"], "fuera_horario")
        f21 = clasificar_franja_horaria(21)
        self.assertEqual(f21["tipo_franja"], "fuera_horario")

    def test_11_discrepancias_registros_encontrados_durante_cierre(self):
        """Los registros que aparezcan en periodos de cierre confirmado no deben borrarse, sino reportarse como discrepancias."""
        filas_fuente = [
            ("2026-08-03", 9, "Central", 3),   # Registrado durante vacaciones (01-23 ago)
            ("2026-08-04", 10, "Central", 15), # Registrado durante vacaciones
            ("2026-08-25", 10, "Central", 40)  # Registrado en periodo lectivo normal
        ]
        filas_prep = [
            ("2026-08-03", 9, "Central", 3),
            ("2026-08-04", 10, "Central", 15),
            ("2026-08-25", 10, "Central", 40)
        ]
        ruta_fte = self._crear_csv_fuente_temporal(filas_fuente, separador=";")
        ruta_prep = self._crear_csv_preparado_temporal(filas_prep)

        resultado = auditar_procedencia(ruta_preparado=ruta_prep, ruta_fuente=ruta_fte)

        disc = resultado["discrepancias_periodo_cierre"]
        self.assertEqual(disc["total_filas_encontradas"], 2)
        self.assertEqual(disc["total_ingresos_encontrados"], 18)
        self.assertEqual(disc["cantidad_fechas_afectadas"], 2)
        self.assertIn("2026-08-03", disc["fechas_afectadas"])
        self.assertIn("2026-08-04", disc["fechas_afectadas"])
        self.assertIn("No se eliminan automáticamente", disc["tratamiento_regla_4"])

    def test_12_integridad_con_fuente_real_si_disponible(self):
        """Verificar conciliación exacta si el archivo fuente original está disponible en Downloads."""
        ruta_fte_real = obtener_ruta_fuente_defecto()
        if ruta_fte_real is None or not ruta_fte_real.is_file():
            self.skipTest("Archivo 02_ingresos_reales_actual.csv no disponible en Downloads.")

        resultado = auditar_procedencia(ruta_preparado=RUTA_PREPARADO_DEFECTO, ruta_fuente=ruta_fte_real)

        # 1. Cifras del archivo fuente
        fte = resultado["archivo_fuente"]
        self.assertEqual(fte["total_filas"], 1029)
        self.assertEqual(fte["total_ingresos"], 32788)

        # 2. Filtrado Central, días laborables y horas 08-20
        filtro = fte["filtro_notebook_central_habiles_8_20"]
        self.assertEqual(filtro["filas_utilizables"], 1024)
        self.assertEqual(filtro["ingresos_utilizables"], 32779)
        self.assertEqual(filtro["filas_fisicas_excluidas_netas"], 5)
        self.assertEqual(filtro["ingresos_excluidos_netos"], 9)
        self.assertEqual(filtro["filas_superpuestas_fin_semana_y_fuera_horario"], 2)
        self.assertEqual(filtro["ingresos_superpuestos"], 5)

        # 3. Conciliación exacta
        conc = resultado["conciliacion"]
        self.assertTrue(conc["coincidencia_exacta_filas_positivas"])
        self.assertTrue(conc["coincidencia_exacta_ingresos"])
        self.assertEqual(conc["diferencia_ingresos"], 0)

        # 4. Discrepancias durante cierre confirmado de vacaciones (01/08 al 23/08)
        disc = resultado["discrepancias_periodo_cierre"]
        self.assertEqual(disc["total_filas_encontradas"], 65)
        self.assertEqual(disc["total_ingresos_encontrados"], 214)
        self.assertEqual(disc["cantidad_fechas_afectadas"], 14)

        # 5. Reclasificación exhaustiva de los 224 ceros
        ceros = resultado["clasificacion_ceros"]
        desglose = ceros["desglose_por_contexto_operativo"]
        self.assertEqual(ceros["total_ceros_preparados"], 224)
        self.assertEqual(desglose["ceros_en_cierre_confirmado_vacaciones"], 117)
        self.assertEqual(desglose["ceros_en_fechas_preoperativas_desconocidas"], 45)
        self.assertEqual(desglose["ceros_observados_demanda_nula_60m"], 25)
        self.assertEqual(desglose["ceros_observados_franja_parcial_45m"], 37)
        # Suma exacta: 117 + 45 + 25 + 37 = 224
        self.assertEqual(
            desglose["ceros_en_cierre_confirmado_vacaciones"] +
            desglose["ceros_en_fechas_preoperativas_desconocidas"] +
            desglose["ceros_observados_demanda_nula_60m"] +
            desglose["ceros_observados_franja_parcial_45m"],
            224
        )

        # 6. Estadísticas de la franja parcial 20h
        h20 = resultado["tratamiento_franja_parcial_20h"]["estadisticas_dataset_preparado"]
        self.assertEqual(h20["total_fechas_cubiertas"], 96)
        self.assertEqual(h20["fechas_con_cero"], 55)
        self.assertEqual(h20["fechas_con_ingresos"], 41)
        self.assertEqual(h20["total_ingresos_acumulados"], 105)

    def test_13_generacion_reporte_markdown_y_csv_auxiliar(self):
        """Verificar la creación del reporte en Markdown y del CSV auxiliar agregado."""
        resultado = auditar_procedencia(ruta_preparado=RUTA_PREPARADO_DEFECTO)

        ruta_md = self.dir_path / "test_reporte.md"
        contenido_md = generar_reporte_markdown(resultado, ruta_salida=ruta_md)

        self.assertTrue(ruta_md.is_file())
        self.assertIn("# Auditoría de Calidad y Procedencia de Datos Históricos de Afluencia", contenido_md)
        self.assertIn("## 1. Contexto Operativo Confirmado", contenido_md)
        self.assertIn("## 2. Calendario Contrastado y Clasificación de Fechas", contenido_md)
        self.assertIn("## 5. Reclasificación Rigurosa de los 224 Ceros del Dataset Preparado", contenido_md)
        self.assertIn("## 6. Tratamiento de la Franja Parcial (20:00–20:45)", contenido_md)

        ruta_csv = self.dir_path / "test_resumen_agregado.csv"
        generar_csv_auxiliar_agregado(resultado, ruta_salida=ruta_csv)

        self.assertTrue(ruta_csv.is_file())
        with open(ruta_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
            self.assertIn("Fecha", header)
            self.assertIn("TotalIngresos", header)
            self.assertIn("FranjasPositivas_60m", header)
            self.assertIn("FranjaPositiva_45m", header)
            self.assertIn("EsPeriodoCierre", header)
            filas_csv = list(reader)
            self.assertEqual(len(filas_csv), 96)


if __name__ == "__main__":
    unittest.main()
