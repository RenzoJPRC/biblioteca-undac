from flask import Blueprint, render_template, session, redirect, url_for, flash, send_file
import pandas as pd
import io
import zipfile
from datetime import datetime
from db import get_db_connection

admin_backup_bp = Blueprint('admin_backup', __name__, url_prefix='/admin/backup')


# Lista central de tablas que se intentarán respaldar.
# Si una tabla no existe en la BD, se omite y el backup continúa.
TABLAS_BACKUP = [
    # Tablas principales (Personas)
    ("Alumnos", "Alumnos"),
    ("Egresados", "Egresados"),
    ("Docentes", "Docentes"),
    ("PersonalAdm", "PersonalAdministrativo"),
    ("Visitantes", "Visitantes"),

    # Historial de accesos
    ("RegistroIngresos", "RegistroIngresos"),

    # Eventos (Si aplican)
    ("Eventos", "Eventos"),
    ("AsistenciaEventos", "AsistenciaEventos"),
    ("InvitadosEvento", "InvitadosEvento"),
    
    # Logs y Usuarios del Sistema (Para trazabilidad)
    ("UsuariosSistema", "UsuariosSistema"),
    ("AdminAuditLog", "AdminAuditLog"),
]


def tabla_existe(conn, nombre_tabla):
    """
    Verifica si una tabla existe en la base de datos actual.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_NAME = ?
    """, nombre_tabla)

    return cursor.fetchone()[0] > 0


def nombre_seguro_sql(nombre_tabla):
    """
    Evita problemas con nombres de tabla al colocarlos entre corchetes.
    """
    return f"[{nombre_tabla.replace(']', ']]')}]"


@admin_backup_bp.route('/')
def index():
    if session.get('admin_rol') != 'SuperAdmin':
        flash(
            'Acceso denegado. Se requiere Nivel SuperAdmin para generar y descargar copias de seguridad de la Base de Datos.',
            'error'
        )
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    return render_template(
        'admin_backup.html',
        tablas_backup=[alias for alias, _ in TABLAS_BACKUP]
    )


@admin_backup_bp.route('/generar', methods=['POST'])
def generar():
    if session.get('admin_rol') != 'SuperAdmin':
        return redirect(url_for('admin_dashboard.admin_dashboard'))

    conn = get_db_connection()
    if not conn:
        flash("Error de conexión a la base de datos.", "error")
        return redirect(url_for('admin_backup.index'))

    memory_file = io.BytesIO()
    fecha_archivo = datetime.now().strftime('%d%m%Y')
    tablas_exportadas = []
    tablas_omitidas = []

    try:
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for alias_excel, nombre_tabla in TABLAS_BACKUP:

                if not tabla_existe(conn, nombre_tabla):
                    tablas_omitidas.append(nombre_tabla)
                    continue

                try:
                    query = f"SELECT * FROM {nombre_seguro_sql(nombre_tabla)}"
                    df = pd.read_sql(query, conn)

                    excel_buffer = io.BytesIO()

                    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                        # Excel permite máximo 31 caracteres en el nombre de la hoja
                        sheet_name = alias_excel[:31]
                        df.to_excel(writer, index=False, sheet_name=sheet_name)

                    nombre_excel = f"{alias_excel}_backup_{fecha_archivo}.xlsx"
                    zf.writestr(nombre_excel, excel_buffer.getvalue())

                    tablas_exportadas.append(nombre_tabla)

                except Exception as e:
                    tablas_omitidas.append(f"{nombre_tabla} - Error: {str(e)}")
                    continue

            # Agrega un resumen dentro del ZIP
            resumen = []
            resumen.append("BACKUP SISTEMA BIBLIOTECA UNDAC")
            resumen.append(f"Fecha de generación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            resumen.append("")
            resumen.append("TABLAS EXPORTADAS:")
            resumen.extend([f"- {t}" for t in tablas_exportadas])
            resumen.append("")
            resumen.append("TABLAS OMITIDAS:")
            if tablas_omitidas:
                resumen.extend([f"- {t}" for t in tablas_omitidas])
            else:
                resumen.append("- Ninguna")

            zf.writestr("RESUMEN_BACKUP.txt", "\n".join(resumen))

    except Exception as e:
        flash(f"Error crítico generando el backup: {str(e)}", "error")
        return redirect(url_for('admin_backup.index'))

    finally:
        conn.close()

    memory_file.seek(0)

    fecha_hoy = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"DB_SistemaBiblioteca_Backup_{fecha_hoy}.zip"

    return send_file(
        memory_file,
        download_name=filename,
        as_attachment=True,
        mimetype='application/zip'
    )