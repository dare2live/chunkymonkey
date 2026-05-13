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

import logging
from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import SelectionConfig
from services.paper_sim.swap_rules import Candidate
from services.portfolio_walk_forward.liquidity import (
    LiquidityConfig, passes_liquidity, round_to_lots,
)


TIER_RANK = {"NO_SIGNAL": 0, "WATCH": 1, "BUY": 2, "STRONG_BUY": 3}

log = logging.getLogger("paper_sim.selector")


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


def load_today_candidates_ensemble(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """Phase ψ.β.4 ensemble mode: 多 alpha 综合 zscore 排名 + regime gate.

    严格 PIT (Rule 9.1):
      - 每个 alpha 表 JOIN 时 WHERE pit_key <= signal_date
      - zscore 跨当日全市场算 (不偷看未来)
      - regime_gate 用 fact_regime_state PIT (trade_date <= signal_date)

    无 selection leakage — 因为 alpha 来源都是 PIT 时序数据 (β.1/β.2/β.3 backfill 过).
    用 cfg.default_holding 给所有候选用统一 hp/stop/target (ensemble 不调单股 params).
    """
    import math
    alphas = list(cfg.ensemble_alphas)
    if not alphas:
        return []

    # 1. 每个 alpha 拉当日 (stock_code → raw_value) — ASOF 最近 ≤ signal_date
    alpha_data: dict[str, dict[str, float]] = {}
    for a in alphas:
        name = a["name"]
        table = a["source_table"]
        col = a["source_col"]
        pit_key = a["pit_key"]
        extra_filter = a.get("filter", "")
        formula_filter = a.get("filter_formula_ids", [])
        # SQL: ASOF JOIN — 拿每股 ≤ signal_date 的最近一行 col 值
        # 简化版: 对每股查 MAX(pit_key) <= signal_date, 然后 JOIN 拿值
        filter_sql = ""
        params: list = [signal_date]
        if extra_filter:
            filter_sql += f" AND {extra_filter}"
        if formula_filter and "formula_id" in {c[1] for c in conn.execute(
            f"PRAGMA table_info('{table}')").fetchall()}:
            ph = ",".join(["?"] * len(formula_filter))
            filter_sql += f" AND formula_id IN ({ph})"
            params += list(formula_filter)
        sql = f"""
            WITH pit_max AS (
                SELECT stock_code, MAX({pit_key}) AS pit
                  FROM {table}
                 WHERE {pit_key} <= ?
                   {filter_sql}
                 GROUP BY stock_code
            )
            SELECT t.stock_code, t.{col}
              FROM {table} t
              JOIN pit_max p
                ON p.stock_code = t.stock_code
               AND p.pit = t.{pit_key}
             WHERE t.{col} IS NOT NULL
        """
        try:
            rows = conn.execute(sql, params).fetchall()
            alpha_data[name] = {r[0]: float(r[1]) for r in rows if r[1] is not None}
        except Exception as e:
            log.warning(f"  ensemble alpha {name} JOIN failed: {e}")
            alpha_data[name] = {}

    # 2. 算各 alpha 的 zscore (跨当日市场分布)
    alpha_zscore: dict[str, dict[str, float]] = {}
    for a in alphas:
        name = a["name"]
        direction = a.get("direction", 1)
        data = alpha_data[name]
        if not data:
            alpha_zscore[name] = {}
            continue
        vals = list(data.values())
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1)
        std = math.sqrt(var) if var > 0 else 1.0
        alpha_zscore[name] = {
            sc: ((v - mean) / std) * direction
            for sc, v in data.items()
        }

    # 3. 加权综合 score
    weight_sum = sum(a["weight"] for a in alphas)
    all_stocks: set = set()
    for d in alpha_data.values():
        all_stocks.update(d.keys())

    scores: dict[str, float] = {}
    for sc in all_stocks:
        score = 0.0
        n_alphas_present = 0
        for a in alphas:
            name = a["name"]
            z = alpha_zscore[name].get(sc)
            if z is None:
                continue
            score += (a["weight"] / weight_sum) * z
            n_alphas_present += 1
        # 至少需要一半 alpha 有值才信
        if n_alphas_present >= max(2, len(alphas) // 2):
            scores[sc] = score

    # 4. regime gate
    rg = cfg.regime_gate
    if rg.get("enabled"):
        try:
            regime_row = conn.execute(f"""
                SELECT regime_label FROM {rg.get('source_table', 'fact_regime_state')}
                 WHERE {rg.get('pit_key', 'trade_date')} <= ?
                 ORDER BY {rg.get('pit_key', 'trade_date')} DESC LIMIT 1
            """, [signal_date]).fetchone()
            if regime_row and regime_row[0]:
                regime = regime_row[0].lower()
                mul = rg.get(f"{regime}_multiplier", 1.0)
                scores = {sc: v * mul for sc, v in scores.items()}
        except Exception as e:
            log.warning(f"  regime gate failed: {e}")

    # 5. 取 top N (按 score DESC), 用 default_holding 给统一 params
    dh = cfg.default_holding
    sorted_scores = sorted(scores.items(), key=lambda kv: -kv[1])

    # tier 简化: top 50% → STRONG_BUY, top 50-90% → BUY, 其余跳
    n_total = len(sorted_scores)
    n_strong = max(1, n_total // 10)   # 前 10% STRONG_BUY
    n_buy    = max(n_strong, n_total // 3)   # 前 33% BUY

    out: list[CandidateRow] = []
    for i, (sc, score) in enumerate(sorted_scores):
        if i < n_strong:
            tier = "STRONG_BUY"
        elif i < n_buy:
            tier = "BUY"
        else:
            break
        if cfg.min_tier_to_buy == "STRONG_BUY" and tier != "STRONG_BUY":
            continue
        out.append(CandidateRow(
            stock_code=sc,
            formula_id="ensemble",
            formula_variant="ensemble",
            tier=tier,
            score=float(score),
            expected_total_return=float(dh.get("target_pct", 0.20)),
            optimal_hp=int(dh.get("hp", 15)),
            optimal_target_pct=float(dh.get("target_pct", 0.20)),
            optimal_stop_pct=float(dh.get("stop_pct", -0.10)),
            optimal_trailing_pct=float(dh.get("trailing_pct", 0.05)),
            signal_close=0.0,
            sell_target=None, stop_price=None,
            stage=None, match_tier="ensemble",
        ))
    return out


def load_today_candidates_dispatch(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """根据 cfg.mode 分发到 production / backtest / ensemble loader."""
    if cfg.mode == "ensemble":
        return load_today_candidates_ensemble(conn, signal_date, cfg)
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
