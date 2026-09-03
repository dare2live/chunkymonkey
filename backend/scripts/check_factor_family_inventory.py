#!/usr/bin/env python3
"""L2 gate: factor-family inventory + gate_matrix structural honesty.

Authority: 本文件 + factor_family_inventory.yaml (因子族边界, 窗口对齐)

Run:
  PYTHONPATH=backend python backend/scripts/check_factor_family_inventory.py
  PYTHONPATH=backend python backend/scripts/check_factor_family_inventory.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.factor_family_inventory import (  # noqa: E402
    audit_report,
    collect_violations,
    load_inventory,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_factor_family_inventory")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit_report()
        viol = list(report.get("violations") or [])
        if not viol:
            viol = collect_violations(load_inventory())
            report["violations"] = viol
            report["verdict"] = "PASS" if not viol else "FAIL"
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
                f"FAIL factor_family_inventory: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif viol:
        print("FAIL factor_family_inventory:", file=sys.stderr)
        for item in viol:
            print(f"  - {item}", file=sys.stderr)
    else:
        print(
            "OK factor_family_inventory: "
            f"families={report.get('family_count', 0)} "
            f"gates={report.get('gate_count', 0)} "
            "bricks+domains+matrix wired"
        )
    return 0 if not viol else 1


if __name__ == "__main__":
    raise SystemExit(main())
