#!/usr/bin/env python3
"""Batfish gate CLI for CI. Exit 1 when Batfish fails. Writes a JSON report."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from vireonet.adapters.base import AdapterConfig  # noqa: E402
from vireonet.adapters.batfish import BatfishAdapter  # noqa: E402
from vireonet.adapters.fakes import FakeBatfishAdapter  # noqa: E402
from vireonet.batfish_gate import run_batfish_gate  # noqa: E402
from vireonet.config import get_settings  # noqa: E402
from vireonet.intent import parse_intent  # noqa: E402


async def _run(candidate_dir: Path, current_dir: Path, intent_path: Path | None, use_fake: bool) -> dict:
    candidate = {p.name: p.read_text() for p in candidate_dir.glob("*.cfg")} if candidate_dir.exists() else {}
    current = {p.name: p.read_text() for p in current_dir.glob("*.cfg")} if current_dir.exists() else {}
    if intent_path is not None:
        intent = parse_intent(yaml.safe_load(intent_path.read_text()))
        settings = get_settings()
        batfish = FakeBatfishAdapter() if use_fake else BatfishAdapter(
            AdapterConfig(name="batfish"), host=settings.batfish_host, port=settings.batfish_port,
            snapshot_dir=settings.batfish_snapshot_dir,
        )
        return await run_batfish_gate(intent, current, batfish)
    # No intent: analyse the candidate snapshot directly.
    settings = get_settings()
    batfish = FakeBatfishAdapter() if use_fake else BatfishAdapter(
        AdapterConfig(name="batfish"), host=settings.batfish_host, port=settings.batfish_port,
        snapshot_dir=settings.batfish_snapshot_dir,
    )
    result = await batfish.analyse(candidate)
    return {"passed": result.passed, "failures": result.failures, "evidence": result.evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--current", default="configs/current")
    parser.add_argument("--intent", default=None)
    parser.add_argument("--out", default="batfish-report.json")
    parser.add_argument("--fake", action="store_true", help="use Fake Batfish (no Batfish server)")
    args = parser.parse_args()
    report = asyncio.run(_run(Path(args.candidate), Path(args.current), Path(args.intent) if args.intent else None, args.fake))
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
