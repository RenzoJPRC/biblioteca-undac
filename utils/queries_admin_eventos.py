import pandas as pd
import io
from datetime import datetime
from db import get_db_connection
from utils.task_manager import update_task_progress, finish_task
from utils.validaciones import formatear_nombre_estetico

def buscar_eventos(query='', page=1, sede_filtro='Todas'):
    items_por_pagina = 10
    offset = (page - 1) * items_por_pagina

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if sede_filtro != 'Todas':
            sql_base = "FROM Eventos WHERE NombreEvento LIKE ? AND (ISNULL(SedeAsignada, 'Central') = ? OR SedeAsignada = 'Todas')"
            base_params = [f'%{query}%', sede_filtro]
        else:
            sql_base = "FROM Eventos WHERE NombreEvento LIKE ?"
            base_params = [f'%{query}%']
        
        # Total
        cursor.execute(f"SELECT COUNT(*) {sql_base}", tuple(base_params))
        total_items = cursor.fetchone()[0]
        total_paginas = (total_items + items_por_pagina - 1) // items_por_pagina

        # Datos
        sql_datos = f"""
            SELECT EventoID, NombreEvento, FechaEvento, HoraInicio, HoraFin, Lugar, Estado,
                   PermiteAlumnos, PermiteEgresados, PermitePersonal, PermiteVisitantes, PermiteDocentes, NombreSede
            {sql_base}
            ORDER BY FechaEvento DESC, HoraInicio DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """
        params_datos = base_params + [offset, items_por_pagina]
        cursor.execute(sql_datos, tuple(params_datos))
        
        eventos = []
        now = datetime.now()
        
        for row in cursor.fetchall():
            evento_id = row[0]
            fecha_evento = row[2]
            hora_fin = row[4]
            estado = row[6]
            
            # Verificar si el evento ya expiró y actualizarlo dinámicamente
            if estado == 'Activo':
                fecha_hora_fin = datetime.combine(fecha_evento, hora_fin)
                if now > fecha_hora_fin:
                    estado = 'Finalizado'
                    # Instanciar otro cursor para no interferir con la iteración principal fetchall
                    cursor_update = conn.cursor()
                    cursor_update.execute("UPDATE Eventos SET Estado = 'Finalizado' WHERE EventoID = ?", (evento_id,))
                    cursor_update.commit()
            
            # Calcular estado display
            estado_display = estado
            if estado == 'Activo':
                fecha_hora_inicio = datetime.combine(fecha_evento, row[3]) # HoraInicio
                if now < fecha_hora_inicio:
                    estado_display = 'Próximo'
                else:
                    estado_display = 'En Curso'
            
            # Contar asistencia
            cursor.execute("SELECT COUNT(*) FROM AsistenciaEventos WHERE EventoID = ?", (evento_id,))
            asistentes = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM InvitadosEvento WHERE EventoID = ?", (evento_id,))
            invitados = cursor.fetchone()[0]
            
            eventos.append({
                'id': evento_id,
                'nombre': row[1],
                'fecha': fecha_evento.strftime('%d/%m/%Y'),
                'fecha_raw': fecha_evento.strftime('%Y-%m-%d'),
                'hora_inicio': row[3].strftime('%H:%M'),
                'hora_fin': hora_fin.strftime('%H:%M'),
                'lugar': row[5] or '',
                'estado': estado,
                'estado_display': estado_display,
                'permite_alumnos': bool(row[7]),
                'permite_egresados': bool(row[8]),
                'permite_personal': bool(row[9]),
                'permite_visitantes': bool(row[10]),
                'permite_docentes': bool(row[11]),
                'sede': row[12] or 'Central',
                'total_asistentes': asistentes,
                'total_invitados': invitados
            })

        return {
            'status': 'success',
            'data': eventos,
            'page': page,
            'total_pages': total_paginas,
            'total_items': total_items
        }

    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals() and conn: conn.close()

def guardar_evento(data):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        evento_id = data.get('id')
        nombre = data.get('nombre')
        fecha = data.get('fecha')
        hora_inicio = data.get('hora_inicio')
        hora_fin = data.get('hora_fin')
        lugar = data.get('lugar', '')
        estado = data.get('estado', 'Activo')
        sede = data.get('sede', 'Central')
        sede_asignada = data.get('sede_asignada', 'Central')
        
        p_alu = 1 if data.get('permite_alumnos') else 0
        p_egr = 1 if data.get('permite_egresados') else 0
        p_per = 1 if data.get('permite_personal') else 0
        p_vis = 1 if data.get('permite_visitantes') else 0
        p_doc = 1 if data.get('permite_docentes') else 0

        if evento_id: # UPDATE
            cursor.execute("""
                UPDATE Eventos 
                SET NombreEvento=?, FechaEvento=?, HoraInicio=?, HoraFin=?, Lugar=?, Estado=?,
                    PermiteAlumnos=?, PermiteEgresados=?, PermitePersonal=?, PermiteVisitantes=?, PermiteDocentes=?, NombreSede=?, SedeAsignada=?
                WHERE EventoID=?
            """, (nombre, fecha, hora_inicio, hora_fin, lugar, estado, p_alu, p_egr, p_per, p_vis, p_doc, sede, sede_asignada, evento_id))
            msg = 'Evento actualizado correctamente.'
        else: # INSERT
            cursor.execute("""
                INSERT INTO Eventos 
                (NombreEvento, FechaEvento, HoraInicio, HoraFin, Lugar, Estado, 
                 PermiteAlumnos, PermiteEgresados, PermitePersonal, PermiteVisitantes, PermiteDocentes, NombreSede, SedeAsignada)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (nombre, fecha, hora_inicio, hora_fin, lugar, estado, p_alu, p_egr, p_per, p_vis, p_doc, sede, sede_asignada))
            msg = 'Evento creado correctamente.'
            
        conn.commit()
        return {'status': 'success', 'msg': msg}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals() and conn: conn.close()
        
def borrar_evento(evento_id, sede_owner='Todas'):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if sede_owner != 'Todas':
            cursor.execute("SELECT 1 FROM Eventos WHERE EventoID = ? AND ISNULL(SedeAsignada, 'Central') = ?", (evento_id, sede_owner))
            if not cursor.fetchone():
                return {'status': 'error', 'msg': 'Operación prohibida. No tienes permisos sobre este evento.'}
        
        # Eliminar dependencias
        cursor.execute("DELETE FROM AsistenciaEventos WHERE EventoID = ?", (evento_id,))
        cursor.execute("DELETE FROM InvitadosEvento WHERE EventoID = ?", (evento_id,))
        # Eliminar evento
        cursor.execute("DELETE FROM Eventos WHERE EventoID = ?", (evento_id,))
        
        conn.commit()
        return {'status': 'success', 'msg': 'Evento eliminado.'}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals() and conn: conn.close()

def procesar_excel_invitados_async(file_bytes, evento_id, task_id):
    conn = get_db_connection()
    errores = []
    contador = 0
    
    try:
        update_task_progress(task_id, 0, msg="Leyendo archivo Excel de Invitados VIP...")
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        df = df.fillna('')
        df.columns = df.columns.astype(str).str.strip().str.upper()
        
        total_filas = len(df)
        update_task_progress(task_id, 0, total=total_filas, msg=f"Validando cabeceras y preparando {total_filas} registros...")
        
        cursor = conn.cursor()
        
        # Solo DNI y Nombre son obligatorios
        for idx, row in df.iterrows():
            fila_num = idx + 2
            
            dni = str(row.get('DNI', '')).strip()
            if dni.endswith('.0'): dni = dni[:-2]
            
            # Restaurar ceros a la izquierda borrados por Excel numérico
            if dni.isdigit() and dni != '0' and len(dni) > 0 and len(dni) < 8:
                dni = dni.zfill(8)

            # Prevenir colisiones de DNIs fantasmas
            if dni == '0' or dni == '0.0':
                dni = ''
            
            # Buscar variaciones de la columna nombre en mayúsculas
            nombre_raw = str(row.get('NOMBRE COMPLETO', row.get('APELLIDOS Y NOMBRES', row.get('NOMBRES', row.get('APELLIDOS Y NOMBRE', ''))))).strip()
                
            nombre = formatear_nombre_estetico(nombre_raw)
            
            inst = str(row.get('INSTITUCIÓN', row.get('INSTITUCION', ''))).strip()
            
            if not dni or len(dni) < 5:
                errores.append(f"Fila {fila_num}: Falta DNI válido.")
                continue
                
            if not nombre:
                errores.append(f"Fila {fila_num}: Falta nombre válido.")
                continue
            
            # Check si ya está invitado al mismo evento
            cursor.execute("SELECT InvitadoID FROM InvitadosEvento WHERE DNI = ? AND EventoID = ?", (dni, evento_id))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO InvitadosEvento (DNI, NombreCompleto, Institucion, EventoID) 
                    VALUES (?, ?, ?, ?)
                """, (dni, nombre, inst, evento_id))
                contador += 1
                
                if contador % 500 == 0:
                    conn.commit()
            else:
                errores.append(f"Fila {fila_num}: DNI {dni} ya registrado para este evento.")
                
            if idx % 50 == 0:
                print(f"-> Procesados {idx} invitados VIP...")
                update_task_progress(task_id, idx, total=total_filas, msg=f"Guardando en BD: {idx} de {total_filas}...")
                
        conn.commit()
        msg = f'Se agregaron {contador} de {total_filas} invitados VIP con éxito.'
        if errores:
            detalles = "<br> • ".join(errores[:5])
            if len(errores) > 5: detalles += f"<br> • ... y {len(errores)-5} más."
            msg += f'<div class="mt-2 text-xs text-rose-600 bg-rose-50 p-2 rounded border border-rose-200"><p class="font-bold mb-1">Filas omitidas ({len(errores)}):</p> • {detalles}</div>'
            if contador == 0:
                finish_task(task_id, success=False, msg=msg)
                return
                
        finish_task(task_id, success=True, msg=msg)
    except Exception as e:
        finish_task(task_id, success=False, msg=f'Error fatal al procesar Excel VIP: {str(e)}')
    finally:
        conn.close()
