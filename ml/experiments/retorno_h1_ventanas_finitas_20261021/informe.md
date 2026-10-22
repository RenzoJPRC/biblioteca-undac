# Retorno H1 experimental con ventanas finitas

Corte de captura: 2026-10-21. Dataset sin identificadores. Candidato: **HistGradientBoosting**.
Las métricas seleccionan solo por septiembre. Octubre ya fue examinado: evaluación retrospectiva, no prueba independiente.

## Definición y exclusiones

[FechaRef-W, FechaRef), W=7,14,30 días calendario; excluye FechaRef y objetivo
días activos únicos y número de escaneos válidos; sumas solo en días abiertos con captura confirmada
días activos / días abiertos confirmados de cada ventana; si una ventana no tiene apertura, se excluye el ejemplo en vez de inventar una tasa
>=3 días activos en la ventana previa de 30 días, sin acumulación de por vida
Se excluye el ejemplo si cualquier ventana contiene un desconocido o su siguiente día objetivo no es confirmable. No se salta el 24/07 como si fuera un cierre.
Fines de semana, feriados y receso 01/08–23/08 no son días de ausencia: no entran en conteos, tasas ni objetivos. Las ventanas son calendario, no se extienden para recuperar días cerrados.
No se modificó el constructor ni los parámetros de producción. Las nuevas 12 variables son exclusivas de este candidato.
Se conservan los cinco algoritmos/configuraciones previos y escalado de logística ajustado solo al train. HistGB conserva early_stopping=auto: su parada interna usa únicamente train, no septiembre/octubre; no es una validación temporal adicional.

## Particiones maduras tras exclusiones y purga

| Bloque | Fechas | Alumnos | Observaciones | Negativas | Positivas |
|---|---|---:|---:|---:|---:|
| octubre_retrospectivo | 2026-10-19 a 2026-10-20 (2) | 776 | 1502 | 938 | 564 |
| solo_ajuste_final | 2026-08-25 a 2026-08-31 (5) | 129 | 331 | 171 | 160 |
| train | 2026-06-11 a 2026-07-21 (28) | 1594 | 25737 | 18447 | 7290 |
| val | 2026-09-01 a 2026-09-16 (12) | 665 | 4531 | 2992 | 1539 |

## Validación de septiembre

| Algoritmo | Brier ↓ | Log-loss ↓ | PR-AUC(AP) ↑ | ROC-AUC ↑ | Macro F1 | Umbral | Convergencia |
|---|---:|---:|---:|---:|---:|---:|---|
| Dummy (Clase Mayoritaria) | 0.3397 | 12.2426 | 0.3397 | 0.5000 | 0.3977 | 0.50 | No aplica (presupuesto fijo o baseline) |
| Regresión Logística | 0.3247 | 0.8848 | 0.5268 | 0.6818 | 0.6204 | 0.75 | Sí |
| Random Forest | 0.2388 | 0.6733 | 0.5008 | 0.6722 | 0.6208 | 0.60 | No aplica (presupuesto fijo o baseline) |
| Extra Trees | 0.2421 | 0.6807 | 0.5060 | 0.6713 | 0.6185 | 0.60 | No aplica (presupuesto fijo o baseline) |
| Gradient Boosting | 0.2084 | 0.6061 | 0.5022 | 0.6720 | 0.6218 | 0.40 | No aplica (presupuesto fijo o baseline) |
| HistGradientBoosting | 0.2079 | 0.6054 | 0.5175 | 0.6752 | 0.6250 | 0.40 | Parada interna anticipada |

## Límites

longitudinal temporal, no alumnos disjuntos; grupos usados solo en memoria para conteos/solapamientos
Alumnos compartidos (solo conteos): {'train_octubre_retrospectivo': 611, 'train_val': 541, 'val_octubre_retrospectivo': 414}.
La falta de confirmación del 18/09 censura también sus ventanas de 30 días: octubre puede quedar con muy pocas fechas, insuficientes para conclusiones de robustez temporal. Su resultado no decide la selección.
Conservar ventanas calendario implica menos días observables cerca del receso; no se rellena el cierre como inasistencia ni se alarga la ventana.
El mínimo de tres días activos en 30 días cambia la población elegible respecto del historial de por vida; las métricas antiguas no son una comparación directa.
El umbral se ajustó en la misma validación utilizada para seleccionar; F1 de validación es optimista. No se aplicó calibración adicional ni se demostró calibración perfecta.
El candidato final incorpora todas las filas válidas maduras, incluso septiembre/octubre; no tiene una prueba independiente nueva.
Convergencia/ajuste final: Parada interna anticipada. Producción intacta y candidato no conectado a interfaz.
