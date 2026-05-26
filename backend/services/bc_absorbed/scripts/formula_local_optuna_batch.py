"""单公式 Optuna 跑批 — GCP 用, 支持 checkpoint resume.

每个公式独立跑 N trials, 输出 CSV + checkpoint JSON.
Preempt 后重启自动跳过 complete 的公式.

Usage:
    PYTHONPATH=bestchoice:backend python backend/services/bc_absorbed/scripts/formula_local_optuna_batch.py \
        --formulas gs_raw_buy --trials 100 --output results/gs_raw_buy.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import optuna

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parents[3]
BESTCHOICE_DIR = Path(__file__).resolve().parents[4] / "bestchoice"

# bc_absorbed 必须在 bestchoice 前, 否则 formula_engine 会找到 bestchoice 版本
for p in [str(ROOT), str(BACKEND_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)
if BESTCHOICE_DIR.exists() and str(BESTCHOICE_DIR) not in sys.path:
    sys.path.append(str(BESTCHOICE_DIR))

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MARKET_DB = PROJECT_ROOT / "data" / "market.duckdb"
SMART_DB = PROJECT_ROOT / "data" / "smartmoney.duckdb"

from compute import HOLDING_PERIODS, normalize_code
from execution_model import build_fixed_holding_trades
from formula_engine import compute_formula_signals, FORMULA_DEFINITIONS, _register_bank_definitions
_register_bank_definitions()


TRAIN_RATIO = 0.70
MAX_SIGNALS_PER_SPLIT = 120


def _load_stocks(max_stocks: int = 0) -> dict[str, dict]:
    import duckdb
    from services.universe import get_active_universe
    smart = duckdb.connect(str(SMART_DB), read_only=True)
    universe = get_active_universe(smart)
    smart.close()

    con = duckdb.connect(str(MARKET_DB), read_only=True)
    raw = con.execute(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM v_price_kline_qfq ORDER BY code, date"
    ).fetchnumpy()
    con.close()

    codes = raw["code"]
    unique_codes, counts = np.unique(codes, return_counts=True)
    stocks: dict[str, dict] = {}
    idx = 0
    for code_raw, cnt in zip(unique_codes, counts):
        sl = slice(idx, idx + cnt)
        code = normalize_code(code_raw)
        idx += cnt
        if code not in universe or cnt < 220:
            continue
        if max_stocks and len(stocks) >= max_stocks:
            break
        stocks[code] = {
            "code": code,
            "dates": raw["date"][sl],
            "open": raw["open"][sl].astype(np.float64),
            "high": raw["high"][sl].astype(np.float64),
            "low": raw["low"][sl].astype(np.float64),
            "close": raw["close"][sl].astype(np.float64),
            "volume": raw["volume"][sl].astype(np.float64),
            "amount": raw["amount"][sl].astype(np.float64),
        }
    return stocks


def _suggest_params(formula_id: str, trial: optuna.Trial) -> dict[str, Any]:
    from scripts.formula_local_optuna import _suggest_params as _sp
    return _sp(formula_id, trial)


def _metrics(trades: list[dict]) -> dict[str, Any] | None:
    usable = [t for t in trades if t.get("ret") is not None]
    if not usable:
        return None
    rets = [float(t["ret"]) for t in usable]
    dds = [float(t.get("max_dd") or 0) for t in usable]
    avg_dd = float(np.mean(dds))
    return {
        "n": len(usable),
        "win_rate": float(np.mean([r > 0 for r in rets])),
        "avg_ret": float(np.mean(rets)),
        "avg_dd": avg_dd,
        "calmar": float(np.mean(rets)) / max(abs(avg_dd), 0.005),
    }


def _score(m: dict[str, Any]) -> float:
    import math
    n = int(m.get("n") or 0)
    if n <= 0:
        return -999.0
    sample = min(math.log1p(n) / math.log(12), 1.0) * 20.0
    win = float(m.get("win_rate") or 0) * 30.0
    ret = max(min(float(m.get("avg_ret") or 0) * 500, 25), -25)
    calmar = max(min(float(m.get("calmar") or 0) * 5, 20), -20)
    return sample + win + ret + calmar


def _param_count(formula_id: str) -> int:
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    study = optuna.create_study(direction="maximize")
    return len(_suggest_params(formula_id, study.ask()))


def _tiered_trials(formula_id: str, requested_trials: int) -> tuple[int, int]:
    n_params = _param_count(formula_id)
    if n_params <= 0:
        tier = 1
    elif n_params <= 2:
        tier = 30
    elif n_params <= 6:
        tier = 60
    else:
        tier = 100
    return min(requested_trials, tier), n_params


def _split_idx(stock: dict[str, Any]) -> int:
    n = len(stock["dates"])
    return max(1, min(n - 1, int(n * TRAIN_RATIO)))


def _stock_head(stock: dict[str, Any], end_idx: int) -> dict[str, Any]:
    return {
        **stock,
        "dates": stock["dates"][:end_idx],
        "open": stock["open"][:end_idx],
        "high": stock["high"][:end_idx],
        "low": stock["low"][:end_idx],
        "close": stock["close"][:end_idx],
        "volume": stock["volume"][:end_idx],
        "amount": stock["amount"][:end_idx],
    }


def _entries_for(formula_id: str, stock: dict[str, Any], params: dict[str, Any]) -> np.ndarray:
    run_params = dict(params)
    if formula_id == "pullback_doji" and "limit_up_pct" not in run_params:
        from services.universe import get_limit_up_pct
        run_params["limit_up_pct"] = get_limit_up_pct(stock.get("code", ""))
    r = compute_formula_signals(
        formula_id, open_=stock["open"], high=stock["high"],
        low=stock["low"], close=stock["close"], volume=stock["volume"],
        amount=stock["amount"], params=run_params,
    )
    return np.where(r["entry"])[0]


def _trades_for_entries(stock: dict[str, Any], entries: np.ndarray) -> list[dict]:
    if len(entries) == 0:
        return []
    if len(entries) > MAX_SIGNALS_PER_SPLIT:
        entries = entries[-MAX_SIGNALS_PER_SPLIT:]
    trade_map = build_fixed_holding_trades(
        code=stock["code"], dates=stock["dates"],
        opens=stock["open"], highs=stock["high"], lows=stock["low"], closes=stock["close"],
        volumes=stock["volume"], amounts=stock["amount"],
        signal_indices=entries, holding_periods=[10], include_open=False,
    )
    return trade_map.get(10, [])


def _verify_data() -> dict[str, str]:
    import duckdb
    issues: dict[str, str] = {}
    for db_name, db_path in [("market", MARKET_DB), ("smartmoney", SMART_DB)]:
        if not db_path.exists():
            issues[db_name] = f"MISSING: {db_path}"
            continue
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            if db_name == "market":
                r = conn.execute("SELECT COUNT(DISTINCT code), MAX(date) FROM price_kline_tdxhub WHERE freq='daily'").fetchone()
                if r[0] < 4000:
                    issues[db_name] = f"INCOMPLETE: only {r[0]} stocks (expect 4500+)"
                print(f"  market.duckdb: {r[0]} stocks, max_date={r[1]}", flush=True)
            else:
                r = conn.execute("SELECT COUNT(*) FROM dim_active_a_stock").fetchone()
                if r[0] < 4000:
                    issues[db_name] = f"INCOMPLETE: only {r[0]} active stocks"
                print(f"  smartmoney.duckdb: {r[0]} active stocks", flush=True)
            conn.close()
        except Exception as e:
            issues[db_name] = f"ERROR: {e}"
    return issues


def run_formula_optuna(
    formula_id: str,
    stocks: dict[str, dict],
    trials: int,
    seed: int,
    sample_codes: list[str],
) -> dict[str, Any]:
    best_payload: dict[str, Any] | None = None

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_payload
        params = _suggest_params(formula_id, trial)
        train_trades: list[dict] = []
        validation_trades: list[dict] = []
        for code in sample_codes:
            stock = stocks.get(code)
            if stock is None:
                continue
            try:
                split_i = _split_idx(stock)
                train_stock = _stock_head(stock, split_i)
                train_trades.extend(_trades_for_entries(train_stock, _entries_for(formula_id, train_stock, params)))
                full_entries = _entries_for(formula_id, stock, params)
                validation_entries = full_entries[full_entries >= split_i]
                validation_trades.extend(_trades_for_entries(stock, validation_entries))
            except Exception:
                continue
        train_m = _metrics(train_trades)
        if train_m is None:
            return -999.0
        validation_m = _metrics(validation_trades)
        train_score = _score(train_m)
        validation_score = _score(validation_m) if validation_m else -999.0
        if best_payload is None or train_score > best_payload.get("train_score", -999):
            best_payload = {
                "params": params,
                "score": validation_score,
                "metrics": validation_m,
                "train_score": train_score,
                "train_metrics": train_m,
                "validation_score": validation_score,
                "validation_metrics": validation_m,
            }
        return train_score

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=trials, show_progress_bar=False)
    return best_payload or {"params": {}, "score": -999, "metrics": None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Formula Optuna batch runner (GCP-safe, checkpoint resume)")
    parser.add_argument("--formulas", nargs="+", required=True)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260526)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--checkpoint-dir", type=str, default=None)
    parser.add_argument("--max-stocks", type=int, default=200)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    print("formula_optuna_batch: verifying data...", flush=True)
    issues = _verify_data()
    if issues:
        print(f"DATA VERIFICATION FAILED: {issues}", flush=True)
        sys.exit(1)
    print("formula_optuna_batch: data OK", flush=True)

    print("formula_optuna_batch: validating plan logic...", flush=True)
    from plan_validator import validate_optuna_plan, PlanValidationError
    trial_plan = {fid: _tiered_trials(fid, args.trials) for fid in args.formulas}
    searchable_formulas = [fid for fid, (_, n_params) in trial_plan.items() if n_params > 0]
    if searchable_formulas:
        plan_result = validate_optuna_plan(
            formulas=searchable_formulas,
            trials=max(trial_plan[fid][0] for fid in searchable_formulas),
            output_path=args.output,
        )
        print(plan_result.summary(), flush=True)
        if not plan_result.passed:
            print("PLAN VALIDATION FAILED — refusing to run", flush=True)
            sys.exit(2)
    else:
        print("Plan Validation: no searchable params; all formulas run 1 baseline trial", flush=True)

    print("formula_optuna_batch: loading stocks...", flush=True)
    stocks = _load_stocks(args.max_stocks)
    sample_codes = list(stocks.keys())
    print(f"formula_optuna_batch: {len(stocks)} stocks loaded", flush=True)

    cp_dir = Path(args.checkpoint_dir) if args.checkpoint_dir else None
    results: list[dict] = []

    for formula_id in args.formulas:
        if formula_id not in FORMULA_DEFINITIONS:
            print(f"formula_optuna_batch: SKIP {formula_id} (not registered)", flush=True)
            continue

        if args.resume and cp_dir:
            cp_file = cp_dir / f"{formula_id}.json"
            if cp_file.exists():
                cp = json.loads(cp_file.read_text())
                effective_trials, _ = trial_plan.get(formula_id, (args.trials, 0))
                if cp.get("status") == "complete" and cp.get("trials") == effective_trials:
                    print(f"formula_optuna_batch: SKIP {formula_id} (checkpoint complete)", flush=True)
                    continue

        effective_trials, n_params = trial_plan.get(formula_id, _tiered_trials(formula_id, args.trials))
        print(f"formula_optuna_batch: START {formula_id} trials={effective_trials} params={n_params}", flush=True)
        started = time.time()

        if cp_dir:
            cp_dir.mkdir(parents=True, exist_ok=True)
            (cp_dir / f"{formula_id}.json").write_text(json.dumps({
                "formula_id": formula_id, "status": "running",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }))

        try:
            result = run_formula_optuna(formula_id, stocks, effective_trials, args.seed, sample_codes)
            elapsed = time.time() - started
            row = {
                "formula_id": formula_id,
                "trials": effective_trials,
                "n_params": n_params,
                "score": result.get("score"),
                "train_score": result.get("train_score"),
                "win_rate": (result.get("metrics") or {}).get("win_rate"),
                "avg_ret": (result.get("metrics") or {}).get("avg_ret"),
                "n_trades": (result.get("metrics") or {}).get("n"),
                "params": json.dumps(result.get("params", {}), ensure_ascii=False, sort_keys=True),
                "elapsed_sec": round(elapsed, 1),
                "status": "complete",
            }
            results.append(row)
            s_str = f"{row['score']:.2f}" if row['score'] is not None else "N/A"
            w_str = f"{row['win_rate']:.2%}" if row['win_rate'] is not None else "N/A"
            print(f"formula_optuna_batch: DONE {formula_id} score={s_str} "
                  f"win={w_str} elapsed={elapsed:.0f}s", flush=True)

            if cp_dir:
                (cp_dir / f"{formula_id}.json").write_text(json.dumps({
                    "formula_id": formula_id, "status": "complete",
                    "trials": effective_trials, "walk_forward_mode": "temporal_70_30",
                    "score": row["score"], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }, indent=2))
        except Exception as e:
            elapsed = time.time() - started
            print(f"formula_optuna_batch: FAIL {formula_id} after {elapsed:.0f}s: {e}", flush=True)
            if cp_dir:
                (cp_dir / f"{formula_id}.json").write_text(json.dumps({
                    "formula_id": formula_id, "status": "failed",
                    "error": str(e)[:200],
                    "elapsed_sec": round(elapsed, 1),
                }))

    if args.output and results:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = ["formula_id", "trials", "n_params", "score", "train_score", "win_rate", "avg_ret", "n_trades", "params", "elapsed_sec", "status"]
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(results)
        print(f"formula_optuna_batch: wrote {len(results)} rows to {out}", flush=True)

    complete = sum(1 for r in results if r["status"] == "complete")
    print(f"formula_optuna_batch: ALL DONE {complete}/{len(args.formulas)}", flush=True)


if __name__ == "__main__":
    main()
