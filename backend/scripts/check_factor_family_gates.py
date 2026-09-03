#!/usr/bin/env python3
"""L2 gate: factor-family frequency-typed continuity / readiness matrix.

Authority: 本文件 + factor_family_inventory.yaml (因子族边界, 窗口对齐)

Run:
  PYTHONPATH=backend python backend/scripts/check_factor_family_gates.py
  PYTHONPATH=backend python backend/scripts/check_factor_family_gates.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.factor_family_continuity_gates import gate_audit_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_factor_family_gates")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = gate_audit_report()
        viol = list(report.get("violations") or [])
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(
                json.dumps(
                    {
                        "verdict": "FAIL",
                        "violations": [f"{type(exc).__name__}: {exc}"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"FAIL factor_family_gates: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif viol:
        print("FAIL factor_family_gates:", file=sys.stderr)
        for item in viol:
            print(f"  - {item}", file=sys.stderr)
    else:
        modes = report.get("families_by_mode") or {}
        print(
            "OK factor_family_gates: "
            f"families={report.get('family_count', 0)} "
            f"modes={len(modes)} matrix+wiring"
        )
    return 0 if not viol else 1


if __name__ == "__main__":
    raise SystemExit(main())
