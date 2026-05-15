"""Paper Sim — 实验 ledger 横向对比 (用户 2026-05-15: 保留多种组合, 优选/重组).

跑所有 swap_v1_20260515_* sim_run_id (本 session 实验), 对比关键 KPI 一表.

用法:
    PYTHONPATH=backend python backend/scripts/audit_sim_run_ledger.py

输出: stdout 表格 — 年化 / max_dd / 超额 / 月胜率 / Sharpe / 换手 / Path 标签.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn


def main() -> int:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            sim_run_id, variant,
            annual_return, max_dd, monthly_win_rate,
            excess_total_return, sharpe, calmar,
            avg_holding_days, annual_turnover, tx_cost_pct_of_gross_pnl,
            swap_count, swap_uplift_total,
            (SELECT MIN(date) FROM mart_paper_sim_nav n WHERE n.sim_run_id = k.sim_run_id) AS first_d,
            (SELECT MAX(date) FROM mart_paper_sim_nav n WHERE n.sim_run_id = k.sim_run_id) AS last_d,
            (SELECT COUNT(*) FROM mart_paper_sim_nav n WHERE n.sim_run_id = k.sim_run_id) AS n_days
        FROM mart_paper_sim_kpi k
        WHERE sim_run_id LIKE 'swap_v1_20260515_%'
        ORDER BY first_d, sim_run_id
        """
    ).fetchall()

    if not rows:
        print("(无 swap_v1_20260515_* sim_run)")
        return 0

    print(f"\n本 session 实验 ledger ({len(rows)} runs):\n")
    # Header
    h = ("run_id"[:30], "ann_ret", "max_dd", "win%", "excess", "shrp", "trn",
         "持仓", "n_d", "window")
    print(f"  {h[0]:<30} {h[1]:>8} {h[2]:>8} {h[3]:>6} {h[4]:>8} {h[5]:>6} {h[6]:>6} {h[7]:>6} {h[8]:>5} {h[9]:<22}")
    print("  " + "-" * 130)
    for r in rows:
        rid = r[0][-12:]                       # 最后 12 char (timestamp_uuid)
        ann = f"{(r[2] or 0)*100:+.1f}%"
        dd = f"{(r[3] or 0)*100:+.1f}%"
        wr = f"{(r[4] or 0)*100:.0f}%"
        ex = f"{(r[5] or 0)*100:+.1f}%"
        sh = f"{r[6] or 0:+.2f}"
        trn = f"{r[9] or 0:.0f}x"
        hld = f"{r[8] or 0:.1f}"
        nd = f"{r[15] or 0}"
        win = f"{r[13]}~{r[14]}"
        print(f"  ...{rid:<27} {ann:>8} {dd:>8} {wr:>6} {ex:>8} {sh:>6} {trn:>6} {hld:>6} {nd:>5} {win:<22}")

    # Best alpha (年化) + Best dd 横向比较
    if len(rows) > 1:
        best_ann = max(rows, key=lambda r: r[2] or 0)
        best_dd = max(rows, key=lambda r: r[3] or -999)   # 最不深 dd
        print()
        print(f"  Best 年化:   ...{best_ann[0][-12:]} = {(best_ann[2] or 0)*100:+.1f}%")
        print(f"  Best max_dd: ...{best_dd[0][-12:]} = {(best_dd[3] or 0)*100:+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
