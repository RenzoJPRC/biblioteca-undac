from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import check_password_hash
from db import get_db_connection
import json

admin_auth_bp = Blueprint('admin_auth', __name__, url_prefix='/admin')

# Rate Limiter Global (Memoria de Sesiones)
login_attempts = {}
MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 900 # 15 minutos

@admin_auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'admin_user' in session:
        if session.get('admin_rol') == 'Consultor':
            return redirect(url_for('admin_carnets.gestion_carnets'))
        return redirect(url_for('admin_dashboard.admin_dashboard'))
        
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        password = request.form.get('password')

        if not usuario or not password:
            flash('Por favor ingrese usuario y contraseña.', 'error')
            return render_template('admin_login.html')

        try:
            conn = get_db_connection()
            if not conn:
                flash('Error de conexión a la base de datos.', 'error')
                return render_template('admin_login.html')

            cursor = conn.cursor()
            
            # Verificar si el usuario existe y su estado de bloqueo
            cursor.execute("SELECT Usuario, PasswordHash, Rol, Activo, SedeAsignada, IntentosFallidos, BloqueadoHasta FROM UsuariosSistema WHERE Usuario = ?", (usuario,))
            row = cursor.fetchone()

            if not row:
                flash('Usuario o contraseña incorrectos.', 'error')
                conn.close()
                return render_template('admin_login.html')
            
            # Verificar si está bloqueado
            import datetime
            ahora = datetime.datetime.now()
            
            if row.BloqueadoHasta and row.BloqueadoHasta > ahora:
                minutos_restantes = int((row.BloqueadoHasta - ahora).total_seconds() / 60) + 1
                flash(f'¡Seguridad! Sistema bloqueado por demasiados intentos fallidos. Intente en {minutos_restantes} minutos.', 'error')
                conn.close()
                return render_template('admin_login.html')

            hash_guardado = row.PasswordHash or ''
            try:
                es_valido = check_password_hash(hash_guardado, password)
            except Exception:
                es_valido = False

            if es_valido:
                if not row.Activo:
                    flash('Esta cuenta está desactivada.', 'error')
                else:
                    # Login Correcto - Resetear contador
                    cursor.execute("UPDATE UsuariosSistema SET IntentosFallidos = 0, BloqueadoHasta = NULL WHERE Usuario = ?", (usuario,))
                    conn.commit()
                    
                    session['admin_user'] = row.Usuario
                    session['admin_rol'] = row.Rol
                    session['admin_sede'] = row.SedeAsignada or 'Central'
                    conn.close()
                    if row.Rol == 'Consultor':
                        return redirect(url_for('admin_carnets.gestion_carnets'))
                    return redirect(url_for('admin_dashboard.admin_dashboard'))
            else:
                # Contraseña incorrecta
                intentos_actuales = (row.IntentosFallidos or 0) + 1
                
                if intentos_actuales >= 5:
                    # Bloquear por 15 minutos
                    bloqueo_time = ahora + datetime.timedelta(minutes=15)
                    cursor.execute("UPDATE UsuariosSistema SET IntentosFallidos = ?, BloqueadoHasta = ? WHERE Usuario = ?", (intentos_actuales, bloqueo_time, usuario))
                    
                    # Log en auditoría
                    payload = json.dumps({"Target": usuario, "Alerta": "Exceso de Ataques", "Bloqueo": "15 Minutos"})
                    cursor.execute("INSERT INTO AdminAuditLog (Usuario, Accion, Detalle, IP) VALUES (?, ?, ?, ?)", ('Sistema', 'SEC_BRUTEFORCE', payload, request.remote_addr))
                    
                    conn.commit()
                    flash('¡Ataque detectado! Cuenta bloqueada por 15 minutos por seguridad.', 'error')
                else:
                    cursor.execute("UPDATE UsuariosSistema SET IntentosFallidos = ? WHERE Usuario = ?", (intentos_actuales, usuario))
                    conn.commit()
                    intentos_restantes = 5 - intentos_actuales
                    flash(f'Usuario o contraseña incorrectos. Quedan {intentos_restantes} intento(s) antes del bloqueo.', 'error')

            conn.close()

        except Exception as e:
            flash(f"Error técnico interno: {str(e)}", "error")
            return render_template('admin_login.html')

    return render_template('admin_login.html')

@admin_auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin_auth.login'))
