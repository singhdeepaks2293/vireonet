"""E4: Containerlab adapter alongside EVE-NG, LabAdapter protocol,
registry backend selection, and lab_smoke cleanup.

No network, no Docker, no real EVE-NG/Containerlab: EVE-NG is respx-mocked
HTTP (same pattern as tests/test_eveng_real.py), Containerlab is a fake
``_run_cli`` recording CLI calls, and the Fake lab adapter is in-memory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lab_smoke  # noqa: E402
from lab_smoke import run_lab_smoke  # noqa: E402

from tests.contract_lab import check_lab  # noqa: E402
from vireonet.adapters.base import AdapterConfig, AdapterHealth  # noqa: E402
from vireonet.adapters.containerlab import ContainerlabAdapter, _parse_inspect  # noqa: E402
from vireonet.adapters.errors import AdapterUnavailable  # noqa: E402
from vireonet.adapters.eveng import EveNgAdapter  # noqa: E402
from vireonet.adapters.lab import LabAdapter, LabNode  # noqa: E402
from vireonet.adapters.registry import build_adapters  # noqa: E402
from vireonet.config import Settings  # noqa: E402

BASE = "https://eveng.example"


class FakeLabAdapter:
    """In-memory LabAdapter. ``fail_after_deploy`` makes wait_ready/exec
    raise after a successful deploy, for the cleanup test."""

    def __init__(self, *, fail_after_deploy: str | None = None) -> None:
        self.fail_after_deploy = fail_after_deploy
        self.destroyed: list[str] = []
        self.deployed: list[str] = []

    async def deploy(self, topology_path: str, name: str) -> str:
        if self.fail_after_deploy == "deploy":
            raise RuntimeError("deploy failed")
        self.deployed.append(name)
        return name

    async def wait_ready(self, lab_id: str, *, timeout_seconds: int = 600) -> list[LabNode]:
        if self.fail_after_deploy == "wait_ready":
            raise RuntimeError("nodes never came up")
        return await self.inspect(lab_id)

    async def inspect(self, lab_id: str) -> list[LabNode]:
        return [
            LabNode(name="leaf-01", kind="cisco_iol", mgmt_ip="10.0.1.11", state="running"),
            LabNode(name="leaf-02", kind="cisco_iol", mgmt_ip="10.0.1.12", state="running"),
        ]

    async def exec(self, lab_id: str, node: str, command: str) -> str:
        if self.fail_after_deploy == "exec":
            raise RuntimeError("exec failed")
        return f"{node}# {command}\nCisco IOS Software, Version 15.2\n"

    async def save_config(self, lab_id: str, node: str) -> str:
        return await self.exec(lab_id, node, "show running-config")

    async def restore_config(self, lab_id: str, node: str, config: str) -> None:
        raise NotImplementedError("fake restore is kind-specific")

    async def destroy(self, lab_id: str) -> None:
        self.destroyed.append(lab_id)

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name="fake-lab", status="ok", capabilities=self.capabilities())

    def capabilities(self) -> list[str]:
        return ["deploy", "inspect", "exec", "save_config", "destroy"]


async def test_contract_against_fake_lab_adapter() -> None:
    adapter = FakeLabAdapter()
    await check_lab(adapter, "topologies/two-leaf.clab.yml", "two-leaf")
    assert adapter.destroyed == ["two-leaf"]


def _eveng_adapter() -> EveNgAdapter:
    return EveNgAdapter(
        AdapterConfig(name="eveng", base_url=BASE, max_retries=0),
        username="admin",
        password="secret",  # noqa: S106 — respx-mocked test credential
    )


def _mock_eveng_lab() -> None:
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=Response(
            200, json={"status": "success"}, headers={"Set-Cookie": "session=abc; Path=/"}
        )
    )
    # Real EVE-NG answers lab creation with a message only (no lab path).
    respx.post(f"{BASE}/api/labs").mock(
        return_value=Response(
            200, json={"code": 200, "status": "success", "message": "Lab has been created"}
        )
    )
    respx.get(f"{BASE}/api/labs/ci-1.unl/nodes/start").mock(
        return_value=Response(200, json={"code": 200, "status": "success"})
    )
    respx.get(f"{BASE}/api/labs/ci-1.unl/nodes/stop").mock(
        return_value=Response(200, json={"code": 200, "status": "success"})
    )
    respx.get(f"{BASE}/api/labs/ci-1.unl/nodes").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "status": "success",
                "data": {
                    "1": {
                        "id": 1,
                        "name": "leaf-01",
                        "status": 2,
                        "template": "iol",
                        "url": "telnet://eveng.example:32769",
                    },
                    "2": {
                        "id": 2,
                        "name": "leaf-02",
                        "status": 2,
                        "template": "iol",
                        "url": "telnet://eveng.example:32770",
                    },
                },
            },
        )
    )
    respx.get(f"{BASE}/api/labs/ci-1.unl").mock(
        return_value=Response(200, json={"data": {"name": "ci-1", "filename": "ci-1.unl"}})
    )
    respx.delete(f"{BASE}/api/labs/ci-1.unl").mock(return_value=Response(200, json={}))


@respx.mock
async def test_contract_against_eveng_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_eveng_lab()
    adapter = _eveng_adapter()
    seen: dict[str, Any] = {}

    async def fake_telnet(host: str, port: int, command: str, *, timeout: int = 30) -> str:
        seen.update(host=host, port=port, command=command)
        return "Cisco IOS Software, Version 15.2\n"

    monkeypatch.setattr(adapter, "_telnet", fake_telnet)
    # EVE-NG nodes expose no mgmt IPs — so the contract runs with
    # expect_mgmt_ip=False (honest, not invented).
    await check_lab(adapter, "topologies/two-leaf.unl", "ci-1", expect_mgmt_ip=False)
    # Console target comes from the node's "url" field.
    assert seen["host"] == "eveng.example"
    assert seen["port"] == 32769
    assert seen["command"] == "show version"
    assert isinstance(adapter, LabAdapter)


@respx.mock
async def test_eveng_exec_telnet_failure_is_adapter_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_eveng_lab()
    adapter = _eveng_adapter()
    # Point the telnet target at a closed localhost port so the real
    # _telnet path runs and fails fast (connection refused, no network).
    monkeypatch.setattr(adapter, "_console_target", lambda url: ("127.0.0.1", 1))
    with pytest.raises(AdapterUnavailable, match="telnet"):
        await adapter.exec("/ci-1.unl", "leaf-01", "show version")


@respx.mock
async def test_eveng_old_methods_still_work() -> None:
    """Pre-E4 contract: start_lab / load_topology / wait_for_nodes /
    stop_lab / wipe_lab / get_console_details are unchanged."""
    _mock_eveng_lab()
    adapter = _eveng_adapter()
    lab = await adapter.start_lab("ci-1", "two-leaf.unl")
    assert lab.id == "/ci-1.unl"
    loaded = await adapter.load_topology(lab.id, "two-leaf.unl")
    assert loaded.nodes == ["two-leaf.unl"]
    ready = await adapter.wait_for_nodes(lab.id, timeout_seconds=5)
    assert set(ready.nodes) == {"leaf-01", "leaf-02"}
    details = await adapter.get_console_details(lab.id, "leaf-01")
    assert details["node"] == "leaf-01"
    assert details["console"] == "telnet://eveng.example:32769"
    await adapter.stop_lab(lab.id)
    await adapter.wipe_lab(lab.id)
    # destroy = stop + wipe, via the mocked endpoints.
    await adapter.destroy(lab.id)


def _clab_adapter() -> ContainerlabAdapter:
    return ContainerlabAdapter(
        AdapterConfig(name="containerlab", max_retries=0), host="lab.example", user="lab"
    )


def _inspect_json(state: str = "running") -> str:
    return json.dumps(
        {
            "containers": [
                {
                    "name": "clab-two-leaf-leaf-01",
                    "kind": "cisco_iol",
                    "state": state,
                    "ipv4": "10.0.1.11/24",
                },
                {
                    "name": "clab-two-leaf-leaf-02",
                    "kind": "cisco_iol",
                    "state": state,
                    "ipv4": "10.0.1.12/24",
                },
            ]
        }
    )


async def test_contract_against_containerlab_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _clab_adapter()
    calls: list[list[str]] = []
    states = iter(["deploying", "running"])

    async def fake_run_cli(*args: str, timeout: int = 120) -> str:
        calls.append(list(args))
        if args[0] == "inspect":
            # First poll: still deploying; second poll: running.
            return _inspect_json(next(states, "running"))
        if args[0] == "exec":
            return "Cisco IOS Software, Version 15.2\n"
        return ""

    monkeypatch.setattr(adapter, "_run_cli", fake_run_cli)
    await check_lab(adapter, "topologies/two-leaf.clab.yml", "two-leaf")
    assert calls[0] == ["deploy", "-t", "topologies/two-leaf.clab.yml"]
    assert ["destroy", "--name", "two-leaf", "--cleanup"] in calls
    exec_call = [c for c in calls if c[0] == "exec"][0]
    assert exec_call == [
        "exec",
        "--name",
        "two-leaf",
        "--label",
        "clab-node-name=leaf-01",
        "--cmd",
        "show version",
    ]
    assert isinstance(adapter, LabAdapter)


def test_containerlab_inspect_parses_top_level_list_shape() -> None:
    raw = json.dumps(
        [
            {"name": "leaf-01", "kind": "srl", "state": "running", "ipv4_address": "10.0.1.11/24"},
            {"name": "leaf-02", "kind": "srl", "state": "running", "ipv4": "10.0.1.12"},
        ]
    )
    nodes = _parse_inspect(raw, "two-leaf")
    assert [(n.name, n.kind, n.mgmt_ip, n.state) for n in nodes] == [
        ("leaf-01", "srl", "10.0.1.11", "running"),
        ("leaf-02", "srl", "10.0.1.12", "running"),
    ]


def test_containerlab_inspect_rejects_non_json() -> None:
    with pytest.raises(AdapterUnavailable, match="not JSON"):
        _parse_inspect("not json at all", "two-leaf")


async def test_containerlab_restore_is_not_invented() -> None:
    adapter = _clab_adapter()
    with pytest.raises(NotImplementedError, match="kind-specific"):
        await adapter.restore_config("two-leaf", "leaf-01", "hostname leaf-01\n")


async def test_containerlab_save_config_runs_show_running_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _clab_adapter()
    seen: list[list[str]] = []

    async def fake_run_cli(*args: str, timeout: int = 120) -> str:
        seen.append(list(args))
        return "hostname leaf-01\n"

    monkeypatch.setattr(adapter, "_run_cli", fake_run_cli)
    assert await adapter.save_config("two-leaf", "leaf-01") == "hostname leaf-01\n"
    assert seen[0][-1] == "show running-config"


# -- registry backend selection -------------------------------------------


def _real_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"adapter_mode": "real", "lab_backend": "eveng"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_registry_eveng_is_default_single_backend() -> None:
    adapters = build_adapters(_real_settings())
    assert "eveng" in adapters
    assert "containerlab" not in adapters
    assert "lab:eveng" not in adapters


def test_registry_containerlab_single_backend() -> None:
    adapters = build_adapters(
        _real_settings(
            lab_backend="containerlab", containerlab_host="lab.example", containerlab_user="lab"
        )
    )
    assert "containerlab" in adapters
    assert isinstance(adapters["containerlab"], ContainerlabAdapter)
    assert "eveng" not in adapters


def test_registry_both_exposes_both_under_distinct_names() -> None:
    adapters = build_adapters(
        _real_settings(lab_backend="both", containerlab_host="lab.example", containerlab_user="lab")
    )
    assert "lab:eveng" in adapters
    assert "lab:containerlab" in adapters
    assert "eveng" not in adapters
    assert "containerlab" not in adapters


def test_registry_unknown_lab_backend_raises() -> None:
    with pytest.raises(AdapterUnavailable, match="unknown lab backend"):
        build_adapters(_real_settings(lab_backend="bogus"))


def test_registry_containerlab_without_host_or_user_raises() -> None:
    with pytest.raises(AdapterUnavailable, match="containerlab backend needs"):
        build_adapters(_real_settings(lab_backend="containerlab"))
    with pytest.raises(AdapterUnavailable, match="containerlab backend needs"):
        build_adapters(_real_settings(lab_backend="containerlab", containerlab_host="lab.example"))


def test_registry_fake_mode_includes_both_lab_fakes() -> None:
    adapters = build_adapters(Settings(adapter_mode="fake"))
    assert "eveng" in adapters
    assert "containerlab" in adapters


# -- lab_smoke cleanup ------------------------------------------------------


async def test_lab_smoke_destroys_on_wait_ready_failure() -> None:
    adapter = FakeLabAdapter(fail_after_deploy="wait_ready")
    assert await run_lab_smoke(adapter, "topologies/two-leaf.clab.yml", "two-leaf") == 1
    assert adapter.destroyed == ["two-leaf"]


async def test_lab_smoke_destroys_on_exec_failure() -> None:
    adapter = FakeLabAdapter(fail_after_deploy="exec")
    assert await run_lab_smoke(adapter, "topologies/two-leaf.clab.yml", "two-leaf") == 1
    assert adapter.destroyed == ["two-leaf"]


async def test_lab_smoke_success_destroys() -> None:
    adapter = FakeLabAdapter()
    assert await run_lab_smoke(adapter, "topologies/two-leaf.clab.yml", "two-leaf") == 0
    assert adapter.destroyed == ["two-leaf"]


async def test_lab_smoke_deploy_failure_has_no_lab_to_destroy() -> None:
    adapter = FakeLabAdapter(fail_after_deploy="deploy")
    assert await run_lab_smoke(adapter, "topologies/two-leaf.clab.yml", "two-leaf") == 1
    assert adapter.destroyed == []  # deploy never returned a lab id


def test_lab_smoke_module_exports() -> None:
    assert callable(lab_smoke.main)
    assert callable(lab_smoke.run_lab_smoke)
