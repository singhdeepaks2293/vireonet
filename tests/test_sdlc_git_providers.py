"""SDLC — Requirements R10: Git providers (M10).

Contract tests for all four GitProvider implementations via respx
(no network). Extends tests/test_git_providers.py: registry, status
normalisation, get_file / branch / MR flows per provider.
"""
from __future__ import annotations

import base64

import pytest
import respx
from httpx import Response

from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.git.base import normalise_pipeline_status
from vireonet.adapters.git.registry import get_git_provider

KW = {"timeout": 5.0}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("success", "success"), ("successful", "success"), ("passed", "success"),
        ("failed", "failed"), ("failure", "failed"), ("error", "failed"),
        ("running", "running"), ("pending", "pending"), ("created", "pending"),
        ("canceled", "canceled"), ("cancelled", "canceled"), ("skipped", "canceled"),
        ("weird-status", "unknown"), ("", "unknown"),
    ],
)
def test_normalise_pipeline_status(raw: str, expected: str) -> None:
    assert normalise_pipeline_status(raw) == expected


def test_registry_returns_correct_provider_types() -> None:
    assert get_git_provider(provider="github", base_url="", token="t", repo="acme/intent", **KW).name == "github"
    assert get_git_provider(provider="gitea", base_url="https://gitea.example.com", token="t", repo="acme/intent", **KW).name == "gitea"
    assert get_git_provider(provider="bitbucket", base_url="", token="t", repo="acme/intent", **KW).name == "bitbucket"
    assert get_git_provider(provider="gitlab", base_url="", token="t", repo="acme/intent", **KW).name == "gitlab"


def test_registry_is_case_insensitive() -> None:
    assert get_git_provider(provider="GitHub", base_url="", token="t", repo="acme/intent", **KW).name == "github"


@pytest.mark.parametrize("bad", ["svn", "mercurial", "", "git"])
def test_registry_rejects_unknown_provider(bad: str) -> None:
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider=bad, base_url="", token="t", repo="acme/intent", **KW)


@pytest.mark.parametrize("provider", ["github", "gitlab", "bitbucket", "gitea"])
def test_registry_requires_token_and_repo(provider: str) -> None:
    base = "https://gitea.example.com" if provider == "gitea" else ""
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider=provider, base_url=base, token="", repo="acme/intent", **KW)
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider=provider, base_url=base, token="t", repo="", **KW)


def test_registry_gitea_requires_base_url() -> None:
    with pytest.raises(AdapterValidationError, match="GIT_BASE_URL"):
        get_git_provider(provider="gitea", base_url="", token="t", repo="acme/intent", **KW)


def test_registry_rejects_bad_repo_shape() -> None:
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider="github", base_url="", token="t", repo="no-slash", **KW)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@respx.mock
async def test_github_get_file_decodes_base64() -> None:
    respx.get("https://api.github.com/repos/acme/intent/contents/vlan.j2?ref=main").mock(
        return_value=Response(200, json={"type": "file", "content": _b64("vlan {{ vlan_id }}"), "sha": "abc"})
    )
    provider = get_git_provider(provider="github", base_url="", token="t", repo="acme/intent", **KW)
    f = await provider.get_file("vlan.j2", "main")
    assert f.content == "vlan {{ vlan_id }}"
    assert f.sha == "abc"


@respx.mock
async def test_github_create_branch_and_open_mr() -> None:
    respx.get("https://api.github.com/repos/acme/intent/git/ref/heads/main").mock(
        return_value=Response(200, json={"object": {"sha": "deadbeef"}})
    )
    respx.post("https://api.github.com/repos/acme/intent/git/refs").mock(return_value=Response(201, json={}))
    respx.post("https://api.github.com/repos/acme/intent/pulls").mock(
        return_value=Response(201, json={"number": 9, "title": "RITM0001234: x", "state": "open", "html_url": "https://github.com/acme/intent/pull/9"})
    )
    provider = get_git_provider(provider="github", base_url="", token="t", repo="acme/intent", **KW)
    assert await provider.create_branch("change/RITM0001234", "main") == "change/RITM0001234"
    mr = await provider.open_mr(source_branch="change/RITM0001234", target_branch="main", title="RITM0001234: x")
    assert mr.id == 9 and mr.url.endswith("/pull/9")


@respx.mock
async def test_gitlab_get_file_and_empty_pipeline() -> None:
    respx.get("https://gitlab.com/api/v4/projects/acme%2Fintent/repository/files/vlan.j2?ref=main").mock(
        return_value=Response(200, json={"content": _b64("vlan 100"), "blob_id": "b1"})
    )
    respx.get("https://gitlab.com/api/v4/projects/acme%2Fintent/merge_requests/4/pipelines").mock(
        return_value=Response(200, json=[])
    )
    provider = get_git_provider(provider="gitlab", base_url="", token="t", repo="acme/intent", **KW)
    f = await provider.get_file("vlan.j2", "main")
    assert f.content == "vlan 100"
    pipe = await provider.get_pipeline(4)
    assert pipe.status == "unknown"


@respx.mock
async def test_gitlab_pipeline_success_maps() -> None:
    respx.get("https://gitlab.com/api/v4/projects/acme%2Fintent/merge_requests/4/pipelines").mock(
        return_value=Response(200, json=[{"id": 77, "status": "success", "web_url": "https://gitlab.com/p/77", "ref": "main"}])
    )
    provider = get_git_provider(provider="gitlab", base_url="", token="t", repo="acme/intent", **KW)
    pipe = await provider.get_pipeline(4)
    assert pipe.status == "success"
    assert pipe.id == "77"


@respx.mock
async def test_gitea_pipeline_success_and_empty() -> None:
    provider = get_git_provider(provider="gitea", base_url="https://gitea.example.com", token="t", repo="acme/intent", **KW)
    pr_url = "https://gitea.example.com/api/v1/repos/acme/intent/pulls/3"
    respx.get(pr_url).mock(return_value=Response(200, json={"number": 3, "title": "t", "state": "open", "head": {"label": "acme:change/x"}, "base": {"label": "acme:main"}, "html_url": "https://gitea.example.com/acme/intent/pulls/3"}))
    respx.get("https://gitea.example.com/api/v1/repos/acme/intent/actions/runs").mock(
        return_value=Response(200, json={"workflow_runs": [{"id": 5, "status": "success", "html_url": "https://gitea.example.com/run/5"}]})
    )
    pipe = await provider.get_pipeline(3)
    assert pipe.status == "success"

    respx.get("https://gitea.example.com/api/v1/repos/acme/intent/actions/runs").mock(
        return_value=Response(200, json={"workflow_runs": []})
    )
    pipe = await provider.get_pipeline(3)
    assert pipe.status == "unknown"


@respx.mock
async def test_bitbucket_pipeline_failed_maps() -> None:
    respx.get("https://api.bitbucket.org/2.0/repositories/acme/intent/pullrequests/5").mock(
        return_value=Response(200, json={"id": 5, "title": "t", "state": "OPEN", "source": {"branch": {"name": "change/x"}}, "destination": {"branch": {"name": "main"}}, "links": {"html": {"href": "https://bitbucket.org/acme/intent/pull-requests/5"}}})
    )
    respx.get("https://api.bitbucket.org/2.0/repositories/acme/intent/pullrequests/5/statuses").mock(
        return_value=Response(200, json={"values": [{"key": "ci", "state": "FAILED", "url": "https://bitbucket.org/ci"}]})
    )
    provider = get_git_provider(provider="bitbucket", base_url="", token="t", repo="acme/intent", **KW)
    pipe = await provider.get_pipeline(5)
    assert pipe.status == "failed"


@respx.mock
async def test_all_providers_report_capabilities() -> None:
    for provider_name, base in [
        ("github", ""), ("gitlab", ""), ("bitbucket", ""), ("gitea", "https://gitea.example.com"),
    ]:
        provider = get_git_provider(provider=provider_name, base_url=base, token="t", repo="acme/intent", **KW)
        caps = provider.capabilities()
        assert "open_mr" in caps and "get_file" in caps and "merge_mr" in caps
