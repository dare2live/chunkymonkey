#!/usr/bin/env python3
"""Diagnose LambdaMART true train-log OOS stability by replay window."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.run_phase4_gate_on_msaf import compute_port_returns, load_model_train_log, load_predictions
from scripts.run_msaf_ensemble_paper_sim import load_sniper_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline

REPORTS_DIR = REPO_ROOT / "data" / "reports"
DEFAULT_MODEL_ID = "lgbm_phase5_gcp_20260520T010718"
DEFAULT_IS_OOS_RELATIVE_DROP_THRESHOLD = 0.30


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _std(values: list[float]) -> float | None:
    return float(np.std(values, ddof=1)) if len(values) > 1 else None


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": _mean(values),
        "median": _median(values),
        "std": _std(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "positive_rate": float(sum(1 for value in values if value > 0) / len(values)) if values else None,
    }


def _relative_drop(is_metric: float | None, oos_metric: float | None) -> float | None:
    if is_metric is None or oos_metric is None or abs(is_metric) <= 1e-12:
        return None
    return float(max(0.0, (is_metric - oos_metric) / abs(is_metric)))


def _load_metrics_json(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("metrics_json")
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    raise ValueError("train-log record has no parseable metrics_json")


def _date_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def parse_window_metrics(record: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = _load_metrics_json(record)
    windows = metrics.get("window_metrics") or []
    parsed: list[dict[str, Any]] = []
    for item in windows:
        train_metrics = item.get("train_metrics") or {}
        oos_metrics = item.get("oos_metrics") or {}
        train_rank_ic = _safe_float(train_metrics.get("rank_ic"))
        oos_rank_ic = _safe_float(oos_metrics.get("rank_ic"))
        train_top5_spread = _safe_float(train_metrics.get("top5_spread"))
        oos_top5_spread = _safe_float(oos_metrics.get("top5_spread"))
        parsed.append(
            {
                "window_idx": int(item.get("window_idx", len(parsed))),
                "train_start": _date_str(item.get("train_start")),
                "train_end": _date_str(item.get("train_end")),
                "test_start": _date_str(item.get("test_start")),
                "test_end": _date_str(item.get("test_end")),
                "n_train_rows": int(item.get("n_train_rows") or 0),
                "n_test_rows": int(item.get("n_test_rows") or 0),
                "train_rank_ic": train_rank_ic,
                "oos_rank_ic": oos_rank_ic,
                "rank_ic_relative_drop": _relative_drop(train_rank_ic, oos_rank_ic),
                "train_top5_spread": train_top5_spread,
                "oos_top5_spread": oos_top5_spread,
                "top5_spread_relative_drop": _relative_drop(train_top5_spread, oos_top5_spread),
                "train_ndcg10": _safe_float(train_metrics.get("ndcg10")),
                "oos_ndcg10": _safe_float(oos_metrics.get("ndcg10")),
                "oos_top5_turnover": _safe_float(oos_metrics.get("top5_turnover")),
            }
        )
    return parsed


def attach_regime(windows: list[dict[str, Any]], hs300: pd.DataFrame) -> None:
    for window in windows:
        test_start = window.get("test_start")
        if not test_start:
            continue
        try:
            regime = compute_regime_state(str(test_start), hs300)
        except ValueError:
            continue
        window["regime_state"] = regime.state
        window["regime_ret_60d"] = regime.ret_60d
        window["regime_above_ma60"] = regime.above_ma60


def attach_strategy_returns(
    windows: list[dict[str, Any]],
    port_returns: list[tuple[pd.Timestamp, float]],
) -> None:
    ordered_returns = sorted(
        (pd.Timestamp(date).normalize(), float(ret))
        for date, ret in port_returns
    )
    return_dates = [date for date, _ret in ordered_returns]
    return_prefix_products = [1.0]
    return_prefix_sums = [0.0]
    for _, ret in ordered_returns:
        return_prefix_products.append(return_prefix_products[-1] * (1.0 + ret))
        return_prefix_sums.append(return_prefix_sums[-1] + ret)

    for window in windows:
        start = pd.Timestamp(window["test_start"]).normalize()
        end = pd.Timestamp(window["test_end"]).normalize()
        start_idx = bisect_left(return_dates, start)
        end_idx = bisect_right(return_dates, end)
        n_obs = end_idx - start_idx
        window["strategy_n_obs"] = n_obs
        if n_obs <= 0:
            window["strategy_return"] = None
            window["strategy_mean_return"] = None
            continue
        window["strategy_return"] = float(return_prefix_products[end_idx] / return_prefix_products[start_idx] - 1.0)
        window["strategy_mean_return"] = float((return_prefix_sums[end_idx] - return_prefix_sums[start_idx]) / n_obs)


def _corr(xs: list[float | None], ys: list[float | None]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    arr_x = np.array([x for x, _y in pairs], dtype=float)
    arr_y = np.array([y for _x, y in pairs], dtype=float)
    if float(arr_x.std()) <= 1e-12 or float(arr_y.std()) <= 1e-12:
        return None
    return float(np.corrcoef(arr_x, arr_y)[0, 1])


def _group_breakdown(windows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        key = str(window.get(group_key) or "unknown")
        groups.setdefault(key, []).append(window)
    rows = []
    for key, items in sorted(groups.items()):
        oos_rank_ics = [value for value in (_safe_float(item.get("oos_rank_ic")) for item in items) if value is not None]
        rel_drops = [
            value for value in (_safe_float(item.get("rank_ic_relative_drop")) for item in items) if value is not None
        ]
        strategy_returns = [
            value for value in (_safe_float(item.get("strategy_return")) for item in items) if value is not None
        ]
        rows.append(
            {
                group_key: key,
                "n_windows": len(items),
                "oos_rank_ic": _stats(oos_rank_ics),
                "rank_ic_relative_drop": _stats(rel_drops),
                "strategy_return": _stats(strategy_returns),
            }
        )
    return rows


def build_stability_payload(
    record: dict[str, Any],
    windows: list[dict[str, Any]],
    *,
    relative_drop_threshold: float = DEFAULT_IS_OOS_RELATIVE_DROP_THRESHOLD,
    phase4_gate: dict[str, Any] | None = None,
    strategy_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    train_rank_ics = [value for value in (_safe_float(w.get("train_rank_ic")) for w in windows) if value is not None]
    oos_rank_ics = [value for value in (_safe_float(w.get("oos_rank_ic")) for w in windows) if value is not None]
    rel_drops = [value for value in (_safe_float(w.get("rank_ic_relative_drop")) for w in windows) if value is not None]
    strategy_returns = [value for value in (_safe_float(w.get("strategy_return")) for w in windows) if value is not None]
    is_metric = _safe_float(record.get("is_rank_ic")) or _mean(train_rank_ics)
    oos_metric = _safe_float(record.get("oos_rank_ic_avg")) or _mean(oos_rank_ics)
    relative_drop = _relative_drop(is_metric, oos_metric)
    def _oos_rank_ic_sort_key(item: dict[str, Any]) -> float:
        value = _safe_float(item.get("oos_rank_ic"))
        return value if value is not None else 999.0

    worst_windows = sorted(
        windows,
        key=_oos_rank_ic_sort_key,
    )[:8]
    payload = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_id": record.get("model_id"),
        "run_id": record.get("run_id"),
        "built_at": record.get("built_at"),
        "n_windows": len(windows),
        "true_is_oos_gate": {
            "is_rank_ic": is_metric,
            "oos_rank_ic_avg": oos_metric,
            "relative_drop": relative_drop,
            "threshold": relative_drop_threshold,
            "passes": bool(relative_drop is not None and relative_drop <= relative_drop_threshold),
        },
        "window_stability": {
            "train_rank_ic": _stats(train_rank_ics),
            "oos_rank_ic": _stats(oos_rank_ics),
            "rank_ic_relative_drop": _stats(rel_drops),
            "n_negative_oos_rank_ic": sum(1 for value in oos_rank_ics if value < 0),
            "strategy_return": _stats(strategy_returns),
            "corr_oos_rank_ic_vs_strategy_return": _corr(
                [w.get("oos_rank_ic") for w in windows],
                [w.get("strategy_return") for w in windows],
            ),
            "corr_rank_ic_drop_vs_strategy_return": _corr(
                [w.get("rank_ic_relative_drop") for w in windows],
                [w.get("strategy_return") for w in windows],
            ),
        },
        "regime_breakdown": _group_breakdown(windows, "regime_state"),
        "worst_oos_rank_ic_windows": worst_windows,
        "recent_windows": windows[-6:],
        "phase4_gate": phase4_gate,
        "strategy_config": strategy_config,
        "recommendations": [],
    }
    recommendations = payload["recommendations"]
    if not payload["true_is_oos_gate"]["passes"]:
        recommendations.append(
            "Do not promote this model family until true train/test RankIC drop is reduced; proxy split-half evidence is insufficient."
        )
    if payload["window_stability"]["oos_rank_ic"]["positive_rate"] is not None:
        positive_rate = payload["window_stability"]["oos_rank_ic"]["positive_rate"]
        if positive_rate < 0.70:
            recommendations.append(
                f"Prioritize alpha or entry/exit changes that improve monthly sign stability; OOS RankIC positive rate is {positive_rate:.1%}."
            )
    bad_regimes = [
        row for row in payload["regime_breakdown"]
        if (row["oos_rank_ic"]["mean"] is not None and row["oos_rank_ic"]["mean"] < 0)
    ]
    if bad_regimes:
        names = ", ".join(str(row["regime_state"]) for row in bad_regimes)
        recommendations.append(f"Inspect regime-conditioned gating for negative mean OOS RankIC regimes: {names}.")
    if payload["window_stability"]["corr_oos_rank_ic_vs_strategy_return"] is not None:
        corr = payload["window_stability"]["corr_oos_rank_ic_vs_strategy_return"]
        if corr > 0.30:
            recommendations.append(
                f"Strategy returns are meaningfully aligned with OOS RankIC (corr={corr:.2f}); window-level rank-stability filters may be useful if PIT-predictable."
            )
    return payload


def _load_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.artifact_json is not None:
        return json.loads(args.artifact_json.read_text(encoding="utf-8"))
    record = load_model_train_log(args.smartmoney_db, args.model_id)
    if record is None:
        raise RuntimeError(f"no fact_model_train_log row found for model_id={args.model_id}")
    return record


def _load_phase4_gate(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    gate = payload.get("gate_result") or {}
    is_oos = gate.get("is_oos") or {}
    return {
        "path": str(path),
        "all_pass": gate.get("all_pass"),
        "promote_action": gate.get("promote_action"),
        "is_oos_passes": is_oos.get("passes"),
        "is_oos_reason": is_oos.get("reason"),
        "is_oos_proxy_mode": payload.get("is_oos_proxy_mode"),
    }


def _strategy_returns(args: argparse.Namespace) -> tuple[list[tuple[pd.Timestamp, float]], dict[str, Any]]:
    preds = load_predictions(args.smartmoney_db, args.model_id)
    if preds.empty:
        raise RuntimeError(f"No predictions found for model_id={args.model_id}")
    hs300 = load_hs300_kline(args.market_db)
    sniper_by_sd = None
    if args.sniper_weight is not None and args.sniper_weight > 0:
        start = str(preds["signal_date"].min())[:10]
        end = str(preds["signal_date"].max())[:10]
        sniper_df = load_sniper_scores(args.smartmoney_db, start, end)
        sniper_by_sd = {
            sd: group.set_index("stock_code")["sniper_score"]
            for sd, group in sniper_df.groupby("signal_date", sort=False)
        }
    config = {
        "horizon": args.horizon,
        "max_positions": args.max_positions,
        "lambdamart_weight": args.lambdamart_weight,
        "sniper_weight": args.sniper_weight,
        "institution_weight": args.institution_weight,
        "min_top_score": args.min_top_score,
        "min_sniper_score": args.min_sniper_score,
        "rank_decay": args.rank_decay,
        "neutral_cash_pct": args.neutral_cash_pct,
    }
    returns = compute_port_returns(
        preds,
        args.horizon,
        hs300,
        max_positions=args.max_positions,
        sniper_by_sd=sniper_by_sd,
        lambdamart_weight=args.lambdamart_weight,
        sniper_weight=args.sniper_weight,
        institution_weight=args.institution_weight,
        min_top_score=args.min_top_score,
        min_sniper_score=args.min_sniper_score,
        rank_decay=args.rank_decay,
        neutral_cash_pct=args.neutral_cash_pct,
    )
    return returns, config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit LambdaMART true train-log OOS stability")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--artifact-json", type=Path, default=None)
    parser.add_argument("--phase4-gate-json", type=Path, default=None)
    parser.add_argument("--relative-drop-threshold", type=float, default=DEFAULT_IS_OOS_RELATIVE_DROP_THRESHOLD)
    parser.add_argument("--compute-strategy-window-returns", action="store_true")
    parser.add_argument("--horizon", choices=["5d", "10d", "20d"], default="10d")
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--lambdamart-weight", type=float, default=0.735)
    parser.add_argument("--sniper-weight", type=float, default=0.265)
    parser.add_argument("--institution-weight", type=float, default=0.0)
    parser.add_argument("--min-top-score", type=float, default=None)
    parser.add_argument("--min-sniper-score", type=float, default=None)
    parser.add_argument("--rank-decay", type=float, default=None)
    parser.add_argument("--neutral-cash-pct", type=float, default=0.2)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args(argv)

    record = _load_record(args)
    windows = parse_window_metrics(record)
    if not windows:
        raise RuntimeError("train-log metrics_json contains no window_metrics")
    hs300 = load_hs300_kline(args.market_db)
    attach_regime(windows, hs300)
    strategy_config = None
    if args.compute_strategy_window_returns:
        returns, strategy_config = _strategy_returns(args)
        attach_strategy_returns(windows, returns)
    phase4_gate = _load_phase4_gate(args.phase4_gate_json)
    payload = build_stability_payload(
        record,
        windows,
        relative_drop_threshold=args.relative_drop_threshold,
        phase4_gate=phase4_gate,
        strategy_config=strategy_config,
    )
    if args.output_json is None:
        args.output_json = REPORTS_DIR / f"lambdamart_train_log_stability_{args.model_id}.json"
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    summary = {
        "model_id": payload["model_id"],
        "n_windows": payload["n_windows"],
        "true_is_oos_gate": payload["true_is_oos_gate"],
        "oos_rank_ic_positive_rate": payload["window_stability"]["oos_rank_ic"]["positive_rate"],
        "n_negative_oos_rank_ic": payload["window_stability"]["n_negative_oos_rank_ic"],
        "corr_oos_rank_ic_vs_strategy_return": payload["window_stability"]["corr_oos_rank_ic_vs_strategy_return"],
        "json_out": str(args.output_json),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
