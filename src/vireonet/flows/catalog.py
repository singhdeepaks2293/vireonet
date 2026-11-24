"""Every flow action and trigger, described as data.

The UI builds its action palette and forms from this catalog, and the
validator checks saved flows against it, so the two can never disagree.

Field kinds: text, textarea, code, number, bool, select, list (of strings),
kv (string map), json (any value), condition, steps (nested actions).
Every text-like field accepts templates such as ``{{ vars.device }}``.
"""

from __future__ import annotations

from typing import Any

from vireonet.flows.expr import OPERATORS


def F(name: str, label: str, kind: str = "text", **kw: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "kind": kind, **kw}


SAVE_AS = F(
    "save_as",
    "Save result as variable",
    help="The result is always at steps.<step id>; also store it in vars.<name>.",
)

CATEGORIES = [
    {"id": "scripting", "label": "Variables and scripting", "color": "#3daee9"},
    {"id": "control", "label": "Flow control", "color": "#f67400"},
    {"id": "network", "label": "Network", "color": "#27ae60"},
    {"id": "itsm", "label": "ITSM", "color": "#9b59b6"},
    {"id": "integrations", "label": "API, scripts and people", "color": "#da4453"},
    {"id": "controllers", "label": "Vendor controllers", "color": "#1d99f3"},
    {"id": "intelligence", "label": "Intelligence (neural networks)", "color": "#8e44ad"},
]

STEPS: dict[str, dict[str, Any]] = {
    # --- variables and scripting --------------------------------------------------
    "set_variable": {
        "category": "scripting",
        "label": "Set variable",
        "icon": "variable",
        "description": "Store a value (text, number, list or an expression) in vars.<name>.",
        "fields": [
            F("name", "Variable name", required=True),
            F("value", "Value", "json", help="Text, a number, a list, or {{ expression }}."),
        ],
    },
    "append_to_list": {
        "category": "scripting",
        "label": "Add to list",
        "icon": "list",
        "description": "Append a value to the list in vars.<name> (created if missing).",
        "fields": [F("name", "List variable", required=True), F("value", "Value", "json")],
    },
    "log": {
        "category": "scripting",
        "label": "Log message",
        "icon": "log",
        "description": "Write a line to the run log.",
        "fields": [
            F("message", "Message", "textarea", required=True),
            F("level", "Level", "select", options=["info", "warning", "error"], default="info"),
        ],
    },
    "wait": {
        "category": "scripting",
        "label": "Wait",
        "icon": "wait",
        "description": "Pause for a number of seconds (durable: survives restarts).",
        "fields": [F("seconds", "Seconds", "number", required=True, default=60)],
    },
    "stop": {
        "category": "scripting",
        "label": "Stop flow",
        "icon": "stop",
        "description": "End the run now with success.",
        "fields": [F("message", "Reason", "text")],
    },
    "fail": {
        "category": "scripting",
        "label": "Fail",
        "icon": "fail",
        "description": "Raise an error: caught by an enclosing Try, otherwise the run fails.",
        "fields": [F("message", "Error message", "text", required=True)],
    },
    # --- flow control -------------------------------------------------------------------
    "if": {
        "category": "control",
        "label": "If / Else if / Otherwise",
        "icon": "if",
        "description": "Run the first branch whose condition matches, else the Otherwise steps.",
        "fields": [],
        "branches": True,
        "containers": ["else"],
    },
    "for_each": {
        "category": "control",
        "label": "Repeat with each",
        "icon": "loop",
        "description": "Run the steps once per item of a list (vars.<as> is the current item).",
        "fields": [
            F(
                "items",
                "List",
                "json",
                required=True,
                help="e.g. {{ trigger.ticket.fields.devices }}",
            ),
            F("as", "Item variable", default="item"),
            F("max_iterations", "Maximum iterations", "number", default=100),
            F(
                "concurrency",
                "Items at a time",
                "number",
                default=1,
                help="Like NSP/Mistral with-items concurrency: 1 = one after another.",
            ),
        ],
        "containers": ["steps"],
    },
    "repeat": {
        "category": "control",
        "label": "Repeat N times",
        "icon": "loop",
        "description": "Run the steps a fixed number of times (vars.<as> is 1, 2, …).",
        "fields": [
            F("times", "Times", "number", required=True, default=3),
            F("as", "Counter variable", default="attempt"),
        ],
        "containers": ["steps"],
    },
    "while": {
        "category": "control",
        "label": "Repeat while",
        "icon": "loop",
        "description": "Run the steps while the condition is true, up to the maximum.",
        "fields": [
            F("condition", "While", "condition", required=True),
            F("max_iterations", "Maximum iterations", "number", default=20, required=True),
            F("delay_seconds", "Wait between iterations (s)", "number", default=0),
        ],
        "containers": ["steps"],
    },
    "switch": {
        "category": "control",
        "label": "Switch",
        "icon": "switch",
        "description": "Compare one value with several cases; run the matching case, else Default.",
        "fields": [F("value", "On value", required=True, default="{{ vars.platform }}")],
        "cases": True,
        "containers": ["default"],
    },
    "parallel": {
        "category": "control",
        "label": "Run in parallel",
        "icon": "parallel",
        "description": "Run several branches at the same time and wait for all of them (join).",
        "fields": [F("fail_fast", "Stop all branches when one fails", "bool", default=True)],
        "parallel_branches": True,
    },
    "try": {
        "category": "control",
        "label": "Try / On error / Finally",
        "icon": "try",
        "description": "Run steps; on any error run the On error steps (vars.error holds it); always run Finally.",
        "fields": [],
        "containers": ["steps", "catch", "finally"],
    },
    "break": {
        "category": "control",
        "label": "Exit loop",
        "icon": "stop",
        "description": "Leave the innermost loop.",
        "fields": [],
    },
    "continue": {
        "category": "control",
        "label": "Next iteration",
        "icon": "loop",
        "description": "Skip to the next item of the innermost loop.",
        "fields": [],
    },
    "run_flow": {
        "category": "control",
        "label": "Run another flow",
        "icon": "flow",
        "description": "Run a saved flow as a sub-flow and wait for it; its final vars are the result.",
        "fields": [
            F("flow", "Flow name", required=True),
            F("inputs", "Inputs", "kv"),
            SAVE_AS,
        ],
    },
    # --- network --------------------------------------------------------------------------
    "run_commands": {
        "category": "network",
        "label": "Run show commands",
        "icon": "terminal",
        "description": "Run read-only commands on devices (through the execution engine); optionally parse with TextFSM.",
        "fields": [
            F("devices", "Devices", "list", required=True),
            F("commands", "Commands", "list", required=True),
            F("parse", "Parse with TextFSM", "bool", default=False),
            SAVE_AS,
        ],
        "output": "{device: {command: text or parsed rows}}",
    },
    "parse_textfsm": {
        "category": "network",
        "label": "Parse text (TextFSM)",
        "icon": "textfsm",
        "description": "Parse command output with an ntc-template, your own template, or inline template text.",
        "fields": [
            F("platform", "Platform", required=True, default="ios"),
            F("command", "Command", required=True),
            F("text", "Output text", "textarea", required=True),
            F("template", "Inline template (optional)", "code"),
            SAVE_AS,
        ],
    },
    "run_checks": {
        "category": "network",
        "label": "Run pre/post checks",
        "icon": "checks",
        "description": "Run the check templates of a change type (policies/checks.yaml) against devices.",
        "fields": [
            F("devices", "Devices", "list", required=True),
            F("change_type", "Change type (check template)", required=True, default="vlan_add"),
            F("phase", "Phase", "select", options=["pre", "post"], default="post"),
            F("intent", "Extra intent fields", "kv", help="e.g. vlan_id: 300"),
            F("fail_on_error", "Fail the flow when a check fails", "bool", default=False),
            SAVE_AS,
        ],
        "output": "{ok, results: [{name, ok, message}]}",
    },
    "config_push": {
        "category": "network",
        "label": "Change: push config",
        "icon": "push",
        "description": "Create a VireoNet change (full pipeline: policy, locks, backup, dry-run, waves, checks, rollback) and wait for it.",
        "fields": [
            F(
                "intent",
                "Change intent",
                "json",
                required=True,
                help='{"change_type": "config_push", "devices": [...], "configs": {...}, ...}',
            ),
            F("ticket_number", "Ticket number", required=True, default="{{ trigger.ticket.key }}"),
            F(
                "automation",
                "Automation",
                "select",
                options=["full", "manual"],
                default="full",
                help="full: pre-approved, no CI gate (needs a planned window); manual: waits for CI and approval.",
            ),
            F("window_minutes", "Planned window from now (minutes)", "number", default=120),
            F("wait", "Wait for the change to finish", "bool", default=True),
            SAVE_AS,
        ],
        "output": "{change_id, ticket_number, result}",
    },
    "rollback_change": {
        "category": "network",
        "label": "Change: roll back",
        "icon": "rollback",
        "description": "Ask a running or soaking change to roll back.",
        "fields": [F("ticket_number", "Change ticket", required=True), F("reason", "Reason")],
    },
    "get_device": {
        "category": "network",
        "label": "Get device (NetBox)",
        "icon": "device",
        "description": "Look up a device: platform, site, status, primary IP.",
        "fields": [F("device", "Device name", required=True), SAVE_AS],
    },
    # --- ITSM -----------------------------------------------------------------------------
    "itsm_get_ticket": {
        "category": "itsm",
        "label": "Get ticket",
        "icon": "ticket",
        "description": "Read a ticket and its attachments from the ITSM tool.",
        "fields": [
            F("ticket", "Ticket", required=True, default="{{ trigger.ticket.key }}"),
            SAVE_AS,
        ],
    },
    "itsm_comment": {
        "category": "itsm",
        "label": "Add work note",
        "icon": "comment",
        "description": "Add a work note / comment to a ticket.",
        "fields": [
            F("ticket", "Ticket", required=True, default="{{ trigger.ticket.key }}"),
            F("text", "Note", "textarea", required=True),
        ],
    },
    "itsm_close": {
        "category": "itsm",
        "label": "Close ticket",
        "icon": "close",
        "description": "Close / resolve a ticket with a closing note.",
        "fields": [
            F("ticket", "Ticket", required=True, default="{{ trigger.ticket.key }}"),
            F("note", "Close note", "textarea", required=True),
        ],
    },
    # --- API, scripts, people ---------------------------------------------------------------
    "http_request": {
        "category": "integrations",
        "label": "Call API (HTTP)",
        "icon": "api",
        "description": "Call any REST API. Use {{ secrets.NAME }} for tokens (VIREONET_FLOW_SECRET_NAME).",
        "fields": [
            F(
                "method",
                "Method",
                "select",
                options=["GET", "POST", "PUT", "PATCH", "DELETE"],
                default="GET",
            ),
            F("url", "URL", required=True),
            F("headers", "Headers", "kv"),
            F("query", "Query parameters", "kv"),
            F("body", "JSON body", "json"),
            F("timeout_seconds", "Timeout (s)", "number", default=30),
            F("expect_status", "Expected status codes", "list", default=["200-299"]),
            SAVE_AS,
        ],
        "output": "{status, ok, headers, json, text}",
    },
    "script": {
        "category": "integrations",
        "label": "Run custom script (Python)",
        "icon": "script",
        "description": "Run Python on the worker in an isolated process. Read `inputs`, set `result`. Admins only.",
        "admin_only": True,
        "fields": [
            F(
                "code",
                "Python code",
                "code",
                required=True,
                default="# inputs: dict from below; vars: flow variables\nresult = {'hello': inputs.get('name', 'world')}\n",
            ),
            F("inputs", "Inputs", "kv"),
            F("timeout_seconds", "Timeout (s)", "number", default=30),
            SAVE_AS,
        ],
        "output": "the value of `result` (JSON)",
    },
    "notify": {
        "category": "integrations",
        "label": "Send notification",
        "icon": "notify",
        "description": "Post a message to the configured notification channel (Slack/Teams/webhook).",
        "fields": [
            F("message", "Message", "textarea", required=True),
            F(
                "channel",
                "Channel",
                "select",
                options=["", "slack", "teams", "email"],
                default="",
                help="Empty = VIREONET_NOTIFICATION_DEFAULT_CHANNEL.",
            ),
        ],
    },
    "approval": {
        "category": "integrations",
        "label": "Ask for approval",
        "icon": "approval",
        "description": "Pause until a person approves or rejects in VireoNet (or the timeout passes).",
        "fields": [
            F("message", "What to approve", "textarea", required=True),
            F(
                "role",
                "Who can approve",
                "select",
                options=["operator", "approver", "admin"],
                default="approver",
            ),
            F("timeout_minutes", "Timeout (minutes)", "number", default=60),
            F(
                "on_timeout",
                "On timeout",
                "select",
                options=["reject", "approve"],
                default="reject",
            ),
            SAVE_AS,
        ],
        "output": "{approved, by, comment}",
    },
}

CONTROLLER = F(
    "controller",
    "Controller",
    "controller",
    default="auto",
    required=True,
    help="A configured controller (VIREONET_CONTROLLERS), or auto: the one that manages the device's platform.",
)
WAIT = F("wait", "Wait until the job finishes", "bool", default=True)
TIMEOUT = F("timeout_minutes", "Give up after (minutes)", "number", default=90)

STEPS.update(
    {
        "controller_get_device": {
            "category": "controllers",
            "label": "Controller: get device",
            "icon": "device",
            "description": "Read a device from its controller (Catalyst Center, CloudVision, Mist, Apstra, FortiManager, Panorama, NSP, ExtremeCloud IQ, Meraki, Aruba Central): version, model, serial, reachability.",
            "fields": [CONTROLLER, F("device", "Device", required=True), SAVE_AS],
            "output": "{id, name, platform, version, serial, model, reachable}",
        },
        "controller_list_images": {
            "category": "controllers",
            "label": "Controller: list software images",
            "icon": "list",
            "description": "Software versions the controller can install.",
            "fields": [CONTROLLER, F("device", "Device (for auto and platform)"), SAVE_AS],
            "output": "[{id, name, version, platform}]",
        },
        "controller_distribute": {
            "category": "controllers",
            "label": "Controller: distribute image",
            "icon": "push",
            "description": "Copy (stage) a software version to the device through its controller. Nothing reloads.",
            "fields": [
                CONTROLLER,
                F("device", "Device", required=True),
                F("version", "Version", required=True),
                WAIT,
                TIMEOUT,
                SAVE_AS,
            ],
            "output": "{controller, device, version, job: {id, status, progress, message}}",
        },
        "controller_activate": {
            "category": "controllers",
            "label": "Controller: activate image",
            "icon": "rollback",
            "description": "Install / boot the staged version (the device reloads). Activating the previous version is the OS rollback.",
            "fields": [
                CONTROLLER,
                F("device", "Device", required=True),
                F("version", "Version", required=True),
                WAIT,
                TIMEOUT,
                SAVE_AS,
            ],
        },
        "controller_upgrade": {
            "category": "controllers",
            "label": "Controller: upgrade (distribute + activate)",
            "icon": "push",
            "description": "One step: the controller's own upgrade where it has one, otherwise distribute then activate.",
            "fields": [
                CONTROLLER,
                F("device", "Device", required=True),
                F("version", "Version", required=True),
                WAIT,
                TIMEOUT,
                SAVE_AS,
            ],
        },
        "controller_job": {
            "category": "controllers",
            "label": "Controller: job status",
            "icon": "wait",
            "description": "Read a controller job (task) once.",
            "fields": [CONTROLLER, F("job", "Job id", required=True), SAVE_AS],
        },
        "controller_api": {
            "category": "controllers",
            "label": "Controller: API call",
            "icon": "api",
            "description": "Any other controller API, with the controller's own login (e.g. Catalyst Center templates, CloudVision tags, Mist WLANs).",
            "fields": [
                CONTROLLER,
                F(
                    "method",
                    "Method",
                    "select",
                    options=["GET", "POST", "PUT", "PATCH", "DELETE"],
                    default="GET",
                ),
                F("path", "Path", required=True, help="e.g. /dna/intent/api/v1/site"),
                F("query", "Query parameters", "kv"),
                F("body", "JSON body", "json"),
                SAVE_AS,
            ],
        },
    }
)
STEPS.update(
    {
        "log_search": {
            "category": "integrations",
            "label": "Search logs",
            "icon": "log",
            "description": "Search a log platform (Elasticsearch, OpenSearch, Splunk, Grafana Loki, Graylog) and get the matching lines.",
            "fields": [
                F(
                    "backend",
                    "Log platform",
                    required=True,
                    default="fake",
                    help="A name from VIREONET_LOG_BACKENDS (fake mode: fake).",
                ),
                F(
                    "query",
                    "Query",
                    required=True,
                    help="Elasticsearch/OpenSearch: query_string; Splunk: SPL; Loki: LogQL or text; Graylog: search query.",
                ),
                F("minutes", "Look back (minutes)", "number", default=60),
                F("limit", "Maximum lines", "number", default=100),
                SAVE_AS,
            ],
            "output": "{count, hits: [{timestamp, host, message, fields}]}",
        },
        "log_send": {
            "category": "integrations",
            "label": "Send event to log platform",
            "icon": "notify",
            "description": "Write an event (with any fields) to a log platform, e.g. the flow's result for your SIEM.",
            "fields": [
                F("backend", "Log platform", required=True, default="fake"),
                F("message", "Message", "textarea", required=True),
                F("fields", "Fields", "kv"),
            ],
        },
    }
)
STEPS.update(
    {
        "predict_change_risk": {
            "category": "intelligence",
            "label": "Predict change risk",
            "icon": "checks",
            "description": "Neural-network risk score (0–1) for a change: blast radius, vendor mix, hour, emergency, past failures. Branch on it, e.g. ask for approval when risk ≥ 0.5.",
            "fields": [
                F("change_type", "Change type", default="vlan_add"),
                F("devices", "Devices (count)", "number", default=1),
                F("platforms", "Platforms (count)", "number", default=1),
                F("hour", "Hour (UTC)", "number", default="{{ run.started_at[11:13] | int }}"),
                F("emergency", "Emergency", "bool", default=False),
                F("prior_failure_rate", "Past failure rate (0–1)", "number", default=0.05),
                SAVE_AS,
            ],
            "output": "{risk, level: low|medium|high, top_factors, auc}",
        },
        "classify_ticket": {
            "category": "intelligence",
            "label": "Classify ticket text",
            "icon": "ticket",
            "description": "Read a free-text request and suggest the change type, the devices and fields (VLAN id, version).",
            "fields": [
                F(
                    "text",
                    "Text",
                    "textarea",
                    required=True,
                    default="{{ trigger.ticket.summary }}",
                ),
                SAVE_AS,
            ],
            "output": "{change_type, confidence, devices, fields}",
        },
    }
)
STEPS.update(
    {
        "run_check_profile": {
            "category": "network",
            "label": "Run Pre/Post check profile",
            "icon": "checks",
            "description": "Take a snapshot with a saved Pre/Post profile (commands parsed with TextFSM), run its tests and, given a pre snapshot, diff pre against post (parsed and raw, with ignored fields and tolerances).",
            "fields": [
                F("profile", "Profile", required=True, default="VLAN baseline"),
                F("devices", "Devices", "list", required=True),
                F("label", "Snapshot", "select", options=["pre", "post", "adhoc"], default="pre"),
                F(
                    "compare_with",
                    "Compare with snapshot id",
                    help="e.g. {{ steps.pre_snap.snapshot_id }}",
                ),
                F("fail_on_test_failure", "Fail the step when a test fails", "bool", default=True),
                SAVE_AS,
            ],
            "output": "{snapshot_id, ok, failed, tests, diff: {changed, details}}",
        },
    }
)
CONTROLLER_WAIT_STEPS = {"controller_distribute", "controller_activate", "controller_upgrade"}

TRIGGERS: dict[str, dict[str, Any]] = {
    "manual": {
        "label": "Manually (Run button or API)",
        "description": "Start from the Run button or POST /flows/<id>/run with inputs.",
        "fields": [],
        "event": {"inputs": {}, "user": "alice"},
    },
    "itsm": {
        "label": "ITSM ticket event",
        "description": "A ticket is created or updated in ServiceNow, Jira, Freshservice, BMC Helix… (sent to VireoNet's ITSM webhook).",
        "fields": [
            F("events", "Events", "list", default=["created", "updated", "state_changed"]),
            F("states", "Only in states (optional)", "list"),
        ],
        "event": {
            "source": "itsm",
            "event": "state_changed",
            "provider": "servicenow",
            "ticket": {
                "key": "CHG0030005",
                "summary": "OS UPGRADE leaf-01 to 17.9.4",
                "status": "Implement",
                "requester": "carol",
                "fields": {"category": "network", "priority": "3"},
            },
            "previous": {"ticket": {"status": "Scheduled"}},
        },
    },
    "webhook": {
        "label": "Webhook (any JSON)",
        "description": "Any system POSTs JSON to /webhooks/flows/<flow id> with the flow's token.",
        "fields": [],
        "event": {
            "source": "webhook",
            "body": {"alert": "InterfaceDown", "device": "leaf-02"},
            "headers": {},
        },
    },
    "schedule": {
        "label": "Schedule",
        "description": "Run on a cron schedule (UTC), e.g. 0 2 * * 1-5, or every N minutes.",
        "fields": [
            F("cron", "Cron (UTC)", help="minute hour day month weekday"),
            F("every_minutes", "Or every N minutes", "number"),
        ],
        "event": {"source": "schedule", "scheduled_at": "2026-11-24T02:00:00Z"},
    },
    "kafka": {
        "label": "Kafka message (optional)",
        "description": "A message on a Kafka topic (needs VIREONET_KAFKA_BOOTSTRAP). JSON values are parsed.",
        "fields": [F("topic", "Topic", required=True)],
        "event": {
            "source": "kafka",
            "topic": "network-events",
            "key": "leaf-02",
            "value": {"type": "link_down"},
        },
    },
    "vireonet": {
        "label": "VireoNet event",
        "description": "Something happened in VireoNet: a change finished, drift was found, log anomalies were detected.",
        "fields": [
            F(
                "events",
                "Events",
                "list",
                default=[
                    "change.closed",
                    "change.rolled_back",
                    "change.failed",
                    "drift.detected",
                    "logs.anomaly",
                ],
            ),
        ],
        "event": {
            "source": "vireonet",
            "event": "change.rolled_back",
            "change": {"ticket_number": "CHG0030001", "state": "rolled_back"},
        },
    },
}


def _src(label: str, desc: str, events: list[str], sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": label,
        "description": desc,
        "fields": [F("events", "Events", "list", default=events)],
        "event": sample,
        "adapter": True,
    }


TRIGGERS.update(
    {
        "netbox": _src(
            "NetBox change",
            "A device, interface, IP, VLAN… is created, updated or deleted in NetBox "
            "(NetBox webhook → /webhooks/flows/source/netbox).",
            ["created", "updated", "deleted"],
            {
                "source": "netbox",
                "event": "updated",
                "model": "device",
                "name": "leaf-04",
                "object": {
                    "name": "leaf-04",
                    "status": {"value": "active"},
                    "site": {"slug": "dc1"},
                    "platform": {"slug": "cisco-ios-xe"},
                    "primary_ip4": {"address": "10.0.0.14/24"},
                },
                "changed_fields": ["status"],
                "user": "netops",
                "previous": {"object": {"status": {"value": "planned"}}},
            },
        ),
        "zabbix": _src(
            "Zabbix problem",
            "A Zabbix trigger fires, recovers or is updated (webhook media type → /webhooks/flows/source/zabbix).",
            ["problem", "recovery", "update"],
            {
                "source": "zabbix",
                "event": "problem",
                "event_id": "8812",
                "host": "leaf-02",
                "host_ip": "10.0.0.12",
                "trigger": "Interface Ethernet1/49: Link down",
                "severity": "high",
                "severity_level": 4,
                "tags": {"interface": "Ethernet1/49", "scope": "availability"},
            },
        ),
        "prometheus": _src(
            "Prometheus alert",
            "Alertmanager sends a firing or resolved alert (webhook receiver → /webhooks/flows/source/prometheus).",
            ["firing", "resolved"],
            {
                "source": "prometheus",
                "event": "firing",
                "alertname": "InterfaceErrorsHigh",
                "severity": "warning",
                "device": "spine-01",
                "interface": "Ethernet1/1",
                "labels": {
                    "alertname": "InterfaceErrorsHigh",
                    "instance": "spine-01:9116",
                    "ifName": "Ethernet1/1",
                },
                "annotations": {"summary": "CRC errors > 100/min"},
            },
        ),
        "librenms": _src(
            "LibreNMS alert",
            "A LibreNMS alert rule fires or recovers (API transport → /webhooks/flows/source/librenms).",
            ["alert", "recovery", "acknowledged", "worse", "better"],
            {
                "source": "librenms",
                "event": "alert",
                "host": "border-01",
                "title": "BGP session down",
                "severity": "critical",
                "rule": "BGP Session down",
            },
        ),
        "prtg": _src(
            "PRTG sensor",
            "A PRTG sensor goes down or up (HTTP action → /webhooks/flows/source/prtg).",
            ["down", "up", "warning"],
            {
                "source": "prtg",
                "event": "down",
                "host": "fw-01",
                "sensor": "Ping",
                "status": "Down",
                "message": "timeout",
            },
        ),
        "solarwinds": _src(
            "SolarWinds alert",
            "A SolarWinds Orion alert triggers or resets (web request action → /webhooks/flows/source/solarwinds).",
            ["triggered", "reset"],
            {
                "source": "solarwinds",
                "event": "triggered",
                "host": "leaf-03",
                "alert": "Node down",
                "severity": "critical",
            },
        ),
        "git": _src(
            "Git push / merge",
            "A push or merge in GitHub, GitLab or Gitea (repo webhook → /webhooks/flows/source/git).",
            ["push", "merged"],
            {
                "source": "git",
                "event": "push",
                "provider": "gitea",
                "repo": "netops/device-configs",
                "branch": "main",
                "files": ["leaf-01/config"],
                "devices": ["leaf-01"],
                "author": "alice",
            },
        ),
        "awx": _src(
            "AWX job finished",
            "An AWX / Ansible Automation Platform job finishes (notification webhook → /webhooks/flows/source/awx).",
            ["succeeded", "failed", "error", "canceled"],
            {
                "source": "awx",
                "event": "failed",
                "job_id": 812,
                "template": "vireonet-backup",
                "status": "failed",
            },
        ),
    }
)

VIREONET_EVENTS = [
    "change.closed",
    "change.rolled_back",
    "change.rollback_failed",
    "change.failed",
    "change.rejected",
    "change.pre_check_failed",
    "change.ci_failed",
    "drift.detected",
    "logs.anomaly",
]


# Settings every action has (Power Automate "settings / run after",
# NSP/Mistral task policies).
STEP_SETTINGS = [
    F("run_if", "Run only if", "condition", help="Skip this step unless the condition matches."),
    F(
        "run_after",
        "Run after previous step",
        "select",
        options=["success", "failure", "always"],
        default="success",
        help="failure/always: run even when the previous step failed (its error is in vars.error).",
    ),
    F("retry_count", "Retries", "number", default=0),
    F("retry_delay_seconds", "Delay between retries (s)", "number", default=10),
    F("retry_backoff", "Backoff multiplier", "number", default=2),
    F("timeout_seconds", "Step timeout (s)", "number", default=0, help="0 = the action's default."),
    F(
        "continue_on_error",
        "Continue on error",
        "bool",
        default=False,
        help="Record the error and carry on instead of failing.",
    ),
    F("disabled", "Disabled", "bool", default=False),
    F("note", "Note", "textarea"),
]

# Typed run-form inputs (NSP workflow input form / Power Automate manual trigger inputs).
INPUT_TYPES = ["text", "number", "bool", "select", "list", "device", "json"]


def container_keys(step_type: str) -> list[str]:
    return list(STEPS.get(step_type, {}).get("containers", []))


def catalog() -> dict[str, Any]:
    return {
        "categories": CATEGORIES,
        "steps": [{"type": k, **v} for k, v in STEPS.items()],
        "triggers": [{"type": k, **v} for k, v in TRIGGERS.items()],
        "operators": [{"op": k, "label": v} for k, v in OPERATORS.items()],
        "step_settings": STEP_SETTINGS,
        "input_types": INPUT_TYPES,
        "vireonet_events": VIREONET_EVENTS,
    }
