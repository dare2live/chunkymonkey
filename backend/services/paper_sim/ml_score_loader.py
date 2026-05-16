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
    exit_table: str = "mart_per_stock_stage_strategy_optimal_pit",
    use_pit: bool = True,
    fallback_enabled: bool = False,
    fallback_params: dict | None = None,
) -> list[CandidateRow]:
    """ML score 排序的候选 loader (Codex C-A 改造 2026-05-15 — PIT-safe).

    SQL:
        SELECT top K from mart_p0b_oos_predictions ORDER BY score DESC
        ASOF JOIN mart_per_stock_stage_strategy_optimal_pit
                  WHERE cutoff_date <= signal_date (PIT-safe)

    Args:
        conn: smartmoney.duckdb 连接.
        signal_date: 'YYYY-MM-DD'.
        model_id: 用哪个 model 的 OOS predictions.
        max_candidates: 取前 K (默认 30).
        min_score: 可选 score 下限.
        exit_table: PIT 表 default mart_per_stock_stage_strategy_optimal_pit (Codex adc5b44520 D CRITICAL fix).
        use_pit: True 用 PIT ASOF cutoff_date<=signal_date. False 用 latest snapshot (deprecated, 含 leakage).

    Returns:
        list[CandidateRow] sorted by score DESC. Missing PIT row → 不入候选 (Codex C-A: no fallback to latest).

    PIT 保证 (Codex C-A):
        - mart_p0b_oos_predictions OOS predictions (上游 walk-forward)
        - mart_per_stock_stage_strategy_optimal_pit cutoff_date <= signal_date (Day 5 PIT)
        - 无 fallback latest snapshot, missing row 直接 drop (防 leakage)
    """
    score_filter = f"AND score >= {float(min_score)}" if min_score is not None else ""

    if use_pit:
        # PIT-safe ASOF JOIN (Codex C-A): cutoff_date <= signal_date 取最近, missing 不 fallback
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
        exit_params_pit AS (
            -- ASOF: per (stock × variant × stage) 取最近 cutoff_date <= signal_date 的 best (COALESCE oos_sharpe DESC)
            SELECT stock_code, formula_id, formula_variant,
                   oos_avg_ret, holding_days,
                   optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                   stage_filter, cutoff_date
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY stock_code, formula_variant, stage_filter
                    ORDER BY cutoff_date DESC,
                             COALESCE(oos_sharpe, sharpe) DESC NULLS LAST
                ) AS rn
                FROM {exit_table}
                WHERE CAST(cutoff_date AS DATE) <= CAST(? AS DATE)
                  AND n_traded >= 5
            ) WHERE rn = 1
        ),
        exit_per_stock AS (
            -- 每 stock 取 best (variant × stage) 一行
            SELECT stock_code, formula_id, formula_variant,
                   oos_avg_ret, holding_days,
                   optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                   stage_filter
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY stock_code ORDER BY oos_avg_ret DESC NULLS LAST
                ) AS rn
                FROM exit_params_pit
            ) WHERE rn = 1
        )
        SELECT
            p.stock_code, p.signal_date, p.score,
            e.formula_id, e.formula_variant,
            e.holding_days AS optimal_hp,
            e.optimal_target_pct, e.optimal_stop_pct, e.optimal_trailing_pct,
            e.oos_avg_ret AS expected_total_return,
            e.stage_filter AS stage
        FROM preds p
        INNER JOIN exit_per_stock e
            ON e.stock_code = p.stock_code
        ORDER BY p.score DESC
        """
        # Note: INNER JOIN — 没 PIT exit params 的 stock 不入候选 (Codex C-A no fallback, Option F)
        rows = conn.execute(sql, [signal_date, model_id, max_candidates, signal_date]).fetchall()
        # Phase 1a Option C (Codex round 3+4): fallback path 走 LEFT JOIN + ex-ante 弱 default
        if fallback_enabled:
            from services.universe import sql_where_active_a_share
            fp = fallback_params or {"hp": 10, "stop_pct": -0.08, "target_pct": 0.12, "trailing_pct": 0.08}
            keep_filter = sql_where_active_a_share("p.stock_code")
            sql_c = f"""
            WITH preds AS (
                SELECT stock_code, signal_date, score,
                       fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d
                FROM mart_p0b_oos_predictions
                WHERE signal_date = ? AND model_id = ?
                      {score_filter}
                ORDER BY score DESC NULLS LAST
                LIMIT ?
            ),
            exit_params_pit AS (
                SELECT stock_code, formula_id, formula_variant,
                       oos_avg_ret, holding_days,
                       optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                       stage_filter, cutoff_date
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY stock_code, formula_variant, stage_filter
                        ORDER BY cutoff_date DESC,
                                 COALESCE(oos_sharpe, sharpe) DESC NULLS LAST
                    ) AS rn
                    FROM {exit_table}
                    WHERE CAST(cutoff_date AS DATE) <= CAST(? AS DATE)
                      AND n_traded >= 5
                ) WHERE rn = 1
            ),
            exit_per_stock AS (
                SELECT stock_code, formula_id, formula_variant,
                       oos_avg_ret, holding_days,
                       optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                       stage_filter
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY stock_code ORDER BY oos_avg_ret DESC NULLS LAST
                    ) AS rn
                    FROM exit_params_pit
                ) WHERE rn = 1
            )
            SELECT
                p.stock_code, p.signal_date, p.score,
                COALESCE(e.formula_id, 'fallback')         AS formula_id,
                COALESCE(e.formula_variant, 'fallback')    AS formula_variant,
                COALESCE(e.holding_days, {int(fp["hp"])})  AS optimal_hp,
                COALESCE(e.optimal_target_pct,   {float(fp["target_pct"])})   AS optimal_target_pct,
                COALESCE(e.optimal_stop_pct,     {float(fp["stop_pct"])})     AS optimal_stop_pct,
                COALESCE(e.optimal_trailing_pct, {float(fp["trailing_pct"])}) AS optimal_trailing_pct,
                COALESCE(e.oos_avg_ret, 0.0) AS expected_total_return,
                COALESCE(e.stage_filter, 'unknown') AS stage,
                CASE WHEN e.stock_code IS NULL THEN 'fallback' ELSE 'pit' END AS exit_source
            FROM preds p
            LEFT JOIN exit_per_stock e ON e.stock_code = p.stock_code
            WHERE {keep_filter}
            ORDER BY p.score DESC
            """
            rows_c = conn.execute(sql_c, [signal_date, model_id, max_candidates, signal_date]).fetchall()
            # 复制 row + 加 exit_source (column 11) — 替换 F 的 INNER JOIN 结果
            rows = rows_c
            _n_fallback = sum(1 for r in rows if r[11] == "fallback")
            _n_pit = len(rows) - _n_fallback
            log.info(f"ml_score loader (Option C fallback): pit={_n_pit} fallback={_n_fallback} total={len(rows)}")
    else:
        # Legacy path: latest snapshot (含 leakage, 仅保留 backwards compat)
        log.warning("use_pit=False — latest snapshot 含 D CRITICAL leakage, 仅 backwards compat")
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
            SELECT stock_code, formula_id, formula_variant,
                   oos_avg_ret, holding_days,
                   optimal_target_pct, optimal_stop_pct, optimal_trailing_pct,
                   stage_filter
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY stock_code
                    ORDER BY COALESCE(oos_sharpe, sharpe) DESC NULLS LAST
                ) AS rn
                FROM mart_per_stock_stage_strategy_optimal
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
        # Phase 1a Option C: fallback path 多 1 列 exit_source (idx 11). F path 没该列, default 'pit'.
        _exit_source = r[11] if (len(r) > 11) else "pit"
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
            exit_source=_exit_source,   # Phase 1a Option C: 'pit' or 'fallback'
        ))

    log.info(f"ml_score loader: signal_date={signal_date} model_id={model_id} "
             f"loaded {len(out)} candidates")
    return out
