# Auditoría de Calidad y Procedencia de Datos Históricos de Afluencia
**Sistema de Biblioteca UNDAC — Módulo de Machine Learning**

- **Fecha y hora de auditoría:** `2026-10-15T20:08:34.258267`
- **Dataset preparado evaluado:** `E:\API\scratch\worktree_integracion\ml\data\dataset_afluencia_real_preparado.csv`
- **Modo de ejecución:** Solo Lectura (sin escrituras, exportaciones personales ni modificaciones de base de datos)

---

## 1. Contexto Operativo Confirmado

De acuerdo con la validación de operación en la sede Central:
1. **Régimen de atención:** Lunes a Viernes, en horario de **08:00 a 20:45**.
2. **Captura continua:** El sistema y los torniquetes registraron entradas de forma continua mientras la biblioteca estuvo abierta y el servidor encendido.
3. **Apagado del servidor:** El servidor se apagaba diariamente a las **20:45 (fin de captura automática)**, finalizando la captura.
4. **Franja parcial:** La franja de las 20:00 corresponde exclusivamente al intervalo **45 minutos (20:00–20:45, franja parcial)**, por lo que **no debe compararse directamente** con las franjas de 60 minutos sin documentar su incompatibilidad.
5. **Cierres confirmados institucionales:**
   - **2026-07-27:** Cierre confirmado de biblioteca (puente patrio).
   - **2026-08-01 al 2026-08-23:** Cierre confirmado de biblioteca por vacaciones académicas de medio año.
   - **2026-08-24:** Reapertura confirmada con el reinicio de clases lectivas.

---

## 2. Calendario Contrastado y Clasificación de Fechas

Para evitar dobles conteos, cada fecha del calendario ha sido clasificada bajo una única condición prioritaria:

| Fecha | Condición Operativa Principal | Coincidencia de Calendario | Estado Operativo |
|---|---|---|---|
| `02/04/2026` y `03/04/2026` | Fuera de periodo (anterior) | Jueves y Viernes Santo | Fuera de datos (inician 10/04) |
| `01/05/2026` | Feriado nacional cerrado | Día del Trabajo (Viernes) | Cierre confirmado (0 filas) |
| `07/06/2026` | Fin de semana (Domingo) | Día de la Bandera y Batalla de Arica | No laborable del modelo |
| `29/06/2026` | Feriado nacional cerrado | San Pedro y San Pablo (Lunes) | Cierre confirmado (0 filas) |
| `23/07/2026` | Feriado nacional cerrado | Día de la Fuerza Aérea del Perú (Jueves) | Cierre confirmado (0 filas) |
| `27/07/2026` | Cierre confirmado de biblioteca | Día no laborable puente previo a Fiestas Patrias | Cierre confirmado (0 filas) |
| `28/07/2026` y `29/07/2026` | Feriado nacional cerrado | Fiestas Patrias (Martes y Miércoles) | Cierre confirmado (0 filas) |
| `01/08/2026` al `23/08/2026` | Cierre confirmado de vacaciones | Vacaciones académicas de medio año | Cierre confirmado |
| `06/08/2026` | Cierre confirmado de vacaciones | Feriado nacional Batalla de Junín | Absorbido en vacaciones (sin doble conteo) |
| `30/08/2026` | Fin de semana (Domingo) | Santa Rosa de Lima | No laborable del modelo |
| `08/10/2026` | Fuera de periodo (posterior) | Combate de Angamos (Jueves) | Fuera de datos (terminan 28/09) |

---

## 3. Conciliación Cuantitativa de Filas e Ingresos

La conciliación matemática entre el archivo fuente, los filtros del Notebook 03 y el dataset preparado es **100% exacta**:

| Etapa / Conjunto | Filas Totales | Filas Positivas | Filas con Cero | Ingresos Totales |
|---|:---:|:---:|:---:|:---:|
| **Archivo fuente original (`02_ingresos_reales_actual.csv`)** | **1,029** | 1,029 | 0 | **32,788** |
| *(-) Exclusión por Sede (Tarma)* | 1 | 1 | 0 | 1 |
| *(-) Exclusión por Fin de semana (Sáb 00h [4 ing], Dom 22h [1 ing])* | 2 | 2 | 0 | 5 |
| *(-) Exclusión por Fuera de horario (<8 o >20 h)* | 4 | 4 | 0 | 8 |
| *(+) Solapamiento fin de semana y fuera de horario (Sáb 00h y Dom 22h)* | 2 | 2 | 0 | 5 |
| **(=) Filas utilizables tras filtros en Notebook 03** | **1,024** | 1,024 | 0 | **32,779** |
| *(+) Expansión de cuadrícula (96 fechas × 13 horas = 1,248)* | +224 | 0 | +224 | 0 |
| **(=) Dataset preparado (`dataset_afluencia_real_preparado.csv`)** | **1,248** | **1,024** | **224** | **32,779** |

> **Conclusión de conciliación:** Se descartan exactamente **5 filas físicas netas** y **9 ingresos netos**, con una superposición exacta de 2 filas y 5 ingresos. Ningún ingreso se perdió por errores de cálculo.

---

## 4. Discrepancias: Filas de Origen Detectadas en Días de Cierre Confirmado

En cumplimiento de la **Regla 4**, no se eliminaron automáticamente los registros que aparecieron durante fechas de cierre confirmado. Se reportan como **discrepancias pendientes de investigación administrativa**:


---

## 5. Reclasificación Rigurosa de los 224 Ceros del Dataset Preparado

En cumplimiento de la **Regla 5**, los 224 ceros del dataset preparado **no son observaciones originales**, sino claves compuestas ausentes en la fuente completadas sintéticamente mediante `fillna(0)`. Su reclasificación operativa oficial es:

| Categoría de Cero | Franjas Afectadas | % del Total | Justificación y Regla Operativa |
|---|:---:|:---:|---|
| **1. Cierre confirmado de biblioteca (Vacaciones)** | **117** | 52.23% | **Regla 2:** La biblioteca estuvo cerrada del 01/08 al 23/08. La cuadrícula completó indebidamente con ceros días que debieron clasificarse como recinto cerrado. |
| **2. Desconocido (Puesta en marcha preoperativa)** | **45** | 20.09% | **Regla 3:** Días preoperativos (10/04, 29/04, 04/05, 05/05) sin evidencia confirmada de apertura ni captura continua de 08:00 a 20:45. |
| **3. Cero observado en franja parcial (20:00–20:45)** | **37** | 16.52% | **Regla 6:** Cero real observado en la franja reducida de 45 minutos (servidor apagado a las 20:45) en días lectivos regulares. |
| **4. Cero observado de demanda nula (Franja 60m)** | **25** | 11.16% | **Regla 1:** Cero real observado en franjas estándar de 60 minutos (08:00 a 19:59) en días lectivos confirmados abiertos con captura continua. |
| **Total Ceros Reclasificados** | **224** | **100.00%** | Conciliación exhaustiva de ceros. |

> **Hallazgo metodológico clave:** Únicamente **25 ceros (el 11.16%)** corresponden a franjas completas de 60 minutos con demanda nula real bajo régimen regular. El **72.32% (162 ceros)** corresponde a periodos de cierre confirmado o sin evidencia de operación.

---

## 6. Tratamiento de la Franja Parcial (20:00–20:45)

En cumplimiento de la **Regla 6**:
1. **Causa del truncamiento:** El servidor central se apagaba diariamente a las **20:45**, interrumpiendo la captura automática.
2. **Duración efectiva:** Contiene únicamente **45 minutos**, en contraste con los 60 minutos de las franjas de 08:00 a 19:59.
3. **Comportamiento estadístico:**
   - En todo el dataset preparado, la franja de las 20:00 acumuló solo **105 ingresos** en 96 fechas.
   - Presenta **55 ceros en total** (37 de ellos en días lectivos regulares).
   - Cuando hay afluencia, el promedio es de solo 2.56 ingresos por fecha activa.
4. **Impacto en Machine Learning:**
   - Tratar la hora 20 como una hora completa de 60 minutos distorsiona la comparación temporal e introduce un sesgo artificial a la baja.
   - En futuras iteraciones del modelo se recomienda normalizar esta variable por tasa horaria (`ingresos / 0.75`) o modelarla con una variable indicadora de franja parcial.

---

## 7. Limitaciones Técnicas y Recomendaciones

1. **Consulta de extracción original no documentada:** El script actual del repositorio filtra en SQL Server con `WHERE Hora BETWEEN 8 AND 20`, mientras que el CSV original fue exportado sin ese filtro. Se recomienda preservar la procedencia documentada en este reporte.
2. **Reentrenamiento futuro:** Cuando se reentrene el modelo de Machine Learning, no se deben usar las franjas de días cerrados como demanda cero.
3. **Investigación administrativa:** Solicitar al área de biblioteca una aclaración formal sobre las 65 filas registradas durante el receso vacacional de agosto (personal docente, administrativo, tesistas o modo de prueba de torniquetes).

---
*Reporte generado automáticamente bajo estándares de auditoría de solo lectura.*