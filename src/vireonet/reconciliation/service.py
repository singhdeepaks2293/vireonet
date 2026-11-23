"""Reconciliation (E3): after a successful post_check, make Git and NetBox
agree with what the device actually runs.

Flow per device:
1. Store the collected running config as the new baseline and commit it to
   the device repo (netops/config-<device>).
2. Apply each declared change to NetBox — but only if its scope is inside
   the manifest ``writes`` list. Out-of-scope changes are never applied.
3. Verification scan is a placeholder (real verification is D2).
4. Any failure or out-of-scope change leaves the outcome
   ``reconciliation_pending`` and creates a drift record.

Failures are appended, never raised: a half-reconciled change must be
visible as pending, not crash the workflow.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.diff import config_hash, normalize_config
from vireonet.drift.baseline import commit_baseline
from vireonet.execution.types import DeclaredChange, StepResult
from vireonet.reconciliation.drift import DriftStore
from vireonet.services.backup import BEGIN_MARKER, extract_running_config


@dataclass
class ReconcileOutcome:
    status: Literal["reconciled", "reconciliation_pending"]
    git_commit: str | None = None
    netbox_applied: bool = False
    drift_records: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


class ReconciliationService:
    def __init__(
        self,
        config_store: ConfigStore,
        device_repos: DeviceRepoManager,
        netbox: Any,
        drift_store: DriftStore | None,
    ) -> None:
        self._config_store = config_store
        self._device_repos = device_repos
        self._netbox = netbox
        self._drift_store = drift_store

    async def _verify(self, device: str, declared: list[DeclaredChange]) -> list[str]:
        """Verification scan placeholder (D2 builds the real one)."""
        return []

    async def reconcile(
        self,
        *,
        ticket: str,
        device: str,
        job_type: str,
        script_name: str | None,
        script_sha: str | None,
        awx_job_id: int,
        declared: list[DeclaredChange | dict[str, Any]],
        collect_result: StepResult,
        manifest_writes: list[str] | None,
    ) -> ReconcileOutcome:
        changes = [c if isinstance(c, DeclaredChange) else DeclaredChange(**c) for c in declared]
        writes = list(manifest_writes or [])
        failures: list[str] = []
        drift_records: list[str] = []
        git_commit: str | None = None

        # 1. Baseline: normalize the collected running config, store it, commit it.
        raw = (
            collect_result.output.get("running_config")
            or collect_result.output.get("stdout")
            or collect_result.output.get("stdout_tail")
            or ""
        )
        raw = str(raw)
        if BEGIN_MARKER in raw:
            # Fall back to the raw text when the markers do not parse.
            with suppress(ValueError):
                raw = extract_running_config(raw)
        normalized = normalize_config(raw)
        baseline_hash = config_hash(normalized)
        running_hash = baseline_hash
        # 1. Baseline: shared idempotent commit (D1 drift.baseline) —
        # same normalised hash as the last commit = no new Git commit.
        try:
            message = f"{ticket}: reconcile after {job_type} job {awx_job_id}"
            if script_name:
                message += f" {script_name}@{script_sha or ''}"
            git_commit = await commit_baseline(
                device=device,
                running_config=normalized,
                ticket=ticket,
                config_store=self._config_store,
                device_repos=self._device_repos,
                message=message,
            )
        except Exception as exc:  # noqa: BLE001 — recorded, not fatal
            failures.append(f"baseline commit failed: {exc}")

        # 2. NetBox: only declared changes whose scope is in manifest_writes.
        out_of_scope: list[str] = []
        netbox_failures: list[str] = []
        applied_any = False
        for change in changes:
            scope = f"{change.object_type}.{change.field}"
            if scope not in writes and change.object_type not in writes:
                out_of_scope.append(scope)
                continue
            try:
                if change.object_type == "interface":
                    update_interface = getattr(self._netbox, "update_interface", None)
                    if update_interface is None:
                        raise AttributeError("netbox adapter has no update_interface")
                    await update_interface(
                        device, change.identifier, **{change.field: change.new_value}
                    )
                elif change.object_type == "vlan":
                    ensure_vlan = getattr(self._netbox, "ensure_vlan", None)
                    if ensure_vlan is None:
                        raise AttributeError("netbox adapter has no ensure_vlan")
                    await ensure_vlan(vlan_id=int(change.identifier), name=change.new_value or None)
                else:
                    raise ValueError(f"no NetBox mapping for object type {change.object_type}")
                applied_any = True
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal
                netbox_failures.append(f"netbox update for {scope} failed: {exc}")
        failures.extend(netbox_failures)
        if changes:
            try:
                await self._netbox.add_journal_entry(
                    device, "change", f"{ticket}: reconciled after {job_type} job {awx_job_id}"
                )
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal
                failures.append(f"netbox journal entry failed: {exc}")
        netbox_applied = applied_any and not netbox_failures

        # 3. Verification scan (placeholder for now).
        verify_failures = await self._verify(device, changes)
        failures.extend(verify_failures)

        # 4. Drift: out-of-scope declarations or NetBox failures mean the
        # change is pending, not reconciled.
        if out_of_scope or netbox_failures:
            kind = "out_of_scope" if out_of_scope else "netbox_failure"
            diff_text = "\n".join(
                [f"out of scope: {s}" for s in out_of_scope]
                + [f"netbox failure: {f}" for f in netbox_failures]
            )
            create = getattr(self._drift_store, "create", None) if self._drift_store else None
            if create is not None:
                try:
                    drift_id = await create(
                        device,
                        kind,
                        "medium",
                        diff_text,
                        baseline_hash,
                        running_hash,
                        ticket,
                    )
                    drift_records.append(str(drift_id))
                except Exception as exc:  # noqa: BLE001 — recorded, not fatal
                    failures.append(f"drift record creation failed: {exc}")
            else:
                failures.append("drift store has no create method; drift not recorded")

        status: Literal["reconciled", "reconciliation_pending"] = (
            "reconciliation_pending" if failures or drift_records else "reconciled"
        )
        return ReconcileOutcome(
            status=status,
            git_commit=git_commit,
            netbox_applied=netbox_applied,
            drift_records=drift_records,
            failures=failures,
            evidence={
                "baseline_hash": baseline_hash,
                "running_hash": running_hash,
                "out_of_scope": out_of_scope,
                "declared": len(changes),
            },
        )
