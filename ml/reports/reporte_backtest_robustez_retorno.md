# Reporte Técnico de Backtesting y Robustez: Modelos de Retorno Individual
**Biblioteca Central UNDAC** | **Fecha:** 2026-10-21 | **Metodología:** Backtesting Cronológico Purgado Multi-Corte

---

## 1. Definición Operativa de Agregaciones y Metodología

- **Línea Base vs. Modelos de Machine Learning:** La `Línea Base (Dummy)` se presenta separada como cota inferior no informativa y no compite con los algoritmos predictivos.
- **Definición de Agregación Temporal:**
  1. **Trayectoria A (Memoria Corta):** Promedio y desviación estándar calculados sobre **3 periodos cronológicos independientes**: Julio (C1), Septiembre (C2) y Octubre Intra-Semestre (C3A).
  2. **Trayectoria B (Acumulación Anual):** Promedio y desviación estándar calculados sobre **3 periodos cronológicos independientes**: Julio (C1), Septiembre (C2) y Octubre Acumulado (C3B).
  3. **Macro-Promedio de los 4 Cortes:** Promedio aritmético no ponderado de los 4 cortes evaluados (C1, C2, C3A, C3B).
     > [!IMPORTANT]
     > **Aviso de Ponderación:** Los cortes C3A y C3B comparten exactamente el mismo conjunto de prueba de Octubre con distinto régimen de entrenamiento; por tanto, el macro-promedio de los 4 cortes asigna un 50% de peso a las observaciones de Octubre.
- **Estructura del Panel y Correlación Intra-Alumno:** Los periodos temporales de entrenamiento y prueba no se solapan (garantizado por la purga temporal de 1 y 7 días), pero las observaciones de un mismo alumno en diferentes fechas pueden estar correlacionadas longitudinalmente (dependencia intra-sujeto a través del tiempo).
- **Carácter Exploratorio:** Las tablas presentan una comparación exploratoria de backtests históricos; **no se declara un ganador definitivo a partir de los conjuntos de prueba**.

---

## 2. Tabla Maestra de Particiones Verificada Automáticamente

| Corte | Horiz. | Bloque | Fecha Mínima | Fecha Máxima | Días Calendario | Días con Obs | Observaciones | Estado / Purga |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Corte 1** | H1 (1d) | `Full Train` | `2026-06-01` | `2026-06-26` | 20 d | 20 d | **17,890** | `Purgadas: 1` |
| **Corte 1** | H1 (1d) | `Inner Train` | `2026-06-01` | `2026-06-17` | 13 d | 13 d | **9,844** | `Entrenamiento Interno` |
| **Corte 1** | H1 (1d) | `Inner Val` | `2026-06-19` | `2026-06-26` | 6 d | 6 d | **7,014** | `suficiente` |
| **Corte 1** | H1 (1d) | `Test Final` | `2026-07-01` | `2026-07-30` | 17 d | 17 d | **27,958** | `Censuradas: 1` |
| **Corte 1** | H7 (7d) | `Full Train` | `2026-06-01` | `2026-06-18` | 14 d | 14 d | **10,876** | `Purgadas: 7` |
| **Corte 1** | H7 (7d) | `Inner Train` | `2026-06-01` | `2026-06-11` | 9 d | 9 d | **6,139** | `Entrenamiento Interno` |
| **Corte 1** | H7 (7d) | `Inner Val` | `N/A` | `N/A` | 0 d | 0 d | **0** | `insuficiente_por_purga` |
| **Corte 1** | H7 (7d) | `Test Final` | `2026-07-01` | `2026-07-15` | 11 d | 11 d | **16,857** | `Censuradas: 7` |
| **Corte 2** | H1 (1d) | `Full Train` | `2026-05-11` | `2026-07-30` | 53 d | 53 d | **50,930** | `Purgadas: 1` |
| **Corte 2** | H1 (1d) | `Inner Train` | `2026-05-11` | `2026-06-23` | 32 d | 32 d | **17,972** | `Entrenamiento Interno` |
| **Corte 2** | H1 (1d) | `Inner Val` | `2026-06-25` | `2026-07-30` | 20 d | 20 d | **31,755** | `suficiente` |
| **Corte 2** | H1 (1d) | `Test Final` | `2026-09-01` | `2026-09-30` | 22 d | 22 d | **48,089** | `Censuradas: 0` |
| **Corte 2** | H7 (7d) | `Full Train` | `2026-05-11` | `2026-07-15` | 47 d | 47 d | **39,829** | `Purgadas: 7` |
| **Corte 2** | H7 (7d) | `Inner Train` | `2026-05-11` | `2026-06-17` | 28 d | 28 d | **13,623** | `Entrenamiento Interno` |
| **Corte 2** | H7 (7d) | `Inner Val` | `2026-06-30` | `2026-07-15` | 12 d | 12 d | **18,160** | `suficiente` |
| **Corte 2** | H7 (7d) | `Test Final` | `2026-09-01` | `2026-09-30` | 22 d | 22 d | **48,089** | `Censuradas: 0` |
| **Corte 3A** | H1 (1d) | `Full Train` | `2026-09-01` | `2026-09-29` | 21 d | 21 d | **45,774** | `Purgadas: 1` |
| **Corte 3A** | H1 (1d) | `Inner Train` | `2026-09-01` | `2026-09-17` | 13 d | 13 d | **27,726** | `Entrenamiento Interno` |
| **Corte 3A** | H1 (1d) | `Inner Val` | `2026-09-21` | `2026-09-29` | 7 d | 7 d | **15,812** | `suficiente` |
| **Corte 3A** | H1 (1d) | `Test Final` | `2026-10-01` | `2026-10-19` | 12 d | 12 d | **28,938** | `Censuradas: 1` |
| **Corte 3A** | H7 (7d) | `Full Train` | `2026-09-01` | `2026-09-21` | 15 d | 15 d | **32,202** | `Purgadas: 7` |
| **Corte 3A** | H7 (7d) | `Inner Train` | `2026-09-01` | `2026-09-11` | 9 d | 9 d | **18,873** | `Entrenamiento Interno` |
| **Corte 3A** | H7 (7d) | `Inner Val` | `N/A` | `N/A` | 0 d | 0 d | **0** | `insuficiente_por_purga` |
| **Corte 3A** | H7 (7d) | `Test Final` | `2026-10-01` | `2026-10-09` | 6 d | 6 d | **14,225** | `Censuradas: 7` |
| **Corte 3B** | H1 (1d) | `Full Train` | `2026-05-11` | `2026-09-29` | 75 d | 74 d | **96,704** | `Purgadas: 1` |
| **Corte 3B** | H1 (1d) | `Inner Train` | `2026-05-11` | `2026-07-15` | 47 d | 47 d | **39,829** | `Entrenamiento Interno` |
| **Corte 3B** | H1 (1d) | `Inner Val` | `2026-07-17` | `2026-09-29` | 27 d | 26 d | **55,114** | `suficiente` |
| **Corte 3B** | H1 (1d) | `Test Final` | `2026-10-01` | `2026-10-19` | 12 d | 12 d | **28,938** | `Censuradas: 1` |
| **Corte 3B** | H7 (7d) | `Full Train` | `2026-05-11` | `2026-09-21` | 69 d | 62 d | **72,031** | `Purgadas: 7` |
| **Corte 3B** | H7 (7d) | `Inner Train` | `2026-05-11` | `2026-07-09` | 43 d | 43 d | **33,175** | `Entrenamiento Interno` |
| **Corte 3B** | H7 (7d) | `Inner Val` | `2026-09-01` | `2026-09-21` | 15 d | 15 d | **32,202** | `suficiente` |
| **Corte 3B** | H7 (7d) | `Test Final` | `2026-10-01` | `2026-10-09` | 6 d | 6 d | **14,225** | `Censuradas: 7` |

---

## 3. Línea Base Simple (Dummy Classifier)

| Corte | Horizonte | ROC-AUC | PR-AUC | Brier Score | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Corte 1** | H1 (1d) | 0.5000 | 0.2074 | 0.2074 | 0.5000 | 0.4421 | 0.0000 | 0.0000 |
| **Corte 1** | H7 (7d) | 0.5000 | 0.6489 | 0.3511 | 0.5000 | 0.3935 | 1.0000 | 0.6489 |
| **Corte 2** | H1 (1d) | 0.5000 | 0.1113 | 0.1113 | 0.5000 | 0.4705 | 0.0000 | 0.0000 |
| **Corte 2** | H7 (7d) | 0.5000 | 0.4136 | 0.5864 | 0.5000 | 0.2926 | 1.0000 | 0.4136 |
| **Corte 3A** | H1 (1d) | 0.5000 | 0.1391 | 0.1391 | 0.5000 | 0.4626 | 0.0000 | 0.0000 |
| **Corte 3A** | H7 (7d) | 0.5000 | 0.4671 | 0.4671 | 0.5000 | 0.3477 | 0.0000 | 0.0000 |
| **Corte 3B** | H1 (1d) | 0.5000 | 0.1391 | 0.1391 | 0.5000 | 0.4626 | 0.0000 | 0.0000 |
| **Corte 3B** | H7 (7d) | 0.5000 | 0.4671 | 0.5329 | 0.5000 | 0.3184 | 1.0000 | 0.4671 |

---

## 4. Comparativa de los Cinco Modelos de Machine Learning por Corte

### Corte 1: Semestre I (Referencia Histórica Junio -> Julio)

#### Horizonte 1 (Siguiente Día):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7436** | 0.4650 | **0.2578** | 0.80 | **0.6588** | 0.6674 | 0.4289 | 0.5022 | `optimizado_en_validacion` |
| **Random Forest** | **0.7354** | 0.4422 | **0.2020** | 0.60 | **0.6535** | 0.6538 | 0.4494 | 0.4522 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7450** | 0.4633 | **0.2112** | 0.65 | **0.6662** | 0.6702 | 0.4575 | 0.4892 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7304** | 0.4294 | **0.1459** | 0.30 | **0.6631** | 0.6434 | 0.5332 | 0.4026 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7418** | 0.4668 | **0.1408** | 0.30 | **0.6782** | 0.6594 | 0.5499 | 0.4266 | `optimizado_en_validacion` |

#### Horizonte 2 (Próximos 7 Días):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7414** | 0.8531 | **0.1952** | 0.50 | **0.6752** | 0.6649 | 0.6960 | 0.7883 | `insuficiente_por_purga` |
| **Random Forest** | **0.7227** | 0.8438 | **0.2040** | 0.50 | **0.6420** | 0.6348 | 0.6854 | 0.7594 | `insuficiente_por_purga` |
| **Extra Trees** | **0.7335** | 0.8490 | **0.2003** | 0.50 | **0.6709** | 0.6575 | 0.6726 | 0.7898 | `insuficiente_por_purga` |
| **Gradient Boosting** | **0.7121** | 0.8333 | **0.2034** | 0.50 | **0.6112** | 0.6145 | 0.8195 | 0.7173 | `insuficiente_por_purga` |
| **HistGradientBoosting** | **0.7296** | 0.8438 | **0.1982** | 0.50 | **0.6144** | 0.6176 | 0.8519 | 0.7165 | `insuficiente_por_purga` |

### Corte 2: Intersemestral (Semestre I -> Septiembre 2026)

#### Horizonte 1 (Siguiente Día):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7701** | 0.3498 | **0.1528** | 0.60 | **0.6715** | 0.6549 | 0.4426 | 0.3579 | `optimizado_en_validacion` |
| **Random Forest** | **0.7614** | 0.3319 | **0.1371** | 0.55 | **0.6709** | 0.6463 | 0.4554 | 0.3343 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7624** | 0.3386 | **0.1555** | 0.60 | **0.6579** | 0.6521 | 0.4015 | 0.3697 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7550** | 0.3210 | **0.0888** | 0.35 | **0.6076** | 0.6258 | 0.2627 | 0.4096 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7612** | 0.3377 | **0.0878** | 0.40 | **0.6028** | 0.6263 | 0.2415 | 0.4574 | `optimizado_en_validacion` |

#### Horizonte 2 (Próximos 7 Días):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7601** | 0.7108 | **0.2023** | 0.40 | **0.6849** | 0.6759 | 0.7257 | 0.5899 | `optimizado_en_validacion` |
| **Random Forest** | **0.7420** | 0.7011 | **0.2011** | 0.55 | **0.6800** | 0.6833 | 0.5266 | 0.6904 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7476** | 0.7063 | **0.2006** | 0.50 | **0.6892** | 0.6902 | 0.6188 | 0.6447 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7275** | 0.6944 | **0.2222** | 0.60 | **0.6793** | 0.6814 | 0.5806 | 0.6483 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7335** | 0.6941 | **0.2306** | 0.60 | **0.6850** | 0.6859 | 0.6159 | 0.6385 | `optimizado_en_validacion` |

### Corte 3A: Intra-Semestre II (Septiembre solo -> Octubre 2026)

#### Horizonte 1 (Siguiente Día):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7932** | 0.4453 | **0.1669** | 0.50 | **0.7146** | 0.6545 | 0.6188 | 0.3452 | `optimizado_en_validacion` |
| **Random Forest** | **0.7901** | 0.4310 | **0.1680** | 0.60 | **0.6921** | 0.6781 | 0.4953 | 0.4189 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7924** | 0.4327 | **0.1852** | 0.60 | **0.7023** | 0.6729 | 0.5400 | 0.3917 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7893** | 0.4442 | **0.0986** | 0.20 | **0.6842** | 0.6769 | 0.4697 | 0.4282 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7802** | 0.4412 | **0.0988** | 0.20 | **0.6854** | 0.6759 | 0.4759 | 0.4225 | `optimizado_en_validacion` |

#### Horizonte 2 (Próximos 7 Días):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.6736** | 0.6533 | **0.2764** | 0.50 | **0.5086** | 0.3669 | 0.0178 | 0.9672 | `insuficiente_por_purga` |
| **Random Forest** | **0.7828** | 0.7806 | **0.1906** | 0.50 | **0.7004** | 0.6965 | 0.5173 | 0.7954 | `insuficiente_por_purga` |
| **Extra Trees** | **0.7886** | 0.7884 | **0.1866** | 0.50 | **0.7152** | 0.7155 | 0.6102 | 0.7484 | `insuficiente_por_purga` |
| **Gradient Boosting** | **0.7715** | 0.7723 | **0.2107** | 0.50 | **0.6530** | 0.6299 | 0.3566 | 0.8608 | `insuficiente_por_purga` |
| **HistGradientBoosting** | **0.7584** | 0.7531 | **0.2180** | 0.50 | **0.6347** | 0.6071 | 0.3274 | 0.8321 | `insuficiente_por_purga` |

### Corte 3B: Acumulado Anual (Mayo a Septiembre -> Octubre 2026)

#### Horizonte 1 (Siguiente Día):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7929** | 0.4484 | **0.1422** | 0.65 | **0.6411** | 0.6669 | 0.3265 | 0.5436 | `optimizado_en_validacion` |
| **Random Forest** | **0.7936** | 0.4415 | **0.1407** | 0.60 | **0.6759** | 0.6839 | 0.4272 | 0.4778 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7924** | 0.4370 | **0.1608** | 0.65 | **0.6655** | 0.6787 | 0.3986 | 0.4877 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7923** | 0.4527 | **0.0978** | 0.35 | **0.6239** | 0.6549 | 0.2773 | 0.6029 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7847** | 0.4483 | **0.0980** | 0.35 | **0.6306** | 0.6608 | 0.2947 | 0.5874 | `optimizado_en_validacion` |

#### Horizonte 2 (Próximos 7 Días):

| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Regresión Logística** | **0.7844** | 0.7866 | **0.2024** | 0.55 | **0.6382** | 0.6039 | 0.3030 | 0.9088 | `optimizado_en_validacion` |
| **Random Forest** | **0.7883** | 0.7889 | **0.1998** | 0.60 | **0.6356** | 0.6001 | 0.2974 | 0.9085 | `optimizado_en_validacion` |
| **Extra Trees** | **0.7855** | 0.7850 | **0.1910** | 0.60 | **0.6513** | 0.6245 | 0.3393 | 0.8902 | `optimizado_en_validacion` |
| **Gradient Boosting** | **0.7832** | 0.7790 | **0.2120** | 0.70 | **0.5656** | 0.4809 | 0.1364 | 0.9587 | `optimizado_en_validacion` |
| **HistGradientBoosting** | **0.7697** | 0.7610 | **0.2224** | 0.65 | **0.5632** | 0.4768 | 0.1318 | 0.9542 | `optimizado_en_validacion` |

---

## 5. Resumen de Métricas Agregadas y Reconciliación

### A. Agregación Cronológica por Trayectorias (3 Periodos Independientes cada una):

| Modelo ML | Trayectoria A: AUC Medio H1 | Trayectoria A: Brier H1 | Trayectoria B: AUC Medio H1 | Trayectoria B: Brier H1 | Trayectoria A: AUC Medio H7 | Trayectoria A: Brier H7 | Trayectoria B: AUC Medio H7 | Trayectoria B: Brier H7 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Regresión Logística** | 0.769 $\pm$ 0.020 | 0.1925 | 0.769 $\pm$ 0.020 | 0.1842 | 0.725 $\pm$ 0.037 | 0.2246 | 0.762 $\pm$ 0.018 | 0.2000 |
| **Random Forest** | 0.762 $\pm$ 0.022 | 0.1690 | 0.763 $\pm$ 0.024 | 0.1600 | 0.749 $\pm$ 0.025 | 0.1986 | 0.751 $\pm$ 0.028 | 0.2016 |
| **Extra Trees** | 0.767 $\pm$ 0.020 | 0.1840 | 0.767 $\pm$ 0.020 | 0.1758 | 0.756 $\pm$ 0.023 | 0.1958 | 0.755 $\pm$ 0.022 | 0.1973 |
| **Gradient Boosting** | 0.758 $\pm$ 0.024 | 0.1111 | 0.759 $\pm$ 0.025 | 0.1108 | 0.737 $\pm$ 0.025 | 0.2121 | 0.741 $\pm$ 0.031 | 0.2125 |
| **HistGradientBoosting** | 0.761 $\pm$ 0.016 | 0.1092 | 0.763 $\pm$ 0.018 | 0.1089 | 0.741 $\pm$ 0.013 | 0.2156 | 0.744 $\pm$ 0.018 | 0.2171 |

### B. Rangos Globales Reconciliados y Macro-Promedio de los Cuatro Cortes:

*Advertencia: Este macro-promedio asigna 50% de ponderación a Octubre al incluir tanto C3A como C3B.*

| Modelo ML | Horiz. | Rango ROC-AUC [Min - Max] | Macro-Promedio AUC | Rango Brier [Min - Max] | Macro-Promedio Brier | Rango Recall Retorno | Macro-Promedio BalAcc |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Regresión Logística** | H1 (1d) | [0.7436 - 0.7932] | **0.7749** | [0.1422 - 0.2578] | 0.1799 | [0.3265 - 0.6188] | **0.6715** |
| **Random Forest** | H1 (1d) | [0.7354 - 0.7936] | **0.7701** | [0.1371 - 0.2020] | 0.1620 | [0.4272 - 0.4953] | **0.6731** |
| **Extra Trees** | H1 (1d) | [0.7450 - 0.7924] | **0.7730** | [0.1555 - 0.2112] | 0.1782 | [0.3986 - 0.5400] | **0.6730** |
| **Gradient Boosting** | H1 (1d) | [0.7304 - 0.7923] | **0.7668** | [0.0888 - 0.1459] | 0.1078 | [0.2627 - 0.5332] | **0.6447** |
| **HistGradientBoosting** | H1 (1d) | [0.7418 - 0.7847] | **0.7670** | [0.0878 - 0.1408] | 0.1064 | [0.2415 - 0.5499] | **0.6493** |
| **Regresión Logística** | H7 (7d) | [0.6736 - 0.7844] | **0.7399** | [0.1952 - 0.2764] | 0.2191 | [0.0178 - 0.7257] | **0.6267** |
| **Random Forest** | H7 (7d) | [0.7227 - 0.7883] | **0.7590** | [0.1906 - 0.2040] | 0.1989 | [0.2974 - 0.6854] | **0.6645** |
| **Extra Trees** | H7 (7d) | [0.7335 - 0.7886] | **0.7638** | [0.1866 - 0.2006] | 0.1946 | [0.3393 - 0.6726] | **0.6816** |
| **Gradient Boosting** | H7 (7d) | [0.7121 - 0.7832] | **0.7486** | [0.2034 - 0.2222] | 0.2121 | [0.1364 - 0.8195] | **0.6273** |
| **HistGradientBoosting** | H7 (7d) | [0.7296 - 0.7697] | **0.7478** | [0.1982 - 0.2306] | 0.2173 | [0.1318 - 0.8519] | **0.6243** |

---

## 6. Reconciliación Específica de Discrepancias Identificadas

1. **Reconciliación de Random Forest en Horizonte 2 (H7):**
   - **Valores por corte:** C1 = `0.7227`, C2 = `0.7420`, C3A = `0.7828`, C3B = `0.7883`.
   - **Rango global exacto:** `[0.7227, 0.7883]`.
   - **Trayectoria A (3 periodos: C1, C2, C3A):** Promedio = `0.7492` $\pm$ `0.025`.
   - **Trayectoria B (3 periodos: C1, C2, C3B):** Promedio = `0.7510` $\pm$ `0.027`.
   - **Macro-promedio de los 4 cortes:** `(0.7227 + 0.7420 + 0.7828 + 0.7883) / 4 = 0.75895` $\approx$ **`0.7590`**.
   *Conclusión:* La cifra `0.759` corresponde exactamente al macro-promedio de los 4 cortes, mientras que `0.751` corresponde a la Trayectoria B (tres periodos independientes con acumulación anual). Ambas provienen de la misma fuente de datos.

2. **Reconciliación del Rango de Brier Score de HistGradientBoosting en Horizonte 1 (H1):**
   - **Valores por corte:** C1 = `0.1408`, C2 = `0.0878`, C3A = `0.0988`, C3B = `0.0980`.
   - **Rango global exacto:** **`[0.0878, 0.1408]`**.
   - **Trayectoria A Brier medio:** `(0.1408 + 0.0878 + 0.0988) / 3 = 0.1091`.
   - **Trayectoria B Brier medio:** `(0.1408 + 0.0878 + 0.0980) / 3 = 0.1089`.
   - **Macro-promedio Brier 4 cortes:** `0.1064`.
   *Conclusión:* La mención previa '0.0878 a 0.0988' omitía el Corte 1 (`0.1408`). El rango fidedigno completo a través de todos los cortes evaluados es estrictamente **`[0.0878, 0.1408]`**.

---

## 7. Discusión Metodológica y Selección Interna

### A. Selección en Cortes Internos Purgados:
- **Cortes con Validación Suficiente ($\ge 3$ fechas hábiles tras purga):**
  * En **Corte 1 H1**, **Corte 2 H1 y H7**, **Corte 3A H1** y **Corte 3B H1 y H7**, la partición interna purgada (`Inner Train` $\rightarrow$ `Inner Val`) dispuso de fechas suficientes para seleccionar umbrales operativos $\theta^*$ que optimizaran la Macro F1 sin contaminar el conjunto de prueba.
  * En estos cortes, los modelos basados en árboles y boosting permitieron sintonizar umbrales adaptados a la prevalencia interna (entre $0.20$ y $0.70$), mientras que los modelos lineales mantuvieron umbrales entre $0.50$ y $0.80$.
- **Cortes con Validación Insuficiente (`insuficiente_por_purga`):**
  * En **Corte 1 H7** y **Corte 3A H7**, la purga temporal obligatoria de 7 días consumió la totalidad del periodo de validación interna disponible ($0$ fechas con observaciones válidas).
  * **Declaración metodológica:** **No hay ganador validado** en estos cortes. Al no existir un subconjunto de validación interno utilizable sin incurrir en fuga de información futura, no es posible comparar ni seleccionar válidamente ningún modelo a nivel interno; por diseño experimental, se aplicó el umbral neutral $\theta = 0.50$ fijado a priori.

### B. Estructura del Panel y Correlación Intra-Alumno:
- **Ausencia de Solapamiento Temporal:** La purga temporal estricta de 1 y 7 días garantiza que las ventanas de predicción no invadan los periodos de prueba posteriores.
- **Correlación Intra-Sujeto:** Dado que se trata de un panel longitudinal donde los mismos estudiantes asisten a la biblioteca a lo largo de varios meses, **las observaciones de un mismo alumno en distintas fechas pueden estar correlacionadas serialmente** (hábitos y estacionalidad personal). Las filas del panel no son observaciones $i.i.d.$; por tanto, los resultados ilustran la capacidad de generalización hacia nuevos periodos de calendario (*out-of-time*) sobre una población conocida con regularidad previa, y no deben extrapolarse a estudiantes nuevos sin historial (*cold-start*).

### C. Carácter Exploratorio y Ausencia de Ganador en Prueba:
- Las tablas de desempeño sobre los conjuntos de prueba representan una **comparación exploratoria de backtests históricos** bajo distintos cortes y regímenes de información.
- **No se declara un ganador definitivo a partir de los conjuntos de prueba.** Emplear el desempeño fuera de muestra para proclamar un modelo ganador violaría el principio de evaluación a ciegas.

---

## 8. Conclusiones y Estado Operativo

- **Conservación del Modelo en Producción:** El artefacto actual `modelo_retorno_alumno.joblib` (basado en `HistGradientBoostingClassifier v1.0.0`) permanece **intacto, activo y en servicio** en la aplicación web.
- **Validación Futura Pendiente:** Cualquier eventual consideración de cambio o actualización algorítmica queda supeditada a una futura **validación prospectiva en sombra** (*shadow testing*) en periodos operativos en vivo, descartando modificaciones directas a partir de backtests retrospectivos.
- **Integridad del Entorno:** Cero cambios en la base de datos, en las rutas de la aplicación, en las plantillas o en el repositorio Git.