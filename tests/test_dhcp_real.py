from __future__ import annotations

import pytest

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.protocols import DhcpReservation


def _adapter() -> DhcpAdapter:
    return DhcpAdapter(AdapterConfig(name="dhcp"), server="isc", tftp_server="10.0.0.5")


async def test_render_isc_includes_option_66_67() -> None:
    cfg = await _adapter().render_config(
        [DhcpReservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101", "10.0.0.5", "leaf-01.cfg")],
        "isc",
    )
    assert (
        "host leaf-01" in cfg
        and "fixed-address 10.0.1.101" in cfg
        and 'filename "leaf-01.cfg"' in cfg
    )


async def test_render_kea_is_valid_json() -> None:
    import json

    cfg = await _adapter().render_config(
        [DhcpReservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101")], "kea"
    )
    assert json.loads(cfg)["Dhcp4"]["reservations"][0]["hostname"] == "leaf-01"


async def test_validate_isc_fallback() -> None:
    a = _adapter()
    assert await a.validate_config(
        "host leaf-01 { hardware ethernet aa:bb:cc:dd:ee:ff; fixed-address 10.0.1.101; }"
    )
    assert not await a.validate_config("garbage")


async def test_deploy_rejects_invalid_config() -> None:
    with pytest.raises(AdapterValidationError):
        await _adapter().deploy("garbage", "isc")


async def test_deploy_git_first_with_mocks() -> None:
    from unittest.mock import AsyncMock

    gitlab = AsyncMock()
    gitlab.create_branch.return_value = "dhcp/update"
    gitlab.commit_files.return_value = "abc123"
    awx = AsyncMock()
    awx.launch_job.return_value = type("J", (), {"id": 7, "status": "successful"})()
    a = DhcpAdapter(AdapterConfig(name="dhcp"), gitlab=gitlab, awx=awx)
    cfg = await a.render_config(
        [DhcpReservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101")], "isc"
    )
    evidence = await a.deploy(cfg, "isc")
    assert evidence["gitlab_commit"] == "abc123" and evidence["reload_job_id"] == 7
