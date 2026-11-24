"""Ready-made flows for the templates gallery (and the user manual).

Each one runs as-is in fake mode against the demo network."""

from __future__ import annotations

import copy
from typing import Any

_DEV = "{{ trigger.match.device }}"

OS_UPGRADE: dict[str, Any] = {
    "name": "OS upgrade from an ITSM ticket (boot image via change pipeline)",
    "description": (
        "A ticket whose summary is 'OS UPGRADE <device> to <version>' reaches Implement: "
        "check the current version, ask for approval on P1/P2, set the boot image through "
        "the full change pipeline (locks, backup, dry-run, checks, Git rollback), verify, "
        "and close the ticket — or report the failure on the ticket."
    ),
    "trigger": {
        "type": "itsm",
        "events": ["created", "updated", "state_changed"],
        "states": ["Implement"],
        "conditions": {
            "all": [
                {
                    "field": "ticket.summary",
                    "op": "matches",
                    "value": r"OS UPGRADE (?P<device>[A-Za-z0-9._-]+) to (?P<version>\d+\.\d+\.\d+)",
                },
                {"field": "ticket.fields.category", "op": "in", "value": ["network", ""]},
            ]
        },
    },
    "definition": {
        "variables": {},
        "steps": [
            {
                "id": "start_note",
                "type": "itsm_comment",
                "text": f"VireoNet flow: OS upgrade of {_DEV} to {{{{ trigger.match.version }}}} started.",
            },
            {
                "id": "set_target",
                "type": "set_variable",
                "name": "target",
                "value": "{{ trigger.match.version }}",
            },
            {
                "id": "set_parts",
                "type": "set_variable",
                "name": "parts",
                "value": "{{ vars.target.split('.') | map('int') | list }}",
            },
            {
                "id": "set_image",
                "type": "set_variable",
                "name": "image",
                "value": "flash:cat9k_iosxe.{{ '%02d' % vars.parts[0] }}.{{ '%02d' % vars.parts[1] }}.{{ '%02d' % vars.parts[2] }}.SPA.bin",
            },
            {"id": "device", "type": "get_device", "device": _DEV, "save_as": "device"},
            {
                "id": "pre",
                "type": "run_commands",
                "devices": [_DEV],
                "commands": ["show version", "show boot"],
                "save_as": "pre",
            },
            {
                "id": "set_current",
                "type": "set_variable",
                "name": "current",
                "value": "{{ (vars.pre[trigger.match.device]['show version'] | regex_findall('Version ([0-9.]+), RELEASE'))[0] }}",
            },
            {
                "id": "already",
                "type": "if",
                "branches": [
                    {
                        "condition": {
                            "field": "vars.current",
                            "op": "equals",
                            "value": "{{ vars.target }}",
                        },
                        "steps": [
                            {
                                "id": "close_noop",
                                "type": "itsm_close",
                                "note": f"{_DEV} already runs {{{{ vars.target }}}}: nothing to do.",
                            },
                            {
                                "id": "stop_noop",
                                "type": "stop",
                                "message": "already on the target version",
                            },
                        ],
                    }
                ],
            },
            {
                "id": "needs_approval",
                "type": "if",
                "branches": [
                    {
                        "condition": {
                            "field": "trigger.ticket.fields.priority",
                            "op": "in",
                            "value": ["1", "2"],
                        },
                        "steps": [
                            {
                                "id": "approve",
                                "type": "approval",
                                "message": f"Upgrade {_DEV} from {{{{ vars.current }}}} to {{{{ vars.target }}}} (P{{{{ trigger.ticket.fields.priority }}}})?",
                                "role": "approver",
                                "timeout_minutes": 60,
                            },
                        ],
                    }
                ],
            },
            {
                "id": "upgrade",
                "type": "try",
                "steps": [
                    {
                        "id": "push_boot",
                        "type": "config_push",
                        "name": "Set boot image (full change pipeline)",
                        "ticket_number": "{{ trigger.ticket.key }}",
                        "automation": "full",
                        "window_minutes": 120,
                        "intent": {
                            "change_type": "config_push",
                            "devices": [_DEV],
                            "site": "{{ vars.device.site }}",
                            "configs": {_DEV: "boot system {{ vars.image }}"},
                            "rollback_configs": {
                                _DEV: "{{ vars.pre[trigger.match.device]['show boot'] | regex_findall('BOOT variable = ([^,]+)') | map('regex_replace', '^', 'boot system ') | first }}"
                            },
                        },
                        "save_as": "change",
                    },
                    {
                        "id": "post",
                        "type": "run_commands",
                        "devices": [_DEV],
                        "commands": ["show boot"],
                        "save_as": "post",
                        "retry_count": 2,
                        "retry_delay_seconds": 10,
                    },
                    {
                        "id": "verify",
                        "type": "if",
                        "branches": [
                            {
                                "condition": {
                                    "field": "{{ vars.post[trigger.match.device]['show boot'] }}",
                                    "op": "not_contains",
                                    "value": "{{ vars.image }}",
                                },
                                "steps": [
                                    {
                                        "id": "boot_wrong",
                                        "type": "fail",
                                        "message": "boot variable is not {{ vars.image }} after the change",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "id": "close_ok",
                        "type": "itsm_close",
                        "note": f"VireoNet: {_DEV} boot image set to {{{{ vars.image }}}} (change {{{{ vars.change.ticket_number }}}} closed). Reload in the maintenance window to activate {{{{ vars.target }}}}.",
                    },
                ],
                "catch": [
                    {
                        "id": "fail_note",
                        "type": "itsm_comment",
                        "text": f"VireoNet: OS upgrade of {_DEV} failed: {{{{ vars.error.message }}}}. The change pipeline rolled back automatically.",
                    },
                    {
                        "id": "fail_notify",
                        "type": "notify",
                        "message": "OS upgrade of {{ trigger.match.device }} failed: {{ vars.error.message }}",
                    },
                    {"id": "fail_run", "type": "fail", "message": "{{ vars.error.message }}"},
                ],
            },
        ],
        "outputs": {
            "device": _DEV,
            "from": "{{ vars.current }}",
            "to": "{{ vars.target }}",
            "image": "{{ vars.image }}",
        },
    },
}

ZABBIX_LINK_DOWN: dict[str, Any] = {
    "name": "Zabbix link down: triage",
    "description": "A high-severity Zabbix 'Link down on <interface>' problem: look at the interface a few times, then notify with the result.",
    "trigger": {
        "type": "zabbix",
        "events": ["problem"],
        "conditions": {
            "all": [
                {"field": "severity_level", "op": "gte", "value": 4},
                {
                    "field": "trigger",
                    "op": "matches",
                    "value": r"(?i)link down on (?P<interface>\S+)",
                },
            ]
        },
    },
    "definition": {
        "steps": [
            {
                "id": "check",
                "type": "repeat",
                "times": 3,
                "as": "attempt",
                "steps": [
                    {
                        "id": "descr",
                        "type": "run_commands",
                        "devices": ["{{ trigger.host }}"],
                        "commands": ["show interfaces {{ trigger.match.interface }} description"],
                        "save_as": "descr",
                    },
                    {"id": "pause", "type": "wait", "seconds": 5},
                ],
            },
            {
                "id": "tell",
                "type": "notify",
                "message": "Zabbix: {{ trigger.trigger }} on {{ trigger.host }}. Checked 3 times. Last output: {{ vars.descr[trigger.host] | tojson }}",
            },
        ],
        "outputs": {"host": "{{ trigger.host }}", "interface": "{{ trigger.match.interface }}"},
    },
}

NETBOX_ONBOARD: dict[str, Any] = {
    "name": "NetBox device activated: onboarding checks",
    "description": "A device's status changes to active in NetBox: read it, run read-only checks, post the result.",
    "trigger": {
        "type": "netbox",
        "events": ["updated"],
        "conditions": {
            "all": [
                {"field": "model", "op": "equals", "value": "device"},
                {"field": "object.status.value", "op": "changed", "from": "", "to": "active"},
            ]
        },
    },
    "definition": {
        "steps": [
            {
                "id": "dev",
                "type": "get_device",
                "device": "{{ trigger.name }}",
                "save_as": "device",
            },
            {
                "id": "facts",
                "type": "run_commands",
                "devices": ["{{ trigger.name }}"],
                "commands": ["show version", "show vlan brief"],
                "parse": True,
                "continue_on_error": True,
            },
            {
                "id": "tell",
                "type": "notify",
                "message": "{{ trigger.name }} is active ({{ vars.device.platform }} at {{ vars.device.site }}); onboarding facts collected.",
            },
        ]
    },
}

NIGHTLY_AUDIT: dict[str, Any] = {
    "name": "Nightly VLAN audit",
    "description": "Every night: check that VLAN 100 exists on every leaf; collect the ones without it and notify.",
    "trigger": {"type": "schedule", "cron": "0 2 * * *"},
    "definition": {
        "inputs": [],
        "variables": {"devices": ["leaf-01", "leaf-02", "leaf-03"], "missing": []},
        "steps": [
            {
                "id": "each",
                "type": "for_each",
                "items": "{{ vars.devices }}",
                "as": "dev",
                "steps": [
                    {
                        "id": "vlan",
                        "type": "run_commands",
                        "devices": ["{{ vars.dev }}"],
                        "commands": ["show vlan brief"],
                        "save_as": "out",
                    },
                    {
                        "id": "has",
                        "type": "if",
                        "branches": [
                            {
                                "condition": {
                                    "field": "{{ vars.out[vars.dev]['show vlan brief'] }}",
                                    "op": "not_matches",
                                    "value": r"(?m)^100\s",
                                },
                                "steps": [
                                    {
                                        "id": "add",
                                        "type": "append_to_list",
                                        "name": "missing",
                                        "value": "{{ vars.dev }}",
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "id": "report",
                "type": "switch",
                "value": "{{ vars.missing | length }}",
                "cases": [
                    {
                        "value": 0,
                        "steps": [
                            {"id": "fine", "type": "log", "message": "VLAN 100 present everywhere"}
                        ],
                    },
                ],
                "default": [
                    {
                        "id": "alert",
                        "type": "notify",
                        "message": "VLAN 100 missing on {{ vars.missing | join(', ') }}",
                    }
                ],
            },
        ],
        "outputs": {"missing": "{{ vars.missing }}"},
    },
}

API_AND_SCRIPT: dict[str, Any] = {
    "name": "API call and custom script (learning example)",
    "description": "Manual run with inputs: call a REST API, transform the answer in Python, branch on the result.",
    "trigger": {"type": "manual"},
    "definition": {
        "inputs": [
            {
                "name": "url",
                "label": "API URL",
                "type": "text",
                "default": "http://api:8000/healthz",
                "required": True,
            },
            {"name": "threshold", "label": "Threshold", "type": "number", "default": 1},
        ],
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "method": "GET",
                "url": "{{ inputs.url }}",
                "timeout_seconds": 10,
                "save_as": "resp",
                "retry_count": 2,
                "retry_delay_seconds": 2,
            },
            {
                "id": "calc",
                "type": "script",
                "code": "status = inputs['body'].get('status', '?')\nresult = {'status': status, 'score': len(status)}\nprint('computed', result)\n",
                "inputs": {"body": "{{ vars.resp.json }}"},
                "save_as": "calc",
            },
            {
                "id": "decide",
                "type": "if",
                "branches": [
                    {
                        "condition": {
                            "field": "vars.calc.result.score",
                            "op": "gte",
                            "value": "{{ inputs.threshold }}",
                        },
                        "steps": [
                            {
                                "id": "good",
                                "type": "log",
                                "message": "API says {{ vars.calc.result.status }}",
                            }
                        ],
                    },
                ],
                "else": [{"id": "bad", "type": "fail", "message": "score below threshold"}],
            },
        ],
        "outputs": {"status": "{{ vars.calc.result.status }}"},
    },
}

_D, _V = "{{ trigger.match.device }}", "{{ trigger.match.version }}"

OS_UPGRADE_CONTROLLER: dict[str, Any] = {
    "name": "OS upgrade via controller (any vendor)",
    "description": (
        "A ticket 'OS UPGRADE <device> to <version>' in Implement: the device's controller "
        "(Catalyst Center, CloudVision, Mist, Apstra, FortiManager, Panorama, NSP, XIQ, Meraki, Aruba "
        "Central — picked automatically from the NetBox platform) upgrades it; verify; close the "
        "ticket. On failure, activate the previous version and report on the ticket."
    ),
    "trigger": {
        "type": "itsm",
        "events": ["created", "updated", "state_changed"],
        "states": ["Implement"],
        "conditions": {
            "all": [
                {
                    "field": "ticket.summary",
                    "op": "matches",
                    "value": r"OS UPGRADE (?P<device>[A-Za-z0-9._-]+) to (?P<version>[0-9][0-9A-Za-z.]*)",
                }
            ]
        },
    },
    "definition": {
        "variables": {},
        "steps": [
            {
                "id": "start_note",
                "type": "itsm_comment",
                "text": f"VireoNet flow: upgrading {_D} to {_V} through its controller.",
            },
            {
                "id": "before",
                "type": "controller_get_device",
                "controller": "auto",
                "device": _D,
                "save_as": "before",
            },
            {
                "id": "already",
                "type": "if",
                "branches": [
                    {
                        "condition": {"field": "vars.before.version", "op": "equals", "value": _V},
                        "steps": [
                            {
                                "id": "close_noop",
                                "type": "itsm_close",
                                "note": f"{_D} already runs {_V}: nothing to do.",
                            },
                            {
                                "id": "stop_noop",
                                "type": "stop",
                                "message": "already on the target version",
                            },
                        ],
                    }
                ],
            },
            {
                "id": "pre",
                "type": "run_commands",
                "devices": [_D],
                "commands": ["show version"],
                "save_as": "pre",
                "continue_on_error": True,
            },
            {
                "id": "needs_approval",
                "type": "if",
                "branches": [
                    {
                        "condition": {
                            "field": "trigger.ticket.fields.priority",
                            "op": "in",
                            "value": ["1", "2"],
                        },
                        "steps": [
                            {
                                "id": "approve",
                                "type": "approval",
                                "message": f"Upgrade {_D} from {{{{ vars.before.version }}}} to {_V} via {{{{ vars.before.controller }}}}?",
                                "role": "approver",
                                "timeout_minutes": 60,
                            }
                        ],
                    }
                ],
            },
            {
                "id": "images",
                "type": "controller_list_images",
                "controller": "auto",
                "device": _D,
                "save_as": "images",
            },
            {
                "id": "offered",
                "type": "if",
                "branches": [
                    {
                        "condition": {
                            "field": "{{ vars.images | map(attribute='version') | list }}",
                            "op": "not_contains",
                            "value": _V,
                        },
                        "steps": [
                            {
                                "id": "not_offered",
                                "type": "fail",
                                "message": f"the controller does not offer {_V} for {_D}",
                            }
                        ],
                    }
                ],
            },
            {
                "id": "upgrade",
                "type": "try",
                "steps": [
                    {
                        "id": "run_upgrade",
                        "type": "controller_upgrade",
                        "name": "Upgrade through the controller",
                        "controller": "auto",
                        "device": _D,
                        "version": _V,
                        "wait": True,
                        "timeout_minutes": 90,
                        "save_as": "job",
                    },
                    {
                        "id": "after",
                        "type": "controller_get_device",
                        "controller": "auto",
                        "device": _D,
                        "save_as": "after",
                        "retry_count": 3,
                        "retry_delay_seconds": 30,
                    },
                    {
                        "id": "verify",
                        "type": "if",
                        "branches": [
                            {
                                "condition": {
                                    "field": "vars.after.version",
                                    "op": "not_equals",
                                    "value": _V,
                                },
                                "steps": [
                                    {
                                        "id": "wrong_version",
                                        "type": "fail",
                                        "message": f"{_D} reports {{{{ vars.after.version }}}} after the upgrade, not {_V}",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "id": "post",
                        "type": "run_commands",
                        "devices": [_D],
                        "commands": ["show version"],
                        "save_as": "post",
                        "continue_on_error": True,
                    },
                    {
                        "id": "close_ok",
                        "type": "itsm_close",
                        "note": f"VireoNet: {_D} upgraded from {{{{ vars.before.version }}}} to {{{{ vars.after.version }}}} by {{{{ vars.job.vendor }}}} (job {{{{ vars.job.job.id }}}}).",
                    },
                ],
                "catch": [
                    {
                        "id": "fail_note",
                        "type": "itsm_comment",
                        "text": f"VireoNet: upgrade of {_D} to {_V} failed: {{{{ vars.error.message }}}}. Activating the previous version {{{{ vars.before.version }}}}.",
                    },
                    {
                        "id": "os_rollback",
                        "type": "controller_activate",
                        "name": "Roll back: activate the previous version",
                        "controller": "auto",
                        "device": _D,
                        "version": "{{ vars.before.version }}",
                        "wait": True,
                        "timeout_minutes": 60,
                        "continue_on_error": True,
                    },
                    {
                        "id": "fail_notify",
                        "type": "notify",
                        "message": "OS upgrade of {{ trigger.match.device }} failed: {{ vars.error.message }}",
                    },
                    {"id": "fail_run", "type": "fail", "message": "{{ vars.error.message }}"},
                ],
            },
        ],
        "outputs": {
            "device": _D,
            "from": "{{ vars.before.version }}",
            "to": "{{ vars.after.version }}",
            "controller": "{{ vars.job.controller }}",
            "vendor": "{{ vars.job.vendor }}",
        },
    },
}

EXAMPLES: dict[str, dict[str, Any]] = {
    "os-upgrade-controller": OS_UPGRADE_CONTROLLER,
    "os-upgrade-itsm": OS_UPGRADE,
    "zabbix-link-down": ZABBIX_LINK_DOWN,
    "netbox-onboarding": NETBOX_ONBOARD,
    "nightly-vlan-audit": NIGHTLY_AUDIT,
    "api-and-script": API_AND_SCRIPT,
}


def example(key: str) -> dict[str, Any]:
    return copy.deepcopy(EXAMPLES[key])
