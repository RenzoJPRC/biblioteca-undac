"""Evaluación local separada de producción. Extracción agregada o reproducción offline.

Uso: python -m ml.scripts.evaluacion_afluencia_reproducible --output DIRECTORIO_NUEVO
     python -m ml.scripts.evaluacion_afluencia_reproducible --replay DIRECTORIO --output OTRO
Nunca guarda estimadores ni cambia la configuración de la aplicación.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.metrics import accuracy_score, balanced_accuracy_score, classification_report, confusion_matrix, f1_score

ROOT = Path(__file__).resolve().parents[2]
START, END = date(2026, 4, 10), date(2026, 10, 20)
CLASSES = ["Bajo", "Medio", "Alto"]
FEATURES = ["Hora", "DiaSemana", "Mes", "DiaMes", "SemanaAnio", "EsLunes", "EsViernes",
            "DuracionMinutos", "EsFranjaParcial", "IngresosUltimaFranjaConfirmada",
            "IngresosQuintaFranjaConfirmada", "PromedioMovil5", "PromedioHistoricoHora", "TendenciaReciente"]
HOLIDAYS = {"2026-04-02", "2026-04-03", "2026-05-01", "2026-06-07", "2026-06-29",
            "2026-07-23", "2026-07-28", "2026-07-29", "2026-08-06", "2026-08-30", "2026-10-08"}
SQL = """SELECT CAST(FechaHora AS DATE) AS Fecha,
 DATEPART(HOUR, FechaHora) AS Hora,
 COUNT_BIG(*) AS CantidadOriginal,
 SUM(CAST(CASE WHEN CAST(FechaHora AS TIME) >= CAST('08:00:00' AS TIME)
 AND CAST(FechaHora AS TIME) <= CAST('20:45:00' AS TIME) THEN 1 ELSE 0 END AS BIGINT)) AS CantidadHorario
FROM dbo.RegistroIngresos
WHERE FechaHora >= ? AND FechaHora < ? AND Sede = ? AND ISNULL(EsPrueba, 0) = 0
GROUP BY CAST(FechaHora AS DATE), DATEPART(HOUR, FechaHora)
ORDER BY Fecha, Hora"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(frame, path):
    frame.to_csv(path, index=False, lineterminator="\n", float_format="%.12g")


def model_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / "ml/models").glob("*")) if p.is_file()}


def calendar(day):
    key = day.isoformat()
    if date(2026, 8, 1) <= day <= date(2026, 8, 23):
        return "cerrado_receso"
    if day.weekday() >= 5:
        return "cerrado_fin_semana"
    if key in HOLIDAYS or key == "2026-07-27":
        return "cerrado_feriado_o_institucional"
    if key == "2026-07-24":
        return "desconocido_24_julio"
    return "atencion_segun_calendario"


def historical_coverage(frame):
    """No extiende la declaración histórica a días ausentes del archivo auditado."""
    return set(frame.loc[frame.EstadoOperativo.eq("abierto_regular"), "Fecha"].astype(str))


def build_dataset(raw, confirmed_dates):
    raw = raw.copy()
    raw["Fecha"] = raw.Fecha.astype(str)
    if raw.duplicated(["Fecha", "Hora"]).any():
        raise ValueError("Claves agregadas duplicadas")
    for col in ["Hora", "CantidadOriginal", "CantidadHorario"]:
        raw[col] = pd.to_numeric(raw[col], errors="raise")
        if (raw[col] % 1 != 0).any():
            raise ValueError("El agregado debe contener enteros")
    if not raw.Hora.between(0, 23).all() or (raw.CantidadHorario < 0).any() or (raw.CantidadOriginal < raw.CantidadHorario).any():
        raise ValueError("Agregado inválido")
    observed = {(r.Fecha, int(r.Hora)): r for r in raw.itertuples()}
    rows, daily = [], []
    for day in pd.date_range(START, END):
        d = day.date()
        key = d.isoformat()
        state = calendar(d)
        confirmed = key in confirmed_dates and state == "atencion_segun_calendario"
        source = ("confirmacion_operativa_responsable_2026-10-21" if key >= "2026-09-29"
                  else "declaracion_operativa_historica_reporte_auditoria") if confirmed else "sin_confirmacion_captura"
        daily.append({"Fecha": key, "Calendario": state, "CapturaConfirmada": confirmed,
                      "FuenteConfirmacion": source})
        for hour in range(8, 21):
            r = observed.get((key, hour))
            count = int(r.CantidadHorario) if r is not None else 0
            rows.append({"Fecha": key, "Hora": hour, "Sede": "Central", "Calendario": state,
                         "CapturaConfirmada": confirmed, "FuenteConfirmacion": source,
                         "FilaEnAgregado": r is not None,
                         "CantidadObservada": count if r is not None else np.nan,
                         "CantidadIngresos": count if confirmed else np.nan,
                         "CeroGenerado": confirmed and r is None,
                         "Elegible": confirmed, "DuracionMinutos": 45 if hour == 20 else 60,
                         "EsFranjaParcial": int(hour == 20)})
    evidence = pd.DataFrame(rows)
    # Las fechas cerradas/desconocidas no entran ni en shift ni en expanding.
    df = evidence[evidence.Elegible].copy().sort_values(["Hora", "Fecha"])
    dates = pd.to_datetime(df.Fecha)
    df["DiaSemana"], df["Mes"], df["DiaMes"] = dates.dt.dayofweek, dates.dt.month, dates.dt.day
    df["SemanaAnio"] = dates.dt.isocalendar().week.astype(int)
    df["EsLunes"] = df.DiaSemana.eq(0).astype(int)
    df["EsViernes"] = df.DiaSemana.eq(4).astype(int)
    group = df.groupby("Hora").CantidadIngresos
    df["IngresosUltimaFranjaConfirmada"] = group.shift(1)
    df["IngresosQuintaFranjaConfirmada"] = group.shift(5)
    df["PromedioMovil5"] = group.transform(lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    df["PromedioHistoricoHora"] = group.transform(lambda s: s.shift(1).expanding(min_periods=5).mean())
    df["TendenciaReciente"] = df.IngresosUltimaFranjaConfirmada - df.PromedioMovil5
    df = df.dropna(subset=FEATURES).sort_values(["Fecha", "Hora"]).reset_index(drop=True)
    df["NivelAfluencia"] = np.select([df.CantidadIngresos <= 7, df.CantidadIngresos <= 35], ["Bajo", "Medio"], default="Alto")
    return df, evidence, pd.DataFrame(daily)


def partitions(df):
    parts = {"entrenamiento": df[df.Fecha <= "2026-08-31"],
             "validacion": df[df.Fecha.between("2026-09-01", "2026-09-30")],
             "octubre_retrospectivo_ya_examinado": df[df.Fecha.between("2026-10-01", END.isoformat())]}
    for name, frame in parts.items():
        counts = frame.NivelAfluencia.value_counts()
        if set(counts.index) != set(CLASSES) or counts.min() < 5:
            raise ValueError(f"Datos insuficientes en {name}: se requieren al menos 5 ejemplos por clase; {counts.to_dict()}")
    return parts


def select_candidate(validation):
    return sorted(validation, key=lambda n: (-validation[n]["f1_macro"], -validation[n]["balanced_accuracy"], n))[0]


def metrics(y, pred):
    return {"f1_macro": float(f1_score(y, pred, labels=CLASSES, average="macro", zero_division=0)),
            "balanced_accuracy": float(balanced_accuracy_score(y, pred)) if set(y) == set(CLASSES) else None,
            "clases_presentes": sorted(set(y)),
            "nota": "F1 macro usa las tres clases fijas; balanced accuracy no se informa si faltan clases reales en el subgrupo",
            "accuracy": float(accuracy_score(y, pred)),
            "por_clase": classification_report(y, pred, labels=CLASSES, output_dict=True, zero_division=0),
            "matriz_confusion": confusion_matrix(y, pred, labels=CLASSES).tolist()}


def extract():
    # Importación diferida: reproducción y pruebas nunca necesitan importar db.
    from db import get_db_connection
    conn = get_db_connection()
    if conn is None:
        raise RuntimeError("No se pudo abrir la conexión configurada")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(SQL, (START.isoformat(), (END + timedelta(days=1)).isoformat(), "Central"))
        rows = cursor.fetchall()
        return pd.DataFrame([(str(r[0])[:10], int(r[1]), int(r[2]), int(r[3])) for r in rows],
                            columns=["Fecha", "Hora", "CantidadOriginal", "CantidadHorario"])
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()  # Sin commit, DML, procedimientos ni consultas de detalle.


def run(output, replay=None):
    before = model_hashes()
    if END >= datetime.now(ZoneInfo("America/Lima")).date():
        raise ValueError("El corte debe ser un día completo")
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "ml/experiments"):
        raise ValueError("La salida debe estar dentro de ml/experiments, separada de producción")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"estado": "preparacion", "corte_confirmado": END.isoformat(),
                "confirmacion_responsable": {"desde": "2026-09-29", "hasta": END.isoformat(),
                    "declarada": "2026-10-21", "naturaleza": "confirmación operativa del responsable; no comprobación técnica independiente"},
                "octubre": "evaluación retrospectiva ya examinada; no prueba virgen; no selección",
                "objetivo": "nivel del conteo por franja (45 o 60 minutos), umbrales fijos heredados 7 y 35; no tasa horaria",
                "rezagos": "últimas franjas de igual hora con apertura y captura confirmadas, shift previo; excluye cierres y desconocidos; evaluación diaria walk-forward, no pronóstico multietapa",
                "seleccion": "F1 macro de septiembre; desempate balanced accuracy y nombre; sin ajuste por octubre",
                "calendario": {"feriados": sorted(HOLIDAYS), "cierre_adicional": "2026-07-27",
                    "receso": ["2026-08-01", "2026-08-23"], "desconocido": "2026-07-24",
                    "horario": "lunes a viernes 08:00:00 a 20:45:00 inclusive", "fuente": "calendario confirmado por usuario y reporte_auditoria_procedencia_cobertura.md"},
                "entorno": {"python": platform.python_version(), "sklearn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__},
                "modelos_antes": before, "archivos": {}, "fecha_ejecucion": datetime.now(ZoneInfo("America/Lima")).isoformat()}
    try:
        if replay:
            replay = Path(replay)
            original = json.loads((replay / "manifest.json").read_text(encoding="utf-8"))
            for name in ["agregado_original.csv", "cobertura_fuente.csv"]:
                if sha(replay / name) != original["archivos"][name]:
                    raise ValueError(f"Hash de fuente incorrecto: {name}")
            raw = pd.read_csv(replay / "agregado_original.csv")
            coverage = pd.read_csv(replay / "cobertura_fuente.csv")
            manifest["reproduce_manifest_sha256"] = sha(replay / "manifest.json")
        else:
            coverage = pd.read_csv(ROOT / "ml/data/resumen_cobertura_por_fecha.csv")
            raw = extract()
        if raw.empty:
            raise ValueError("La extracción agregada está vacía")
        write_csv(raw, output / "agregado_original.csv")
        write_csv(coverage, output / "cobertura_fuente.csv")
        for source, name in [(Path(__file__), "receta_evaluacion.py"),
                             (ROOT / "ml/scripts/experimento_comparativo_modelos.py", "receta_modelos_original.py")]:
            (output / name).write_bytes(source.read_bytes())
        manifest["entorno"]["dependencias"] = {name: importlib.metadata.version(name)
            for name in ["scipy", "joblib", "threadpoolctl", "python-dotenv"]}
        requirements = {"scikit-learn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__,
                        **manifest["entorno"]["dependencias"]}
        (output / "requirements_evaluacion.txt").write_text("\n".join(f"{k}=={v}" for k,v in requirements.items()) + "\n", encoding="utf-8")
        (output / "consulta.sql").write_text(SQL + "\n", encoding="utf-8")
        manifest["consulta_parametros"] = [START.isoformat(), (END + timedelta(days=1)).isoformat(), "Central"]
        manifest["fuentes_preservadas"] = {str(p.relative_to(ROOT)): sha(p) for p in [
            ROOT / "ml/data/dataset_afluencia_real_preparado.csv", ROOT / "ml/data/resumen_cobertura_por_fecha.csv",
            ROOT / "ml/reports/reporte_auditoria_procedencia_cobertura.md",
            ROOT / "ml/scripts/experimento_comparativo_modelos.py", Path(__file__)]}
        confirmed = historical_coverage(coverage)
        confirmed.update(d.date().isoformat() for d in pd.date_range("2026-09-29", END))
        df, evidence, daily = build_dataset(raw, confirmed)
        write_csv(df, output / "dataset_evaluacion.csv")
        write_csv(evidence, output / "evidencia_franjas.csv")
        write_csv(daily, output / "calendario_cobertura.csv")
        manifest["resumen"] = {"filas_agregadas_originales": len(raw), "filas_evaluables": len(df),
            "ceros_generados": int(evidence.CeroGenerado.sum()),
            "fechas_desconocidas": daily.loc[~daily.CapturaConfirmada & daily.Calendario.str.startswith(("atencion", "desconocido")), "Fecha"].tolist()}
        parts = partitions(df)
        manifest["particiones"] = {n: {"filas": len(f), "fechas": int(f.Fecha.nunique()),
            "inicio": f.Fecha.min(), "fin": f.Fecha.max(), "clases": {k: int(v) for k,v in f.NivelAfluencia.value_counts().items()}} for n,f in parts.items()}
        # Misma configuración previa, siete algoritmos y Dummy como control adicional.
        from ml.scripts.experimento_comparativo_modelos import definir_modelos
        models = definir_modelos()
        models = {n.replace("Actual (Producción)", "(réplica experimental)"): clone(m) for n,m in models.items()}
        for model in models.values():
            if "n_jobs" in model.get_params():
                model.set_params(n_jobs=1)
        write_json(output / "configuracion_modelos.json", {n: {key: repr(value) for key,value in model.get_params(deep=True).items()}
                                                         for n,model in models.items()})
        train, val, october = parts.values()
        results = {"validacion": {}, "octubre_retrospectivo_ya_examinado": {}, "por_duracion": {}}
        fitted, times, notices = {}, {}, {}
        for name, model in models.items():
            started = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model.fit(train[FEATURES], train.NivelAfluencia)
            notices[name] = [type(w.message).__name__ + ": " + str(w.message) for w in caught]
            times[name] = time.perf_counter() - started
            fitted[name] = model
            results["validacion"][name] = metrics(val.NivelAfluencia, model.predict(val[FEATURES]))
        winner = select_candidate(results["validacion"])
        # El candidato se fija antes de inferir octubre; no se reajusta con validación.
        results["candidato_seleccionado_por_septiembre"] = winner
        write_json(output / "seleccion_septiembre.json", {"candidato": winner, "criterio": manifest["seleccion"], "resultados": results["validacion"]})
        for name, model in fitted.items():
            results["octubre_retrospectivo_ya_examinado"][name] = metrics(october.NivelAfluencia, model.predict(october[FEATURES]))
            results["por_duracion"][name] = {part: {str(minutes): metrics(f.NivelAfluencia, model.predict(f[FEATURES]))
                for minutes, f in frame.groupby("DuracionMinutos")} for part, frame in [("validacion", val), ("octubre", october)]}
        results["advertencias_entrenamiento"] = notices
        write_json(output / "resultados.json", results)
        write_json(output / "tiempos_no_deterministas.json", times)
        write_json(output / "candidato_metadata.json", {"nombre": winner, "promovido": False,
            "modelo_serializado": False, "produccion_modificada": False, "configuracion": str(fitted[winner]),
            "variables": FEATURES, "train": manifest["particiones"]["entrenamiento"]})
        report = ["# Evaluación local reproducible de Afluencia", "",
            "Central. Corte: 20/10/2026. Siete algoritmos y un Dummy adicional.",
            "La continuidad 29/09–20/10 es confirmación operativa del responsable, no verificación técnica independiente.",
            "La cobertura histórica usa únicamente las fechas abiertas del resumen auditado; no se extiende a fechas ausentes.",
            "24/07 y toda cobertura desconocida quedan fuera; cierres y receso no alimentan objetivos ni rezagos.",
            "El agregado original conserva registros de cierres, desconocidos y fuera de horario para auditoría.",
            "Ceros solo con calendario abierto y cobertura confirmada. Hora 20: duración explícita de 45 minutos para todos.",
            "Se predice el nivel del conteo de cada franja, no una tasa equivalente a 60 minutos.",
            "Rezagos de observaciones confirmadas previas a igual hora: evaluación diaria walk-forward, no pronóstico multietapa.",
            "Umbrales fijos heredados: Bajo <=7, Medio <=35, Alto >35; no se fuerza su coincidencia con percentiles nuevos.",
            "Octubre es evaluación retrospectiva ya examinada, no prueba virgen. No interviene en selección ni ajuste.",
            "", f"Candidato por septiembre: **{winner}**. No promovido ni serializado.", "",
            "| Algoritmo | F1 macro septiembre | F1 macro octubre retrospectivo |",
            "|---|---:|---:|"]
        for name in models:
            report.append(f"| {name} | {results['validacion'][name]['f1_macro']:.4f} | {results['octubre_retrospectivo_ya_examinado'][name]['f1_macro']:.4f} |")
        report += ["", "## Advertencias y límites", "", f"Advertencias de entrenamiento: {json.dumps(notices, ensure_ascii=False)}",
            "No se retoca la configuración a partir de octubre. Un aviso de falta de convergencia limita la conclusión sobre ese candidato.",
            "Las métricas estratificadas por duración pueden carecer de clases; balanced accuracy se deja nula en esos subgrupos.",
            "No se garantiza representatividad de fechas desconocidas, ni independencia estadística de observaciones temporales.",
            "", "## Reproducción sin BD", "",
            f"`{sys.executable} -m ml.scripts.evaluacion_afluencia_reproducible --replay {output.relative_to(ROOT).as_posix()} --output ml/experiments/afluencia_reproduccion_nueva`",
            "", "Usar la misma versión de recetas y dependencias guardadas. Comparar hashes de dataset y métricas; tiempos y fecha de ejecución varían.",
            "Las recetas congeladas son evidencia de código; el comando usa el módulo del repositorio. Revisar sus hashes antes de reproducir."]
        (output / "informe.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        manifest["estado"] = "evaluacion_completada"
        print(json.dumps({"estado": manifest["estado"], "candidato": winner, "particiones": manifest["particiones"]}, ensure_ascii=False))
    except Exception as exc:
        manifest["estado"] = "bloqueado"
        manifest["motivo"] = type(exc).__name__  # No guardar posibles credenciales de errores del driver.
        raise
    finally:
        manifest["modelos_despues"] = model_hashes()
        manifest["produccion_y_candidatos_existentes_intactos"] = before == manifest["modelos_despues"]
        manifest["archivos"] = {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != "manifest.json"}
        write_json(output / "manifest.json", manifest)
        if before != manifest["modelos_despues"]:
            raise RuntimeError("Se detectó un cambio en artefactos existentes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay")
    args = parser.parse_args()
    try:
        run(args.output, args.replay)
    except Exception as exc:
        print("Evaluación detenida:", type(exc).__name__, "(sin mostrar detalles de conexión)", file=sys.stderr)
        sys.exit(1)
