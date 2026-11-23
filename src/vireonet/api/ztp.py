from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from vireonet.api.security import require_role
from vireonet.models import ZtpDevice
from vireonet.temporal.ztp_workflow import CallbackPayload, ZtpWorkflowInput
from vireonet.ztp.service import ZtpService

router = APIRouter()


class RegisterDeviceRequest(BaseModel):
    hostname: str
    serial: str
    mac_address: str
    model: str
    platform: str
    site: str


class CallbackRequest(BaseModel):
    token: str
    serial: str
    model: str
    platform: str
    version: str = ""
    ip_address: str = ""


class ZtpDeviceResponse(BaseModel):
    serial: str
    hostname: str
    model: str
    platform: str
    site: str
    stage: str
    ip_address: str | None = None
    version: str | None = None
    callback_at: str | None = None
    ticket_number: str
    netbox_status: str = "unknown"
    dhcp_reservation: str = ""


class PaginatedZtpDevices(BaseModel):
    items: list[ZtpDeviceResponse]
    total: int
    page: int
    page_size: int
    pages: int


def _ztp_service(request: Request) -> ZtpService:
    svc = request.app.state.ztp_service
    if svc is None:
        raise HTTPException(status_code=503, detail="ZTP service not configured")
    return cast(ZtpService, svc)


def _to_response(device: ZtpDevice, *, netbox_status: str = "unknown") -> ZtpDeviceResponse:
    return ZtpDeviceResponse(
        serial=device.serial,
        hostname=device.hostname,
        model=device.model,
        platform=device.platform,
        site=device.site,
        stage=device.stage,
        ip_address=device.ip_address,
        version=device.version,
        callback_at=device.callback_at.isoformat() if device.callback_at else None,
        ticket_number=device.ticket_number,
        netbox_status=netbox_status,
        dhcp_reservation=f"host {device.hostname} {{ fixed-address {device.ip_address}; }}"
        if device.ip_address
        else "",
    )


@router.get("/ztp/devices", response_model=PaginatedZtpDevices)
async def list_devices(
    request: Request,
    stage: str | None = Query(default=None),
    site: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _role: str = Depends(require_role("viewer")),
) -> PaginatedZtpDevices:
    svc = _ztp_service(request)
    devices, total = await svc.list(
        stage=stage, site=site, platform=platform, page=page, page_size=page_size
    )
    netbox = (
        request.app.state.adapters.get("netbox") if hasattr(request.app.state, "adapters") else None
    )
    items: list[ZtpDeviceResponse] = []
    for device in devices:
        status = "unknown"
        if netbox is not None:
            try:
                status = (await netbox.get_device(device.hostname)).status
            except Exception:
                status = "unknown"
        items.append(_to_response(device, netbox_status=status))
    pages = (total + page_size - 1) // page_size if total else 0
    return PaginatedZtpDevices(
        items=items, total=total, page=page, page_size=page_size, pages=pages
    )


@router.post("/ztp/devices", status_code=202)
async def register_device(payload: RegisterDeviceRequest, request: Request) -> dict[str, object]:
    svc = _ztp_service(request)
    device, created = await svc.register_device(
        hostname=payload.hostname,
        serial=payload.serial,
        mac_address=payload.mac_address,
        model=payload.model,
        platform=payload.platform,
        site=payload.site,
    )
    token = await svc.issue_token(payload.serial)
    temporal = request.app.state.temporal_client
    if temporal is not None and created:
        await temporal.start_ztp(
            ZtpWorkflowInput(
                serial=payload.serial,
                hostname=payload.hostname,
                mac_address=payload.mac_address,
                model=payload.model,
                platform=payload.platform,
                site=payload.site,
                ticket_number=device.ticket_number,
            )
        )
    return {
        "serial": device.serial,
        "hostname": device.hostname,
        "ticket_number": device.ticket_number,
        "stage": device.stage,
        "token": token,
        "created": created,
    }


@router.post("/ztp/callback", status_code=202)
async def device_callback(payload: CallbackRequest, request: Request) -> dict[str, str]:
    svc = _ztp_service(request)
    if not await svc.verify_token(payload.serial, payload.token):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    temporal = request.app.state.temporal_client
    if temporal is not None and hasattr(temporal, "signal_ztp_callback"):
        await temporal.signal_ztp_callback(
            payload.serial,
            CallbackPayload(
                serial=payload.serial,
                model=payload.model,
                platform=payload.platform,
                version=payload.version,
                ip_address=payload.ip_address,
            ),
        )
    return {"serial": payload.serial, "status": "callback_accepted"}


@router.get("/ztp/devices/{serial}")
async def get_device(serial: str, request: Request) -> dict[str, object]:
    svc = _ztp_service(request)
    device = await svc.get_by_serial(serial)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "serial": device.serial,
        "hostname": device.hostname,
        "model": device.model,
        "platform": device.platform,
        "site": device.site,
        "stage": device.stage,
        "ip_address": device.ip_address,
        "version": device.version,
    }


@router.post("/ztp/devices/{serial}/reservation", status_code=202)
async def create_reservation(
    serial: str, request: Request, _role: str = Depends(require_role("operator"))
) -> dict[str, object]:
    svc = _ztp_service(request)
    try:
        return await svc.create_reservation(serial)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/ztp/devices/{serial}/reservation")
async def delete_reservation(
    serial: str, request: Request, _role: str = Depends(require_role("operator"))
) -> dict[str, object]:
    svc = _ztp_service(request)
    try:
        return await svc.remove_reservation(serial)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/ztp/devices/{serial}/retry", status_code=202)
async def retry_device(
    serial: str, request: Request, _role: str = Depends(require_role("operator"))
) -> dict[str, object]:
    svc = _ztp_service(request)
    try:
        device, _ = await svc.retry(serial)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    token = await svc.issue_token(serial)
    temporal = request.app.state.temporal_client
    if temporal is not None:
        await temporal.start_ztp(
            ZtpWorkflowInput(
                serial=device.serial,
                hostname=device.hostname,
                mac_address=device.mac_address,
                model=device.model,
                platform=device.platform,
                site=device.site,
                ticket_number=device.ticket_number,
            )
        )
    return {"serial": serial, "stage": device.stage, "token": token}


@router.post("/ztp/devices/{serial}/reset", status_code=202)
async def reset_device(
    serial: str, request: Request, _role: str = Depends(require_role("operator"))
) -> dict[str, object]:
    svc = _ztp_service(request)
    try:
        device = await svc.reset(serial)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return {"serial": serial, "stage": device.stage}
