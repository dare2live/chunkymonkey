"""Post-data-fetch audit for ChunkyMonkey sync checkpoints."""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from services.calendar import latest_completed_trade_date

logger = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "data" / "reports" / "data_audit_latest.json"
SMART_DB_PATH = ROOT / "data" / "smartmoney.duckdb"
MARKET_DB_PATH = ROOT / "data" / "market.duckdb"
KLINE_MIN_START = "2022-01-01"

AUDIT_RULES = [
    "kline_completeness", "kline_consistency", "board_coverage",
    "date_range", "volume_sanity", "smartmoney_freshness", "cross_table_consistency",
]
SMART_MONEY_FRESHNESS_TABLES = (
    ("fact_risk_factors", "calc_date"),
    ("fact_sector_momentum_daily", "date"),
    ("fact_capital_flow_pit_daily", "trade_date"),
    ("mart_sniper_score_daily", "signal_date"),
    ("mart_institution_score_daily", "signal_date"),
    ("mart_stock_survey_activity", "as_of_date"),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _open_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(SMART_DB_PATH), read_only=True)
    conn.execute(f"ATTACH '{MARKET_DB_PATH}' AS market (READ_ONLY)")
    return conn


def _to_date(value: Any) -> Any:
    if value is None:
        return None
    s = str(value).strip()[:10]
    return datetime.fromisoformat(s).date() if len(s) == 10 else None


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = conn.execute(sql, params or []).fetchone()
    return row[0] if row else None


def _trading_index(conn: duckdb.DuckDBPyConnection) -> dict:
    return {d: i for i, (d,) in enumerate(
        conn.execute("SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1 ORDER BY trade_date").fetchall()
    )}


def _trading_lag_days(index: dict, from_date: Any, to_date: Any) -> int | None:
    if from_date is None or to_date is None:
        return None
    if from_date not in index or to_date not in index:
        return None
    return abs(index[from_date] - index[to_date])


def _check_kline_completeness(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    try:
        rows = conn.execute("""
            SELECT code, MIN(date), MAX(date), COUNT(DISTINCT date)
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
            GROUP BY code
        """).fetchall()
    except Exception as exc:
        return CheckResult("kline_completeness", "FAIL", f"query failed: {exc}")

    if not rows:
        return CheckResult("kline_completeness", "FAIL", "price_kline_tdxhub is empty")

    misses: list[str] = []
    for code, mn, mx, actual in rows:
        expected = _scalar(
            conn,
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE is_trading=1 AND trade_date BETWEEN ? AND ?",
            [str(mn)[:10], str(mx)[:10]],
        )
        expected = int(expected or 0)
        if actual < expected:
            misses.append(f"{code}: actual={actual} expected={expected}")
    if misses:
        return CheckResult("kline_completeness", "FAIL", f"{len(misses)} stock(s) miss trading days; sample: {', '.join(misses[:5])}")
    return CheckResult("kline_completeness", "PASS", "no missing trading days")


def _check_kline_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    dup = conn.execute("""
        SELECT code, date, COUNT(*)
        FROM market.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq'
        GROUP BY code, date
        HAVING COUNT(*) > 1
    """).fetchall()
    if dup:
        return CheckResult("kline_consistency", "FAIL", f"duplicate rows for {len(dup)} (stock,date) pairs")

    idx = _trading_index(conn)
    if not idx:
        return CheckResult("kline_consistency", "FAIL", "trading calendar unavailable")

    rows = conn.execute("""
        SELECT code, date
        FROM market.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq'
        ORDER BY code, date
    """).fetchall()
    if not rows:
        return CheckResult("kline_consistency", "FAIL", "price_kline_tdxhub is empty")

    prev_code: str | None = None
    prev_day_idx: int | None = None
    samples: list[str] = []
    for code, d in rows:
        di = _to_date(d)
        if di is None or di not in idx:
            samples.append(f"{code}:{d}")
            continue
        cur = idx[di]
        if prev_code == code and prev_day_idx is not None and cur - prev_day_idx - 1 > 5:
            samples.append(f"{code}: +{cur-prev_day_idx-1} missing trading days")
        prev_code, prev_day_idx = code, cur

    if samples:
        return CheckResult("kline_consistency", "FAIL", f"duplicates or gaps >5; sample: {', '.join(samples[:8])}")
    return CheckResult("kline_consistency", "PASS", "no duplicates and no >5 trading-day gaps")


def _check_board_coverage(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    rows = conn.execute("""
        SELECT DISTINCT code
        FROM market.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq' AND code IS NOT NULL
    """).fetchall()
    prefixes = {str(c[0]).zfill(6)[:2] for c in rows}
    missing = sorted({"00", "30", "60", "68"} - prefixes)
    if missing:
        return CheckResult("board_coverage", "FAIL", f"missing board prefixes: {', '.join(missing)}")
    return CheckResult("board_coverage", "PASS", "all 4 board prefixes present")


def _check_date_range(conn: duckdb.DuckDBPyConnection, calendar_svc=latest_completed_trade_date) -> CheckResult:
    mn, mx = conn.execute("""
        SELECT MIN(date), MAX(date)
        FROM market.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq'
    """).fetchone()
    mn_d, mx_d = _to_date(mn), _to_date(mx)
    if not mn_d or not mx_d:
        return CheckResult("date_range", "FAIL", "could not read min/max from kline")

    if mn_d < datetime.fromisoformat(KLINE_MIN_START).date():
        return CheckResult("date_range", "FAIL", f"min_date {mn_d} < {KLINE_MIN_START}")

    cal_d = _to_date(calendar_svc(conn))
    if not cal_d:
        return CheckResult("date_range", "FAIL", "calendar latest date unavailable")

    if abs((mx_d - cal_d).days) > 1:
        return CheckResult("date_range", "FAIL", f"max_date {mx_d} deviates from calendar latest {cal_d} by >1d")
    return CheckResult("date_range", "PASS", f"min={mn_d} max={mx_d}, calendar={cal_d}")


def _check_volume_sanity(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    neg = int(_scalar(conn, """
        SELECT COUNT(*)
        FROM market.price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq'
          AND (COALESCE(volume,0) < 0 OR COALESCE(amount,0) < 0)
    """) or 0)
    if neg:
        return CheckResult("volume_sanity", "FAIL", f"{neg} rows with negative volume/amount")

    zero_active = int(_scalar(conn, """
        SELECT COUNT(*)
        FROM market.price_kline_tdxhub p
        INNER JOIN dim_active_a_stock a ON a.stock_code=p.code
        WHERE p.freq='daily' AND p.adjust='qfq'
          AND COALESCE(p.volume,0)=0 AND COALESCE(p.amount,0)=0
    """) or 0)
    if zero_active:
        return CheckResult("volume_sanity", "FAIL", f"{zero_active} all-zero rows for active stocks")
    return CheckResult("volume_sanity", "PASS", "no negative and no active all-zero rows")


def _check_smartmoney_freshness(conn: duckdb.DuckDBPyConnection, calendar_svc=latest_completed_trade_date) -> CheckResult:
    cal = _to_date(calendar_svc(conn))
    if not cal:
        return CheckResult("smartmoney_freshness", "FAIL", "calendar latest date unavailable")
    idx = _trading_index(conn)
    if not idx:
        return CheckResult("smartmoney_freshness", "FAIL", "trading calendar unavailable")

    fails: list[str] = []
    for table, date_col in SMART_MONEY_FRESHNESS_TABLES:
        row = _scalar(conn, f"SELECT MAX({date_col}) FROM {table}")
        latest = _to_date(row)
        if not latest:
            fails.append(f"{table}: no rows")
            continue
        lag = _trading_lag_days(idx, latest, cal)
        if lag is None or lag > 3:
            fails.append(f"{table}: lag>{lag if lag is not None else '?'} (latest={latest}, calendar={cal})")
    if fails:
        return CheckResult("smartmoney_freshness", "FAIL", f"stale smartmoney tables: {'; '.join(fails)}")
    return CheckResult("smartmoney_freshness", "PASS", "all key smartmoney tables within 3 trading days")


def _check_cross_table_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    kline_codes = {c for (c,) in conn.execute("""
        SELECT DISTINCT code
        FROM market.price_kline_tdxhub
        WHERE code IS NOT NULL
    """).fetchall()}
    if not kline_codes:
        return CheckResult("cross_table_consistency", "FAIL", "kline table has no stock codes")

    all_codes = {c for (c,) in conn.execute("""
        SELECT stock_code FROM dim_active_a_stock
        UNION
        SELECT stock_code FROM dim_all_ever_listed
    """).fetchall() if c is not None}
    extras = sorted(c for c in kline_codes if c not in all_codes)

    # 检查 dim_all_ever_listed 误标退市 (K线近期有交易但 is_active=0)
    recent_codes = {c for (c,) in conn.execute("""
        SELECT DISTINCT code FROM market.price_kline_tdxhub
        WHERE freq='daily' AND date >= CURRENT_DATE - INTERVAL '10 days'
    """).fetchall()}
    wrongly_inactive = {c for (c,) in conn.execute(
        "SELECT stock_code FROM dim_all_ever_listed WHERE is_active=0"
    ).fetchall()} & recent_codes

    issues = []
    if extras:
        issues.append(f"{len(extras)} kline codes not in universe tables")
    if wrongly_inactive:
        issues.append(f"{len(wrongly_inactive)} stocks marked inactive but still trading (dim_all_ever_listed.is_active=0 误标)")

    if issues:
        return CheckResult("cross_table_consistency", "FAIL", "; ".join(issues) + f"; sample: {sorted(list(wrongly_inactive)[:5])}")
    return CheckResult("cross_table_consistency", "PASS", "kline codes consistent with universe tables, no wrongly-inactive stocks")


def _overall_status(checks: list[CheckResult]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    return "PASS"


def _is_strict(strict: bool) -> bool:
    return bool(strict and os.getenv("AUDIT_STRICT", "1") not in {"0", "false", "False", "FALSE"})


def run_post_sync_audit(step_name: str, strict: bool = True) -> dict[str, Any]:
    with _open_conn() as conn:
        checks = [
            _check_kline_completeness(conn),
            _check_kline_consistency(conn),
            _check_board_coverage(conn),
            _check_date_range(conn),
            _check_volume_sanity(conn),
            _check_smartmoney_freshness(conn),
            _check_cross_table_consistency(conn),
        ]

    result = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "checks": [asdict(c) for c in checks],
        "overall": _overall_status(checks),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if result["overall"] == "FAIL":
        msg = f"post-data-sync audit FAILED at step={step_name}: " + \
              "; ".join(f"{c['name']}={c['status']}" for c in result["checks"] if c["status"] == "FAIL")
        if _is_strict(strict):
            raise RuntimeError(msg)
        logger.warning(msg)
    logger.info("data_audit step=%s overall=%s report=%s", step_name, result["overall"], REPORT_PATH)
    return result


# Backward-compatible exports used by legacy routes.
def audit_all(_conn: Any = None) -> list[dict[str, Any]]:
    out = run_post_sync_audit("legacy", strict=False)["checks"]
    checks = []
    for r in out:
        checks.append({
            "table": r["name"],
            "issues": [{"level": "warn" if r["status"] == "WARN" else "error", "msg": r["detail"]}]
            if r["status"] != "PASS" else [],
            "status": r["status"],
        })
    return checks


def save_audit_report(_conn: Any, results: list[dict[str, Any]]) -> int:
    # keep prior behavior/shape for callers expecting an integer id
    return int(datetime.now().timestamp() * 1000)


def load_last_audit_report(_conn: Any = None) -> dict[str, Any] | None:
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def summary() -> dict[str, Any]:
    return {"n_checks": 7, "report_path": str(REPORT_PATH)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True)
    parser.add_argument("--strict", action="store_true", default=True)
    args = parser.parse_args()
    run_post_sync_audit(args.step, strict=args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
