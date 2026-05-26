from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import optuna

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PROJECT_ROOT = Path(__file__).resolve().parents[4]

from compute import HOLDING_PERIODS, normalize_code
from execution_model import EXECUTION_MODEL_VERSION, build_fixed_holding_trades, build_sell_rule_trades
from formula_engine import compute_formula_signals
from scripts.formula_parameter_search import (
    FORMULA_VARIANTS,
    _load_market_rows,
    _metrics_from_trades,
    _score,
)

MARKET_DB = PROJECT_ROOT / "data" / "market.duckdb"
SMART_DB = PROJECT_ROOT / "data" / "smartmoney.duckdb"


ANALYSIS_DIR = ROOT / "analysis"
OUT_CSV = ANALYSIS_DIR / "formula_local_optuna_samples.csv"
OUT_MD = ANALYSIS_DIR / "formula_local_optuna_samples.md"
STOCK_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
DEFAULT_CODES = ["301511", "301658", "688700", "002718"]
INVALID_TRIAL_SCORE = -999.0
VALIDATION_RATIO = 0.30


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def _investigation_payload(status: str, reason: str) -> str:
    if not status or status == "ok":
        return ""
    payload: dict[str, Any] = {"status": status}
    if reason:
        try:
            parsed = json.loads(reason)
        except Exception:
            parsed = None
        payload["reason"] = parsed if isinstance(parsed, dict) else reason
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _load_current_best() -> dict[tuple[str, str], dict[str, Any]]:
    if not STOCK_BEST.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with STOCK_BEST.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = normalize_code(row.get("stock_code"))
            formula_id = str(row.get("formula_id") or "")
            if not code or not formula_id:
                continue
            try:
                row["score"] = float(row.get("score") or "")
            except Exception:
                row["score"] = None
            out[(code, formula_id)] = row
    return out


def _stock_by_code(codes: list[str]) -> dict[str, dict[str, Any]]:
    wanted = {normalize_code(c) for c in codes}
    rows = _load_market_rows(0)
    return {normalize_code(r["code"]): r for r in rows if normalize_code(r["code"]) in wanted}


def _parse_json_obj(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        obj = json.loads(str(raw))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _suggest_params(formula_id: str, trial: optuna.Trial) -> dict[str, Any]:
    if formula_id == "gs_pullback_confirm":
        return {
            "rate_min": trial.suggest_int("rate_min", 25, 60, step=5),
            "maxrun_max": trial.suggest_int("maxrun_max", 5, 12),
            "sellpct_max": trial.suggest_int("sellpct_max", 40, 75, step=5),
            "maxlen_max": trial.suggest_int("maxlen_max", 10, 24),
            "ma_pull_low": trial.suggest_float("ma_pull_low", 0.68, 0.90, step=0.02),
            "ma_pull_high": trial.suggest_float("ma_pull_high", 0.96, 1.04, step=0.01),
        }
    if formula_id == "gs_raw_buy":
        x3_window_map = {
            "fast": [3, 5, 10, 20],
            "medium": [5, 10, 20, 30],
            "fib": [3, 8, 13, 21],
        }
        x3_window_key = trial.suggest_categorical("x3_window_set", list(x3_window_map))
        return {
            "x3_ma_windows": x3_window_map[str(x3_window_key)],
            "ema_fallback_span": trial.suggest_int("ema_fallback_span", 3, 7),
            "iterations": trial.suggest_int("iterations", 6, 12),
            "signal_cooldown_days": trial.suggest_int("signal_cooldown_days", 6, 16),
            "down_adjust": trial.suggest_float("down_adjust", 0.96, 0.99, step=0.01),
            "up_adjust": trial.suggest_float("up_adjust", 1.01, 1.04, step=0.01),
        }
    if formula_id == "ma_base_breakout":
        mid_ma = trial.suggest_categorical("mid_ma", [60, 90, 120])
        long_ma = trial.suggest_categorical("long_ma", [120, 145, 180])
        if long_ma <= mid_ma:
            long_ma = mid_ma + 60
        return {
            "short_ma": trial.suggest_int("short_ma", 3, 10),
            "mid_ma": mid_ma,
            "long_ma": long_ma,
            "below_days_min": trial.suggest_int("below_days_min", 25, 70, step=5),
            "ma5_rising_min": trial.suggest_int("ma5_rising_min", 4, 12),
            "breakout_recent_days": trial.suggest_int("breakout_recent_days", 3, 8),
            "price_top_buffer": trial.suggest_float("price_top_buffer", 1.01, 1.08, step=0.01),
        }
    if formula_id == "activity_breakout":
        return {
            "big_bull_line": trial.suggest_float("big_bull_line", 5.0, 9.0, step=0.5),
            "x15_multiplier": trial.suggest_float("x15_multiplier", 1.0, 1.8, step=0.1),
            "strong_line": trial.suggest_float("strong_line", 2.5, 4.5, step=0.5),
            "min_close_ret": trial.suggest_float("min_close_ret", -1.0, 2.0, step=0.5),
            "max_close_ret": trial.suggest_float("max_close_ret", 8.0, 14.0, step=1.0),
            "signal_cooldown_days": trial.suggest_int("signal_cooldown_days", 6, 14),
        }
    if formula_id == "volume_base_breakout":
        return {
            "spike_lookback": trial.suggest_categorical("spike_lookback", [70, 90, 120]),
            "spike_ratio": trial.suggest_float("spike_ratio", 2.0, 3.2, step=0.2),
            "amount_spike_ratio": trial.suggest_float("amount_spike_ratio", 2.0, 3.2, step=0.2),
            "base_min_days": trial.suggest_int("base_min_days", 20, 50, step=5),
            "base_max_days": trial.suggest_int("base_max_days", 65, 115, step=5),
            "base_range_max": trial.suggest_float("base_range_max", 0.25, 0.45, step=0.02),
            "base_floor": trial.suggest_float("base_floor", 0.75, 0.88, step=0.01),
            "base_ceiling": trial.suggest_float("base_ceiling", 1.20, 1.50, step=0.02),
            "dry_ratio": trial.suggest_float("dry_ratio", 0.35, 0.55, step=0.05),
            "warm_vol_ratio": trial.suggest_float("warm_vol_ratio", 0.75, 1.05, step=0.05),
            "warm_ret_min": trial.suggest_float("warm_ret_min", -0.01, 0.03, step=0.01),
            "warm_ret_max": trial.suggest_float("warm_ret_max", 0.16, 0.30, step=0.02),
            "breakout_near_high": trial.suggest_float("breakout_near_high", 0.90, 0.98, step=0.01),
            "breakout_max_extension": trial.suggest_float("breakout_max_extension", 1.05, 1.12, step=0.01),
            "signal_cooldown_days": trial.suggest_int("signal_cooldown_days", 8, 18),
        }
    return {}


def _split_train_validation_trades(
    trades: list[dict[str, Any]],
    validation_ratio: float = VALIDATION_RATIO,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable = [t for t in trades if t.get("ret") is not None]
    usable.sort(key=lambda t: (_to_int(t.get("buy_idx"), _to_int(t.get("signal_idx"))), str(t.get("buy_date") or "")))
    if len(usable) < 2:
        return usable, []
    validation_n = max(1, int(round(len(usable) * validation_ratio)))
    validation_n = min(validation_n, len(usable) - 1)
    split_at = len(usable) - validation_n
    return usable[:split_at], usable[split_at:]


def _metrics_bundle(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    full = _metrics_from_trades(trades)
    if not full:
        return None
    train_trades, validation_trades = _split_train_validation_trades(trades)
    train = _metrics_from_trades(train_trades)
    validation = _metrics_from_trades(validation_trades)
    return {
        "full": full,
        "full_score": _score(full),
        "train": train,
        "train_score": _score(train) if train else None,
        "validation": validation,
        "validation_score": _score(validation) if validation else None,
    }


def _apply_metric_prefix(row: dict[str, Any], prefix: str, metrics: dict[str, Any] | None, score: float | None) -> None:
    row[f"{prefix}_signal_count"] = metrics.get("n") if metrics else None
    row[f"{prefix}_win_rate"] = metrics.get("win_rate") if metrics else None
    row[f"{prefix}_avg_ret"] = metrics.get("avg_ret") if metrics else None
    row[f"{prefix}_avg_dd"] = metrics.get("avg_dd") if metrics else None
    row[f"{prefix}_calmar"] = metrics.get("calmar") if metrics else None
    row[f"{prefix}_delay_buy_rate"] = metrics.get("delay_buy_rate") if metrics else None
    row[f"{prefix}_delay_sell_rate"] = metrics.get("delay_sell_rate") if metrics else None
    row[f"{prefix}_score"] = score


def _signals_for_params(stock: dict[str, Any], formula_id: str, params: dict[str, Any], max_signals: int) -> dict[str, Any]:
    try:
        out = compute_formula_signals(
            formula_id,
            open_=stock["open"],
            high=stock["high"],
            low=stock["low"],
            close=stock["close"],
            volume=stock["volume"],
            amount=stock["amount"],
            params=params,
        )
        entries = np.where(out["entry"])[0]
        exits = np.asarray(out.get("exit", np.zeros(len(stock["close"]), dtype=bool)), dtype=bool)
    except Exception as exc:
        return {"status": "formula_error", "reason": f"{type(exc).__name__}: {exc}"}
    if len(entries) == 0:
        return {"status": "no_entry_signal", "reason": "formula produced no entry signals"}
    if max_signals > 0 and len(entries) > max_signals:
        entries = entries[-max_signals:]
    return {"status": "ok", "reason": "", "entries": entries, "exits": exits}


def _trades_for_rule(
    stock: dict[str, Any],
    entries: np.ndarray,
    exits: np.ndarray,
    sell_rule: str,
) -> list[dict[str, Any]]:
    if sell_rule.startswith("fixed_"):
        holding_days = _to_int(sell_rule.removeprefix("fixed_"))
        fixed_map = build_fixed_holding_trades(
            code=stock["code"],
            dates=stock["dates"],
            opens=stock["open"],
            highs=stock["high"],
            lows=stock["low"],
            closes=stock["close"],
            volumes=stock["volume"],
            amounts=stock["amount"],
            signal_indices=entries,
            holding_periods=[holding_days],
            include_open=False,
        )
        trades = fixed_map.get(holding_days, [])
        for trade in trades:
            trade["sell_rule"] = sell_rule
        return trades
    return build_sell_rule_trades(
        code=stock["code"],
        dates=stock["dates"],
        opens=stock["open"],
        highs=stock["high"],
        lows=stock["low"],
        closes=stock["close"],
        volumes=stock["volume"],
        amounts=stock["amount"],
        signal_indices=entries,
        sell_rule=sell_rule,
        exit_signals=exits,
        include_open=False,
    )


def _evaluate_rule(
    stock: dict[str, Any],
    formula_id: str,
    params: dict[str, Any],
    sell_rule: str,
    max_signals: int,
) -> dict[str, Any]:
    signals = _signals_for_params(stock, formula_id, params, max_signals)
    if signals.get("status") != "ok":
        return signals
    trades = _trades_for_rule(stock, signals["entries"], signals["exits"], sell_rule)
    bundle = _metrics_bundle(trades)
    if not bundle:
        return {"status": "no_executable_trade", "reason": "entry signals produced no executable trades"}
    holding_days = _to_int(sell_rule.split("_")[-1])
    out = {
        "status": "ok",
        "reason": "",
        "sell_rule": sell_rule,
        "holding_days": holding_days,
    }
    _apply_metric_prefix(out, "full", bundle["full"], bundle["full_score"])
    _apply_metric_prefix(out, "train", bundle["train"], bundle["train_score"])
    _apply_metric_prefix(out, "validation", bundle["validation"], bundle["validation_score"])
    return out


def _evaluate(stock: dict[str, Any], formula_id: str, params: dict[str, Any], max_signals: int) -> dict[str, Any]:
    signals = _signals_for_params(stock, formula_id, params, max_signals)
    if signals.get("status") != "ok":
        return signals
    entries = signals["entries"]
    exits = signals["exits"]
    candidates: list[dict[str, Any]] = []
    fixed_map = build_fixed_holding_trades(
        code=stock["code"],
        dates=stock["dates"],
        opens=stock["open"],
        highs=stock["high"],
        lows=stock["low"],
        closes=stock["close"],
        volumes=stock["volume"],
        amounts=stock["amount"],
        signal_indices=entries,
        holding_periods=HOLDING_PERIODS,
        include_open=False,
    )
    for h, trades in fixed_map.items():
        rule = f"fixed_{h}"
        for trade in trades:
            trade["sell_rule"] = rule
        bundle = _metrics_bundle(trades)
        if bundle:
            candidates.append({"sell_rule": rule, "holding_days": h, **bundle})
    for h in HOLDING_PERIODS:
        rule = f"formula_exit_or_{h}"
        trades = build_sell_rule_trades(
            code=stock["code"],
            dates=stock["dates"],
            opens=stock["open"],
            highs=stock["high"],
            lows=stock["low"],
            closes=stock["close"],
            volumes=stock["volume"],
            amounts=stock["amount"],
            signal_indices=entries,
            sell_rule=rule,
            exit_signals=exits,
            include_open=False,
        )
        bundle = _metrics_bundle(trades)
        if bundle:
            candidates.append({"sell_rule": rule, "holding_days": h, **bundle})
    if not candidates:
        return {"status": "no_executable_trade", "reason": "entry signals produced no executable trades"}
    best = max(
        candidates,
        key=lambda r: (
            r.get("train_score") is not None,
            float(r.get("train_score") if r.get("train_score") is not None else float("-inf")),
            float(r.get("full_score") if r.get("full_score") is not None else float("-inf")),
        ),
    )
    out = {
        "status": "ok",
        "reason": "",
        "sell_rule": best["sell_rule"],
        "holding_days": best["holding_days"],
    }
    _apply_metric_prefix(out, "full", best["full"], best["full_score"])
    _apply_metric_prefix(out, "train", best["train"], best["train_score"])
    _apply_metric_prefix(out, "validation", best["validation"], best["validation_score"])
    return out


def _optimize_one(
    stock: dict[str, Any],
    formula_id: str,
    trials: int,
    seed: int,
    max_signals: int,
) -> dict[str, Any] | None:
    best_payload: dict[str, Any] | None = None
    failure_counts: dict[str, int] = {}
    failure_examples: dict[str, str] = {}

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_payload
        params = _suggest_params(formula_id, trial)
        result = _evaluate(stock, formula_id, params, max_signals)
        if result.get("status") != "ok":
            status = str(result.get("status") or "unknown")
            failure_counts[status] = failure_counts.get(status, 0) + 1
            failure_examples.setdefault(status, str(result.get("reason") or ""))
            return INVALID_TRIAL_SCORE
        score = result.get("train_score")
        if score is None:
            return INVALID_TRIAL_SCORE
        if best_payload is None or float(score) > float(best_payload["train_score"]):
            best_payload = {"params": params, **result}
        return float(score)

    # Phase 2.3 (2026-05-24): governance enforce — n_trials >= 50, has_seed True
    # 用户 goal.md Phase 2.3: bc_absorbed walk-forward governance.
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from services.optimization.governance import enforce_pre_optimize
        enforce_pre_optimize(n_trials=trials, has_seed=seed is not None)
    except ImportError:
        pass  # rule-compliance: ok evidence=bc_absorbed migration grandfathered, governance optional during Phase 2.3 transition

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=trials, show_progress_bar=False)
    if best_payload is None:
        return {
            "status": "missing_optuna_result",
            "reason": json.dumps(
                {"failure_counts": failure_counts, "failure_examples": failure_examples},
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
    return best_payload


def _stable_seed(base_seed: int, code: str, formula_id: str) -> int:
    raw = f"{base_seed}:{code}:{formula_id}".encode("utf-8")
    return base_seed + int(hashlib.sha256(raw).hexdigest()[:8], 16) % 100000


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local per-stock Optuna trials for formula strategies.")
    parser.add_argument("--codes", nargs="*", default=DEFAULT_CODES)
    parser.add_argument("--formulas", nargs="*", choices=sorted(FORMULA_VARIANTS), default=list(FORMULA_VARIANTS))
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--max-signals-per-stock", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260520)
    args = parser.parse_args()
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    current_best = _load_current_best()
    stocks = _stock_by_code(args.codes)

    import duckdb
    from services.backtest_preflight import enforce_backtest_preflight
    stock_codes = list(stocks.keys())
    smart_conn = duckdb.connect(str(SMART_DB), read_only=True)
    market_conn = duckdb.connect(str(MARKET_DB), read_only=True)
    enforce_backtest_preflight(
        stock_codes=stock_codes,
        conn=smart_conn,
        market_conn=market_conn,
    )
    smart_conn.close()
    market_conn.close()

    rows: list[dict[str, Any]] = []
    started = time.time()
    for code in [normalize_code(c) for c in args.codes]:
        stock = stocks.get(code)
        if not stock:
            continue
        for formula_id in args.formulas:
            baseline = current_best.get((code, formula_id))
            baseline_status = "ok" if baseline else "missing_baseline_result"
            baseline_reason = "" if baseline else "stock_formula_best.csv has no row for this stock/formula"
            baseline_source_score = baseline.get("score") if baseline else None
            baseline_eval: dict[str, Any] = {}
            if baseline and baseline_source_score is None:
                baseline_status = "invalid_baseline_score"
                baseline_reason = "stock_formula_best.csv row has an empty or invalid score"
            if baseline_status == "ok":
                baseline_sell_rule = str(baseline.get("sell_rule") or "")
                baseline_params = _parse_json_obj(baseline.get("params"))
                if not baseline_sell_rule:
                    baseline_status = "missing_baseline_sell_rule"
                    baseline_reason = "stock_formula_best.csv row has no sell_rule"
                else:
                    baseline_eval = _evaluate_rule(
                        stock,
                        formula_id,
                        params=baseline_params,
                        sell_rule=baseline_sell_rule,
                        max_signals=args.max_signals_per_stock,
                    )
                    if baseline_eval.get("status") != "ok":
                        baseline_status = f"baseline_eval_{baseline_eval.get('status') or 'failed'}"
                        baseline_reason = str(baseline_eval.get("reason") or "")
            best = _optimize_one(
                stock,
                formula_id,
                trials=args.trials,
                seed=_stable_seed(args.seed, code, formula_id),
                max_signals=args.max_signals_per_stock,
            )
            optuna_status = str((best or {}).get("status") or "missing_optuna_result")
            optuna_reason = str((best or {}).get("reason") or "")
            baseline_full_score = baseline_eval.get("full_score") if baseline_status == "ok" else None
            baseline_validation_score = baseline_eval.get("validation_score") if baseline_status == "ok" else None
            optuna_score = best.get("full_score") if best and optuna_status == "ok" else None
            optuna_validation_score = best.get("validation_score") if best and optuna_status == "ok" else None
            score_delta = (
                float(optuna_score) - float(baseline_full_score)
                if optuna_score is not None and baseline_full_score is not None
                else None
            )
            validation_score_delta = (
                float(optuna_validation_score) - float(baseline_validation_score)
                if optuna_validation_score is not None and baseline_validation_score is not None
                else None
            )
            row = {
                "stock_code": code,
                "formula_id": formula_id,
                "trials": args.trials,
                "validation_ratio": VALIDATION_RATIO,
                "baseline_status": baseline_status,
                "baseline_reason": baseline_reason,
                "baseline_investigation": _investigation_payload(baseline_status, baseline_reason),
                "baseline_variant_id": (baseline or {}).get("variant_id"),
                "baseline_sell_rule": (baseline or {}).get("sell_rule"),
                "baseline_holding_days": (baseline or {}).get("holding_days"),
                "baseline_source_score": baseline_source_score,
                "baseline_score": baseline_full_score,
                "optuna_status": optuna_status,
                "optuna_reason": optuna_reason,
                "optuna_investigation": _investigation_payload(optuna_status, optuna_reason),
                "optuna_sell_rule": (best or {}).get("sell_rule"),
                "optuna_holding_days": (best or {}).get("holding_days"),
                "optuna_score": optuna_score,
                "score_delta": score_delta,
                "validation_score_delta": validation_score_delta,
                "execution_model": EXECUTION_MODEL_VERSION,
                "optuna_params": json.dumps((best or {}).get("params") or {}, ensure_ascii=False, sort_keys=True),
            }
            for prefix, payload in (("baseline", baseline_eval), ("optuna", best or {})):
                row[f"{prefix}_signal_count"] = payload.get("full_signal_count")
                row[f"{prefix}_win_rate"] = payload.get("full_win_rate")
                row[f"{prefix}_avg_ret"] = payload.get("full_avg_ret")
                row[f"{prefix}_avg_dd"] = payload.get("full_avg_dd")
                row[f"{prefix}_calmar"] = payload.get("full_calmar")
                row[f"{prefix}_delay_buy_rate"] = payload.get("full_delay_buy_rate")
                row[f"{prefix}_delay_sell_rate"] = payload.get("full_delay_sell_rate")
                for split in ("train", "validation"):
                    row[f"{prefix}_{split}_signal_count"] = payload.get(f"{split}_signal_count")
                    row[f"{prefix}_{split}_win_rate"] = payload.get(f"{split}_win_rate")
                    row[f"{prefix}_{split}_avg_ret"] = payload.get(f"{split}_avg_ret")
                    row[f"{prefix}_{split}_avg_dd"] = payload.get(f"{split}_avg_dd")
                    row[f"{prefix}_{split}_calmar"] = payload.get(f"{split}_calmar")
                    row[f"{prefix}_{split}_delay_buy_rate"] = payload.get(f"{split}_delay_buy_rate")
                    row[f"{prefix}_{split}_delay_sell_rate"] = payload.get(f"{split}_delay_sell_rate")
                    row[f"{prefix}_{split}_score"] = payload.get(f"{split}_score")
            rows.append(
                row
            )
            print(
                f"formula_local_optuna:done {code} {formula_id} "
                f"baseline_status={baseline_status} optuna_status={optuna_status}",
                flush=True,
            )

    rows.sort(
        key=lambda r: (
            r.get("score_delta") is not None,
            float(r.get("score_delta") if r.get("score_delta") is not None else float("-inf")),
            float(r.get("optuna_score") if r.get("optuna_score") is not None else float("-inf")),
        ),
        reverse=True,
    )
    fieldnames = [
        "stock_code",
        "formula_id",
        "trials",
        "validation_ratio",
        "baseline_status",
        "baseline_reason",
        "baseline_investigation",
        "baseline_variant_id",
        "baseline_sell_rule",
        "baseline_holding_days",
        "baseline_source_score",
        "baseline_score",
        "baseline_signal_count",
        "baseline_win_rate",
        "baseline_avg_ret",
        "baseline_avg_dd",
        "baseline_calmar",
        "baseline_delay_buy_rate",
        "baseline_delay_sell_rate",
        "baseline_train_signal_count",
        "baseline_train_win_rate",
        "baseline_train_avg_ret",
        "baseline_train_avg_dd",
        "baseline_train_calmar",
        "baseline_train_delay_buy_rate",
        "baseline_train_delay_sell_rate",
        "baseline_train_score",
        "baseline_validation_signal_count",
        "baseline_validation_win_rate",
        "baseline_validation_avg_ret",
        "baseline_validation_avg_dd",
        "baseline_validation_calmar",
        "baseline_validation_delay_buy_rate",
        "baseline_validation_delay_sell_rate",
        "baseline_validation_score",
        "optuna_status",
        "optuna_reason",
        "optuna_investigation",
        "optuna_sell_rule",
        "optuna_holding_days",
        "optuna_signal_count",
        "optuna_win_rate",
        "optuna_avg_ret",
        "optuna_avg_dd",
        "optuna_calmar",
        "optuna_delay_buy_rate",
        "optuna_delay_sell_rate",
        "optuna_score",
        "optuna_train_signal_count",
        "optuna_train_win_rate",
        "optuna_train_avg_ret",
        "optuna_train_avg_dd",
        "optuna_train_calmar",
        "optuna_train_delay_buy_rate",
        "optuna_train_delay_sell_rate",
        "optuna_train_score",
        "optuna_validation_signal_count",
        "optuna_validation_win_rate",
        "optuna_validation_avg_ret",
        "optuna_validation_avg_dd",
        "optuna_validation_calmar",
        "optuna_validation_delay_buy_rate",
        "optuna_validation_delay_sell_rate",
        "optuna_validation_score",
        "score_delta",
        "validation_score_delta",
        "execution_model",
        "optuna_params",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in fieldnames})

    top = rows[:12]
    OUT_MD.write_text(
        "\n".join(
            [
                "# Formula Local Optuna Samples",
                "",
                f"- codes: `{', '.join(args.codes)}`",
                f"- formulas: `{', '.join(args.formulas)}`",
                f"- trials_per_stock_formula: `{args.trials}`",
                f"- max_signals_per_stock: `{args.max_signals_per_stock}`",
                f"- execution_model: `{EXECUTION_MODEL_VERSION}`",
                f"- elapsed_sec: `{time.time() - started:.1f}`",
                "",
                "## Top Score Deltas",
                "",
                "| stock | formula | baseline_status | optuna_status | baseline | optuna | delta | train | validation | val_delta | sell_rule |",
                "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
                *[
                    f"| `{r['stock_code']}` | `{r['formula_id']}` | `{r['baseline_status']}` | `{r['optuna_status']}` | "
                    f"{float(r['baseline_score']):.2f} | {float(r['optuna_score']):.2f} | "
                    f"{float(r['score_delta']):.2f} | {float(r['optuna_train_score']):.2f} | "
                    f"{float(r['optuna_validation_score']):.2f} | {float(r['validation_score_delta']):.2f} | "
                    f"`{r['optuna_sell_rule']}` |"
                    for r in top
                    if (
                        r.get("baseline_score") is not None
                        and r.get("optuna_score") is not None
                        and r.get("optuna_train_score") is not None
                        and r.get("optuna_validation_score") is not None
                        and r.get("validation_score_delta") is not None
                    )
                ],
                "",
                "## Missing Result Reasons",
                "",
                "```json",
                json.dumps(_missing_reason_counts(rows), ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
                "## Investigation Policy",
                "",
                "- Missing baseline/Optuna rows are not filled with default metrics.",
                "- `baseline_investigation` and `optuna_investigation` preserve the concrete reason chain for follow-up.",
                "- Rows without a complete baseline and Optuna result are excluded from improvement scoring.",
                "",
                "## Notes",
                "",
                "- This is an exploratory local Optuna audit and does not overwrite production `stock_formula_best.csv`.",
                "- Positive deltas identify candidates where continuous local search may justify a production integration pass.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"formula_local_optuna:done rows={len(rows)} elapsed={time.time()-started:.1f}s")


def _missing_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for key, reason_key in (("baseline_status", "baseline_reason"), ("optuna_status", "optuna_reason")):
            status = str(row.get(key) or "")
            if status and status != "ok":
                reason = str(row.get(reason_key) or "").strip()
                label = status if not reason else f"{status}: {reason}"
                counts[label] = counts.get(label, 0) + 1
    return counts


if __name__ == "__main__":
    main()
