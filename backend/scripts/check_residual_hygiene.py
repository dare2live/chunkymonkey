"""check_residual_hygiene — FOUNDATION F9 residual lag SLA gate.

Type-B raw→fact publish lag + declared ann tip lag vs eligible_end.
FAIL = exit 1 + optional ALERT flag (same pattern as continuity). Does not
mutate Continuity READY and does not clear honest WARN/UNTRUSTED.

Wire: services/pipeline/store.py after continuity; type_b post-evaluate in acquire.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.residual_hygiene import (  # noqa: E402
    evaluate_residual_hygiene,
    load_policy,
    load_trading_days,
)


REPO = Path(__file__).resolve().parents[2]


def write_alert_flag(flag_path: Path, overall: str, findings: list[dict[str, Any]]) -> None:
    if overall == "FAIL":
        lines = [f"[{datetime.now().strftime('%F %T')}] residual_hygiene FAIL"]
        for f in findings:
            if f.get("status") == "fail":
                lines.append(
                    f"  [{f.get('check')}] {f.get('domain')}: {f.get('detail')} "
                    f"lag={f.get('lag_trading_days')}"
                )
        flag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        flag_path.unlink(missing_ok=True)


def _conn_for_alias(alias: str):
    from services.data_access import resolver

    return duck_connect(str(resolver.db_path(alias)), read_only=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Residual hygiene SLA (FOUNDATION F9)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--alert-flag", default=None)
    ap.add_argument("--policy", default=None, help="Override residual_hygiene.yaml path")
    ap.add_argument("--type-b-only", action="store_true")
    args = ap.parse_args(argv)

    policy = load_policy(Path(args.policy) if args.policy else None)
    trading_days = load_trading_days()
    raw_conn = _conn_for_alias("tushare_raw")
    fact_conn = _conn_for_alias("smartmoney")
    try:
        payload = evaluate_residual_hygiene(
            policy=policy,
            trading_days=trading_days,
            raw_conn=raw_conn,
            fact_conn=fact_conn,
            conn_for_alias=None if args.type_b_only else _conn_for_alias,
            type_b_only=bool(args.type_b_only),
        )
    finally:
        raw_conn.close()
        fact_conn.close()

    payload["as_of"] = datetime.now().isoformat(timespec="seconds")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    else:
        print(
            f"residual-hygiene: overall={payload['overall']} "
            f"summary={payload.get('summary')}"
        )
        for f in payload.get("findings") or []:
            if f.get("status") in ("fail", "warn"):
                print(
                    f"  {f.get('status').upper()} {f.get('check')} {f.get('domain')}: "
                    f"{f.get('detail')} lag={f.get('lag_trading_days')}"
                )

    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    if args.alert_flag:
        write_alert_flag(
            Path(args.alert_flag),
            str(payload["overall"]),
            list(payload.get("findings") or []),
        )
    return 1 if payload["overall"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
