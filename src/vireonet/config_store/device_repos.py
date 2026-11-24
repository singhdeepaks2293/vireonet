from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from vireonet.adapters.git.base import GitCommit, GitProvider
from vireonet.config_store.layout import GitLayout, device_paths
from vireonet.config_store.store import ConfigStore


class DeviceRepoManager:
    """One repo per device. Every change is a commit in that device's repo only."""

    def __init__(
        self,
        git_factory: Callable[[str], GitProvider],
        config_store: ConfigStore | None = None,
        layout: GitLayout | None = None,
    ) -> None:
        self.layout = layout or GitLayout()
        # git_factory(repo: str) -> GitProvider, bound to one repo at a time.
        # The M10 GitProvider is bound to a single repo, so per-device repos need
        # a factory, not a single shared instance.
        self._factory = git_factory
        # F1: when a ConfigStore is wired in, version history comes from the
        # stored running configs instead of the (unimplemented) git history.
        self._config_store = config_store

    def repo_name(self, hostname: str) -> str:
        return self.layout.repo_for(hostname)

    async def ensure_repo(self, hostname: str) -> str:
        repo = self.layout.repo_for(hostname)
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
        await git.create_branch(branch, self.layout.branch)
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
            target_branch=self.layout.branch,
            title=f"{ticket_number}: config for {hostname}",
            description=f"Template {template_version}. Rendered config for {hostname}.",
        )
        return str(mr.url)

    async def commit_backup(self, *, hostname: str, ticket_number: str, running: str) -> str:
        repo = await self.ensure_repo(hostname)
        git = self._factory(repo)
        branch = f"backup/{ticket_number}"
        await git.create_branch(branch, self.layout.branch)
        result = await git.commit_file(
            branch,
            self.layout.running_path(hostname),
            running,
            f"{ticket_number}: running backup for {hostname}",
        )
        await git.open_mr(
            source_branch=branch,
            target_branch=self.layout.branch,
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
        await git.create_branch(branch, self.layout.branch)
        result = await git.commit_file(branch, self.layout.running_path(hostname), content, message)
        await git.open_mr(
            source_branch=branch,
            target_branch=self.layout.branch,
            title=message,
            description=f"Reconciled running config for {hostname}.",
        )
        return str(result.sha)

    async def get_rendered(self, hostname: str, ref: str | None = None) -> str:
        git = self._factory(self.layout.repo_for(hostname))
        # In the monorepo layout the branch file IS the running config.
        path = (
            self.layout.running_path(hostname)
            if self.layout.mode == "monorepo"
            else device_paths(hostname)["rendered"]
        )
        return str((await git.get_file(path, ref or self.layout.branch)).content)

    # --- Version history and rollback targets --------------------------------

    async def commits(self, hostname: str, limit: int = 20) -> list[GitCommit]:
        """Commits on the main/master branch that changed this device's config."""
        git = self._factory(self.layout.repo_for(hostname))
        return list(
            await git.list_commits(self.layout.running_path(hostname), self.layout.branch, limit)
        )

    async def running_config_at(self, hostname: str, ref: str) -> str:
        """The device's config file at a branch or commit."""
        git = self._factory(self.layout.repo_for(hostname))
        content = str((await git.get_file(self.layout.running_path(hostname), ref)).content)
        if not content.strip():
            raise ValueError(f"{hostname}: config at {ref} is empty")
        return content

    async def resolve_target(self, hostname: str, target: str) -> GitCommit:
        """Rollback target -> commit.

        "branch" (or the branch name, e.g. main/master): the branch head, i.e.
            the approved running config.
        "previous": the commit before the head ("last commit" before the
            latest change to this device's config).
        a commit SHA (7-40 hex): that commit, which must be in this device's
            config history on the branch.
        """
        history = await self.commits(hostname, limit=50)
        if not history:
            raise ValueError(f"{hostname}: no commits for {self.layout.running_path(hostname)}")
        if target in {"branch", self.layout.branch, "main", "master", "head"}:
            return history[0]
        if target == "previous":
            if len(history) < 2:
                raise ValueError(f"{hostname}: only one commit; there is no previous version")
            return history[1]
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", target):
            for commit in history:
                if commit.sha.startswith(target.lower()) or commit.sha == target:
                    return commit
            raise ValueError(f"{hostname}: commit {target} is not in this device's config history")
        raise ValueError(f"unknown rollback target {target!r} (branch | previous | <sha>)")

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
