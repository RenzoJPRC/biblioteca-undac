"""
Experimento reproducible de comparación de cinco modelos de Machine Learning para
la Predicción de Retorno Individual de Alumnos en la Biblioteca Central UNDAC.

Garantías metodológicas, de calendario y evaluación con Purged Temporal Split:
1. Calendario con fechas reales de atención:
   - Distingue cierres efectivos (fines de semana y feriados oficiales: 29/06, 23/07, 24/07,
     27/07 a 29/07, 06/08, 30/08, 08/10) de días con atención reducida en agosto (03/08 a 21/08).
   - No marca agosto completo como cerrado: la biblioteca atendió en jornada diurna y registró visitas.
2. Tratamiento riguroso de referencias del 17 al 31 de julio:
   - Para H1 (1 día): Todas tienen siguiente día operativo observable (31/07 -> 03/08). Ninguna censurada.
   - Para H7 (7 días): Al haber atención en agosto, las referencias del 17 al 31 de julio completan sus
     7 días de atención en agosto (17/07 completa al 04/08; 31/07 completa al 17/08). Se computan sin
     censura artificial, midiendo la tasa real de retorno en la transición al receso.
3. Censura por ventana abierta al corte del 21/10/2026:
   - Horizonte 1: Máximo referencias hasta el 19/10/2026 (su día siguiente fue el 20/10/2026, día cerrado).
     El 20/10 tiene como siguiente día el 21/10 (en curso/parcial), por lo que se censura (NaN).
   - Horizonte 2: Máximo referencias hasta el 09/10/2026 (sus 7 días concluyeron el 20/10/2026).
     Referencias desde el 12/10/2026 en adelante no han completado su ventana y se censuran (NaN).
4. Cinco modelos candidatos de ML + Línea Base:
   - Línea base: DummyClassifier (most_frequent), fuera del conteo de cinco modelos.
   - 5 Candidatos: Regresión Logística, Random Forest, Extra Trees, Gradient Boosting e HistGradientBoosting.
5. Evaluación cronológica con purga:
   - Train (Semestre I: Jun-Jul) -> Val (Septiembre purgado) -> Test (Octubre out-of-time).
   - Selección de umbral y modelo ganador EXCLUSIVAMENTE en Validación interna purgada.
   - Prohibición estricta de elegir al ganador mirando la prueba final.
6. Conservación e integridad:
   - Cero escrituras en BD, modelo_retorno_alumno.joblib de producción permanece 100% intacto.
   - Candidato separado guardado en ml/models/modelo_retorno_alumno_candidato_hgb.joblib.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import joblib
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

from ml.services.analisis_individual import FERIADOS_CONFIRMADOS_2026

# Variables operativas del modelo
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

# Cierres efectivos documentados (cero atención física): fines de semana y feriados oficiales
CIERRES_EFECTIVOS = FERIADOS_CONFIRMADOS_2026 | {
    date(2026, 7, 24),  # Puente institucional
    date(2026, 7, 27),  # Cierre institucional previo Fiestas Patrias
    date(2026, 8, 6),   # Batalla de Junín
    date(2026, 8, 30),  # Santa Rosa de Lima
    date(2026, 10, 8),  # Combate de Angamos
}


def construir_calendario_real_operativo(fecha_inicio=date(2026, 6, 1), fecha_fin=date(2026, 10, 20)):
    """
    Construye la secuencia cronológica de días operativos con atención real de biblioteca.
    Distingue:
    - Cierres efectivos: Fines de semana y feriados oficiales donde la biblioteca no abrió (0 ingresos).
    - Días con atención reducida en agosto (03/08 al 21/08): La biblioteca atendió con afluencia baja/administrativa.
    - Días con atención académica regular: Junio, Julio, 24/08 al 31/08, Septiembre y Octubre.
    """
    calendario = []
    actual = fecha_inicio
    while actual <= fecha_fin:
        if actual.weekday() < 5 and actual not in CIERRES_EFECTIVOS:
            calendario.append(actual)
        actual += timedelta(days=1)
    return calendario


def preparar_datos_panel_y_visitas():
    """
    Carga el panel longitudinal de alumnos y las visitas diarias, resolviendo las etiquetas
    H1 y H7 según el calendario real de atención y aplicando la censura estricta a la derecha.
    """
    path_panel = ROOT_DIR / "ml" / "data" / "dataset_retorno_panel_completo_backtest.csv"
    path_visitas = ROOT_DIR / "ml" / "data" / "dataset_retorno_visitas_diarias_completo.csv"

    if not path_panel.exists() or not path_visitas.exists():
        raise FileNotFoundError("Los datasets preparados completos no existen en ml/data/.")

    df_panel = pd.read_csv(path_panel)
    df_panel["FechaRef"] = pd.to_datetime(df_panel["FechaRef"]).dt.date

    df_visitas = pd.read_csv(path_visitas)
    df_visitas["Fecha"] = pd.to_datetime(df_visitas["Fecha"]).dt.date
    visitas_set = set(zip(df_visitas["AlumnoID"], df_visitas["Fecha"]))

    calendario = construir_calendario_real_operativo()
    cal_set = set(calendario)

    # Recomputar y validar etiquetas H1 y H7 conforme al calendario real de atención
    h1_labels = []
    h7_labels = []

    limite_h1 = date(2026, 10, 19)  # 20/10 tiene como sig. día el 21/10 (en curso), censurado
    limite_h7 = date(2026, 10, 9)   # Del 12/10 en adelante la ventana de 7 días no ha concluido

    for _, row in df_panel.iterrows():
        al_id = row["AlumnoID"]
        f_ref = row["FechaRef"]

        if f_ref not in cal_set:
            h1_labels.append(np.nan)
            h7_labels.append(np.nan)
            continue

        idx = calendario.index(f_ref)

        # H1: Retorno en el siguiente día hábil operativo
        if f_ref <= limite_h1 and idx + 1 < len(calendario):
            f_next = calendario[idx + 1]
            y_h1 = 1 if (al_id, f_next) in visitas_set else 0
        else:
            y_h1 = np.nan
        h1_labels.append(y_h1)

        # H7: Retorno dentro de los próximos 7 días operativos
        if f_ref <= limite_h7 and idx + 7 < len(calendario):
            w7 = calendario[idx + 1 : idx + 8]
            y_h7 = 1 if any((al_id, d) in visitas_set for d in w7) else 0
        else:
            y_h7 = np.nan
        h7_labels.append(y_h7)

    df_panel["target_h1"] = h1_labels
    df_panel["target_h7"] = h7_labels

    return df_panel, calendario


def obtener_particiones_purgadas(df_panel, calendario, horizonte_dias=1):
    """
    Aplica rigurosamente la separación temporal con purga (Purged Temporal Split).
    - Train: Semestre I histórico (01/06/2026 al 31/07/2026).
    - Validation: Septiembre 2026 (01/09/2026 al 30/09/2026), purgado al final antes de Octubre.
    - Test: Octubre 2026 (out-of-time hasta la fecha límite de censura).
    """
    cal_train = [d for d in calendario if date(2026, 6, 1) <= d <= date(2026, 7, 31)]
    cal_val = [d for d in calendario if date(2026, 9, 1) <= d <= date(2026, 9, 30)]

    col_target = "target_h1" if horizonte_dias == 1 else "target_h7"

    if horizonte_dias == 1:
        # H1: 1 día operativo hacia adelante
        # 1. Train: Junio-Julio (31/07 tiene su día siguiente el 03/08; Val inicia el 01/09, >20 días de separación)
        df_train = df_panel[
            (df_panel["FechaRef"] >= date(2026, 6, 1)) &
            (df_panel["FechaRef"] <= date(2026, 7, 31)) &
            df_panel[col_target].notna()
        ].copy()

        # 2. Validation: Septiembre purgado. 30/09 se purga porque su siguiente día es 01/10 (inicio de Test)
        df_val = df_panel[
            (df_panel["FechaRef"] >= date(2026, 9, 1)) &
            (df_panel["FechaRef"] <= date(2026, 9, 29)) &
            df_panel[col_target].notna()
        ].copy()
        fechas_purgadas_val = [date(2026, 9, 30)]

        # 3. Test Final: Octubre out-of-time hasta 19/10 (20/10 censurado por tener sig. día el 21/10 en curso)
        df_test = df_panel[
            (df_panel["FechaRef"] >= date(2026, 10, 1)) &
            (df_panel["FechaRef"] <= date(2026, 10, 19)) &
            df_panel[col_target].notna()
        ].copy()
        fechas_censuradas_test = [date(2026, 10, 20)]

    elif horizonte_dias == 7:
        # H7: 7 días operativos hacia adelante
        # 1. Train: Junio-Julio (31/07 completa al 17/08; Val inicia el 01/09, >10 días de separación natural)
        df_train = df_panel[
            (df_panel["FechaRef"] >= date(2026, 6, 1)) &
            (df_panel["FechaRef"] <= date(2026, 7, 31)) &
            df_panel[col_target].notna()
        ].copy()

        # 2. Validation: Septiembre purgado. Los últimos 7 días operativos (22/09 al 30/09) se purgan
        #    porque sus ventanas de 7 días tocan fechas de Octubre (Test).
        cal_val_purga = [d for d in cal_val if d >= date(2026, 9, 22)]
        df_val = df_panel[
            (df_panel["FechaRef"] >= date(2026, 9, 1)) &
            (df_panel["FechaRef"] <= date(2026, 9, 21)) &
            df_panel[col_target].notna()
        ].copy()
        fechas_purgadas_val = cal_val_purga

        # 3. Test Final: Octubre out-of-time hasta 09/10 (a partir del 12/10 censurado por ventana abierta)
        df_test = df_panel[
            (df_panel["FechaRef"] >= date(2026, 10, 1)) &
            (df_panel["FechaRef"] <= date(2026, 10, 9)) &
            df_panel[col_target].notna()
        ].copy()
        cal_test_censura = [d for d in calendario if d >= date(2026, 10, 12)]
        fechas_censuradas_test = cal_test_censura

    else:
        raise ValueError(f"Horizonte no soportado: {horizonte_dias}")

    return {
        "train": df_train,
        "val": df_val,
        "test": df_test,
        "col_target": col_target,
        "fechas_purgadas_val": fechas_purgadas_val,
        "fechas_censuradas_test": fechas_censuradas_test,
    }


def seleccionar_umbral_optimo_validacion(clf_factory, X_tr, y_tr, X_val, y_val):
    """
    Selecciona el umbral óptimo de clasificación maximizando Macro F1 en la validación interna purgada.
    NUNCA observa ni evalúa el conjunto final de prueba.
    """
    clf = clf_factory()
    if not hasattr(clf, "predict_proba"):
        return 0.50

    clf.fit(X_tr, y_tr)
    probs_val = clf.predict_proba(X_val)[:, 1]

    mejor_u = 0.50
    mejor_mf1 = -1.0

    for u in np.arange(0.15, 0.85, 0.05):
        pred_val = (probs_val >= u).astype(int)
        mf1 = f1_score(y_val, pred_val, average="macro", zero_division=0)
        if mf1 > mejor_mf1:
            mejor_mf1 = mf1
            mejor_u = round(float(u), 2)

    return mejor_u


def obtener_fabricas_modelos():
    """
    Retorna la Línea Base simple y los cinco modelos candidatos de Machine Learning.
    DummyClassifier se mantiene formalmente como línea base fuera del conteo de los cinco.
    """
    return {
        "Dummy (Clase Mayoritaria)": {
            "factory": lambda: DummyClassifier(strategy="most_frequent"),
            "tipo": "Línea base ingenua (fuera de los 5 candidatos)",
            "es_linea_base": True,
        },
        "Regresión Logística": {
            "factory": lambda: Pipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42))
            ]),
            "tipo": "Modelo lineal regularizado balanceado",
            "es_linea_base": False,
        },
        "Random Forest": {
            "factory": lambda: RandomForestClassifier(
                n_estimators=100,
                max_depth=8,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1
            ),
            "tipo": "Ensamble bagging no lineal balanceado",
            "es_linea_base": False,
        },
        "Extra Trees": {
            "factory": lambda: ExtraTreesClassifier(
                n_estimators=100,
                max_depth=8,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1
            ),
            "tipo": "Ensamble de árboles extremadamente aleatorizados",
            "es_linea_base": False,
        },
        "Gradient Boosting": {
            "factory": lambda: GradientBoostingClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.08,
                random_state=42
            ),
            "tipo": "Boosting secuencial tradicional",
            "es_linea_base": False,
        },
        "HistGradientBoosting": {
            "factory": lambda: HistGradientBoostingClassifier(
                learning_rate=0.08,
                random_state=42
            ),
            "tipo": "Boosting por histogramas y cuantiles",
            "es_linea_base": False,
        },
    }


def evaluar_modelo_en_conjunto(clf, X, y, umbral=0.50):
    """Calcula las métricas de clasificación dependientes e independientes del umbral."""
    if hasattr(clf, "predict_proba"):
        probs = clf.predict_proba(X)[:, 1]
        preds = (probs >= umbral).astype(int)
        auc_roc = float(roc_auc_score(y, probs))
        pr_auc = float(average_precision_score(y, probs))
        brier = float(brier_score_loss(y, probs))
    else:
        preds = clf.predict(X)
        probs = np.zeros(len(y))
        auc_roc = 0.5000
        pr_auc = float(y.mean())
        brier = float(np.mean((preds - y) ** 2))

    acc = float(accuracy_score(y, preds))
    bal_acc = float(balanced_accuracy_score(y, preds))
    prec = float(precision_score(y, preds, zero_division=0))
    rec = float(recall_score(y, preds, zero_division=0))
    f1_pos = float(f1_score(y, preds, zero_division=0))
    macro_f1 = float(f1_score(y, preds, average="macro", zero_division=0))

    cm = confusion_matrix(y, preds)
    tn, fp, fn, tp = int(cm[0, 0]), int(cm[0, 1]), int(cm[1, 0]), int(cm[1, 1])

    return {
        "metricas_independientes_del_umbral": {
            "auc_roc": round(auc_roc, 4),
            "pr_auc": round(pr_auc, 4),
            "brier_score": round(brier, 4),
        },
        "metricas_dependientes_del_umbral": {
            "umbral": round(float(umbral), 2),
            "balanced_accuracy": round(bal_acc, 4),
            "accuracy": round(acc, 4),
            "precision_positivo": round(prec, 4),
            "recall_positivo": round(rec, 4),
            "f1_score_positivo": round(f1_pos, 4),
            "macro_f1": round(macro_f1, 4),
            "matriz_confusion": {
                "verdaderos_negativos_TN": tn,
                "falsos_positivos_FP": fp,
                "falsos_negativos_FN": fn,
                "verdaderos_positivos_TP": tp,
            },
        },
    }


def ejecutar_experimento_cinco_modelos():
    """
    Ejecuta el protocolo experimental completo:
    1. Prepara datos y etiquetas bajo el calendario real de atención.
    2. Aplica Purged Temporal Split (Train -> Val -> Test).
    3. Ajusta umbrales y evalúa modelos candidatos en Validación.
    4. Evalúa en Prueba Final out-of-time a ciegas una sola vez.
    5. Guarda resultados, reporte y el artefacto candidato separado.
    """
    print("=" * 90)
    print("EXPERIMENTO DE COMPARACIÓN DE CINCO MODELOS DE ML (RETORNO INDIVIDUAL UNDAC)")
    print("CALENDARIO DE ATENCIÓN REAL | VALIDACIÓN PURGADA | PRUEBA OUT-OF-TIME CIEGA")
    print("=" * 90)

    df_panel, calendario = preparar_datos_panel_y_visitas()

    horizontes_cfg = [
        {"id": "horizonte_1_dia", "nombre": "Horizonte 1 (Siguiente Día Hábil)", "dias": 1},
        {"id": "horizonte_7_dias", "nombre": "Horizonte 2 (Próximos 7 Días Operativos)", "dias": 7},
    ]

    reporte_dict = {
        "metadatos_benchmark": {
            "fecha_ejecucion": date.today().isoformat(),
            "sede": "Central",
            "metodologia": "Purged Temporal Split con validación purgada interna y prueba ciega",
            "poblacion_evaluada": "Alumnos conocidos con regularidad demostrable (>= 3 días activos previos)",
            "distincion_agosto": [
                "Cierres efectivos: Sábados, domingos y feriados confirmados (29/06, 23-29/07, 06/08, 30/08, 08/10).",
                "Atención reducida en agosto: 03/08 al 21/08 (jornada diurna con afluencia intersemestral).",
                "Atención regular: Junio, Julio, 24-31/08, Septiembre y Octubre (94 fechas operativas totales)."
            ],
            "tratamiento_julio_17_31": [
                "Para H1: Resuelto sin censura (todas tienen siguiente día observable, 31/07 -> 03/08).",
                "Para H7: Resuelto en agosto (17/07 completa al 04/08; 31/07 completa al 17/08)."
            ],
            "censuras_corte_octubre": [
                "H1 censurado: Referencias >= 20/10/2026 (siguiente día 21/10 en curso).",
                "H7 censurado: Referencias >= 12/10/2026 (ventana de 7 días no concluida al corte)."
            ]
        },
        "horizontes": {},
    }

    mejor_candidato_val = None
    mejor_mf1_val_global = -1.0

    for cfg in horizontes_cfg:
        h_id = cfg["id"]
        h_nom = cfg["nombre"]
        h_dias = cfg["dias"]

        print(f"\n{'-'*80}\nPROCESANDO: {h_nom.upper()} (H={h_dias})\n{'-'*80}")

        parts = obtener_particiones_purgadas(df_panel, calendario, horizonte_dias=h_dias)

        df_tr = parts["train"]
        df_val = parts["val"]
        df_te = parts["test"]
        col_y = parts["col_target"]

        X_tr = df_tr[VARIABLES_RETORNO]
        y_tr = df_tr[col_y].astype(int)

        X_val = df_val[VARIABLES_RETORNO]
        y_val = df_val[col_y].astype(int)

        X_te = df_te[VARIABLES_RETORNO]
        y_te = df_te[col_y].astype(int)

        n_tr, pos_tr = len(df_tr), int(y_tr.sum())
        n_val, pos_val = len(df_val), int(y_val.sum())
        n_te, pos_te = len(df_te), int(y_te.sum())

        print(f"Train (Jun-Jul): {n_tr:,} obs ({len(df_tr['FechaRef'].unique())} fechas) | Positivos: {pos_tr:,} ({pos_tr*100/n_tr:.2f}%)")
        print(f"Val (Septiembre purgado): {n_val:,} obs ({len(df_val['FechaRef'].unique())} fechas) | Positivos: {pos_val:,} ({pos_val*100/n_val:.2f}%)")
        print(f"  - Fechas purgadas antes de Test: {len(parts['fechas_purgadas_val'])} fechas")
        print(f"Test (Octubre out-of-time): {n_te:,} obs ({len(df_te['FechaRef'].unique())} fechas) | Positivos: {pos_te:,} ({pos_te*100/n_te:.2f}%)")
        print(f"  - Fechas censuradas a la derecha: {len(parts['fechas_censuradas_test'])} fechas")

        fabricas = obtener_fabricas_modelos()
        modelos_res = {}

        ganador_val_h = None
        mejor_mf1_val_h = -1.0

        for nom_mod, mod_info in fabricas.items():
            print(f"  Entrenando y evaluando: {nom_mod}...")

            # 1. Ajustar umbral en validación interna purgada
            u_opt = seleccionar_umbral_optimo_validacion(
                mod_info["factory"], X_tr, y_tr, X_val, y_val
            )

            # 2. Entrenar clasificador en Train
            clf = mod_info["factory"]()
            clf.fit(X_tr, y_tr)

            # 3. Evaluar en Validación interna
            res_val = evaluar_modelo_en_conjunto(clf, X_val, y_val, umbral=u_opt)
            mf1_val = res_val["metricas_dependientes_del_umbral"]["macro_f1"]

            # Rastrear ganador en validación (solo entre los 5 modelos de ML)
            if not mod_info["es_linea_base"] and mf1_val > mejor_mf1_val_h:
                mejor_mf1_val_h = mf1_val
                ganador_val_h = nom_mod

            # 4. Evaluar a ciegas en Test Final (una sola vez)
            res_test = evaluar_modelo_en_conjunto(clf, X_te, y_te, umbral=u_opt)

            modelos_res[nom_mod] = {
                "tipo": mod_info["tipo"],
                "es_linea_base": mod_info["es_linea_base"],
                "umbral_seleccionado_en_validacion": u_opt,
                "desempeno_validacion_septiembre": res_val,
                "desempeno_prueba_final_octubre": res_test,
            }

            # Guardar candidato global de producción si es H1 y lidera validación
            if h_dias == 1 and not mod_info["es_linea_base"] and mf1_val > mejor_mf1_val_global:
                mejor_mf1_val_global = mf1_val
                mejor_candidato_val = (nom_mod, clf, u_opt, res_val, res_test)

        reporte_dict["horizontes"][h_id] = {
            "nombre": h_nom,
            "horizonte_dias": h_dias,
            "particiones": {
                "train_obs": n_tr,
                "train_fechas": len(df_tr["FechaRef"].unique()),
                "val_obs": n_val,
                "val_fechas": len(df_val["FechaRef"].unique()),
                "val_fechas_purgadas": len(parts["fechas_purgadas_val"]),
                "test_obs": n_te,
                "test_fechas": len(df_te["FechaRef"].unique()),
                "test_fechas_censuradas": len(parts["fechas_censuradas_test"]),
            },
            "ganador_declarado_en_validacion": ganador_val_h,
            "mejor_macro_f1_validacion": mejor_mf1_val_h,
            "modelos": modelos_res,
        }

    # Serializar candidato separado si se obtuvo
    path_cand_model = ROOT_DIR / "ml" / "models" / "modelo_retorno_alumno_candidato_hgb.joblib"
    path_cand_meta = ROOT_DIR / "ml" / "models" / "metadatos_retorno_alumno_candidato.json"

    if mejor_candidato_val:
        c_nom, c_clf, c_u, c_rval, c_rte = mejor_candidato_val
        joblib.dump(c_clf, path_cand_model)

        meta_cand = {
            "nombre": f"Modelo Candidato de Retorno Individual ({c_nom})",
            "version": "1.1.0-candidato",
            "algoritmo": c_nom,
            "estado": "candidato_evaluado_pendiente_pase",
            "umbral_optimo_seleccionado_en_validacion": c_u,
            "metricas_validacion_septiembre": c_rval["metricas_dependientes_del_umbral"],
            "metricas_prueba_octubre_ciega": c_rte["metricas_dependientes_del_umbral"],
            "variables": VARIABLES_RETORNO,
            "criterio_suficiencia": "minimo_dias_activos >= 3",
            "aviso_conservacion": "El artefacto modelo_retorno_alumno.joblib se mantuvo intacto en produccion."
        }
        with open(path_cand_meta, "w", encoding="utf-8") as f:
            json.dump(meta_cand, f, indent=4, ensure_ascii=False)
        print(f"\n[OK] Artefacto candidato serializado en: {path_cand_model}")
        print(f"[OK] Metadatos del candidato guardados en: {path_cand_meta}")

    # Guardar resultados JSON
    path_json = ROOT_DIR / "ml" / "reports" / "resultados_comparacion_cinco_modelos_retorno.json"
    with open(path_json, "w", encoding="utf-8") as f:
        json.dump(reporte_dict, f, indent=4, ensure_ascii=False)
    print(f"[OK] Resultados JSON guardados en: {path_json}")

    # Generar Reporte Markdown
    path_md = ROOT_DIR / "ml" / "reports" / "reporte_comparacion_cinco_modelos_retorno.md"
    generar_reporte_markdown(reporte_dict, path_md)
    print(f"[OK] Reporte Markdown generado en: {path_md}")

    return reporte_dict


def generar_reporte_markdown(rep, ruta_md):
    """Genera el reporte técnico exhaustivo en Markdown."""
    md = [
        "# Comparación Experimental de Cinco Modelos de ML: Retorno Individual de Alumnos",
        "",
        "**Biblioteca Central UNDAC** | **Metodología:** Purged Temporal Split | **Estado:** Experimento Local Concluido",
        "",
        "---",
        "",
        "## 1. Calendario de Atención Real y Tratamiento de Agosto y Julio",
        "",
        "### A. Distinción entre Cierres Efectivos y Atención Reducida en Agosto",
        "- **Cierres efectivos documentados (cero atención):** Sábados, domingos y feriados nacionales confirmados: `29/06`, `23/07`, `24/07`, `27/07` a `29/07` (Fiestas Patrias), `06/08` (Batalla de Junín), `30/08` (Santa Rosa) y `08/10` (Combate de Angamos).",
        "- **Atención reducida en agosto (03/08 al 21/08):** La biblioteca **no estuvo completamente cerrada**. Atendió en jornada diurna con afluencia reducida/administrativa (14 fechas hábiles con visitas comprobadas).",
        "- **Atención regular restaurada:** `24/08` al `31/08` y semestres ordinarios (total de **94 fechas operativas de atención real** desde junio hasta el 20 de octubre).",
        "",
        "### B. Tratamiento de las Referencias del 17 al 31 de Julio",
        "- **Horizonte 1 (1 Día):** Todas las referencias del 17 al 31 de julio tienen siguiente día hábil observable con atención (el 31/07 tuvo su siguiente día el 03/08, con 30 alumnos). **Ninguna referencia de julio está censurada para H1**.",
        "- **Horizonte 2 (7 Días):** Al disponer de las fechas de atención de agosto, las referencias del 17 al 31 de julio completan sus 7 días de atención en agosto (ej. 17/07 completa al 04/08 con 54.76% de retorno; 31/07 completa al 17/08 con 4.06% de retorno). Se computan sin censura artificial.",
        "",
        "### C. Tratamiento de Censuras al Corte del 21/10/2026",
        "- **Horizonte 1 (H1):** Máximo referencias hasta el `19/10/2026`. Las referencias del `20/10/2026` tienen como siguiente día el `21/10/2026` (en curso/parcial) y quedan **censuradas a la derecha** (`NaN`).",
        "- **Horizonte 2 (H7):** Máximo referencias hasta el `09/10/2026` (sus 7 días concluyeron el 20/10/2026). Referencias a partir del `12/10/2026` tienen ventanas abiertas que concluyen después del 20/10 y quedan **censuradas a la derecha** (`NaN`).",
        "",
        "---",
        "",
        "## 2. Protocolo de Partición Temporal Purgada (Purged Split)",
        "",
        "1. **Entrenamiento (Train):** Semestre I histórico (`01/06/2026` al `31/07/2026`).",
        "2. **Validación Interna Purgada (Val):** Septiembre 2026 (`01/09/2026` al `30/09/2026`).",
        "   - En H1 se purga el `30/09` (1 fecha antes de Test).",
        "   - En H7 se purgan del `22/09` al `30/09` (7 fechas operativas cuyas ventanas tocan Octubre).",
        "   - **Sintonización de umbral $\\theta^*$:** Se selecciona en validación maximizando Macro F1.",
        "   - **Selección de ganador:** El modelo candidato se selecciona **estrictamente por su desempeño en Validación**.",
        "3. **Prueba Final Out-of-Time (Test):** Octubre 2026 (`01/10` a `19/10` para H1; `01/10` a `09/10` para H7).",
        "   - Evaluado una sola vez a ciegas con el umbral fijado en validación. No se observa para elegir ganador.",
        "",
        "---",
        "",
        "## 3. Resultados Detallados: Horizonte 1 (Siguiente Día Hábil)",
        "",
    ]

    h1_info = rep["horizontes"]["horizonte_1_dia"]
    md.append(f"**Particiones:** Train: {h1_info['particiones']['train_obs']:,} obs | Val: {h1_info['particiones']['val_obs']:,} obs | Test: {h1_info['particiones']['test_obs']:,} obs.")
    md.append(f"**Ganador seleccionado en Validación (H1):** `{h1_info['ganador_declarado_en_validacion']}` (Macro F1 Val: {h1_info['mejor_macro_f1_validacion']:.4f}).\n")

    md.extend([
        "### A. Desempeño en Validación (Septiembre purgado):",
        "",
        "| Modelo | Tipo / Rol | Umbral $\\theta^*$ | ROC-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ])

    for nom, m in h1_info["modelos"].items():
        v = m["desempeno_validacion_septiembre"]
        mi = v["metricas_independientes_del_umbral"]
        mdep = v["metricas_dependientes_del_umbral"]
        resaltado = "**" if nom == h1_info["ganador_declarado_en_validacion"] else ""
        md.append(
            f"| {resaltado}{nom}{resaltado} | {m['tipo']} | {mdep['umbral']:.2f} | {mi['auc_roc']:.4f} | "
            f"{mi['brier_score']:.4f} | {mdep['balanced_accuracy']:.4f} | **{mdep['macro_f1']:.4f}** | "
            f"{mdep['recall_positivo']:.4f} | {mdep['precision_positivo']:.4f} |"
        )

    md.extend([
        "",
        "### B. Desempeño Fuera de Muestra en Prueba Final Ciega (Octubre 01 al 19):",
        "",
        "| Modelo | Tipo / Rol | ROC-AUC | PR-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) | Matriz Confusión [[TN, FP], [FN, TP]] |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
    ])

    for nom, m in h1_info["modelos"].items():
        t = m["desempeno_prueba_final_octubre"]
        mi = t["metricas_independientes_del_umbral"]
        mdep = t["metricas_dependientes_del_umbral"]
        cm = mdep["matriz_confusion"]
        cm_str = f"[[{cm['verdaderos_negativos_TN']:,}, {cm['falsos_positivos_FP']:,}], [{cm['falsos_negativos_FN']:,}, {cm['verdaderos_positivos_TP']:,}]]"
        md.append(
            f"| **{nom}** | {m['tipo']} | **{mi['auc_roc']:.4f}** | {mi['pr_auc']:.4f} | {mi['brier_score']:.4f} | "
            f"{mdep['balanced_accuracy']:.4f} | **{mdep['macro_f1']:.4f}** | {mdep['recall_positivo']:.4f} | "
            f"{mdep['precision_positivo']:.4f} | `{cm_str}` |"
        )

    md.extend([
        "",
        "---",
        "",
        "## 4. Resultados Detallados: Horizonte 2 (Próximos 7 Días Operativos)",
        "",
    ])

    h7_info = rep["horizontes"]["horizonte_7_dias"]
    md.append(f"**Particiones:** Train: {h7_info['particiones']['train_obs']:,} obs | Val: {h7_info['particiones']['val_obs']:,} obs | Test: {h7_info['particiones']['test_obs']:,} obs.")
    md.append(f"**Ganador seleccionado en Validación (H7):** `{h7_info['ganador_declarado_en_validacion']}` (Macro F1 Val: {h7_info['mejor_macro_f1_validacion']:.4f}).\n")

    md.extend([
        "### A. Desempeño en Validación (Septiembre purgado):",
        "",
        "| Modelo | Tipo / Rol | Umbral $\\theta^*$ | ROC-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ])

    for nom, m in h7_info["modelos"].items():
        v = m["desempeno_validacion_septiembre"]
        mi = v["metricas_independientes_del_umbral"]
        mdep = v["metricas_dependientes_del_umbral"]
        resaltado = "**" if nom == h7_info["ganador_declarado_en_validacion"] else ""
        md.append(
            f"| {resaltado}{nom}{resaltado} | {m['tipo']} | {mdep['umbral']:.2f} | {mi['auc_roc']:.4f} | "
            f"{mi['brier_score']:.4f} | {mdep['balanced_accuracy']:.4f} | **{mdep['macro_f1']:.4f}** | "
            f"{mdep['recall_positivo']:.4f} | {mdep['precision_positivo']:.4f} |"
        )

    md.extend([
        "",
        "### B. Desempeño Fuera de Muestra en Prueba Final Ciega (Octubre 01 al 09):",
        "",
        "| Modelo | Tipo / Rol | ROC-AUC | PR-AUC | Brier | Bal. Acc | Macro F1 | Recall (+) | Precision (+) | Matriz Confusión [[TN, FP], [FN, TP]] |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"
    ])

    for nom, m in h7_info["modelos"].items():
        t = m["desempeno_prueba_final_octubre"]
        mi = t["metricas_independientes_del_umbral"]
        mdep = t["metricas_dependientes_del_umbral"]
        cm = mdep["matriz_confusion"]
        cm_str = f"[[{cm['verdaderos_negativos_TN']:,}, {cm['falsos_positivos_FP']:,}], [{cm['falsos_negativos_FN']:,}, {cm['verdaderos_positivos_TP']:,}]]"
        md.append(
            f"| **{nom}** | {m['tipo']} | **{mi['auc_roc']:.4f}** | {mi['pr_auc']:.4f} | {mi['brier_score']:.4f} | "
            f"{mdep['balanced_accuracy']:.4f} | **{mdep['macro_f1']:.4f}** | {mdep['recall_positivo']:.4f} | "
            f"{mdep['precision_positivo']:.4f} | `{cm_str}` |"
        )

    md.extend([
        "",
        "---",
        "",
        "## 5. Conclusiones y Estado de los Artefactos",
        "",
        "1. **Preservación Incondicional del Modelo Activo:**",
        "   - El artefacto oficial `ml/models/modelo_retorno_alumno.joblib` permanece **intacto, activo y sin modificaciones**.",
        "2. **Generación de Artefactos Candidatos Separados:**",
        "   - Se generó el artefacto candidato evaluado en `ml/models/modelo_retorno_alumno_candidato_hgb.joblib` y sus metadatos en `ml/models/metadatos_retorno_alumno_candidato.json`.",
        "3. **Metodología Científica Verificada:**",
        "   - La selección del modelo candidato se basó exclusivamente en la validación interna purgada de Septiembre, protegiendo la integridad ciega del conjunto final de prueba de Octubre.",
    ])

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    ejecutar_experimento_cinco_modelos()
