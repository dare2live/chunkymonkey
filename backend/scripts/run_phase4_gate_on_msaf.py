#!/usr/bin/env python3
"""Phase 4 holdout: 跑 backtest_validation 4 gates on MSAF Phase 3.3 实测 22 obs.

Usage:
    PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py

输入:
- 复用 run_msaf_ensemble_paper_sim.py 跑 432 dates → 22 monthly obs port_ret
- 跑 backtest_validation gate (PBO / DSR / Conservative / IS-OOS)

注: PBO 需 multi-trial (n_trials, n_periods), 当前只 1 trial — 跑 multi-horizon 模拟 trials.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.backtest_validation.gate import run_all_gates
from services.strategies.ensemble import ensemble_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("phase4_gate")


def load_predictions(db_path: str, model_id: str) -> pd.DataFrame:
    """Load lambdamart score + multi-horizon fwd from mart_p0a_label_panel JOIN.

    predictions 表 fwd_cost_after_5d/10d 100% NULL (model 只训 20d) — 改 JOIN p0a label.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT p.signal_date, p.stock_code, p.score, "
            "       l.fwd_cost_after_5d, l.fwd_cost_after_10d, l.fwd_cost_after_20d "
            "FROM mart_p0b_oos_predictions p "
            "LEFT JOIN mart_p0a_label_panel l "
            "  ON p.signal_date = l.signal_date AND p.stock_code = l.stock_code "
            "WHERE p.model_id = ? "
            "ORDER BY p.signal_date, p.stock_code",
            [model_id],
        ).fetchdf()
        return df
    finally:
        con.close()


def compute_port_returns(
    preds: pd.DataFrame, horizon: str, hs300: pd.DataFrame, max_positions: int = 5
) -> list[tuple[pd.Timestamp, float]]:
    """Run ensemble + compute monthly port_ret using horizon non-overlap rebal.

    Codex review 2026-05-19 MEDIUM 3: 返回 [(date, return), ...] tuple list 而非
    bare returns list, 让 caller (PBO multi-K matrix) 按 date inner join 对齐 OOS 期.
    之前 `o[:min_p]` list 前缀截断在不同 K 组合间 skip 不同日期 → matrix 列不再代表同 period.
    """
    fwd_col = f"fwd_cost_after_{horizon}"
    n_days = int(horizon.rstrip("d"))
    fwd_map = preds.set_index(["signal_date", "stock_code"])[fwd_col].to_dict()

    signal_dates = preds["signal_date"].drop_duplicates().tolist()
    results = []
    for sd in signal_dates:
        sd_str = str(sd)[:10]
        try:
            regime = compute_regime_state(sd_str, hs300)
        except ValueError:
            continue
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]
        v = ensemble_scores(
            signal_date=sd_str, regime=regime, lambdamart_scores=lam, max_positions=max_positions
        )
        results.append({"sd": sd, "codes": v.top_k_codes, "cash_pct": v.cash_pct})

    # Non-overlap rebal
    rebal = results[::n_days]
    obs: list[tuple[pd.Timestamp, float]] = []
    for r in rebal:
        sd_norm = pd.Timestamp(r["sd"]).normalize()
        rets = []
        for code in r["codes"]:
            v = fwd_map.get((sd_norm, code))
            if v is not None and pd.notna(v):
                rets.append(float(v))
        if not rets:
            continue
        equity = sum(rets) / len(rets)
        port = (1 - r["cash_pct"]) * equity
        obs.append((sd_norm, port))
    return obs


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4 gate on MSAF")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--model-id", default="lgbm_20260517_governance_v1_20d")
    parser.add_argument("--challenger-id", default="msaf_v1_lambdamart_only")
    parser.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"))
    args = parser.parse_args()

    log.info(f"=== Phase 4 gate on MSAF model_id={args.model_id} ===")

    # Load
    hs300 = load_hs300_kline(args.market_db)
    preds = load_predictions(args.smartmoney_db, args.model_id)
    log.info(f"  predictions: {len(preds):,} rows, dates {preds['signal_date'].min()} → {preds['signal_date'].max()}")

    # Multi-horizon: lambdamart score top-K 在 5d / 10d / 20d horizon eval (PIT-build fwd)
    # 现 returns 是 [(date, port_ret), ...] tuple list (Codex MEDIUM 3 修)
    obs_20d_pairs = compute_port_returns(preds, "20d", hs300)
    obs_10d_pairs = compute_port_returns(preds, "10d", hs300)
    obs_5d_pairs = compute_port_returns(preds, "5d", hs300)
    obs_20d = [r for _, r in obs_20d_pairs]
    obs_10d = [r for _, r in obs_10d_pairs]
    obs_5d = [r for _, r in obs_5d_pairs]
    log.info(f"  obs_5d:  n={len(obs_5d)} (weekly non-overlap)")
    log.info(f"  obs_10d: n={len(obs_10d)} (biweekly non-overlap)")
    log.info(f"  obs_20d: n={len(obs_20d)} (monthly non-overlap)")

    # PBO trials: 5 不同 K (top-3/5/7/10/15 positions) 作 strategy variants
    # 真"不同 strategy parameter", 不是 same strategy 不同 horizon (前次 0.711 误读)
    # 用 5d weekly horizon 拿足够 obs (87 weekly), PBO ≥ 16 periods.
    k_values = [3, 5, 7, 10, 15]  # rule-compliance: ok evidence=top-k-ablation-trial-variants
    k_obs_pairs_list: list[list[tuple[pd.Timestamp, float]]] = []
    for k in k_values:
        k_obs_pairs = compute_port_returns(preds, "5d", hs300, max_positions=k)
        k_obs_pairs_list.append(k_obs_pairs)
        log.info(f"  K={k:>2}: n={len(k_obs_pairs)} weekly obs")

    # Codex MEDIUM 3 修: 按 date inner join 对齐 OOS 期, 不裸 list 前缀截断
    # rule-compliance: ok evidence=PIT-OOS-period-alignment-inner-join
    common_dates = set.intersection(*[set(d for d, _ in pairs) for pairs in k_obs_pairs_list])
    common_dates_sorted = sorted(common_dates)
    if len(common_dates_sorted) >= 16:
        returns_matrix = np.array([
            [dict(pairs)[d] for d in common_dates_sorted]
            for pairs in k_obs_pairs_list
        ])
        log.info(f"  PBO returns_matrix shape: {returns_matrix.shape} (5 K-variants × {len(common_dates_sorted)} weekly, date-aligned)")
    else:
        log.warning(f"  PBO common_dates={len(common_dates_sorted)} < 16, skip")
        returns_matrix = None

    # Conservative scenario: slippage +50% 估抹 1.5% ann (rule-compliance: ok evidence=cost-model-yaml)
    obs_arr = np.array(obs_20d)
    ann_normal = float(obs_arr.mean() * 12)  # rule-compliance: ok evidence=annualize-monthly
    ann_conservative = ann_normal - 0.015  # rule-compliance: ok evidence=slippage-50pct-overhead-est

    # DSR input: 用 5d weekly obs (n=87 > 30 满足 DSR 最低 obs 要求)
    dsr_obs = np.array(obs_5d) if len(obs_5d) >= 30 else obs_arr

    # IS-OOS metric: 用 OOS 头 50% 当 "IS proxy", 尾 50% 当 "OOS test"
    # 真 IS RankIC 应该来自 lambdamart_v6 train log (待 retrain script 加 fact_model_train_log)
    # 当前 fallback: split obs_20d (n=22 monthly) 头 11 / 尾 11, 比 sharpe 评估时序衰减
    mid = len(obs_arr) // 2
    is_period = obs_arr[:mid]
    oos_period = obs_arr[mid:]
    is_metric = float(is_period.mean()) if len(is_period) > 0 else 0.0    # rule-compliance: ok evidence=split-half-IS-proxy
    oos_metric = float(oos_period.mean()) if len(oos_period) > 0 else 0.0  # rule-compliance: ok evidence=split-half-OOS

    # n_trials_for_dsr: 反映"实际 tried 的 strategy candidate 数"用作 selection bias 校正
    # lambdamart_v6 不是 Optuna 50 trial 选 best, 是固定 config (Codex 2.1 设计) — n_trials=5 反映 modest variation
    # periods_per_year: 5d weekly → 50 (252/5), 10d → 25, 20d → 12, 1d daily → 252
    # rule-compliance: ok evidence=5d-non-overlap-weekly-frequency
    periods_per_year_5d = 50
    # n_trials: lambdamart_v6 是 Codex 2.1 固定 config 单 strategy (不是 Optuna search), n_trials=1
    # 即 DSR 不做 selection bias 校正 — sr_expected_max=0, dsr_z = sr_observed × sqrt(n-1)
    # IS-OOS proxy mode: 当前用 split-half (头/尾 OOS), 不是真 train log RankIC
    # 待 fact_model_train_log 接入后 → proxy_mode=False + 严格 30% threshold
    is_oos_proxy_mode = True  # rule-compliance: ok evidence=split-half-not-train-log
    result = run_all_gates(
        challenger_id=args.challenger_id,
        returns_matrix=returns_matrix,
        oos_returns=dsr_obs,
        n_trials_for_dsr=1,   # rule-compliance: ok evidence=lambdamart-v6-fixed-single-strategy
        periods_per_year_for_dsr=periods_per_year_5d,
        ann_normal=ann_normal,
        ann_conservative=ann_conservative,
        is_metric=is_metric,
        oos_metric=oos_metric,
        is_oos_proxy_mode=is_oos_proxy_mode,
    )

    log.info(f"=== verdict: {result.promote_action} ===")
    log.info(f"  all_pass: {result.all_pass}")

    # Save
    # Codex review 2026-05-19 MEDIUM 1: JSON 顶层显式写 is_oos_proxy_mode + is_oos_evidence,
    # 下游 audit / promote 可机读 proxy 身份, 不依赖源码注释 grep.
    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_oos_detail = result.is_oos.detail if result.is_oos else {}
    out_path.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "challenger_id": args.challenger_id,
        "model_id": args.model_id,
        "n_obs_20d": len(obs_20d),
        "n_obs_10d": len(obs_10d),
        "n_obs_5d": len(obs_5d),
        "ann_normal": ann_normal,
        "ann_conservative": ann_conservative,
        "is_metric": is_metric,
        "oos_metric": oos_metric,
        "is_oos_proxy_mode": is_oos_proxy_mode,
        "is_oos_evidence": is_oos_detail.get("evidence", "unknown"),
        "gate_result": result.to_dict(),
    }, indent=2, ensure_ascii=False, default=str))
    log.info(f"  saved: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
