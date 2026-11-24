"""Request shapes per the providers' official API docs (respx, no network).

Gitea: docs.gitea.com/api/1.24 ; GitHub REST ; GitLab commits / merge_requests ;
Bitbucket Cloud source + pullrequests + refs API groups.
"""

from __future__ import annotations

import base64
import json

import pytest
import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterNotFound
from vireonet.adapters.git.bitbucket import BitbucketAdapter
from vireonet.adapters.git.registry import get_git_provider
from vireonet.adapters.gitlab import GitLabAdapter as LegacyGitLab

KW = {"timeout": 5.0}
GITEA = "https://gitea.example.com/api/v1/repos/acme/intent"
GH = "https://api.github.com/repos/acme/intent"
GL = "https://gitlab.com/api/v4/projects/acme%2Fintent"
BB = "https://api.bitbucket.org/2.0/repositories/acme/intent"
SHA = "a" * 40


def _gitea():
    return get_git_provider(
        provider="gitea", base_url="https://gitea.example.com", token="t", repo="acme/intent", **KW
    )


def _gh():
    return get_git_provider(provider="github", base_url="", token="t", repo="acme/intent", **KW)


def _gl():
    return get_git_provider(provider="gitlab", base_url="", token="t", repo="acme/intent", **KW)


def _bb() -> BitbucketAdapter:
    p = get_git_provider(provider="bitbucket", base_url="", token="t", repo="acme/intent", **KW)
    assert isinstance(p, BitbucketAdapter)
    p.merge_poll_interval = 0
    return p


def _bb_pr(state: str = "OPEN") -> dict:
    return {
        "id": 5,
        "title": "t",
        "state": state,
        "source": {"branch": {"name": "change/x"}},
        "destination": {"branch": {"name": "main"}},
        "links": {"html": {"href": "https://bitbucket.org/acme/intent/pull-requests/5"}},
    }


def _gitea_pr(sha: str = SHA) -> dict:
    return {
        "number": 3,
        "title": "t",
        "state": "open",
        "head": {"label": "acme:change/x", "sha": sha},
        "base": {"label": "acme:main"},
        "html_url": "https://gitea.example.com/acme/intent/pulls/3",
    }


# ---------------------------------------------------------------- Gitea


@respx.mock
async def test_gitea_create_branch_uses_branches_endpoint() -> None:
    route = respx.post(f"{GITEA}/branches").mock(return_value=Response(201, json={}))
    assert await _gitea().create_branch("change/x", "main") == "change/x"
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "new_branch_name": "change/x",
        "old_ref_name": "main",
        "old_branch_name": "main",
    }


@pytest.mark.parametrize(
    "run,expected",
    [
        ({"status": "completed", "conclusion": "success"}, "success"),
        ({"status": "completed", "conclusion": "failure"}, "failed"),
        ({"status": "completed", "conclusion": "cancelled"}, "canceled"),
        ({"status": "completed", "conclusion": "skipped"}, "canceled"),
        ({"status": "in_progress"}, "running"),
        ({"status": "queued"}, "pending"),
        ({"status": "waiting"}, "pending"),
    ],
)
@respx.mock
async def test_gitea_pipeline_filters_by_head_sha_and_maps(run: dict, expected: str) -> None:
    respx.get(f"{GITEA}/pulls/3").mock(return_value=Response(200, json=_gitea_pr()))
    route = respx.get(f"{GITEA}/actions/runs", params={"head_sha": SHA}).mock(
        return_value=Response(200, json={"workflow_runs": [{"id": 9, "head_sha": SHA, **run}]})
    )
    pipe = await _gitea().get_pipeline(3)
    assert route.called
    assert route.calls.last.request.url.params["head_sha"] == SHA
    assert pipe.status == expected and pipe.id == "9"


@respx.mock
async def test_gitea_pipeline_404_is_unknown() -> None:
    respx.get(f"{GITEA}/pulls/3").mock(return_value=Response(200, json=_gitea_pr()))
    respx.get(url__startswith=f"{GITEA}/actions/runs").mock(return_value=Response(404))
    pipe = await _gitea().get_pipeline(3)
    assert pipe.status == "unknown"


# ---------------------------------------------------------------- GitHub


def _gh_pr(**extra: object) -> dict:
    return {
        "number": 7,
        "title": "t",
        "state": "open",
        "head": {"ref": "change/x", "sha": "headsha"},
        "base": {"ref": "main"},
        "html_url": "https://github.com/acme/intent/pull/7",
        **extra,
    }


@respx.mock
async def test_github_pipeline_uses_pr_head_sha() -> None:
    respx.get(f"{GH}/pulls/7").mock(return_value=Response(200, json=_gh_pr()))
    commits = respx.get(f"{GH}/pulls/7/commits").mock(return_value=Response(200, json=[]))
    checks = respx.get(f"{GH}/commits/headsha/check-runs").mock(
        return_value=Response(200, json={"check_runs": [{"status": "in_progress"}]})
    )
    pipe = await _gh().get_pipeline(7)
    assert checks.called and not commits.called
    assert pipe.id == "headsha" and pipe.status == "running"


@respx.mock
async def test_github_merged_pr_state() -> None:
    respx.get(f"{GH}/pulls/7").mock(
        return_value=Response(200, json=_gh_pr(state="closed", merged=True))
    )
    assert (await _gh().get_mr(7)).state == "merged"


@respx.mock
async def test_github_closed_unmerged_pr_state() -> None:
    respx.get(f"{GH}/pulls/7").mock(
        return_value=Response(200, json=_gh_pr(state="closed", merged=False))
    )
    assert (await _gh().get_mr(7)).state == "closed"


# ---------------------------------------------------------------- GitLab (git provider)


@pytest.mark.parametrize("exists,action", [(True, "update"), (False, "create")])
@respx.mock
async def test_gitlab_commit_file_picks_action(exists: bool, action: str) -> None:
    content = base64.b64encode(b"old").decode()
    respx.get(url__startswith=f"{GL}/repository/files/").mock(
        return_value=Response(200, json={"content": content, "blob_id": "b"})
        if exists
        else Response(404, json={"message": "404 File Not Found"})
    )
    route = respx.post(f"{GL}/repository/commits").mock(
        return_value=Response(201, json={"id": "c1"})
    )
    res = await _gl().commit_file("change/x", "vlan.yml", "new", "msg")
    body = json.loads(route.calls.last.request.content)
    assert body["actions"][0]["action"] == action
    assert body["branch"] == "change/x" and res.sha == "c1"


@respx.mock
async def test_gitlab_mr_diff_uses_diffs_endpoint() -> None:
    changes = respx.get(f"{GL}/merge_requests/4/changes").mock(return_value=Response(200))
    diffs = respx.get(url__startswith=f"{GL}/merge_requests/4/diffs").mock(
        return_value=Response(
            200,
            json=[
                {"diff": "@@ -1 +1 @@\n-a\n+b\n", "old_path": "a", "new_path": "a"},
                {"diff": "@@ -0,0 +1 @@\n+c\n", "old_path": "c", "new_path": "c"},
            ],
        )
    )
    out = await _gl().get_mr_diff(4)
    assert diffs.called and not changes.called
    assert "+b" in out and "+c" in out


# ---------------------------------------------------------------- GitLab (legacy adapter)


def _legacy() -> LegacyGitLab:
    return LegacyGitLab(
        AdapterConfig(name="gitlab", base_url="https://gitlab.example", max_retries=0),
        token="tok",
        project_id="123",
    )


@respx.mock
async def test_legacy_commit_files_create_or_update_per_file() -> None:
    files_base = "https://gitlab.example/api/v4/projects/123/repository/files"
    exists = respx.get(f"{files_base}/existing.yml", params={"ref": "b"}).mock(
        return_value=Response(200, json={"content": ""})
    )
    missing = respx.get(f"{files_base}/new.yml", params={"ref": "b"}).mock(
        return_value=Response(404, json={})
    )
    route = respx.post(url__regex=r".*/repository/commits$").mock(
        return_value=Response(201, json={"id": "sha1"})
    )
    sha = await _legacy().commit_files("b", {"existing.yml": "x", "new.yml": "y"}, "m")
    assert sha == "sha1" and exists.called and missing.called
    actions = {
        a["file_path"]: a["action"] for a in json.loads(route.calls.last.request.content)["actions"]
    }
    assert actions == {"existing.yml": "update", "new.yml": "create"}


@respx.mock
async def test_legacy_job_logs_non_2xx_raises() -> None:
    respx.get(url__regex=r".*/pipelines/1/jobs.*").mock(
        return_value=Response(200, json=[{"id": 11, "name": "lint"}])
    )
    respx.get(url__regex=r".*/jobs/11/trace$").mock(return_value=Response(404, text="gone"))
    with pytest.raises(AdapterNotFound):
        await _legacy().get_job_logs(1, "lint")


# ---------------------------------------------------------------- Bitbucket


@respx.mock
async def test_bitbucket_commit_file_multipart_field_is_path() -> None:
    route = respx.post(f"{BB}/src").mock(
        return_value=Response(201, headers={"Location": f"{BB}/commit/{SHA}"})
    )
    res = await _bb().commit_file("change/x", "configs/vlan.json", '{"vlan": 100}', "msg")
    req = route.calls.last.request
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content.decode()
    assert 'name="configs/vlan.json"' in body
    assert '{"vlan": 100}' in body  # raw content, not base64
    assert 'name="message"' in body and "msg" in body
    assert 'name="branch"' in body and "change/x" in body
    assert res.sha == SHA


@respx.mock
async def test_bitbucket_get_file_keeps_json_as_text() -> None:
    respx.get(f"{BB}/src/main/vlan.json").mock(
        return_value=Response(200, json={"vlan": 100}, headers={"Content-Type": "application/json"})
    )
    f = await _bb().get_file("vlan.json", "main")
    assert json.loads(f.content) == {"vlan": 100}


@respx.mock
async def test_bitbucket_diff_follows_redirect() -> None:
    respx.get(f"{BB}/pullrequests/5/diff").mock(
        return_value=Response(302, headers={"Location": f"{BB}/diff/acme/intent:abc%0Ddef"})
    )
    respx.get(url__startswith=f"{BB}/diff/").mock(return_value=Response(200, text="diff --git"))
    assert await _bb().get_mr_diff(5) == "diff --git"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESSFUL", "success"),
        ("FAILED", "failed"),
        ("INPROGRESS", "running"),
        ("STOPPED", "canceled"),
    ],
)
@respx.mock
async def test_bitbucket_pipeline_states(state: str, expected: str) -> None:
    respx.get(f"{BB}/pullrequests/5").mock(return_value=Response(200, json=_bb_pr()))
    respx.get(f"{BB}/pullrequests/5/statuses").mock(
        return_value=Response(200, json={"values": [{"key": "ci", "state": state}]})
    )
    assert (await _bb().get_pipeline(5)).status == expected


@respx.mock
async def test_bitbucket_create_branch_resolves_hash() -> None:
    respx.get(f"{BB}/refs/branches/main").mock(
        return_value=Response(200, json={"name": "main", "target": {"hash": SHA}})
    )
    route = respx.post(f"{BB}/refs/branches").mock(return_value=Response(201, json={}))
    await _bb().create_branch("change/x", "main")
    assert json.loads(route.calls.last.request.content) == {
        "name": "change/x",
        "target": {"hash": SHA},
    }


@respx.mock
async def test_bitbucket_create_branch_from_hash_skips_lookup() -> None:
    lookup = respx.get(url__startswith=f"{BB}/refs/branches/").mock(return_value=Response(404))
    route = respx.post(f"{BB}/refs/branches").mock(return_value=Response(201, json={}))
    await _bb().create_branch("change/x", SHA)
    assert not lookup.called
    assert json.loads(route.calls.last.request.content)["target"]["hash"] == SHA


@respx.mock
async def test_bitbucket_merge_202_polls_task_status() -> None:
    task_url = f"{BB}/pullrequests/5/merge/task-status/abc"
    respx.post(f"{BB}/pullrequests/5/merge").mock(
        return_value=Response(202, headers={"Location": task_url})
    )
    task = respx.get(task_url).mock(
        side_effect=[
            Response(200, json={"task_status": "PENDING"}),
            Response(200, json={"task_status": "SUCCESS"}),
        ]
    )
    respx.get(f"{BB}/pullrequests/5").mock(return_value=Response(200, json=_bb_pr("MERGED")))
    mr = await _bb().merge_mr(5)
    assert task.call_count == 2 and mr.state == "merged"


@respx.mock
async def test_bitbucket_merge_202_still_pending() -> None:
    task_url = f"{BB}/pullrequests/5/merge/task-status/abc"
    respx.post(f"{BB}/pullrequests/5/merge").mock(
        return_value=Response(202, headers={"Location": task_url})
    )
    respx.get(task_url).mock(return_value=Response(200, json={"task_status": "PENDING"}))
    respx.get(f"{BB}/pullrequests/5").mock(return_value=Response(200, json=_bb_pr()))
    mr = await _bb().merge_mr(5)
    assert mr.state == "pending"
