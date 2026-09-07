import os
import pyodbc
from dotenv import load_dotenv
from contextlib import contextmanager

# Cargar variables de entorno
load_dotenv()

DEFAULT_SERVER = r"RENZO\SQL2025"

def get_db_connection():
    """
    Establece y retorna una conexión directa a la base de datos SQL Server.
    """
    try:
        driver = os.getenv('DB_DRIVER', '{ODBC Driver 17 for SQL Server}')
        server = os.getenv('DB_SERVER', DEFAULT_SERVER)
        database = os.getenv('DB_DATABASE', 'BibliotecaUNDAC')
        trusted = os.getenv('DB_TRUSTED_CONNECTION', 'yes')

        conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection={trusted};"
        return pyodbc.connect(conn_str, timeout=15)
    except Exception as e:
        print(f"--- ERROR DE CONEXIÓN BD --- : {e}")
        return None


@contextmanager
def get_db_cursor(autocommit=False):
    """
    Gestor de contexto (Context Manager) seguro para pyodbc.
    Garantiza el cierre automático de la conexión y cursor, aplicando commit
    o rollback automático según el éxito de la transacción.

    Uso:
        with get_db_cursor() as (conn, cursor):
            if conn and cursor:
                cursor.execute(...)
    """
    conn = get_db_connection()
    if not conn:
        yield None, None
        return

    cursor = conn.cursor()
    try:
        yield conn, cursor
        if not autocommit:
            conn.commit()
    except Exception as e:
        print(f"--- ERROR EN TRANSACCIÓN BD (ROLLBACK) --- : {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        raise e
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass