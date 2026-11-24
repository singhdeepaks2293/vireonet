"""Neural-network models for the Intelligence tab.

All are scikit-learn multilayer perceptrons (MLPClassifier / MLPRegressor):
light, CPU-only, no extra dependency. Each model says what it is and reports
how well it does on held-out data, so nobody has to trust a black box.

1. log_sequences   — DeepLog-style: an MLP learns "which event normally comes
                     next" from a window of previous syslog events per device;
                     an event outside the model's top-k guesses is anomalous.
2. forecast        — MLP over sliding windows of interface utilisation predicts
                     the next hours (multi-step, recursive); reports time to
                     saturation and compares with a naive baseline.
3. change_risk     — MLP classifier on change features (blast radius, vendor
                     mix, hour, emergency, past failure rate) with permutation
                     importance; usable from flows ("Predict change risk").
4. root_cause      — simplified graph convolution (SGC): alarm features are
                     propagated over the topology (A^k X) and an MLP ranks which
                     device most likely caused the alarms. Trained on simulated
                     failures of the real topology.
5. ticket_text     — TF-IDF + MLP classifies a free-text ticket into a change
                     type; device names are extracted ("leaf 1" -> leaf-01).

Advisory only: these scores inform people and flow conditions; they never
change anything by themselves.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.inspection import permutation_importance
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 7


# --- 1. Log event sequences (DeepLog-style) -------------------------------------------


def log_sequences(
    events: list[Any],
    *,
    window: int = 4,
    top_k: int = 3,
    recent_fraction: float = 0.3,
    max_devices_report: int = 50,
) -> dict[str, Any]:
    """`events`: parsed syslog events with .device, .mnemonic, .timestamp.

    DeepLog-style: the MLP learns normal event order from the OLDER part of
    each device's log and scores the RECENT part; a recent event outside the
    model's top-k guesses (or a never-seen event type) is unexpected."""
    by_dev: dict[str, list[str]] = defaultdict(list)
    for e in sorted(events, key=lambda e: (e.device, str(e.timestamp))):
        by_dev[e.device].append(str(e.mnemonic))
    # oldest 50 %: learn; next 20 %: the device's own baseline (held out); rest: scored.
    split = {d: max(window + 1, int(len(seq) * (1 - recent_fraction))) for d, seq in by_dev.items()}
    train_end = {d: max(window + 1, int(len(seq) * 0.5)) for d, seq in by_dev.items()}
    vocab = sorted({m for d, seq in by_dev.items() for m in seq[: train_end[d]]})
    if len(vocab) < 3:
        raise ValueError("not enough distinct log events to learn sequences")
    idx = {m: i for i, m in enumerate(vocab)}
    dim = window * (len(vocab) + 1)

    def encode(ctx: list[str]) -> np.ndarray:
        v = np.zeros(dim)
        for j, m in enumerate(ctx):
            v[j * (len(vocab) + 1) + idx.get(m, len(vocab))] = 1.0  # last slot = unknown event
        return v

    X, y = [], []
    for d, seq in by_dev.items():
        for i in range(window, train_end[d]):
            X.append(encode(seq[i - window : i]))
            y.append(idx[seq[i]])
    if len(X) < 30:
        raise ValueError("not enough log events per device to learn sequences (need > 30 windows)")
    model = MLPClassifier(hidden_layer_sizes=(64,), max_iter=500, random_state=SEED)
    model.fit(np.array(X), np.array(y))
    classes = list(model.classes_)
    devices: list[dict[str, Any]] = []
    hits, total = 0, 0
    _all_unexpected: dict[str, list[dict[str, Any]]] = {}
    baseline: dict[str, float] = {}
    for d, seq in by_dev.items():
        unexpected = []
        scored = 0
        for i in range(max(window, split[d]), len(seq)):
            scored += 1
            true = seq[i]
            row = model.predict_proba(np.array([encode(seq[i - window : i])]))[0]
            top = [vocab[classes[c]] for c in np.argsort(row)[::-1][:top_k]]
            if true in top:
                hits += 1
            else:
                unexpected.append(
                    {
                        "position": i,
                        "event": true,
                        "new_event_type": true not in idx,
                        "expected_one_of": top,
                        "after": seq[i - window : i],
                    }
                )
            total += 1
        _all_unexpected[d] = unexpected
        # The device's own miss rate on held-out older windows: a device whose
        # normal log has no fixed order is not anomalous for being unordered.
        base_miss, base_n = 0, 0
        for i in range(train_end[d], split[d]):
            row = model.predict_proba(np.array([encode(seq[i - window : i])]))[0]
            top = [vocab[classes[c]] for c in np.argsort(row)[::-1][:top_k]]
            base_miss += seq[i] not in top
            base_n += 1
        baseline[d] = base_miss / max(1, base_n)
        devices.append(
            {
                "device": d,
                "events": len(seq),
                "scored": scored,
                "unexpected": len(unexpected),
                "rate": round(len(unexpected) / max(1, scored), 3),
                "examples": unexpected[:3],
            }
        )
    rates = np.array([r["rate"] for r in devices])
    med = float(np.median(rates))
    mad = float(np.median(np.abs(rates - med))) or 0.02
    cut = med + 3 * 1.4826 * mad
    for r in devices:
        new_types = sorted({u["event"] for u in r["examples"] + [] if u["new_event_type"]})
        r["new_event_types"] = sorted(
            {u["event"] for u in _all_unexpected[r["device"]] if u["new_event_type"]}
        )
        reasons = []
        if r["new_event_types"]:
            reasons.append(f"new kind of event: {', '.join(r['new_event_types'][:3])}")
        r["baseline_rate"] = round(baseline[r["device"]], 3)
        if (
            r["scored"] >= 10
            and r["rate"] > cut
            and r["rate"] > r["baseline_rate"] + 0.2
            and r["unexpected"] >= 3
        ):
            reasons.append(
                f"{r['rate']:.0%} of recent events out of the usual order (usually {r['baseline_rate']:.0%})"
            )
        r["anomalous"] = bool(reasons)
        r["reasons"] = reasons
        del new_types
    devices.sort(key=lambda r: -r["rate"])
    return {
        "model": f"MLP next-event classifier (window {window}, top-{top_k}) trained on the older {int((1 - recent_fraction) * 100)}% of each log",
        "top_k_accuracy": round(hits / max(1, total), 3),
        "threshold_rate": round(cut, 3),
        "devices": devices[:max_devices_report],
        "vocabulary": vocab,
    }


# --- 2. Traffic forecast ----------------------------------------------------------------


def forecast(
    series: list[Any],
    *,
    lags: int = 36,
    horizon: int = 18,
    saturation: float = 0.8,
    step_minutes: int = 10,
    max_series: int = 40,
) -> dict[str, Any]:
    """`series`: TrafficSeries (in_util / out_util 0..1 arrays).

    The window (36 samples = 6 h at 10-minute steps) must see enough of the daily
    cycle; with too short a window long recursive forecasts drift."""
    rows_X, rows_y = [], []
    usable = []
    for s in series:
        for direction, arr in (("in", s.in_util), ("out", s.out_util)):
            a = np.asarray(arr, dtype=float)
            if len(a) < lags + horizon + 10 or not np.isfinite(a).all():
                continue
            usable.append((s, direction, a))
            for i in range(lags, len(a)):
                w = a[i - lags : i]
                # Relative to the last value: the network learns the *change*, so
                # trends extrapolate instead of drifting back to the average.
                rows_X.append(w - w[-1])
                rows_y.append(a[i] - w[-1])
    if len(rows_X) < 50:
        raise ValueError("not enough interface history to train a forecast")
    X, y = np.array(rows_X), np.array(rows_y)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=SEED)
    model = make_pipeline(
        StandardScaler(),
        MLPRegressor(
            hidden_layer_sizes=(16,),
            alpha=1.0,
            max_iter=800,
            random_state=SEED,
            early_stopping=True,
        ),
    )
    model.fit(Xtr, ytr)
    # Cap each recursive step at the largest changes seen in training, so a spike
    # in the input is not compounded into an impossible forecast.
    step_cap = float(np.quantile(np.abs(ytr), 0.99)) or 0.05
    mae = float(mean_absolute_error(yte, model.predict(Xte)))  # change error = level error
    naive = float(mean_absolute_error(yte, np.zeros_like(yte)))  # naive: next = last
    out = []
    for s, direction, a in usable[: max_series * 2]:
        hist = list(a[-lags:])
        preds = []
        for _ in range(horizon):
            w = np.array(hist[-lags:])
            delta = float(np.clip(model.predict(np.array([w - w[-1]]))[0], -step_cap, step_cap))
            nxt = float(np.clip(w[-1] + delta, 0.0, 1.0))  # utilisation is 0-100 %
            preds.append(nxt)
            hist.append(nxt)
        cross = next((k for k, v in enumerate(preds) if v >= saturation), None)
        out.append(
            {
                "device": s.device,
                "interface": s.interface,
                "direction": direction,
                "now": round(float(a[-1]), 4),
                "forecast": [round(v, 4) for v in preds],
                "peak": round(max(preds), 4),
                "saturates_in_minutes": (cross + 1) * step_minutes if cross is not None else None,
            }
        )
    out.sort(
        key=lambda r: (
            r["saturates_in_minutes"] is None,
            r["saturates_in_minutes"] or 0,
            -r["peak"],
        )
    )
    return {
        "model": f"MLP regressor on the change over {lags} lagged samples, recursive {horizon}-step forecast",
        "mae": round(mae, 4),
        "naive_mae": round(naive, 4),
        "beats_naive": mae < naive,
        "saturation": saturation,
        "step_minutes": step_minutes,
        "series": out,
    }


# --- 3. Change risk ------------------------------------------------------------------------


_RISK_CACHE: dict[str, Any] = {}


def train_change_risk(dataset: Any) -> dict[str, Any]:
    X, y = dataset.X, dataset.failed
    if len(set(y.tolist())) < 2:
        raise ValueError("the change history needs both successful and failed changes")
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=SEED, stratify=y)
    model = make_pipeline(
        StandardScaler(),
        MLPClassifier(
            hidden_layer_sizes=(32, 16),
            alpha=0.1,
            max_iter=2000,
            random_state=SEED,
            early_stopping=True,
            validation_fraction=0.15,
            n_iter_no_change=30,
        ),
    )
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    imp = permutation_importance(model, Xte, yte, n_repeats=5, random_state=SEED, scoring="roc_auc")
    importances = sorted(
        (
            {"feature": n, "importance": round(float(v), 4)}
            for n, v in zip(dataset.feature_names, imp.importances_mean, strict=True)
        ),
        key=lambda r: -r["importance"],
    )
    trained = {
        "model": model,
        "auc": round(float(roc_auc_score(yte, p)), 3),
        "base_rate": round(float(y.mean()), 3),
        "importances": importances,
        "rows": int(dataset.rows),
        "source": dataset.source,
    }
    _RISK_CACHE["latest"] = trained
    return trained


def change_risk(trained: dict[str, Any], **features: Any) -> dict[str, Any]:
    from vireonet.ml.datasets import change_features

    x = np.array(
        [
            change_features(
                str(features.get("change_type", "vlan_add")),
                int(features.get("devices", 1)),
                int(features.get("platforms", 1)),
                int(features.get("hour", 2)),
                bool(features.get("weekend", False)),
                bool(features.get("emergency", False)),
                float(features.get("prior_failure_rate", 0.05)),
            )
        ]
    )
    risk = float(trained["model"].predict_proba(x)[0, 1])
    level = "high" if risk >= 0.5 else "medium" if risk >= 0.2 else "low"
    return {
        "risk": round(risk, 3),
        "level": level,
        "auc": trained["auc"],
        "base_rate": trained["base_rate"],
        "top_factors": trained["importances"][:4],
        "model": "MLP classifier (32-16), standardised features",
        "trained_on": f"{trained['rows']} {trained['source']} changes",
    }


def default_change_risk_model() -> dict[str, Any]:
    if "latest" not in _RISK_CACHE:
        from vireonet.ml.datasets import synthetic_changes

        train_change_risk(synthetic_changes(3000))
    out: dict[str, Any] = _RISK_CACHE["latest"]
    return out


# --- 4. Root cause on the topology (simplified graph convolution) ----------------------------


def _adjacency(nodes: list[str], links: list[tuple[str, str]]) -> np.ndarray:
    n, ix = len(nodes), {d: i for i, d in enumerate(nodes)}
    A = np.eye(n)
    for a, b in links:
        if a in ix and b in ix:
            A[ix[a], ix[b]] = A[ix[b], ix[a]] = 1.0
    d = A.sum(1)
    return A / np.sqrt(np.outer(d, d))  # symmetric normalisation (with self loops)


def _graph_features(A: np.ndarray, alarms: np.ndarray, hops: int = 2) -> np.ndarray:
    feats, h = [alarms], alarms
    for _ in range(hops):
        h = A @ h
        feats.append(h)
    deg = (A > 0).sum(1, keepdims=True).astype(float)
    return np.hstack([*feats, deg / deg.max()])


def root_cause(
    nodes: list[str],
    links: list[tuple[str, str]],
    alarms: dict[str, float],
    *,
    scenarios: int = 1500,
) -> dict[str, Any]:
    """`alarms`: device -> alarm strength (0..1). Returns devices ranked by root-cause probability."""
    if len(nodes) < 3:
        raise ValueError("the topology needs at least 3 devices")
    A = _adjacency(nodes, links)
    rnd = random.Random(SEED)
    neigh = {i: [j for j in range(len(nodes)) if A[i, j] > 0 and j != i] for i in range(len(nodes))}
    X, y = [], []
    for _ in range(scenarios):
        root = rnd.randrange(len(nodes))
        a = np.zeros((len(nodes), 1))
        a[root] = (
            rnd.uniform(0.6, 1.0) if rnd.random() < 0.8 else 0.0
        )  # roots are often silent (dead box)
        for j in neigh[root]:
            if rnd.random() < 0.85:
                a[j] = rnd.uniform(0.5, 1.0)
        for j in range(len(nodes)):  # unrelated noise
            if rnd.random() < 0.05:
                a[j] = max(a[j, 0], rnd.uniform(0.1, 0.6))
        F = _graph_features(A, a)
        for i in range(len(nodes)):
            X.append(F[i])
            y.append(1 if i == root else 0)
    Xa, ya = np.array(X), np.array(y)
    Xtr, Xte, ytr, yte = train_test_split(Xa, ya, test_size=0.2, random_state=SEED, stratify=ya)
    model = MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=600, random_state=SEED)
    model.fit(Xtr, ytr)
    auc = float(roc_auc_score(yte, model.predict_proba(Xte)[:, 1]))
    a = np.array([[float(alarms.get(d, 0.0))] for d in nodes])
    p = model.predict_proba(_graph_features(A, a))[:, 1]
    p = p / p.sum() if p.sum() > 0 else p
    ranked = sorted(
        (
            {"device": d, "probability": round(float(v), 3), "alarm": float(alarms.get(d, 0.0))}
            for d, v in zip(nodes, p, strict=True)
        ),
        key=lambda r: -float(r["probability"]),  # type: ignore[arg-type]
    )
    return {
        "model": "simplified graph convolution (A^1, A^2 alarm propagation) + MLP",
        "auc": round(auc, 3),
        "trained_on": f"{scenarios} simulated failures of this topology",
        "ranking": ranked[:10],
    }


# --- 5. Ticket text ------------------------------------------------------------------------------

_TEMPLATES = {
    "vlan_add": [
        "add vlan {v} on {d}",
        "please create vlan {v} named {n} on {d}",
        "new vlan {v} for {n} on {d}",
        "need vlan {v} trunked to {d}",
        "provision vlan {v} {n} at {d}",
    ],
    "vlan_remove": [
        "remove vlan {v} from {d}",
        "delete vlan {v} on {d}",
        "decommission vlan {v} {n}",
        "vlan {v} no longer needed on {d}",
    ],
    "acl_entry_add": [
        "allow tcp {p} from {ip} to {ip2} on {d}",
        "permit {ip} to {ip2} port {p} in acl edge-in",
        "open firewall rule port {p} for {ip}",
        "add acl entry permitting {ip} on {d}",
    ],
    "acl_entry_remove": [
        "remove acl entry {s} from edge-in",
        "delete rule {s} permitting {ip}",
        "close port {p} rule on {d}",
        "revoke access for {ip} in acl",
    ],
    "static_route_add": [
        "add static route {net} via {ip} on {d}",
        "route {net} next hop {ip} on {d}",
        "need a route to {net} through {ip}",
        "create static route for {net}",
    ],
    "static_route_remove": [
        "remove static route {net} on {d}",
        "delete route to {net} via {ip}",
        "withdraw static {net} from {d}",
    ],
    "interface_set": [
        "change description of {i} on {d} to {n}",
        "shut {i} on {d}",
        "set interface {i} description {n}",
        "enable port {i} on {d}",
        "update mtu on {i} {d}",
    ],
    "os_upgrade": [
        "upgrade {d} to {ver}",
        "os upgrade {d} to version {ver}",
        "software upgrade of {d} please",
        "patch {d} to {ver} for the security advisory",
        "update firmware on {d}",
    ],
}


def _fill(t: str, rnd: random.Random, devices: list[str]) -> str:
    return t.format(
        v=rnd.randint(2, 3999),
        n=rnd.choice(["web", "voice", "guest", "db", "backup", "iot"]),
        d=rnd.choice(devices + ["leaf 1", "spine2", "the core switch", "fw 02"]),
        p=rnd.choice([22, 443, 8443, 3306, 161]),
        ip=f"10.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}",
        ip2=f"172.16.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}",
        s=rnd.choice([10, 20, 30, 40]),
        net=f"10.{rnd.randint(0, 255)}.0.0/{rnd.choice([16, 20, 24])}",
        i=rnd.choice(["Gi1/0/2", "Ethernet1/49", "xe-0/0/1", "port 12"]),
        ver=rnd.choice(["17.9.4", "4.32.1F", "11.1.2", "23.4R1"]),
    )


_TEXT_CACHE: dict[str, Any] = {}


def _text_model(devices: list[str]) -> dict[str, Any]:
    key = ",".join(sorted(devices))
    if key in _TEXT_CACHE:
        cached: dict[str, Any] = _TEXT_CACHE[key]
        return cached
    rnd = random.Random(SEED)
    texts, labels = [], []
    for label, tpls in _TEMPLATES.items():
        for _ in range(140):
            t = _fill(rnd.choice(tpls), rnd, devices)
            texts.append(
                t
                if rnd.random() < 0.6
                else t.capitalize() + rnd.choice(["", ".", " asap", " - thanks", " (CAB approved)"])
            )
            labels.append(label)
    Xtr, Xte, ytr, yte = train_test_split(
        texts, labels, test_size=0.2, random_state=SEED, stratify=labels
    )
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True),
        MLPClassifier(hidden_layer_sizes=(48,), max_iter=300, random_state=SEED),
    )
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)
    trained = {
        "model": model,
        "accuracy": round(float(accuracy_score(yte, pred)), 3),
        "f1": round(float(f1_score(yte, pred, average="macro")), 3),
    }
    _TEXT_CACHE[key] = trained
    return trained


def extract_devices(text: str, devices: list[str]) -> list[str]:
    """Device names in free text, tolerant of 'leaf 1', 'leaf01', 'LEAF-01'."""
    norm = {re.sub(r"[^a-z0-9]", "", d.lower()): d for d in devices}
    found = []
    for m in re.finditer(r"\b([a-z][a-z]*)[\s_-]?0*(\d{1,3})\b", text.lower()):
        for cand in (f"{m.group(1)}{int(m.group(2)):02d}", f"{m.group(1)}{m.group(2)}"):
            if cand in norm and norm[cand] not in found:
                found.append(norm[cand])
    for d in devices:
        if d.lower() in text.lower() and d not in found:
            found.append(d)
    return found


def ticket_text(text: str, devices: list[str]) -> dict[str, Any]:
    trained = _text_model(devices)
    model = trained["model"]
    proba = model.predict_proba([text])[0]
    order = np.argsort(proba)[::-1]
    classes = list(model.classes_)
    version = re.search(r"\b(\d+\.\d+(?:\.\d+)?[A-Za-z0-9.]*)\b", text)
    vlan = re.search(r"\bvlan\s*(\d{1,4})\b", text, re.I)
    return {
        "change_type": classes[order[0]],
        "confidence": round(float(proba[order[0]]), 3),
        "alternatives": [
            {"change_type": classes[i], "confidence": round(float(proba[i]), 3)} for i in order[1:3]
        ],
        "devices": extract_devices(text, devices),
        "fields": {
            k: v
            for k, v in {
                "vlan_id": int(vlan.group(1)) if vlan else None,
                "version": version.group(1) if version and not vlan else None,
            }.items()
            if v is not None
        },
        "model": "TF-IDF (1–2 grams) + MLP classifier",
        "accuracy": trained["accuracy"],
        "macro_f1": trained["f1"],
    }
