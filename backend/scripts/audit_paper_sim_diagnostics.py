"""Paper Sim KPI 诊断 — Codex aa2d79d2 CRITICAL #2 + MAJOR #3 落地.

A. PIT coverage daily audit (Codex CRITICAL #2):
   每个 signal_date 对应 ASOF cutoff_date 后 distinct stock 数 — 是否有"突变"或长期 < 100?
   显示 PIT 累积曲线 + 与 ML predictions 的重合率.

B. Turnover breakdown (Codex MAJOR #3):
   每 day n_buys / n_exits / n_swaps 序列 + 平均持仓天数分布 + sell_reason 拆分.
   验证 turnover 是 candidate sparse churn (n_exits=0,n_buys 频繁) 还是 ranking 波动 (n_swaps 高).

C. Candidate ranking stability:
   连续 day top 5 stock 重叠率 (Jaccard) + score 排名 Spearman 相关.

用法:
    PYTHONPATH=backend python backend/scripts/audit_paper_sim_diagnostics.py \\
        --sim-run-id <latest_swap_v1_*>

输出: stdout 表格 (不入库, read-only audit).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn


def audit_pit_coverage(conn, start_date: str, end_date: str) -> None:
    """Section A: PIT 表 daily coverage."""
    print("\n" + "=" * 80)
    print("A. PIT coverage daily audit (Codex CRITICAL #2)")
    print("=" * 80)

    sql = """
    WITH dates AS (
        SELECT trade_date FROM dim_trading_calendar
        WHERE is_trading=1 AND trade_date >= ? AND trade_date <= ?
    ),
    pit_asof AS (
        SELECT d.trade_date AS signal_date,
               p.stock_code,
               MAX(p.cutoff_date) AS asof_cutoff
        FROM dates d
        LEFT JOIN mart_per_stock_stage_strategy_optimal_pit p
            ON CAST(p.cutoff_date AS DATE) <= CAST(d.trade_date AS DATE)
            AND p.n_traded >= 5
        GROUP BY d.trade_date, p.stock_code
    )
    SELECT signal_date,
           COUNT(DISTINCT stock_code) AS pit_universe_size,
           MIN(asof_cutoff) AS earliest_cutoff_used,
           MAX(asof_cutoff) AS latest_cutoff_used
    FROM pit_asof
    WHERE stock_code IS NOT NULL
    GROUP BY signal_date
    ORDER BY signal_date
    """
    rows = conn.execute(sql, [start_date, end_date]).fetchall()

    if not rows:
        print("  (无数据)")
        return

    print(f"  {'date':<12} {'pit_universe':>14} {'asof_cutoff':>14}")
    # 每月初取 1 行 (太多日子)
    last_month = None
    for r in rows:
        sd, size, _e_co, latest_co = r
        month = sd[:7]
        if month != last_month:
            print(f"  {sd:<12} {size:>14} {str(latest_co):>14}")
            last_month = month
    # 最后一行也打
    if rows[-1][0][:7] == last_month:
        sd, size, _e_co, latest_co = rows[-1]
        print(f"  {sd:<12} {size:>14} {str(latest_co):>14}  (last)")


def audit_turnover(conn, sim_run_id: str) -> None:
    """Section B: turnover breakdown."""
    print("\n" + "=" * 80)
    print(f"B. Turnover breakdown (sim_run_id={sim_run_id})")
    print("=" * 80)

    # 总览
    sql = """
    SELECT type, reason,
           COUNT(*) AS n_trades,
           MIN(date) AS first_date, MAX(date) AS last_date
    FROM fact_paper_sim_trade
    WHERE sim_run_id = ?
    GROUP BY 1, 2
    ORDER BY n_trades DESC
    """
    rows = conn.execute(sql, [sim_run_id]).fetchall()
    if not rows:
        print(f"  (sim_run_id 无 trade 记录)")
        return

    print(f"  {'type':<10} {'reason':<35} {'n_trades':>10} {'first':<12} {'last':<12}")
    for r in rows[:20]:
        type_, reason, n, fd, ld = r
        rsn = (reason or "")[:33]
        print(f"  {type_:<10} {rsn:<35} {n:>10} {fd:<12} {ld:<12}")

    # 持仓天数分布
    print()
    print("  持仓天数分布:")
    sql = """
    SELECT
        CASE
            WHEN days_held = 0 THEN '0d (同日 close, 异常)'
            WHEN days_held <= 1 THEN '1d (T+1 强出, 异常)'
            WHEN days_held <= 5 THEN '2-5d (短线)'
            WHEN days_held <= 10 THEN '6-10d'
            WHEN days_held <= 20 THEN '11-20d'
            ELSE '20d+'
        END AS bucket,
        COUNT(*) AS n_positions
    FROM fact_paper_sim_position
    WHERE sim_run_id = ? AND close_date IS NOT NULL
    GROUP BY 1
    ORDER BY MIN(days_held)
    """
    for r in conn.execute(sql, [sim_run_id]).fetchall():
        print(f"    {r[0]:<22} {r[1]:>6}")


def audit_candidate_stability(conn, sim_run_id: str) -> None:
    """Section C: candidate top 5 重叠率 (近似 — paper_sim 不存候选 history, 用持仓代替)."""
    print("\n" + "=" * 80)
    print(f"C. Candidate stability (Holdings rolling overlap)")
    print("=" * 80)

    # paper_sim 不存 daily candidate list, 用 持仓 (fact_paper_sim_position) 计算
    # 算 D 和 D-1 持仓的 Jaccard
    sql = """
    WITH daily_holdings AS (
        SELECT d.trade_date,
               LIST(DISTINCT p.stock_code ORDER BY p.stock_code) AS held
        FROM dim_trading_calendar d
        LEFT JOIN fact_paper_sim_position p
            ON p.sim_run_id = ?
            AND p.open_date <= d.trade_date
            AND (p.close_date IS NULL OR p.close_date > d.trade_date)
        WHERE d.is_trading = 1
          AND d.trade_date BETWEEN (SELECT MIN(open_date) FROM fact_paper_sim_position WHERE sim_run_id = ?)
                              AND (SELECT MAX(open_date) FROM fact_paper_sim_position WHERE sim_run_id = ?)
        GROUP BY d.trade_date
        ORDER BY d.trade_date
    ),
    paired AS (
        SELECT trade_date, held,
               LAG(held) OVER (ORDER BY trade_date) AS prev_held
        FROM daily_holdings
    )
    SELECT
        AVG(LENGTH(LIST_INTERSECT(held, prev_held))::DOUBLE
             / NULLIF(LENGTH(LIST_DISTINCT(LIST_CONCAT(held, prev_held))), 0)) AS avg_jaccard,
        COUNT(*) AS n_pairs
    FROM paired
    WHERE prev_held IS NOT NULL AND LENGTH(prev_held) > 0 AND LENGTH(held) > 0
    """
    r = conn.execute(sql, [sim_run_id, sim_run_id, sim_run_id]).fetchone()
    if r and r[1]:
        print(f"  持仓 day-over-day Jaccard overlap (近似 candidate stability):")
        print(f"    {r[0]:.3f} (1.0 = 完全相同, 0.0 = 完全不同, target ≥ 0.6)")
        print(f"    pairs analyzed: {r[1]}")
        if r[0] < 0.5:
            print(f"  WARN: Jaccard < 0.5 → 持仓波动大, candidate sparse churn 嫌疑")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim-run-id", required=False,
                        help="paper_sim run_id; 缺省取最近 swap_v1 run")
    parser.add_argument("--start", default="2025-07-01")   # rule-compliance: ok evidence=Codex-C-D PIT-充足起点
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=lgbm_v3_honest_20d 预测末点
    args = parser.parse_args()

    conn = get_conn()

    if not args.sim_run_id:
        r = conn.execute(
            "SELECT sim_run_id FROM mart_paper_sim_nav "
            "WHERE sim_run_id LIKE 'swap_v1_%' "
            "GROUP BY sim_run_id ORDER BY MAX(date) DESC LIMIT 1"
        ).fetchone()
        if not r:
            print("ERROR: 没 swap_v1 sim_run_id; --sim-run-id 必须传")
            return 1
        args.sim_run_id = r[0]
        print(f"INFO: 用最近 sim_run_id = {args.sim_run_id}")

    audit_pit_coverage(conn, args.start, args.end)
    audit_turnover(conn, args.sim_run_id)
    audit_candidate_stability(conn, args.sim_run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
