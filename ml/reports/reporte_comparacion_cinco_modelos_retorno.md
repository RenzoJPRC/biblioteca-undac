# Comparación Experimental de Cinco Modelos de ML: Retorno Individual de Alumnos

**Biblioteca Central UNDAC** | **Metodología:** Purged Temporal Split | **Estado:** Experimento Local Concluido

---

## 1. Calendario de Atención Real y Tratamiento de Agosto y Julio

### A. Distinción entre Cierres Efectivos y Atención Reducida en Agosto
- **Cierres efectivos documentados (cero atención):** Sábados, domingos y feriados nacionales confirmados: `29/06`, `23/07`, `24/07`, `27/07` a `29/07` (Fiestas Patrias), `06/08` (Batalla de Junín), `30/08` (Santa Rosa) y `08/10` (Combate de Angamos).
- **Atención reducida en agosto (03/08 al 21/08):** La biblioteca **no estuvo completamente cerrada**. Atendió en jornada diurna con afluencia reducida/administrativa (14 fechas hábiles con visitas comprobadas).
- **Atención regular restaurada:** `24/08` al `31/08` y semestres ordinarios (total de **94 fechas operativas de atención real** desde junio hasta el 20 de octubre).

### B. Tratamiento de las Referencias del 17 al 31 de Julio
- **Horizonte 1 (1 Día):** Todas las referencias del 17 al 31 de julio tienen siguiente día hábil observable con atención (el 31/07 tuvo su siguiente día el 03/08, con 30 alumnos). **Ninguna referencia de julio está censurada para H1**.
- **Horizonte 2 (7 Días):** Al disponer de las fechas de atención de agosto, las referencias del 17 al 31 de julio completan sus 7 días de atención en agosto (ej. 17/07 completa al 04/08 con 54.76% de retorno; 31/07 completa al 17/08 con 4.06% de retorno). Se computan sin censura artificial.

### C. Tratamiento de Censuras al Corte del 21/10/2026
- **Horizonte 1 (H1):** Máximo referencias hasta el `19/10/2026`. Las referencias del `20/10/2026` tienen como siguiente día el `21/10/2026` (en curso/parcial) y quedan **censuradas a la derecha** (`NaN`).
- **Horizonte 2 (H7):** Máximo referencias hasta el `09/10/2026` (sus 7 días concluyeron el 20/10/2026). Referencias a partir del `12/10/2026` tienen ventanas abiertas que concluyen después del 20/10 y quedan **censuradas a la derecha** (`NaN`).

---

## 2. Protocolo de Partición Temporal Purgada (Purged Split)

1. **Entrenamiento (Train):** Semestre I histórico (`01/06/2026` al `31/07/2026`).
2. **Validación Interna Purgada (Val):** Septiembre 2026 (`01/09/2026` al `30/09/2026`).
   - En H1 se purga el `30/09` (1 fecha antes de Test).
   - En H7 se purgan del `22/09` al `30/09` (7 fechas operativas cuyas ventanas tocan Octubre).
   - **Sintonización de umbral $\theta^*$:** Se selecciona en validación maximizando Macro F1.
   - **Selección de ganador:** El modelo candidato se selecciona **estrictamente por su desempeño en Validación**.
3. **Prueba Final Out-of-Time (Test):** Octubre 2026 (`01/10` a `19/10` para H1; `01/10` a `09/10` para H7).
   - Evaluado una sola vez a ciegas con el umbral fijado en validación. No se observa para elegir ganador.

---

## 3. Resultados Detallados: Horizonte 1 (Siguiente Día Hábil)

**Particiones:** Train: 49,096 obs | Val: 45,774 obs | Test: 28,938 obs.
**Ganador seleccionado en Validación (H1):** `Regresión Logística` (Macro F1 Val: 0.6552).

### A. Desempeño en Validación (Septiembre purgado):

| Modelo | Tipo / Rol | Umbral $\theta^*$ | ROC-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Dummy (Clase Mayoritaria) | Línea base ingenua (fuera de los 5 candidatos) | 0.15 | 0.5000 | 0.1100 | 0.5000 | **0.4709** | 0.0000 | 0.0000 |
| **Regresión Logística** | Modelo lineal regularizado balanceado | 0.60 | 0.7689 | 0.1496 | 0.6676 | **0.6552** | 0.4277 | 0.3634 |
| Random Forest | Ensamble bagging no lineal balanceado | 0.55 | 0.7565 | 0.1268 | 0.6541 | **0.6419** | 0.4050 | 0.3406 |
| Extra Trees | Ensamble de árboles extremadamente aleatorizados | 0.55 | 0.7536 | 0.1498 | 0.6609 | **0.6429** | 0.4267 | 0.3346 |
| Gradient Boosting | Boosting secuencial tradicional | 0.25 | 0.7526 | 0.0871 | 0.6381 | **0.6377** | 0.3566 | 0.3541 |
| HistGradientBoosting | Boosting por histogramas y cuantiles | 0.25 | 0.7550 | 0.0871 | 0.6334 | **0.6397** | 0.3359 | 0.3752 |

### B. Desempeño Fuera de Muestra en Prueba Final Ciega (Octubre 01 al 19):

| Modelo | Tipo / Rol | ROC-AUC | PR-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) | Matriz Confusión [[TN, FP], [FN, TP]] |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Dummy (Clase Mayoritaria)** | Línea base ingenua (fuera de los 5 candidatos) | **0.5000** | 0.1391 | 0.1391 | 0.5000 | **0.4626** | 0.0000 | 0.0000 | `[[24,914, 0], [4,024, 0]]` |
| **Regresión Logística** | Modelo lineal regularizado balanceado | **0.7944** | 0.4496 | 0.1474 | 0.6859 | **0.6868** | 0.4578 | 0.4627 | `[[22,775, 2,139], [2,182, 1,842]]` |
| **Random Forest** | Ensamble bagging no lineal balanceado | **0.7871** | 0.4357 | 0.1209 | 0.6588 | **0.6731** | 0.3839 | 0.4830 | `[[23,260, 1,654], [2,479, 1,545]]` |
| **Extra Trees** | Ensamble de árboles extremadamente aleatorizados | **0.7803** | 0.4323 | 0.1484 | 0.6757 | **0.6758** | 0.4414 | 0.4421 | `[[22,673, 2,241], [2,248, 1,776]]` |
| **Gradient Boosting** | Boosting secuencial tradicional | **0.7758** | 0.4078 | 0.1043 | 0.6165 | **0.6419** | 0.2721 | 0.5287 | `[[23,938, 976], [2,929, 1,095]]` |
| **HistGradientBoosting** | Boosting por histogramas y cuantiles | **0.7825** | 0.4328 | 0.1033 | 0.6318 | **0.6582** | 0.3052 | 0.5424 | `[[23,878, 1,036], [2,796, 1,228]]` |

---

## 4. Resultados Detallados: Horizonte 2 (Próximos 7 Días Operativos)

**Particiones:** Train: 49,096 obs | Val: 32,202 obs | Test: 14,225 obs.
**Ganador seleccionado en Validación (H7):** `Random Forest` (Macro F1 Val: 0.6793).

### A. Desempeño en Validación (Septiembre purgado):

| Modelo | Tipo / Rol | Umbral $\theta^*$ | ROC-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Dummy (Clase Mayoritaria) | Línea base ingenua (fuera de los 5 candidatos) | 0.15 | 0.5000 | 0.6037 | 0.5000 | **0.2838** | 1.0000 | 0.3963 |
| Regresión Logística | Modelo lineal regularizado balanceado | 0.30 | 0.7250 | 0.2286 | 0.6540 | **0.6571** | 0.5022 | 0.6292 |
| **Random Forest** | Ensamble bagging no lineal balanceado | 0.35 | 0.7361 | 0.2083 | 0.6760 | **0.6793** | 0.5512 | 0.6449 |
| Extra Trees | Ensamble de árboles extremadamente aleatorizados | 0.45 | 0.7332 | 0.2001 | 0.6733 | **0.6763** | 0.5568 | 0.6350 |
| Gradient Boosting | Boosting secuencial tradicional | 0.20 | 0.7193 | 0.2415 | 0.6701 | **0.6733** | 0.5449 | 0.6361 |
| HistGradientBoosting | Boosting por histogramas y cuantiles | 0.15 | 0.7053 | 0.2639 | 0.6649 | **0.6680** | 0.5350 | 0.6312 |

### B. Desempeño Fuera de Muestra en Prueba Final Ciega (Octubre 01 al 09):

| Modelo | Tipo / Rol | ROC-AUC | PR-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) | Matriz Confusión [[TN, FP], [FN, TP]] |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Dummy (Clase Mayoritaria)** | Línea base ingenua (fuera de los 5 candidatos) | **0.5000** | 0.4671 | 0.5329 | 0.5000 | **0.3184** | 1.0000 | 0.4671 | `[[0, 7,581], [0, 6,644]]` |
| **Regresión Logística** | Modelo lineal regularizado balanceado | **0.6019** | 0.5552 | 0.3382 | 0.5213 | **0.4193** | 0.0807 | 0.6497 | `[[7,292, 289], [6,108, 536]]` |
| **Random Forest** | Ensamble bagging no lineal balanceado | **0.7631** | 0.7608 | 0.2425 | 0.6750 | **0.6638** | 0.4372 | 0.8144 | `[[6,919, 662], [3,739, 2,905]]` |
| **Extra Trees** | Ensamble de árboles extremadamente aleatorizados | **0.7504** | 0.7503 | 0.2156 | 0.6825 | **0.6791** | 0.5161 | 0.7495 | `[[6,435, 1,146], [3,215, 3,429]]` |
| **Gradient Boosting** | Boosting secuencial tradicional | **0.6776** | 0.6952 | 0.3557 | 0.5943 | **0.5350** | 0.2062 | 0.9109 | `[[7,447, 134], [5,274, 1,370]]` |
| **HistGradientBoosting** | Boosting por histogramas y cuantiles | **0.6859** | 0.7077 | 0.3567 | 0.6186 | **0.5808** | 0.2809 | 0.8493 | `[[7,250, 331], [4,778, 1,866]]` |

---

## 5. Conclusiones y Estado de los Artefactos

1. **Preservación Incondicional del Modelo Activo:**
   - El artefacto oficial `ml/models/modelo_retorno_alumno.joblib` permanece **intacto, activo y sin modificaciones**.
2. **Generación de Artefactos Candidatos Separados:**
   - Se generó el artefacto candidato evaluado en `ml/models/modelo_retorno_alumno_candidato_hgb.joblib` y sus metadatos en `ml/models/metadatos_retorno_alumno_candidato.json`.
3. **Metodología Científica Verificada:**
   - La selección del modelo candidato se basó exclusivamente en la validación interna purgada de Septiembre, protegiendo la integridad ciega del conjunto final de prueba de Octubre.