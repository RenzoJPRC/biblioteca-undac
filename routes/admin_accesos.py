from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from utils.cache_manager import global_cache
from utils.queries_accesos import (
    get_all_usuarios, db_crear_usuario, db_eliminar_usuario,
    check_admin_auth, db_actualizar_gestor
)

admin_accesos_bp = Blueprint('admin_accesos', __name__, url_prefix='/admin/accesos')

@admin_accesos_bp.route('/', methods=['GET'])
def index():
    # Solo el SuperAdmin puede ver esta página
    if session.get('admin_rol') != 'SuperAdmin':
        flash('Acceso denegado. No tienes permisos para gestionar administradores.', 'error')
        return redirect(url_for('admin_dashboard.admin_dashboard'))
        
    usuarios = global_cache.get('usuarios_sistema')
    
    if usuarios is None:
        usuarios = get_all_usuarios()
        if usuarios is None:
            flash('Error de BD', 'error')
            return redirect(url_for('admin_dashboard.admin_dashboard'))
        
        # Guardar en caché LRU por 60 segundos
        global_cache.set('usuarios_sistema', usuarios)
    
    return render_template('admin_accesos.html', usuarios=usuarios)

@admin_accesos_bp.route('/crear', methods=['POST'])
def crear():
    if session.get('admin_rol') != 'SuperAdmin':
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    usuario = request.form.get('usuario')
    password = request.form.get('password')
    rol = request.form.get('rol') # 'SuperAdmin' o 'Supervisor'
    sede = request.form.get('sede', 'Central')
    
    if not usuario or not password or not rol:
        flash('Por favor completa todos los campos obligatorios.', 'error')
        return redirect(url_for('admin_accesos.index'))
        
    hash_pw = generate_password_hash(password)
    email_generado = f"{usuario}@undac.edu.pe"
    
    ok, msg = db_crear_usuario(usuario, email_generado, hash_pw, rol, sede)
    if ok:
        global_cache.clear('usuarios_sistema')
        flash(f'Usuario {usuario} creado con éxito.', 'success')
    else:
        flash(f'Error al crear usuario: {msg}', 'error')
        
    return redirect(url_for('admin_accesos.index'))

@admin_accesos_bp.route('/editar', methods=['POST'])
def editar():
    # El usuario actual (quien edita) debe ser SuperAdmin
    if session.get('admin_rol') != 'SuperAdmin':
        flash('Acceso denegado. Se requiere nivel SuperAdmin.', 'error')
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    admin_username = session.get('admin_user')
    password_admin = request.form.get('password_admin')
    
    id_usuario_target = request.form.get('id_usuario')
    nuevo_usuario = request.form.get('nuevo_usuario', '').strip()
    nuevo_correo = request.form.get('nuevo_correo', '').strip()
    nueva_password = request.form.get('nueva_password', '').strip()

    if not password_admin or not id_usuario_target:
        flash('Faltan credenciales de verificación para editar el perfil.', 'error')
        return redirect(url_for('admin_accesos.index'))

    # 1. Validar contraseña admin actual
    admin_data = check_admin_auth(admin_username)
    if not admin_data or not check_password_hash(admin_data[0], password_admin):
        flash('La contraseña actual es incorrecta. Operación abortada por seguridad.', 'error')
        return redirect(url_for('admin_accesos.index'))

    # 2. Proceder con la actualización
    updates = []
    params = []
    
    if nuevo_usuario:
        updates.append("Usuario = ?")
        params.append(nuevo_usuario)
        
    if nuevo_correo:
        updates.append("Email = ?")
        params.append(nuevo_correo)
        
    if nueva_password:
        updates.append("PasswordHash = ?")
        params.append(generate_password_hash(nueva_password))
        
    if updates:
        ok, msg = db_actualizar_gestor(id_usuario_target, updates, params)
        if ok:
            global_cache.clear('usuarios_sistema')
            flash('Gestor actualizado con éxito.', 'success')
        else:
            flash(f'Error de sistema al editar: {msg}', 'error')
    else:
        flash('No detectamos ningún cambio para aplicar.', 'warning')

    return redirect(url_for('admin_accesos.index'))

@admin_accesos_bp.route('/eliminar/<int:id>', methods=['POST'])
def eliminar(id):
    if session.get('admin_rol') != 'SuperAdmin':
        return redirect(url_for('admin_dashboard.admin_dashboard'))
        
    ok, msg = db_eliminar_usuario(id)
    if ok:
        global_cache.clear('usuarios_sistema')
        flash('Usuario eliminado.', 'success')
    else:
        flash(f'Error al eliminar: {msg}', 'error')
        
    return redirect(url_for('admin_accesos.index'))
