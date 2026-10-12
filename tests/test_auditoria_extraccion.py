import os
import subprocess
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from ml.scripts.auditoria_extraccion_datos import (
    AuditoriaSeguridadError,
    auditar_cobertura_carrera_y_usuarios,
    auditar_datos_faltantes_y_anomalias,
    auditar_periodo_y_temporalidad,
    auditar_tablas_y_deduplicacion,
    ejecutar_auditoria_completa,
    ejecutar_select_seguro,
    extraer_dataset_ml_agregado,
    obtener_get_db_cursor,
    validar_consulta_solo_lectura,
    validar_entorno_academico,
)


class TestAuditoriaExtraccionDatos(unittest.TestCase):

    # =========================================================================
    # 1. SEGURIDAD Y VALIDACIÓN DE ENTORNO ACADÉMICO
    # =========================================================================

    def test_01_sin_confirmacion_bloquea_ejecucion(self):
        """Si no se proporciona confirmación explícita, debe detenerse y rechazar la conexión."""
        with patch.dict(os.environ, {"CONFIRMAR_BASE_ACADEMICA": "", "ENTORNO_ACADEMICO": ""}, clear=False):
            valido, msg, info = validar_entorno_academico(confirmar_cli=False)
            self.assertFalse(valido)
            self.assertIn("Seguridad preventiva", msg)
            self.assertIn("--confirmar-academica", msg)

    def test_02_con_confirmacion_cli_o_env_permite_acceso(self):
        """Con --confirmar-academica o variable de entorno se autoriza la ejecución."""
        # Vía CLI flag
        valido, msg, info = validar_entorno_academico(confirmar_cli=True)
        self.assertTrue(valido)
        self.assertIn("validado", msg.lower())
        self.assertIn("database", info)

        # Vía variable de entorno
        with patch.dict(os.environ, {"CONFIRMAR_BASE_ACADEMICA": "1"}, clear=False):
            valido_env, msg_env, info_env = validar_entorno_academico(confirmar_cli=False)
            self.assertTrue(valido_env)
            self.assertEqual(info_env["database"], info["database"])

    def test_03_info_conexion_no_expone_credenciales(self):
        """La información retornada no debe contener contraseñas ni usuarios."""
        valido, msg, info = validar_entorno_academico(confirmar_cli=True)
        self.assertNotIn("password", info)
        self.assertNotIn("user", info)
        self.assertNotIn("username", info)
        self.assertNotIn("pwd", info)

    # =========================================================================
    # 2. ENFORCEMENT DE SOLO LECTURA (ESTRICTAMENTE SELECT / WITH)
    # =========================================================================

    def test_04_consultas_select_validas_son_permitidas(self):
        """Sentencias SELECT y WITH bien formadas deben pasar la validación."""
        validar_consulta_solo_lectura("SELECT COUNT(*) FROM RegistroIngresos")
        validar_consulta_solo_lectura("   select top 10 * from Alumnos  ")
        validar_consulta_solo_lectura("""
            WITH CTE AS (
                SELECT FechaHora FROM RegistroIngresos
            )
            SELECT COUNT(*) FROM CTE
        """)

    def test_05_consultas_escritura_son_rechazadas_estrictamente(self):
        """Cualquier intento de INSERT, UPDATE, DELETE, DROP, TRUNCATE o ALTER debe lanzar excepción."""
        consultas_peligrosas = [
            "INSERT INTO RegistroIngresos (Piso) VALUES (1)",
            "UPDATE RegistroIngresos SET Piso = 2",
            "DELETE FROM RegistroIngresos WHERE RegistroID = 1",
            "DROP TABLE RegistroIngresos",
            "ALTER TABLE RegistroIngresos ADD ColumnaPrueba INT",
            "TRUNCATE TABLE RegistroIngresos",
            "EXEC sp_RegistrarIngreso '2020', 1",
            "CREATE TABLE Hack (id int)"
        ]
        for sql in consultas_peligrosas:
            with self.assertRaises(AuditoriaSeguridadError, msg=f"Debía fallar: {sql}"):
                validar_consulta_solo_lectura(sql)

    def test_06_ejecutar_select_seguro_valida_y_retorna_columnas_filas(self):
        """ejecutar_select_seguro valida antes de tocar el cursor y devuelve columnas y filas."""
        mock_cursor = MagicMock()
        mock_cursor.description = [("Total",), ("Sede",)]
        mock_cursor.fetchall.return_value = [(100, "Central")]

        columnas, filas = ejecutar_select_seguro(mock_cursor, "SELECT COUNT(*), Sede FROM RegistroIngresos GROUP BY Sede")
        self.assertEqual(columnas, ["Total", "Sede"])
        self.assertEqual(filas, [(100, "Central")])
        mock_cursor.execute.assert_called_once()

    # =========================================================================
    # 3. AUDITORÍA DE TABLAS Y DEDUPLICACIÓN
    # =========================================================================

    def test_07_auditar_tablas_deduplicacion_con_solapamiento(self):
        """Detecta registros en ambas tablas y deduce correctamente los duplicados cruzados."""
        mock_cursor = MagicMock()

        # Simular respuestas secuenciales:
        # 1. _tabla_existe(RegistroIngresos) -> 1
        # 2. SELECT COUNT(*) FROM RegistroIngresos -> 20,000
        # 3. _tabla_existe(RegistroIngresos_Historico) -> 1
        # 4. SELECT COUNT(*) FROM RegistroIngresos_Historico -> 15,000
        # 5. SELECT COUNT(*) FROM RegistroIngresos R INNER JOIN Historico H -> 2,212
        mock_cursor.description = [("val",)]
        mock_cursor.fetchone.side_effect = [(1,), (1,)]
        mock_cursor.fetchall.side_effect = [
            [(20000,)],
            [(15000,)],
            [(2212,)]
        ]

        res = auditar_tablas_y_deduplicacion(mock_cursor)
        self.assertTrue(res["registro_ingresos_disponible"])
        self.assertTrue(res["historico_disponible"])
        self.assertEqual(res["filas_registro_ingresos"], 20000)
        self.assertEqual(res["filas_historico"], 15000)
        self.assertEqual(res["duplicados_registro_id_cruzados"], 2212)
        # Neto = 20000 + 15000 - 2212 = 32788
        self.assertEqual(res["ingresos_netos_consolidados"], 32788)

    # =========================================================================
    # 4. TEMPORALIDAD Y DISTRIBUCIONES
    # =========================================================================

    def test_08_auditar_periodo_y_temporalidad(self):
        """Audita periodo, mes, sede, día y hora sin exponer PII."""
        mock_cursor = MagicMock()

        # Mock de consultas:
        # 1. _tabla_existe(Historico) -> 0
        # 2. Periodo global: min, max, dias, total
        # 3. Distribución mes
        # 4. Distribución sede
        # 5. Distribución día
        # 6. Distribución hora
        mock_cursor.description = [("c1",), ("c2",), ("c3",), ("c4",)]
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.side_effect = [
            [(datetime(2026, 4, 10, 8, 30), datetime(2026, 9, 28, 20, 15), 98, 32788)],
            [(2026, 4, 120), (2026, 5, 4500), (2026, 9, 8200)],
            [("Central", 32787), ("Tarma", 1)],
            [(2, "Monday", 7500), (3, "Tuesday", 6800)],
            [(8, 1200), (9, 2500), (10, 3800), (12, 4500)]
        ]

        temp = auditar_periodo_y_temporalidad(mock_cursor, historico_activo=False)
        self.assertEqual(temp["fecha_inicial"], "2026-04-10T08:30:00")
        self.assertEqual(temp["fecha_final"], "2026-09-28T20:15:00")
        self.assertEqual(temp["dias_con_registros"], 98)
        self.assertEqual(temp["total_registros_validos"], 32788)
        self.assertEqual(temp["distribucion_sede"]["Central"], 32787)
        self.assertEqual(temp["distribucion_sede"]["Tarma"], 1)
        self.assertEqual(len(temp["distribucion_mes"]), 3)
        self.assertIn("distribucion_hora", temp)

    # =========================================================================
    # 5. COBERTURA DE CARRERA Y USUARIOS (PRIVACIDAD GARANTIZADA)
    # =========================================================================

    def test_09_auditar_cobertura_carrera_y_usuarios_sin_datos_personales(self):
        """Verifica conteos de carrera y comprueba que NO se expongan nombres, DNI ni matrículas."""
        mock_cursor = MagicMock()

        # Mock:
        # 1. _tabla_existe(RegistroIngresos) -> 1
        # 2. Tipos de persona: Total, Alumnos, Egresados, Docentes, Personal, Visitantes, SinPersona
        # 3. _tabla_existe(Alumnos) -> 1
        # 4. _tabla_existe(Escuelas) -> 1
        # 5. Cobertura Alumnos: Total, ConEscuela
        # 6. Top Escuelas
        mock_cursor.description = [("Total",), ("Escuela",), ("Cantidad",)]
        mock_cursor.fetchone.side_effect = [(1,), (1,), (1,)]
        mock_cursor.fetchall.side_effect = [
            [(32788, 30500, 1200, 450, 200, 350, 88)],
            [(30500, 29800)],
            [("Ingeniería de Sistemas y Computación", 5400), ("Medicina Humana", 4100)]
        ]

        cob = auditar_cobertura_carrera_y_usuarios(mock_cursor)
        self.assertEqual(cob["total_ingresos_analizados"], 32788)
        self.assertEqual(cob["ingresos_alumnos"], 30500)
        self.assertEqual(cob["ingresos_con_escuela"], 29800)
        self.assertAlmostEqual(cob["porcentaje_cobertura_escuela_alumnos"], 97.7, delta=0.1)

        # Top escuelas debe contener solo nombres de escuela y cantidades
        self.assertEqual(len(cob["top_escuelas"]), 2)
        for esc in cob["top_escuelas"]:
            self.assertIn("escuela", esc)
            self.assertIn("cantidad", esc)
            # Confirmar ausencia absoluta de DNI, persona_id, nombres
            self.assertNotIn("dni", esc)
            self.assertNotIn("nombre", esc)
            self.assertNotIn("alumno_id", esc)

        # Justificación técnica para ML presente
        self.assertIn("EVALUACIÓN DE CARRERA/ESCUELA PARA ML", cob["evaluacion_ml_carrera"])

    # =========================================================================
    # 6. DATOS FALTANTES Y ANOMALÍAS
    # =========================================================================

    def test_10_auditar_datos_faltantes_y_anomalias(self):
        """Detecta valores nulos, anomalías horarias y fines de semana."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)  # Tabla existe
        mock_cursor.fetchall.return_value = [(32788, 0, 4, 8, 2)]

        anom = auditar_datos_faltantes_y_anomalias(mock_cursor)
        self.assertEqual(anom["fechahora_nula"], 0)
        self.assertEqual(anom["sede_nula_o_vacia"], 4)
        self.assertEqual(anom["ingresos_fuera_horario_8_a_20"], 8)
        self.assertEqual(anom["ingresos_fin_de_semana"], 2)

    # =========================================================================
    # 7. EXTRACCIÓN DEL DATASET ML AGREGADO (HORARIO POR FECHA Y SEDE)
    # =========================================================================

    def test_11_dataset_ml_agregado_estructura_y_cero_pii(self):
        """Genera el dataset horario (8 a 20) y asegura ausencia absoluta de PII."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)  # Historico no disponible en este test

        # Dos fechas reales con varias horas
        mock_cursor.fetchall.return_value = [
            (date(2026, 9, 24), 8, "Central", 15),
            (date(2026, 9, 24), 12, "Central", 45),
            (date(2026, 9, 25), 10, "Central", 20),
        ]

        filas_ml, resumen = extraer_dataset_ml_agregado(mock_cursor, exportar_ruta=None, incluir_historico=False)

        # 2 fechas * 13 horas (8 a 20) * 1 sede = 26 filas
        self.assertEqual(len(filas_ml), 26)
        self.assertEqual(resumen["total_ejemplos_ml"], 26)
        self.assertEqual(resumen["total_ingresos_acumulados"], 80)
        self.assertEqual(resumen["slots_con_ingresos_positivos"], 3)
        self.assertEqual(resumen["slots_con_cero_ingresos"], 23)

        # Validar campos de cada fila
        campos_permitidos = {
            "Fecha", "Hora", "Sede", "CantidadIngresos",
            "DiaSemana", "Mes", "DiaMes", "SemanaAnio",
            "EsLunes", "EsViernes", "Turno"
        }
        campos_prohibidos = {"nombre", "dni", "codigo", "matricula", "alumno_id", "persona_id", "id"}

        for fila in filas_ml:
            for k in fila.keys():
                self.assertIn(k, campos_permitidos)
                self.assertNotIn(k.lower(), campos_prohibidos)

    def test_12_exportacion_dataset_guarda_archivo_local_sin_pii(self):
        """Exporta el dataset a un archivo CSV en ruta local verificando contenido."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = [
            (date(2026, 9, 24), 10, "Central", 30),
        ]

        ruta_temporal = Path("scratch/test_dataset_ml.csv")
        try:
            filas_ml, resumen = extraer_dataset_ml_agregado(mock_cursor, exportar_ruta=ruta_temporal, incluir_historico=False)
            self.assertTrue(ruta_temporal.exists())
            self.assertIn("archivo_exportado", resumen)

            # Leer archivo y verificar cabecera
            with open(ruta_temporal, "r", encoding="utf-8-sig") as f:
                header = f.readline().strip()
                self.assertIn("Fecha", header)
                self.assertIn("Hora", header)
                self.assertIn("CantidadIngresos", header)
                self.assertNotIn("DNI", header)
                self.assertNotIn("Nombre", header)
        finally:
            if ruta_temporal.exists():
                ruta_temporal.unlink()

    # =========================================================================
    # 8. INTEGRACIÓN DE AUDITORÍA COMPLETA
    # =========================================================================

    @patch("ml.scripts.auditoria_extraccion_datos.auditar_tablas_y_deduplicacion")
    @patch("ml.scripts.auditoria_extraccion_datos.auditar_periodo_y_temporalidad")
    @patch("ml.scripts.auditoria_extraccion_datos.auditar_cobertura_carrera_y_usuarios")
    @patch("ml.scripts.auditoria_extraccion_datos.auditar_datos_faltantes_y_anomalias")
    @patch("ml.scripts.auditoria_extraccion_datos.extraer_dataset_ml_agregado")
    def test_13_ejecutar_auditoria_completa_estructura_reporte(
        self, mock_ml, mock_anom, mock_cob, mock_temp, mock_tablas
    ):
        """ejecutar_auditoria_completa produce un reporte íntegro con todas las secciones."""
        mock_tablas.return_value = {"filas_registro_ingresos": 32788, "historico_disponible": False}
        mock_temp.return_value = {"fecha_inicial": "2026-04-10", "fecha_final": "2026-09-28"}
        mock_cob.return_value = {"total_ingresos_analizados": 32788}
        mock_anom.return_value = {"fechahora_nula": 0}
        mock_ml.return_value = ([], {"total_ejemplos_ml": 1248})

        mock_cursor = MagicMock()
        reporte = ejecutar_auditoria_completa(mock_cursor, exportar_dataset=False)
        self.assertEqual(reporte["modo"], "SOLO_LECTURA_SELECT")
        self.assertIn("tablas_y_deduplicacion", reporte)
        self.assertIn("temporalidad", reporte)
        self.assertIn("cobertura_carrera_y_usuarios", reporte)
        self.assertIn("anomalias_y_faltantes", reporte)
        self.assertIn("resumen_dataset_ml", reporte)

    # =========================================================================
    # 9. PRUEBAS DE HUMO (CLI, IMPORTACIÓN DE RUTA Y PRIVACIDAD)
    # =========================================================================

    def test_14_smoke_obtener_get_db_cursor_importa_funcion_correctamente(self):
        """Prueba de humo: resuelve la raíz E:\\API e importa get_db_cursor sin conectarse a SQL Server."""
        fn = obtener_get_db_cursor()
        self.assertTrue(callable(fn))
        self.assertEqual(fn.__name__, "get_db_cursor")

    def test_15_smoke_cli_ejecuta_ayuda_sin_conectar_bd(self):
        """Prueba de humo CLI: python ml/scripts/auditoria_extraccion_datos.py --help funciona y no conecta a BD."""
        raiz = Path(__file__).resolve().parents[1]
        script = raiz / "ml" / "scripts" / "auditoria_extraccion_datos.py"
        resultado = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            cwd=str(raiz)
        )
        self.assertEqual(resultado.returncode, 0)
        self.assertIn("--confirmar-academica", resultado.stdout)
        self.assertIn("--exportar", resultado.stdout)
        self.assertEqual(resultado.stderr.strip(), "")

    def test_16_smoke_cli_sin_confirmacion_se_detiene_sin_error_de_importacion(self):
        """Prueba de humo CLI: sin --confirmar-academica se detiene por seguridad sin fallar en importación de db."""
        raiz = Path(__file__).resolve().parents[1]
        script = raiz / "ml" / "scripts" / "auditoria_extraccion_datos.py"
        env_limpio = os.environ.copy()
        env_limpio.pop("CONFIRMAR_BASE_ACADEMICA", None)
        env_limpio.pop("ENTORNO_ACADEMICO", None)

        resultado = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(raiz),
            env=env_limpio
        )
        self.assertEqual(resultado.returncode, 1)
        self.assertIn("[ALERTA DE SEGURIDAD]", resultado.stderr)
        self.assertNotIn("No se pudo importar get_db_cursor", resultado.stderr)

    def test_17_smoke_obtener_get_db_cursor_distingue_ruta_inexistente(self):
        """Distingue error de ruta cuando db.py no existe en la raíz especificada."""
        with patch.object(Path, "is_file", return_value=False):
            with self.assertRaises(ImportError) as ctx:
                obtener_get_db_cursor()
            self.assertIn("No se encontró el archivo db.py", str(ctx.exception))

    def test_18_smoke_obtener_get_db_cursor_no_expone_credenciales(self):
        """Los mensajes de error de importación nunca deben exponer credenciales ni contraseñas."""
        with patch.object(Path, "is_file", return_value=False):
            try:
                obtener_get_db_cursor()
            except ImportError as e:
                msg = str(e).lower()
                self.assertNotIn("password", msg)
                self.assertNotIn("pwd", msg)
                self.assertNotIn("token", msg)


if __name__ == "__main__":
    unittest.main()
