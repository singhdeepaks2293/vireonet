"""Vendor controllers (Catalyst Center, CloudVision, Mist, Apstra,
FortiManager, Panorama, Nokia NSP, ExtremeCloud IQ, Meraki, Aruba Central).

One interface for what flows need from a controller: find a device, list
software images, distribute (stage) an image, activate it (install +
reload), do both in one call where the controller can, follow the job, and
make any other API call through the controller's own authentication.

Vendor behaviour that could not be verified against a live controller is
marked ``UNCERTAIN`` in each adapter (see docs/SDLC.md §3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"


class ControllerError(Exception):
    pass


@dataclass
class ControllerDevice:
    id: str
    name: str
    platform: str = ""
    version: str = ""
    serial: str = ""
    model: str = ""
    reachable: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ControllerImage:
    id: str
    name: str
    version: str
    platform: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ControllerJob:
    id: str
    status: str = JOB_RUNNING  # running | succeeded | failed
    progress: str = ""
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in (JOB_SUCCEEDED, JOB_FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "done": self.done}


@dataclass
class ControllerConfig:
    """One configured controller (VIREONET_CONTROLLERS, a JSON list)."""

    name: str
    type: str
    url: str = ""
    username: str = ""
    password: str = ""
    token: str = ""
    verify_tls: bool = True
    timeout_seconds: float = 30.0
    # Vendor scoping: Mist org/site, FortiManager ADOM, CloudVision tenant, …
    options: dict[str, Any] = field(default_factory=dict)


class Controller(Protocol):
    name: str
    vendor: str
    platforms: tuple[str, ...]
    # Does upgrade() exist natively (one API call), or is it distribute + activate?
    native_upgrade: bool

    async def health(self) -> dict[str, Any]: ...
    async def get_device(self, name: str) -> ControllerDevice: ...
    async def list_devices(self) -> list[ControllerDevice]: ...
    async def list_images(self, platform: str = "") -> list[ControllerImage]: ...
    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob: ...
    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob: ...
    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob: ...
    async def job(self, job_id: str) -> ControllerJob: ...
    async def api(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> Any: ...


def find_image(images: list[ControllerImage], version: str, platform: str = "") -> ControllerImage:
    """The image for a version: exact version first, then the version inside the name."""
    wanted = version.strip().lower()
    pool = [i for i in images if not platform or not i.platform or i.platform == platform] or images
    for img in pool:
        if img.version.lower() == wanted:
            return img
    for img in pool:
        if wanted and wanted in img.name.lower():
            return img
    raise ControllerError(
        f"no image for version {version!r} on the controller (have: {sorted({i.version for i in pool})[:10]})"
    )
