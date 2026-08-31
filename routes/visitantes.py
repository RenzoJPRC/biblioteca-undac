from flask import Blueprint, render_template, request, jsonify
from utils.queries_visitantes import obtener_todos_visitantes, registrar_nuevo_visitante, actualizar_visitante, borrar_visitante, procesar_excel_visitantes_async
import threading
from utils.task_manager import create_task

visitantes_bp = Blueprint('visitantes', __name__, url_prefix='/admin')

@visitantes_bp.route('/visitantes')
def admin_visitantes_page():
    query = request.args.get('q', '').strip()
    lista = obtener_todos_visitantes(query)
    return render_template('admin_visitantes.html', visitantes=lista, search_query=query)

@visitantes_bp.route('/agregar_visitante', methods=['POST'])
def agregar_visitante():
    data = request.json
    resultado = registrar_nuevo_visitante(data)
    return jsonify(resultado)

@visitantes_bp.route('/editar_visitante', methods=['POST'])
def editar_visitante():
    data = request.json
    resultado = actualizar_visitante(data)
    return jsonify(resultado)

@visitantes_bp.route('/subir_excel_visitantes', methods=['POST'])
def subir_excel_visitantes():
    if 'archivo_excel_vis' not in request.files: 
        return jsonify({'status': 'error', 'msg': 'No se adjuntó ningún archivo'})
    
    file = request.files['archivo_excel_vis']
    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'status': 'error', 'msg': 'Formato no válido. Únicamente se permiten archivos Excel (.xlsx, .xls)'})
        
    try:
        print("1. Recibiendo archivo Excel de Visitantes y delegando a segundo plano...")
        file_bytes = file.read()
        task_id = create_task()
        
        thread = threading.Thread(target=procesar_excel_visitantes_async, args=(file_bytes, task_id))
        thread.daemon = True
        thread.start()
        
        return jsonify({'status': 'processing', 'task_id': task_id})
    except Exception as e:
        print("ERROR CRÍTICO AL LEER EXCEL:", str(e))
        return jsonify({'status': 'error', 'msg': str(e)})

@visitantes_bp.route('/eliminar_visitante/<int:id>', methods=['DELETE'])
def eliminar_visitante(id):
    resultado = borrar_visitante(id)
    return jsonify(resultado)

@visitantes_bp.route('/vaciar_visitantes', methods=['POST', 'DELETE'])
def vaciar_visitantes_route():
    from utils.queries_visitantes import borrar_todos_visitantes
    resultado = borrar_todos_visitantes()
    return jsonify(resultado)