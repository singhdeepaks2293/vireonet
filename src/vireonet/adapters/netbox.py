from __future__ import annotations

from typing import Any

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter, create_http_client
from vireonet.adapters.errors import (
    AdapterAuthError,
    AdapterConflict,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)
from vireonet.adapters.protocols import NetBoxDevice, NetBoxVlan


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status == 401:
        return AdapterAuthError(adapter, operation, "bad NetBox token (401)")
    if status == 403:
        return AdapterAuthError(adapter, operation, "NetBox token lacks permission (403)")
    if status == 404:
        return AdapterNotFound(adapter, operation, body or "not found")
    if status == 409:
        return AdapterConflict(adapter, operation, body or "conflict")
    if status == 429:
        return AdapterRateLimited(adapter, operation, "rate limited")
    if status in {400, 422}:
        return AdapterValidationError(adapter, operation, body or "validation error")
    if status >= 500:
        return AdapterUnavailable(adapter, operation, f"server error {status}")
    return AdapterUnavailable(adapter, operation, f"unexpected status {status}")


def _device(row: dict[str, Any]) -> NetBoxDevice:
    def slug(v: Any) -> str:
        return str(v.get("slug") if isinstance(v, dict) else v or "")

    primary = row.get("primary_ip4") or row.get("primary_ip")
    ip = primary.get("address") if isinstance(primary, dict) else None
    return NetBoxDevice(
        name=str(row.get("name", "")),
        serial=str(row.get("serial") or ""),
        model=slug(row.get("device_type")),
        site=slug(row.get("site")),
        status=str(
            row.get("status", {}).get("value")
            if isinstance(row.get("status"), dict)
            else row.get("status") or ""
        ),
        platform=slug(row.get("platform")),
        primary_ip=str(ip).split("/")[0] if ip else None,
        id=row.get("id"),
        last_updated=str(row.get("last_updated") or ""),
    )


class NetBoxAdapter(BaseAdapter):
    """Real NetBox REST API adapter.

    Auth: NetBox 4.5+ v2 API tokens (prefixed ``nbt_``) are sent as
    ``Authorization: Bearer <token>``; legacy v1 tokens keep
    ``Authorization: Token <token>``.
    https://netboxlabs.com/docs/netbox/integrations/rest-api/
    """

    def __init__(
        self, config: AdapterConfig, *, token: str = "", correlation_id: str | None = None
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.token = token

    def _auth_header(self) -> str:
        scheme = "Bearer" if self.token.startswith("nbt_") else "Token"
        return f"{scheme} {self.token}"

    def capabilities(self) -> list[str]:
        return ["devices", "interfaces", "vlans", "ips", "journal", "inventory_export", "bulk"]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._request("GET", "/api/dcim/devices/", params={"limit": 1}, idempotent=True)
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotent: bool,
    ) -> Any:
        async def do() -> Any:
            headers = {"Authorization": self._auth_header()}
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
                try:
                    resp = await client.request(
                        method, path, params=params, json=json_body, headers=headers
                    )
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code >= 400:
                    raise _map_status(resp.status_code, self.name, method, resp.text[:500])
                return resp.json() if resp.content else {}

        return await self.execute_with_retry(method, do, idempotent=idempotent)

    async def _get_all(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Follow NetBox pagination (results/next). Returns all rows."""
        out: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, Any] | None = dict(params or {})
        while url:
            data = await self._request("GET", url, params=query, idempotent=True)
            out.extend(data.get("results", []))
            nxt = data.get("next")
            # `next` is an absolute URL; httpx handles it, params must then be empty.
            url = nxt
            query = None
            if nxt and not nxt.startswith("http"):
                url = nxt
        return out

    async def get_device(self, name: str) -> NetBoxDevice:
        rows = await self._get_all("/api/dcim/devices/", {"name": name})
        if not rows:
            raise AdapterNotFound(self.name, "get_device", name)
        return _device(rows[0])

    async def list_devices(self, site: str | None = None) -> list[NetBoxDevice]:
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        return [_device(r) for r in await self._get_all("/api/dcim/devices/", params)]

    async def create_device(
        self,
        name: str,
        serial: str,
        site: str,
        status: str = "staged",
        device_type_id: int | None = None,
        role_id: int | None = None,
    ) -> NetBoxDevice:
        # UNCERTAIN: NetBox requires device_type and role as IDs; there is no
        # lookup by model name in the API. Callers must resolve IDs first
        # (list via /api/dcim/device-types/?model=...), or pass them here.
        if device_type_id is None or role_id is None:
            raise AdapterValidationError(
                self.name,
                "create_device",
                "device_type_id and role_id are required (NetBox needs IDs, not names)",
            )
        sites = await self._get_all("/api/dcim/sites/", {"slug": site})
        if not sites:
            raise AdapterNotFound(self.name, "create_device", f"site {site}")
        body = {
            "name": name,
            "serial": serial,
            "device_type": device_type_id,
            "role": role_id,
            "site": sites[0]["id"],
            "status": status,
        }
        data = await self._request("POST", "/api/dcim/devices/", json_body=body, idempotent=False)
        return _device(data)

    async def update_device_status(
        self, name: str, status: str, expected_last_updated: str | None = None
    ) -> NetBoxDevice:
        dev = await self.get_device(name)
        if (
            expected_last_updated is not None
            and dev.last_updated
            and dev.last_updated != expected_last_updated
        ):
            raise AdapterConflict(
                self.name,
                "update_device_status",
                f"{name} changed since {expected_last_updated} (now {dev.last_updated})",
            )
        if dev.id is None:
            raise AdapterNotFound(self.name, "update_device_status", name)
        data = await self._request(
            "PATCH", f"/api/dcim/devices/{dev.id}/", json_body={"status": status}, idempotent=True
        )
        return _device(data)

    async def list_interfaces(self, device: str) -> list[str]:
        rows = await self._get_all("/api/dcim/interfaces/", {"device": device})
        return [str(r.get("name", "")) for r in rows]

    async def list_cables(self, site: str | None = None) -> list[dict[str, str]]:
        """Interface-to-interface cables: {a_device, a_interface, b_device, b_interface}.

        NetBox 3.3+ (a_terminations / b_terminations lists). Cables to
        circuits, power or front/rear ports are skipped.
        """
        params: dict[str, Any] = {"site": site} if site else {}
        rows = await self._get_all("/api/dcim/cables/", params)
        out: list[dict[str, str]] = []
        for r in rows:
            a = (r.get("a_terminations") or [{}])[0]
            b = (r.get("b_terminations") or [{}])[0]
            if a.get("object_type") != "dcim.interface" or b.get("object_type") != "dcim.interface":
                continue
            ao, bo = a.get("object") or {}, b.get("object") or {}
            out.append(
                {
                    "a_device": str((ao.get("device") or {}).get("name", "")),
                    "a_interface": str(ao.get("name", "")),
                    "b_device": str((bo.get("device") or {}).get("name", "")),
                    "b_interface": str(bo.get("name", "")),
                }
            )
        return out

    async def get_vlan(self, vlan_id: int, site: str | None = None) -> NetBoxVlan | None:
        params: dict[str, Any] = {"vid": vlan_id}
        if site:
            params["site"] = site
        rows = await self._get_all("/api/ipam/vlans/", params)
        if not rows:
            return None
        r = rows[0]
        site_slug = r.get("site", {}).get("slug") if isinstance(r.get("site"), dict) else ""
        return NetBoxVlan(
            vlan_id=int(r.get("vid", vlan_id)),
            name=str(r.get("name", "")),
            site=str(site_slug),
            status=str(
                r.get("status", {}).get("value") if isinstance(r.get("status"), dict) else "active"
            ),
        )

    async def list_vlans(self, site: str | None = None) -> list[NetBoxVlan]:
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        out = []
        for r in await self._get_all("/api/ipam/vlans/", params):
            v = await self.get_vlan(int(r.get("vid", 0)), site=site)
            if v:
                out.append(v)
        return out

    async def check_ip_free(self, ip: str) -> bool:
        rows = await self._get_all("/api/ipam/ip-addresses/", {"address": ip})
        return not rows

    async def allocate_ip(self, prefix: str, description: str) -> str:
        # Find the prefix, then POST to its available-ips endpoint for the next free IP.
        rows = await self._get_all("/api/ipam/prefixes/", {"prefix": prefix})
        if not rows:
            raise AdapterNotFound(self.name, "allocate_ip", f"prefix {prefix}")
        data = await self._request(
            "POST",
            f"/api/ipam/prefixes/{rows[0]['id']}/available-ips/",
            json_body={"description": description, "status": "active"},
            idempotent=False,
        )
        return str(data.get("address", "")).split("/")[0]

    async def add_journal_entry(self, device: str, kind: str, comments: str) -> str:
        dev = await self.get_device(device)
        data = await self._request(
            "POST",
            "/api/extras/journal-entries/",
            json_body={
                "assigned_object_type": "dcim.device",
                "assigned_object_id": dev.id,
                "kind": kind,
                "comments": comments,
            },
            idempotent=False,
        )
        return str(data.get("id", ""))

    async def export_inventory_for_awx(self, site: str | None = None) -> dict[str, object]:
        hosts: dict[str, object] = {}
        for dev in await self.list_devices(site=site):
            hosts[dev.name] = {
                "ansible_host": dev.primary_ip or dev.name,
                "netbox_status": dev.status,
                "site": dev.site,
            }
        return {"all": {"hosts": hosts}}
