import os
import pyodbc
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

def get_db_connection():
    try:
        conn_str = (
            f"DRIVER={os.getenv('DB_DRIVER')};"
            f"SERVER={os.getenv('DB_SERVER')};"
            f"DATABASE={os.getenv('DB_DATABASE')};"
            f"Trusted_Connection={os.getenv('DB_TRUSTED_CONNECTION')};"
        )
        return pyodbc.connect(conn_str, timeout=15)
    except Exception as e:
        print(f"--- ERROR DE CONEXIÓN BD --- : {e}")
        return None