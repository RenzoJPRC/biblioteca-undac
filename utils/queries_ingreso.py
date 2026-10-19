import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple
import re
import requests
from db import get_db_connection

DIRECT_API_UNDAC_URL = "https://api.undac.edu.pe/tasks/a3945a7384cbdcd33f49e8f5b8ec29f5/91f33e2776c526b9cca723a63476f028"

def is_undac_api_autoregister_enabled() -> bool:
    """
    Indica si la integración de alta automática desde la API institucional está activa.
    Por defecto se encuentra ACTIVA (True) según la política confirmada del proyecto.
    Requiere exclusivamente conexiones seguras HTTPS con validación de certificado TLS (verify=True).
    """
    return os.getenv('ALLOW_UNDAC_API_AUTOREGISTER', 'true').strip().lower() in ('true', '1', 'yes')

# -------------------------------------------------------------------------
# Concurrencia y Debounce para Escáner
# -------------------------------------------------------------------------
_scan_locks_mutex = threading.Lock()
_codigo_locks = {}
_recent_scans_cache = {}  # key: (codigo_clean, sala_id) -> (timestamp, response_dict)
SCAN_DEBOUNCE_SECONDS = 3.0

def _get_codigo_lock(codigo: str) -> threading.Lock:
    with _scan_locks_mutex:
        if codigo not in _codigo_locks:
            _codigo_locks[codigo] = threading.Lock()
        return _codigo_locks[codigo]

def _limpiar_cache_scans_antiguos(now: float):
    to_del = [k for k, v in _recent_scans_cache.items() if now - v[0] > 30.0]
    for k in to_del:
        _recent_scans_cache.pop(k, None)

_last_sync_error = None

def _reset_scan_cache():
    """Limpia el caché de escaneos y locks (útil para pruebas unitarias)."""
    global _last_sync_error
    with _scan_locks_mutex:
        _recent_scans_cache.clear()
        _codigo_locks.clear()
        _last_sync_error = None

def _set_last_sync_error(msg: Optional[str]):
    global _last_sync_error
    _last_sync_error = msg

def _get_last_sync_error() -> Optional[str]:
    global _last_sync_error
    return _last_sync_error



def validar_sala_activa(sala_id: int, sede: Optional[str] = None, cursor: Any = None) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Verifica que la sala exista y esté activa (Activo = 1).
    Si se proporciona sede, valida que pertenezca a dicha sede.
    Retorna (es_valida, mensaje_error, datos_sala).
    Garantiza cierre de conexión aunque falle la creación del cursor.
    """
    if isinstance(sala_id, bool) or not isinstance(sala_id, int) or sala_id <= 0:
        return False, "Identificador de sala inválido", None

    conn = None
    try:
        if cursor is None:
            conn = get_db_connection()
            if not conn:
                return False, "Error de conexión al verificar sala", None
            cursor = conn.cursor()

        cursor.execute("SELECT SalaID, NombreSala, Sede, Activo FROM Salas WHERE SalaID = ?", (sala_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Sala no registrada en el sistema", None

        activo = bool(row[3]) if row[3] is not None else False
        if not activo:
            return False, "La sala seleccionada se encuentra inactiva", None

        nombre_sala = row[1]
        sede_sala = row[2]
        if sede and sede_sala and str(sede_sala).strip().lower() != str(sede).strip().lower():
            return False, f"La sala no pertenece a la sede {sede}", None

        return True, None, {'sala_id': row[0], 'nombre_sala': nombre_sala, 'sede': sede_sala}
    except Exception as e:
        print(f"[VALIDAR SALA ERROR] Fallo al consultar sala: {type(e).__name__}")
        return False, "Error de base de datos al validar la sala", None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# Rango académico admitido en la universidad (ciclos 1 al 14)
# Comprende programas regulares (1 a 10) y programas de ciencias de la salud / medicina humana (hasta 14)
CATALOGO_SEMESTRES_ACADEMICOS = {str(i) for i in range(1, 15)}
CATALOGO_SEMESTRES_OFICIALES = CATALOGO_SEMESTRES_ACADEMICOS

# Catálogo real persistido en la base de datos (dbo.Semestres) con sus claves foráneas
# Nota: La tabla dbo.Semestres contiene históricamente solo 10 filas (faltan los ciclos 1, 7, 9, 13)
MAPEO_SEMESTRES_BD = {
    '2': 1,
    '3': 2,
    '6': 3,
    '11': 4,
    '4': 5,
    '10': 6,
    '14': 7,
    '12': 8,
    '8': 9,
    '5': 10
}
CATALOGO_SEMESTRES_BD = set(MAPEO_SEMESTRES_BD.keys())

ROMANOS_A_ARABIGOS = {
    'I': '1', 'II': '2', 'III': '3', 'IV': '4', 'V': '5',
    'VI': '6', 'VII': '7', 'VIII': '8', 'IX': '9', 'X': '10',
    'XI': '11', 'XII': '12', 'XIII': '13', 'XIV': '14'
}
# Roles y categorías de usuarios que NO son semestres académicos
ROLES_NO_SEMESTRES = {
    'DOCENTE', 'ADMINISTRATIVO', 'EGRESADO', 'VISITANTE', 'EXTERNO', 
    'INVITADO', 'PÚBLICO GENERAL', 'PUBLICO GENERAL', 'ESTUDIANTE'
}
VALORES_NO_VALIDOS = {'NONE', 'NULL', 'REGULAR', '0', '', 'NINGUNO', 'SIN SEMESTRE', '-', 'N/D', 'ND'} | ROLES_NO_SEMESTRES


@dataclass
class ResultadoSemestre:
    estado: str               # 'CONFIRMADO', 'ESTIMADO', 'NO_DISPONIBLE'
    semestre: Optional[str]   # e.g., '1', '6', '7', '9', '13', '5-6', o None
    es_confirmado: bool       # True solo si proviene de fuente institucional explícita y verificada
    fuente: str               # 'INSTITUCIONAL_EXPLICITA', 'ESTIMACION_ANIO_PERIODO', 'ESTIMACION_ANIO_RANGO', 'NINGUNA'
    periodo_referencia: Optional[str] # e.g. '2026-II' o None
    semestre_min: Optional[str] = None
    semestre_max: Optional[str] = None
    semestre_id: Optional[int] = None
    inconsistencia_catalogo: bool = False
    motivo: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return self.semestre or ''


def parsear_periodo_academico(periodo_str: Optional[str]) -> Tuple[int, int]:
    """
    Parsea cadenas de periodo académico como '2026-II', '2026-I', '2026-2', '2026-1'.
    Retorna (año, periodo) donde periodo es 1 o 2.
    Lanza ValueError si el formato es inválido, incompleto o no se puede distinguir I de II.
    Punto 1: No asume periodo II ante formatos incompletos o ambiguos.
    """
    if not periodo_str or not isinstance(periodo_str, str):
        raise ValueError(f"Periodo de referencia inválido o vacío: {periodo_str}")
    s = periodo_str.strip().upper()
    
    # Debe contener un año de 4 dígitos (19xx o 20xx) y un separador/sufijo que indique claramente I o II (o 1 o 2)
    m = re.search(r'^(?:SEMESTRE\s+)?(19\d{2}|20\d{2})[-\s/_.]*([A-Z0-9]+)$', s)
    if not m:
        raise ValueError(f"Formato de periodo académico incompleto o inválido: '{periodo_str}'. Debe incluir año y ciclo I o II (ej. '2026-II', '2026-I').")
    
    año = int(m.group(1))
    resto = m.group(2).strip()

    if resto in ('II', '2'):
        periodo = 2
    elif resto in ('I', '1'):
        periodo = 1
    else:
        raise ValueError(f"No se puede determinar periodo académico (I o II) a partir de '{periodo_str}'. Ciclo no reconocido: '{resto}'.")
        
    return año, periodo


def obtener_periodo_referencia_vigente(periodo_param: Optional[str] = None) -> Optional[str]:
    """
    Obtiene el periodo académico de referencia vigente institucional.
    Prioridad:
    1. Parámetro explícito recibido.
    2. Variable de entorno institucional PERIODO_ACADEMICO_VIGENTE o UNDAC_PERIODO_VIGENTE.
    3. Si no hay periodo configurado ni disponible, retorna None.
    """
    if periodo_param and str(periodo_param).strip():
        return str(periodo_param).strip()
    env_p = os.getenv('PERIODO_ACADEMICO_VIGENTE') or os.getenv('UNDAC_PERIODO_VIGENTE')
    if env_p and env_p.strip():
        return env_p.strip()
    return None




def resolver_semestre_id(
    semestre_str: Optional[str], 
    cursor=None, 
    usar_fallback_estatico: bool = True
) -> Tuple[Optional[int], bool]:
    """
    Separa la resolución relacional del SemestreID del semestre académico textual.
    
    Punto 3: Al guardar en la BD con un cursor activo, resuelve SemestreID contra el catálogo
    real dbo.Semestres sin depender de un mapeo fijo que pueda quedar desactualizado.
    
    Retorna (semestre_id, inconsistencia_catalogo):
    - Si existe fila en dbo.Semestres, retorna (SemestreID, False).
    - Si falta en dbo.Semestres (como los ciclos 1, 7, 9, 13), retorna (None, True) e informa la inconsistencia.
    - Si cursor es None y usar_fallback_estatico=True, usa MAPEO_SEMESTRES_BD como fallback para pruebas unitarias sin BD.
    """
    if not semestre_str:
        return None, False

    s_clean = str(semestre_str).strip()
    semestre_id = None
    consulto_bd = False

    if cursor is not None:
        try:
            cursor.execute("SELECT TOP 1 SemestreID FROM Semestres WHERE NombreSemestre = ?", (s_clean,))
            row = cursor.fetchone()
            consulto_bd = True
            if row:
                semestre_id = int(row[0])
        except Exception as e:
            print(f"[RESOLUCION SEMESTRE_ID ERROR BD] {e}")

    # Solo si no se consultó la BD con cursor y el fallback estático está permitido
    if semestre_id is None and not consulto_bd and usar_fallback_estatico:
        semestre_id = MAPEO_SEMESTRES_BD.get(s_clean)

    if semestre_id is not None:
        return semestre_id, False
    else:
        print(f"[CATALOGO SEMESTRES INCONSISTENCIA] El ciclo académico confirmado '{s_clean}' no tiene correspondencia en dbo.Semestres (falta en el catálogo relacional). Se conserva Semestre textual y SemestreID = NULL.")
        return None, True


def normalizar_semestre_oficial(
    semestre_raw: Any, 
    catalogo_admitido: Optional[set] = None
) -> Optional[str]:
    """
    Valida y normaliza un semestre institucional explícito contra el rango académico admitido (1 al 14).
    Retorna el valor numérico normalizado ('1' a '14') o None si no es válido, si es un marcador de posición,
    o si corresponde a un rol de usuario (Docente, Administrativo, Egresado, etc.).
    """
    if semestre_raw is None:
        return None
    s = str(semestre_raw).strip()
    if not s:
        return None
    s_upper = s.upper()
    if s_upper in VALORES_NO_VALIDOS or s_upper in ROLES_NO_SEMESTRES:
        return None

    cat = catalogo_admitido if catalogo_admitido is not None else CATALOGO_SEMESTRES_ACADEMICOS

    # Numérico directo ('1' a '14')
    if s in cat:
        return s

    # Ordinal tipo '1°', '1º', '1ER', '2DO', '3RO', '4TO', '6TO', '7MO', '8VO', '9NO', '10MO'
    m_num = re.match(r'^(\d{1,2})\s*(?:°|º|er|do|ro|to|vo|mo|no)?$', s, re.IGNORECASE)
    if m_num:
        num = str(int(m_num.group(1)))
        if num in cat:
            return num

    # 'Semestre X' o 'Semestre VI'
    m_sem = re.match(r'^SEMESTRE\s+(\d{1,2}|[IVXLCDM]+)$', s_upper)
    if m_sem:
        val = m_sem.group(1)
        if val in cat:
            return val
        if val in ROMANOS_A_ARABIGOS and ROMANOS_A_ARABIGOS[val] in cat:
            return ROMANOS_A_ARABIGOS[val]

    # Número romano directo ('I' a 'XIV')
    if s_upper in ROMANOS_A_ARABIGOS and ROMANOS_A_ARABIGOS[s_upper] in cat:
        return ROMANOS_A_ARABIGOS[s_upper]

    return None


def calcular_semestre_undac(
    codigo: Any = None, 
    fecha_ingreso_api: Any = None, 
    semestre_raw: Any = None, 
    periodo_ingreso: Any = None, 
    periodo_referencia: Optional[str] = None,
    catalogo_permitido: Optional[set] = None,
    fuente_oficial_verificada: bool = False,
    cursor: Any = None
) -> ResultadoSemestre:
    """
    Calcula el semestre académico o determina su no disponibilidad según reglas institucionales.
    
    Regla 1: Un semestre_raw procedente de una fuente institucional explícita y verificada
             (fuente_oficial_verificada=True) se confirma contra el rango académico admitido (1 al 14).
             Se resuelve SemestreID si existe en dbo.Semestres; si falta, conserva el texto y SemestreID = NULL.
    Regla 2: Si solo hay año de ingreso (o inferido del código), se devuelve como ESTIMADO (nunca confirmado).
    Regla 3: Si solo se conoce el año y no el periodo I/II, devuelve una estimación acotada (ej. '5-6').
             Roles de usuario (Docente, Administrativo, Egresado) no se aceptan como semestre y dan NO_DISPONIBLE.
    Regla 4: No existe fallback a semestre 10 ante código inválido o datos insuficientes (retorna NO_DISPONIBLE).
    Regla 5: Rechaza años nulos, cero, futuros o fuera de rango (evita que fecha_ingreso_api=0 produzca semestre 10).
    Regla 6: Usa un periodo de referencia configurable o institucional vigente. Si no hay periodo vigente disponible,
             devuelve NO_DISPONIBLE para cualquier estimación.
    """
    periodo_ref_vigente = obtener_periodo_referencia_vigente(periodo_referencia)

    # 1. Validación de roles no semestrales (Regla 3: No aceptar Docente, Administrativo, Egresado, etc.)
    if semestre_raw is not None:
        s_raw_upper = str(semestre_raw).strip().upper()
        if s_raw_upper in ROLES_NO_SEMESTRES:
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f'Rol o categoría institucional ({semestre_raw}) no constituye un semestre académico admitido'
            )

    # 2. Prioridad: semestre oficial confirmado si proviene de fuente oficial verificada
    # Regla 1 y Punto 2: Se valida contra el rango académico admitido (1 al 14).
    # Punto 3: Se guarda SemestreID únicamente cuando exista correspondencia en dbo.Semestres;
    # si falta, se conserva el semestre textual y SemestreID = NULL informando la inconsistencia.
    if fuente_oficial_verificada and semestre_raw is not None:
        sem_oficial = normalizar_semestre_oficial(semestre_raw, catalogo_admitido=catalogo_permitido)
        if sem_oficial:
            sem_id, inconsistencia = resolver_semestre_id(sem_oficial, cursor=cursor)
            motivo_desc = f"Confirmado por fuente institucional explícita y verificada contra rango académico (1-14)"
            if inconsistencia:
                motivo_desc += f"; dbo.Semestres carece de fila para el ciclo '{sem_oficial}', SemestreID = NULL"
            return ResultadoSemestre(
                estado='CONFIRMADO',
                semestre=sem_oficial,
                es_confirmado=True,
                fuente='INSTITUCIONAL_EXPLICITA',
                periodo_referencia=periodo_ref_vigente,
                semestre_min=sem_oficial,
                semestre_max=sem_oficial,
                semestre_id=sem_id,
                inconsistencia_catalogo=inconsistencia,
                motivo=motivo_desc
            )
        else:
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f"El valor '{semestre_raw}' no es un ciclo académico válido en el rango admitido (1-14)"
            )

    # Si se proporcionó un semestre_raw pero la fuente NO está contractualmente verificada como periodo vigente:
    # (Punto 3: Si no se puede demostrar su significado como semestre académico vigente, no marcarlo como confirmado)
    motivo_no_verificado = None
    if semestre_raw is not None:
        s_clean = str(semestre_raw).strip()
        if s_clean and s_clean.upper() not in VALORES_NO_VALIDOS:
            motivo_no_verificado = f"Campo semestre ('{s_clean}') no está demostrado contractualmente como semestre vigente en la fuente"

    # 3. Si se requiere calcular proyección o estimación por año/código, verificar periodo de referencia (Punto 2)
    if not periodo_ref_vigente:
        return ResultadoSemestre(
            estado='NO_DISPONIBLE',
            semestre=None,
            es_confirmado=False,
            fuente='NINGUNA',
            periodo_referencia=None,
            motivo=motivo_no_verificado or 'Periodo académico de referencia no configurado o no disponible para realizar la estimación'
        )

    try:
        año_ref, per_ref = parsear_periodo_academico(periodo_ref_vigente)
    except ValueError as e:
        return ResultadoSemestre(
            estado='NO_DISPONIBLE',
            semestre=None,
            es_confirmado=False,
            fuente='NINGUNA',
            periodo_referencia=periodo_ref_vigente,
            motivo=f'Periodo de referencia inválido: {e}'
        )

    # 4. Extracción de año y periodo de ingreso
    año_ingreso = None
    periodo_ing = None

    # A) Desde periodo_ingreso explícito
    if periodo_ingreso is not None:
        p_str = str(periodo_ingreso).strip().upper()
        if p_str in ('1', 'I'):
            periodo_ing = 1
        elif p_str in ('2', 'II'):
            periodo_ing = 2

    # B) Desde fecha_ingreso_api
    if fecha_ingreso_api is not None:
        f_str = str(fecha_ingreso_api).strip()
        try:
            val_num = float(f_str)
            if val_num <= 0:
                return ResultadoSemestre(
                    estado='NO_DISPONIBLE',
                    semestre=None,
                    es_confirmado=False,
                    fuente='NINGUNA',
                    periodo_referencia=periodo_ref_vigente,
                    motivo=f'Año de ingreso inválido ({fecha_ingreso_api}): menor o igual a cero'
                )
        except (ValueError, TypeError):
            pass

        if f_str.startswith('-'):
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f'Año de ingreso inválido ({fecha_ingreso_api}): negativo'
            )

        if f_str.lower() in ('null', 'none', ''):
            año_ingreso = None
        else:
            m_año = re.search(r'(19\d{2}|20\d{2})', f_str)
            if m_año:
                año_ingreso = int(m_año.group(1))
                if periodo_ing is None:
                    resto = f_str.replace(m_año.group(1), '').strip('- /_').upper()
                    if 'II' in resto or '2' in resto:
                        periodo_ing = 2
                    elif 'I' in resto or '1' in resto:
                        periodo_ing = 1

    # C) Desde prefijo del código de matrícula (si no se obtuvo año)
    if año_ingreso is None and codigo:
        cod_clean = str(codigo).strip()
        if len(cod_clean) >= 8:
            prefijo = cod_clean[:2]
            if prefijo.isdigit():
                val = int(prefijo)
                año_max_prefijo = año_ref % 100
                if 15 <= val <= año_max_prefijo:
                    año_ingreso = 2000 + val

    # 5. Validación estricta del año de ingreso (Regla 5)
    if año_ingreso is not None:
        if año_ingreso <= 0:
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f'Año de ingreso inválido ({año_ingreso}): menor o igual a cero'
            )
        if año_ingreso > año_ref:
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f'Año de ingreso inválido ({año_ingreso}): año futuro posterior a {año_ref}'
            )
        if año_ingreso < 1980:
            return ResultadoSemestre(
                estado='NO_DISPONIBLE',
                semestre=None,
                es_confirmado=False,
                fuente='NINGUNA',
                periodo_referencia=periodo_ref_vigente,
                motivo=f'Año de ingreso ({año_ingreso}) fuera del rango admitido'
            )

    # 6. Cálculo de estimación si el año es válido
    if año_ingreso is not None:
        def calc_avance(a_ing, p_ing):
            idx_ref = año_ref * 2 + (per_ref - 1)
            idx_ing = a_ing * 2 + (p_ing - 1)
            return (idx_ref - idx_ing) + 1

        if periodo_ing in (1, 2):
            avance = calc_avance(año_ingreso, periodo_ing)
            sem_str = str(max(1, avance))
            p_romano = 'I' if periodo_ing == 1 else 'II'
            return ResultadoSemestre(
                estado='ESTIMADO',
                semestre=sem_str,
                es_confirmado=False,
                fuente='ESTIMACION_ANIO_PERIODO',
                periodo_referencia=periodo_ref_vigente,
                semestre_min=sem_str,
                semestre_max=sem_str,
                motivo=f'Estimación por avance regular desde {año_ingreso}-{p_romano} para el periodo de referencia {periodo_ref_vigente}'
            )
        else:
            avance_si_p2 = calc_avance(año_ingreso, 2)
            avance_si_p1 = calc_avance(año_ingreso, 1)
            
            s_min = max(1, avance_si_p2)
            s_max = max(1, avance_si_p1)
            
            sem_display = str(s_min) if s_min == s_max else f"{s_min}-{s_max}"
                
            return ResultadoSemestre(
                estado='ESTIMADO',
                semestre=sem_display,
                es_confirmado=False,
                fuente='ESTIMACION_ANIO_RANGO',
                periodo_referencia=periodo_ref_vigente,
                semestre_min=str(s_min),
                semestre_max=str(s_max),
                motivo=f'Año de ingreso ({año_ingreso}) conocido pero periodo (I/II) no especificado; rango acotado entre semestres {s_min} y {s_max} para {periodo_ref_vigente}'
            )

    # 7. Regla 4: Código inválido o datos insuficientes -> NO_DISPONIBLE (sin fallback a semestre 10)
    return ResultadoSemestre(
        estado='NO_DISPONIBLE',
        semestre=None,
        es_confirmado=False,
        fuente='NINGUNA',
        periodo_referencia=periodo_ref_vigente,
        motivo=motivo_no_verificado or 'Código inválido o datos insuficientes para determinar semestre'
    )


def auto_registrar_alumno_api_undac(codigo, periodo_referencia=None, return_detail=False):
    """
    Consulta en tiempo real a la API REST central de la UNDAC si un código de estudiante no existe localmente.
    Si la API institucional confirma la validez del alumno, lo inserta automáticamente en la tabla Alumnos.
    Regla 7: No guarda estimaciones en Alumnos.Semestre ni SemestreID como si fueran valores confirmados.
    Punto 2: El periodo de referencia es configurable o proviene de la variable de entorno institucional.
    Punto 3: El campo data.get('Semestre') de la API externa no está contractualmente demostrado como semestre vigente;
             por tanto, no se marca como confirmado (fuente_oficial_verificada=False).
    Conserva esos campos en NULL cuando no haya semestre oficial confirmado disponible.
    Manejo atómico de transacciones: rollback y cierre garantizado sin fugas de conexión.
    Protección de privacidad: logs anonimizados sin código, DNI ni nombres.
    """
    codigo_clean = str(codigo).strip()
    if not codigo_clean or codigo_clean.lower() in ('none', 'null', '0', '0.0'):
        if return_detail:
            return False, None, 'Código inválido o vacío'
        return False, None

    # Bloqueo seguro: si el alta automática institucional está desactivada por certificado TLS inválido
    if not is_undac_api_autoregister_enabled():
        msg_operativo = "Alta automática desde API institucional desactivada por certificado TLS inválido en servidor central"
        _set_last_sync_error(msg_operativo)
        if return_detail:
            return False, None, msg_operativo
        return False, None

    conn = None
    try:
        # Consulta a la API institucional UNDAC (Estricto HTTPS con validación TLS activa)
        base_url = os.getenv('UNDAC_API_URL', DIRECT_API_UNDAC_URL)
        if base_url.startswith('http://'):
            base_url = 'https://' + base_url[len('http://'):]
        url = f"{base_url}/{codigo_clean}"
        try:
            resp = requests.get(url, timeout=5, verify=True, allow_redirects=False)
        except requests.exceptions.SSLError:
            print("[API UNDAC NOTICE] Error de validación TLS: certificado inválido en API institucional")
            _set_last_sync_error('Certificado TLS inválido en la API institucional')
            if return_detail:
                return False, None, 'Certificado TLS inválido en la API institucional'
            return False, None
        except requests.exceptions.Timeout:
            print("[API UNDAC NOTICE] Timeout al consultar API institucional")
            _set_last_sync_error('Timeout: La API institucional tardó demasiado en responder')
            if return_detail:
                return False, None, 'Timeout: La API institucional tardó demasiado en responder'
            return False, None
        except requests.exceptions.RequestException as req_err:
            print(f"[API UNDAC NOTICE] Error de conexión con API institucional: {type(req_err).__name__}")
            _set_last_sync_error('Error de conexión con el servicio institucional')
            if return_detail:
                return False, None, 'Error de conexión con el servicio institucional'
            return False, None

        # Rechazo estricto de redirecciones (mantener HTTPS estricto y evitar redirecciones hacia HTTP)
        if (getattr(resp, 'is_redirect', None) is True) or (300 <= getattr(resp, 'status_code', 0) < 400):
            print(f"[API UNDAC NOTICE] Redirección no permitida en API institucional (HTTP {resp.status_code})")
            _set_last_sync_error(f'Redirección no permitida en la API institucional (HTTP {resp.status_code})')
            if return_detail:
                return False, None, f'Redirección no permitida en la API institucional (HTTP {resp.status_code})'
            return False, None

        if resp.status_code == 404:
            _set_last_sync_error('Estudiante no encontrado en la API institucional')
            if return_detail:
                return False, None, 'Estudiante no encontrado en la API institucional'
            return False, None
        elif resp.status_code != 200:
            print(f"[API UNDAC NOTICE] Respuesta HTTP no exitosa: {resp.status_code}")
            _set_last_sync_error(f'Error en el servicio institucional (HTTP {resp.status_code})')
            if return_detail:
                return False, None, f'Error en el servicio institucional (HTTP {resp.status_code})'
            return False, None

        try:
            data = resp.json()
        except Exception:
            print("[API UNDAC NOTICE] Respuesta no válida (JSON mal formado)")
            _set_last_sync_error('Respuesta inválida recibida de la API institucional')
            if return_detail:
                return False, None, 'Respuesta inválida recibida de la API institucional'
            return False, None

        if "message" in data:
            _set_last_sync_error('Estudiante no registrado en la API institucional')
            if return_detail:
                return False, None, 'Estudiante no registrado en la API institucional'
            return False, None

        nombres = str(data.get('Nombres') or '').strip()
        ap_paterno = str(data.get('Apellido paterno') or '').strip()
        ap_materno = str(data.get('Apellido materno') or '').strip()
        if not nombres or (not ap_paterno and not ap_materno):
            print("[API UNDAC NOTICE] Respuesta incompleta recibida (faltan nombres o apellidos)")
            _set_last_sync_error('Respuesta incompleta de la API institucional')
            if return_detail:
                return False, None, 'Respuesta incompleta de la API institucional'
            return False, None

        nombre_completo = f"{ap_paterno} {ap_materno}, {nombres}".strip(" ,")
        dni = str(data.get('Dni') or '').strip()
        facultad = str(data.get('Programa facultad') or '').strip()

        resultado_semestre = calcular_semestre_undac(
            codigo=codigo_clean, 
            fecha_ingreso_api=data.get('Fecha de Ingreso'), 
            semestre_raw=data.get('Semestre'),
            periodo_referencia=periodo_referencia,
            fuente_oficial_verificada=False
        )

        semestre_val = resultado_semestre.semestre if resultado_semestre.es_confirmado else None

        conn = get_db_connection()
        if not conn:
            if return_detail:
                return False, None, 'Error de conexión con la base de datos local'
            return False, None

        cursor = conn.cursor()
        semestre_id_val = None
        if semestre_val is not None:
            semestre_id_val, _ = resolver_semestre_id(semestre_val, cursor=cursor, usar_fallback_estatico=False)

        # Verificar si existe por código o DNI antes de insertar
        cursor.execute("SELECT AlumnoID FROM Alumnos WHERE CodigoMatricula = ? OR (DNI = ? AND len(DNI) > 4)", (codigo_clean, dni))
        row_ex = cursor.fetchone()
        if row_ex:
            if semestre_val is not None:
                cursor.execute(
                    "UPDATE Alumnos SET Semestre = ?, SemestreID = ? WHERE AlumnoID = ? AND (Semestre IS NULL OR Semestre = '' OR Semestre = 'Regular')", 
                    (semestre_val, semestre_id_val, row_ex[0])
                )
                conn.commit()
            conn.close()
            conn = None
            if return_detail:
                return True, nombre_completo, None
            return True, nombre_completo

        # Inserción atómica compatible con el esquema actual para prevenir duplicación concurrente
        try:
            cursor.execute("""
                IF NOT EXISTS (
                    SELECT 1 FROM Alumnos WITH (UPDLOCK, HOLDLOCK) 
                    WHERE CodigoMatricula = ? OR (DNI = ? AND LEN(DNI) > 4)
                )
                BEGIN
                    INSERT INTO Alumnos (NombreCompleto, DNI, CodigoMatricula, Escuela, Semestre, SemestreID, Estado)
                    VALUES (?, ?, ?, ?, ?, ?, 1);
                END
            """, (codigo_clean, dni, nombre_completo, dni, codigo_clean, facultad, semestre_val, semestre_id_val))
            conn.commit()
        except Exception as e_dup:
            try:
                conn.rollback()
            except Exception:
                pass
            cursor.execute("SELECT AlumnoID FROM Alumnos WHERE CodigoMatricula = ? OR (DNI = ? AND LEN(DNI) > 4)", (codigo_clean, dni))
            row_c = cursor.fetchone()
            if not row_c:
                raise e_dup

        conn.close()
        conn = None

        desc_sem = f"Semestre confirmado '{semestre_val}'" if resultado_semestre.es_confirmado else f"Semestre no confirmado ({resultado_semestre.estado})"
        # Log anonimizado sin PII
        print(f"[API UNDAC AUTO-SYNC] Alumno registrado automáticamente en tiempo real con {desc_sem}.")
        if return_detail:
            return True, nombre_completo, None
        return True, nombre_completo

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[API UNDAC AUTO-SYNC ERROR] Fallo durante la sincronización: {type(e).__name__}")
        if return_detail:
            return False, None, 'Error interno durante el alta automática del alumno'
        return False, None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def registrar_ingreso_general(codigo, sala_id, periodo_referencia=None, sede=None, validar_sala=False):
    """
    Ejecuta el stored procedure sp_RegistrarIngreso y retorna el resultado
    en un formato de diccionario que el endpoint espera. Integrado con Auto-Sync API UNDAC.
    Adapta el flujo sin romper el escáner y sin guardar estimaciones como semestres oficiales.
    Protección contra doble lectura accidental y altas concurrentes.
    """
    if codigo is None:
        return {'status': 'error', 'msg': 'Código de matrícula o DNI requerido'}

    if isinstance(codigo, bool) or not isinstance(codigo, (str, int)):
        return {'status': 'error', 'msg': 'Formato de código inválido: debe ser texto o número'}

    codigo_clean = str(codigo).strip()
    if not codigo_clean or codigo_clean.lower() in ('none', 'null', '0', '0.0', 'undefined') or codigo_clean.replace('-', '').strip() == '':
        return {'status': 'error', 'msg': 'Posible Lectura Errónea del Escáner'}

    if len(codigo_clean) < 4 or len(codigo_clean) > 30:
        return {'status': 'error', 'msg': 'Longitud de código fuera de rango admitido'}

    if not re.match(r'^[A-Za-z0-9\-]+$', codigo_clean):
        return {'status': 'error', 'msg': 'Código con caracteres no permitidos'}

    if isinstance(sala_id, bool):
        return {'status': 'error', 'msg': 'Identificador de sala inválido: no se admiten booleanos'}
    if isinstance(sala_id, float):
        return {'status': 'error', 'msg': 'Identificador de sala inválido: no se admiten decimales'}

    if isinstance(sala_id, str):
        if not sala_id.strip().isdigit():
            return {'status': 'error', 'msg': 'Identificador de sala inválido'}
        sala_id_int = int(sala_id.strip())
    elif isinstance(sala_id, int):
        sala_id_int = sala_id
    else:
        return {'status': 'error', 'msg': 'Identificador de sala inválido'}

    if sala_id_int <= 0:
        return {'status': 'error', 'msg': 'Identificador de sala inválido'}

    # 1. Protección contra doble lectura accidental (Debounce de 3.0s por alumno y sala)
    now = time.time()
    cache_key = (codigo_clean, sala_id_int)
    with _scan_locks_mutex:
        _limpiar_cache_scans_antiguos(now)
        if cache_key in _recent_scans_cache:
            prev_time, prev_res = _recent_scans_cache[cache_key]
            if (now - prev_time) < SCAN_DEBOUNCE_SECONDS:
                return {
                    'status': 'warning',
                    'msg': 'Lectura duplicada detectada (ignorado escaneo repetido)',
                    'warning': 'doble_lectura',
                    'alumno': prev_res.get('alumno'),
                    'escuela': prev_res.get('escuela'),
                    'semestre': prev_res.get('semestre')
                }

    # 2. Serializar por código de estudiante para evitar altas o ingresos concurrentes duplicados
    lock = _get_codigo_lock(codigo_clean)
    with lock:
        # Re-verificar cooldown dentro del lock
        now_locked = time.time()
        with _scan_locks_mutex:
            if cache_key in _recent_scans_cache:
                prev_time, prev_res = _recent_scans_cache[cache_key]
                if (now_locked - prev_time) < SCAN_DEBOUNCE_SECONDS:
                    return {
                        'status': 'warning',
                        'msg': 'Lectura duplicada detectada (ignorado escaneo repetido)',
                        'warning': 'doble_lectura',
                        'alumno': prev_res.get('alumno'),
                        'escuela': prev_res.get('escuela'),
                        'semestre': prev_res.get('semestre')
                    }

        conn = get_db_connection()
        if not conn: 
            return {'status': 'error', 'msg': 'Error de base de datos al conectar'}

        try:
            cursor = conn.cursor()

            # Validación de sala si está explícitamente requerida
            if validar_sala:
                es_sala_valida, msg_sala, _ = validar_sala_activa(sala_id_int, sede=sede, cursor=cursor)
                if not es_sala_valida:
                    return {'status': 'error', 'msg': msg_sala}

            sql = """
            DECLARE @out_msg nvarchar(250);
            DECLARE @out_nombre nvarchar(250);
            DECLARE @out_escuela nvarchar(200);
            DECLARE @out_semestre nvarchar(40);
            
            -- Ejecutamos el procedimiento enviando Codigo y SalaID
            EXEC sp_RegistrarIngreso ?, ?, @out_msg OUTPUT, @out_nombre OUTPUT, @out_escuela OUTPUT, @out_semestre OUTPUT;
            
            SELECT @out_msg, @out_nombre, @out_escuela, @out_semestre;
            """
            cursor.execute(sql, (codigo_clean, sala_id_int))
            row = cursor.fetchone()
            conn.commit()
            
            if row:
                mensaje = row[0]
                nombre = row[1]
                escuela = row[2]
                semestre = row[3]

                # --- AUTO-PROVISIONING DESDE LA API REST DE LA UNDAC SI NO EXISTE EN LA BD LOCAL ---
                if 'NO ENCONTRADO' in mensaje or 'USUARIO NO ENCONTRADO' in mensaje:
                    exito_sync, nombre_sync = auto_registrar_alumno_api_undac(
                        codigo_clean, periodo_referencia=periodo_referencia
                    )
                    if exito_sync:
                        # Reintentar ejecutar el Stored Procedure inmediatamente
                        cursor.execute(sql, (codigo_clean, sala_id_int))
                        row_retry = cursor.fetchone()
                        conn.commit()
                        if row_retry:
                            mensaje = row_retry[0]
                            nombre = row_retry[1]
                            escuela = row_retry[2]
                            semestre = row_retry[3]
                            if 'CONCEDIDO' in mensaje or 'NUEVO INGRESO' in mensaje:
                                display_sem = semestre if (semestre and str(semestre).strip().lower() not in ('none', 'null', '', 'regular')) else None
                                res_success = {
                                    'status': 'success', 
                                    'msg': f"{mensaje} (Autenticado vía API UNDAC)", 
                                    'warning': None,
                                    'alumno': nombre, 
                                    'escuela': escuela, 
                                    'semestre': display_sem
                                }
                                with _scan_locks_mutex:
                                    _recent_scans_cache[cache_key] = (time.time(), res_success)
                                return res_success
                    else:
                        motivo_sync = _get_last_sync_error()
                        if motivo_sync and ('desactivada por configuración' in motivo_sync or 'desactivada' in motivo_sync):
                            return {
                                'status': 'error',
                                'msg': 'Usuario no registrado localmente. El alta automática institucional está desactivada por configuración. Solicite su registro en Administración.'
                            }
                        elif motivo_sync and ('TLS' in motivo_sync or 'certificado' in motivo_sync.lower()):
                            return {
                                'status': 'error',
                                'msg': 'Usuario no registrado localmente. Falló la consulta segura TLS a la API institucional UNDAC por validación de certificado. Acérquese a Administración.'
                            }
                        elif motivo_sync and 'Timeout' in motivo_sync:
                            return {'status': 'error', 'msg': 'Usuario no encontrado localmente y la API institucional tardó demasiado en responder'}
                        elif motivo_sync and 'conexión' in motivo_sync.lower():
                            return {'status': 'error', 'msg': 'Usuario no encontrado localmente y la API institucional no se encuentra disponible'}
                        elif motivo_sync and 'redirección' in motivo_sync.lower():
                            return {'status': 'error', 'msg': 'Usuario no encontrado localmente y la API institucional intentó una redirección no permitida'}

                # Si el semestre devuelto por la BD está vacío o es 'Regular', no inventamos un semestre
                # ni ejecutamos un UPDATE con valores estimados sobre dbo.Alumnos.
                display_sem = semestre if (semestre and str(semestre).strip().lower() not in ('none', 'null', '', 'regular')) else None

                if 'CONCEDIDO' in mensaje or 'NUEVO INGRESO' in mensaje: 
                    warning_type = None
                    if 'VENCIDO' in mensaje or 'CARNET VENCIDO' in mensaje:
                        warning_type = 'carnet_vencido'

                    res_success = {
                        'status': 'success', 
                        'msg': mensaje, 
                        'warning': warning_type,
                        'alumno': nombre, 
                        'escuela': escuela, 
                        'semestre': display_sem
                    }
                    with _scan_locks_mutex:
                        _recent_scans_cache[cache_key] = (time.time(), res_success)
                    return res_success
                
                elif 'YA REGISTRADO' in mensaje or 'YA ESTÁS REGISTRADO' in mensaje:
                    res_warn = {
                        'status': 'warning', 
                        'msg': mensaje, 
                        'alumno': nombre, 
                        'escuela': escuela, 
                        'semestre': display_sem
                    }
                    with _scan_locks_mutex:
                        _recent_scans_cache[cache_key] = (time.time(), res_warn)
                    return res_warn

                else:
                    return {'status': 'error', 'msg': mensaje}
            
            return {'status': 'error', 'msg': 'Error desconocido en BD'}

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"[REGISTRO INGRESO ERROR] Fallo durante ejecución: {type(e).__name__}")
            return {'status': 'error', 'msg': 'Error interno de base de datos al registrar el ingreso'}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass



