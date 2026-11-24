"""Pre/Post checks: saved profiles, snapshots (capture + store), compare."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select

from vireonet.models import PrePostProfile, PrePostSnapshot
from vireonet.prepost.engine import ProfileError, command_for, compare, ensure_profile, evaluate

MAX_RAW = 200_000

BUILTIN: list[dict[str, Any]] = [
    {
        "name": "VLAN baseline",
        "description": "VLANs present and active; after a change no VLAN disappeared and names did not change.",
        "commands": [
            {
                "id": "vlans",
                "command": {"default": "show vlan brief", "junos": "show vlans"},
                "key": ["vlan_id"],
                "ignore_fields": ["interfaces"],
            }
        ],
        "tests": [
            {
                "name": "vlan-1-present",
                "command": "vlans",
                "type": "row_exists",
                "where": {"vlan_id": "1"},
            },
            {
                "name": "all-vlans-active",
                "command": "vlans",
                "type": "all_rows",
                "expect": {"status": {"op": "equals", "value": "active"}},
            },
            {"name": "no-vlan-removed", "command": "vlans", "type": "no_rows_removed"},
            {
                "name": "vlan-names-unchanged",
                "command": "vlans",
                "type": "unchanged",
                "fields": ["vlan_name"],
            },
            {"name": "vlan-count-stable", "command": "vlans", "type": "delta", "max_abs": 5},
        ],
    },
    {
        "name": "Software version",
        "description": "The running software before and after (OS upgrades): parsed version, uptime ignored.",
        "commands": [
            {
                "id": "version",
                "command": {"default": "show version"},
                "key": [],
                "ignore_fields": [
                    "uptime",
                    "uptime_years",
                    "uptime_weeks",
                    "uptime_days",
                    "uptime_hours",
                    "uptime_minutes",
                    "restarted",
                ],
                "raw_ignore": [r"uptime is .*", r"[Uu]ptime: .*"],
            }
        ],
        "tests": [
            {
                "name": "version-parsed",
                "command": "version",
                "type": "count",
                "op": "gte",
                "value": 1,
            }
        ],
    },
]


def _snap_dict(r: PrePostSnapshot, *, data: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(r.id),
        "profile": r.profile,
        "profile_version": r.profile_version,
        "label": r.label,
        "ticket": r.ticket,
        "devices": r.devices,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "results": r.results,
        "failed": sum(not x.get("ok") for x in r.results or []),
    }
    if data:
        out["data"] = r.data
    return out


class PrePostService:
    def __init__(
        self,
        sessions: Any,
        *,
        engine: Any = None,
        netbox: Any = None,
        textfsm_dir: str = "policies/textfsm",
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._netbox = netbox
        self._textfsm_dir = textfsm_dir

    # --- profiles ---------------------------------------------------------------------

    async def _seed(self) -> None:
        async with self._sessions() as s:
            have = set((await s.execute(select(PrePostProfile.name))).scalars().all())
            for p in BUILTIN:
                if p["name"] not in have:
                    s.add(
                        PrePostProfile(
                            name=p["name"],
                            description=p["description"],
                            definition=p,
                            builtin=True,
                            updated_by="vireonet",
                        )
                    )
            await s.commit()

    async def list_profiles(self) -> list[dict[str, Any]]:
        await self._seed()
        async with self._sessions() as s:
            rows = (
                (await s.execute(select(PrePostProfile).order_by(PrePostProfile.name)))
                .scalars()
                .all()
            )
        return [
            {
                "name": r.name,
                "description": r.description,
                "version": r.version,
                "builtin": r.builtin,
                "commands": len(r.definition.get("commands", [])),
                "tests": len(r.definition.get("tests", [])),
                "updated_by": r.updated_by,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]

    async def get_profile(self, name: str) -> dict[str, Any]:
        await self._seed()
        async with self._sessions() as s:
            r = await s.get(PrePostProfile, name)
        if r is None:
            raise KeyError(name)
        return {
            **r.definition,
            "name": r.name,
            "description": r.description,
            "version": r.version,
            "builtin": r.builtin,
        }

    async def save_profile(self, profile: dict[str, Any], user: str) -> dict[str, Any]:
        p = ensure_profile({k: v for k, v in profile.items() if k not in ("version", "builtin")})
        async with self._sessions() as s:
            r = await s.get(PrePostProfile, p["name"])
            if r is None:
                s.add(
                    PrePostProfile(
                        name=p["name"],
                        description=str(p.get("description", "")),
                        definition=p,
                        updated_by=user,
                    )
                )
            else:
                r.definition, r.description, r.version, r.updated_by = (
                    p,
                    str(p.get("description", "")),
                    r.version + 1,
                    user,
                )
            await s.commit()
        return await self.get_profile(p["name"])

    async def delete_profile(self, name: str) -> None:
        async with self._sessions() as s:
            r = await s.get(PrePostProfile, name)
            if r is None:
                raise KeyError(name)
            await s.delete(r)
            await s.commit()

    # --- snapshots ------------------------------------------------------------------------

    async def _platform(self, device: str) -> str:
        if self._netbox is None:
            return "ios"
        from vireonet.rendering.platforms import normalise_platform

        d = await self._netbox.get_device(device)
        return normalise_platform(d.platform) or d.platform

    async def capture(
        self,
        profile_name: str,
        devices: list[str],
        *,
        label: str = "adhoc",
        ticket: str = "",
        user: str = "",
        pre_id: str | None = None,
    ) -> dict[str, Any]:
        from vireonet.execution.types import DeviceRef, StepRequest
        from vireonet.textfsm_lib import TextFsmError, parse

        if self._engine is None:
            raise RuntimeError("no execution engine to run the commands")
        profile = await self.get_profile(profile_name)
        data: dict[str, Any] = {"devices": {}, "label": label}
        for device in devices:
            platform = await self._platform(device)
            cmds = {c["id"]: command_for(c, platform) for c in profile["commands"]}
            req = StepRequest(
                ticket_number=ticket or f"SNAP-{uuid.uuid4().hex[:6]}",
                device=DeviceRef(name=device, platform=platform),
                step="run_commands",
                change_type="prepost",
                extra_vars={"commands": json.dumps(list(cmds.values()))},
                dry_run=True,
            )
            res = await self._engine.run_commands(req)
            outputs = list(res.output.get("command_outputs") or []) if res.ok else []
            per: dict[str, Any] = {}
            for (cid, cmd), raw in zip(
                cmds.items(), outputs + [None] * (len(cmds) - len(outputs)), strict=True
            ):
                entry: dict[str, Any] = {
                    "command": cmd,
                    "platform": platform,
                    "raw": (raw or "")[:MAX_RAW] if raw is not None else None,
                }
                if raw is None:
                    entry["error"] = "the command did not run"
                elif str(raw).startswith("% Invalid"):
                    entry["error"] = "the device rejected the command"
                else:
                    try:
                        r = parse(platform, cmd, raw, user_dir=self._textfsm_dir)
                        entry["rows"], entry["template"] = r.rows, r.template
                    except TextFsmError as exc:
                        entry["error"] = str(exc)
                per[cid] = entry
            data["devices"][device] = per
        pre = await self.get_snapshot(pre_id) if pre_id else None
        results = evaluate(profile, data, pre["data"] if pre else None)
        async with self._sessions() as s:
            row = PrePostSnapshot(
                profile=profile_name,
                profile_version=int(profile.get("version", 1)),
                label=label,
                ticket=ticket,
                devices=list(devices),
                data=data,
                results=results,
                created_by=user,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _snap_dict(row)

    async def get_snapshot(self, snap_id: str) -> dict[str, Any]:
        async with self._sessions() as s:
            r = await s.get(PrePostSnapshot, uuid.UUID(snap_id))
        if r is None:
            raise KeyError(snap_id)
        return _snap_dict(r)

    async def list_snapshots(
        self, *, profile: str = "", ticket: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self._sessions() as s:
            q = select(PrePostSnapshot).order_by(PrePostSnapshot.created_at.desc()).limit(limit)
            if profile:
                q = q.where(PrePostSnapshot.profile == profile)
            if ticket:
                q = q.where(PrePostSnapshot.ticket == ticket)
            rows = (await s.execute(q)).scalars().all()
        return [_snap_dict(r, data=False) for r in rows]

    async def compare(self, pre_id: str, post_id: str) -> dict[str, Any]:
        pre, post = await self.get_snapshot(pre_id), await self.get_snapshot(post_id)
        if pre["profile"] != post["profile"]:
            raise ProfileError(
                [f"snapshots use different profiles ({pre['profile']} vs {post['profile']})"]
            )
        profile = await self.get_profile(post["profile"])
        result = compare(profile, pre["data"], post["data"])
        return {
            **result,
            "pre": {k: pre[k] for k in ("id", "label", "created_at", "ticket")},
            "post": {k: post[k] for k in ("id", "label", "created_at", "ticket")},
            "profile": post["profile"],
        }
