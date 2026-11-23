#!/usr/bin/env python3
"""Lab smoke test (E4), parameterised by backend.

Same smoke test as scripts/eveng_smoke.py — deploy -> wait ready ->
``show version`` on leaf-01 -> destroy in a ``finally`` — but the backend
is selected with ``--backend eveng|containerlab`` and built through the
registry (``lab_adapter_for``), so the same script covers both lab
backends. ``eveng_smoke.py`` is unchanged; this is the backend-agnostic
replacement for new runs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vireonet.adapters.lab import LabAdapter  # noqa: E402
from vireonet.adapters.registry import lab_adapter_for  # noqa: E402
from vireonet.config import get_settings  # noqa: E402


async def run_lab_smoke(
    adapter: LabAdapter, topology: str, lab_name: str, *, timeout_seconds: int = 600
) -> int:
    """Deploy -> wait ready -> exec "show version" -> destroy, always.

    Returns 0 on success, 1 on failure. The lab is destroyed in a
    ``finally`` whenever deploy returned a lab id — including when
    wait_ready or exec raises — so a failed run never leaks a lab.
    """
    lab_id: str | None = None
    try:
        lab_id = await adapter.deploy(topology, lab_name)
        nodes = await adapter.wait_ready(lab_id, timeout_seconds=timeout_seconds)
        print(f"lab {lab_id} ready: {[n.name for n in nodes]}")
        output = await adapter.exec(lab_id, "leaf-01", "show version")
        print(output[:500])
        return 0
    except Exception as exc:  # noqa: BLE001 — smoke script: report, don't crash
        print(f"lab smoke failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if lab_id is not None:
            # Always clean up — a leaked lab blocks the next CI run.
            try:
                await adapter.destroy(lab_id)
            except Exception as exc:  # noqa: BLE001 — destroy is best-effort here
                print(f"lab destroy failed for {lab_id}: {exc}", file=sys.stderr)


async def _run(topology: str, lab_name: str, backend: str) -> int:
    settings = get_settings()
    adapter = lab_adapter_for(settings, backend)
    return await run_lab_smoke(adapter, topology, lab_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["eveng", "containerlab"], required=True)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--lab", required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.topology, args.lab, args.backend))


if __name__ == "__main__":
    raise SystemExit(main())
