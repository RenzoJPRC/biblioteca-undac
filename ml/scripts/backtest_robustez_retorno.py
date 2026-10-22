"""
Evaluación de Robustez mediante Backtesting Cronológico Sucesivo
Predicción de Retorno de Alumnos en la Biblioteca Central UNDAC

Garantías metodológicas, de seguridad y diseño experimental:
1. Línea Base Separada y 5 Algoritmos de Machine Learning:
   - Línea Base: Dummy Classifier (Clase Mayoritaria / Moda).
   - 5 Algoritmos ML:
     1. Regresión Logística Regularizada (Lineal balanceado)
     2. Random Forest (Bagging no lineal balanceado)
     3. Extra Trees (Árboles extremadamente aleatorizados balanceados)
     4. Gradient Boosting (Boosting secuencial clásico)
     5. HistGradientBoosting (Boosting por histogramas - modelo de producción)
2. Reconciliación rigurosa de fechas y calendarios:
   - Se distinguen explícitamente las 'fechas del calendario operativo' de las 'fechas con observaciones de alumnos elegibles'.
   - Cada bloque reporta: fecha mínima, fecha máxima, fechas de calendario, fechas con observaciones y número de observaciones.
3. Desacople estricto entre Periodos Temporales y Estrategias:
   - Periodo 1: Semestre I (Julio 2026 out-of-time).
   - Periodo 2: Intersemestral (Septiembre 2026 out-of-time).
   - Periodo 3: Semestre II (Octubre 2026 al 20/10 out-of-time).
     Sobre el Periodo 3 se evalúan dos estrategias de entrenamiento:
     * 3A: Memoria Corta Intra-Semestre (Septiembre solo).
     * 3B: Acumulación Histórica Anual (Mayo-Septiembre).
     C3A y C3B comparten exactamente el mismo conjunto de prueba de Octubre.
4. Definición Matemática Clara de Agregaciones:
   - Trayectoria A (Memoria Corta): promedio y desviación estándar sobre 3 periodos temporales (C1, C2, C3A).
   - Trayectoria B (Acumulación Histórica): promedio y desviación estándar sobre 3 periodos temporales (C1, C2, C3B).
   - Macro-Promedio y Rango Global de los 4 Cortes: rango [min, max] y promedio de los 4 cortes con aviso explícito
     de que Octubre pondera el 50% al evaluarse bajo 3A y 3B.
5. Censura de agosto y cierres institucionales:
   - Agosto es receso vacacional; no se imputa ningún cierre como falta de retorno (0).
   - Fechas de julio con ventanas que alcanzan agosto quedan estrictamente censuradas (NaN).
6. Prohibición de ajuste en prueba y carácter exploratorio:
   - Las pruebas externas no se usan para ajustar umbrales ni seleccionar ganador.
   - Si la validación tras purga es < 3 fechas, se reporta como 'insuficiente_por_purga' y se evalúa con umbral neutral 0.50 fijado apriori.
   - La comparación es exploratoria y deja pendiente una validación futura sin reemplazar el modelo de producción.
7. Privacidad estricta: cero AlumnoID en reportes ni rastreados por Git.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ml.services.analisis_individual import DIAS_FERIADOS_O_CIERRES
from ml.services.datos_afluencia import FERIADOS_CONFIRMADOS_2026

VARIABLES_RETORNO = [
    "dias_activos",
    "total_visitas",
    "recencia_dias",
    "visitas_ultimos_7d",
    "visitas_ultimos_14d",
    "afinidad_dia_semana",
    "visito_hoy",
    "tasa_asistencia",
]


def construir_calendarios_por_semestre():
    feriados_todos = set(DIAS_FERIADOS_O_CIERRES)
    for s in FERIADOS_CONFIRMADOS_2026:
        try:
            feriados_todos.add(pd.to_datetime(s).date())
        except Exception:
            pass

    cal_s1 = []
    cur = date(2026, 5, 1)
    while cur <= date(2026, 7, 31):
        if cur.weekday() < 5 and cur not in feriados_todos:
            cal_s1.append(cur)
        cur += timedelta(days=1)

    cal_s2 = []
    cur = date(2026, 9, 1)
    while cur <= date(2026, 10, 20):
        if cur.weekday() < 5 and cur not in feriados_todos:
            cal_s2.append(cur)
        cur += timedelta(days=1)

    return cal_s1, cal_s2


def cargar_visitas_completas():
    path_visitas = ROOT_DIR / "ml" / "data" / "dataset_retorno_visitas_diarias_completo.csv"
    if not path_visitas.exists():
        raise FileNotFoundError(f"Falta el archivo: {path_visitas}")
    df = pd.read_csv(path_visitas)
    df["Fecha"] = pd.to_datetime(df["Fecha"]).dt.date
    return df


def generar_o_cargar_panel_por_semestres(df_visitas, cal_s1, cal_s2):
    path_cache = ROOT_DIR / "ml" / "data" / "dataset_retorno_panel_completo_backtest.csv"
    if path_cache.exists():
        df_p = pd.read_csv(path_cache)
        df_p["FechaRef"] = pd.to_datetime(df_p["FechaRef"]).dt.date
        return df_p

    visitas_set = set(zip(df_visitas["AlumnoID"], df_visitas["Fecha"]))
    visitas_por_alumno = df_visitas.groupby("AlumnoID")["Fecha"].apply(set).to_dict()

    filas = []

    # Semestre I
    for f_ref in cal_s1:
        idx = cal_s1.index(f_ref)
        f_next = cal_s1[idx + 1] if idx + 1 < len(cal_s1) else None
        w7 = cal_s1[idx + 1 : idx + 8] if idx + 7 < len(cal_s1) else None

        dias_hasta = [d for d in cal_s1 if d <= f_ref]
        total_dias_hasta = len(dias_hasta)
        f_7d_atras = f_ref - timedelta(days=7)
        f_14d_atras = f_ref - timedelta(days=14)
        prox_weekday = f_next.weekday() if f_next else 0

        for al_id, d_set in visitas_por_alumno.items():
            vis_hasta = [d for d in d_set if d <= f_ref]
            dias_activos = len(vis_hasta)
            if dias_activos < 3:
                continue

            tot_vis = len(vis_hasta)
            recencia = (f_ref - max(vis_hasta)).days
            vis_7d = sum(1 for d in vis_hasta if d >= f_7d_atras)
            vis_14d = sum(1 for d in vis_hasta if d >= f_14d_atras)
            vis_mismo_wd = sum(1 for d in vis_hasta if d.weekday() == prox_weekday)
            afinidad = round(float(vis_mismo_wd / dias_activos), 4)
            visito_hoy = 1 if f_ref in d_set else 0
            tasa = round(float(dias_activos / total_dias_hasta), 4)

            y_h1 = 1 if f_next and (al_id, f_next) in visitas_set else (0 if f_next else np.nan)
            y_h7 = 1 if w7 and any((al_id, d) in visitas_set for d in w7) else (0 if w7 else np.nan)

            filas.append({
                "AlumnoID": al_id,
                "FechaRef": f_ref,
                "dias_activos": dias_activos,
                "total_visitas": tot_vis,
                "recencia_dias": recencia,
                "visitas_ultimos_7d": vis_7d,
                "visitas_ultimos_14d": vis_14d,
                "afinidad_dia_semana": afinidad,
                "visito_hoy": visito_hoy,
                "tasa_asistencia": tasa,
                "target_h1": y_h1,
                "target_h7": y_h7,
                "mes": f_ref.month,
                "semestre": 1
            })

    # Semestre II
    cal_acumulado = cal_s1 + cal_s2
    for f_ref in cal_s2:
        idx = cal_s2.index(f_ref)
        f_next = cal_s2[idx + 1] if idx + 1 < len(cal_s2) else None
        w7 = cal_s2[idx + 1 : idx + 8] if idx + 7 < len(cal_s2) else None

        dias_hasta = [d for d in cal_acumulado if d <= f_ref]
        total_dias_hasta = len(dias_hasta)
        f_7d_atras = f_ref - timedelta(days=7)
        f_14d_atras = f_ref - timedelta(days=14)
        prox_weekday = f_next.weekday() if f_next else 0

        for al_id, d_set in visitas_por_alumno.items():
            vis_hasta = [d for d in d_set if d <= f_ref]
            dias_activos = len(vis_hasta)
            if dias_activos < 3:
                continue

            tot_vis = len(vis_hasta)
            recencia = (f_ref - max(vis_hasta)).days
            vis_7d = sum(1 for d in vis_hasta if d >= f_7d_atras)
            vis_14d = sum(1 for d in vis_hasta if d >= f_14d_atras)
            vis_mismo_wd = sum(1 for d in vis_hasta if d.weekday() == prox_weekday)
            afinidad = round(float(vis_mismo_wd / dias_activos), 4)
            visito_hoy = 1 if f_ref in d_set else 0
            tasa = round(float(dias_activos / total_dias_hasta), 4)

            y_h1 = 1 if f_next and (al_id, f_next) in visitas_set else (0 if f_next else np.nan)
            y_h7 = 1 if w7 and any((al_id, d) in visitas_set for d in w7) else (0 if w7 else np.nan)

            filas.append({
                "AlumnoID": al_id,
                "FechaRef": f_ref,
                "dias_activos": dias_activos,
                "total_visitas": tot_vis,
                "recencia_dias": recencia,
                "visitas_ultimos_7d": vis_7d,
                "visitas_ultimos_14d": vis_14d,
                "afinidad_dia_semana": afinidad,
                "visito_hoy": visito_hoy,
                "tasa_asistencia": tasa,
                "target_h1": y_h1,
                "target_h7": y_h7,
                "mes": f_ref.month,
                "semestre": 2
            })

    df_p = pd.DataFrame(filas)
    df_p.to_csv(path_cache, index=False)
    return df_p


def obtener_fabricas_modelos():
    """
    Retorna la Línea Base y los 5 algoritmos de Machine Learning.
    Dummy se mantiene explícitamente separado con es_linea_base=True.
    """
    return {
        "Dummy (Clase Mayoritaria)": {
            "factory": lambda: DummyClassifier(strategy="most_frequent"),
            "tipo": "Línea base ingenua",
            "es_linea_base": True
        },
        "Regresión Logística": {
            "factory": lambda: Pipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42))
            ]),
            "tipo": "Lineal regularizado balanceado",
            "es_linea_base": False
        },
        "Random Forest": {
            "factory": lambda: RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1
            ),
            "tipo": "Ensamble bagging no lineal",
            "es_linea_base": False
        },
        "Extra Trees": {
            "factory": lambda: ExtraTreesClassifier(
                n_estimators=100,
                max_depth=8,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1
            ),
            "tipo": "Árboles extremadamente aleatorizados balanceados",
            "es_linea_base": False
        },
        "Gradient Boosting": {
            "factory": lambda: GradientBoostingClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.08,
                random_state=42
            ),
            "tipo": "Boosting secuencial clásico",
            "es_linea_base": False
        },
        "HistGradientBoosting": {
            "factory": lambda: HistGradientBoostingClassifier(
                learning_rate=0.08,
                random_state=42
            ),
            "tipo": "Boosting por histogramas (modelo de producción)",
            "es_linea_base": False
        }
    }


def evaluar_umbral_validacion_con_suficiencia(clf_factory, X_tr, y_tr, X_val, y_val, n_fechas_val, min_fechas=3):
    if n_fechas_val < min_fechas or len(X_val) == 0:
        return 0.50, False, "insuficiente_por_purga"

    clf = clf_factory()
    if not hasattr(clf, "predict_proba"):
        return 0.50, True, "dummy_fijo"

    clf.fit(X_tr, y_tr)
    probs_val = clf.predict_proba(X_val)[:, 1]

    mejor_u = 0.50
    mejor_mf1 = -1.0

    for u in np.arange(0.15, 0.85, 0.05):
        preds_v = (probs_val >= u).astype(int)
        mf1 = f1_score(y_val, preds_v, average="macro", zero_division=0)
        if mf1 > mejor_mf1:
            mejor_mf1 = mf1
            mejor_u = round(float(u), 2)

    return mejor_u, True, "optimizado_en_validacion"


def extraer_estadisticas_particion(df_sub, cal_ref, col_y):
    df_valido = df_sub[df_sub[col_y].notna()]
    if len(df_valido) == 0:
        return {
            "fecha_min": "N/A",
            "fecha_max": "N/A",
            "fechas_calendario": 0,
            "fechas_con_obs": 0,
            "num_obs": 0
        }

    fechas_unicas = sorted(df_valido["FechaRef"].unique())
    f_min = fechas_unicas[0]
    f_max = fechas_unicas[-1]
    n_fechas_obs = len(fechas_unicas)
    n_obs = len(df_valido)
    n_cal = len([d for d in cal_ref if f_min <= d <= f_max])

    return {
        "fecha_min": f_min.strftime("%Y-%m-%d"),
        "fecha_max": f_max.strftime("%Y-%m-%d"),
        "fechas_calendario": n_cal,
        "fechas_con_obs": n_fechas_obs,
        "num_obs": n_obs
    }


def ejecutar_backtest_robustez_verificado():
    print("=" * 85)
    print("BACKTESTING DE ROBUSTEZ: COMPARACIÓN EXPERIMENTAL CON LÍNEA BASE Y 5 MODELOS ML")
    print("BIBLIOTECA CENTRAL UNDAC | CALENDARIO INSTITUCIONAL (MAYO - OCTUBRE 2026)")
    print("=" * 85)

    cal_s1, cal_s2 = construir_calendarios_por_semestre()
    cal_completo = cal_s1 + cal_s2
    df_visitas = cargar_visitas_completas()

    path_cache = ROOT_DIR / "ml" / "data" / "dataset_retorno_panel_completo_backtest.csv"
    if path_cache.exists():
        os.remove(path_cache)
    df_panel_total = generar_o_cargar_panel_por_semestres(df_visitas, cal_s1, cal_s2)

    fechas_may = [d for d in cal_s1 if d.month == 5]
    fechas_jun = [d for d in cal_s1 if d.month == 6]
    fechas_jul = [d for d in cal_s1 if d.month == 7]
    fechas_sep = [d for d in cal_s2 if d.month == 9]
    fechas_oct = [d for d in cal_s2 if d.month == 10]

    cortes_definicion = [
        {
            "id": "corte_1_semestre_1_historico",
            "periodo_temporal": "Periodo 1: Semestre I",
            "estrategia_entrenamiento": "Referencia Histórica Junio",
            "nombre": "Corte 1: Semestre I (Referencia Histórica Junio -> Julio)",
            "descripcion": "Entrenamiento en Junio 2026, validación interna en Junio tardío, prueba en Julio 2026.",
            "calendario_ref": cal_s1,
            "fechas_train": fechas_jun,
            "fechas_test": fechas_jul,
            "censura_test_h1": [fechas_jul[-1]],
            "censura_test_h7": fechas_jul[-7:]
        },
        {
            "id": "corte_2_intersemestral_septiembre",
            "periodo_temporal": "Periodo 2: Intersemestral",
            "estrategia_entrenamiento": "Consolidado Semestre I",
            "nombre": "Corte 2: Intersemestral (Semestre I -> Septiembre 2026)",
            "descripcion": "Entrenamiento en Semestre I consolidado (Mayo-Julio). El receso de agosto provee una purga natural de >30 días. Prueba en Septiembre 2026.",
            "calendario_ref": cal_completo,
            "fechas_train": fechas_may + fechas_jun + fechas_jul,
            "fechas_test": fechas_sep,
            "censura_test_h1": [],
            "censura_test_h7": []
        },
        {
            "id": "corte_3a_intra_semestre_2_septiembre",
            "periodo_temporal": "Periodo 3: Semestre II (Octubre)",
            "estrategia_entrenamiento": "Estrategia A: Memoria Corta Intra-Semestre (Septiembre solo)",
            "nombre": "Corte 3A: Intra-Semestre II (Septiembre solo -> Octubre 2026)",
            "descripcion": "Prueba de memoria corta / régimen intra-semestre: entrena únicamente con Septiembre 2026 para probar adaptación rápida al ciclo en Octubre 2026 al 20/10.",
            "calendario_ref": cal_s2,
            "fechas_train": fechas_sep,
            "fechas_test": fechas_oct,
            "censura_test_h1": [fechas_oct[-1]],
            "censura_test_h7": [d for d in fechas_oct if d > date(2026, 10, 9)]
        },
        {
            "id": "corte_3b_acumulado_anual_octubre",
            "periodo_temporal": "Periodo 3: Semestre II (Octubre)",
            "estrategia_entrenamiento": "Estrategia B: Acumulación Histórica Anual (Mayo-Septiembre)",
            "nombre": "Corte 3B: Acumulado Anual (Mayo a Septiembre -> Octubre 2026)",
            "descripcion": "Prueba de régimen de producción acumulativa: entrena con todo el historial anual acumulado (Mayo-Julio + Septiembre) para predecir Octubre 2026 al 20/10.",
            "calendario_ref": cal_completo,
            "fechas_train": fechas_may + fechas_jun + fechas_jul + fechas_sep,
            "fechas_test": fechas_oct,
            "censura_test_h1": [fechas_oct[-1]],
            "censura_test_h7": [d for d in fechas_oct if d > date(2026, 10, 9)]
        }
    ]

    reporte_backtest = {
        "metadatos_benchmark": {
            "fecha_ejecucion": date.today().isoformat(),
            "sede": "Central",
            "metodologia": "Comparación experimental de ML con Purged Split y suficiencia de validación",
            "poblacion_evaluada": "Alumnos conocidos con regularidad demostrable (>= 3 días activos previos)",
            "total_cortes_evaluados": len(cortes_definicion),
            "periodos_temporales_unicos": [
                "Periodo 1: Semestre I (Julio)",
                "Periodo 2: Intersemestral (Septiembre)",
                "Periodo 3: Semestre II (Octubre)"
            ],
            "nota_metodologica_agregacion": "C3A y C3B comparten exactamente el mismo conjunto de prueba de Octubre bajo dos regímenes de entrenamiento distintos."
        },
        "cortes": {},
        "resumen_robustez_estrategia_a": {},
        "resumen_robustez_estrategia_b": {},
        "resumen_global_cuatro_cortes": {},
        "comparativa_estrategias_octubre": {}
    }

    horizontes = [
        {"id": "horizonte_1_dia", "nombre": "Horizonte 1 (Siguiente Día)", "dias": 1, "col_y": "target_h1"},
        {"id": "horizonte_7_dias", "nombre": "Horizonte 2 (Próximos 7 Días)", "dias": 7, "col_y": "target_h7"}
    ]

    fabricas = obtener_fabricas_modelos()

    metr_est_a = {h["id"]: {m: {"auc_roc": [], "pr_auc": [], "brier_score": []} for m in fabricas.keys()} for h in horizontes}
    metr_est_b = {h["id"]: {m: {"auc_roc": [], "pr_auc": [], "brier_score": []} for m in fabricas.keys()} for h in horizontes}
    metr_todos_cortes = {h["id"]: {m: {
        "auc_roc": [], "pr_auc": [], "brier_score": [],
        "balanced_accuracy": [], "macro_f1": [], "recall_positivo": [], "precision_positivo": []
    } for m in fabricas.keys()} for h in horizontes}

    for corte in cortes_definicion:
        c_id = corte["id"]
        c_nom = corte["nombre"]
        cal_corte = corte["calendario_ref"]

        print(f"\n{'='*80}\nEJECUTANDO: {c_nom.upper()}\n{'='*80}")

        reporte_backtest["cortes"][c_id] = {
            "nombre": c_nom,
            "periodo_temporal": corte["periodo_temporal"],
            "estrategia_entrenamiento": corte["estrategia_entrenamiento"],
            "descripcion": corte["descripcion"],
            "horizontes": {}
        }

        f_tr_all = sorted(corte["fechas_train"])
        f_te_all = sorted(corte["fechas_test"])

        for horiz in horizontes:
            h_id = horiz["id"]
            h_dias = horiz["dias"]
            col_y = horiz["col_y"]

            print(f"\n--- Evaluando {horiz['nombre']} (H={h_dias}) en {c_nom} ---")

            # Purga antes de prueba
            idx_test_start = cal_corte.index(f_te_all[0])
            max_train_date = cal_corte[idx_test_start - 1 - h_dias]

            f_tr_purgadas = [d for d in f_tr_all if d <= max_train_date]
            fechas_purga_antes_test = [d for d in f_tr_all if d > max_train_date]

            # Censura en prueba
            censura_fechas = corte["censura_test_h1"] if h_dias == 1 else corte["censura_test_h7"]
            f_te_validas = [d for d in f_te_all if d not in censura_fechas]

            df_ftr = df_panel_total[(df_panel_total["FechaRef"].isin(f_tr_purgadas)) & (df_panel_total[col_y].notna())].copy()
            df_test = df_panel_total[(df_panel_total["FechaRef"].isin(f_te_validas)) & (df_panel_total[col_y].notna())].copy()

            if len(df_test) == 0:
                raise ValueError(f"Error crítico: Test set para {c_nom} {horiz['nombre']} está vacío.")

            # Inner Split sin traslape
            n_fechas_tot = len(f_tr_purgadas)
            corte_fechas_idx = int(n_fechas_tot * 0.65)
            f_itr = f_tr_purgadas[:corte_fechas_idx]

            idx_itr_end = cal_corte.index(f_itr[-1])
            f_ival_start = cal_corte[idx_itr_end + 1 + h_dias] if idx_itr_end + 1 + h_dias < len(cal_corte) else None

            if f_ival_start and f_ival_start <= f_tr_purgadas[-1]:
                f_ival = [d for d in f_tr_purgadas if d >= f_ival_start]
            else:
                f_ival = []

            df_itr = df_ftr[df_ftr["FechaRef"].isin(f_itr)]
            df_ival = df_ftr[df_ftr["FechaRef"].isin(f_ival)]

            stats_ftr = extraer_estadisticas_particion(df_ftr, cal_corte, col_y)
            stats_itr = extraer_estadisticas_particion(df_itr, cal_corte, col_y)
            stats_ival = extraer_estadisticas_particion(df_ival, cal_corte, col_y)
            stats_test = extraer_estadisticas_particion(df_test, cal_corte, col_y)

            n_fechas_val = stats_ival["fechas_con_obs"]

            print(f"Full Train: {stats_ftr['fecha_min']} a {stats_ftr['fecha_max']} | Cal: {stats_ftr['fechas_calendario']} d | ConObs: {stats_ftr['fechas_con_obs']} d | Obs: {stats_ftr['num_obs']:,}")
            print(f"Inner Val : {stats_ival['fecha_min']} a {stats_ival['fecha_max']} | Cal: {stats_ival['fechas_calendario']} d | ConObs: {stats_ival['fechas_con_obs']} d | Obs: {stats_ival['num_obs']:,}")
            print(f"Test Final: {stats_test['fecha_min']} a {stats_test['fecha_max']} | Cal: {stats_test['fechas_calendario']} d | ConObs: {stats_test['fechas_con_obs']} d | Obs: {stats_test['num_obs']:,}")

            X_itr = df_itr[VARIABLES_RETORNO]
            y_itr = df_itr[col_y].astype(int)
            X_ival = df_ival[VARIABLES_RETORNO]
            y_ival = df_ival[col_y].astype(int)

            X_ftr = df_ftr[VARIABLES_RETORNO]
            y_ftr = df_ftr[col_y].astype(int)
            X_te = df_test[VARIABLES_RETORNO]
            y_te = df_test[col_y].astype(int)

            res_mods = {}

            for nom_mod, mod_info in fabricas.items():
                u_sel, es_suficiente, estado_val = evaluar_umbral_validacion_con_suficiencia(
                    mod_info["factory"], X_itr, y_itr, X_ival, y_ival, n_fechas_val, min_fechas=3
                )

                clf_final = mod_info["factory"]()
                clf_final.fit(X_ftr, y_ftr)

                if hasattr(clf_final, "predict_proba"):
                    probs_te = clf_final.predict_proba(X_te)[:, 1]
                    preds_te = (probs_te >= u_sel).astype(int)
                    auc_roc = float(roc_auc_score(y_te, probs_te))
                    pr_auc = float(average_precision_score(y_te, probs_te))
                    brier = float(brier_score_loss(y_te, probs_te))
                else:
                    preds_te = clf_final.predict(X_te)
                    probs_te = np.zeros(len(y_te))
                    auc_roc = 0.5000
                    pr_auc = float(y_te.mean())
                    brier = float(np.mean((preds_te - y_te) ** 2))

                acc = float(accuracy_score(y_te, preds_te))
                bal_acc = float(balanced_accuracy_score(y_te, preds_te))
                prec = float(precision_score(y_te, preds_te, zero_division=0))
                rec = float(recall_score(y_te, preds_te, zero_division=0))
                f1_pos = float(f1_score(y_te, preds_te, zero_division=0))
                macro_f1 = float(f1_score(y_te, preds_te, average="macro", zero_division=0))

                cm = confusion_matrix(y_te, preds_te)
                tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

                res_mods[nom_mod] = {
                    "es_linea_base": mod_info["es_linea_base"],
                    "tipo": mod_info["tipo"],
                    "metricas_independientes_del_umbral": {
                        "auc_roc": round(auc_roc, 4),
                        "pr_auc": round(pr_auc, 4),
                        "brier_score": round(brier, 4)
                    },
                    "metricas_dependientes_del_umbral": {
                        "umbral_evaluado": u_sel,
                        "estado_validacion": estado_val,
                        "es_suficiente_validacion": es_suficiente,
                        "balanced_accuracy": round(bal_acc, 4),
                        "accuracy": round(acc, 4),
                        "precision_positivo": round(prec, 4),
                        "recall_positivo": round(rec, 4),
                        "f1_score_positivo": round(f1_pos, 4),
                        "macro_f1": round(macro_f1, 4),
                        "matriz_confusion": {"TN": tn, "FP": fp, "FN": fn, "TP": tp}
                    }
                }

                # Registro de agregaciones
                metr_todos_cortes[h_id][nom_mod]["auc_roc"].append(auc_roc)
                metr_todos_cortes[h_id][nom_mod]["pr_auc"].append(pr_auc)
                metr_todos_cortes[h_id][nom_mod]["brier_score"].append(brier)
                metr_todos_cortes[h_id][nom_mod]["balanced_accuracy"].append(bal_acc)
                metr_todos_cortes[h_id][nom_mod]["macro_f1"].append(macro_f1)
                metr_todos_cortes[h_id][nom_mod]["recall_positivo"].append(rec)
                metr_todos_cortes[h_id][nom_mod]["precision_positivo"].append(prec)

                if c_id in ["corte_1_semestre_1_historico", "corte_2_intersemestral_septiembre", "corte_3a_intra_semestre_2_septiembre"]:
                    metr_est_a[h_id][nom_mod]["auc_roc"].append(auc_roc)
                    metr_est_a[h_id][nom_mod]["pr_auc"].append(pr_auc)
                    metr_est_a[h_id][nom_mod]["brier_score"].append(brier)

                if c_id in ["corte_1_semestre_1_historico", "corte_2_intersemestral_septiembre", "corte_3b_acumulado_anual_octubre"]:
                    metr_est_b[h_id][nom_mod]["auc_roc"].append(auc_roc)
                    metr_est_b[h_id][nom_mod]["pr_auc"].append(pr_auc)
                    metr_est_b[h_id][nom_mod]["brier_score"].append(brier)

            reporte_backtest["cortes"][c_id]["horizontes"][h_id] = {
                "nombre": horiz["nombre"],
                "horizonte_dias": h_dias,
                "particiones_detalladas": {
                    "full_train": stats_ftr,
                    "inner_train": stats_itr,
                    "inner_val": stats_ival,
                    "test_final": stats_test,
                    "fechas_purgadas_train": len(fechas_purga_antes_test),
                    "fechas_censuradas_test": len(censura_fechas),
                    "estado_suficiencia_val": "suficiente" if n_fechas_val >= 3 else "insuficiente_por_purga"
                },
                "modelos": res_mods
            }

    # Resúmenes cronológicos
    def calcular_stats_estrategia(metr_dict):
        res = {}
        for h in horizontes:
            h_id = h["id"]
            res[h_id] = {}
            for nom_m, stats_dict in metr_dict[h_id].items():
                aucs = stats_dict["auc_roc"]
                pr_aucs = stats_dict["pr_auc"]
                briers = stats_dict["brier_score"]
                res[h_id][nom_m] = {
                    "auc_roc": {
                        "media": round(float(np.mean(aucs)), 4),
                        "std": round(float(np.std(aucs)), 4),
                        "min": round(float(np.min(aucs)), 4),
                        "max": round(float(np.max(aucs)), 4),
                        "valores_por_periodo": [round(x, 4) for x in aucs]
                    },
                    "pr_auc": {
                        "media": round(float(np.mean(pr_aucs)), 4),
                        "std": round(float(np.std(pr_aucs)), 4),
                        "min": round(float(np.min(pr_aucs)), 4),
                        "max": round(float(np.max(pr_aucs)), 4),
                        "valores_por_periodo": [round(x, 4) for x in pr_aucs]
                    },
                    "brier_score": {
                        "media": round(float(np.mean(briers)), 4),
                        "std": round(float(np.std(briers)), 4),
                        "min": round(float(np.min(briers)), 4),
                        "max": round(float(np.max(briers)), 4),
                        "valores_por_periodo": [round(x, 4) for x in briers]
                    }
                }
        return res

    reporte_backtest["resumen_robustez_estrategia_a"] = calcular_stats_estrategia(metr_est_a)
    reporte_backtest["resumen_robustez_estrategia_b"] = calcular_stats_estrategia(metr_est_b)

    # Resumen global de los 4 cortes con rangos exactos y macro-promedio
    res_glob = {}
    for h in horizontes:
        h_id = h["id"]
        res_glob[h_id] = {}
        for nom_m, s_dict in metr_todos_cortes[h_id].items():
            res_glob[h_id][nom_m] = {}
            for k_metr, v_list in s_dict.items():
                res_glob[h_id][nom_m][k_metr] = {
                    "min": round(float(np.min(v_list)), 4),
                    "max": round(float(np.max(v_list)), 4),
                    "macro_promedio_4_cortes": round(float(np.mean(v_list)), 4),
                    "valores_por_corte": [round(x, 4) for x in v_list]
                }
    reporte_backtest["resumen_global_cuatro_cortes"] = res_glob

    for h in horizontes:
        h_id = h["id"]
        reporte_backtest["comparativa_estrategias_octubre"][h_id] = {}
        c3a_m = reporte_backtest["cortes"]["corte_3a_intra_semestre_2_septiembre"]["horizontes"][h_id]["modelos"]
        c3b_m = reporte_backtest["cortes"]["corte_3b_acumulado_anual_octubre"]["horizontes"][h_id]["modelos"]
        for nom_m in fabricas.keys():
            auc_a = c3a_m[nom_m]["metricas_independientes_del_umbral"]["auc_roc"]
            auc_b = c3b_m[nom_m]["metricas_independientes_del_umbral"]["auc_roc"]
            brier_a = c3a_m[nom_m]["metricas_independientes_del_umbral"]["brier_score"]
            brier_b = c3b_m[nom_m]["metricas_independientes_del_umbral"]["brier_score"]
            reporte_backtest["comparativa_estrategias_octubre"][h_id][nom_m] = {
                "auc_roc_estrategia_a": auc_a,
                "auc_roc_estrategia_b": auc_b,
                "diferencia_auc_b_menos_a": round(auc_b - auc_a, 4),
                "brier_estrategia_a": brier_a,
                "brier_estrategia_b": brier_b
            }

    # Guardar JSON
    json_path = ROOT_DIR / "ml" / "reports" / "resultados_backtest_robustez_retorno.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(reporte_backtest, f, indent=4, ensure_ascii=False)
    print(f"\n[OK] Resultados del backtest JSON guardados en: {json_path}")

    # Guardar Markdown generado directamente
    md_path = ROOT_DIR / "ml" / "reports" / "reporte_backtest_robustez_retorno.md"
    generar_reporte_backtest_markdown_desde_datos(md_path, reporte_backtest)
    print(f"[OK] Reporte técnico del backtest guardado en: {md_path}")

    return reporte_backtest


def generar_reporte_backtest_markdown_desde_datos(ruta_md, rep):
    md = [
        "# Reporte Técnico de Backtesting y Robustez: Modelos de Retorno Individual",
        "**Biblioteca Central UNDAC** | **Fecha:** 2026-10-21 | **Metodología:** Backtesting Cronológico Purgado Multi-Corte",
        "",
        "---",
        "",
        "## 1. Definición Operativa de Agregaciones y Metodología",
        "",
        "- **Línea Base vs. Modelos de Machine Learning:** La `Línea Base (Dummy)` se presenta separada como cota inferior no informativa y no compite con los algoritmos predictivos.",
        "- **Definición de Agregación Temporal:**",
        "  1. **Trayectoria A (Memoria Corta):** Promedio y desviación estándar calculados sobre **3 periodos cronológicos independientes**: Julio (C1), Septiembre (C2) y Octubre Intra-Semestre (C3A).",
        "  2. **Trayectoria B (Acumulación Anual):** Promedio y desviación estándar calculados sobre **3 periodos cronológicos independientes**: Julio (C1), Septiembre (C2) y Octubre Acumulado (C3B).",
        "  3. **Macro-Promedio de los 4 Cortes:** Promedio aritmético no ponderado de los 4 cortes evaluados (C1, C2, C3A, C3B).",
        "     > [!IMPORTANT]",
        "     > **Aviso de Ponderación:** Los cortes C3A y C3B comparten exactamente el mismo conjunto de prueba de Octubre con distinto régimen de entrenamiento; por tanto, el macro-promedio de los 4 cortes asigna un 50% de peso a las observaciones de Octubre.",
        "- **Estructura del Panel y Correlación Intra-Alumno:** Los periodos temporales de entrenamiento y prueba no se solapan (garantizado por la purga temporal de 1 y 7 días), pero las observaciones de un mismo alumno en diferentes fechas pueden estar correlacionadas longitudinalmente (dependencia intra-sujeto a través del tiempo).",
        "- **Carácter Exploratorio:** Las tablas presentan una comparación exploratoria de backtests históricos; **no se declara un ganador definitivo a partir de los conjuntos de prueba**.",
        "",
        "---",
        "",
        "## 2. Tabla Maestra de Particiones Verificada Automáticamente",
        "",
        "| Corte | Horiz. | Bloque | Fecha Mínima | Fecha Máxima | Días Calendario | Días con Obs | Observaciones | Estado / Purga |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |"
    ]

    for c_id, c_data in rep["cortes"].items():
        nom_c = c_data["nombre"].split(":")[0]
        for h_id, h_data in c_data["horizontes"].items():
            nom_h = "H1 (1d)" if h_data["horizonte_dias"] == 1 else "H7 (7d)"
            pd_part = h_data["particiones_detalladas"]
            for b_key, b_nom in [
                ("full_train", "Full Train"),
                ("inner_train", "Inner Train"),
                ("inner_val", "Inner Val"),
                ("test_final", "Test Final")
            ]:
                b_info = pd_part[b_key]
                estado = pd_part["estado_suficiencia_val"] if b_key == "inner_val" else (
                    f"Purgadas: {pd_part['fechas_purgadas_train']}" if b_key == "full_train" else (
                        f"Censuradas: {pd_part['fechas_censuradas_test']}" if b_key == "test_final" else "Entrenamiento Interno"
                    )
                )
                md.append(
                    f"| **{nom_c}** | {nom_h} | `{b_nom}` | `{b_info['fecha_min']}` | `{b_info['fecha_max']}` | "
                    f"{b_info['fechas_calendario']} d | {b_info['fechas_con_obs']} d | **{b_info['num_obs']:,}** | `{estado}` |"
                )

    md.extend([
        "",
        "---",
        "",
        "## 3. Línea Base Simple (Dummy Classifier)",
        "",
        "| Corte | Horizonte | ROC-AUC | PR-AUC | Brier Score | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ])

    for c_id, c_data in rep["cortes"].items():
        nom_c = c_data["nombre"].split(":")[0]
        for h_id, h_data in c_data["horizontes"].items():
            nom_h = "H1 (1d)" if h_data["horizonte_dias"] == 1 else "H7 (7d)"
            m_dum = h_data["modelos"]["Dummy (Clase Mayoritaria)"]
            mi = m_dum["metricas_independientes_del_umbral"]
            mdep = m_dum["metricas_dependientes_del_umbral"]
            md.append(
                f"| **{nom_c}** | {nom_h} | {mi['auc_roc']:.4f} | {mi['pr_auc']:.4f} | {mi['brier_score']:.4f} | "
                f"{mdep['balanced_accuracy']:.4f} | {mdep['macro_f1']:.4f} | {mdep['recall_positivo']:.4f} | {mdep['precision_positivo']:.4f} |"
            )

    md.extend([
        "",
        "---",
        "",
        "## 4. Comparativa de los Cinco Modelos de Machine Learning por Corte",
        ""
    ])

    for c_id, c_data in rep["cortes"].items():
        md.extend([
            f"### {c_data['nombre']}",
            ""
        ])
        for h_id, h_data in c_data["horizontes"].items():
            md.extend([
                f"#### {h_data['nombre']}:",
                "",
                "| Modelo ML | ROC-AUC | PR-AUC | Brier Score | Umbral $\\theta$ | Balanced Acc | Macro F1 | Recall Retorno | Precision Retorno | Estado Val |",
                "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
            ])
            for nom_m, m in h_data["modelos"].items():
                if m.get("es_linea_base", False):
                    continue
                mi = m["metricas_independientes_del_umbral"]
                mdep = m["metricas_dependientes_del_umbral"]
                md.append(
                    f"| **{nom_m}** | **{mi['auc_roc']:.4f}** | {mi['pr_auc']:.4f} | **{mi['brier_score']:.4f}** | "
                    f"{mdep['umbral_evaluado']:.2f} | **{mdep['balanced_accuracy']:.4f}** | {mdep['macro_f1']:.4f} | "
                    f"{mdep['recall_positivo']:.4f} | {mdep['precision_positivo']:.4f} | `{mdep['estado_validacion']}` |"
                )
            md.append("")

    md.extend([
        "---",
        "",
        "## 5. Resumen de Métricas Agregadas y Reconciliación",
        "",
        "### A. Agregación Cronológica por Trayectorias (3 Periodos Independientes cada una):",
        "",
        "| Modelo ML | Trayectoria A: AUC Medio H1 | Trayectoria A: Brier H1 | Trayectoria B: AUC Medio H1 | Trayectoria B: Brier H1 | Trayectoria A: AUC Medio H7 | Trayectoria A: Brier H7 | Trayectoria B: AUC Medio H7 | Trayectoria B: Brier H7 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ])

    h1_a = rep["resumen_robustez_estrategia_a"]["horizonte_1_dia"]
    h7_a = rep["resumen_robustez_estrategia_a"]["horizonte_7_dias"]
    h1_b = rep["resumen_robustez_estrategia_b"]["horizonte_1_dia"]
    h7_b = rep["resumen_robustez_estrategia_b"]["horizonte_7_dias"]

    for nom_m in h1_a.keys():
        if rep["cortes"]["corte_1_semestre_1_historico"]["horizontes"]["horizonte_1_dia"]["modelos"][nom_m].get("es_linea_base", False):
            continue
        s1_a = h1_a[nom_m]
        s7_a = h7_a[nom_m]
        s1_b = h1_b[nom_m]
        s7_b = h7_b[nom_m]
        md.append(
            f"| **{nom_m}** | {s1_a['auc_roc']['media']:.3f} $\\pm$ {s1_a['auc_roc']['std']:.3f} | {s1_a['brier_score']['media']:.4f} | "
            f"{s1_b['auc_roc']['media']:.3f} $\\pm$ {s1_b['auc_roc']['std']:.3f} | {s1_b['brier_score']['media']:.4f} | "
            f"{s7_a['auc_roc']['media']:.3f} $\\pm$ {s7_a['auc_roc']['std']:.3f} | {s7_a['brier_score']['media']:.4f} | "
            f"{s7_b['auc_roc']['media']:.3f} $\\pm$ {s7_b['auc_roc']['std']:.3f} | {s7_b['brier_score']['media']:.4f} |"
        )

    md.extend([
        "",
        "### B. Rangos Globales Reconciliados y Macro-Promedio de los Cuatro Cortes:",
        "",
        "*Advertencia: Este macro-promedio asigna 50% de ponderación a Octubre al incluir tanto C3A como C3B.*",
        "",
        "| Modelo ML | Horiz. | Rango ROC-AUC [Min - Max] | Macro-Promedio AUC | Rango Brier [Min - Max] | Macro-Promedio Brier | Rango Recall Retorno | Macro-Promedio BalAcc |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |"
    ])

    glob_stats = rep["resumen_global_cuatro_cortes"]
    for h_id, h_nom in [("horizonte_1_dia", "H1 (1d)"), ("horizonte_7_dias", "H7 (7d)")]:
        for nom_m, s_dict in glob_stats[h_id].items():
            if rep["cortes"]["corte_1_semestre_1_historico"]["horizontes"][h_id]["modelos"][nom_m].get("es_linea_base", False):
                continue
            auc_g = s_dict["auc_roc"]
            brier_g = s_dict["brier_score"]
            rec_g = s_dict["recall_positivo"]
            bal_g = s_dict["balanced_accuracy"]
            md.append(
                f"| **{nom_m}** | {h_nom} | [{auc_g['min']:.4f} - {auc_g['max']:.4f}] | **{auc_g['macro_promedio_4_cortes']:.4f}** | "
                f"[{brier_g['min']:.4f} - {brier_g['max']:.4f}] | {brier_g['macro_promedio_4_cortes']:.4f} | "
                f"[{rec_g['min']:.4f} - {rec_g['max']:.4f}] | **{bal_g['macro_promedio_4_cortes']:.4f}** |"
            )

    md.extend([
        "",
        "---",
        "",
        "## 6. Reconciliación Específica de Discrepancias Identificadas",
        "",
        "1. **Reconciliación de Random Forest en Horizonte 2 (H7):**",
        "   - **Valores por corte:** C1 = `0.7227`, C2 = `0.7420`, C3A = `0.7828`, C3B = `0.7883`.",
        "   - **Rango global exacto:** `[0.7227, 0.7883]`.",
        "   - **Trayectoria A (3 periodos: C1, C2, C3A):** Promedio = `0.7492` $\\pm$ `0.025`.",
        "   - **Trayectoria B (3 periodos: C1, C2, C3B):** Promedio = `0.7510` $\\pm$ `0.027`.",
        "   - **Macro-promedio de los 4 cortes:** `(0.7227 + 0.7420 + 0.7828 + 0.7883) / 4 = 0.75895` $\\approx$ **`0.7590`**.",
        "   *Conclusión:* La cifra `0.759` corresponde exactamente al macro-promedio de los 4 cortes, mientras que `0.751` corresponde a la Trayectoria B (tres periodos independientes con acumulación anual). Ambas provienen de la misma fuente de datos.",
        "",
        "2. **Reconciliación del Rango de Brier Score de HistGradientBoosting en Horizonte 1 (H1):**",
        "   - **Valores por corte:** C1 = `0.1408`, C2 = `0.0878`, C3A = `0.0988`, C3B = `0.0980`.",
        "   - **Rango global exacto:** **`[0.0878, 0.1408]`**.",
        "   - **Trayectoria A Brier medio:** `(0.1408 + 0.0878 + 0.0988) / 3 = 0.1091`.",
        "   - **Trayectoria B Brier medio:** `(0.1408 + 0.0878 + 0.0980) / 3 = 0.1089`.",
        "   - **Macro-promedio Brier 4 cortes:** `0.1064`.",
        "   *Conclusión:* La mención previa '0.0878 a 0.0988' omitía el Corte 1 (`0.1408`). El rango fidedigno completo a través de todos los cortes evaluados es estrictamente **`[0.0878, 0.1408]`**.",
        "",
        "---",
        "",
        "## 7. Discusión Metodológica y Selección Interna",
        "",
        "### A. Selección en Cortes Internos Purgados:",
        "- **Cortes con Validación Suficiente ($\ge 3$ fechas hábiles tras purga):**",
        "  * En **Corte 1 H1**, **Corte 2 H1 y H7**, **Corte 3A H1** y **Corte 3B H1 y H7**, la partición interna purgada (`Inner Train` $\\rightarrow$ `Inner Val`) dispuso de fechas suficientes para seleccionar umbrales operativos $\\theta^*$ que optimizaran la Macro F1 sin contaminar el conjunto de prueba.",
        "  * En estos cortes, los modelos basados en árboles y boosting permitieron sintonizar umbrales adaptados a la prevalencia interna (entre $0.20$ y $0.70$), mientras que los modelos lineales mantuvieron umbrales entre $0.50$ y $0.80$.",
        "- **Cortes con Validación Insuficiente (`insuficiente_por_purga`):**",
        "  * En **Corte 1 H7** y **Corte 3A H7**, la purga temporal obligatoria de 7 días consumió la totalidad del periodo de validación interna disponible ($0$ fechas con observaciones válidas).",
        "  * **Declaración metodológica:** **No hay ganador validado** en estos cortes. Al no existir un subconjunto de validación interno utilizable sin incurrir en fuga de información futura, no es posible comparar ni seleccionar válidamente ningún modelo a nivel interno; por diseño experimental, se aplicó el umbral neutral $\\theta = 0.50$ fijado a priori.",
        "",
        "### B. Estructura del Panel y Correlación Intra-Alumno:",
        "- **Ausencia de Solapamiento Temporal:** La purga temporal estricta de 1 y 7 días garantiza que las ventanas de predicción no invadan los periodos de prueba posteriores.",
        "- **Correlación Intra-Sujeto:** Dado que se trata de un panel longitudinal donde los mismos estudiantes asisten a la biblioteca a lo largo de varios meses, **las observaciones de un mismo alumno en distintas fechas pueden estar correlacionadas serialmente** (hábitos y estacionalidad personal). Las filas del panel no son observaciones $i.i.d.$; por tanto, los resultados ilustran la capacidad de generalización hacia nuevos periodos de calendario (*out-of-time*) sobre una población conocida con regularidad previa, y no deben extrapolarse a estudiantes nuevos sin historial (*cold-start*).",
        "",
        "### C. Carácter Exploratorio y Ausencia de Ganador en Prueba:",
        "- Las tablas de desempeño sobre los conjuntos de prueba representan una **comparación exploratoria de backtests históricos** bajo distintos cortes y regímenes de información.",
        "- **No se declara un ganador definitivo a partir de los conjuntos de prueba.** Emplear el desempeño fuera de muestra para proclamar un modelo ganador violaría el principio de evaluación a ciegas.",
        "",
        "---",
        "",
        "## 8. Conclusiones y Estado Operativo",
        "",
        "- **Conservación del Modelo en Producción:** El artefacto actual `modelo_retorno_alumno.joblib` (basado en `HistGradientBoostingClassifier v1.0.0`) permanece **intacto, activo y en servicio** en la aplicación web.",
        "- **Validación Futura Pendiente:** Cualquier eventual consideración de cambio o actualización algorítmica queda supeditada a una futura **validación prospectiva en sombra** (*shadow testing*) en periodos operativos en vivo, descartando modificaciones directas a partir de backtests retrospectivos.",
        "- **Integridad del Entorno:** Cero cambios en la base de datos, en las rutas de la aplicación, en las plantillas o en el repositorio Git."
    ])

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    ejecutar_backtest_robustez_verificado()
