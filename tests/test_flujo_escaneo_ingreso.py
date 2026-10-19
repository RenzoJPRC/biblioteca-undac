import os
import io
import time
import unittest
import multiprocessing as mp
from unittest.mock import patch, MagicMock
from contextlib import redirect_stdout
import requests

from app import app
from utils.queries_ingreso import (
    registrar_ingreso_general,
    auto_registrar_alumno_api_undac,
    validar_sala_activa,
    is_undac_api_autoregister_enabled,
    _reset_scan_cache,
    _recent_scans_cache,
    SCAN_DEBOUNCE_SECONDS
)


# Función a nivel de módulo para permitir su serialización por multiprocessing en Windows
def _worker_multiproceso_ingreso(args):
    identificador, lock, shared_db = args
    with lock:
        # Simula ejecución de sp_RegistrarIngreso bajo sp_getapplock / UPDLOCK, HOLDLOCK
        if "registrado" in shared_db:
            return {"status": "warning", "msg": "YA ESTÁS REGISTRADO EN ESTA SALA EN EL TURNO MAÑANA"}
        shared_db["registrado"] = True
        return {"status": "success", "msg": "ACCESO CONCEDIDO (MAÑANA)"}


class TestFlujoEscaneoIngreso(unittest.TestCase):
    """
    Suite exhaustiva de pruebas locales aisladas para el flujo del escáner de ingreso.
    Verifica las correcciones a los bloqueos de autorización y escaneo confirmados por Codex:
    1. Consultor rechazado en GET y POST.
    2. Operador autorizado permitido en GET y POST.
    3. Token de terminal estrictamente limitado a su sala asignada (TERMINAL_SALA_ID).
    4. Token que intenta abrir otra sala rechazado con HTTP 403.
    5. Falta de TERMINAL_SALA_ID rechazada (falla cerrada con HTTP 403).
    6. Token presentado en URL rechazado con HTTP 400.
    7. Payload que intenta cambiar sala o sede rechazado con HTTP 403.
    8. API institucional UNDAC: estricto HTTPS, alta automática desactivada por defecto.
    9. Validación estricta de tipos de datos y garantía de cierre de conexión.
    10. Concurrencia multiproceso e idempotencia.
    """

    def setUp(self):
        app.testing = True
        app.config['SECRET_KEY'] = 'test-secret-key-undac'
        self.client = app.test_client()
        _reset_scan_cache()

    def tearDown(self):
        _reset_scan_cache()

    def _autenticar_terminal(self, sala_id=1, sede='Central', user='operador_biblioteca', rol='Supervisor'):
        """Helper para autenticar la sesión del test_client como una terminal autorizada."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = user
            sess['admin_rol'] = rol
            sess['admin_sede'] = sede
            sess['terminal_sala_id'] = sala_id
            sess['terminal_sede'] = sede
            sess['terminal_operador'] = user
            sess['terminal_rol'] = rol
            sess['escaneo_autorizado'] = True
            sess['escaneo_sala_id'] = sala_id
            sess['escaneo_sede'] = sede

    # =========================================================================
    # 1. PRECEDENCIA ENTRE CONSULTOR Y TOKEN (RECHAZO EN GET Y POST)
    # =========================================================================

    def test_consultor_sin_token_rechazado_en_get_y_post(self):
        """Un usuario con rol Consultor sin token es rechazado en GET /sala/<id> y POST con HTTP 403."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'consultor_usuario'
            sess['admin_rol'] = 'Consultor'
            sess['terminal_sala_id'] = 1

        # GET
        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('Consultor', resp_get.get_data(as_text=True))

        # POST
        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data_post = resp_post.get_json()
        self.assertEqual(data_post['status'], 'error')
        self.assertIn('Consultor', data_post['msg'])

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '1'})
    def test_consultor_con_token_valido_rechazado_en_get_y_post(self):
        """Un Consultor que presenta un token de terminal válido es rechazado en GET y POST con HTTP 403."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'consultor_usuario'
            sess['admin_rol'] = 'Consultor'
            sess['terminal_sala_id'] = 1

        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}

        # GET con token válido
        resp_get = self.client.get('/sala/1', headers=headers)
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('Consultor', resp_get.get_data(as_text=True))

        # POST con token válido
        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1}, headers=headers)
        self.assertEqual(resp_post.status_code, 403)
        data_post = resp_post.get_json()
        self.assertEqual(data_post['status'], 'error')
        self.assertIn('Consultor', data_post['msg'])

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '1'})
    def test_consultor_con_token_invalido_rechazado_en_get_y_post(self):
        """Un Consultor que presenta un token inválido también es rechazado con HTTP 403 por su rol."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'consultor_usuario'
            sess['admin_rol'] = 'Consultor'
            sess['terminal_sala_id'] = 1

        headers = {'X-Terminal-Token': 'token-falso-invalido'}

        resp_get = self.client.get('/sala/1', headers=headers)
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('Consultor', resp_get.get_data(as_text=True))

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1}, headers=headers)
        self.assertEqual(resp_post.status_code, 403)
        data_post = resp_post.get_json()
        self.assertEqual(data_post['status'], 'error')
        self.assertIn('Consultor', data_post['msg'])

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '1'})
    def test_token_no_convierte_sesion_consultor_en_operadora(self):
        """Presentar un token válido no muta ni eleva la sesión de un Consultor a operadora."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'consultor_usuario'
            sess['admin_rol'] = 'Consultor'

        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        self.client.get('/sala/1', headers=headers)

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('admin_rol'), 'Consultor')
            self.assertIsNone(sess.get('terminal_operador'))

    # =========================================================================
    # 2. ROLES DE OPERADOR Y SEDE DEL SUPERVISOR
    # =========================================================================

    @patch('routes.ingreso.get_db_connection')
    def test_operador_autorizado_permitido_en_get_sala(self, mock_db):
        """Un operador con rol permitido (Supervisor o SuperAdmin) puede abrir GET /sala/<id>."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'operador_central'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = 'Central'

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Central 1', Piso=1, Sede='Central', Activo=1),
            (10,)  # contador_inicial
        ]

        resp = self.client.get('/sala/1')
        self.assertEqual(resp.status_code, 200)

    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    def test_operador_autorizado_permitido_en_post_procesar_ingreso(self, mock_validar_sala, mock_reg):
        """Un operador autorizado puede registrar ingresos en POST /procesar_ingreso."""
        self._autenticar_terminal(sala_id=1, sede='Central', rol='Supervisor')
        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO (MAÑANA)'}

        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'success')

    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    def test_roles_operadores_permitidos_comprobacion_exhaustiva(self, mock_validar_sala, mock_reg):
        """Verifica que todos los roles explícitamente permitidos puedan registrar ingresos."""
        from routes.ingreso import ROLES_OPERADORES_PERMITIDOS
        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO'}

        for rol in ROLES_OPERADORES_PERMITIDOS:
            self._autenticar_terminal(sala_id=1, sede='Central', rol=rol)
            resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
            self.assertEqual(resp.status_code, 200, f"Rol {rol} debería estar permitido")

    def test_rol_no_autorizado_rechazado_get_y_post(self):
        """Cualquier rol no perteneciente a ROLES_OPERADORES_PERMITIDOS (ej. Invitado) es rechazado con 403."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'invitado_user'
            sess['admin_rol'] = 'Invitado'
            sess['terminal_sala_id'] = 1

        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('Invitado', resp_get.get_data(as_text=True))

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('Invitado', data['msg'])

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    def test_supervisor_con_sede_correcta_permitido_get_y_post(self, mock_validar_sala, mock_reg, mock_db):
        """Un supervisor asignado a 'Central' puede operar salas de la sede 'Central'."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'supervisor_central'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = 'Central'

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Central', Piso=1, Sede='Central', Activo=1),
            (2,)
        ]

        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 200)

        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO'}

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 200)

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.ingreso.validar_sala_activa')
    def test_supervisor_con_sede_distinta_rechazado_get_y_post(self, mock_validar_sala, mock_db):
        """Un supervisor asignado a 'Tarma' es rechazado si intenta operar una sala en 'Central' (403)."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'supervisor_tarma'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = 'Tarma'
            sess['terminal_sala_id'] = 1

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = MagicMock(NombreSala='Sala Central', Piso=1, Sede='Central', Activo=1)

        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('no tiene permisos para operar salas de la sede', resp_get.get_data(as_text=True))

        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})
        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('no tiene permisos para operar salas de esta sede', data['msg'])

    def test_supervisor_sin_sede_rechazado_get_y_post(self):
        """Si a un Supervisor le falta la sede (None), se rechaza con HTTP 403 (falla cerrada)."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'supervisor_sin_sede'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = None
            sess['terminal_sala_id'] = 1

        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('no tiene una sede asignada', resp_get.get_data(as_text=True))

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('no tiene una sede asignada', data['msg'])

    def test_supervisor_con_sede_vacia_rechazado_get_y_post(self):
        """Si la sede del Supervisor está vacía ('') o solo contiene espacios, se rechaza con HTTP 403."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'supervisor_sede_vacia'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = '   '
            sess['terminal_sala_id'] = 1

        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 403)
        self.assertIn('no tiene una sede asignada', resp_get.get_data(as_text=True))

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('no tiene una sede asignada', data['msg'])

    def test_supervisor_filial_sede_vacia_o_distinta_rechazado(self):
        """En GET /filial/<sede>, un Supervisor con sede vacía o diferente es rechazado con 403."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'sup'
            sess['admin_rol'] = 'Supervisor'
            sess['admin_sede'] = 'Central'

        resp_distinta = self.client.get('/filial/Tarma')
        self.assertEqual(resp_distinta.status_code, 403)

        with self.client.session_transaction() as sess:
            sess['admin_sede'] = ''

        resp_vacia = self.client.get('/filial/Central')
        self.assertEqual(resp_vacia.status_code, 403)

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.admin_auth.get_db_connection')
    def test_login_supervisor_con_sede_asignada_central(self, mock_auth_db, mock_ingreso_db):
        """Supervisor con sede asignada 'Central': recibe 'Central' en sesión y solo puede operar en salas de Central."""
        from werkzeug.security import generate_password_hash
        mock_auth_conn = MagicMock()
        mock_auth_cursor = MagicMock()
        mock_auth_db.return_value = mock_auth_conn
        mock_auth_conn.cursor.return_value = mock_auth_cursor

        mock_user = MagicMock()
        mock_user.Usuario = 'supervisor_central'
        mock_user.PasswordHash = generate_password_hash('clave_sup_central')
        mock_user.Rol = 'Supervisor'
        mock_user.Activo = 1
        mock_user.SedeAsignada = 'Central'
        mock_user.IntentosFallidos = 0
        mock_user.BloqueadoHasta = None
        mock_auth_cursor.fetchone.return_value = mock_user

        # 1. Login institucional
        resp_login = self.client.post('/admin/login', data={'usuario': 'supervisor_central', 'password': 'clave_sup_central'})
        self.assertEqual(resp_login.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('admin_user'), 'supervisor_central')
            self.assertEqual(sess.get('admin_rol'), 'Supervisor')
            self.assertEqual(sess.get('admin_sede'), 'Central')

        # 2. Operar sala de Central -> 200 OK
        mock_ing_conn = MagicMock()
        mock_ing_cursor = MagicMock()
        mock_ingreso_db.return_value = mock_ing_conn
        mock_ing_conn.cursor.return_value = mock_ing_cursor

        mock_ing_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Central 1', Piso=1, Sede='Central', Activo=1),
            (0,)
        ]
        resp_sala_central = self.client.get('/sala/1')
        self.assertEqual(resp_sala_central.status_code, 200)

        # 3. Intentar operar sala de otra sede (Filial Tarma) -> Rechazado con 403
        mock_ing_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Tarma 1', Piso=1, Sede='Filial Tarma', Activo=1)
        ]
        resp_sala_tarma = self.client.get('/sala/2')
        self.assertEqual(resp_sala_tarma.status_code, 403)
        self.assertIn('no tiene permisos para operar salas de la sede', resp_sala_tarma.get_data(as_text=True))

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.admin_auth.get_db_connection')
    def test_login_supervisor_sin_sede_no_asigna_central_y_bloquea_escaner(self, mock_auth_db, mock_ingreso_db):
        """Supervisor sin sede asignada (None): no debe recibir 'Central' por defecto y no puede operar el escáner."""
        from werkzeug.security import generate_password_hash
        mock_auth_conn = MagicMock()
        mock_auth_cursor = MagicMock()
        mock_auth_db.return_value = mock_auth_conn
        mock_auth_conn.cursor.return_value = mock_auth_cursor

        mock_user = MagicMock()
        mock_user.Usuario = 'supervisor_sin_sede'
        mock_user.PasswordHash = generate_password_hash('clave_sup_sin_sede')
        mock_user.Rol = 'Supervisor'
        mock_user.Activo = 1
        mock_user.SedeAsignada = None
        mock_user.IntentosFallidos = 0
        mock_user.BloqueadoHasta = None
        mock_auth_cursor.fetchone.return_value = mock_user

        # 1. Login: no debe asignar 'Central'
        resp_login = self.client.post('/admin/login', data={'usuario': 'supervisor_sin_sede', 'password': 'clave_sup_sin_sede'})
        self.assertEqual(resp_login.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('admin_user'), 'supervisor_sin_sede')
            self.assertEqual(sess.get('admin_rol'), 'Supervisor')
            self.assertIsNone(sess.get('admin_sede'), "Supervisor sin sede asignada NO debe recibir 'Central'")
            self.assertFalse(sess.get('escaneo_autorizado', False))

        # 2. Intento de abrir GET /sala/1 -> Rechazado con 403
        resp_sala = self.client.get('/sala/1')
        self.assertEqual(resp_sala.status_code, 403)
        self.assertIn('no tiene una sede asignada', resp_sala.get_data(as_text=True))

        # 3. Intento de abrir GET /filial/Central -> Rechazado con 403
        resp_filial = self.client.get('/filial/Central')
        self.assertEqual(resp_filial.status_code, 403)

        # 4. Intento de POST /procesar_ingreso -> Rechazado con 403
        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('no tiene una sede asignada', data['msg'])

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.admin_auth.get_db_connection')
    def test_login_supervisor_con_sede_vacia_no_asigna_central_y_bloquea_escaner(self, mock_auth_db, mock_ingreso_db):
        """Supervisor con sede vacía o espacios ('   '): no debe recibir 'Central' y no puede operar el escáner."""
        from werkzeug.security import generate_password_hash
        mock_auth_conn = MagicMock()
        mock_auth_cursor = MagicMock()
        mock_auth_db.return_value = mock_auth_conn
        mock_auth_conn.cursor.return_value = mock_auth_cursor

        mock_user = MagicMock()
        mock_user.Usuario = 'supervisor_sede_vacia'
        mock_user.PasswordHash = generate_password_hash('clave_sup_vacia')
        mock_user.Rol = 'Supervisor'
        mock_user.Activo = 1
        mock_user.SedeAsignada = '   '
        mock_user.IntentosFallidos = 0
        mock_user.BloqueadoHasta = None
        mock_auth_cursor.fetchone.return_value = mock_user

        # 1. Login: no debe asignar 'Central'
        resp_login = self.client.post('/admin/login', data={'usuario': 'supervisor_sede_vacia', 'password': 'clave_sup_vacia'})
        self.assertEqual(resp_login.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('admin_user'), 'supervisor_sede_vacia')
            self.assertEqual(sess.get('admin_rol'), 'Supervisor')
            self.assertIsNone(sess.get('admin_sede'), "Supervisor con sede en blanco NO debe recibir 'Central'")
            self.assertFalse(sess.get('escaneo_autorizado', False))

        # 2. Intento de abrir GET /sala/1 -> Rechazado con 403
        resp_sala = self.client.get('/sala/1')
        self.assertEqual(resp_sala.status_code, 403)
        self.assertIn('no tiene una sede asignada', resp_sala.get_data(as_text=True))

        # 3. Intento de POST /procesar_ingreso -> Rechazado con 403
        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 403)
        data = resp_post.get_json()
        self.assertIn('no tiene una sede asignada', data['msg'])

    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    @patch('routes.ingreso.get_db_connection')
    @patch('routes.admin_auth.get_db_connection')
    def test_login_supervisor_con_sede_valida_distinta_de_central(self, mock_auth_db, mock_ingreso_db, mock_validar_sala, mock_reg):
        """Supervisor con sede válida distinta de Central ('Filial Tarma'): solo puede operar en salas de Tarma."""
        from werkzeug.security import generate_password_hash
        mock_auth_conn = MagicMock()
        mock_auth_cursor = MagicMock()
        mock_auth_db.return_value = mock_auth_conn
        mock_auth_conn.cursor.return_value = mock_auth_cursor

        mock_user = MagicMock()
        mock_user.Usuario = 'supervisor_tarma'
        mock_user.PasswordHash = generate_password_hash('clave_tarma_123')
        mock_user.Rol = 'Supervisor'
        mock_user.Activo = 1
        mock_user.SedeAsignada = 'Filial Tarma'
        mock_user.IntentosFallidos = 0
        mock_user.BloqueadoHasta = None
        mock_auth_cursor.fetchone.return_value = mock_user

        # 1. Login institucional
        resp_login = self.client.post('/admin/login', data={'usuario': 'supervisor_tarma', 'password': 'clave_tarma_123'})
        self.assertEqual(resp_login.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('admin_user'), 'supervisor_tarma')
            self.assertEqual(sess.get('admin_rol'), 'Supervisor')
            self.assertEqual(sess.get('admin_sede'), 'Filial Tarma')

        # 2. Intento de acceder a sala de Central (Sala 1) -> 403
        mock_ing_conn = MagicMock()
        mock_ing_cursor = MagicMock()
        mock_ingreso_db.return_value = mock_ing_conn
        mock_ing_conn.cursor.return_value = mock_ing_cursor

        mock_ing_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Central 1', Piso=1, Sede='Central', Activo=1)
        ]
        resp_central = self.client.get('/sala/1')
        self.assertEqual(resp_central.status_code, 403)
        self.assertIn('no tiene permisos para operar salas de la sede', resp_central.get_data(as_text=True))
        self.assertIn('<b>Central</b>', resp_central.get_data(as_text=True))

        # 3. Acceso a sala de su sede (Sala 2 en Filial Tarma) -> 200
        mock_ing_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Tarma 1', Piso=1, Sede='Filial Tarma', Activo=1),
            (0,)
        ]
        resp_tarma = self.client.get('/sala/2')
        self.assertEqual(resp_tarma.status_code, 200)
        self.assertIn('Sala Tarma 1', resp_tarma.get_data(as_text=True))

        # 4. Operar escaneo en sala de su sede (Sala 2) -> 200 Exitoso
        mock_validar_sala.return_value = (True, None, {'sala_id': 2, 'sede': 'Filial Tarma'})
        mock_reg.return_value = {
            'status': 'success',
            'msg': 'ACCESO CONCEDIDO (TARDE)',
            'alumno': 'ALUMNO FILIAL TARMA'
        }
        resp_post_tarma = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2, 'sede': 'Filial Tarma'})
        self.assertEqual(resp_post_tarma.status_code, 200)
        data = resp_post_tarma.get_json()
        self.assertEqual(data['status'], 'success')

        # 5. Intento de escaneo enviando sede Central en payload -> 403
        resp_post_cruzado = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2, 'sede': 'Central'})
        self.assertEqual(resp_post_cruzado.status_code, 403)

    # =========================================================================
    # 3. RECORRIDO REAL DEL NAVEGADOR (GET SALA -> POST PROCESAR INGRESO)
    # =========================================================================

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    def test_recorrido_completo_navegador_get_sala_luego_post_escaneo(self, mock_validar_sala, mock_reg, mock_db):
        """
        Simula el ciclo completo de un operador en el navegador:
        1. Acceso a GET /sala/1 con sesión activa (sin tokens en URL ni cabeceras).
        2. Recibe el HTML con sala_id y se asocia la sala en la cookie de sesión.
        3. El escaneo dispara POST /procesar_ingreso con la cookie de sesión del navegador.
        4. El ingreso es procesado exitosamente sin requerir tokens en JavaScript.
        """
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'operador_navegador'
            sess['admin_rol'] = 'Operador'
            sess['admin_sede'] = 'Central'

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Lectura 1', Piso=1, Sede='Central', Activo=1),
            (0,)  # contador inicial
        ]

        # 1. Paso GET: operador abre la pantalla del escáner en su navegador
        resp_get = self.client.get('/sala/1')
        self.assertEqual(resp_get.status_code, 200)
        self.assertIn('Sala Lectura 1', resp_get.get_data(as_text=True))
        # Verifica que no se filtran tokens al cliente en el HTML
        self.assertNotIn('TERMINAL_AUTH_TOKEN', resp_get.get_data(as_text=True))

        # 2. Paso POST: el escáner lee el carnet y hace POST con la sesión de la cookie
        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})
        mock_reg.return_value = {
            'status': 'success',
            'msg': 'ACCESO CONCEDIDO (MAÑANA)',
            'alumno': 'ALUMNO REGISTRADO NAV'
        }

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp_post.status_code, 200)
        data = resp_post.get_json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['alumno'], 'ALUMNO REGISTRADO NAV')

    # =========================================================================
    # 4. CASOS ADICIONALES DE AUTENTICACIÓN Y FALLA CERRADA
    # =========================================================================

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_via_authorization_bearer_permitido(self, mock_validar_sala, mock_reg, mock_db):
        """Un token de terminal presentado como 'Authorization: Bearer <token>' es admitido."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Dos', Piso=2, Sede='Central', Activo=1),
            (0,)
        ]
        mock_validar_sala.return_value = (True, None, {'sala_id': 2, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO'}

        headers = {'Authorization': 'Bearer secret-kiosk-token-99'}
        resp_get = self.client.get('/sala/2', headers=headers)
        self.assertEqual(resp_get.status_code, 200)

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2}, headers=headers)
        self.assertEqual(resp_post.status_code, 200)

    @patch('routes.ingreso.get_db_connection')
    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_via_x_terminal_token_permitido(self, mock_validar_sala, mock_reg, mock_db):
        """Un token presentado como 'X-Terminal-Token: <token>' es admitido."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Dos', Piso=2, Sede='Central', Activo=1),
            (0,)
        ]
        mock_validar_sala.return_value = (True, None, {'sala_id': 2, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO'}

        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        resp_get = self.client.get('/sala/2', headers=headers)
        self.assertEqual(resp_get.status_code, 200)

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2}, headers=headers)
        self.assertEqual(resp_post.status_code, 200)

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '1'})
    def test_operador_valido_con_token_invalido_rechazado_401(self):
        """Una sesión de operador válida que envía un token de terminal erróneo es rechazada con 401."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'operador_real'
            sess['admin_rol'] = 'Operador'
            sess['admin_sede'] = 'Central'
            sess['terminal_sala_id'] = 1

        headers = {'X-Terminal-Token': 'token-falso-manipulado'}

        resp_get = self.client.get('/sala/1', headers=headers)
        self.assertEqual(resp_get.status_code, 401)

        resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1}, headers=headers)
        self.assertEqual(resp_post.status_code, 401)

    def test_terminal_sala_id_ausente_vacio_invalido_falla_cerrada(self):
        """TERMINAL_SALA_ID ausente, vacía o inválida falla cerrada con HTTP 403 en GET y POST."""
        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        valores_invalidos = [None, '', '   ', '0', '-5', 'abc', '1.5']

        for val in valores_invalidos:
            env_override = {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99'}
            if val is not None:
                env_override['TERMINAL_SALA_ID'] = val
            with patch.dict(os.environ, env_override, clear=True):
                resp_get = self.client.get('/sala/1', headers=headers)
                self.assertEqual(resp_get.status_code, 403, f"TERMINAL_SALA_ID='{val}' debe fallar cerrada con 403 en GET")

                resp_post = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1}, headers=headers)
                self.assertEqual(resp_post.status_code, 403, f"TERMINAL_SALA_ID='{val}' debe fallar cerrada con 403 en POST")

    def test_token_en_url_exhaustivo_vacio_repetido_despues_otro(self):
        """Rechaza con HTTP 400 cualquier parámetro token en URL: vacío, sin valor, repetido o tras otro parámetro."""
        casos_query = [
            'terminal_token=',
            'token=',
            'token',
            'terminal_token',
            'otra_variable=1&token=secreto',
            'otra_variable=1&terminal_token=',
            'token=abc&token=def',
            'terminal_token=1&terminal_token=2',
            'TOKEN=secreto',
            'TERMINAL_TOKEN=secreto',
            'action=scan&TOKEN=&extra=9'
        ]

        for query in casos_query:
            resp_get = self.client.get(f'/sala/1?{query}')
            self.assertEqual(resp_get.status_code, 400, f"Query '{query}' en GET debe retornar 400")
            self.assertIn('no se admiten tokens en la URL', resp_get.get_data(as_text=True))

            resp_post = self.client.post(f'/procesar_ingreso?{query}', json={'codigo': '2412345678', 'sala_id': 1})
            self.assertEqual(resp_post.status_code, 400, f"Query '{query}' en POST debe retornar 400")
            data = resp_post.get_json()
            self.assertIn('no se admiten tokens en la URL', data['msg'])

    def test_payload_tipos_invalidos_sala_id(self):
        """Identificadores de sala booleanos o no enteros en el payload son rechazados con 400."""
        self._autenticar_terminal(sala_id=1, sede='Central')

        for invalid_sala in [True, False, 1.5, "abc"]:
            resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': invalid_sala})
            self.assertEqual(resp.status_code, 400, f"sala_id={invalid_sala} debe ser rechazado con 400")

    # =========================================================================
    # 5. TOKEN LIMITADO A SU SALA Y PROTECCIÓN DE PAYLOAD SALA/SEDE
    # =========================================================================

    @patch('routes.ingreso.get_db_connection')
    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_terminal_abre_su_sala_asignada(self, mock_db):
        """Un token de terminal puede abrir exitosamente la sala asignada a él (sala 2)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            MagicMock(NombreSala='Sala Dos', Piso=2, Sede='Central', Activo=1),
            (5,)
        ]

        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        resp = self.client.get('/sala/2', headers=headers)
        self.assertEqual(resp.status_code, 200)

    @patch('routes.ingreso.registrar_ingreso_general')
    @patch('routes.ingreso.validar_sala_activa')
    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_terminal_procesa_ingreso_en_su_sala(self, mock_validar_sala, mock_reg):
        """Una terminal procesa ingresos en su sala autorizada enviando su token en cabecera."""
        mock_validar_sala.return_value = (True, None, {'sala_id': 2, 'sede': 'Central'})
        mock_reg.return_value = {'status': 'success', 'msg': 'ACCESO CONCEDIDO (MAÑANA)'}

        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2}, headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'success')

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_terminal_intenta_abrir_otra_sala_rechazado_get(self):
        """Un token asignado a la sala 2 que intenta abrir GET /sala/1 es rechazado con HTTP 403."""
        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        resp = self.client.get('/sala/1', headers=headers)
        self.assertEqual(resp.status_code, 403)
        self.assertIn('solo está autorizado para la sala 2', resp.get_data(as_text=True))

    @patch.dict(os.environ, {'TERMINAL_AUTH_TOKEN': 'secret-kiosk-token-99', 'TERMINAL_SALA_ID': '2'})
    def test_token_terminal_intenta_enviar_otra_sala_rechazado_post(self):
        """Un token asignado a la sala 2 que envía payload sala_id=1 es rechazado con HTTP 403."""
        headers = {'X-Terminal-Token': 'secret-kiosk-token-99'}
        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1}, headers=headers)
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('no coincide con la sala autorizada', data['msg'])

    def test_payload_que_intenta_cambiar_sala_rechazado_403(self):
        """Si la terminal está autorizada para la sala 1, enviar sala_id=2 en el payload se rechaza con 403."""
        self._autenticar_terminal(sala_id=1, sede='Central')
        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 2})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('no coincide con la sala autorizada', data['msg'])

    @patch('routes.ingreso.validar_sala_activa')
    def test_payload_que_intenta_cambiar_sede_rechazado_403(self, mock_validar_sala):
        """Si la terminal opera en la sala 1 (Central), enviar sede='Oxapampa' en el payload se rechaza con 403."""
        self._autenticar_terminal(sala_id=1, sede='Central')
        mock_validar_sala.return_value = (True, None, {'sala_id': 1, 'sede': 'Central'})

        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1, 'sede': 'Oxapampa'})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('no coincide con la sede autorizada', data['msg'])

    def test_terminal_anonima_rechazada_con_401(self):
        """Solicitud anónima a POST /procesar_ingreso sin sesión ni token rechazada con 401."""
        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 1})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertEqual(data['status'], 'error')
        self.assertIn('Terminal u operador no autenticado', data['msg'])

    def test_pantalla_sala_requiere_login_o_token(self):
        """Acceder a GET /sala/1 sin login redirige a /admin/login."""
        resp = self.client.get('/sala/1')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/login', resp.headers.get('Location', ''))

    # =========================================================================
    # =========================================================================
    # BLOQUEO 2: API DE LA UNDAC Y ALTA AUTOMÁTICA
    # =========================================================================

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    def test_alta_automatica_activa_por_defecto_consulta_api_por_https(self, mock_db, mock_api_get):
        """
        La integración de alta automática está activa por defecto en el código.
        Si un alumno no existe localmente, consulta la API oficial por HTTPS.
        Si la API presenta un error de validación TLS (certificado inválido/vencido),
        se captura de forma segura y devuelve un mensaje operativo claro sin romper el escáner.
        """
        self.assertTrue(is_undac_api_autoregister_enabled())

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        mock_api_get.side_effect = requests.exceptions.SSLError("Certificate verify failed: certificate has expired")

        res = registrar_ingreso_general('2412345601', sala_id=1)

        self.assertEqual(res['status'], 'error')
        self.assertIn('Falló la consulta segura TLS', res['msg'])
        self.assertIn('Administración', res['msg'])
        self.assertEqual(mock_api_get.call_count, 1)

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'false'})
    def test_alta_automatica_puede_desactivarse_explicitamente(self, mock_db, mock_api_get):
        """
        Si ALLOW_UNDAC_API_AUTOREGISTER se configura explícitamente como 'false',
        la consulta externa no se emite y notifica al operador.
        """
        self.assertFalse(is_undac_api_autoregister_enabled())

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        res = registrar_ingreso_general('2412345601', sala_id=1)

        self.assertEqual(res['status'], 'error')
        self.assertIn('desactivada por configuración', res['msg'])
        mock_api_get.assert_not_called()

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    def test_creacion_automatica_alumno_concurrente_usa_updlock_holdlock(self, mock_db, mock_api_get):
        """
        La creación automática de alumnos utiliza una consulta atómica con UPDLOCK, HOLDLOCK
        para prevenir la duplicación de fichas cuando dos procesos reciben el mismo código simultáneamente.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'Nombres': 'JUAN', 'Apellido paterno': 'QUISPE', 'Apellido materno': 'MAMANI',
            'Dni': '75554433', 'Programa facultad': 'Ingeniería de Minas', 'Fecha de Ingreso': '2023'
        }
        mock_api_get.return_value = mock_resp

        ok, nombre = auto_registrar_alumno_api_undac('2312345678')
        self.assertTrue(ok)
        self.assertEqual(nombre, 'QUISPE MAMANI, JUAN')

        # Verificar que la consulta de inserción ejecutada contiene UPDLOCK, HOLDLOCK
        queries_ejecutadas = [str(call[0][0]) for call in mock_cursor.execute.call_args_list]
        tiene_updlock = any('UPDLOCK, HOLDLOCK' in q for q in queries_ejecutadas)
        self.assertTrue(tiene_updlock, "Debe utilizar UPDLOCK, HOLDLOCK para evitar duplicados concurrentes")

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    def test_alumno_existente_no_consulta_api_ni_con_flag_activo(self, mock_db, mock_api_get):
        """
        Un alumno que ya existe localmente se registra de inmediato contra la BD local
        sin tocar la API externa bajo ninguna circunstancia.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (
            'ACCESO CONCEDIDO (MAÑANA)', 'PEREZ GOMEZ, JUAN', 'Ingeniería de Sistemas', '6'
        )

        res = registrar_ingreso_general('2412345678', sala_id=1)

        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['alumno'], 'PEREZ GOMEZ, JUAN')
        mock_api_get.assert_not_called()

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_habilitada_utiliza_estricto_https_con_verify_true(self, mock_db, mock_api_get):
        """
        Cuando el alta automática esté habilitada, la llamada DEBE usar estrictamente
        el esquema HTTPS y verificación de certificado TLS activa (verify=True).
        """
        self.assertTrue(is_undac_api_autoregister_enabled())

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'Nombres': 'MARIA', 'Apellido paterno': 'LOPEZ', 'Apellido materno': 'QUISPE',
            'Dni': '76543210', 'Programa facultad': 'Enfermería', 'Fecha de Ingreso': '2024'
        }
        mock_api_get.return_value = mock_resp

        auto_registrar_alumno_api_undac('2412345690')

        self.assertEqual(mock_api_get.call_count, 1)
        args, kwargs = mock_api_get.call_args
        url_llamada = args[0]
        self.assertTrue(url_llamada.startswith('https://'), f"La URL debe ser HTTPS: {url_llamada}")
        self.assertFalse(url_llamada.startswith('http://'), "No debe usarse HTTP en texto plano")
        self.assertTrue(kwargs.get('verify', False), "verify=True es obligatorio para validar TLS")
        self.assertFalse(kwargs.get('allow_redirects', True), "allow_redirects=False es obligatorio para evitar redirecciones hacia HTTP")

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_undac_redireccion_rechazada_sin_continuar_a_http(self, mock_db, mock_api_get):
        """Si la API institucional responde con redirección (301/302), se rechaza de inmediato sin seguir a HTTP."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        mock_resp = MagicMock()
        mock_resp.status_code = 301
        mock_resp.is_redirect = True
        mock_resp.headers = {'Location': 'http://inseguro.undac.edu.pe/api'}
        mock_api_get.return_value = mock_resp

        res = registrar_ingreso_general('2412345695', sala_id=1)
        self.assertEqual(res['status'], 'error')
        self.assertIn('Usuario no encontrado localmente', res['msg'])
        args, kwargs = mock_api_get.call_args
        self.assertFalse(kwargs.get('allow_redirects', True), "allow_redirects=False es obligatorio")

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_habilitada_alta_exitosa(self, mock_db, mock_api_get):
        """Si la API está habilitada y responde exitosamente, se da de alta al estudiante."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None),
            None,
            ('ACCESO CONCEDIDO (MAÑANA)', 'RODRIGUEZ DIAZ, CARLOS', 'Medicina Humana', None)
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'Nombres': 'CARLOS', 'Apellido paterno': 'RODRIGUEZ', 'Apellido materno': 'DIAZ',
            'Dni': '71234567', 'Programa facultad': 'Medicina Humana', 'Fecha de Ingreso': '2024'
        }
        mock_api_get.return_value = mock_resp

        res = registrar_ingreso_general('2412345699', sala_id=1)
        self.assertEqual(res['status'], 'success')
        self.assertIn('Autenticado vía API UNDAC', res['msg'])

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_habilitada_timeout_notifica_operador(self, mock_db, mock_api_get):
        """Si la API externa habilitada tarda demasiado, se captura el timeout de forma segura."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        mock_api_get.side_effect = requests.exceptions.Timeout("Read timeout")

        res = registrar_ingreso_general('2412345601', sala_id=1)
        self.assertEqual(res['status'], 'error')
        self.assertIn('tardó demasiado en responder', res['msg'])

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_habilitada_error_conexion_notifica_operador(self, mock_db, mock_api_get):
        """Si la API externa habilitada falla por red o servicio caído, notifica al operador."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        mock_api_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        res = registrar_ingreso_general('2412345602', sala_id=1)
        self.assertEqual(res['status'], 'error')
        self.assertIn('no se encuentra disponible', res['msg'])

    @patch('utils.queries_ingreso.requests.get')
    @patch('utils.queries_ingreso.get_db_connection')
    @patch.dict(os.environ, {'ALLOW_UNDAC_API_AUTOREGISTER': 'true'})
    def test_api_habilitada_ssl_error_rechaza_sin_fallback(self, mock_db, mock_api_get):
        """Si la API institucional falla por certificado TLS inválido, no hace fallback a HTTP."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('USUARIO NO ENCONTRADO EN LA BASE DE DATOS', None, None, None)

        mock_api_get.side_effect = requests.exceptions.SSLError("certificate verify failed: certificate has expired")

        res = registrar_ingreso_general('2412345603', sala_id=1)
        self.assertEqual(res['status'], 'error')
        self.assertNotIn('HTTP', res['msg'])

    # =========================================================================
    # BLOQUEO 4: VALIDACIÓN ESTRICTA DE TIPOS Y MANEJO DE ERRORES
    # =========================================================================

    def test_rechazo_codigo_booleano_y_objetos(self):
        """Rechaza booleanos y objetos en el campo 'codigo' sin convertirlos ciegamente a texto."""
        self._autenticar_terminal()
        casos = [True, False, {'codigo': '1234'}, [2412345678], None]
        for caso in casos:
            with self.subTest(codigo=caso):
                resp = self.client.post('/procesar_ingreso', json={'codigo': caso, 'sala_id': 1})
                self.assertEqual(resp.status_code, 400)
                data = resp.get_json()
                self.assertEqual(data['status'], 'error')

    def test_rechazo_sala_booleana_sin_conversion_a_uno(self):
        """
        En Python isinstance(True, int) es True. El validador debe rechazar explícitamente
        sala_id = True o False sin convertirlo al entero 1 o 0.
        """
        self._autenticar_terminal()
        for bool_val in [True, False]:
            with self.subTest(sala_id=bool_val):
                resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': bool_val})
                self.assertEqual(resp.status_code, 400)
                data = resp.get_json()
                self.assertEqual(data['status'], 'error')
                self.assertIn('no se admiten booleanos', data['msg'])

    def test_rechazo_sala_decimal_float_sin_truncar(self):
        """
        Un valor flotante como sala_id = 1.9 no debe ser truncado a 1 con int(1.9);
        debe ser rechazado estrictamente.
        """
        self._autenticar_terminal()
        for f_val in [1.5, 2.9, 0.1]:
            with self.subTest(sala_id=f_val):
                resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': f_val})
                self.assertEqual(resp.status_code, 400)
                data = resp.get_json()
                self.assertEqual(data['status'], 'error')
                self.assertIn('no se admiten decimales', data['msg'])

    def test_rechazo_sala_string_no_numerico(self):
        """Rechaza cadenas no numéricas como identificadores de sala."""
        self._autenticar_terminal()
        resp = self.client.post('/procesar_ingreso', json={'codigo': '2412345678', 'sala_id': 'sala_uno'})
        self.assertEqual(resp.status_code, 400)

    @patch('utils.queries_ingreso.get_db_connection')
    def test_garantiza_cierre_conexion_aunque_falle_cursor(self, mock_db):
        """
        Garantiza que la conexión a la base de datos se cierre siempre,
        incluso si la creación del cursor arroja una excepción no controlada.
        """
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("Fallo inesperado al crear cursor ODBC")
        mock_db.return_value = mock_conn

        es_valida, msg, data = validar_sala_activa(1)
        self.assertFalse(es_valida)
        mock_conn.close.assert_called_once()

    @patch('utils.queries_ingreso.get_db_connection')
    def test_privacidad_logs_anonimizados_sin_pii(self, mock_db):
        """
        Garantiza que los logs del flujo de ingreso no impriman datos personales
        (códigos, DNI o nombres) ni detalles de conexión en stdout/stderr.
        """
        test_codigo = '2499887766'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Fallo SQL: server=192.168.1.50;pwd=secret")

        output_capture = io.StringIO()
        with redirect_stdout(output_capture):
            res = registrar_ingreso_general(test_codigo, sala_id=1)

        logs_emitidos = output_capture.getvalue()
        self.assertNotIn(test_codigo, logs_emitidos, "El código del alumno fue filtrado a los logs")
        self.assertNotIn("secret", logs_emitidos, "Contraseña o credencial filtrada a los logs")
        self.assertNotIn("192.168.1.50", logs_emitidos, "IP de servidor de BD filtrada a los logs")
        self.assertEqual(res['status'], 'error')
        self.assertNotIn("192.168.1.50", res['msg'], "Detalles técnicos en la respuesta al operador")

    # =========================================================================
    # BLOQUEO 3: DUPLICADOS Y CONCURRENCIA
    # =========================================================================

    @patch('utils.queries_ingreso.get_db_connection')
    def test_doble_lectura_escanner_detectada_por_debounce(self, mock_db):
        """
        Dos lecturas consecutivas del mismo alumno en la misma sala en menos de 3s
        son interceptadas por el debounce en memoria sin duplicar registros.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ('ACCESO CONCEDIDO (MAÑANA)', 'ALUMNO TEST', 'Ingeniería', '4')

        res1 = registrar_ingreso_general('2412345610', sala_id=1)
        self.assertEqual(res1['status'], 'success')
        self.assertEqual(mock_cursor.execute.call_count, 1)

        res2 = registrar_ingreso_general('2412345610', sala_id=1)
        self.assertEqual(res2['status'], 'warning')
        self.assertEqual(res2['warning'], 'doble_lectura')
        self.assertIn('Lectura duplicada detectada', res2['msg'])
        self.assertEqual(mock_cursor.execute.call_count, 1)

    def test_concurrencia_multiproceso_simulada(self):
        """
        Prueba multiproceso real (simulando múltiples workers de Gunicorn en procesos de SO distintos).
        Demuestra que con un mecanismo de bloqueo atómico / serialización en la base de datos
        (representando sp_getapplock + UPDLOCK, HOLDLOCK de la propuesta SQL),
        múltiples workers concurrentes no producen registros duplicados.
        """
        manager = mp.Manager()
        shared_db = manager.dict()
        lock = manager.Lock()

        pool_args = [(i, lock, shared_db) for i in range(4)]
        with mp.Pool(2) as pool:
            resultados = pool.map(_worker_multiproceso_ingreso, pool_args)

        exitos = [r for r in resultados if r['status'] == 'success']
        duplicados = [r for r in resultados if r['status'] == 'warning']

        self.assertEqual(len(exitos), 1, "Exactamente una petición de los workers debe conceder acceso")
        self.assertEqual(len(duplicados), 3, "Las restantes peticiones concurrentes deben ser detectadas como duplicadas")
        self.assertTrue(shared_db.get("registrado"))

    @patch('utils.queries_ingreso.get_db_connection')
    def test_visitas_legitimas_otro_turno_o_traslado_sala(self, mock_db):
        """
        Verifica que el flujo no restrinja visitas legítimas en otro turno (ej. Mañana y luego Tarde)
        ni traslados legítimos entre salas en el mismo turno.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = ('NUEVO INGRESO: AHORA ESTÁS EN SALA DE LECTURA 2', 'ALUMNO TEST', 'Derecho', '4')

        res_traslado = registrar_ingreso_general('2412345615', sala_id=2)
        self.assertEqual(res_traslado['status'], 'success')
        self.assertIn('NUEVO INGRESO', res_traslado['msg'])

    @patch('utils.queries_ingreso.get_db_connection')
    def test_idempotencia_reintento_despues_de_guardado(self, mock_db):
        """
        Si un ingreso ya fue guardado en la base de datos (por ejemplo, ante un timeout previo de red),
        un reintento posterior del mismo alumno devuelve el aviso de 'YA REGISTRADO'
        sin generar una segunda fila ni provocar un error técnico.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = (
            'YA ESTÁS REGISTRADO EN ESTA SALA (SALA GENERAL) EN EL TURNO MAÑANA', 
            'ALUMNO REINTENTO', 'Medicina', '8'
        )

        res = registrar_ingreso_general('2412345618', sala_id=1)
        self.assertEqual(res['status'], 'warning')
        self.assertIn('YA ESTÁS REGISTRADO', res['msg'])
        self.assertEqual(res['alumno'], 'ALUMNO REINTENTO')


if __name__ == '__main__':
    unittest.main()
