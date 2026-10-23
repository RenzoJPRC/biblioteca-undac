"""Unión descriptiva de solo lectura: AHA conserva prioridad; no se sintetizan ceros."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from ml.services.datos_afluencia import (FERIADOS_CONFIRMADOS_2026, CAPTURA_CONTINUA_CONFIRMADA,
                                        CIERRE_CONFIRMADO_27_JULIO)


def dia_operativo(dia):
    return (dia.weekday() < 5 and dia.isoformat() not in FERIADOS_CONFIRMADOS_2026
            and dia.isoformat() != CIERRE_CONFIRMADO_27_JULIO
            and not date(2026,8,1) <= dia <= date(2026,8,23))


def fecha(valor):
    return valor.date() if isinstance(valor,datetime) else (valor if isinstance(valor,date) else date.fromisoformat(str(valor)[:10]))


def unir_recientes(cursor, historico, corte, sede, inicio, fin, hoy=None):
    hoy = hoy or datetime.now(ZoneInfo('America/Lima')).date()
    limite = hoy
    if corte is None:
        raise ValueError('No se pudo identificar el corte histórico; no se presume ausencia de ingresos recientes.')
    corte = fecha(corte)
    cursor.execute("""SELECT CAST(FechaHora AS DATE) AS Fecha,
             DATEPART(HOUR, FechaHora) AS Hora, COUNT(*) AS CantidadIngresos, Sede
        FROM dbo.RegistroIngresos
        WHERE FechaHora >= ? AND FechaHora < ? AND Sede = ?
          AND ISNULL(EsPrueba, 0) = 0
          AND CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
          AND CAST(FechaHora AS TIME) <= CAST('20:45:00.000' AS TIME)
        GROUP BY CAST(FechaHora AS DATE), DATEPART(HOUR, FechaHora), Sede
        ORDER BY Fecha, Hora""", (corte.isoformat(), limite.isoformat(), sede))
    recientes = cursor.fetchall()  # Si falla RI, propagar; nunca presentar AHA sola como fuente actual.
    combinado = {}
    for fila in historico:
        d,h = fecha(fila[0]), int(fila[1])
        if inicio <= d <= fin and d < limite and dia_operativo(d) and 8 <= h <= 20:
            combinado.setdefault((d,h,sede),(d,h,int(fila[2]), h == 20))
    ultimo = corte if corte < limite else None
    agregadas = 0
    for fila in recientes:
        d,h = fecha(fila[0]), int(fila[1])
        if len(fila)<4 or fila[3] != sede or not corte <= d < limite or not dia_operativo(d) or not 8 <= h <= 20:
            continue
        ultimo = max(ultimo,d) if ultimo else d
        clave=(d,h,sede)
        if inicio <= d <= fin and clave not in combinado:
            combinado[clave]=(d,h,int(fila[2]),h == 20)
            agregadas += 1
    periodo = CAPTURA_CONTINUA_CONFIRMADA.get(sede)
    confirmadas = set()
    desconocidas = []
    d=inicio
    while d <= fin:
        if dia_operativo(d):
            if periodo and periodo[0] <= d <= periodo[1] and d < limite:
                confirmadas.add(d)
            else:
                desconocidas.append(d.isoformat())
        d += timedelta(days=1)
    # El 24/07 no tiene captura confirmada; tener filas no acredita captura continua.
    filas = sorted(combinado.values(),key=lambda f:(f[0],f[1]))
    completa = not desconocidas and bool(confirmadas)
    cobertura = dict(estado='confirmada' if completa else 'parcial',
        captura_completa_confirmada=completa, ultimo_dia_con_datos=ultimo.isoformat() if ultimo else None,
        ultimo_dia_calendario_completo=(hoy-timedelta(days=1)).isoformat(),
        ultimo_dia_observado_en_periodo=max((f[0] for f in filas),default=None),
        fechas_captura_no_confirmada=desconocidas,
        dias_captura_confirmada=len(confirmadas),franjas_recientes_incorporadas=agregadas,
        confirmacion='Confirmación operativa del responsable registrada en el proyecto; no comprobación técnica independiente',
        ausencias_se_interpretan_como_cero=False)
    if cobertura['ultimo_dia_observado_en_periodo']:
        cobertura['ultimo_dia_observado_en_periodo']=cobertura['ultimo_dia_observado_en_periodo'].isoformat()
    return filas,cobertura
