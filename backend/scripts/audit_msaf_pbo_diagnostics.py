#!/usr/bin/env python3
"""Diagnose Phase 4 MSAF PBO failures by top-K variant."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.run_phase4_gate_on_msaf import compute_port_returns, load_predictions
from scripts.run_msaf_ensemble_paper_sim import load_sniper_scores
from services.strategies.regime.regime_state import load_hs300_kline

REPORTS_DIR = REPO_ROOT / "data" / "reports"
DEFAULT_MODEL_ID = "lgbm_phase5_gcp_20260520T010718"
DEFAULT_K_VALUES = (3, 5, 7, 10, 15)
PBO_THRESHOLD = 0.20


def _safe_sharpe(returns: np.ndarray) -> float:
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    return float(returns.mean() / std) if std > 1e-12 else 0.0


def _max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    nav = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(nav)
    drawdowns = nav / peak - 1.0
    return float(drawdowns.min())


def variant_stats(returns_matrix: np.ndarray, k_values: list[int], *, periods_per_year: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, k in enumerate(k_values):
        returns = returns_matrix[idx]
        period_sharpe = _safe_sharpe(returns)
        rows.append(
            {
                "k": k,
                "n_obs": int(len(returns)),
                "mean_return": float(returns.mean()),
                "std_return": float(returns.std(ddof=1)) if len(returns) > 1 else 0.0,
                "period_sharpe": period_sharpe,
                "ann_sharpe": float(period_sharpe * math.sqrt(periods_per_year)),
                "win_rate": float((returns > 0).mean()),
                "max_dd": _max_drawdown(returns),
                "total_return": float(np.prod(1.0 + returns) - 1.0),
            }
        )
    return rows


def pbo_variant_diagnostics(
    returns_matrix: np.ndarray,
    k_values: list[int],
    *,
    sub_periods: int = 16,
    threshold: float = PBO_THRESHOLD,
) -> dict[str, Any]:
    if returns_matrix.ndim != 2:
        raise ValueError(f"returns_matrix must be 2D, got {returns_matrix.shape}")
    n_trials, n_periods = returns_matrix.shape
    if n_trials != len(k_values):
        raise ValueError(f"k_values length {len(k_values)} != n_trials {n_trials}")
    if n_trials < 2:
        raise ValueError(f"need at least 2 trials, got {n_trials}")
    if sub_periods % 2 != 0:
        raise ValueError(f"sub_periods must be even, got {sub_periods}")
    if n_periods < sub_periods:
        raise ValueError(f"n_periods={n_periods} < sub_periods={sub_periods}")

    base = n_periods // sub_periods
    rem = n_periods % sub_periods
    sub_indices = []
    cursor = 0
    for idx in range(sub_periods):
        size = base + (1 if idx < rem else 0)
        sub_indices.append(np.arange(cursor, cursor + size))
        cursor += size

    lambda_values: list[float] = []
    selection_counts: Counter[int] = Counter()
    failure_counts: Counter[int] = Counter()
    rank_sums: defaultdict[int, float] = defaultdict(float)
    winner_oos_sharpe_sums: defaultdict[int, float] = defaultdict(float)
    half = sub_periods // 2
    for is_subs in itertools.combinations(range(sub_periods), half):
        is_set = set(is_subs)
        is_idx = np.concatenate([sub_indices[i] for i in is_subs])
        oos_idx = np.concatenate([sub_indices[i] for i in range(sub_periods) if i not in is_set])

        is_returns = returns_matrix[:, is_idx]
        is_sharpe = np.array([_safe_sharpe(row) for row in is_returns])
        best_is = int(np.argmax(is_sharpe))
        selected_k = k_values[best_is]

        oos_returns = returns_matrix[:, oos_idx]
        oos_sharpe = np.array([_safe_sharpe(row) for row in oos_returns])
        ranks = np.argsort(np.argsort(oos_sharpe)) + 1
        omega = (ranks[best_is] - 0.5) / n_trials
        omega = max(min(float(omega), 1 - 1e-12), 1e-12)
        lam = math.log(omega / (1 - omega))

        lambda_values.append(lam)
        selection_counts[selected_k] += 1
        rank_sums[selected_k] += float(ranks[best_is])
        winner_oos_sharpe_sums[selected_k] += float(oos_sharpe[best_is])
        if lam < 0:
            failure_counts[selected_k] += 1

    lambda_arr = np.array(lambda_values)
    n_combos = len(lambda_values)
    by_selected_k = []
    for k in k_values:
        n_selected = selection_counts[k]
        by_selected_k.append(
            {
                "k": k,
                "n_selected": int(n_selected),
                "selection_pct": float(n_selected / n_combos) if n_combos else 0.0,
                "n_oos_bottom_half": int(failure_counts[k]),
                "bottom_half_rate_when_selected": (
                    float(failure_counts[k] / n_selected) if n_selected else None
                ),
                "avg_oos_rank_when_selected": (
                    float(rank_sums[k] / n_selected) if n_selected else None
                ),
                "avg_oos_sharpe_when_selected": (
                    float(winner_oos_sharpe_sums[k] / n_selected) if n_selected else None
                ),
            }
        )

    pbo = float((lambda_arr < 0).mean())
    return {
        "pbo": pbo,
        "passes": pbo <= threshold,
        "threshold": threshold,
        "lambda_mean": float(lambda_arr.mean()),
        "lambda_std": float(lambda_arr.std(ddof=1)) if len(lambda_arr) > 1 else 0.0,
        "n_combos": int(n_combos),
        "sub_periods": int(sub_periods),
        "by_selected_k": by_selected_k,
    }


def _parse_k_values(value: str) -> list[int]:
    k_values = [int(part.strip()) for part in value.split(",") if part.strip()]
    if len(k_values) < 2:
        raise argparse.ArgumentTypeError("need at least two K values")
    return k_values


def build_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    hs300 = load_hs300_kline(args.market_db)
    preds = load_predictions(args.smartmoney_db, args.model_id)
    if preds.empty:
        raise RuntimeError(f"No predictions found for model_id={args.model_id}")

    sniper_by_sd = None
    if args.sniper_weight is not None and args.sniper_weight > 0:
        start = str(preds["signal_date"].min())[:10]
        end = str(preds["signal_date"].max())[:10]
        sniper_df = load_sniper_scores(args.smartmoney_db, start, end)
        sniper_by_sd = {
            sd: group.set_index("stock_code")["sniper_score"]
            for sd, group in sniper_df.groupby("signal_date", sort=False)
        }

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
    }
    pairs_by_k = {
        k: compute_port_returns(preds, "5d", hs300, max_positions=k, **source_weight_kwargs)
        for k in args.k_values
    }
    common_dates = sorted(set.intersection(*[set(date for date, _ in pairs) for pairs in pairs_by_k.values()]))
    returns_matrix = np.array(
        [
            [dict(pairs_by_k[k])[date] for date in common_dates]
            for k in args.k_values
        ]
    )
    correlations = np.corrcoef(returns_matrix)
    diagnostics = pbo_variant_diagnostics(
        returns_matrix,
        args.k_values,
        sub_periods=args.sub_periods,
        threshold=args.pbo_threshold,
    )
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_id": args.model_id,
        "prediction_table": preds.attrs.get("prediction_table", "unknown"),
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
        "pbo_horizon": "5d",
        "k_values": args.k_values,
        "n_common_dates": len(common_dates),
        "common_date_start": str(common_dates[0].date()) if common_dates else None,
        "common_date_end": str(common_dates[-1].date()) if common_dates else None,
        "variant_stats": variant_stats(returns_matrix, args.k_values),
        "variant_correlation_matrix": correlations.tolist(),
        "pbo_diagnostics": diagnostics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose MSAF Phase4 PBO by top-K variants")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--lambdamart-weight", type=float, required=True)
    parser.add_argument("--sniper-weight", type=float, required=True)
    parser.add_argument("--institution-weight", type=float, default=0.0)
    parser.add_argument("--min-top-score", type=float, default=None)
    parser.add_argument("--min-sniper-score", type=float, default=None)
    parser.add_argument("--score-exposure-floor", type=float, default=None)
    parser.add_argument("--score-exposure-ceiling", type=float, default=None)
    parser.add_argument("--score-min-exposure", type=float, default=0.0)
    parser.add_argument("--rank-decay", type=float, default=None)
    parser.add_argument("--k-values", type=_parse_k_values, default=list(DEFAULT_K_VALUES))
    parser.add_argument("--sub-periods", type=int, default=16)
    parser.add_argument("--pbo-threshold", type=float, default=PBO_THRESHOLD)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = build_diagnostics(args)
    if args.json_out is None:
        weight_tag = f"lm{int(round(args.lambdamart_weight * 100))}_sniper{int(round(args.sniper_weight * 100))}"
        args.json_out = REPORTS_DIR / f"msaf_pbo_diagnostics_{weight_tag}.json"
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    summary = {
        "model_id": payload["model_id"],
        "source_weight_override": payload["source_weight_override"],
        "n_common_dates": payload["n_common_dates"],
        "pbo": payload["pbo_diagnostics"]["pbo"],
        "passes": payload["pbo_diagnostics"]["passes"],
        "by_selected_k": payload["pbo_diagnostics"]["by_selected_k"],
        "json_out": str(args.json_out),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
