from __future__ import annotations

from vireonet.adapters.fakes import FakeNotificationAdapter
from vireonet.notifications import NotificationService


async def test_all_four_events_send() -> None:
    fake = FakeNotificationAdapter()
    svc = NotificationService(fake, channel="slack")
    assert await svc.approval_needed("RITM1", "vlan_add", ["leaf-01"])
    assert await svc.change_failed("RITM1", "post-check failed")
    assert await svc.rolled_back("RITM1", "post-check failed")
    assert await svc.change_completed("RITM1")
    assert len(fake.calls) == 4
    assert all(c["ticket"] == "RITM1" for c in fake.calls)
