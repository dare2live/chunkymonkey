from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PROJECT_ROOT = Path(__file__).resolve().parents[4]

from compute import HOLDING_PERIODS, _attach_smart_db, normalize_code
from execution_model import EXECUTION_MODEL_VERSION, build_fixed_holding_trades, build_sell_rule_trades
from formula_engine import compute_formula_signals

MARKET_DB = PROJECT_ROOT / "data" / "market.duckdb"
SMART_DB = PROJECT_ROOT / "data" / "smartmoney.duckdb"


ANALYSIS_DIR = ROOT / "analysis"
VARIANT_METRICS = ANALYSIS_DIR / "formula_variant_metrics.csv"
STOCK_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
SEARCH_REPORT = ANALYSIS_DIR / "formula_parameter_search_report.md"


FORMULA_VARIANTS: dict[str, list[dict[str, Any]]] = {
    "gs_pullback_confirm": [
        {"variant_id": "default", "params": {}},
        {"variant_id": "loose", "params": {"rate_min": 30, "maxrun_max": 10, "sellpct_max": 70, "ma_pull_low": 0.70, "ma_pull_high": 1.02}},
        {"variant_id": "strict", "params": {"rate_min": 50, "maxrun_max": 6, "sellpct_max": 50, "maxlen_max": 15, "ma_pull_low": 0.85, "ma_pull_high": 0.98}},
    ],
    "gs_raw_buy": [
        {"variant_id": "default", "params": {}},
        {"variant_id": "fast_x3_cooldown", "params": {"x3_ma_windows": [3, 5, 10, 20], "ema_fallback_span": 3, "iterations": 8, "signal_cooldown_days": 8}},
        {"variant_id": "medium_x3_cooldown", "params": {"x3_ma_windows": [5, 10, 20, 30], "ema_fallback_span": 5, "iterations": 10, "signal_cooldown_days": 10}},
        {"variant_id": "wide_band_cooldown", "params": {"down_adjust": 0.97, "up_adjust": 1.03, "iterations": 8, "signal_cooldown_days": 12}},
    ],
    "ma_base_breakout": [
        {"variant_id": "default", "params": {}},
        {"variant_id": "early_60_120", "params": {"short_ma": 3, "mid_ma": 60, "long_ma": 120, "below_days_min": 30, "ma5_rising_min": 5, "breakout_recent_days": 5}},
        {"variant_id": "classic_90_145", "params": {"short_ma": 5, "mid_ma": 90, "long_ma": 145, "below_days_min": 45, "ma5_rising_min": 7}},
        {"variant_id": "strict_120_180", "params": {"short_ma": 8, "mid_ma": 120, "long_ma": 180, "below_days_min": 60, "ma5_rising_min": 10, "price_top_buffer": 1.03}},
    ],
    "activity_breakout": [
        {"variant_id": "default", "params": {}},
        {"variant_id": "classic_capped", "params": {"big_bull_line": 6.0, "x15_multiplier": 1.2, "strong_line": 3.0, "min_close_ret": 0.0, "max_close_ret": 12.0, "signal_cooldown_days": 8}},
        {"variant_id": "strict_capped", "params": {"big_bull_line": 8.0, "x15_multiplier": 1.5, "strong_line": 4.0, "min_close_ret": 0.0, "max_close_ret": 10.0, "signal_cooldown_days": 10}},
    ],
    "volume_base_breakout": [
        {"variant_id": "default_broad", "params": {}},
        {"variant_id": "case_301511_broad", "params": {"spike_lookback": 90, "spike_ratio": 2.2, "amount_spike_ratio": 2.2, "base_min_days": 25, "base_max_days": 75, "base_range_max": 0.40, "base_floor": 0.78, "base_ceiling": 1.45, "dry_ratio": 0.50, "warm_vol_ratio": 0.80, "warm_ret_min": 0.0, "warm_ret_max": 0.28, "breakout_near_high": 0.92, "breakout_max_extension": 1.10}},
        {"variant_id": "case_301511_cooldown", "params": {"spike_lookback": 90, "spike_ratio": 2.2, "amount_spike_ratio": 2.2, "base_min_days": 25, "base_max_days": 75, "base_range_max": 0.40, "base_floor": 0.78, "base_ceiling": 1.45, "dry_ratio": 0.50, "warm_vol_ratio": 0.80, "warm_ret_min": 0.0, "warm_ret_max": 0.28, "breakout_near_high": 0.92, "breakout_max_extension": 1.10, "signal_cooldown_days": 10}},
        {"variant_id": "strict_two_month_box", "params": {"spike_lookback": 90, "spike_ratio": 3.0, "amount_spike_ratio": 3.0, "base_min_days": 35, "base_max_days": 90, "base_range_max": 0.30, "base_floor": 0.85, "base_ceiling": 1.25, "dry_ratio": 0.40, "warm_vol_ratio": 1.00, "warm_ret_min": 0.01, "warm_ret_max": 0.20, "breakout_near_high": 0.97, "breakout_max_extension": 1.06, "signal_cooldown_days": 15}},
        {"variant_id": "long_quiet_box", "params": {"spike_lookback": 120, "spike_ratio": 2.5, "amount_spike_ratio": 2.5, "base_min_days": 45, "base_max_days": 110, "base_range_max": 0.32, "base_floor": 0.82, "base_ceiling": 1.30, "dry_ratio": 0.45, "warm_vol_ratio": 0.90, "warm_ret_min": 0.0, "warm_ret_max": 0.22, "breakout_near_high": 0.95, "signal_cooldown_days": 15}},
    ],
}


def _load_market_rows(max_stocks: int = 0) -> list[dict[str, Any]]:
    from services.universe import get_active_universe
    smart_conn = duckdb.connect(str(SMART_DB), read_only=True)
    universe = get_active_universe(smart_conn)
    smart_conn.close()

    con = duckdb.connect(str(MARKET_DB), read_only=True)
    try:
        raw = con.execute(
            """
            SELECT code, date, open, high, low, close, volume, amount
            FROM v_price_kline_qfq
            ORDER BY code, date
            """
        ).fetchnumpy()
    finally:
        con.close()

    codes = raw["code"]
    unique_codes, counts = np.unique(codes, return_counts=True)
    rows: list[dict[str, Any]] = []
    idx = 0
    for code_raw, cnt in zip(unique_codes, counts):
        if max_stocks and len(rows) >= max_stocks:
            break
        sl = slice(idx, idx + cnt)
        code = normalize_code(code_raw)
        idx += cnt
        if code not in universe:
            continue
        rows.append(
            {
                "code": code,
                "dates": raw["date"][sl],
                "open": raw["open"][sl].astype(np.float64),
                "high": raw["high"][sl].astype(np.float64),
                "low": raw["low"][sl].astype(np.float64),
                "close": raw["close"][sl].astype(np.float64),
                "volume": raw["volume"][sl].astype(np.float64),
                "amount": raw["amount"][sl].astype(np.float64),
            }
        )
    return rows


def _metrics_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [t for t in trades if t.get("ret") is not None]
    if not usable:
        return None
    rets = [float(t["ret"]) for t in usable]
    dds = [float(t.get("max_dd") or 0.0) for t in usable]
    avg_ret = float(np.mean(rets))
    avg_dd = float(np.mean(dds))
    win_rate = float(np.mean([r > 0 for r in rets]))
    calmar = avg_ret / max(abs(avg_dd), 0.005)
    delayed_buy = sum(1 for t in usable if int(t.get("delay_buy_days") or 0) > 0)
    delayed_sell = sum(1 for t in usable if int(t.get("delay_sell_days") or 0) > 0)
    return {
        "n": len(usable),
        "win_rate": win_rate,
        "avg_ret": avg_ret,
        "avg_dd": avg_dd,
        "calmar": calmar,
        "delay_buy_rate": delayed_buy / len(usable),
        "delay_sell_rate": delayed_sell / len(usable),
    }


def _score(m: dict[str, Any]) -> float:
    n = int(m.get("n") or 0)
    if n <= 0:
        return -999.0
    sample_score = min(math.log1p(n) / math.log(12), 1.0) * 20.0
    win_score = float(m.get("win_rate") or 0.0) * 30.0
    ret_score = max(min(float(m.get("avg_ret") or 0.0) * 500.0, 25.0), -25.0)
    calmar_score = max(min(float(m.get("calmar") or 0.0) * 5.0, 20.0), -20.0)
    delay_penalty = (float(m.get("delay_buy_rate") or 0.0) + float(m.get("delay_sell_rate") or 0.0)) * 10.0
    return sample_score + win_score + ret_score + calmar_score - delay_penalty


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def _stock_rows_from_trade_map(
    stock_code: str,
    formula_id: str,
    variant: dict[str, Any],
    trade_map: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    rows = []
    best: dict[str, Any] | None = None
    for sell_rule, trades in trade_map.items():
        m = _metrics_from_trades(trades)
        if not m:
            continue
        holding_days = _holding_days_from_sell_rule(sell_rule)
        row = {
            "formula_id": formula_id,
            "variant_id": variant["variant_id"],
            "stock_code": stock_code,
            "sell_rule": sell_rule,
            "holding_days": holding_days,
            "signal_count": m["n"],
            "win_rate": m["win_rate"],
            "avg_ret": m["avg_ret"],
            "avg_dd": m["avg_dd"],
            "calmar": m["calmar"],
            "delay_buy_rate": m["delay_buy_rate"],
            "delay_sell_rate": m["delay_sell_rate"],
            "score": _score(m),
            "params": json.dumps(variant["params"], ensure_ascii=False, sort_keys=True),
        }
        rows.append(row)
        if best is None or row["score"] > best["score"]:
            best = row
    return rows, best


def _holding_days_from_sell_rule(sell_rule: str) -> int:
    try:
        return int(str(sell_rule).rsplit("_", 1)[1])
    except Exception:
        return 0


def _trim_signal_indices(signal_indices: np.ndarray, max_signals_per_stock: int) -> np.ndarray:
    if max_signals_per_stock > 0 and len(signal_indices) > max_signals_per_stock:
        return signal_indices[-max_signals_per_stock:]
    return signal_indices


def _evaluate_formula_variant(args: tuple[str, dict[str, Any], list[dict[str, Any]], int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    formula_id, variant, stocks, max_signals_per_stock = args
    stock_rows: list[dict[str, Any]] = []
    sell_rules = [f"fixed_{h}" for h in HOLDING_PERIODS] + [f"formula_exit_or_{h}" for h in HOLDING_PERIODS]
    aggregate_trades: dict[str, list[dict[str, Any]]] = {rule: [] for rule in sell_rules}
    started = time.time()
    progress_every = max(0, int(os.environ.get("BESTCHOICE_PARAM_PROGRESS_EVERY", "0") or "0"))
    for stock_i, stock in enumerate(stocks, 1):
        if progress_every and stock_i % progress_every == 0:
            print(f"formula_parameter_search:progress {formula_id}/{variant['variant_id']} {stock_i}/{len(stocks)}", flush=True)
        if len(stock["close"]) < 220:
            continue
        try:
            out = compute_formula_signals(
                formula_id,
                open_=stock["open"],
                high=stock["high"],
                low=stock["low"],
                close=stock["close"],
                volume=stock["volume"],
                amount=stock["amount"],
                params=variant["params"],
            )
            signal_indices = np.where(out["entry"])[0]
            exit_signals = np.asarray(out.get("exit", np.zeros(len(stock["close"]), dtype=bool)), dtype=bool)
        except Exception:
            signal_indices = np.array([], dtype=np.int64)
            exit_signals = np.zeros(len(stock["close"]), dtype=bool)
        if len(signal_indices) == 0:
            continue
        signal_indices = _trim_signal_indices(signal_indices, max_signals_per_stock)
        fixed_trade_map = build_fixed_holding_trades(
            code=stock["code"],
            dates=stock["dates"],
            opens=stock["open"],
            highs=stock["high"],
            lows=stock["low"],
            closes=stock["close"],
            volumes=stock["volume"],
            amounts=stock["amount"],
            signal_indices=signal_indices,
            holding_periods=HOLDING_PERIODS,
            include_open=False,
        )
        trade_map: dict[str, list[dict[str, Any]]] = {}
        for h, trades in fixed_trade_map.items():
            rule = f"fixed_{h}"
            for trade in trades:
                trade["sell_rule"] = rule
            trade_map[rule] = trades
        for h in HOLDING_PERIODS:
            rule = f"formula_exit_or_{h}"
            trade_map[rule] = build_sell_rule_trades(
                code=stock["code"],
                dates=stock["dates"],
                opens=stock["open"],
                highs=stock["high"],
                lows=stock["low"],
                closes=stock["close"],
                volumes=stock["volume"],
                amounts=stock["amount"],
                signal_indices=signal_indices,
                sell_rule=rule,
                exit_signals=exit_signals,
                include_open=False,
            )
        for rule, trades in trade_map.items():
            aggregate_trades[rule].extend(trades)
        rows, _ = _stock_rows_from_trade_map(stock["code"], formula_id, variant, trade_map)
        stock_rows.extend(rows)

    variant_rows = []
    for sell_rule, trades in aggregate_trades.items():
        m = _metrics_from_trades(trades)
        if not m:
            continue
        h = _holding_days_from_sell_rule(sell_rule)
        variant_rows.append(
            {
                "formula_id": formula_id,
                "variant_id": variant["variant_id"],
                "sell_rule": sell_rule,
                "holding_days": h,
                "stock_count": len(stocks),
                "trade_count": m["n"],
                "win_rate": m["win_rate"],
                "avg_ret": m["avg_ret"],
                "avg_dd": m["avg_dd"],
                "calmar": m["calmar"],
                "delay_buy_rate": m["delay_buy_rate"],
                "delay_sell_rate": m["delay_sell_rate"],
                "score": _score(m),
                "elapsed_sec": time.time() - started,
                "execution_model": EXECUTION_MODEL_VERSION,
                "params": json.dumps(variant["params"], ensure_ascii=False, sort_keys=True),
            }
        )
    return variant_rows, stock_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in fieldnames})


def _read_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    parser = argparse.ArgumentParser(description="Search formula parameters with the shared VWAP execution model.")
    parser.add_argument("--only", nargs="*", choices=sorted(FORMULA_VARIANTS), help="Formula ids to search.")
    parser.add_argument("--exclude", nargs="*", choices=sorted(FORMULA_VARIANTS), help="Formula ids to skip.")
    parser.add_argument("--append", action="store_true", help="Append to existing output CSVs and recompute per-stock best.")
    parser.add_argument("--max-stocks", type=int, default=0, help="Optional stock limit for smoke tests.")
    parser.add_argument("--max-signals-per-stock", type=int, default=120, help="Cap dense formula signals per stock/variant; 0 means no cap.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel formula-variant workers. Keep low for full-market runs.")
    args = parser.parse_args()

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    formulas = args.only or list(FORMULA_VARIANTS)
    if args.exclude:
        excluded = set(args.exclude)
        formulas = [fid for fid in formulas if fid not in excluded]
    stocks = _load_market_rows(args.max_stocks)

    from services.backtest_preflight import enforce_backtest_preflight
    stock_codes = [s["code"] for s in stocks]
    smart_conn = duckdb.connect(str(SMART_DB), read_only=True)
    market_conn = duckdb.connect(str(MARKET_DB), read_only=True)
    enforce_backtest_preflight(
        stock_codes=stock_codes,
        conn=smart_conn,
        market_conn=market_conn,
    )
    smart_conn.close()
    market_conn.close()

    tasks = [(fid, variant, stocks, args.max_signals_per_stock) for fid in formulas for variant in FORMULA_VARIANTS[fid]]

    print(
        "formula_parameter_search:start "
        f"formulas={len(formulas)} variants={len(tasks)} stocks={len(stocks)} "
        f"workers={args.workers} max_signals_per_stock={args.max_signals_per_stock}",
        flush=True,
    )
    all_variant_rows: list[dict[str, Any]] = []
    all_stock_rows: list[dict[str, Any]] = []
    started = time.time()

    if args.workers <= 1:
        for i, task in enumerate(tasks, 1):
            fid, variant, _, _ = task
            print(f"formula_parameter_search:variant {i}/{len(tasks)} {fid}/{variant['variant_id']}", flush=True)
            variant_rows, stock_rows = _evaluate_formula_variant(task)
            all_variant_rows.extend(variant_rows)
            all_stock_rows.extend(stock_rows)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            future_map = {pool.submit(_evaluate_formula_variant, task): task for task in tasks}
            for i, fut in enumerate(as_completed(future_map), 1):
                fid, variant, _, _ = future_map[fut]
                variant_rows, stock_rows = fut.result()
                all_variant_rows.extend(variant_rows)
                all_stock_rows.extend(stock_rows)
                print(f"formula_parameter_search:done {i}/{len(tasks)} {fid}/{variant['variant_id']}", flush=True)

    best_by_stock_formula: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_stock_rows:
        if not row.get("sell_rule"):
            row["sell_rule"] = f"fixed_{int(float(row.get('holding_days') or 0))}"
        key = (str(row["stock_code"]), str(row["formula_id"]))
        if key not in best_by_stock_formula or float(row["score"]) > float(best_by_stock_formula[key]["score"]):
            best_by_stock_formula[key] = row
    best_rows = sorted(best_by_stock_formula.values(), key=lambda r: (r["stock_code"], r["formula_id"]))

    if args.append:
        all_variant_rows = _read_existing_rows(VARIANT_METRICS) + all_variant_rows
        all_stock_rows = _read_existing_rows(STOCK_BEST) + all_stock_rows
        best_by_stock_formula = {}
        for row in all_stock_rows:
            if not row.get("sell_rule"):
                row["sell_rule"] = f"fixed_{int(float(row.get('holding_days') or 0))}"
            key = (str(row["stock_code"]), str(row["formula_id"]))
            if key not in best_by_stock_formula or float(row["score"]) > float(best_by_stock_formula[key]["score"]):
                best_by_stock_formula[key] = row
        best_rows = sorted(best_by_stock_formula.values(), key=lambda r: (r["stock_code"], r["formula_id"]))

    all_variant_rows.sort(key=lambda r: (r["formula_id"], -float(r["score"]), r["variant_id"], r["holding_days"]))
    _write_csv(
        VARIANT_METRICS,
        all_variant_rows,
        [
            "formula_id",
            "variant_id",
            "sell_rule",
            "holding_days",
            "stock_count",
            "trade_count",
            "win_rate",
            "avg_ret",
            "avg_dd",
            "calmar",
            "delay_buy_rate",
            "delay_sell_rate",
            "score",
            "elapsed_sec",
            "execution_model",
            "params",
        ],
    )
    _write_csv(
        STOCK_BEST,
        best_rows,
        [
            "formula_id",
            "variant_id",
            "stock_code",
            "sell_rule",
            "holding_days",
            "signal_count",
            "win_rate",
            "avg_ret",
            "avg_dd",
            "calmar",
            "delay_buy_rate",
            "delay_sell_rate",
            "score",
            "params",
        ],
    )

    SEARCH_REPORT.write_text(
        "\n".join(
            [
                "# Formula Parameter Search",
                "",
                f"- formulas: `{', '.join(formulas)}`",
                f"- formula_variants: `{len(tasks)}`",
                f"- stocks: `{len(stocks)}`",
                f"- workers: `{args.workers}`",
                f"- max_signals_per_stock: `{args.max_signals_per_stock}`",
                f"- execution_model: `{EXECUTION_MODEL_VERSION}`",
                f"- elapsed_sec: `{time.time() - started:.1f}`",
                "",
                "## Artifacts",
                "",
                f"- `{VARIANT_METRICS.relative_to(ROOT)}`",
                f"- `{STOCK_BEST.relative_to(ROOT)}`",
                "",
                "## Scope",
                "",
                "- First-stage grid search across named formula variants and fixed holding periods.",
                "- Sell-rule search now compares fixed holding rules with formula-exit capped rules per stock and variant.",
                "- Dense formulas are capped to the latest signals per stock during exploratory search so an over-broad formula cannot dominate runtime.",
                "- Per-stock best rows are selected by score within each formula.",
                "- Later Optuna/local search can expand the same output schema without changing the UI contract.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"formula_parameter_search:done variants={len(all_variant_rows)} stock_best={len(best_rows)} elapsed={time.time()-started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
