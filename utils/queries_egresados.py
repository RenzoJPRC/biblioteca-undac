from db import get_db_connection
from utils.validaciones import verificar_dni_global, formatear_nombre_estetico
import pandas as pd
import io
from utils.task_manager import update_task_progress, finish_task
import functools

@functools.lru_cache(maxsize=128)
def buscar_egresados_paginados(query, page, limit):
    offset = (page - 1) * limit
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    params = []
    where_clause = ""
    
    if query:
        where_clause = "WHERE NombreCompleto LIKE ? OR DNI LIKE ? OR CodigoMatricula LIKE ?"
        p = f"%{query}%"
        params = [p, p, p]
    
    count_sql = f"SELECT COUNT(*) FROM Egresados {where_clause}"
    cursor.execute(count_sql, params)
    total_items = cursor.fetchone()[0]
    total_pages = (total_items + limit - 1) // limit

    data_sql = f"""
    SELECT EgresadoID, NombreCompleto, DNI, CodigoMatricula, Facultad, EscuelaProfesional, CorreoPersonal, CorreoInstitucional, Celular, Estado
    FROM Egresados 
    {where_clause}
    ORDER BY NombreCompleto
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
            'codigo': r[3],
            'facultad': r[4],
            'escuela': r[5],
            'correo_personal': r[6] if r[6] else '',
            'correo_inst': r[7] if r[7] else '',
            'celular': r[8] if r[8] else '',
            'estado': 'ACTIVO' if r[9] else 'INACTIVO'
        })
    
    return resultados, total_items, total_pages


def guardar_egresado_individual(data):
    buscar_egresados_paginados.cache_clear()
    egresado_id = data.get('id')
    nombre = data.get('nombre')
    dni = data.get('dni')
    codigo = data.get('codigo')
    facultad = data.get('facultad')
    escuela = data.get('escuela')
    correo_personal = data.get('correo_personal')
    correo_inst = data.get('correo_inst')
    celular = data.get('celular')

    if not all([nombre, dni, codigo, facultad, escuela]):
        return False, 'Faltan campos obligatorios'

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # --- VALIDACIÓN GLOBAL DE DNI ---
        err_bool, err_msg = verificar_dni_global(dni, ignora_tabla='Egresados', ignora_id=egresado_id)
        if err_bool:
            conn.close()
            return False, err_msg
        # --------------------------------

        if egresado_id:
            cursor.execute("""
                UPDATE Egresados 
                SET NombreCompleto=?, DNI=?, CodigoMatricula=?, Facultad=?, EscuelaProfesional=?, CorreoPersonal=?, CorreoInstitucional=?, Celular=?
                WHERE EgresadoID=?
            """, (nombre, dni, codigo, facultad, escuela, correo_personal, correo_inst, celular, egresado_id))
        else:
            cursor.execute("""
                INSERT INTO Egresados (NombreCompleto, DNI, CodigoMatricula, Facultad, EscuelaProfesional, CorreoPersonal, CorreoInstitucional, Celular, Estado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (nombre, dni, codigo, facultad, escuela, correo_personal, correo_inst, celular))
            
        conn.commit()
        return True, 'Egresado guardado correctamente'
    except Exception as e:
        return False, str(e)
    finally:
        if 'conn' in locals():
            conn.close()


def procesar_excel_egresados_async(file_bytes, task_id):
    buscar_egresados_paginados.cache_clear()
    conn = get_db_connection()
    contador = 0
    errores = []
    
    try:
        update_task_progress(task_id, 0, msg="Leyendo archivo Excel...")
        
        # Leemos garantizando que todos los datos se procesen como texto puro
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        df = df.fillna('')
        
        # Limpiamos los espacios en blanco y forzamos mayúsculas
        df.columns = df.columns.astype(str).str.strip().str.upper()
        
        total_filas = len(df)
        update_task_progress(task_id, 0, total=total_filas, msg=f"Validando cabeceras y preparando {total_filas} registros...")
        
        cursor = conn.cursor()
        
        for index, row in df.iterrows():
            num_fila = index + 2
            
            # Extraer DNI y limpiar decimales (.0) y espacios
            dni = str(row.get('DNI', '')).strip()
            if dni.endswith('.0'): 
                dni = dni[:-2]
                
            # Restaurar ceros a la izquierda borrados por Excel numérico
            if dni.isdigit() and dni != '0' and len(dni) > 0 and len(dni) < 8:
                dni = dni.zfill(8)
                
            # Prevenir colisiones de DNIs fantasmas
            if dni == '0' or dni == '0.0':
                dni = ''

            # Mapear a las columnas aceptando nombres en mayúsculas
            nombre_raw = str(row.get('APELLIDOS Y NOMBRES', row.get('APELLIDOS Y NOMBRE', row.get('NOMBRE COMPLETO', '')))).strip()
            nombre = formatear_nombre_estetico(nombre_raw)
            
            codigo = str(row.get('CÓDIGO MATRÍCULA', row.get('CODIGO DE MATRICULA', row.get('CODIGO', '')))).strip()
            if codigo.endswith('.0'): 
                codigo = codigo[:-2]
                
            facultad = str(row.get('FACULTAD', '')).strip()
            escuela = str(row.get('ESCUELA PROFESIONAL', row.get('ESCUELA', ''))).strip()
            
            c_personal = str(row.get('CORREO PERSONAL', row.get('CORREO', ''))).strip()
            c_institucional = str(row.get('CORREO INSTITUCIONAL', '')).strip()
            celular = str(row.get('CELULAR', '')).strip()
            if celular.endswith('.0'):
                celular = celular[:-2]
            
            # Si la fila no tiene nombre, la saltamos guardando el error
            if not nombre: 
                errores.append(f"Fila {num_fila}: Celda de nombre vacía.")
                pass
            else:
                # --- VALIDACIÓN GLOBAL ---
                skip_row = False
                if dni not in ['0', '', '0.0'] and len(dni) >= 5:
                    err_bool, msg_valid = verificar_dni_global(dni, ignora_tabla='Egresados', cursor=cursor)
                    if err_bool: 
                        errores.append(f"Fila {num_fila}: {msg_valid} - DNI {dni}")
                        skip_row = True
                elif not codigo:
                    errores.append(f"Fila {num_fila}: DNI y Código ausentes o inválidos.")
                    skip_row = True
                
                if not skip_row:
                    # Buscar si el egresado ya existe usando el Código de Matrícula
                    cursor.execute("SELECT EgresadoID FROM Egresados WHERE CodigoMatricula = ? AND CodigoMatricula != ''", (codigo,))
                    existe = cursor.fetchone()
                    
                    # Si no existe por código, intentamos por DNI
                    if not existe and dni not in ['0', '', '0.0']:
                        cursor.execute("SELECT EgresadoID FROM Egresados WHERE DNI = ?", (dni,))
                        existe = cursor.fetchone()
        
                    if existe:
                        cursor.execute("""
                            UPDATE Egresados 
                            SET NombreCompleto=?, CodigoMatricula=?, Facultad=?, EscuelaProfesional=?, DNI=?, CorreoPersonal=?, CorreoInstitucional=?, Celular=?, Estado=1 
                            WHERE EgresadoID=?
                        """, (nombre, codigo, facultad, escuela, dni, c_personal, c_institucional, celular, existe[0]))
                    else:
                        cursor.execute("""
                            INSERT INTO Egresados (NombreCompleto, CodigoMatricula, Facultad, EscuelaProfesional, DNI, CorreoPersonal, CorreoInstitucional, Celular, Estado) 
                            VALUES (?,?,?,?,?,?,?,?,1)
                        """, (nombre, codigo, facultad, escuela, dni, c_personal, c_institucional, celular))
                    
                    contador += 1
                    
                    if contador % 500 == 0:
                        conn.commit()
            
            # Update progress every 50 records
            if index % 50 == 0:
                print(f"-> Procesados {index} egresados...")
                update_task_progress(task_id, index, total=total_filas, msg=f"Guardando en BD: {index} de {total_filas}...")
            
        conn.commit()
        
        msg = f'Procesados {contador} de {total_filas} egresados con éxito.'
        if errores:
            detalles = "<br> • ".join(errores[:5])
            if len(errores) > 5: detalles += f"<br> • ... y {len(errores)-5} más."
            msg += f'<div class="mt-2 text-xs text-rose-600 bg-rose-50 p-2 rounded border border-rose-200"><p class="font-bold mb-1">Filas omitidas ({len(errores)}):</p> • {detalles}</div>'
            if contador == 0:
                finish_task(task_id, success=False, msg=msg)
                return
                
        finish_task(task_id, success=True, msg=msg)
        
    except Exception as e:
        print("ERROR CRÍTICO TAREA SEGUNDO PLANO:", str(e))
        finish_task(task_id, success=False, msg=f"Error fatal: {str(e)}")
    finally:
        conn.close()


def eliminar_egresado_permanente(id):
    buscar_egresados_paginados.cache_clear()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Eliminar registros de ingreso que tenga este egresado
        cursor.execute("DELETE FROM RegistroIngresos WHERE EgresadoID = ?", (id,))
        
        # Eliminar al egresado
        cursor.execute("DELETE FROM Egresados WHERE EgresadoID = ?", (id,))
        
        conn.commit()
        conn.close()
        return True, 'Egresado eliminado permanentemente.'
    except Exception as e:
        return False, f"No se pudo eliminar: {str(e)}"

def eliminar_egresados_masivo(ids):
    buscar_egresados_paginados.cache_clear()
    if not ids:
        return False, "No hay IDs para eliminar"
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # SQL Server requires parameter markers ? for each ID
        placeholders = ','.join(['?'] * len(ids))
        
        # Eliminar registros de ingreso asociados primero
        cursor.execute(f"DELETE FROM RegistroIngresos WHERE EgresadoID IN ({placeholders})", ids)
        
        # Eliminar egresados
        cursor.execute(f"DELETE FROM Egresados WHERE EgresadoID IN ({placeholders})", ids)
        
        conn.commit()
        return True, f"{len(ids)} egresados eliminados exitosamente."
    except Exception as e:
        return False, f"Error al eliminar en bloque: {str(e)}"
    finally:
        if 'conn' in locals():
            conn.close()

def vaciar_egresados_db():
    buscar_egresados_paginados.cache_clear()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Eliminar todos los ingresos vinculados a egresados primero para evitar errores de llave foránea
        cursor.execute("DELETE FROM RegistroIngresos WHERE EgresadoID IS NOT NULL")
        
        # Luego truncamos o vaciamos la tabla principal
        cursor.execute("DELETE FROM Egresados")
        
        # Opcional: Reiniciar el identity (contador de IDs) a 0. En SQL Server es DBCC CHECKIDENT
        cursor.execute("DBCC CHECKIDENT ('Egresados', RESEED, 0)")
        
        conn.commit()
        return True, "La tabla de Egresados ha sido VACIADA permanentemente."
    except Exception as e:
        return False, f"Error crítico al vaciar tabla: {str(e)}"
    finally:
        if 'conn' in locals():
            conn.close()
