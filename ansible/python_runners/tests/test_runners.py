"""E2 runner tests: mocked Nornir/NAPALM and Netmiko drivers."""

from __future__ import annotations

import io
import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from vireonet_runners import runners as runners_mod
from vireonet_runners.netmiko_runner import run_netmiko_step
from vireonet_runners.nornir_runner import run_nornir_step

DEVICE = "leaf-01"
RUNNING = "hostname leaf-01\n!\nvlan 1\n name default\n"


def _req(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ticket_number": "RITM1",
        "device": DEVICE,
        "platform": "iosxe",
        "mgmt_ip": "10.0.0.11",
        "step": "backup",
        "change_type": "vlan_add",
        "rendered_config": "vlan 100\n name web\n",
        "diff": "",
        "extra_vars": {},
        "dry_run": True,
        "timeout_seconds": 60,
    }
    base.update(overrides)
    return base


# -- fake nornir -----------------------------------------------------------


def _napalm_get(*args: Any, **kwargs: Any) -> None:
    pass


def _napalm_configure(*args: Any, **kwargs: Any) -> None:
    pass


class FakeNR:
    """Records InitNornir kwargs and run() calls; returns canned results."""

    instances: list[FakeNR] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.runs: list[tuple[Any, dict[str, Any]]] = []
        FakeNR.instances.append(self)

    def run(self, task: Any, **kwargs: Any) -> dict[str, SimpleNamespace]:
        self.runs.append((task, kwargs))
        if task is _napalm_get:
            getters = kwargs.get("getters") or []
            if "config" in getters:
                payload: dict[str, Any] = {"config": {"running": RUNNING}}
            else:
                payload = {
                    "facts": {"hostname": DEVICE},
                    "interfaces": {"Gi0/1": {"is_up": True}},
                    "lldp_neighbors": {},
                }
            return {DEVICE: SimpleNamespace(result=payload)}
        # napalm_configure: dry_run=True returns a diff; commit returns "".
        if kwargs.get("dry_run"):
            return {DEVICE: SimpleNamespace(result="+vlan 100\n+ name web\n")}
        return {DEVICE: SimpleNamespace(result="")}


@pytest.fixture()
def fake_nornir(monkeypatch: pytest.MonkeyPatch) -> FakeNR:
    FakeNR.instances.clear()
    nornir_mod = types.ModuleType("nornir")
    nornir_mod.InitNornir = FakeNR  # type: ignore[attr-defined]
    tasks_mod = types.ModuleType("nornir_napalm.plugins.tasks")
    tasks_mod.napalm_get = _napalm_get  # type: ignore[attr-defined]
    tasks_mod.napalm_configure = _napalm_configure  # type: ignore[attr-defined]
    plugins_mod = types.ModuleType("nornir_napalm.plugins")
    pkg_mod = types.ModuleType("nornir_napalm")
    monkeypatch.setitem(sys.modules, "nornir", nornir_mod)
    monkeypatch.setitem(sys.modules, "nornir_napalm", pkg_mod)
    monkeypatch.setitem(sys.modules, "nornir_napalm.plugins", plugins_mod)
    monkeypatch.setitem(sys.modules, "nornir_napalm.plugins.tasks", tasks_mod)
    monkeypatch.setenv("VIREONET_DEVICE_USERNAME", "admin")
    monkeypatch.setenv("VIREONET_DEVICE_PASSWORD", "pw")
    return FakeNR


def test_nornir_backup_returns_mocked_config(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="backup"))
    assert result["status"] == "successful"
    assert result["output"]["running_config"] == RUNNING
    # Inventory was built in-memory: no static hosts file.
    inventory = FakeNR.instances[0].kwargs["inventory"]
    assert inventory["options"]["host_file"] == "/dev/null"
    assert DEVICE in inventory["options"]["hosts"]


def test_nornir_collect_state(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="collect_state"))
    assert result["status"] == "successful"
    assert result["output"]["facts"]["hostname"] == DEVICE


def test_nornir_dry_run_records_zero_commits(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="dry_run", dry_run=True))
    assert result["status"] == "successful"
    assert result["output"]["would_change"] is True
    assert result["declared_changes"][0]["identifier"] == "100"
    nr = FakeNR.instances[0]
    assert len(nr.runs) == 1
    assert nr.runs[0][1]["dry_run"] is True


def test_nornir_apply_dry_run_true_sends_nothing(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="apply", dry_run=True))
    assert result["status"] == "successful"
    nr = FakeNR.instances[0]
    assert len(nr.runs) == 1
    assert all(call[1]["dry_run"] is True for call in nr.runs)


def test_nornir_apply_dry_run_false_commits_once(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="apply", dry_run=False))
    assert result["status"] == "successful"
    assert result["output"]["committed"] is True
    nr = FakeNR.instances[0]
    assert len(nr.runs) == 2
    assert nr.runs[0][1]["dry_run"] is True  # compare first
    assert nr.runs[1][1]["dry_run"] is False  # commit once


def test_nornir_unsupported_step_fails(fake_nornir: FakeNR) -> None:
    result = run_nornir_step(_req(step="pre_check"))
    assert result["status"] == "failed"
    assert "unsupported step" in str(result["error"])


# -- fake netmiko ----------------------------------------------------------


class FakeConn:
    instances: list[FakeConn] = []
    fail_connect = False

    def __init__(self, **kwargs: Any) -> None:
        if FakeConn.fail_connect:
            raise ConnectionError("ssh refused")
        self.kwargs = kwargs
        self.sent_config: list[list[str]] = []
        self.commands: list[str] = []
        self.saved = 0
        self.disconnected = False
        FakeConn.instances.append(self)

    def send_command(self, command: str, **kwargs: Any) -> Any:
        self.commands.append(command)
        if command == "show running-config":
            return RUNNING
        return [{"interface": "Gi0/1", "status": "connected"}]

    def send_config_set(self, lines: list[str]) -> str:
        self.sent_config.append(list(lines))
        return "config ok"

    def save_config(self) -> str:
        self.saved += 1
        return "saved"

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture()
def fake_netmiko(monkeypatch: pytest.MonkeyPatch) -> type[FakeConn]:
    FakeConn.instances.clear()
    FakeConn.fail_connect = False
    mod = types.ModuleType("netmiko")
    mod.ConnectHandler = FakeConn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "netmiko", mod)
    monkeypatch.setenv("VIREONET_DEVICE_USERNAME", "admin")
    monkeypatch.setenv("VIREONET_DEVICE_PASSWORD", "pw")
    return FakeConn


def test_netmiko_backup_returns_mocked_config(fake_netmiko: type[FakeConn]) -> None:
    result = run_netmiko_step(_req(step="backup", platform="ios"))
    assert result["status"] == "successful"
    assert result["output"]["running_config"] == RUNNING


def test_netmiko_collect_state(fake_netmiko: type[FakeConn]) -> None:
    result = run_netmiko_step(_req(step="collect_state", platform="ios"))
    assert result["status"] == "successful"
    assert "interfaces_status" in result["output"]


def test_netmiko_dry_run_sends_nothing(fake_netmiko: type[FakeConn]) -> None:
    result = run_netmiko_step(_req(step="dry_run", platform="ios", dry_run=True))
    assert result["status"] == "successful"
    assert result["output"]["would_send"] == ["vlan 100", " name web"]
    assert FakeConn.instances[0].sent_config == []
    assert FakeConn.instances[0].saved == 0
    assert FakeConn.instances[0].disconnected is True


def test_netmiko_apply_sends_config_once(fake_netmiko: type[FakeConn]) -> None:
    result = run_netmiko_step(_req(step="apply", platform="ios", dry_run=False))
    assert result["status"] == "successful"
    conn = FakeConn.instances[0]
    assert conn.sent_config == [["vlan 100", " name web"]]
    assert conn.saved == 1


def test_netmiko_exception_still_emits_valid_json_failed(
    fake_netmiko: type[FakeConn],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    FakeConn.fail_connect = True
    req = _req(step="backup", platform="ios")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(req)))
    rc = runners_mod.main("backup")
    assert rc == 1
    out = capsys.readouterr().out
    assert "VIREONET-RESULT-BEGIN" in out
    assert "VIREONET-RESULT-END" in out
    block = out.split("VIREONET-RESULT-BEGIN")[1].split("VIREONET-RESULT-END")[0]
    parsed = json.loads(block)
    assert parsed["status"] == "failed"
    assert parsed["device"] == DEVICE
