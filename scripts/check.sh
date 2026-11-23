#!/usr/bin/env bash
# F4 hardening gate: lint + format + strict types + fast tests.
# CI runs this as the lint job; the test job runs the full pytest suite.
# `python -m` keeps this working in CI (pip install -e ".[dev]") and in a
# local venv without relying on ruff/mypy/pytest being on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy --strict src/vireonet
python -m pytest -q -k "not temporal"
