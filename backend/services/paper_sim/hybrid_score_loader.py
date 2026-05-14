"""P0c-extended hybrid score loader — Codex 7-day plan Day 6 (PLAN_V3 v3.2 B 选项).

Codex Q5 sequential filter + rank-linear blend:
    s_ml    = 2 * pct_rank_by_date(ml_score)         - 1
    s_stage = 2 * pct_rank_by_date(stage_oos_sharpe) - 1
    eligible = tradable AND limit_mask=0 AND (NOT q60_min_stage OR stage_oos_sharpe >= q60_by_date)
    hybrid_score = (1 - w) * s_stage + w * s_ml

w grid (nested WF 选最优): {0, 0.10, 0.20, 0.30, 0.40}

**Codex 反馈处理 (CLAUDE §10 §10 三档反应 — 折中)**:
- 完全接受: rank-linear blend 公式 + sequential filter (q60 stage eligibility) + w grid 不用 Optuna
- 折中: stage_opt 用 mart_per_stock_stage_strategy_optimal **latest snapshot** (NOT PIT)
  跟 P0c only-ML 同妥协. Day 5 PIT walk-forward 重 Optuna 表 (mart_per_stock_stage_strategy_optimal_pit)
  待 v3 smoke RankIC 验证 ML alpha ≥ 0.025 后再做, 否则做无 value.
  ⚠ 历史回测注: latest snapshot 跟 signal_date 不对齐, 这是已知 leakage 风险.
  paper_sim 上线前必须切 PIT 表.

**为何不用 Optuna 搜 w**:
- w grid 5 个值 small, nested WF 直接遍历比 Optuna 更可控
- 防过拟合验证集 (Optuna 容易 overfit small param space)
- Codex Q5 推荐
"""
from __future__ import annotations

import logging

from services.paper_sim.selector import CandidateRow


log = logging.getLogger("paper_sim.hybrid_score_loader")


def load_today_candidates_hybrid(
    conn,
    signal_date: str,
    *,
    model_id: str = "lgbm_baseline_v1",
    max_candidates: int = 30,
    w_ml: float = 0.20,
    q60_min_stage: bool = True,
    exit_table: str = "mart_per_stock_stage_strategy_optimal",
) -> list[CandidateRow]:
    """Hybrid ML + stage_opt 排序 loader (Codex Q5 Day 6).

    Args:
        conn: smartmoney.duckdb 连接.
        signal_date: 'YYYY-MM-DD'.
        model_id: 用哪个 model 的 OOS predictions.
        max_candidates: 取前 K (默认 30).
        w_ml: ML 权重 (1-w_ml = stage 权重), w_ml ∈ [0, 0.4] grid.
        q60_min_stage: 默认 True — 仅取 stage_oos_sharpe >= q60 入候选池 (防弱 ML 挤掉强 stage).
        exit_table: 取 exit params 的表 (默认 stage-aware).

    Returns:
        list[CandidateRow] sorted by hybrid_score DESC.

    SQL 流程:
      1. ml_preds: mart_p0b_oos_predictions WHERE signal_date AND model_id
      2. stage_per_stock: exit_table 每 stock 取最优 row (COALESCE oos_sharpe / sharpe DESC, n_traded>=5)
      3. joined: ml × stage INNER (仅有 ml score 且 stage 寻优过的 stock)
      4. q60_filter: signal_date 截面 stage_oos_sharpe 60 分位
      5. eligible: q60_min_stage=True 时仅取 stage_oos_sharpe >= q60
      6. ranked: PERCENT_RANK over ORDER BY → s_ml, s_stage ∈ [-1, 1]
      7. hybrid_score = (1-w_ml) * s_stage + w_ml * s_ml
      8. ORDER BY hybrid_score DESC LIMIT max_candidates

    PIT 妥协 (CLAUDE §10 §7 真金白银 — 显式标):
      - mart_per_stock_stage_strategy_optimal 是 latest snapshot, NOT PIT
      - 历史 signal_date 用 latest oos_sharpe = 跟 P0c only-ML 同 leakage 风险
      - Day 5 PIT 表 (mart_per_stock_stage_strategy_optimal_pit) 验证 blend 有 value 后才做
    """
    if not (0.0 <= w_ml <= 1.0):
        raise ValueError(f"w_ml {w_ml} out of [0, 1]")
    # Codex MAJOR (a0b7c84f) 折中: 不强限 grid {0,0.10,0.20,0.30,0.40}, 但非 grid 值 log warning
    # 理由: 用户可能合理用 grid 外 w 微调 (e.g. nested WF 选 0.15), rigid 限制太僵
    _APPROVED_GRID = {0.0, 0.10, 0.20, 0.30, 0.40}
    if w_ml not in _APPROVED_GRID:
        log.warning(
            f"w_ml={w_ml} 不在 Codex Q5 推荐 grid {_APPROVED_GRID} — "
            "若用于 nested WF 选优请确保已 documented 偏离"
        )

    sql = f"""
    WITH ml_preds AS (
        SELECT stock_code, signal_date, score AS ml_score
        FROM mart_p0b_oos_predictions
        WHERE signal_date = ? AND model_id = ?
              AND score IS NOT NULL
    ),
    stage_per_stock AS (
        SELECT stock_code,
               COALESCE(oos_sharpe, sharpe) AS stage_oos_sharpe,
               formula_id, formula_variant,
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
    ),
    joined AS (
        SELECT m.stock_code, m.ml_score, s.stage_oos_sharpe,
               s.formula_id, s.formula_variant, s.oos_avg_ret, s.holding_days,
               s.optimal_target_pct, s.optimal_stop_pct, s.optimal_trailing_pct,
               s.stage_filter
        FROM ml_preds m
        INNER JOIN stage_per_stock s ON s.stock_code = m.stock_code
        WHERE s.stage_oos_sharpe IS NOT NULL
    ),
    q60_filter AS (
        SELECT QUANTILE_CONT(stage_oos_sharpe, 0.6) AS q60
        FROM joined
    ),
    eligible AS (
        SELECT j.*, q.q60
        FROM joined j CROSS JOIN q60_filter q
        WHERE (NOT ?::BOOLEAN) OR j.stage_oos_sharpe >= q.q60
    ),
    ranked AS (
        SELECT *,
               2.0 * PERCENT_RANK() OVER (ORDER BY ml_score) - 1.0          AS s_ml,
               2.0 * PERCENT_RANK() OVER (ORDER BY stage_oos_sharpe) - 1.0  AS s_stage
        FROM eligible
    )
    SELECT
        stock_code, ml_score, stage_oos_sharpe,
        s_ml, s_stage,
        (1.0 - ?) * s_stage + ? * s_ml AS hybrid_score,
        formula_id, formula_variant, oos_avg_ret, holding_days,
        optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
        stage_filter
    FROM ranked
    ORDER BY hybrid_score DESC NULLS LAST
    LIMIT ?
    """

    rows = conn.execute(
        sql,
        [signal_date, model_id, q60_min_stage, w_ml, w_ml, max_candidates],
    ).fetchall()

    out: list[CandidateRow] = []
    for r in rows:
        out.append(CandidateRow(
            stock_code=r[0],
            formula_id=r[6] or "ml_default",
            formula_variant=r[7] or "default",
            tier="HYBRID_ML_STAGE",
            score=float(r[5]) if r[5] is not None else 0.0,
            expected_total_return=float(r[8]) if r[8] is not None else 0.0,
            optimal_hp=int(r[9] or 10),
            optimal_target_pct=float(r[10]) if r[10] is not None else None,
            optimal_stop_pct=float(r[11]) if r[11] is not None else None,
            optimal_trailing_pct=float(r[12]) if r[12] is not None else None,
            signal_close=0.0,
            sell_target=None,
            stop_price=None,
            stage=r[13],
            match_tier=f"hybrid_w{w_ml:.2f}",
        ))

    log.info(
        f"hybrid_score loader: signal_date={signal_date} model_id={model_id} "
        f"w_ml={w_ml:.2f} q60_min={q60_min_stage} → {len(out)} candidates"
    )
    return out
