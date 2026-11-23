from __future__ import annotations

import difflib
import tempfile
from pathlib import Path
from typing import Any, cast

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterUnavailable, AdapterValidationError
from vireonet.adapters.protocols import BatfishResult

# UNCERTAIN — pybatfish question names / column names vary by Batfish version.
# Change only this dict if your version renames a question.
BATFISH_QUESTIONS = {
    "undefined_references": "undefinedReferences",
    "duplicate_ips": "ipOwners",
    "bgp_session_status": "bgpSessionStatus",
    "reachability": "reachability",
    "routes": "routes",
}

# UNCERTAIN — the differential API surface (answer(snapshot=...,
# reference_snapshot=...) and the Differential/Status column names) varies by
# Batfish/pybatfish version. _differential_rows falls back to
# q(differential=True) when the snapshot kwargs are rejected.
_DIFFERENTIAL_QUESTIONS = {
    "reachability": BATFISH_QUESTIONS["reachability"],
    "routes": BATFISH_QUESTIONS["routes"],
    "bgp_session_status": BATFISH_QUESTIONS["bgp_session_status"],
    "undefined_references": BATFISH_QUESTIONS["undefined_references"],
    "duplicate_ips": BATFISH_QUESTIONS["duplicate_ips"],
}

_LOST_TOKENS = ("decreased", "lost", "denied", "unreachable", "no_route", "not_reachable")
_GAINED_TOKENS = ("increased", "gained", "permitted", "reachable")


def _write_snapshot(parent: Path, name: str, files: dict[str, str]) -> Path:
    """Write one snapshot's config files under parent/name. Batfish reads from files, not memory."""
    snap_dir = parent / name
    snap_dir.mkdir()
    for file_name, content in files.items():
        target = snap_dir / file_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return snap_dir


def _row_status(row: dict[str, Any]) -> str:
    """Differential status of one question row. Column names vary by Batfish
    version, so check the known names in order; unknown rows count as 'changed'."""
    for key in ("Differential", "Status", "FlowDisposition", "status", "disposition"):
        value = row.get(key)
        if value is not None and str(value) != "":
            return str(value).lower()
    return "changed"


def _classify(rows: list[dict[str, Any]]) -> dict[str, object]:
    """Count reachability-differential rows as lost / gained / changed."""
    lost = gained = changed = 0
    lost_sample: list[dict[str, Any]] = []
    gained_sample: list[dict[str, Any]] = []
    for row in rows:
        status = _row_status(row)
        if any(token in status for token in _LOST_TOKENS):
            lost += 1
            if len(lost_sample) < 5:
                lost_sample.append(row)
        elif any(token in status for token in _GAINED_TOKENS):
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

    def _question_rows(self, session: Any, question: str) -> list[dict[str, Any]]:
        """Run one pybatfish question and return rows as dicts.

        pybatfish questions return an answer object whose .frame() is a
        pandas DataFrame. Column names differ by question; we return the
        raw rows so the gate can count / inspect them without inventing columns.
        """
        try:
            q = getattr(session.q, question)
            answer = q().answer()
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
            snap_dir = Path(tmp) / "candidate"
            snap_dir.mkdir()
            for name, content in files.items():
                target = snap_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            try:
                session.init_snapshot(str(snap_dir), name="candidate", overwrite=True)
            except Exception as exc:
                raise AdapterUnavailable(self.name, "init_snapshot", str(exc)) from exc
            failures: list[str] = []
            evidence: dict[str, Any] = {}
            for label, question in BATFISH_QUESTIONS.items():
                rows = self._question_rows(session, question)
                evidence[label] = {"rows": len(rows)}
                if label == "undefined_references" and rows:
                    failures.append(f"undefined references: {len(rows)}")
                    evidence[label]["sample"] = rows[:3]
                if label == "duplicate_ips":
                    # ipOwners rows with >1 owner are duplicates. The exact
                    # column is version-specific; count rows where any value
                    # looks like a list with >1 entry.
                    dups = [
                        r
                        for r in rows
                        if any(isinstance(v, (list, tuple)) and len(v) > 1 for v in r.values())
                    ]
                    if dups:
                        failures.append(f"duplicate IPs: {len(dups)}")
                if label == "bgp_session_status":
                    down = [
                        r
                        for r in rows
                        if str(r.get("Established_Status", r.get("status", ""))).lower()
                        not in {"established", "established_status"}
                        and r
                    ]
                    # Only fail on rows that explicitly say a session is down.
                    down = [
                        r
                        for r in rows
                        if "down" in str(r).lower() or "not_established" in str(r).lower()
                    ]
                    if down:
                        failures.append(f"BGP sessions down: {len(down)}")
                if label == "reachability":
                    # Reachability question returns flows; an empty result with
                    # a candidate that should be reachable is not, by itself,
                    # a failure — differential reachability is the gate in CI.
                    evidence[label]["note"] = (
                        "reachability rows counted, differential check runs in CI"
                    )
            return BatfishResult(passed=not failures, failures=failures, evidence=evidence)

    def _differential_rows(self, session: Any, question: str) -> list[dict[str, Any]]:
        """Run one pybatfish question differentially (candidate vs current snapshot).

        Primary form is answer(snapshot="candidate", reference_snapshot="current").
        Older pybatfish versions reject those kwargs; fall back to
        q(differential=True).answer(). Column names vary by version; rows are
        returned raw, as in _question_rows.
        """
        try:
            q = getattr(session.q, question)
            try:
                answer = q().answer(snapshot="candidate", reference_snapshot="current")
            except TypeError:
                answer = q(differential=True).answer()
            frame = answer.frame()
        except Exception as exc:
            raise AdapterUnavailable(self.name, question, str(exc)) from exc
        if frame is None:
            return []
        try:
            return frame.to_dict(orient="records")  # type: ignore[no-any-return]
        except Exception:
            return []

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

        Both snapshots are loaded (current, then candidate) and each question
        is answered differentially against them. Without a current snapshot
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
            failures: list[str] = []
            evidence: dict[str, Any] = {}
            reachability_rows: list[dict[str, Any]] = []
            for label, question in _DIFFERENTIAL_QUESTIONS.items():
                rows = self._differential_rows(session, question)
                evidence[label] = {"rows": len(rows)}
                if label == "reachability":
                    reachability_rows = rows
                if label == "undefined_references" and rows:
                    failures.append(f"undefined references: {len(rows)}")
                    evidence[label]["sample"] = rows[:3]
                if label == "duplicate_ips":
                    dups = [
                        r
                        for r in rows
                        if any(isinstance(v, (list, tuple)) and len(v) > 1 for v in r.values())
                    ]
                    if dups:
                        failures.append(f"duplicate IPs: {len(dups)}")
                if label == "bgp_session_status":
                    down = [
                        r
                        for r in rows
                        if "down" in str(r).lower() or "not_established" in str(r).lower()
                    ]
                    if down:
                        failures.append(f"BGP sessions down: {len(down)}")
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
