"""
Rutas del Centro de Auditoría de Base de Datos y Seguridad.
Sistema Bibliotecario UNDAC.

Cumplimiento:
- CIS Microsoft SQL Server 2022 Benchmark
- COBIT 2019 DSS05
- Ley N.º 29733 (Protección de Datos Personales)
- Ley N.º 30096 (Delitos Informáticos - Marco de Autorización)

Control de Acceso:
- Exclusivo para SuperAdmin (RBAC institucional).
- Protección CSRF en todas las operaciones POST.
- Producción = Evidencia de solo lectura.
- Laboratorio = Pruebas controladas de trazabilidad y recuperación.
- Optimización: Carga perezosa (lazy-loading) por pestaña y paginación en base de datos.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from utils.queries_auditoria import (
    calcular_sha256_bytes,
    calcular_sha256_texto,
    cambiar_estado_hallazgo,
    convertir_riesgo_a_borrador_hallazgo,
    generar_informe_consolidado_markdown,
    guardar_evidencia_auditoria,
    guardar_hallazgo_auditoria,
    guardar_restauracion_verificada,
    guardar_riesgo_auditoria,
    invalidar_cache_kpis,
    obtener_auditoria_dashboard,
    obtener_completitud_conciliacion,
    obtener_evidencias_auditoria,
    obtener_hallazgos_auditoria,
    obtener_historial_backups_y_restauraciones,
    obtener_kpis_centro_auditoria,
    obtener_matriz_criterios_auditoria,
    obtener_permisos_sql,
    obtener_principales_sql,
    obtener_riesgos_auditoria,
    obtener_roles_sql,
    obtener_top_usuarios_actividad,
    obtener_trazabilidad_ddl,
    obtener_trazabilidad_dml,
    parsear_detalle,
    traducir_accion,
)

admin_auditoria_bp = Blueprint('admin_auditoria', __name__, url_prefix='/admin/auditoria')


def _verificar_superadmin() -> bool:
    """Valida que la sesión activa pertenezca a un SuperAdmin."""
    return session.get('admin_rol') == 'SuperAdmin'


def _verificar_csrf() -> bool:
    """Invoca la función de validación CSRF centralizada de la aplicación."""
    try:
        from app import validar_csrf
        return validar_csrf()
    except Exception:
        # Fallback defensivo: comparar token de sesión
        token_req = (
            request.form.get('csrf_token') 
            or request.headers.get('X-CSRFToken') 
            or request.headers.get('X-CSRF-Token')
        )
        token_ses = session.get('csrf_token')
        return bool(token_req and token_ses and token_req == token_ses)


# =====================================================================
# RUTA PRINCIPAL (CENTRO DE AUDITORÍA CON CARGA PEREZOSA POR PESTAÑA)
# =====================================================================

@admin_auditoria_bp.route('/', methods=['GET'])
def index():
    if not _verificar_superadmin():
        flash('Acceso denegado: El Centro de Auditoría de Base de Datos es exclusivo para SuperAdmin.', 'error')
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    tab_activa = request.args.get('tab', 'resumen').strip().lower()
    tabs_validas = {
        'resumen', 'eventos', 'sql', 'trazabilidad', 
        'evidencias', 'riesgos', 'hallazgos', 'backups', 'criterios'
    }
    if tab_activa not in tabs_validas:
        tab_activa = 'resumen'

    # Parámetros de paginación
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    page_perm = request.args.get('page_perm', 1, type=int)
    per_page_perm = request.args.get('per_page_perm', 50, type=int)

    # Filtros para la pestaña de Eventos de Aplicación
    filtros_eventos = {
        'fecha_desde': request.args.get('fecha_desde', ''),
        'fecha_hasta': request.args.get('fecha_hasta', ''),
        'usuario': request.args.get('usuario', ''),
        'accion': request.args.get('accion', ''),
        'ip': request.args.get('ip', ''),
        'nivel': request.args.get('nivel', '')
    }

    # KPIs consolidados (usando caché de 60 segundos)
    kpis = obtener_kpis_centro_auditoria()

    # Estructuras seguras por defecto para evitar errores en Jinja
    completitud = []
    matriz_criterios = []
    logs = []
    total_logs = 0
    top_usuarios = []
    paginacion_eventos = {'page': page, 'per_page': per_page, 'total_logs': 0, 'total_paginas': 1}
    principales_res = {'datos': [], 'total': 0}
    roles_res = {'datos': [], 'total': 0}
    permisos_res = {'datos': [], 'total': 0, 'paginacion': {'page': page_perm, 'per_page': per_page_perm, 'total': 0, 'total_paginas': 1}}
    dml_res = {'datos': [], 'cobertura': 0, 'esperados': 4, 'observados': 0, 'disclaimer': ''}
    ddl_res = {'datos': [], 'total': 0}
    evidencias = []
    riesgos = []
    hallazgos = []
    backups_res = {'ultimo_backup': None, 'restauraciones': []}

    # CARGA PEREZOSA (LAZY-LOADING): Solo ejecuta consultas para la pestaña activa
    if tab_activa == 'resumen':
        completitud = obtener_completitud_conciliacion()
        top_usuarios = obtener_top_usuarios_actividad(5)
    elif tab_activa == 'eventos':
        res_dash = obtener_auditoria_dashboard(
            filtros_eventos, page=page, per_page=per_page
        )
        if len(res_dash) == 4:
            logs, total_logs, top_usuarios, paginacion_eventos = res_dash
        else:
            logs, total_logs, top_usuarios = res_dash[0], res_dash[1], res_dash[2]
            paginacion_eventos = {'page': page, 'per_page': per_page, 'total_logs': total_logs, 'total_paginas': 1}
    elif tab_activa == 'sql':
        principales_res = obtener_principales_sql()
        roles_res = obtener_roles_sql()
        permisos_res = obtener_permisos_sql(page=page_perm, per_page=per_page_perm)
    elif tab_activa == 'trazabilidad':
        dml_res = obtener_trazabilidad_dml()
        ddl_res = obtener_trazabilidad_ddl()
    elif tab_activa == 'evidencias':
        evidencias = obtener_evidencias_auditoria()
    elif tab_activa == 'riesgos':
        riesgos = obtener_riesgos_auditoria()
    elif tab_activa == 'hallazgos':
        hallazgos = obtener_hallazgos_auditoria()
    elif tab_activa == 'backups':
        backups_res = obtener_historial_backups_y_restauraciones()
    elif tab_activa == 'criterios':
        matriz_criterios = obtener_matriz_criterios_auditoria()

    return render_template(
        'admin_auditoria.html',
        tab_activa=tab_activa,
        filtros=filtros_eventos,
        # Paginación
        paginacion=paginacion_eventos,
        # Pestaña 1: Resumen
        kpis=kpis,
        completitud=completitud,
        # Pestaña 2: Eventos de aplicación
        logs=logs,
        total=total_logs,
        tops=top_usuarios,
        # Pestaña 3: Seguridad SQL Server
        principales=principales_res,
        roles_sql=roles_res,
        permisos_sql=permisos_res,
        # Pestaña 4: Trazabilidad DML / DDL
        dml=dml_res,
        ddl=ddl_res,
        # Pestaña 5: Evidencias y cadena de custodia
        evidencias=evidencias,
        # Pestaña 6: Riesgos
        riesgos=riesgos,
        # Pestaña 7: Hallazgos
        hallazgos=hallazgos,
        # Pestaña 8: Backups y recuperación
        backups=backups_res,
        # Pestaña 9: Criterios y Matriz
        matriz=matriz_criterios,
        # Metadata de ambiente
        entorno_produccion="Producción (SmarterASP.NET SQL Server 2022)",
        entorno_laboratorio="Laboratorio (SQL Server Local - Caso Controlado)"
    )


# =====================================================================
# RUTAS DE ACCIÓN: EVIDENCIAS Y CADENA DE CUSTODIA (SHA-256)
# =====================================================================

@admin_auditoria_bp.route('/evidencias/guardar', methods=['POST'])
def guardar_evidencia():
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='evidencias'))

    datos = request.form.to_dict()
    archivo = request.files.get('archivo_evidencia')
    if archivo and archivo.filename:
        contenido = archivo.read()
        datos['nombre_archivo'] = archivo.filename
        datos['sha256_hash'] = calcular_sha256_bytes(contenido)

    ok, msg = guardar_evidencia_auditoria(datos)
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='evidencias'))


@admin_auditoria_bp.route('/evidencias/verificar-hash', methods=['POST'])
def verificar_hash():
    """Endpoint API/AJAX para calcular o verificar la integridad SHA-256 de una evidencia."""
    if not _verificar_superadmin():
        return jsonify({'status': 'error', 'mensaje': 'Acceso no autorizado'}), 403

    archivo = request.files.get('archivo')
    texto = request.form.get('texto', '')
    hash_esperado = request.form.get('hash_esperado', '').strip().lower()

    if archivo and archivo.filename:
        contenido = archivo.read()
        hash_calculado = calcular_sha256_bytes(contenido)
        nombre = archivo.filename
    else:
        hash_calculado = calcular_sha256_texto(texto)
        nombre = "Texto / Carga útil"

    coincide = None
    if hash_esperado:
        coincide = (hash_calculado == hash_esperado)

    return jsonify({
        'status': 'success',
        'nombre': nombre,
        'hash_calculado': hash_calculado,
        'hash_esperado': hash_esperado or None,
        'integridad_verificada': coincide,
        'explicacion': 'El hash SHA-256 permite comprobar si la evidencia fue modificada después de su extracción.'
    })


# =====================================================================
# RUTAS DE ACCIÓN: REGISTRO Y GESTIÓN DE RIESGOS PRELIMINARES
# =====================================================================

@admin_auditoria_bp.route('/riesgos/guardar', methods=['POST'])
def guardar_riesgo():
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='riesgos'))

    ok, msg = guardar_riesgo_auditoria(request.form.to_dict())
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='riesgos'))


@admin_auditoria_bp.route('/riesgos/convertir-hallazgo', methods=['POST'])
def convertir_riesgo():
    """Convierte una hipótesis de riesgo a Borrador de Hallazgo (requiere revisión humana)."""
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='riesgos'))

    codigo_riesgo = request.form.get('codigo_riesgo', '').strip()
    responsable = session.get('admin_user', 'Auditor Líder')
    ok, msg = convertir_riesgo_a_borrador_hallazgo(codigo_riesgo, responsable)
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='hallazgos'))


# =====================================================================
# RUTAS DE ACCIÓN: GESTIÓN DE HALLAZGOS FORMALES DE AUDITORÍA
# =====================================================================

@admin_auditoria_bp.route('/hallazgos/guardar', methods=['POST'])
def guardar_hallazgo():
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='hallazgos'))

    datos = request.form.to_dict()
    datos['responsable_revision'] = session.get('admin_user', 'Auditor Líder')
    ok, msg = guardar_hallazgo_auditoria(datos)
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='hallazgos'))


@admin_auditoria_bp.route('/hallazgos/cambiar-estado', methods=['POST'])
def cambiar_estado():
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='hallazgos'))

    codigo = request.form.get('codigo', '').strip()
    nuevo_estado = request.form.get('nuevo_estado', '').strip()
    responsable = session.get('admin_user', 'Auditor Líder')

    ok, msg = cambiar_estado_hallazgo(codigo, nuevo_estado, responsable)
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='hallazgos'))


# =====================================================================
# RUTAS DE ACCIÓN: RESTAURACIÓN VERIFICADA DE BACKUPS
# =====================================================================

@admin_auditoria_bp.route('/recuperacion/guardar', methods=['POST'])
def guardar_recuperacion():
    if not _verificar_superadmin() or not _verificar_csrf():
        flash('Operación no autorizada o token CSRF inválido.', 'error')
        return redirect(url_for('admin_auditoria.index', tab='backups'))

    datos = request.form.to_dict()
    datos['responsable'] = session.get('admin_user', 'DBA')
    ok, msg = guardar_restauracion_verificada(datos)
    invalidar_cache_kpis()
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_auditoria.index', tab='backups'))


# =====================================================================
# EXPORTACIONES CSV CON BOM UTF-8 (SIN CONTRASEÑAS NI SECRETOS)
# =====================================================================

def _crear_respuesta_csv(filas: list[list[Any]], nombre_archivo: str) -> Response:
    """Genera una respuesta HTTP de descarga CSV compatible con Excel (UTF-8 con BOM)."""
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, lineterminator='\r\n')
    for f in filas:
        writer.writerow(f)

    response = Response(output.getvalue(), mimetype='text/csv; charset=utf-8-sig')
    response.headers['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


@admin_auditoria_bp.route('/exportar/eventos', methods=['GET'])
def exportar_eventos_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    filtros = {
        'fecha_desde': request.args.get('fecha_desde', ''),
        'fecha_hasta': request.args.get('fecha_hasta', ''),
        'usuario': request.args.get('usuario', ''),
        'accion': request.args.get('accion', ''),
        'ip': request.args.get('ip', ''),
        'nivel': request.args.get('nivel', '')
    }
    # Exportación completa sin paginación truncada
    res_dash = obtener_auditoria_dashboard(filtros, exportar_todo=True)
    logs = res_dash[0] if isinstance(res_dash, (tuple, list)) else []

    filas = [['ID Log', 'Fecha y Hora', 'Usuario', 'Acción Protocolar', 'Categoría', 'Nivel Severidad', 'IP Origen', 'Detalle Operativo']]
    for l in logs:
        filas.append([
            l['log_id'], l['fecha_fmt'], l['usuario'], l['accion_legible'],
            l['categoria'], l['nivel'], l['ip'], l['detalle']
        ])

    nombre = f"auditoria_eventos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/principales', methods=['GET'])
def exportar_principales_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    res = obtener_principales_sql()
    filas = [['Nombre Principal', 'Tipo', 'Autenticación', 'Fecha Creación', 'Fecha Modificación']]
    for p in res.get('datos', []):
        filas.append([p['name'], p['type_desc'], p['authentication_type_desc'], p['create_date'], p['modify_date']])

    nombre = f"PT-01_principales_sql_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/roles', methods=['GET'])
def exportar_roles_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    res = obtener_roles_sql()
    filas = [['Usuario / Miembro', 'Rol de Base de Datos']]
    for r in res.get('datos', []):
        filas.append([r['usuario'], r['rol']])

    nombre = f"PT-02_roles_db_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/permisos', methods=['GET'])
def exportar_permisos_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    res = obtener_permisos_sql(exportar_todo=True)
    filas = [['Usuario / Receptor', 'Permiso Explícito', 'Estado', 'Tipo de Objeto']]
    for p in res.get('datos', []):
        filas.append([p['usuario'], p['permiso'], p['estado'], p['tipo_objeto']])

    nombre = f"PT-03_permisos_explicitos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/trazabilidad-dml', methods=['GET'])
def exportar_dml_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    res = obtener_trazabilidad_dml()
    filas = [['ID', 'FechaHora', 'Operación', 'Tabla', 'Identificador', 'Usuario Técnico', 'Valores Antes', 'Valores Después', 'Ambiente']]
    for d in res.get('datos', []):
        filas.append([d['id'], d['fecha_hora'], d['operacion'], d['tabla'], d['identificador'], d['usuario_tecnico'], d['valores_antes'], d['valores_despues'], 'Laboratorio'])

    nombre = f"PT-04_trazabilidad_dml_lab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/trazabilidad-ddl', methods=['GET'])
def exportar_ddl_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    res = obtener_trazabilidad_ddl()
    filas = [['ID', 'FechaHora', 'Evento', 'Esquema', 'Objeto', 'Tipo Objeto', 'Usuario SQL', 'Comando SQL', 'Ambiente']]
    for d in res.get('datos', []):
        filas.append([d['id'], d['fecha_hora'], d['evento'], d['esquema'], d['objeto'], d['tipo_objeto'], d['usuario_sql'], d['comando'], 'Laboratorio'])

    nombre = f"PT-05_trazabilidad_ddl_lab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/evidencias', methods=['GET'])
def exportar_evidencias_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    evidencias = obtener_evidencias_auditoria()
    filas = [['Código', 'Procedimiento Relacionado', 'Descripción', 'Entorno', 'Fuente', 'Archivo', 'Fecha y Hora', 'Responsable', 'Hash SHA-256 (Custodia)', 'Observación', 'Estado']]
    for e in evidencias:
        filas.append([
            e['codigo'], e['procedimiento'], e['descripcion'], e['entorno'],
            e['fuente'], e['nombre_archivo'], e['fecha_hora'], e['responsable'],
            e['sha256_hash'], e['observacion'], e['estado']
        ])

    nombre = f"registro_evidencias_custodia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/riesgos', methods=['GET'])
def exportar_riesgos_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    riesgos = obtener_riesgos_auditoria()
    filas = [['Código', 'Riesgo Preliminar', 'Probabilidad (1-5)', 'Impacto (1-5)', 'Nivel de Riesgo', 'Razón Técnica', 'Control Existente', 'Evidencia Disponible', 'Evidencia Pendiente', 'Estado', 'Fecha Identificado']]
    for r in riesgos:
        filas.append([
            r['codigo'], r['riesgo'], r['probabilidad'], r['impacto'],
            r['nivel'], r['razon'], r['control_existente'], r['evidencia_disponible'],
            r['evidencia_pendiente'], r['estado'], r['fecha_creado']
        ])

    nombre = f"registro_riesgos_preliminares_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/hallazgos', methods=['GET'])
def exportar_hallazgos_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    hallazgos = obtener_hallazgos_auditoria()
    filas = [['ID', 'Título del Hallazgo', 'Condición Observada', 'Criterio Normativo', 'Causa Raíz', 'Efecto / Impacto', 'Recomendación Técnica', 'Riesgo Asociado', 'Evidencias', 'Procedimiento', 'Estado', 'Fecha', 'Responsable Revisión']]
    for h in hallazgos:
        filas.append([
            h['codigo'], h['titulo'], h['condicion'], h['criterio'],
            h['causa'], h['efecto'], h['recomendacion'], h['riesgo_asociado'],
            h['evidencias_relacionadas'], h['procedimiento_relacionado'],
            h['estado'], h['fecha'], h['responsable_revision']
        ])

    nombre = f"registro_hallazgos_auditoria_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/matriz', methods=['GET'])
def exportar_matriz_csv():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    matriz = obtener_matriz_criterios_auditoria()
    filas = [['Control Auditado', 'Criterio Normativo', 'Procedimiento de Auditoría', 'Evidencia Vinculada', 'Resultado Evaluación', 'Observación']]
    for m in matriz:
        filas.append([m['control'], m['criterio'], m['procedimiento'], m['evidencia'], m['resultado'], m['observacion']])

    nombre = f"matriz_criterios_cis_cobit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return _crear_respuesta_csv(filas, nombre)


@admin_auditoria_bp.route('/exportar/informe-consolidado', methods=['GET'])
def exportar_informe_consolidado():
    if not _verificar_superadmin():
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    contenido_md = generar_informe_consolidado_markdown()
    nombre = f"informe_auditoria_consolidado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    response = Response(contenido_md, mimetype='text/markdown; charset=utf-8')
    response.headers['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return response
