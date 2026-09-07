from datetime import datetime

def calcular_semestre_undac(codigo, fecha_ingreso_api=None, semestre_raw=None):
    """Calcula el semestre académico numérico/texto según el código o año de ingreso."""
    if semestre_raw and str(semestre_raw).strip() and str(semestre_raw).strip().lower() not in ('none', 'null', 'regular', '', '0'):
        return str(semestre_raw).strip()
    
    año_ingreso = None
    if fecha_ingreso_api and str(fecha_ingreso_api).isdigit():
        año_ingreso = int(fecha_ingreso_api)
    elif codigo and len(str(codigo).strip()) >= 8:
        prefijo = str(codigo).strip()[:2]
        if prefijo.isdigit():
            val = int(prefijo)
            if 15 <= val <= 26:
                año_ingreso = 2000 + val
    
    if año_ingreso:
        año_actual = datetime.now().year
        diff = año_actual - año_ingreso
        num_semestre = max(1, min(10, (diff * 2) + 2))
        return str(num_semestre)
    
    return '10'


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
        
        semestre_val = calcular_semestre_undac(codigo, data.get('Fecha de Ingreso'), data.get('Semestre'))
        
        conn = get_db_connection()
        if not conn:
            return False, None
            
        cursor = conn.cursor()
        
        # Verificar que no exista por si acaso antes de insertar
        cursor.execute("SELECT AlumnoID FROM Alumnos WHERE CodigoMatricula = ? OR (DNI = ? AND len(DNI) > 4)", (codigo, dni))
        row_ex = cursor.fetchone()
        if row_ex:
            cursor.execute("UPDATE Alumnos SET Semestre = ? WHERE AlumnoID = ? AND (Semestre IS NULL OR Semestre = '' OR Semestre = 'Regular')", (semestre_val, row_ex[0]))
            conn.commit()
            conn.close()
            return True, nombre_completo

        cursor.execute("""
            INSERT INTO Alumnos (NombreCompleto, DNI, CodigoMatricula, Escuela, Semestre, Estado)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (nombre_completo, dni, str(codigo).strip(), facultad, semestre_val))
        conn.commit()
        conn.close()
        print(f"[API UNDAC AUTO-SYNC] Alumno {codigo} ({nombre_completo}) registrado automáticamente en tiempo real con Semestre '{semestre_val}'.")
        return True, nombre_completo
    except Exception as e:
        print(f"[API UNDAC AUTO-SYNC ERROR] {e}")
        return False, None


def registrar_ingreso_general(codigo, sala_id):
    """
    Ejecuta el stored procedure sp_RegistrarIngreso y retorna el resultado
    en un formato de diccionario que el endpoint espera. Integrado con Auto-Sync API UNDAC.
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
                            if not semestre or str(semestre).strip().lower() in ('none', 'null', '', 'regular'):
                                semestre = calcular_semestre_undac(codigo)
                            return {
                                'status': 'success', 
                                'msg': f"{mensaje} (Autenticado vía API UNDAC)", 
                                'warning': None,
                                'alumno': nombre, 
                                'escuela': escuela, 
                                'semestre': semestre
                            }

            if not semestre or str(semestre).strip().lower() in ('none', 'null', '', 'regular'):
                semestre = calcular_semestre_undac(codigo)
                try:
                    cursor.execute("UPDATE Alumnos SET Semestre = ? WHERE CodigoMatricula = ? OR DNI = ?", (semestre, codigo, codigo))
                    conn.commit()
                except Exception:
                    pass

            if 'CONCEDIDO' in mensaje or 'NUEVO INGRESO' in mensaje: 
                warning_type = None
                if 'VENCIDO' in mensaje or 'CARNET VENCIDO' in mensaje:
                    warning_type = 'carnet_vencido'

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

