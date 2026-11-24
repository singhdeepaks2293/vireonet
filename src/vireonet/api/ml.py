"""ML tab API. Models are trained on request and are advisory only: nothing
here changes a workflow, a wave size or a device."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from vireonet.api.security import require_role
from vireonet.ml.datasets import CHANGE_TYPES, load_changes, load_traffic
from vireonet.ml.rl import WAVE_SIZES, recommend_wave, train_wave_policy
from vireonet.ml.supervised import (
    CLASSIFIERS,
    REGRESSORS,
    classify_change_risk,
    predict_change_risk,
    regress_change_duration,
    regress_traffic,
)
from vireonet.ml.unsupervised import cluster_interfaces, detect_anomalies
from vireonet.topology.service import build_topology

router = APIRouter(prefix="/ml")
MAX_INTERFACES = 60


async def _interfaces(request: Request) -> list[tuple[str, str]]:
    topo = await build_topology(
        netbox=request.app.state.adapters["netbox"],
        telemetry=None,
        demo=request.app.state.settings.adapter_mode == "fake",
    )
    seen: list[tuple[str, str]] = []
    for link in topo["links"]:
        for pair in (
            (link["source"], link["source_interface"]),
            (link["target"], link["target_interface"]),
        ):
            if pair not in seen:
                seen.append(pair)
    return seen[:MAX_INTERFACES]


async def _traffic(
    request: Request, minutes: int, step_seconds: int
) -> tuple[list[Any], list[str], str]:
    telemetry = getattr(request.app.state, "telemetry", None)
    if telemetry is None:
        raise HTTPException(
            status_code=503, detail="telemetry is disabled; set VIREONET_TELEMETRY_BACKEND"
        )
    interfaces = await _interfaces(request)
    if not interfaces:
        raise HTTPException(status_code=409, detail="no cabled interfaces in NetBox to learn from")
    series, notes = await load_traffic(
        telemetry, interfaces, minutes=minutes, step_seconds=step_seconds
    )
    if not series:
        raise HTTPException(
            status_code=409, detail={"message": "no interface history returned", "notes": notes}
        )
    return series, notes, str(telemetry.name)


def _bad(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/overview")
async def overview(_role: Any = Depends(require_role("viewer"))) -> dict[str, Any]:
    return {
        "advisory": True,
        "unsupervised": {
            "anomaly": ["isolation_forest", "local_outlier_factor", "zscore"],
            "clustering": ["kmeans"],
        },
        "classification": sorted(CLASSIFIERS),
        "regression": sorted(REGRESSORS),
        "reinforcement_learning": ["q_learning"],
        "change_types": CHANGE_TYPES,
    }


class AnomalyRequest(BaseModel):
    algorithm: Literal["isolation_forest", "local_outlier_factor", "zscore"] = "isolation_forest"
    hours: int = Field(default=24, ge=1, le=24 * 14)
    step_seconds: int = Field(default=600, ge=60, le=3600)
    contamination: float = Field(default=0.005, gt=0, le=0.2)


@router.post("/anomalies")
async def anomalies(
    payload: AnomalyRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    series, notes, source = await _traffic(request, payload.hours * 60, payload.step_seconds)
    result = await asyncio.to_thread(
        detect_anomalies, series, payload.algorithm, payload.contamination
    )
    return {**result, "source": source, "notes": notes}


class ClusterRequest(BaseModel):
    k: int | None = Field(default=None, ge=2, le=10)
    hours: int = Field(default=24, ge=1, le=24 * 14)


@router.post("/clusters")
async def clusters(
    payload: ClusterRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    series, notes, source = await _traffic(request, payload.hours * 60, 600)
    try:
        result = await asyncio.to_thread(cluster_interfaces, series, payload.k)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {**result, "source": source, "notes": notes}


class ClassifyRequest(BaseModel):
    algorithms: list[str] | None = None


@router.post("/classification")
async def classification(
    payload: ClassifyRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    data = await load_changes(getattr(request.app.state, "session_factory", None))
    try:
        result = await asyncio.to_thread(classify_change_risk, data, payload.algorithms)
    except ValueError as exc:
        raise _bad(exc) from exc
    request.app.state.ml_risk_model = (result.pop("_best_model"), result["best"], result["source"])
    return result


class RiskRequest(BaseModel):
    change_type: str
    devices: int = Field(ge=1, le=5000)
    platforms: int = Field(default=1, ge=1, le=20)
    hour: int = Field(default=10, ge=0, le=23)
    weekend: bool = False
    emergency: bool = False
    prior_failure_rate: float = Field(default=0.0, ge=0, le=1)


@router.post("/risk")
async def risk(
    payload: RiskRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    """Failure probability for a proposed change, plus the RL wave-size advice."""
    if payload.change_type not in CHANGE_TYPES:
        raise HTTPException(status_code=422, detail=f"change_type must be one of {CHANGE_TYPES}")
    trained = getattr(request.app.state, "ml_risk_model", None)
    if trained is None:
        data = await load_changes(getattr(request.app.state, "session_factory", None))
        result = await asyncio.to_thread(classify_change_risk, data, None)
        trained = (result["_best_model"], result["best"], result["source"])
        request.app.state.ml_risk_model = trained
    model, algorithm, source = trained
    p = predict_change_risk(model, **payload.model_dump())
    policy: dict[str, Any] | None = getattr(request.app.state, "ml_wave_policy", None)
    if policy is None:
        policy = await asyncio.to_thread(train_wave_policy)
        request.app.state.ml_wave_policy = policy
    return {
        "failure_probability": round(p, 4),
        "model": algorithm,
        "source": source,
        "recommended_wave_size": recommend_wave(policy, payload.devices, p),
        "advisory": True,
    }


class RegressionRequest(BaseModel):
    target: Literal["change_duration", "traffic_forecast"] = "change_duration"
    algorithms: list[str] | None = None
    horizon_steps: int = Field(default=6, ge=1, le=72)
    hours: int = Field(default=48, ge=6, le=24 * 14)


@router.post("/regression")
async def regression(
    payload: RegressionRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        if payload.target == "change_duration":
            data = await load_changes(getattr(request.app.state, "session_factory", None))
            return await asyncio.to_thread(regress_change_duration, data, payload.algorithms)
        series, notes, source = await _traffic(request, payload.hours * 60, 600)
        result = await asyncio.to_thread(
            regress_traffic, series, payload.horizon_steps, payload.algorithms
        )
        return {**result, "source": source, "notes": notes}
    except ValueError as exc:
        raise _bad(exc) from exc


class RLRequest(BaseModel):
    episodes: int = Field(default=60000, ge=1000, le=300000)
    blast_weight: float = Field(default=4.0, ge=0, le=50)


@router.post("/reinforcement")
async def reinforcement(
    payload: RLRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        train_wave_policy, payload.episodes, 0, 0.1, 0.15, payload.blast_weight
    )
    request.app.state.ml_wave_policy = result
    return {k: v for k, v in result.items() if k != "_q"} | {
        "advisory": True,
        "wave_sizes": WAVE_SIZES,
    }


class LogAnomalyRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=24 * 7)
    window_minutes: int = Field(default=30, ge=5, le=240)
    push_to_netbox: bool = False


@router.post("/log-anomalies")
async def log_anomalies(
    payload: LogAnomalyRequest, request: Request, user: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    """`show logging` from every device -> TextFSM -> anomaly detection.

    With push_to_netbox (operator+), each anomalous device gets one NetBox
    journal entry summarising its anomalies; raw logs never go to NetBox.
    """
    from vireonet.iam.permissions import can
    from vireonet.logs.analytics import analyse, netbox_journal_text
    from vireonet.logs.collector import collect
    from vireonet.rendering.platforms import normalise_platform
    from vireonet.topology.demo import demo_interfaces

    if payload.push_to_netbox and not can(getattr(user, "role", "viewer"), "operator"):
        raise HTTPException(status_code=403, detail="operator role required to write to NetBox")
    settings = request.app.state.settings
    fake = settings.adapter_mode == "fake"
    topo = await build_topology(
        netbox=request.app.state.adapters["netbox"], telemetry=None, demo=fake
    )
    devices = [
        (
            n["name"],
            normalise_platform(n.get("platform", "")),
            demo_interfaces(n["name"]) if fake else [],
        )
        for n in topo["nodes"]
    ][:MAX_INTERFACES]
    events, stats = await collect(
        devices,
        fake=fake,
        engine=getattr(request.app.state, "execution_engine", None),
        hours=payload.hours,
    )
    result = await asyncio.to_thread(analyse, events, window_minutes=payload.window_minutes)
    result["collection"] = stats
    result["source"] = "fake syslog" if fake else "show logging via AWX run-commands"
    pushed: list[dict[str, Any]] = []
    if payload.push_to_netbox:
        netbox = request.app.state.adapters["netbox"]
        by_dev: dict[str, list[dict[str, Any]]] = {}
        for inc in result["incidents"]:
            by_dev.setdefault(inc["device"], []).append(inc)
        for device, incs in by_dev.items():
            kind, text = netbox_journal_text(device, incs)
            try:
                entry = await netbox.add_journal_entry(device, kind, text)
                pushed.append({"device": device, "kind": kind, "journal_entry": str(entry)})
            except Exception as exc:  # noqa: BLE001 — report per device
                pushed.append({"device": device, "error": str(exc)})
    result["netbox"] = pushed
    return result


# --- Neural networks (Intelligence tab) ------------------------------------------------------


async def _log_events(request: Request, hours: int) -> tuple[list[Any], str]:
    from vireonet.logs.collector import collect
    from vireonet.rendering.platforms import normalise_platform
    from vireonet.topology.demo import demo_interfaces

    fake = request.app.state.settings.adapter_mode == "fake"
    topo = await build_topology(
        netbox=request.app.state.adapters["netbox"], telemetry=None, demo=fake
    )
    devices = [
        (
            n["name"],
            normalise_platform(n.get("platform", "")),
            demo_interfaces(n["name"]) if fake else [],
        )
        for n in topo["nodes"]
    ][:MAX_INTERFACES]
    events, _stats = await collect(
        devices, fake=fake, engine=getattr(request.app.state, "execution_engine", None), hours=hours
    )
    return events, "fake syslog" if fake else "show logging via AWX run-commands"


class NeuralLogRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=24 * 7)
    window: int = Field(default=4, ge=2, le=10)
    top_k: int = Field(default=3, ge=1, le=10)


@router.post("/neural/log-sequences")
async def neural_log_sequences(
    payload: NeuralLogRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.ml.neural import log_sequences

    events, source = await _log_events(request, payload.hours)
    try:
        result = await asyncio.to_thread(
            log_sequences, events, window=payload.window, top_k=payload.top_k
        )
    except ValueError as exc:
        raise _bad(exc) from exc
    return {**result, "source": source, "advisory": True}


class NeuralForecastRequest(BaseModel):
    hours: int = Field(default=24, ge=4, le=24 * 14)
    step_seconds: int = Field(default=600, ge=60, le=3600)
    horizon: int = Field(default=18, ge=1, le=144)
    saturation: float = Field(default=0.8, gt=0, le=1.0)


@router.post("/neural/forecast")
async def neural_forecast(
    payload: NeuralForecastRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.ml.neural import forecast

    series, notes, source = await _traffic(request, payload.hours * 60, payload.step_seconds)
    try:
        result = await asyncio.to_thread(
            forecast,
            series,
            horizon=payload.horizon,
            saturation=payload.saturation,
            step_minutes=payload.step_seconds // 60,
        )
    except ValueError as exc:
        raise _bad(exc) from exc
    return {**result, "source": source, "notes": notes, "advisory": True}


class NeuralRiskRequest(BaseModel):
    change_type: str = "vlan_add"
    devices: int = Field(default=1, ge=1, le=500)
    platforms: int = Field(default=1, ge=1, le=20)
    hour: int = Field(default=2, ge=0, le=23)
    weekend: bool = False
    emergency: bool = False
    prior_failure_rate: float = Field(default=0.05, ge=0, le=1)


@router.post("/neural/change-risk")
async def neural_change_risk(
    payload: NeuralRiskRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.ml.neural import change_risk, train_change_risk

    dataset = await load_changes(getattr(request.app.state, "session_factory", None))
    if dataset.source == "synthetic" and dataset.rows < 3000:
        from vireonet.ml.datasets import synthetic_changes

        dataset = synthetic_changes(3000)
    try:
        trained = await asyncio.to_thread(train_change_risk, dataset)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {
        **change_risk(trained, **payload.model_dump()),
        "importances": trained["importances"],
        "advisory": True,
    }


class NeuralRootCauseRequest(BaseModel):
    alarms: dict[str, float] = Field(
        default_factory=dict, description="device -> alarm strength 0..1"
    )


@router.post("/neural/root-cause")
async def neural_root_cause(
    payload: NeuralRootCauseRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.ml.neural import root_cause

    fake = request.app.state.settings.adapter_mode == "fake"
    topo = await build_topology(
        netbox=request.app.state.adapters["netbox"], telemetry=None, demo=fake
    )
    nodes = [n["name"] for n in topo["nodes"]]
    links = [(str(link.get("source")), str(link.get("target"))) for link in topo.get("links", [])]
    try:
        result = await asyncio.to_thread(root_cause, nodes, links, payload.alarms)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {**result, "advisory": True}


class NeuralTicketRequest(BaseModel):
    text: str = Field(min_length=3, max_length=5000)


@router.post("/neural/ticket")
async def neural_ticket(
    payload: NeuralTicketRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.ml.neural import ticket_text

    fake = request.app.state.settings.adapter_mode == "fake"
    topo = await build_topology(
        netbox=request.app.state.adapters["netbox"], telemetry=None, demo=fake
    )
    devices = [n["name"] for n in topo["nodes"]]
    return {**(await asyncio.to_thread(ticket_text, payload.text, devices)), "advisory": True}
