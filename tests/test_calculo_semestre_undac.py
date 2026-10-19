import os
import unittest
from unittest.mock import patch, MagicMock
from utils.queries_ingreso import (
    calcular_semestre_undac,
    normalizar_semestre_oficial,
    resolver_semestre_id,
    parsear_periodo_academico,
    obtener_periodo_referencia_vigente,
    ResultadoSemestre,
    auto_registrar_alumno_api_undac,
    registrar_ingreso_general,
    CATALOGO_SEMESTRES_ACADEMICOS,
    CATALOGO_SEMESTRES_BD,
    MAPEO_SEMESTRES_BD
)


class TestCalculoSemestreUndac(unittest.TestCase):
    """
    Pruebas unitarias para la lógica de cálculo y asignación de semestre en UNDAC.
    Garantiza:
    1. Parseo estricto de periodo académico: rechazo de formatos inválidos o incompletos sin asumir periodo II.
    2. Comportamiento seguro y transparente: sin heurísticas de duración no sustentadas, estimación honesta como ESTIMADO.
    3. Resolución de SemestreID contra el catálogo real dbo.Semestres al guardar sin depender de mapeo fijo.
    4. Separación de validación académica (1 al 14) de la resolución relacional foránea.
    5. No confirmación de semestres de fuentes no contractualmente demostradas como vigentes (guardar NULL).
    6. Estimaciones por año nunca se presentan ni guardan como semestres oficiales.
    """

    def setUp(self):
        # Limpiar variables de entorno antes de cada prueba para aislamiento
        self.orig_env_p = os.environ.get('PERIODO_ACADEMICO_VIGENTE')
        self.orig_env_u = os.environ.get('UNDAC_PERIODO_VIGENTE')
        if 'PERIODO_ACADEMICO_VIGENTE' in os.environ:
            del os.environ['PERIODO_ACADEMICO_VIGENTE']
        if 'UNDAC_PERIODO_VIGENTE' in os.environ:
            del os.environ['UNDAC_PERIODO_VIGENTE']

    def tearDown(self):
        if self.orig_env_p is not None:
            os.environ['PERIODO_ACADEMICO_VIGENTE'] = self.orig_env_p
        elif 'PERIODO_ACADEMICO_VIGENTE' in os.environ:
            del os.environ['PERIODO_ACADEMICO_VIGENTE']

        if self.orig_env_u is not None:
            os.environ['UNDAC_PERIODO_VIGENTE'] = self.orig_env_u
        elif 'UNDAC_PERIODO_VIGENTE' in os.environ:
            del os.environ['UNDAC_PERIODO_VIGENTE']

    # -------------------------------------------------------------------------
    # 1. Parseo estricto de periodo académico (Punto 1 y 4)
    # -------------------------------------------------------------------------
    def test_parsear_periodo_academico_formatos_validos(self):
        """Formatos válidos con año y ciclo I o II reconocido."""
        self.assertEqual(parsear_periodo_academico("2026-II"), (2026, 2))
        self.assertEqual(parsear_periodo_academico("2026-I"), (2026, 1))
        self.assertEqual(parsear_periodo_academico("2025-2"), (2025, 2))
        self.assertEqual(parsear_periodo_academico("2025-1"), (2025, 1))
        self.assertEqual(parsear_periodo_academico("2026 II"), (2026, 2))
        self.assertEqual(parsear_periodo_academico("2026/I"), (2026, 1))
        self.assertEqual(parsear_periodo_academico("2026_II"), (2026, 2))
        self.assertEqual(parsear_periodo_academico("Semestre 2026-II"), (2026, 2))

    def test_parsear_periodo_academico_formatos_invalidos_e_incompletos(self):
        """
        Punto 1: Rechaza formatos inválidos o incompletos.
        No asume periodo II ante ambigüedad o falta de ciclo.
        """
        casos_invalidos = [
            "", None, "sin_año", 12345,
            "2026",        # Incompleto: falta periodo I o II (no asumir II)
            "2026-",       # Incompleto: separador sin periodo
            "2026-III",    # Inválido: ciclo III no existe en periodo regular
            "2026-3",      # Inválido: ciclo 3
            "2026-0",      # Inválido: ciclo 0
            "2026-ABC",    # Inválido: sufijo no numérico ni romano
            "2026-X",      # Inválido: ciclo X
            "ABCD-II"      # Inválido: año no numérico
        ]
        for caso in casos_invalidos:
            with self.subTest(caso=caso):
                with self.assertRaises(ValueError):
                    parsear_periodo_academico(caso)

    def test_calcular_semestre_periodo_referencia_invalido_devuelve_no_disponible(self):
        """
        Punto 1 y 4: Periodos de referencia mal formados o incompletos
        deben producir NO_DISPONIBLE en calcular_semestre_undac de manera controlada.
        """
        casos_mal_formados = ["2026", "2026-III", "2026-0", "invalido", "2026-X", ""]
        for p_mal in casos_mal_formados:
            with self.subTest(periodo=p_mal):
                res = calcular_semestre_undac(
                    fecha_ingreso_api="2024",
                    periodo_ingreso=1,
                    periodo_referencia=p_mal
                )
                self.assertEqual(res.estado, "NO_DISPONIBLE")
                self.assertIsNone(res.semestre)
                motivo_lower = res.motivo.lower()
                self.assertTrue(
                    any(term in motivo_lower for term in ["inválido", "incompleto", "no configurado", "no disponible"]),
                    f"Motivo inesperado para periodo '{p_mal}': {res.motivo}"
                )

    # -------------------------------------------------------------------------
    # 2. Estimación transparente de avance sin heurísticas no sustentadas
    # -------------------------------------------------------------------------
    def test_calculo_estimacion_avance_regular_con_periodo_definido(self):
        """
        Calcula el avance regular directo (año + periodo) sin inventar duraciones ni aplicar topes ciegos.
        El resultado es estrictamente ESTIMADO y es_confirmado=False.
        """
        # Ingreso 2024-I en 2026-II: avance = 6
        res_6 = calcular_semestre_undac(
            fecha_ingreso_api="2024",
            periodo_ingreso=1,
            periodo_referencia="2026-II"
        )
        self.assertEqual(res_6.estado, "ESTIMADO")
        self.assertEqual(res_6.semestre, "6")
        self.assertFalse(res_6.es_confirmado)
        self.assertEqual(res_6.semestre_min, "6")
        self.assertEqual(res_6.semestre_max, "6")
        self.assertIn("Estimación por avance regular", res_6.motivo)

        # Ingreso 2020-I en 2026-II: avance = 14
        res_14 = calcular_semestre_undac(
            fecha_ingreso_api="2020",
            periodo_ingreso=1,
            periodo_referencia="2026-II"
        )
        self.assertEqual(res_14.estado, "ESTIMADO")
        self.assertEqual(res_14.semestre, "14")
        self.assertFalse(res_14.es_confirmado)

    def test_calculo_estimacion_sin_periodo_definido_devuelve_rango_acotado(self):
        """
        Si solo se conoce el año y no el periodo (I o II), devuelve un rango acotado s_min..s_max
        estrictamente como ESTIMADO y es_confirmado=False.
        """
        # Ingreso 2024 en 2026-II: s_min=5, s_max=6 -> '5-6'
        res_rango = calcular_semestre_undac(
            fecha_ingreso_api="2024",
            periodo_referencia="2026-II"
        )
        self.assertEqual(res_rango.estado, "ESTIMADO")
        self.assertEqual(res_rango.semestre, "5-6")
        self.assertEqual(res_rango.semestre_min, "5")
        self.assertEqual(res_rango.semestre_max, "6")
        self.assertFalse(res_rango.es_confirmado)

        # Ingreso 2020 en 2026-II: s_min=13, s_max=14 -> '13-14'
        res_rango_2020 = calcular_semestre_undac(
            fecha_ingreso_api="2020",
            periodo_referencia="2026-II"
        )
        self.assertEqual(res_rango_2020.estado, "ESTIMADO")
        self.assertEqual(res_rango_2020.semestre, "13-14")
        self.assertEqual(res_rango_2020.semestre_min, "13")
        self.assertEqual(res_rango_2020.semestre_max, "14")
        self.assertFalse(res_rango_2020.es_confirmado)

    def test_estimacion_nunca_se_presenta_como_oficial_ni_genera_semestre_id(self):
        """
        Garantiza que un resultado ESTIMADO mantenga es_confirmado=False y semestre_id=None,
        evitando que se guarde o muestre como oficial.
        """
        res = calcular_semestre_undac(
            codigo="2400000001",
            periodo_referencia="2026-II"
        )
        self.assertEqual(res.estado, "ESTIMADO")
        self.assertFalse(res.es_confirmado)
        self.assertIsNone(res.semestre_id)
        self.assertFalse(res.inconsistencia_catalogo)

    # -------------------------------------------------------------------------
    # 3. Resolución de SemestreID contra catálogo real dbo.Semestres (Punto 3 y 4)
    # -------------------------------------------------------------------------
    def test_resolucion_semestre_id_contra_catalogo_real_con_cursor(self):
        """
        Punto 3 y 4: Verifica que SemestreID se resuelva contra el catálogo real al guardar,
        sin depender de un mapeo fijo que pueda quedar desactualizado.
        """
        mock_cursor = MagicMock()

        # Caso A: Fila existente en dbo.Semestres (ej. ciclo '6' retorna SemestreID 3)
        mock_cursor.fetchone.return_value = (3,)
        sem_id, inc = resolver_semestre_id("6", cursor=mock_cursor, usar_fallback_estatico=False)
        self.assertEqual(sem_id, 3)
        self.assertFalse(inc)
        mock_cursor.execute.assert_called_with(
            "SELECT TOP 1 SemestreID FROM Semestres WHERE NombreSemestre = ?", ("6",)
        )

        # Caso B: Fila faltante en dbo.Semestres (ej. ciclo '1' retorna None de la BD)
        mock_cursor.fetchone.return_value = None
        sem_id, inc = resolver_semestre_id("1", cursor=mock_cursor, usar_fallback_estatico=False)
        self.assertIsNone(sem_id, "Al faltar en dbo.Semestres, SemestreID debe ser None sin recurrir a mapeo fijo")
        self.assertTrue(inc)

        # Caso C: Actualización futura de dbo.Semestres (ej. ciclo '1' fue agregado con SemestreID 15)
        # Verifica que reconozca el valor dinámicamente de la BD y no falle por mapeo estático desactualizado
        mock_cursor.fetchone.return_value = (15,)
        sem_id, inc = resolver_semestre_id("1", cursor=mock_cursor, usar_fallback_estatico=False)
        self.assertEqual(sem_id, 15, "Debe tomar el SemestreID devuelto por la BD real dinámicamente")
        self.assertFalse(inc)

    @patch('utils.queries_ingreso.calcular_semestre_undac')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch('utils.queries_ingreso.requests.get')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_auto_registrar_alumno_resuelve_semestre_id_contra_bd_al_guardar(self, mock_get, mock_db, mock_calc):
        """
        Punto 3: Al guardar en auto_registrar_alumno_api_undac, SemestreID se resuelve
        mediante consulta SQL contra dbo.Semestres usando el cursor activo.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'Nombres': 'ESTUDIANTE',
            'Apellido paterno': 'MEDICINA',
            'Apellido materno': 'PRUEBA',
            'Dni': '77777785',
            'Programa facultad': 'Medicina Humana',
            'Fecha de Ingreso': '2020',
            'Semestre': '14'
        }
        mock_get.return_value = mock_resp

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Simular que el cálculo retorna ciclo '14' confirmado
        mock_calc.return_value = ResultadoSemestre(
            estado='CONFIRMADO',
            semestre='14',
            es_confirmado=True,
            fuente='INSTITUCIONAL_EXPLICITA',
            periodo_referencia='2026-II',
            semestre_id=7,
            inconsistencia_catalogo=False
        )

        # Simular respuestas del cursor:
        # 1. SELECT TOP 1 SemestreID FROM Semestres WHERE NombreSemestre = '14' -> (7,)
        # 2. SELECT AlumnoID FROM Alumnos ... -> None
        mock_cursor.fetchone.side_effect = [(7,), None]

        exito, nombre = auto_registrar_alumno_api_undac("2000000014", periodo_referencia="2026-II")
        self.assertTrue(exito)

        # Verificar que se consultó la tabla Semestres con el cursor activo
        select_semestres_calls = [
            call for call in mock_cursor.execute.call_args_list 
            if "SELECT TOP 1 SemestreID FROM Semestres" in call[0][0]
        ]
        self.assertTrue(len(select_semestres_calls) >= 1, "Debe resolver SemestreID consultando dbo.Semestres con el cursor")

    # -------------------------------------------------------------------------
    # 4. Rango académico 1..14 y ciclos impares 1, 7, 9, 13
    # -------------------------------------------------------------------------
    def test_semestre_oficial_todos_los_ciclos_admitidos_1_al_14_confirmados(self):
        """Todos los ciclos académicos admitidos (1 al 14) deben confirmarse con fuente verificada."""
        for i in range(1, 15):
            sem = str(i)
            with self.subTest(semestre=sem):
                res = calcular_semestre_undac(
                    semestre_raw=sem,
                    fuente_oficial_verificada=True,
                    periodo_referencia="2026-II"
                )
                self.assertEqual(res.estado, "CONFIRMADO")
                self.assertEqual(res.semestre, sem)
                self.assertTrue(res.es_confirmado)
                self.assertEqual(res.fuente, "INSTITUCIONAL_EXPLICITA")

    def test_ciclos_impares_1_7_9_13_confirmados_con_semestre_id_null(self):
        """
        Los ciclos 1, 7, 9 y 13 deben confirmarse como texto válido pero dejar SemestreID = NULL
        e informar la inconsistencia del catálogo, sin ser rechazados por la tabla incompleta.
        """
        for sem in ['1', '7', '9', '13']:
            with self.subTest(semestre=sem):
                res = calcular_semestre_undac(
                    semestre_raw=sem,
                    fuente_oficial_verificada=True,
                    periodo_referencia="2026-II"
                )
                self.assertEqual(res.estado, "CONFIRMADO", f"El ciclo {sem} no debe ser rechazado por dbo.Semestres incompleta")
                self.assertEqual(res.semestre, sem)
                self.assertTrue(res.es_confirmado)
                self.assertIsNone(res.semestre_id, f"El ciclo {sem} no tiene fila en dbo.Semestres, SemestreID debe ser None")
                self.assertTrue(res.inconsistencia_catalogo, f"Debe reportar inconsistencia de catálogo para ciclo {sem}")
                self.assertIn("dbo.Semestres carece de fila", res.motivo)

    def test_ciclos_en_catalogo_dbo_semestres_con_semestre_id_poblado(self):
        """Los ciclos que SÍ existen en dbo.Semestres deben confirmarse y tener SemestreID poblado."""
        for sem, sem_id_esperado in MAPEO_SEMESTRES_BD.items():
            with self.subTest(semestre=sem):
                res = calcular_semestre_undac(
                    semestre_raw=sem,
                    fuente_oficial_verificada=True,
                    periodo_referencia="2026-II"
                )
                self.assertEqual(res.estado, "CONFIRMADO")
                self.assertEqual(res.semestre, sem)
                self.assertTrue(res.es_confirmado)
                self.assertEqual(res.semestre_id, sem_id_esperado)
                self.assertFalse(res.inconsistencia_catalogo)

    def test_semestre_fuera_rango_academico_rechazado(self):
        """Valores fuera del rango académico admitido (1 al 14) deben ser rechazados."""
        for sem_invalido in ['0', '15', '16', '99', '-1', '-5']:
            with self.subTest(semestre=sem_invalido):
                res = calcular_semestre_undac(
                    semestre_raw=sem_invalido,
                    fuente_oficial_verificada=True,
                    periodo_referencia="2026-II"
                )
                self.assertEqual(res.estado, "NO_DISPONIBLE")
                self.assertIsNone(res.semestre)
                self.assertFalse(res.es_confirmado)
                self.assertIsNone(res.semestre_id)

    def test_semestre_oficial_romanos_y_ordinales_admitidos(self):
        """Números romanos y ordinales en el rango 1 al 14 deben normalizarse y confirmarse."""
        casos = [
            ("I", "1", None, True),
            ("1°", "1", None, True),
            ("1er", "1", None, True),
            ("Semestre 1", "1", None, True),
            ("VII", "7", None, True),
            ("7mo", "7", None, True),
            ("Semestre VII", "7", None, True),
            ("IX", "9", None, True),
            ("9no", "9", None, True),
            ("Semestre IX", "9", None, True),
            ("XIII", "13", None, True),
            ("13vo", "13", None, True),
            ("Semestre XIII", "13", None, True),
            ("VI", "6", 3, False),
            ("6to", "6", 3, False),
            ("Semestre VI", "6", 3, False),
            ("X", "10", 6, False),
            ("Semestre 8", "8", 9, False),
            ("Semestre IV", "4", 5, False),
            ("XIV", "14", 7, False),
        ]
        for raw, num_esp, id_esp, inc_esp in casos:
            with self.subTest(raw=raw, esperado=num_esp):
                res = calcular_semestre_undac(
                    semestre_raw=raw,
                    fuente_oficial_verificada=True,
                    periodo_referencia="2026-II"
                )
                self.assertEqual(res.estado, "CONFIRMADO")
                self.assertEqual(res.semestre, num_esp)
                self.assertTrue(res.es_confirmado)
                self.assertEqual(res.semestre_id, id_esp)
                self.assertEqual(res.inconsistencia_catalogo, inc_esp)

        # Romano fuera del rango 1 al 14
        res_xv = calcular_semestre_undac(semestre_raw="XV", fuente_oficial_verificada=True, periodo_referencia="2026-II")
        self.assertEqual(res_xv.estado, "NO_DISPONIBLE")
        self.assertFalse(res_xv.es_confirmado)

    def test_roles_no_aceptados_como_semestre(self):
        """Roles (Docente, Administrativo, Egresado, Visitante) no deben aceptarse como semestre."""
        for rol in ["Docente", "ADMINISTRATIVO", "Egresado", "Visitante", "Externo", "Estudiante"]:
            with self.subTest(rol=rol):
                res = calcular_semestre_undac(semestre_raw=rol, fuente_oficial_verificada=True)
                self.assertEqual(res.estado, "NO_DISPONIBLE")
                self.assertIsNone(res.semestre)
                self.assertFalse(res.es_confirmado)
                self.assertIn("no constituye un semestre", res.motivo.lower())

    def test_semestre_placeholders_rechazados(self):
        """Placeholders como 'regular', 'null', '0', etc. no se confirman."""
        for placeholder in ["regular", "Regular", "NULL", "none", "0", "", "ninguno", "sin semestre"]:
            with self.subTest(placeholder=placeholder):
                res = calcular_semestre_undac(semestre_raw=placeholder, fuente_oficial_verificada=True)
                self.assertFalse(res.es_confirmado)
                self.assertNotEqual(res.estado, "CONFIRMADO")

    # -------------------------------------------------------------------------
    # 5. Periodo de referencia institucional dinámico
    # -------------------------------------------------------------------------
    def test_sin_periodo_referencia_vigente_devuelve_no_disponible(self):
        """Sin periodo de referencia vigente, la estimación devuelve NO_DISPONIBLE."""
        res = calcular_semestre_undac(fecha_ingreso_api="2024", periodo_referencia=None)
        self.assertEqual(res.estado, "NO_DISPONIBLE")
        self.assertIsNone(res.semestre)
        self.assertIsNone(res.periodo_referencia)
        self.assertIn("no configurado o no disponible", res.motivo.lower())

    def test_periodo_referencia_configurable_por_parametro(self):
        """Verifica que el periodo de referencia pueda pasarse explícitamente como parámetro."""
        res_2025 = calcular_semestre_undac(
            fecha_ingreso_api="2024",
            periodo_ingreso=1,
            periodo_referencia="2025-I"
        )
        self.assertEqual(res_2025.semestre, "3")
        self.assertEqual(res_2025.periodo_referencia, "2025-I")

    def test_periodo_referencia_desde_variable_de_entorno(self):
        """Verifica que el periodo de referencia se obtenga de la variable de entorno institucional."""
        os.environ['PERIODO_ACADEMICO_VIGENTE'] = '2026-II'
        res = calcular_semestre_undac(fecha_ingreso_api="2024", periodo_ingreso=1)
        self.assertEqual(res.semestre, "6")
        self.assertEqual(res.periodo_referencia, "2026-II")

    # -------------------------------------------------------------------------
    # 6. Contrato de API no verificado
    # -------------------------------------------------------------------------
    def test_api_undac_semestre_sin_contrato_demostrado_no_se_confirma(self):
        """El campo Semestre de la API externa no se marca como confirmado."""
        for sem_val in ["1", "6", "7", "9", "13"]:
            with self.subTest(semestre=sem_val):
                res = calcular_semestre_undac(
                    semestre_raw=sem_val,
                    fuente_oficial_verificada=False,
                    periodo_referencia="2026-II"
                )
                self.assertFalse(res.es_confirmado)
                self.assertNotEqual(res.estado, "CONFIRMADO")
                self.assertIn("no está demostrado contractualmente", res.motivo.lower())

    def test_fuente_oficial_verificada_requerida_para_confirmacion(self):
        """Solo fuentes con contrato verificado pueden confirmar semestre."""
        res_verificada = calcular_semestre_undac(
            semestre_raw="6",
            fuente_oficial_verificada=True,
            periodo_referencia="2026-II"
        )
        self.assertTrue(res_verificada.es_confirmado)
        self.assertEqual(res_verificada.estado, "CONFIRMADO")

    # -------------------------------------------------------------------------
    # 7. Estimación periodo I vs II y rango acotado
    # -------------------------------------------------------------------------
    def test_año_ingreso_periodo_I_vs_periodo_II_en_2026_II(self):
        """2024-I (semestre 6) frente a 2024-II (semestre 5) en 2026-II."""
        res_i = calcular_semestre_undac(
            fecha_ingreso_api="2024",
            periodo_ingreso=1,
            periodo_referencia="2026-II"
        )
        res_ii = calcular_semestre_undac(
            fecha_ingreso_api="2024",
            periodo_ingreso=2,
            periodo_referencia="2026-II"
        )

        self.assertEqual(res_i.estado, "ESTIMADO")
        self.assertEqual(res_i.semestre, "6")
        self.assertFalse(res_i.es_confirmado)

        self.assertEqual(res_ii.estado, "ESTIMADO")
        self.assertEqual(res_ii.semestre, "5")
        self.assertFalse(res_ii.es_confirmado)
        self.assertNotEqual(res_i.semestre, res_ii.semestre)

    def test_año_conocido_periodo_desconocido_rango_acotado(self):
        """Año 2024 sin periodo I/II devuelve rango acotado '5-6'."""
        res = calcular_semestre_undac(fecha_ingreso_api="2024", periodo_referencia="2026-II")
        self.assertEqual(res.estado, "ESTIMADO")
        self.assertEqual(res.semestre, "5-6")
        self.assertEqual(res.semestre_min, "5")
        self.assertEqual(res.semestre_max, "6")
        self.assertFalse(res.es_confirmado)

    # -------------------------------------------------------------------------
    # 8. Casos límite de año y código
    # -------------------------------------------------------------------------
    def test_fecha_ingreso_cero_o_nula_rechazo(self):
        """fecha_ingreso_api=0 o '0' debe ser rechazada y NO producir semestre 10."""
        for val_cero in [0, "0", "0.0"]:
            with self.subTest(val=val_cero):
                res = calcular_semestre_undac(fecha_ingreso_api=val_cero, periodo_referencia="2026-II")
                self.assertEqual(res.estado, "NO_DISPONIBLE")
                self.assertIsNone(res.semestre)

    def test_año_futuro_o_negativo_rechazo(self):
        """Años futuros o negativos deben ser rechazados."""
        res_futuro = calcular_semestre_undac(fecha_ingreso_api="2030", periodo_referencia="2026-II")
        self.assertEqual(res_futuro.estado, "NO_DISPONIBLE")

        res_neg = calcular_semestre_undac(fecha_ingreso_api=-2024, periodo_referencia="2026-II")
        self.assertEqual(res_neg.estado, "NO_DISPONIBLE")

    def test_codigo_invalido_sin_fallback_a_10(self):
        """Ante códigos basura o inválidos, NO debe existir fallback a semestre 10."""
        for cod in ["0", "ABC", "123", "00000000", "9900000000", None, ""]:
            with self.subTest(codigo=cod):
                res = calcular_semestre_undac(codigo=cod, periodo_referencia="2026-II")
                self.assertEqual(res.estado, "NO_DISPONIBLE")
                self.assertIsNone(res.semestre)
                self.assertNotEqual(res.semestre, "10")

    # -------------------------------------------------------------------------
    # 9. Flujo del escáner y auto-registro
    # -------------------------------------------------------------------------
    @patch('utils.queries_ingreso.get_db_connection')
    @patch('utils.queries_ingreso.requests.get')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_auto_registrar_alumno_semestre_api_no_demostrado_guarda_null(self, mock_get, mock_db):
        """
        Incluso si la API externa retorna Semestre='6', al no estar demostrado contractualmente,
        Alumnos.Semestre y SemestreID deben insertarse como NULL (None).
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'Nombres': 'ESTUDIANTE',
            'Apellido paterno': 'OFICIAL',
            'Apellido materno': 'TEST',
            'Dni': '77777778',
            'Programa facultad': 'Ingenieria de Sistemas',
            'Fecha de Ingreso': '2024',
            'Semestre': '6'
        }
        mock_get.return_value = mock_resp

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        exito, nombre = auto_registrar_alumno_api_undac("2400000002", periodo_referencia="2026-II")

        self.assertTrue(exito)
        insert_args = mock_cursor.execute.call_args[0]
        sql_called = insert_args[0]
        params = insert_args[1]
        self.assertIn("SemestreID", sql_called)
        # Orden real de parámetros en utils/queries_ingreso.py:
        # params[0]: codigo_clean (WHERE CodigoMatricula = ?)
        # params[1]: dni (WHERE DNI = ?)
        # params[2]: nombre_completo (NombreCompleto)
        # params[3]: dni (DNI)
        # params[4]: codigo_clean (CodigoMatricula)
        # params[5]: facultad (Escuela)
        # params[6]: semestre_val (Semestre) -> NULL (None)
        # params[7]: semestre_id_val (SemestreID) -> NULL (None)
        self.assertEqual(params[4], "2400000002", "params[4] corresponde a CodigoMatricula")
        self.assertEqual(params[5], "Ingenieria de Sistemas", "params[5] corresponde a Escuela")
        self.assertIsNone(params[6], "Alumnos.Semestre (params[6]) debe conservarse en NULL")
        self.assertIsNone(params[7], "Alumnos.SemestreID (params[7]) debe conservarse en NULL")

    @patch('utils.queries_ingreso.get_db_connection')
    @patch('utils.queries_ingreso.requests.get')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_auto_registrar_alumno_respuesta_https_normal(self, mock_get, mock_db):
        """
        Punto 3: Respuesta HTTPS normal (HTTP 200) de la API institucional:
        Verifica uso estricto de HTTPS, verify=True y allow_redirects=False.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_redirect = False
        mock_resp.json.return_value = {
            'Nombres': 'ANA',
            'Apellido paterno': 'TORRES',
            'Apellido materno': 'PAREDES',
            'Dni': '78901234',
            'Programa facultad': 'Ingeniería Ambiental',
            'Fecha de Ingreso': '2024'
        }
        mock_get.return_value = mock_resp

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        exito, nombre = auto_registrar_alumno_api_undac("2412345679", periodo_referencia="2026-II")
        self.assertTrue(exito)
        self.assertEqual(nombre, "TORRES PAREDES, ANA")

        args, kwargs = mock_get.call_args
        url_llamada = args[0]
        self.assertTrue(url_llamada.startswith("https://"), f"La URL debe ser HTTPS: {url_llamada}")
        self.assertTrue(kwargs.get("verify", False), "Debe requerir verificación TLS (verify=True)")
        self.assertFalse(kwargs.get("allow_redirects", True), "Debe impedir seguir redirecciones automáticamente (allow_redirects=False)")

    @patch('utils.queries_ingreso.get_db_connection')
    @patch('utils.queries_ingreso.requests.get')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_auto_registrar_alumno_redireccion_rechazada_sin_seguir_a_http(self, mock_get, mock_db):
        """
        Punto 3: Si la API responde con redirección (HTTP 301/302/307), la solicitud se rechaza
        inmediatamente sin continuar hacia HTTP ni insertar datos.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 301
        mock_resp.is_redirect = True
        mock_resp.headers = {'Location': 'http://inseguro.undac.edu.pe/api/alumnos'}
        mock_get.return_value = mock_resp

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        exito, nombre, err = auto_registrar_alumno_api_undac("2412345679", return_detail=True)
        self.assertFalse(exito)
        self.assertIsNone(nombre)
        self.assertIn("Redirección no permitida", err)
        # Garantiza que no se ejecutó ninguna sentencia SQL en la base de datos
        mock_cursor.execute.assert_not_called()

    @patch('utils.queries_ingreso.get_db_connection')
    def test_registrar_ingreso_general_sin_semestre_no_ejecuta_update_con_estimacion(self, mock_db):
        """En el escáner, si la BD devuelve semestre NULL o 'Regular', no ejecuta UPDATE con estimación."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = ('ACCESO CONCEDIDO', 'ESTUDIANTE PRUEBA', 'Sistemas', 'Regular')

        res = registrar_ingreso_general("2400000003", sala_id=1, periodo_referencia="2026-II")

        self.assertEqual(res['status'], 'success')
        self.assertIsNone(res['semestre'])

        for call_item in mock_cursor.execute.call_args_list:
            sql_called = call_item[0][0]
            self.assertNotIn("UPDATE Alumnos SET Semestre", sql_called)

    @patch('utils.queries_ingreso.auto_registrar_alumno_api_undac')
    @patch('utils.queries_ingreso.get_db_connection')
    def test_registrar_ingreso_general_flujo_no_encontrado_con_retry(self, mock_db, mock_auto_reg):
        """Flujo de reintento en escáner con usuario no encontrado."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ('USUARIO NO ENCONTRADO', None, None, None),
            ('ACCESO CONCEDIDO', 'NUEVO ESTUDIANTE', 'Ingenieria', None)
        ]
        mock_auto_reg.return_value = (True, 'NUEVO ESTUDIANTE')

        res = registrar_ingreso_general("2400000005", sala_id=1, periodo_referencia="2026-II")

        self.assertEqual(res['status'], 'success')
        self.assertIn("Autenticado vía API UNDAC", res['msg'])
        self.assertEqual(res['alumno'], 'NUEVO ESTUDIANTE')
        self.assertIsNone(res['semestre'])
        mock_auto_reg.assert_called_once_with("2400000005", periodo_referencia="2026-II")


if __name__ == '__main__':
    unittest.main()
