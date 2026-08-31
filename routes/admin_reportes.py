from flask import Blueprint, request, send_file
import pandas as pd
import io
from datetime import datetime
from db import get_db_connection

admin_reportes_bp = Blueprint('admin_reportes', __name__, url_prefix='/admin')

@admin_reportes_bp.route('/reporte_hoy')
def descargar_reporte():
    conn = get_db_connection()
    sql = """
    SELECT 
        R.RegistroID as ID, 
        COALESCE(A.NombreCompleto, V.NombreCompleto, E.NombreCompleto, P.ApellidosNombres, D.ApellidosNombres) as Persona, 
        COALESCE(A.DNI, V.DNI, E.DNI, P.DNI, D.DNI) as DNI, 
        COALESCE(A.Escuela, V.Institucion, E.EscuelaProfesional, P.Oficina, 'Escuela de ' + D.Facultad) as Origen, 
        ISNULL(R.TipoUsuario, 'Desconocido') as Tipo,
        ISNULL(R.Sede, 'Central') as Sede,
        CAST(R.Piso AS VARCHAR) as Piso, 
        ISNULL(S.NombreSala, 'N/A') as Sala,
        R.Turno,
        FORMAT(R.FechaHora, 'HH:mm:ss') as Hora,
        FORMAT(R.FechaHora, 'dd/MM/yyyy') as Fecha
    FROM RegistroIngresos R 
    LEFT JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
    LEFT JOIN Visitantes V ON R.VisitanteID = V.VisitanteID
    LEFT JOIN Egresados E ON R.EgresadoID = E.EgresadoID
    LEFT JOIN PersonalAdministrativo P ON R.PersonalID = P.PersonalID
    LEFT JOIN Docentes D ON R.DocenteID = D.DocenteID
    LEFT JOIN Salas S ON R.SalaID = S.SalaID
    WHERE CAST(R.FechaHora AS DATE) = CAST(GETDATE() AS DATE) 
    ORDER BY R.FechaHora DESC
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name=f"Reporte_{datetime.now().date()}.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# --- REPORTES ADICIONALES ---

@admin_reportes_bp.route('/reporte_rango')
def reporte_rango():
    # Recibimos las fechas desde la URL (ej: ?inicio=2026-02-01&fin=2026-02-05)
    fecha_inicio = request.args.get('inicio')
    fecha_fin = request.args.get('fin')

    if not fecha_inicio or not fecha_fin:
        return "Error: Debes seleccionar ambas fechas", 400

    conn = get_db_connection()
    
    # Consulta SQL filtrando por rango de fechas
    sql = """
    SELECT 
        R.RegistroID as ID,
        COALESCE(A.NombreCompleto, V.NombreCompleto, E.NombreCompleto, P.ApellidosNombres, D.ApellidosNombres) as Persona,
        COALESCE(A.DNI, V.DNI, E.DNI, P.DNI, D.DNI) as DNI,
        COALESCE(A.Escuela, V.Institucion, E.EscuelaProfesional, P.Oficina, 'Escuela de ' + D.Facultad) as Origen,
        ISNULL(R.TipoUsuario, 'Desconocido') as Tipo,
        ISNULL(R.Sede, 'Central') as Sede,
        CAST(R.Piso AS VARCHAR) as Piso,
        ISNULL(S.NombreSala, 'N/A') as Sala,
        R.Turno,
        FORMAT(R.FechaHora, 'HH:mm:ss') as Hora,
        FORMAT(R.FechaHora, 'dd/MM/yyyy') as Fecha
    FROM RegistroIngresos R
    LEFT JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
    LEFT JOIN Visitantes V ON R.VisitanteID = V.VisitanteID
    LEFT JOIN Egresados E ON R.EgresadoID = E.EgresadoID
    LEFT JOIN PersonalAdministrativo P ON R.PersonalID = P.PersonalID
    LEFT JOIN Docentes D ON R.DocenteID = D.DocenteID
    LEFT JOIN Salas S ON R.SalaID = S.SalaID
    WHERE CAST(R.FechaHora AS DATE) >= ? 
    AND CAST(R.FechaHora AS DATE) <= ?
    ORDER BY R.FechaHora DESC
    """
    
    # Ejecutamos la consulta enviando las fechas
    # IMPORTANTE: Asegúrate de tener instalado openpyxl (pip install openpyxl)
    df = pd.read_sql(sql, conn, params=(fecha_inicio, fecha_fin))
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte_Rango')
    output.seek(0)

    return send_file(output, 
                     download_name=f"Reporte_{fecha_inicio}_al_{fecha_fin}.xlsx", 
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
