"""每日选股输出 — 跑 Layer 0→1→2→3 全 pipeline, 输出明日买入列表.

Usage:
    PYTHONPATH=bestchoice:backend python backend/services/bc_absorbed/scripts/daily_formula_picks.py
    PYTHONPATH=bestchoice:backend python backend/services/bc_absorbed/scripts/daily_formula_picks.py --date 2026-05-25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MARKET_DB = PROJECT_ROOT / "data" / "market.duckdb"
SMART_DB = PROJECT_ROOT / "data" / "smartmoney.duckdb"
CONFIG_PATH = BACKEND_DIR / "config" / "paper_sim_formula.yaml"
OUTPUT_DIR = PROJECT_ROOT / "analysis"


def _load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_stocks(max_stocks: int = 0) -> dict[str, dict]:
    from services.universe import get_active_universe
    smart_conn = duckdb.connect(str(SMART_DB), read_only=True)
    universe = get_active_universe(smart_conn)
    smart_conn.close()

    con = duckdb.connect(str(MARKET_DB), read_only=True)
    raw = con.execute(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM v_price_kline_qfq ORDER BY code, date"
    ).fetchnumpy()
    con.close()

    from compute import normalize_code
    codes = raw["code"]
    unique_codes, counts = np.unique(codes, return_counts=True)
    stocks: dict[str, dict] = {}
    idx = 0
    for code_raw, cnt in zip(unique_codes, counts):
        sl = slice(idx, idx + cnt)
        code = normalize_code(code_raw)
        idx += cnt
        if code not in universe:
            continue
        if max_stocks and len(stocks) >= max_stocks:
            break
        stocks[code] = {
            "dates": raw["date"][sl],
            "open": raw["open"][sl].astype(np.float64),
            "high": raw["high"][sl].astype(np.float64),
            "low": raw["low"][sl].astype(np.float64),
            "close": raw["close"][sl].astype(np.float64),
            "volume": raw["volume"][sl].astype(np.float64),
            "amount": raw["amount"][sl].astype(np.float64),
        }
    return stocks


def run_pipeline(stocks: dict[str, dict], cfg: dict, formulas: list[str] | None = None) -> list[dict]:
    from stock_profiler import StockProfiler
    from signal_ranker import SignalRanker
    from formula_engine import compute_formula_signals, FORMULA_DEFINITIONS

    profiler = StockProfiler(cfg.get("profiler"))
    ranker = SignalRanker(cfg.get("ranker"))

    if formulas is None:
        formulas = list(FORMULA_DEFINITIONS.keys())

    all_signals: dict[str, dict[str, np.ndarray]] = {}
    for code, stock in stocks.items():
        if len(stock["close"]) < 60:
            continue
        formula_entries: dict[str, np.ndarray] = {}
        for fid in formulas:
            try:
                r = compute_formula_signals(
                    fid, open_=stock["open"], high=stock["high"],
                    low=stock["low"], close=stock["close"],
                    volume=stock["volume"], amount=stock["amount"],
                )
                if np.any(r["entry"]):
                    formula_entries[fid] = r["entry"]
            except Exception:
                continue
        if formula_entries:
            all_signals[code] = formula_entries

    scored = ranker.score_multi_stock(all_signals, stocks_data=stocks)

    n_bars = max((len(s["close"]) for s in stocks.values()), default=0)
    latest_signals = [s for s in scored if s.date_idx >= n_bars - 3]

    profiles = {}
    for s in latest_signals[:20]:
        if s.code in stocks and s.code not in profiles:
            st = stocks[s.code]
            profiles[s.code] = profiler.compute(s.code, st["close"], st["high"], st["low"], st["volume"])

    output = []
    for s in latest_signals[:20]:
        p = profiles.get(s.code)
        output.append({
            "code": s.code,
            "score": s.score,
            "resonance": s.resonance_count,
            "formulas": s.formulas,
            "profile": p.tags() if p else [],
            "detail": s.detail,
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily formula stock picks")
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--formulas", nargs="*", default=None)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    cfg = _load_config()
    stocks = _load_stocks(args.max_stocks)
    print(f"daily_formula_picks: {len(stocks)} stocks loaded", flush=True)

    picks = run_pipeline(stocks, cfg, args.formulas)

    for i, p in enumerate(picks[:args.top], 1):
        print(f"  #{i} {p['code']} score={p['score']:.3f} resonance={p['resonance']} "
              f"formulas={p['formulas'][:3]} profile={p['profile']}")

    out_path = OUTPUT_DIR / "daily_formula_picks_latest.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(picks, f, ensure_ascii=False, indent=2, default=str)
    print(f"daily_formula_picks: {len(picks)} picks written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
