from __future__ import annotations

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.git.base import GitProvider
from vireonet.adapters.git.bitbucket import BitbucketAdapter
from vireonet.adapters.git.gitea import GiteaAdapter
from vireonet.adapters.git.github import GitHubAdapter
from vireonet.adapters.git.gitlab import GitLabAdapter

DEFAULT_BASE_URLS = {
    "gitlab": "https://gitlab.com/api/v4",
    "github": "https://api.github.com",
    "bitbucket": "https://api.bitbucket.org/2.0",
    "gitea": "",  # no cloud default; GIT_BASE_URL is required
}


def get_git_provider(
    *, provider: str, base_url: str, token: str, repo: str, timeout: float = 10.0
) -> GitProvider:
    """Select the Git provider by config. Token is passed in, never read here."""
    p = provider.lower()
    if p not in DEFAULT_BASE_URLS:
        raise AdapterValidationError(
            "git",
            "get_git_provider",
            f"unknown GIT_PROVIDER {provider!r}; expected one of gitlab, github, bitbucket, gitea",
        )
    url = (base_url or DEFAULT_BASE_URLS[p]).rstrip("/")
    if p == "gitea" and not url:
        raise AdapterValidationError(
            "git", "get_git_provider", "GIT_BASE_URL is required for Gitea"
        )
    if not token:
        raise AdapterValidationError(
            "git", "get_git_provider", "git token is required (from Vault, GIT_TOKEN_SECRET)"
        )
    if not repo:
        raise AdapterValidationError("git", "get_git_provider", "GIT_REPO is required")
    cfg = AdapterConfig(name=p, base_url=url, timeout_seconds=timeout, max_retries=0)
    if p == "gitlab":
        return GitLabAdapter(cfg, token=token, project=repo.replace("/", "%2F"))
    if p == "github":
        return GitHubAdapter(cfg, token=token, repo=repo)
    if p == "bitbucket":
        return BitbucketAdapter(cfg, token=token, repo=repo)
    return GiteaAdapter(cfg, token=token, repo=repo)
