"""Request shapes the infrastructure adapters send, checked against the
vendors' documented APIs (NetBox, AWX/AAP, Vault, Batfish, EVE-NG,
containerlab, Kea, Teams Workflows). HTTP is respx-mocked; hvac, pybatfish
and the containerlab CLI are stubbed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import hvac.exceptions
import pytest
import respx
from httpx import Request, Response

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.batfish import BatfishAdapter
from vireonet.adapters.containerlab import ContainerlabAdapter, _parse_inspect
from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.errors import AdapterNotFound
from vireonet.adapters.eveng import EveNgAdapter
from vireonet.adapters.netbox import NetBoxAdapter
from vireonet.adapters.notification import NotificationAdapter
from vireonet.adapters.protocols import DhcpReservation
from vireonet.adapters.vault import VaultAdapter
from vireonet.config import Settings

# -- NetBox ------------------------------------------------------------------

NB = "https://netbox.example"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("nbt_abc123.secretpart", "Bearer nbt_abc123.secretpart"),
        ("0123456789abcdef", "Token 0123456789abcdef"),
    ],
)
@respx.mock
async def test_netbox_v2_tokens_use_bearer(token: str, expected: str) -> None:
    route = respx.get(url__regex=r".*/api/dcim/devices/.*").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    adapter = NetBoxAdapter(AdapterConfig(name="netbox", base_url=NB, max_retries=0), token=token)
    health = await adapter.health_check()
    assert health.status == "ok"
    assert route.calls.last.request.headers["Authorization"] == expected


# -- AWX / AAP ---------------------------------------------------------------

AWX = "https://awx.example"


def _awx(prefix: str = "/api/v2") -> AwxAdapter:
    return AwxAdapter(
        AdapterConfig(name="awx", base_url=AWX, max_retries=0), token="tok", api_prefix=prefix
    )


def test_awx_api_prefix_setting_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIREONET_AWX_API_PREFIX", raising=False)
    assert Settings(_env_file=None).awx_api_prefix == "/api/v2"  # type: ignore[call-arg]
    monkeypatch.setenv("VIREONET_AWX_API_PREFIX", "/api/controller/v2")
    assert Settings(_env_file=None).awx_api_prefix == "/api/controller/v2"  # type: ignore[call-arg]


@respx.mock
async def test_awx_aap_gateway_prefix_used_for_all_paths() -> None:
    ping = respx.get(f"{AWX}/api/controller/v2/ping/").mock(return_value=Response(200, json={}))
    job = respx.get(f"{AWX}/api/controller/v2/jobs/7/").mock(
        return_value=Response(200, json={"id": 7, "status": "running"})
    )
    adapter = _awx("/api/controller/v2/")
    assert (await adapter.health_check()).status == "ok"
    assert (await adapter.get_job(7)).status == "running"
    assert ping.called and job.called


@respx.mock
async def test_awx_stdout_requests_plain_text() -> None:
    route = respx.get(f"{AWX}/api/v2/jobs/42/stdout/").mock(
        return_value=Response(200, text="PLAY RECAP\n")
    )
    assert await _awx().get_job_stdout(42) == "PLAY RECAP\n"
    assert route.calls.last.request.url.params["format"] == "txt"


@respx.mock
async def test_awx_sync_inventory_uses_update_inventory_sources() -> None:
    route = respx.post(f"{AWX}/api/v2/inventories/3/update_inventory_sources/").mock(
        return_value=Response(
            202,
            json=[
                {"inventory_update": 91, "inventory_source": 5, "status": "started"},
                {"inventory_update": 92, "inventory_source": 6, "status": "started"},
            ],
        )
    )
    assert await _awx().sync_inventory(3) == 91
    assert route.called


@respx.mock
async def test_awx_approve_resolves_approval_job_from_node() -> None:
    respx.get(f"{AWX}/api/v2/workflow_job_nodes/11/").mock(
        return_value=Response(
            200,
            json={
                "id": 11,
                "job": 555,
                "summary_fields": {"job": {"id": 555, "type": "workflow_approval"}},
            },
        )
    )
    approve = respx.post(f"{AWX}/api/v2/workflow_approvals/555/approve/").mock(
        return_value=Response(204)
    )
    await _awx().approve_workflow_node(11)
    assert approve.called


@respx.mock
async def test_awx_approve_with_approval_job_id_directly() -> None:
    approve = respx.post(f"{AWX}/api/v2/workflow_approvals/555/approve/").mock(
        return_value=Response(204)
    )
    await _awx().approve_workflow_node(555, resolve_node=False)
    assert approve.called


# -- Vault -------------------------------------------------------------------


def _vault_client() -> MagicMock:
    client = MagicMock()
    client.is_authenticated.return_value = False
    client.auth.approle.login.return_value = {
        "auth": {"client_token": "tok-1", "lease_duration": 3600}
    }
    client.auth.token.create.return_value = {"auth": {"client_token": "bootstrap-1"}}
    return client


def _vault(client: MagicMock, **kw: Any) -> VaultAdapter:
    return VaultAdapter(
        AdapterConfig(name="vault"),
        addr="https://vault.example",
        role_id="r",
        secret_id="s",
        client=client,
        **kw,
    )


async def test_vault_renew_uses_lease_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _vault_client()
    client.auth.token.renew_self.return_value = {"auth": {"lease_duration": 120}}
    adapter = _vault(client)
    monkeypatch.setattr("vireonet.adapters.vault.time.time", lambda: 1000.0)
    await adapter.renew_token()
    assert adapter._token_expires_at == 1120.0


async def test_vault_invalid_path_is_not_found() -> None:
    client = _vault_client()
    client.secrets.kv.v2.read_secret_version.side_effect = hvac.exceptions.InvalidPath()
    with pytest.raises(AdapterNotFound):
        await _vault(client).read_secret("missing")


async def test_vault_bootstrap_token_default_and_role() -> None:
    client = _vault_client()
    await _vault(client).issue_bootstrap_token("leaf-01")
    _, kwargs = client.auth.token.create.call_args
    assert "no_parent" not in kwargs and "role_name" not in kwargs  # no sudo needed

    client = _vault_client()
    await _vault(client, bootstrap_token_role="ztp-bootstrap").issue_bootstrap_token("leaf-01")
    _, kwargs = client.auth.token.create.call_args
    assert kwargs["role_name"] == "ztp-bootstrap"


# -- Batfish -----------------------------------------------------------------


class _BfQuestion:
    def __init__(self, name: str, rows: list[dict[str, Any]], calls: list[Any]) -> None:
        self.name, self.rows, self.calls, self.params = name, rows, calls, {}

    def __call__(self, **params: Any) -> _BfQuestion:
        self.params = params
        return self

    def answer(self, **kwargs: Any) -> Any:
        self.calls.append((self.name, dict(self.params), kwargs))
        rows = self.rows
        return SimpleNamespace(frame=lambda: SimpleNamespace(to_dict=lambda orient: list(rows)))


class _BfSession:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.calls: list[Any] = []
        self.layout: dict[str, list[str]] = {}
        self.q = SimpleNamespace()
        for name in (
            "undefinedReferences",
            "ipOwners",
            "bgpSessionStatus",
            "reachability",
            "routes",
            "differentialReachability",
        ):
            setattr(self.q, name, _BfQuestion(name, rows.get(name, []), self.calls))

    def init_snapshot(self, path: str, name: str, overwrite: bool = False) -> None:
        root = Path(path)
        self.layout[name] = sorted(str(p.relative_to(root)) for p in root.rglob("*.cfg"))


async def test_batfish_snapshot_layout_and_duplicate_ips() -> None:
    session = _BfSession({"ipOwners": [{"IP": "10.0.0.1", "Node": "leaf-01"}]})
    adapter = BatfishAdapter(AdapterConfig(name="batfish"), session=session)
    result = await adapter.analyse({"leaf-01.cfg": "hostname leaf-01\n"})
    assert session.layout["candidate"] == ["configs/leaf-01.cfg"]
    assert ("ipOwners", {"duplicatesOnly": True}, {"snapshot": "candidate"}) in session.calls
    assert not result.passed and result.failures == ["duplicate IPs: 1"]


async def test_batfish_differential_reachability_columns() -> None:
    lost = {
        "Flow": "10.0.0.1 -> 10.0.0.2",
        "Snapshot_Traces": [SimpleNamespace(disposition="DENIED_IN")],
        "Reference_Traces": [SimpleNamespace(disposition="ACCEPTED")],
    }
    session = _BfSession({"differentialReachability": [lost]})
    adapter = BatfishAdapter(AdapterConfig(name="batfish"), session=session)
    result = await adapter.analyse_differential({"a.cfg": "x\n"}, {"a.cfg": "y\n"})
    assert (
        "differentialReachability",
        {},
        {"snapshot": "candidate", "reference_snapshot": "current"},
    ) in session.calls
    assert session.layout == {"current": ["configs/a.cfg"], "candidate": ["configs/a.cfg"]}
    assert result.differential["reachability"]["lost"] == 1  # type: ignore[index]
    assert "reachability lost: 1 flow(s)" in result.failures


# -- EVE-NG ------------------------------------------------------------------

EVE = "https://eveng.example"


@respx.mock
async def test_eveng_paths_nodes_and_console() -> None:
    respx.post(f"{EVE}/api/auth/login").mock(
        return_value=Response(200, json={}, headers={"Set-Cookie": "unetlab_session=x; Path=/"})
    )
    create = respx.post(f"{EVE}/api/labs").mock(
        return_value=Response(200, json={"code": 200, "status": "success", "message": "created"})
    )
    lab = f"{EVE}/api/labs/ci/my%20lab.unl"
    start = respx.get(f"{lab}/nodes/start").mock(return_value=Response(200, json={}))
    stop = respx.get(f"{lab}/nodes/stop").mock(return_value=Response(200, json={}))
    respx.get(f"{lab}/nodes").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "1": {"id": 1, "name": "leaf-01", "status": 2, "url": "telnet://10.1.1.1:32769"}
                }
            },
        )
    )
    adapter = EveNgAdapter(
        AdapterConfig(name="eveng", base_url=EVE, max_retries=0),
        username="admin",
        password="eve",  # noqa: S106 — mocked
        folder="/ci",
    )
    created = await adapter.start_lab("my lab", "t.unl")
    assert created.id == "/ci/my lab.unl"
    assert json.loads(create.calls.last.request.content)["path"] == "/ci"
    assert start.called
    details = await adapter.get_console_details(created.id, "leaf-01")
    assert details["console"] == "telnet://10.1.1.1:32769"
    assert adapter._console_target(details["console"]) == ("10.1.1.1", 32769)
    await adapter.stop_lab(created.id)
    assert stop.called and stop.calls.last.request.method == "GET"


# -- containerlab ------------------------------------------------------------


def test_containerlab_inspect_current_lab_keyed_shape() -> None:
    raw = json.dumps(
        {
            "two-leaf": [
                {
                    "lab_name": "two-leaf",
                    "name": "clab-two-leaf-leaf-01",
                    "kind": "cisco_iol",
                    "state": "running",
                    "ipv4_address": "172.20.20.2/24",
                },
                {
                    "lab_name": "two-leaf",
                    "name": "clab-two-leaf-leaf-02",
                    "kind": "cisco_iol",
                    "state": "running",
                    "ipv4_address": "172.20.20.3/24",
                },
            ]
        }
    )
    nodes = _parse_inspect(raw, "two-leaf")
    assert [(n.name, n.mgmt_ip, n.state) for n in nodes] == [
        ("leaf-01", "172.20.20.2", "running"),
        ("leaf-02", "172.20.20.3", "running"),
    ]


async def test_containerlab_inspect_old_containers_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ContainerlabAdapter(host="lab.example", user="lab")
    raw = json.dumps({"containers": [{"name": "clab-x-n1", "state": "running", "ipv4": "1.1.1.1"}]})

    async def fake_run_cli(*args: str, timeout: int = 120) -> str:
        assert list(args) == ["inspect", "--name", "x", "--format", "json"]
        return raw

    monkeypatch.setattr(adapter, "_run_cli", fake_run_cli)
    nodes = await adapter.inspect("x")
    assert [(n.name, n.mgmt_ip) for n in nodes] == [("n1", "1.1.1.1")]


# -- Kea ---------------------------------------------------------------------


async def test_kea_global_reservations_enabled_and_subnet4_kept() -> None:
    dhcp = DhcpAdapter(AdapterConfig(name="dhcp"), server="kea")
    cfg = json.loads(
        await dhcp.render_config(
            [DhcpReservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101")], "kea"
        )
    )
    assert cfg["Dhcp4"]["reservations-global"] is True
    assert cfg["Dhcp4"]["reservations"][0]["hw-address"] == "aa:bb:cc:dd:ee:ff"

    existing = {
        "Dhcp4": {
            "subnet4": [{"id": 1, "subnet": "10.0.1.0/24"}],
            "reservations": [
                {"hostname": "leaf-01", "ip-address": "10.0.1.101"},
                {"hostname": "leaf-02", "ip-address": "10.0.1.102"},
            ],
        }
    }
    updated = json.loads(
        DhcpAdapter.remove_host_from_config(json.dumps(existing), "leaf-01", "kea")
    )
    assert updated["Dhcp4"]["subnet4"] == existing["Dhcp4"]["subnet4"]
    assert updated["Dhcp4"]["reservations-global"] is True
    assert [r["hostname"] for r in updated["Dhcp4"]["reservations"]] == ["leaf-02"]


# -- Teams Workflows ---------------------------------------------------------


@pytest.mark.parametrize("status", [200, 202])
@respx.mock
async def test_teams_workflows_adaptive_card_payload(status: int) -> None:
    seen: list[Request] = []

    def handler(request: Request) -> Response:
        seen.append(request)
        return Response(status)

    respx.post("https://prod.logic.example/workflows/x").mock(side_effect=handler)
    adapter = NotificationAdapter(
        AdapterConfig(name="notification", max_retries=0),
        teams_webhook_url="https://prod.logic.example/workflows/x",
    )
    assert await adapter.send("teams", "Approval needed", "Change CHG1", "RITM1") is True
    payload = json.loads(seen[0].content)
    assert payload["type"] == "message"
    (attachment,) = payload["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    title, body = card["body"]
    assert title == {
        "type": "TextBlock",
        "text": "Approval needed",
        "weight": "Bolder",
        "wrap": True,
    }
    assert body["type"] == "TextBlock" and body["wrap"] is True
    assert body["text"].startswith("Change CHG1")
    assert "@type" not in payload  # no retired O365 MessageCard
