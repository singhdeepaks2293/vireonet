"""In-memory Git provider with real commit history (tests and demo mode).

Implements the GitProvider protocol: branches, per-path commit history
(newest first), file content at a branch head or at any commit SHA, and
merge requests that fast-forward the target branch when merged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.errors import AdapterNotFound
from vireonet.adapters.git.base import GitCommit, GitCommitResult, GitFile, GitMR, GitPipeline


@dataclass
class _Commit:
    sha: str
    path: str
    content: str
    message: str
    author: str = "vireonet"
    date: str = ""


@dataclass
class FakeGitRepo:
    repo: str = "netops/configs"
    # branch -> commits, oldest first
    branches: dict[str, list[_Commit]] = field(default_factory=lambda: {"main": []})
    mrs: dict[int, GitMR] = field(default_factory=dict)
    _clock: int = 0

    @property
    def name(self) -> str:
        return "fake-git"

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name="fake-git", status="ok", capabilities=self.capabilities())

    def capabilities(self) -> list[str]:
        return ["files", "branches", "mrs", "commits"]

    # --- helpers -----------------------------------------------------------

    def _history(self, ref: str) -> list[_Commit]:
        if ref in self.branches:
            return self.branches[ref]
        for commits in self.branches.values():
            for n, c in enumerate(commits):
                if c.sha == ref or c.sha.startswith(ref):
                    return commits[: n + 1]
        raise AdapterNotFound(self.name, "ref", f"unknown ref {ref}")

    def seed(self, path: str, content: str, message: str, branch: str = "main") -> str:
        """Commit directly (test/demo setup)."""
        self._clock += 1
        sha = hashlib.sha1(f"{self.repo}{branch}{path}{content}{self._clock}".encode()).hexdigest()
        date = f"2026-11-{1 + self._clock % 28:02d}T10:00:00Z"
        self.branches.setdefault(branch, []).append(_Commit(sha, path, content, message, date=date))
        return sha

    # --- GitProvider -------------------------------------------------------

    async def get_file(self, path: str, ref: str) -> GitFile:
        for c in reversed(self._history(ref)):
            if c.path == path:
                return GitFile(path=path, content=c.content, sha=c.sha)
        raise AdapterNotFound(self.name, "get_file", f"file {path} not found at {ref}")

    async def list_commits(self, path: str, ref: str, limit: int = 20) -> list[GitCommit]:
        hist = [c for c in reversed(self._history(ref)) if c.path == path]
        return [GitCommit(c.sha, c.message, c.author, c.date) for c in hist[:limit]]

    async def create_branch(self, branch: str, from_ref: str) -> str:
        self.branches.setdefault(branch, list(self._history(from_ref)))
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        sha = self.seed(path, content, message, branch)
        return GitCommitResult(sha=sha, branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        mr_id = len(self.mrs) + 1
        mr = GitMR(
            mr_id,
            mr_id,
            title,
            source_branch,
            target_branch,
            f"fake://{self.repo}/mr/{mr_id}",
            "opened",
        )
        self.mrs[mr_id] = mr
        return mr

    async def get_mr(self, mr_id: int) -> GitMR:
        return self.mrs[mr_id]

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        return GitPipeline(id=str(mr_id), status="success", url="fake://pipeline")

    async def merge_mr(self, mr_id: int) -> GitMR:
        mr = self.mrs[mr_id]
        self.branches[mr.target_branch] = list(self.branches[mr.source_branch])
        mr.state = "merged"
        return mr

    async def add_comment(self, mr_id: int, body: str) -> None:
        return None

    async def get_mr_diff(self, mr_id: int) -> str:
        return ""


def fake_git_factory() -> tuple[Callable[[str], FakeGitRepo], dict[str, FakeGitRepo]]:
    """(factory, repos): one FakeGitRepo per repo name, created on first use."""
    repos: dict[str, FakeGitRepo] = {}

    def factory(repo: str) -> FakeGitRepo:
        return repos.setdefault(repo, FakeGitRepo(repo))

    return factory, repos
