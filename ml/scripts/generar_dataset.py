"""Genera un dataset horario sintético para el prototipo de afluencia.

Los datos creados por este script NO representan ingresos reales y nunca deben
insertarse en RegistroIngresos. Su único propósito es desarrollar y probar el
pipeline de Machine Learning hasta disponer del histórico institucional.
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path


SEMILLA = 20260926
FECHA_INICIO = date(2026, 3, 1)
FECHA_FIN = date(2026, 8, 31)
HORAS_ATENCION = range(8, 21)  # 08:00 a 20:29 aproximadamente

SEDES = {
    "Central": 1.00,
    "Tarma": 0.58,
    "La Merced": 0.52,
    "Oxapampa": 0.40,
    "Paucartambo": 0.30,
    "Yanahuanca": 0.34,
}

PERFIL_HORARIO = {
    8: 4,
    9: 9,
    10: 16,
    11: 20,
    12: 14,
    13: 6,
    14: 10,
    15: 16,
    16: 19,
    17: 15,
    18: 10,
    19: 6,
    20: 2,
}

FACTOR_DIA = {0: 0.95, 1: 1.08, 2: 1.05, 3: 1.00, 4: 0.84}

# Feriados nacionales dentro del periodo. Se conservan con cero ingresos para
# que el prototipo distinga un día cerrado de una hora ordinaria de baja demanda.
FERIADOS = {
    date(2026, 4, 2),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 6, 7),
    date(2026, 6, 29),
    date(2026, 7, 23),
    date(2026, 7, 28),
    date(2026, 7, 29),
    date(2026, 8, 6),
}

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def periodo_academico(fecha: date) -> tuple[str, float, int]:
    """Devuelve nombre, factor de afluencia e indicador de vacaciones."""
    if fecha.month == 3:
        return "Inicio de semestre", 0.82, 0
    if fecha.month in (4, 5):
        return "Periodo regular", 1.00, 0
    if fecha.month == 6:
        return "Evaluaciones", 1.20, 0
    if fecha.month == 7 and fecha.day <= 17:
        return "Evaluaciones finales", 1.28, 0
    if fecha.month == 7:
        return "Vacaciones", 0.38, 1
    if fecha.month == 8 and fecha.day <= 16:
        return "Vacaciones", 0.42, 1
    return "Inicio de semestre", 0.78, 0


def poisson(rng: random.Random, promedio: float) -> int:
    """Muestreo Poisson sin depender de numpy."""
    limite = math.exp(-promedio)
    producto = 1.0
    k = 0
    while producto > limite:
        k += 1
        producto *= rng.random()
    return max(0, k - 1)


def generar_filas() -> list[dict[str, object]]:
    rng = random.Random(SEMILLA)
    filas: list[dict[str, object]] = []
    fecha = FECHA_INICIO

    while fecha <= FECHA_FIN:
        # La biblioteca no atiende sábados ni domingos según el alcance actual.
        if fecha.weekday() <= 4:
            nombre_periodo, factor_periodo, es_vacaciones = periodo_academico(fecha)
            es_feriado = int(fecha in FERIADOS)

            for sede, factor_sede in SEDES.items():
                # Variación por sede y día para evitar curvas idénticas.
                variacion_diaria = rng.uniform(0.82, 1.18)
                for hora in HORAS_ATENCION:
                    if es_feriado:
                        cantidad = 0
                    else:
                        promedio = (
                            PERFIL_HORARIO[hora]
                            * factor_sede
                            * FACTOR_DIA[fecha.weekday()]
                            * factor_periodo
                            * variacion_diaria
                        )
                        cantidad = poisson(rng, max(0.15, promedio))

                    filas.append(
                        {
                            "Fecha": fecha.isoformat(),
                            "Hora": hora,
                            "Sede": sede,
                            "CantidadIngresos": cantidad,
                            "DiaSemana": DIAS[fecha.weekday()],
                            "NumeroDiaSemana": fecha.weekday(),
                            "Mes": fecha.month,
                            "EsFinSemana": 0,
                            "EsFeriado": es_feriado,
                            "EsVacaciones": es_vacaciones,
                            "PeriodoAcademico": nombre_periodo,
                            "EsSintetico": 1,
                        }
                    )
        fecha += timedelta(days=1)

    return filas


def main() -> None:
    salida = Path(__file__).resolve().parents[1] / "data" / "dataset_afluencia_sintetico.csv"
    salida.parent.mkdir(parents=True, exist_ok=True)
    filas = generar_filas()

    with salida.open("w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)

    print(f"Dataset generado: {salida}")
    print(f"Filas: {len(filas):,}")
    print(f"Periodo: {FECHA_INICIO} a {FECHA_FIN}")
    print(f"Sedes: {', '.join(SEDES)}")


if __name__ == "__main__":
    main()
