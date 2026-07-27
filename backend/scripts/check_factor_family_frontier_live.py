#!/usr/bin/env python3
"""Fail-closed K3 live frontier artifact gate.

This gate intentionally remains separate from ``check_factor_family_gates.py``,
which validates only the static continuity matrix and inventory wiring.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.factor_family_frontier_projection import (  # noqa: E402
    DEFAULT_PROJECTION_PATH,
    projection_violations,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_factor_family_frontier_live")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--path", type=Path, default=DEFAULT_PROJECTION_PATH)
    parser.add_argument("--max-age-seconds", type=int, default=86_400)
    args = parser.parse_args(argv)
    path = args.path if args.path.is_absolute() else REPO / args.path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        violations = projection_violations(
            payload, max_age_seconds=args.max_age_seconds
        )
    except Exception as exc:  # noqa: BLE001
        payload = {}
        violations = [f"{type(exc).__name__}: {exc}"]
    report = {
        "gate": "factor_family_frontier_live",
        "artifact": str(path),
        "verdict": "PASS" if not violations else "BLOCKED",
        "violations": violations,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif violations:
        print("BLOCKED factor_family_frontier_live:", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
    else:
        print("PASS factor_family_frontier_live")
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
