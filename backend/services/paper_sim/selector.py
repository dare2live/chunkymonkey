"""Paper Sim v2 — 候选选股 + 流动性过滤.

两种 mode:

  selection.mode = "production"  (live 推荐用)
    数据源: mart_daily_position_recommendation (上游已 Wilson+Kelly+6 因子综合 score)
    + JOIN mart_stock_formula_buy_signal_daily.tier

  selection.mode = "backtest"    (walk-forward 用, 历史每天 inline 算)
    数据源: fact_technical_trigger + mart_per_stock_stage_strategy_optimal
            (cross-stage fallback) + fact_signal_context.technical_stage
    评分跟 portfolio_backtest.py 同款: tier 简化判定 (sharpe + win + calmar)

mode 选择放 config 里, business 代码不动 — Rule 2 + 项目特定 "模块化, 不硬编码".
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


def load_today_candidates_inline(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """backtest mode: 直接 JOIN trigger + optimal 表算候选, 不依赖 daily_rec.

    跟 portfolio_backtest.py 同款 SQL (Wilson + Kelly 简化, 通过 sharpe/win/calmar
    打 tier — 用户 6 因子综合 score 在 buy_signal_daily 里, 但历史每日不可用,
    所以 backtest 用简化 tier 评分).
    """
    th = cfg.backtest_tier_thresholds
    sb_th = th["strong_buy"]
    by_th = th["buy"]

    # Phase ψ.α B: 严格 walk-forward selector (0 leakage)
    #   - 用 mart_per_formula_stage_optimal (per-formula × stage × train_end_date 多行)
    #   - JOIN WHERE train_end_date <= signal_date — 在历史 t 时只能用 t-1 之前训出的 params
    #   - ORDER BY train_end_date DESC LIMIT 1 — 取最近一版 best params
    #   - 排名: 不用 mart.sharpe (会含 leakage), 用 t.strength DESC (公式当日 strength)
    #
    # 注意: cross-stage fallback (opt) 暂不接 — 反转策略 stage 是关键, 不该 fallback.
    # Phase ψ.α: formula_whitelist 过滤
    formula_filter_sql = ""
    formula_filter_params: list = []
    if cfg.formula_whitelist:
        ph = ",".join(["?"] * len(cfg.formula_whitelist))
        formula_filter_sql = f" AND t.formula_id IN ({ph})"
        formula_filter_params = list(cfg.formula_whitelist)
    rows = conn.execute(
        f"""
        WITH latest_train_end AS (
          SELECT formula_id, formula_variant, stage_filter,
                 MAX(train_end_date) AS train_end_date
            FROM mart_per_formula_stage_optimal
           WHERE train_end_date <= ?
           GROUP BY 1, 2, 3
        )
        SELECT t.date, t.stock_code, t.formula_id, t.formula_variant,
               COALESCE(c.technical_stage, '?') AS stage,
               pfo.optimal_hp        AS opt_hp,
               pfo.optimal_stop_pct  AS opt_stop,
               pfo.optimal_target_pct AS opt_target,
               pfo.optimal_trailing_pct AS opt_trail,
               -- 仅用于 tier 评级 (走 backtest_tier_thresholds), 不影响 selection bias:
               --   不再 ORDER BY oos_sharpe; selection 排名走 t.strength DESC 在下游 sort
               pfo.oos_sharpe        AS sharpe,
               pfo.oos_win_rate      AS win_rate,
               pfo.in_sample_calmar  AS calmar,
               pfo.oos_avg_ret       AS avg_ret,
               'walk_forward_global' AS source_tier,
               -- 关键: 当日 strength 用于排名 (公式当日算的, 0 leakage)
               t.strength            AS today_strength
          FROM fact_technical_trigger t
          LEFT JOIN fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
          JOIN latest_train_end lte
            ON lte.formula_id      = t.formula_id
           AND lte.formula_variant = t.formula_variant
           AND lte.stage_filter    = COALESCE(c.technical_stage, '?')
          JOIN mart_per_formula_stage_optimal pfo
            ON pfo.formula_id      = lte.formula_id
           AND pfo.formula_variant = lte.formula_variant
           AND pfo.stage_filter    = lte.stage_filter
           AND pfo.train_end_date  = lte.train_end_date
         WHERE t.date = ?
           {formula_filter_sql}
        """,
        [signal_date, signal_date] + formula_filter_params,
    ).fetchall()

    out: list[CandidateRow] = []
    for r in rows:
        sharpe = r[9] or 0
        win = r[10] or 0
        calmar = r[11] or 0
        avg_ret = r[12] or 0
        today_strength = r[14] or 0   # 当日 strength (公式当日算, 0 leakage)
        # tier (用 oos metric 评级 — oos 来自 train_end 当时的 forward 60d 实测, 0 leakage)
        if (sharpe >= sb_th["sharpe_min"]
                and win >= sb_th["win_rate_min"]
                and calmar >= sb_th["calmar_min"]):
            tier = "STRONG_BUY"
        elif sharpe >= by_th["sharpe_min"] and win >= by_th["win_rate_min"]:
            tier = "BUY"
        else:
            continue
        # min_tier_to_buy 过滤
        if cfg.min_tier_to_buy == "STRONG_BUY" and tier != "STRONG_BUY":
            continue
        # Phase ψ.α B 排名: 用 today_strength × tier_multiplier, 不用 sharpe (避免 selection leakage)
        # STRONG_BUY 候选 × 1.5, BUY × 1.0
        tier_mul = 1.5 if tier == "STRONG_BUY" else 1.0
        score = today_strength * tier_mul
        if cfg.exclude_stage and r[4] in cfg.exclude_stage:
            continue
        out.append(CandidateRow(
            stock_code=r[1], formula_id=r[2], formula_variant=r[3],
            tier=tier, score=score,
            expected_total_return=avg_ret,
            optimal_hp=int(r[5] or 0),
            optimal_target_pct=float(r[7]) if r[7] is not None else None,
            optimal_stop_pct=float(r[6]) if r[6] is not None else None,
            optimal_trailing_pct=float(r[8]) if r[8] is not None else None,
            signal_close=0,           # 不需要 (driver 走 K 线 close)
            sell_target=None, stop_price=None,
            stage=r[4], match_tier=r[13],
        ))
    out.sort(key=lambda c: -c.score)
    return out


def load_today_candidates_dispatch(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """根据 cfg.mode 分发到 production / backtest loader."""
    if cfg.mode == "backtest":
        return load_today_candidates_inline(conn, signal_date, cfg)
    return load_today_candidates(conn, signal_date, cfg)


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
