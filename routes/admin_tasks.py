from flask import Blueprint, jsonify
from utils.task_manager import get_task_status

admin_tasks_bp = Blueprint('admin_tasks', __name__, url_prefix='/admin')

@admin_tasks_bp.route('/upload_status/<task_id>', methods=['GET'])
def upload_status(task_id):
    status_data = get_task_status(task_id)
    return jsonify(status_data)
