"""Phase 4+ live forward simulation — 每日 cron 触发 3 组并行 paper_sim.

Codex a49c90a6 verdict: skip Phase 2-3, launch v4 live multi-portfolio.

3 组 portfolio:
- A 保守: v4 (cash 0.30 + max_dd_hard_stop -20% + freeze 14 + max_pos 5)
- B 激进: v8 (cash 0.30 + max_dd_hard_stop -22% + freeze 14 + max_pos 5)
- C 自适应: Phase 1 后开发 (regime gate 熊段缩 60%) — defer 到有 feature research 进展

输出:
- 各 portfolio 独立 sim_run_id (live_A_2026-05-16 / live_B_... / live_C_...)
- mart_paper_sim_nav 每日 NAV / mart_paper_sim_kpi 每日 KPI 更新
- stdout 3-way 横向对比 (audit_sim_run_ledger 输出 format)

PIT-safe (Codex Rule 5 + 7):
- 每日 09:25 决策时只用 D-1 EOD 数据 + 当日 09:25 之前 K线 (T 当日 VWAP entry)
- ml_score_loader / hybrid 用 mart_per_stock_stage_strategy_optimal_pit ASOF
- pre_close LAG / amount_ma20 strict prior

用法:
    # Daily cron (假设盘后 17:00):
    PYTHONPATH=backend python backend/scripts/run_paper_sim_live_daily.py \\
        --today $(date +%Y-%m-%d)

    # Bootstrap 历史 NAV (live 启动前 catch up):
    PYTHONPATH=backend python backend/scripts/run_paper_sim_live_daily.py \\
        --catchup --start 2025-07-01 --end 2026-04-23

Acceptance:
- 3 sim_run_id 每日 nav row update 0 缺失
- 各 portfolio KPI 跟 backtest baseline 对照 (v4 +66.6% / v8 +106.4% historical)
- live forward 期间持续 monitor max_dd 触发
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb
import yaml

from services.db import get_conn
from services.market_db import get_market_conn
from services.utils import latest_completed_trade_date
from services.paper_sim.config import load_config, PaperSimConfig
from services.paper_sim.ddl import ensure_paper_sim_tables
from services.paper_sim.driver import run_paper_sim_day_multi


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("run_paper_sim_live_daily")


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def load_portfolio_configs() -> dict[str, tuple[str, PaperSimConfig]]:
    """3 组 portfolio 配置 (A v4 / B v8 / C defer).

    Returns:
        {portfolio_id: (sim_run_id_prefix, PaperSimConfig)}.
        sim_run_id 由 daily caller append today date.
    """
    # A 保守 = v4 (paper_sim_ml_score.yaml 当前是 v4 final, max_dd -0.20)
    cfg_a = load_config(CONFIG_DIR / "paper_sim_ml_score.yaml")
    # B 激进 = v8 (override max_dd -0.22 / freeze 14, 其它同 v4)
    cfg_b_raw_path = CONFIG_DIR / "paper_sim_ml_score.yaml"
    with open(cfg_b_raw_path, "r", encoding="utf-8") as f:
        b_yaml = yaml.safe_load(f)
    cfg_b = load_config(cfg_b_raw_path, override={
        "risk": {**b_yaml["risk"], "max_dd_hard_stop_pct": -0.22},
    })
    # C 自适应 — defer 到 Phase 2+ feature research 完
    # 当前 same as v4 (placeholder, 后续接入 regime_gate logic)
    cfg_c = cfg_a

    return {
        "A_v4": ("live_A_v4", cfg_a),     # 保守
        "B_v8": ("live_B_v8", cfg_b),     # 激进
        "C_adaptive": ("live_C_adaptive", cfg_c),  # 自适应 placeholder
    }


def trading_days_between(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """SELECT trade_date FROM dim_trading_calendar
            WHERE is_trading=1 AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date""",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def run_one_day(today: str) -> dict[str, dict]:
    """跑 3 组 portfolio 同一天."""
    log.info(f"=== Live daily paper_sim {today} ===")
    conn = get_conn()
    mkt = get_market_conn()
    try:
        ensure_paper_sim_tables(conn)
        portfolios_def = load_portfolio_configs()

        # 转 sim_run_id (固定 prefix, 不带 today — 多日 row 共享 sim_run_id)
        portfolios = {
            pid: (prefix, cfg)
            for pid, (prefix, cfg) in portfolios_def.items()
        }
        results = run_paper_sim_day_multi(
            conn, mkt,
            today=today,
            portfolios=portfolios,
            starting_cash_map=None,   # 第一天 driver 自取 initial_cash
        )
        log.info(f"  Done: {len(results)} portfolios")
        for pid, res in results.items():
            log.info(f"    {pid}: nav={res.get('total_value', 0):,.0f} "
                     f"pos={res.get('n_positions', 0)} "
                     f"exits={res.get('n_exits', 0)} buys={res.get('n_buys', 0)}")
        return results
    finally:
        conn.close()
        mkt.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=None,
                        help="YYYY-MM-DD; default 今天")
    parser.add_argument("--catchup", action="store_true",
                        help="Bootstrap: 跑 --start ~ --end 全 catchup")
    parser.add_argument("--start", default="2025-07-01")   # rule-compliance: ok evidence=v4-baseline-window
    parser.add_argument("--end", default=None)              # rule-compliance: ok evidence=cron-dynamic
    args = parser.parse_args()

    if args.catchup:
        conn = get_conn()
        try:
            end = args.end or latest_completed_trade_date(conn)
            if not end:
                log.error("latest_completed_trade_date returned None — kline 数据缺失? 拒启动")
                return 2
            days = trading_days_between(conn, args.start, end)
            log.info(f"Catchup {len(days)} trading days: {args.start} → {end}")
        finally:
            conn.close()
        for d in days:
            run_one_day(d)
    else:
        if args.today:
            today = args.today
        else:
            conn = get_conn()
            try:
                today = latest_completed_trade_date(conn)
            finally:
                conn.close()
            if not today:
                log.error("latest_completed_trade_date returned None — kline 数据缺失? 拒启动")
                return 2
        run_one_day(today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
