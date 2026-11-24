"""Vendor-neutral rendering: built-ins, vendor templates, safety, wiring."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.protocols import NetBoxDevice
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.intent import parse_intent
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.rendering import Renderer, UnsupportedChange, normalise_platform
from vireonet.rendering.templates import RenderTemplateError, RenderTemplates
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ApplyInput, ChangeActivities, ValidateInput

REPO = Path(__file__).resolve().parents[1]
BASE = {"devices": ["r1"], "site": "dc1", "ticket_number": "RITM42", "requested_by": "alice"}
SHIPPED = RenderTemplates.load(REPO / "policies/render_templates.yaml")


def _i(**kw):  # type: ignore[no-untyped-def]
    return parse_intent({**BASE, **kw})


def _r(intent, platform: str, renderer: Renderer | None = None) -> list[str]:  # type: ignore[no-untyped-def]
    return (renderer or Renderer(SHIPPED)).render(intent, "r1", platform).lines


VLAN = {"change_type": "vlan_add", "vlan_id": 120, "vlan_name": "app", "interfaces": ["port1"]}
ROUTE = {"change_type": "static_route_add", "prefix": "172.16.50.0/24", "next_hop": "10.0.0.1"}
ACL = {
    "change_type": "acl_entry_add",
    "acl_name": "EDGE-IN",
    "action": "permit",
    "protocol": "tcp",
    "source": "10.10.0.0/16",
    "destination": "10.20.5.10/32",
    "destination_port": 443,
}


# --- Platforms ----------------------------------------------------------------


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("cisco-ios", "ios"),
        ("isr", "ios-xe"),
        ("ISR-G2", "ios"),
        ("cisco-nxos", "nxos"),
        ("arista", "eos"),
        ("juniper-junos", "junos"),
        ("fortinet", "fortios"),
        ("palo-alto", "panos"),
        ("nokia", "sros"),
        ("nokia-srlinux", "srlinux"),
        ("extreme", "exos"),
        ("huawei-vrp", "huawei-vrp"),
    ],
)
def test_platform_aliases(slug: str, expected: str) -> None:
    assert normalise_platform(slug) == expected


# --- Built-in renderers: correct vendor syntax ---------------------------------


def test_ios_uses_netmask_and_wildcards_not_cidr() -> None:
    assert _r(_i(**ROUTE), "ios") == ["ip route 172.16.50.0 255.255.255.0 10.0.0.1"]
    acl = _r(_i(**ACL), "ios")
    assert acl[1] == " permit tcp 10.10.0.0 0.0.255.255 host 10.20.5.10 eq 443"


def test_ios_static_route_remove_does_not_crash_without_next_hop() -> None:
    intent = _i(change_type="static_route_remove", prefix="172.16.50.0/24")
    assert _r(intent, "ios") == ["no ip route 172.16.50.0 255.255.255.0"]


def test_nxos_vrf_route_and_remove_needs_next_hop() -> None:
    lines = _r(_i(**ROUTE, vrf="blue"), "nxos")
    assert lines == ["vrf context blue", "  ip route 172.16.50.0/24 10.0.0.1"]
    with pytest.raises(UnsupportedChange, match="next_hop"):
        _r(_i(change_type="static_route_remove", prefix="172.16.50.0/24"), "nxos")


def test_eos_and_isr_render() -> None:
    assert _r(_i(**VLAN), "arista")[:2] == ["vlan 120", "   name app"]
    assert _r(_i(**VLAN), "isr")[:2] == ["vlan 120", " name app"]


def test_junos_set_format() -> None:
    assert _r(_i(**VLAN), "junos")[0] == "set vlans app vlan-id 120"
    assert _r(_i(**ROUTE), "junos") == [
        "set routing-options static route 172.16.50.0/24 next-hop 10.0.0.1"
    ]
    with pytest.raises(UnsupportedChange, match="vlan_name"):
        _r(_i(change_type="vlan_remove", vlan_id=120), "junos")


# --- Vendor templates ---------------------------------------------------------------


def test_shipped_vendor_templates_load() -> None:
    assert {"fortios", "panos", "sros", "srlinux", "exos"} <= SHIPPED.platforms()


def test_fortinet_vlan_and_route() -> None:
    vlan = _r(_i(**VLAN), "fortinet")
    assert vlan[0] == "config system interface"
    assert '    edit "vlan120"' in vlan and "        set vlanid 120" in vlan
    assert "        set dst 172.16.50.0 255.255.255.0" in _r(_i(**ROUTE), "fortios")


def test_fortinet_vlan_needs_parent_interface() -> None:
    with pytest.raises(UnsupportedChange, match="interfaces"):
        _r(_i(change_type="vlan_add", vlan_id=5, vlan_name="x"), "fortinet")


def test_paloalto_rule_and_route() -> None:
    acl = _r(_i(**ACL), "paloalto")
    assert acl[0] == "set service vn-tcp-443 protocol tcp port 443"
    assert any("action allow" in line for line in acl)
    route = _r(_i(**ROUTE), "panos")
    assert route == [
        "set network virtual-router default routing-table ip static-route vn-172.16.50.0_24 "
        "destination 172.16.50.0/24 nexthop ip-address 10.0.0.1"
    ]


def test_nokia_sros_and_srlinux() -> None:
    assert _r(_i(**ROUTE), "nokia") == [
        '/configure router "Base" static-routes route 172.16.50.0/24 route-type unicast '
        "next-hop 10.0.0.1 admin-state enable"
    ]
    srl = _r(_i(**{**VLAN, "interfaces": ["ethernet-1/1"]}), "srlinux")
    assert "set / network-instance vlan-120 type mac-vrf" in srl
    assert "set / network-instance vlan-120 interface ethernet-1/1.120" in srl


def test_extreme_exos() -> None:
    assert _r(_i(**{**VLAN, "interfaces": ["1:1"]}), "extreme") == [
        'create vlan "app" tag 120',
        'configure vlan "app" add ports 1:1 untagged',
    ]
    with pytest.raises(UnsupportedChange, match="next_hop"):
        _r(_i(change_type="static_route_remove", prefix="172.16.50.0/24"), "exos")


def test_missing_vendor_template_is_rejected_with_a_pointer() -> None:
    with pytest.raises(UnsupportedChange, match="templates.vlan_add.sros"):
        _r(_i(**VLAN), "nokia")


def test_unknown_platform_rejected() -> None:
    with pytest.raises(UnsupportedChange, match="no renderer for platform 'huawei-vrp'"):
        _r(_i(**VLAN), "huawei-vrp")
    with pytest.raises(UnsupportedChange, match="platform unknown"):
        _r(_i(**VLAN), "")


def test_user_template_overrides_builtin_and_adds_vendor() -> None:
    t = RenderTemplates.from_yaml_text(
        """
version: 1
templates:
  vlan_add:
    ios:
      config: |
        vlan {{ intent.vlan_id }}
         name {{ intent.vlan_name|upper }}
    huawei-vrp:
      config: |
        vlan {{ intent.vlan_id }}
         description {{ intent.vlan_name }}
"""
    )
    r = Renderer(t)
    assert _r(_i(**VLAN), "ios", r) == ["vlan 120", " name APP"]
    assert _r(_i(**VLAN), "huawei-vrp", r) == ["vlan 120", " description app"]
    # Other change types still use the built-in.
    assert _r(_i(**ROUTE), "ios", r)[0].startswith("ip route 172.16.50.0 255.255.255.0")


def test_invalid_render_templates_rejected() -> None:
    with pytest.raises(RenderTemplateError, match="unknown change type"):
        RenderTemplates.from_yaml_text("version: 1\ntemplates:\n  nope:\n    ios: {config: x}\n")
    with pytest.raises(RenderTemplateError, match="does not parse"):
        RenderTemplates.from_yaml_text(
            "version: 1\ntemplates:\n  vlan_add:\n    ios: {config: '{{ x'}\n"
        )


# --- Safety --------------------------------------------------------------------------


def test_newline_injection_in_description_is_refused_for_every_platform() -> None:
    evil = _i(change_type="interface_set", interface="Ethernet1", description="x\nusername a")
    for platform in [
        "ios",
        "nxos",
        "eos",
        "junos",
        "fortinet",
        "paloalto",
        "nokia",
        "srlinux",
        "extreme",
    ]:
        with pytest.raises(UnsupportedChange, match="description contains a control character"):
            _r(evil, platform)


def test_interface_names_cannot_carry_spaces_or_quotes() -> None:
    for bad in ["Gi0/1 shutdown", 'eth"1', "a\nb"]:
        with pytest.raises(ValidationError):
            _i(change_type="interface_set", interface=bad)
        with pytest.raises(ValidationError):
            _i(**{**VLAN, "interfaces": [bad]})
    for good in ["GigabitEthernet0/1", "ethernet-1/1", "1/1/1", "1:1", "port1", "et-0/0/1"]:
        _i(change_type="interface_set", interface=good)


# --- Workflow wiring ------------------------------------------------------------------


async def _acts(tmp_path, platforms: dict[str, str]):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    netbox = FakeNetBoxAdapter()
    for name, plat in platforms.items():
        netbox.devices[name] = NetBoxDevice(name, "S", "m", "dc1", "active", plat, "10.0.0.9")
    intent = {**BASE, **VLAN, "devices": list(platforms), "interfaces": ["port1"]}
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="RITM42",
                change_type="vlan_add",
                state=ChangeState.PRE_CHECKED,
                requester="alice",
                intent=intent,
                devices=list(platforms),
            )
        )
        await s.commit()
    engine = FakeExecutionEngine()
    acts = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=netbox,
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
        engine=engine,
        renderer=Renderer(SHIPPED),
    )
    return acts, engine, change_id, intent, db


async def test_mixed_vendor_change_applies_each_device_with_its_own_config(tmp_path) -> None:
    acts, engine, change_id, intent, db = await _acts(
        tmp_path, {"leaf-a": "cisco-ios", "fw-b": "fortinet", "sw-c": "juniper-junos"}
    )
    res = await acts.apply_change(ApplyInput(change_id=change_id, intent=intent))
    assert res["ok"], res
    applies = {c.device.name: c for c in engine.calls if c.step == "apply"}
    assert set(applies) == {"leaf-a", "fw-b", "sw-c"}
    assert applies["leaf-a"].device.platform == "ios"
    assert applies["leaf-a"].extra_vars["intended_config"].startswith("vlan 120\n")
    assert applies["fw-b"].extra_vars["intended_config"].startswith("config system interface")
    assert applies["sw-c"].extra_vars["config_format"] == "set"
    assert (
        json.loads(applies["sw-c"].extra_vars["intended_lines"])[0] == "set vlans app vlan-id 120"
    )
    # Every device got a dry-run with its config before any apply.
    dry = [c for c in engine.calls if c.step == "dry_run"]
    assert len(dry) == 3 and engine.calls.index(dry[-1]) < engine.calls.index(applies["leaf-a"])
    await db.dispose()


async def test_validate_rejects_a_platform_that_cannot_render(tmp_path) -> None:
    acts, _engine, change_id, intent, db = await _acts(tmp_path, {"pe-1": "nokia"})
    res = await acts.validate_change(ValidateInput(change_id=change_id, intent=intent))
    assert res["ok"] is False
    assert any("templates.vlan_add.sros" in e for e in res["errors"])
    await db.dispose()


# --- API -----------------------------------------------------------------------------


def _client() -> TestClient:
    app = create_app(
        Settings(
            adapter_mode="fake",
            dev=True,
            database_url="sqlite+aiosqlite:///:memory:",
            render_templates_file=str(REPO / "policies/render_templates.yaml"),
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    return TestClient(app)


def test_api_render_templates_and_preview() -> None:
    client = _client()
    info = client.get("/render/templates").json()
    assert {"ios", "fortios", "panos", "sros", "srlinux", "exos"} <= set(info["platforms"])
    resp = client.post(
        "/render/preview", json={"intent": {**BASE, **ROUTE}, "platform": "paloalto"}
    ).json()
    assert resp["rendered"]["platform"] == "panos"
    assert resp["rendered"]["source"] == "template"
    bad = client.post(
        "/render/preview", json={"intent": {**BASE, **VLAN}, "platform": "nokia"}
    ).json()
    assert bad["rendered"] is None and "sros" in bad["errors"][0]
