#!/usr/bin/env python3
"""MSAF Phase 3.3: Ensemble paper_sim runner.

跑 3 类策略 ensemble + regime adaptive 加权的历史 paper_sim, 输出 KPI 跟 baseline 对比.

3 类输入:
- lambdamart_v6 (mart_p0b_oos_predictions 或 mart_p0b_lambdamart_v6_predictions)
- sniper confluence (services.strategies.sniper, 当前 fallback 全 0)
- 机构跟随 composite (services.strategies.institution_follow, 当前 fallback 全 0)

For Phase 3.3 minimum viable:
- 用现有 lambdamart_v6 (Codex 2.1) 或 v4 ml_score 作输入
- sniper / institution placeholder (返回 0 score, ensemble 仅取 lambdamart)
- 后续 Phase 3.4 接全 3 source

Usage:
    PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py \
        --start 2024-07-01 --end 2026-04-13 --max-positions 5 --output mart_msaf_ensemble_kpi
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.strategies.ensemble import ensemble_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("msaf_ensemble")


def load_lambdamart_predictions(
    db_path: str,
    model_id: str = "lgbm_20260517_governance_v1_20d",
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Load LambdaMART (or LGBM) predictions + fwd returns from mart_p0b_oos_predictions."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, score, "
            "       fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d "
            "FROM mart_p0b_oos_predictions "
            "WHERE model_id = ? AND signal_date >= ? AND signal_date <= ? "
            "ORDER BY signal_date, stock_code",
            [model_id, start_date, end_date],
        ).fetchdf()
        return df
    finally:
        con.close()


def load_institution_scores(
    db_path: str,
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Phase 3.4 minimum viable: institution score = lhb_inst_buy_30d (panel_v4 已有).

    后续 Codex sniper builder (a432eadffa) 完成后, 改读 mart_institution_score_daily (4 alpha class composite).
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, "
            "       CAST(lhb_inst_buy_30d AS DOUBLE) AS inst_score "
            "FROM mart_p0a_feature_label_panel_v4 "
            "WHERE signal_date >= ? AND signal_date <= ? "
            "  AND lhb_inst_buy_30d IS NOT NULL "
            "ORDER BY signal_date, stock_code",
            [start_date, end_date],
        ).fetchdf()
        return df
    finally:
        con.close()


def compute_kpi(results: list[dict], preds: pd.DataFrame, horizon: str = "20d") -> dict:
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
    n_skip = 0
    for r in rebal_results:
        sd_key = pd.Timestamp(r["signal_date"]).normalize()
        # ensemble top_k_codes — equal weight, drop NaN
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
        equity_ret = sum(rets) / len(rets)
        # Total portfolio (cash 收益 0)
        port_ret = (1 - cash_pct) * equity_ret
        obs.append(port_ret)

    if not obs:
        return {"ann_ret": None, "max_dd": None, "sharpe": None, "n_obs": 0,
                "n_skip": n_skip, "horizon": horizon}

    obs_arr = np.array(obs)
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
    }


import numpy as np  # noqa: E402


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
    parser.add_argument("--with-institution", action="store_true",
                        help="Phase 3.4 minimum viable: 用 panel_v4.lhb_inst_buy_30d 作 institution score")
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
    log.info(f"  predictions: {len(preds):,} rows, {preds['signal_date'].min()} → {preds['signal_date'].max()}")

    # 2b. Load institution score (Phase 3.4 minimum viable: lhb_inst_buy_30d)
    inst_df = pd.DataFrame()
    if args.with_institution:
        log.info("Loading institution scores (lhb_inst_buy_30d)...")
        inst_df = load_institution_scores(args.smartmoney_db, args.start, args.end)
        log.info(f"  institution scores: {len(inst_df):,} rows")

    # 3. Loop daily signals
    signal_dates = preds["signal_date"].drop_duplicates().tolist()
    log.info(f"  unique signal_dates: {len(signal_dates)}")

    # Pre-build per-signal-date institution map
    inst_by_sd = (
        inst_df.groupby("signal_date").apply(lambda g: g.set_index("stock_code")["inst_score"])
        if len(inst_df) else {}
    )

    results: list[dict] = []
    regime_counts = {"bull": 0, "neutral": 0, "bear": 0, "crash": 0}
    for sd in signal_dates:
        sd_str = str(sd)[:10]
        try:
            regime = compute_regime_state(sd_str, hs300)
        except ValueError as e:
            log.warning(f"  {sd_str}: skip — {e}")
            continue
        regime_counts[regime.state] += 1

        # lambdamart scores for this signal_date
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]

        # Institution score (Phase 3.4 minimum viable)
        inst = inst_by_sd.get(sd) if isinstance(inst_by_sd, dict) else (
            inst_by_sd.loc[sd] if sd in inst_by_sd.index.get_level_values(0).unique() else None
        )

        # Sniper: 待 Codex agent a432eadffa 完成 mart_sniper_score_daily, 暂 None
        verdict = ensemble_scores(
            signal_date=sd_str,
            regime=regime,
            lambdamart_scores=lam,
            sniper_scores=None,
            institution_scores=inst,
            max_positions=args.max_positions,
        )
        results.append({
            "signal_date": sd_str,
            "regime_state": verdict.regime_state,
            "cash_pct": verdict.cash_pct,
            "n_positions": len(verdict.top_k_codes),
            "top_k_codes": verdict.top_k_codes,
            "top_k_scores": [round(float(s), 4) for s in verdict.top_k_scores],
        })

    log.info(f"=== ensemble done ===")
    log.info(f"  signal_dates processed: {len(results)}")
    log.info(f"  regime distribution: {regime_counts}")

    # KPI compute
    kpi = None
    if args.compute_kpi:
        log.info(f"=== compute KPI (horizon={args.horizon}) ===")
        kpi = compute_kpi(results, preds, horizon=args.horizon)
        log.info(f"  ann_ret (CAGR):     {kpi.get('ann_ret_cagr'):+.2%}" if kpi.get('ann_ret_cagr') else "  ann_ret_cagr: N/A")
        log.info(f"  ann_ret (arith):    {kpi.get('ann_ret_arith'):+.2%}" if kpi.get('ann_ret_arith') else "  ann_ret_arith: N/A")
        log.info(f"  ann_ret (median):   {kpi.get('ann_ret_median'):+.2%} ★ robust" if kpi.get('ann_ret_median') is not None else "  ann_ret_median: N/A")
        log.info(f"  ann_ret (trim 10%): {kpi.get('ann_ret_trimmed10'):+.2%} ★ robust" if kpi.get('ann_ret_trimmed10') is not None else "  ann_ret_trim10: N/A")
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
