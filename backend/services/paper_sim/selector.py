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
import math
from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import SelectionConfig
from services.paper_sim.swap_rules import Candidate
from services.portfolio_walk_forward.liquidity import (
    LiquidityConfig, passes_liquidity, round_to_lots,
)


TIER_RANK = {"NO_SIGNAL": 0, "WATCH": 1, "BUY": 2, "STRONG_BUY": 3}


def _load_per_stock_stage_optimal(conn, stock_stage_pairs: list[tuple[str, str]],
                                    min_n_traded: int = 5) -> dict[tuple[str, str], dict]:
    """Phase ψ.γ.2: 批量加载 (stock × stage) 的 best params from
    mart_per_stock_stage_strategy_optimal (Phase ψ 9 维 Optuna OOS 产物).

    选 row 规则: 每 (stock × stage) 取 oos_sharpe DESC 第一行, 跨 formula 取 best.
    过滤: oos_n_traded >= min_n_traded (避免少 trade 数据噪音).

    Rule 8: 只读 oos_* 字段, 不读 in-sample fit.
    Rule 7: stock_code × stage_filter (PIT — stage 是 signal_date 当天的, 不是事后).

    Returns: {(stock_code, stage_filter): {hp, stop, target, trailing, source_formula}}.
    """
    if not stock_stage_pairs:
        return {}
    # 构 IN 列表 — DuckDB 不直接支持 tuple IN, 用 OR 拼或临时 table
    # 简化: 在 Python 层 group by stage, 多次 query (stage 数 ≤ ~5)
    by_stage: dict[str, list[str]] = {}
    for sc, st in stock_stage_pairs:
        if st:
            by_stage.setdefault(str(st), []).append(sc)
    out: dict[tuple[str, str], dict] = {}
    for stage, codes in by_stage.items():
        if not codes:
            continue
        qs = ",".join("?" * len(codes))
        try:
            rows = conn.execute(f"""
                WITH ranked AS (
                    SELECT stock_code, formula_id, formula_variant,
                           optimal_hp, optimal_stop_pct, optimal_target_pct,
                           optimal_trailing_pct, oos_sharpe, oos_n_traded,
                           ROW_NUMBER() OVER (
                             PARTITION BY stock_code
                             ORDER BY oos_sharpe DESC NULLS LAST, oos_n_traded DESC
                           ) AS rk
                      FROM mart_per_stock_stage_strategy_optimal
                     WHERE stage_filter = ?
                       AND stock_code IN ({qs})
                       AND oos_sharpe IS NOT NULL
                       AND oos_n_traded >= ?
                )
                SELECT stock_code, formula_id, optimal_hp,
                       optimal_stop_pct, optimal_target_pct, optimal_trailing_pct
                  FROM ranked
                 WHERE rk = 1
            """, [stage, *codes, min_n_traded]).fetchall()
            for r in rows:
                sc = r[0]
                out[(sc, stage)] = {
                    "hp": int(r[2]),
                    "stop_pct": float(r[3]),
                    "target_pct": float(r[4]),
                    "trailing_pct": float(r[5]),
                    "source_formula": str(r[1]),
                }
        except Exception as e:
            log.warning(f"  per_stock_stage load failed for stage={stage}: {e}")
    return out


def _vol_aware_params(vol_60d_annualized: Optional[float], hp_days: int,
                       va: dict, default_stop: float, default_target: float,
                       default_trailing: float) -> tuple[float, float, float]:
    """L2 vol-aware: 按 vol_60d 缩放 stop/target/trailing.

    Rule 6 (数据驱动): vol_60d 从 fact_risk_factors.vol_60d PIT 来, hp_days × sigma scaling.
    Rule 9.1 真金白银: clip 到 [stop_min, stop_max] 等 hard bounds — 防极端 vol 估算失真.

    Args:
        vol_60d_annualized: PIT vol_60d (年化 std), None 则返回默认值
        hp_days: 持仓周期 (trading days)
        va: vol_aware 配置 dict (sigma 倍数 + min/max bounds)
        default_*: vol 缺失时的兜底默认值
    Returns:
        (stop_pct, target_pct, trailing_pct) — stop 是负数, target/trailing 是正数
    """
    if not va.get("enabled") or not vol_60d_annualized or vol_60d_annualized <= 0:
        return default_stop, default_target, default_trailing
    # 年化 -> hp 日 std
    sigma_hp = vol_60d_annualized * math.sqrt(max(hp_days, 1) / 252.0)
    stop_sigma     = float(va.get("stop_sigma", 2.0))
    target_sigma   = float(va.get("target_sigma", 3.0))
    trailing_sigma = float(va.get("trailing_sigma", 1.0))
    raw_stop     = -stop_sigma * sigma_hp
    raw_target   = +target_sigma * sigma_hp
    raw_trailing = +trailing_sigma * sigma_hp
    # clip
    stop_min     = float(va.get("stop_min",     -0.20))   # 最宽 stop (最负)
    stop_max     = float(va.get("stop_max",     -0.05))   # 最紧 stop (最接近 0)
    target_min   = float(va.get("target_min",    0.10))
    target_max   = float(va.get("target_max",    0.35))
    trailing_min = float(va.get("trailing_min",  0.03))
    trailing_max = float(va.get("trailing_max",  0.10))
    stop     = max(stop_min,     min(stop_max,     raw_stop))
    target   = max(target_min,   min(target_max,   raw_target))
    trailing = max(trailing_min, min(trailing_max, raw_trailing))
    return stop, target, trailing

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
    # Phase 1a Option C (Codex round 4 MAJOR): exit params 来源标识. 'pit' = INNER JOIN
    # mart_per_stock_stage_strategy_optimal_pit 拿到; 'fallback' = 缺 PIT, 走 ex-ante 弱 default.
    exit_source: str = "pit"


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
        # Phase ψ.β.align: 按 mart_per_formula_stage_optimal.oos_sharpe (PIT 干净) 排名
        # mart 表是 walk-forward 多行 (per train_end_date), JOIN WHERE train_end_date <= signal_date
        # → 该 oos_sharpe 在 signal_date 时刻**可知**, 不偷未来. 这才是实盘真实选股逻辑:
        #   "看每个 (stock × formula × stage) setup 的历史 OOS 多强, 跨公式可比".
        # 之前用 today_strength 是过度保守, 丢了主排名信号.
        # Tiebreaker: today_strength (公式当日触发强度).
        tier_mul = 1.5 if tier == "STRONG_BUY" else 1.0
        # 主 score = oos_sharpe × tier_mul (跨公式可比)
        # 加小量 today_strength 作 tiebreaker
        score = sharpe * tier_mul + 0.01 * today_strength
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

    # 3.5. Phase ψ.β.4.6: quality filter — 排除高波动 / 下跌趋势 stock
    qf = getattr(cfg, "ensemble_quality_filters", {}) or {}
    max_vol = qf.get("max_vol_60d")
    allowed_stages = set(qf.get("allowed_stages") or [])

    if max_vol is not None:
        try:
            vol_rows = conn.execute("""
                WITH pit_max AS (
                    SELECT stock_code, MAX(calc_date) AS pit
                      FROM fact_risk_factors
                     WHERE calc_date <= ?
                     GROUP BY stock_code
                )
                SELECT t.stock_code, t.vol_60d
                  FROM fact_risk_factors t
                  JOIN pit_max p ON p.stock_code = t.stock_code AND p.pit = t.calc_date
                 WHERE t.vol_60d IS NOT NULL AND t.vol_60d > ?
            """, [signal_date, max_vol]).fetchall()
            high_vol_stocks = {r[0] for r in vol_rows}
            n_before = len(scores)
            scores = {sc: v for sc, v in scores.items() if sc not in high_vol_stocks}
            log.debug(f"  quality vol filter: {n_before} -> {len(scores)} (高 vol 剔 {len(high_vol_stocks)})")
        except Exception as e:
            log.warning(f"  quality vol filter failed: {e}")

    # stage_map 无条件 load (P2 per-stock-stage 也需要), 之后 quality filter 用 + L2/L3 接入用
    stage_map: dict[str, str] = {}
    try:
        stage_rows = conn.execute("""
            SELECT stock_code, technical_stage
              FROM fact_signal_context
             WHERE date = ?
        """, [signal_date]).fetchall()
        stage_map = {r[0]: str(r[1]) for r in stage_rows if r[1] is not None}
    except Exception as e:
        log.warning(f"  stage_map load failed: {e}")

    if allowed_stages:
        n_before = len(scores)
        scores = {sc: v for sc, v in scores.items()
                  if stage_map.get(sc) in allowed_stages}
        log.debug(f"  quality stage filter: {n_before} -> {len(scores)}")

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

    # Phase ψ.β.5 L2 + Phase ψ.γ.2 per-stock-stage 优先级:
    #   per_stock_stage > vol_aware > default_holding
    # 都是 PIT 干净: per_stock_stage 用 mart_per_stock_stage_strategy_optimal (OOS-cleaned)
    # vol_aware 用 fact_risk_factors.vol_60d (WHERE calc_date <= signal_date PIT max)
    va = getattr(cfg, "vol_aware", {}) or {}
    pss_cfg = getattr(cfg, "per_stock_stage", {}) or {}
    final_codes = [sc for sc, _ in sorted_scores[:n_buy]]

    # 1. per-stock × stage 批量加载 (最高优先级)
    pss_params: dict[tuple[str, str], dict] = {}
    if pss_cfg.get("enabled") and final_codes:
        pairs = [(sc, stage_map.get(sc, "")) for sc in final_codes if stage_map.get(sc)]
        min_n_traded = int(pss_cfg.get("min_n_traded", 5))
        pss_params = _load_per_stock_stage_optimal(conn, pairs, min_n_traded=min_n_traded)
        log.debug(f"  P2 per_stock_stage: 命中 {len(pss_params)}/{len(pairs)} stock×stage")

    # 2. vol_aware 批量加载 vol_60d (次优先级, 用于 pss 没命中的)
    vol_pit: dict[str, float] = {}
    if va.get("enabled") and final_codes:
        try:
            qs = ",".join("?" * len(final_codes))
            vol_rows = conn.execute(f"""
                WITH pit_max AS (
                    SELECT stock_code, MAX(calc_date) AS pit
                      FROM fact_risk_factors
                     WHERE calc_date <= ? AND stock_code IN ({qs})
                     GROUP BY stock_code
                )
                SELECT t.stock_code, t.vol_60d
                  FROM fact_risk_factors t
                  JOIN pit_max p ON p.stock_code = t.stock_code AND p.pit = t.calc_date
                 WHERE t.vol_60d IS NOT NULL
            """, [signal_date, *final_codes]).fetchall()
            vol_pit = {r[0]: float(r[1]) for r in vol_rows}
            log.debug(f"  L2 vol_aware: 加载 {len(vol_pit)}/{len(final_codes)} 个 vol_60d")
        except Exception as e:
            log.warning(f"  L2 vol_aware fetch failed (fallback default): {e}")

    out: list[CandidateRow] = []
    hp_default = int(dh.get("hp", 15))
    def_stop     = float(dh.get("stop_pct",     -0.10))   # rule-compliance: ok evidence=yaml-default
    def_target   = float(dh.get("target_pct",    0.20))   # rule-compliance: ok evidence=yaml-default
    def_trailing = float(dh.get("trailing_pct",  0.05))   # rule-compliance: ok evidence=yaml-default
    n_pss_hit, n_va_hit, n_default = 0, 0, 0
    for i, (sc, score) in enumerate(sorted_scores):
        if i < n_strong:
            tier = "STRONG_BUY"
        elif i < n_buy:
            tier = "BUY"
        else:
            break
        if cfg.min_tier_to_buy == "STRONG_BUY" and tier != "STRONG_BUY":
            continue
        stage = stage_map.get(sc, "")
        # 优先级 1: per-stock × stage (mart_per_stock_stage_strategy_optimal)
        pss_p = pss_params.get((sc, stage)) if pss_cfg.get("enabled") else None
        if pss_p:
            hp_i        = pss_p["hp"]
            stop_pct    = pss_p["stop_pct"]
            target_pct  = pss_p["target_pct"]
            trailing_pct = pss_p["trailing_pct"]
            n_pss_hit += 1
        else:
            # 优先级 2: vol_aware (sigma × vol_60d, va.enabled=false 时返回 default)
            hp_i = hp_default
            stop_pct, target_pct, trailing_pct = _vol_aware_params(
                vol_pit.get(sc), hp_i, va, def_stop, def_target, def_trailing
            )
            if va.get("enabled") and vol_pit.get(sc):
                n_va_hit += 1
            else:
                n_default += 1
        out.append(CandidateRow(
            stock_code=sc,
            formula_id="ensemble",
            formula_variant="ensemble",
            tier=tier,
            score=float(score),
            expected_total_return=target_pct,
            optimal_hp=hp_i,
            optimal_target_pct=target_pct,
            optimal_stop_pct=stop_pct,
            optimal_trailing_pct=trailing_pct,
            signal_close=0.0,
            sell_target=None, stop_price=None,
            stage=stage or None, match_tier="ensemble",
        ))
    if pss_cfg.get("enabled") or va.get("enabled"):
        log.debug(f"  param source: pss={n_pss_hit} vol={n_va_hit} default={n_default}")
    return out


def load_today_candidates_dispatch(
    conn,
    signal_date: str,
    cfg: SelectionConfig,
) -> list[CandidateRow]:
    """根据 cfg.mode 分发到 production / backtest / ensemble / ml_score loader.

    ml_score (PLAN_V3 v3.2 P0c Option A): ML score 替换 selector ranking,
        从 mart_p0b_oos_predictions ORDER BY score DESC 取 top-K,
        exit/swap 仍走 Optuna 9-dim 公式. 见 services/paper_sim/ml_score_loader.
    """
    if cfg.mode == "ensemble":
        return load_today_candidates_ensemble(conn, signal_date, cfg)
    if cfg.mode == "backtest":
        return load_today_candidates_inline(conn, signal_date, cfg)
    if cfg.mode == "ml_score":
        # Lazy import 防循环 (ml_score_loader 依赖 CandidateRow from selector).
        from services.paper_sim.ml_score_loader import load_today_candidates_ml_score
        return load_today_candidates_ml_score(
            conn, signal_date,
            model_id=getattr(cfg, "ml_score_model_id", "lgbm_baseline_v1"),
            max_candidates=getattr(cfg, "ml_score_max_candidates", 30),
            min_score=getattr(cfg, "ml_score_min_score", None),
            # Phase 1a Option C (Codex round 4): propagate fallback flag/params
            fallback_enabled=getattr(cfg, "ml_score_fallback_enabled", False),
            fallback_params=getattr(cfg, "ml_score_fallback_params", None),
        )
    if cfg.mode == "hybrid":
        # Codex 7-day plan Day 6: hybrid blend (sequential filter + rank-linear).
        from services.paper_sim.hybrid_score_loader import load_today_candidates_hybrid
        return load_today_candidates_hybrid(
            conn, signal_date,
            model_id=getattr(cfg, "hybrid_model_id", "lgbm_baseline_v1"),
            max_candidates=getattr(cfg, "hybrid_max_candidates", 30),
            w_ml=getattr(cfg, "hybrid_w_ml", 0.20),
            q60_min_stage=getattr(cfg, "hybrid_q60_min_stage", True),
        )
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
