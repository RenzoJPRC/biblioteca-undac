from flask import Blueprint, render_template, request, jsonify
import sys
import os

import threading
from utils.task_manager import create_task

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.queries_carnets import (
    buscar_alumnos_paginados,
    crear_alumno_individual,
    actualizar_alumno_completo_db,
    eliminar_alumno_individual,
    vaciar_alumnos_db,
    actualizar_vencimiento_masivo,
    actualizar_vencimiento_global,
    eliminar_alumnos_masivo_db,
    procesar_excel_alumnos_async
)

admin_carnets_bp = Blueprint('admin_carnets', __name__, url_prefix='/admin')

@admin_carnets_bp.route('/carnets')
def gestion_carnets():
    return render_template('admin_carnets.html')

@admin_carnets_bp.route('/buscar_alumno')
def buscar_alumno():
    query = request.args.get('q', '').strip()
    page = int(request.args.get('page', 1))
    limit = 20 # Elementos por página
    
    resultados, total_items, total_pages = buscar_alumnos_paginados(query, page, limit)
    
    return jsonify({
        'data': resultados,
        'pagination': {
            'page': page,
            'limit': limit,
            'total_items': total_items,
            'total_pages': total_pages
        }
    })

@admin_carnets_bp.route('/crear_alumno', methods=['POST'])
def crear_alumno():
    data = request.json
    nombre = data.get('nombre')
    
    if not nombre: return jsonify({'status': 'error', 'msg': 'El nombre es obligatorio'})

    success, msg = crear_alumno_individual(data)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/actualizar_alumno_completo', methods=['POST'])
def actualizar_alumno_completo():
    data = request.json
    alumno_id = data.get('id')
    
    if not alumno_id: return jsonify({'status': 'error', 'msg': 'Faltan datos (ID)'})

    success, msg = actualizar_alumno_completo_db(data)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/eliminar_alumno/<int:id>', methods=['DELETE'])
def eliminar_alumno(id):
    success, msg = eliminar_alumno_individual(id)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/vaciar_alumnos', methods=['DELETE'])
def vaciar_alumnos():
    success, msg = vaciar_alumnos_db()
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/eliminar_alumnos_masivo', methods=['POST'])
def eliminar_alumnos_masivo():
    data = request.json
    ids = data.get('ids', [])
    if not ids: return jsonify({'status': 'error', 'msg': 'No hay alumnos seleccionados'})
    
    success, msg = eliminar_alumnos_masivo_db(ids)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/actualizar_carnet_masivo', methods=['POST'])
def actualizar_carnet_masivo():
    data = request.json
    ids = data.get('ids', [])
    accion = data.get('accion') # 'activar' | 'desactivar' | 'auto'

    if not ids: return jsonify({'status': 'error', 'msg': 'No seleccionaste ningun alumno'})

    success, msg = actualizar_vencimiento_masivo(ids, accion)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/actualizar_carnet_global', methods=['POST'])
def actualizar_carnet_global():
    data = request.json
    accion = data.get('accion') # 'activar' | 'desactivar' | 'auto'

    success, msg = actualizar_vencimiento_global(accion)
    if success:
        return jsonify({'status': 'success', 'msg': msg})
    else:
        return jsonify({'status': 'error', 'msg': msg})

@admin_carnets_bp.route('/subir_excel', methods=['POST'])
def subir_excel():
    if 'archivo_excel' not in request.files: 
        return jsonify({'status': 'error', 'msg': 'No se adjuntó ningún archivo'})
    file = request.files['archivo_excel']
    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'status': 'error', 'msg': 'Formato no válido. Únicamente se permiten archivos Excel (.xlsx, .xls)'})

    try:
        print("1. Recibiendo archivo Excel de Alumnos y delegando a segundo plano...")
        file_bytes = file.read()
        task_id = create_task()
        
        thread = threading.Thread(target=procesar_excel_alumnos_async, args=(file_bytes, task_id))
        thread.daemon = True
        thread.start()
        
        return jsonify({'status': 'processing', 'task_id': task_id})
    except Exception as e:
        print("ERROR CRÍTICO AL LEER EXCEL:", str(e))
        return jsonify({'status': 'error', 'msg': str(e)})
