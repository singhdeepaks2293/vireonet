"""Log anomaly detection on parsed events.

Three complementary detectors, all explainable:

1. Burst (robust z-score): per device and event type, counts per time
   window compared with that device's own median/MAD for the type.
2. Isolation Forest: one model over every device's window feature vectors
   (log counts of the most common event types, by severity), scaled per
   device, so unusual *mixes* of events stand out.
3. Rare / critical: any severity 0-2 event, or an event type the device
   has not logged before the analysed window's last part.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from vireonet.logs.parser import LogEvent

SEVERITY_NAMES = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]


def _bucket(ts: str, window_min: int) -> int | None:
    try:
        return int(datetime.fromisoformat(ts).timestamp()) // (window_min * 60)
    except ValueError:
        return None


def analyse(
    events: list[LogEvent],
    *,
    window_minutes: int = 30,
    z_threshold: float = 6.0,
    min_burst: int = 10,
    contamination: float = 0.02,
) -> dict[str, Any]:
    by_dev: dict[str, list[LogEvent]] = defaultdict(list)
    for e in events:
        by_dev[e.device].append(e)
    buckets_all = sorted(
        {b for e in events if (b := _bucket(e.timestamp, window_minutes)) is not None}
    )
    if not buckets_all:
        return {"devices": [], "incidents": [], "events": len(events), "windows": 0}
    top_types = [m for m, _ in Counter(e.mnemonic for e in events).most_common(12)]

    incidents: list[dict[str, Any]] = []
    rows: list[list[float]] = []
    row_keys: list[tuple[str, int]] = []
    for device, evs in by_dev.items():
        counts: dict[int, Counter[str]] = defaultdict(Counter)
        for e in evs:
            b = _bucket(e.timestamp, window_minutes)
            if b is not None:
                counts[b][e.mnemonic] += 1
        # 1. bursts per event type
        for mnem in {e.mnemonic for e in evs}:
            series = np.array([counts[b][mnem] for b in buckets_all], dtype=float)
            med = float(np.median(series))
            mad = float(np.median(np.abs(series - med))) * 1.4826 + 0.5
            for i, b in enumerate(buckets_all):
                c = series[i]
                if c >= min_burst and (c - med) / mad > z_threshold:
                    sample = [
                        e
                        for e in evs
                        if e.mnemonic == mnem and _bucket(e.timestamp, window_minutes) == b
                    ]
                    incidents.append(
                        _incident(
                            device,
                            "burst",
                            b,
                            window_minutes,
                            mnem,
                            int(c),
                            sample,
                            f"{mnem} x{int(c)} in {window_minutes} min (normal {med:.0f}/window)",
                            (c - med) / mad,
                        )
                    )
        # 3. rare / critical
        seen_early = {
            e.mnemonic
            for e in evs
            if (_bucket(e.timestamp, window_minutes) or 0) < buckets_all[len(buckets_all) * 2 // 3]
        }
        for e in evs:
            if e.severity <= 2:
                incidents.append(
                    _incident(
                        device,
                        "critical",
                        _bucket(e.timestamp, window_minutes) or 0,
                        window_minutes,
                        e.mnemonic,
                        1,
                        [e],
                        f"severity {e.severity} ({SEVERITY_NAMES[e.severity]}): {e.mnemonic}",
                        10 - e.severity,
                    )
                )
        for mnem in {e.mnemonic for e in evs} - seen_early:
            sample = [e for e in evs if e.mnemonic == mnem]
            # A one-off warning is noise; repeated, or error-level, is news.
            if all(s.severity > 2 for s in sample) and (
                len(sample) >= 3 or min(s.severity for s in sample) <= 3
            ):
                incidents.append(
                    _incident(
                        device,
                        "new_event",
                        _bucket(sample[0].timestamp, window_minutes) or 0,
                        window_minutes,
                        mnem,
                        len(sample),
                        sample,
                        f"first {mnem} seen on this device in the analysed period",
                        3.0,
                    )
                )
        # features for 2.
        for b in buckets_all:
            c = counts.get(b, Counter())
            sev: Counter[int] = Counter()
            for e in evs:
                if _bucket(e.timestamp, window_minutes) == b:
                    sev[min(e.severity, 5)] += 1
            rows.append(
                [float(np.log1p(c[m])) for m in top_types]
                + [float(np.log1p(sev[s])) for s in range(6)]
            )
            row_keys.append((device, b))

    # 2. Isolation Forest over windows, scaled per device
    X = np.array(rows)
    devs = np.array([k[0] for k in row_keys])
    for d in set(devs):
        m = devs == d
        X[m] = X[m] - np.median(X[m], axis=0)
    iso_flags = np.zeros(len(rows), dtype=bool)
    if len(rows) >= 20:
        iso = IsolationForest(contamination=contamination, random_state=0, n_estimators=200).fit(X)
        scores = -iso.score_samples(X)
        iso_flags = iso.predict(X) == -1
        # Only keep windows with real volume above the device's normal.
        for i, (device, b) in enumerate(row_keys):
            if not iso_flags[i]:
                continue
            evs = [e for e in by_dev[device] if _bucket(e.timestamp, window_minutes) == b]
            if len(evs) < min_burst:
                continue
            top = Counter(e.mnemonic for e in evs).most_common(1)[0]
            if any(x["device"] == device and x["window_start_bucket"] == b for x in incidents):
                continue
            incidents.append(
                _incident(
                    device,
                    "unusual_mix",
                    b,
                    window_minutes,
                    top[0],
                    len(evs),
                    evs,
                    f"unusual mix of {len(evs)} events (mostly {top[0]})",
                    float(scores[i]) * 5,
                )
            )

    incidents.sort(key=lambda x: -x["score"])
    summary = []
    for device, evs in sorted(by_dev.items()):
        inc = [x for x in incidents if x["device"] == device]
        sev_counts = Counter(SEVERITY_NAMES[min(e.severity, 7)] for e in evs)
        summary.append(
            {
                "device": device,
                "events": len(evs),
                "by_severity": dict(sev_counts),
                "top_events": Counter(e.mnemonic for e in evs).most_common(5),
                "incidents": len(inc),
                "status": "anomalous" if inc else "normal",
            }
        )
    return {
        "events": len(events),
        "windows": len(buckets_all),
        "window_minutes": window_minutes,
        "devices": summary,
        "incidents": incidents,
        "timeline": _timeline(by_dev, buckets_all, window_minutes),
    }


def _incident(
    device: str,
    kind: str,
    bucket: int,
    window_min: int,
    mnem: str,
    count: int,
    sample: list[LogEvent],
    why: str,
    score: float,
) -> dict[str, Any]:
    start = datetime.fromtimestamp(bucket * window_min * 60).astimezone().isoformat()
    interfaces = sorted({e.interface for e in sample if e.interface})
    return {
        "device": device,
        "kind": kind,
        "event": mnem,
        "count": count,
        "window_start": start,
        "window_start_bucket": bucket,
        "interfaces": interfaces,
        "explanation": why,
        "score": round(float(score), 2),
        "samples": [f"{e.timestamp} {e.mnemonic}: {e.message}" for e in sample[:5]],
    }


def _timeline(
    by_dev: dict[str, list[LogEvent]], buckets: list[int], window_min: int
) -> dict[str, Any]:
    return {
        "windows": [
            datetime.fromtimestamp(b * window_min * 60).astimezone().isoformat() for b in buckets
        ],
        "devices": {
            d: [sum(1 for e in evs if _bucket(e.timestamp, window_min) == b) for b in buckets]
            for d, evs in by_dev.items()
        },
    }


def netbox_journal_text(device: str, incidents: list[dict[str, Any]]) -> tuple[str, str]:
    """(kind, markdown) for a NetBox journal entry summarising a device's incidents."""
    worst = min(
        (i for i in incidents),
        key=lambda i: {"critical": 0, "burst": 1, "unusual_mix": 2, "new_event": 3}[i["kind"]],
    )
    kind = "danger" if worst["kind"] == "critical" else "warning"
    lines = [f"**VireoNet log analytics** — {len(incidents)} anomaly(ies) on {device}"]
    for i in incidents[:10]:
        ifs = f" ({', '.join(i['interfaces'])})" if i["interfaces"] else ""
        lines.append(f"- {i['kind']}: {i['explanation']}{ifs} at {i['window_start'][:16]}")
    return kind, "\n".join(lines)
