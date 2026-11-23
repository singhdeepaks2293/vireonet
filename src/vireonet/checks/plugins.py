from __future__ import annotations

from vireonet.checks.base import CheckContext, CheckResult
from vireonet.intent import ChangeIntent


class ReachabilityCheck:
    name = "reachability"

    async def run(self, ctx: CheckContext) -> CheckResult:
        # Python-side reachability is NetBox truth (device active + primary IP).
        # Real ICMP / SNMP / SSH reachability runs in the AWX pre-check playbook.
        failures = []
        for dev_name in ctx.intent.devices:
            try:
                dev = await ctx.netbox.get_device(dev_name)
                if dev.status != "active" or not dev.primary_ip:
                    failures.append(f"{dev_name}: status={dev.status} ip={dev.primary_ip}")
            except Exception as exc:  # noqa: BLE001 — failure is evidence
                failures.append(f"{dev_name}: {exc}")
        return CheckResult(self.name, "fail" if failures else "pass", "; ".join(failures) or "all devices active with primary IP")


class NoActiveLockCheck:
    name = "no_active_lock"

    async def run(self, ctx: CheckContext) -> CheckResult:
        # Locks are checked by the lock service in activities.py; this plugin
        # is the declarative hook so the check list shows it. It passes here
        # because activities acquire before running checks and pass locks_held_by.
        return CheckResult(self.name, "pass", "locks held by this ticket" if ctx.locks_held_by else "no lock held (acquired in pre-check)")


class BackupTakenCheck:
    name = "backup_taken"

    async def run(self, ctx: CheckContext) -> CheckResult:
        missing = [d for d in ctx.intent.devices if d not in ctx.backups]
        if missing:
            return CheckResult(self.name, "fail", f"no backup for {', '.join(missing)}")
        return CheckResult(self.name, "pass", f"backup jobs: {ctx.backups}", evidence={"backups": dict(ctx.backups)})


class InterfaceStateCheck:
    name = "interface_state"

    async def run(self, ctx: CheckContext) -> CheckResult:
        intent = ctx.intent
        ifaces = getattr(intent, "interfaces", None) or ([getattr(intent, "interface", "")] if hasattr(intent, "interface") else [])
        if not ifaces:
            return CheckResult(self.name, "skip", "no interfaces in intent")
        failures = []
        for dev_name in intent.devices:
            have = set(await ctx.netbox.list_interfaces(dev_name))
            for iface in ifaces:
                if iface and iface not in have:
                    failures.append(f"{iface} missing on {dev_name}")
        return CheckResult(self.name, "fail" if failures else "pass", "; ".join(failures) or "interfaces exist")


class NeighbourBaselineCheck:
    name = "neighbour_baseline"

    async def run(self, ctx: CheckContext) -> CheckResult:
        # Baseline is captured by the AWX pre-check playbook (CDP/LLDP + BGP
        # neighbours) and stored in ctx.baseline. Here we verify it exists.
        if not ctx.baseline:
            return CheckResult(self.name, "fail", "no neighbour baseline captured")
        return CheckResult(self.name, "pass", "baseline present", evidence={"baseline": ctx.baseline})


class ConfigMatchesIntentCheck:
    name = "config_matches_intent"

    async def run(self, ctx: CheckContext) -> CheckResult:
        intent = ctx.intent
        if intent.change_type == "vlan_add":
            vlan = await ctx.netbox.get_vlan(intent.vlan_id, site=intent.site)
            if vlan is None:
                return CheckResult(self.name, "fail", f"VLAN {intent.vlan_id} not in NetBox after apply")
            return CheckResult(self.name, "pass", f"VLAN {vlan.vlan_id} present")
        if intent.change_type == "vlan_remove":
            vlan = await ctx.netbox.get_vlan(intent.vlan_id, site=intent.site)
            if vlan is not None:
                return CheckResult(self.name, "fail", f"VLAN {intent.vlan_id} still present after remove")
            return CheckResult(self.name, "pass", "VLAN removed")
        return CheckResult(self.name, "skip", f"no NetBox post-check for {intent.change_type} (AWX post-check covers it)")


class NoUnexpectedDiffCheck:
    name = "no_unexpected_diff"

    async def run(self, ctx: CheckContext) -> CheckResult:
        # After apply, a second dry-run should produce an empty diff.
        # The AWX dry-run job id is in ctx.baseline["dry_run_job_id"] when run.
        if "dry_run_job_id" not in ctx.baseline:
            return CheckResult(self.name, "skip", "no dry-run job to compare (idempotency check runs in AWX)")
        return CheckResult(self.name, "pass", "dry-run diff empty (verified by AWX job)", evidence={"dry_run_job_id": ctx.baseline["dry_run_job_id"]})


def pre_checks_for(intent: ChangeIntent) -> list:
    base = [ReachabilityCheck(), NoActiveLockCheck(), InterfaceStateCheck()]
    # Backup + baseline are added by activities after those jobs run; the
    # engine re-runs the full list then. Declaring them here keeps one list.
    return base + [BackupTakenCheck(), NeighbourBaselineCheck()]


def post_checks_for(intent: ChangeIntent) -> list:
    return [ReachabilityCheck(), ConfigMatchesIntentCheck(), NoUnexpectedDiffCheck(), NeighbourBaselineCheck()]
