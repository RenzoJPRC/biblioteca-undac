from db import get_db_connection
from datetime import datetime
from utils.validaciones import verificar_dni_global, formatear_nombre_estetico
import pandas as pd
import io
from utils.task_manager import update_task_progress, finish_task
import functools

def _get_global_expiration():
    """Calcula la fecha de vencimiento global según la lógica anual."""
    today = datetime.now().date()
    year = today.year
    # Enero-Marzo (Mes < 4): Vence año anterior
    if today.month < 4:
        return datetime(year - 1, 12, 31).date()
    # Abril-Diciembre: Vence año actual
    return datetime(year, 12, 31).date()

@functools.lru_cache(maxsize=128)
def buscar_alumnos_paginados(query, page, limit):
    offset = (page - 1) * limit
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    params = []
    where_clause = ""
    
    if query:
        where_clause = "WHERE NombreCompleto LIKE ? OR DNI LIKE ? OR CodigoMatricula LIKE ?"
        p = f"%{query}%"
        params = [p, p, p]
    
    # 1. Obtener Total de registros
    count_sql = f"SELECT COUNT(*) FROM Alumnos {where_clause}"
    cursor.execute(count_sql, params)
    total_items = cursor.fetchone()[0]
    total_pages = (total_items + limit - 1) // limit

    # 2. Obtener Datos paginados
    data_sql = f"""
    SELECT AlumnoID, NombreCompleto, DNI, CodigoMatricula, Escuela, FechaVencimientoCarnet
    FROM Alumnos 
    {where_clause}
    ORDER BY NombreCompleto
    OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """
    
    # Agregar params de paginación al final
    full_params = params + [offset, limit]
    
    cursor.execute(data_sql, full_params)
    rows = cursor.fetchall()
    conn.close()
    
    resultados = []
    today = datetime.now().date()
    global_expiration = _get_global_expiration()

    for r in rows:
        fecha_manual = r[5] # Date object or None
        
        # Calcular fecha efectiva
        fecha_efectiva = fecha_manual if fecha_manual else global_expiration
        
        # Determinar estado
        estado = 'ACTIVO' if fecha_efectiva >= today else 'VENCIDO'

        resultados.append({
            'id': r[0],
            'nombre': r[1],
            'dni': r[2],
            'codigo': r[3],
            'escuela': r[4],
            'fecha_manual': fecha_manual.strftime('%Y-%m-%d') if fecha_manual else None,
            'fecha_efectiva': fecha_efectiva.strftime('%d/%m/%Y'),
            'estado': estado
        })
    
    return resultados, total_items, total_pages

def crear_alumno_individual(data):
    buscar_alumnos_paginados.cache_clear()
    nombre = data.get('nombre')
    dni = data.get('dni')
    codigo = data.get('codigo')
    escuela = data.get('escuela')
    fecha = data.get('fecha')
    
    val_fecha = fecha if fecha else None
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Validar DNI si existe
        if dni and len(dni) >= 5:
            err_bool, msg_valid = verificar_dni_global(dni, ignora_tabla='', cursor=cursor)
            if err_bool: return False, f"Error: {msg_valid}"
            
        cursor.execute("""
            INSERT INTO Alumnos (NombreCompleto, DNI, CodigoMatricula, Escuela, FechaVencimientoCarnet, Estado)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (nombre, dni, codigo, escuela, val_fecha))
        conn.commit()
        return True, "Alumno creado correctamente"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def actualizar_alumno_completo_db(data):
    buscar_alumnos_paginados.cache_clear()
    alumno_id = data.get('id')
    nombre = data.get('nombre')
    dni = data.get('dni')
    codigo = data.get('codigo')
    escuela = data.get('escuela')
    fecha = data.get('fecha')

    val_fecha = fecha if fecha else None
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Alumnos 
            SET NombreCompleto = ?, DNI = ?, CodigoMatricula = ?, Escuela = ?, FechaVencimientoCarnet = ? 
            WHERE AlumnoID = ?
        """, (nombre, dni, codigo, escuela, val_fecha, alumno_id))
        conn.commit()
        return True, "Alumno actualizado correctamente"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def eliminar_alumno_individual(alumno_id):
    buscar_alumnos_paginados.cache_clear()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Eliminar cascada de historial de ingresos para liberar la Foreign Key
        cursor.execute("DELETE FROM RegistroIngresos WHERE AlumnoID = ?", (alumno_id,))
        cursor.execute("DELETE FROM Alumnos WHERE AlumnoID = ?", (alumno_id,))
        conn.commit()
        return True, "Alumno eliminado correctamente"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def eliminar_alumnos_masivo_db(ids):
    buscar_alumnos_paginados.cache_clear()
    if not ids: return False, "Lista vacía"
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(ids))
        # Purga de ingresos pre-eliminación
        sql_ingresos = f"DELETE FROM RegistroIngresos WHERE AlumnoID IN ({placeholders})"
        cursor.execute(sql_ingresos, ids)
        
        sql = f"DELETE FROM Alumnos WHERE AlumnoID IN ({placeholders})"
        cursor.execute(sql, ids)
        conn.commit()
        return True, "Alumnos eliminados correctamente"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def vaciar_alumnos_db():
    buscar_alumnos_paginados.cache_clear()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM RegistroIngresos WHERE AlumnoID IS NOT NULL")
        cursor.execute("DELETE FROM Alumnos")
        conn.commit()
        return True, "Base de datos de alumnos truncada/vaciada exitosamente."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def actualizar_vencimiento_masivo(ids, accion):
    buscar_alumnos_paginados.cache_clear()
    año_actual = datetime.now().year
    fecha_val = None

    if accion == 'activar':
        fecha_val = f"{año_actual}-12-31"
    elif accion == 'desactivar':
        fecha_val = f"{año_actual - 1}-12-31"
    elif accion == 'auto':
        fecha_val = None
    else:
        return False, 'Acción no válida'

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(ids))
        sql = f"UPDATE Alumnos SET FechaVencimientoCarnet = ? WHERE AlumnoID IN ({placeholders})"
        
        params = [fecha_val] + ids
        cursor.execute(sql, params)
        conn.commit()
        return True, f"Se actualizaron {len(ids)} carnets."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def actualizar_vencimiento_global(accion):
    buscar_alumnos_paginados.cache_clear()
    año_actual = datetime.now().year
    fecha_val = None

    if accion == 'activar':
        fecha_val = f"{año_actual}-12-31"
    elif accion == 'desactivar':
        fecha_val = f"{año_actual - 1}-12-31"
    elif accion == 'auto':
        fecha_val = None
    else:
        return False, 'Acción no válida'

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        sql = "UPDATE Alumnos SET FechaVencimientoCarnet = ?"
        cursor.execute(sql, (fecha_val,))
        conn.commit()
        return True, "Se actualizó el estado de todos los alumnos en la base de datos."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def procesar_excel_alumnos_async(file_bytes, task_id):
    buscar_alumnos_paginados.cache_clear()
    conn = get_db_connection()
    contador = 0
    errores = []
    try:
        update_task_progress(task_id, 0, msg="Leyendo archivo Excel de Alumnos...")
        
        # Leemos garantizando que todos los datos se procesen como texto puro
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        df = df.fillna('')
        df.columns = df.columns.astype(str).str.strip().str.upper()
        
        total_filas = len(df)
        update_task_progress(task_id, 0, total=total_filas, msg=f"Validando cabeceras y preparando {total_filas} registros...")
        
        cursor = conn.cursor()
        
        for index, row in df.iterrows():
            num_fila = index + 2
            
            # Limpieza exhaustiva
            dni = str(row.get('DNI', '')).strip()
            if dni.endswith('.0'): dni = dni[:-2]
            
            # Restaurar ceros a la izquierda borrados por Excel numérico
            if dni.isdigit() and dni != '0' and len(dni) > 0 and len(dni) < 8:
                dni = dni.zfill(8)
                
            # Prevenir colisiones de DNIs fantasmas
            if dni == '0' or dni == '0.0':
                dni = ''
            
            # Buscar variaciones comunes de cabeceras EN MAYÚSCULAS Y CON/SIN S
            nombre_raw = str(row.get('APELLIDOS Y NOMBRE', 
                         row.get('APELLIDOS Y NOMBRES',
                         row.get('NOMBRE COMPLETO', 
                         row.get('NOMBRES Y APELLIDOS', ''))))).strip()
            nombre = formatear_nombre_estetico(nombre_raw)
            
            codigo = str(row.get('CÓDIGO DE MATRÍCULA',
                        row.get('CODIGO DE MATRICULA',
                        row.get('CÓDIGO',
                        row.get('CODIGO',
                        row.get('CODIGO MATRICULA', '')))))).strip()
            if codigo.endswith('.0'): codigo = codigo[:-2]
            
            escuela = str(row.get('ESCUELA PROFESIONAL', row.get('ESCUELA', ''))).strip()
            
            facultad = str(row.get('FACULTAD', '')).strip()
            if facultad.lower() in ('nan', 'null', 'none', '0'): facultad = ''
            
            correo_inst = str(row.get('CORREO INSTITUCIONAL', '')).strip()
            if correo_inst.lower() in ('nan', 'null', 'none', '0'):
                correo_inst = ''

            correo_per = str(row.get('CORREO PERSONAL',
                         row.get('CORREO ALTERNO',
                         row.get('CORREO ALTERNATIVO', '')))).strip()
            if correo_per.lower() in ('nan', 'null', 'none', '0'):
                correo_per = ''

            semestre = str(row.get('SEMESTRE', '')).strip()
            if semestre.endswith('.0'): semestre = semestre[:-2]
            
            if not nombre: 
                errores.append(f"Fila {num_fila}: Celda de nombre vacía.")
                continue

            # --- VALIDACIÓN GLOBAL ---
            skip_row = False
            if dni not in ['0', '', '0.0'] and len(dni) >= 5:
                err_bool, msg_valid = verificar_dni_global(dni, ignora_tabla='Alumnos', cursor=cursor)
                if err_bool: 
                    errores.append(f"Fila {num_fila}: {msg_valid} - DNI {dni}")
                    skip_row = True
            elif not codigo:
                # Si ni DNI válido ni código existe, no podemos identificar
                errores.append(f"Fila {num_fila}: DNI y Código ausentes o inválidos.")
                skip_row = True
                
            if not skip_row:
                # ----------------- INICIO RESOLUCIÓN RELACIONAL -----------------
                escuela_id = None
                semestre_id = None
                
                if escuela:
                    # Intenta encontrar coincidencias flexibles con Escuelas
                    cursor.execute("SELECT TOP 1 EscuelaID FROM Escuelas WHERE NombreEscuela LIKE ?", ('%' + escuela[:15] + '%',))
                    row_escuela = cursor.fetchone()
                    if row_escuela: escuela_id = row_escuela[0]
                    
                if semestre:
                    # Intenta encontrar coincidencias exactas del numero de semestre
                    cursor.execute("SELECT TOP 1 SemestreID FROM Semestres WHERE NombreSemestre = ?", (semestre,))
                    row_semestre = cursor.fetchone()
                    if row_semestre: semestre_id = row_semestre[0]
                # ----------------- FIN RESOLUCIÓN RELACIONAL -----------------

                # Buscar por código primero
                cursor.execute("SELECT AlumnoID FROM Alumnos WHERE CodigoMatricula = ? AND CodigoMatricula != ''", (codigo,))
                existe = cursor.fetchone()
                
                # Si no existe por código, buscar por DNI
                if not existe and dni not in ['0', '', '0.0'] and len(dni) >= 5:
                    cursor.execute("SELECT AlumnoID FROM Alumnos WHERE DNI = ?", (dni,))
                    existe = cursor.fetchone()

                if existe:
                    cursor.execute("UPDATE Alumnos SET NombreCompleto=?, CodigoMatricula=?, CorreoInstitucional=?, CorreoPersonal=?, Escuela=?, Facultad=?, Semestre=?, Estado=1, EscuelaID=?, SemestreID=? WHERE AlumnoID=?", 
                                   (nombre, codigo, correo_inst, correo_per, escuela, facultad, semestre, escuela_id, semestre_id, existe[0]))
                else:
                    cursor.execute("INSERT INTO Alumnos (NombreCompleto, DNI, CodigoMatricula, CorreoInstitucional, CorreoPersonal, Escuela, Facultad, Semestre, Estado, EscuelaID, SemestreID) VALUES (?,?,?,?,?,?,?,?,1,?,?)", 
                                   (nombre, dni, codigo, correo_inst, correo_per, escuela, facultad, semestre, escuela_id, semestre_id))
                contador += 1
                
                if contador % 500 == 0:
                    conn.commit()
            
            # Reporte cada 50 filas
            if index % 50 == 0:
                print(f"-> Procesados {index} alumnos...")
                update_task_progress(task_id, index, total=total_filas, msg=f"Guardando en BD: {index} de {total_filas}...")
                
        conn.commit()
        
        msg = f'Procesados {contador} de {total_filas} alumnos con éxito.'
        if errores:
            detalles = "<br> • ".join(errores[:5])
            if len(errores) > 5: detalles += f"<br> • ... y {len(errores)-5} más."
            msg += f'<div class="mt-2 text-xs text-rose-600 bg-rose-50 p-2 rounded border border-rose-200"><p class="font-bold mb-1">Filas omitidas ({len(errores)}):</p> • {detalles}</div>'
            if contador == 0:
                finish_task(task_id, success=False, msg=msg)
                return
                
        finish_task(task_id, success=True, msg=msg)
    except Exception as e:
        finish_task(task_id, success=False, msg=f"Error fatal: {str(e)}")
    finally:
        conn.close()
