from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vireonet.adapters.git.base import GitProvider
from vireonet.config_store.layout import device_paths, device_repo
from vireonet.config_store.store import ConfigStore


class DeviceRepoManager:
    """One repo per device. Every change is a commit in that device's repo only."""

    def __init__(
        self, git_factory: Callable[[str], GitProvider], config_store: ConfigStore | None = None
    ) -> None:
        # git_factory(repo: str) -> GitProvider, bound to one repo at a time.
        # The M10 GitProvider is bound to a single repo, so per-device repos need
        # a factory, not a single shared instance.
        self._factory = git_factory
        # F1: when a ConfigStore is wired in, version history comes from the
        # stored running configs instead of the (unimplemented) git history.
        self._config_store = config_store

    def repo_name(self, hostname: str) -> str:
        return device_repo(hostname)

    async def ensure_repo(self, hostname: str) -> str:
        repo = device_repo(hostname)
        git = self._factory(repo)
        health = await git.health_check()
        if health.status != "ok":
            # health_check here is "repo exists and is reachable". If it does not
            # exist, the caller creates it via the provider's admin API / UI.
            # Do not auto-create repos with an empty commit — an empty repo has
            # no history and no metadata to audit.
            raise ValueError(
                f"device repo {repo} does not exist; create it first (Gitea/GitLab/GitHub UI or API), then retry"
            )
        return repo

    async def commit_rendered(
        self,
        *,
        hostname: str,
        ticket_number: str,
        rendered: str,
        vars_snapshot: str,
        template_version: str,
        metadata: str,
    ) -> str:
        repo = await self.ensure_repo(hostname)
        git = self._factory(repo)
        branch = f"change/{ticket_number}"
        await git.create_branch(branch, "main")
        paths = device_paths(hostname)
        await git.commit_file(
            branch,
            paths["candidate"],
            rendered,
            f"{ticket_number}: candidate config for {hostname}",
        )
        await git.commit_file(
            branch,
            paths["vars_snapshot"],
            vars_snapshot,
            f"{ticket_number}: vars snapshot for {hostname}",
        )
        await git.commit_file(
            branch,
            paths["metadata"],
            metadata,
            f"{ticket_number}: metadata (template {template_version})",
        )
        mr = await git.open_mr(
            source_branch=branch,
            target_branch="main",
            title=f"{ticket_number}: config for {hostname}",
            description=f"Template {template_version}. Rendered config for {hostname}.",
        )
        return str(mr.url)

    async def commit_backup(self, *, hostname: str, ticket_number: str, running: str) -> str:
        repo = await self.ensure_repo(hostname)
        git = self._factory(repo)
        branch = f"backup/{ticket_number}"
        await git.create_branch(branch, "main")
        result = await git.commit_file(
            branch,
            device_paths(hostname)["backup"],
            running,
            f"{ticket_number}: running backup for {hostname}",
        )
        await git.open_mr(
            source_branch=branch,
            target_branch="main",
            title=f"{ticket_number}: backup for {hostname}",
        )
        return str(result.sha)

    async def commit_config(
        self,
        *,
        hostname: str,
        ticket_number: str,
        content: str,
        message: str,
    ) -> str:
        """Commit a running config to the device repo (E3 reconcile).

        One branch per ticket (``reconcile/<ticket>``), one commit of the
        backup path, then an MR — the same shape as commit_backup, but the
        caller supplies the message (reconcile messages name the job and
        the script that produced the config).
        """
        repo = await self.ensure_repo(hostname)
        git = self._factory(repo)
        branch = f"reconcile/{ticket_number}"
        await git.create_branch(branch, "main")
        result = await git.commit_file(branch, device_paths(hostname)["backup"], content, message)
        await git.open_mr(
            source_branch=branch,
            target_branch="main",
            title=message,
            description=f"Reconciled running config for {hostname}.",
        )
        return str(result.sha)

    async def get_rendered(self, hostname: str, ref: str = "main") -> str:
        git = self._factory(device_repo(hostname))
        return str((await git.get_file(device_paths(hostname)["rendered"], ref)).content)

    async def history(self, hostname: str) -> list[dict[str, Any]]:
        if self._config_store is not None:
            return await self._config_store.history(hostname)
        # The GitProvider protocol has no list-commits API; history is read from
        # the MR list for the repo in a follow-up. This method returns the MR
        # that last touched the rendered config, via get_mr_diff on the latest MR.
        # For now, return an empty list and let the API surface the MR URL from
        # commit_rendered instead of inventing a history.
        return []

    async def compare_history(self, hostname: str, v_from: int, v_to: int) -> dict[str, Any]:
        if self._config_store is None:
            raise ValueError(f"no config store configured for {hostname}; cannot compare versions")
        return await self._config_store.compare_versions(hostname, v_from, v_to)
