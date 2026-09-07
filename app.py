from flask import Flask, request, session, redirect, url_for, jsonify
import os
import secrets
from datetime import timedelta

# Cargar variables del archivo .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from db import get_db_connection

from routes.ingreso import ingreso_bp
from routes.visitantes import visitantes_bp

# Módulos Administrador segregados
from routes.admin_dashboard import admin_dashboard_bp
from routes.admin_reportes import admin_reportes_bp
from routes.admin_carnets import admin_carnets_bp
from routes.admin_egresados import admin_egresados_bp
from routes.admin_personal import admin_personal_bp
from routes.admin_docentes import admin_docentes_bp
from routes.admin_eventos import admin_eventos_bp
from routes.admin_tasks import admin_tasks_bp
from routes.admin_auth import admin_auth_bp
from routes.admin_accesos import admin_accesos_bp
from routes.admin_auditoria import admin_auditoria_bp
from routes.admin_backup import admin_backup_bp
from routes.admin_salas import admin_salas_bp
from routes.api_undac import api_undac_bp
from routes.api_external import api_external_bp


app = Flask(__name__)

# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_hex(32)

app.secret_key = secret_key

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Detección dinámica de cookies seguras en HTTPS / Cloudflare Tunnel
is_secure_env = os.environ.get("SESSION_COOKIE_SECURE", "False").lower() == "true"
app.config["SESSION_COOKIE_SECURE"] = is_secure_env

# Límite de carga: 50 MB
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


# ============================================================
# CSRF
# ============================================================

def generar_csrf_token_si_no_existe():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


def validar_csrf():
    token_enviado = (
        request.form.get("csrf_token")
        or request.args.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )

    token_sesion = session.get("csrf_token")

    return token_enviado and token_sesion and secrets.compare_digest(
        token_enviado,
        token_sesion
    )


def respuesta_csrf_bloqueado():
    return """
    <h1>403 Peligro - Bloqueo CSRF</h1>
    <p>Se ha detectado una petición de servidor cruzado sin firma válida. Accesión denegada.</p>
    <a href="/admin/">Volver al Panel de Control</a>
    """, 403


# ============================================================
# CONTROL DE ACCESO ADMIN
# ============================================================

@app.before_request
def requerir_login_admin():
    # Solo proteger rutas administrativas
    if not request.path.startswith("/admin"):
        return

    # Permitir login sin sesión
    if request.path == "/admin/login":
        return

    # Permitir estáticos administrativos, si existieran
    if request.path.startswith("/admin/static"):
        return

    # Si no inició sesión como admin
    if "admin_user" not in session:
        return redirect(url_for("admin_auth.login"))

    session.permanent = True

    # Crear token CSRF
    generar_csrf_token_si_no_existe()

    # Proteger métodos que modifican datos
    METODOS_PROTEGIDOS = {"POST", "PUT", "PATCH", "DELETE"}

    if request.method in METODOS_PROTEGIDOS and not request.path.startswith("/admin/api"):
        if not validar_csrf():
            return respuesta_csrf_bloqueado()

    # Restricción para rol Supervisor
    if session.get("admin_rol") == "Supervisor":
        RUTAS_PERMITIDAS_SUPERVISOR = {
            "/admin",
            "/admin/",
            "/admin/login",
            "/admin/logout",
            "/admin/api/dashboard_data",
            "/admin/reporte_rango",
            "/admin/exportar_ingresos_excel",
            "/admin/eventos",
            "/admin/buscar_eventos",
            "/admin/guardar_evento",
            "/admin/importar_invitados_evento",
            "/admin/imprimir_reporte"
        }

        es_dinamica = (
            request.path.startswith("/admin/api/dashboard")
            or request.path.startswith("/admin/evento_detalle")
            or request.path.startswith("/admin/eliminar_evento")
            or request.path.startswith("/admin/eventos")
        )

        if request.path not in RUTAS_PERMITIDAS_SUPERVISOR and not es_dinamica:
            return """
            <h1>403 Forbidden</h1>
            <p>Tu rol de Supervisor no tiene permisos para visitar este módulo.</p>
            <a href="/admin/">Volver al Dashboard</a>
            """, 403

    # Restricción para rol Consultor
    if session.get("admin_rol") == "Consultor":
        if request.method in METODOS_PROTEGIDOS and request.path != "/admin/login" and request.path != "/admin/logout":
            return jsonify({'status': 'error', 'msg': 'Operación denegada. Rol Consultor (Solo Lectura).'})

        RUTAS_PERMITIDAS_CONSULTOR = {
            "/admin/login",
            "/admin/logout",
            "/admin/carnets",
            "/admin/egresados",
            "/admin/docentes",
            "/admin/personal",
            "/admin/visitantes"
        }

        es_busqueda = request.path.startswith("/admin/buscar_")
        
        if request.path not in RUTAS_PERMITIDAS_CONSULTOR and not es_busqueda and not request.path.startswith("/admin/static"):
            return """
            <h1>403 Forbidden</h1>
            <p>Tu rol de Consultor solo tiene acceso de lectura a los módulos de listas.</p>
            <a href="/admin/carnets">Ir a Alumnos</a>
            """, 403


# ============================================================
# INYECTAR CSRF EN PLANTILLAS
# ============================================================

@app.context_processor
def inject_csrf_token():
    if "admin_user" in session:
        generar_csrf_token_si_no_existe()

    return dict(csrf_token=session.get("csrf_token", ""))


# ============================================================
# AUDITORÍA ADMINISTRATIVA
# ============================================================

@app.after_request
def log_admin_actions(response):
    metodos_auditar = ["POST", "PUT", "PATCH", "DELETE"]

    rutas_excluidas = [
        "/admin/login",
        "/admin/logout"
    ]

    debe_auditar = (
        request.path.startswith("/admin")
        and request.method in metodos_auditar
        and request.path not in rutas_excluidas
        and "admin_user" in session
    )

    if debe_auditar:
        conn = None

        try:
            usuario = session.get("admin_user", "SISTEMA")
            accion = f"{request.method} {request.path}"
            detalle = f"Status: {response.status_code}"
            ip = request.remote_addr

            conn = get_db_connection()

            if conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO AdminAuditLog (Usuario, Accion, Detalle, IP)
                    VALUES (?, ?, ?, ?)
                """, (usuario, accion, detalle, ip))

                conn.commit()

        except Exception as e:
            print(f"Error guardando auditoría administrativa: {e}")

        finally:
            try:
                if conn:
                    conn.close()
            except Exception as e:
                print(f"Error cerrando conexión en auditoría: {e}")

    return response


# ============================================================
# REGISTRAR BLUEPRINTS
# ============================================================

app.register_blueprint(ingreso_bp)
app.register_blueprint(visitantes_bp)

app.register_blueprint(admin_dashboard_bp)
app.register_blueprint(admin_reportes_bp)
app.register_blueprint(admin_carnets_bp)
app.register_blueprint(admin_egresados_bp)
app.register_blueprint(admin_personal_bp)
app.register_blueprint(admin_docentes_bp)
app.register_blueprint(admin_eventos_bp)
app.register_blueprint(admin_tasks_bp)
app.register_blueprint(admin_auth_bp)
app.register_blueprint(admin_accesos_bp)
app.register_blueprint(admin_auditoria_bp)
app.register_blueprint(admin_backup_bp)
app.register_blueprint(admin_salas_bp)
app.register_blueprint(api_undac_bp)
app.register_blueprint(api_external_bp)

# ============================================================
# ARRANQUE DEL SERVIDOR
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    print("============================================================")
    print("   SISTEMA BIBLIOTECARIO UNDAC - ENTORNO DE PRODUCCIÓN")
    print("============================================================")
    print(f"[*] Servidor Waitress iniciado en el puerto {port}")
    print(f"[*] Acceso local: http://127.0.0.1:{port}")
    print(f"[*] Acceso en red: http://IP_DE_LA_PC_BIBLIOTECA:{port}")
    print("[*] Host activo: 0.0.0.0")
    print("[*] Multi-Threading activo para escaneos en paralelo")
    print("============================================================")

    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=16)

    except KeyboardInterrupt:
        print("\n[*] Servidor apagado por el administrador.")
        os._exit(0)
    except OSError as e:
        print(f"\n[AVISO] No se pudo abrir el puerto {port}: {e}")
        print("[!] Esto ocurre si ya tienes el sistema ejecutándose en otra ventana o consola.")
        print("[!] Verifica las ventanas abiertas o cierra la ventana previa antes de iniciar de nuevo.")
        os._exit(1)

