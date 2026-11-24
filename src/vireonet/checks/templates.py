"""User-defined, per-intent pre/post check templates (policies/checks.yaml).

Operators describe which checks run for each change type, before and after
apply, without writing Python. A check is either:

- a **built-in** check, by name (``reachability``, ``backup_taken``, ...), or
- a **command** check: a read-only show command rendered per device and
  per platform (vendor-neutral: ``ios``, ``nxos``, ``eos``, ``junos``, ...,
  plus ``default``), whose output is tested with ``contains`` /
  ``not_contains`` / ``regex`` / ``not_regex``.

Safety rules (enforced at load time and again after rendering):

- templates render in a sandboxed Jinja environment with StrictUndefined,
  so a typo in ``{{ intent.vlan_idd }}`` is an error, not an empty string;
- rendered commands must be one line and start with an allowed read-only
  prefix (``show``, ``display``, ``get`` by default);
- locks and backups are taken by the workflow regardless of templates;
  removing ``backup_taken`` from a template removes the *check*, never the
  backup itself.

The file is policy: review it in Git like the other policies/*.yaml files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from vireonet.checks.base import Check, CheckContext, CheckResult
from vireonet.intent import ChangeIntent

Phase = Literal["pre", "post"]

# Change types the intent model accepts (vireonet.intent). Keep in sync.
KNOWN_CHANGE_TYPES = frozenset(
    {
        "vlan_add",
        "vlan_remove",
        "acl_entry_add",
        "acl_entry_remove",
        "static_route_add",
        "static_route_remove",
        "interface_set",
        "ztp_device",
        "config_push",
    }
)
DEFAULT_ALLOWED_PREFIXES = ("show ", "display ", "get ")

_ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)

# Runs read-only commands on one device: (device, platform, commands) -> outputs.
CommandRunner = Callable[[str, str, list[str]], Awaitable[list[str]]]


class TemplateValidationError(ValueError):
    """The check-template file is invalid. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# --- File model --------------------------------------------------------------


class Expectation(BaseModel):
    model_config = {"extra": "forbid"}

    contains: str | None = None
    not_contains: str | None = None
    regex: str | None = None
    not_regex: str | None = None
    # With `parse: textfsm`: field -> regex (templated). `row`: at least one
    # parsed row matches every field. `no_row`: no parsed row matches.
    row: dict[str, str] | None = None
    no_row: dict[str, str] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Expectation:
        if not any(v is not None for v in self.model_dump().values()):
            raise ValueError(
                "expect needs one of contains, not_contains, regex, not_regex, row, no_row"
            )
        return self


class CommandCheckSpec(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    description: str = ""
    # platform -> command template. "default" is used when the device's
    # platform has no entry.
    command: dict[str, str]
    # One expectation for every platform, or platform -> expectation (with
    # an optional "default") when vendors format output differently.
    expect: Expectation | dict[str, Expectation]
    # block: a failure fails the phase. warn: recorded, never blocks.
    on_fail: Literal["block", "warn"] = "block"
    # What to do when no command matches the device platform.
    on_missing_platform: Literal["skip", "fail"] = "skip"
    # raw: test the text. textfsm: parse it first (your templates in
    # policies/textfsm, then ntc-templates) and test fields with row/no_row.
    parse: Literal["raw", "textfsm"] = "raw"

    @field_validator("command")
    @classmethod
    def _non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("command needs at least one platform entry (or 'default')")
        return v

    def expectation_for(self, platform: str) -> Expectation | None:
        if isinstance(self.expect, Expectation):
            return self.expect
        return self.expect.get(platform) or self.expect.get("default")

    def all_expectations(self) -> dict[str, Expectation]:
        if isinstance(self.expect, Expectation):
            return {"*": self.expect}
        return dict(self.expect)


class BuiltinCheckSpec(BaseModel):
    model_config = {"extra": "forbid"}

    builtin: str
    on_fail: Literal["block", "warn"] = "block"


CheckSpec = CommandCheckSpec | BuiltinCheckSpec


class PhaseSpec(BaseModel):
    model_config = {"extra": "forbid"}

    # Prepend the file's defaults for this phase (default true).
    extends_defaults: bool = True
    checks: list[CheckSpec] = []


class ChangeTypeSpec(BaseModel):
    model_config = {"extra": "forbid"}

    description: str = ""
    pre: PhaseSpec = PhaseSpec()
    post: PhaseSpec = PhaseSpec()


class TemplatesFile(BaseModel):
    model_config = {"extra": "forbid"}

    version: Literal[1] = 1
    allowed_command_prefixes: list[str] = list(DEFAULT_ALLOWED_PREFIXES)
    defaults: dict[Phase, list[CheckSpec]] = {}
    change_types: dict[str, ChangeTypeSpec] = {}


def _checks(raw: Any) -> Any:
    """Short form `- reachability` means `- {builtin: reachability}`."""
    if isinstance(raw, list):
        return [{"builtin": x} if isinstance(x, str) else x for x in raw]
    return raw


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Expand the short forms, only where check lists live."""
    out = dict(raw)
    if isinstance(out.get("defaults"), dict):
        out["defaults"] = {k: _checks(v) for k, v in out["defaults"].items()}
    if isinstance(out.get("change_types"), dict):
        types: dict[str, Any] = {}
        for name, spec in out["change_types"].items():
            spec = dict(spec or {})
            for phase in ("pre", "post"):
                value = spec.get(phase)
                if isinstance(value, list):  # `pre: [a, b]` == `pre: {checks: [a, b]}`
                    value = {"checks": value}
                if isinstance(value, dict):
                    value = {**value, "checks": _checks(value.get("checks", []))}
                if value is not None:
                    spec[phase] = value
            types[name] = spec
        out["change_types"] = types
    return out


# --- Rendering ---------------------------------------------------------------


def render(template: str, variables: dict[str, Any]) -> str:
    return _ENV.from_string(template).render(**variables)


def _check_command_safe(command: str, prefixes: list[str]) -> str | None:
    """Problem with a (rendered or literal-prefix) command, or None."""
    if "\n" in command or "\r" in command:
        return "command must be a single line"
    lowered = command.strip().lower()
    if not any(lowered.startswith(p.lower()) for p in prefixes):
        return f"command {command!r} must start with one of {prefixes} (read-only only)"
    return None


def _literal_prefix(template: str) -> str:
    """The template text before its first Jinja tag."""
    return re.split(r"{{|{%", template, maxsplit=1)[0]


# --- Running -----------------------------------------------------------------


@dataclass
class TemplateCommandCheck:
    """A command check from the template file, runnable by the CheckEngine."""

    spec: CommandCheckSpec
    prefixes: list[str]

    @property
    def name(self) -> str:
        return self.spec.name

    def command_for(self, platform: str) -> str | None:
        return self.spec.command.get(platform) or self.spec.command.get("default")

    def render_for(self, intent: ChangeIntent, device: str, platform: str) -> str | None:
        template = self.command_for(platform)
        if template is None:
            return None
        variables = _variables(intent, device, platform)
        command = render(template, variables).strip()
        problem = _check_command_safe(command, self.prefixes)
        if problem:
            raise TemplateValidationError([f"{self.name}: {problem}"])
        return command

    def evaluate(
        self, output: str, intent: ChangeIntent, device: str, platform: str, command: str = ""
    ) -> str | None:
        """Failure message for one device's output, or None when it matches."""
        variables = _variables(intent, device, platform)
        exp = self.spec.expectation_for(platform)
        if exp is None:
            return f"no expectation defined for platform {platform!r}"
        if self.spec.parse == "textfsm":
            problem = _evaluate_rows(exp, output, command, platform, variables)
            if problem:
                return problem
        if exp.contains is not None:
            needle = render(exp.contains, variables)
            if needle not in output:
                return f"expected output to contain {needle!r}"
        if exp.not_contains is not None:
            needle = render(exp.not_contains, variables)
            if needle in output:
                return f"expected output not to contain {needle!r}"
        if exp.regex is not None:
            pattern = render(exp.regex, variables)
            if re.search(pattern, output, re.MULTILINE) is None:
                return f"expected output to match /{pattern}/"
        if exp.not_regex is not None:
            pattern = render(exp.not_regex, variables)
            if re.search(pattern, output, re.MULTILINE) is not None:
                return f"expected output not to match /{pattern}/"
        return None

    async def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.run_commands is None:
            return self._result("fail", "no command runner configured for template checks", {})
        failures: list[str] = []
        skipped: list[str] = []
        evidence: dict[str, object] = {}
        for device in ctx.intent.devices:
            platform = await _platform_of(ctx, device)
            try:
                command = self.render_for(ctx.intent, device, platform)
            except (TemplateError, TemplateValidationError) as exc:
                failures.append(f"{device}: template error: {exc}")
                continue
            if command is None:
                msg = f"{device}: no command for platform {platform or 'unknown'!r}"
                if self.spec.on_missing_platform == "fail":
                    failures.append(msg)
                else:
                    skipped.append(msg)
                continue
            try:
                outputs = await ctx.run_commands(device, platform, [command])
            except Exception as exc:  # noqa: BLE001 — a failed command run is check evidence
                failures.append(f"{device}: command failed: {exc}")
                continue
            output = outputs[0] if outputs else ""
            evidence[device] = {"platform": platform, "command": command, "output": output[-2000:]}
            try:
                problem = self.evaluate(output, ctx.intent, device, platform, command)
            except (TemplateError, re.error) as exc:
                problem = f"expectation error: {exc}"
            if problem:
                failures.append(f"{device}: `{command}`: {problem}")
        if failures:
            status = "fail" if self.spec.on_fail == "block" else "skip"
            prefix = "" if self.spec.on_fail == "block" else "WARN (non-blocking): "
            return self._result(status, prefix + "; ".join(failures), evidence)
        if skipped and not evidence:
            return self._result("skip", "; ".join(skipped), evidence)
        message = self.spec.description or "output matched on all devices"
        if skipped:
            message += f" (skipped: {'; '.join(skipped)})"
        return self._result("pass", message, evidence)

    def _result(self, status: Any, message: str, evidence: dict[str, object]) -> CheckResult:
        return CheckResult(self.name, status, message, evidence=evidence)


def _evaluate_rows(
    exp: Expectation, output: str, command: str, platform: str, variables: dict[str, Any]
) -> str | None:
    from vireonet.textfsm_lib import TextFsmError, parse

    try:
        rows = parse(platform, command, output).rows
    except TextFsmError as exc:
        return f"TextFSM: {exc}"

    def matches(row: dict[str, Any], want: dict[str, str]) -> bool:
        for field_name, pattern in want.items():
            value = row.get(field_name.lower())
            text = " ".join(map(str, value)) if isinstance(value, list) else str(value or "")
            if re.fullmatch(render(pattern, variables), text) is None:
                return False
        return True

    if exp.row is not None and not any(matches(r, exp.row) for r in rows):
        want = {k: render(v, variables) for k, v in exp.row.items()}
        return f"no parsed row with {want} ({len(rows)} rows)"
    if exp.no_row is not None and any(matches(r, exp.no_row) for r in rows):
        want = {k: render(v, variables) for k, v in exp.no_row.items()}
        return f"a parsed row matches {want}, which must not exist"
    return None


@dataclass
class WarnOnly:
    """Wraps a built-in check so its failure is recorded but never blocks."""

    inner: Check

    @property
    def name(self) -> str:
        return self.inner.name

    async def run(self, ctx: CheckContext) -> CheckResult:
        result = await self.inner.run(ctx)
        if result.status == "fail":
            return CheckResult(
                result.name,
                "skip",
                f"WARN (non-blocking): {result.message}",
                evidence=result.evidence,
            )
        return result


def _variables(intent: ChangeIntent, device: str, platform: str) -> dict[str, Any]:
    return {
        "intent": intent.model_dump(),
        "device": device,
        "platform": platform,
        "ticket": intent.ticket_number,
    }


async def _platform_of(ctx: CheckContext, device: str) -> str:
    try:
        return str((await ctx.netbox.get_device(device)).platform or "")
    except Exception:  # noqa: BLE001 — unknown platform falls back to "default"
        return ""


# --- Loading -----------------------------------------------------------------


class CheckTemplates:
    """Validated templates, ready to build the check list for an intent."""

    def __init__(self, data: TemplatesFile, source: str = "<builtin>", text: str = "") -> None:
        self.data = data
        self.source = source
        # The YAML as written, so the UI editor starts from the real file.
        self.text = text

    # Built-ins are resolved lazily to avoid an import cycle with plugins.
    @staticmethod
    def builtins() -> dict[str, Callable[[], Check]]:
        from vireonet.checks import plugins

        return {
            "reachability": plugins.ReachabilityCheck,
            "no_active_lock": plugins.NoActiveLockCheck,
            "interface_state": plugins.InterfaceStateCheck,
            "backup_taken": plugins.BackupTakenCheck,
            "neighbour_baseline": plugins.NeighbourBaselineCheck,
            "config_matches_intent": plugins.ConfigMatchesIntentCheck,
            "no_unexpected_diff": plugins.NoUnexpectedDiffCheck,
        }

    @classmethod
    def default(cls) -> CheckTemplates:
        """The behaviour before templates existed: the same lists for every type."""
        return cls.from_yaml_text(DEFAULT_TEMPLATES_YAML, source="<builtin>")

    @classmethod
    def from_file(cls, path: str | Path) -> CheckTemplates:
        p = Path(path)
        return cls.from_yaml_text(p.read_text(encoding="utf-8"), source=str(p))

    @classmethod
    def load(cls, path: str | Path) -> CheckTemplates:
        """The file when it exists, else the built-in defaults. Raises on an invalid file."""
        return cls.from_file(path) if Path(path).exists() else cls.default()

    @classmethod
    def from_yaml_text(cls, text: str, source: str = "<text>") -> CheckTemplates:
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise TemplateValidationError([f"YAML does not parse: {exc}"]) from exc
        if not isinstance(raw, dict):
            raise TemplateValidationError(["top level must be a mapping"])
        try:
            data = TemplatesFile.model_validate(_normalise(raw))
        except ValidationError as exc:
            raise TemplateValidationError(
                [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            ) from exc
        errors = cls._semantic_errors(data)
        if errors:
            raise TemplateValidationError(errors)
        return cls(data, source, text)

    @classmethod
    def _semantic_errors(cls, data: TemplatesFile) -> list[str]:
        errors: list[str] = []
        known = cls.builtins()
        prefixes = data.allowed_command_prefixes
        if not prefixes:
            errors.append("allowed_command_prefixes must not be empty")
        for ct in data.change_types:
            if ct not in KNOWN_CHANGE_TYPES:
                errors.append(
                    f"change_types.{ct}: unknown change type (known: {sorted(KNOWN_CHANGE_TYPES)})"
                )

        def check_list(where: str, specs: list[CheckSpec]) -> None:
            seen: set[str] = set()
            for spec in specs:
                name = spec.builtin if isinstance(spec, BuiltinCheckSpec) else spec.name
                if name in seen:
                    errors.append(f"{where}: check {name!r} listed twice")
                seen.add(name)
                if isinstance(spec, BuiltinCheckSpec):
                    if spec.builtin not in known:
                        errors.append(
                            f"{where}: unknown built-in {spec.builtin!r} (known: {sorted(known)})"
                        )
                    continue
                if spec.name in known:
                    errors.append(f"{where}.{spec.name}: name clashes with a built-in check")
                for platform, template in spec.command.items():
                    loc = f"{where}.{spec.name}.command.{platform}"
                    try:
                        _ENV.parse(template)
                    except TemplateError as exc:
                        errors.append(f"{loc}: template does not parse: {exc}")
                        continue
                    literal = _literal_prefix(template).lstrip()
                    if not literal:
                        errors.append(
                            f"{loc}: command must start with literal text, not a template tag"
                        )
                    elif not any((literal.lower() + " ").startswith(p.lower()) for p in prefixes):
                        errors.append(
                            f"{loc}: command {template!r} must start with one of {prefixes} "
                            "(read-only only)"
                        )
                uses_rows = any(
                    e.row is not None or e.no_row is not None
                    for e in spec.all_expectations().values()
                )
                if uses_rows and spec.parse != "textfsm":
                    errors.append(f"{where}.{spec.name}: row/no_row need `parse: textfsm`")
                if isinstance(spec.expect, dict) and not spec.expect:
                    errors.append(f"{where}.{spec.name}.expect: must not be empty")
                for plat, exp in spec.all_expectations().items():
                    for key, value in exp.model_dump().items():
                        if value is None:
                            continue
                        for part in value.values() if isinstance(value, dict) else [value]:
                            try:
                                _ENV.parse(part)
                            except TemplateError as exc:
                                errors.append(
                                    f"{where}.{spec.name}.expect.{plat}.{key}: does not parse: {exc}"
                                )
                    if (
                        isinstance(spec.expect, dict)
                        and plat != "default"
                        and plat not in spec.command
                        and "default" not in spec.command
                    ):
                        errors.append(
                            f"{where}.{spec.name}.expect.{plat}: no command for that platform"
                        )

        for phase, specs in data.defaults.items():
            check_list(f"defaults.{phase}", specs)
        for ct, spec in data.change_types.items():
            for phase in ("pre", "post"):
                ps: PhaseSpec = getattr(spec, phase)
                combined = (data.defaults.get(phase, []) if ps.extends_defaults else []) + ps.checks
                check_list(f"change_types.{ct}.{phase}", combined)
        return errors

    def specs_for(self, change_type: str, phase: Phase) -> list[CheckSpec]:
        ct = self.data.change_types.get(change_type)
        defaults = list(self.data.defaults.get(phase, []))
        if ct is None:
            return defaults
        ps: PhaseSpec = getattr(ct, phase)
        return (defaults if ps.extends_defaults else []) + list(ps.checks)

    def checks_for(self, intent: ChangeIntent, phase: Phase) -> list[Check]:
        known = self.builtins()
        checks: list[Check] = []
        for spec in self.specs_for(intent.change_type, phase):
            if isinstance(spec, BuiltinCheckSpec):
                check: Check = known[spec.builtin]()
                checks.append(WarnOnly(check) if spec.on_fail == "warn" else check)
            else:
                checks.append(TemplateCommandCheck(spec, self.data.allowed_command_prefixes))
        return checks

    def preview(self, intent: ChangeIntent, platform: str) -> dict[str, list[dict[str, Any]]]:
        """What would run for this intent on a device of this platform."""
        out: dict[str, list[dict[str, Any]]] = {}
        device = intent.devices[0]
        for phase in ("pre", "post"):
            rows: list[dict[str, Any]] = []
            for check in self.checks_for(intent, phase):
                row: dict[str, Any] = {"name": check.name}
                if isinstance(check, TemplateCommandCheck):
                    row["kind"] = "command"
                    row["on_fail"] = check.spec.on_fail
                    try:
                        row["command"] = check.render_for(intent, device, platform)
                    except (TemplateError, TemplateValidationError) as exc:
                        row["error"] = str(exc)
                    exp = check.spec.expectation_for(platform)
                    if exp is not None and "error" not in row:
                        try:
                            variables = _variables(intent, device, platform)
                            row["expect"] = {
                                k: (
                                    {f: render(x, variables) for f, x in v.items()}
                                    if isinstance(v, dict)
                                    else render(v, variables)
                                )
                                for k, v in exp.model_dump().items()
                                if v is not None
                            }
                            row["parse"] = check.spec.parse
                        except TemplateError as exc:
                            row["error"] = f"expectation: {exc}"
                else:
                    row["kind"] = "builtin"
                    row["on_fail"] = "warn" if isinstance(check, WarnOnly) else "block"
                rows.append(row)
            out[phase] = rows
        return out

    def summary(self) -> dict[str, Any]:
        """JSON-friendly view of every change type's pre/post lists."""

        def spec_view(spec: CheckSpec) -> dict[str, Any]:
            if isinstance(spec, BuiltinCheckSpec):
                return {"kind": "builtin", "name": spec.builtin, "on_fail": spec.on_fail}
            return {
                "kind": "command",
                "name": spec.name,
                "description": spec.description,
                "command": dict(spec.command),
                "expect": {
                    plat: {k: v for k, v in exp.model_dump().items() if v is not None}
                    for plat, exp in spec.all_expectations().items()
                },
                "on_fail": spec.on_fail,
                "on_missing_platform": spec.on_missing_platform,
                "parse": spec.parse,
            }

        return {
            "source": self.source,
            "allowed_command_prefixes": list(self.data.allowed_command_prefixes),
            "change_types": {
                ct: {
                    phase: [spec_view(s) for s in self.specs_for(ct, phase)]
                    for phase in ("pre", "post")
                }
                for ct in sorted(KNOWN_CHANGE_TYPES)
            },
        }


def engine_command_runner(engine: Any, intent: ChangeIntent) -> CommandRunner:
    """CommandRunner backed by an ExecutionEngine's run_commands step."""
    from vireonet.execution.types import DeviceRef, StepRequest

    async def run(device: str, platform: str, commands: list[str]) -> list[str]:
        req = StepRequest(
            ticket_number=intent.ticket_number,
            device=DeviceRef(name=device, platform=platform),
            step="run_commands",
            change_type=intent.change_type,
            extra_vars={"commands": json.dumps(commands)},
            dry_run=True,
            correlation_id=intent.ticket_number,
        )
        result = await engine.run_commands(req)
        if not result.ok:
            raise RuntimeError(f"run_commands job failed (job {result.job_id})")
        outputs = list(result.output.get("command_outputs") or [])
        if len(outputs) != len(commands):
            raise RuntimeError(f"expected {len(commands)} outputs, got {len(outputs)}")
        return [str(o) for o in outputs]

    return run


# Same lists every change type used before templates existed.
DEFAULT_TEMPLATES_YAML = """
version: 1
defaults:
  pre: [reachability, no_active_lock, interface_state, backup_taken, neighbour_baseline]
  post: [reachability, config_matches_intent, no_unexpected_diff, neighbour_baseline]
"""
