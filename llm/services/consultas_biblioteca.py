import re
from datetime import date, datetime
from db import get_db_cursor


ROLES_PERMITIDOS = {
    "SuperAdmin",
    "Supervisor",
    "Consultor"
}

SEDES_PERMITIDAS = {
    "Central",
    "Tarma",
    "La Merced",
    "Oxapampa",
    "Paucartambo",
    "Yanahuanca"
}


def _es_error_tabla_inexistente(error):
    """
    Determina si una excepción corresponde a una tabla no encontrada en SQL Server
    (Error 208, SQLSTATE 42S02 o mensaje 'Invalid object name').
    Compatible con pyodbc, pymssql y adaptadores DB-API.
    """
    if not error:
        return False

    msg = str(error).lower()
    if "invalid object name" in msg or "42s02" in msg:
        return True

    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate and str(sqlstate).upper() == "42S02":
        return True

    args = getattr(error, "args", ())
    for arg in args:
        if isinstance(arg, (list, tuple)):
            for sub_arg in arg:
                if sub_arg == 208 or str(sub_arg).strip() == "208" or str(sub_arg).upper() == "42S02":
                    return True
        elif arg == 208 or str(arg).strip() == "208" or str(arg).upper() == "42S02":
            return True

    return False


def escapar_like(texto):
    """
    Escapa caracteres comodín de SQL LIKE (%, _, [, \\).
    """
    if not texto:
        return ""
    res = str(texto).replace("\\", "\\\\")
    res = res.replace("%", "\\%")
    res = res.replace("_", "\\_")
    res = res.replace("[", "\\[")
    return res


def enmascarar_codigo(codigo):
    """
    Enmascara parcialmente el código de matrícula para no exponer datos sensibles.
    Ejemplo: 2020123456 -> 20******56
    """
    if not codigo:
        return None
    c = str(codigo).strip()
    if len(c) <= 3:
        return "***"
    elif len(c) <= 6:
        return c[0] + "*" * (len(c) - 2) + c[-1]
    else:
        return c[:2] + "*" * (len(c) - 4) + c[-2:]


def convertir_fecha(fecha):
    """
    Convierte un string YYYY-MM-DD, date o datetime a date.
    """
    if isinstance(fecha, datetime):
        return fecha.date()
    if isinstance(fecha, date):
        return fecha
    try:
        return datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError("La fecha debe tener el formato YYYY-MM-DD.")


def validar_rol(rol):
    """
    Valida que el rol pertenezca a la lista blanca autorizada.
    """
    if not rol or str(rol).strip() not in ROLES_PERMITIDOS:
        raise PermissionError("Acceso denegado. Rol administrativo no reconocido o no autorizado.")
    return str(rol).strip()


def buscar_personas(nombre, limite=10):
    """
    Busca personas por nombre en Alumnos, Visitantes, Egresados,
    PersonalAdministrativo y Docentes.

    - Consulta parametrizada con LIKE y caracteres comodín escapados.
    - Máximo 10 resultados.
    - No devuelve DNI, correo ni teléfono.
    - Ordena coincidencias exactas primero, luego parciales.
    """
    if not nombre or not isinstance(nombre, str):
        raise ValueError("El nombre a buscar es obligatorio.")

    nombre_limpio = nombre.strip()
    if len(nombre_limpio) < 3 or len(nombre_limpio) > 120:
        raise ValueError("El nombre debe tener entre 3 y 120 caracteres.")

    if isinstance(limite, bool):
        raise ValueError("El límite debe ser un número entero.")

    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = 10
    limite = max(1, min(limite, 10))

    nombre_escapado = escapar_like(nombre_limpio)
    patron_like = f"%{nombre_escapado}%"

    consultas = [
        ("""
            SELECT 'Alumno' AS tipo_persona, AlumnoID AS persona_id, NombreCompleto AS nombre, CodigoMatricula AS codigo
            FROM Alumnos
            WHERE NombreCompleto LIKE ? ESCAPE '\\'
        """, patron_like),

        ("""
            SELECT 'Visitante' AS tipo_persona, VisitanteID AS persona_id, NombreCompleto AS nombre, NULL AS codigo
            FROM Visitantes
            WHERE NombreCompleto LIKE ? ESCAPE '\\'
        """, patron_like),

        ("""
            SELECT 'Egresado' AS tipo_persona, EgresadoID AS persona_id, NombreCompleto AS nombre, CodigoMatricula AS codigo
            FROM Egresados
            WHERE NombreCompleto LIKE ? ESCAPE '\\'
        """, patron_like),

        ("""
            SELECT 'PersonalAdministrativo' AS tipo_persona, PersonalID AS persona_id, ApellidosNombres AS nombre, NULL AS codigo
            FROM PersonalAdministrativo
            WHERE ApellidosNombres LIKE ? ESCAPE '\\'
        """, patron_like),

        ("""
            SELECT 'Docente' AS tipo_persona, DocenteID AS persona_id, ApellidosNombres AS nombre, NULL AS codigo
            FROM Docentes
            WHERE ApellidosNombres LIKE ? ESCAPE '\\'
        """, patron_like)
    ]

    resultados_raw = []

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        for sql, param in consultas:
            try:
                cursor.execute(sql, (param,))
                filas = cursor.fetchall()
                for f in filas:
                    resultados_raw.append({
                        "tipo_persona": f[0],
                        "persona_id": int(f[1]),
                        "nombre": str(f[2]).strip(),
                        "codigo_mascarado": enmascarar_codigo(f[3])
                    })
            except Exception as e:
                if _es_error_tabla_inexistente(e):
                    continue
                raise

    nombre_lower = nombre_limpio.lower()

    def criterio_orden(item):
        item_nom = item["nombre"].lower()
        if item_nom == nombre_lower:
            prioridad = 0
        elif item_nom.startswith(nombre_lower):
            prioridad = 1
        else:
            prioridad = 2
        return (prioridad, item_nom)

    resultados_ordenados = sorted(resultados_raw, key=criterio_orden)
    return resultados_ordenados[:limite]


def obtener_historial_persona(tipo_persona, persona_id, rol, sede_permitida=None, limite=50):
    """
    Obtiene el historial de ingresos de una persona específica combinando
    la tabla de producción (RegistroIngresos) y la histórica (RegistroIngresos_Historico).

    - Bloquea al rol Consultor.
    - Rechaza roles desconocidos o None.
    - Forza al Supervisor a su sede permitida.
    - No devuelve datos sensibles de contacto ni DNI.
    - Para Docentes sólo consulta RegistroIngresos.
    """
    rol_valido = validar_rol(rol)

    if rol_valido == "Consultor":
        raise PermissionError("Acceso denegado. El rol Consultor no tiene permiso para consultar historiales personales.")

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_consulta = str(sede_permitida).strip() if sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS else None

    tipos_validos = {"Alumno", "Visitante", "Egresado", "PersonalAdministrativo", "Docente"}
    if tipo_persona not in tipos_validos:
        raise ValueError(f"Tipo de persona inválido. Tipos permitidos: {', '.join(sorted(tipos_validos))}")

    if isinstance(persona_id, bool):
        raise ValueError("El ID de persona debe ser un número entero positivo.")

    try:
        persona_id = int(persona_id)
        if persona_id <= 0:
            raise ValueError("El ID de persona debe ser un número entero positivo.")
    except (TypeError, ValueError):
        raise ValueError("El ID de persona debe ser un número entero positivo.")

    if isinstance(limite, bool):
        raise ValueError("El límite debe ser un número entero.")

    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = 50
    limite = max(1, min(limite, 50))

    columna_id_map = {
        "Alumno": "AlumnoID",
        "Visitante": "VisitanteID",
        "Egresado": "EgresadoID",
        "PersonalAdministrativo": "PersonalID",
        "Docente": "DocenteID"
    }
    col_id = columna_id_map[tipo_persona]

    filtros_actual = [f"R.{col_id} = ?"]
    params_actual = [persona_id]

    # Excluir registros de prueba marcados reversiblemente
    filtros_actual.append("ISNULL(R.EsPrueba, 0) = 0")

    if sede_consulta:
        filtros_actual.append("ISNULL(R.Sede, 'Central') = ?")
        params_actual.append(sede_consulta)

    where_actual = " AND ".join(filtros_actual)

    sql_actual = f"""
        SELECT R.RegistroID, R.FechaHora, R.Turno, ISNULL(R.Sede, 'Central') AS Sede, R.Piso, 'actual' AS Fuente, R.SalaID, R.DocenteID, R.TipoUsuario
        FROM RegistroIngresos R
        WHERE {where_actual}
        ORDER BY R.FechaHora DESC
    """

    filas_raw = []

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        # 1. Ejecutar registros actuales
        cursor.execute(sql_actual, tuple(params_actual))
        filas_actuales = cursor.fetchall()
        filas_raw.extend(filas_actuales)

        # 2. Ejecutar registros históricos solo si no es Docente
        if tipo_persona != "Docente":
            filtros_hist = [f"H.{col_id} = ?"]
            params_hist = [persona_id]
            if sede_consulta:
                filtros_hist.append("ISNULL(H.Sede, 'Central') = ?")
                params_hist.append(sede_consulta)

            where_hist = " AND ".join(filtros_hist)
            sql_historico = f"""
                SELECT H.RegistroID, H.FechaHora, H.Turno, ISNULL(H.Sede, 'Central') AS Sede, H.Piso, 'historico' AS Fuente, NULL AS SalaID, NULL AS DocenteID, '{tipo_persona}' AS TipoUsuario
                FROM RegistroIngresos_Historico H
                WHERE {where_hist}
                ORDER BY H.FechaHora DESC
            """
            try:
                cursor.execute(sql_historico, tuple(params_hist))
                filas_historicas = cursor.fetchall()
                filas_raw.extend(filas_historicas)
            except Exception as e:
                if _es_error_tabla_inexistente(e):
                    pass
                else:
                    raise

    # Deduplicar por registro_id priorizando 'actual' sobre 'historico'
    registros_dict = {}
    for f in filas_raw:
        reg_id = int(f[0])
        if reg_id not in registros_dict or f[5] == "actual":
            registros_dict[reg_id] = f

    filas_unicas = list(registros_dict.values())
    filas_unicas.sort(key=lambda x: x[1] if x[1] else datetime.min, reverse=True)

    total_ingresos = len(filas_unicas)
    primera_fecha = None
    ultima_fecha = None
    registros = []

    if filas_unicas:
        fechas_ordenadas = sorted([f[1] for f in filas_unicas if f[1]])
        if fechas_ordenadas:
            primera_fecha = fechas_ordenadas[0].isoformat() if isinstance(fechas_ordenadas[0], (datetime, date)) else str(fechas_ordenadas[0])
            ultima_fecha = fechas_ordenadas[-1].isoformat() if isinstance(fechas_ordenadas[-1], (datetime, date)) else str(fechas_ordenadas[-1])

        for f in filas_unicas[:limite]:
            fecha_val = f[1].isoformat() if isinstance(f[1], (datetime, date)) else str(f[1])
            registros.append({
                "registro_id": int(f[0]),
                "fecha_hora": fecha_val,
                "turno": f[2],
                "sede": f[3],
                "piso": f[4],
                "fuente": f[5],
                "sala_id": f[6],
                "docente_id": f[7],
                "tipo_usuario": f[8]
            })

    return {
        "status": "success",
        "tipo_persona": tipo_persona,
        "persona_id": persona_id,
        "total_ingresos": total_ingresos,
        "primera_fecha": primera_fecha,
        "ultima_fecha": ultima_fecha,
        "registros": registros,
        "referencia_horaria": "Hora almacenada en la base de datos",
        "advertencia": "La fecha y hora corresponden al valor almacenado en SQL Server sin conversión horaria."
    }


def obtener_resumen_ingresos(fecha_inicio, fecha_fin, rol, sede_permitida=None):
    """
    Obtiene el resumen de ingresos agregados entre dos fechas.
    - SuperAdmin y Consultor consultan todas las sedes o la sede especificada.
    - Supervisor queda obligado a su sede permitida.
    - Límite máximo de rango: 366 días.
    """
    rol_valido = validar_rol(rol)

    f_inicio = convertir_fecha(fecha_inicio)
    f_fin = convertir_fecha(fecha_fin)

    if f_inicio > f_fin:
        raise ValueError("La fecha de inicio debe ser anterior o igual a la fecha de fin.")

    if (f_fin - f_inicio).days > 366:
        raise ValueError("El rango de fechas no puede ser mayor a 366 días.")

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_consulta = str(sede_permitida).strip() if sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS else None

    filtros_actual = ["CAST(R.FechaHora AS DATE) BETWEEN ? AND ?"]
    params_actual = [f_inicio.isoformat(), f_fin.isoformat()]
    filtros_actual.append("ISNULL(R.EsPrueba, 0) = 0")
    if sede_consulta:
        filtros_actual.append("ISNULL(R.Sede, 'Central') = ?")
        params_actual.append(sede_consulta)

    sql_actual = f"""
        SELECT ISNULL(R.Sede, 'Central') AS Sede,
               ISNULL(R.TipoUsuario, 'Alumno') AS TipoPersona
        FROM RegistroIngresos R
        WHERE {" AND ".join(filtros_actual)}
    """

    filas = []

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        cursor.execute(sql_actual, tuple(params_actual))
        filas.extend(cursor.fetchall())

        filtros_hist = ["CAST(H.FechaHora AS DATE) BETWEEN ? AND ?"]
        params_hist = [f_inicio.isoformat(), f_fin.isoformat()]
        if sede_consulta:
            filtros_hist.append("ISNULL(H.Sede, 'Central') = ?")
            params_hist.append(sede_consulta)

        sql_historico = f"""
            SELECT ISNULL(H.Sede, 'Central') AS Sede,
                   CASE
                       WHEN H.AlumnoID IS NOT NULL THEN 'Alumno'
                       WHEN H.VisitanteID IS NOT NULL THEN 'Visitante'
                       WHEN H.EgresadoID IS NOT NULL THEN 'Egresado'
                       WHEN H.PersonalID IS NOT NULL THEN 'PersonalAdministrativo'
                       ELSE 'Alumno'
                   END AS TipoPersona
            FROM RegistroIngresos_Historico H
            WHERE {" AND ".join(filtros_hist)}
        """
        try:
            cursor.execute(sql_historico, tuple(params_hist))
            filas.extend(cursor.fetchall())
        except Exception as e:
            if _es_error_tabla_inexistente(e):
                pass
            else:
                raise

    ingresos_por_sede = {}
    ingresos_por_tipo = {}

    total_ingresos = len(filas)

    for f in filas:
        s = f[0] or "Central"
        t = f[1] or "Otros"
        ingresos_por_sede[s] = ingresos_por_sede.get(s, 0) + 1
        ingresos_por_tipo[t] = ingresos_por_tipo.get(t, 0) + 1

    return {
        "status": "success",
        "fecha_inicio": f_inicio.isoformat(),
        "fecha_fin": f_fin.isoformat(),
        "dias_rango": (f_fin - f_inicio).days + 1,
        "total_ingresos": total_ingresos,
        "ingresos_por_sede": ingresos_por_sede,
        "ingresos_por_tipo_persona": ingresos_por_tipo,
        "referencia_horaria": "Hora almacenada en la base de datos"
    }


def obtener_horas_mayor_afluencia(fecha_inicio, fecha_fin, rol, sede_permitida=None, limite=5):
    """
    Obtiene las horas de mayor afluencia en un rango de fechas.
    - SuperAdmin y Consultor consultan todas las sedes o la sede especificada.
    - Supervisor queda obligado a su sede permitida.
    - Límite máximo de rango: 366 días.
    """
    rol_valido = validar_rol(rol)

    f_inicio = convertir_fecha(fecha_inicio)
    f_fin = convertir_fecha(fecha_fin)

    if f_inicio > f_fin:
        raise ValueError("La fecha de inicio debe ser anterior o igual a la fecha de fin.")

    if (f_fin - f_inicio).days > 366:
        raise ValueError("El rango de fechas no puede ser mayor a 366 días.")

    if isinstance(limite, bool):
        raise ValueError("El límite debe ser un número entero.")

    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = 5
    limite = max(1, min(limite, 24))

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_consulta = str(sede_permitida).strip() if sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS else None

    filtros_actual = ["CAST(R.FechaHora AS DATE) BETWEEN ? AND ?"]
    params_actual = [f_inicio.isoformat(), f_fin.isoformat()]
    filtros_actual.append("ISNULL(R.EsPrueba, 0) = 0")
    if sede_consulta:
        filtros_actual.append("ISNULL(R.Sede, 'Central') = ?")
        params_actual.append(sede_consulta)

    sql_actual = f"""
        SELECT DATEPART(HOUR, FechaHora) AS Hora, CAST(FechaHora AS DATE) AS Fecha
        FROM RegistroIngresos R
        WHERE {" AND ".join(filtros_actual)}
    """

    filas = []

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        cursor.execute(sql_actual, tuple(params_actual))
        filas.extend(cursor.fetchall())

        filtros_hist = ["CAST(H.FechaHora AS DATE) BETWEEN ? AND ?"]
        params_hist = [f_inicio.isoformat(), f_fin.isoformat()]
        if sede_consulta:
            filtros_hist.append("ISNULL(H.Sede, 'Central') = ?")
            params_hist.append(sede_consulta)

        sql_historico = f"""
            SELECT DATEPART(HOUR, FechaHora) AS Hora, CAST(FechaHora AS DATE) AS Fecha
            FROM RegistroIngresos_Historico H
            WHERE {" AND ".join(filtros_hist)}
        """
        try:
            cursor.execute(sql_historico, tuple(params_hist))
            filas.extend(cursor.fetchall())
        except Exception as e:
            if _es_error_tabla_inexistente(e):
                pass
            else:
                raise

    conteo_horas = {}
    fechas_con_datos = set()

    for f in filas:
        h = int(f[0])
        fechas_con_datos.add(f[1])
        conteo_horas[h] = conteo_horas.get(h, 0) + 1

    dias_con_datos = len(fechas_con_datos) if fechas_con_datos else 1

    horas_ordenadas = sorted(conteo_horas.items(), key=lambda x: x[1], reverse=True)[:limite]

    horas_top = []
    for h, total in horas_ordenadas:
        promedio = round(total / dias_con_datos, 2)
        horas_top.append({
            "hora": h,
            "hora_formato": f"{h:02d}:00 - {h+1:02d}:00",
            "total_ingresos": total,
            "promedio_diario": promedio
        })

    return {
        "status": "success",
        "fecha_inicio": f_inicio.isoformat(),
        "fecha_fin": f_fin.isoformat(),
        "dias_con_registros": len(fechas_con_datos),
        "limite": limite,
        "horas_top": horas_top,
        "referencia_horaria": "Hora almacenada en la base de datos"
    }


def consultar_afluencia_historica(
    fecha_inicio,
    fecha_fin,
    sede="Central",
    rol=None,
    sede_permitida=None
):
    """
    Herramienta analítica de consulta descriptiva de afluencia histórica.
    Une AHA con franjas recientes no representadas de RegistroIngresos, mediante SELECT agregados.

    - Valida parámetros de fechas (YYYY-MM-DD) y sede.
    - Respeta roles (Supervisor forzado a su sede_permitida).
    - Evalúa cobertura mínima: la sede Central puede usar el historial disponible;
      Tarma y demás sedes devuelven explícitamente 'datos insuficientes' (ValueError).
    - Responde indicando periodo, sede, fuente y límites de cobertura.
    - Excluye filas atípicas, de vacaciones de agosto, feriados y cierre del 27/07.
    - Indica el tratamiento de la franja parcial de las 20:00 (45 minutos).
    - Las franjas no observadas en el archivo fuente NO se interpretan como cero.
    - No genera visitas sintéticas ni atribuye alumnos a los conteos agregados.
    """
    rol_valido = validar_rol(rol)

    f_inicio = convertir_fecha(fecha_inicio)
    f_fin = convertir_fecha(fecha_fin)

    if f_inicio > f_fin:
        raise ValueError("La fecha de inicio debe ser anterior o igual a la fecha de fin.")

    if (f_fin - f_inicio).days > 366:
        raise ValueError("El rango de fechas no puede ser mayor a 366 días.")

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_str = str(sede).strip() if sede else "Central"
        if sede_str not in SEDES_PERMITIDAS:
            raise ValueError(f"La sede '{sede_str}' no es válida. Sedes permitidas: {', '.join(sorted(SEDES_PERMITIDAS))}.")
        sede_consulta = sede_str

    sql_suficiencia = """
        SELECT
            COUNT(DISTINCT Fecha) AS total_fechas,
            COUNT(*) AS total_franjas,
            SUM(CantidadIngresos) AS total_ingresos,
            MIN(Fecha) AS min_fecha,
            MAX(Fecha) AS max_fecha
        FROM dbo.AfluenciaHistoricaAgregada
        WHERE Sede = ?
          AND EsHorarioAtipico = 0
          AND EsCierreAgosto = 0
          AND EsCierre27Julio = 0
          AND EsFeriado = 0
    """

    sql_rango = """
        SELECT
            Fecha,
            Hora,
            CantidadIngresos,
            EsFranjaParcial
        FROM dbo.AfluenciaHistoricaAgregada
        WHERE Sede = ?
          AND Fecha BETWEEN ? AND ?
          AND EsHorarioAtipico = 0
          AND EsCierreAgosto = 0
          AND EsCierre27Julio = 0
          AND EsFeriado = 0
        ORDER BY Fecha ASC, Hora ASC
    """

    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        cursor.execute(sql_suficiencia, (sede_consulta,))
        fila_suf = cursor.fetchone()

        total_fechas_sede = int(fila_suf[0]) if fila_suf and fila_suf[0] is not None else 0
        min_sistema = fila_suf[3].strftime("%Y-%m-%d") if fila_suf and fila_suf[3] else None
        max_sistema = fila_suf[4].strftime("%Y-%m-%d") if fila_suf and fila_suf[4] else None

        # Criterio operativo mínimo: al menos 15 fechas operativas registradas
        if total_fechas_sede < 15:
            raise ValueError(
                f"Historial insuficiente: La sede '{sede_consulta}' cuenta con {total_fechas_sede} fecha(s) "
                "operativa(s) registrada(s) en la base histórica analítica, no alcanzando el mínimo "
                "configurado de 15 fechas operativas para generar un análisis confiable. "
                "No se completa con registros de prueba de RegistroIngresos."
            )

        cursor.execute(sql_rango, (sede_consulta, f_inicio.isoformat(), f_fin.isoformat()))
        filas_rango = cursor.fetchall()
        from llm.services.fuente_afluencia_asistente import unir_recientes
        filas_rango, cobertura = unir_recientes(cursor, filas_rango, max_sistema, sede_consulta, f_inicio, f_fin)
        max_sistema = cobertura['ultimo_dia_con_datos']

    total_ingresos = sum(int(r[2]) for r in filas_rango)
    franjas_observadas = len(filas_rango)
    fechas_con_datos = sorted(list(set(r[0] for r in filas_rango)))
    dias_con_registros = len(fechas_con_datos)

    # Identificar franjas parciales de las 20:00 (45 minutos de atención efectiva)
    franjas_20h = [r for r in filas_rango if int(r[1]) == 20 or (len(r) > 3 and bool(r[3]))]
    total_franjas_20h = len(franjas_20h)
    ingresos_20h = sum(int(r[2]) for r in franjas_20h)

    promedio_diario = round(total_ingresos / dias_con_registros, 2) if dias_con_registros > 0 else 0.0
    promedio_franja = round(total_ingresos / franjas_observadas, 2) if franjas_observadas > 0 else 0.0

    # Distribución por hora observada
    conteo_por_hora = {}
    for r in filas_rango:
        h = int(r[1])
        conteo_por_hora[h] = conteo_por_hora.get(h, 0) + int(r[2])

    horas_top = sorted(
        [{"hora": h, "hora_formato": f"{h:02d}:00 - {h+1:02d}:00" if h != 20 else "20:00 - 20:45 (parcial)", "ingresos": cnt} for h, cnt in conteo_por_hora.items()],
        key=lambda x: x["ingresos"],
        reverse=True
    )

    if franjas_observadas > 0:
        aviso_20h = f" Incluye {total_franjas_20h} franja(s) a las 20:00 con {ingresos_20h} ingresos (franja operativa parcial de 45 minutos hasta las 20:45)." if total_franjas_20h > 0 else ""
        mensaje = (
            f"Consulta descriptiva de afluencia histórica para la sede {sede_consulta} "
            f"del {f_inicio.isoformat()} al {f_fin.isoformat()}: Total de {total_ingresos} ingresos observados "
            f"en {franjas_observadas} franja(s) horaria(s) a lo largo de {dias_con_registros} día(s) con registros "
            f"(promedio de {promedio_diario} ingresos/día con registros y {promedio_franja} ingresos/franja observada).{aviso_20h} "
            f"Fuente: SQL Server, AfluenciaHistoricaAgregada + RegistroIngresos (SELECT agregados, sin duplicar claves). "
            f"Límites de cobertura del sistema: Registros disponibles entre el {min_sistema} y el {max_sistema} en horario de 08:00 a 20:45. "
            f"Aviso metodológico: Las franjas horarias no observadas representan intervalos no capturados o no registrados por el sistema "
            "y NO se interpretan automáticamente como cero ingresos ni como ausencia real de visitas."
        )
    else:
        mensaje = (
            f"No se encontraron franjas en las fuentes consultadas para la sede {sede_consulta} en el periodo "
            f"del {f_inicio.isoformat()} al {f_fin.isoformat()}. "
            f"Fuente: SQL Server, AfluenciaHistoricaAgregada + RegistroIngresos (SELECT agregados). Esto no demuestra ausencia de ingresos ni captura completa. "
            f"Límites de cobertura del sistema: Registros disponibles entre el {min_sistema} y el {max_sistema} "
            "(excluyendo cierres institucionales como el 27/07, vacaciones del 01/08 al 23/08 y feriados nacionales). "
            f"Aviso metodológico: Las franjas no observadas no se interpretan automáticamente como cero."
        )

    if cobertura['estado'] == 'parcial':
        mensaje = 'PERIODO PARCIAL: no se acredita captura completa de todo el periodo solicitado. ' + mensaje
    mensaje += ' Último día observado dentro del periodo: ' + (cobertura['ultimo_dia_observado_en_periodo'] or 'sin observaciones verificables') + '.'
    mensaje += ' Último día con datos en las fuentes: ' + (max_sistema or 'no verificable') + '. Confirmación de captura: operativa del responsable, no verificación técnica independiente.'

    return {
        "cobertura": cobertura,
        "status": "success",
        "herramienta": "consulta_afluencia_historica",
        "periodo": {
            "fecha_inicio": f_inicio.isoformat(),
            "fecha_fin": f_fin.isoformat(),
            "dias_calendario": (f_fin - f_inicio).days + 1
        },
        "sede": sede_consulta,
        "fuente": "SQL Server: dbo.AfluenciaHistoricaAgregada + dbo.RegistroIngresos (SELECT agregados, AHA prioritaria por Fecha/Hora/Sede)",
        "limites_cobertura": {
            "fecha_minima_sistema": min_sistema,
            "fecha_maxima_sistema": max_sistema,
            "estado": cobertura["estado"],
            "captura_completa_confirmada": cobertura["captura_completa_confirmada"],
            "horario_operativo": "08:00 a 20:45 (lunes a viernes)",
            "franja_20h_parcial": "20:00 a 20:45 (45 minutos de horario operativo)",
            "sedes_con_historial_suficiente": [sede_consulta],
            "sedes_con_datos_insuficientes": [],
            "sedes_no_evaluadas": sorted(SEDES_PERMITIDAS - {sede_consulta})
        },
        "metricas": {
            "total_ingresos": total_ingresos,
            "franjas_observadas": franjas_observadas,
            "dias_con_registros": dias_con_registros,
            "promedio_por_dia_activo": promedio_diario,
            "promedio_por_franja_observada": promedio_franja,
            "franjas_parciales_20h": total_franjas_20h,
            "ingresos_franjas_parciales_20h": ingresos_20h,
            "horas_top": horas_top[:5]
        },
        "interpretacion_franjas_no_observadas": {
            "asume_cero": False,
            "aviso": "Las franjas no observadas corresponden a intervalos no registrados en el archivo fuente y NO se asumen como cero visitas."
        },
        "mensaje": mensaje
    }


def contar_ingresos_alumno_por_codigo(
    codigo_matricula,
    fecha_inicio=None,
    fecha_fin=None,
    rol=None,
    sede_permitida=None,
    informe=False
):
    """
    Cuenta los ingresos de un alumno específico por código de matrícula
    utilizando una consulta parametrizada sobre RegistroIngresos y Alumnos.

    - Consulta parametrizada obligatoria con marcadores '?' (sin concatenación de SQL).
    - Valida roles y restringe a Supervisor a su sede permitida si corresponde.
    - Bloquea al rol Consultor (protección de informes individuales).
    - No expone DNI, correo ni información confidencial; enmascara el código de matrícula.
    - Devuelve el conteo de visitas y comunica explícitamente el periodo global
      que cubren los datos individuales del sistema.
    """
    rol_valido = validar_rol(rol)

    if rol_valido == "Consultor":
        raise PermissionError("Acceso denegado. El rol Consultor no tiene permiso para consultar informes individuales por alumno.")

    if not codigo_matricula or not isinstance(codigo_matricula, str):
        raise ValueError("El código de matrícula es obligatorio.")

    codigo_limpio = codigo_matricula.strip()
    if len(codigo_limpio) < 3 or len(codigo_limpio) > 30:
        raise ValueError("El código de matrícula debe tener entre 3 y 30 caracteres.")

    f_inicio = convertir_fecha(fecha_inicio) if fecha_inicio else None
    f_fin = convertir_fecha(fecha_fin) if fecha_fin else None

    if f_inicio and f_fin and f_inicio > f_fin:
        raise ValueError("La fecha de inicio debe ser anterior o igual a la fecha de fin.")

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_consulta = str(sede_permitida).strip() if sede_permitida and str(sede_permitida).strip() in SEDES_PERMITIDAS else None

    filtros = [
        "A.CodigoMatricula = ?",
        "ISNULL(R.EsPrueba, 0) = 0"
    ]
    params = [codigo_limpio]

    if f_inicio:
        filtros.append("CAST(R.FechaHora AS DATE) >= ?")
        params.append(f_inicio.isoformat())
    if f_fin:
        filtros.append("CAST(R.FechaHora AS DATE) <= ?")
        params.append(f_fin.isoformat())
    if sede_consulta:
        filtros.append("R.Sede = ?")
        params.append(sede_consulta)

    where_sql = " AND ".join(filtros)

    sql_conteo = f"""
        SELECT
            COUNT(*) AS total_ingresos,
            MIN(R.FechaHora) AS primer_ingreso,
            MAX(R.FechaHora) AS ultimo_ingreso,
            COUNT(DISTINCT CAST(R.FechaHora AS DATE)) AS dias_con_visita,
            MAX(ISNULL(A.Escuela, '')) AS escuela
        FROM RegistroIngresos R
        INNER JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
        WHERE {where_sql}
    """

    sql_cobertura_global = """
        SELECT
            MIN(CASE WHEN ISNULL(EsPrueba, 0) = 0 THEN FechaHora END) AS fecha_minima,
            MAX(CASE WHEN ISNULL(EsPrueba, 0) = 0 THEN FechaHora END) AS fecha_maxima,
            COUNT(*) AS total_registros,
            SUM(CASE WHEN ISNULL(EsPrueba, 0) = 1 THEN 1 ELSE 0 END) AS total_pruebas,
            SUM(CASE WHEN ISNULL(EsPrueba, 0) = 0 THEN 1 ELSE 0 END) AS total_reales
        FROM RegistroIngresos
    """

    distribuciones = {}
    with get_db_cursor(autocommit=True) as (_, cursor):
        if cursor is None:
            raise ConnectionError("No fue posible conectarse a SQL Server.")

        cursor.execute(sql_conteo, tuple(params))
        fila_conteo = cursor.fetchone()

        # Auditar pruebas de la misma sede, manteniendo el corte solo de visitas válidas.
        # El conteo individual y sus distribuciones conservan su exclusión de pruebas.
        params_cobertura = ()
        if sede_consulta:
            sql_cobertura_global += " WHERE Sede = ?"
            params_cobertura = (sede_consulta,)
        cursor.execute(sql_cobertura_global, params_cobertura)
        fila_cob = cursor.fetchone()

        if informe:
            # Lista cerrada de expresiones SQL; ningún texto del usuario forma SQL.
            grupos = {
                'mensual': "CONVERT(char(7), R.FechaHora, 120)",
                'dias_semana': "((DATEDIFF(day, '19000101', CAST(R.FechaHora AS date)) % 7 + 7) % 7)",
                'horarios': "DATEPART(hour, R.FechaHora)",
                'salas': "COALESCE(NULLIF(LTRIM(RTRIM(S.NombreSala)), ''), 'Sala no identificada')",
            }
            for nombre, expresion in grupos.items():
                union_sala = 'LEFT JOIN Salas S ON S.SalaID = R.SalaID AND S.Sede = R.Sede' if nombre == 'salas' else ''
                cursor.execute(f"""SELECT {expresion} AS grupo, COUNT(*) AS visitas
                    FROM RegistroIngresos R INNER JOIN Alumnos A ON R.AlumnoID = A.AlumnoID
                    {union_sala} WHERE {where_sql} GROUP BY {expresion} ORDER BY grupo""", tuple(params))
                distribuciones[nombre] = [{'grupo': f[0], 'visitas': int(f[1])} for f in cursor.fetchall()]
                if nombre in {'salas', 'horarios'}:
                    distribuciones[nombre].sort(key=lambda fila: (-fila['visitas'], str(fila['grupo'])))

    total_ingresos = int(fila_conteo[0]) if fila_conteo and fila_conteo[0] is not None else 0
    primer_ingreso = fila_conteo[1].isoformat() if fila_conteo and fila_conteo[1] else None
    ultimo_ingreso = fila_conteo[2].isoformat() if fila_conteo and fila_conteo[2] else None
    dias_visita = int(fila_conteo[3]) if fila_conteo and fila_conteo[3] is not None else 0
    escuela = str(fila_conteo[4]).strip() if fila_conteo and fila_conteo[4] else ""

    min_global = fila_cob[0].strftime("%Y-%m-%d") if fila_cob and fila_cob[0] else None
    max_global = fila_cob[1].strftime("%Y-%m-%d") if fila_cob and fila_cob[1] else None
    total_global = int(fila_cob[2]) if fila_cob and fila_cob[2] is not None else 0
    total_pruebas = int(fila_cob[3]) if fila_cob and len(fila_cob) > 3 and fila_cob[3] is not None else 0
    total_reales = int(fila_cob[4]) if fila_cob and len(fila_cob) > 4 and fila_cob[4] is not None else (total_global - total_pruebas)

    codigo_mask = enmascarar_codigo(codigo_limpio)

    rango_txt = f"del {f_inicio.isoformat()} al {f_fin.isoformat()}" if (f_inicio and f_fin) else (
        f"desde {f_inicio.isoformat()}" if f_inicio else (
            f"hasta {f_fin.isoformat()}" if f_fin else "en el historial disponible"
        )
    )

    if total_reales == 0:
        nota_auditoria = (
            f"Nota de auditoría: El sistema contiene 0 visitas reales registradas (aún no hay visitas individuales reales registradas en el sistema). "
            f"Se identificaron y excluyeron {total_pruebas} registros manuales de prueba para garantizar la veracidad del conteo. "
            "No se generan registros ni se atribuyen alumnos a los conteos agregados de molinetes."
        )
    elif total_pruebas > 0:
        nota_auditoria = (
            f"Nota de auditoría: El sistema contiene {total_reales} visitas reales registradas. "
            f"Se identificaron y excluyeron {total_pruebas} registros manuales de prueba para garantizar la veracidad del conteo."
        )
    else:
        nota_auditoria = (
            f"Nota de cobertura: La base de datos contiene {total_reales} visitas reales registradas entre el {min_global} y el {max_global}."
        )

    corte_legible = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(max_global)) if max_global else 'sin datos'
    periodo_info = f"Periodo consultado: {rango_txt}." if (f_inicio or f_fin) else "Periodo consultado: todo el historial disponible."
    sede_etiqueta = f"Sede: {sede_consulta}." if sede_consulta else "Todas las sedes."
    if total_ingresos > 0:
        mensaje = (
            f"El alumno con código {codigo_mask} registra {total_ingresos} ingreso(s) real(es) {rango_txt} "
            f"en {dias_visita} fecha(s) distinta(s). {periodo_info} {sede_etiqueta} "
            f"Primer ingreso: {primer_ingreso}. Último ingreso: {ultimo_ingreso}. "
            f"Corte observado de la fuente: {corte_legible}. "
            f"{nota_auditoria}"
        )
    else:
        mensaje = (
            f"No se encontraron ingresos reales registrados para el alumno con código {codigo_mask} {rango_txt}. "
            f"{periodo_info} {sede_etiqueta} Corte observado de la fuente: {corte_legible}. "
            f"{nota_auditoria}"
        )

    resultado = {
        "status": "success",
        "codigo_mascarado": codigo_mask,
        "total_ingresos": total_ingresos,
        "dias_con_visita": dias_visita,
        "primer_ingreso": primer_ingreso,
        "ultimo_ingreso": ultimo_ingreso,
        "escuela": escuela,
        "periodo_consultado": {
            "fecha_inicio": f_inicio.isoformat() if f_inicio else None,
            "fecha_fin": f_fin.isoformat() if f_fin else None
        },
        "fuente": "RegistroIngresos (registros individuales de acceso por carnet/DNI)",
        "cobertura_sistema": {
            "fecha_minima": min_global,
            "fecha_maxima": max_global,
            "total_visitas_sistema": total_global,
            "total_pruebas_excluidas": total_pruebas,
            "total_visitas_reales": total_reales,
            "aviso": f"Los registros individuales del sistema corresponden al periodo {min_global} a {max_global}."
        },
        "mensaje": mensaje
    }
    if informe:
        resultado.update(distribuciones)
        resultado['estado'] = 'verificado_con_ingresos' if total_ingresos else 'sin_ingresos_verificables'
        resultado['fecha_corte'] = max_global
        resultado['fuente'] = 'RegistroIngresos + Alumnos; solo accesos no marcados como prueba'
        primer_legible = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(primer_ingreso)) if primer_ingreso else 'sin datos'
        ultimo_legible = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(ultimo_ingreso)) if ultimo_ingreso else 'sin datos'
        if total_ingresos:
            resultado['mensaje'] = (
                f"Informe local del alumno {codigo_mask}: {total_ingresos} visitas en {dias_visita} días distintos. "
                f"{periodo_info} {sede_etiqueta} "
                f"Primera visita en periodo: {primer_legible}. Última visita en periodo: {ultimo_legible}. "
                f"Corte observado de la fuente autorizada: {corte_legible}. "
                "Fuente: RegistroIngresos y Alumnos; se excluyen registros de prueba. "
                "El corte observado no acredita captura completa. No se calcula probabilidad de retorno."
            )
        else:
            resultado['mensaje'] = (
                f"No hay ingresos verificables para la matrícula {codigo_mask} en el periodo consultado ({rango_txt}) "
                f"{('en ' + sede_consulta) if sede_consulta else ''}. "
                f"{periodo_info} {sede_etiqueta} "
                f"Corte observado de la fuente autorizada: {corte_legible}. "
                "Esto no confirma la existencia de una ficha ni constituye un informe individual validado. Revise matrícula, sede y periodo."
            )
    return resultado


def consultar_analisis_descriptivo_individual(
    rol,
    sede="Central",
    sede_permitida=None,
    fecha_inicio=None,
    fecha_fin=None
):
    """
    Herramienta analítica de consulta descriptiva de ingresos individuales.
    Utiliza dbo.RegistroIngresos (EsPrueba = 0) relacionado con Alumnos y Salas.
    - Proporciona desglose de visitas por mes, escuela/carrera, sala, hora y frecuencia por alumno.
    - NUNCA consulta, evalúa ni retorna DNI ni nombres personales.
    - Filtra estrictamente registros de prueba (EsPrueba = 0).
    """
    rol_valido = validar_rol(rol)

    if rol_valido == "Supervisor":
        if not sede_permitida or str(sede_permitida).strip() not in SEDES_PERMITIDAS:
            raise ValueError("El usuario supervisor debe tener una sede válida permitida.")
        sede_consulta = str(sede_permitida).strip()
    else:
        sede_consulta = str(sede).strip() if sede and str(sede).strip() in SEDES_PERMITIDAS else "Central"

    from ml.services.analisis_individual import obtener_analisis_descriptivo_ingresos

    datos = obtener_analisis_descriptivo_ingresos(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        sede=sede_consulta
    )

    total_vis = datos.get("total_visitas", 0)
    total_al = datos.get("total_alumnos", 0)
    frec = datos.get("frecuencia_por_alumno", {})
    prom_vis = frec.get("promedio_visitas", 0)
    top_esc = datos.get("visitas_por_escuela", [])[:3]
    top_salas = datos.get("visitas_por_sala", [])[:3]

    resumen_esc = ", ".join(f"{e['escuela']} ({e['total_visitas']})" for e in top_esc) if top_esc else "N/D"
    resumen_sal = ", ".join(f"{s['nombre_sala']} Piso {s['piso']} ({s['total_visitas']})" for s in top_salas) if top_salas else "N/D"

    mensaje = (
        f"Análisis descriptivo de ingresos individuales (junio–julio 2026, sede {sede_consulta}): "
        f"Se registraron {total_vis} visitas reales correspondientes a {total_al} alumnos únicos "
        f"(promedio de {prom_vis} visitas por alumno). "
        f"Principales escuelas: {resumen_esc}. "
        f"Salas más concurridas: {resumen_sal}. "
        "Filtros aplicados: Excluidas 38 filas de prueba manuales (EsPrueba = 1). Sin uso de identificadores personales sensibles."
    )

    return {
        "status": "success",
        "herramienta": "analisis_descriptivo_individual",
        "sede": sede_consulta,
        "mensaje": mensaje,
        "analisis": datos
    }


def consultar_prediccion_retorno_alumno(
    codigo_matricula,
    rol,
    sede_permitida=None,
    fecha_referencia=None
):
    """
    Herramienta predictiva de retorno de alumno ("volver mañana" / siguiente día hábil operativo).
    - Valida roles y restringe a Supervisor (bloquea a Consultor por protección individual).
    - Aplica validación cronológica estricta hasta fecha_referencia.
    - Devuelve probabilidad solo si el alumno cuenta con al menos 3 días activos previos;
      de lo contrario, devuelve 'historial insuficiente' con explicación detallada.
    - Excluye estrictamente registros de prueba y NUNCA procesa ni retorna DNI.
    """
    rol_valido = validar_rol(rol)

    if rol_valido == "Consultor":
        raise PermissionError("Acceso denegado. El rol Consultor no tiene autorización para predicciones individuales por alumno.")

    if not codigo_matricula or not isinstance(codigo_matricula, str):
        raise ValueError("El código de matrícula es obligatorio.")

    codigo_limpio = codigo_matricula.strip()
    if len(codigo_limpio) < 3 or len(codigo_limpio) > 30:
        raise ValueError("El código de matrícula debe tener entre 3 y 30 caracteres.")

    from ml.services.analisis_individual import predecir_retorno_alumno

    resultado = predecir_retorno_alumno(
        codigo_matricula=codigo_limpio,
        fecha_referencia=fecha_referencia
    )

    return resultado
