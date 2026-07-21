#!/usr/bin/env python3
"""B5 gate: L2/L3 brick registry completeness + hop/raw/orphan/Type-B honesty.

Checks (authority: analysis/data_brick_architecture_20260721.md):
  1. brick_registry.yaml version=1 + max_composite_hops
  2. L2 bricks are primitives; deps never L3/L4; no silent raw_* bypass
  3. L3 feature_blocks hop depth ≤ max_composite_hops
  4. Every FEATURE_BLOCK_ID in backend/services is registered
  5. Every data_layers L2_feature table appears in some outputs (Type-B)
  6. status=partial requires typed partial_reasons; type_b_edge → feature_store
  7. Owner paths exist

Run:
  PYTHONPATH=backend python backend/scripts/check_brick_registry.py
  PYTHONPATH=backend python backend/scripts/check_brick_registry.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.brick_registry import (  # noqa: E402
    audit_report,
    collect_violations,
    load_registry,
)


def build_report() -> dict:
    return audit_report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_brick_registry")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable audit report",
    )
    args = parser.parse_args(argv)
    try:
        report = build_report()
        viol = list(report.get("violations") or [])
        # Re-run collect for fail-closed if report somehow incomplete
        if not viol:
            viol = collect_violations(load_registry())
            report["violations"] = viol
            report["verdict"] = "PASS" if not viol else "FAIL"
    except Exception as exc:  # noqa: BLE001 — gate must fail closed
        if args.json:
            print(
                json.dumps(
                    {
                        "verdict": "FAIL",
                        "violations": [f"{type(exc).__name__}: {exc}"],
                        "orphan_feature_blocks": [],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"FAIL brick_registry: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif viol:
        print("FAIL brick_registry:", file=sys.stderr)
        for item in viol:
            print(f"  - {item}", file=sys.stderr)
    else:
        print(
            "OK brick_registry: L2/L3/Type-B classified; "
            f"l2={report.get('l2_count', 0)} l3={report.get('l3_count', 0)} "
            f"type_b={report.get('type_b_count', 0)} "
            f"orphans=0 type_b_orphans=0 "
            f"hops≤{report.get('max_composite_hops', 2)}"
        )
    return 0 if not viol else 1


if __name__ == "__main__":
    raise SystemExit(main())
