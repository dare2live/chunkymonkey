"""Paper Sim v2 — walk-forward CLI.

用法:
  # 单 variant (swap_v1) 历史回放
  PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \\
      --variant swap_v1 --start 2023-01-03 --end 2026-05-12

  # ablation 三个 variant 都跑 + 对比 (KPI 决策树阻断式)
  PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py --ablation \\
      --start 2023-01-03 --end 2026-05-12

输出:
  - mart_paper_sim_nav (每日)
  - fact_paper_sim_position (每笔开/平)
  - fact_paper_sim_trade (每笔 BUY/SELL/SWAP)
  - mart_paper_sim_kpi (每 variant 一行 KPI summary)
  - stdout 打 KPI 表格 + 决策树阻断结果
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn
from services.market_db import get_market_conn
from services.paper_sim.config import load_config, PaperSimConfig
from services.paper_sim.ddl import ensure_paper_sim_tables
from services.paper_sim.driver import run_paper_sim_day
from services.paper_sim.reporter import write_kpi_summary
from services.utils import latest_closed_or_raise


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("run_paper_sim_v2")


def _trading_days(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """SELECT trade_date FROM dim_trading_calendar
            WHERE is_trading=1 AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date""",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def run_walk_forward(
    variant: str,
    start: str,
    end: str,
    cfg: PaperSimConfig,
    *,
    sim_run_id: str | None = None,
) -> dict:
    """跑一次完整 walk-forward. variant 仅用作 KPI 标签 + config 微调入口."""
    if sim_run_id is None:
        sim_run_id = f"{variant}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    log.info(f"=== walk-forward {variant} ({start} → {end}, run_id={sim_run_id}) ===")
    t0 = time.time()
    conn = get_conn()
    mkt = get_market_conn()
    try:
        ensure_paper_sim_tables(conn)
        # 先清掉同 run_id 残留 (重跑场景)
        for tbl in ("mart_paper_sim_nav", "fact_paper_sim_position",
                    "fact_paper_sim_trade", "mart_paper_sim_kpi"):
            conn.execute(f"DELETE FROM {tbl} WHERE sim_run_id = ?", [sim_run_id])

        days = _trading_days(conn, start, end)
        log.info(f"  交易日: {len(days):,} ({days[0]} → {days[-1]})")

        cash = cfg.portfolio.initial_cash
        for i, d in enumerate(days):
            res = run_paper_sim_day(
                conn, mkt,
                sim_run_id=sim_run_id, today=d, cfg=cfg,
                starting_cash=cash if i == 0 else None,
            )
            cash = None   # 后续天 driver 自己从 nav 推
            if (i + 1) % 50 == 0:
                log.info(f"  day {i+1}/{len(days)} ({d}): "
                         f"nav={res['total_value']:,.0f} cash={res['cash']:,.0f} "
                         f"pos={res['n_positions']} exits={res['n_exits']} "
                         f"swaps={res['n_swaps']} buys={res['n_buys']}")

        log.info(f"  walk-forward 完成 ({time.time() - t0:.0f}s), 算 KPI ...")
        summary = write_kpi_summary(conn, sim_run_id, variant, cfg)
        return summary
    finally:
        conn.close()
        mkt.close()


def _print_kpi(summary: dict, label: str) -> None:
    print(f"\n{'='*120}")
    print(f"  {label} — sim_run_id={summary.get('variant')}")
    print(f"{'='*120}")
    uc = summary["user_criteria"]
    print(f"\n  A. 用户终极标准 ({'✅ PASS' if uc['pass'] else '❌ FAIL'}):")
    print(f"     年化     {uc['annual_return']*100:+.1f}%  (≥ 30% 必过)")
    print(f"     max_dd   {uc['max_dd']*100:+.1f}%  (≥ -20% 必过)")
    print(f"     超额     {uc.get('excess_total_return', 0)*100:+.1f}%  (> 0 必过)")
    print(f"     月胜率   {uc['monthly_win_rate']*100:.0f}%  (≥ 55% 必过)")
    print(f"     Sharpe   {uc['sharpe']:+.2f}  Calmar {uc['calmar']:+.2f}  IR {uc.get('information_ratio',0):+.2f}")

    ac = summary["anti_churn"]
    print(f"\n  B. Anti-churn ({'✅ PASS' if ac['pass'] else '❌ FAIL'}):")
    print(f"     平均持仓天数  {ac['avg_holding_days']:.1f}  (≥ 5 必过)")
    print(f"     年化换手     {ac['annual_turnover']:.2f}x  (≤ 8 必过)")
    print(f"     手续费占毛利  {ac['tx_cost_pct_of_gross_pnl']*100:.1f}%  (≤ 10% 必过)")
    print(f"     swap 次数    {ac['swap_count']}")
    print(f"     swap 净 uplift {ac['swap_uplift_total']:+.3f}  (> 0 必过 — 反事实证明 swap 有价值)")

    rb = summary["robustness"]
    print(f"\n  C. Robustness ({'✅ PASS' if rb['pass'] else '❌ FAIL'}):")
    ir_med = rb.get("rolling_ir_60d_median")
    ir_p25 = rb.get("rolling_ir_60d_p25")
    ann_med = rb.get("rolling_annual_90d_median")
    print(f"     60d IR 中位数 {ir_med:.2f}" if ir_med is not None else "     60d IR 中位数 N/A")
    print(f"     60d IR 25 分位 {ir_p25:.2f}" if ir_p25 is not None else "     60d IR 25 分位 N/A")
    print(f"     90d 年化 中位数 {ann_med*100:+.1f}%" if ann_med is not None else "     90d 年化 中位数 N/A")
    bull = rb.get("regime_bull_return")
    bear = rb.get("regime_bear_return")
    side = rb.get("regime_sideways_return")
    print(f"     牛市段 {bull*100:+.1f}%" if bull is not None else "     牛市段 N/A")
    print(f"     熊市段 {bear*100:+.1f}%" if bear is not None else "     熊市段 N/A")
    print(f"     震荡段 {side*100:+.1f}%" if side is not None else "     震荡段 N/A")

    print(f"\n  >>> 综合 KPI: {'✅✅✅ ALL PASS — 可上 live' if summary['all_pass'] else '❌ 至少一类 FAIL — 不上线'}")


def _ablation_compare(baseline: dict, swap_v1: dict, cfg: PaperSimConfig) -> None:
    """D. Ablation: swap_v1 vs baseline 必须显著好."""
    print(f"\n{'='*120}\n  D. Ablation — swap_v1 vs baseline\n{'='*120}")
    uplift = swap_v1["user_criteria"]["annual_return"] - baseline["user_criteria"]["annual_return"]
    dd_degrade = swap_v1["user_criteria"]["max_dd"] - baseline["user_criteria"]["max_dd"]
    sharpe_ratio = (swap_v1["user_criteria"]["sharpe"]
                    / baseline["user_criteria"]["sharpe"]
                    if baseline["user_criteria"]["sharpe"] > 0 else 0)
    th = cfg.validation.ablation

    print(f"     年化提升 {uplift*100:+.1f}pp  (≥ {th['swap_vs_baseline_annual_uplift_min']*100:.1f}pp 必过)")
    print(f"     max_dd 变化 {dd_degrade*100:+.1f}pp  (≤ {th['swap_vs_baseline_max_dd_degradation_max']*100:.1f}pp 必过)")
    print(f"     Sharpe 比 {sharpe_ratio:.2f}x  (≥ {th['swap_vs_baseline_sharpe_ratio_min']:.2f}x 必过)")
    ablation_pass = (
        uplift >= th["swap_vs_baseline_annual_uplift_min"]
        and dd_degrade <= th["swap_vs_baseline_max_dd_degradation_max"]
        and sharpe_ratio >= th["swap_vs_baseline_sharpe_ratio_min"]
    )
    print(f"     >>> {'✅ swap 显著贡献 — 保留' if ablation_pass else '❌ swap 价值不显著 — 考虑关 swap 上线 baseline 简化版'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="swap_v1",
                        choices=["swap_v1", "baseline", "swap_optuna"],
                        help="单跑哪个 variant; --ablation 时忽略")
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--ablation", action="store_true",
                        help="跑 baseline + swap_v1 两个 variant 并对比")
    parser.add_argument("--config-path", default=None,
                        help="自定义 yaml 路径 (Phase ψ.α: paper_sim_momentum.yaml / "
                             "paper_sim_reversal.yaml / paper_sim_reversal_deep_only.yaml 等). "
                             "默认 backend/config/paper_sim_config.yaml.")
    args = parser.parse_args()

    if args.end is None:
        args.end = latest_closed_or_raise()
        log.info(f"--end 默认 (calendar-gated): {args.end}")

    from pathlib import Path
    cfg_path = Path(args.config_path) if args.config_path else None
    if cfg_path:
        log.info(f"--config-path: {cfg_path}")

    if args.ablation:
        # baseline: swap.enabled = False
        cfg_baseline = load_config(path=cfg_path, override={"swap": {"enabled": False}})
        # swap_v1: 默认 config
        cfg_swap = load_config(path=cfg_path)

        baseline = run_walk_forward("baseline", args.start, args.end, cfg_baseline)
        swap_v1 = run_walk_forward("swap_v1", args.start, args.end, cfg_swap)

        _print_kpi(baseline, "BASELINE (no swap)")
        _print_kpi(swap_v1, "SWAP_V1 (用户审过规则)")
        _ablation_compare(baseline, swap_v1, cfg_swap)
    else:
        if args.variant == "baseline":
            cfg = load_config(path=cfg_path, override={"swap": {"enabled": False}})
        else:
            cfg = load_config(path=cfg_path)
        summary = run_walk_forward(args.variant, args.start, args.end, cfg)
        _print_kpi(summary, args.variant.upper())


if __name__ == "__main__":
    main()
