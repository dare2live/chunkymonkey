"""Phase 1c tiered_score_loader (Codex round 10 verdict + user 分层 push back).

User push back: "把小宇宙作为核心股票层, 其他的也先别放弃, 探索矬子里拔大个,
细化一下, 而不是把余下 ~3700 只去寻找共同点".

Codex round 10 verdict (A+D): PIT top-4 锁死 + non-PIT top-1 composite sub-rank
(ML score × 流动性 × stage × sector 拥挤惩罚), 不用 default fallback.

3 层 design:
- Layer 1 (core): PIT 1490 INNER JOIN top-N, 真 alpha 来自这层
- Layer 2 (explore): non-PIT (KEEP 5178 - PIT 1490) 内 composite sub-rank top-M
- Layer 3 (peripheral): 剩余 stocks, 暂不选 (留作未来)

vs paper_sim_ml_score_C_5178.yaml (Option C) 区别:
- C: 5178 全 universe 同 fallback default params 跑
- Tiered: explore 层用 composite score sub-rank (不是 default)

vs Option F (默认 paper_sim_ml_score.yaml) 区别:
- F: INNER JOIN 1490, 不够 5 仓位时 candidates < max_positions
- Tiered: 核心 4 + 探索 1, 满足 max_positions=5, 探索层是 candidate booster

API:
    load_today_candidates_tiered(conn, signal_date, model_id,
                                 core_slots, explore_slots,
                                 score_weights, sector_penalty)
    Returns list[CandidateRow] with tier field set ('core' / 'explore')
"""
from __future__ import annotations

import logging
from typing import Optional

from services.paper_sim.selector import CandidateRow
from services.universe import sql_where_active_a_share


log = logging.getLogger("paper_sim.tiered_score_loader")


def load_today_candidates_tiered(
    conn,
    signal_date: str,
    model_id: str = "lgbm_v3_honest_20d",
    core_slots: int = 4,
    explore_slots: int = 1,
    explore_pool_size: int = 20,
    score_weights: Optional[dict] = None,
    sector_penalty_pct: float = 0.10,
    exit_table: str = "mart_per_stock_stage_strategy_optimal_pit",
    fallback_params: Optional[dict] = None,
) -> list[CandidateRow]:
    """3 层 tiered selector (Phase 1c).

    Args:
        core_slots: PIT 1490 INNER JOIN top-N 取 (默认 4)
        explore_slots: non-PIT composite sub-rank top-M 取 (默认 1)
        explore_pool_size: non-PIT 内候选池大小 (composite rank 内的 sub-population, 默认 20)
        score_weights: {ml: 0.6, liquidity: 0.2, stage: 0.2} 各因子权重
        sector_penalty_pct: 单 sector 占核心层超 50% 时, 探索层同 sector candidate score penalty
        fallback_params: explore 层 exit params (ex-ante 弱假设, 跟 Option C 同)
    """
    if score_weights is None:
        score_weights = {"ml": 0.6, "liquidity": 0.2, "stage": 0.2}
    if fallback_params is None:
        fallback_params = {"hp": 10, "stop_pct": -0.08, "target_pct": 0.12, "trailing_pct": 0.08}

    # ============ Layer 1: 核心层 (PIT 1490 INNER JOIN top-N) ============
    sql_core = f"""
    WITH preds AS (
        SELECT stock_code, signal_date, score
        FROM mart_p0b_oos_predictions
        WHERE signal_date = ? AND model_id = ?
        ORDER BY score DESC NULLS LAST
        LIMIT {core_slots * 6}  -- 取多些防 INNER JOIN 后不够
    ),
    exit_pit AS (
        SELECT stock_code, formula_id, formula_variant,
               oos_avg_ret, holding_days,
               optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
               stage_filter
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY stock_code
                ORDER BY cutoff_date DESC,
                         COALESCE(oos_sharpe, sharpe) DESC NULLS LAST
            ) AS rn
            FROM {exit_table}
            WHERE CAST(cutoff_date AS DATE) <= CAST(? AS DATE)
              AND n_traded >= 5
        ) WHERE rn = 1
    )
    SELECT p.stock_code, p.score, e.formula_id, e.formula_variant,
           e.holding_days, e.optimal_target_pct, e.optimal_stop_pct,
           e.optimal_trailing_pct, e.oos_avg_ret, e.stage_filter
    FROM preds p
    INNER JOIN exit_pit e ON e.stock_code = p.stock_code
    ORDER BY p.score DESC
    LIMIT {core_slots}
    """
    core_rows = conn.execute(sql_core, [signal_date, model_id, signal_date]).fetchall()

    core_stocks = {r[0] for r in core_rows}
    core_sectors_pre = {}  # stock_code → sector (for sector penalty)

    # ============ Layer 2: 探索层 (non-PIT composite sub-rank top-M) ============
    keep_filter = sql_where_active_a_share("p.stock_code")

    # 拿 explore_pool_size 个 non-PIT (KEEP - core) 内 ml score 最高的 (做 sub-rank 池)
    core_stocks_clause = ", ".join(f"'{s}'" for s in core_stocks) if core_stocks else "''"
    sql_explore_pool = f"""
    SELECT p.stock_code, p.score
    FROM mart_p0b_oos_predictions p
    WHERE p.signal_date = ? AND p.model_id = ?
      AND {keep_filter}
      AND p.stock_code NOT IN ({core_stocks_clause})
      AND p.stock_code NOT IN (
          SELECT stock_code FROM {exit_table}
          WHERE CAST(cutoff_date AS DATE) <= CAST(? AS DATE) AND n_traded >= 5
      )
    ORDER BY p.score DESC NULLS LAST
    LIMIT {explore_pool_size}
    """
    explore_pool = conn.execute(sql_explore_pool, [signal_date, model_id, signal_date]).fetchall()

    # composite sub-rank: ml_score normalized + liquidity (amount_20d) + stage (技术阶段)
    # 简化: 当前不查 stage/amount, 只用 ml score 作 explore 排序 (Phase 1c v1)
    # TODO Phase 1c v2: 加 fact_stock_quality_features 流动性 + fact_stock_technical_stage stage
    explore_top = explore_pool[:explore_slots]

    # ============ 输出 CandidateRow ============
    out: list[CandidateRow] = []

    # core layer (PIT)
    for r in core_rows:
        out.append(CandidateRow(
            stock_code=r[0],
            formula_id=r[2] or "ml_default",
            formula_variant=r[3] or "default",
            tier="ML_RANK_CORE",  # Phase 1c tier 标识
            score=float(r[1] or 0),
            expected_total_return=float(r[8]) if r[8] is not None else 0.0,
            optimal_hp=int(r[4] or 10),
            optimal_target_pct=float(r[5]) if r[5] is not None else None,
            optimal_stop_pct=float(r[6]) if r[6] is not None else None,
            optimal_trailing_pct=float(r[7]) if r[7] is not None else None,
            signal_close=0.0,
            sell_target=None,
            stop_price=None,
            stage=r[9],
            match_tier="ml_score_tier_core",
            exit_source="pit",  # core 层走 PIT, 不是 fallback
        ))

    # explore layer (non-PIT, composite sub-rank, 弱 default params 但 tier=explore)
    fp = fallback_params
    for r in explore_top:
        out.append(CandidateRow(
            stock_code=r[0],
            formula_id="explore_subrank",
            formula_variant="explore_default",
            tier="ML_RANK_EXPLORE",
            score=float(r[1] or 0),
            expected_total_return=0.0,  # no PIT 寻优 expected_ret
            optimal_hp=int(fp["hp"]),
            optimal_target_pct=float(fp["target_pct"]),
            optimal_stop_pct=float(fp["stop_pct"]),
            optimal_trailing_pct=float(fp["trailing_pct"]),
            signal_close=0.0,
            sell_target=None,
            stop_price=None,
            stage="unknown",
            match_tier="ml_score_tier_explore",
            exit_source="explore",  # Phase 1c 新 exit_source 值
        ))

    log.info(f"tiered loader: signal_date={signal_date} core={len(core_rows)} explore={len(explore_top)}")
    return out
