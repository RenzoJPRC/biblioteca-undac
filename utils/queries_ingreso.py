import requests
from db import get_db_connection

BASE_API_UNDAC_URL = "http://api.undac.edu.pe/tasks/a3945a7384cbdcd33f49e8f5b8ec29f5/91f33e2776c526b9cca723a63476f028"

def auto_registrar_alumno_api_undac(codigo):
    """
    Consulta en tiempo real a la API REST central de la UNDAC si un código de estudiante no existe localmente.
    Si la API institucional confirma la validez del alumno, lo inserta automáticamente en la tabla Alumnos.
    """
    try:
        url = f"{BASE_API_UNDAC_URL}/{str(codigo).strip()}"
        resp = requests.get(url, timeout=4)
        if resp.status_code != 200:
            return False, None
            
        data = resp.json()
        if "message" in data or not data.get('Nombres'):
            return False, None
            
        nombres = data.get('Nombres', '').strip()
        ap_paterno = data.get('Apellido paterno', '').strip()
        ap_materno = data.get('Apellido materno', '').strip()
        nombre_completo = f"{ap_paterno} {ap_materno}, {nombres}".strip(" ,")
        dni = data.get('Dni', '').strip()
        facultad = data.get('Programa facultad', '').strip()
        
        conn = get_db_connection()
        if not conn:
            return False, None
            
        cursor = conn.cursor()
        
        # Verificar que no exista por si acaso antes de insertar
        cursor.execute("SELECT AlumnoID FROM Alumnos WHERE CodigoMatricula = ? OR (DNI = ? AND len(DNI) > 4)", (codigo, dni))
        if cursor.fetchone():
            conn.close()
            return True, nombre_completo

        cursor.execute("""
            INSERT INTO Alumnos (NombreCompleto, DNI, CodigoMatricula, Escuela, Estado)
            VALUES (?, ?, ?, ?, 1)
        """, (nombre_completo, dni, str(codigo).strip(), facultad))
        conn.commit()
        conn.close()
        print(f"[API UNDAC AUTO-SYNC] Alumno {codigo} ({nombre_completo}) registrado automáticamente en tiempo real.")
        return True, nombre_completo
    except Exception as e:
        print(f"[API UNDAC AUTO-SYNC ERROR] {e}")
        return False, None


import threading
from utils.telegram_notify import enviar_notificacion_ingreso_telegram

def _notificar_telegram_async(nombre, sala_id, conn_cursor=None):
    """Obtiene el piso y la sala para enviar la notificación a Telegram sin demorar la respuesta web."""
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT NombreSala, Piso FROM Salas WHERE SalaID = ?", (sala_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                nombre_sala = row[0]
                piso = row[1]
                enviar_notificacion_ingreso_telegram(nombre, piso, nombre_sala)
    except Exception as e:
        print(f"[TELEGRAM THREAD ERROR] {e}")


def registrar_ingreso_general(codigo, sala_id):
    """
    Ejecuta el stored procedure sp_RegistrarIngreso y retorna el resultado
    en un formato de diccionario que el endpoint espera. Integrado con Auto-Sync API UNDAC y Telegram Notify.
    """
    conn = get_db_connection()
    if not conn: 
        return {'status': 'error', 'msg': 'Error BD'}

    try:
        cursor = conn.cursor()
        sql = """
        DECLARE @out_msg nvarchar(250);
        DECLARE @out_nombre nvarchar(250);
        DECLARE @out_escuela nvarchar(200);
        DECLARE @out_semestre nvarchar(40);
        
        -- Ejecutamos el procedimiento enviando Codigo y SalaID
        EXEC sp_RegistrarIngreso ?, ?, @out_msg OUTPUT, @out_nombre OUTPUT, @out_escuela OUTPUT, @out_semestre OUTPUT;
        
        SELECT @out_msg, @out_nombre, @out_escuela, @out_semestre;
        """
        cursor.execute(sql, (codigo, sala_id))
        row = cursor.fetchone()
        conn.commit()
        
        if row:
            mensaje = row[0]
            nombre = row[1]
            escuela = row[2]
            semestre = row[3]

            # --- AUTO-PROVISIONING DESDE LA API REST DE LA UNDAC SI NO EXISTE EN LA BD LOCAL ---
            if 'NO ENCONTRADO' in mensaje or 'USUARIO NO ENCONTRADO' in mensaje:
                exito_sync, nombre_sync = auto_registrar_alumno_api_undac(codigo)
                if exito_sync:
                    # Reintentar ejecutar el Stored Procedure inmediatamente
                    cursor.execute(sql, (codigo, sala_id))
                    row_retry = cursor.fetchone()
                    conn.commit()
                    if row_retry:
                        mensaje = row_retry[0]
                        nombre = row_retry[1]
                        escuela = row_retry[2]
                        semestre = row_retry[3]
                        if 'CONCEDIDO' in mensaje or 'NUEVO INGRESO' in mensaje:
                            # Notificar en segundo plano a Telegram
                            threading.Thread(target=_notificar_telegram_async, args=(nombre, sala_id), daemon=True).start()

                            return {
                                'status': 'success', 
                                'msg': f"{mensaje} (Autenticado vía API UNDAC)", 
                                'warning': None,
                                'alumno': nombre, 
                                'escuela': escuela, 
                                'semestre': semestre
                            }

            if 'CONCEDIDO' in mensaje or 'NUEVO INGRESO' in mensaje: 
                warning_type = None
                if 'VENCIDO' in mensaje or 'CARNET VENCIDO' in mensaje:
                    warning_type = 'carnet_vencido'

                # Notificar en segundo plano a Telegram
                threading.Thread(target=_notificar_telegram_async, args=(nombre, sala_id), daemon=True).start()

                return {
                    'status': 'success', 
                    'msg': mensaje, 
                    'warning': warning_type,
                    'alumno': nombre, 
                    'escuela': escuela, 
                    'semestre': semestre
                }
            
            elif 'YA REGISTRADO' in mensaje or 'YA ESTÁS REGISTRADO' in mensaje:
                return {
                    'status': 'warning', 
                    'msg': mensaje, 
                    'alumno': nombre, 
                    'escuela': escuela, 
                    'semestre': semestre
                }

            else:
                return {'status': 'error', 'msg': mensaje}
        
        return {'status': 'error', 'msg': 'Error desconocido en BD'}

    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals() and conn:
            conn.close()

