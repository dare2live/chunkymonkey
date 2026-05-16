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
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("audit_live_dashboard")


LIVE_PORTFOLIOS = ["live_A_v4", "live_B_v8", "live_C_adaptive"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None,
                        help="Date YYYY-MM-DD; default today")
    args = parser.parse_args()
    as_of = args.as_of or date.today().isoformat()

    conn = get_conn()
    print(f"\n=== Live Forward Sim Dashboard {as_of} ===\n")

    for pid in LIVE_PORTFOLIOS:
        # 当前 NAV
        r = conn.execute(
            """SELECT total_value, cash, positions_value, n_positions, hs300_nav, date
               FROM mart_paper_sim_nav
               WHERE sim_run_id = ? AND date <= ?
               ORDER BY date DESC LIMIT 1""",
            [pid, as_of],
        ).fetchone()
        if not r:
            print(f"  {pid}: (no data yet)")
            continue
        nav, cash, pos_val, n_pos, hs300, last_date = r
        # 起始 NAV
        r_start = conn.execute(
            """SELECT total_value, date FROM mart_paper_sim_nav
               WHERE sim_run_id = ? ORDER BY date ASC LIMIT 1""",
            [pid],
        ).fetchone()
        start_nav, start_date = r_start if r_start else (1_000_000, "?")
        cum_ret = (nav / start_nav - 1) if start_nav > 0 else 0
        # peak NAV → current dd
        peak = conn.execute(
            """SELECT MAX(total_value) FROM mart_paper_sim_nav
               WHERE sim_run_id = ? AND date <= ?""",
            [pid, as_of],
        ).fetchone()[0] or nav
        dd = (nav / peak - 1) if peak > 0 else 0
        # 30-day rolling return
        r30 = conn.execute(
            """SELECT total_value FROM mart_paper_sim_nav
               WHERE sim_run_id = ? AND date <= ?
               ORDER BY date DESC LIMIT 30""",
            [pid, as_of],
        ).fetchall()
        nav_30d_ago = r30[-1][0] if len(r30) >= 30 else None
        ret_30d = (nav / nav_30d_ago - 1) if nav_30d_ago else None
        # Hard stop count
        n_hard = conn.execute(
            """SELECT COUNT(*) FROM fact_paper_sim_trade
               WHERE sim_run_id = ? AND reason LIKE 'hard_stop%'""",
            [pid],
        ).fetchone()[0]

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
               excess_total_return, sharpe, calmar, annual_turnover
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
