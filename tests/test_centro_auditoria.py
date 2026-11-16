"""
Pruebas automatizadas para el Centro de Auditoría de Base de Datos y Seguridad.
Sistema Bibliotecario UNDAC.

Cobertura de pruebas:
1. Control de acceso RBAC estricto (acceso exclusivo para SuperAdmin, rechazo a otros roles).
2. Renderizado de las 9 pestañas del módulo de auditoría.
3. Endpoints de exportación CSV (UTF-8 con BOM) e informe consolidado Markdown.
4. Lógica de cálculo criptográfico SHA-256 para cadena de custodia de evidencias.
5. Clasificación semántica de eventos y sanitización (ocultamiento de credenciales/tokens).
6. Matriz de riesgos (cálculo de severidad P x I) y conversión a borrador de hallazgo.
7. Principio de confirmación humana obligatoria para hallazgos (nunca confirmación automática).
8. Cobertura de trazabilidad DML y presencia de la advertencia metodológica de laboratorio.
9. Manejo de restricciones de proveedor de hosting ("No evaluable por restricción del proveedor").
"""

from __future__ import annotations

import hashlib
import unittest
from unittest.mock import MagicMock, patch

from app import app
from utils.queries_auditoria import (
    calcular_nivel_riesgo,
    calcular_sha256_archivo,
    calcular_sha256_bytes,
    calcular_sha256_texto,
    clasificar_accion_evento,
    convertir_riesgo_a_borrador_hallazgo,
    guardar_hallazgo_auditoria,
    guardar_riesgo_auditoria,
    obtener_completitud_conciliacion,
    obtener_matriz_criterios_auditoria,
    obtener_permisos_sql,
    obtener_principales_sql,
    obtener_roles_sql,
    obtener_trazabilidad_dml,
    parsear_detalle,
    traducir_accion,
)


class TestCentroAuditoriaControlAcceso(unittest.TestCase):
    """Pruebas de autenticación y autorización RBAC para el Centro de Auditoría."""

    def setUp(self):
        app.testing = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def test_01_acceso_denegado_sin_sesion(self):
        """Un usuario anónimo debe ser redirigido a login y no tener acceso a /admin/auditoria."""
        resp = self.client.get('/admin/auditoria/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin/login', resp.headers.get('Location', ''))

    def test_02_acceso_denegado_para_rol_no_superadmin(self):
        """Un usuario logueado con rol 'Bibliotecario' o 'Admin' debe ser redirigido."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'operador1'
            sess['admin_rol'] = 'Bibliotecario'

        resp = self.client.get('/admin/auditoria/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/admin', resp.headers.get('Location', ''))

    def test_03_acceso_denegado_post_sin_superadmin(self):
        """Operaciones POST deben ser bloqueadas si el rol no es SuperAdmin."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'admin_regular'
            sess['admin_rol'] = 'Admin'

        resp = self.client.post('/admin/auditoria/evidencias/guardar', data={
            'codigo': 'EVD-TEST',
            'descripcion': 'Test no autorizado'
        })
        self.assertIn(resp.status_code, [302, 403])


class BaseAuditoriaMockedTestCase(unittest.TestCase):
    """Clase base con todos los mocks de consultas para pruebas de interfaz y rutas."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()

        # Configuración común de parches para evitar llamadas a la nube durante tests unitarios
        self.patchers = {
            'kpis': patch('routes.admin_auditoria.obtener_kpis_centro_auditoria'),
            'dash': patch('routes.admin_auditoria.obtener_auditoria_dashboard'),
            'princ': patch('routes.admin_auditoria.obtener_principales_sql'),
            'roles': patch('routes.admin_auditoria.obtener_roles_sql'),
            'perm': patch('routes.admin_auditoria.obtener_permisos_sql'),
            'dml': patch('routes.admin_auditoria.obtener_trazabilidad_dml'),
            'ddl': patch('routes.admin_auditoria.obtener_trazabilidad_ddl'),
            'evid': patch('routes.admin_auditoria.obtener_evidencias_auditoria'),
            'riesgos': patch('routes.admin_auditoria.obtener_riesgos_auditoria'),
            'hallazgos': patch('routes.admin_auditoria.obtener_hallazgos_auditoria'),
            'backups': patch('routes.admin_auditoria.obtener_historial_backups_y_restauraciones'),
            'matriz': patch('routes.admin_auditoria.obtener_matriz_criterios_auditoria'),
            'comp': patch('routes.admin_auditoria.obtener_completitud_conciliacion'),
        }

        self.mocks = {name: p.start() for name, p in self.patchers.items()}

        # Valores estándar simulados
        self.mocks['kpis'].return_value = {
            'total_eventos': 100,
            'principales_visibles': 5,
            'principales_sql': 5,
            'roles_detectados': 2,
            'permisos_detectados': 228,
            'cobertura_dml_pct': 100.0,
            'dml_cobertura': '100% (Caso Lab)',
            'ddl_eventos': '3 eventos (Lab)',
            'evidencias_registradas': 6,
            'evidencias_validas': 5,
            'evidencias_pendientes': 1,
            'riesgos_registrados': 4,
            'riesgos_preliminares': 4,
            'hallazgos_abiertos': 1,
            'hallazgos_confirmados': 1,
            'ultimo_backup': '14/11/2026 23:00:00',
            'backups_verificados': 1,
            'controles_cis': 10,
        }
        self.mocks['dash'].return_value = (
            [{
                'log_id': 1,
                'usuario': 'admin',
                'accion_raw': 'MODIFICAR_ROL',
                'accion_legible': 'Modificar Rol',
                'categoria': 'Control de Accesos',
                'nivel': 'Advertencia',
                'detalle': 'Cambio a SuperAdmin',
                'ip': '127.0.0.1',
                'fecha_fmt': '15/11/2026 01:00:00',
                'fecha_raw': None
            }],
            100,
            [('admin', 15), ('operador', 8)],
            {'page': 1, 'per_page': 50, 'total_logs': 100, 'total_paginas': 2}
        )
        self.mocks['princ'].return_value = {
            'status': 'ok',
            'datos': [{
                'name': 'dbo',
                'type_desc': 'SQL_USER',
                'authentication_type_desc': 'INSTANCE',
                'create_date': '01/01/2026 00:00:00',
                'modify_date': '01/01/2026 00:00:00'
            }]
        }
        self.mocks['roles'].return_value = {
            'status': 'ok',
            'datos': [{'usuario': 'portal_user', 'rol': 'db_owner'}]
        }
        self.mocks['perm'].return_value = {
            'status': 'ok',
            'datos': [{'usuario': 'dbo', 'permiso': 'CONTROL', 'estado': 'GRANT', 'tipo_objeto': 'DATABASE'}]
        }
        self.mocks['dml'].return_value = {
            'status': 'success',
            'entorno': 'Laboratorio (Prueba Controlada)',
            'esperados': 4,
            'observados': 4,
            'cobertura': 100.0,
            'disclaimer': 'Este porcentaje corresponde únicamente al caso controlado ejecutado y no demuestra cobertura total de todas las operaciones del sistema.',
            'datos': [{
                'id': 1,
                'fecha_hora': '15/11/2026 02:00:00',
                'operacion': 'INSERT',
                'tabla': 'dbo.RegistroIngresos',
                'identificador': '101',
                'usuario_tecnico': 'sa',
                'valores_antes': 'N/A (Alta)',
                'valores_despues': 'Ingreso registrado',
                'es_laboratorio': True
            }]
        }
        self.mocks['ddl'].return_value = {
            'status': 'success',
            'datos': [{
                'id': 1,
                'fecha_hora': '15/11/2026 02:05:00',
                'evento': 'CREATE_TABLE',
                'esquema': 'dbo',
                'objeto': 'AuditDmlLog',
                'tipo_objeto': 'TABLE',
                'usuario_sql': 'sa',
                'comando': 'CREATE TABLE AuditDmlLog...',
                'es_laboratorio': True
            }]
        }
        self.mocks['evid'].return_value = [{
            'codigo': 'EVD-01',
            'tipo': 'Configuración SQL',
            'descripcion': 'Vistas catálogo',
            'sha256_hash': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'tamano': '2.4 KB',
            'custodio': 'Auditor Líder',
            'fecha_fmt': '15/11/2026 02:00:00'
        }]
        self.mocks['riesgos'].return_value = [
            {
                'codigo': 'RSK-01',
                'riesgo': 'Dependencia de respaldo manual sin calendarización automatizada institucional',
                'probabilidad': 4,
                'impacto': 4,
                'nivel': 'Alto',
                'razon': 'Los respaldos completos .bak dependen de ejecución manual por el operador aproximadamente una vez por semana.',
                'control_existente': 'Exportación manual de backups ZIP desde el panel web y dumps esporádicos del proveedor.',
                'evidencia_disponible': 'PT-06_restauracion_verificada.log, logs de AdminAuditLog (BACKUP/GENERAR)',
                'evidencia_pendiente': 'Procedimiento documentado de backups automáticos y políticas RPO/RTO',
                'estado': 'Identificado',
                'fecha_creado': '15/11/2026'
            },
            {
                'codigo': 'RSK-02',
                'riesgo': 'Principal con membresía db_owner requiere identificación de propietario y justificación',
                'probabilidad': 3,
                'impacto': 4,
                'nivel': 'Alto',
                'razon': 'Se identificó el principal portal_4997910989b62d8cf6d5 con rol db_owner en producción.',
                'control_existente': 'Controles de capa de aplicación Flask y validación de sesiones RBAC en servidor web.',
                'evidencia_disponible': 'PT-02_roles_db.csv (sys.database_role_members)',
                'evidencia_pendiente': 'Plan de segregación de cuenta con rol restrictivo de menor privilegio',
                'estado': 'Requiere evidencia adicional',
                'fecha_creado': '15/11/2026'
            },
            {
                'codigo': 'RSK-03',
                'riesgo': 'Ausencia de auditoría de consultas SELECT a nivel del motor de base de datos',
                'probabilidad': 3,
                'impacto': 3,
                'nivel': 'Medio',
                'razon': 'En hosting compartido SmarterASP.NET no está disponible SQL Server Audit ni lectura de servidor.',
                'control_existente': 'Registro en AdminAuditLog para transacciones POST/PUT/DELETE.',
                'evidencia_disponible': 'AdminAuditLog (eventos administrativos de aplicación)',
                'evidencia_pendiente': 'Evaluación de logs de aplicación para detección de anomalías de lectura',
                'estado': 'Identificado',
                'fecha_creado': '15/11/2026'
            },
            {
                'codigo': 'RSK-04',
                'riesgo': 'Falta de captura continua DDL en entorno de producción compartido',
                'probabilidad': 2,
                'impacto': 4,
                'nivel': 'Medio',
                'razon': 'Restricción de hosting impide crear triggers DDL a nivel de base de datos en producción.',
                'control_existente': 'Gestión de cambios controlada por repositorio Git.',
                'evidencia_disponible': 'PT-05_trazabilidad_ddl_lab.csv (demostrado en laboratorio)',
                'evidencia_pendiente': 'Aprobación del proveedor para trigger DDL o control estricto de accesos',
                'estado': 'Identificado',
                'fecha_creado': '15/11/2026'
            }
        ]
        self.mocks['hallazgos'].return_value = [{
            'codigo': 'H01',
            'titulo': 'Dependencia de respaldos manuales y ausencia de verificación programada',
            'condicion': 'Los archivos .bak se generan manualmente sin programación formal.',
            'criterio': 'COBIT 2019 DSS05.04 / CIS Benchmark Sección Backups',
            'causa': 'Falta de agente SQL programado en hosting compartido',
            'efecto': 'Riesgo de pérdida de información en caso de contingencia',
            'recomendacion': 'Establecer cron job y pruebas periódicas de restauración',
            'riesgo_asociado': 'RSK-01 (Nivel Alto)',
            'evidencias_relacionadas': 'PT-06_restauracion_verificada.log, Registro AdminAuditLog',
            'procedimiento_relacionado': 'Auditoría de Continuidad y Procedimientos de Respaldo',
            'severidad': 'Medio',
            'estado': 'Borrador',
            'responsable_revision': 'Pendiente de revisión del auditor',
            'fecha_fmt': '15/11/2026'
        }]
        self.mocks['backups'].return_value = {
            'ultimo_backup': {
                'usuario': 'admin',
                'accion': 'BACKUP',
                'detalle': 'Backup semanal',
                'fecha': '14/11/2026 23:00:00',
                'tipo': 'Lógico (ZIP/Excel)',
                'entorno': 'Producción',
                'estado': 'Generado exitosamente'
            },
            'restauraciones': [{
                'id': 1,
                'fecha': '15/11/2026 22:20:06',
                'fecha_verificacion': '15/11/2026 22:20:06',
                'fecha_restauracion': 'No disponible (Restauración previa en laboratorio)',
                'base_restaurada': 'BibliotecaUNDAC_Auditoria',
                'archivo_bak': 'db_acdcfa_bibliotecaundac_10_29_2026_23.bak',
                'pruebas_realizadas': "DBCC CHECKDB ('BibliotecaUNDAC_Auditoria') WITH NO_INFOMSGS; RESTORE VERIFYONLY",
                'ambiente': 'Laboratorio (SQL Server Local)',
                'responsable': 'DBA Auditor',
                'evidencia_adjunta': 'PT-06_restauracion_verificada.log',
                'sha256_hash': '9da3b4899b50c7bfd968a59345911eb65598f088797549f776c5a45b4fe888db',
                'observaciones': 'Integridad comprobada',
                'estado': 'Verificado'
            }]
        }
        self.mocks['matriz'].return_value = [{
            'control': 'Gestión de Principales',
            'criterio': 'CIS §2.1',
            'procedimiento': 'Censo',
            'evidencia': 'PT-01',
            'resultado': 'Conforme',
            'observacion': 'OK'
        }]
        self.mocks['comp'].return_value = [{
            'elemento': 'Principales de Base de Datos',
            'fuente': 'sys.database_principals',
            'tipo_poblacion': 'Censo Completo de la BD',
            'elementos_visibles': '5 visibles',
            'limitacion': 'Restricción de proveedor'
        }]

    def tearDown(self):
        for p in self.patchers.values():
            p.stop()


class TestCentroAuditoriaVistasYTabs(BaseAuditoriaMockedTestCase):
    """Pruebas de renderizado de las 9 pestañas para SuperAdmin."""

    def test_04_render_tab_resumen(self):
        """Verifica la carga de la pestaña 1: Resumen Ejecutivo."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'
            sess['csrf_token'] = 'token-prueba'

        resp = self.client.get('/admin/auditoria/?tab=resumen')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Centro de Auditoría de Base de Datos', texto)
        self.assertIn('Usuarios con mayor actividad registrada', texto)
        self.assertIn('Monitoreo de eventos administrativos activo', texto)

    def test_05_render_tab_eventos(self):
        """Verifica la carga de la pestaña 2: Eventos de Aplicación."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=eventos')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Acción Protocolar', texto)
        self.assertIn('Modificar Rol', texto)

    def test_06_render_tab_sql_seguridad(self):
        """Verifica la pestaña 3: Seguridad SQL Server."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=sql')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Principales de Base de Datos', texto)
        self.assertIn('sys.database_principals', texto)

    def test_07_render_tab_trazabilidad(self):
        """Verifica la pestaña 4 y la presencia de la advertencia de entorno de laboratorio."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=trazabilidad')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Trazabilidad DML', texto)
        self.assertIn('laboratorio', texto.lower())
        self.assertIn('100.0%', texto)

    def test_08_render_tab_evidencias(self):
        """Verifica la pestaña 5: Evidencias y Cadena de Custodia SHA-256."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=evidencias')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Integridad Criptográfica de Evidencias', texto)
        self.assertIn('EVD-01', texto)
        self.assertIn('SHA-256 es una huella digital que permite verificar la integridad de la evidencia', texto)
        self.assertNotIn('firma para garantizar la inmutabilidad', texto)

    def test_09_render_tab_riesgos(self):
        """Verifica la pestaña 6: Matriz de Riesgos Preliminares."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=riesgos')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Riesgo preliminar ≠ hallazgo confirmado', texto)
        self.assertIn('RSK-01', texto)

    def test_10_render_tab_hallazgos(self):
        """Verifica la pestaña 7: Hallazgos Formales."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=hallazgos')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Registro Formal de Hallazgos', texto)
        self.assertIn('H01', texto)

    def test_11_render_tab_backups(self):
        """Verifica la pestaña 8: Backups y Pruebas de Restauración."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=backups')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Último Respaldo Lógico Generado', texto)
        self.assertIn('BibliotecaUNDAC_Auditoria', texto)
        self.assertIn('db_acdcfa_bibliotecaundac_10_29_2026_23.bak', texto)
        self.assertIn('DBCC CHECKDB', texto)
        self.assertIn('RESTORE VERIFYONLY', texto)
        self.assertIn('15/11/2026 22:20:06', texto)
        self.assertIn('No disponible (Restauración previa en laboratorio)', texto)

    def test_12_render_tab_criterios(self):
        """Verifica la pestaña 9: Criterios CIS Benchmark y Normativa."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/?tab=criterios')
        self.assertEqual(resp.status_code, 200)
        texto = resp.get_data(as_text=True)
        self.assertIn('Matriz de Evaluación', texto)
        self.assertIn('COBIT 2019 DSS05', texto)


class TestCentroAuditoriaExportaciones(BaseAuditoriaMockedTestCase):
    """Pruebas para las exportaciones CSV (UTF-8 con BOM) e informe consolidado."""

    def test_13_exportar_principales_csv(self):
        """Verifica la exportación CSV de principales con BOM UTF-8."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/exportar/principales')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp.headers.get('Content-Type', ''))
        data = resp.data
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'dbo', data)

    def test_14_exportar_roles_csv(self):
        """Verifica la exportación CSV de roles."""
        with self.client.session_transaction() as sess:
            sess['admin_user'] = 'superauditor'
            sess['admin_rol'] = 'SuperAdmin'

        resp = self.client.get('/admin/auditoria/exportar/roles')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'db_owner', resp.data)

    def test_15_exportar_informe_consolidado_markdown(self):
        """Verifica la generación del informe consolidado en Markdown."""
        with patch('routes.admin_auditoria.generar_informe_consolidado_markdown') as mock_inf:
            mock_inf.return_value = '# INFORME CONSOLIDADO DE AUDITORÍA\n\nSistema Bibliotecario UNDAC'
            with self.client.session_transaction() as sess:
                sess['admin_user'] = 'superauditor'
                sess['admin_rol'] = 'SuperAdmin'

            resp = self.client.get('/admin/auditoria/exportar/informe-consolidado')
            self.assertEqual(resp.status_code, 200)
            self.assertIn('text/markdown', resp.headers.get('Content-Type', ''))
            self.assertIn('INFORME CONSOLIDADO', resp.get_data(as_text=True))


class TestCentroAuditoriaLogicaNegocio(unittest.TestCase):
    """Pruebas unitarias de las funciones de soporte en utils/queries_auditoria.py."""

    def test_16_calculo_sha256_cadena_custodia(self):
        """Verifica que el cálculo SHA-256 sea exacto para textos y bytes."""
        texto = "Evidencia forense backup 2026-11-15"
        esperado = hashlib.sha256(texto.encode('utf-8')).hexdigest()
        self.assertEqual(calcular_sha256_texto(texto), esperado)

        bytes_data = b"Contenido binario de prueba .bak"
        esperado_bytes = hashlib.sha256(bytes_data).hexdigest()
        self.assertEqual(calcular_sha256_bytes(bytes_data), esperado_bytes)

    def test_17_sanitizacion_datos_sensibles(self):
        """Verifica que parsear_detalle enmascare o elimine contraseñas, tokens y hashes."""
        detalle_raw = 'Interceptado: {"token": "secret_abc123", "password": "SuperPassword99!", "user": "admin"}'
        limpio = parsear_detalle(detalle_raw)
        self.assertNotIn('secret_abc123', limpio)
        self.assertNotIn('SuperPassword99!', limpio)
        self.assertIn('admin', limpio)

    def test_18_clasificacion_severidad_eventos(self):
        """Verifica la clasificación semántica de severidad en eventos administrativos."""
        _, _, sev_crit = clasificar_accion_evento('VACIAR_EGRESADOS')
        self.assertEqual(sev_crit, 'Crítico')

        _, _, sev_adv = clasificar_accion_evento('ACCESOS/ELIMINAR')
        self.assertEqual(sev_adv, 'Advertencia')

        _, _, sev_info = clasificar_accion_evento('EXPORTAR_CSV')
        self.assertEqual(sev_info, 'Informativo')

    def test_19_matriz_riesgos_severidad_pxi(self):
        """Verifica que el cálculo de severidad P x I cumpla la escala definida."""
        # Escala: 1-4 Bajo, 5-9 Medio, 10-14 Alto, 15-25 Crítico
        self.assertEqual(calcular_nivel_riesgo(5, 4), 'Crítico')  # 20
        self.assertEqual(calcular_nivel_riesgo(5, 3), 'Crítico')  # 15
        self.assertEqual(calcular_nivel_riesgo(4, 3), 'Alto')     # 12
        self.assertEqual(calcular_nivel_riesgo(3, 3), 'Medio')    # 9
        self.assertEqual(calcular_nivel_riesgo(2, 2), 'Bajo')     # 4
        self.assertEqual(calcular_nivel_riesgo(1, 1), 'Bajo')     # 1

    def test_20_conversion_riesgo_a_borrador_hallazgo_requiere_humano(self):
        """
        Verifica que la conversión de un riesgo genera estrictamente un 'Borrador'
        y nunca un hallazgo 'Confirmado' automáticamente.
        """
        with patch('utils.queries_auditoria.get_db_connection') as mock_conn:
            cursor_mock = MagicMock()
            mock_conn.return_value.cursor.return_value = cursor_mock
            
            # Simular lectura del riesgo original
            cursor_mock.fetchone.side_effect = [
                ('RSK-001', 'Uso de cuenta sa', 'Alto', 'Riesgo alto de compromiso', 'db_owner', 'EVD-01'),
                (0,)  # Total hallazgos existentes
            ]

            ok, msg = convertir_riesgo_a_borrador_hallazgo('RSK-001', 'Auditor Responsable')
            self.assertTrue(ok)
            self.assertIn('Borrador de Hallazgo', msg)
            self.assertIn('Requiere revisión humana', msg)
            
            # Verificar el INSERT ejecutado
            insert_call = cursor_mock.execute.call_args_list[2]
            params = insert_call[0][1]
            # params: (nuevo_codigo, titulo, condicion, criterio, causa, efecto, recomendacion, riesgo_asociado, evidencia_rel, procedimiento_rel, responsable)
            self.assertIn('Borrador', insert_call[0][0])  # El query inserta 'Borrador'
            self.assertEqual(params[10], 'Auditor Responsable')

    def test_21_cobertura_dml_con_disclaimer_metodologico(self):
        """
        Verifica que obtener_trazabilidad_dml calcule la cobertura de laboratorio
        e incluya la advertencia obligatoria de alcance.
        """
        with patch('utils.queries_auditoria.get_db_connection') as mock_conn:
            cursor_mock = MagicMock()
            mock_conn.return_value.cursor.return_value = cursor_mock
            cursor_mock.fetchall.return_value = [
                (1, '15/11/2026 02:00:00', 'INSERT', 'dbo.RegistroIngresos', '101', 'sa', 'N/A', 'Alta 1', 1),
                (2, '15/11/2026 02:01:00', 'INSERT', 'dbo.RegistroIngresos', '102', 'sa', 'N/A', 'Alta 2', 1),
                (3, '15/11/2026 02:02:00', 'UPDATE', 'dbo.RegistroIngresos', '101', 'sa', 'Alta 1', 'Mod 1', 1),
                (4, '15/11/2026 02:03:00', 'DELETE', 'dbo.RegistroIngresos', '102', 'sa', 'Alta 2', 'N/A', 1),
            ]

            res = obtener_trazabilidad_dml()
            self.assertEqual(res['cobertura'], 100.0)
            self.assertEqual(res['observados'], 4)
            self.assertEqual(res['esperados'], 4)
            self.assertIn('disclaimer', res)
            self.assertIn('laboratorio', res['entorno'].lower())

    def test_22_manejo_restricciones_hosting_proveedor(self):
        """
        Verifica que cuando el proveedor de hosting deniega acceso o falla la conexión,
        el módulo reporte el estado defensivo sin generar excepciones no controladas.
        """
        with patch('utils.queries_auditoria.get_db_connection') as mock_conn:
            mock_conn.return_value = None  # Simula falla de conexión o denegación
            
            principales = obtener_principales_sql()
            roles = obtener_roles_sql()
            permisos = obtener_permisos_sql()

            self.assertEqual(principales['status'], 'error')
            self.assertEqual(principales['datos'], [])
            self.assertEqual(roles['status'], 'error')
            self.assertEqual(roles['datos'], [])
            self.assertEqual(permisos['status'], 'error')
            self.assertEqual(permisos['datos'], [])

            completitud = obtener_completitud_conciliacion()
            self.assertIsInstance(completitud, list)
            self.assertGreater(len(completitud), 0)

    def test_23_verificacion_evidencia_fisica_pt06(self):
        """
        Verifica la detección del archivo físico PT-06 en disco,
        lectura de sus bytes reales y coincidencia exacta del hash SHA-256.
        """
        import os
        ruta_base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'exportaciones', 'evidencias'
        )
        ruta_pt06 = os.path.join(ruta_base, 'PT-06_restauracion_verificada.log')
        self.assertTrue(os.path.isfile(ruta_pt06), f"El archivo {ruta_pt06} debe existir en disco.")

        hash_calculado = calcular_sha256_archivo(ruta_pt06)
        self.assertEqual(len(hash_calculado), 64)
        self.assertEqual(hash_calculado, '9da3b4899b50c7bfd968a59345911eb65598f088797549f776c5a45b4fe888db')


if __name__ == '__main__':
    unittest.main()
