from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx


class ItsmError(RuntimeError):
    """The ITSM tool failed or returned something unusable."""


class ItsmAuthError(ItsmError):
    """The ITSM tool rejected the credentials (HTTP 401/403)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Ticket:
    key: str  # RITM0001234, NET-42, 1234, INC000000000123 ...
    summary: str = ""
    status: str = ""
    requester: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Attachment:
    id: str
    filename: str
    size: int = 0
    content_type: str = ""
    # Direct download URL when the ITSM tool gives one (Jira "content",
    # Freshservice "attachment_url").
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ItsmProvider(Protocol):
    name: str

    async def health(self) -> dict[str, Any]: ...
    async def get_ticket(self, key: str) -> Ticket: ...
    async def list_attachments(self, key: str) -> list[Attachment]: ...
    async def download_attachment(self, key: str, attachment: Attachment) -> bytes: ...
    async def attach_file(self, key: str, filename: str, content: bytes) -> str: ...
    async def add_comment(self, key: str, text: str) -> None: ...
    async def close(self, key: str, note: str) -> None: ...


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class HttpItsm:
    """Shared HTTP plumbing for REST-based ITSM tools."""

    name = "itsm"

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 15.0,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ItsmError(f"{self.name}: base URL is required")
        self.base_url = base_url.rstrip("/")
        self._kw: dict[str, Any] = {
            "timeout": timeout,
            "verify": verify_tls,
            "headers": headers or {},
        }
        if auth:
            self._kw["auth"] = auth
        if transport is not None:
            self._kw["transport"] = transport

    async def _send(self, method: str, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, **self._kw) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ItsmError(f"{self.name}: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ItsmAuthError(
                f"{self.name}: authentication failed ({resp.status_code})", resp.status_code
            )
        if resp.status_code == 404:
            raise ItsmError(f"{self.name}: not found: {path}")
        if resp.status_code >= 400:
            raise ItsmError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
        if raw:
            return resp.content
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ItsmError(f"{self.name}: response is not JSON") from exc
