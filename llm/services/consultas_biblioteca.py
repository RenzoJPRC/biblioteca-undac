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
        "limite": limite,
        "horas_top": horas_top,
        "referencia_horaria": "Hora almacenada en la base de datos"
    }
