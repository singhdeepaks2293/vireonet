"""E3: script manifest gate, declared-change contract, dry-run hash."""

from __future__ import annotations

import pytest

from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.types import DeclaredChange, DeviceRef
from vireonet.scripts.contract import dry_run_hash, validate_declared_changes
from vireonet.scripts.manifest import ScriptManifest
from vireonet.scripts.runner import ScriptRunner

MANIFEST = ScriptManifest.from_yaml("policies/scripts.yaml")
IFACE_SHA = "9f2c1a4b7d8e3f0a1b2c3d4e5f6a7b8c9d0e1f2a"
VLAN_SHA = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"


class FakeGit:
    """Minimal GitProvider stand-in: records get_file calls."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {
            "scripts/set_interface_description.py": "print('hi')\n",
            "scripts/add_vlan_nornir.py": "print('hi')\n",
        }
        self.get_calls: list[tuple[str, str]] = []
        self.commits: list[str] = []

    async def get_file(self, path: str, ref: str) -> object:
        self.get_calls.append((path, ref))
        if path not in self.files:
            raise ValueError(f"not found: {path}")
        return {"path": path, "content": self.files[path]}


def _iface_change() -> DeclaredChange:
    return DeclaredChange(
        object_type="interface",
        identifier="GigabitEthernet0/1",
        field="description",
        old_value="",
        new_value="uplink",
        config_lines=["interface GigabitEthernet0/1", " description uplink"],
    )


def _vlan_change() -> DeclaredChange:
    return DeclaredChange(
        object_type="vlan",
        identifier="100",
        field="name",
        old_value="",
        new_value="web",
        config_lines=["vlan 100", " name web"],
    )


def _runner(
    engine: FakeExecutionEngine, git: FakeGit | None = None
) -> tuple[ScriptRunner, FakeGit]:
    git = git or FakeGit()
    return ScriptRunner(engine, git, MANIFEST), git


async def test_unlisted_script_refused_before_awx() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_iface_change()])
    runner, _ = _runner(engine)
    with pytest.raises(ValueError, match="not listed in the script manifest"):
        await runner.run(
            "nope",
            "abc",
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "interface_set",
            "",
            False,
        )
    assert engine.calls == []


async def test_wrong_sha_refused_before_awx() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_iface_change()])
    runner, _ = _runner(engine)
    with pytest.raises(ValueError, match="SHA mismatch"):
        await runner.run(
            "set_interface_description",
            "deadbeef",
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "interface_set",
            "",
            False,
        )
    assert engine.calls == []


async def test_wrong_change_type_and_platform_refused() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_iface_change()])
    runner, _ = _runner(engine)
    with pytest.raises(ValueError, match="not allowed for change type"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "vlan_add",
            "",
            False,
        )
    with pytest.raises(ValueError, match="not allowed for platform"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="eos"),
            "interface_set",
            "",
            False,
        )
    assert engine.calls == []


async def test_missing_script_in_git_refused() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_iface_change()])
    git = FakeGit()
    del git.files["scripts/set_interface_description.py"]
    runner, _ = _runner(engine, git)
    with pytest.raises(ValueError, match="not found in git"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "interface_set",
            "",
            False,
        )
    assert engine.calls == []


async def test_change_outside_manifest_scope_refused_no_reconcile() -> None:
    # The script declares a vlan change; its manifest writes are interface.*.
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_vlan_change()])
    runner, git = _runner(engine)
    with pytest.raises(ValueError, match="outside manifest writes"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "interface_set",
            "",
            False,
        )
    assert len(engine.calls) == 1  # the engine ran, the contract refused after
    assert git.commits == []  # no git write from the runner
    entry = MANIFEST.get("set_interface_description")
    assert entry is not None
    assert validate_declared_changes(_fake_result(engine), entry) != []


def _fake_result(engine: FakeExecutionEngine):  # type: ignore[no-untyped-def]
    # Rebuild the StepResult the fake engine returned, for contract checks.
    from vireonet.execution.types import StepResult, StepStatus

    return StepResult(
        status=StepStatus.SUCCESSFUL,
        step="apply",
        device="leaf-01",
        output={},
        declared_changes=list(engine.python_declared_changes),
    )


async def test_missing_declared_changes_fails_step() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[])
    runner, _ = _runner(engine)
    with pytest.raises(ValueError, match="declared_changes is empty"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            DeviceRef(name="leaf-01", platform="iosxe"),
            "interface_set",
            "",
            False,
        )


async def test_dry_run_hash_mismatch_at_apply_refused() -> None:
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_iface_change()])
    runner, _ = _runner(engine)
    device = DeviceRef(name="leaf-01", platform="iosxe")
    dry = await runner.run(
        "set_interface_description",
        IFACE_SHA,
        "dry_run",
        "RITM1",
        device,
        "interface_set",
        "interface Gi0/1\n description uplink\n",
        True,
    )
    assert dry.ok
    approved = dry_run_hash(dry)
    with pytest.raises(ValueError, match="apply output differs from the approved dry-run"):
        await runner.run(
            "set_interface_description",
            IFACE_SHA,
            "apply",
            "RITM1",
            device,
            "interface_set",
            "interface Gi0/1\n description uplink\n",
            False,
            approved_hash="0" * 64,
        )
    # The same hash approves the apply.
    applied = await runner.run(
        "set_interface_description",
        IFACE_SHA,
        "apply",
        "RITM1",
        device,
        "interface_set",
        "interface Gi0/1\n description uplink\n",
        False,
        approved_hash=approved,
    )
    assert applied.ok
