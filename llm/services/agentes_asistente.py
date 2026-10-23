"""Agentes de herramientas síncronos. Sin cola, scheduler, SQL ni envíos reales."""
from copy import deepcopy
from datetime import date
import os
import re
from threading import Lock
from time import perf_counter

from llm.services.coordinador_asistente import procesar_pregunta
from llm.services.motor_intenciones import clasificar_intencion, sanitizar_contexto
from llm.services.consultas_biblioteca import SEDES_PERMITIDAS


class RechazoAuditoria(ValueError):
    pass


class TelemetriaAgentes:
    """Solo contadores del proceso actual: nunca textos, destinatarios o alumnos."""
    def __init__(self):
        self.lock = Lock()
        self.tareas = dict(recibidas=0, completadas=0, rechazadas=0, fallidas=0, pendientes=0)
        self.agentes = {nombre: dict(ejecuciones=0, fallos=0, tiempo_ms=0)
                        for nombre in ('orquestador', 'afluencia', 'analisis_individual', 'auditor', 'reportes_automatizacion')}
        self.reportes = 0

    def tarea(self, estado):
        with self.lock:
            self.tareas[estado] += 1

    def ejecutar(self, agente, funcion):
        inicio = perf_counter()
        fallo = False
        try:
            resultado = funcion()
            fallo = isinstance(resultado, dict) and resultado.get('status') == 'error' and resultado.get('codigo_http', 400) >= 500
            return resultado
        except Exception:
            fallo = True
            raise
        finally:
            with self.lock:
                item = self.agentes.setdefault(agente, dict(ejecuciones=0, fallos=0, tiempo_ms=0))
                item['ejecuciones'] += 1
                item['fallos'] += int(fallo)
                item['tiempo_ms'] += round((perf_counter() - inicio) * 1000, 3)

    def resumen(self):
        with self.lock:
            return deepcopy(dict(tareas=self.tareas, agentes=self.agentes, reportes_generados=self.reportes,
                persistencia='Memoria del worker actual; no compartida ni permanente',
                entrega='No se realizan envíos', recurrentes_activas=None,
                ultima_ejecucion_recurrente=None, proxima_ejecucion=None,
                programacion_disponible=False))


telemetria = TelemetriaAgentes()


class AgenteAfluencia:
    nombre = 'afluencia'
    instrucciones = 'Consultar afluencia mediante herramientas existentes; conservar sus límites y corte.'
    herramientas = ('procesar_pregunta:afluencia_historica', 'procesar_pregunta:prediccion_afluencia',
                    'procesar_pregunta:resumen_ingresos', 'procesar_pregunta:horas_mayor_afluencia')

    def ejecutar(self, pregunta, rol, sede, contexto=None):
        intencion = clasificar_intencion(pregunta)['intencion']
        if intencion == 'intencion_desconocida':
            intencion = (contexto or {}).get('intencion')
        if intencion not in {
                'afluencia_historica', 'prediccion_afluencia', 'resumen_ingresos', 'horas_mayor_afluencia'}:
            return dict(status='requires_input', origen='regla_local', intencion='intencion_desconocida',
                        mensaje='Indique un resumen, afluencia histórica o predicción con periodo y sede. Para un informe individual indique la matrícula.', datos={}, contexto={})
        return procesar_pregunta(pregunta, rol, sede_permitida=sede, permitir_proveedor=False, contexto=contexto)


class AgenteAnalisisIndividual:
    nombre = 'analisis_individual'
    instrucciones = 'Reutilizar informe por matrícula; no utilizar proveedor externo ni guardar identidades.'
    herramientas = ('procesar_pregunta:conteo_ingresos_alumno',)

    def ejecutar(self, pregunta, rol, sede, contexto=None):
        if rol == 'Consultor':
            raise PermissionError('Consultor no puede acceder a informes personales.')
        if clasificar_intencion(pregunta)['intencion'] != 'conteo_ingresos_alumno' and (contexto or {}).get('intencion') != 'conteo_ingresos_alumno':
            raise PermissionError('Herramienta no permitida para este agente.')
        return procesar_pregunta(pregunta, rol, sede_permitida=sede, permitir_proveedor=False, informe_individual=True, contexto=contexto)


class AgenteAuditor:
    nombre = 'auditor'
    instrucciones = 'Rechazar resultados sin evidencia, inconsistentes o fuera de los permisos.'
    herramientas = ('validar_resultado_estructurado',)

    def validar(self, resultado, rol, sede):
        if resultado.get('status') != 'success' or resultado.get('origen') not in ('consulta_segura', 'machine_learning'):
            raise RechazoAuditoria('Falta un resultado de herramienta autorizado.')
        intencion = resultado.get('intencion')
        individual = intencion == 'conteo_ingresos_alumno'
        if individual and rol == 'Consultor':
            raise RechazoAuditoria('Informe personal no permitido.')
        if rol == 'Supervisor' and resultado.get('sede_aplicada') != sede:
            raise RechazoAuditoria('Sede inconsistente con la sesión.')
        datos = resultado.get('datos')
        if not isinstance(datos, dict) or not datos:
            raise RechazoAuditoria('No hay evidencia estructurada.')
        if individual:
            if not datos.get('fuente') or 'fecha_corte' not in datos:
                raise RechazoAuditoria('Informe individual sin fuente o corte declarado.')
            total = datos.get('total_ingresos')
            if type(total) is not int or total < 0:
                raise RechazoAuditoria('Conteo inválido.')
            for grupo in ('mensual', 'dias_semana', 'horarios', 'salas'):
                filas = datos.get(grupo)
                if not isinstance(filas, list) or any(type(f.get('visitas')) is not int or f['visitas'] < 0 for f in filas):
                    raise RechazoAuditoria('Distribución no comprobable.')
                if sum(f['visitas'] for f in filas) != total:
                    raise RechazoAuditoria('Distribución y total no coinciden.')
            if total and (not datos.get('primer_ingreso') or not datos.get('ultimo_ingreso') or not datos.get('fecha_corte')):
                raise RechazoAuditoria('Faltan fechas de los ingresos.')
            if total:
                primera = date.fromisoformat(datos['primer_ingreso'][:10])
                ultima = date.fromisoformat(datos['ultimo_ingreso'][:10])
                corte = date.fromisoformat(datos['fecha_corte'][:10])
                if not primera <= ultima <= corte:
                    raise RechazoAuditoria('Fechas individuales inconsistentes.')
            dias = datos.get('dias_con_visita')
            if type(dias) is not int or not 0 <= dias <= total:
                raise RechazoAuditoria('Días distintos inconsistentes.')
        elif intencion == 'resumen_ingresos':
            total = datos.get('total_ingresos')
            if type(total) is not int or total < 0:
                raise RechazoAuditoria('Total inválido.')
            for grupo in ('ingresos_por_sede', 'ingresos_por_tipo_persona'):
                valores = datos.get(grupo)
                if not isinstance(valores, dict) or any(type(v) is not int or v < 0 for v in valores.values()) or sum(valores.values()) != total:
                    raise RechazoAuditoria('Resumen inconsistente.')
            if rol == 'Supervisor' and set(datos['ingresos_por_sede']) - {sede}:
                raise RechazoAuditoria('El resumen mezcla sedes.')
            for campo in ('fecha_inicio', 'fecha_fin'):
                date.fromisoformat(datos[campo])
            if datos['fecha_inicio'] > datos['fecha_fin']:
                raise RechazoAuditoria('Periodo invertido.')
        elif intencion == 'prediccion_afluencia':
            consulta = datos.get('consulta', {})
            if not consulta.get('ultima_fecha_disponible') or not consulta.get('fuente_historial'):
                raise RechazoAuditoria('Predicción sin procedencia o corte.')
            if consulta.get('modo_demostracion'):
                raise RechazoAuditoria('Historial no representativo para un reporte operativo.')
            if consulta['ultima_fecha_disponible'] >= consulta.get('fecha', ''):
                raise RechazoAuditoria('Corte no anterior al objetivo.')
        elif intencion == 'afluencia_historica':
            if not datos.get('fuente') or not datos.get('periodo') or not datos.get('limites_cobertura'):
                raise RechazoAuditoria('Histórico sin fuente, periodo o límites.')
            if rol == 'Supervisor' and datos.get('sede') != sede:
                raise RechazoAuditoria('Histórico de otra sede.')
            if type(datos.get('metricas', {}).get('total_ingresos')) is not int or datos['metricas']['total_ingresos'] < 0:
                raise RechazoAuditoria('Métricas históricas no comprobables.')
        elif intencion == 'horas_mayor_afluencia':
            if not all(datos.get(k) for k in ('fecha_inicio', 'fecha_fin')) or not isinstance(datos.get('horas_top'), list):
                raise RechazoAuditoria('Faltan periodo y distribución horaria.')
            for fila in datos['horas_top']:
                if type(fila.get('hora')) is not int or not 0 <= fila['hora'] <= 23 or type(fila.get('total_ingresos')) is not int or fila['total_ingresos'] < 0:
                    raise RechazoAuditoria('Distribución horaria inválida.')
        else:
            raise RechazoAuditoria('Esta intención aún no dispone de auditoría de reportes.')
        return deepcopy(resultado)


class AgenteReportesAutomatizacion:
    nombre = 'reportes_automatizacion'
    instrucciones = 'Componer solo evidencia aprobada; recurrencia en borrador y sin envío.'
    herramientas = ('componer_reporte', 'validar_borrador_recurrente')

    def componer(self, aprobado):
        datos = aprobado['datos']
        # El cuerpo se genera de campos de herramientas; no de una conclusión LLM.
        from json import dumps
        return dict(estado='preparado_no_enviado', tipo=aprobado['intencion'],
                    contenido=dumps(datos, ensure_ascii=False, indent=2),
                    datos=deepcopy(datos), sede=aprobado.get('sede_aplicada'),
                    periodo={'inicio': datos.get('fecha_inicio') or datos.get('periodo', {}).get('fecha_inicio') or datos.get('periodo_consultado', {}).get('fecha_inicio'),
                             'fin': datos.get('fecha_fin') or datos.get('periodo', {}).get('fecha_fin') or datos.get('periodo_consultado', {}).get('fecha_fin')},
                    fuente=datos.get('consulta', {}).get('fuente_historial') or datos.get('fuente') or 'RegistroIngresos y, si existe, RegistroIngresos_Historico',
                    corte=datos.get('fecha_corte') or datos.get('consulta', {}).get('ultima_fecha_disponible') or datos.get('limites_cobertura', {}).get('fecha_maxima_sistema'),
                    limite_consulta=datos.get('fecha_fin') or datos.get('periodo', {}).get('fecha_fin'),
                    limitaciones='El límite consultado no acredita captura completa. Sin programación ni envío.')

    def borrador(self, tipo, frecuencia, destinatario, rol, sede):
        validar_sesion(rol, sede)
        if rol not in ('SuperAdmin', 'Supervisor'):
            raise PermissionError('No tiene permiso para preparar tareas recurrentes.')
        if tipo not in ('afluencia', 'individual') or frecuencia not in ('diaria', 'semanal'):
            raise ValueError('Tipo o frecuencia no admitidos.')
        # No autorizar un destinatario por haberlo recibido del navegador.
        autorizados = {v.strip().lower() for v in os.getenv('LLM_DESTINATARIOS_AUTORIZADOS', '').split(',') if v.strip()}
        if not isinstance(destinatario, str) or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', destinatario):
            raise ValueError('Destinatario inválido.')
        if destinatario.lower() not in autorizados:
            raise PermissionError('Destinatario no autorizado por la configuración del servidor.')
        # Sin política de destinatarios por alumno: nunca preparar correo personal.
        if tipo == 'individual':
            raise PermissionError('Falta política de destinatarios autorizados para informes personales.')
        return dict(estado='borrador_no_persistido', tipo=tipo, frecuencia=frecuencia,
                    destinatario=destinatario, sede=sede if rol == 'Supervisor' else 'por definir',
                    activa=False, ultima_ejecucion=None, proxima_ejecucion=None,
                    bloqueo='Faltan ejecutor único y almacenamiento compartido duradero con idempotencia. Falta definir periodo y hora de ejecución. No se activó ni se enviará correo.')


def validar_sesion(rol, sede):
    if rol not in ('SuperAdmin', 'Supervisor', 'Consultor'):
        raise PermissionError('Rol no autorizado.')
    if rol == 'Supervisor' and sede not in SEDES_PERMITIDAS:
        raise PermissionError('Supervisor sin sede válida.')


class Orquestador:
    nombre = 'orquestador'
    instrucciones = 'Validar sesión, delegar por intención y exigir auditoría antes de componer.'
    herramientas = ('clasificar_intencion', 'agentes_especializados', 'auditor', 'reportes')

    def ejecutar(self, pregunta, rol, sede=None, contexto=None):
        telemetria.tarea('recibidas')
        traza = []
        def paso(agente, funcion):
            traza.append(agente.nombre)
            return telemetria.ejecutar(agente.nombre, funcion)
        try:
            paso(self, lambda: validar_sesion(rol, sede))
            clasificacion = clasificar_intencion(pregunta)
            contexto = sanitizar_contexto(contexto)
            intencion = clasificacion['intencion']
            if intencion == 'intencion_desconocida':
                intencion = contexto.get('intencion')
            if intencion == 'conteo_ingresos_alumno':
                agente = AgenteAnalisisIndividual()
            else:
                agente = AgenteAfluencia()
            resultado = paso(agente, lambda: agente.ejecutar(pregunta, rol, sede, contexto))
            if resultado.get('status') != 'success':
                estado = 'pendientes' if resultado.get('status') == 'requires_input' else ('fallidas' if resultado.get('codigo_http', 400) >= 500 else 'rechazadas')
                telemetria.tarea(estado)
                resultado['contexto'] = sanitizar_contexto({**resultado.get('contexto', {}), 'intencion': resultado.get('intencion')})
                return {**resultado, 'agentes': traza, 'auditoria': 'no_ejecutada', 'reporte': None}
            if resultado.get('datos', {}).get('estado') == 'sin_ingresos_verificables':
                telemetria.tarea('pendientes')
                return {**resultado, 'status': 'requires_input', 'agentes': traza, 'auditoria': 'no_ejecutada', 'reporte': None}
            auditor = AgenteAuditor()
            aprobado = paso(auditor, lambda: auditor.validar(resultado, rol, sede))
            reportes = AgenteReportesAutomatizacion()
            reporte = paso(reportes, lambda: reportes.componer(aprobado))
            telemetria.tarea('completadas')
            with telemetria.lock:
                telemetria.reportes += 1
            return {**aprobado, 'agentes': traza, 'auditoria': 'aprobado', 'reporte': reporte}
        except PermissionError:
            telemetria.tarea('rechazadas')
            return dict(status='error', codigo_http=403,
                        mensaje='Se requiere una sesión SuperAdmin o Supervisor con sede válida para este informe personal. Consultor no puede consultar historiales individuales.', agentes=traza, auditoria='no_ejecutada', reporte=None)
        except RechazoAuditoria:
            telemetria.tarea('rechazadas')
            return dict(status='error', codigo_http=422,
                        mensaje='El auditor rechazó el reporte: la fuente, las fechas o los conteos no son consistentes con los permisos.', agentes=traza, auditoria='rechazado', reporte=None)
        except Exception:
            telemetria.tarea('fallidas')
            return dict(status='error', codigo_http=503, mensaje='No se pudo preparar el reporte.', agentes=traza, reporte=None)


_lock_ensayo_correo = Lock()


def simular_correo(reporte, destinatario, transporte, clave, entregados):
    """Únicamente transporte simulado explícito. Idempotencia del ensayo, no de Render.

    No existe transporte SMTP de producción ni ruta de envío. Un timeout incierto
    no se reintenta automáticamente; se necesita conciliación en un futuro ejecutor.
    """
    if getattr(transporte, 'es_simulado', False) is not True:
        raise ValueError('Solo se admite un transporte de prueba.')
    if not destinatario.endswith('.invalid'):
        raise ValueError('Solo se admiten destinatarios ficticios .invalid.')
    with _lock_ensayo_correo:
        if clave in entregados:
            return 'duplicado_omitido'
        entregados.add(clave)  # Un resultado incierto se conserva; no reenviar a ciegas.
    transporte.enviar(destinatario, reporte)
    return 'simulado'
