"""主入口: 候选信号 + 历史 metrics + risk profile → 加权仓位推荐.

输入: list of candidate dicts (含 win_rate / n_signals / avg_ret / avg_dd / hp 等)
输出: filtered + ranked + sized list (含 wilson_win / kelly_f / position_pct / tier)

Phase η++++ (2026-05-12): 接入 sentiment factor_registry — 不硬编码 profile.
Phase ε.2  (2026-05-12): 买卖价计算全部走 trading_config (回测 ↔ 实盘一致).
"""
from __future__ import annotations

from typing import Any

from services.portfolio_sizer.kelly import kelly_fraction
from services.portfolio_sizer.profiles import RiskProfile
from services.portfolio_sizer.wilson import wilson_from_rate
from services.sentiment.factor_registry import get_eligible_factors
from services.trading_config import EXECUTION_MODEL
from services.trading_config.buy_pricing import compute_buy_price


MATCH_TIER_PRIORITY = {
    "stage_pit": 2,
    "stage_pit_formula_fallback": 1,
}


def confidence_tier(wilson_win: float, n_signals: int) -> int:
    """Tier 1=高信心 / 2=中 / 3=低."""
    if wilson_win >= 0.80 and n_signals >= 10:
        return 1
    if wilson_win >= 0.70 and n_signals >= 5:
        return 2
    return 3


def match_tier_priority(match_tier: str | None) -> int:
    """PIT-safe 匹配层级优先于 cross-stage fallback."""
    if not match_tier:
        return 0
    return MATCH_TIER_PRIORITY.get(match_tier, 0)


def trailing_threshold(target_ret: float, profile: RiskProfile) -> float:
    """trailing = max(profile.trailing_pct_min, target_ret × ratio)."""
    if target_ret is None or target_ret <= 0:
        return profile.trailing_pct_min
    return max(profile.trailing_pct_min, target_ret * profile.trailing_ratio)


def rank_and_size(
    candidates: list[dict],
    profile: RiskProfile,
) -> list[dict]:
    """主流水:
       1. filter: hp ∈ profile.holding_days, n_signals ≥ min_n, wilson_win ≥ min_wilson
       2. exclude: fundamental_stage in exclude_fund_stages
       3. compute: wilson_win / kelly_f / tier
       4. rank by score (wilson_win × log(n) × calmar)
       5. take top max_positions
       6. normalize: 单股 cap + 总 ≤ 90% (留 10% cash)

    Args:
        candidates: list of dict, 必须含:
          - stock_code, formula_id, formula_variant, stage_filter (可选)
          - holding_days, n_signals, win_rate (朴素), avg_ret, avg_dd, calmar
          - fundamental_stage (可选, 用于风险过滤)
          - signal_close (T 日收盘, 用于算 buy/sell price)
        profile: RiskProfile

    Returns:
        list of dict, 含原字段 + 新增:
          - wilson_win, kelly_f, position_pct, confidence_tier
          - buy_price, sell_target, stop_price, trailing_pct
          - rank_in_profile, score
    """
    # Step 0: 查注册表 — 该 profile 启用哪些 sentiment 因子加权
    eligible_factors = get_eligible_factors(profile.profile_id)

    # Step 1: filter
    pool = []
    for c in candidates:
        # hp filter
        hp = c.get("holding_days")
        if hp not in profile.holding_days:
            continue
        n = int(c.get("n_signals") or 0)
        if n < profile.min_n_signals:
            continue
        raw_win = float(c.get("win_rate") or 0.0)
        avg_ret = float(c.get("avg_ret") or 0.0)
        avg_dd = float(c.get("avg_dd") or 0.0)
        if avg_ret <= 0:
            continue  # 历史不赚不推荐
        # Step 2: fundamental_stage 排除
        fund_stage = c.get("fundamental_stage")
        if fund_stage and fund_stage in profile.exclude_fund_stages:
            continue
        # Step 3: Wilson 修正
        wilson_win = wilson_from_rate(raw_win, n, confidence=0.95)
        if wilson_win < profile.min_wilson_win:
            continue
        # Kelly
        kelly_f = kelly_fraction(
            win_rate=wilson_win, avg_ret=avg_ret, avg_dd=avg_dd,
            kelly_mul=profile.kelly_fraction, max_f=profile.stock_cap_pct,
        )
        if kelly_f <= 0:
            continue
        tier = confidence_tier(wilson_win, n)
        # Step 4: score 排序键 (base)
        import math
        calmar = float(c.get("calmar") or 0.0)
        score_base = wilson_win * math.log(1.0 + n) * max(0.1, calmar)

        # Step 4b: sentiment 加权 — 查 registry, 逐因子乘 multiplier (架构: 无 if-profile)
        sentiment_mult = 1.0
        sentiment_trace: list[str] = []  # 记录各因子贡献, 供 UI / 调试
        for f in eligible_factors:
            bin_val = c.get(f.bin_column) if f.bin_column else None
            m = f.get_multiplier(bin_val)
            sentiment_mult *= m
            if m != 1.0:
                sentiment_trace.append(f"{f.factor_id}:{bin_val}×{m:.2f}")
        score = score_base * sentiment_mult
        # 价格估算 — 走 trading_config (Phase ε.2: 不再硬编码 1.005)
        # candidate dict 应携带 T+1 OHLCV (build_*.py 注入), 优先用 VWAP; 缺数据回退 signal_close+pct
        signal_close = c.get("signal_close")
        next_open    = c.get("next_open")
        next_amount  = c.get("next_amount")
        next_volume  = c.get("next_volume")
        buy_price = compute_buy_price(
            signal_close=signal_close,
            next_open=next_open, next_amount=next_amount, next_volume=next_volume,
            config=EXECUTION_MODEL.buy_pricing,
        )
        # daily 推荐场景下 T+1 数据未到, 回退用 signal_close × (1 + 默认溢价 0.5%)
        if buy_price is None and signal_close and signal_close > 0:
            from services.trading_config.buy_pricing import BuyPricingConfig
            fallback_cfg = BuyPricingConfig(mode="signal_close_plus_pct", signal_close_pct=0.005)
            buy_price = compute_buy_price(signal_close=signal_close, config=fallback_cfg)

        # Phase ζ: 用每股每 variant Optuna 寻优出的最佳策略参数 (不再用 DEFAULT_STRATEGY)
        # 这些参数从 mart_per_stock_strategy_optimal 通过 candidate dict 传入.
        # fallback 到 DEFAULT_STRATEGY 仅在 candidate 无寻优结果时 (新股 / 数据缺失).
        from services.backtest.strategy_defaults import DEFAULT_STRATEGY
        opt_stop   = c.get("optimal_stop_pct")
        opt_target = c.get("optimal_target_pct")
        opt_trail  = c.get("optimal_trailing_pct")
        eff_stop   = opt_stop   if opt_stop   is not None else DEFAULT_STRATEGY.stop_pct
        eff_target = opt_target if opt_target is not None else DEFAULT_STRATEGY.target_pct
        eff_trail  = opt_trail  if opt_trail  is not None else DEFAULT_STRATEGY.trailing_pct
        if buy_price and buy_price > 0:
            sell_target = buy_price * (1.0 + eff_target)
            stop_price  = buy_price * (1.0 + eff_stop)
        else:
            buy_price = sell_target = stop_price = None
        trailing_pct = eff_trail

        pool.append({
            **c,
            "wilson_win": wilson_win,
            "kelly_f": kelly_f,
            "confidence_tier": tier,
            "score": score,
            "score_base": score_base,
            "sentiment_mult": sentiment_mult,
            "sentiment_trace": "; ".join(sentiment_trace) if sentiment_trace else None,
            "buy_price": buy_price,
            "sell_target": sell_target,
            "stop_price": stop_price,
            "trailing_pct": trailing_pct,
        })

    # Step 5a: dedup — per (stock_code, formula_variant) 只保留最高分一行
    # 同一只股票可能有多个 hp/variant 信号; portfolio 内只持仓一次
    pool.sort(
        key=lambda x: (
            match_tier_priority(x.get("match_tier")),
            x["score"],
        ),
        reverse=True,
    )
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for x in pool:
        key = (x.get("stock_code"), x.get("formula_variant"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(x)

    # Step 5b: dedup 再做一次: 同股不同 variant 也只保留一只 (portfolio 视角)
    seen_stocks: set[str] = set()
    final_pool: list[dict] = []
    for x in deduped:
        sc = x.get("stock_code")
        if sc in seen_stocks:
            continue
        seen_stocks.add(sc)
        final_pool.append(x)

    selected = final_pool[: profile.max_positions]

    # Step 6: normalize position_pct
    # 朴素策略: kelly_f 直接当 position; 但总和需 ≤ 90% (10% cash)
    total_kelly = sum(x["kelly_f"] for x in selected)
    cash_target = 0.10
    budget = 1.0 - cash_target
    if total_kelly > budget:
        # 按比例缩放
        scale = budget / total_kelly
        for x in selected:
            x["position_pct"] = round(x["kelly_f"] * scale, 4)
    else:
        for x in selected:
            x["position_pct"] = round(x["kelly_f"], 4)

    # 单股 cap 兜底
    for x in selected:
        x["position_pct"] = min(x["position_pct"], profile.stock_cap_pct)
        x["rank_in_profile"] = selected.index(x) + 1

    return selected
