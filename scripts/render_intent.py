#!/usr/bin/env python3
"""Render an intent YAML file to per-device configs. Used by .gitlab-ci.yml render job."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vireonet.intent import parse_intent  # noqa: E402
from vireonet.render import render_candidate_configs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("intent")
    parser.add_argument("--out", required=True)
    parser.add_argument("--current-dir", default="configs/current")
    args = parser.parse_args()
    intent = parse_intent(yaml.safe_load(Path(args.intent).read_text()))
    current: dict[str, str] = {}
    current_dir = Path(args.current_dir)
    if current_dir.exists():
        for p in current_dir.glob("*.cfg"):
            current[p.name] = p.read_text()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in render_candidate_configs(intent, current).items():
        (out_dir / name).write_text(content)
        print(f"wrote {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
