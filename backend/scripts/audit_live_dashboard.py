"""Phase 4+ live dashboard — 每日 live paper_sim KPI 3-way 对比.

跑在 run_paper_sim_live_daily.py 之后, 输出 stdout / 写 mart_paper_sim_kpi.

3 组实盘 portfolio:
- A_v4 (保守 -20%)
- B_v8 (激进 -22%)
- C_adaptive (placeholder)

Dashboard 内容:
- 各 portfolio 当前 NAV / cumulative return / max_dd-to-date
- 7-day rolling / 30-day rolling return
- 持仓数 / cash %
- 最近 hard_stop fire (if any)
- vs HS300 benchmark excess

用法:
    PYTHONPATH=backend python backend/scripts/audit_live_dashboard.py
    PYTHONPATH=backend python backend/scripts/audit_live_dashboard.py --as-of 2026-05-16
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn
from services.utils import latest_completed_trade_date


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("audit_live_dashboard")


LIVE_PORTFOLIOS = ["live_A_v4", "live_B_v8", "live_C_adaptive"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None,
                        help="Date YYYY-MM-DD; default latest completed trade date")
    args = parser.parse_args()

    conn = get_conn()
    as_of = args.as_of or latest_completed_trade_date(conn)
    if not as_of:
        log.error("latest_completed_trade_date returned None — kline 数据缺失? 拒启动")
        return 2
    print(f"\n=== Live Forward Sim Dashboard {as_of} ===\n")

    placeholders = ", ".join(["?"] * len(LIVE_PORTFOLIOS))
    latest_rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT
            sim_run_id,
            total_value,
            cash,
            positions_value,
            n_positions,
            hs300_nav,
            date,
            ROW_NUMBER() OVER (PARTITION BY sim_run_id ORDER BY date DESC) AS rn
          FROM mart_paper_sim_nav
          WHERE sim_run_id IN ({placeholders}) AND date <= ?
        )
        SELECT sim_run_id, total_value, cash, positions_value, n_positions, hs300_nav, date
        FROM ranked
        WHERE rn = 1
        """,
        [*LIVE_PORTFOLIOS, as_of],
    ).fetchall()
    latest_by_pid = {row[0]: tuple(row)[1:] for row in latest_rows}
    start_rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT
            sim_run_id,
            total_value,
            date,
            ROW_NUMBER() OVER (PARTITION BY sim_run_id ORDER BY date ASC) AS rn
          FROM mart_paper_sim_nav
          WHERE sim_run_id IN ({placeholders})
        )
        SELECT sim_run_id, total_value, date
        FROM ranked
        WHERE rn = 1
        """,
        LIVE_PORTFOLIOS,
    ).fetchall()
    start_by_pid = {row[0]: tuple(row)[1:] for row in start_rows}
    peak_by_pid = {
        sim_run_id: peak
        for sim_run_id, peak in conn.execute(
            f"""
            SELECT sim_run_id, MAX(total_value) AS peak
            FROM mart_paper_sim_nav
            WHERE sim_run_id IN ({placeholders}) AND date <= ?
            GROUP BY sim_run_id
            """,
            [*LIVE_PORTFOLIOS, as_of],
        ).fetchall()
    }
    r30_by_pid = {
        sim_run_id: values
        for sim_run_id, values in conn.execute(
            f"""
            WITH ranked AS (
              SELECT
                sim_run_id,
                total_value,
                ROW_NUMBER() OVER (PARTITION BY sim_run_id ORDER BY date DESC) AS rn
              FROM mart_paper_sim_nav
              WHERE sim_run_id IN ({placeholders}) AND date <= ?
            )
            SELECT sim_run_id, LIST(total_value ORDER BY rn) AS values
            FROM ranked
            WHERE rn <= 30
            GROUP BY sim_run_id
            """,
            [*LIVE_PORTFOLIOS, as_of],
        ).fetchall()
    }
    hard_stop_by_pid = {
        sim_run_id: int(n_hard)
        for sim_run_id, n_hard in conn.execute(
            f"""
            SELECT sim_run_id, COUNT(*) AS n_hard
            FROM fact_paper_sim_trade
            WHERE sim_run_id IN ({placeholders}) AND reason LIKE 'hard_stop%'
            GROUP BY sim_run_id
            """,
            LIVE_PORTFOLIOS,
        ).fetchall()
    }
    for pid in LIVE_PORTFOLIOS:
        # 当前 NAV
        r = latest_by_pid.get(pid)
        if not r:
            print(f"  {pid}: (no data yet)")
            continue
        nav, cash, pos_val, n_pos, hs300, last_date = r
        # 起始 NAV
        r_start = start_by_pid.get(pid)
        start_nav, start_date = r_start if r_start else (1_000_000, "?")
        cum_ret = (nav / start_nav - 1) if start_nav > 0 else 0
        # peak NAV → current dd
        peak = peak_by_pid.get(pid) or nav
        dd = (nav / peak - 1) if peak > 0 else 0
        # 30-day rolling return
        r30 = r30_by_pid.get(pid, [])
        nav_30d_ago = r30[-1] if len(r30) >= 30 else None
        ret_30d = (nav / nav_30d_ago - 1) if nav_30d_ago else None
        # Hard stop count
        n_hard = hard_stop_by_pid.get(pid, 0)

        cash_pct = (cash / nav * 100) if nav > 0 else 0
        print(f"  {pid} (last {last_date}):")
        print(f"    NAV          {nav:>12,.0f} (start {start_date} {start_nav:,.0f}, cumret {cum_ret*100:+.2f}%)")
        print(f"    peak / dd    {peak:>12,.0f} / {dd*100:+.2f}%")
        print(f"    cash / pos   {cash_pct:>10.1f}% / {n_pos} pos")
        if ret_30d is not None:
            print(f"    30d return   {ret_30d*100:+.2f}%")
        print(f"    hard_stop fires: {n_hard}")
        print()

    # 3-way KPI 对比表 (from mart_paper_sim_kpi if exists)
    print("--- KPI 横向 (from mart_paper_sim_kpi, if KPI write triggered) ---")
    kpi_rows = conn.execute("""
        SELECT sim_run_id, annual_return, max_dd, monthly_win_rate,
               excess_vs_hs300, sharpe, calmar, annual_turnover
        FROM mart_paper_sim_kpi
        WHERE sim_run_id IN ('live_A_v4', 'live_B_v8', 'live_C_adaptive')
        ORDER BY sim_run_id
    """).fetchall()
    if not kpi_rows:
        print("  (no KPI rows yet — KPI 由 run_paper_sim_v2.py 完整跑完才写; daily live 仅 NAV)")
    else:
        header = ("portfolio", "ann_ret", "max_dd", "win%", "excess", "sharpe", "calmar", "turnover")
        print(f"  {header[0]:<16} {header[1]:>8} {header[2]:>8} {header[3]:>6} {header[4]:>8} {header[5]:>6} {header[6]:>6} {header[7]:>8}")
        for r in kpi_rows:
            print(f"  {r[0]:<16} {(r[1] or 0)*100:>+7.1f}% {(r[2] or 0)*100:>+7.1f}% "
                  f"{(r[3] or 0)*100:>5.0f}% {(r[4] or 0)*100:>+7.1f}% {r[5] or 0:>+6.2f} "
                  f"{r[6] or 0:>+6.2f} {r[7] or 0:>7.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
