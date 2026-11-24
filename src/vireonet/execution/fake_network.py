"""A small simulated network for fake mode.

The fake execution engine applies change intents to this model and answers
read-only show commands from it, so template pre/post checks behave like
they would on real devices: a VLAN added by a change shows up in
``show vlan``, a rollback restores the backup snapshot, and so on.

Output imitates Cisco IOS (also used for ios-xe, nxos and eos, which
format these commands similarly) and Junos for ``junos``. It is a teaching
and test aid, not an emulator: only the commands below are understood;
anything else returns a "% Invalid input" line, as a real CLI would.
"""

from __future__ import annotations

import copy
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceState:
    vlans: dict[int, str] = field(default_factory=lambda: {1: "default", 100: "web"})
    # vlan id -> access ports
    vlan_ports: dict[int, list[str]] = field(default_factory=dict)
    # prefix -> next hop
    static_routes: dict[str, str] = field(default_factory=dict)
    # acl name -> entries
    acls: dict[str, list[str]] = field(
        default_factory=lambda: {"EDGE-IN": ["10 permit tcp any host 10.20.0.10 eq 443"]}
    )
    # interface -> description
    descriptions: dict[str, str] = field(default_factory=dict)
    # Connected networks: next hops inside these are reachable.
    connected: list[str] = field(default_factory=lambda: ["10.0.0.0/8"])
    # Software (OS upgrade flows): running version, boot variable, images on flash.
    version: str = "17.6.5"
    boot_image: str = "flash:cat9k_iosxe.17.06.05.SPA.bin"
    images: list[str] = field(default_factory=lambda: ["flash:cat9k_iosxe.17.06.05.SPA.bin"])
    uptime_minutes: int = 43_200


# Starting software per platform, and what a controller offers for each.
DEFAULT_VERSIONS = {
    "ios": "17.6.5",
    "ios-xe": "17.6.5",
    "nxos": "10.2.5",
    "eos": "4.30.2F",
    "junos": "22.4R3",
    "fortios": "7.2.8",
    "panos": "10.2.9",
    "sros": "23.10.R1",
    "srlinux": "23.10.1",
    "exos": "32.6.1",
    "aos-cx": "10.12.1000",
}
AVAILABLE_VERSIONS = {
    "ios": ["17.6.5", "17.9.4", "17.12.3"],
    "ios-xe": ["17.6.5", "17.9.4", "17.12.3"],
    "nxos": ["10.2.5", "10.3.4", "10.4.2"],
    "eos": ["4.30.2F", "4.31.3M", "4.32.1F"],
    "junos": ["22.4R3", "23.2R2", "23.4R1"],
    "fortios": ["7.2.8", "7.4.3"],
    "panos": ["10.2.9", "11.1.2"],
    "sros": ["23.10.R1", "24.7.R1"],
    "srlinux": ["23.10.1", "24.7.1"],
    "exos": ["32.6.1", "33.1.1"],
    "aos-cx": ["10.12.1000", "10.13.1010"],
}


_OTHER_VERSION = {
    "nxos": "Cisco Nexus Operating System (NX-OS) Software\n  NXOS: version {v}\n  NXOS image file is: {image}\n",
    "eos": "Arista DCS-7050SX3-48YC8\nSoftware image version: {v}\nArchitecture: x86_64\n",
    "junos": "Hostname: device\nModel: qfx5120-48y\nJunos: {v}\n",
    "fortios": "Version: FortiGate-600F v{v},build2795,240517 (GA.M)\n",
    "panos": "sw-version: {v}\nmodel: PA-3410\n",
    "sros": "TiMOS-C-{v} cpm/x86_64 Nokia 7750 SR Copyright (c) 2000-2024 Nokia.\n",
    "srlinux": "Software Version : v{v}\n",
    "exos": "Image   : ExtremeXOS version {v} by release-manager\n",
    "aos-cx": "Version      : {v}\n",
}


class FakeNetwork:
    def __init__(self) -> None:
        self.devices: dict[str, DeviceState] = {}
        self._snapshots: dict[tuple[str, str], DeviceState] = {}
        self.platforms: dict[str, str] = {}

    def device(self, name: str, platform: str | None = None) -> DeviceState:
        if name not in self.devices:
            state = DeviceState()
            if platform:
                self.platforms[name] = platform
                if platform in DEFAULT_VERSIONS:
                    state.version = DEFAULT_VERSIONS[platform]
                    state.boot_image = state.images[0] = self.image_name(state.version, platform)
            self.devices[name] = state
        elif platform and name not in self.platforms:
            self.platforms[name] = platform
        return self.devices[name]

    # --- lifecycle hooks driven by the fake engine -------------------------

    def snapshot(self, device: str, ticket: str) -> None:
        self._snapshots[(device, ticket)] = copy.deepcopy(self.device(device))

    def restore(self, device: str, ticket: str) -> bool:
        snap = self._snapshots.get((device, ticket))
        if snap is None:
            return False
        self.devices[device] = copy.deepcopy(snap)
        return True

    def apply(self, device: str, intent: dict[str, Any]) -> None:
        state = self.device(device)
        kind = intent.get("change_type")
        if kind == "vlan_add":
            state.vlans[int(intent["vlan_id"])] = str(intent["vlan_name"])
            if intent.get("interfaces"):
                state.vlan_ports[int(intent["vlan_id"])] = list(intent["interfaces"])
        elif kind == "vlan_remove":
            state.vlans.pop(int(intent["vlan_id"]), None)
            state.vlan_ports.pop(int(intent["vlan_id"]), None)
        elif kind == "static_route_add":
            state.static_routes[str(intent["prefix"])] = str(intent["next_hop"])
        elif kind == "static_route_remove":
            state.static_routes.pop(str(intent["prefix"]), None)
        elif kind == "acl_entry_add":
            entries = state.acls.setdefault(str(intent["acl_name"]), [])
            seq = (len(entries) + 1) * 10
            port = f" eq {intent['destination_port']}" if intent.get("destination_port") else ""
            entries.append(
                f"{seq} {intent['action']} {intent.get('protocol', 'tcp')} "
                f"{intent['source']} {intent['destination']}{port}"
            )
        elif kind == "acl_entry_remove":
            entries = state.acls.get(str(intent["acl_name"]), [])
            state.acls[str(intent["acl_name"])] = [
                e for e in entries if not e.startswith(f"{intent['sequence']} ")
            ]
        elif kind == "interface_set" and intent.get("description") is not None:
            state.descriptions[str(intent["interface"])] = str(intent["description"])
        elif kind == "config_push":
            for line in str((intent.get("configs") or {}).get(device, "")).splitlines():
                m = re.match(r"\s*boot system (?:flash )?(\S+)", line)
                if m:
                    state.boot_image = m.group(1) if ":" in m.group(1) else f"flash:{m.group(1)}"

    # --- software (used by the vendor-controller fakes) ----------------------

    @staticmethod
    def image_name(version: str, platform: str = "ios-xe") -> str:
        if platform in ("ios", "ios-xe", ""):
            parts = (version.split(".") + ["0", "0"])[:3]
            try:
                major, minor, patch = (int(x) for x in parts)
            except ValueError:
                return f"flash:{version}.bin"
            return f"flash:cat9k_iosxe.{major:02d}.{minor:02d}.{patch:02d}.SPA.bin"
        names = {
            "nxos": f"bootflash:nxos64-cs.{version}.M.bin",
            "eos": f"flash:EOS-{version}.swi",
            "junos": f"/var/tmp/junos-install-{version}.tgz",
            "fortios": f"FGT-v{version}.out",
            "panos": f"PanOS-{version}",
            "sros": f"cf3:/{version}/both.tim",
            "srlinux": f"srlinux-{version}.bin",
            "exos": f"summitX-{version}.xos",
            "aos-cx": f"ArubaOS-CX_{version}.swi",
        }
        return names.get(platform, f"{platform}-{version}.bin")

    def stage_image(self, device: str, version: str) -> str:
        """Copy (distribute) an image to flash."""
        state = self.device(device)
        image = self.image_name(version, self.platforms.get(device, "ios-xe"))
        if image not in state.images:
            state.images.append(image)
        return image

    def activate_image(self, device: str, version: str) -> None:
        """Set the boot variable to the image and reload: the device comes back
        running that version. The image must have been distributed first."""
        state = self.device(device)
        image = self.image_name(version, self.platforms.get(device, "ios-xe"))
        if image not in state.images:
            raise ValueError(f"{device}: image for {version} is not on flash (distribute it first)")
        state.boot_image, state.version, state.uptime_minutes = image, version, 2

    def running_config(self, device: str) -> str:
        """A plausible IOS-style running config from the simulated state."""
        s = self.device(device)
        lines = [f"hostname {device}", "!"]
        for vid, name in sorted(s.vlans.items()):
            if vid != 1:
                lines += [f"vlan {vid}", f" name {name}", "!"]
        for iface, desc in sorted(s.descriptions.items()):
            lines += [f"interface {iface}", f" description {desc}", "!"]
        for name, entries in sorted(s.acls.items()):
            lines += [f"ip access-list extended {name}", *[f" {e}" for e in entries], "!"]
        for prefix, nh in sorted(s.static_routes.items()):
            lines.append(f"ip route {prefix} {nh}")
        lines.append("end")
        return "\n".join(lines)

    # --- show commands -----------------------------------------------------

    def respond(self, device: str, platform: str, command: str) -> str:
        state = self.device(device, platform)
        cmd = " ".join(command.split())
        junos = platform == "junos"
        for pattern, handler in self._handlers(junos):
            m = re.fullmatch(pattern, cmd, re.IGNORECASE)
            if m:
                return str(handler(state, m))
        return f"% Invalid input detected: {cmd}"

    def _handlers(self, junos: bool):  # type: ignore[no-untyped-def]
        if junos:
            return [
                (r"show vlans(?: \| match \"?(\S+?)\"?)?", self._junos_vlans_match),
                (r"show vlans (\S+)", self._junos_vlan_named),
                (r"show route (\S+)(?: exact)?", self._junos_route),
                (r"show route protocol static", self._junos_static),
                (r"show interfaces descriptions (\S+)", self._descr),
                (r"show configuration firewall filter (\S+)", self._acl),
            ]
        return [
            (r"show version", self._version),
            (r"show boot(?:var)?", self._boot),
            (r"dir(?: flash:?)?", self._dir),
            (r"show vlan id (\d+)", self._vlan_id),
            (r"show vlan(?: brief)?", self._vlan_brief),
            (r"show vlan (\d+)", self._vlan_id),
            (r"show ip route static(?: vrf \S+)?", self._static),
            (r"show ip route (\S+)(?: vrf \S+)?", self._route),
            (r"show interfaces? (\S+) description", self._descr),
            (r"show ip access-lists (\S+)", self._acl),
        ]

    def _version(self, s: DeviceState, _m: re.Match[str]) -> str:
        platform = next(
            (p for d, p in self.platforms.items() if self.devices.get(d) is s), "ios-xe"
        )
        if platform not in ("ios", "ios-xe"):
            return _OTHER_VERSION.get(platform, "Software version: {v}\n").format(
                v=s.version, image=s.boot_image
            )
        days, rest = divmod(s.uptime_minutes, 1440)
        hours, minutes = divmod(rest, 60)
        major, minor, patch = (s.version.split(".") + ["0", "0"])[:3]
        return (
            f"Cisco IOS XE Software, Version {int(major):02d}.{int(minor):02d}.{int(patch):02d}\n"
            "Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), "
            f"Version {s.version}, RELEASE SOFTWARE (fc2)\n"
            f"uptime is {days} days, {hours} hours, {minutes} minutes\n"
            f'System image file is "{s.boot_image}"\n'
        )

    def _boot(self, s: DeviceState, _m: re.Match[str]) -> str:
        return f"BOOT variable = {s.boot_image},12;\nCONFIG_FILE variable = \nManual Boot = no\n"

    def _dir(self, s: DeviceState, _m: re.Match[str]) -> str:
        rows = [
            f"  {i + 1:>6}  -rw-  1068000000  Nov 24 2026 01:00:00 +00:00  {img.split(':', 1)[1]}"
            for i, img in enumerate(s.images)
        ]
        return "Directory of flash:/\n\n" + "\n".join(rows) + "\n"

    @staticmethod
    def _vlan_row(vid: int, name: str, ports: list[str]) -> str:
        return f"{vid:<4} {name:<32} {'active':<9} {', '.join(ports)}".rstrip()

    _VLAN_HEADER = (
        "VLAN Name                             Status    Ports\n"
        "---- -------------------------------- --------- -------------------------------"
    )

    def _vlan_id(self, s: DeviceState, m: re.Match[str]) -> str:
        vid = int(m.group(1))
        if vid not in s.vlans:
            return f"VLAN id {vid} not found in current VLAN database"
        return (
            f"{self._VLAN_HEADER}\n{self._vlan_row(vid, s.vlans[vid], s.vlan_ports.get(vid, []))}"
        )

    def _vlan_brief(self, s: DeviceState, _m: re.Match[str]) -> str:
        rows = [self._vlan_row(v, n, s.vlan_ports.get(v, [])) for v, n in sorted(s.vlans.items())]
        return "\n".join([self._VLAN_HEADER, *rows])

    def _route(self, s: DeviceState, m: re.Match[str]) -> str:
        target = m.group(1)
        try:
            addr = ipaddress.ip_network(target, strict=False)
        except ValueError:
            return "% Invalid input detected"
        for prefix, nh in s.static_routes.items():
            if addr.subnet_of(ipaddress.ip_network(prefix)):  # type: ignore[arg-type]
                return (
                    f'Routing entry for {prefix}\n  Known via "static", distance 1, metric 0\n'
                    f"  Routing Descriptor Blocks:\n  * {nh}\n      Route metric is 0"
                )
        for net in s.connected:
            if addr.subnet_of(ipaddress.ip_network(net)):  # type: ignore[arg-type]
                return f'Routing entry for {net}\n  Known via "connected", distance 0, metric 0'
        return "% Network not in table"

    def _static(self, s: DeviceState, _m: re.Match[str]) -> str:
        lines = ["Codes: S - static"]
        lines += [f"S        {p} [1/0] via {nh}" for p, nh in s.static_routes.items()]
        return "\n".join(lines)

    def _descr(self, s: DeviceState, m: re.Match[str]) -> str:
        iface = m.group(1)
        return f"Interface  Status  Protocol Description\n{iface}  up  up  {s.descriptions.get(iface, '')}"

    def _acl(self, s: DeviceState, m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in s.acls:
            return ""
        return "\n".join([f"Extended IP access list {name}", *[f"    {e}" for e in s.acls[name]]])

    # Junos ----------------------------------------------------------------

    def _junos_rows(self, s: DeviceState) -> list[str]:
        return [f"default-switch  {n:<16} {v:<5}" for v, n in sorted(s.vlans.items())]

    def _junos_vlans_match(self, s: DeviceState, m: re.Match[str]) -> str:
        rows = self._junos_rows(s)
        if m.group(1):
            rows = [r for r in rows if m.group(1) in r]
        return "\n".join(["Routing instance  VLAN name        Tag", *rows])

    def _junos_vlan_named(self, s: DeviceState, m: re.Match[str]) -> str:
        rows = [r for r in self._junos_rows(s) if f" {m.group(1)} " in r]
        return "\n".join(["Routing instance  VLAN name        Tag", *rows])

    def _junos_route(self, s: DeviceState, m: re.Match[str]) -> str:
        out = self._route(s, m)
        if out.startswith("%"):
            return ""
        nh = re.search(r"\* (\S+)", out)
        return (
            f"inet.0: routes\n{m.group(1)}  *[Static/5]\n    > to {nh.group(1) if nh else 'direct'}"
        )

    def _junos_static(self, s: DeviceState, _m: re.Match[str]) -> str:
        return "\n".join(f"{p}  *[Static/5]\n    > to {nh}" for p, nh in s.static_routes.items())


class NetworkStateStore:
    """Fake/demo mode: share the simulated devices between processes (API and
    worker) through the demo_state table, so a change the worker applies is
    what the API's snapshots and controllers see."""

    NAMESPACE = "fake_network"

    def __init__(self, sessions: Any) -> None:
        self._sessions = sessions

    @staticmethod
    def _dump(state: DeviceState) -> dict[str, Any]:
        from dataclasses import asdict

        d = asdict(state)
        d["vlans"] = {str(k): v for k, v in state.vlans.items()}
        d["vlan_ports"] = {str(k): v for k, v in state.vlan_ports.items()}
        return d

    @staticmethod
    def _load_state(d: dict[str, Any]) -> DeviceState:
        d = dict(d)
        d["vlans"] = {int(k): v for k, v in (d.get("vlans") or {}).items()}
        d["vlan_ports"] = {int(k): v for k, v in (d.get("vlan_ports") or {}).items()}
        known = DeviceState.__dataclass_fields__
        return DeviceState(**{k: v for k, v in d.items() if k in known})

    async def load_into(self, network: FakeNetwork, names: list[str]) -> None:
        if not names:
            return
        from sqlalchemy import select

        from vireonet.models import DemoState

        async with self._sessions() as s:
            rows = (
                (
                    await s.execute(
                        select(DemoState).where(
                            DemoState.namespace == self.NAMESPACE, DemoState.key.in_(names)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for r in rows:
            network.devices[r.key] = self._load_state(r.value)

    async def save_from(self, network: FakeNetwork, names: list[str]) -> None:
        from vireonet.models import DemoState

        async with self._sessions() as s:
            for name in names:
                value = self._dump(network.device(name))
                row = await s.get(DemoState, (self.NAMESPACE, name))
                if row is None:
                    s.add(DemoState(namespace=self.NAMESPACE, key=name, value=value))
                else:
                    row.value = value
            await s.commit()
