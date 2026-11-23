from __future__ import annotations

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter
from vireonet.checks.base import CheckContext, CheckEngine
from vireonet.checks.plugins import (
    BackupTakenCheck,
    ConfigMatchesIntentCheck,
    ReachabilityCheck,
    post_checks_for,
    pre_checks_for,
)
from vireonet.intent import parse_intent

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}


def _ctx(intent, **kw):  # type: ignore[no-untyped-def]
    return CheckContext(intent=intent, ticket_number="RITM1", netbox=FakeNetBoxAdapter(), awx=FakeAwxAdapter(), **kw)


async def test_reachability_pass_and_fail() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    assert (await ReachabilityCheck().run(_ctx(intent))).ok
    bad = parse_intent({**BASE, "devices": ["ghost"], "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    assert not (await ReachabilityCheck().run(_ctx(bad))).ok


async def test_backup_check_requires_backup() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    assert not (await BackupTakenCheck().run(_ctx(intent))).ok
    assert (await BackupTakenCheck().run(_ctx(intent, backups={"leaf-01": 42}))).ok


async def test_engine_all_pass_and_fail_fast() -> None:
    # VLAN 100 is the one the fake NetBox seeds, so the post-check can find it.
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    results = await CheckEngine().run(pre_checks_for(intent), _ctx(intent, backups={"leaf-01": 1}, baseline={"x": 1}))
    # pre-checks include backup + baseline, both satisfied here, plus reachability/interface.
    assert CheckEngine.all_pass(results), [r.message for r in results]
    post = await CheckEngine().run(post_checks_for(intent), _ctx(intent, backups={"leaf-01": 1}, baseline={"dry_run_job_id": 1}))
    assert CheckEngine.all_pass(post), [r.message for r in post]


async def test_config_matches_intent_vlan() -> None:
    add = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    assert (await ConfigMatchesIntentCheck().run(_ctx(add))).ok
    remove = parse_intent({**BASE, "change_type": "vlan_remove", "vlan_id": 100})
    assert not (await ConfigMatchesIntentCheck().run(_ctx(remove))).ok
