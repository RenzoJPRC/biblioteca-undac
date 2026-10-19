import os
import re
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from db import get_db_connection
from utils.queries_eventos import obtener_agenda_eventos_hoy, procesar_ingreso_evento, verificar_estado_evento, obtener_sede_evento
from utils.queries_ingreso import registrar_ingreso_general, validar_sala_activa

# Definimos el Blueprint
ingreso_bp = Blueprint('ingreso', __name__)

# Roles autorizados explícitamente para operar la terminal de registro de ingresos
ROLES_OPERADORES_PERMITIDOS = {'SuperAdmin', 'Supervisor', 'Operador', 'Administrador', 'Bibliotecario'}


def extraer_token_terminal_headers(req):
    """
    Obtiene el token de terminal exclusivamente desde las cabeceras HTTP o cookies de terminal.
    No acepta tokens desde parámetros de URL (query string).
    """
    token = req.headers.get('X-Terminal-Token')
    if not token:
        auth = req.headers.get('Authorization')
        if auth and auth.startswith('Bearer '):
            token = auth[7:].strip()
    if not token:
        token = req.cookies.get('terminal_auth_token') or req.cookies.get('X-Terminal-Token')
    return token.strip() if token else None


def contiene_token_en_url(req) -> bool:
    """
    Detecta si la URL (query string) contiene 'token' o 'terminal_token' en cualquier forma:
    vacío, repetido, mayúsculas, minúsculas, con o sin valor, antes o después de otros parámetros.
    """
    for key in req.args.keys():
        if key.strip().lower() in ('terminal_token', 'token'):
            return True
    try:
        raw_qs = req.query_string.decode('utf-8', errors='ignore').lower()
        if re.search(r'(^|[&?;])(terminal_token|token)(=|&|$)', raw_qs):
            return True
    except Exception:
        pass
    return False


def validar_autenticacion_terminal(req):
    """
    Verifica la identidad y autorización de la terminal o del operador que envía la solicitud.
    Retorna (autorizado: bool, sala_id_autorizada: Optional[int], sede_autorizada: Optional[str], error_msg: Optional[str], status_code: int)
    
    Reglas de autorización:
    1. Si se envía token en URL (query string), rechazar inmediatamente con HTTP 400.
    2. Si existe un usuario en sesión:
       - Su rol prevalece sobre cualquier token.
       - El rol 'Consultor' solo puede consultar; es rechazado con HTTP 403 con token válido, inválido o sin token.
       - Cualquier rol no perteneciente a ROLES_OPERADORES_PERMITIDOS es rechazado con HTTP 403.
       - Si es 'Supervisor', debe tener obligatoriamente una sede asignada; si falta o está vacía, rechaza con HTTP 403.
       - Si presenta además un token de terminal, este debe ser auténtico; un token inválido es rechazado con HTTP 401.
       - En flujo estándar de navegador (sin token), la sala efectiva proviene de session['terminal_sala_id'].
    3. Si es una terminal física autónoma con cabecera de token:
       - Debe presentar cabecera HTTP o cookie de terminal con token válido coincidente con TERMINAL_AUTH_TOKEN.
       - Debe existir TERMINAL_SALA_ID válida en el entorno; si falta o es inválida, falla cerrada con HTTP 403.
    4. Si es una sesión de terminal de biblioteca (escáner/kiosk de navegador sin login administrativo):
       - Debe contar con una sala previamente vinculada y autorizada en sesión ('terminal_sala_id' y 'escaneo_autorizado').
       - Si falta o es inválida, rechaza con HTTP 403.
    5. Sin sesión autorizada ni token de terminal válido, rechaza con HTTP 401 (no es ruta anónima).
    """
    # 1. Rechazo estricto de tokens en la URL (mantener fuera del historial y registros de acceso)
    if contiene_token_en_url(req):
        return False, None, None, 'Rechazado por seguridad: no se admiten tokens en la URL. Utilice la cabecera HTTP X-Terminal-Token.', 400

    admin_user = session.get('admin_user')
    admin_rol = session.get('admin_rol')
    token_header = extraer_token_terminal_headers(req)
    env_terminal_token = os.getenv('TERMINAL_AUTH_TOKEN')

    # 2. Si existe un usuario en sesión, su rol prevalece sobre cualquier token:
    if admin_user:
        if admin_rol == 'Consultor':
            return False, None, None, "Acceso denegado: el rol 'Consultor' solo puede consultar y no tiene permisos para operar el registro de ingresos.", 403

        if admin_rol not in ROLES_OPERADORES_PERMITIDOS:
            return False, None, None, f"Acceso denegado: el rol '{admin_rol}' no está autorizado para operar el registro de ingresos.", 403

        if admin_rol == 'Supervisor':
            sede_supervisor = session.get('admin_sede')
            if not sede_supervisor or not str(sede_supervisor).strip():
                return False, None, None, "Acceso denegado: el supervisor no tiene una sede asignada.", 403

        # Si el operador presenta además un token de terminal en la cabecera:
        if token_header:
            if not env_terminal_token or token_header != env_terminal_token:
                return False, None, None, 'Token de terminal inválido o no configurado en el servidor.', 401

            env_sala = os.getenv('TERMINAL_SALA_ID')
            if env_sala is None or not str(env_sala).strip() or not str(env_sala).strip().isdigit() or int(str(env_sala).strip()) <= 0:
                return False, None, None, 'Configuración de terminal incompleta: falta asignar la sala autorizada (TERMINAL_SALA_ID).', 403

            sala_autorizada_token = int(str(env_sala).strip())
            return True, sala_autorizada_token, session.get('admin_sede'), None, 200

        # Flujo estándar de navegador con sesión de operador (sin token):
        sala_sesion = session.get('terminal_sala_id') or session.get('escaneo_sala_id')
        if sala_sesion is None or isinstance(sala_sesion, bool) or not isinstance(sala_sesion, int) or sala_sesion <= 0:
            return False, None, None, 'Sesión de operador no vinculada a una sala válida. Abra primero la pantalla de sala autorizada.', 403

        sede_sesion = session.get('terminal_sede') or session.get('escaneo_sede')
        return True, sala_sesion, sede_sesion, None, 200

    # 3. Terminal autónoma sin sesión de operador:
    if token_header:
        if not env_terminal_token or token_header != env_terminal_token:
            return False, None, None, 'Token de terminal inválido o no configurado en el servidor.', 401

        env_sala = os.getenv('TERMINAL_SALA_ID')
        if env_sala is None or not str(env_sala).strip() or not str(env_sala).strip().isdigit() or int(str(env_sala).strip()) <= 0:
            return False, None, None, 'Configuración de terminal incompleta: falta asignar la sala autorizada (TERMINAL_SALA_ID).', 403

        sala_autorizada = int(str(env_sala).strip())
        return True, sala_autorizada, None, None, 200

    # 4. Terminal de navegador autorizada vinculada a sala (escáner sin login administrativo):
    if session.get('escaneo_autorizado'):
        sala_sesion = session.get('terminal_sala_id') or session.get('escaneo_sala_id')
        if sala_sesion is not None and not isinstance(sala_sesion, bool) and isinstance(sala_sesion, int) and sala_sesion > 0:
            sede_sesion = session.get('terminal_sede') or session.get('escaneo_sede')
            return True, sala_sesion, sede_sesion, None, 200
        return False, None, None, 'Sesión de terminal no vinculada a una sala válida. Abra primero la pantalla de sala autorizada.', 403

    # 5. Sin sesión válida ni token de terminal:
    return False, None, None, 'Terminal u operador no autenticado. Requiere sesión autorizada o cabecera X-Terminal-Token válida.', 401


@ingreso_bp.route('/')
def index():
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return "Error al conectar con la base de datos.", 500
        cursor = conn.cursor()
        cursor.execute("SELECT SalaID, NombreSala, Piso, Sede FROM Salas WHERE Activo = 1 ORDER BY Sede ASC, Piso ASC, NombreSala ASC")
        salas_db = cursor.fetchall()
        
        salas_agrupadas = {}
        for s in salas_db:
            if s.Sede not in salas_agrupadas:
                salas_agrupadas[s.Sede] = {}
            if s.Piso not in salas_agrupadas[s.Sede]:
                salas_agrupadas[s.Sede][s.Piso] = []
            salas_agrupadas[s.Sede][s.Piso].append({
                'SalaID': s.SalaID,
                'NombreSala': s.NombreSala
            })
            
        return render_template('index.html', salas_agrupadas=salas_agrupadas)
    except Exception as e:
        print(f"[INDEX ERROR] Fallo al listar salas: {type(e).__name__}")
        return "Error interno al cargar la lista de salas.", 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@ingreso_bp.route('/sala/<int:sala_id>')
def ingreso_sala(sala_id):
    # 1. Rechazo estricto de tokens en la URL
    if contiene_token_en_url(request):
        return """
        <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
            <h1 style='color: #dc2626;'>Error de Seguridad</h1>
            <p>Rechazado por seguridad: no se admiten tokens en la URL (query string).</p>
            <p>Configure la terminal utilizando la cabecera HTTP <code>X-Terminal-Token</code>.</p>
        </div>
        """, 400

    admin_user = session.get('admin_user')
    admin_rol = session.get('admin_rol')
    token_header = extraer_token_terminal_headers(request)
    env_token = os.getenv('TERMINAL_AUTH_TOKEN')

    # 2. Prioridad de políticas para usuario en sesión:
    # Un Consultor o rol no operador no puede acceder bajo ninguna circunstancia (con o sin token).
    if admin_user:
        if admin_rol == 'Consultor':
            return """
            <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                <p>El rol <b>Consultor</b> solo tiene permisos de consulta y no puede operar el escáner de ingresos.</p>
                <a href='/admin/carnets' style='color: #2563eb;'>Ir a Gestión de Carnets</a>
            </div>
            """, 403

        if admin_rol not in ROLES_OPERADORES_PERMITIDOS:
            return f"""
            <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                <p>El rol <b>{admin_rol}</b> no está autorizado para operar el escáner de ingresos.</p>
                <a href='/admin/login' style='color: #2563eb;'>Iniciar Sesión</a>
            </div>
            """, 403

        if admin_rol == 'Supervisor':
            sede_supervisor = session.get('admin_sede')
            if not sede_supervisor or not str(sede_supervisor).strip():
                return """
                <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                    <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                    <p>El operador supervisor no tiene una sede asignada. Contacte al administrador.</p>
                    <a href='/admin/dashboard' style='color: #2563eb;'>Volver al Panel</a>
                </div>
                """, 403

    # 3. Comprobación de credenciales:
    if token_header:
        if not env_token or token_header != env_token:
            return "ERROR: Token de terminal inválido.", 401

        env_sala = os.getenv('TERMINAL_SALA_ID')
        if env_sala is None or not str(env_sala).strip() or not str(env_sala).strip().isdigit() or int(str(env_sala).strip()) <= 0:
            return "ERROR: Configuración de terminal incompleta: falta asignar la sala autorizada (TERMINAL_SALA_ID).", 403

        sala_autorizada_token = int(str(env_sala).strip())
        if sala_id != sala_autorizada_token:
            return f"ERROR: Acceso denegado. Este token de terminal solo está autorizado para la sala {sala_autorizada_token}.", 403

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return "Error de conexión a la base de datos.", 500
        cursor = conn.cursor()
        
        # Obtener información de la sala solicitada
        cursor.execute("SELECT NombreSala, Piso, Sede, Activo FROM Salas WHERE SalaID = ?", (sala_id,))
        row = cursor.fetchone()
        
        if not row or not row.Activo:
            return "ERROR: Sala inactiva o no registrada en el sistema. Contacte Administración.", 404

        # Validar permisos de sede para rol Supervisor
        if admin_user and session.get('admin_rol') == 'Supervisor':
            sede_supervisor = session.get('admin_sede')
            if not sede_supervisor or not str(sede_supervisor).strip() or not row.Sede or str(row.Sede).strip().lower() != str(sede_supervisor).strip().lower():
                return f"""
                <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                    <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                    <p>El operador supervisor de la sede <b>{sede_supervisor}</b> no tiene permisos para operar salas de la sede <b>{row.Sede}</b>.</p>
                    <a href='/admin/dashboard' style='color: #2563eb;'>Volver al Panel</a>
                </div>
                """, 403

        # Si es sesión de terminal de biblioteca sin token de cabecera, asociar la sala a la sesión.
        # Si se presenta un token de terminal en cabeceras, no guardar en sesión una sala elegida libremente.
        if not token_header:
            session.permanent = True
            session['terminal_sala_id'] = sala_id
            session['terminal_sede'] = row.Sede
            session['terminal_operador'] = session.get('admin_user')
            session['terminal_rol'] = session.get('admin_rol')
            session['escaneo_autorizado'] = True
            session['escaneo_sala_id'] = sala_id
            session['escaneo_sede'] = row.Sede
            session.modified = True
            
        contador_inicial = 0
        try:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM RegistroIngresos 
                WHERE SalaID = ? AND CAST(FechaHora AS DATE) = CAST(SYSDATETIMEOFFSET() AT TIME ZONE 'SA Pacific Standard Time' AS DATE)
            """, (sala_id,))
            res = cursor.fetchone()
            if res:
                contador_inicial = res[0]
        except Exception as e:
            print(f"[CONTADOR INICIAL] Aviso: {type(e).__name__}")
            
        return render_template('ingreso.html', 
                               sala_id=sala_id, 
                               nombre_sala=row.NombreSala, 
                               piso=row.Piso, 
                               sede=row.Sede,
                               contador_inicial=contador_inicial)
    except Exception as e:
        print(f"[INGRESO SALA ERROR] Fallo: {type(e).__name__}")
        return "Error interno al abrir sala de ingreso.", 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@ingreso_bp.route('/filial/<sede>')
def filial(sede):
    # 1. Rechazo estricto de tokens en URL
    if contiene_token_en_url(request):
        return """
        <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
            <h1 style='color: #dc2626;'>Error de Seguridad</h1>
            <p>Rechazado por seguridad: no se admiten tokens en la URL (query string).</p>
        </div>
        """, 400

    admin_user = session.get('admin_user')
    admin_rol = session.get('admin_rol')

    if admin_user:
        if admin_rol == 'Consultor':
            return """
            <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                <p>El rol <b>Consultor</b> no tiene permisos para operar el escáner de ingresos.</p>
                <a href='/admin/carnets' style='color: #2563eb;'>Ir a Gestión de Carnets</a>
            </div>
            """, 403

        if admin_rol not in ROLES_OPERADORES_PERMITIDOS:
            return f"""
            <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                <p>El rol <b>{admin_rol}</b> no está autorizado para operar el escáner.</p>
            </div>
            """, 403

        if admin_rol == 'Supervisor':
            sede_usuario = session.get('admin_sede')
            if not sede_usuario or not str(sede_usuario).strip() or str(sede).strip().lower() != str(sede_usuario).strip().lower():
                return f"""
                <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                    <h1 style='color: #dc2626;'>Acceso Denegado</h1>
                    <p>No tiene permisos para operar la filial <b>{sede}</b>.</p>
                    <a href='/admin/dashboard' style='color: #2563eb;'>Volver al Panel</a>
                </div>
                """, 403

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return "Error de conexión a la base de datos.", 500
        cursor = conn.cursor()
        
        # Buscar la primera sala activa de esta filial
        cursor.execute("SELECT SalaID, NombreSala, Piso FROM Salas WHERE Sede = ? AND Activo = 1 ORDER BY Piso ASC, NombreSala ASC", (sede,))
        row = cursor.fetchone()
        
        if not row:
            return f"""
            <div style='font-family: sans-serif; padding: 40px; text-align: center;'>
                <h1 style='color: #dc2626;'>Acceso Bloqueado</h1>
                <p>No existe ninguna sala o ambiente configurado para la filial <b>{sede}</b>.</p>
                <p>Por favor, pídele al administrador que ingrese al Panel y cree la sala en "Gestión de Salas".</p>
                <a href='/' style='color: #2563eb;'>Volver al Menú Principal</a>
            </div>
            """, 404
            
        sala_id = row.SalaID
        nombre_sala = row.NombreSala
        piso = row.Piso

        session.permanent = True
        session['terminal_sala_id'] = sala_id
        session['terminal_sede'] = sede
        session['terminal_operador'] = admin_user
        session['terminal_rol'] = admin_rol
        session['escaneo_autorizado'] = True
        session['escaneo_sala_id'] = sala_id
        session['escaneo_sede'] = sede
        session.modified = True
            
        contador_inicial = 0
        try:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM RegistroIngresos 
                WHERE SalaID = ? AND CAST(FechaHora AS DATE) = CAST(SYSDATETIMEOFFSET() AT TIME ZONE 'SA Pacific Standard Time' AS DATE)
            """, (sala_id,))
            res = cursor.fetchone()
            if res:
                contador_inicial = res[0]
        except Exception as e:
            print(f"[CONTADOR INICIAL FILIAL] Aviso: {type(e).__name__}")

        return render_template('ingreso.html', 
                               sala_id=sala_id, 
                               nombre_sala=nombre_sala, 
                               piso=piso, 
                               sede=sede,
                               contador_inicial=contador_inicial)
    except Exception as e:
        print(f"[FILIAL ERROR] Fallo: {type(e).__name__}")
        return "Error interno al abrir filial.", 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# API: PROCESAR EL ESCANEO
@ingreso_bp.route('/procesar_ingreso', methods=['POST'])
def procesar_ingreso():
    # 1. Autorización estricta de terminal / operador
    auth_ok, sala_auth, sede_auth, auth_err, auth_status = validar_autenticacion_terminal(request)
    if not auth_ok:
        return jsonify({'status': 'error', 'msg': auth_err or 'Terminal no autorizada'}), auth_status

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return jsonify({'status': 'error', 'msg': 'Solicitud inválida: cuerpo JSON requerido'}), 400

    codigo = data.get('codigo')
    if codigo is None:
        return jsonify({'status': 'error', 'msg': 'Código de matrícula o DNI requerido'}), 400

    # Rechazar booleanos y objetos arbitrarios sin convertirlos ciegamente a texto
    if isinstance(codigo, bool) or not isinstance(codigo, (str, int)):
        return jsonify({'status': 'error', 'msg': 'Formato de código inválido: debe ser texto o número'}), 400

    codigo_str = str(codigo).strip()
    
    # Prevenir inputs basura del escáner
    if not codigo_str or codigo_str.lower() in ('none', 'null', '0', '0.0', 'undefined') or codigo_str.replace('-', '').strip() == '':
        return jsonify({'status': 'error', 'msg': 'Posible Lectura Errónea del Escáner'})

    if len(codigo_str) < 4 or len(codigo_str) > 30:
        return jsonify({'status': 'error', 'msg': 'Longitud de código fuera de rango admitido'})

    if not re.match(r'^[A-Za-z0-9\-]+$', codigo_str):
        return jsonify({'status': 'error', 'msg': 'Código con caracteres no permitidos'})

    # 2. La sala efectiva proviene ESTRICTAMENTE de la identidad autorizada (sala_auth)
    # Si la configuración es incompleta o ambigua, falla cerrada sin asumir sala 1
    if sala_auth is None or isinstance(sala_auth, bool) or not isinstance(sala_auth, int) or sala_auth <= 0:
        return jsonify({'status': 'error', 'msg': 'No se pudo determinar la sala autorizada para la terminal'}), 403

    sala_id_efectiva = sala_auth

    # Si el cliente intenta enviar 'sala_id' en el payload, verificar estrictamente sus tipos
    sala_id_payload = data.get('sala_id')
    if sala_id_payload is not None:
        if isinstance(sala_id_payload, bool):
            return jsonify({'status': 'error', 'msg': 'Identificador de sala inválido: no se admiten booleanos'}), 400
        if isinstance(sala_id_payload, float):
            return jsonify({'status': 'error', 'msg': 'Identificador de sala inválido: no se admiten decimales'}), 400
        if isinstance(sala_id_payload, str):
            if not sala_id_payload.strip().isdigit():
                return jsonify({'status': 'error', 'msg': 'Identificador de sala inválido: debe ser un número entero'}), 400
            sala_id_solicitada = int(sala_id_payload.strip())
        elif isinstance(sala_id_payload, int):
            sala_id_solicitada = sala_id_payload
        else:
            return jsonify({'status': 'error', 'msg': 'Identificador de sala inválido'}), 400

        # Si el payload intenta cambiar o no coincide con la sala autorizada, rechazar con HTTP 403
        if sala_id_solicitada != sala_id_efectiva:
            return jsonify({'status': 'error', 'msg': 'La sala indicada en el payload no coincide con la sala autorizada para la terminal'}), 403

    # 3. La sede efectiva proviene de la sala autorizada, nunca de la confianza en el cliente
    sede_payload = data.get('sede')
    if sede_payload is not None:
        if not isinstance(sede_payload, str):
            return jsonify({'status': 'error', 'msg': 'Formato de sede inválido'}), 400

    # 4. Validar que la sala exista, esté activa y resolver su sede real en la BD
    es_sala_valida, msg_sala, datos_sala = validar_sala_activa(sala_id_efectiva, sede=sede_auth)
    if not es_sala_valida:
        return jsonify({'status': 'error', 'msg': msg_sala})

    sede_real = datos_sala['sede'] if datos_sala else sede_auth

    # Si el payload envió sede y difiere de la sede real autorizada, rechazar con HTTP 403
    if sede_payload is not None and sede_real:
        if sede_payload.strip().lower() != str(sede_real).strip().lower():
            return jsonify({'status': 'error', 'msg': 'La sede indicada en el payload no coincide con la sede autorizada para la sala'}), 403

    # Si es operador con rol Supervisor, re-verificar permisos sobre la sede
    if session.get('admin_rol') == 'Supervisor':
        sede_supervisor = session.get('admin_sede')
        if not sede_supervisor or not str(sede_supervisor).strip():
            return jsonify({'status': 'error', 'msg': 'Acceso denegado: el supervisor no tiene una sede asignada'}), 403
        if not sede_real or str(sede_real).strip().lower() != str(sede_supervisor).strip().lower():
            return jsonify({'status': 'error', 'msg': 'Acceso denegado: el supervisor no tiene permisos para operar salas de esta sede'}), 403

    res = registrar_ingreso_general(codigo_str, sala_id_efectiva, sede=sede_real)
    return jsonify(res)


# --- RUTAS DE EVENTOS ---
@ingreso_bp.route('/api/eventos_activos', methods=['GET'])
def api_eventos_activos():
    sede = request.args.get('sede', 'Central')
    eventos = obtener_agenda_eventos_hoy(sede)
    if eventos and len(eventos) > 0:
        return jsonify({'status': 'success', 'eventos_activos': True, 'eventos': eventos})
    return jsonify({'status': 'success', 'eventos_activos': False, 'eventos': []})


@ingreso_bp.route('/api/evento_estado/<int:evento_id>', methods=['GET'])
def api_evento_estado(evento_id):
    estado = verificar_estado_evento(evento_id)
    return jsonify(estado)


@ingreso_bp.route('/evento/<int:evento_id>')
def ingreso_evento(evento_id):
    sede_evento = obtener_sede_evento(evento_id)
    return render_template('ingreso.html', piso="EVENTO", sede=sede_evento, evento_id=evento_id)


@ingreso_bp.route('/procesar_evento_ingreso', methods=['POST'])
def procesar_evento():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'status': 'error', 'msg': 'Datos inválidos o faltantes'}), 400

    codigo = data.get('codigo')
    evento_id = data.get('evento_id')
    
    if not codigo or str(codigo).strip() in ['0', '0.0', ''] or not evento_id: 
        return jsonify({'status': 'error', 'msg': 'Datos inválidos o faltantes'})
        
    res = procesar_ingreso_evento(str(codigo).strip(), evento_id)
    return jsonify(res)