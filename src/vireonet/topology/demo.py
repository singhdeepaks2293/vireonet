"""Demo topology for fake mode: a small multi-vendor data centre + edge.

Two Arista spines, four leaves (Cisco NX-OS / IOS-XE / Arista / Juniper),
FortiGate and Palo Alto firewalls, a Nokia SR OS border router and an
Extreme access switch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vireonet.config_store.device_repos import DeviceRepoManager

DEMO_NODES = [
    {"name": "spine-01", "platform": "eos", "role": "spine", "site": "dc1"},
    {"name": "spine-02", "platform": "eos", "role": "spine", "site": "dc1"},
    {"name": "leaf-01", "platform": "ios-xe", "role": "leaf", "site": "dc1"},
    {"name": "leaf-02", "platform": "nxos", "role": "leaf", "site": "dc1"},
    {"name": "leaf-03", "platform": "eos", "role": "leaf", "site": "dc1"},
    {"name": "leaf-04", "platform": "junos", "role": "leaf", "site": "dc1"},
    {"name": "fw-01", "platform": "fortios", "role": "firewall", "site": "dc1"},
    {"name": "fw-02", "platform": "panos", "role": "firewall", "site": "dc1"},
    {"name": "border-01", "platform": "sros", "role": "router", "site": "dc1"},
    {"name": "access-01", "platform": "exos", "role": "access", "site": "dc1"},
]

DEMO_LINKS = [
    ("spine-01", "Ethernet1", "leaf-01", "TenGigabitEthernet1/0/49"),
    ("spine-01", "Ethernet2", "leaf-02", "Ethernet1/49"),
    ("spine-01", "Ethernet3", "leaf-03", "Ethernet49"),
    ("spine-01", "Ethernet4", "leaf-04", "et-0/0/48"),
    ("spine-02", "Ethernet1", "leaf-01", "TenGigabitEthernet1/0/50"),
    ("spine-02", "Ethernet2", "leaf-02", "Ethernet1/50"),
    ("spine-02", "Ethernet3", "leaf-03", "Ethernet50"),
    ("spine-02", "Ethernet4", "leaf-04", "et-0/0/49"),
    ("spine-01", "Ethernet10", "fw-01", "port1"),
    ("spine-02", "Ethernet10", "fw-02", "ethernet1/1"),
    ("fw-01", "port2", "border-01", "1/1/1"),
    ("fw-02", "ethernet1/2", "border-01", "1/1/2"),
    ("leaf-03", "Ethernet1", "access-01", "1:49"),
]


def demo_interfaces(device: str) -> list[str]:
    out = [a_if for a, a_if, _b, _bi in DEMO_LINKS if a == device]
    out += [b_if for _a, _ai, b, b_if in DEMO_LINKS if b == device]
    return out or ["Ethernet1"]


def seed_demo_netbox(netbox: object) -> None:
    """Fake mode: make the demo topology's devices real in the fake NetBox
    (active, with platforms) so demo changes and Excel pushes validate."""
    from vireonet.adapters.protocols import NetBoxDevice

    devices = getattr(netbox, "devices", None)
    if not isinstance(devices, dict):
        return
    for n, node in enumerate(DEMO_NODES, start=21):
        if node["name"] not in devices:
            devices[node["name"]] = NetBoxDevice(
                node["name"],
                f"DEMO{n:04d}",
                "demo",
                node["site"],
                "active",
                node["platform"],
                f"10.0.0.{n}",
            )


def fake_device_repos(config_store: Any, layout: Any = None) -> DeviceRepoManager:
    """Fake mode: device repos in an in-memory Git, with each demo device's
    running config committed on the main branch (main = running config), so
    pre-check records Git commits, rollback can restore from Git and reconcile
    can commit. Deterministic, so the API and worker processes agree."""
    from vireonet.adapters.fake_git import fake_git_factory
    from vireonet.config_store.device_repos import DeviceRepoManager
    from vireonet.config_store.layout import GitLayout
    from vireonet.execution.fake_network import FakeNetwork

    factory, _repos = fake_git_factory()
    lay = layout if isinstance(layout, GitLayout) else GitLayout()
    manager = DeviceRepoManager(factory, config_store=config_store, layout=lay)
    network = FakeNetwork()
    for node in DEMO_NODES:
        name = node["name"]
        repo = factory(lay.repo_for(name))
        repo.seed(
            lay.running_path(name),
            network.running_config(name),
            f"DEMO: initial running config of {name}",
            branch=lay.branch,
        )
    return manager
