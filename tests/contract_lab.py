"""Shared LabAdapter contract (E4).

One suite, run against every lab backend (Fake, EVE-NG respx-mocked,
Containerlab with a fake SSH runner): deploy -> wait_ready -> inspect
returns both nodes -> exec returns output -> destroy called. The suite
itself destroys the lab in a ``finally``; the cleanup-on-failure case is
covered separately in tests/test_e4_lab.py via run_lab_smoke.
"""

from __future__ import annotations

from vireonet.adapters.lab import LabAdapter, LabNode

EXPECTED_NODES = {"leaf-01", "leaf-02"}


async def check_lab(
    adapter: LabAdapter,
    topology_path: str,
    lab_name: str,
    *,
    expect_mgmt_ip: bool = True,
    timeout_seconds: int = 30,
) -> None:
    """Run the deploy -> ready -> inspect -> exec -> destroy contract."""
    assert isinstance(adapter, LabAdapter)
    lab_id = await adapter.deploy(topology_path, lab_name)
    assert lab_id
    try:
        nodes = await adapter.wait_ready(lab_id, timeout_seconds=timeout_seconds)
        assert {n.name for n in nodes} == EXPECTED_NODES
        if expect_mgmt_ip:
            assert all(n.mgmt_ip for n in nodes), f"nodes without mgmt IP: {nodes}"
        inspected = await adapter.inspect(lab_id)
        assert {n.name for n in inspected} == EXPECTED_NODES
        assert all(isinstance(n, LabNode) for n in inspected)
        output = await adapter.exec(lab_id, "leaf-01", "show version")
        assert output.strip(), "exec returned no output"
    finally:
        await adapter.destroy(lab_id)
