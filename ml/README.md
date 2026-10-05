# Prototipo de predicción de afluencia

Este directorio contiene los recursos del módulo experimental de Machine
Learning para clasificar la afluencia horaria como baja, media o alta.

## Advertencia sobre los datos

`dataset_afluencia_sintetico.csv` contiene datos simulados. No representa
ingresos reales, no contiene información personal y no debe importarse en las
tablas operativas de SQL Server. El modelo definitivo deberá reentrenarse con
el histórico institucional agregado y anonimizado.

## Generación

Desde la raíz del proyecto:

```bash
python ml/scripts/generar_dataset.py
```

La semilla fija permite reproducir exactamente el mismo conjunto de datos.
