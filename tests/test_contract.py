from __future__ import annotations

from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from tests.contract_suite import check_netbox, check_servicenow


async def test_servicenow_contract_fake() -> None:
    await check_servicenow(FakeServiceNowAdapter())


async def test_netbox_contract_fake() -> None:
    await check_netbox(FakeNetBoxAdapter())
