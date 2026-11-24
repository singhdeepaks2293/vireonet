"""Source normalisers against payloads modelled on each vendor's documented examples."""

from __future__ import annotations

import json

from vireonet.flows.sources import normalise


def test_netbox_4x_object_type_and_request_user() -> None:
    body = {
        "event": "updated",
        "timestamp": "2025-08-19T15:45:02.123456+00:00",
        "object_type": "dcim.device",
        "request": {"id": "7f2c1f3e-5b1d-4d3a-9b55-2c8c9a1e0d11", "user": "jstretch"},
        "data": {"id": 42, "name": "leaf-01", "display": "leaf-01", "status": {"value": "active"}},
        "snapshots": {
            "prechange": {"name": "leaf-01", "status": "planned"},
            "postchange": {"name": "leaf-01", "status": "active"},
        },
    }
    (e,) = normalise("netbox", body)
    assert e["event"] == "updated"
    assert e["model"] == "device"
    assert e["object_type"] == "dcim.device"
    assert e["user"] == "jstretch"
    assert e["name"] == "leaf-01"
    assert e["changed_fields"] == ["status"]


def test_netbox_legacy_model_and_username() -> None:
    (e,) = normalise(
        "netbox",
        {"event": "created", "model": "interface", "username": "admin", "data": {"name": "eth0"}},
    )
    assert (e["model"], e["user"], e["object_type"]) == ("interface", "admin", "")


def test_zabbix_tagsjson_string_and_severity_name() -> None:
    body = {
        "event_id": "12345",
        "event_value": "1",
        "severity": "Not classified",
        "host": "leaf-01",
        "trigger_name": "Interface Ethernet1/49: Link down",
        "tags": '[{"tag":"interface","value":"Ethernet1/49"},{"tag":"scope","value":"availability"}]',
    }
    (e,) = normalise("zabbix", body)
    assert e["tags"] == {"interface": "Ethernet1/49", "scope": "availability"}
    assert e["severity"] == "not_classified"
    assert e["severity_level"] is None


def test_zabbix_tags_as_list() -> None:
    (e,) = normalise(
        "zabbix", {"event_value": "0", "severity": "4", "tags": [{"tag": "a", "value": "1"}]}
    )
    assert (e["event"], e["severity"], e["tags"]) == ("recovery", "high", {"a": "1"})


def test_gitea_push_with_github_header_is_gitea() -> None:
    body = {
        "ref": "refs/heads/main",
        "before": "28e1879d029cb852e4844d9c718537df08844e03",
        "after": "bffeb74224043ba2feb48d137756c8a9331c449a",
        "commits": [{"id": "bffeb742", "added": [], "modified": ["leaf-01/config"], "removed": []}],
        "repository": {"name": "configs", "full_name": "netops/configs"},
        "pusher": {"id": 1, "login": "gitea", "full_name": "", "username": "gitea"},
    }
    headers = {"X-Gitea-Event": "push", "X-GitHub-Event": "push", "X-Gogs-Event": "push"}
    (e,) = normalise("gitea", body, headers)
    assert e["provider"] == "gitea"
    assert e["event"] == "push"
    assert e["author"] == "gitea"
    assert e["repo"] == "netops/configs"
    assert e["devices"] == ["leaf-01"]


def _gitlab_mr(action: str, state: str) -> dict[str, object]:
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"id": 1, "name": "Administrator", "username": "root"},
        "project": {"id": 1, "name": "configs", "path_with_namespace": "netops/configs"},
        "repository": {"name": "configs"},
        "object_attributes": {
            "iid": 1,
            "title": "Update leaf-01",
            "target_branch": "main",
            "source_branch": "leaf-01",
            "state": state,
            "action": action,
        },
    }


def test_gitlab_mr_merge_vs_update() -> None:
    headers = {"X-Gitlab-Event": "Merge Request Hook"}
    (merged,) = normalise("gitlab", _gitlab_mr("merge", "merged"), headers)
    assert merged["event"] == "merged"
    assert merged["provider"] == "gitlab"
    assert merged["repo"] == "netops/configs"
    assert merged["branch"] == "main"
    assert merged["author"] == "Administrator"
    (updated,) = normalise("gitlab", _gitlab_mr("update", "merged"), headers)
    assert updated["event"] == "updated"


def _github_pr(action: str, merged: bool) -> dict[str, object]:
    return {
        "action": action,
        "number": 2,
        "pull_request": {
            "number": 2,
            "state": "closed" if action == "closed" else "open",
            "title": "Update leaf-01",
            "merged": merged,
            "base": {"ref": "main"},
        },
        "repository": {"full_name": "netops/configs"},
        "sender": {"login": "octocat"},
    }


def test_github_pr_closed_merged_vs_edited() -> None:
    headers = {"X-GitHub-Event": "pull_request"}
    (m,) = normalise("github", _github_pr("closed", True), headers)
    assert (m["event"], m["provider"], m["branch"]) == ("merged", "github", "main")
    (ed,) = normalise("github", _github_pr("edited", True), headers)
    assert ed["event"] == "edited"
    (closed,) = normalise("github", _github_pr("closed", False), headers)
    assert closed["event"] == "closed"


def test_awx_extra_vars_string() -> None:
    body = {
        "id": 38,
        "name": "vireonet-backup",
        "url": "https://awx.example.com/#/jobs/playbook/38",
        "status": "successful",
        "hosts": {"leaf-01": {"failed": False}},
        "extra_vars": json.dumps({"device": "leaf-01"}),
    }
    (e,) = normalise("awx", body)
    assert e["event"] == "succeeded"
    assert e["extra_vars"] == {"device": "leaf-01"}
    (bad,) = normalise("awx", {"status": "failed", "extra_vars": "{not json"})
    assert bad["extra_vars"] == {}


def test_prtg_current_status() -> None:
    (up,) = normalise("prtg", {"device": "fw-01", "status": "Up (was: Down)"})
    assert up["event"] == "up"
    (down,) = normalise("prtg", {"device": "fw-01", "status": "Down (was: Up)"})
    assert down["event"] == "down"
    assert normalise("prtg", {"status": "Down"})[0]["event"] == "down"
    assert normalise("prtg", {"status": "Up"})[0]["event"] == "up"


def test_librenms_state_changed() -> None:
    (e,) = normalise("librenms", {"state": 5, "hostname": "border-01", "title": "Changed"})
    assert e["event"] == "changed"
