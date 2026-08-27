#!/usr/bin/env python3
"""Read-only moneyflow layer recon: EOD vendors vs bounded TDX ticks.

Does not change primaries. Three named layers are never summed. Minute
vendor 主力净流入 has no accepted publication. Tick sample is truncated
and not identity with EOD imbalance. Sample only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.moneyflow_recon import (  # noqa: E402
    compare_eod_vendors,
    compare_tick_vs_eod,
    fetch_history_ticks,
    latest_fact_day,
    load_eod_dc,
    load_eod_tushare,
    moneyflow_publication_status,
    tick_active_delta,
)
from services.data_sources.sources.tdxhub import (  # noqa: E402
    is_hq_transport_error,
    quotes_client,
)
from services.duck_adapter import connect  # noqa: E402

DEFAULT_CODES = ("600519.SH", "000001.SZ")


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _sample_codes(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _classify_tdx(exc: BaseException) -> dict[str, Any]:
    text = str(exc)
    kind = "transport" if is_hq_transport_error(exc) else type(exc).__name__
    if "empty" in text.lower():
        kind = "empty_payload"
    return {"status": "tdx_unavailable", "class": kind, "error": text[:300]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "moneyflow_recon.json",
    )
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--day", default="")
    parser.add_argument("--skip-tdx", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=4000)
    args = parser.parse_args(argv)

    codes = _sample_codes(args.codes)
    con = connect(str(db_path("smartmoney")), read_only=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "primary_cut": False,
        "formula_winner_rate": False,
        "sample_codes": codes,
        "publication": moneyflow_publication_status(),
        "identity": False,
    }
    client = None
    try:
        asof = compact_or_latest(con, args.day)
        report["asof"] = asof
        if asof is None:
            report["samples"] = {"status": "empty_recon", "reason": "no moneyflow_dc asof"}
            _write(args.out, report)
            return 0
        tdx_error = None
        if not args.skip_tdx:
            try:
                client = quotes_client()
            except Exception as exc:  # noqa: BLE001
                tdx_error = _classify_tdx(exc)
                report["tdx"] = tdx_error
        samples = []
        for code in codes:
            dc = load_eod_dc(con, code, asof)
            ts = load_eod_tushare(con, code, asof)
            body: dict[str, Any] = {
                "ts_code": code,
                "eod_dc": dc,
                "eod_tushare": ts,
                "eod_vendors": compare_eod_vendors(
                    dc.get("net_amount"), ts.get("net_mf_amount")
                ),
                "minute": report["publication"]["minute"],
            }
            if client is not None:
                try:
                    fetched = fetch_history_ticks(
                        client, code, asof, max_ticks=args.max_ticks
                    )
                    ticks = tick_active_delta(fetched.get("ticks") or [])
                    ticks["truncated"] = fetched.get("truncated")
                    ticks["coverage"] = fetched.get("coverage")
                    ticks["n_fetched"] = fetched.get("n")
                    body["tick"] = ticks
                    body["tick_vs_eod_dc"] = compare_tick_vs_eod(
                        ticks, dc.get("net_amount")
                    )
                except Exception as exc:  # noqa: BLE001
                    body["tick"] = _classify_tdx(exc)
            elif tdx_error is not None:
                body["tick"] = tdx_error
            else:
                body["tick"] = {"status": "skipped"}
            body["identity"] = False
            samples.append(body)
        report["samples"] = samples
    finally:
        con.close()
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — rule-compliance: ok evidence=tdx-socket-close-best-effort
                pass

    _write(args.out, report)
    return 0


def compact_or_latest(con: Any, day: str) -> str | None:
    from services.data_sources.moneyflow_recon import compact_yyyymmdd

    if day.strip():
        return compact_yyyymmdd(day)
    return latest_fact_day(con)


if __name__ == "__main__":
    raise SystemExit(main())
