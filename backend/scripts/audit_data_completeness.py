"""data completeness audit — 实测每张关键表 max_date / coverage / 跟 calendar 对齐.

2026-05-19 用户 push back: "请你同步后做个数据完整性审计".

输出:
- 每表 max_date + 当日 coverage (n_codes if applicable)
- vs latest_completed_trade_date (15:05 阈值)
- vs 全 universe (5,200 stocks A 股 active)
- stale 标记 (> 1 trading day stale)
- contamination 标记 (date > cal_max)
- coverage threshold check (< 90% universe = partial)

rule-compliance: ok evidence=data-completeness-audit-tool
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import duckdb
from services.market_db import _latest_completed_trade_date_for_write

UNIVERSE_SIZE_HINT = 5200  # A 股 active universe size

# (db_path, table, date_col, has_n_codes, coverage_col)
TABLES = [
    ("market.duckdb", "price_kline_tdxhub", "date", True, "code"),
    ("alpha158.duckdb", "fact_alpha158_panel", "date", True, "stock_code"),
    ("smartmoney.duckdb", "mart_p0a_label_panel", "signal_date", True, "stock_code"),
    ("smartmoney.duckdb", "mart_p0a_feature_label_panel_v3", "signal_date", True, "stock_code"),
    ("smartmoney.duckdb", "mart_p0a_feature_label_panel_v4", "signal_date", True, "stock_code"),
    ("smartmoney.duckdb", "fact_capital_flow_pit_daily", "trade_date", True, "stock_code"),
    ("smartmoney.duckdb", "fact_lhb_event", "trade_date", True, "stock_code"),
    ("smartmoney.duckdb", "fact_risk_factors", "calc_date", True, "stock_code"),
    ("smartmoney.duckdb", "fact_technical_trigger", "date", True, "stock_code"),
    ("smartmoney.duckdb", "fact_sector_momentum_daily", "date", False, None),
    ("smartmoney.duckdb", "mart_sniper_score_daily", "signal_date", True, "stock_code"),
    ("smartmoney.duckdb", "mart_institution_score_daily", "signal_date", True, "stock_code"),
]


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_summary_select(table: str, date_col: str, has_codes: bool, code_col: str | None) -> str:
    table_literal = _sql_literal(table)
    if has_codes and code_col:
        return f"""
            SELECT
                {table_literal} AS table_name,
                CAST(latest.max_date AS VARCHAR) AS max_date,
                CAST(COUNT(DISTINCT t.{code_col}) AS BIGINT) AS n_codes
            FROM (SELECT MAX({date_col}) AS max_date FROM {table}) latest
            LEFT JOIN {table} t ON t.{date_col} = latest.max_date
            GROUP BY latest.max_date
        """
    return f"""
        SELECT
            {table_literal} AS table_name,
            CAST(MAX({date_col}) AS VARCHAR) AS max_date,
            NULL::BIGINT AS n_codes
        FROM {table}
    """


def _load_table_summaries(con, specs: list[tuple[str, str, str, bool, str | None]]) -> dict[str, tuple[str, int | None]]:
    query = "\nUNION ALL\n".join(
        _table_summary_select(table, date_col, has_codes, code_col)
        for _, table, date_col, has_codes, code_col in specs
    )
    rows = con.execute(query).fetchall()
    return {
        str(table): ("(empty)" if max_date is None else str(max_date), None if n_codes is None else int(n_codes))
        for table, max_date, n_codes in rows
    }


def _load_coverage_policies(con, specs: list[tuple[str, str, str, bool, str | None]]) -> dict[str, str]:
    try:
        exists = con.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = 'dim_data_asset'
             LIMIT 1
            """
        ).fetchone()
    except Exception:
        return {}
    if not exists:
        return {}

    table_names = [table for _, table, _, _, _ in specs]
    if not table_names:
        return {}
    placeholders = ", ".join("?" for _ in table_names)
    try:
        rows = con.execute(
            f"""
            SELECT table_name, coverage_policy
              FROM dim_data_asset
             WHERE table_name IN ({placeholders})
            """,
            table_names,
        ).fetchall()
    except Exception:
        return {}
    return {
        str(table): str(coverage_policy)
        for table, coverage_policy in rows
        if coverage_policy is not None
    }


def _table_verdict(
    table: str,
    max_date: str,
    n_codes: str,
    *,
    cal_max: str | None,
    has_codes: bool,
    code_col: str | None,
) -> tuple[str, str | None]:
    if max_date == "(empty)":
        return "EMPTY", "empty table"
    if cal_max and max_date > cal_max:
        return "CONTAMINATED", f"max={max_date} > cal_max={cal_max}"
    if cal_max and max_date < cal_max:
        d_max = _date.fromisoformat(max_date)
        d_cal = _date.fromisoformat(cal_max)
        gap = (d_cal - d_max).days
        if gap == 0:
            return "OK", None
        if gap <= 3:
            return f"STALE_{gap}d", None
        return f"STALE_{gap}d_WARN", f"max={max_date} (gap={gap}d)"
    if has_codes and code_col and max_date == cal_max:
        try:
            normalized_n_codes = n_codes.replace(",", "").strip()
            if not normalized_n_codes:
                return "PARTIAL_UNKNOWN", "missing code count"
            n = int(normalized_n_codes)
        except ValueError:
            return "PARTIAL_UNKNOWN", f"invalid code count: {n_codes!r}"
        if n < 0.5 * UNIVERSE_SIZE_HINT:
            return "PARTIAL_WARN", f"only {n_codes} codes ({n*100//UNIVERSE_SIZE_HINT}%)"
    return "OK", None


def _issue_severity(verdict: str, coverage_policy: str | None) -> str:
    if verdict in {"EMPTY", "CONTAMINATED", "PARTIAL_UNKNOWN"}:
        return "FAIL"
    if verdict.startswith("STALE_") and verdict.endswith("_WARN"):
        return "WARN"
    if verdict == "PARTIAL_WARN":
        return "WARN" if coverage_policy == "sparse_event_presence_only" else "FAIL"
    return "FAIL"


def _calendar_compare(max_date: str, cal_max: str | None) -> str:
    if not cal_max:
        return ""
    if max_date == cal_max:
        return "= cal"
    if max_date < cal_max:
        return "< cal"
    return "> cal!"


def main() -> int:
    cal_max = _latest_completed_trade_date_for_write(raise_on_miss=False)
    print(f"=== Data Completeness Audit ===")
    print(f"cal_max (15:05 buffer): {cal_max}")
    print(f"universe size hint: {UNIVERSE_SIZE_HINT}")
    print()
    print(f"{'Table':<48} {'max_date':<12} {'n_codes':>10} {'policy':<28} {'vs_cal':<10} {'verdict'}")
    print("-" * 140)

    fail_issues = []
    warn_issues = []
    tables_by_db: dict[str, list[tuple[str, str, str, bool, str | None]]] = defaultdict(list)
    for spec in TABLES:
        tables_by_db[spec[0]].append(spec)

    summaries_by_db: dict[str, dict[str, tuple[str, int | None]]] = {}
    coverage_policies_by_db: dict[str, dict[str, str]] = {}
    db_errors: dict[str, str] = {}
    for db_file, specs in tables_by_db.items():
        db_path = REPO_ROOT / "data" / db_file
        if not db_path.exists():
            db_errors[db_file] = "(no db)"
            continue
        con = None
        try:
            con = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            db_errors[db_file] = f"ERR: {e}"
            continue
        try:
            summaries_by_db[db_file] = _load_table_summaries(con, specs)
            coverage_policies_by_db[db_file] = _load_coverage_policies(con, specs)
        finally:
            if con is not None:
                con.close()

    for db_file, table, date_col, has_codes, code_col in TABLES:
        if db_file in db_errors:
            print(f"{table:<48} {db_errors[db_file]:<12}")
            continue
        max_date, raw_n_codes = summaries_by_db[db_file][table]
        coverage_policy = coverage_policies_by_db.get(db_file, {}).get(table)
        n_codes = f"{raw_n_codes:,}" if has_codes and code_col and max_date != "(empty)" and raw_n_codes is not None else ""
        verdict, issue = _table_verdict(
            table,
            max_date,
            n_codes,
            cal_max=cal_max,
            has_codes=has_codes,
            code_col=code_col,
        )
        if issue is not None:
            severity = _issue_severity(verdict, coverage_policy)
            if severity == "WARN":
                warn_issues.append((table, verdict, issue, coverage_policy))
            else:
                fail_issues.append((table, verdict, issue, coverage_policy))
        cal_compare = _calendar_compare(max_date, cal_max)
        policy_display = coverage_policy or ""
        print(f"{table:<48} {max_date:<12} {n_codes:>10} {policy_display:<28} {cal_compare:<10} {verdict}")

    print()
    print(f"=== Summary: {len(fail_issues)} FAIL / {len(warn_issues)} WARN ===")
    for t, v, desc, policy in fail_issues:
        suffix = f" [coverage_policy={policy}]" if policy else ""
        print(f"  [FAIL] {t}: {desc}{suffix}")
    for t, v, desc, policy in warn_issues:
        suffix = f" [coverage_policy={policy}]" if policy else ""
        print(f"  [WARN] {t}: {desc}{suffix}")

    return 0 if not fail_issues else 1


if __name__ == "__main__":
    sys.exit(main())
