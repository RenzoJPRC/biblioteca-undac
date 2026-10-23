"""Riesgos del arranque de memoria reducida, con servicios simulados."""
import json
from pathlib import Path
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class TestMemoriaRender(unittest.TestCase):
    def test_comando_conserva_cuatro_slots_en_un_proceso(self):
        docker = (Path(__file__).resolve().parents[1] / 'Dockerfile').read_text(encoding='utf-8')
        command = json.loads(next(line[4:] for line in docker.splitlines() if line.startswith('CMD ')))
        self.assertEqual(command[0], 'gunicorn')
        self.assertEqual(command[-1], 'app:app')
        self.assertEqual(command[command.index('--workers') + 1], '1')
        self.assertEqual(command[command.index('--worker-class') + 1], 'gthread')
        self.assertEqual(command[command.index('--threads') + 1], '4')

    def test_cuatro_terminales_concurrentes_y_rutas_con_servicios_simulados(self):
        from app import app
        barrier = threading.Barrier(4)

        def register(*args, **kwargs):
            barrier.wait(timeout=10)
            return {'status': 'success', 'msg': 'Registro simulado'}

        def request(index):
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session.update(admin_user='operador_ficticio', admin_rol='Supervisor', admin_sede='Central',
                                   terminal_sala_id=1, terminal_sede='Central', terminal_operador='operador_ficticio',
                                   terminal_rol='Supervisor', escaneo_autorizado=True, escaneo_sala_id=1,
                                   escaneo_sede='Central')
                response = client.post('/procesar_ingreso', json={'codigo': '990000000' + str(index), 'sala_id': 1})
                return response.status_code, response.get_json()['status']

        with patch('routes.ingreso.validar_sala_activa', return_value=(True, None, {'sala_id': 1, 'sede': 'Central'})), \
             patch('routes.ingreso.registrar_ingreso_general', side_effect=register), \
             patch('app.get_db_connection', return_value=None):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(request, range(4)))
        self.assertEqual(results, [(200, 'success')] * 4)

        with app.test_client() as client, patch('app.get_db_connection', return_value=None), \
             patch('routes.admin_tasks.get_task_status', return_value={'status': 'processing', 'progress': 50}):
            self.assertEqual(client.get('/admin/ml').status_code, 302)
            with client.session_transaction() as session:
                session.update(admin_user='admin_ficticio', admin_rol='SuperAdmin', admin_sede='Central')
            response = client.get('/admin/upload_status/tarea-ficticia')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['progress'], 50)
