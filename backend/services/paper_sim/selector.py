"""Paper Sim v2 — 候选选股 + 流动性过滤.

数据源: mart_daily_position_recommendation (已含 Wilson+Kelly+6 因子综合 score,
        每股每 formula 一行, 多 horizon)

不在这里重做评分; 信任上游 daily_position_recommendation 输出, 仅:
  - tier 过滤
  - 流动性过滤 (复用 services/portfolio_walk_forward/liquidity.py)
  - 跳过当前已持仓 (driver 传入持仓 set)
  - 按 score 降序
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import SelectionConfig
from services.paper_sim.swap_rules import Candidate
from services.portfolio_walk_forward.liquidity import (
    LiquidityConfig, passes_liquidity, round_to_lots,
)


TIER_RANK = {"NO_SIGNAL": 0, "WATCH": 1, "BUY": 2, "STRONG_BUY": 3}


@dataclass(frozen=True)
class CandidateRow:
    """daily_position_recommendation 的一行 (Optuna+sizing 出来的)."""
    stock_code: str
    formula_id: str
    formula_variant: str
    tier: str
    score: float
    expected_total_return: float    # avg_ret
    optimal_hp: int
    optimal_target_pct: Optional[float]
    optimal_stop_pct: Optional[float]
    optimal_trailing_pct: Optional[float]
    signal_close: float
    sell_target: Optional[float]
    stop_price: Optional[float]
    stage: Optional[str]
    match_tier: str                 # 'stage_aware' / 'cross_stage_fallback'


def load_today_candidates(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """加载当日 mart_daily_position_recommendation + JOIN buy_signal_daily.tier.

    daily_position_recommendation 自身的 confidence_tier 是数字 (T1/T2/T3 置信度),
    跟 BUY/STRONG_BUY 是两回事. STRONG_BUY/BUY tier 字符串在
    mart_stock_formula_buy_signal_daily 里 — 上游已按 (stock × formula × signal_date)
    打过 tier, 这里 JOIN 拿到.
    """
    rows = conn.execute(
        f"""
        SELECT dpr.stock_code, dpr.formula_id, dpr.formula_variant,
               COALESCE(bs.tier, 'BUY') AS tier,
               dpr.score, dpr.avg_ret AS expected_total_return,
               dpr.holding_days AS optimal_hp,
               dpr.optimal_target_pct, dpr.optimal_stop_pct, dpr.optimal_trailing_pct,
               dpr.signal_close_price, dpr.sell_target_price, dpr.stop_price,
               dpr.stage_bin AS stage, dpr.match_tier
          FROM {cfg.candidate_source} dpr
          LEFT JOIN mart_stock_formula_buy_signal_daily bs
                 ON bs.signal_date     = dpr.signal_date
                AND bs.stock_code      = dpr.stock_code
                AND bs.formula_id      = dpr.formula_id
                AND bs.formula_variant = dpr.formula_variant
         WHERE dpr.signal_date = ?
           AND (
              ? = 'WATCH'
              OR (? = 'BUY' AND COALESCE(bs.tier, 'BUY') IN ('BUY', 'STRONG_BUY'))
              OR (? = 'STRONG_BUY' AND bs.tier = 'STRONG_BUY')
           )
         ORDER BY dpr.score DESC
        """,
        [signal_date, cfg.min_tier_to_buy, cfg.min_tier_to_buy, cfg.min_tier_to_buy],
    ).fetchall()

    out: list[CandidateRow] = []
    for r in rows:
        if cfg.exclude_stage and r[13] in cfg.exclude_stage:
            continue
        out.append(CandidateRow(
            stock_code=r[0], formula_id=r[1], formula_variant=r[2],
            tier=r[3] or "BUY", score=float(r[4] or 0),
            expected_total_return=float(r[5] or 0),
            optimal_hp=int(r[6] or 0),
            optimal_target_pct=float(r[7]) if r[7] is not None else None,
            optimal_stop_pct=float(r[8]) if r[8] is not None else None,
            optimal_trailing_pct=float(r[9]) if r[9] is not None else None,
            signal_close=float(r[10] or 0),
            sell_target=float(r[11]) if r[11] is not None else None,
            stop_price=float(r[12]) if r[12] is not None else None,
            stage=r[13], match_tier=r[14],
        ))
    return out


def filter_by_liquidity(
    candidates: list[CandidateRow],
    kline_today: dict[str, dict],
    cfg: SelectionConfig,
) -> tuple[list[CandidateRow], dict[str, str]]:
    """过滤流动性. kline_today: {stock: {amount, close, volume, amount_ma20}}.

    Returns (passed_list, rejected_reasons_dict).
    """
    liq_cfg = LiquidityConfig(
        min_avg_amount_yuan=cfg.liquidity_min_amount_20d,
        max_price_per_share=cfg.liquidity_max_price,
    )
    passed: list[CandidateRow] = []
    rejected: dict[str, str] = {}
    for c in candidates:
        k = kline_today.get(c.stock_code)
        if not k:
            rejected[c.stock_code] = "no_kline_today"
            continue
        ok, why = passes_liquidity(
            today_amount=k.get("amount"),
            today_price=k.get("close"),
            today_volume=k.get("volume"),
            avg_amount_20d=k.get("amount_ma20"),
            config=liq_cfg,
        )
        if ok:
            passed.append(c)
        else:
            rejected[c.stock_code] = why or "liquidity_reject"
    return passed, rejected


def to_swap_candidate(c: CandidateRow) -> Candidate:
    """从选股 row 转换成 swap_rules.Candidate (供 swap 评估)."""
    return Candidate(
        stock_code=c.stock_code,
        tier=c.tier,
        score=c.score,
        expected_total_return=c.expected_total_return,
        optimal_hp=c.optimal_hp,
    )
