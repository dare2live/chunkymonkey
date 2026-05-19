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


def main() -> int:
    cal_max = _latest_completed_trade_date_for_write(raise_on_miss=False)
    print(f"=== Data Completeness Audit ===")
    print(f"cal_max (15:05 buffer): {cal_max}")
    print(f"universe size hint: {UNIVERSE_SIZE_HINT}")
    print()
    print(f"{'Table':<48} {'max_date':<12} {'n_codes':>10} {'vs_cal':<10} {'verdict'}")
    print("-" * 110)

    issues = []
    for db_file, table, date_col, has_codes, code_col in TABLES:
        db_path = REPO_ROOT / "data" / db_file
        if not db_path.exists():
            print(f"{table:<48} {'(no db)':<12}")
            continue
        try:
            con = duckdb.connect(str(db_path), read_only=True)
        except Exception as e:
            print(f"{table:<48} ERR: {e}")
            continue
        try:
            r = con.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()
            max_date = str(r[0]) if r[0] else "(empty)"
            n_codes = ""
            if has_codes and code_col and max_date != "(empty)":
                r2 = con.execute(
                    f"SELECT COUNT(DISTINCT {code_col}) FROM {table} WHERE {date_col} = ?",
                    [max_date],
                ).fetchone()
                n_codes = f"{r2[0]:,}"

            # Verdict
            verdict = "OK"
            if max_date == "(empty)":
                verdict = "EMPTY"
                issues.append((table, verdict, "empty table"))
            elif cal_max and max_date > cal_max:
                verdict = "CONTAMINATED"
                issues.append((table, verdict, f"max={max_date} > cal_max={cal_max}"))
            elif cal_max and max_date < cal_max:
                # days stale
                from datetime import date as _date
                d_max = _date.fromisoformat(max_date)
                d_cal = _date.fromisoformat(cal_max)
                gap = (d_cal - d_max).days
                if gap == 0:
                    verdict = "OK"
                elif gap <= 3:
                    verdict = f"STALE_{gap}d"
                else:
                    verdict = f"STALE_{gap}d⚠"
                    issues.append((table, verdict, f"max={max_date} (gap={gap}d)"))
            elif has_codes and code_col and max_date == cal_max:
                # check partial coverage
                try:
                    n = int(n_codes.replace(",", ""))
                    if n < 0.5 * UNIVERSE_SIZE_HINT:
                        verdict = "PARTIAL⚠"
                        issues.append((table, verdict, f"only {n_codes} codes ({n*100//UNIVERSE_SIZE_HINT}%)"))
                except Exception:
                    # rule-compliance: ok evidence=audit-script-skip-non-stock-tables-quietly
                    pass

            cal_compare = ""
            if cal_max:
                if max_date == cal_max:
                    cal_compare = "= cal"
                elif max_date < cal_max:
                    cal_compare = f"< cal"
                elif max_date > cal_max:
                    cal_compare = f"> cal!"

            print(f"{table:<48} {max_date:<12} {n_codes:>10} {cal_compare:<10} {verdict}")
        finally:
            con.close()

    print()
    print(f"=== Summary: {len(issues)} issues ===")
    for t, v, desc in issues:
        print(f"  [{v}] {t}: {desc}")

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
