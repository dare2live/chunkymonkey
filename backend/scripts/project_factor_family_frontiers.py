"""Project factor-family defer frontiers (K3).

Usage:
  PYTHONPATH=backend python backend/scripts/project_factor_family_frontiers.py
  PYTHONPATH=backend python backend/scripts/project_factor_family_frontiers.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.factor_family_frontier_projection import (  # noqa: E402
    assert_defer_reasons_honest,
    project_family_frontiers,
    write_frontier_projection,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--path", default="", help="Output projection JSON path")
    args = ap.parse_args(argv)

    viol = assert_defer_reasons_honest()
    if viol:
        print("FAIL inventory defer reasons:", file=sys.stderr)
        for v in viol:
            print(f"  - {v}", file=sys.stderr)
        return 2

    sm = raw = org = None
    try:
        from services.db import get_conn
        from services.data_access.resolver import connect_ro
        from services.org_holding_db import connect_org_holding

        sm = get_conn()
        raw = connect_ro("tushare_raw")
        try:
            org = connect_org_holding(read_only=True)
        except Exception as exc:  # noqa: BLE001 — org file missing until split copy
            print(f"[project] org_holding db unavailable: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[project] live DB unavailable: {exc}", file=sys.stderr)

    try:
        payload = project_family_frontiers(
            smartmoney_conn=sm, raw_conn=raw, org_holding_conn=org
        )
        out = write_frontier_projection(
            payload, path=Path(args.path) if args.path else None
        )
    finally:
        if sm is not None:
            sm.close()
        if raw is not None:
            raw.close()
        if org is not None:
            org.close()

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"[project] wrote {out} families={len(payload.get('families') or [])}")
    violations = list(payload.get("violations") or [])
    if violations:
        print("[project] BLOCKED live frontier:", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
