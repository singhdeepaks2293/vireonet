"""Supervised: change-failure classification and regression (duration, traffic)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import Lasso, LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TunedThresholdClassifierCV, train_test_split
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from vireonet.ml.datasets import ChangeDataset, TrafficSeries, change_features

CLASSIFIERS = {
    "gradient_boosting": lambda: GradientBoostingClassifier(random_state=0),
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=200, random_state=0, class_weight="balanced"
    ),
    "logistic_regression": lambda: make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")
    ),
}

REGRESSORS = {
    "linear": lambda: make_pipeline(StandardScaler(), LinearRegression()),
    "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
    "lasso": lambda: make_pipeline(StandardScaler(), Lasso(alpha=0.01, max_iter=5000)),
    "random_forest": lambda: RandomForestRegressor(n_estimators=200, random_state=0),
    "gradient_boosting": lambda: GradientBoostingRegressor(random_state=0),
    "knn": lambda: make_pipeline(StandardScaler(), KNeighborsRegressor(n_neighbors=7)),
    "svr": lambda: make_pipeline(StandardScaler(), SVR(C=3.0, epsilon=0.02)),
}


def _importances(model: Any, names: list[str]) -> list[dict[str, Any]]:
    est = model[-1] if hasattr(model, "steps") else model
    if hasattr(est, "feature_importances_"):
        vals = est.feature_importances_
    elif hasattr(est, "coef_"):
        vals = np.abs(np.ravel(est.coef_))
    else:
        return []
    order = np.argsort(vals)[::-1][:10]
    return [{"feature": names[i], "importance": round(float(vals[i]), 4)} for i in order]


def classify_change_risk(
    data: ChangeDataset, algorithms: list[str] | None = None
) -> dict[str, Any]:
    """Train classifiers to predict whether a change fails (rolls back),
    compared on a held-out 25% split (stratified)."""
    if len(set(data.failed.tolist())) < 2:
        raise ValueError("need both successful and failed changes to train a classifier")
    X_tr, X_te, y_tr, y_te = train_test_split(
        data.X, data.failed, test_size=0.25, random_state=0, stratify=data.failed
    )
    models: list[dict[str, Any]] = []
    for name in algorithms or list(CLASSIFIERS):
        if name not in CLASSIFIERS:
            raise ValueError(f"unknown classifier {name!r} (want {sorted(CLASSIFIERS)})")
        # Failures are the minority class: tune the decision threshold by
        # cross-validation (F1) instead of a fixed 0.5.
        tuned = TunedThresholdClassifierCV(CLASSIFIERS[name](), scoring="f1", cv=5).fit(X_tr, y_tr)
        model = tuned.estimator_
        proba = tuned.predict_proba(X_te)[:, 1]
        pred = tuned.predict(X_te)
        models.append(
            {
                "algorithm": name,
                "accuracy": round(float(accuracy_score(y_te, pred)), 3),
                "precision": round(float(precision_score(y_te, pred, zero_division=0)), 3),
                "recall": round(float(recall_score(y_te, pred, zero_division=0)), 3),
                "f1": round(float(f1_score(y_te, pred, zero_division=0)), 3),
                "roc_auc": round(float(roc_auc_score(y_te, proba)), 3),
                "threshold": round(float(tuned.best_threshold_), 3),
                "top_features": _importances(model, data.feature_names),
                "_model": tuned,
            }
        )
    models.sort(key=lambda m: -m["roc_auc"])
    best = models[0]
    return {
        "task": "change_failure",
        "source": data.source,
        "notes": data.notes,
        "rows": data.rows,
        "failure_rate": round(float(np.mean(data.failed)), 3),
        "test_rows": len(y_te),
        "best": best["algorithm"],
        "models": [{k: v for k, v in m.items() if k != "_model"} for m in models],
        "_best_model": best["_model"],
    }


def predict_change_risk(model: Any, **features: Any) -> float:
    """Failure probability (the tuned threshold only affects the yes/no call)."""
    return float(model.predict_proba(np.array([change_features(**features)]))[0, 1])


def _regress(
    X: np.ndarray, y: np.ndarray, names: list[str], algorithms: list[str] | None
) -> list[dict[str, Any]]:
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=0)
    out: list[dict[str, Any]] = []
    for name in algorithms or list(REGRESSORS):
        if name not in REGRESSORS:
            raise ValueError(f"unknown regressor {name!r} (want {sorted(REGRESSORS)})")
        model = REGRESSORS[name]().fit(X_tr, y_tr)
        pred = model.predict(X_te)
        out.append(
            {
                "algorithm": name,
                "mae": round(float(mean_absolute_error(y_te, pred)), 4),
                "rmse": round(float(math.sqrt(mean_squared_error(y_te, pred))), 4),
                "r2": round(float(r2_score(y_te, pred)), 3),
                "top_features": _importances(model, names),
                "sample": [
                    {"actual": round(float(a), 4), "predicted": round(float(p), 4)}
                    for a, p in list(zip(y_te, pred, strict=True))[:40]
                ],
            }
        )
    out.sort(key=lambda m: m["rmse"])
    return out


def regress_change_duration(
    data: ChangeDataset, algorithms: list[str] | None = None
) -> dict[str, Any]:
    """Predict how long a change takes (minutes) from its features."""
    models = _regress(data.X, data.duration_min, data.feature_names, algorithms)
    return {
        "task": "change_duration_minutes",
        "source": data.source,
        "notes": data.notes,
        "rows": data.rows,
        "best": models[0]["algorithm"],
        "models": models,
    }


TRAFFIC_FEATURES = ["lag1", "lag2", "lag3", "lag6", "mean6", "hour_sin", "hour_cos"]


def _traffic_rows(series: list[TrafficSeries], horizon: int) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    from datetime import datetime

    for s in series:
        u = np.maximum(s.in_util, s.out_util)
        for t in range(6, len(u) - horizon):
            try:
                hour = datetime.fromisoformat(s.timestamps[t]).hour
            except ValueError:
                hour = 0
            ang = 2 * math.pi * hour / 24
            X.append(
                [
                    u[t],
                    u[t - 1],
                    u[t - 2],
                    u[t - 5],
                    float(np.mean(u[t - 5 : t + 1])),
                    math.sin(ang),
                    math.cos(ang),
                ]
            )
            y.append(u[t + horizon])
    return np.array(X), np.array(y)


def regress_traffic(
    series: list[TrafficSeries], horizon_steps: int = 6, algorithms: list[str] | None = None
) -> dict[str, Any]:
    """Forecast utilisation `horizon_steps` ahead from recent lags and time of day."""
    X, y = _traffic_rows(series, horizon_steps)
    if len(y) < 30:
        raise ValueError("not enough traffic history to train a forecast")
    models = _regress(X, y, TRAFFIC_FEATURES, algorithms)
    return {
        "task": "traffic_forecast_utilisation",
        "horizon_steps": horizon_steps,
        "rows": len(y),
        "interfaces": len(series),
        "best": models[0]["algorithm"],
        "models": models,
    }
