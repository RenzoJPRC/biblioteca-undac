from flask import Blueprint, jsonify, session
import requests

api_undac_bp = Blueprint('api_undac', __name__, url_prefix='/api/undac')

BASE_URL = "http://api.undac.edu.pe/tasks/a3945a7384cbdcd33f49e8f5b8ec29f5/91f33e2776c526b9cca723a63476f028"

@api_undac_bp.route('/alumno/<codigo>', methods=['GET'])
def buscar_alumno_api(codigo):
    if "admin_user" not in session:
        return jsonify({'status': 'error', 'msg': 'Acceso denegado'}), 403
        
    if not codigo or not codigo.strip():
        return jsonify({'status': 'error', 'msg': 'Código inválido'}), 400
        
    try:
        url = f"{BASE_URL}/{codigo.strip()}"
        response = requests.get(url, timeout=5)
        
        if response.status_code != 200:
            return jsonify({'status': 'error', 'msg': 'Error conectando con la UNDAC'}), 500
            
        data = response.json()
        
        if "message" in data:
            return jsonify({'status': 'error', 'msg': data["message"]}), 404
            
        # Limpiar datos para el frontend
        resultado = {
            'nombres': data.get('Nombres', ''),
            'apellido_paterno': data.get('Apellido paterno', ''),
            'apellido_materno': data.get('Apellido materno', ''),
            'nombre_completo': f"{data.get('Apellido paterno', '')} {data.get('Apellido materno', '')}, {data.get('Nombres', '')}".strip(" ,"),
            'correo': data.get('Correo Institucional', ''),
            'dni': data.get('Dni', ''),
            'facultad': data.get('Programa facultad', ''),
            'rol': data.get('Rol', '')
        }
        
        return jsonify({'status': 'success', 'data': resultado})
        
    except requests.exceptions.Timeout:
        return jsonify({'status': 'error', 'msg': 'La API de la UNDAC tardó demasiado en responder.'}), 504
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500
