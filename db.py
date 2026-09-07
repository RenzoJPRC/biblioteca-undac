import os
from dotenv import load_dotenv
from contextlib import contextmanager

# Cargar variables de entorno
load_dotenv()

DEFAULT_SERVER = r"RENZO\SQL2025"

def get_db_connection():
    """
    Establece y retorna una conexión directa a la base de datos SQL Server.
    Soporta Autenticación de Windows (Trusted_Connection) y Autenticación SQL (Usuario/Password).
    Intenta conectar con pyodbc (en Windows/ODBC) y cuenta con respaldo automático en pymssql (en Linux/Render).
    """
    driver = os.getenv('DB_DRIVER', '{ODBC Driver 17 for SQL Server}')
    server = os.getenv('DB_SERVER', DEFAULT_SERVER)
    database = os.getenv('DB_DATABASE', 'BibliotecaUNDAC')
    username = os.getenv('DB_USERNAME')
    password = os.getenv('DB_PASSWORD')
    trusted = os.getenv('DB_TRUSTED_CONNECTION', 'no' if username else 'yes')

    # 1. Intentar conexión primaria con pyodbc
    try:
        import pyodbc
        if username and password:
            conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password};"
        else:
            conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection={trusted};"
        return pyodbc.connect(conn_str, timeout=15)
    except Exception as e_pyodbc:
        print(f"--- AVISO PYODBC --- ({e_pyodbc}). Intentando respaldo con pymssql...")

    # 2. Respaldo secundario con pymssql (Para entornos Linux / Cloud como Render sin driver C de ODBC)
    if username and password:
        try:
            import pymssql
            return pymssql.connect(server=server, user=username, password=password, database=database, timeout=15)
        except Exception as e_pymssql:
            print(f"--- ERROR DE CONEXIÓN BD (PYMSSQL) --- : {e_pymssql}")
            return None
    else:
        print("--- ERROR DE CONEXIÓN BD --- : No se pudo conectar vía pyodbc ni pymssql.")
        return None


@contextmanager
def get_db_cursor(autocommit=False):
    """
    Gestor de contexto (Context Manager) seguro para pyodbc y pymssql.
    Garantiza el cierre automático de la conexión y cursor, aplicando commit
    o rollback automático según el éxito de la transacción.
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