"""Turn each adapter's native webhook payload into flow trigger events.

Every event has ``source`` and ``event`` plus source-specific, documented
fields, so trigger conditions read the same whatever sent it. One payload
can become several events (Alertmanager batches alerts).

Endpoint: ``POST /webhooks/flows/source/<source>``.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from typing import Any

Event = dict[str, Any]

ZABBIX_SEVERITY = {
    "0": "not_classified",
    "1": "information",
    "2": "warning",
    "3": "average",
    "4": "high",
    "5": "disaster",
}


def _netbox(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # NetBox webhooks: {event, timestamp, object_type ("dcim.device"), request: {id, user},
    # data, snapshots: {prechange, postchange}}. ``model``/``username``/``request_id``
    # are the deprecated (pre-4.7) spellings and are still honoured.
    event = str(body.get("event", "")).lower()  # created / updated / deleted
    object_type = str(body.get("object_type") or "")
    model = body.get("model") or (object_type.rsplit(".", 1)[-1] if object_type else "")
    request = body.get("request")
    if not isinstance(request, dict):
        request = {}
    data = body.get("data") or {}
    snaps = body.get("snapshots") or {}
    pre = snaps.get("prechange") or {}
    return [
        {
            "source": "netbox",
            "event": event,
            "model": model,  # e.g. "device", "interface", "ipaddress"
            "object_type": object_type,  # e.g. "dcim.device"
            "object": data,
            "name": data.get("name") or data.get("display") or data.get("address") or "",
            "user": request.get("user") or body.get("username") or "",
            "timestamp": body.get("timestamp", ""),
            "changed_fields": sorted(
                k
                for k in set(pre) | set(snaps.get("postchange") or {})
                if pre.get(k) != (snaps.get("postchange") or {}).get(k)
            ),
            "previous": {"object": pre},
        }
    ]


def _zabbix_tags(tags: Any) -> Any:
    """Tags as {tag: value}: from {EVENT.TAGSJSON}, a list of {tag, value}, or "k:v,k:v"."""
    if isinstance(tags, str):
        text = tags.strip()
        if text.startswith("["):
            with contextlib.suppress(ValueError):
                tags = json.loads(text)
        if isinstance(tags, str):  # "key:value,key2:value2"
            parsed: dict[str, str] = {}
            for t in tags.split(","):
                k, _, v = t.strip().partition(":")
                if k:
                    parsed[k] = v
            return parsed
    if isinstance(tags, list):
        return {
            str(t.get("tag", "")): t.get("value", "")
            for t in tags
            if isinstance(t, dict) and t.get("tag")
        }
    return tags


def _zabbix(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # Zabbix webhook media type; recommended parameters (see the manual):
    # event_id, event_value (1 problem, 0 recovery), event_update_status,
    # severity, host, host_ip, trigger_name, trigger_id, item_value, tags, url
    value = str(body.get("event_value", body.get("value", "1")))
    if str(body.get("event_update_status", "0")) == "1":
        kind = "update"
    else:
        kind = "problem" if value == "1" else "recovery"
    sev = str(body.get("severity", body.get("event_nseverity", "")))
    tags = _zabbix_tags(body.get("tags") or body.get("event_tags") or {})
    return [
        {
            "source": "zabbix",
            "event": kind,
            "event_id": str(body.get("event_id", "")),
            "host": body.get("host", body.get("host_name", "")),
            "host_ip": body.get("host_ip", ""),
            "trigger": body.get("trigger_name", body.get("subject", "")),
            "trigger_id": str(body.get("trigger_id", "")),
            "severity": ZABBIX_SEVERITY.get(sev, sev.strip().lower().replace(" ", "_")),
            "severity_level": int(sev) if sev.isdigit() else None,
            "value": body.get("item_value", ""),
            "message": body.get("message", ""),
            "tags": tags,
            "url": body.get("url", ""),
        }
    ]


def _alertmanager(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    out = []
    for a in body.get("alerts") or []:
        labels = a.get("labels") or {}
        out.append(
            {
                "source": "prometheus",
                "event": "firing" if a.get("status") == "firing" else "resolved",
                "alertname": labels.get("alertname", ""),
                "severity": labels.get("severity", ""),
                "instance": labels.get("instance", ""),
                "device": labels.get("device")
                or labels.get("hostname")
                or labels.get("instance", "").split(":")[0],
                "interface": labels.get("ifName") or labels.get("interface", ""),
                "labels": labels,
                "annotations": a.get("annotations") or {},
                "starts_at": a.get("startsAt", ""),
                "fingerprint": a.get("fingerprint", ""),
                "receiver": body.get("receiver", ""),
            }
        )
    return out


def _librenms(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # LibreNMS API transport with a JSON body template: {title, msg, hostname,
    # sysName, severity, state (1 alert, 0 ok, 2 ack), rule, timestamp}
    state = str(body.get("state", "1"))
    return [
        {
            "source": "librenms",
            "event": {
                "1": "alert",
                "0": "recovery",
                "2": "acknowledged",
                "3": "worse",
                "4": "better",
                "5": "changed",
            }.get(state, state),
            "host": body.get("sysName") or body.get("hostname", ""),
            "hostname": body.get("hostname", ""),
            "title": body.get("title", ""),
            "message": body.get("msg", ""),
            "severity": str(body.get("severity", "")).lower(),
            "rule": body.get("rule", body.get("name", "")),
            "timestamp": body.get("timestamp", ""),
        }
    ]


def _prtg(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # PRTG "Execute HTTP Action" with placeholders: device, sensor, status, message, down, lastvalue
    # %status carries the current and previous status, e.g. "Up (was: Down)";
    # classify by the current one only.
    status = str(body.get("status", "")).lower().strip()
    current = status.split("(", 1)[0].split("was", 1)[0].strip(" :,")
    return [
        {
            "source": "prtg",
            "event": "up"
            if current.startswith("up")
            else ("down" if "down" in current else current or status or "state"),
            "host": body.get("device", ""),
            "sensor": body.get("sensor", ""),
            "status": body.get("status", ""),
            "message": body.get("message", ""),
            "value": body.get("lastvalue", ""),
        }
    ]


def _solarwinds(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # SolarWinds Orion alert action "Send a GET or POST request to a web server".
    return [
        {
            "source": "solarwinds",
            "event": str(body.get("event", "triggered")).lower(),
            "host": body.get("node") or body.get("NodeName", ""),
            "alert": body.get("alert") or body.get("AlertName", ""),
            "severity": str(body.get("severity", "")).lower(),
            "message": body.get("message", ""),
            "object": body.get("object", ""),
        }
    ]


GITLAB_MR_ACTIONS = {
    "open": "opened",
    "update": "updated",
    "close": "closed",
    "reopen": "reopened",
    "approved": "approved",
    "unapproved": "unapproved",
}


def _git(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    h = {k.lower(): v for k, v in headers.items()}
    # Gitea (and Gogs) also send X-GitHub-Event, so check their own header first.
    provider, kind = "git", ""
    for header, name in (
        ("x-gitea-event", "gitea"),
        ("x-gogs-event", "gitea"),
        ("x-github-event", "github"),
        ("x-gitlab-event", "gitlab"),
    ):
        if header in h:
            provider, kind = name, str(h[header])
            break
    ref = str(body.get("ref", ""))
    files: set[str] = set()
    for c in body.get("commits") or []:
        for key in ("added", "modified", "removed"):
            files.update(c.get(key) or [])
    project = body.get("project")
    if not isinstance(project, dict):
        project = {}
    repo = body.get("repository") or project or {}
    mr = body.get("pull_request") or body.get("object_attributes") or {}
    if ref or "push" in kind.lower():
        event = "push"
    elif body.get("pull_request"):  # GitHub / Gitea pull_request event
        action = str(body.get("action", "")).lower()
        merged = action == "closed" and mr.get("merged") is True
        event = "merged" if merged else action or kind.lower() or "event"
    elif body.get("object_attributes"):  # GitLab merge request hook
        action = str(mr.get("action", "")).lower()
        event = (
            "merged"
            if action == "merge"
            else GITLAB_MR_ACTIONS.get(action, action) or kind.lower() or "event"
        )
    else:
        event = kind.lower() or "event"
    pusher = body.get("pusher") or {}
    return [
        {
            "source": "git",
            "event": event,
            "provider": provider,
            "repo": project.get("path_with_namespace")
            or repo.get("full_name")
            or repo.get("path_with_namespace")
            or repo.get("name", ""),
            "branch": ref.removeprefix("refs/heads/")
            if ref
            else mr.get("target_branch") or (mr.get("base") or {}).get("ref", ""),
            "files": sorted(files),
            "devices": sorted({f.split("/", 1)[0] for f in files if "/" in f}),
            "author": pusher.get("name")
            or pusher.get("login")
            or pusher.get("username")
            or pusher.get("full_name")
            or body.get("user_name")
            or (body.get("user") or {}).get("name")
            or "",
            "commit": body.get("after", ""),
            "title": mr.get("title", ""),
        }
    ]


def _awx(body: dict[str, Any], headers: dict[str, str]) -> list[Event]:
    # AWX/AAP webhook notification: {id, name, status, url, extra_vars, hosts, ...}
    status = str(body.get("status", "")).lower()
    extra_vars: Any = body.get("extra_vars") or {}
    if isinstance(extra_vars, str):  # AWX sends extra_vars as a JSON string
        try:
            extra_vars = json.loads(extra_vars) or {}
        except ValueError:
            extra_vars = {}
    return [
        {
            "source": "awx",
            "event": "succeeded" if status == "successful" else status or "finished",
            "job_id": body.get("id"),
            "template": body.get("name", ""),
            "status": status,
            "url": body.get("url", ""),
            "hosts": body.get("hosts") or {},
            "extra_vars": extra_vars,
        }
    ]


NORMALISERS: dict[str, Callable[[dict[str, Any], dict[str, str]], list[Event]]] = {
    "netbox": _netbox,
    "zabbix": _zabbix,
    "prometheus": _alertmanager,
    "alertmanager": _alertmanager,
    "librenms": _librenms,
    "prtg": _prtg,
    "solarwinds": _solarwinds,
    "git": _git,
    "github": _git,
    "gitlab": _git,
    "gitea": _git,
    "awx": _awx,
}

# Trigger type for each source (aliases share one trigger type).
TRIGGER_FOR = {"alertmanager": "prometheus", "github": "git", "gitlab": "git", "gitea": "git"}


def normalise(source: str, body: Any, headers: dict[str, str] | None = None) -> list[Event]:
    fn = NORMALISERS.get(source)
    if fn is None:
        raise KeyError(source)
    if not isinstance(body, dict):
        raise ValueError("the body must be a JSON object")
    events = fn(body, headers or {})
    for e in events:
        e.setdefault("raw", body)
    return events
