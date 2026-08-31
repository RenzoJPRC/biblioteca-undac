import uuid
from db import get_db_connection

def create_task():
    task_id = str(uuid.uuid4())
    conn = get_db_connection()
    if not conn:
        return task_id
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO UploadTasks (TaskID, Status, Progress, TotalRows, Message)
            VALUES (?, ?, ?, ?, ?)
        ''', (task_id, 'processing', 0, 0, 'Iniciando carga...'))
        conn.commit()
    except Exception as e:
        print(f"Error creating task: {e}")
    finally:
        conn.close()
    return task_id

def update_task_progress(task_id, progress, total=None, msg=""):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        
        updates = ["Progress = ?", "UpdatedAt = GETDATE()"]
        params = [progress]
        
        if total is not None:
            updates.append("TotalRows = ?")
            params.append(total)
        if msg:
            updates.append("Message = ?")
            params.append(msg)
            
        params.append(task_id)
        
        query = f"UPDATE UploadTasks SET {', '.join(updates)} WHERE TaskID = ?"
        cursor.execute(query, params)
        conn.commit()
    except Exception as e:
        print(f"Error updating task: {e}")
    finally:
        conn.close()

def finish_task(task_id, success=True, msg=""):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        status = 'completed' if success else 'error'
        
        updates = ["Status = ?", "UpdatedAt = GETDATE()"]
        params = [status]
        
        if msg:
            updates.append("Message = ?")
            params.append(str(msg)[:250])
            
        params.append(task_id)
        
        query = f"UPDATE UploadTasks SET {', '.join(updates)} WHERE TaskID = ?"
        cursor.execute(query, params)
        conn.commit()
    except Exception as e:
        print(f"Error finishing task: {e}")
        try:
            # Fallback for truncation or DataErrors: Force state to error so UI unfreezes
            cursor.execute("UPDATE UploadTasks SET Status='error', Message='Error Fatal Interno', UpdatedAt=GETDATE() WHERE TaskID=?", (task_id,))
            conn.commit()
        except:
            pass
    finally:
        conn.close()

def get_task_status(task_id):
    conn = get_db_connection()
    if not conn: 
        return {"status": "error", "message": "Error de conexión a BD"}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT Status, Progress, TotalRows, Message FROM UploadTasks WHERE TaskID = ?", (task_id,))
        row = cursor.fetchone()
        if row:
            return {
                "status": row.Status,
                "progress": row.Progress,
                "total": row.TotalRows,
                "message": row.Message or ""
            }
        else:
            return {"status": "error", "message": "Tarea no encontrada en BD"}
    except Exception as e:
        print(f"Error getting task status: {e}")
        return {"status": "error", "message": "Error leyendo tarea"}
    finally:
        conn.close()
