"""P0c ML score loader — paper_sim selector ranking 替换为 ML score (Option A).

PLAN_V3 v3.2 P0c 决策:
- selector ranking 用 ML score (from mart_p0b_oos_predictions) ORDER BY score DESC
- exit / swap 仍走 Optuna 9-dim 公式 (从 mart_per_stock_stage_strategy_optimal 取 best params)
- KEEP universe 守门 (services/universe.py)
- T+1 + 涨跌停 + 停牌 + 流动性过滤 由 paper_sim 引擎现有逻辑负责

为啥 Option A (而非 dynamic exit / probability threshold swap):
- 最小改造, 隔离"选股 alpha 是否成立"
- exit/swap 已经 Optuna 9-dim 寻优过, 不在 P0 同时重构
- P2 再做 A/B/C 对比 (selection-only vs dynamic exit vs prob swap)

返回结构: 跟现有 CandidateRow 兼容 (selector.py 同款), 这样下游 sizer / swap 不需要改.
"""
from __future__ import annotations

import logging

from services.paper_sim.selector import CandidateRow


log = logging.getLogger("paper_sim.ml_score_loader")


def load_today_candidates_ml_score(
    conn,
    signal_date: str,
    model_id: str = "lgbm_baseline_v1",
    max_candidates: int = 30,
    min_score: float | None = None,
    exit_table: str = "mart_per_stock_stage_strategy_optimal",
) -> list[CandidateRow]:
    """ML score 排序的候选 loader.

    SQL:
        SELECT top K from mart_p0b_oos_predictions ORDER BY score DESC
        LEFT JOIN mart_per_stock_stage_strategy_optimal (latest stage params)

    Args:
        conn: smartmoney.duckdb 连接.
        signal_date: 'YYYY-MM-DD'.
        model_id: 用哪个 model 的 OOS predictions.
        max_candidates: 取前 K (默认 30; P0c gate 不限制具体值, P2 由 composite 决定).
        min_score: 可选 score 下限 (默认 None = 不过滤).
        exit_table: 取 exit params 的 mart 表 (默认 stage-aware).

    Returns:
        list[CandidateRow] sorted by score DESC.

    PIT 保证 (Rule 7):
        mart_p0b_oos_predictions 上游 train_lightgbm_walkforward 已用
        split_expanding_monthly, predictions 是 OOS. 严禁读 in-sample fit 字段.
    """
    score_filter = f"AND score >= {float(min_score)}" if min_score is not None else ""

    # 联合 mart_p0b_oos_predictions (主排名) + exit params (mart_per_stock_stage_strategy_optimal).
    # exit params 按 stock × stage best avg_calmar 取一行 (v3.2 ψ.γ.1 9-dim).
    # 若 stage 未知则 stage_filter='cross' 兜底 (mart_per_stock_strategy_optimal 旧表).
    sql = f"""
    WITH preds AS (
        SELECT stock_code, signal_date, score,
               fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d
        FROM mart_p0b_oos_predictions
        WHERE signal_date = ? AND model_id = ?
              {score_filter}
        ORDER BY score DESC NULLS LAST
        LIMIT ?
    ),
    exit_params AS (
        -- 每 stock 取 best stage_filter (最高 oos_sharpe / sharpe)
        SELECT stock_code, formula_id, formula_variant,
               oos_avg_ret, holding_days,
               optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
               stage_filter
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY stock_code
                ORDER BY COALESCE(oos_sharpe, sharpe) DESC NULLS LAST
            ) AS rn
            FROM {exit_table}
            WHERE n_traded >= 5
        ) WHERE rn = 1
    )
    SELECT
        p.stock_code, p.signal_date, p.score,
        COALESCE(e.formula_id, 'ml_default')   AS formula_id,
        COALESCE(e.formula_variant, 'default') AS formula_variant,
        COALESCE(e.holding_days, 10)           AS optimal_hp,
        e.optimal_target_pct, e.optimal_stop_pct, e.optimal_trailing_pct,
        e.oos_avg_ret AS expected_total_return,
        e.stage_filter AS stage
    FROM preds p
    LEFT JOIN exit_params e
        ON e.stock_code = p.stock_code
    ORDER BY p.score DESC
    """
    rows = conn.execute(sql, [signal_date, model_id, max_candidates]).fetchall()

    out: list[CandidateRow] = []
    for r in rows:
        out.append(CandidateRow(
            stock_code=r[0],
            formula_id=r[3],
            formula_variant=r[4],
            tier="ML_RANK",            # ML 模式特定 tier; 跟 STRONG_BUY/BUY/WATCH 区分
            score=float(r[2] or 0),
            expected_total_return=float(r[9]) if r[9] is not None else 0.0,
            optimal_hp=int(r[5] or 10),
            optimal_target_pct=float(r[6]) if r[6] is not None else None,
            optimal_stop_pct=float(r[7]) if r[7] is not None else None,
            optimal_trailing_pct=float(r[8]) if r[8] is not None else None,
            signal_close=0.0,           # ML loader 不依赖 signal_close (paper_sim 引擎实时拿 K 线)
            sell_target=None,
            stop_price=None,
            stage=r[10],
            match_tier="ml_score",      # 跟 stage_aware / cross_stage_fallback 区分
        ))

    log.info(f"ml_score loader: signal_date={signal_date} model_id={model_id} "
             f"loaded {len(out)} candidates")
    return out
