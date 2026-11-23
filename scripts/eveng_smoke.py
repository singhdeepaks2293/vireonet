#!/usr/bin/env python3
"""Optional EVE-NG smoke job. Always wipes the lab, even on failure."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vireonet.adapters.base import AdapterConfig  # noqa: E402
from vireonet.adapters.eveng import EveNgAdapter  # noqa: E402
from vireonet.config import get_settings  # noqa: E402


async def _run(topology: str, lab_name: str) -> int:
    settings = get_settings()
    adapter = EveNgAdapter(
        AdapterConfig(name="eveng", base_url=settings.eveng_base_url),
        username=settings.eveng_username,
        password=settings.eveng_password,
    )
    lab = await adapter.start_lab(lab_name, topology)
    try:
        await adapter.load_topology(lab.id, topology)
        ready = await adapter.wait_for_nodes(lab.id, timeout_seconds=600)
        print(f"lab {ready.id} ready: {ready.nodes}")
        return 0
    finally:
        # Always clean up — a leaked lab blocks the next CI run.
        try:
            await adapter.stop_lab(lab.id)
        finally:
            await adapter.wipe_lab(lab.id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True)
    parser.add_argument("--lab", required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.topology, args.lab))


if __name__ == "__main__":
    raise SystemExit(main())
