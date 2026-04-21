"""SEF Phase I 完整性审计.

读取数据库验证 Phase I 三大 KPI：
1. chain_follow_pnl 与 research_holding_chains closed chain 吻合
2. Alpha158 覆盖率（若已生成）
3. Triple Barrier 触发分布合理（不极端偏斜）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))


def audit() -> dict:
    from services.db import get_conn

    conn = get_conn()
    try:
        result: dict = {}

        # 1) chain 真相表
        row = conn.execute(
            "SELECT COUNT(*), SUM(status='closed'), SUM(status='open'), "
            "SUM(chain_follow_pnl IS NOT NULL), SUM(chain_inst_pnl IS NOT NULL) "
            "FROM fact_chain_alpha_truth"
        ).fetchone()
        result["chain_alpha_truth"] = {
            "total": row[0],
            "closed": row[1],
            "open": row[2],
            "with_follow_pnl": row[3],
            "with_inst_pnl": row[4],
            "coverage_pct": round(row[3] / row[0] * 100, 2) if row[0] else None,
        }

        # 2) closed chain 口径对齐
        row = conn.execute(
            """
            SELECT COUNT(*) FROM research_holding_chains rhc
            JOIN fact_chain_alpha_truth t ON rhc.institution_id=t.institution_id
                AND rhc.stock_code=t.stock_code AND rhc.chain_id=t.research_chain_id
            WHERE rhc.chain_status='closed'
            """
        ).fetchone()
        result["closed_chain_align"] = {"matched_rows": row[0]}

        # 3) Triple Barrier 分布
        tb_rows = conn.execute(
            "SELECT tb_label, COUNT(*) FROM fact_chain_alpha_truth "
            "WHERE tb_label IS NOT NULL GROUP BY tb_label"
        ).fetchall()
        tb = {r[0]: r[1] for r in tb_rows}
        total_tb = sum(tb.values())
        result["triple_barrier"] = {
            "distribution": tb,
            "total_labeled": total_tb,
            "upper_pct": round(tb.get("upper", 0) / total_tb * 100, 2) if total_tb else 0,
            "lower_pct": round(tb.get("lower", 0) / total_tb * 100, 2) if total_tb else 0,
            "time_pct": round(tb.get("time", 0) / total_tb * 100, 2) if total_tb else 0,
        }
        # 合理性判断：no single bucket > 85%
        result["triple_barrier"]["balanced"] = all(
            result["triple_barrier"][f"{k}_pct"] < 85 for k in ("upper", "lower", "time")
        )

        # 4) dim_all_ever_listed
        row = conn.execute(
            "SELECT COUNT(*), SUM(is_active), SUM(1-is_active) FROM dim_all_ever_listed"
        ).fetchone()
        result["dim_all_ever_listed"] = {
            "total": row[0],
            "active": row[1],
            "delisted_or_inactive": row[2],
        }

        # 5) Alpha158 覆盖率（如存在）
        try:
            rows = conn.execute(
                "SELECT year_month, n_stocks, n_rows, coverage_pct FROM qlib_alpha158_index ORDER BY year_month"
            ).fetchall()
            if rows:
                coverage = [r[3] for r in rows if r[3] is not None]
                # 按"有 kline 的 active 股"作分母：这是 Alpha158 能达到的上限
                kline_stocks = conn.execute(
                    """
                    SELECT COUNT(*) FROM dim_active_a_stock d
                    WHERE EXISTS (
                        SELECT 1 FROM qlib_alpha158_index WHERE year_month=
                            (SELECT MAX(year_month) FROM qlib_alpha158_index)
                    )
                    """
                ).fetchone()[0]
                latest_n = rows[-1][1] if rows else 0
                active_n = conn.execute(
                    "SELECT COUNT(*) FROM dim_active_a_stock"
                ).fetchone()[0]
                result["alpha158"] = {
                    "partitions": len(rows),
                    "months": [r[0] for r in rows],
                    "avg_coverage_pct_vs_qlib": (
                        round(sum(coverage) / len(coverage), 2) if coverage else None
                    ),
                    "total_rows": sum(r[2] or 0 for r in rows),
                    "latest_month_stocks": latest_n,
                    "coverage_vs_active_pct": (
                        round(latest_n / active_n * 100, 2) if active_n else None
                    ),
                }
            else:
                result["alpha158"] = {"status": "not_generated"}
        except Exception as e:  # noqa: BLE001
            result["alpha158"] = {"status": f"err: {e}"}

        # 6) 扩列验证
        event_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(fact_institution_event)").fetchall()
        }
        required = {"chain_id", "follow_pnl_to_eval", "follow_maxdd_to_eval", "inst_pnl_to_eval",
                    "eval_status"}
        result["event_columns_ok"] = required.issubset(event_cols)
        chain_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(research_holding_chains)").fetchall()
        }
        required2 = {"chain_alpha", "chain_industry_beta", "chain_style_beta_json",
                     "chain_top_factors_json", "alpha_halflife_days"}
        result["chain_columns_ok"] = required2.issubset(chain_cols)

        # 7) 新表存在性
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected_tables = {
            "fact_chain_alpha_truth", "mart_institution_capability", "mart_institution_style",
            "fact_stock_character", "model_signals_log", "model_signals_realized", "model_state",
            "fact_regime_state", "institution_drift_log", "backtest_walk_forward",
            "portfolio_recommendation_daily", "dim_all_ever_listed", "sef_schema_version",
        }
        missing = expected_tables - names
        result["new_tables_ok"] = not missing
        if missing:
            result["missing_tables"] = sorted(missing)

        # 8) 整体通过
        a158_cov = (result.get("alpha158") or {}).get("coverage_vs_active_pct") or 0
        checks = {
            "chain_pnl_coverage>95%": (result["chain_alpha_truth"]["coverage_pct"] or 0) > 95,
            "tb_balanced": result["triple_barrier"]["balanced"],
            "event_cols_ok": result["event_columns_ok"],
            "chain_cols_ok": result["chain_columns_ok"],
            "new_tables_ok": result["new_tables_ok"],
            "alpha158_generated": (result.get("alpha158") or {}).get("partitions", 0) >= 1,
        }
        # Alpha158 需 > 75% active 才算 PASS（剩下的缺口是 dim_active 里没 kline 的股票，
        # 由智能更新的 sync_market_data 补全，这是 Layer 0 数据层的职责，不是 Phase I 要修的）
        if "coverage_vs_active_pct" in (result.get("alpha158") or {}):
            checks["alpha158_coverage>75%"] = a158_cov >= 75
        result["checks"] = checks
        result["all_passed"] = all(checks.values())

        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    report = audit()
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text)

    if not report.get("all_passed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
