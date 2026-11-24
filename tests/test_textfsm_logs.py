"""TextFSM (ntc-templates + your own) and show-logging anomaly detection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.checks.templates import CheckTemplates, TemplateValidationError
from vireonet.config import Settings
from vireonet.logs.analytics import analyse, netbox_journal_text
from vireonet.logs.fake_syslog import INCIDENTS, fake_show_logging
from vireonet.logs.parser import parse_show_logging
from vireonet.main import create_app
from vireonet.textfsm_lib import TextFsmError, list_templates, parse, read_template, validate
from vireonet.topology.demo import DEMO_NODES, demo_interfaces

REPO = Path(__file__).resolve().parents[1]
USER = str(REPO / "policies/textfsm")
NOW = datetime(2026, 11, 23, 17, 0, tzinfo=UTC)
VLANS = (
    "VLAN Name                             Status    Ports\n"
    "---- -------------------------------- --------- -------------------------------\n"
    "1    default                          active    Gi1/0/1, Gi1/0/2\n"
    "300  excel-demo                       active    \n"
)


# --- TextFSM library -----------------------------------------------------------------


def test_ntc_templates_parse_and_platform_mapping() -> None:
    r = parse("ios-xe", "sh vlan br", VLANS, user_dir=USER)  # abbreviated command, mapped platform
    assert (r.source, r.template, r.platform) == ("ntc", "cisco_ios_show_vlan.textfsm", "cisco_ios")
    assert r.rows[0]["interfaces"] == ["Gi1/0/1", "Gi1/0/2"]
    assert {
        "vlan_id": "300",
        "vlan_name": "excel-demo",
        "status": "active",
        "interfaces": [],
    } in r.rows


def test_user_templates_win_over_ntc(tmp_path) -> None:
    (tmp_path / "index").write_text(
        "Template, Hostname, Platform, Command\n\nmy_vlans.textfsm, .*, cisco_ios, sh[[ow]] vl[[an]] br[[ief]]\n"
    )
    (tmp_path / "my_vlans.textfsm").write_text(
        "Value ID (\\d+)\nValue NAME (\\S+)\n\nStart\n  ^${ID}\\s+${NAME}\\s+active -> Record\n"
    )
    r = parse("ios", "show vlan brief", VLANS, user_dir=tmp_path)
    assert r.source == "user" and r.rows == [
        {"id": "1", "name": "default"},
        {"id": "300", "name": "excel-demo"},
    ]


def test_inline_draft_template_and_validation() -> None:
    draft = "Value A (\\d+)\n\nStart\n  ^${A}\\s -> Record\n"
    assert parse("ios", "anything", "12 x\n34 y", template_text=draft).rows == [
        {"a": "12"},
        {"a": "34"},
    ]
    assert validate(draft) == {"valid": True, "errors": [], "values": ["A"], "states": ["Start"]}
    assert validate("nonsense")["valid"] is False
    with pytest.raises(TextFsmError, match="does not compile"):
        parse("ios", "x", "y", template_text="nonsense")


def test_no_template_is_a_clear_error() -> None:
    with pytest.raises(TextFsmError, match="no TextFSM template"):
        parse("ios", "show unicorn status", "x", user_dir=USER)


def test_listing_and_reading_templates() -> None:
    every = list_templates(user_dir=USER)
    assert every["total"] > 900
    junos_bgp = list_templates("junos", "bgp", user_dir=USER)["templates"]
    assert junos_bgp and all(t["platform"] == "juniper_junos" for t in junos_bgp)
    assert any(
        t["source"] == "user" for t in list_templates("nxos", "logging", user_dir=USER)["templates"]
    )
    assert "Value" in read_template("ntc", "cisco_ios_show_vlan.textfsm")
    with pytest.raises(TextFsmError):
        read_template("ntc", "../../etc/passwd")


# --- Check templates with parse: textfsm ------------------------------------------------


def test_shipped_textfsm_check_is_valid_and_bad_ones_are_rejected() -> None:
    t = CheckTemplates.from_file(REPO / "policies/checks.yaml")
    names = [c["name"] for c in t.summary()["change_types"]["vlan_add"]["post"]]
    assert "vlan_active_parsed" in names
    with pytest.raises(TemplateValidationError, match="need `parse: textfsm`"):
        CheckTemplates.from_yaml_text(
            "version: 1\ndefaults:\n  post:\n    - name: c1\n      command: {default: 'show vlan brief'}\n      expect: {row: {vlan_id: '1'}}\n"
        )


# --- Log parsing and anomaly detection -------------------------------------------------------


def _all_events():  # type: ignore[no-untyped-def]
    out = []
    for n in DEMO_NODES:
        text = fake_show_logging(n["name"], n["platform"], demo_interfaces(n["name"]), now=NOW)
        events, unparsed = parse_show_logging(n["name"], n["platform"], text, year=2026)
        assert unparsed == 0, (n["name"], n["platform"])
        out.extend(events)
    return out


def test_every_log_format_parses() -> None:
    nx = parse_show_logging(
        "leaf-02",
        "nxos",
        "2026 Nov 23 10:15:01 leaf-02 %ETHPORT-5-IF_DOWN_LINK_FAILURE: Interface Ethernet1/49 is down (Link failure)",
    )[0][0]
    assert (nx.severity, nx.mnemonic, nx.interface) == (
        5,
        "ETHPORT-IF_DOWN_LINK_FAILURE",
        "Ethernet1/49",
    )
    ios = parse_show_logging(
        "r1",
        "ios",
        "000123: *Nov 23 10:15:01.123 UTC: %LINK-3-UPDOWN: Interface GigabitEthernet1/0/2, changed state to down",
        year=2026,
    )[0][0]
    assert (ios.severity, ios.mnemonic, ios.timestamp) == (
        3,
        "LINK-UPDOWN",
        "2026-11-23T10:15:01+00:00",
    )
    eos = parse_show_logging(
        "s1",
        "eos",
        "Nov 23 10:15:01 s1 Ebra: %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down",
    )[0][0]
    assert eos.mnemonic == "LINEPROTO-UPDOWN"
    jun = parse_show_logging(
        "j1",
        "junos",
        "Nov 23 10:15:01  j1 mib2d[2211]: SNMP_TRAP_LINK_DOWN: ifIndex 512, ifName et-0/0/48",
    )[0][0]
    assert (jun.severity, jun.mnemonic, jun.interface) == (3, "SNMP_TRAP_LINK_DOWN", "et-0/0/48")
    events, unparsed = parse_show_logging("r1", "ios", "garbage line\n%not a log")
    assert events == [] and unparsed == 2


def test_anomaly_detection_finds_exactly_the_injected_incidents() -> None:
    result = analyse(_all_events())
    anomalous = {d["device"] for d in result["devices"] if d["status"] == "anomalous"}
    assert anomalous == set(INCIDENTS)
    kinds = {(i["device"], i["kind"]) for i in result["incidents"]}
    assert ("fw-02", "burst") in kinds  # SSH brute force
    assert ("leaf-02", "burst") in kinds  # link flapping
    assert ("border-01", "burst") in kinds  # BGP flaps
    assert ("spine-02", "critical") in kinds  # power supply
    flap = next(i for i in result["incidents"] if i["device"] == "leaf-02" and i["kind"] == "burst")
    assert flap["interfaces"] == ["Ethernet1/49"]


def test_netbox_journal_summary() -> None:
    result = analyse(_all_events())
    spine = [i for i in result["incidents"] if i["device"] == "spine-02"]
    kind, text = netbox_journal_text("spine-02", spine)
    assert kind == "danger" and "VireoNet log analytics" in text and "PS_FAIL" in text


def test_log_api_and_netbox_push_permissions() -> None:
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    c = TestClient(app)
    r = c.post("/ml/log-anomalies", json={}).json()
    assert {d["device"] for d in r["devices"] if d["status"] == "anomalous"} == set(INCIDENTS)
    assert r["netbox"] == []
    assert c.post("/ml/log-anomalies", json={"push_to_netbox": True}).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "op", "operator")
    pushed = c.post("/ml/log-anomalies", json={"push_to_netbox": True}).json()["netbox"]
    assert {p["device"] for p in pushed} == set(INCIDENTS)


def test_textfsm_api() -> None:
    app = create_app(
        Settings(
            adapter_mode="fake",
            dev=True,
            database_url="sqlite+aiosqlite:///:memory:",
            textfsm_user_dir=USER,
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    c = TestClient(app)
    assert (
        c.get("/textfsm/templates", params={"platform": "eos", "q": "interface"}).json()["total"]
        > 0
    )
    parsed = c.post(
        "/textfsm/parse", json={"platform": "ios", "command": "show vlan brief", "output": VLANS}
    ).json()
    assert parsed["ok"] and parsed["template"] == "cisco_ios_show_vlan.textfsm"
    bad = c.post(
        "/textfsm/parse", json={"platform": "ios", "command": "show unicorn", "output": "x"}
    ).json()
    assert bad["ok"] is False
    assert c.get("/textfsm/templates/ntc/cisco_ios_show_vlan.textfsm").status_code == 200
    assert c.get("/textfsm/templates/evil/x.textfsm").status_code == 404
