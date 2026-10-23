# Coordinación local de agentes de herramientas

Implementación síncrona y determinista en `services/agentes_asistente.py`.
No son cinco prompts ni cinco llamadas a un proveedor. Cada agente ejecuta
responsabilidades concretas con herramientas existentes y resultados estructurados.

## Flujo

1. Orquestador: valida rol y sede de sesión y clasifica la tarea.
2. Afluencia: usa el coordinador y sus herramientas actuales de consultas/ML.
   Análisis Individual: reutiliza el informe por matrícula del mismo coordinador.
   Los ámbitos de ambas herramientas están restringidos por intención.
3. Auditor: comprueba origen autorizado, sede, fechas y consistencia de conteos.
   Rechaza predicciones en modo demostración e informes sin evidencia suficiente.
4. Reportes y Automatización: compone un cuerpo de texto a partir del resultado
   aprobado. No genera conclusiones adicionales mediante LLM ni envía correo.

El chat habitual no se sustituye. La acción «Preparar reporte con agentes y
auditoría» usa `/admin/api/asistente/reportes/preparar`. Obtiene permisos de la
sesión y valida CSRF. Los reportes personales solo existen en la respuesta a la
sesión autorizada; no se guardan ni se envían al proveedor externo.

La auditoría comprueba contratos y consistencia de las herramientas autorizadas;
no certifica de manera independiente la calidad de los registros de la BD.
El corte observado y el límite del periodo consultado son conceptos distintos.
Cuando la herramienta no acredita captura completa, el reporte lo advierte.

## Recurrencia: bloqueo deliberado

El Dockerfile publicado configura un worker de Gunicorn (`gthread`) con cuatro
hilos. No se encontró una cola,
un ejecutor único ni un almacén compartido de tareas y entregas en el repositorio.
Por ello no se instala scheduler en Flask, no hay endpoint de activación y no
se declara ninguna próxima ejecución.

`/admin/api/asistente/tareas/borrador` valida tipo, frecuencia y destinatario y
devuelve una configuración **no persistida e inactiva**, solo para revisión.
Exige lista autorizada del servidor `LLM_DESTINATARIOS_AUTORIZADOS`; si no está
configurada, ningún destinatario se autoriza. No se modificó el entorno.
El envío personal permanece bloqueado: falta una política de destinatarios
autorizados por persona/sede y de minimización de datos.

Para activar recurrencia faltan:

- almacenamiento compartido duradero de configuraciones, ejecuciones y entregas;
- ejecutor único o mecanismo de adquisición atómica, compatible con reintentos;
- clave única de ejecución por tarea y periodo, y conciliación tras timeout SMTP;
- política de destinatarios, periodo del reporte, hora y zona de ejecución;
- transporte de correo autorizado y configuración SMTP mediante entorno.

No se ha creado otra BD, migración, infraestructura ni transporte real.
`simular_correo` solo acepta transporte marcado explícitamente como simulado y
destinatarios `.invalid`. El registro del ensayo evita dos intentos con la misma
clave entre hilos y conserva una entrega incierta sin reintentar automáticamente.
Esto **no demuestra idempotencia multiproceso ni entrega exactamente una vez**.
Los reportes síncronos pueden recalcularse: no tienen efectos de envío.

## Uso del asistente

Los contadores nuevos miden intentos y tiempos del worker actual, sin preguntas,
respuestas, destinatarios o identificadores personales. Se pierden al reiniciar.
El JSON existente de métricas LLM tampoco es una fuente compartida segura para
varios procesos si se cambia la configuración; no se presenta como historial
permanente de Render. Con el worker actual, los contadores siguen siendo locales
y se pierden al reiniciar; los cuatro hilos comparten ese proceso.
El panel diferencia esas limitaciones y muestra recurrencia/entregas no disponibles.
Los diagnósticos de endpoint quedan restringidos a SuperAdmin.

Los agentes de reportes usan backend local con `permitir_proveedor=False`.
Las consultas generales del chat conservan su política existente de proveedor
opcional y fallback; no se presupone conectividad local ni en Render.

Se conservó el guard global de roles existente: para nuevos POST de Consultor
intercepta la solicitud antes del blueprint y devuelve `status:error` con HTTP
200. No llega a los agentes; los botones nuevos no se muestran a Consultor.
Normalizar ese código HTTP requiere un cambio separado del guard global.

## Validación

Las pruebas sustituyen las consultas, bloquean drivers de BD y tráfico HTTP,
usan matrículas y correos ficticios y comprueban delegación, rechazos,
permisos, telemetría, borradores y simulación de reintentos. La plantilla y el
JavaScript renderizado se validan sin acceder al despliegue.
