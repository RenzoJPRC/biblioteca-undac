import pandas as pd
import io
from db import get_db_connection
from utils.validaciones import verificar_dni_global, formatear_nombre_estetico
from utils.task_manager import update_task_progress, finish_task

def buscar_personal_administrativo(query, page, limit=20):
    offset = (page - 1) * limit
    conn = get_db_connection()
    cursor = conn.cursor()
    
    params = []
    where_clause = ""
    
    if query:
        where_clause = "WHERE ApellidosNombres LIKE ? OR DNI LIKE ? OR Oficina LIKE ?"
        p = f"%{query}%"
        params = [p, p, p]
    
    count_sql = f"SELECT COUNT(*) FROM PersonalAdministrativo {where_clause}"
    cursor.execute(count_sql, params)
    total_items = cursor.fetchone()[0]
    total_pages = (total_items + limit - 1) // limit

    data_sql = f"""
    SELECT PersonalID, ApellidosNombres, DNI, Oficina, CorreoPersonal, CorreoInstitucional, Telefono
    FROM PersonalAdministrativo 
    {where_clause}
    ORDER BY ApellidosNombres
    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """
    
    full_params = params + [offset, limit]
    cursor.execute(data_sql, full_params)
    rows = cursor.fetchall()
    conn.close()
    
    resultados = []
    for r in rows:
        resultados.append({
            'id': r[0],
            'nombre': r[1],
            'dni': r[2],
            'oficina': r[3],
            'correo_personal': r[4] if r[4] else '',
            'correo_inst': r[5] if r[5] else '',
            'telefono': r[6] if r[6] else ''
        })
    
    return {
        'data': resultados,
        'pagination': {
            'page': page,
            'limit': limit,
            'total_items': total_items,
            'total_pages': total_pages
        }
    }

def guardar_personal_administrativo(data):
    personal_id = data.get('id')
    nombre = data.get('nombre')
    dni = data.get('dni')
    oficina = data.get('oficina')
    correo_personal = data.get('correo_personal')
    correo_inst = data.get('correo_inst')
    telefono = data.get('telefono')

    if not all([nombre, dni]):
        return {'status': 'error', 'msg': 'Faltan campos obligatorios (DNI y Nombre)'}

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Validación Global
        err_bool, err_msg = verificar_dni_global(dni, ignora_tabla='PersonalAdministrativo', ignora_id=personal_id, cursor=cursor)
        if err_bool:
            return {'status': 'error', 'msg': err_msg}

        if personal_id:
            cursor.execute("""
                UPDATE PersonalAdministrativo 
                SET ApellidosNombres=?, DNI=?, Oficina=?, CorreoPersonal=?, CorreoInstitucional=?, Telefono=?
                WHERE PersonalID=?
            """, (nombre, dni, oficina, correo_personal, correo_inst, telefono, personal_id))
        else:
            cursor.execute("""
                INSERT INTO PersonalAdministrativo (ApellidosNombres, DNI, Oficina, CorreoPersonal, CorreoInstitucional, Telefono)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (nombre, dni, oficina, correo_personal, correo_inst, telefono))
            
        conn.commit()
        return {'status': 'success', 'msg': 'Personal Administrativo guardado correctamente'}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals(): conn.close()
        
def borrar_personal_administrativo(id_per):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Eliminar registros relacionados
        cursor.execute("DELETE FROM RegistroIngresos WHERE PersonalID = ?", (id_per,))
        
        # Eliminar el personal
        cursor.execute("DELETE FROM PersonalAdministrativo WHERE PersonalID = ?", (id_per,))
        
        conn.commit()
        return {'status': 'success', 'msg': 'Personal eliminado permanentemente.'}
    except Exception as e:
        return {'status': 'error', 'msg': f"No se pudo eliminar: {str(e)}"}
    finally:
        if 'conn' in locals(): conn.close()

def procesar_excel_personal_async(file_bytes, task_id):
    conn = get_db_connection()
    errores = []
    contador = 0
    
    try:
        update_task_progress(task_id, 0, msg="Leyendo archivo Excel de Personal...")
        # Leemos garantizando texto puro
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        df = df.fillna('')
        df.columns = df.columns.astype(str).str.strip().str.upper()
        
        total_filas = len(df)
        update_task_progress(task_id, 0, total=total_filas, msg=f"Validando cabeceras y preparando {total_filas} registros...")
        
        cursor = conn.cursor()
        
        for idx, row in df.iterrows():
            fila_num = idx + 2 # +2 por cabecera y base 0
            
            dni = str(row.get('DNI', '')).strip()
            if dni.endswith('.0'): dni = dni[:-2]
            
            # Restaurar ceros a la izquierda borrados por Excel numérico
            if dni.isdigit() and dni != '0' and len(dni) > 0 and len(dni) < 8:
                dni = dni.zfill(8)
                
            # Prevenir colisiones de DNIs fantasmas
            if dni == '0' or dni == '0.0':
                dni = ''

            # Mapeo universal en mayúsculas
            nombre_raw = str(row.get('APELLIDOS Y NOMBRES', row.get('NOMBRE COMPLETO', row.get('APELLIDOS Y NOMBRE', '')))).strip()
            nombre = formatear_nombre_estetico(nombre_raw)
            
            oficina = str(row.get('OFICINA', '')).strip()
            c_inst = str(row.get('CORREO INSTITUCIONAL', '')).strip()
            c_per = str(row.get('CORREO PERSONAL', row.get('CORREO', ''))).strip()
            
            telefono = str(row.get('NUMERO DE CELULAR', row.get('NÚMERO DE CELULAR', row.get('TELÉFONO', row.get('TELEFONO', row.get('CELULAR', '')))))).strip()
            if telefono.endswith('.0'): telefono = telefono[:-2]
            
            if not nombre: 
                errores.append(f"Fila {fila_num}: Celda de nombre vacía.")
                continue

            # --- VALIDACIÓN GLOBAL ---
            skip_row = False
            if dni not in ['0', '', '0.0'] and len(dni) >= 5:
                err_bool, msg_error = verificar_dni_global(dni, ignora_tabla='PersonalAdministrativo', cursor=cursor)
                if err_bool: 
                    errores.append(f"Fila {fila_num}: {msg_error}")
                    skip_row = True
            else:
                errores.append(f"Fila {fila_num}: Falta DNI válido.")
                skip_row = True
                
            if not skip_row:
                cursor.execute("SELECT PersonalID FROM PersonalAdministrativo WHERE DNI = ?", (dni,))
                if cursor.fetchone():
                    cursor.execute("""
                        UPDATE PersonalAdministrativo 
                        SET ApellidosNombres=?, Oficina=?, CorreoInstitucional=?, CorreoPersonal=?, Telefono=? 
                        WHERE DNI=?
                    """, (nombre, oficina, c_inst, c_per, telefono, dni))
                else:
                    cursor.execute("""
                        INSERT INTO PersonalAdministrativo (ApellidosNombres, DNI, Oficina, CorreoInstitucional, CorreoPersonal, Telefono) 
                        VALUES (?,?,?,?,?,?)
                    """, (nombre, dni, oficina, c_inst, c_per, telefono))
                contador += 1
                
                if contador % 500 == 0:
                    conn.commit()
            
            if idx % 50 == 0:
                print(f"-> Procesados {idx} registros de personal...")
                update_task_progress(task_id, idx, total=total_filas, msg=f"Guardando en BD: {idx} de {total_filas}...")
            
        conn.commit()
        msg = f'Procesados {contador} de {total_filas} registros de personal con éxito.'
        if errores:
            detalles = "<br> • ".join(errores[:5])
            if len(errores) > 5: detalles += f"<br> • ... y {len(errores)-5} más."
            msg += f'<div class="mt-2 text-xs text-rose-600 bg-rose-50 p-2 rounded border border-rose-200"><p class="font-bold mb-1">Filas omitidas ({len(errores)}):</p> • {detalles}</div>'
            if contador == 0:
                finish_task(task_id, success=False, msg=msg)
                return
                
        finish_task(task_id, success=True, msg=msg)
        
    except Exception as e:
        finish_task(task_id, success=False, msg=f"Error fatal al procesar: {str(e)}")
    finally:
        conn.close()

def eliminar_personal_masivo(ids):
    if not ids:
        return {'status': 'error', 'msg': "No hay IDs para eliminar"}
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        placeholders = ','.join(['?'] * len(ids))
        
        # Eliminar registros de ingreso asociados primero
        cursor.execute(f"DELETE FROM RegistroIngresos WHERE PersonalID IN ({placeholders})", ids)
        
        # Eliminar personal
        cursor.execute(f"DELETE FROM PersonalAdministrativo WHERE PersonalID IN ({placeholders})", ids)
        
        conn.commit()
        return {'status': 'success', 'msg': f"{len(ids)} registros de personal eliminados exitosamente."}
    except Exception as e:
        return {'status': 'error', 'msg': f"Error al eliminar en bloque: {str(e)}"}
    finally:
        if 'conn' in locals():
            conn.close()

def vaciar_personal_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Eliminar primero las dependencias de visitas vinculadas
        cursor.execute("DELETE FROM RegistroIngresos WHERE PersonalID IS NOT NULL")
        
        # Luego truncamos o vaciamos la tabla principal
        cursor.execute("DELETE FROM PersonalAdministrativo")
        
        # Opcional: Reiniciar el identity (contador de IDs) a 0.
        cursor.execute("DBCC CHECKIDENT ('PersonalAdministrativo', RESEED, 0)")
        
        conn.commit()
        return {'status': 'success', 'msg': "La tabla de Personal Administrativo ha sido VACIADA permanentemente."}
    except Exception as e:
        return {'status': 'error', 'msg': f"Error crítico al vaciar tabla: {str(e)}"}
    finally:
        if 'conn' in locals():
            conn.close()
