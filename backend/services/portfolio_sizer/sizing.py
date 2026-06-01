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


def evaluate_candidate(
    candidate: dict[str, Any],
    profile: RiskProfile,
    eligible_factors: list[Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, bool]]:
    """Evaluate one candidate against a profile and return enriched output or a fail reason.

    The trace is intentionally boolean and ordered so audit tools can count
    how many candidates reached each gate without re-implementing the ranking
    logic.
    """
    trace = {
        "hp": False,
        "n_signals": False,
        "avg_ret": False,
        "fund_stage": False,
        "wilson": False,
        "kelly": False,
    }
    hp = candidate.get("holding_days")
    if hp not in profile.holding_days:
        return None, "hp", trace
    trace["hp"] = True

    n = int(candidate.get("n_signals") or 0)
    if n < profile.min_n_signals:
        return None, "n_signals", trace
    trace["n_signals"] = True

    raw_win = float(candidate.get("win_rate") or 0.0)
    avg_ret = float(candidate.get("avg_ret") or 0.0)
    avg_dd = float(candidate.get("avg_dd") or 0.0)
    if avg_ret <= 0:
        return None, "avg_ret", trace  # 历史不赚不推荐
    trace["avg_ret"] = True

    fund_stage = candidate.get("fundamental_stage")
    if fund_stage and fund_stage in profile.exclude_fund_stages:
        return None, "fund_stage", trace
    trace["fund_stage"] = True

    wilson_win = wilson_from_rate(raw_win, n, confidence=0.95)
    if wilson_win < profile.min_wilson_win:
        return None, "wilson", trace
    trace["wilson"] = True

    kelly_f = kelly_fraction(
        win_rate=wilson_win, avg_ret=avg_ret, avg_dd=avg_dd,
        kelly_mul=profile.kelly_fraction, max_f=profile.stock_cap_pct,
    )
    if kelly_f <= 0:
        return None, "kelly", trace
    trace["kelly"] = True

    if eligible_factors is None:
        eligible_factors = get_eligible_factors(profile.profile_id)
    import math
    calmar = float(candidate.get("calmar") or 0.0)
    score_base = wilson_win * math.log(1.0 + n) * max(0.1, calmar)

    sentiment_mult = 1.0
    sentiment_trace: list[str] = []
    for f in eligible_factors:
        bin_val = candidate.get(f.bin_column) if f.bin_column else None
        m = f.get_multiplier(bin_val)
        sentiment_mult *= m
        if m != 1.0:
            sentiment_trace.append(f"{f.factor_id}:{bin_val}×{m:.2f}")
    score = score_base * sentiment_mult

    signal_close = candidate.get("signal_close")
    next_open = candidate.get("next_open")
    next_amount = candidate.get("next_amount")
    next_volume = candidate.get("next_volume")
    buy_price = compute_buy_price(
        signal_close=signal_close,
        next_open=next_open, next_amount=next_amount, next_volume=next_volume,
        config=EXECUTION_MODEL.buy_pricing,
    )
    if buy_price is None and signal_close and signal_close > 0:
        from services.trading_config.buy_pricing import BuyPricingConfig
        fallback_cfg = BuyPricingConfig(mode="signal_close_plus_pct", signal_close_pct=0.005)
        buy_price = compute_buy_price(signal_close=signal_close, config=fallback_cfg)

    from services.backtest.strategy_defaults import DEFAULT_STRATEGY
    opt_stop = candidate.get("optimal_stop_pct")
    opt_target = candidate.get("optimal_target_pct")
    opt_trail = candidate.get("optimal_trailing_pct")
    eff_stop = opt_stop if opt_stop is not None else DEFAULT_STRATEGY.stop_pct
    eff_target = opt_target if opt_target is not None else DEFAULT_STRATEGY.target_pct
    eff_trail = opt_trail if opt_trail is not None else DEFAULT_STRATEGY.trailing_pct
    if buy_price and buy_price > 0:
        sell_target = buy_price * (1.0 + eff_target)
        stop_price = buy_price * (1.0 + eff_stop)
    else:
        buy_price = sell_target = stop_price = None

    enriched = {
        **candidate,
        "wilson_win": wilson_win,
        "kelly_f": kelly_f,
        "confidence_tier": confidence_tier(wilson_win, n),
        "score": score,
        "score_base": score_base,
        "sentiment_mult": sentiment_mult,
        "sentiment_trace": "; ".join(sentiment_trace) if sentiment_trace else None,
        "buy_price": buy_price,
        "sell_target": sell_target,
        "stop_price": stop_price,
        "trailing_pct": eff_trail,
    }
    return enriched, None, trace


def select_candidates(pool: list[dict[str, Any]], profile: RiskProfile) -> list[dict[str, Any]]:
    """Apply ranking and portfolio dedup rules to a scored pool."""
    pool.sort(
        key=lambda x: (
            match_tier_priority(x.get("match_tier")),
            x["score"],
        ),
        reverse=True,
    )
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for x in pool:
        key = (x.get("stock_code"), x.get("formula_variant"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(x)

    seen_stocks: set[str] = set()
    final_pool: list[dict[str, Any]] = []
    for x in deduped:
        sc = x.get("stock_code")
        if sc in seen_stocks:
            continue
        seen_stocks.add(sc)
        final_pool.append(x)

    return final_pool[: profile.max_positions]


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

    # Step 1: filter + score
    pool = []
    for c in candidates:
        enriched, fail_reason, _trace = evaluate_candidate(c, profile, eligible_factors)
        if fail_reason:
            continue
        if enriched is not None:
            pool.append(enriched)

    selected = select_candidates(pool, profile)

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
