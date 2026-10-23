"""Chat: datos calculados localmente, redacción externa opcional y sin memoria personal."""
import json
import re
import time
import calendar
import hashlib
from pathlib import Path
from datetime import date

# Huella del código realmente cargado; no se vuelve a leer en cada petición.
CHAT_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
from llm.services.motor_intenciones import clasificar_intencion, extraer_parametros_texto, normalizar_texto, sanitizar_contexto, es_pregunta_seguimiento
from llm.services.consultas_biblioteca import validar_rol, SEDES_PERMITIDAS
from llm.services.filtro_privacidad import es_consulta_personal_o_identificable, sanitizar_datos_herramienta_para_llm
from llm.services.proveedor_llm import obtener_configuracion, ProveedorLLM, clasificar_error, LLMSafetyViolationError, LLMError
from llm.services.metricas_asistente import obtener_gestor_metricas
from llm.services.motor_intenciones import MESES_ESPANOL, extraer_codigo_matricula, resolver_expresion_temporal

AGREGADAS = {'resumen_ingresos', 'horas_mayor_afluencia', 'afluencia_historica', 'prediccion_afluencia'}
PERSONALES = {'conteo_ingresos_alumno', 'historial_persona', 'ultimo_ingreso_persona'}


def _respuesta_local(mensaje, status='requires_input', intencion='intencion_desconocida'):
    return dict(status=status, intencion=intencion, mensaje=mensaje, datos={}, contexto={},
                origen='regla_local', chat=True, opciones=[], requiere_aclaracion=status == 'requires_input', requiere_parametros=False)


def _fragmento_filtros(texto, parametros):
    # Una frase de tema nuevo nunca hereda el informe anterior, aunque empiece por «y».
    vocabulario = set(MESES_ESPANOL) | set(normalizar_texto(' '.join(SEDES_PERMITIDAS)).split()) | {
        'y','e','de','del','al','a','en','el','la','los','las','para','sede','durante','entre',
        'desde','hasta','mes','ano','ayer','hoy','anteayer','antier','semana','pasada','pasado',
        'esta','este','ultimos','dias','por','favor'}
    tokens = re.findall(r'[a-z]+|[0-9]+', texto)
    return bool(tokens) and all(t in vocabulario or t.isdigit() for t in tokens) and bool(
        parametros or any(t in MESES_ESPANOL for t in tokens) or re.fullmatch(r'(?:en |el )?\d{4}',texto))


def _ayuda_autorizada(rol):
    texto = 'Puedo consultar resúmenes de ingresos, horas de mayor afluencia y afluencia histórica por sede y periodo, mediante funciones autorizadas del backend.'
    if rol != 'Consultor':
        texto += ' También puedo consultar informes de visitas de un alumno por matrícula, exclusivamente en el backend local.'
    return texto + ' No ejecuto SQL libre. ¿Qué desea consultar?'


def _preparar_resumen_visible(resultado):
    datos=resultado.get('datos') or {}
    metricas=datos.get('metricas')
    if not isinstance(metricas,dict) or 'total_ingresos' not in metricas or 'dias_con_registros' not in metricas:
        return
    cobertura=datos.get('cobertura') or {}
    ultimo=cobertura.get('ultimo_dia_observado_en_periodo') or 'no disponible'
    ultimo_legible=re.sub(r'(\d{4})-(\d{2})-(\d{2})',r'\3/\2/\1',str(ultimo))
    total=format(int(metricas['total_ingresos']),',').replace(',','.')
    estado='Periodo parcial: no se acredita cobertura completa de todo el rango solicitado.' if cobertura.get('estado')!='confirmada' else 'Captura confirmada operativamente por el responsable; no certificada técnicamente.'
    resultado['resumen_visible']=f"{datos.get('sede','Sede consultada')}: {total} ingresos observados · {metricas['dias_con_registros']} días con registros. Último día observado: {ultimo_legible}. {estado}"
    resultado['metodologia_visible']='Se combinan el histórico agregado y accesos recientes sin duplicar franjas. Se excluyen pruebas y registros fuera del horario operativo. Las franjas sin registros no se convierten automáticamente en cero. La franja de las 20:00 termina a las 20:45. La confirmación operativa de captura no es una certificación técnica.'


def _preparar_resumen_visible_alumno(resultado):
    datos = resultado.get('datos') or {}
    codigo_mask = datos.get('codigo_mascarado')
    if not codigo_mask:
        return
    total = datos.get('total_ingresos', 0)
    dias = datos.get('dias_con_visita', 0)
    periodo = datos.get('periodo_consultado') or {}
    f_ini, f_fin = periodo.get('fecha_inicio'), periodo.get('fecha_fin')
    rango = f"del {f_ini} al {f_fin}" if (f_ini and f_fin) else (
        f"desde {f_ini}" if f_ini else (f"hasta {f_fin}" if f_fin else "todo el historial disponible")
    )
    sede = datos.get('sede') or resultado.get('sede_aplicada') or 'Central'
    def fecha(valor):
        try:
            return date.fromisoformat(str(valor)[:10]) if valor else None
        except (ValueError, TypeError):
            return None

    def legible(dia):
        return dia.strftime('%d/%m/%Y')

    cobertura = datos.get('cobertura_sistema') or {}
    corte = fecha(datos.get('fecha_corte') or cobertura.get('fecha_maxima'))
    inicio, fin = fecha(f_ini), fecha(f_fin)
    inicio_fuente = fecha(cobertura.get('fecha_minima'))
    desde = max(d for d in (inicio, inicio_fuente) if d) if (inicio or inicio_fuente) else None
    hasta = min(corte, fin) if corte and fin else corte
    parcial = bool(corte and fin and fin > corte)
    lineas = [
        f"Alumno {codigo_mask}: {total} visita(s) en {dias} día(s) distintos.",
        f"Rango solicitado: {rango} ({sede}).",
    ]
    if corte:
        if desde and hasta and desde <= hasta:
            lineas.append(f"Intervalo limitado a los datos disponibles: del {legible(desde)} al {legible(hasta)}.")
        elif desde and hasta and desde > hasta:
            lineas.append('Sin intervalo de datos disponibles dentro del rango solicitado.')
        else:
            lineas.append('El inicio del intervalo disponible no está informado.')
        if parcial:
            lineas.append(f"Periodo parcial; datos observados hasta el {legible(corte)}.")
        else:
            lineas.append(f"Corte observado de la fuente: {legible(corte)}.")
    else:
        lineas.append('Disponibilidad no verificable: no hay un corte observado informado.')

    ultima = fecha(datos.get('ultimo_ingreso'))
    ultima_valida = (total > 0 and ultima and corte and ultima <= corte
                     and (not inicio or ultima >= inicio) and (not fin or ultima <= fin))
    if total == 0:
        lineas.append('Sin visitas individuales registradas en el periodo consultado; no hay fecha de última visita.')
    elif ultima_valida:
        lineas.append(f"Última visita individual dentro del periodo y hasta el corte: {legible(ultima)}.")
    else:
        lineas.append('Fecha de última visita individual no disponible o no verificable dentro del periodo y hasta el corte.')
    lineas.append('El corte observado no acredita captura continua ni cobertura completa de cada jornada.')
    resultado['periodo_individual'] = {
        'solicitado': {'inicio': f_ini, 'fin': f_fin},
        'intervalo_hasta_corte': {'inicio': desde.isoformat(), 'fin': hasta.isoformat()}
            if desde and hasta and desde <= hasta else None,
        'corte_observado': corte.isoformat() if corte else None,
        'parcial_por_corte': parcial if corte else None,
        'ultima_visita_individual': ultima.isoformat() if ultima_valida else None,
    }
    resultado['resumen_visible'] = '\n'.join(lineas)
    resultado['mensaje'] = resultado['resumen_visible']



def _causa_segura(categoria):
    return {'cuota':'Gemini rechazó la solicitud por cuota o límite de uso.',
            'servidor':'Gemini informó un error temporal del servicio.',
            'timeout':'Gemini no completó la respuesta dentro del tiempo configurado.',
            'autenticacion':'Gemini rechazó la autenticación configurada.',
            'configuracion':'Gemini está desactivado o falta configuración necesaria.',
            'modelo':'El modelo configurado no está disponible para esta solicitud.',
            'modelo_o_endpoint':'El modelo o la dirección configurados no están disponibles.',
            'conexion':'No se pudo conectar con Gemini.',
            'respuesta_no_verificable':'La respuesta recibida no superó la validación de contenido.',
            'respuesta_invalida':'La respuesta de Gemini estaba vacía, incompleta o tenía un formato inválido.'}.get(categoria,'No se recibió una respuesta válida del proveedor.')


def procesar_chat(motor, pregunta, rol, sede_permitida, contexto, pregunta_personal_pendiente=None, **filtros):
    ctx = sanitizar_contexto(contexto)
    pregunta_motor = pregunta
    def local(**extra):
        return motor(pregunta_motor, rol, sede_permitida=sede_permitida, contexto=ctx, permitir_proveedor=False, **{**filtros, **extra})
    try:
        validar_rol(rol)
        if rol == 'Supervisor' and sede_permitida not in SEDES_PERMITIDAS:
            raise PermissionError()
    except PermissionError:
        return dict(status='error', codigo_http=403, categoria_error='permiso', mensaje='Rol o sede no autorizados para la consulta.', datos={}, contexto={}, origen='regla_local', chat=True)
    norm = normalizar_texto(pregunta)
    if norm == 'nueva consulta':
        return _respuesta_local('Nueva consulta. ¿Qué desea consultar?', status='success')
    if re.fullmatch(r'(?:hola[,. ]*)?(?:en que puedes ayudarme|que puedes hacer|que consultas puedes resolver|ayuda|que puedes ayudarme)', norm):
        return _respuesta_local(_ayuda_autorizada(rol), status='success', intencion='consulta_general')
    # No existe una herramienta de préstamos o catálogo entre las consultas permitidas.
    if re.search(r'\b(prestamos?|prestados?|prestar|ejemplares?|titulos?|libros?|movimiento bibliografico)\b', norm):
        return _respuesta_local('La consulta de préstamos, títulos o disponibilidad de libros no está disponible en este asistente.')
    clasif = clasificar_intencion(pregunta)
    respuesta_personal = (ctx.get('intencion') == 'conteo_ingresos_alumno'
                          and bool(ctx.get('aclaracion_pendiente'))
                          and bool(re.fullmatch(r'(?:los?\s+)?ingresos?', norm)))
    if respuesta_personal:
        clasif['intencion'] = 'conteo_ingresos_alumno'
    tema_pendiente = ctx.get('tema_pendiente') == 'afluencia_agregada'
    es_ingresos = bool(re.search(r'\b(ingresos?|resumen(?: de ingresos)?|afluencia)\b', norm))
    es_horas = bool(re.search(r'\b(horas?|picos?|mayor afluencia)\b', norm))
    respuesta_tema = tema_pendiente and (es_ingresos or es_horas)
    if respuesta_tema:
        clasif['intencion'] = 'horas_mayor_afluencia' if (es_horas and not es_ingresos) else 'afluencia_historica'
        ctx.pop('tema_pendiente', None)
        ctx.pop('aclaracion_pendiente', None)
    if re.search(r'horas.*mas ingresos', norm):
        clasif['intencion'] = 'horas_mayor_afluencia'
    saludo = bool(re.search(r'\b(hola|ayuda|gracias|que puedes hacer|consultas puedes resolver)\b', norm))
    extraidos = extraer_parametros_texto(pregunta, norm)
    fragmento = _fragmento_filtros(norm, extraidos)
    intencion = clasif['intencion']
    referencia_periodo = bool(re.search(
        r'\b(en ese periodo|dicho periodo|mismo periodo|ese periodo|en ese rango|en esas fechas|en esa fecha|en el periodo|en este periodo|en el mismo periodo|mismas fechas|en las mismas fechas)\b',
        norm
    ))
    referencia_alumno = bool(re.search(
        r'\b(el\s+mismo\s+alumn[oa]|del\s+mismo\s+alumn[oa]|dich[oa]\s+alumn[oa]|este\s+alumn[oa]|esta\s+alumna|mismo\s+estudiante)\b',
        norm
    ))
    continuar = respuesta_personal or respuesta_tema or (bool(ctx.get('intencion')) and fragmento and not saludo) or (referencia_alumno and ctx.get('intencion') in PERSONALES)
    if intencion in PERSONALES or (referencia_alumno and ctx.get('intencion') in PERSONALES):
        if referencia_alumno:
            intencion = 'conteo_ingresos_alumno'
            continuar = True
            if not extraidos.get('sede') and ctx.get('sede'):
                extraidos['sede'] = ctx['sede']
            anios_ctx = [ctx[c][:4] for c in ('fecha_inicio', 'fecha_fin', 'fecha') if ctx.get(c) and str(ctx[c])[:4].isdigit()]
            if anios_ctx:
                anio_c = int(anios_ctx[0])
                if extraidos.get('mes_inicio'):
                    mes_ini = int(extraidos['mes_inicio'])
                    mes_fin = int(extraidos.get('mes_fin', mes_ini))
                    ult_fin = calendar.monthrange(anio_c, mes_fin)[1]
                    dia_ini = int(extraidos.get('dia_inicio', 1))
                    dia_fin = int(extraidos.get('dia_fin', ult_fin))
                    extraidos['fecha_inicio'] = f"{anio_c:04d}-{mes_ini:02d}-{dia_ini:02d}"
                    extraidos['fecha_fin'] = f"{anio_c:04d}-{mes_fin:02d}-{dia_fin:02d}"
                    extraidos.pop('error_temporal', None)
                    extraidos.pop('mensaje_error_temporal', None)
                    extraidos.pop('mes_inicio', None)
                    extraidos.pop('mes_fin', None)
                    extraidos.pop('dia_inicio', None)
                    extraidos.pop('dia_fin', None)

        tiene_fechas_previa = bool((ctx.get('fecha_inicio') and ctx.get('fecha_fin')) or ctx.get('fecha'))
        tiene_fechas_pregunta = bool((extraidos.get('fecha_inicio') and extraidos.get('fecha_fin')) or extraidos.get('fecha'))
        if referencia_periodo and not tiene_fechas_previa and not tiene_fechas_pregunta:
            aclaracion = _respuesta_local(
                'No se encontró un periodo previo en la conversación. ¿De qué fechas o periodo desea consultar el informe del alumno?',
                status='requires_input', intencion=intencion
            )
            aclaracion['contexto'] = sanitizar_contexto({'intencion': intencion, 'aclaracion_pendiente': '1'})
            return aclaracion

        if (referencia_periodo or referencia_alumno or not tiene_fechas_pregunta) and tiene_fechas_previa:
            ctx = {k: ctx[k] for k in ('fecha_inicio', 'fecha_fin', 'fecha', 'sede') if ctx.get(k)}
            ctx['intencion'] = intencion
            continuar = True
        else:
            ctx = {}
    else:
        if continuar:
            if intencion == 'intencion_desconocida': intencion = ctx['intencion']
        else:
            ctx = {}
    if intencion == 'intencion_desconocida' and not saludo and es_pregunta_seguimiento(pregunta):
        mensaje = 'No se encontró una consulta previa para continuar. ¿Desea consultar ingresos o las horas de mayor afluencia?' if fragmento else 'La pregunta de seguimiento es ambigua. ¿Qué dato o periodo desea consultar?'
        aclaracion = _respuesta_local(mensaje)
        aclaracion['opciones'] = ['Resumen de ingresos', 'Horas de mayor afluencia']
        return aclaracion
    distinguir_afluencia = (
        bool(re.search(r'\bafluencia\b', norm)) and
        not bool(re.search(r'\b(ingresos?|horas?|picos?|personas?|alumn[oa]|matricula|codigo)\b', norm)) and
        not respuesta_tema
    )
    if (intencion == 'intencion_desconocida' or (intencion == 'resumen_ingresos' and distinguir_afluencia)) and not saludo:
        aclaracion = _respuesta_local('¿Sobre qué desea consultar: ingresos, horas de mayor afluencia o historial de visitas? Indique el tema para continuar.')
        aclaracion['opciones'] = ['Resumen de ingresos', 'Horas de mayor afluencia']
        if re.search(r'\bafluencia\b', norm):
            pendientes = dict(extraidos)
            pendientes.pop('error_temporal', None)
            pendientes.pop('mensaje_error_temporal', None)
            # Una petición larga puede incluir «de octubre de 2026» sin el prefijo «en».
            # Se delega el parseo al resolver existente, sin inferir año ni cambiar rangos de días.
            if not pendientes.get('fecha_inicio') or pendientes.get('error_temporal'):
                periodo_mes = re.search(r'\b(' + '|'.join(MESES_ESPANOL) + r')\s+(?:de\s+)?(\d{4})\b', norm)
                tiene_dias = re.search(r'\b\d{1,2}\s+(?:al|de|hasta)\s+', norm)
                if periodo_mes and not tiene_dias:
                    resuelto = resolver_expresion_temporal('en ' + periodo_mes[1] + ' de ' + periodo_mes[2]) or {}
                    if not resuelto.get('error_temporal'): pendientes.update(resuelto)
            aclaracion['contexto'] = sanitizar_contexto({**pendientes, 'tema_pendiente': 'afluencia_agregada', 'aclaracion_pendiente': '1'})
        return aclaracion
    params = {**ctx, **extraidos}
    params.update({k:v for k,v in filtros.items() if v is not None})
    # El parser común exige «en mes de año». El chat acepta también la respuesta aislada.
    mes_anio = re.fullmatch(r'(?:(?:y |de |en |del |el |mes |ano )+)?([a-z]+) (?:de )?(\d{4})', norm)
    if fragmento and mes_anio and mes_anio[1] in MESES_ESPANOL and 1900 <= int(mes_anio[2]) <= 2100:
        mes, anio = MESES_ESPANOL[mes_anio[1]], int(mes_anio[2])
        params.update(fecha_inicio=f'{anio:04d}-{mes:02d}-01', fecha_fin=f'{anio:04d}-{mes:02d}-{calendar.monthrange(anio,mes)[1]:02d}')
        params.pop('error_temporal', None)
        params.pop('mensaje_error_temporal', None)
    elif continuar and params.get('error_temporal') == 'fecha_ambigua' and not re.search(r'\b\d{4}\b', norm):
        anios = {ctx[c][:4] for c in ('fecha_inicio','fecha_fin','fecha') if ctx.get(c) and str(ctx[c])[:4].isdigit()}
        if len(anios) == 1:
            anio_heredado = int(next(iter(anios)))
            if params.get('mes_inicio'):
                mes_ini = int(params['mes_inicio'])
                mes_fin = int(params.get('mes_fin', mes_ini))
                ult_fin = calendar.monthrange(anio_heredado, mes_fin)[1]
                dia_ini = int(params.get('dia_inicio', 1))
                dia_fin = int(params.get('dia_fin', ult_fin))
                for campo in ('fecha_inicio', 'fecha_fin', 'fecha'): params.pop(campo, None)
                params.update(fecha_inicio=f'{anio_heredado:04d}-{mes_ini:02d}-{dia_ini:02d}', fecha_fin=f'{anio_heredado:04d}-{mes_fin:02d}-{dia_fin:02d}')
                params.pop('error_temporal', None)
                params.pop('mensaje_error_temporal', None)
                params.pop('mes_inicio', None)
                params.pop('mes_fin', None)
                params.pop('dia_inicio', None)
                params.pop('dia_fin', None)
            else:
                completados = resolver_expresion_temporal(norm + ' de ' + str(anio_heredado)) or {}
                if completados.get('fecha_inicio') and not completados.get('error_temporal'):
                    for campo in ('fecha_inicio','fecha_fin','fecha'): params.pop(campo,None)
                    params.update(completados)
                    params.pop('error_temporal', None)
                    params.pop('mensaje_error_temporal', None)
    esperando_anio = bool(ctx.get('mes_pendiente') or ctx.get('aclaracion_pendiente') in ('anio', '1'))
    es_anio_puro = bool(re.fullmatch(r'\s*(?:en\s+)?(?:el\s+)?(?:ano\s+)?\d{4}\s*', pregunta))
    if esperando_anio and es_anio_puro:
        anio = int(re.search(r'\d{4}', pregunta).group())
        if 1900 <= anio <= 2100:
            mes = int(ctx.get('mes_pendiente', 1))
            mes_fin = int(ctx.get('mes_fin_pendiente', mes))
            ultimo_fin = calendar.monthrange(anio, mes_fin)[1]
            dia_inicio = int(ctx.get('dia_inicio_pendiente', 1))
            dia_fin = int(ctx.get('dia_fin_pendiente', ultimo_fin))
            params.update(fecha_inicio=f'{anio:04d}-{mes:02d}-{dia_inicio:02d}', fecha_fin=f'{anio:04d}-{mes_fin:02d}-{dia_fin:02d}')
            params.pop('mes_pendiente', None)
            params.pop('mes_fin_pendiente', None)
            params.pop('error_temporal', None)
            params.pop('mensaje_error_temporal', None)
            if intencion in AGREGADAS:
                pregunta_motor = {'resumen_ingresos':'Resumen de ingresos', 'horas_mayor_afluencia':'Horas de mayor afluencia'}.get(intencion, pregunta)
    if rol == 'Supervisor': params['sede'] = sede_permitida
    if intencion in PERSONALES and rol == 'Consultor':
        return dict(status='error', codigo_http=403, categoria_error='permiso', mensaje='Su rol no permite consultar historiales individuales.', datos={}, contexto={}, origen='regla_local', chat=True)

    if params.get('error_temporal') == 'fecha_ambigua':
        pendientes = {**params, 'intencion': intencion, 'aclaracion_pendiente': 'anio'}
        if params.get('mes_inicio'):
            pendientes['mes_pendiente'] = params['mes_inicio']
        if params.get('mes_fin'):
            pendientes['mes_fin_pendiente'] = params['mes_fin']
        if not pendientes.get('mes_pendiente'):
            mes = next((n for nombre,n in MESES_ESPANOL.items() if re.search(r'\b'+nombre+r'\b',normalizar_texto(pregunta))), None)
            if mes: pendientes['mes_pendiente'] = mes
        dias = re.search(r'(?:del?\s+)?(\d{1,2})\s+al\s+(\d{1,2})', pregunta)
        if dias: pendientes.update(dia_inicio_pendiente=dias[1], dia_fin_pendiente=dias[2])
        else:
            dia = re.search(r'\b(\d{1,2})\s+de\s+[a-záéíóúñ]+', pregunta, re.I)
            if dia: pendientes.update(dia_inicio_pendiente=dia[1], dia_fin_pendiente=dia[1])
        return dict(status='requires_input', intencion=intencion, mensaje='¿De qué año desea consultar ese periodo?', datos={}, contexto=sanitizar_contexto(pendientes), origen='regla_local', chat=True)
    if params.get('mes_pendiente') and not (params.get('fecha_inicio') and params.get('fecha_fin')) and not params.get('fecha'):
        return dict(status='requires_input', intencion=intencion, mensaje='¿De qué año desea consultar ese periodo?', datos={}, contexto=sanitizar_contexto({**params, 'intencion':intencion, 'aclaracion_pendiente':'anio'}), origen='regla_local', chat=True)

    if intencion in PERSONALES:
        # Extraer matrícula del mensaje actual o de pregunta_personal_pendiente
        codigo = extraer_codigo_matricula(pregunta)
        if not codigo and isinstance(pregunta_personal_pendiente, str) and len(pregunta_personal_pendiente) <= 500:
            codigo = extraer_codigo_matricula(pregunta_personal_pendiente)

        # Regla de privacidad: NUNCA guardar ni reutilizar la matrícula entre consultas.
        # Si hace falta el código, solicitarlo explícitamente antes de consultar SQL.
        if not codigo:
            sede_txt = params.get('sede') or ctx.get('sede') or 'Central'
            params['sede'] = sede_txt
            f_ini = params.get('fecha_inicio')
            f_fin = params.get('fecha_fin')
            if f_ini and f_fin:
                f_ini_fmt = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(f_ini))
                f_fin_fmt = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(f_fin))
                rango_txt = f"del {f_ini_fmt} al {f_fin_fmt}"
            elif params.get('fecha'):
                f_fmt = re.sub(r'(\d{4})-(\d{2})-(\d{2})', r'\3/\2/\1', str(params['fecha']))
                rango_txt = f"para el {f_fmt}"
            else:
                rango_txt = "el periodo indicado"
            mensaje = f"Por motivos de privacidad, no se conserva la matrícula de consultas anteriores. Por favor, indique el código de matrícula del alumno para consultar {rango_txt} en {sede_txt}."
            return dict(
                status='requires_input',
                intencion=intencion,
                mensaje=mensaje,
                datos={},
                contexto=sanitizar_contexto({**params, 'sede': sede_txt, 'intencion': intencion, 'aclaracion_pendiente': 'codigo_matricula'}),
                origen='regla_local',
                chat=True,
                requiere_aclaracion=False,
                requiere_parametros=True,
                parametros_faltantes=['codigo_matricula'],
                sede_aplicada=sede_txt,
                opciones=[]
            )

        pregunta_motor = 'Informe del alumno ' + codigo
    if intencion in AGREGADAS:
        pregunta_motor = {'resumen_ingresos':'Resumen de ingresos', 'horas_mayor_afluencia':'Horas de mayor afluencia', 'afluencia_historica':'Afluencia histórica', 'prediccion_afluencia':'Predicción de afluencia'}[intencion]

    if intencion in AGREGADAS and not params.get('error_temporal'):
        faltantes = []
        if not params.get('sede'): faltantes.append('sede')
        if intencion != 'prediccion_afluencia' and not (params.get('fecha_inicio') and params.get('fecha_fin')) and not params.get('fecha'):
            faltantes.append('periodo')
        if intencion == 'prediccion_afluencia':
            if not params.get('fecha'): faltantes.append('fecha')
            if params.get('hora') is None: faltantes.append('hora')
        if faltantes:
            return dict(status='requires_input', intencion=intencion, mensaje='¿Qué ' + ' y '.join(faltantes) + ' desea consultar? Puede responder aquí con el dato que falta.',
                        datos={}, contexto=sanitizar_contexto({**params, 'intencion':intencion, 'aclaracion_pendiente':'1'}), origen='regla_local', chat=True,
                        requiere_parametros=True, requiere_aclaracion=False, parametros_faltantes=faltantes, sede_aplicada=params.get('sede') or 'Por definir', opciones=[])
    personal = intencion in PERSONALES or es_consulta_personal_o_identificable(pregunta, filtros.get('seleccion_persona'), ctx)[0]
    resultado = local(**{k:params[k] for k in ('fecha_inicio','fecha_fin','fecha','hora','sede') if k in params})
    resultado['chat'] = True
    if resultado.get('datos', {}).get('datos_simulados'): resultado['datos_simulados'] = True
    resultado['consulta_sql_realizada'] = resultado.get('status') == 'success' and intencion in {'resumen_ingresos','horas_mayor_afluencia','afluencia_historica'} and not resultado.get('datos_simulados',False)
    if isinstance(resultado.get('mensaje'), str):
        resultado['mensaje'] = resultado['mensaje'].replace('Si lo prefiere, también puede ingresar las fechas en los parámetros complementarios.', 'Puede responder con esos datos en el siguiente mensaje.')
    if personal:
        resultado['consulta_confidencial'] = True
        if intencion == 'conteo_ingresos_alumno':
            _preparar_resumen_visible_alumno(resultado)
        if resultado.get('status') == 'requires_input':
            resultado['contexto'] = sanitizar_contexto({**params, 'intencion':intencion, 'aclaracion_pendiente':'1'})
        else:
            resultado['contexto'] = sanitizar_contexto({**params, 'intencion':intencion})
        if resultado.get('datos', {}).get('estado') == 'sin_ingresos_verificables':
            resultado['status'] = 'requires_input'
        return resultado
    general = intencion == 'intencion_desconocida'
    if not general and resultado.get('status') != 'success':
        return resultado
    if general:
        resultado = dict(status='success' if saludo else 'requires_input', intencion='consulta_general' if saludo else 'intencion_desconocida', mensaje=_ayuda_autorizada(rol), datos={}, contexto={}, origen='regla_local', chat=True, opciones=[])
    if intencion == 'afluencia_historica': _preparar_resumen_visible(resultado)
    config = obtener_configuracion()
    if not config.esta_disponible:
        resultado['aviso_origen'] = 'Respuesta de respaldo; Gemini no respondió'
        resultado['error_proveedor'] = 'configuracion'
        resultado['causa_proveedor_visible'] = _causa_segura('configuracion')
        resultado['variables_faltantes'] = [nombre for nombre, valor in
            [('LLM_API_KEY', getattr(config, 'api_key', None)), ('LLM_MODELO', getattr(config, 'modelo', None))] if not valor]
        if not getattr(config, 'activado', True): resultado['variables_faltantes'].append('LLM_ACTIVADO')
        return resultado
    codigo_http = None
    inicio = time.perf_counter()
    try:
        if general:
            mensajes = [{'role':'system','content':'Eres el asistente de Biblioteca UNDAC. Responde con un saludo breve. Las únicas capacidades disponibles son: ' + _ayuda_autorizada(rol) + ' No ofrezcas préstamos, catálogo, títulos, disponibilidad de ejemplares ni otras funciones. No has consultado datos.'},
                        {'role':'user','content':'Saluda y explica tus capacidades de consulta de biblioteca.' if saludo else 'Pide aclarar una consulta no reconocida y orienta sobre consultas por sede y periodo. No afirmes haber consultado datos.'}]
        else:
            seguros = sanitizar_datos_herramienta_para_llm(resultado['datos'])
            # No viajan la pregunta, nombres ni la conversación; solo evidencia agregada.
            evidencia = {'datos':seguros, 'fuente':resultado.get('fuente') or 'Herramientas autorizadas de biblioteca',
                         'periodo':{k:params.get(k) for k in ('fecha_inicio','fecha_fin','fecha')},
                         'cobertura':resultado.get('datos',{}).get('cobertura'),
                         'limites':resultado.get('limites_cobertura') or 'No acredita captura completa; no inferir ausencias como ceros.'}
            if intencion == 'afluencia_historica' and isinstance(resultado['datos'].get('metricas'),dict):
                d=resultado['datos'];m=d['metricas'];c=d.get('cobertura') or {}
                evidencia={'sede':d.get('sede'), 'periodo_solicitado':d.get('periodo'),
                           'ingresos_observados':m.get('total_ingresos'), 'dias_con_registros':m.get('dias_con_registros'),
                           'ultimo_dia_observado':c.get('ultimo_dia_observado_en_periodo'),
                           'periodo_parcial':c.get('estado')!='confirmada', 'captura_certificada_tecnicamente':False}
            mensajes = [{'role':'system','content':'Redacta únicamente una o dos frases breves, en texto plano, de como máximo 400 caracteres. No uses listas numeradas. Explica brevemente cómo leer este resultado agregado. No repitas cifras ni fechas: el backend las muestra. Respeta estrictamente el estado de cobertura: un periodo parcial no es un mes completo y ausencia de filas no demuestra cero ingresos. No añadas cálculos, conclusiones causales ni predicciones. No generes SQL.'},
                        {'role':'user','content':json.dumps(evidencia, ensure_ascii=False, default=str)}]
        respuesta = ProveedorLLM(config).responder_chat(mensajes)
        if not isinstance(respuesta,dict) or not isinstance(respuesta.get('mensaje'),str) or not respuesta['mensaje'].strip():
            raise LLMError('Respuesta vacía o inválida del proveedor.')
        if not general and len(respuesta['mensaje']) > 600:
            raise LLMSafetyViolationError('Explicación demasiado extensa.')
        if not general and re.search(r'\d', respuesta['mensaje']):
            raise LLMSafetyViolationError('La explicación intenta añadir cifras.')
        if general:
            # El proveedor no puede ampliar el catálogo de capacidades: solo se toma un saludo permitido.
            saludo_seguro = re.match(r'\s*(?:¡)?(hola|buenos días|buenas tardes|buenas noches)\b', respuesta['mensaje'], re.I)
            prefijo = saludo_seguro[1].capitalize() + '. ' if saludo_seguro else ''
            resultado.update(mensaje=prefijo + _ayuda_autorizada(rol), status='success')
        else: resultado['explicacion_llm'] = respuesta['mensaje']
        resultado.update(origen='llm_externo', proveedor=config.proveedor, origen_detalle='ayuda_verificada' if general else 'biblioteca_explicada')
        exito = True
    except Exception as error:
        resultado['aviso_origen'] = 'Respuesta de respaldo; Gemini no respondió'
        resultado['error_proveedor'] = clasificar_error(error)
        resultado['causa_proveedor_visible'] = _causa_segura(resultado['error_proveedor'])
        codigo_http = getattr(error, 'status_code', None)
        if isinstance(codigo_http,int) and 100 <= codigo_http <= 599: resultado['codigo_http_proveedor'] = codigo_http
        exito = False
        respuesta = {}
    try:
        usage = respuesta.get('usage') or {}
        obtener_gestor_metricas().registrar_interaccion(proveedor=config.proveedor, modelo=config.modelo,
            intencion=intencion if intencion in AGREGADAS else 'consulta_general', tiempo_ms=int((time.perf_counter()-inicio)*1000),
            tokens={'disponible':usage.get('total_tokens') is not None, **usage}, exito=exito, es_fallback=not exito, categoria_error=resultado.get('error_proveedor'), codigo_http=codigo_http)
    except Exception:
        pass
    return resultado
