"""Unsupervised: anomaly detection and k-means clustering on interface traffic."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.metrics import silhouette_score
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler

from vireonet.ml.datasets import TrafficSeries

AnomalyAlgorithm = Literal["isolation_forest", "local_outlier_factor", "zscore"]


def _robust_features(s: TrafficSeries) -> np.ndarray:
    """Per time point: in/out utilisation and their step change, each scaled
    by this interface's own median and MAD. A busy uplink and a quiet access
    port become comparable, so one model can learn "normal" network-wide."""
    U = np.column_stack([s.in_util, s.out_util])
    F = np.column_stack([U, np.diff(U, axis=0, prepend=U[:1])])
    med = np.median(F, axis=0)
    mad = np.median(np.abs(F - med), axis=0) * 1.4826 + 1e-6
    return np.asarray((F - med) / mad)


def detect_anomalies(
    series: list[TrafficSeries],
    algorithm: AnomalyAlgorithm = "isolation_forest",
    contamination: float = 0.005,
    z_threshold: float = 4.0,
) -> dict[str, Any]:
    """Anomalous traffic points.

    isolation_forest / local_outlier_factor: one model over every interface's
    robust-scaled points; `contamination` is the expected share of anomalous
    points network-wide. zscore: per interface, |x - median| / MAD above
    `z_threshold` on in or out utilisation (a transparent baseline).
    """
    feats = [_robust_features(s) for s in series]
    if algorithm == "zscore":
        scores = [np.max(np.abs(f[:, :2]), axis=1) for f in feats]
        flags = [sc > z_threshold for sc in scores]
    else:
        X = np.vstack(feats)
        bounds = np.cumsum([0] + [len(f) for f in feats])
        if algorithm == "local_outlier_factor":
            lof = LocalOutlierFactor(n_neighbors=min(35, len(X) - 1), contamination=contamination)
            all_flags = lof.fit_predict(X) == -1
            all_scores = -lof.negative_outlier_factor_
        else:
            iso = IsolationForest(contamination=contamination, random_state=0, n_estimators=200)
            all_flags = iso.fit_predict(X) == -1
            all_scores = -iso.score_samples(X)
        # Keep only points that are also unusual for their own interface
        # (robust z > 3 on in or out utilisation): a forced contamination
        # share must not turn a normal peak into an "anomaly".
        all_flags = all_flags & (np.max(np.abs(X[:, :2]), axis=1) > 3.0)
        flags = [all_flags[bounds[i] : bounds[i + 1]] for i in range(len(series))]
        scores = [all_scores[bounds[i] : bounds[i + 1]] for i in range(len(series))]
    results: list[dict[str, Any]] = []
    for s, flag, score in zip(series, flags, scores, strict=True):
        idx = np.flatnonzero(flag)
        results.append(
            {
                "device": s.device,
                "interface": s.interface,
                "points": len(s.in_util),
                "anomalies": [
                    {
                        "timestamp": s.timestamps[i],
                        "in_util": round(float(s.in_util[i]), 4),
                        "out_util": round(float(s.out_util[i]), 4),
                        "score": round(float(score[i]), 3),
                    }
                    for i in idx
                ],
                "series": {
                    "timestamps": s.timestamps,
                    "in_util": [round(float(v), 4) for v in s.in_util],
                    "out_util": [round(float(v), 4) for v in s.out_util],
                },
            }
        )
    results.sort(key=lambda r: -len(r["anomalies"]))
    return {
        "algorithm": algorithm,
        "interfaces": len(results),
        "anomalous_interfaces": sum(1 for r in results if r["anomalies"]),
        "results": results,
    }


PROFILE_FEATURES = [
    "mean_in",
    "mean_out",
    "peak_in",
    "peak_out",
    "std_in",
    "std_out",
    "out_in_ratio",
]


def _profile(s: TrafficSeries) -> list[float]:
    return [
        float(np.mean(s.in_util)),
        float(np.mean(s.out_util)),
        float(np.percentile(s.in_util, 95)),
        float(np.percentile(s.out_util, 95)),
        float(np.std(s.in_util)),
        float(np.std(s.out_util)),
        float(np.mean(s.out_util) / (np.mean(s.in_util) + 1e-6)),
    ]


def _describe(center: dict[str, float]) -> str:
    load = max(center["peak_in"], center["peak_out"])
    level = "hot" if load >= 0.7 else "busy" if load >= 0.4 else "quiet"
    swing = "bursty" if max(center["std_in"], center["std_out"]) > 0.12 else "steady"
    ratio = center["out_in_ratio"]
    shape = "mostly outbound" if ratio > 1.3 else "mostly inbound" if ratio < 0.77 else "balanced"
    return f"{level}, {swing}, {shape}"


def cluster_interfaces(series: list[TrafficSeries], k: int | None = None) -> dict[str, Any]:
    """K-means on per-interface traffic profiles. k=None picks k in 2..6 by
    silhouette score."""
    if len(series) < 3:
        raise ValueError("need at least 3 interfaces with history to cluster")
    X = np.array([_profile(s) for s in series])
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    tried: list[dict[str, float]] = []
    if k is None:
        best = None
        for cand in range(2, min(6, len(series) - 1) + 1):
            labels = KMeans(n_clusters=cand, n_init=10, random_state=0).fit_predict(Xs)
            sil = float(silhouette_score(Xs, labels))
            tried.append({"k": cand, "silhouette": round(sil, 3)})
            if best is None or sil > best[1]:
                best = (cand, sil)
        k = best[0] if best else 2
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(Xs)
    centers = scaler.inverse_transform(km.cluster_centers_)
    clusters: list[dict[str, Any]] = []
    for c in range(k):
        center = {f: round(float(v), 4) for f, v in zip(PROFILE_FEATURES, centers[c], strict=True)}
        members = [
            {"device": s.device, "interface": s.interface}
            for s, lab in zip(series, km.labels_, strict=True)
            if lab == c
        ]
        clusters.append(
            {
                "cluster": c,
                "size": len(members),
                "center": center,
                "label": _describe(center),
                "members": members,
            }
        )
    clusters.sort(key=lambda c: -max(c["center"]["peak_in"], c["center"]["peak_out"]))
    return {
        "algorithm": "kmeans",
        "k": k,
        "silhouette": round(float(silhouette_score(Xs, km.labels_)), 3) if k > 1 else None,
        "k_search": tried,
        "features": PROFILE_FEATURES,
        "clusters": clusters,
        "points": [
            {
                "device": s.device,
                "interface": s.interface,
                "cluster": int(lab),
                "mean_in": round(float(x[0]), 4),
                "mean_out": round(float(x[1]), 4),
            }
            for s, lab, x in zip(series, km.labels_, X, strict=True)
        ],
    }
