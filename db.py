import os
from dotenv import load_dotenv
from contextlib import contextmanager

# Cargar variables de entorno
load_dotenv()

DEFAULT_SERVER = r"RENZO\SQL2025"

class PyMSSQLCursorWrapper:
    """
    Envoltorio de Cursor para pymssql que traduce automáticamente
    los marcadores de posición '?' (estilo pyodbc) a '%s' (estilo pymssql),
    garantizando que todas las consultas SQL funcionen idénticamente en ambos drivers.
    """
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        if params is not None:
            # Convertir estilo ? (pyodbc) a estilo %s (pymssql)
            sql_conv = sql.replace('?', '%s')
            if not isinstance(params, (tuple, list)):
                params = (params,)
            return self._cursor.execute(sql_conv, params)
        else:
            return self._cursor.execute(sql)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size) if size else self._cursor.fetchmany()

    def close(self):
        try:
            return self._cursor.close()
        except Exception:
            pass

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class PyMSSQLConnectionWrapper:
    """Envoltorio de Conexión para pymssql."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return PyMSSQLCursorWrapper(self._conn.cursor())

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        try:
            return self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            return self._conn.close()
        except Exception:
            pass


def get_db_connection():
    """
    Establece y retorna una conexión directa a la base de datos SQL Server.
    1. Intenta pyodbc (en Windows o Render Docker con msodbcsql18).
    2. Si pyodbc falla por falta de driver C en Linux, conmuta a pymssql con traducción automática de '?'.
    """
    driver = os.getenv('DB_DRIVER', '{ODBC Driver 17 for SQL Server}')
    server = os.getenv('DB_SERVER', DEFAULT_SERVER)
    database = os.getenv('DB_DATABASE', 'BibliotecaUNDAC')
    username = os.getenv('DB_USERNAME')
    password = os.getenv('DB_PASSWORD')
    trusted = os.getenv('DB_TRUSTED_CONNECTION', 'no' if username else 'yes')

    # 1. Conexión Primaria con pyodbc
    try:
        import pyodbc
        if username and password:
            # Soportar Driver 18 y Driver 17
            conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password};TrustServerCertificate=yes;"
        else:
            conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection={trusted};"
        return pyodbc.connect(conn_str, timeout=15)
    except Exception as e_pyodbc:
        print(f"--- AVISO PYODBC --- ({e_pyodbc}). Intentando respaldo con pymssql...")

    # 2. Respaldo secundario con pymssql + CursorWrapper
    if username and password:
        try:
            import pymssql
            raw_conn = pymssql.connect(server=server, user=username, password=password, database=database, timeout=15)
            return PyMSSQLConnectionWrapper(raw_conn)
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