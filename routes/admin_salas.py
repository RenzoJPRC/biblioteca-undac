from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from db import get_db_connection

admin_salas_bp = Blueprint('admin_salas', __name__, url_prefix='/admin/salas')

@admin_salas_bp.route('/', methods=['GET'])
def index():
    if 'admin_user' not in session: return redirect(url_for('admin_auth.login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SalaID, NombreSala, Piso, Sede, Activo FROM Salas ORDER BY Piso ASC, NombreSala ASC")
    salas_db = cursor.fetchall()
    conn.close()

    # Convertimos a diccionario para usarlo facil en Jinja
    salas = []
    for s in salas_db:
        salas.append({
            'SalaID': s.SalaID,
            'NombreSala': s.NombreSala,
            'Piso': s.Piso,
            'Sede': s.Sede,
            'Activo': s.Activo
        })

    return render_template('admin_salas.html', salas=salas)

@admin_salas_bp.route('/crear', methods=['POST'])
def crear():
    if 'admin_user' not in session: return redirect(url_for('admin_auth.login'))
    
    nombre = request.form.get('nombre')
    piso = request.form.get('piso')
    sede = request.form.get('sede', 'Central')
    
    if not nombre or not piso:
        flash('Faltan datos obligatorios para crear la sala.', 'error')
        return redirect(url_for('admin_salas.index'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Salas (NombreSala, Piso, Sede, Activo) VALUES (?, ?, ?, 1)", (nombre, int(piso), sede))
        conn.commit()
        conn.close()
        flash('Nueva Sala estructurada correctamente.', 'success')
    except Exception as e:
        flash(f'Error al crear la sala: {str(e)}', 'error')

    return redirect(url_for('admin_salas.index'))

@admin_salas_bp.route('/toggle/<int:sala_id>', methods=['POST'])
def toggle_estado(sala_id):
    if 'admin_user' not in session: return redirect(url_for('admin_auth.login'))
    
    nuevo_estado = request.form.get('estado')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE Salas SET Activo = ? WHERE SalaID = ?", (int(nuevo_estado), sala_id))
        conn.commit()
        conn.close()
        flash('Estado de la sala actualizado.', 'success')
    except Exception as e:
        flash(f'Error al modificar la sala: {str(e)}', 'error')
        
    return redirect(url_for('admin_salas.index'))
