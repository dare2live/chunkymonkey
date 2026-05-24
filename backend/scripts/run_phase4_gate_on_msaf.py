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
from scripts.run_msaf_ensemble_paper_sim import (
    apply_cash_overlay,
    apply_score_exposure,
    apply_score_floor,
    apply_sniper_floor,
    apply_source_weight_override,
    load_sniper_scores,
    weighted_return_by_rank,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("phase4_gate")

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


def _prediction_row_counts(con: duckdb.DuckDBPyConnection, model_id: str) -> dict[str, int]:
    existing = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN (?, ?)",
            list(PREDICTION_TABLE_CANDIDATES),
        ).fetchall()
    }
    parts = [
        f"SELECT '{table_name}' AS table_name, COUNT(*) AS n_rows FROM {table_name} WHERE model_id = ?"
        for table_name in PREDICTION_TABLE_CANDIDATES
        if table_name in existing
    ]
    if not parts:
        return {}
    return {
        row[0]: int(row[1] or 0)
        for row in con.execute(" UNION ALL ".join(parts), [model_id] * len(parts)).fetchall()
    }


def _table_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    rows = con.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _prediction_window_coverage(
    con: duckdb.DuckDBPyConnection,
    model_id: str,
) -> dict[str, object] | None:
    """Return distinct prediction window coverage for the model when inferable."""

    row_counts = _prediction_row_counts(con, model_id)
    table_name = next((name for name in PREDICTION_TABLE_CANDIDATES if row_counts.get(name)), None)
    if table_name is None:
        return None

    required = {"train_start", "train_end", "test_start", "test_end"}
    columns = _table_columns(con, table_name)
    missing = sorted(required - columns)
    if missing:
        return {
            "prediction_table": table_name,
            "n_prediction_windows": None,
            "coverage_reason": "prediction-window-columns-missing",
            "missing_columns": missing,
        }

    row = con.execute(
        f"""
        SELECT COUNT(*)
          FROM (
            SELECT DISTINCT train_start, train_end, test_start, test_end
              FROM {table_name}
             WHERE model_id = ?
               AND test_start IS NOT NULL
               AND test_end IS NOT NULL
          )
        """,
        [model_id],
    ).fetchone()
    return {
        "prediction_table": table_name,
        "n_prediction_windows": int(row[0] or 0) if row else 0,
    }


def load_predictions(db_path: str, model_id: str) -> pd.DataFrame:
    """Load lambdamart score + multi-horizon fwd from mart_p0a_label_panel JOIN.

    predictions 表 fwd_cost_after_5d/10d 100% NULL (model 只训 20d) — 改 JOIN p0a label.
    Phase 5 LambdaMART v6 artifacts may be stored in mart_p0b_lambdamart_v6_predictions
    instead of the legacy mart_p0b_oos_predictions table, so prefer legacy and fall
    back to the v6 table when the requested model has no legacy rows.
    """
    con = duckdb.connect(db_path, read_only=True)
    try:
        row_counts = _prediction_row_counts(con, model_id)
        table_name = next((name for name in PREDICTION_TABLE_CANDIDATES if row_counts.get(name)), None)
        if table_name:
            df = con.execute(
                "SELECT p.signal_date, p.stock_code, p.score, "
                "       l.fwd_cost_after_5d, l.fwd_cost_after_10d, l.fwd_cost_after_20d "
                f"FROM {table_name} p "
                "LEFT JOIN mart_p0a_label_panel l "
                "  ON p.signal_date = l.signal_date AND p.stock_code = l.stock_code "
                "WHERE p.model_id = ? "
                "ORDER BY p.signal_date, p.stock_code",
                [model_id],
            ).fetchdf()
            df.attrs["prediction_table"] = table_name
            return df
        return pd.DataFrame(
            columns=[
                "signal_date",
                "stock_code",
                "score",
                "fwd_cost_after_5d",
                "fwd_cost_after_10d",
                "fwd_cost_after_20d",
            ]
        )
    finally:
        con.close()


def _row_to_dict(row, columns: list[str]) -> dict[str, object] | None:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {columns[i]: row[i] for i in range(len(columns))}


def load_model_train_log(db_path: str, model_id: str) -> dict[str, object] | None:
    """Load latest true train/OOS metrics for a model, if retrain emitted them."""

    con = duckdb.connect(db_path, read_only=True)
    try:
        if not table_exists(con, "fact_model_train_log"):
            return None
        cur = con.execute(
            """
            SELECT model_id, run_id, model_version, feature_version, label_version,
                   train_start, train_end, n_train_rows, n_features,
                   is_rank_ic, is_rank_ic_ir, is_ndcg5, is_ndcg10, is_ndcg20,
                   oos_rank_ic_avg, oos_rank_ic_ir,
                   seed, n_trials, n_windows, optuna_best_value,
                   walk_forward_mode, metrics_json, built_at
              FROM fact_model_train_log
             WHERE model_id = ?
               AND is_rank_ic IS NOT NULL
               AND oos_rank_ic_avg IS NOT NULL
             ORDER BY built_at DESC, run_id DESC
             LIMIT 1
            """,
            [model_id],
        )
        return _row_to_dict(cur.fetchone(), [str(item[0]) for item in cur.description or []])
    finally:
        con.close()


def _positive_int(value: object) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _train_log_detail(train_log: dict[str, object]) -> dict[str, object]:
    return {
        "source_table": "fact_model_train_log",
        "run_id": train_log.get("run_id"),
        "built_at": train_log.get("built_at"),
        "n_windows": train_log.get("n_windows"),
        "n_train_rows": train_log.get("n_train_rows"),
        "train_start": train_log.get("train_start"),
        "train_end": train_log.get("train_end"),
        "walk_forward_mode": train_log.get("walk_forward_mode"),
    }


def _validate_true_train_log(
    db_path: str,
    model_id: str,
    train_log: dict[str, object],
    *,
    min_train_log_windows: int | None,
) -> tuple[bool, dict[str, object] | None]:
    """Gate true IS/OOS evidence so partial smoke logs cannot hard-promote."""

    detail = _train_log_detail(train_log)
    n_windows = _positive_int(train_log.get("n_windows"))
    n_train_rows = _positive_int(train_log.get("n_train_rows"))
    if n_train_rows is None:
        return False, {**detail, "reason": "non-positive-train-rows"}
    if n_windows is None:
        return False, {**detail, "reason": "non-positive-window-count"}
    if min_train_log_windows is not None and n_windows < min_train_log_windows:
        return False, {
            **detail,
            "reason": "below-min-train-log-windows",
            "min_train_log_windows": min_train_log_windows,
        }
    if train_log.get("walk_forward_mode") != "expanding_monthly":
        return False, {**detail, "reason": "unsupported-walk-forward-mode"}

    con = duckdb.connect(db_path, read_only=True)
    try:
        coverage = _prediction_window_coverage(con, model_id)
    finally:
        con.close()
    if coverage is not None:
        detail.update(coverage)
        n_prediction_windows = coverage.get("n_prediction_windows")
        if isinstance(n_prediction_windows, int) and n_prediction_windows > 0 and n_windows < n_prediction_windows:
            return False, {
                **detail,
                "reason": "partial-train-log-window-coverage",
                "expected_windows": n_prediction_windows,
                "actual_windows": n_windows,
            }

    return True, None


def _split_half_is_oos_metrics(
    obs_arr: np.ndarray,
    *,
    rejected_train_log: dict[str, object] | None = None,
) -> dict[str, object]:
    mid = len(obs_arr) // 2
    is_period = obs_arr[:mid]
    oos_period = obs_arr[mid:]
    return {
        "is_metric": float(is_period.mean()) if len(is_period) > 0 else 0.0,
        "oos_metric": float(oos_period.mean()) if len(oos_period) > 0 else 0.0,
        "is_oos_proxy_mode": True,
        "is_oos_evidence": "degraded-split-half-not-train-log",
        "train_log": None,
        "train_log_rejected": rejected_train_log,
    }


def resolve_is_oos_metrics(
    db_path: str,
    model_id: str,
    obs_arr: np.ndarray,
    *,
    min_train_log_windows: int | None = 2,
) -> dict[str, object]:
    """Prefer true train-log RankIC; fallback to split-half OOS proxy when absent."""

    train_log = load_model_train_log(db_path, model_id)
    if train_log is not None:
        accepted, rejection = _validate_true_train_log(
            db_path,
            model_id,
            train_log,
            min_train_log_windows=min_train_log_windows,
        )
        if not accepted:
            return _split_half_is_oos_metrics(obs_arr, rejected_train_log=rejection)
        return {
            "is_metric": float(train_log["is_rank_ic"]),
            "oos_metric": float(train_log["oos_rank_ic_avg"]),
            "is_oos_proxy_mode": False,
            "is_oos_evidence": "true-train-log-PIT",
            "train_log": _train_log_detail(train_log),
            "train_log_rejected": None,
        }

    return _split_half_is_oos_metrics(obs_arr)


def compute_port_returns(
    preds: pd.DataFrame,
    horizon: str,
    hs300: pd.DataFrame,
    max_positions: int = 5,
    *,
    sniper_by_sd: dict[pd.Timestamp, pd.Series] | None = None,
    lambdamart_weight: float | None = None,
    sniper_weight: float | None = None,
    institution_weight: float | None = None,
    min_top_score: float | None = None,
    min_sniper_score: float | None = None,
    score_exposure_floor: float | None = None,
    score_exposure_ceiling: float | None = None,
    score_min_exposure: float = 0.0,
    rank_decay: float | None = None,
    bull_cash_pct: float | None = None,
    neutral_cash_pct: float | None = None,
    bear_cash_pct: float | None = None,
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
        regime = apply_source_weight_override(
            regime,
            lambdamart_weight=lambdamart_weight,
            sniper_weight=sniper_weight,
            institution_weight=institution_weight,
        )
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]
        day_sniper = (
            sniper_by_sd.get(pd.Timestamp(sd).normalize(), pd.Series(dtype=float))
            if sniper_by_sd is not None
            else None
        )
        v = ensemble_scores(
            signal_date=sd_str,
            regime=regime,
            lambdamart_scores=lam,
            sniper_scores=day_sniper,
            max_positions=max_positions,
        )
        base_cash_pct = apply_cash_overlay(
            regime_state=v.regime_state,
            base_cash_pct=v.cash_pct,
            bull_cash_pct=bull_cash_pct,
            neutral_cash_pct=neutral_cash_pct,
            bear_cash_pct=bear_cash_pct,
        )
        top_codes, top_scores, cash_pct, _dropped_by_score = apply_score_floor(
            top_k_codes=v.top_k_codes,
            top_k_scores=[float(s) for s in v.top_k_scores],
            cash_pct=base_cash_pct,
            min_top_score=min_top_score,
        )
        top_codes, top_scores, cash_pct, _dropped_by_sniper = apply_sniper_floor(
            top_k_codes=top_codes,
            top_k_scores=top_scores,
            sniper_scores=day_sniper,
            cash_pct=cash_pct,
            min_sniper_score=min_sniper_score,
        )
        cash_pct, _score_exposure, _avg_top_score = apply_score_exposure(
            top_k_scores=top_scores,
            cash_pct=cash_pct,
            score_exposure_floor=score_exposure_floor,
            score_exposure_ceiling=score_exposure_ceiling,
            score_min_exposure=score_min_exposure,
        )
        results.append({"sd": sd, "codes": top_codes, "cash_pct": cash_pct})

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
        equity = weighted_return_by_rank(rets, rank_decay=rank_decay)
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
    # L7 enforcement (2026-05-24): default ON — strict mode required for production promotion.
    # 用户原话 MASTER_SYNTHESIS Phase 1.2: Phase 4 strict default ON.
    parser.add_argument("--require-true-train-log",
                        action=argparse.BooleanOptionalAction,
                        default=True,
                        help="STRICT mode (DEFAULT ON 2026-05-24): abort if fact_model_train_log unavailable. "
                             "Use --no-require-true-train-log for legacy proxy mode (audit/diagnostic only).")
    parser.add_argument("--lambdamart-weight", type=float, default=None)
    parser.add_argument("--sniper-weight", type=float, default=None)
    parser.add_argument("--institution-weight", type=float, default=None)
    parser.add_argument("--max-positions", type=int, default=5,
                        help="Primary strategy max positions; PBO still tests the fixed top-K variant grid")
    parser.add_argument("--min-top-score", type=float, default=None,
                        help="Opt-in conviction filter matching run_msaf_ensemble_paper_sim.py")
    parser.add_argument("--min-sniper-score", type=float, default=None,
                        help="Opt-in source filter matching run_msaf_ensemble_paper_sim.py")
    parser.add_argument("--score-exposure-floor", type=float, default=None,
                        help="Opt-in smooth conviction sizing floor matching run_msaf_ensemble_paper_sim.py")
    parser.add_argument("--score-exposure-ceiling", type=float, default=None,
                        help="Opt-in score where full exposure resumes")
    parser.add_argument("--score-min-exposure", type=float, default=0.0,
                        help="Minimum equity exposure multiplier for --score-exposure-floor")
    parser.add_argument("--rank-decay", type=float, default=None,
                        help="Opt-in rank-decay position sizing; 1.0 equals equal weight, lower values are more top-heavy")
    parser.add_argument("--bull-cash-pct", type=float, default=None)
    parser.add_argument("--neutral-cash-pct", type=float, default=None)
    parser.add_argument("--bear-cash-pct", type=float, default=None)
    parser.add_argument("--primary-horizon", default="20d", choices=["5d", "10d", "20d"],
                        help="Horizon used for ann/conservative and split-half IS-OOS gate")
    args = parser.parse_args()

    log.info(f"=== Phase 4 gate on MSAF model_id={args.model_id} ===")

    # Load
    hs300 = load_hs300_kline(args.market_db)
    preds = load_predictions(args.smartmoney_db, args.model_id)
    prediction_table = preds.attrs.get("prediction_table", "none")
    log.info(
        f"  predictions: {len(preds):,} rows from {prediction_table}, "
        f"dates {preds['signal_date'].min()} → {preds['signal_date'].max()}"
    )
    if preds.empty:
        raise RuntimeError(
            f"No predictions found for model_id={args.model_id} in "
            f"{', '.join(PREDICTION_TABLE_CANDIDATES)}; refusing to write empty gate result"
        )
    sniper_by_sd = None
    if args.sniper_weight is not None and args.sniper_weight > 0:
        start = str(preds["signal_date"].min())[:10]
        end = str(preds["signal_date"].max())[:10]
        sniper_df = load_sniper_scores(args.smartmoney_db, start, end)
        sniper_by_sd = {
            sd: g.set_index("stock_code")["sniper_score"]
            for sd, g in sniper_df.groupby("signal_date", sort=False)
        }
        log.info(f"  sniper scores: {len(sniper_df):,} rows, {len(sniper_by_sd):,} signal_dates")

    # Multi-horizon: lambdamart score top-K 在 5d / 10d / 20d horizon eval (PIT-build fwd)
    # 现 returns 是 [(date, port_ret), ...] tuple list (Codex MEDIUM 3 修)
    source_weight_kwargs = {
        "sniper_by_sd": sniper_by_sd,
        "lambdamart_weight": args.lambdamart_weight,
        "sniper_weight": args.sniper_weight,
        "institution_weight": args.institution_weight,
        "min_top_score": args.min_top_score,
        "min_sniper_score": args.min_sniper_score,
        "score_exposure_floor": args.score_exposure_floor,
        "score_exposure_ceiling": args.score_exposure_ceiling,
        "score_min_exposure": args.score_min_exposure,
        "rank_decay": args.rank_decay,
        "bull_cash_pct": args.bull_cash_pct,
        "neutral_cash_pct": args.neutral_cash_pct,
        "bear_cash_pct": args.bear_cash_pct,
    }
    obs_20d_pairs = compute_port_returns(preds, "20d", hs300, max_positions=args.max_positions, **source_weight_kwargs)
    obs_10d_pairs = compute_port_returns(preds, "10d", hs300, max_positions=args.max_positions, **source_weight_kwargs)
    obs_5d_pairs = compute_port_returns(preds, "5d", hs300, max_positions=args.max_positions, **source_weight_kwargs)
    obs_20d = [r for _, r in obs_20d_pairs]
    obs_10d = [r for _, r in obs_10d_pairs]
    obs_5d = [r for _, r in obs_5d_pairs]
    obs_by_horizon = {"5d": obs_5d, "10d": obs_10d, "20d": obs_20d}
    log.info(f"  obs_5d:  n={len(obs_5d)} (weekly non-overlap)")
    log.info(f"  obs_10d: n={len(obs_10d)} (biweekly non-overlap)")
    log.info(f"  obs_20d: n={len(obs_20d)} (monthly non-overlap)")

    # PBO trials: 5 不同 K (top-3/5/7/10/15 positions) 作 strategy variants
    # 真"不同 strategy parameter", 不是 same strategy 不同 horizon (前次 0.711 误读)
    # 用 5d weekly horizon 拿足够 obs (87 weekly), PBO ≥ 16 periods.
    k_values = [3, 5, 7, 10, 15]  # rule-compliance: ok evidence=top-k-ablation-trial-variants
    k_obs_pairs_list: list[list[tuple[pd.Timestamp, float]]] = []
    for k in k_values:
        k_obs_pairs = compute_port_returns(preds, "5d", hs300, max_positions=k, **source_weight_kwargs)
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
    primary_obs = obs_by_horizon[args.primary_horizon]
    primary_days = int(args.primary_horizon.rstrip("d"))
    primary_periods_per_year = 252 / primary_days
    obs_arr = np.array(primary_obs)
    ann_normal = float(obs_arr.mean() * primary_periods_per_year)  # rule-compliance: ok evidence=annualize-from-primary-horizon
    ann_conservative = ann_normal - 0.015  # rule-compliance: ok evidence=slippage-50pct-overhead-est

    # DSR input: 用 5d weekly obs (n=87 > 30 满足 DSR 最低 obs 要求)
    dsr_obs = np.array(obs_5d) if len(obs_5d) >= 30 else obs_arr

    # IS-OOS metric: prefer true train-log RankIC; fallback to OOS split-half proxy.
    # rule-compliance: ok evidence=true-train-log-or-degraded-split-half-explicit
    is_oos_metrics = resolve_is_oos_metrics(args.smartmoney_db, args.model_id, obs_arr)
    is_metric = float(is_oos_metrics["is_metric"])
    oos_metric = float(is_oos_metrics["oos_metric"])

    # n_trials_for_dsr: 反映"实际 tried 的 strategy candidate 数"用作 selection bias 校正
    # lambdamart_v6 不是 Optuna 50 trial 选 best, 是固定 config (Codex 2.1 设计) — n_trials=5 反映 modest variation
    # periods_per_year: 5d weekly → 50 (252/5), 10d → 25, 20d → 12, 1d daily → 252
    # rule-compliance: ok evidence=5d-non-overlap-weekly-frequency
    periods_per_year_5d = 50
    # n_trials: lambdamart_v6 是 Codex 2.1 固定 config 单 strategy (不是 Optuna search), n_trials=1
    # 即 DSR 不做 selection bias 校正 — sr_expected_max=0, dsr_z = sr_observed × sqrt(n-1)
    # IS-OOS proxy mode: false only when fact_model_train_log supplied true RankIC evidence.
    is_oos_proxy_mode = bool(is_oos_metrics["is_oos_proxy_mode"])
    # rule-compliance: ok evidence=Phase 4 strict mode prevents proxy fallback (operational ready gate)
    if args.require_true_train_log and is_oos_proxy_mode:
        log.error(
            "ABORT: --require-true-train-log set but model_id=%s has no fact_model_train_log row "
            "(or rejected). Cannot use proxy split-half for production promotion gate. "
            "First write train_log evidence via retrain_lambdamart_v6.py.",
            args.model_id,
        )
        return 4
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
        "primary_horizon": args.primary_horizon,
        "max_positions": args.max_positions,
        "source_weight_override": {
            "lambdamart_weight": args.lambdamart_weight,
            "sniper_weight": args.sniper_weight,
            "institution_weight": args.institution_weight,
        },
        "score_filter": {
            "min_top_score": args.min_top_score,
            "min_sniper_score": args.min_sniper_score,
        },
        "score_exposure": {
            "score_exposure_floor": args.score_exposure_floor,
            "score_exposure_ceiling": args.score_exposure_ceiling,
            "score_min_exposure": args.score_min_exposure,
        },
        "position_sizing": {
            "rank_decay": args.rank_decay,
        },
        "cash_overlay": {
            "bull_cash_pct": args.bull_cash_pct,
            "neutral_cash_pct": args.neutral_cash_pct,
            "bear_cash_pct": args.bear_cash_pct,
        },
        "n_obs_20d": len(obs_20d),
        "n_obs_10d": len(obs_10d),
        "n_obs_5d": len(obs_5d),
        "ann_normal": ann_normal,
        "ann_conservative": ann_conservative,
        "is_metric": is_metric,
        "oos_metric": oos_metric,
        "is_oos_proxy_mode": is_oos_proxy_mode,
        "is_oos_evidence": is_oos_detail.get("evidence", "unknown"),
        "is_oos_metric_source": {
            "mode": "split_half_proxy" if is_oos_proxy_mode else "true_train_log",
            "evidence": is_oos_metrics.get("is_oos_evidence"),
            "train_log": is_oos_metrics.get("train_log"),
            "train_log_rejected": is_oos_metrics.get("train_log_rejected"),
        },
        "gate_result": result.to_dict(),
    }, indent=2, ensure_ascii=False, default=str))
    log.info(f"  saved: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
