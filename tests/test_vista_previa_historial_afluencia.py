"""
Pruebas Unitarias Aisladas para la Vista Previa de Sincronización del Historial de Afluencia
Biblioteca UNDAC.

Pruebas con base de datos sustituida/bloqueada (Mocks) que verifican:
1. Acceso anónimo rechazado (HTTP 302/401).
2. Rol Consultor rechazado (HTTP 403).
3. Rol no permitido rechazado (HTTP 403).
4. Validación de sede del Supervisor contra catálogo real (dbo.Salas):
   - Sede ausente, vacía o con solo espacios rechazada (HTTP 403).
   - Sede inexistente rechazada (HTTP 403) antes de consultar el historial.
   - Sede inactiva rechazada (HTTP 403) antes de consultar el historial.
   - Sede válida y activa permitida (HTTP 200).
5. Segregación estricta de Sede NULL / Desconocida:
   - Sede NULL nunca se atribuye a Central.
   - Se clasifica como 'Desconocida' por separado y se excluye de los totales de Central y cualquier sede con nombre.
   - Mismo criterio en las dos consultas (RegistroIngresos y AfluenciaHistoricaAgregada) y en todos los resultados.
6. Cierre operativo exacto a las 20:45:00:
   - Límites 20:44:59, 20:45:00 son franja válida (<= 20:45:00).
   - Límites 20:45:00.001 y 20:45:59.999 son posteriores al cierre (> 20:45:00, EsHorarioAtipico = 1).
   - Conserva por separado ingresos válidos y posteriores al cierre cuando una hora tenga ambos.
7. Cierre del 24/07/2026 identificado como regla configurada pendiente de confirmación documental.
8. Aislamiento por sede y privacidad de datos.
"""

import unittest
from datetime import date, datetime, time as dt_time
from unittest.mock import MagicMock, patch

from app import app
from ml.services.vista_previa_historial import (
    calcular_banderas_analiticas,
    clasificar_marca_temporal,
    obtener_vista_previa_historial_afluencia,
    validar_sede_activa,
    SEDE_DESCONOCIDA,
    FERIADOS_CONFIRMADOS_2026,
    CIERRE_CONFIGURADO_24_JULIO,
    CIERRE_CONFIRMADO_27_JULIO,
    HORA_CIERRE_OPERATIVO
)


class TestControlAccesoRutaVistaPrevia(unittest.TestCase):
    """Verifica autenticación y autorización por roles para /admin/api/ml/vista-previa-historial."""

    def setUp(self):
        self.app = app
        self.client = self.app.test_client()

    def test_1_acceso_anonimo_rechazado(self):
        """Acceso sin sesión administrativa debe ser rechazado (302 redirect o 401)."""
        resp = self.client.get("/admin/api/ml/vista-previa-historial")
        self.assertIn(resp.status_code, (302, 401))

    def test_2_rol_consultor_rechazado(self):
        """El rol Consultor debe ser rechazado con HTTP 403."""
        with self.client.session_transaction() as sess:
            sess["admin_user"] = "consultor_test"
            sess["admin_rol"] = "Consultor"

        resp = self.client.get("/admin/api/ml/vista-previa-historial")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("no tiene permisos", data["mensaje"])

    def test_3_rol_no_permitido_rechazado(self):
        """Cualquier rol no contemplado en la lista explícita debe ser rechazado con HTTP 403."""
        for rol_invalido in ["OperadorScanner", "Estudiante", "Visitante", "Desconocido"]:
            with self.subTest(rol=rol_invalido):
                with self.client.session_transaction() as sess:
                    sess["admin_user"] = "user_invalido"
                    sess["admin_rol"] = rol_invalido

                resp = self.client.get("/admin/api/ml/vista-previa-historial")
                self.assertEqual(resp.status_code, 403)
                data = resp.get_json()
                self.assertEqual(data["status"], "error")

    def test_4_supervisor_sin_sede_o_vacia_rechazado(self):
        """Supervisor con sede ausente, vacía o con espacios debe ser rechazado con HTTP 403."""
        casos_sede_invalida = [None, "", "   ", "\t  \n"]
        for sede_inv in casos_sede_invalida:
            with self.subTest(sede=sede_inv):
                with self.client.session_transaction() as sess:
                    sess["admin_user"] = "supervisor_invalido"
                    sess["admin_rol"] = "Supervisor"
                    sess["admin_sede"] = sede_inv

                resp = self.client.get("/admin/api/ml/vista-previa-historial")
                self.assertEqual(resp.status_code, 403)
                data = resp.get_json()
                self.assertEqual(data["status"], "error")
                self.assertIn("sede asignada válida", data["mensaje"])

    @patch("routes.admin_ml.validar_sede_activa")
    @patch("routes.admin_ml.obtener_vista_previa_historial_afluencia")
    def test_5_supervisor_con_sede_inexistente_rechazado_antes_de_consultar(self, mock_vista, mock_validar):
        """Supervisor con sede inexistente en catálogo debe ser rechazado con HTTP 403 antes de consultar historial."""
        mock_validar.return_value = (False, None, "sede_inexistente")

        with self.client.session_transaction() as sess:
            sess["admin_user"] = "supervisor_fantasma"
            sess["admin_rol"] = "Supervisor"
            sess["admin_sede"] = "SedeInexistente"

        resp = self.client.get("/admin/api/ml/vista-previa-historial")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("no existe en el catálogo", data["mensaje"])
        mock_vista.assert_not_called()

    @patch("routes.admin_ml.validar_sede_activa")
    @patch("routes.admin_ml.obtener_vista_previa_historial_afluencia")
    def test_6_supervisor_con_sede_inactiva_rechazado_antes_de_consultar(self, mock_vista, mock_validar):
        """Supervisor con sede inactiva debe ser rechazado con HTTP 403 antes de consultar historial."""
        mock_validar.return_value = (False, None, "sede_inactiva")

        with self.client.session_transaction() as sess:
            sess["admin_user"] = "supervisor_inactivo"
            sess["admin_rol"] = "Supervisor"
            sess["admin_sede"] = "SedeInactiva"

        resp = self.client.get("/admin/api/ml/vista-previa-historial")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("se encuentra inactiva", data["mensaje"])
        mock_vista.assert_not_called()

    @patch("routes.admin_ml.validar_sede_activa")
    @patch("routes.admin_ml.obtener_vista_previa_historial_afluencia")
    def test_7_supervisor_con_sede_valida_permitido(self, mock_vista, mock_validar):
        """Supervisor con sede activa válida accede y consulta exclusivamente su sede."""
        mock_validar.return_value = (True, "Tarma", None)
        mock_vista.return_value = {
            "status": "success",
            "tipo_operacion": "vista_previa_solo_lectura",
            "filtro_sede_aplicado": "Tarma"
        }

        with self.client.session_transaction() as sess:
            sess["admin_user"] = "supervisor_tarma"
            sess["admin_rol"] = "Supervisor"
            sess["admin_sede"] = "Tarma"

        resp = self.client.get("/admin/api/ml/vista-previa-historial")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "success")
        mock_vista.assert_called_once_with(sede_filtro="Tarma")

    @patch("routes.admin_ml.obtener_vista_previa_historial_afluencia")
    def test_8_roles_superadmin_y_admin_permitidos(self, mock_vista):
        """SuperAdmin y Administrador tienen acceso y pueden consultar vista previa."""
        mock_vista.return_value = {"status": "success", "tipo_operacion": "vista_previa_solo_lectura"}

        for rol in ["SuperAdmin", "Administrador"]:
            with self.subTest(rol=rol):
                with self.client.session_transaction() as sess:
                    sess["admin_user"] = f"{rol.lower()}_user"
                    sess["admin_rol"] = rol

                resp = self.client.get("/admin/api/ml/vista-previa-historial")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.get_json()["status"], "success")


class TestValidacionCatalogoSedes(unittest.TestCase):
    """Verifica la función validar_sede_activa contra la fuente real del catálogo (dbo.Salas)."""

    def setUp(self):
        self.cursor_mock = MagicMock()

    def test_sede_ausente_o_vacia(self):
        """Sede ausente, None, vacía o con solo espacios debe retornar 'sede_ausente_o_vacia'."""
        for caso in [None, "", "   ", "\t\n"]:
            with self.subTest(caso=caso):
                valida, canon, motivo = validar_sede_activa(caso, cursor=self.cursor_mock)
                self.assertFalse(valida)
                self.assertIsNone(canon)
                self.assertEqual(motivo, "sede_ausente_o_vacia")
                self.cursor_mock.execute.assert_not_called()

    def test_sede_inexistente_en_salas(self):
        """Si dbo.Salas no retorna ninguna fila para la sede, reporta 'sede_inexistente'."""
        self.cursor_mock.fetchone.return_value = None

        valida, canon, motivo = validar_sede_activa("SedeDesconocida", cursor=self.cursor_mock)
        self.assertFalse(valida)
        self.assertIsNone(canon)
        self.assertEqual(motivo, "sede_inexistente")
        self.cursor_mock.execute.assert_called_once()
        sql, params = self.cursor_mock.execute.call_args[0]
        self.assertIn("FROM dbo.Salas", sql)
        self.assertEqual(params, ("SedeDesconocida",))

    def test_sede_inactiva_sin_salas_activas(self):
        """Si dbo.Salas retorna la sede pero TieneSalasActivas = 0, reporta 'sede_inactiva'."""
        self.cursor_mock.fetchone.return_value = ("SedeCerrada", 0)

        valida, canon, motivo = validar_sede_activa("sedecerrada", cursor=self.cursor_mock)
        self.assertFalse(valida)
        self.assertIsNone(canon)
        self.assertEqual(motivo, "sede_inactiva")

    def test_sede_valida_con_salas_activas(self):
        """Si dbo.Salas retorna TieneSalasActivas = 1, la sede es válida y devuelve nombre canónico."""
        self.cursor_mock.fetchone.return_value = ("Central", 1)

        valida, canon, motivo = validar_sede_activa("central", cursor=self.cursor_mock)
        self.assertTrue(valida)
        self.assertEqual(canon, "Central")
        self.assertIsNone(motivo)


class TestSedeDesconocidaYCentral(unittest.TestCase):
    """
    Verifica que los registros con Sede NULL o en blanco:
    - NO se atribuyan a Central.
    - Se clasifiquen como 'Desconocida' de forma separada.
    - Se excluyan de los totales de Central y de cualquier sede con nombre en ambas consultas.
    """

    def setUp(self):
        self.cursor_mock = MagicMock()

    def test_filtro_central_excluye_sede_null(self):
        """
        Al consultar Central con filtro, la consulta SQL debe exigir explícitamente
        la coincidencia de Sede y excluir los registros con Sede NULL.
        """
        # Simulamos datos en RI: solo 10 ingresos confirmados de Central
        filas_ri_central = [
            (date(2026, 10, 1), 10, "Central", 10, 10, 0)
        ]
        filas_aha_central = [
            (date(2026, 6, 1), 10, "Central", 10, 0, 0, 0, 0, 0)
        ]
        self.cursor_mock.fetchall.side_effect = [filas_ri_central, filas_aha_central]

        res = obtener_vista_previa_historial_afluencia(
            sede_filtro="Central",
            cursor_externo=self.cursor_mock
        )

        # 1. Comprobar que ambas consultas filtran con el CASE WHEN segregador
        llamadas = self.cursor_mock.execute.call_args_list
        sql_ri, params_ri = llamadas[0][0]
        sql_aha, params_aha = llamadas[1][0]

        self.assertIn("THEN 'Desconocida' ELSE Sede END) = ?", sql_ri)
        self.assertEqual(params_ri, ("Central",))
        self.assertNotIn("ISNULL(Sede, 'Central') = ?", sql_ri)

        self.assertIn("THEN 'Desconocida' ELSE Sede END) = ?", sql_aha)
        self.assertEqual(params_aha, ("Central",))

        # 2. Resumen de conteos para Central: exactamente 10, no contiene desconocidos
        resumen = res["resumen_conteos_base"]
        self.assertEqual(resumen["sede_consultada"], "Central")
        self.assertEqual(resumen["RegistroIngresos_total_ingresos_reales"], 10)
        self.assertEqual(resumen["sede_desconocida_segregada"]["RegistroIngresos_ingresos"], 0)

    def test_consulta_general_segrega_sede_null_y_no_la_atribuye_a_central(self):
        """
        Al consultar sin filtro de sede (todas las sedes):
        - Fila 1: Central con 20 ingresos.
        - Fila 2: Sede NULL mapeada a 'Desconocida' con 7 ingresos.
        Total general = 27.
        Central debe tener exactamente 20 ingresos (NUNCA 27).
        'Desconocida' debe estar listada por separado con 7 ingresos.
        """
        filas_ri = [
            (date(2026, 10, 2), 10, "Central", 20, 20, 0),
            (date(2026, 10, 2), 10, "Desconocida", 7, 7, 0)
        ]
        filas_aha = []
        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            sede_filtro=None,
            cursor_externo=self.cursor_mock
        )

        # 1. Conteo total general
        resumen_base = res["resumen_conteos_base"]
        self.assertEqual(resumen_base["RegistroIngresos_total_ingresos_reales"], 27)

        # 2. Conteo segregado de sede desconocida
        seg = resumen_base["sede_desconocida_segregada"]
        self.assertEqual(seg["RegistroIngresos_ingresos"], 7)
        self.assertEqual(seg["RegistroIngresos_grupos"], 1)

        # 3. Desglose por sede en proyección posterior
        desglose = res["proyeccion_periodo_posterior"]["desglose_por_sede"]
        self.assertIn("Central", desglose)
        self.assertIn("Desconocida", desglose)

        # Central SOLO tiene sus 20 ingresos, NO absorbió los 7 de Desconocida
        self.assertEqual(desglose["Central"]["ingresos"], 20)
        self.assertEqual(desglose["Central"]["filas"], 1)

        # Desconocida reporta sus 7 ingresos de forma independiente
        self.assertEqual(desglose["Desconocida"]["ingresos"], 7)
        self.assertEqual(desglose["Desconocida"]["filas"], 1)

        # Indicador explícito de sede desconocida
        res_sede_desc = res["proyeccion_periodo_posterior"]["sede_desconocida"]
        self.assertEqual(res_sede_desc["ingresos_nuevos"], 7)
        self.assertEqual(res_sede_desc["filas_nuevas"], 1)


class TestAislamientoSedeYPrivacidad(unittest.TestCase):
    """Verifica que el filtro de sede se aplique antes de calcular conteos y que no filtre datos sensibles."""

    def setUp(self):
        self.cursor_mock = MagicMock()

    def test_aislamiento_estricto_supervisor_sede(self):
        """
        Un supervisor de Tarma solo debe originar consultas filtradas por 'Tarma'
        y solo recibir métricas de su sede, sin datos de Central ni globales.
        """
        filas_ri_tarma = [
            (date(2026, 10, 1), 10, "Tarma", 15, 15, 0),
            (date(2026, 10, 1), 11, "Tarma", 12, 12, 0)
        ]
        filas_aha_tarma = []

        self.cursor_mock.fetchall.side_effect = [filas_ri_tarma, filas_aha_tarma]

        res = obtener_vista_previa_historial_afluencia(
            sede_filtro="Tarma",
            cursor_externo=self.cursor_mock
        )

        # 1. Verificar consultas SQL filtradas con parámetro antes del cálculo
        llamadas_sql = self.cursor_mock.execute.call_args_list
        sql_ri, params_ri = llamadas_sql[0][0]
        self.assertIn("THEN 'Desconocida' ELSE Sede END) = ?", sql_ri)
        self.assertEqual(params_ri, ("Tarma",))

        sql_aha, params_aha = llamadas_sql[1][0]
        self.assertIn("THEN 'Desconocida' ELSE Sede END) = ?", sql_aha)
        self.assertEqual(params_aha, ("Tarma",))

        # 2. Conteos exclusivamente de Tarma
        resumen = res["resumen_conteos_base"]
        self.assertEqual(resumen["sede_consultada"], "Tarma")
        self.assertEqual(resumen["RegistroIngresos_total_grupos"], 2)
        self.assertEqual(resumen["RegistroIngresos_total_ingresos_reales"], 27)

        # 3. Desglose solo contiene Tarma
        desglose = res["proyeccion_periodo_posterior"]["desglose_por_sede"]
        self.assertIn("Tarma", desglose)
        self.assertNotIn("Central", desglose)
        self.assertNotIn("La Merced", desglose)

        # 4. Privacidad: Ninguna fila debe contener marcas temporales exactas individuales
        for fila in res["proyeccion_periodo_posterior"]["detalle_filas_nuevas"]:
            self.assertNotIn("PrimerIngreso", fila)
            self.assertNotIn("UltimoIngreso", fila)
            self.assertNotIn("PrimerIngresoFranja", fila)
            self.assertNotIn("UltimoIngresoFranja", fila)
            self.assertEqual(fila["Sede"], "Tarma")


class TestLimitesExactosCierre2045(unittest.TestCase):
    """
    Verifica el cierre operativo exactamente a las 20:45:00:
    - 20:44:59 (y 20:44:59.999) -> válido operativo (<= 20:45:00).
    - 20:45:00 (y 20:45:00.000) -> límite exacto válido (<= 20:45:00).
    - 20:45:00.001 -> posterior al cierre (> 20:45:00, atípico).
    - 20:45:59.999 -> posterior al cierre (> 20:45:00, atípico).
    - Hora 20 con ingresos válidos y posteriores -> conserva ambos conteos por separado.
    """

    def test_clasificacion_marcas_temporales_limites(self):
        """Evalúa los límites temporales exactos mediante clasificar_marca_temporal."""
        # 1. 20:44:59 -> Válido
        c1 = clasificar_marca_temporal("20:44:59")
        self.assertTrue(c1["es_hora_20"])
        self.assertTrue(c1["es_valido_hora20"])
        self.assertFalse(c1["es_post_cierre_hora20"])

        # 1b. 20:44:59.999 -> Válido
        c1b = clasificar_marca_temporal("20:44:59.999000")
        self.assertTrue(c1b["es_valido_hora20"])
        self.assertFalse(c1b["es_post_cierre_hora20"])

        # 2. 20:45:00 (límite exacto) -> Válido
        c2 = clasificar_marca_temporal("20:45:00")
        self.assertTrue(c2["es_valido_hora20"])
        self.assertFalse(c2["es_post_cierre_hora20"])

        # 2b. 20:45:00.000000 -> Válido
        c2b = clasificar_marca_temporal(dt_time(20, 45, 0, 0))
        self.assertTrue(c2b["es_valido_hora20"])
        self.assertFalse(c2b["es_post_cierre_hora20"])

        # 3. 20:45:00.001 -> Posterior al cierre (> 20:45:00)
        c3 = clasificar_marca_temporal("20:45:00.001000")
        self.assertFalse(c3["es_valido_hora20"])
        self.assertTrue(c3["es_post_cierre_hora20"])
        self.assertTrue(c3["es_post_cierre_general"])

        # 4. 20:45:59.999 -> Posterior al cierre (> 20:45:00)
        c4 = clasificar_marca_temporal("20:45:59.999000")
        self.assertFalse(c4["es_valido_hora20"])
        self.assertTrue(c4["es_post_cierre_hora20"])
        self.assertTrue(c4["es_post_cierre_general"])

    def test_franja_20h_valida_solo_ingresos_hasta_2045(self):
        """Hora 20 con solo ingresos válidos (<= 20:45:00) -> EsFranjaParcial=1, EsHorarioAtipico=0."""
        res = calcular_banderas_analiticas(
            fecha_str="2026-10-01",
            hora_int=20,
            cantidad_hora20_valida=12,
            cantidad_hora20_post_cierre=0
        )
        self.assertEqual(res["EsFranjaParcial"], 1)
        self.assertEqual(res["EsHorarioAtipico"], 0)
        self.assertEqual(res["EstadoFranja20h"], "franja_parcial_valida_45m")

    def test_franja_20h_solo_post_cierre(self):
        """Hora 20 con solo ingresos post-cierre (> 20:45:00) -> EsFranjaParcial=0, EsHorarioAtipico=1."""
        res = calcular_banderas_analiticas(
            fecha_str="2026-10-01",
            hora_int=20,
            cantidad_hora20_valida=0,
            cantidad_hora20_post_cierre=3
        )
        self.assertEqual(res["EsFranjaParcial"], 0)
        self.assertEqual(res["EsHorarioAtipico"], 1)
        self.assertEqual(res["EstadoFranja20h"], "posterior_al_cierre")

    def test_franja_20h_mixta_conserva_ambos_conteos_por_separado(self):
        """
        Hora 20 con 8 ingresos válidos (<= 20:45:00) y 2 ingresos posteriores (> 20:45:00):
        - Debe clasificar como 'mixta_con_registros_post_cierre'.
        - Debe marcar EsFranjaParcial = 1 y EsHorarioAtipico = 1.
        - En la vista previa se conservan IngresosValidos = 8 e IngresosPostCierre = 2.
        """
        cursor_mock = MagicMock()
        filas_ri = [
            (date(2026, 10, 1), 20, "Central", 10, 8, 2)  # Total 10: 8 válidos, 2 post
        ]
        filas_aha = []
        cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=cursor_mock
        )

        filas_nuevas = res["proyeccion_periodo_posterior"]["detalle_filas_nuevas"]
        self.assertEqual(len(filas_nuevas), 1)
        f20 = filas_nuevas[0]

        self.assertEqual(f20["CantidadIngresos"], 10)
        self.assertEqual(f20["IngresosValidos"], 8)
        self.assertEqual(f20["IngresosPostCierre"], 2)
        self.assertEqual(f20["CantidadHora20Valida"], 8)
        self.assertEqual(f20["CantidadHora20PostCierre"], 2)
        self.assertEqual(f20["EsFranjaParcial"], 1)
        self.assertEqual(f20["EsHorarioAtipico"], 1)
        self.assertEqual(f20["EstadoFranja20h"], "mixta_con_registros_post_cierre")

        # Totales globales de ingresos válidos y posteriores conservados por separado
        proy = res["proyeccion_periodo_posterior"]
        self.assertEqual(proy["total_ingresos_nuevos_validos"], 8)
        self.assertEqual(proy["total_ingresos_nuevos_post_cierre"], 2)


class TestCalendarioOperativoYRegla24Julio(unittest.TestCase):
    """
    Verifica las reglas del calendario y que el 24/07 no se declare confirmado institucionalmente,
    sino como regla configurada pendiente de confirmación documental.
    """

    def test_cierre_24_julio_identificado_como_regla_pendiente_confirmacion(self):
        """El 24/07 debe reportarse como regla configurada pendiente de confirmación documental."""
        cursor_mock = MagicMock()
        cursor_mock.fetchall.side_effect = [[], []]

        res = obtener_vista_previa_historial_afluencia(cursor_externo=cursor_mock)
        regla_24 = res["reglas_aplicadas"]["cierre_24_julio"]

        self.assertEqual(regla_24["fecha"], "2026-07-24")
        self.assertEqual(regla_24["estado"], "regla_configurada_pendiente_confirmacion_documental")
        self.assertIn("pendiente de confirmación documental", regla_24["descripcion"].lower())

    def test_cierre_27_julio_confirmado(self):
        """El 27/07/2026 es cierre institucional confirmado previo a Fiestas Patrias (EsCierre27Julio=1, horario regular 10h=0)."""
        res = calcular_banderas_analiticas("2026-07-27", 10)
        self.assertEqual(res["EsCierre27Julio"], 1)
        self.assertEqual(res["EsHorarioAtipico"], 0)

    def test_fila_agosto_cierre_agosto_1_y_horario_atipico_0(self):
        """
        Una fila en día laborable de agosto durante horario operativo (ej. lunes 2026-08-03 a las 10:00)
        debe tener EsCierreAgosto=1 y EsHorarioAtipico=0 (metodología de banderas ortogonales).
        """
        res = calcular_banderas_analiticas("2026-08-03", 10)
        self.assertEqual(res["EsCierreAgosto"], 1)
        self.assertEqual(res["EsHorarioAtipico"], 0)
        self.assertEqual(res["EsFeriado"], 0)

    def test_fila_agosto_coincidencia_exacta_entre_ri_y_aha(self):
        """
        Si RI y AHA tienen una fila en agosto con EsCierreAgosto=1 y EsHorarioAtipico=0,
        la vista previa debe clasificarla como coincidencia exacta histórica y no como discrepancia.
        """
        cursor_mock = MagicMock()
        filas_ri = [
            (date(2026, 8, 3), 10, "Central", 15, 15, 0)
        ]
        filas_aha = [
            (date(2026, 8, 3), 10, "Central", 15, 0, 1, 0, 0, 0)  # EsHorarioAtipico=0, EsCierreAgosto=1
        ]
        cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=cursor_mock
        )

        hist = res["reconciliacion_periodo_historico"]
        self.assertEqual(hist["coincidencias_exactas_filas"], 1)
        self.assertEqual(hist["discrepancias_filas"], 0)
        self.assertEqual(len(hist["detalle_discrepancias"]), 0)


class TestComparacionBanderasAnaliticas(unittest.TestCase):
    """Verifica la comparación detallada de banderas analíticas entre RI y AHA."""

    def setUp(self):
        self.cursor_mock = MagicMock()

    def test_discrepancia_en_banderas_detectada(self):
        """
        Si la cantidad coincide pero las banderas analíticas difieren,
        debe marcarse como discrepancia y detallar qué banderas no coinciden.
        """
        filas_ri = [
            (date(2026, 6, 1), 10, "Central", 50, 50, 0)
        ]
        filas_aha = [
            (date(2026, 6, 1), 10, "Central", 50, 1, 0, 0, 0, 0)
        ]

        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=self.cursor_mock
        )

        hist = res["reconciliacion_periodo_historico"]
        self.assertEqual(hist["coincidencias_exactas_filas"], 0)
        self.assertEqual(hist["discrepancias_filas"], 1)

        detalle = hist["detalle_discrepancias"][0]
        self.assertFalse(detalle["TieneDiscrepanciaCantidad"])
        self.assertTrue(detalle["TieneDiscrepanciaBanderas"])
        self.assertIn("EsHorarioAtipico", detalle["DiscrepanciasBanderas"])
        self.assertEqual(detalle["DiscrepanciasBanderas"]["EsHorarioAtipico"]["esperado"], 0)
        self.assertEqual(detalle["DiscrepanciasBanderas"]["EsHorarioAtipico"]["almacenado"], 1)

    def test_separacion_discrepancias_cantidad_vs_banderas(self):
        """
        Verifica que el resumen separe claramente las discrepancias de cantidad
        de las discrepancias de banderas analíticas.
        """
        # Fila 1: Discrepancia solo en cantidad (2026-05-05 17h: RI=2 vs AHA=4, banderas coinciden)
        # Fila 2: Discrepancia solo en banderas (2026-06-01 10h: RI=50 vs AHA=50, EsHorarioAtipico difiere)
        filas_ri = [
            (date(2026, 5, 5), 17, "Central", 2, 2, 0),
            (date(2026, 6, 1), 10, "Central", 50, 50, 0)
        ]
        filas_aha = [
            (date(2026, 5, 5), 17, "Central", 4, 0, 0, 0, 0, 0),
            (date(2026, 6, 1), 10, "Central", 50, 1, 0, 0, 0, 0)
        ]
        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=self.cursor_mock
        )

        hist = res["reconciliacion_periodo_historico"]
        self.assertEqual(hist["discrepancias_filas"], 2)
        resumen_disc = hist["resumen_discrepancias"]

        self.assertEqual(resumen_disc["total_discrepancias"], 2)
        self.assertEqual(resumen_disc["discrepancias_cantidad"], 1)
        self.assertEqual(resumen_disc["discrepancias_solo_cantidad"], 1)
        self.assertEqual(resumen_disc["discrepancias_banderas"], 1)
        self.assertEqual(resumen_disc["discrepancias_solo_banderas"], 1)
        self.assertEqual(resumen_disc["discrepancias_mixtas_cantidad_y_banderas"], 0)

        self.assertEqual(len(hist["detalle_discrepancias_cantidad"]), 1)
        self.assertEqual(hist["detalle_discrepancias_cantidad"][0]["Fecha"], "2026-05-05")
        self.assertEqual(len(hist["detalle_discrepancias_banderas"]), 1)
        self.assertEqual(hist["detalle_discrepancias_banderas"][0]["Fecha"], "2026-06-01")


class TestFilasExclusivasYDiscrepanciaHistorica(unittest.TestCase):
    """Verifica filas exclusivas en ambos lados y la discrepancia histórica del 05/05/2026."""

    def setUp(self):
        self.cursor_mock = MagicMock()

    def test_discrepancia_historica_05_mayo(self):
        """
        Simula la discrepancia real del 05/05/2026 a las 17h:
        2 ingresos registrados en RI vs 4 ingresos en AHA.
        """
        filas_ri = [(date(2026, 5, 5), 17, "Central", 2, 2, 0)]
        filas_aha = [(date(2026, 5, 5), 17, "Central", 4, 0, 0, 0, 0, 0)]

        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=self.cursor_mock
        )

        hist = res["reconciliacion_periodo_historico"]
        self.assertEqual(hist["discrepancias_filas"], 1)
        disc = hist["detalle_discrepancias"][0]
        self.assertEqual(disc["Fecha"], "2026-05-05")
        self.assertEqual(disc["Hora"], 17)
        self.assertEqual(disc["CantidadEnRI"], 2)
        self.assertEqual(disc["CantidadEnAHA"], 4)
        self.assertEqual(disc["DiferenciaCantidad"], -2)
        self.assertTrue(disc["TieneDiscrepanciaCantidad"])

    def test_filas_exclusivas_aha_pre_y_post_corte(self):
        """
        Verifica la clasificación de filas que solo existen en AHA:
        - Pre-corte -> filas_solo_en_AfluenciaHistoricaAgregada.
        - Post-corte -> filas_solo_en_aha_post_corte.
        """
        filas_ri = []
        filas_aha = [
            (date(2026, 4, 10), 12, "Central", 2, 0, 0, 0, 0, 0),
            (date(2026, 10, 5), 10, "Central", 15, 0, 0, 0, 0, 0)
        ]

        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=self.cursor_mock
        )

        solo_aha_hist = res["reconciliacion_periodo_historico"]["filas_solo_en_AfluenciaHistoricaAgregada"]
        self.assertEqual(solo_aha_hist, 1)
        self.assertEqual(res["reconciliacion_periodo_historico"]["detalle_solo_en_AHA"][0]["Fecha"], "2026-04-10")

        claves_post = res["proyeccion_periodo_posterior"]["analisis_claves_existentes"]
        self.assertEqual(claves_post["filas_solo_en_aha_post_corte"], 1)
        self.assertEqual(claves_post["detalle_solo_en_aha_post_corte"][0]["Fecha"], "2026-10-05")

    def test_filas_exclusivas_ri_pre_y_post_corte(self):
        """
        Verifica la clasificación de filas que solo existen en RI:
        - Pre-corte -> filas_solo_en_RegistroIngresos.
        - Post-corte -> filas_nuevas_esperadas.
        """
        filas_ri = [
            (date(2026, 5, 10), 10, "Central", 20, 20, 0),
            (date(2026, 10, 6), 11, "Central", 35, 35, 0)
        ]
        filas_aha = []

        self.cursor_mock.fetchall.side_effect = [filas_ri, filas_aha]

        res = obtener_vista_previa_historial_afluencia(
            fecha_corte="2026-09-28",
            cursor_externo=self.cursor_mock
        )

        self.assertEqual(res["reconciliacion_periodo_historico"]["filas_solo_en_RegistroIngresos"], 1)
        self.assertEqual(res["reconciliacion_periodo_historico"]["detalle_solo_en_RI"][0]["Fecha"], "2026-05-10")

        self.assertEqual(res["proyeccion_periodo_posterior"]["filas_nuevas_esperadas"], 1)
        self.assertEqual(res["proyeccion_periodo_posterior"]["total_ingresos_nuevos_esperados"], 35)


if __name__ == "__main__":
    unittest.main()
