from db import get_db_connection

def obtener_auditoria_dashboard():
    """
    Recupera los útimos 200 logs de auditoría, el conteo total acumulado,
    y el top de gestores más activos.
    """
    conn = get_db_connection()
    if not conn: return None, 0, []
    
    try:
        cursor = conn.cursor()
        
        # 1. 200 logs detallados
        cursor.execute("""
            SELECT TOP 200 LogID, Usuario, Accion, Detalle, IP, FORMAT(Fecha, 'dd/MM/yyyy HH:mm:ss') as FechaFmt
            FROM AdminAuditLog
            ORDER BY Fecha DESC
        """)
        logs = cursor.fetchall()
        
        # 2. Conteo Absoluto
        cursor.execute("SELECT COUNT(*) FROM AdminAuditLog")
        total_logs = cursor.fetchone()[0]
        
        # 3. Top Gestores
        cursor.execute("SELECT TOP 5 Usuario, COUNT(*) as Ops FROM AdminAuditLog GROUP BY Usuario ORDER BY Ops DESC")
        top_usuarios = cursor.fetchall()

        return logs, total_logs, top_usuarios

    except Exception as e:
        print(f"Error extrayendo auditoria: {e}")
        return None, 0, []
    finally:
        conn.close()
