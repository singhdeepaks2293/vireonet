from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.contract_suite import check_dhcp, check_netbox, check_servicenow
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.fakes import FakeDhcpAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.protocols import DhcpReservation


async def test_servicenow_contract_fake() -> None:
    await check_servicenow(FakeServiceNowAdapter())


async def test_netbox_contract_fake() -> None:
    await check_netbox(FakeNetBoxAdapter())  # type: ignore[arg-type]


async def test_dhcp_contract_fake() -> None:
    await check_dhcp(FakeDhcpAdapter())


async def test_dhcp_contract_real() -> None:
    gitlab = AsyncMock()
    gitlab.create_branch.return_value = "dhcp/update"
    gitlab.commit_files.return_value = "abc123"
    awx = AsyncMock()
    awx.launch_job.return_value = SimpleNamespace(id=7, status="successful")
    awx.poll_job.return_value = SimpleNamespace(id=7, status="successful")
    adapter = DhcpAdapter(
        AdapterConfig(name="dhcp"), gitlab=gitlab, awx=awx, server="isc", tftp_server="10.0.0.5"
    )
    # remove_reservation reads the live config from Git; seed it with two
    # hosts so removing leaf-01 still leaves a valid config behind.
    live = await adapter.render_config(
        [
            DhcpReservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101"),
            DhcpReservation("leaf-02", "aa:bb:cc:dd:ee:00", "10.0.1.102"),
        ],
        "isc",
    )
    gitlab.read_file.return_value = live
    await check_dhcp(adapter)
