#!/usr/bin/env python3
"""Read-only Fuyao/TDX calendar+ST recon vs accepted tables.

Does not change primaries. Suspend has no accepted SSOT — recorded blocked.
Fuyao calendar window is trailing ~1 year of open days.
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
from services.data_sources.calendar_identity_recon import (  # noqa: E402
    ACCEPTED_CAL_TABLE,
    ACCEPTED_ST_TABLE,
    compare_open_days,
    compare_st_names,
    fuyao_calendar_days,
    fuyao_ticker_rows,
    load_accepted_open_days,
    load_accepted_st_codes,
    suspend_recon_status,
    tdx_stock_rows,
)
from services.data_sources.sources.fuyao import (  # noqa: E402
    FuyaoRestError,
    resolve_api_key,
    rest_json,
)
from services.duck_adapter import connect  # noqa: E402


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _paginate_tickers(api_key: str, *, timeout: float) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 1000
    while True:
        data = rest_json(
            "/api/meta/tickers/list",
            api_key=api_key,
            params={
                "exchange": "SH,SZ,BJ",
                "asset_type": "a-share",
                "limit": limit,
                "offset": offset,
            },
            timeout=timeout,
        )
        page = list((data or {}).get("item") or [])
        items.extend(page)
        if len(page) < limit:
            break
        offset += limit
        if offset > 20_000:
            break
    return items


def _tdx_listings() -> dict[str, Any]:
    from services.data_sources.sources.tdxhub import quotes_client

    out: dict[str, Any] = {"markets": {}, "bj": "listing_api_unsupported"}
    client = quotes_client()
    try:
        for market, label in ((0, "SZ"), (1, "SH")):
            count = int(client.client.get_security_count(market=market) or 0)
            rows: list[dict[str, Any]] = []
            for start in range(0, count, 1000):
                chunk = client.client.get_security_list(market=market, start=start) or []
                if isinstance(chunk, dict):
                    chunk = [chunk]
                rows.extend(tdx_stock_rows(list(chunk), market=market))
            out["markets"][label] = {"count": count, "n": len(rows), "rows": rows}
    finally:
        try:
            client.close()
        except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
            pass
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "calendar_identity_recon.json",
    )
    parser.add_argument("--skip-fuyao", action="store_true")
    parser.add_argument("--skip-tdx", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    raw_path = db_path("tushare_raw")
    con = connect(str(raw_path), read_only=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # rule-compliance: ok evidence=audit metadata, not trade_date
        "accepted_db": str(raw_path),
        "calendar_table": ACCEPTED_CAL_TABLE,
        "st_table": ACCEPTED_ST_TABLE,
        "suspend": suspend_recon_status(),
        "primary_cut": False,
    }
    try:
        accepted_open = load_accepted_open_days(con)
        report["accepted_open_n"] = len(accepted_open)
        st_asof = con.execute(
            f"SELECT max(CAST(trade_date AS DATE)) FROM {ACCEPTED_ST_TABLE}"
        ).fetchone()
        asof = st_asof[0] if st_asof and st_asof[0] else None
        report["st_asof"] = str(asof) if asof else None
        accepted_st = load_accepted_st_codes(con, asof) if asof else []
        report["accepted_st_n"] = len(accepted_st)
    finally:
        con.close()

    if not args.skip_fuyao:
        api_key = resolve_api_key()
        if not api_key:
            report["fuyao"] = {"status": "auth_missing"}
        else:
            fuyao: dict[str, Any] = {}
            try:
                cal = rest_json(
                    "/api/a-share/calendar/trading-days",
                    api_key=api_key,
                    timeout=args.timeout,
                )
                days = fuyao_calendar_days(list((cal or {}).get("item") or []))
                fuyao["calendar"] = compare_open_days(days, accepted_open)
                fuyao["calendar"]["source_n_raw"] = len(days)
            except FuyaoRestError as exc:
                fuyao["calendar"] = {
                    "status": "error",
                    "http": exc.http,
                    "code": exc.code,
                    "message": str(exc)[:240],
                }
            try:
                tickers = _paginate_tickers(api_key, timeout=args.timeout)
                rows = fuyao_ticker_rows(tickers)
                fuyao["tickers_n"] = len(rows)
                fuyao["st_names"] = compare_st_names(rows, accepted_st)
            except FuyaoRestError as exc:
                fuyao["st_names"] = {
                    "status": "error",
                    "http": exc.http,
                    "code": exc.code,
                    "message": str(exc)[:240],
                }
            report["fuyao"] = fuyao

    if not args.skip_tdx:
        try:
            listing = _tdx_listings()
            rows: list[dict[str, str]] = []
            for payload in listing["markets"].values():
                rows.extend(payload.pop("rows"))
            report["tdx"] = {
                "bj": listing["bj"],
                "markets": {k: {"count": v["count"], "n": v["n"]} for k, v in listing["markets"].items()},
                "st_names": compare_st_names(rows, accepted_st),
            }
        except Exception as exc:  # noqa: BLE001 — live HQ is optional for the unit knife
            report["tdx"] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"[:240]}

    _write(args.out, report)
    cal = (report.get("fuyao") or {}).get("calendar") or {}
    print(
        "calendar overlap "
        f"intersection={cal.get('intersection')} "
        f"only_source={cal.get('only_source')} "
        f"only_accepted={cal.get('only_accepted')} "
        f"window={cal.get('window')}"
    )
    print(f"suspend={report['suspend']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
