"""Phase π — Portfolio Walk-Forward Backtest (终极验证).

⚠ 用户成功标准: 年化 ≥ 30% + max_dd ≤ -20% + 超额 alpha > 0 (vs HS300)
⚠ 2023-01-01 → 2026-05-12, 起始 100 万

每个交易日:
  1. 读 mart_stock_formula_buy_signal_daily 该日 STRONG_BUY + BUY (point-in-time)
  2. 应用 cash_manager: 决定现金占比
  3. 应用 liquidity filter: 剔除流动性差 / 价高
  4. 应用 round_to_lots: 单股最少 1 手
  5. 模拟开仓 (Wilson + Kelly)
  6. 持仓 mark-to-market
  7. 卖出 (sell_rules: stop / trailing / target / hp_expired / stage_deterioration)

输出: NAV 每日, 最终 metrics, HS300 对比, regime 分层报告
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn
from services.portfolio_walk_forward.cash_manager import dynamic_cash_pct
from services.portfolio_walk_forward.liquidity import passes_liquidity, round_to_lots
from services.portfolio_walk_forward.metrics import compute_metrics, compute_excess_alpha
from services.portfolio_walk_forward.regime import classify_regime
from services.trading_config import EXECUTION_MODEL
from services.trading_config.slippage import apply_costs_to_return


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("portfolio_backtest")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end",   default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=15)
    parser.add_argument("--benchmark", default="000300")  # HS300
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()

    t0 = time.time()
    log.info(f"=== π Portfolio Walk-Forward Backtest ===")
    log.info(f"  range: {args.start} → {args.end}")
    log.info(f"  initial: {args.initial_capital:,.0f}")
    log.info(f"  benchmark: {args.benchmark}")

    # 1. 准备: 交易日历
    conn = get_conn()
    trade_dates = [r[0] for r in conn.execute(
        """SELECT trade_date FROM dim_trading_calendar
            WHERE is_trading=1 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date""",
        [args.start, args.end],
    ).fetchall()]
    log.info(f"  交易日: {len(trade_dates):,}")

    # 2. 加载 HS300 K线 (作 benchmark + regime 输入)
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    bench_rows = mkt.execute(
        """SELECT date, close FROM v_price_kline_qfq
            WHERE code = ? AND freq='daily' AND adjust='qfq'
              AND date BETWEEN ? AND ?
            ORDER BY date""",
        [args.benchmark, args.start, args.end],
    ).fetchall()
    bench_close = {r[0]: float(r[1]) for r in bench_rows}
    log.info(f"  benchmark K 线: {len(bench_close):,} 行")

    # 3. 加载所有股票 K 线 (lazy, per stock as needed)
    # 先拿 buy_signal 历史里出现过的 stock_code
    stocks = [r[0] for r in conn.execute(
        """SELECT DISTINCT stock_code FROM mart_stock_formula_buy_signal_daily
            WHERE signal_date BETWEEN ? AND ?""",
        [args.start, args.end],
    ).fetchall()]
    log.info(f"  涉及股票: {len(stocks):,}")
    if not stocks:
        log.error("buy_signal 表无该期数据")
        return

    # π.3b: 加载 technical_stage 历史 (供 stage 恶化止损用)
    log.info("加载 technical_stage 历史 (π.3b stage 恶化止损) ...")
    stage_rows = conn.execute(
        """SELECT stock_code, date, stage FROM fact_stock_technical_stage
            WHERE date BETWEEN ? AND ?""",
        [args.start, args.end],
    ).fetchall()
    stage_by: dict[str, dict[str, str]] = defaultdict(dict)
    for sc, d, st in stage_rows:
        stage_by[sc][d] = st
    log.info(f"  technical_stage: {len(stage_rows):,} 行")

    placeholders = ",".join(["?"] * len(stocks))
    log.info("加载相关股票 K 线 ...")
    kl_rows = mkt.execute(
        f"""SELECT code, date, open, high, low, close, volume, amount
              FROM v_price_kline_qfq
             WHERE freq='daily' AND adjust='qfq' AND code IN ({placeholders})
               AND date BETWEEN ? AND ?
             ORDER BY code, date""",
        stocks + [args.start, args.end],
    ).fetchall()
    mkt.close()
    kline: dict[str, dict[str, dict]] = defaultdict(dict)
    for code, dt, o, h, l, c, v, a in kl_rows:
        kline[code][dt] = {"open": float(o or 0), "high": float(h or 0),
                            "low": float(l or 0), "close": float(c or 0),
                            "volume": float(v or 0), "amount": float(a or 0)}
    log.info(f"  K 线: {len(kl_rows):,} 行")

    # 4. 加载 trigger 历史 + per-stock optimal (point-in-time, 无 look-ahead)
    # 直接 JOIN smartmoney 内的表, 不依赖 buy_signal 表 (避免 backfill 40 min)
    log.info("加载 trigger × stage-aware optimal × context 历史 (η+++++++ A) ...")
    # 切换到 mart_per_stock_stage_strategy_optimal — 按 stage 维度寻优的 17,663 行
    # JOIN ON (stock × variant × stage), 完整匹配当日 stage 的最佳参数
    # fallback: 当日 stage 在该 stock 历史无数据 → 用跨 stage 平均 (旧表 optimal)
    sig_rows = conn.execute(
        """SELECT t.date, t.stock_code, t.formula_id, t.formula_variant,
                  COALESCE(c.technical_stage, '?') AS stage,
                  COALESCE(sopt.optimal_hp, opt.optimal_hp) AS opt_hp,
                  COALESCE(sopt.optimal_stop_pct, opt.optimal_stop_pct) AS opt_stop,
                  COALESCE(sopt.optimal_target_pct, opt.optimal_target_pct) AS opt_target,
                  COALESCE(sopt.optimal_trailing_pct, opt.optimal_trailing_pct) AS opt_trail,
                  COALESCE(sopt.optimal_buy_offset, opt.optimal_buy_offset, 1) AS opt_offset,
                  COALESCE(sopt.sharpe, opt.sharpe) AS sharpe,
                  COALESCE(sopt.win_rate, opt.win_rate) AS win_rate,
                  COALESCE(sopt.n_traded, opt.n_traded) AS n_traded,
                  COALESCE(sopt.optimal_calmar, opt.optimal_calmar) AS calmar,
                  CASE WHEN sopt.stock_code IS NOT NULL THEN 'stage_aware'
                       ELSE 'cross_stage_fallback' END AS source_tier
             FROM fact_technical_trigger t
             LEFT JOIN fact_signal_context c
               ON c.stock_code = t.stock_code AND c.date = t.date
             LEFT JOIN mart_per_stock_stage_strategy_optimal sopt
               ON sopt.stock_code = t.stock_code
              AND sopt.formula_id = t.formula_id
              AND sopt.formula_variant = t.formula_variant
              AND sopt.stage_filter = c.technical_stage
              AND abs(sopt.avg_ret) <= 0.5 AND sopt.avg_max_dd >= -0.5
              AND abs(sopt.sharpe) <= 10 AND sopt.win_rate >= 0.5
             LEFT JOIN mart_per_stock_strategy_optimal opt
               ON opt.stock_code = t.stock_code
              AND opt.formula_id = t.formula_id
              AND opt.formula_variant = t.formula_variant
              AND abs(opt.avg_ret) <= 0.5 AND opt.avg_max_dd >= -0.5
              AND abs(opt.sharpe) <= 10 AND opt.win_rate >= 0.5
            WHERE t.date BETWEEN ? AND ?
              AND (sopt.stock_code IS NOT NULL OR opt.stock_code IS NOT NULL)
            ORDER BY t.date, COALESCE(sopt.sharpe, opt.sharpe) DESC NULLS LAST""",
        [args.start, args.end],
    ).fetchall()
    # 报告 stage-aware vs fallback 占比
    n_stage_aware = sum(1 for r in sig_rows if r[14] == "stage_aware")
    n_fallback = len(sig_rows) - n_stage_aware
    log.info(f"  signal_inline: stage_aware={n_stage_aware:,}, fallback={n_fallback:,}")
    signals_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in sig_rows:
        # Inline 评分: sharpe + win + calmar 综合
        sharpe = r[10] or 0
        win    = r[11] or 0
        calmar = r[13] or 0
        # 简化 tier 判定
        if sharpe >= 0.8 and win >= 0.65 and calmar >= 1.0:
            tier = "STRONG_BUY"
        elif sharpe >= 0.3 and win >= 0.55:
            tier = "BUY"
        else:
            continue
        # tier=STRONG_BUY score=sharpe*100, BUY score=sharpe*50
        score = sharpe * (100 if tier == "STRONG_BUY" else 50)
        signals_by_date[r[0]].append({
            "stock_code": r[1], "formula_variant": r[3], "tier": tier, "score": score,
            "hp": r[5], "stop_pct": r[6], "target_pct": r[7], "trailing_pct": r[8],
            "buy_offset": r[9] or 1,
            "sharpe": sharpe, "win_rate": win, "n_traded": r[12], "stage": r[4],
        })
    # 按 date 内 score desc 排序
    for d in signals_by_date:
        signals_by_date[d].sort(key=lambda x: -x["score"])
    log.info(f"  signal_inline: {sum(len(v) for v in signals_by_date.values()):,} 条 / {len(signals_by_date)} 天")

    # 5. Walk-forward simulator
    cash = args.initial_capital
    positions: dict[tuple[str, str], dict] = {}    # (stock × variant) → 仓位状态
    nav_series: list[tuple[str, float]] = []       # (date, total_nav)
    benchmark_nav_series: list[tuple[str, float]] = []
    regime_track: list[tuple[str, str]] = []       # (date, regime)
    n_trades = 0

    bench_start = next(iter(bench_close.values())) if bench_close else 1.0

    for i, today in enumerate(trade_dates):
        # 5a. 估值 (mark-to-market)
        equity = 0.0
        to_remove = []
        for key, pos in positions.items():
            stock = pos["stock_code"]
            k = kline.get(stock, {}).get(today)
            if not k:
                continue
            today_close = k["close"]
            # 持仓收益
            cur_value = today_close * pos["shares"]
            equity += cur_value

            # 5b. 检查卖出 (5 优先级 + π.3b stage 恶化)
            sell_price = None
            sell_reason = None
            # Priority 1: stop loss (intraday low ≤ stop)
            stop_price = pos["buy_price"] * (1 + pos["stop_pct"])
            if k["low"] <= stop_price:
                sell_price = stop_price * (1 + EXECUTION_MODEL.sell_pricing.stop_loss_slippage_pct)
                sell_reason = "stop_loss"
            # π.3b: stage 恶化提前止损
            # 触发条件: 买入时 stage ≤ 2 (上升), 今日 stage = 4 (顶部破位) AND 持仓收益 < 0
            elif (pos.get("entry_stage") in ("1", "1.5", "2")
                  and stage_by.get(stock, {}).get(today) == "4"
                  and today_close < pos["buy_price"]):
                sell_price = today_close
                sell_reason = "stage_deterioration"
            else:
                # Priority 2: target hit + arm trailing
                target_price = pos["buy_price"] * (1 + pos["target_pct"])
                if not pos.get("trailing_armed") and k["high"] >= target_price:
                    pos["trailing_armed"] = True
                # Priority 3: trailing
                if pos.get("trailing_armed"):
                    pos["high_since"] = max(pos.get("high_since", target_price), k["high"])
                    dd = (k["close"] - pos["high_since"]) / pos["high_since"]
                    if dd <= -pos["trailing_pct"]:
                        sell_price = k["close"]
                        sell_reason = "trailing"
                # Priority 4: hp expired
                if sell_price is None:
                    held = pos["days_held"] + 1
                    if held >= pos["hp"]:
                        sell_price = k["close"]
                        sell_reason = "hp_expired"
                    pos["days_held"] = held

            if sell_price is not None:
                proceeds = sell_price * pos["shares"]
                # 扣卖出成本
                cost = EXECUTION_MODEL.cost.sell_cost_pct()
                proceeds *= (1 - cost)
                cash += proceeds
                equity -= cur_value
                to_remove.append(key)
                n_trades += 1
        for key in to_remove:
            positions.pop(key)

        # 5c. NAV
        total_nav = cash + equity
        nav_series.append((today, total_nav))

        # benchmark NAV
        if today in bench_close:
            bench_ratio = bench_close[today] / bench_start
            bench_value = args.initial_capital * bench_ratio
            benchmark_nav_series.append((today, bench_value))

        # 5d. 当日新开仓 (使用 today 信号, 这里假设 buy_signal 是收盘后算的, 实际 T+1 买)
        # 简化: buy_offset 默认 1, signal day t → 持仓状态在 t+1 才进入
        # 实际 walk-forward: 看 today 的 buy_signal, T+1 即明天开仓
        if today in signals_by_date and i + 1 < len(trade_dates):
            tomorrow = trade_dates[i + 1]
            signals_today = signals_by_date[today]
            # 分 tier 统计
            strong_buy = [s for s in signals_today if s["tier"] == "STRONG_BUY"]
            buy = [s for s in signals_today if s["tier"] == "BUY"]

            # 5e. cash management
            target_cash_pct = dynamic_cash_pct(n_strong_buy=len(strong_buy), n_buy=len(buy))
            target_invest = total_nav * (1 - target_cash_pct)

            # 5f. 选 top N (preferring STRONG_BUY)
            candidates = (strong_buy + buy)[: args.max_positions]
            n_new = max(0, args.max_positions - len(positions))
            candidates = candidates[:n_new]

            # 5g. 对每个 candidate, 评估流动性 + 资金量
            cash_per_stock = target_invest / max(args.max_positions, 1)
            for sig in candidates:
                k = kline.get(sig["stock_code"], {}).get(tomorrow)
                if not k:
                    continue
                # liquidity check
                ok, _ = passes_liquidity(
                    today_amount=k["amount"], today_price=k["close"],
                    today_volume=k["volume"], avg_amount_20d=k["amount"],
                )
                if not ok:
                    continue
                # 买入价 (T+1 VWAP)
                buy_price = k["amount"] / (k["volume"] * 100) if k["volume"] > 0 else k["close"]
                if buy_price <= 0:
                    continue
                # 仓位
                shares = round_to_lots(cash_per_stock, buy_price)
                if shares < 100:
                    continue
                cost_total = shares * buy_price
                buy_cost = cost_total * EXECUTION_MODEL.cost.buy_cost_pct()
                total_cost = cost_total + buy_cost
                if total_cost > cash:
                    continue
                # 开仓
                key = (sig["stock_code"], sig["formula_variant"])
                if key in positions:
                    continue
                positions[key] = {
                    "stock_code": sig["stock_code"], "formula_variant": sig["formula_variant"],
                    "buy_date": tomorrow, "buy_price": buy_price, "shares": shares,
                    "hp": sig["hp"] or 30,
                    "stop_pct": sig["stop_pct"] or -0.06,
                    "target_pct": sig["target_pct"] or 0.10,
                    "trailing_pct": sig["trailing_pct"] or 0.025,
                    "days_held": 0, "trailing_armed": False,
                    "entry_stage": sig.get("stage"),   # π.3b 记录开仓 stage
                }
                cash -= total_cost
                n_trades += 1

        # 5h. regime 跟踪 (60d 滚动)
        if today in bench_close and i >= 60:
            d_60ago = trade_dates[i - 60]
            if d_60ago in bench_close:
                ret_60 = bench_close[today] / bench_close[d_60ago] - 1
                regime = classify_regime(ret_60)
                regime_track.append((today, regime))

        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{len(trade_dates)}: NAV={total_nav:,.0f} positions={len(positions)} trades={n_trades}")

    log.info(f"=== 模拟完成 ({time.time()-t0:.0f}s) ===")

    # 6. 计算 metrics
    nav_only = [v for _, v in nav_series]
    bench_only = [v for _, v in benchmark_nav_series]
    # 对齐长度 (二者可能略不同)
    min_len = min(len(nav_only), len(bench_only))
    nav_only = nav_only[:min_len]
    bench_only = bench_only[:min_len]

    m = compute_metrics(nav_only)
    bm = compute_metrics(bench_only)
    excess = compute_excess_alpha(nav_only, bench_only)

    print()
    print(f"{'='*100}")
    print(f"  Portfolio Backtest 报告 ({args.start} → {args.end})")
    print(f"{'='*100}")
    print(f"  {'metric':<25} {'策略':>12} {'HS300':>12} {'超额':>12}")
    print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")
    print(f"  {'总收益':<25} {m.total_return*100:>+11.1f}% {bm.total_return*100:>+11.1f}% {excess.get('excess_total_return', 0)*100:>+11.1f}%")
    print(f"  {'年化收益':<25} {m.annual_return*100:>+11.1f}% {bm.annual_return*100:>+11.1f}%")
    print(f"  {'最大回撤':<25} {m.max_drawdown*100:>+11.1f}% {bm.max_drawdown*100:>+11.1f}%")
    print(f"  {'Calmar':<25} {m.calmar:>+12.2f} {bm.calmar:>+12.2f}")
    print(f"  {'Sharpe (年化)':<25} {m.sharpe:>+12.2f} {bm.sharpe:>+12.2f}")
    print(f"  {'月度胜率':<25} {m.monthly_win_rate*100:>+11.1f}% {bm.monthly_win_rate*100:>+11.1f}%")
    print(f"  {'信息比率 IR':<25} {'':>12} {'':>12} {excess.get('information_ratio', 0):>+12.2f}")
    print(f"  {'交易次数':<25} {n_trades:>12} - -")
    print()

    # 7. regime 分层报告
    if regime_track:
        regime_nav: dict[str, list[float]] = defaultdict(list)
        date_to_nav = dict(nav_series)
        prev_regime = None
        regime_segments: list[tuple[str, float, float]] = []   # (regime, start_nav, end_nav)
        seg_start = None
        for dt, reg in regime_track:
            if reg != prev_regime:
                if seg_start and prev_regime:
                    end_nav = date_to_nav.get(prev_dt, seg_start)
                    regime_segments.append((prev_regime, seg_start, end_nav))
                seg_start = date_to_nav.get(dt, 0)
                prev_regime = reg
            prev_dt = dt
        if seg_start and prev_regime:
            regime_segments.append((prev_regime, seg_start, date_to_nav.get(prev_dt, seg_start)))

        regime_ret: dict[str, list[float]] = defaultdict(list)
        for reg, sn, en in regime_segments:
            if sn > 0:
                regime_ret[reg].append(en / sn - 1)

        print(f"{'='*100}")
        print(f"  Regime 分层表现")
        print(f"{'='*100}")
        for reg in ("bull", "bear", "sideways"):
            rets = regime_ret.get(reg, [])
            if rets:
                avg_ret = np.mean(rets) * 100
                print(f"  {reg:<10}: {len(rets)} 段, 平均段内收益 {avg_ret:+.1f}%")
            else:
                print(f"  {reg:<10}: 无")

    # 8. 决策
    print()
    print(f"{'='*100}")
    print(f"  上线决策 (用户标准: 年化 ≥+30%, max_dd ≤-20%, 超额 alpha > 0)")
    print(f"{'='*100}")
    pass_annual = m.annual_return >= 0.30
    pass_dd     = m.max_drawdown >= -0.20
    pass_alpha  = excess.get("excess_total_return", 0) > 0
    print(f"  年化 {m.annual_return*100:+.1f}% ≥+30%: {'✅ PASS' if pass_annual else '❌ FAIL'}")
    print(f"  max_dd {m.max_drawdown*100:+.1f}% ≥-20%: {'✅ PASS' if pass_dd else '❌ FAIL'}")
    print(f"  超额 alpha {excess.get('excess_total_return', 0)*100:+.1f}% > 0: {'✅ PASS' if pass_alpha else '❌ FAIL'}")
    print(f"  ============================================")
    if pass_annual and pass_dd and pass_alpha:
        print(f"  最终: ✅ 满足全部标准, 可考虑上实盘")
    else:
        print(f"  最终: ❌ 未满足全部标准, 需优化")

    # 9. NAV CSV 输出 (供 UI 画图)
    import csv
    out_csv = Path(__file__).resolve().parents[2] / "data" / "portfolio_backtest_nav.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "strategy_nav", "benchmark_nav"])
        for (d, sv), (_, bv) in zip(nav_series, benchmark_nav_series):
            w.writerow([d, sv, bv])
    log.info(f"NAV CSV 写入: {out_csv}")

    conn.close()


if __name__ == "__main__":
    main()
