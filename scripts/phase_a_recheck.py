"""Phase A1 — 在新 56K 数据上复核 V6 edge 与各档对照。

跑 4 个配置：
  baseline       — 关掉 D1/D3/D5/D8 + premium 也放宽到老阈值（V3）
  V5a            — 仅 premium≤15、blacklist、hold_ratio
  V6             — 加 D1+D3+D5
  V6+D8          — V6 基础上启用 D8 min_survey=1

每个跑：
  - 全量 backtest_historical
  - cohort_recent_matured(lookback_days=180)

输出对比表。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dataclasses import replace
from services.db import get_conn
from services.signals_v2 import (
    PolicyConfig,
    backtest_historical,
    cohort_recent_matured,
    load_config,
)


def main():
    conn = get_conn()
    base = load_config(conn)
    print(f"[当前 DB 配置] {base}\n")

    cfgs = {
        "V3 baseline (无硬规则)": replace(
            base,
            max_premium_pct=99999,
            min_hold_ratio=0,
            inst_type_blacklist="",
            max_holder_yoy_pct=99999,
            min_forecast_profit_yoy=-9999,
            max_unlock_ratio_180d=99999,
            min_survey_count_90d=0,
        ),
        "V5a (premium≤15+黑名单+持仓)": replace(
            base,
            max_holder_yoy_pct=99999,
            min_forecast_profit_yoy=-9999,
            max_unlock_ratio_180d=99999,
            min_survey_count_90d=0,
        ),
        "V6 (V5a + D1+D3+D5)": replace(base, min_survey_count_90d=0),
        "V6 + D8(survey>=1)": replace(base, min_survey_count_90d=1),
    }

    print("=" * 100)
    print("【Phase A1】全量 backtest_historical 复核")
    print("=" * 100)
    print(f"{'配置':<30} {'follow_n':>8} {'F_EV':>8} {'F_WR':>7} {'blind_n':>8} {'B_EV':>8} {'edge':>8}")
    print("-" * 100)
    bt_results = {}
    for name, cfg in cfgs.items():
        r = backtest_historical(conn, config=cfg)
        bt_results[name] = r
        f = r.get("follow_policy", {}) or {}
        b = r.get("blind_buy", {}) or {}
        cov = r.get("coverage", {}) or {}
        edge = (f.get("ev_pct") or 0) - (b.get("ev_pct") or 0)
        print(
            f"{name:<30} "
            f"{f.get('n', 0):>8} "
            f"{(f.get('ev_pct') or 0):>+7.2f}% "
            f"{(f.get('win_rate') or 0)*100:>6.1f}% "
            f"{b.get('n', 0):>8} "
            f"{(b.get('ev_pct') or 0):>+7.2f}% "
            f"{edge:>+7.2f}pp"
        )
        # 触发分布
        cov_total = cov.get("total_events") or 0
        if cov_total:
            f_pct = (cov.get("follow", 0) / cov_total) * 100
            print(f"  (follow 占总事件 {f_pct:.1f}%, total={cov_total})")
    print()

    print("=" * 100)
    print("【Phase A1】cohort_recent_matured 复核（最近 180d 已成熟样本）")
    print("=" * 100)
    print(f"{'配置':<30} {'cohort':>7} {'follow_n':>8} {'F_EV':>8} {'F_WR':>7} {'blind_EV':>8} {'edge':>8}")
    print("-" * 100)
    for name, cfg in cfgs.items():
        r = cohort_recent_matured(conn, config=cfg)
        cohort_size = r.get("cohort_size", 0)
        if not cohort_size:
            print(f"{name:<30} (无成熟样本) note={r.get('note', '')}")
            continue
        bb = r.get("by_bucket", {}) or {}
        f = bb.get("follow", {}) or {}
        b = bb.get("blind", {}) or {}
        edge_block = r.get("edge_vs_blind", {}) or {}
        edge_f = (edge_block.get("follow") or {}).get("ev_diff_pct", 0)
        print(
            f"{name:<30} "
            f"{cohort_size:>7} "
            f"{f.get('n', 0):>8} "
            f"{(f.get('ev_pct') or 0):>+7.2f}% "
            f"{(f.get('win_rate') or 0)*100:>6.1f}% "
            f"{(b.get('ev_pct') or 0):>+7.2f}% "
            f"{edge_f:>+7.2f}pp"
        )
    print()

    print("=" * 100)
    print("【Phase A1】季度趋势：V6 vs Blind（全量）")
    print("=" * 100)
    v6 = bt_results["V6 (V5a + D1+D3+D5)"]
    trend = v6.get("quarterly_trend", [])
    print(f"{'季度':<10} {'F_n':>5} {'F_EV':>8} {'F_WR':>7} {'B_n':>6} {'B_EV':>8} {'B_WR':>7} {'EV差':>8}")
    print("-" * 70)
    for q in trend[-12:]:
        f_ev = q.get("follow_ev_pct")
        b_ev = q.get("blind_ev_pct")
        diff = (f_ev or 0) - (b_ev or 0) if (f_ev is not None and b_ev is not None) else 0
        print(
            f"{q.get('quarter', ''):<10} "
            f"{q.get('follow_n', 0):>5} "
            f"{(f_ev if f_ev is not None else 0):>+7.2f}% "
            f"{(q.get('follow_win_rate') or 0)*100:>6.1f}% "
            f"{q.get('blind_n', 0):>6} "
            f"{(b_ev if b_ev is not None else 0):>+7.2f}% "
            f"{(q.get('blind_win_rate') or 0)*100:>6.1f}% "
            f"{diff:>+7.2f}pp"
        )

    conn.close()


if __name__ == "__main__":
    main()
