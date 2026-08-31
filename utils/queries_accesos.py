from db import get_db_connection

def get_all_usuarios():
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT UsuarioID, Usuario, Email, Rol, SedeAsignada, Activo, FORMAT(CreadoEn, 'dd/MM/yyyy') FROM UsuariosSistema")
        return cursor.fetchall()
    except Exception:
        return None
    finally:
        conn.close()

def db_crear_usuario(usuario, email, hash_pw, rol, sede):
    conn = get_db_connection()
    if not conn: return False, "Error BD"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM UsuariosSistema WHERE Usuario = ?", (usuario,))
        if cursor.fetchone():
            return False, f"El usuario {usuario} ya existe."
            
        sql = """
        INSERT INTO UsuariosSistema (Usuario, PasswordHash, Rol, SedeAsignada, Email) 
        VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(sql, (usuario, hash_pw, rol, sede, email))
        conn.commit()
        return True, "OK"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def db_eliminar_usuario(user_id):
    conn = get_db_connection()
    if not conn: return False, "Error BD"
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM UsuariosSistema WHERE UsuarioID = ?", (user_id,))
        conn.commit()
        return True, "OK"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def check_admin_auth(usuario_current):
    # Retrieve current admin info
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT PasswordHash, Rol FROM UsuariosSistema WHERE Usuario = ?", (usuario_current,))
        return cursor.fetchone()
    except Exception:
        return None
    finally:
        conn.close()

def db_actualizar_gestor(id_target, updates, params):
    # Updates is a list of strings like "Usuario = ?"
    conn = get_db_connection()
    if not conn: return False, "Error BD"
    try:
        cursor = conn.cursor()
        sql = f"UPDATE UsuariosSistema SET {', '.join(updates)} WHERE UsuarioID = ?"
        params.append(id_target)
        cursor.execute(sql, tuple(params))
        conn.commit()
        return True, "OK"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()
