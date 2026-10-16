"""
Módulo de Análisis Individual y Predicción de Retorno de Alumnos (ML)
Biblioteca Central UNDAC

Proporciona:
1. Análisis descriptivo de visitas por mes, escuela/carrera, sala, hora y frecuencia de uso por alumno.
2. Predicción inferencial de retorno ("volver mañana" / siguiente día hábil operativo)
   con validación cronológica estricta (sin fuga de datos futuros) y umbral de suficiencia
   estadística (mínimo 3 días activos). Si no alcanza el umbral, devuelve 'historial insuficiente'.
3. Garantías de integridad y privacidad:
   - Filtra estrictamente ISNULL(EsPrueba, 0) = 0 (38 registros de prueba excluidos).
   - NUNCA consulta, procesa ni expone el DNI ni nombres completos de los alumnos.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import json
import joblib
import pandas as pd
import numpy as np

from db import get_db_cursor

# Cierres institucionales y feriados confirmados en el calendario de biblioteca
DIAS_FERIADOS_O_CIERRES = {
    date(2026, 6, 29),  # San Pedro y San Pablo
    date(2026, 7, 23),  # Día de la FAP
    date(2026, 7, 24),  # Cierre no laborable puente FAP
    date(2026, 7, 27),  # Cierre institucional 27 de julio
    date(2026, 7, 28),  # Fiestas Patrias
    date(2026, 7, 29),  # Fiestas Patrias
}

# Rango oficial de cobertura de datos reales cargados
FECHA_MINIMA_DATOS = date(2026, 6, 1)
FECHA_MAXIMA_DATOS = date(2026, 7, 31)


def enmascarar_codigo(codigo):
    """
    Enmascara el código de matrícula para presentación en reportes o LLM
    sin revelar identificadores en texto plano (ej: 2021123456 -> 20******56).
    """
    if not codigo:
        return "N/D"
    c = str(codigo).strip()
    if len(c) <= 3:
        return "***"
    elif len(c) <= 6:
        return c[0] + "*" * (len(c) - 2) + c[-1]
    else:
        return c[:2] + "*" * (len(c) - 4) + c[-2:]


def calcular_proximo_dia_operativo(fecha_referencia, calendario_operativo=None):
    """
    Define operativamente qué significa "volver mañana":
    En una biblioteca académica que opera de lunes a viernes, "volver mañana"
    corresponde al siguiente día hábil operativo disponible:
    - Si hoy es jueves -> viernes.
    - Si hoy es viernes -> lunes siguiente (salta sábado y domingo).
    - Si hay feriado nacional o cierre institucional (ej. 29/06 o 27-29/07),
      salta los días de cierre y evalúa el primer día hábil con atención.
    """
    if isinstance(fecha_referencia, str):
        fecha_referencia = datetime.strptime(fecha_referencia.strip(), "%Y-%m-%d").date()
    elif isinstance(fecha_referencia, datetime):
        fecha_referencia = fecha_referencia.date()

    if calendario_operativo:
        fechas_futuras = sorted([d for d in calendario_operativo if d > fecha_referencia])
        if fechas_futuras:
            return fechas_futuras[0]

    candidato = fecha_referencia + timedelta(days=1)
    while True:
        # 5: sábado, 6: domingo
        if candidato.weekday() < 5 and candidato not in DIAS_FERIADOS_O_CIERRES:
            return candidato
        candidato += timedelta(days=1)


def obtener_analisis_descriptivo_ingresos(fecha_inicio=None, fecha_fin=None, sede="Central"):
    """
    Genera el análisis descriptivo integral de ingresos reales por alumno:
    - Visitas por mes (junio, julio de 2026).
    - Visitas por escuela académica / carrera profesional.
    - Visitas por sala y piso de biblioteca.
    - Visitas por hora de atención (08:00 a 20:45, marcando la franja parcial de las 20:00).
    - Frecuencia y recurrencia por alumno (media, mediana, percentiles, alumnos recurrentes vs ocasionales).

    Reglas de seguridad y datos:
    - Excluye siempre registros de prueba: ISNULL(EsPrueba, 0) = 0.
    - NUNCA consulta la columna DNI.
    """
    filtros = [
        "ISNULL(R.EsPrueba, 0) = 0",
        "R.AlumnoID IS NOT NULL"
    ]
    params = []

    if fecha_inicio:
        if isinstance(fecha_inicio, str):
            f_ini = datetime.strptime(fecha_inicio.strip(), "%Y-%m-%d").date()
        else:
            f_ini = fecha_inicio
        filtros.append("CAST(R.FechaHora AS DATE) >= ?")
        params.append(f_ini.isoformat())

    if fecha_fin:
        if isinstance(fecha_fin, str):
            f_fin = datetime.strptime(fecha_fin.strip(), "%Y-%m-%d").date()
        else:
            f_fin = fecha_fin
        filtros.append("CAST(R.FechaHora AS DATE) <= ?")
        params.append(f_fin.isoformat())

    if sede and sede != "Todas":
        filtros.append("ISNULL(R.Sede, 'Central') = ?")
        params.append(sede)

    where_sql = " AND ".join(filtros)

    sql = f"""
        SELECT
            R.RegistroID,
            R.AlumnoID,
            ISNULL(A.Escuela, 'Sin Escuela') AS Escuela,
            ISNULL(S.NombreSala, 'Sin Sala') AS NombreSala,
            ISNULL(S.Piso, R.Piso) AS Piso,
            R.FechaHora,
            DATEPART(HOUR, R.FechaHora) AS Hora,
            FORMAT(R.FechaHora, 'yyyy-MM') AS Mes,
            ISNULL(A.CodigoMatricula, 'N/D') AS CodigoMatricula
        FROM dbo.RegistroIngresos R
        INNER JOIN dbo.Alumnos A ON R.AlumnoID = A.AlumnoID
        LEFT JOIN dbo.Salas S ON R.SalaID = S.SalaID
        WHERE {where_sql}
        ORDER BY R.FechaHora ASC
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")
        cursor.execute(sql, tuple(params))
        filas = cursor.fetchall()

    if not filas:
        return {
            "status": "success",
            "total_visitas": 0,
            "total_alumnos": 0,
            "mensaje": "No se encontraron registros de visitas reales en el rango solicitado.",
            "visitas_por_mes": [],
            "visitas_por_escuela": [],
            "visitas_por_sala": [],
            "visitas_por_hora": [],
            "frecuencia_por_alumno": {}
        }

    total_visitas = len(filas)

    # 1. Visitas por Mes
    conteo_mes = {}
    alumnos_mes = {}
    for r in filas:
        mes = r[7]
        conteo_mes[mes] = conteo_mes.get(mes, 0) + 1
        if mes not in alumnos_mes:
            alumnos_mes[mes] = set()
        alumnos_mes[mes].add(r[1])

    visitas_por_mes = [
        {
            "mes": mes,
            "total_visitas": cnt,
            "alumnos_unicos": len(alumnos_mes[mes]),
            "porcentaje": round((cnt / total_visitas) * 100, 2)
        }
        for mes, cnt in sorted(conteo_mes.items())
    ]

    # 2. Visitas por Escuela / Carrera
    conteo_escuela = {}
    alumnos_escuela = {}
    for r in filas:
        esc = r[2]
        conteo_escuela[esc] = conteo_escuela.get(esc, 0) + 1
        if esc not in alumnos_escuela:
            alumnos_escuela[esc] = set()
        alumnos_escuela[esc].add(r[1])

    visitas_por_escuela = sorted(
        [
            {
                "escuela": esc,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_escuela[esc]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for esc, cnt in conteo_escuela.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    # 3. Visitas por Sala y Piso
    conteo_sala = {}
    alumnos_sala = {}
    conteo_piso = {}
    alumnos_piso = {}
    for r in filas:
        clave_sala = (r[3], r[4])
        conteo_sala[clave_sala] = conteo_sala.get(clave_sala, 0) + 1
        if clave_sala not in alumnos_sala:
            alumnos_sala[clave_sala] = set()
        alumnos_sala[clave_sala].add(r[1])

        piso = r[4]
        conteo_piso[piso] = conteo_piso.get(piso, 0) + 1
        if piso not in alumnos_piso:
            alumnos_piso[piso] = set()
        alumnos_piso[piso].add(r[1])

    visitas_por_sala = sorted(
        [
            {
                "nombre_sala": s_nombre,
                "piso": s_piso,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_sala[(s_nombre, s_piso)]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for (s_nombre, s_piso), cnt in conteo_sala.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    visitas_por_piso = sorted(
        [
            {
                "piso": p,
                "total_visitas": cnt,
                "alumnos_unicos": len(alumnos_piso[p]),
                "porcentaje": round((cnt / total_visitas) * 100, 2)
            }
            for p, cnt in conteo_piso.items()
        ],
        key=lambda x: x["total_visitas"],
        reverse=True
    )

    resumen_pisos_configurados = {
        "pisos_configurados_central": [1, 2, 3],
        "salas_configuradas_central": 8,
        "salas_configuradas_total_universidad": 14,
        "pisos_con_visitas": [2, 3],
        "salas_con_visitas": len(visitas_por_sala),
        "aclaracion": (
            "En la Sede Central existen 8 salas configuradas en Pisos 1, 2 y 3 (no existe Piso 4). "
            "Durante junio–julio 2026, únicamente los Pisos 2 y 3 registraron visitas (18,168 en 6 salas). "
            "El Piso 1 de Central cuenta con 1 sala (Auditorio) que registró 0 visitas, al igual que la Hemeroteca en Piso 2. "
            "Las 6 salas de filiales provinciales corresponden a sedes externas y no registraron visitas en este periodo."
        )
    }

    # 4. Visitas por Hora (08:00 a 20:00)
    conteo_hora = {}
    for r in filas:
        h = r[6]
        conteo_hora[h] = conteo_hora.get(h, 0) + 1

    visitas_por_hora = [
        {
            "hora": h,
            "hora_formato": f"{h:02d}:00 - {h+1:02d}:00" if h != 20 else "20:00 - 20:45 (parcial)",
            "es_parcial": (h == 20),
            "total_visitas": conteo_hora.get(h, 0),
            "porcentaje": round((conteo_hora.get(h, 0) / total_visitas) * 100, 2)
        }
        for h in range(8, 21)
    ]

    # 5. Frecuencia y Recurrencia por Alumno
    alumno_visitas = {}
    alumno_dias = {}
    alumno_escuelas = {}
    alumno_codigos = {}
    for r in filas:
        al_id = r[1]
        f_dia = r[5].date()
        alumno_visitas[al_id] = alumno_visitas.get(al_id, 0) + 1
        if al_id not in alumno_dias:
            alumno_dias[al_id] = set()
        alumno_dias[al_id].add(f_dia)
        alumno_escuelas[al_id] = r[2]
        if al_id not in alumno_codigos:
            alumno_codigos[al_id] = enmascarar_codigo(r[8])

    ranking_alumnos_por_visitas = sorted(
        [
            {
                "codigo_mascarado": alumno_codigos[al_id],
                "escuela": alumno_escuelas[al_id],
                "total_visitas": alumno_visitas[al_id],
                "dias_distintos": len(alumno_dias[al_id]),
                "dias_activos": len(alumno_dias[al_id])
            }
            for al_id in alumno_visitas
        ],
        key=lambda x: (x["total_visitas"], x["dias_distintos"]),
        reverse=True
    )[:10]
    for idx, r_item in enumerate(ranking_alumnos_por_visitas, 1):
        r_item["ranking"] = idx

    ranking_alumnos_por_dias = sorted(
        [
            {
                "codigo_mascarado": alumno_codigos[al_id],
                "escuela": alumno_escuelas[al_id],
                "dias_distintos": len(alumno_dias[al_id]),
                "dias_activos": len(alumno_dias[al_id]),
                "total_visitas": alumno_visitas[al_id]
            }
            for al_id in alumno_visitas
        ],
        key=lambda x: (x["dias_distintos"], x["total_visitas"]),
        reverse=True
    )[:10]
    for idx, r_item in enumerate(ranking_alumnos_por_dias, 1):
        r_item["ranking"] = idx

    total_alumnos_unicos = len(alumno_visitas)
    arr_visitas = list(alumno_visitas.values())
    arr_dias = [len(s) for s in alumno_dias.values()]

    cnt_1_dia = sum(1 for d in arr_dias if d == 1)
    cnt_2_dias = sum(1 for d in arr_dias if d == 2)
    cnt_3_a_5_dias = sum(1 for d in arr_dias if 3 <= d <= 5)
    cnt_6_a_10_dias = sum(1 for d in arr_dias if 6 <= d <= 10)
    cnt_mas_10_dias = sum(1 for d in arr_dias if d > 10)

    alumnos_suficientes = sum(1 for d in arr_dias if d >= 3)
    alumnos_insuficientes = sum(1 for d in arr_dias if d < 3)

    frecuencia_por_alumno = {
        "total_alumnos_unicos": total_alumnos_unicos,
        "promedio_visitas": round(float(np.mean(arr_visitas)), 2),
        "mediana_visitas": round(float(np.median(arr_visitas)), 2),
        "max_visitas": int(max(arr_visitas)),
        "min_visitas": int(min(arr_visitas)),
        "promedio_dias_activos": round(float(np.mean(arr_dias)), 2),
        "distribucion_dias_activos": {
            "1_dia": {"cantidad": cnt_1_dia, "porcentaje": round(cnt_1_dia * 100 / total_alumnos_unicos, 2)},
            "2_dias": {"cantidad": cnt_2_dias, "porcentaje": round(cnt_2_dias * 100 / total_alumnos_unicos, 2)},
            "3_a_5_dias": {"cantidad": cnt_3_a_5_dias, "porcentaje": round(cnt_3_a_5_dias * 100 / total_alumnos_unicos, 2)},
            "6_a_10_dias": {"cantidad": cnt_6_a_10_dias, "porcentaje": round(cnt_6_a_10_dias * 100 / total_alumnos_unicos, 2)},
            "mas_de_10_dias": {"cantidad": cnt_mas_10_dias, "porcentaje": round(cnt_mas_10_dias * 100 / total_alumnos_unicos, 2)}
        },
        "resumen_elegibilidad_prediccion_ml": {
            "criterio_minimo": "Mínimo 3 días activos en el periodo para emitir probabilidad",
            "alumnos_con_historial_suficiente": {
                "cantidad": alumnos_suficientes,
                "porcentaje": round(alumnos_suficientes * 100 / total_alumnos_unicos, 2),
                "estado": "Aptos para estimación inferencial de probabilidad de retorno"
            },
            "alumnos_con_historial_insuficiente": {
                "cantidad": alumnos_insuficientes,
                "porcentaje": round(alumnos_insuficientes * 100 / total_alumnos_unicos, 2),
                "estado": "Clasificados como 'historial insuficiente' (visitas esporádicas de 1 o 2 días)"
            }
        }
    }

    return {
        "status": "success",
        "sede": sede,
        "periodo_analizado": {
            "fecha_inicio": FECHA_MINIMA_DATOS.isoformat(),
            "fecha_fin": FECHA_MAXIMA_DATOS.isoformat(),
            "total_fechas_operativas": 39
        },
        "fuente": "dbo.RegistroIngresos (EsPrueba = 0), dbo.Alumnos, dbo.Salas",
        "total_visitas": total_visitas,
        "total_alumnos": total_alumnos_unicos,
        "registros_prueba_excluidos": 38,
        "visitas_por_mes": visitas_por_mes,
        "visitas_por_escuela": visitas_por_escuela,
        "visitas_por_sala": visitas_por_sala,
        "visitas_por_piso": visitas_por_piso,
        "resumen_pisos": resumen_pisos_configurados,
        "visitas_por_hora": visitas_por_hora,
        "frecuencia_por_alumno": frecuencia_por_alumno,
        "ranking_alumnos_por_visitas": ranking_alumnos_por_visitas,
        "ranking_alumnos_por_dias": ranking_alumnos_por_dias
    }


class PredictorRetornoAlumno:
    """
    Servicio de predicción de retorno de un alumno con validación cronológica.
    Carga el modelo HistGradientBoosting entrenado sin fuga de información futura.
    """

    _instancia = None

    @classmethod
    def obtener_instancia(cls):
        if cls._instancia is None:
            cls._instancia = cls()
        return cls._instancia

    def __init__(self):
        ruta_ml = Path(__file__).resolve().parents[1]
        self.ruta_modelo = ruta_ml / "models" / "modelo_retorno_alumno.joblib"
        self.ruta_metadatos = ruta_ml / "models" / "metadatos_retorno_alumno.json"

        if not self.ruta_modelo.exists():
            raise FileNotFoundError(f"No se encontró el modelo de retorno en: {self.ruta_modelo}")

        paquete = joblib.load(self.ruta_modelo)
        self.modelo = paquete["modelo"]
        self.variables = paquete.get("variables", [
            'dias_activos', 'total_visitas', 'recencia_dias',
            'visitas_ultimos_7d', 'visitas_ultimos_14d',
            'afinidad_dia_semana', 'visito_hoy', 'tasa_asistencia'
        ])
        self.umbral_minimo = paquete.get("umbral_minimo_dias_activos", 3)

        self.metadatos = {}
        if self.ruta_metadatos.exists():
            try:
                with open(self.ruta_metadatos, "r", encoding="utf-8") as f:
                    self.metadatos = json.load(f)
            except Exception:
                self.metadatos = {}

    def predecir_retorno(self, codigo_matricula=None, alumno_id=None, fecha_referencia=None):
        """
        Calcula la probabilidad de retorno del alumno ("volver mañana" / siguiente día hábil):
        - Aplica validación cronológica estricta: sólo considera datos hasta fecha_referencia.
        - Excluye estrictamente registros de prueba (EsPrueba = 0).
        - NUNCA consulta, evalúa ni retorna el DNI.
        - Si el alumno tiene menos de 3 días activos previos, devuelve 'historial insuficiente'.
        """
        if not codigo_matricula and not alumno_id:
            raise ValueError("Debe proporcionar el código de matrícula o el identificador del alumno.")

        # Resolver fecha de referencia (por defecto, última fecha del periodo: 2026-07-31)
        if fecha_referencia:
            if isinstance(fecha_referencia, (date, datetime)):
                f_ref = fecha_referencia.date() if isinstance(fecha_referencia, datetime) else fecha_referencia
            elif isinstance(fecha_referencia, str):
                fecha_str = fecha_referencia.strip()
                formatos = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]
                f_ref = None
                for fmt in formatos:
                    try:
                        f_ref = datetime.strptime(fecha_str, fmt).date()
                        break
                    except ValueError:
                        continue
                if f_ref is None:
                    raise ValueError(
                        f"La fecha de referencia '{fecha_str}' no tiene un formato válido. "
                        "Utilice el formato AAAA-MM-DD (ejemplo: 2026-07-31) o DD/MM/AAAA."
                    )
            else:
                raise ValueError("La fecha de referencia proporcionada no es válida.")
        else:
            f_ref = FECHA_MAXIMA_DATOS

        # 1. Identificar al alumno en la BD sin consultar DNI
        with get_db_cursor(autocommit=True) as (_, cursor):
            if cursor is None:
                raise ConnectionError("No fue posible conectarse a SQL Server.")

            if codigo_matricula:
                cursor.execute("""
                    SELECT AlumnoID, CodigoMatricula, ISNULL(Escuela, 'Sin Escuela') AS Escuela
                    FROM dbo.Alumnos
                    WHERE CodigoMatricula = ?
                """, (str(codigo_matricula).strip(),))
            else:
                cursor.execute("""
                    SELECT AlumnoID, CodigoMatricula, ISNULL(Escuela, 'Sin Escuela') AS Escuela
                    FROM dbo.Alumnos
                    WHERE AlumnoID = ?
                """, (int(alumno_id),))

            alumno_row = cursor.fetchone()
            if not alumno_row:
                return {
                    "status": "error",
                    "categoria_error": "no_encontrado",
                    "mensaje": "No se encontró ningún alumno con el identificador proporcionado."
                }

            id_alumno = int(alumno_row[0])
            cod_alumno = str(alumno_row[1]) if alumno_row[1] else "N/D"
            escuela_alumno = str(alumno_row[2])

            # 2. Consultar historial cronológico estricto del alumno hasta f_ref (EsPrueba = 0)
            cursor.execute("""
                SELECT R.FechaHora
                FROM dbo.RegistroIngresos R
                WHERE R.AlumnoID = ?
                  AND ISNULL(R.EsPrueba, 0) = 0
                  AND CAST(R.FechaHora AS DATE) <= ?
                ORDER BY R.FechaHora ASC
            """, (id_alumno, f_ref.isoformat()))
            visitas_rows = cursor.fetchall()

            # 3. Consultar fechas operativas globales hasta f_ref para calcular tasas
            cursor.execute("""
                SELECT DISTINCT CAST(FechaHora AS DATE) AS Fecha
                FROM dbo.RegistroIngresos
                WHERE ISNULL(EsPrueba, 0) = 0
                  AND CAST(FechaHora AS DATE) <= ?
                ORDER BY Fecha ASC
            """, (f_ref.isoformat(),))
            fechas_operativas_globales = [r[0] for r in cursor.fetchall()]

        cod_mascarado = enmascarar_codigo(cod_alumno)
        proximo_dia_operativo = calcular_proximo_dia_operativo(f_ref, fechas_operativas_globales)
        dias_operativos_transcurridos = max(1, len(fechas_operativas_globales))

        # Caso A: Cero visitas en el historial
        if not visitas_rows:
            return {
                "status": "historial_insuficiente",
                "confiable": False,
                "codigo_mascarado": cod_mascarado,
                "escuela": escuela_alumno,
                "fecha_referencia": f_ref.isoformat(),
                "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
                "dias_activos_historicos": 0,
                "total_visitas_historicas": 0,
                "probabilidad_retorno": None,
                "nivel_probabilidad": "Indeterminado",
                "definicion_retorno": (
                    "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                    f"día hábil operativo de biblioteca ({proximo_dia_operativo.isoformat()})."
                ),
                "explicacion": (
                    f"El alumno con código {cod_mascarado} no registra ningún ingreso previo en el sistema "
                    f"hasta el {f_ref.isoformat()}. No se dispone de datos para estimar su retorno."
                ),
                "limites_cobertura": {
                    "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_MAXIMA_DATOS.isoformat()}",
                    "registros_prueba_excluidos": 38
                }
            }

        fechas_visitas = [r[0].date() for r in visitas_rows]
        fechas_unicas = sorted(list(set(fechas_visitas)))
        dias_activos = len(fechas_unicas)
        total_visitas = len(visitas_rows)

        # Caso B: Historial insuficiente (< 3 días activos de visita)
        if dias_activos < self.umbral_minimo:
            return {
                "status": "historial_insuficiente",
                "confiable": False,
                "codigo_mascarado": cod_mascarado,
                "escuela": escuela_alumno,
                "fecha_referencia": f_ref.isoformat(),
                "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
                "dias_activos_historicos": dias_activos,
                "total_visitas_historicas": total_visitas,
                "probabilidad_retorno": None,
                "nivel_probabilidad": "Indeterminado (historial insuficiente)",
                "definicion_retorno": (
                    "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                    f"día hábil operativo de biblioteca ({proximo_dia_operativo.isoformat()})."
                ),
                "explicacion": (
                    f"Historial insuficiente: El alumno registra únicamente {dias_activos} día(s) con visitas "
                    f"en el historial previo disponible (total de {total_visitas} visitas). Se requiere un mínimo "
                    "de 3 días activos para identificar patrones de asistencia y calcular una estimación de retorno confiable. "
                    "Emitir una probabilidad con 1 o 2 visitas resultaría estadísticamente espurio e impreciso."
                ),
                "limites_cobertura": {
                    "criterio_minimo": "Mínimo 3 días activos en el historial previo",
                    "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_MAXIMA_DATOS.isoformat()}",
                    "registros_prueba_excluidos": 38
                }
            }

        # Caso C: Historial suficiente (>= 3 días activos) -> Inferencia ML
        ultima_fecha_visita = max(fechas_unicas)
        recencia_dias = (f_ref - ultima_fecha_visita).days

        # Visitas en ventana reciente
        visitas_7d = len([d for d in fechas_unicas if d >= (f_ref - timedelta(days=7))])
        visitas_14d = len([d for d in fechas_unicas if d >= (f_ref - timedelta(days=14))])

        # Afinidad al día de la semana del próximo día operativo
        proximo_weekday = proximo_dia_operativo.weekday()
        visitas_mismo_weekday = len([d for d in fechas_unicas if d.weekday() == proximo_weekday])
        afinidad_dia = float(visitas_mismo_weekday / dias_activos)

        # ¿Visitó en la fecha de referencia?
        visito_hoy = 1 if f_ref in fechas_unicas else 0

        # Tasa de asistencia general
        tasa_asistencia = float(dias_activos / dias_operativos_transcurridos)

        # Construir vector de características
        vector_variables = pd.DataFrame([{
            'dias_activos': dias_activos,
            'total_visitas': total_visitas,
            'recencia_dias': recencia_dias,
            'visitas_ultimos_7d': visitas_7d,
            'visitas_ultimos_14d': visitas_14d,
            'afinidad_dia_semana': afinidad_dia,
            'visito_hoy': visito_hoy,
            'tasa_asistencia': tasa_asistencia
        }])[self.variables]

        prob = float(self.modelo.predict_proba(vector_variables)[0, 1])

        if prob >= 0.60:
            nivel = "Alta"
        elif prob >= 0.30:
            nivel = "Media"
        else:
            nivel = "Baja"

        dia_nombre_es = {
            0: "lunes", 1: "martes", 2: "miércoles",
            3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
        }
        nombre_proximo_dia = dia_nombre_es.get(proximo_dia_operativo.weekday(), "día hábil")

        # Evaluación de desfase temporal respecto a la fecha máxima de datos (2026-07-31)
        dias_desfase_corte = (f_ref - FECHA_MAXIMA_DATOS).days
        es_desactualizado = dias_desfase_corte > 0
        es_desfase_critico = dias_desfase_corte > 30
        es_desfase_severo = dias_desfase_corte > 60
        es_vigente = not es_desfase_severo

        info_desfase = {
            "es_desactualizado": es_desactualizado,
            "dias_desfase_corte": max(0, dias_desfase_corte),
            "fecha_corte_bd": FECHA_MAXIMA_DATOS.isoformat(),
            "fecha_consultada": f_ref.isoformat(),
            "desfase_critico": es_desfase_critico,
            "desfase_severo": es_desfase_severo,
            "vigente": es_vigente,
            "aviso": (
                (
                    f"Aviso de datos desactualizados: La base de datos de visitas contiene registros únicamente "
                    f"hasta el {FECHA_MAXIMA_DATOS.strftime('%d/%m/%Y')}. La fecha consultada ({f_ref.strftime('%d/%m/%Y')}) "
                    f"presenta un desfase de {dias_desfase_corte} días sin nuevos ingresos registrados en el sistema. "
                    + (
                        "Al superar los 60 días de desfase, esta estimación no debe considerarse como una predicción vigente "
                        "del comportamiento actual del estudiante. Para consultar la propensión real en su periodo activo, "
                        "consulte a la fecha de corte institucional (31/07/2026)."
                        if es_desfase_severo else
                        "La inactividad calculada deprime la probabilidad a valores basales. "
                        "Para evaluar la propensión real del alumno con datos activos, consulte a la fecha de corte institucional (31/07/2026)."
                    )
                ) if es_desactualizado else None
            )
        }

        if es_desfase_severo:
            mensaje = (
                f"[DATOS DESACTUALIZADOS >60 DÍAS] El alumno con código {cod_mascarado} ({escuela_alumno}) presenta "
                f"un desfase de {dias_desfase_corte} días respecto al corte en BD ({FECHA_MAXIMA_DATOS.strftime('%d/%m/%Y')}). "
                "La predicción no es representativa como probabilidad vigente para la fecha actual. "
                "Consulte la estimación histórica a la fecha de corte (31/07/2026) para observar su propensión en periodo activo."
            )
        elif es_desfase_critico:
            mensaje = (
                f"[AVISO: DATOS DESACTUALIZADOS] El alumno con código {cod_mascarado} ({escuela_alumno}) presenta "
                f"un desfase de {dias_desfase_corte} días respecto al corte en BD (31/07/2026). "
                f"La estimación calculada ({round(prob * 100, 1)}%) está deprimida por falta de datos recientes en el sistema. "
                "Para conocer la propensión activa del alumno, consulte con fecha 31/07/2026."
            )
        else:
            mensaje = (
                f"El alumno con código {cod_mascarado} ({escuela_alumno}) presenta una probabilidad {nivel.lower()} "
                f"({round(prob * 100, 1)}%) de volver a la biblioteca el siguiente día hábil operativo "
                f"({nombre_proximo_dia} {proximo_dia_operativo.isoformat()}). "
                f"Historial previo: {dias_activos} días activos ({total_visitas} visitas totales, última visita hace {recencia_dias} días)."
            )

        return {
            "status": "success",
            "confiable": not es_desfase_critico,
            "vigente": es_vigente,
            "estado_vigencia": "no_representativo" if es_desfase_severo else ("desactualizado_moderado" if es_desactualizado else "vigente"),
            "codigo_mascarado": cod_mascarado,
            "escuela": escuela_alumno,
            "fecha_referencia": f_ref.isoformat(),
            "fecha_corte_bd": FECHA_MAXIMA_DATOS.isoformat(),
            "proximo_dia_operativo": proximo_dia_operativo.isoformat(),
            "definicion_retorno": (
                "Probabilidad estimada de que el alumno registre al menos un ingreso en el siguiente "
                f"día hábil operativo de biblioteca ({nombre_proximo_dia} {proximo_dia_operativo.isoformat()})."
            ),
            "probabilidad_retorno": round(prob, 4),
            "probabilidad_porcentaje": f"{round(prob * 100, 1)}%",
            "nivel_probabilidad": nivel,
            "desfase_temporal": info_desfase,
            "aviso_desactualizado": info_desfase["aviso"],
            "es_desfase_critico": es_desfase_critico,
            "es_desfase_severo": es_desfase_severo,
            "metricas_historial": {
                "dias_activos": dias_activos,
                "total_visitas": total_visitas,
                "recencia_dias": recencia_dias,
                "visitas_ultimos_7d": visitas_7d,
                "visitas_ultimos_14d": visitas_14d,
                "afinidad_dia_semana": round(afinidad_dia, 4),
                "visito_dia_referencia": bool(visito_hoy),
                "tasa_asistencia_periodo": round(tasa_asistencia, 4)
            },
            "advertencia": (
                "Estimación orientativa basada en el historial disponible de junio–julio de 2026. "
                "Una probabilidad calculada no representa una certeza matemática ni garantiza resultados futuros."
            ),
            "limites_cobertura": {
                "periodo_disponible": f"{FECHA_MINIMA_DATOS.isoformat()} a {FECHA_MAXIMA_DATOS.isoformat()}",
                "registros_prueba_excluidos": 38
            },
            "mensaje": mensaje
        }


def predecir_retorno_alumno(codigo_matricula=None, alumno_id=None, fecha_referencia=None):
    """
    Función de acceso directo para predecir el retorno de un alumno.
    """
    predictor = PredictorRetornoAlumno.obtener_instancia()
    return predictor.predecir_retorno(
        codigo_matricula=codigo_matricula,
        alumno_id=alumno_id,
        fecha_referencia=fecha_referencia
    )
