"""Flow scripts and API calls are safe: static checks, restricted builtins,
isolation report, sidecar runner signatures, DNS-rebinding guard."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from vireonet.flows.activities import (
    FlowActionInput,
    FlowActivities,
    _ip_problem,
    _resolved_problem,
)
from vireonet.flows.sandbox import DEFAULT_ALLOWED_IMPORTS, ScriptError, check_code, run_script
from vireonet.flows.script_runner import create_runner_app, sign


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("import os", "import of 'os'"),
        ("import subprocess as s", "import of 'subprocess'"),
        ("from socket import socket", "import from 'socket'"),
        ("from . import x", "import from None"),
        ("x = ().__class__.__mro__", "access to '__class__'"),
        ("eval('1')", "'eval' is not allowed"),
        ("open('/etc/passwd')", "'open' is not allowed"),
        ("getattr(1, 'real')", "'getattr' is not allowed"),
        ("'{0.__class__}'.format(1)", "containing '__'"),
        ("def f(:", "syntax error"),
    ],
)
def test_static_check_refuses_escapes(code: str, problem: str) -> None:
    assert any(problem in e for e in check_code(code, DEFAULT_ALLOWED_IMPORTS)), check_code(
        code, DEFAULT_ALLOWED_IMPORTS
    )


def test_static_check_allows_normal_code() -> None:
    code = "import re, json, ipaddress\nfrom collections import Counter\nresult = Counter(re.findall(r'\\w+', inputs['t']))"
    assert check_code(code, DEFAULT_ALLOWED_IMPORTS) == []


async def test_script_runs_with_restricted_builtins_and_reports_isolation() -> None:
    out = await run_script(
        "import ipaddress\nresult = [str(h) for h in ipaddress.ip_network(inputs['net']).hosts()][:2]",
        {"net": "10.0.0.0/30"},
    )
    assert out["result"] == ["10.0.0.1", "10.0.0.2"]
    assert set(out["isolation"]) == {"uid", "non_root", "network_isolated"}
    with pytest.raises(ScriptError, match="refused"):
        await run_script("import os\nresult = os.environ", {})
    with pytest.raises(ScriptError, match="import of 'importlib'"):
        await run_script("import importlib", {})


async def test_require_isolation_refuses_on_a_non_isolated_worker() -> None:
    if os.name != "posix":
        pytest.skip("POSIX only")
    out = await run_script("result = 1", {})
    iso = out["isolation"]
    if iso["non_root"] and iso["network_isolated"]:
        pytest.skip("this environment isolates scripts already")
    with pytest.raises(ScriptError, match="IsolationError"):
        await run_script("result = 1", {}, require_isolation=True)


async def test_script_runs_as_nobody_without_network_when_root() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        pytest.skip("needs a root worker (Docker)")
    out = await run_script("result = 1", {})
    assert out["isolation"]["uid"] == 65534 and out["isolation"]["non_root"] is True


async def test_script_action_local_and_sidecar() -> None:
    settings = SimpleNamespace(
        flow_scripts_enabled=True,
        flow_script_max_seconds=10,
        flow_script_memory_mb=512,
        flow_script_runner="local",
    )
    acts = FlowActivities(None, settings=settings)
    ok = await acts.flow_action(
        FlowActionInput(
            run_id="r",
            step_id="s",
            step_type="script",
            params={"code": "print('hi')\nresult = sum(inputs['n'])", "inputs": {"n": [1, 2, 3]}},
        )
    )
    assert ok["result"] == 6 and ok["stdout"] == "hi" and ok["runner"] == "local"

    runner = TestClient(create_runner_app("rs"))
    body = json.dumps({"code": "result = 2 + 2", "inputs": {}}).encode()
    assert runner.post("/run", content=body).status_code == 401
    assert (
        runner.post(
            "/run", content=body, headers={"X-VireoNet-Signature": sign("nope", body)}
        ).status_code
        == 401
    )
    good = runner.post("/run", content=body, headers={"X-VireoNet-Signature": sign("rs", body)})
    assert good.status_code == 200 and good.json()["result"] == 4
    bad = json.dumps({"code": "import os"}).encode()
    assert (
        runner.post(
            "/run", content=bad, headers={"X-VireoNet-Signature": sign("rs", bad)}
        ).status_code
        == 422
    )

    def to_runner(req: httpx.Request) -> httpx.Response:
        r = runner.post(
            "/run",
            content=req.content,
            headers={"X-VireoNet-Signature": req.headers["X-VireoNet-Signature"]},
        )
        return httpx.Response(r.status_code, json=r.json())

    side = FlowActivities(
        None,
        settings=SimpleNamespace(
            **{
                **settings.__dict__,
                "flow_script_runner": "sidecar",
                "flow_script_runner_secret": "rs",
                "flow_script_runner_url": "http://script-runner:8100",
            }
        ),
        http_transport=httpx.MockTransport(to_runner),
    )
    via = await side.flow_action(
        FlowActionInput(
            run_id="r",
            step_id="s",
            step_type="script",
            params={"code": "result = inputs['a'] * 2", "inputs": {"a": 21}},
        )
    )
    assert via["result"] == 42 and via["runner"] == "sidecar"


async def test_dns_rebinding_and_private_ranges() -> None:
    deny = ["169.254.0.0/16", "fd00:ec2::254/128"]
    assert "blocked range" in str(_ip_problem("169.254.169.254", deny, False))
    assert _ip_problem("10.1.2.3", deny, False) is None
    assert "private address" in str(_ip_problem("10.1.2.3", deny, True))
    assert "private address" in str(_ip_problem("127.0.0.1", deny, True))
    assert "private address" in str(await _resolved_problem("http://localhost:8000/x", deny, True))
    assert await _resolved_problem("http://127.0.0.1:1/x", deny, False) is None
