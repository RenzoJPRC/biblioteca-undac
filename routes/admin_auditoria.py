from flask import Blueprint, render_template, session, redirect, url_for
from utils.queries_auditoria import obtener_auditoria_dashboard
import json

admin_auditoria_bp = Blueprint('admin_auditoria', __name__, url_prefix='/admin/auditoria')

def traducir_accion(accion_raw):
    if not accion_raw: return "Acción Desconocida"
    a = accion_raw.upper()
    
    # === IMPORTACIONES & EXCEL ===
    if 'SUBIR_EXCEL_EGRESADOS' in a: return 'Importar Egresados'
    if 'SUBIR_EXCEL_PERSONAL' in a: return 'Importar Personal'
    if 'PROCESAR_EXCEL_VISITANTES' in a: return 'Importar Visitantes'
    if 'EGRESADOS/IMPORTAR' in a: return 'Importar Egresados'
    if 'PERSONAL/IMPORTAR' in a: return 'Importar Personal'
    if 'SUBIR_EXCEL' in a: return 'Importar Alumnos'  # Genérico de Alumnos
    
    # === VACIADOS & DDL ===
    if 'VACIAR_EGRESADOS' in a or 'EGRESADOS/VACIAR' in a: return 'Vaciar Egresados'
    if 'VACIAR_ALUMNOS' in a: return 'Vaciar Alumnos'
    if 'VACIAR_PERSONAL' in a or 'PERSONAL/VACIAR' in a: return 'Vaciar Personal'
    if 'VACIAR_VISITANTES' in a or 'VISITANTES/VACIAR' in a: return 'Vaciar Visitantes'
    if 'VACIAR_DOCENTES' in a: return 'Vaciar Docentes'
    
    # === ELIMINACIONES & DELETES ===
    if 'ELIMINAR_ALUMNOS_MASIVO' in a: return 'Borrar Alumnos (Masivo)'
    if 'ELIMINAR_ALUMNO' in a: return 'Borrar Alumno (Individual)'
    if 'ELIMINAR_EGRESADOS_MASIVO' in a or 'EGRESADOS/ELIMINAR' in a: return 'Borrar Egresados (Masivo)'
    if 'ELIMINAR_PERSONAL_MASIVO' in a or 'PERSONAL/ELIMINAR' in a: return 'Borrar Personal (Masivo)'
    if 'ELIMINAR_VISITANTE' in a or 'VISITANTES/ELIMINAR' in a: return 'Borrar Visitante'
    if 'ELIMINAR_EVENTO' in a: return 'Eliminar Evento'
    if 'ELIMINAR_DOCENTES_MASIVO' in a: return 'Borrar Docentes (Masivo)'
    if 'ELIMINAR_DOCENTE' in a: return 'Borrar Docente'
    
    # === MODIFICACIONES & ALTAS ===
    if 'ACTUALIZAR_CARNET_MASIVO' in a: return 'Alterar Carnets (Masivo)'
    if 'ACTUALIZAR_CARNET_GLOBAL' in a: return 'Alterar Carnets (Global)'
    if 'ACTUALIZAR_ALUMNO_COMPLETO' in a: return 'Editar Alumno'
    if 'CREAR_ALUMNO' in a: return 'Crear Alumno (Individual)'
    if 'EGRESADOS/ACCION_MASIVA' in a: return 'Alterar Egresados'
    if 'GUARDAR_EGRESADO' in a: return 'Crear/Editar Egresado'
    if 'GUARDAR_PERSONAL' in a: return 'Crear/Editar Personal'
    if 'GUARDAR_DOCENTES' in a: return 'Crear/Editar Docente'
    if 'AGREGAR_VISITANTE' in a: return 'Crear Visitante'
    if 'GUARDAR_EVENTO' in a or 'CREAR_EVENTO' in a or 'CREAR EVENTO' in a: return 'Crear Evento'
    if 'SALAS/CREAR' in a: return 'Crear Nueva Sala'
    if 'SALAS/TOGGLE' in a: return 'Activar/Desactivar Sala'
    if 'IMPORTAR_DOCENTES' in a: return 'Importar Docentes'
    if 'IMPORTAR_ALUMNOS' in a: return 'Importar Alumnos'
    
    # === SISTEMA & RBAC ===
    if 'SEC_BRUTEFORCE' in a: return 'Bloqueo Anti-Fuerza Bruta'
    if 'BACKUP/GENERAR' in a: return 'Exportar Backup .ZIP'
    if 'ACCESOS/CREAR' in a: return 'Crear Gestor RBAC'
    if 'ACCESOS/ELIMINAR' in a: return 'Eliminar Gestor RBAC'
    if 'ACCESOS/EDITAR' in a: return 'Editar Gestor RBAC'
    
    return accion_raw

def parsear_detalle(detalle_str):
    if not detalle_str: return "Sin detalles adjuntos"
    try:
        if "Interceptado:" in detalle_str:
            raw_json = detalle_str.split(":", 1)[1].strip()
            parsed = json.loads(raw_json)
            
            items = []
            for k, v in parsed.items():
                k_lower = k.lower()
                if 'csrf' in k_lower or 'password' in k_lower: continue
                if str(v).strip():
                    items.append(f"{k}: {v}")
            return " ➔ ".join(items) if items else "Sin metadatos relevantes"
        return detalle_str
    except Exception:
        return detalle_str

@admin_auditoria_bp.route('/')
def index():
    if session.get('admin_rol') != 'SuperAdmin':
        return redirect(url_for('admin_dashboard.admin_dashboard'))
        
    logs, total_logs, top_usuarios = obtener_auditoria_dashboard()
    
    if logs is None:
        return "Error BD"
    # Post-Procesamiento Semántico
    processed_logs = []
    for log in logs:
        lid, usr, acc, det, ip, ffmt = log
        acc_legible = traducir_accion(acc)
        det_legible = parsear_detalle(det)
        processed_logs.append((lid, usr, acc_legible, det_legible, ip, ffmt))
    
    return render_template('admin_auditoria.html', logs=processed_logs, total=total_logs, tops=top_usuarios)
