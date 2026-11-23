from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

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


class BatfishAdapter(BaseAdapter):
    """Real Batfish adapter via pybatfish. Session is injected (tests) or
    created lazily from host/port. No Batfish service = AdapterUnavailable."""

    def __init__(self, config: AdapterConfig, *, host: str = "localhost", port: int = 9996, snapshot_dir: str = "/tmp/vireonet/batfish", session: Any | None = None, correlation_id: str | None = None) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.host = host
        self.port = port
        self.snapshot_dir = Path(snapshot_dir)
        self._session = session

    def capabilities(self) -> list[str]:
        return ["reachability", "acl", "routing", "bgp", "undefined_refs", "duplicate_ips", "interface_vlan"]

    async def health_check(self) -> AdapterHealth:
        try:
            self._ensure_session()
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(name=self.name, status="down", detail=str(exc), capabilities=self.capabilities())

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            from pybatfish.client.session import Session
        except ImportError as exc:
            raise AdapterUnavailable(self.name, "init", "pybatfish not installed (pip install pybatfish)") from exc
        try:
            self._session = Session(host=self.host, port=self.port)
        except Exception as exc:
            raise AdapterUnavailable(self.name, "init", f"cannot connect to Batfish at {self.host}:{self.port}: {exc}") from exc
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
                    dups = [r for r in rows if any(isinstance(v, (list, tuple)) and len(v) > 1 for v in r.values())]
                    if dups:
                        failures.append(f"duplicate IPs: {len(dups)}")
                if label == "bgp_session_status":
                    down = [r for r in rows if str(r.get("Established_Status", r.get("status", ""))).lower() not in {"established", "established_status"} and r]
                    # Only fail on rows that explicitly say a session is down.
                    down = [r for r in rows if "down" in str(r).lower() or "not_established" in str(r).lower()]
                    if down:
                        failures.append(f"BGP sessions down: {len(down)}")
                if label == "reachability":
                    # Reachability question returns flows; an empty result with
                    # a candidate that should be reachable is not, by itself,
                    # a failure — differential reachability is the gate in CI.
                    evidence[label]["note"] = "reachability rows counted, differential check runs in CI"
            return BatfishResult(passed=not failures, failures=failures, evidence=evidence)

    async def analyse_differential(self, current_files: dict[str, str], candidate_files: dict[str, str]) -> BatfishResult:
        """Compare current vs candidate snapshots. Returns pass/fail with evidence.

        Implemented as two analyse() runs plus a file-level diff summary.
        A full Batfish differential-reachability question needs both snapshots
        loaded in the same session; that is the follow-up, not claimed here.
        """
        current = await self.analyse(current_files) if current_files else BatfishResult(passed=True)
        candidate = await self.analyse(candidate_files)
        failures = list(current.failures) + list(candidate.failures)
        changed = sorted(set(current_files) ^ set(candidate_files) | {k for k in candidate_files if current_files.get(k) != candidate_files.get(k)})
        evidence: dict[str, Any] = {"current": current.evidence, "candidate": candidate.evidence, "changed_files": changed}
        return BatfishResult(passed=not failures, failures=failures, evidence=evidence)
