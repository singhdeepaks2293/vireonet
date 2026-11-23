from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from vireonet.adapters.protocols import NetBoxProtocol
from vireonet.intent import ChangeIntent
from vireonet.policy import PolicyEngine, PolicyResult


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, object] = field(default_factory=dict)


async def validate_intent(
    intent: ChangeIntent,
    netbox: NetBoxProtocol,
    policy: PolicyEngine,
    *,
    now: datetime | None = None,
    emergency: bool = False,
) -> ValidationResult:
    """Validate against NetBox (existence / state / conflict) then policy.

    Policy runs even when NetBox validation fails, so the caller gets both
    sets of reasons in one evidence payload.
    """
    errors: list[str] = []
    evidence: dict[str, object] = {"devices_checked": [], "vlan_checked": None}

    # 1. Devices exist. ZTP devices may be staged/planned; all other changes
    #    require an active device.
    for name in intent.devices:
        try:
            dev = await netbox.get_device(name)
        except Exception as exc:  # AdapterNotFound is the expected path; keep type in evidence
            errors.append(f"device {name} not found in NetBox: {exc}")
            continue
        evidence["devices_checked"] = [*evidence["devices_checked"], name]  # type: ignore[list-item]
        if intent.change_type != "ztp_device" and dev.status != "active":
            errors.append(f"device {name} is {dev.status}, not active")
        if intent.change_type == "ztp_device" and dev.status == "active":
            errors.append(f"device {name} is already active, ZTP not applicable")

    # 2. Per-type NetBox checks.
    if intent.change_type == "vlan_add":
        vlan = await netbox.get_vlan(intent.vlan_id, site=intent.site)  # type: ignore[attr-defined]
        evidence["vlan_checked"] = intent.vlan_id  # type: ignore[attr-defined]
        if vlan is not None and vlan.name != intent.vlan_name:  # type: ignore[attr-defined]
            errors.append(f"VLAN {intent.vlan_id} already exists as {vlan.name!r}")
        for iface in intent.interfaces:  # type: ignore[attr-defined]
            if iface not in await netbox.list_interfaces(intent.devices[0]):
                errors.append(f"interface {iface} not found on {intent.devices[0]}")
    if intent.change_type == "vlan_remove":
        vlan = await netbox.get_vlan(intent.vlan_id, site=intent.site)  # type: ignore[attr-defined]
        if vlan is None:
            errors.append(f"VLAN {intent.vlan_id} does not exist in NetBox")
    if intent.change_type == "interface_set":
        ifaces = await netbox.list_interfaces(intent.devices[0])
        if intent.interface not in ifaces:  # type: ignore[attr-defined]
            errors.append(f"interface {intent.interface} not found on {intent.devices[0]}")  # type: ignore[attr-defined]
    if intent.change_type == "ztp_device":
        # Serial must be unique in NetBox.
        for dev in await netbox.list_devices(site=intent.site):
            if dev.serial == intent.serial and dev.name != intent.devices[0]:  # type: ignore[attr-defined]
                errors.append(f"serial {intent.serial} already registered as {dev.name}")

    # 3. Policy.
    policy_result: PolicyResult = policy.evaluate(intent, now=now, emergency=emergency)
    errors.extend(policy_result.denials)
    evidence["policy_denials"] = policy_result.denials
    return ValidationResult(ok=not errors, errors=errors, evidence=evidence)
