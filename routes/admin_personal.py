from flask import Blueprint, render_template, request, jsonify
import threading
from utils.task_manager import create_task
from utils.queries_personal import (
    buscar_personal_administrativo, 
    guardar_personal_administrativo, 
    borrar_personal_administrativo, 
    procesar_excel_personal_async,
    eliminar_personal_masivo,
    vaciar_personal_db
)
admin_personal_bp = Blueprint('admin_personal', __name__, url_prefix='/admin')

@admin_personal_bp.route('/personal')
def gestion_personal():
    return render_template('admin_personal.html')

@admin_personal_bp.route('/buscar_personal')
def buscar_personal():
    query = request.args.get('q', '').strip()
    page = int(request.args.get('page', 1))
    
    resultado = buscar_personal_administrativo(query, page)
    return jsonify(resultado)

@admin_personal_bp.route('/guardar_personal', methods=['POST'])
def guardar_personal():
    data = request.json
    resultado = guardar_personal_administrativo(data)
    return jsonify(resultado)

@admin_personal_bp.route('/eliminar_personal/<int:id>', methods=['DELETE'])
def eliminar_personal(id):
    resultado = borrar_personal_administrativo(id)
    return jsonify(resultado)

@admin_personal_bp.route('/subir_excel_personal', methods=['POST'])
def subir_excel_personal():
    if 'archivo_excel' not in request.files: 
        return jsonify({'status': 'error', 'msg': 'No se adjuntó ningún archivo'})
    
    file = request.files['archivo_excel']
    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'status': 'error', 'msg': 'Formato no válido. Únicamente se permiten archivos Excel (.xlsx, .xls)'})
        
    try:
        print("1. Recibiendo archivo Excel de Personal y delegando a segundo plano...")
        file_bytes = file.read()
        task_id = create_task()
        
        thread = threading.Thread(target=procesar_excel_personal_async, args=(file_bytes, task_id))
        thread.daemon = True
        thread.start()
        
        return jsonify({'status': 'processing', 'task_id': task_id})
    except Exception as e:
        print("ERROR CRÍTICO AL LEER EXCEL:", str(e))
        return jsonify({'status': 'error', 'msg': str(e)})

@admin_personal_bp.route('/eliminar_personal_masivo', methods=['POST'])
def eliminar_personal_masivo_route():
    data = request.json
    ids = data.get('ids', [])
    
    if not ids:
        return jsonify({'status': 'error', 'msg': 'No se enviaron IDs.'})
        
    resultado = eliminar_personal_masivo(ids)
    return jsonify(resultado)

@admin_personal_bp.route('/vaciar_personal', methods=['DELETE'])
def vaciar_personal_route():
    resultado = vaciar_personal_db()
    return jsonify(resultado)
