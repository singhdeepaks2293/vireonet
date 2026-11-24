"""Running flow Python scripts safely.

Layers, from cheapest to strongest (each one alone is not enough; together
they make a script a bounded, side-effect-free computation):

1. Static check before running: only allow-listed imports; no dunder
   attribute access (``__class__``, ``__subclasses__``, ``__globals__`` …);
   no eval/exec/compile/open/__import__/globals.
2. Restricted builtins at run time: the script's ``__import__`` only loads
   allow-listed modules; open/eval/exec/input/breakpoint are gone.
3. Process limits: separate ``python -I`` process, empty environment (no
   VireoNet secrets), own temp dir, CPU / memory / file-size / open-file
   limits, own session, hard timeout and kill.
4. Privileges: when the worker runs as root the script runs as ``nobody``
   (it cannot read the worker's /proc/<pid>/environ or config files) and,
   where the kernel allows it, in its own empty network namespace (no
   network at all). The result reports which of these were in force;
   ``require_isolation`` refuses to run without them.
5. Strongest: the separate script-runner service (``python -m
   vireonet.flows.script_runner``): a container with no secrets, read-only
   root filesystem, internal network only, non-root, HMAC-signed requests.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import sys
import tempfile
import textwrap
from typing import Any

DEFAULT_ALLOWED_IMPORTS = [
    "json",
    "re",
    "math",
    "statistics",
    "datetime",
    "time",
    "ipaddress",
    "collections",
    "itertools",
    "functools",
    "operator",
    "string",
    "textwrap",
    "difflib",
    "hashlib",
    "base64",
    "binascii",
    "uuid",
    "decimal",
    "fractions",
    "random",
    "csv",
    "typing",
    "dataclasses",
    "enum",
    "copy",
    "pprint",
    "bisect",
    "heapq",
    "zlib",
]
FORBIDDEN_NAMES = {
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
    "globals",
    "locals",
    "vars",
    "input",
    "breakpoint",
    "memoryview",
    "getattr",
    "setattr",
    "delattr",
    "help",
    "exit",
    "quit",
}
SCRIPT_MARK = "\x1evireonet-result\x1e"
NOBODY = 65534
MAX_OUT = 100_000


class ScriptError(Exception):
    pass


def check_code(code: str, allowed_imports: list[str]) -> list[str]:
    """Problems that stop the script before it runs."""
    try:
        tree = ast.parse(code, filename="<flow script>")
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]
    allowed = set(allowed_imports)
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in allowed:
                    errors.append(f"line {node.lineno}: import of {a.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if node.level or mod not in allowed:
                errors.append(f"line {node.lineno}: import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"line {node.lineno}: access to {node.attr!r} is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            # "{0.__class__}".format(x) and similar string-based attribute tricks.
            errors.append(f"line {node.lineno}: strings containing '__' are not allowed")
        elif isinstance(node, ast.Name) and (
            node.id in FORBIDDEN_NAMES or (node.id.startswith("__") and node.id != "__name__")
        ):
            errors.append(f"line {node.lineno}: {node.id!r} is not allowed in flow scripts")
    return sorted(
        set(errors),
        key=lambda e: int(e.split()[1].rstrip(":")) if e.split()[1].rstrip(":").isdigit() else 0,
    )


# Trusted wrapper (runs before the user code, in the child process).
_WRAPPER = textwrap.dedent(
    r"""
    import builtins as _b, json as _json, os as _os, socket as _socket, sys as _sys
    _cfg = _json.loads(_sys.stdin.read())
    _iso = {"uid": _os.getuid(), "non_root": _os.getuid() != 0}
    try:
        _ifaces = [n for _, n in _socket.if_nameindex()]
    except OSError:
        _ifaces = []
    _iso["network_isolated"] = all(n == "lo" for n in _ifaces)
    if _cfg["require_isolation"] and not (_iso["non_root"] and _iso["network_isolated"]):
        _sys.stderr.write("IsolationError: this worker cannot isolate scripts (needs non-root + no network); use the script-runner service\n")
        _sys.exit(3)
    _allowed = set(_cfg["allowed_imports"])
    _real_import = _b.__import__
    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".")[0] not in _allowed:
            raise ImportError(f"import of {name!r} is not allowed in flow scripts")
        return _real_import(name, globals, locals, fromlist, level)
    _safe = {k: getattr(_b, k) for k in dir(_b) if not k.startswith("_") and k not in _cfg["forbidden"]}
    _safe["__import__"] = _guarded_import
    _safe["__build_class__"] = _b.__build_class__
    _g = {"__name__": "__flow_script__", "__builtins__": _safe, "inputs": _cfg["inputs"], "result": None}
    with open(_sys.argv[1], encoding="utf-8") as _f:
        _code = compile(_f.read(), "<flow script>", "exec")
    del _f
    exec(_code, _g)
    _sys.stdout.write("\n" + MARK + _json.dumps({"result": _g.get("result"), "isolation": _iso}, default=str) + "\n")
    """
).replace("MARK", repr(SCRIPT_MARK))


def _preexec(memory_mb: int, cpu_seconds: int) -> Any:
    def apply() -> None:  # in the child, before exec (POSIX)
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        with contextlib.suppress(ValueError, OSError):  # not supported on macOS
            resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 2**20, memory_mb * 2**20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 2**20, 10 * 2**20))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        os.setsid()
        if os.geteuid() == 0:
            # Empty network namespace (needs CAP_SYS_ADMIN; best effort), then
            # drop to nobody so /proc/<worker>/environ and files are unreadable.
            unshare = getattr(os, "unshare", None)  # Linux only
            if unshare is not None:
                with contextlib.suppress(OSError):
                    unshare(getattr(os, "CLONE_NEWNET", 0x40000000))
            os.setgroups([])
            os.setgid(NOBODY)
            os.setuid(NOBODY)

    return apply


async def run_script(
    code: str,
    inputs: dict[str, Any],
    *,
    timeout: int = 30,
    memory_mb: int = 512,
    allowed_imports: list[str] | None = None,
    require_isolation: bool = False,
) -> dict[str, Any]:
    allowed = list(allowed_imports or DEFAULT_ALLOWED_IMPORTS)
    problems = check_code(code, allowed)
    if problems:
        raise ScriptError("script refused: " + "; ".join(problems[:5]))
    with tempfile.TemporaryDirectory(prefix="vn-flow-") as tmp:
        os.chmod(tmp, 0o755)
        path = os.path.join(tmp, "script.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        os.chmod(path, 0o644)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            "-c",
            _WRAPPER,
            path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": tmp,
                "PYTHONIOENCODING": "utf-8",
                "LANG": "C.UTF-8",
            },
            preexec_fn=_preexec(memory_mb, timeout + 1) if os.name == "posix" else None,
        )
        cfg = {
            "inputs": inputs,
            "allowed_imports": allowed,
            "forbidden": sorted(FORBIDDEN_NAMES - {"__import__"}),
            "require_isolation": require_isolation,
        }
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(cfg, default=str).encode()), timeout=timeout
            )
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, 9)
            await proc.wait()
            raise ScriptError(f"script timed out after {timeout}s") from exc
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if proc.returncode != 0:
        tail = stderr.strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
        raise ScriptError(f"script failed: {tail[0][:500]}")
    head, _, tail_json = stdout.rpartition(SCRIPT_MARK)
    try:
        payload = json.loads(tail_json)
    except ValueError as exc:
        raise ScriptError("script did not produce a JSON result") from exc
    return {
        "result": payload.get("result"),
        "isolation": payload.get("isolation", {}),
        "stdout": head.strip()[:MAX_OUT],
        "stderr": stderr.strip()[:MAX_OUT],
    }
