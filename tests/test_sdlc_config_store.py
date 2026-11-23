"""SDLC — Requirements R12: config version control (M12).

One repo per device, templates (Jinja only) separate from vars (YAML only),
NetBox Jinja separate and read-only. Fake GitProvider in-memory; no network.
"""
from __future__ import annotations

import pytest

from vireonet.adapters.git.base import GitFile, GitMR
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.layout import TEMPLATES_REPO, VARS_REPO, device_paths, device_repo
from vireonet.config_store.netbox_export import NetBoxJinjaExport
from vireonet.config_store.templates import TemplateStore
from vireonet.config_store.vars_store import VarsStore


def test_device_repo_naming() -> None:
    assert device_repo("leaf-01") == "netops/config-leaf-01"
    assert device_repo("Leaf_01") == "netops/config-leaf-01"
    assert device_repo("core-01") == "netops/config-core-01"


def test_device_repo_name_never_contains_serial() -> None:
    assert "FOC1234A" not in device_repo("leaf-01")
    assert device_repo("leaf-01") != device_repo("leaf-02")


@pytest.mark.parametrize("bad", ["", "!!!", "---"])
def test_device_repo_rejects_empty_hostname(bad: str) -> None:
    with pytest.raises(ValueError):
        device_repo(bad)


def test_device_paths_are_distinct_per_device() -> None:
    assert device_paths("leaf-01")["rendered"] == "rendered/running-config.txt"
    assert device_paths("leaf-01")["vars_snapshot"] == "vars.yaml"
    assert device_paths("leaf-01")["metadata"] == "metadata.yaml"
    assert set(device_paths("leaf-01")) == set(device_paths("leaf-02"))  # same layout, different repo


def test_template_and_vars_repos_are_different() -> None:
    assert TEMPLATES_REPO != VARS_REPO
    assert TEMPLATES_REPO == "netops/templates"
    assert VARS_REPO == "netops/vars"


class _FakeGit:
    """Minimal GitProvider stub recording every call."""

    def __init__(self, files: dict[str, str], healthy: bool = True) -> None:
        self.files = files
        self.healthy = healthy
        self.calls: list[tuple] = []

    async def health_check(self):
        from vireonet.adapters.base import AdapterHealth

        return AdapterHealth(name="fake", status="ok" if self.healthy else "down", capabilities=self.capabilities())

    def capabilities(self) -> list[str]:
        return ["get_file"]

    async def get_file(self, path: str, ref: str) -> GitFile:
        self.calls.append(("get_file", path, ref))
        if path not in self.files:
            from vireonet.adapters.errors import AdapterNotFound

            raise AdapterNotFound("fake", "get_file", path)
        return GitFile(path=path, content=self.files[path])

    async def create_branch(self, branch: str, from_ref: str) -> str:
        self.calls.append(("create_branch", branch, from_ref))
        return branch

    async def commit_file(self, branch: str, path: str, content: str, message: str):
        from vireonet.adapters.git.base import GitCommitResult

        self.calls.append(("commit_file", branch, path, message))
        self.files[path] = content
        return GitCommitResult(sha="sha1", branch=branch, path=path)

    async def open_mr(self, *, source_branch: str, target_branch: str, title: str, description: str = "") -> GitMR:
        self.calls.append(("open_mr", source_branch, target_branch, title))
        return GitMR(id=1, iid=1, title=title, source_branch=source_branch, target_branch=target_branch, url="https://git.example/mr/1", state="opened")

    async def get_mr(self, mr_id: int) -> GitMR:
        raise NotImplementedError

    async def get_pipeline(self, mr_id: int):
        raise NotImplementedError

    async def merge_mr(self, mr_id: int) -> GitMR:
        raise NotImplementedError

    async def add_comment(self, mr_id: int, body: str) -> None:
        raise NotImplementedError

    async def get_mr_diff(self, mr_id: int) -> str:
        raise NotImplementedError


async def test_template_store_rejects_ip_in_template() -> None:
    git = _FakeGit({"templates/ios-xe/vlan.j2": "ip address 10.0.0.1 255.255.255.0"})
    with pytest.raises(ValueError, match="separation rule"):
        await TemplateStore(git).get_template("ios-xe/vlan.j2")


async def test_template_store_rejects_secret_in_template() -> None:
    git = _FakeGit({"templates/ios-xe/vlan.j2": "enable password hunter2"})
    with pytest.raises(ValueError, match="separation rule"):
        await TemplateStore(git).get_template("ios-xe/vlan.j2")


async def test_template_store_returns_clean_template() -> None:
    git = _FakeGit({"templates/ios-xe/vlan.j2": "vlan {{ vlan_id }}\n name {{ vlan_name }}"})
    assert await TemplateStore(git).get_template("ios-xe/vlan.j2") == "vlan {{ vlan_id }}\n name {{ vlan_name }}"


@pytest.mark.parametrize("bad", ["../etc/passwd", "/absolute/path.j2"])
async def test_template_store_rejects_path_traversal(bad: str) -> None:
    git = _FakeGit({})
    with pytest.raises(ValueError):
        await TemplateStore(git).get_template(bad)


async def test_template_store_lists_from_manifest() -> None:
    git = _FakeGit({"manifest.yaml": "- ios-xe/vlan.j2\n- ios-xe/acl.j2\n"})
    assert await TemplateStore(git).list_templates() == ["ios-xe/vlan.j2", "ios-xe/acl.j2"]


async def test_vars_store_merges_site_role_device_type() -> None:
    git = _FakeGit({
        "site/dc1.yaml": "ntp: 10.0.0.1\ndomain: dc1.example\n",
        "role/leaf.yaml": "domain: leaf.example\nmtu: 9000\n",
        "device-type/C9300.yaml": "mtu: 9216\n",
    })
    merged = await VarsStore(git).get_vars(site="dc1", role="leaf", device_type="C9300")
    assert merged["ntp"] == "10.0.0.1"
    assert merged["domain"] == "leaf.example"  # role overrides site
    assert merged["mtu"] == 9216  # device-type overrides role


async def test_vars_store_forbids_jinja() -> None:
    git = _FakeGit({"site/dc1.yaml": "name: {{ hostname }}\n"})
    with pytest.raises(ValueError, match="separation rule"):
        await VarsStore(git).get_vars(site="dc1", role="leaf", device_type="C9300")


async def test_vars_store_missing_files_are_skipped() -> None:
    git = _FakeGit({})
    assert await VarsStore(git).get_vars(site="dc1", role="leaf", device_type="C9300") == {}


async def test_device_repo_manager_commits_rendered_to_that_device_only() -> None:
    created: dict[str, _FakeGit] = {}

    def factory(repo: str) -> _FakeGit:
        git = _FakeGit({}, healthy=True)
        created[repo] = git
        return git

    manager = DeviceRepoManager(factory)
    url = await manager.commit_rendered(
        hostname="leaf-01", ticket_number="RITM0001234", rendered="vlan 100",
        vars_snapshot="vlan_id: 100", template_version="v1", metadata="template: v1",
    )
    assert url == "https://git.example/mr/1"
    git = created["netops/config-leaf-01"]
    branches = [c[1] for c in git.calls if c[0] == "create_branch"]
    assert branches == ["change/RITM0001234"]
    committed = [c[2] for c in git.calls if c[0] == "commit_file"]
    assert "rendered/candidate-config.txt" in committed
    assert "vars.yaml" in committed
    assert "metadata.yaml" in committed
    assert "netops/config-leaf-02" not in created  # other devices untouched


async def test_device_repo_manager_requires_existing_repo() -> None:
    def factory(repo: str) -> _FakeGit:
        return _FakeGit({}, healthy=False)

    with pytest.raises(ValueError, match="does not exist"):
        await DeviceRepoManager(factory).commit_rendered(
            hostname="leaf-01", ticket_number="RITM0001234", rendered="x",
            vars_snapshot="x", template_version="v1", metadata="x",
        )


async def test_device_repo_history_is_empty_documented_gap() -> None:
    # Known limitation (M12): GitProvider has no list-commits API, so history
    # returns []. This test pins the behaviour so it cannot silently change.
    manager = DeviceRepoManager(lambda repo: _FakeGit({}))
    assert await manager.history("leaf-01") == []


async def test_device_repo_backup_commits_to_backup_path() -> None:
    created: dict[str, _FakeGit] = {}

    def factory(repo: str) -> _FakeGit:
        git = _FakeGit({}, healthy=True)
        created[repo] = git
        return git

    sha = await DeviceRepoManager(factory).commit_backup(hostname="leaf-01", ticket_number="RITM0001234", running="hostname leaf-01")
    assert sha == "sha1"
    git = created["netops/config-leaf-01"]
    assert any(c[0] == "commit_file" and c[2] == "backups/running-config.txt" for c in git.calls)


class _FakeNetBox:
    def __init__(self, jinja: str) -> None:
        self.jinja = jinja
        self.calls: list[str] = []

    async def get_device(self, name: str):
        from vireonet.adapters.protocols import NetBoxDevice

        self.calls.append(name)
        dev = NetBoxDevice(name, "FOC1234A", "C9300-48P", "dc1", "active", "ios-xe")
        dev.config_context = {"jinja_template": self.jinja}  # type: ignore[attr-defined]
        return dev


async def test_netbox_export_is_read_only_audit_copy() -> None:
    created: dict[str, _FakeGit] = {}

    def factory(repo: str) -> _FakeGit:
        git = _FakeGit({}, healthy=True)
        created[repo] = git
        return git

    url = await NetBoxJinjaExport(_FakeNetBox("{% for v in vlans %}vlan {{ v }}{% endfor %}"), factory).export_device_template("leaf-01", ticket_number="RITM0001234")
    assert url == "https://git.example/mr/1"
    # Export goes to netbox-export repo only, never to the device config repo.
    assert "netops/netbox-export" in created
    assert "netops/config-leaf-01" not in created
    git = created["netops/netbox-export"]
    assert any(c[0] == "commit_file" and c[2] == "leaf-01.jinja" for c in git.calls)


async def test_netbox_export_empty_jinja_does_nothing() -> None:
    created: dict[str, _FakeGit] = {}

    def factory(repo: str) -> _FakeGit:
        git = _FakeGit({}, healthy=True)
        created[repo] = git
        return git

    result = await NetBoxJinjaExport(_FakeNetBox(""), factory).export_device_template("leaf-01", ticket_number="RITM0001234")
    assert result == ""
    assert created == {}


async def test_template_store_never_reads_netbox_export() -> None:
    # Separation: the render source is netops/templates, not NetBox Jinja.
    git = _FakeGit({"templates/ios-xe/vlan.j2": "vlan {{ vlan_id }}"})
    await TemplateStore(git).get_template("ios-xe/vlan.j2")
    paths = [c[1] for c in git.calls if c[0] == "get_file"]
    assert paths == ["templates/ios-xe/vlan.j2"]
    assert not any("netbox" in p for p in paths)
