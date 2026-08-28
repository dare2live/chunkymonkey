"""Read-only TDX unadjusted daily K vs accepted nominal K.

Does not change kline_daily.primary. qfq/hfq are rejected.
Default window matches the Fuyao 10d dump dates. Universe = accepted
ts_code in that window (includes BJ; listing APIs that refuse BJ are skipped).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_access.resolver import db_path  # noqa: E402
from services.data_sources.fuyao_kline_recon import ACCEPTED_K_TABLE, write_report  # noqa: E402
from services.data_sources.sources.tdxhub import (  # noqa: E402
    is_hq_transport_error,
    quotes_client,
)
from services.data_sources.tdxhub_kline_recon import (  # noqa: E402
    compare_tdx_kline,
    fetch_unadjusted_bars,
    load_tdx_kline,
)
from services.duck_adapter import connect  # noqa: E402

DEFAULT_START = date(2026, 8, 14)
DEFAULT_END = date(2026, 8, 27)


def _accepted_codes(con, start: date, end: date) -> list[str]:
    rows = con.execute(
        f"""
        SELECT DISTINCT ts_code
        FROM tr.{ACCEPTED_K_TABLE}
        WHERE trade_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
        ORDER BY 1
        """
    ).fetchall()
    return [r[0] for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument(
        "--offset",
        type=int,
        default=800,
        help="get_security_bars page size (protocol count, cap 800); not a calendar window",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 = all accepted codes in window")
    parser.add_argument("--sleep-ms", type=int, default=20)
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "data" / "scratch" / "tdx_dumps" / "unadj_window.parquet",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "audit" / "historical" / "tdxhub_kline_recon.json",
    )
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep bars already in --cache and skip those ts_code values",
    )
    parser.add_argument("--checkpoint-every", type=int, default=200)
    args = parser.parse_args(argv)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    raw_path = Path(db_path("tushare_raw"))
    con = connect(":memory:")
    escaped = str(raw_path).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS tr (READ_ONLY)")

    artifacts: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "kline_daily_primary_untouched": True,
        "adjust": "none",
    }
    rows: list[tuple] = []
    fetch_fail: list[dict[str, str]] = []
    bj_ok = 0
    bj_fail = 0

    def _cache_sql(path: Path) -> str:
        return str(path).replace("'", "''")

    def _write_cache(payload: list[tuple]) -> None:
        load_tdx_kline(con, payload)
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY tdx_k TO '{_cache_sql(args.cache)}' (FORMAT PARQUET)")

    if args.skip_download and args.cache.is_file():
        con.execute(f"CREATE TABLE tdx_k AS SELECT * FROM read_parquet('{_cache_sql(args.cache)}')")
        artifacts["k_parquet"] = str(args.cache)
        artifacts["fetch"] = "skipped_local_parquet"
    else:
        done: set[str] = set()
        if args.resume and args.cache.is_file():
            cached = con.execute(
                f"""
                SELECT ts_code, trade_date, open, high, low, close,
                       volume_share, turnover_cny
                FROM read_parquet('{_cache_sql(args.cache)}')
                """
            ).fetchall()
            rows.extend(cached)
            done = {r[0] for r in cached}
            artifacts["resumed_rows"] = len(cached)
            artifacts["resumed_codes"] = len(done)
        codes = [c for c in _accepted_codes(con, start, end) if c not in done]
        if args.limit and args.limit > 0:
            codes = codes[: args.limit]
        artifacts["requested"] = len(codes)
        print(f"fetch unadjusted bars for {len(codes)} codes {start}..{end}")
        client = quotes_client()
        artifacts["hq_server"] = list(getattr(client, "server", ()) or ())
        artifacts["daily_bar_category"] = getattr(client, "_cm_daily_category", None)
        print(f"hq {artifacts['hq_server']} cat={artifacts['daily_bar_category']}")
        try:
            for i, ts_code in enumerate(codes, 1):
                try:
                    batch = fetch_unadjusted_bars(
                        client, ts_code, start=start, end=end, offset=args.offset
                    )
                    rows.extend(batch)
                    if ts_code.endswith(".BJ"):
                        if batch:
                            bj_ok += 1
                        else:
                            bj_fail += 1
                except Exception as exc:  # noqa: BLE001 — per-code, keep going
                    fetch_fail.append({"ts_code": ts_code, "error": str(exc)[:200]})
                    if ts_code.endswith(".BJ"):
                        bj_fail += 1
                    if is_hq_transport_error(exc):
                        try:
                            try:
                                client.close()
                            except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
                                pass
                            client = quotes_client()
                            artifacts["hq_server"] = list(getattr(client, "server", ()) or ())
                            artifacts["daily_bar_category"] = getattr(
                                client, "_cm_daily_category", None
                            )
                        except Exception:  # rule-compliance: ok evidence=tdx-reconnect-best-effort
                            pass
                if i % 50 == 0:
                    print(f"  {i}/{len(codes)} rows={len(rows)} fail={len(fetch_fail)}")
                if args.checkpoint_every and i % args.checkpoint_every == 0:
                    _write_cache(rows)
                    print(f"  checkpoint {args.cache} rows={len(rows)}")
                if args.sleep_ms:
                    time.sleep(args.sleep_ms / 1000.0)
        finally:
            try:
                client.close()
            except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
                pass
        _write_cache(rows)
        artifacts["k_parquet"] = str(args.cache)
        artifacts["fetched_rows"] = len(rows)
        artifacts["fetch_fail"] = len(fetch_fail)
        artifacts["bj_bars_nonempty"] = bj_ok
        artifacts["bj_bars_empty_or_fail"] = bj_fail
        artifacts["fetch_fail_samples"] = fetch_fail[:20]

    report = compare_tdx_kline(con, accepted_table=f"tr.{ACCEPTED_K_TABLE}")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "accepted_k_table": ACCEPTED_K_TABLE,
            "source": "tdxhub_unadjusted_protocol_or_vipdoc",
            "banned_adjust": ["qfq", "hfq"],
            "kline_daily_primary_untouched": True,
            "bj_listing_api": "unsupported_in_tdxhub; bars use ts_code suffix market=2",
        },
        "kline": report,
        "artifacts": artifacts,
    }
    write_report(args.out, payload)
    print(
        "tdx window={window} intersection={intersection} "
        "only_source={only_source} only_accepted={only_accepted} "
        "ohlc_mismatch={ohlc_mismatch} vol_h={vol_scale_hypothesis} "
        "amt_h={amount_scale_hypothesis}".format(**report)
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
