from __future__ import annotations

import difflib
import tempfile
from pathlib import Path
from typing import Any, cast

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterUnavailable, AdapterValidationError
from vireonet.adapters.protocols import BatfishResult

# pybatfish question names (session.q.<name>). Change only this dict if a
# Batfish release renames a question.
BATFISH_QUESTIONS = {
    "undefined_references": "undefinedReferences",
    "duplicate_ips": "ipOwners",
    "bgp_session_status": "bgpSessionStatus",
    "reachability": "reachability",
    "routes": "routes",
}
DIFFERENTIAL_REACHABILITY = "differentialReachability"

# Single-snapshot gating questions, also run on the candidate in the
# differential analysis. Duplicate IPs use ipOwners(duplicatesOnly=True):
# every returned row is a duplicate.
_GATE_KWARGS: dict[str, dict[str, Any]] = {"duplicate_ips": {"duplicatesOnly": True}}

# Batfish flow dispositions that mean the flow got where it was going.
# https://batfish.readthedocs.io/en/latest/notebooks/forwarding.html
_SUCCESS_DISPOSITIONS = frozenset({"ACCEPTED", "DELIVERED_TO_SUBNET", "EXITS_NETWORK"})


def _write_snapshot(parent: Path, name: str, files: dict[str, str]) -> Path:
    """Write one snapshot's device configs under parent/name/configs/.

    Batfish reads snapshots from disk and expects device configs in the
    snapshot's ``configs/`` subdirectory.
    https://batfish.readthedocs.io/en/latest/notebooks/interacting.html#Packaging-snapshot-data
    """
    snap_dir = parent / name
    configs = snap_dir / "configs"
    configs.mkdir(parents=True)
    for file_name, content in files.items():
        rel = Path(file_name)
        if rel.parts and rel.parts[0] == "configs":
            rel = Path(*rel.parts[1:])
        target = configs / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return snap_dir


def _dispositions(traces: Any) -> set[str]:
    """Dispositions of a Snapshot_Traces / Reference_Traces cell.

    pybatfish returns a list of Trace objects (``.disposition``); dicts and
    plain strings are accepted too so serialised answers parse the same way.
    """
    if traces is None:
        return set()
    if isinstance(traces, (str, dict)) or not isinstance(traces, (list, tuple, set)):
        traces = [traces]
    out: set[str] = set()
    for trace in traces:
        if isinstance(trace, str):
            value: Any = trace
        elif isinstance(trace, dict):
            value = trace.get("disposition")
        else:
            value = getattr(trace, "disposition", None)
        if value:
            out.add(str(value).upper())
    return out


def _flow_succeeds(traces: Any) -> bool | None:
    disp = _dispositions(traces)
    if not disp:
        return None
    return bool(disp & _SUCCESS_DISPOSITIONS)


def _classify(rows: list[dict[str, Any]]) -> dict[str, object]:
    """Classify differentialReachability rows as lost / gained / changed.

    Each row is a flow whose behaviour differs between the candidate
    (``Snapshot_Traces``) and the current/reference (``Reference_Traces``)
    snapshot. Lost = succeeded in reference, fails in candidate; gained = the
    opposite; anything else (e.g. different path, same outcome) = changed.
    """
    lost = gained = changed = 0
    lost_sample: list[dict[str, Any]] = []
    gained_sample: list[dict[str, Any]] = []
    for row in rows:
        snap = _flow_succeeds(row.get("Snapshot_Traces"))
        ref = _flow_succeeds(row.get("Reference_Traces"))
        if ref is True and snap is False:
            lost += 1
            if len(lost_sample) < 5:
                lost_sample.append(row)
        elif ref is False and snap is True:
            gained += 1
            if len(gained_sample) < 5:
                gained_sample.append(row)
        else:
            changed += 1
    return {
        "lost": lost,
        "gained": gained,
        "changed": changed,
        "total": len(rows),
        "lost_sample": lost_sample,
        "gained_sample": gained_sample,
    }


def _bgp_down(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """bgpSessionStatus rows whose Established_Status is not ESTABLISHED."""
    return [
        r
        for r in rows
        if r.get("Established_Status") is not None
        and str(r.get("Established_Status")).upper() != "ESTABLISHED"
    ]


class BatfishAdapter(BaseAdapter):
    """Real Batfish adapter via pybatfish. Session is injected (tests) or
    created lazily from host/port. No Batfish service = AdapterUnavailable."""

    def __init__(
        self,
        config: AdapterConfig,
        *,
        host: str = "localhost",
        port: int = 9996,
        snapshot_dir: str = "/tmp/vireonet/batfish",
        session: Any | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.host = host
        self.port = port
        self.snapshot_dir = Path(snapshot_dir)
        self._session = session

    def capabilities(self) -> list[str]:
        return [
            "reachability",
            "acl",
            "routing",
            "bgp",
            "undefined_refs",
            "duplicate_ips",
            "interface_vlan",
            "differential",
        ]

    async def health_check(self) -> AdapterHealth:
        try:
            self._ensure_session()
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            from pybatfish.client.session import Session
        except ImportError as exc:
            raise AdapterUnavailable(
                self.name, "init", "pybatfish not installed (pip install pybatfish)"
            ) from exc
        try:
            self._session = Session(host=self.host, port=self.port)
        except Exception as exc:
            raise AdapterUnavailable(
                self.name, "init", f"cannot connect to Batfish at {self.host}:{self.port}: {exc}"
            ) from exc
        return self._session

    def _question_rows(
        self,
        session: Any,
        question: str,
        *,
        snapshot: str = "candidate",
        reference_snapshot: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one pybatfish question and return rows as dicts.

        ``session.q.<question>(**params).answer(snapshot=...)`` returns an
        answer whose .frame() is a pandas DataFrame; rows are returned raw.
        With ``reference_snapshot`` the question is answered differentially
        (rows carry Snapshot_* / Reference_* columns).
        """
        answer_kwargs: dict[str, Any] = {"snapshot": snapshot}
        if reference_snapshot is not None:
            answer_kwargs["reference_snapshot"] = reference_snapshot
        try:
            q = getattr(session.q, question)
            answer = q(**(params or {})).answer(**answer_kwargs)
            frame = answer.frame()
        except Exception as exc:
            raise AdapterUnavailable(self.name, question, str(exc)) from exc
        if frame is None:
            return []
        try:
            return frame.to_dict(orient="records")  # type: ignore[no-any-return]
        except Exception:
            return []

    async def analyse(self, files: dict[str, str]) -> BatfishResult:
        if not files:
            raise AdapterValidationError(self.name, "analyse", "no config files to analyse")
        session = self._ensure_session()
        # Write the candidate snapshot to disk — Batfish reads from files, not memory.
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            snap_dir = _write_snapshot(Path(tmp), "candidate", files)
            try:
                session.init_snapshot(str(snap_dir), name="candidate", overwrite=True)
            except Exception as exc:
                raise AdapterUnavailable(self.name, "init_snapshot", str(exc)) from exc
            failures, evidence = self._gate_checks(session, "candidate")
            evidence["reachability"] = {
                "note": "single snapshot: no reachability gate; differentialReachability runs "
                "when a current snapshot is available"
            }
            return BatfishResult(passed=not failures, failures=failures, evidence=evidence)

    def _gate_checks(self, session: Any, snapshot: str) -> tuple[list[str], dict[str, Any]]:
        """Undefined references, duplicate IPs and BGP sessions on one snapshot."""
        failures: list[str] = []
        evidence: dict[str, Any] = {}
        for label in ("undefined_references", "duplicate_ips", "bgp_session_status"):
            rows = self._question_rows(
                session,
                BATFISH_QUESTIONS[label],
                snapshot=snapshot,
                params=_GATE_KWARGS.get(label),
            )
            evidence[label] = {"rows": len(rows)}
            if label == "undefined_references" and rows:
                failures.append(f"undefined references: {len(rows)}")
                evidence[label]["sample"] = rows[:3]
            if label == "duplicate_ips" and rows:
                # ipOwners(duplicatesOnly=True): every row is a duplicate IP.
                failures.append(f"duplicate IPs: {len(rows)}")
                evidence[label]["sample"] = rows[:3]
            if label == "bgp_session_status":
                down = _bgp_down(rows)
                if down:
                    failures.append(f"BGP sessions down: {len(down)}")
                    evidence[label]["sample"] = down[:3]
        return failures, evidence

    @staticmethod
    def _acl_impact(
        current_files: dict[str, str],
        candidate_files: dict[str, str],
        reachability: dict[str, object],
    ) -> list[dict[str, object]]:
        """Changed access-list lines per changed file, co-occurring with the
        reachability differential. This is co-occurrence, not a Batfish proof
        that the ACL line caused the flow change — see the attribution."""
        flows_lost = cast(int, reachability.get("lost", 0))
        flows_gained = cast(int, reachability.get("gained", 0))
        changed = sorted(
            set(current_files) ^ set(candidate_files)
            | {k for k in candidate_files if current_files.get(k) != candidate_files.get(k)}
        )
        impact: list[dict[str, object]] = []
        for name in changed:
            old = current_files.get(name, "").splitlines()
            new = candidate_files.get(name, "").splitlines()
            for line in difflib.unified_diff(old, new, lineterm=""):
                if line.startswith(("---", "+++")):
                    continue
                if line[:1] in {"+", "-"} and "access-list" in line:
                    impact.append(
                        {
                            "file": name,
                            "line": line,
                            "flows_lost": flows_lost,
                            "flows_gained": flows_gained,
                            "attribution": "co-occurrence with reachability differential, not a Batfish proof",
                        }
                    )
        return impact

    async def analyse_differential(
        self, current_files: dict[str, str], candidate_files: dict[str, str]
    ) -> BatfishResult:
        """Compare current vs candidate snapshots in the SAME Batfish session.

        Both snapshots are loaded (current, then candidate). The gating checks
        run on the candidate; reachability and routes are answered
        differentially (candidate vs reference=current). Without a current snapshot
        there is nothing to compare against, so fall back to a single-snapshot
        analyse() of the candidate and say so in the differential dict.
        """
        if not current_files:
            result = await self.analyse(candidate_files)
            return BatfishResult(
                passed=result.passed,
                failures=result.failures,
                evidence=result.evidence,
                differential={
                    "differential": False,
                    "note": "no current snapshot; differential skipped",
                },
            )
        if not candidate_files:
            raise AdapterValidationError(
                self.name, "analyse_differential", "no config files to analyse"
            )
        session = self._ensure_session()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            base = Path(tmp)
            current_dir = _write_snapshot(base, "current", current_files)
            candidate_dir = _write_snapshot(base, "candidate", candidate_files)
            try:
                session.init_snapshot(str(current_dir), name="current", overwrite=True)
                session.init_snapshot(str(candidate_dir), name="candidate", overwrite=True)
            except Exception as exc:
                raise AdapterUnavailable(self.name, "init_snapshot", str(exc)) from exc
            # Health checks on the candidate itself, then differential
            # reachability: answer(snapshot=candidate, reference_snapshot=current).
            failures, evidence = self._gate_checks(session, "candidate")
            reachability_rows = self._question_rows(
                session,
                DIFFERENTIAL_REACHABILITY,
                snapshot="candidate",
                reference_snapshot="current",
            )
            evidence["reachability"] = {"rows": len(reachability_rows)}
            route_rows = self._question_rows(
                session,
                BATFISH_QUESTIONS["routes"],
                snapshot="candidate",
                reference_snapshot="current",
            )
            evidence["routes"] = {"rows": len(route_rows)}
            reachability = _classify(reachability_rows)
            if cast(int, reachability["lost"]) > 0:
                failures.append(f"reachability lost: {reachability['lost']} flow(s)")
            changed = sorted(
                set(current_files) ^ set(candidate_files)
                | {k for k in candidate_files if current_files.get(k) != candidate_files.get(k)}
            )
            diff: dict[str, object] = {
                "differential": True,
                "reachability": reachability,
                "changed_files": changed,
                "acl_impact": self._acl_impact(current_files, candidate_files, reachability),
            }
            return BatfishResult(
                passed=not failures, failures=failures, evidence=evidence, differential=diff
            )
