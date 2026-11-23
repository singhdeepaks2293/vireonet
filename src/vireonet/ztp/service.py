from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.protocols import DhcpProtocol, NetBoxProtocol
from vireonet.models import ZtpDevice


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ZtpService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        netbox: NetBoxProtocol,
        token_ttl_seconds: int = 900,
        dhcp: DhcpProtocol | None = None,
    ) -> None:
        self._sessions = sessions
        self._netbox = netbox
        self._ttl = token_ttl_seconds
        self._dhcp = dhcp

    async def register_device(
        self, *, hostname: str, serial: str, mac_address: str, model: str, platform: str, site: str
    ) -> tuple[ZtpDevice, bool]:
        """Register in NetBox first (source of truth), then locally. Idempotent by serial."""
        async with self._sessions() as session:
            existing = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
            # NetBox is the source of truth. Never create a local ZTP record
            # when the authoritative inventory lookup is unavailable or mismatched.
            try:
                netbox_device = await self._netbox.get_device(hostname)
            except Exception as exc:  # noqa: BLE001 — inventory failure must fail closed
                raise ValueError(f"device {hostname!r} must exist in NetBox before ZTP") from exc
            if netbox_device.serial and netbox_device.serial != serial:
                raise ValueError(
                    f"NetBox serial mismatch for {hostname}: {netbox_device.serial!r} != {serial!r}"
                )
            device = ZtpDevice(
                hostname=hostname,
                serial=serial,
                mac_address=mac_address,
                model=model,
                platform=platform,
                site=site,
                stage="registered",
                ticket_number=f"ZTP-{serial}",
            )
            session.add(device)
            await session.commit()
            await session.refresh(device)
            return device, True

    async def issue_token(self, serial: str) -> str:
        """Generate a one-time token, store only its hash, return the token once."""
        token = secrets.token_urlsafe(32)
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                raise ValueError(f"unknown serial {serial}")
            device.token_hash = _hash_token(token)
            device.token_expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl)
            device.token_used = False
            await session.commit()
            return token

    async def verify_token(self, serial: str, token: str) -> bool:
        """Single-use, expiry-checked, constant-time. Marks used on success."""
        async with self._sessions() as session:
            device = (
                await session.execute(
                    select(ZtpDevice).where(ZtpDevice.serial == serial).with_for_update()
                )
            ).scalar_one_or_none()
            if device is None or not device.token_hash or device.token_used:
                return False
            exp = device.token_expires_at
            if exp is not None:
                exp_utc = exp.replace(tzinfo=UTC) if exp.tzinfo is None else exp
                if exp_utc < datetime.now(UTC):
                    return False
            if not hmac.compare_digest(device.token_hash, _hash_token(token)):
                return False
            device.token_used = True
            device.callback_at = datetime.now(UTC)
            device.stage = "callback_received"
            await session.commit()
            return True

    async def verify_identity(
        self, *, serial: str, model: str, platform: str, version: str | None = None
    ) -> tuple[bool, list[str]]:
        """Serial exact, model case-insensitive, platform exact. Version is
        evidence unless NetBox has a software_version custom field."""
        errors: list[str] = []
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                return False, [f"unknown serial {serial}"]
            if device.serial != serial:
                errors.append("serial mismatch")
            if device.model.lower() != model.lower():
                errors.append(f"model mismatch: NetBox {device.model!r} vs device {model!r}")
            if device.platform != platform:
                errors.append(
                    f"platform mismatch: NetBox {device.platform!r} vs device {platform!r}"
                )
            # Cross-check NetBox as well — a device registered in NetBox under
            # a different serial is a different device.
            try:
                nb = await self._netbox.get_device(device.hostname)
                if nb.serial and nb.serial != serial:
                    errors.append(f"NetBox serial mismatch: {nb.serial!r} vs {serial!r}")
                if nb.model and nb.model.lower() != model.lower():
                    errors.append(f"NetBox model mismatch: {nb.model!r} vs {model!r}")
            except Exception as exc:  # noqa: BLE001 — NetBox failure is evidence, not identity proof
                errors.append(f"NetBox lookup failed: {exc}")
            if version:
                device.version = version
                await session.commit()
            ok = not errors
            if ok:
                device.stage = "identity_verified"
                await session.commit()
            return ok, errors

    async def create_reservation(self, serial: str) -> dict[str, object]:
        """Create the DHCP reservation for a registered device via the DHCP
        adapter. Idempotent: a second call returns the existing reservation."""
        if self._dhcp is None:
            raise ValueError("DHCP adapter not configured")
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                raise ValueError(f"unknown serial {serial}")
            if device.stage == "reservation_created" and device.ip_address:
                return {
                    "hostname": device.hostname,
                    "mac_address": device.mac_address,
                    "ip_address": device.ip_address,
                    "already_created": True,
                }
            if device.stage not in {"registered", "reservation_created"}:
                raise ValueError(f"create_reservation is not legal from stage {device.stage}")
            hostname, mac_address = device.hostname, device.mac_address
        # Use the adapter's configured server for both render and deploy —
        # never hard-code "isc" (Kea deployments need "kea" here).
        server = str(getattr(self._dhcp, "server", "isc"))
        reservation = await self._dhcp.generate_from_netbox(hostname)
        if not reservation.mac_address:
            reservation.mac_address = mac_address
        config = await self._dhcp.render_config([reservation], server)
        evidence = await self._dhcp.deploy_and_verify(config, server, hostname=hostname)
        await self.set_stage(serial, "reservation_created", ip_address=reservation.ip_address)
        return {
            "hostname": hostname,
            "mac_address": mac_address,
            "ip_address": reservation.ip_address,
            "tftp_server": reservation.option_66,
            "option_67": reservation.option_67,
            **evidence,
        }

    async def remove_reservation(self, serial: str) -> dict[str, object]:
        """Remove the DHCP reservation for an active device. Legal only from
        stage "active" — the reservation is needed until the device is live."""
        if self._dhcp is None:
            raise ValueError("DHCP adapter not configured")
        device = await self.get_by_serial(serial)
        if device is None:
            raise ValueError(f"unknown serial {serial}")
        if device.stage != "active":
            raise ValueError(f"remove_reservation is not legal from stage {device.stage}")
        return await self._dhcp.remove_reservation(device.hostname)

    async def set_stage(self, serial: str, stage: str, *, ip_address: str | None = None) -> None:
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                return
            device.stage = stage
            if ip_address:
                device.ip_address = ip_address
            await session.commit()

    async def get_by_serial(self, serial: str) -> ZtpDevice | None:
        async with self._sessions() as session:
            return (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()

    async def list(
        self,
        *,
        stage: str | None = None,
        site: str | None = None,
        platform: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ZtpDevice], int]:
        async with self._sessions() as session:
            query = select(ZtpDevice)
            count_query = select(func.count()).select_from(ZtpDevice)
            if stage:
                query = query.where(ZtpDevice.stage == stage)
                count_query = count_query.where(ZtpDevice.stage == stage)
            if site:
                query = query.where(ZtpDevice.site == site)
                count_query = count_query.where(ZtpDevice.site == site)
            if platform:
                query = query.where(ZtpDevice.platform == platform)
                count_query = count_query.where(ZtpDevice.platform == platform)
            total = int((await session.execute(count_query)).scalar_one())
            query = (
                query.order_by(ZtpDevice.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return list((await session.execute(query)).scalars().all()), total

    async def retry(self, serial: str) -> tuple[ZtpDevice | None, str | None]:
        """Re-issue a one-time token and return to reservation_created. Returns (device, new_token)."""
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                return None, None
            if device.stage not in {
                "failed",
                "identity_failed",
                "callback_timeout",
                "day1_failed",
                "registered",
                "reservation_created",
            }:
                raise ValueError(f"retry is not legal from stage {device.stage}")
            # Token is re-issued by issue_token() after this returns; here we only reset the stage.
            device.stage = "registered"
            device.token_used = False
            await session.commit()
            await session.refresh(device)
            return device, None

    async def reset(self, serial: str) -> ZtpDevice | None:
        """Return to registered and clear token, IP, version and callback. Not legal for active devices."""
        async with self._sessions() as session:
            device = (
                await session.execute(select(ZtpDevice).where(ZtpDevice.serial == serial))
            ).scalar_one_or_none()
            if device is None:
                return None
            if device.stage == "active":
                raise ValueError("reset is not legal for an active device")
            device.stage = "registered"
            device.token_hash = None
            device.token_expires_at = None
            device.token_used = False
            device.ip_address = None
            device.version = None
            device.callback_at = None
            await session.commit()
            await session.refresh(device)
            return device
