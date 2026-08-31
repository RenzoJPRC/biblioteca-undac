from flask import Blueprint, render_template, request, jsonify
import threading
from utils.task_manager import create_task
from utils.queries_docentes import (
    buscar_docentes, 
    guardar_docentes, 
    borrar_docentes, 
    procesar_excel_docentes_async,
    eliminar_docentes_masivo,
    vaciar_docentes_db
)

admin_docentes_bp = Blueprint('admin_docentes', __name__, url_prefix='/admin')

@admin_docentes_bp.route('/docentes')
def gestion_docentes():
    return render_template('admin_docentes.html')

@admin_docentes_bp.route('/buscar_docentes')
def buscar_docentes_endp():
    query = request.args.get('q', '').strip()
    page = int(request.args.get('page', 1))
    
    resultado = buscar_docentes(query, page)
    return jsonify(resultado)

@admin_docentes_bp.route('/guardar_docentes', methods=['POST'])
def guardar_docentes_endp():
    data = request.json
    resultado = guardar_docentes(data)
    return jsonify(resultado)

@admin_docentes_bp.route('/eliminar_docentes/<int:id>', methods=['DELETE'])
def eliminar_docentes_endp(id):
    resultado = borrar_docentes(id)
    return jsonify(resultado)

@admin_docentes_bp.route('/subir_excel_docentes', methods=['POST'])
def subir_excel_docentes():
    if 'archivo_excel' not in request.files: 
        return jsonify({'status': 'error', 'msg': 'No se adjuntó ningún archivo'})
    
    file = request.files['archivo_excel']
    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'status': 'error', 'msg': 'Formato no válido. Únicamente se permiten archivos Excel (.xlsx, .xls)'})
        
    try:
        print("1. Recibiendo archivo Excel de Docentes y delegando a segundo plano...")
        file_bytes = file.read()
        task_id = create_task()
        
        thread = threading.Thread(target=procesar_excel_docentes_async, args=(file_bytes, task_id))
        thread.daemon = True
        thread.start()
        
        return jsonify({'status': 'processing', 'task_id': task_id})
    except Exception as e:
        print("ERROR CRÍTICO AL LEER EXCEL DOCENTES:", str(e))
        return jsonify({'status': 'error', 'msg': str(e)})

@admin_docentes_bp.route('/eliminar_docentes_masivo', methods=['POST'])
def eliminar_docentes_masivo_route():
    data = request.json
    ids = data.get('ids', [])
    
    if not ids:
        return jsonify({'status': 'error', 'msg': 'No se enviaron IDs.'})
        
    resultado = eliminar_docentes_masivo(ids)
    return jsonify(resultado)

@admin_docentes_bp.route('/vaciar_docentes', methods=['DELETE'])
def vaciar_docentes_route():
    resultado = vaciar_docentes_db()
    return jsonify(resultado)
