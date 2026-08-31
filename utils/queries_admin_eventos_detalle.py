from db import get_db_connection

def obtener_asistentes_evento(evento_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Consultar información del evento para encabezado
        cursor.execute("SELECT NombreEvento, FechaEvento, HoraInicio, HoraFin FROM Eventos WHERE EventoID = ?", (evento_id,))
        evento = cursor.fetchone()
        if not evento:
            return {'status': 'error', 'msg': 'Evento no encontrado'}

        info_evento = {
            'nombre': evento[0],
            'fecha': evento[1].strftime('%d/%m/%Y'),
            'hora': f"{evento[2].strftime('%H:%M')} - {evento[3].strftime('%H:%M')}"
        }
        
        # Consultar la lista de asistentes cruzando con todas las tablas posibles
        # Usamos LEFT JOINs para obtener los nombres reales según el TipoPersona que guardó el scanner
        sql = """
            SELECT 
                A.HoraAsistencia,
                A.CodigoEscaneado,
                A.TipoPersona,
                COALESCE(
                    AL.NombreCompleto,
                    E.NombreCompleto,
                    P.ApellidosNombres,
                    V.NombreCompleto,
                    I.NombreCompleto,
                    D.ApellidosNombres,
                    'Desconocido'
                ) AS NombreCompleto,
                COALESCE(
                    ES_AL.NombreEscuela,
                    ES_EG.NombreEscuela,
                    P.Oficina,
                    V.Institucion,
                    I.Institucion,
                    D.Facultad,
                    '--'
                ) AS Origen
            FROM AsistenciaEventos A
            LEFT JOIN Alumnos AL ON (A.CodigoEscaneado = AL.CodigoMatricula OR A.CodigoEscaneado = AL.DNI) AND A.TipoPersona = 'Alumno'
            LEFT JOIN Escuelas ES_AL ON AL.EscuelaID = ES_AL.EscuelaID
            LEFT JOIN Egresados E ON A.CodigoEscaneado = E.DNI AND A.TipoPersona = 'Egresado'
            LEFT JOIN Escuelas ES_EG ON E.EscuelaID = ES_EG.EscuelaID
            LEFT JOIN PersonalAdministrativo P ON A.CodigoEscaneado = P.DNI AND A.TipoPersona = 'Personal'
            LEFT JOIN Visitantes V ON A.CodigoEscaneado = V.DNI AND A.TipoPersona = 'Visitante'
            LEFT JOIN InvitadosEvento I ON A.CodigoEscaneado = I.DNI AND A.TipoPersona = 'InvitadoEvento' AND I.EventoID = A.EventoID
            LEFT JOIN Docentes D ON A.CodigoEscaneado = D.DNI AND A.TipoPersona = 'Docente'
            WHERE A.EventoID = ?
            ORDER BY A.HoraAsistencia DESC
        """
        cursor.execute(sql, (evento_id,))
        
        asistentes = []
        for row in cursor.fetchall():
            asistentes.append({
                'hora': row[0].strftime('%H:%M:%S'),
                'codigo': row[1],
                'tipo': row[2],
                'nombre': row[3],
                'origen': row[4]
            })
            
        # Stats agrupadas
        cursor.execute("SELECT TipoPersona, COUNT(*) FROM AsistenciaEventos WHERE EventoID = ? GROUP BY TipoPersona", (evento_id,))
        stats = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            'status': 'success', 
            'evento': info_evento,
            'data': asistentes,
            'stats': stats
        }

    except Exception as e:
        return {'status': 'error', 'msg': str(e)}
    finally:
        if 'conn' in locals() and conn: conn.close()
