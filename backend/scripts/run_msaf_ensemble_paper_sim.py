#!/usr/bin/env python3
"""MSAF Phase 3.4: Ensemble paper_sim runner.

跑 3 类策略 ensemble + regime adaptive 加权的历史 paper_sim, 输出 KPI 跟 baseline 对比.

3 类输入:
- lambdamart_v6 (mart_p0b_oos_predictions 或 mart_p0b_lambdamart_v6_predictions)
- sniper confluence (mart_sniper_score_daily)
- 机构跟随 composite (mart_institution_score_daily)

For Phase 3.4:
- 用现有 lambdamart_v6 (Codex 2.1) 或 v4 ml_score 作输入
- sniper 读取 mart_sniper_score_daily.confluence_score 并归一化到 0..1
- institution 默认读取 mart_institution_score_daily.composite_score

Usage:
    PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py \
        --start 2024-07-01 --end 2026-04-13 --max-positions 5 --output mart_msaf_ensemble_kpi
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.strategies.ensemble import ensemble_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline
from services.strategies.regime.regime_state import RegimeVerdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("msaf_ensemble")

PREDICTION_TABLE_CANDIDATES = (
    "mart_p0b_oos_predictions",
    "mart_p0b_lambdamart_v6_predictions",
)


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def _prediction_row_counts(
    con: duckdb.DuckDBPyConnection,
    *,
    model_id: str,
    start_date: str,
    end_date: str,
) -> dict[str, int]:
    existing = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN (?, ?)",
            list(PREDICTION_TABLE_CANDIDATES),
        ).fetchall()
    }
    parts = [
        f"SELECT '{table_name}' AS table_name, COUNT(*) AS n_rows FROM {table_name} "
        "WHERE model_id = ? AND signal_date >= ? AND signal_date <= ?"
        for table_name in PREDICTION_TABLE_CANDIDATES
        if table_name in existing
    ]
    if not parts:
        return {}
    params = [
        value
        for _ in parts
        for value in (model_id, start_date, end_date)
    ]
    return {row[0]: int(row[1] or 0) for row in con.execute(" UNION ALL ".join(parts), params).fetchall()}


def load_lambdamart_predictions(
    db_path: str,
    model_id: str = "lgbm_20260517_governance_v1_20d",
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Load LambdaMART (or LGBM) predictions + fwd returns.

    predictions table fwd_cost_after_5d/10d 100% NULL (model 只训 20d) — JOIN p0a label panel
    取真 fwd_5d/10d (mirror run_phase4_gate_on_msaf.py:36-50 行为). evidence: 单 model lambdamart_v6
    仅 20d horizon, multi-horizon eval 必须 label panel JOIN.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        row_counts = _prediction_row_counts(
            con,
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
        )
        table_name = next((name for name in PREDICTION_TABLE_CANDIDATES if row_counts.get(name)), None)
        if table_name:
            df = con.execute(
                "SELECT p.signal_date, p.stock_code, p.score, "
                "       l.fwd_cost_after_5d, l.fwd_cost_after_10d, l.fwd_cost_after_20d "
                f"FROM {table_name} p "
                "LEFT JOIN mart_p0a_label_panel l "
                "  ON l.stock_code = p.stock_code AND l.signal_date = p.signal_date "
                "WHERE p.model_id = ? AND p.signal_date >= ? AND p.signal_date <= ? "
                "ORDER BY p.signal_date, p.stock_code",
                [model_id, start_date, end_date],
            ).fetchdf()
            df.attrs["prediction_table"] = table_name
            return df
        df = pd.DataFrame(
            columns=[
                "signal_date",
                "stock_code",
                "score",
                "fwd_cost_after_5d",
                "fwd_cost_after_10d",
                "fwd_cost_after_20d",
            ]
        )
        df.attrs["prediction_table"] = "none"
        return df
    finally:
        con.close()


def load_sniper_scores(
    db_path: str,
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Load sniper confluence scores normalized from 0..7 to 0..1."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, CAST(confluence_score AS DOUBLE) / 7.0 AS sniper_score "
            "FROM mart_sniper_score_daily "
            "WHERE signal_date >= ? AND signal_date <= ? "
            "ORDER BY signal_date, stock_code",
            [start_date, end_date],
        ).fetchdf()
        if not df.empty:
            df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.normalize()
        return df
    finally:
        con.close()


def load_institution_scores(
    db_path: str,
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Load institution composite scores from mart_institution_score_daily."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, "
            "       CAST(composite_score AS DOUBLE) AS inst_score "
            "FROM mart_institution_score_daily "
            "WHERE signal_date >= ? AND signal_date <= ? "
            "  AND composite_score IS NOT NULL "
            "ORDER BY signal_date, stock_code",
            [start_date, end_date],
        ).fetchdf()
        if not df.empty:
            df["signal_date"] = pd.to_datetime(df["signal_date"]).dt.normalize()
        return df
    finally:
        con.close()


def compute_kpi(
    results: list[dict],
    preds: pd.DataFrame,
    horizon: str = "20d",
    *,
    target_ann_vol: float | None = None,
    vol_window: int = 3,
    min_exposure: float = 0.0,
    max_exposure: float = 1.0,
    rank_decay: float | None = None,
) -> dict:
    """计算 portfolio KPI from ensemble top-K + fwd return.

    Args:
        results: list of dict from main loop (含 signal_date + top_k_codes)
        preds: DataFrame with signal_date, stock_code, fwd_cost_after_5d/10d/20d
        horizon: '5d', '10d', '20d' (= rebal_period)

    Returns:
        {ann_ret, max_dd, sharpe, n_obs, hit_rate, mean_ret, std_ret}

    Note: rebal_freq = horizon (non-overlapping). 每 N trading days 取 1 obs.
    horizon=20d 432 dates → 21 non-overlap monthly obs.
    annualize_factor = 252/N (1.0 for 20d if treated as 12 obs/year).

    rule-compliance: ok evidence=p0a-label-fwd-cost-after-N-d
    """
    fwd_col = f"fwd_cost_after_{horizon}"
    n_days = int(horizon.rstrip("d"))
    annualize_factor = 252 / n_days  # rule-compliance: ok evidence=trading-days-per-year

    # Map (signal_date, stock_code) → fwd
    fwd_map = preds.set_index(["signal_date", "stock_code"])[fwd_col].to_dict()

    # Non-overlapping rebal: 每 n_days trading days 取 1 obs
    rebal_results = results[::n_days]

    obs: list[float] = []
    raw_obs: list[float] = []
    exposures: list[float] = []
    n_skip = 0
    for r in rebal_results:
        sd_key = pd.Timestamp(r["signal_date"]).normalize()
        # ensemble top_k_codes — equal/rank-decay weight, drop NaN
        rets = []
        for code in r["top_k_codes"]:
            v = fwd_map.get((sd_key, code))
            if v is not None and pd.notna(v):
                rets.append(float(v))
        if not rets:
            n_skip += 1
            continue
        # Cash 占比 (bear regime 60% cash)
        cash_pct = r.get("cash_pct", 0.0)
        # Equity portfolio return
        equity_ret = weighted_return_by_rank(rets, rank_decay=rank_decay)
        # Total portfolio (cash 收益 0)
        port_ret = (1 - cash_pct) * equity_ret
        raw_obs.append(port_ret)

        exposure = 1.0
        if target_ann_vol is not None and target_ann_vol > 0 and len(obs) >= max(2, vol_window):
            trailing = np.array(obs[-vol_window:], dtype=float)
            realized_period_vol = float(trailing.std(ddof=1))
            target_period_vol = float(target_ann_vol) / (annualize_factor ** 0.5)
            if realized_period_vol > 1e-12:
                exposure = target_period_vol / realized_period_vol
        exposure = float(min(max_exposure, max(min_exposure, exposure)))
        exposures.append(exposure)
        obs.append(exposure * port_ret)

    if not obs:
        return {"ann_ret": None, "max_dd": None, "sharpe": None, "n_obs": 0,
                "n_skip": n_skip, "horizon": horizon}

    obs_arr = np.array(obs)
    raw_obs_arr = np.array(raw_obs)
    exposure_arr = np.array(exposures)
    mean_ret = obs_arr.mean()
    std_ret = obs_arr.std(ddof=1)
    sharpe = (mean_ret / std_ret * (annualize_factor ** 0.5)) if std_ret > 1e-12 else None

    # NAV / max_dd (cumulative product) — non-overlap rebal so 真 compound
    nav = (1 + obs_arr).cumprod()
    running_max = pd.Series(nav).cummax()
    drawdown = (pd.Series(nav) - running_max) / running_max
    max_dd = float(drawdown.min())

    # CAGR (compound annual) — 真实复利, 不是 arithmetic mean*12
    nav_end = float(nav[-1])
    n_years = len(obs_arr) / annualize_factor
    cagr = (nav_end ** (1 / n_years) - 1) if (nav_end > 0 and n_years > 0) else None
    # Arithmetic mean ann_ret (兼容)
    ann_ret_arith = mean_ret * annualize_factor

    hit_rate = float((obs_arr > 0).sum() / len(obs_arr))

    # Robust stats: median + trimmed mean (剔 top/bottom 10% 防 outlier 抬指标)
    median_ret = float(np.median(obs_arr))
    # Trimmed mean (10% each side)
    n_trim = max(1, len(obs_arr) // 10) if len(obs_arr) >= 10 else 0
    if n_trim > 0:
        sorted_obs = np.sort(obs_arr)
        trimmed = sorted_obs[n_trim:-n_trim]
        trimmed_mean = float(trimmed.mean()) if len(trimmed) > 0 else float(mean_ret)
    else:
        trimmed_mean = float(mean_ret)
    median_ann = median_ret * annualize_factor  # rule-compliance: ok evidence=annualize-from-period
    trimmed_ann = trimmed_mean * annualize_factor  # rule-compliance: ok evidence=annualize-from-period

    return {
        "ann_ret_cagr": float(cagr) if cagr is not None else None,  # 主指标 (compound)
        "ann_ret_arith": float(ann_ret_arith),  # arithmetic mean × annualize
        "ann_ret_median": float(median_ann),  # robust median × annualize (Rule 5 anti-outlier)
        "ann_ret_trimmed10": float(trimmed_ann),  # 剔 top/bottom 10% 后 mean × annualize
        "max_dd": max_dd,
        "sharpe": float(sharpe) if sharpe is not None else None,
        "n_obs": len(obs_arr),
        "n_skip": n_skip,
        "n_years": float(n_years),
        "horizon": horizon,
        "mean_ret_per_period": float(mean_ret),
        "median_ret_per_period": median_ret,
        "std_ret_per_period": float(std_ret),
        "hit_rate": hit_rate,
        "nav_end": nav_end,
        "raw_mean_ret_per_period": float(raw_obs_arr.mean()),
        "rank_decay": float(rank_decay) if rank_decay is not None else None,
        "target_ann_vol": float(target_ann_vol) if target_ann_vol is not None else None,
        "vol_window": int(vol_window),
        "min_exposure": float(min_exposure),
        "max_exposure": float(max_exposure),
        "avg_exposure": float(exposure_arr.mean()),
        "min_realized_exposure": float(exposure_arr.min()),
        "max_realized_exposure": float(exposure_arr.max()),
    }


import numpy as np  # noqa: E402


def weighted_return_by_rank(returns: list[float], *, rank_decay: float | None = None) -> float:
    """Return rank-ordered weighted mean; default preserves equal-weight behavior."""
    if not returns:
        return 0.0
    if rank_decay is None:
        return float(sum(returns) / len(returns))
    decay = float(rank_decay)
    if decay <= 0:
        raise ValueError("rank_decay must be > 0")
    weights = [decay ** idx for idx, _ in enumerate(returns)]
    total_weight = sum(weights)
    if total_weight <= 1e-12:
        return float(sum(returns) / len(returns))
    return float(sum(ret * weight for ret, weight in zip(returns, weights)) / total_weight)


def apply_cash_overlay(
    *,
    regime_state: str,
    base_cash_pct: float,
    bull_cash_pct: float | None = None,
    neutral_cash_pct: float | None = None,
    bear_cash_pct: float | None = None,
) -> float:
    """Apply opt-in portfolio cash overlays without changing stock ranking."""
    overlays = {
        "bull": bull_cash_pct,
        "neutral": neutral_cash_pct,
        "bear": bear_cash_pct,
    }
    overlay = overlays.get(regime_state)
    if overlay is None:
        return float(base_cash_pct)
    return float(min(1.0, max(base_cash_pct, overlay)))


def apply_score_floor(
    *,
    top_k_codes: list[str],
    top_k_scores: list[float],
    cash_pct: float,
    min_top_score: float | None = None,
) -> tuple[list[str], list[float], float, int]:
    """Drop low-conviction picks and leave the unused slot budget in cash."""
    if min_top_score is None:
        return top_k_codes, top_k_scores, float(cash_pct), 0
    kept = [
        (code, float(score))
        for code, score in zip(top_k_codes, top_k_scores)
        if float(score) >= min_top_score
    ]
    dropped = len(top_k_codes) - len(kept)
    if not top_k_codes:
        return [], [], 1.0, dropped
    if not kept:
        return [], [], 1.0, dropped
    original_equity_pct = 1.0 - float(cash_pct)
    kept_equity_pct = original_equity_pct * (len(kept) / len(top_k_codes))
    adjusted_cash_pct = 1.0 - kept_equity_pct
    return (
        [code for code, _ in kept],
        [score for _, score in kept],
        float(min(1.0, max(cash_pct, adjusted_cash_pct))),
        dropped,
    )


def apply_sniper_floor(
    *,
    top_k_codes: list[str],
    top_k_scores: list[float],
    sniper_scores: pd.Series | None,
    cash_pct: float,
    min_sniper_score: float | None = None,
) -> tuple[list[str], list[float], float, int]:
    """Drop picks without enough sniper confluence and leave unused slots in cash."""
    if min_sniper_score is None:
        return top_k_codes, top_k_scores, float(cash_pct), 0
    if not top_k_codes:
        return [], [], 1.0, 0
    if sniper_scores is None:
        return [], [], 1.0, len(top_k_codes)
    threshold = float(min_sniper_score)
    kept = []
    for code, score in zip(top_k_codes, top_k_scores):
        value = sniper_scores.get(code)
        if value is not None and pd.notna(value) and float(value) >= threshold:
            kept.append((code, float(score)))
    dropped = len(top_k_codes) - len(kept)
    if not kept:
        return [], [], 1.0, dropped
    original_equity_pct = 1.0 - float(cash_pct)
    kept_equity_pct = original_equity_pct * (len(kept) / len(top_k_codes))
    adjusted_cash_pct = 1.0 - kept_equity_pct
    return (
        [code for code, _ in kept],
        [score for _, score in kept],
        float(min(1.0, max(cash_pct, adjusted_cash_pct))),
        dropped,
    )


def apply_score_exposure(
    *,
    top_k_scores: list[float],
    cash_pct: float,
    score_exposure_floor: float | None = None,
    score_exposure_ceiling: float | None = None,
    score_min_exposure: float = 0.0,
) -> tuple[float, float, float | None]:
    """Smoothly scale equity exposure by top-K conviction without dropping picks."""
    if score_exposure_floor is None:
        return float(cash_pct), 1.0, None
    if not top_k_scores:
        return 1.0, 0.0, None

    floor = float(score_exposure_floor)
    ceiling = float(score_exposure_ceiling if score_exposure_ceiling is not None else floor)
    min_exposure = float(min(1.0, max(0.0, score_min_exposure)))
    avg_score = float(sum(float(s) for s in top_k_scores) / len(top_k_scores))

    if ceiling <= floor:
        multiplier = 1.0 if avg_score >= floor else min_exposure  # rule-compliance: ok evidence=degenerate-range-binary-fallback (退化区间 [floor,ceiling] 不可缩放, score≥floor 给全仓, 否则给 min_exposure)
    else:
        scaled = (avg_score - floor) / (ceiling - floor)
        multiplier = min_exposure + (1.0 - min_exposure) * min(1.0, max(0.0, scaled))

    original_equity_pct = 1.0 - float(cash_pct)
    adjusted_cash_pct = 1.0 - original_equity_pct * multiplier
    return float(min(1.0, max(cash_pct, adjusted_cash_pct))), float(multiplier), avg_score


def apply_source_weight_override(
    regime: RegimeVerdict,
    *,
    lambdamart_weight: float | None = None,
    sniper_weight: float | None = None,
    institution_weight: float | None = None,
) -> RegimeVerdict:
    """Override source weights for opt-in probes while preserving regime cash."""
    if lambdamart_weight is None and sniper_weight is None and institution_weight is None:
        return regime

    cash = float(regime.weights.get("cash", 0.0))
    available_source_weight = max(0.0, 1.0 - cash)
    source_weights = {
        "lambdamart": float(lambdamart_weight if lambdamart_weight is not None else regime.weights.get("lambdamart", 0.0)),
        "sniper": float(sniper_weight if sniper_weight is not None else regime.weights.get("sniper", 0.0)),
        "institution": float(institution_weight if institution_weight is not None else regime.weights.get("institution", 0.0)),
    }
    total = sum(max(0.0, w) for w in source_weights.values())
    if total <= 1e-12 or available_source_weight <= 1e-12:
        weights = {"lambdamart": 0.0, "sniper": 0.0, "institution": 0.0, "cash": cash}
    else:
        weights = {
            name: max(0.0, weight) / total * available_source_weight
            for name, weight in source_weights.items()
        }
        weights["cash"] = cash
    return replace(regime, weights=weights)


def main() -> int:
    parser = argparse.ArgumentParser(description="MSAF ensemble paper_sim runner")
    parser.add_argument("--start", default="2024-07-01")  # rule-compliance: ok evidence=p0b-walk-forward-起始
    parser.add_argument("--end", default="2026-04-13")    # rule-compliance: ok evidence=panel-cutoff
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--lambdamart-model-id", default="lgbm_20260517_governance_v1_20d")
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compute-kpi", action="store_true", help="计算 portfolio KPI (ann/max_dd/sharpe)")
    parser.add_argument("--horizon", default="20d", choices=["5d", "10d", "20d"])
    parser.add_argument("--no-institution", action="store_true",
                        help="Disable mart_institution_score_daily composite for ablation (also default off)")
    parser.add_argument("--with-institution", action="store_true",
                        help="Enable mart_institution_score_daily composite (默认 OFF 实测 dilute lambdamart; Phase 5 调优后再 default ON)")
    parser.add_argument("--bull-cash-pct", type=float, default=None,
                        help="Opt-in risk overlay: minimum cash pct in bull regime; default keeps existing regime cash")
    parser.add_argument("--neutral-cash-pct", type=float, default=None,
                        help="Opt-in risk overlay: minimum cash pct in neutral regime; default keeps existing regime cash")
    parser.add_argument("--bear-cash-pct", type=float, default=None,
                        help="Opt-in risk overlay: minimum cash pct in bear regime; default keeps existing regime cash")
    parser.add_argument("--target-ann-vol", type=float, default=None,
                        help="Opt-in volatility targeting using only prior non-overlap realized returns")
    parser.add_argument("--vol-window", type=int, default=3,
                        help="Trailing non-overlap observations used for --target-ann-vol")
    parser.add_argument("--min-exposure", type=float, default=0.0,
                        help="Minimum exposure for volatility targeting")
    parser.add_argument("--max-exposure", type=float, default=1.0,
                        help="Maximum exposure for volatility targeting; keep <=1 for long-only no leverage")
    parser.add_argument("--min-top-score", type=float, default=None,
                        help="Opt-in conviction filter: drop ensemble picks below this score and keep unused slots in cash")
    parser.add_argument("--min-sniper-score", type=float, default=None,
                        help="Opt-in source filter: drop selected picks whose normalized sniper score is below this threshold")
    parser.add_argument("--score-exposure-floor", type=float, default=None,
                        help="Opt-in smooth conviction sizing: floor for top-K average score before scaling exposure")
    parser.add_argument("--score-exposure-ceiling", type=float, default=None,
                        help="Opt-in smooth conviction sizing: score where full exposure resumes")
    parser.add_argument("--score-min-exposure", type=float, default=0.0,
                        help="Minimum equity exposure multiplier for --score-exposure-floor")
    parser.add_argument("--rank-decay", type=float, default=None,
                        help="Opt-in rank-decay position sizing; 1.0 equals equal weight, lower values are more top-heavy")
    parser.add_argument("--lambdamart-weight", type=float, default=None,
                        help="Opt-in source-weight probe: raw LambdaMART weight, normalized with other source weights")
    parser.add_argument("--sniper-weight", type=float, default=None,
                        help="Opt-in source-weight probe: raw sniper weight, normalized with other source weights")
    parser.add_argument("--institution-weight", type=float, default=None,
                        help="Opt-in source-weight probe: raw institution weight, normalized with other source weights")
    args = parser.parse_args()

    log.info(f"=== MSAF Ensemble paper_sim {args.start} → {args.end} ===")

    # 1. Load HS300 K-line for regime
    log.info("Loading HS300 K-line...")
    hs300 = load_hs300_kline(args.market_db)
    log.info(f"  HS300: {len(hs300)} rows, {hs300.iloc[0]['date']} → {hs300.iloc[-1]['date']}")

    # 2. Load LambdaMART predictions
    log.info(f"Loading predictions: model_id={args.lambdamart_model_id}")
    preds = load_lambdamart_predictions(
        args.smartmoney_db, args.lambdamart_model_id, args.start, args.end
    )
    prediction_table = preds.attrs.get("prediction_table", "mart_p0b_oos_predictions")
    if preds.empty:
        raise RuntimeError(
            f"No predictions found for model_id={args.lambdamart_model_id} "
            f"between {args.start} and {args.end}"
        )
    log.info(f"  predictions: {len(preds):,} rows, {preds['signal_date'].min()} → {preds['signal_date'].max()}")
    log.info(f"  prediction_table: {prediction_table}")

    # 2b. Load sniper score (Phase 3.4)
    log.info("Loading sniper confluence scores...")
    sniper_df = load_sniper_scores(args.smartmoney_db, args.start, args.end)
    sniper_by_sd = {
        sd: g.set_index("stock_code")["sniper_score"]
        for sd, g in sniper_df.groupby("signal_date", sort=False)
    }
    log.info(f"  sniper scores: {len(sniper_df):,} rows, {len(sniper_by_sd):,} signal_dates")

    # 2c. Load institution score (default OFF per commit a10131f9 实测 KPI dilute)
    # 实测 LM+sniper+inst: median -9.76% / max_dd -39.08% (vs LM+sniper +48.40% / -24.28%)
    # 待 Phase 5 Optuna 联合调优 regime weights (institution cap 20%) 后 default ON.
    inst_df = pd.DataFrame()
    if args.with_institution and not args.no_institution:
        log.info("Loading institution composite scores (--with-institution opt-in)...")
        inst_df = load_institution_scores(args.smartmoney_db, args.start, args.end)
        log.info(f"  institution scores: {len(inst_df):,} rows")
    else:
        log.info("Institution scores SKIPPED (default OFF, opt-in --with-institution; see docs/strategy_validation_contract.md)")

    # 3. Loop daily signals
    signal_dates = preds["signal_date"].drop_duplicates().tolist()
    log.info(f"  unique signal_dates: {len(signal_dates)}")

    # Pre-build per-signal-date institution map
    inst_by_sd = {
        sd: g.set_index("stock_code")["inst_score"]
        for sd, g in inst_df.groupby("signal_date", sort=False)
    } if len(inst_df) else {}

    results: list[dict] = []
    regime_counts = {"bull": 0, "neutral": 0, "bear": 0, "crash": 0}
    for sd in signal_dates:
        sd_str = str(sd)[:10]
        try:
            regime = compute_regime_state(sd_str, hs300)
        except ValueError as e:
            log.warning(f"  {sd_str}: skip — {e}")
            continue
        regime = apply_source_weight_override(
            regime,
            lambdamart_weight=args.lambdamart_weight,
            sniper_weight=args.sniper_weight,
            institution_weight=args.institution_weight,
        )
        regime_counts[regime.state] += 1

        # lambdamart scores for this signal_date
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]
        day_sniper = sniper_by_sd.get(pd.Timestamp(sd).normalize(), pd.Series(dtype=float))

        inst = inst_by_sd.get(pd.Timestamp(sd).normalize())

        verdict = ensemble_scores(
            signal_date=sd_str,
            regime=regime,
            lambdamart_scores=lam,
            sniper_scores=day_sniper,
            institution_scores=inst,
            max_positions=args.max_positions,
        )
        base_cash_pct = apply_cash_overlay(
            regime_state=verdict.regime_state,
            base_cash_pct=verdict.cash_pct,
            bull_cash_pct=args.bull_cash_pct,
            neutral_cash_pct=args.neutral_cash_pct,
            bear_cash_pct=args.bear_cash_pct,
        )
        top_codes, top_scores, cash_pct, dropped_by_score = apply_score_floor(
            top_k_codes=verdict.top_k_codes,
            top_k_scores=[float(s) for s in verdict.top_k_scores],
            cash_pct=base_cash_pct,
            min_top_score=args.min_top_score,
        )
        top_codes, top_scores, cash_pct, dropped_by_sniper = apply_sniper_floor(
            top_k_codes=top_codes,
            top_k_scores=top_scores,
            sniper_scores=day_sniper,
            cash_pct=cash_pct,
            min_sniper_score=args.min_sniper_score,
        )
        cash_pct, score_exposure, avg_top_score = apply_score_exposure(
            top_k_scores=top_scores,
            cash_pct=cash_pct,
            score_exposure_floor=args.score_exposure_floor,
            score_exposure_ceiling=args.score_exposure_ceiling,
            score_min_exposure=args.score_min_exposure,
        )
        results.append({
            "signal_date": sd_str,
            "regime_state": verdict.regime_state,
            "cash_pct": cash_pct,
            "base_cash_pct": verdict.cash_pct,
            "n_positions": len(top_codes),
            "dropped_by_score": dropped_by_score,
            "dropped_by_sniper": dropped_by_sniper,
            "score_exposure": round(float(score_exposure), 4),
            "avg_top_score": round(float(avg_top_score), 4) if avg_top_score is not None else None,
            "top_k_codes": top_codes,
            "top_k_scores": [round(float(s), 4) for s in top_scores],
        })

    log.info(f"=== ensemble done ===")
    log.info(f"  signal_dates processed: {len(results)}")
    log.info(f"  regime distribution: {regime_counts}")
    score_exposure_values = [
        float(r["score_exposure"])
        for r in results
        if r.get("score_exposure") is not None
    ]
    score_exposure_summary = {
        "avg_score_exposure": float(np.mean(score_exposure_values)) if score_exposure_values else None,
        "min_score_exposure": float(np.min(score_exposure_values)) if score_exposure_values else None,
        "max_score_exposure": float(np.max(score_exposure_values)) if score_exposure_values else None,
    }

    # KPI compute
    kpi = None
    if args.compute_kpi:
        log.info(f"=== compute KPI (horizon={args.horizon}) ===")
        kpi = compute_kpi(
            results,
            preds,
            horizon=args.horizon,
            target_ann_vol=args.target_ann_vol,
            vol_window=args.vol_window,
            min_exposure=args.min_exposure,
            max_exposure=args.max_exposure,
            rank_decay=args.rank_decay,
        )
        log.info(f"  ann_ret (CAGR):     {kpi.get('ann_ret_cagr'):+.2%}" if kpi.get('ann_ret_cagr') else "  ann_ret_cagr: N/A")
        log.info(f"  ann_ret (arith):    {kpi.get('ann_ret_arith'):+.2%}" if kpi.get('ann_ret_arith') else "  ann_ret_arith: N/A")
        log.info(f"  ann_ret (median):   {kpi.get('ann_ret_median'):+.2%} robust" if kpi.get('ann_ret_median') is not None else "  ann_ret_median: N/A")
        log.info(f"  ann_ret (trim 10%): {kpi.get('ann_ret_trimmed10'):+.2%} robust" if kpi.get('ann_ret_trimmed10') is not None else "  ann_ret_trim10: N/A")
        log.info(f"  max_dd:  {kpi.get('max_dd'):+.2%}" if kpi.get('max_dd') else "  max_dd:  N/A")
        log.info(f"  sharpe:  {kpi.get('sharpe'):.3f}" if kpi.get('sharpe') else "  sharpe:  N/A")
        log.info(f"  hit_rate: {kpi.get('hit_rate'):.2%}" if kpi.get('hit_rate') is not None else "  hit_rate: N/A")
        log.info(f"  n_obs: {kpi.get('n_obs')}, n_skip: {kpi.get('n_skip')}, n_years: {kpi.get('n_years'):.2f}")
        log.info(f"  NAV_end: {kpi.get('nav_end'):.4f}" if kpi.get('nav_end') else "")

    # Output
    if not args.dry_run:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "args": vars(args),
            "prediction_table": prediction_table,
            "cash_overlay": {
                "bull_cash_pct": args.bull_cash_pct,
                "neutral_cash_pct": args.neutral_cash_pct,
                "bear_cash_pct": args.bear_cash_pct,
            },
            "volatility_target": {
                "target_ann_vol": args.target_ann_vol,
                "vol_window": args.vol_window,
                "min_exposure": args.min_exposure,
                "max_exposure": args.max_exposure,
            },
            "score_filter": {
                "min_top_score": args.min_top_score,
                "min_sniper_score": args.min_sniper_score,
            },
            "score_exposure": {
                "score_exposure_floor": args.score_exposure_floor,
                "score_exposure_ceiling": args.score_exposure_ceiling,
                "score_min_exposure": args.score_min_exposure,
                **score_exposure_summary,
            },
            "position_sizing": {
                "rank_decay": args.rank_decay,
            },
            "source_weight_override": {
                "lambdamart_weight": args.lambdamart_weight,
                "sniper_weight": args.sniper_weight,
                "institution_weight": args.institution_weight,
            },
            "regime_counts": regime_counts,
            "n_signal_dates": len(results),
            "results": results[:10],  # first 10 sample, full results 太大不存
            "results_total": len(results),
            "kpi": kpi,
        }, indent=2, ensure_ascii=False, default=str))
        log.info(f"  saved: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
