from __future__ import annotations

import base64
import time
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
from vireonet.adapters.protocols import SNRequest

# UNCERTAIN — ServiceNow Table API field names are instance/catalog-specific.
# These are the defaults for a standard sc_req_item / change_request install.
# If your instance renames them, change only this dict, not the methods below.
SN_TABLES = {
    "request_item": "sc_req_item",
    "change": "change_request",
    "approval": "sysapproval_approver",
}


def table_for(ticket: str) -> str:
    """Change Requests (CHG...) live in change_request; everything else is a
    request item (RITM...)."""
    return SN_TABLES["change"] if ticket.upper().startswith("CHG") else SN_TABLES["request_item"]


# ServiceNow state values are numeric strings by default (e.g. 1=new, 3=closed).
# Map our words to the instance's values here.
# sc_req_item states: 1 Open, 2 Work in Progress, 3 Closed Complete,
# 4 Closed Incomplete, 7 Closed Skipped ("resolved" has no RITM state: Closed Complete).
SN_STATE_MAP = {
    "open": "1",
    "in_progress": "2",
    "closed": "3",
    "resolved": "3",
    "closed_incomplete": "4",
}


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status == 401:
        return AdapterAuthError(adapter, operation, "unauthorised (401)")
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


class ServiceNowAdapter(BaseAdapter):
    """Real ServiceNow Table API adapter. OAuth client-credentials or basic auth.

    Base URL is the instance (or MID Server) URL, e.g. https://instance.service-now.com.
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        auth_mode: str = "oauth",
        client_id: str = "",
        client_secret: str = "",
        username: str = "",
        password: str = "",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.auth_mode = auth_mode
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._notes_seen: set[str] = (
            set()
        )  # process-local dedup; durable dedup is the audit log (M3)

    def capabilities(self) -> list[str]:
        return [
            "read_request",
            "update_state",
            "work_notes",
            "close",
            "attach_file",
            "approval_read",
        ]

    async def health_check(self) -> AdapterHealth:
        start = time.monotonic()
        try:
            await self._request(
                "GET",
                f"/api/now/table/{SN_TABLES['request_item']}",
                params={"sysparm_limit": "1"},
                idempotent=True,
            )
            return AdapterHealth(
                name=self.name,
                status="ok",
                latency_ms=(time.monotonic() - start) * 1000,
                capabilities=self.capabilities(),
            )
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        if self.auth_mode != "oauth":
            raise AdapterAuthError(self.name, "oauth", "oauth mode required for token fetch")
        async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
            try:
                resp = await client.post(
                    "/oauth_token.do",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
            except httpx.TimeoutException as exc:
                raise AdapterTimeout(self.name, "oauth", str(exc)) from exc
            if resp.status_code != 200:
                raise AdapterAuthError(
                    self.name, "oauth", f"token endpoint returned {resp.status_code}"
                )
            data = resp.json()
            self._token = str(data["access_token"])
            self._token_expires_at = time.time() + float(data.get("expires_in", 1800))
            return self._token

    def _auth_headers(self, token: str | None) -> dict[str, str]:
        if self.auth_mode == "basic":
            raw = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        idempotent: bool,
        raw: bool = False,
        _retried_auth: bool = False,
    ) -> Any:
        async def do() -> Any:
            token = await self._get_token() if self.auth_mode == "oauth" else None
            headers = self._auth_headers(token)
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
                try:
                    resp = await client.request(
                        method,
                        path,
                        params=params,
                        json=json_body,
                        files=files,
                        data=data,
                        headers=headers,
                    )
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code == 401 and self.auth_mode == "oauth" and not _retried_auth:
                    self._token = None  # force refresh once, then retry below
                    raise AdapterAuthError(self.name, method, "token expired")
                if resp.status_code >= 400:
                    raise _map_status(resp.status_code, self.name, method, resp.text[:500])
                if raw:
                    return resp.content
                return resp.json() if resp.content else {}

        try:
            return await self.execute_with_retry(method, do, idempotent=idempotent)
        except AdapterAuthError:
            if _retried_auth:
                raise
            # One auth refresh + retry, for idempotent calls only.
            if not idempotent:
                raise
            self._token = None
            return await self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                files=files,
                data=data,
                idempotent=idempotent,
                raw=raw,
                _retried_auth=True,
            )

    @staticmethod
    def _to_request(row: dict[str, Any], variables: dict[str, str] | None = None) -> SNRequest:
        # sysparm_display_value=true returns {"value": ..., "display_value": ...} for refs.
        def val(v: Any) -> str:
            return str(v["value"]) if isinstance(v, dict) else str(v or "")

        def display(v: Any) -> str:
            return (
                str(v.get("display_value") or v.get("value") or "")
                if isinstance(v, dict)
                else str(v or "")
            )

        return SNRequest(
            ticket=val(row.get("number")),
            short_description=display(row.get("short_description")),
            state=val(row.get("state")),
            state_display=display(row.get("state")),
            planned_start=val(row.get("start_date")),
            planned_end=val(row.get("end_date")),
            requested_by=val(row.get("requested_for") or row.get("opened_by")),
            sys_id=val(row.get("sys_id")),
            variables=variables or {},
        )

    async def _fetch_variables(self, sys_id: str) -> dict[str, str]:
        # sc_item_option_mtom links request_item -> sc_item_option; each option
        # holds name/value. Field names here are the standard ones; see SN_TABLES note.
        out: dict[str, str] = {}
        data = await self._request(
            "GET",
            "/api/now/table/sc_item_option_mtom",
            params={"sysparm_query": f"request_item={sys_id}", "sysparm_fields": "sc_item_option"},
            idempotent=True,
        )
        for row in data.get("result", []):
            opt_id = (
                row.get("sc_item_option", {}).get("value")
                if isinstance(row.get("sc_item_option"), dict)
                else row.get("sc_item_option")
            )
            if not opt_id:
                continue
            opt = await self._request(
                "GET",
                f"/api/now/table/sc_item_option/{opt_id}",
                # Without display_value, reference fields have no display_value.
                params={"sysparm_display_value": "all"},
                idempotent=True,
            )
            r = opt.get("result", {})
            if r.get("item_option_new"):
                name = (
                    r["item_option_new"].get("display_value")
                    if isinstance(r["item_option_new"], dict)
                    else str(r["item_option_new"])
                )
                out[str(name)] = str(r.get("value") or "")
        return out

    async def get_request(self, ticket: str) -> SNRequest:
        data = await self._request(
            "GET",
            f"/api/now/table/{table_for(ticket)}",
            params={
                "sysparm_query": f"number={ticket}",
                # "all": every field as {"value", "display_value"} (raw + label).
                "sysparm_display_value": "all",
                "sysparm_limit": "1",
            },
            idempotent=True,
        )
        rows = data.get("result", [])
        if not rows:
            raise AdapterNotFound(self.name, "get_request", ticket)
        row = rows[0]
        variables = await self._fetch_variables(
            str(
                row.get("sys_id", {}).get("value")
                if isinstance(row.get("sys_id"), dict)
                else row.get("sys_id")
            )
        )
        return self._to_request(row, variables)

    async def get_request_by_sys_id(self, sys_id: str) -> SNRequest:
        data = await self._request(
            "GET",
            f"/api/now/table/{SN_TABLES['request_item']}/{sys_id}",
            params={"sysparm_display_value": "all"},
            idempotent=True,
        )
        row = data.get("result", {})
        if not row:
            raise AdapterNotFound(self.name, "get_request_by_sys_id", sys_id)
        return self._to_request(row, await self._fetch_variables(sys_id))

    async def update_state(
        self, ticket: str, state: str, work_notes: str | None = None
    ) -> SNRequest:
        req = await self.get_request(ticket)
        body: dict[str, Any] = {"state": SN_STATE_MAP.get(state, state)}
        if work_notes:
            body["work_notes"] = work_notes
        await self._request(
            "PATCH",
            f"/api/now/table/{table_for(ticket)}/{req.sys_id}",
            json_body=body,
            idempotent=True,
        )
        req.state = state
        return req

    async def add_work_note(self, ticket: str, note: str, idempotency_key: str) -> bool:
        if idempotency_key in self._notes_seen:
            return False
        # Only the work note: re-writing the state would fire state business rules.
        req = await self.get_request(ticket)
        await self._request(
            "PATCH",
            f"/api/now/table/{table_for(ticket)}/{req.sys_id}",
            json_body={"work_notes": note},
            idempotent=True,
        )
        self._notes_seen.add(idempotency_key)
        return True

    async def close(self, ticket: str, close_notes: str) -> SNRequest:
        req = await self.get_request(ticket)
        body: dict[str, Any] = {"state": SN_STATE_MAP["closed"], "close_notes": close_notes}
        if table_for(ticket) == SN_TABLES["change"]:
            # Change Requests need a close code; "Review" (0) then "Closed" (3) is
            # the usual model — set Closed with a successful close code.
            body["close_code"] = "successful"
        await self._request(
            "PATCH",
            f"/api/now/table/{table_for(ticket)}/{req.sys_id}",
            json_body=body,
            idempotent=True,
        )
        req.state = "closed"
        return req

    async def attach_file(self, ticket: str, filename: str, content: bytes) -> str:
        req = await self.get_request(ticket)
        # Attachment API: POST /api/now/attachment/upload is the multipart form
        # (table_name, table_sys_id, then the file part last). /attachment/file
        # takes a raw binary body instead. Not idempotent: no retry.
        import mimetypes

        # Explicit types for what VireoNet attaches: slim containers often have
        # no /etc/mime.types, so mimetypes alone would say octet-stream for .xlsx.
        known = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
            ".csv": "text/csv",
            ".json": "application/json",
            ".txt": "text/plain",
            ".pdf": "application/pdf",
        }
        ext = filename[filename.rfind(".") :].lower() if "." in filename else ""
        mime = known.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data = await self._request(
            "POST",
            "/api/now/attachment/upload",
            data={"table_name": table_for(ticket), "table_sys_id": req.sys_id},
            files={"uploadFile": (filename, content, mime)},
            idempotent=False,
        )
        return str(data.get("result", {}).get("sys_id", ""))

    async def list_attachments(self, ticket: str) -> list[dict[str, Any]]:
        """Attachments on the request item: [{id, filename, size, content_type}]."""
        req = await self.get_request(ticket)
        data = await self._request(
            "GET",
            "/api/now/attachment",
            params={"sysparm_query": f"table_sys_id={req.sys_id}", "sysparm_limit": "100"},
            idempotent=True,
        )
        return [
            {
                "id": str(a.get("sys_id", "")),
                "filename": str(a.get("file_name", "")),
                "size": int(a.get("size_bytes") or 0),
                "content_type": str(a.get("content_type", "")),
            }
            for a in data.get("result", [])
        ]

    async def download_attachment(self, attachment_id: str) -> bytes:
        return bytes(
            await self._request(
                "GET", f"/api/now/attachment/{attachment_id}/file", idempotent=True, raw=True
            )
        )

    async def get_approval_state(self, ticket: str) -> str:
        req = await self.get_request(ticket)
        data = await self._request(
            "GET",
            f"/api/now/table/{SN_TABLES['approval']}",
            params={"sysparm_query": f"sysapproval={req.sys_id}", "sysparm_limit": "1"},
            idempotent=True,
        )
        rows = data.get("result", [])
        if not rows:
            return "not_requested"
        state = rows[0].get("state")
        return str(state.get("display_value") if isinstance(state, dict) else state or "unknown")
