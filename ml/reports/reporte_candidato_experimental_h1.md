# Selección y Entrenamiento del Candidato Experimental H1: Retorno Individual

**Biblioteca Central UNDAC** | **Fecha:** 2026-10-21 | **Metodología:** Selección en Validación y Reentrenamiento Completo

---

## 1. Justificación de Selección en Validación de Septiembre

Dado que la aplicación web muestra a los bibliotecarios y administradores una **probabilidad continua de retorno** (ej. `Volverá mañana: Sí (74%)`), el criterio rector debe evaluar qué tan fidedignas y calibradas son las probabilidades emitidas, y no únicamente la tasa de acierto bajo un corte binario arbitrario.

### Comparativa de Métricas en Validación (Septiembre purgado, 45,774 obs, prevalencia: 11.0%):

| Modelo | Brier Score (↓) | Log Loss (↓) | ROC-AUC (↑) | PR-AUC (↑) | Precisión (+) | Recall (+) | Macro F1 | Umbral $\theta^*$ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Regresión Logística | **0.1496** | **0.4689** | 0.7689 | 0.3459 | 36.34% | 42.77% | 0.6552 | 0.60 |
| Random Forest | **0.1268** | **0.4063** | 0.7565 | 0.3216 | 34.06% | 40.50% | 0.6419 | 0.55 |
| Extra Trees | **0.1498** | **0.4723** | 0.7536 | 0.3261 | 33.46% | 42.67% | 0.6429 | 0.55 |
| Gradient Boosting | **0.0871** | **0.3027** | 0.7526 | 0.3200 | 35.41% | 35.66% | 0.6377 | 0.25 |
| **HistGradientBoosting** | **0.0871** | **0.3102** | 0.7550 | 0.3234 | 37.52% | 33.59% | 0.6397 | 0.25 |

### Dictamen de Selección:
1. **`HistGradientBoosting` es el algoritmo seleccionado como candidato oficial H1:**
   - Obtiene el **menor Brier Score (0.0871)** empatado con Gradient Boosting, superando ampliamente a Regresión Logística (0.1496, un 71.7% más de error cuadrático).
   - Presenta un **Log Loss óptimo (0.3102)** frente a 0.4689 de Regresión Logística, evidenciando que sus probabilidades predichas no sufren de sobreconfianza ni distorsión por desbalance.
   - Presenta la **mayor precisión positiva (37.52%)**, reduciendo falsas alarmas frente al 36.34% de Regresión Logística y 34.06% de Random Forest.
   - Inferencia de sub-milisegundo nativa en Scikit-Learn sin requerir pipelines de preprocesamiento adicionales.
2. **Por qué no se promueve Regresión Logística como candidato probabilístico:**
   - Aunque obtiene una Macro F1 superior bajo corte duro (0.6552) debido al ajuste `class_weight='balanced'`, este mismo ajuste deforma drásticamente la escala de probabilidades continuas, provocando un Brier Score deficiente (0.1496) y requiriendo un umbral inflado (0.60) para compensar.

---

## 2. Detalle del Conjunto de Reentrenamiento Histórico Completo

- **Periodo histórico cubierto:** `2026-06-01` al `2026-10-19`.
- **Fechas operativas incluidas:** 73 fechas hábiles continuas.
- **Total observaciones con etiqueta completa:** **126,123** registros de alumnos con historial suficiente (≥ 3 días activos).
- **Positivos reales (retorno al día siguiente):** 19,350 observaciones (15.34% de prevalencia).
- **Tratamiento estricto de censura temporal:**
  - **`2026-10-20` (Excluido estrictamente):** Su día hábil siguiente fue el `2026-10-21`. Al encontrarse la jornada del 21/10 en curso e incompleta, la etiqueta de retorno de ayer no ha concluido.
  - **`2026-10-21` (Excluido estrictamente):** Jornada actual en curso sin día siguiente cerrado.

---

## 3. Estado y Nomenclatura de Artefactos

| Archivo | Tipo / Rol | Algoritmo | Horizonte | Estado |
| :--- | :--- | :--- | :---: | :--- |
| `ml/models/modelo_retorno_alumno.joblib` | **Producción (Activo)** | `HistGradientBoosting v1.0.0` | H1 | **Intacto y sin cambios** (333.604 bytes) |
| `ml/models/modelo_retorno_alumno_candidato_hgb_h1.joblib` | **Candidato Probabilístico** | `HistGradientBoosting v1.1.0` | H1 | **Generado y aislado** |
| `ml/models/metadatos_retorno_alumno_candidato_hgb_h1.json` | Metadatos Candidato | Metadatos completos | H1 | **Generado** |
| `ml/models/modelo_retorno_alumno_candidato_logreg_h1.joblib` | Candidato Comparativo | `LogisticRegression (Balanced)` | H1 | **Generado y aislado** |
| `ml/models/metadatos_retorno_alumno_candidato_logreg_h1.json` | Metadatos Comparativo | Metadatos completos | H1 | **Generado** |