import pytest
import respx
from httpx import Response

from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.git.registry import get_git_provider

FAKE_KWARGS = {"timeout": 5.0}


@pytest.mark.asyncio
@respx.mock
async def test_github_open_pr_and_pipeline():
    respx.get("https://api.github.com/repos/acme/intent/pulls/7").mock(
        return_value=Response(
            200,
            json={
                "number": 7,
                "title": "RITM0001234: Add VLAN 100",
                "state": "open",
                "head": {"ref": "change/RITM0001234", "sha": "abc123"},
                "base": {"ref": "main"},
                "html_url": "https://github.com/acme/intent/pull/7",
            },
        )
    )
    respx.get("https://api.github.com/repos/acme/intent/commits/abc123/check-runs").mock(
        return_value=Response(
            200, json={"check_runs": [{"status": "completed", "conclusion": "success"}]}
        )
    )
    provider = get_git_provider(
        provider="github", base_url="", token="gh-test", repo="acme/intent", **FAKE_KWARGS
    )
    pr = await provider.get_mr(7)
    assert pr.title == "RITM0001234: Add VLAN 100"
    pipeline = await provider.get_pipeline(7)
    assert pipeline.status == "success"


@pytest.mark.asyncio
@respx.mock
async def test_gitea_open_pr():
    respx.get("https://gitea.example.com/api/v1/repos/acme/intent/pulls/3").mock(
        return_value=Response(
            200,
            json={
                "number": 3,
                "title": "RITM0001234: Add VLAN 100",
                "state": "open",
                "head": {"label": "acme:change/RITM0001234"},
                "base": {"label": "acme:main"},
                "html_url": "https://gitea.example.com/acme/intent/pulls/3",
            },
        )
    )
    provider = get_git_provider(
        provider="gitea",
        base_url="https://gitea.example.com",
        token="gitea-test",
        repo="acme/intent",
        **FAKE_KWARGS,
    )
    pr = await provider.get_mr(3)
    assert pr.source_branch == "change/RITM0001234"
    assert pr.target_branch == "main"


@pytest.mark.asyncio
@respx.mock
async def test_bitbucket_open_pr():
    respx.get("https://api.bitbucket.org/2.0/repositories/acme/intent/pullrequests/5").mock(
        return_value=Response(
            200,
            json={
                "id": 5,
                "title": "RITM0001234: Add VLAN 100",
                "state": "OPEN",
                "source": {"branch": {"name": "change/RITM0001234"}},
                "destination": {"branch": {"name": "main"}},
                "links": {"html": {"href": "https://bitbucket.org/acme/intent/pull-requests/5"}},
            },
        )
    )
    provider = get_git_provider(
        provider="bitbucket", base_url="", token="bb-test", repo="acme/intent", **FAKE_KWARGS
    )
    pr = await provider.get_mr(5)
    assert pr.state == "open"
    assert pr.source_branch == "change/RITM0001234"


def test_registry_rejects_unknown_provider():
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider="svn", base_url="", token="x", repo="acme/intent")


def test_registry_requires_gitea_base_url():
    with pytest.raises(AdapterValidationError):
        get_git_provider(provider="gitea", base_url="", token="x", repo="acme/intent")
