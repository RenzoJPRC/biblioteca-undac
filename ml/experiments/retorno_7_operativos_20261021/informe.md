# Retorno en los próximos siete días operativos — candidato experimental

Algoritmo: **GradientBoostingClassifier**. Corte completo confirmado: 2026-10-21.
Última referencia madura según calendario: 2026-10-12; última elegible usada: 2026-09-08.

Al menos un ingreso válido durante los siguientes siete días operativos, estrictamente posteriores a FechaRef.
Calendario congelado del proyecto. Histórico declarado; 29/09–21/10 confirmación operativa del responsable, no verificación técnica independiente. No se infiere continuidad a partir de MAX(FechaHora).
Ventanas [FechaRef-W,FechaRef), 7/14/30 días calendario; solo apertura confirmada; ventanas con desconocidos excluidas.
>=3 días activos válidos en 30 días anteriores. Sin requisito de visita en los últimos 7 días; insuficiencia no equivale a clase negativa.
Entrenamiento anterior a septiembre y todos sus objetivos anteriores a septiembre; validación de septiembre con objetivos completos antes de octubre.
Longitudinal temporal; pueden repetirse alumnos entre bloques. Sin IDs como variable; no mide generalización a alumnos nuevos. Conteos de solapamiento solo en memoria.
Menor Brier de validación septiembre; desempate menor log-loss, mayor AP, nombre. Sin ConvergenceWarning. Umbral fijo 0.5 para todos.
Retrospectivo ya examinado en el proyecto; no prueba independiente ni criterio de selección. Puede quedar vacío por cobertura histórica desconocida.

| Partición | Referencias | Fin de objetivos | Alumnos | Observaciones | Negativas | Positivas |
|---|---|---|---:|---:|---:|---:|
| entrenamiento | 2026-06-11 a 2026-07-13 (22 fechas) | 2026-07-22 | 1409 | 19150 | 4929 | 14221 |
| octubre_retrospectivo | None a None (0 fechas) | None | 0 | 0 | 0 | 0 |
| solo_ajuste_final | 2026-08-25 a 2026-08-31 (5 fechas) | 2026-09-09 | 129 | 331 | 27 | 304 |
| validacion_septiembre | 2026-09-01 a 2026-09-08 (6 fechas) | 2026-09-17 | 357 | 1419 | 156 | 1263 |

| Algoritmo | Brier ↓ | Log-loss ↓ | ROC-AUC | AP | Macro F1 | Convergencia |
|---|---:|---:|---:|---:|---:|---|
| LogisticRegression | 0.114365 | 0.376178 | 0.736992 | 0.958326 | 0.507327 | Sí |
| RandomForestClassifier | 0.149917 | 0.463319 | 0.703626 | 0.952389 | 0.567943 | No aplica: presupuesto fijo |
| ExtraTreesClassifier | 0.152561 | 0.469243 | 0.713036 | 0.955124 | 0.564488 | No aplica: presupuesto fijo |
| GradientBoostingClassifier | 0.093840 | 0.320001 | 0.707191 | 0.952439 | 0.470917 | No aplica: presupuesto fijo |
| HistGradientBoostingClassifier | 0.106208 | 0.354214 | 0.680710 | 0.949042 | 0.480924 | No aplica: presupuesto fijo |

Baseline de prevalencia de entrenamiento: Brier 0.119593.
**Límite de clasificación:** con el umbral predefinido 0.5, GradientBoostingClassifier clasificó las 1.419 observaciones de validación como positivas (1.263 verdaderos positivos, 156 falsos positivos, cero verdaderos negativos). Su especificidad fue 0 y Macro F1 0.470917. Ganó por Brier probabilístico, no por capacidad demostrada de clasificar ambas clases con ese umbral. No se reajustó el umbral después de observar el resultado. No está avalado para decisiones operativas binarias.
La validación abarca solo seis referencias (01–08/09), con 89.01% de positivos. Hay 309 alumnos compartidos entre entrenamiento y validación, según el protocolo longitudinal; no se evalúan alumnos completamente nuevos.
Aunque la última referencia con siete objetivos maduros es 12/10, sus variables previas de 30 días cruzan el 18/09 de captura desconocida. Por eso la última referencia elegible es 08/09 y no hay bloque de octubre evaluable. El 24/07 también permanece desconocido. Hace falta nueva cobertura confirmada suficiente para una evaluación temporal/prospectiva posterior; no puede afirmarse validación reciente independiente.
Las etiquetas de referencias cercanas se superponen: observaciones y alumnos no son muestras independientes. La selección es histórica y limitada, sin intervalos de confianza ni prueba prospectiva.
El ajuste final incluye toda observación madura elegible, incluida validación; sus métricas no evalúan ese reajuste independientemente. No se reutilizan métricas del otro horizonte.
No se exigieron visitas en los siete días previos. La insuficiencia de historial excluye ejemplos; no crea negativos.
Se preservan únicamente agregados diarios de auditoría, variables y etiquetas sin identificadores. No hay tabla de equivalencias ni tokens.
Dataset SHA256: 688012c8ce25f85591014b1c793d12e42e04b609083fd75e1a8d7365033ba62b. Artefacto SHA256: a82f051c337cb158ad73cc4764eb6130d893b606d4d7d913b1ebfdcf49e0fc0f.
