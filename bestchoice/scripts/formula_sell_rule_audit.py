from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute import HOLDING_PERIODS, normalize_code
from execution_model import EXECUTION_MODEL_VERSION, guarded_vwap, tradability_flags
from formula_engine import compute_formula_signals
from scripts.formula_parameter_search import FORMULA_VARIANTS, _load_market_rows, _metrics_from_trades, _score


ANALYSIS_DIR = ROOT / "analysis"
SELL_RULE_AUDIT = ANALYSIS_DIR / "formula_sell_rule_audit.csv"
SELL_RULE_REPORT = ANALYSIS_DIR / "formula_sell_rule_audit.md"
BUY_DELAY_DAYS = 3
MAX_EXIT_HOLDING_DAYS = 60


def _find_buy_idx(
    code: str,
    planned_idx: int,
    stock: dict[str, Any],
) -> tuple[int | None, str | None, int]:
    n = len(stock["close"])
    last_reason = None
    for i in range(planned_idx, min(n, planned_idx + BUY_DELAY_DAYS + 1)):
        flags = tradability_flags(
            code,
            i,
            stock["open"],
            stock["high"],
            stock["low"],
            stock["close"],
            stock["volume"],
            stock["amount"],
        )
        if flags["can_buy"]:
            return i, None, i - planned_idx
        if flags["suspended"]:
            last_reason = "buy_blocked_suspended"
        elif flags["limit_up"]:
            last_reason = "buy_blocked_limit_up"
        else:
            last_reason = "buy_blocked_untradable"
    return None, last_reason or "buy_blocked_no_bar", BUY_DELAY_DAYS


def _find_sell_idx(
    code: str,
    planned_idx: int,
    stock: dict[str, Any],
) -> tuple[int | None, str | None, int]:
    n = len(stock["close"])
    last_reason = None
    for i in range(planned_idx, n):
        flags = tradability_flags(
            code,
            i,
            stock["open"],
            stock["high"],
            stock["low"],
            stock["close"],
            stock["volume"],
            stock["amount"],
        )
        if flags["can_sell"]:
            return i, None, i - planned_idx
        if flags["suspended"]:
            last_reason = "sell_blocked_suspended"
        elif flags["limit_down"]:
            last_reason = "sell_blocked_limit_down"
        else:
            last_reason = "sell_blocked_untradable"
    return None, last_reason or "sell_blocked_no_bar", max(0, n - planned_idx)


def _formula_exit_trades(
    *,
    stock: dict[str, Any],
    entries: np.ndarray,
    exits: np.ndarray,
    max_holding_days: int,
) -> list[dict[str, Any]]:
    code = normalize_code(stock["code"])
    n = len(stock["close"])
    exit_idxs = np.flatnonzero(exits)
    trades: list[dict[str, Any]] = []
    for signal_i in entries:
        signal_i = int(signal_i)
        planned_buy_i = signal_i + 1
        if planned_buy_i >= n:
            continue
        buy_i, buy_block_reason, delay_buy_days = _find_buy_idx(code, planned_buy_i, stock)
        if buy_i is None:
            trades.append(
                {
                    "signal_idx": signal_i,
                    "signal_date": str(stock["dates"][signal_i]),
                    "ret": None,
                    "skipped": True,
                    "buy_block_reason": buy_block_reason,
                    "delay_buy_days": delay_buy_days,
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )
            continue

        buy_price, buy_method = guarded_vwap(
            stock["amount"][buy_i], stock["volume"][buy_i], stock["close"][buy_i], stock["low"][buy_i], stock["high"][buy_i]
        )
        if buy_price <= 0:
            continue

        exit_pos = int(np.searchsorted(exit_idxs, buy_i + 1, side="left"))
        formula_sell_i = int(exit_idxs[exit_pos]) if exit_pos < len(exit_idxs) else None
        max_sell_i = min(n - 1, buy_i + max_holding_days)
        planned_sell_i = min(formula_sell_i, max_sell_i) if formula_sell_i is not None else max_sell_i
        if planned_sell_i <= buy_i:
            planned_sell_i = min(n - 1, buy_i + 1)

        sell_i, sell_block_reason, delay_sell_days = _find_sell_idx(code, planned_sell_i, stock)
        if sell_i is None:
            trades.append(
                {
                    "signal_idx": signal_i,
                    "signal_date": str(stock["dates"][signal_i]),
                    "buy_idx": buy_i,
                    "buy_date": str(stock["dates"][buy_i]),
                    "ret": None,
                    "sell_block_reason": sell_block_reason,
                    "delay_buy_days": delay_buy_days,
                    "delay_sell_days": delay_sell_days,
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )
            continue

        sell_price, sell_method = guarded_vwap(
            stock["amount"][sell_i], stock["volume"][sell_i], stock["close"][sell_i], stock["low"][sell_i], stock["high"][sell_i]
        )
        low_slice = stock["low"][buy_i : sell_i + 1]
        max_dd = min(0.0, (float(np.min(low_slice)) - buy_price) / buy_price) if len(low_slice) else 0.0
        trades.append(
            {
                "signal_idx": signal_i,
                "buy_idx": buy_i,
                "sell_idx": sell_i,
                "signal_date": str(stock["dates"][signal_i]),
                "buy_date": str(stock["dates"][buy_i]),
                "sell_date": str(stock["dates"][sell_i]),
                "buy_price": round(float(buy_price), 3),
                "buy_price_method": buy_method,
                "buy_block_reason": buy_block_reason,
                "sell_price": round(float(sell_price), 3),
                "sell_price_method": sell_method,
                "sell_block_reason": sell_block_reason,
                "ret": round(float((sell_price - buy_price) / buy_price), 4),
                "max_dd": round(float(max_dd), 4),
                "holding_days": int(sell_i - buy_i),
                "delay_buy_days": delay_buy_days,
                "delay_sell_days": delay_sell_days,
                "sell_rule": "formula_exit_or_60",
                "execution_model": EXECUTION_MODEL_VERSION,
            }
        )
    return trades


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in fieldnames})


def _default_variant(formula_id: str) -> dict[str, Any]:
    variants = FORMULA_VARIANTS[formula_id]
    return next((v for v in variants if "default" in v["variant_id"]), variants[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare formula exit sell rules with fixed holding periods.")
    parser.add_argument("--only", nargs="*", choices=sorted(FORMULA_VARIANTS), help="Formula ids to audit.")
    parser.add_argument("--max-stocks", type=int, default=0, help="Optional stock limit for smoke tests.")
    args = parser.parse_args()

    formulas = args.only or list(FORMULA_VARIANTS)
    stocks = _load_market_rows(args.max_stocks)
    rows: list[dict[str, Any]] = []
    started = time.time()
    for formula_id in formulas:
        variant = _default_variant(formula_id)
        fixed_trades_by_h = {h: [] for h in HOLDING_PERIODS}
        exit_trades: list[dict[str, Any]] = []
        for stock in stocks:
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
            except Exception:
                continue
            entries = np.flatnonzero(out["entry"])
            if len(entries) == 0:
                continue
            from execution_model import build_fixed_holding_trades

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
                fixed_trades_by_h[h].extend(trades)
            exit_trades.extend(
                _formula_exit_trades(
                    stock=stock,
                    entries=entries,
                    exits=np.asarray(out.get("exit", np.zeros(len(stock["close"]), dtype=bool)), dtype=bool),
                    max_holding_days=MAX_EXIT_HOLDING_DAYS,
                )
            )

        for h, trades in fixed_trades_by_h.items():
            m = _metrics_from_trades(trades)
            if not m:
                continue
            rows.append(
                {
                    "formula_id": formula_id,
                    "variant_id": variant["variant_id"],
                    "sell_rule": f"fixed_{h}",
                    "holding_days": h,
                    "trade_count": m["n"],
                    "win_rate": m["win_rate"],
                    "avg_ret": m["avg_ret"],
                    "avg_dd": m["avg_dd"],
                    "calmar": m["calmar"],
                    "delay_buy_rate": m["delay_buy_rate"],
                    "delay_sell_rate": m["delay_sell_rate"],
                    "score": _score(m),
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )
        m = _metrics_from_trades(exit_trades)
        if m:
            rows.append(
                {
                    "formula_id": formula_id,
                    "variant_id": variant["variant_id"],
                    "sell_rule": "formula_exit_or_60",
                    "holding_days": MAX_EXIT_HOLDING_DAYS,
                    "trade_count": m["n"],
                    "win_rate": m["win_rate"],
                    "avg_ret": m["avg_ret"],
                    "avg_dd": m["avg_dd"],
                    "calmar": m["calmar"],
                    "delay_buy_rate": m["delay_buy_rate"],
                    "delay_sell_rate": m["delay_sell_rate"],
                    "score": _score(m),
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )
        print(f"formula_sell_rule_audit:done {formula_id}", flush=True)

    rows.sort(key=lambda r: (r["formula_id"], -float(r["score"]), r["sell_rule"]))
    _write_csv(
        SELL_RULE_AUDIT,
        rows,
        [
            "formula_id",
            "variant_id",
            "sell_rule",
            "holding_days",
            "trade_count",
            "win_rate",
            "avg_ret",
            "avg_dd",
            "calmar",
            "delay_buy_rate",
            "delay_sell_rate",
            "score",
            "execution_model",
        ],
    )
    best_by_formula = {}
    for row in rows:
        best_by_formula.setdefault(row["formula_id"], row)
    lines = [
        "# Formula Sell Rule Audit",
        "",
        f"- formulas: `{', '.join(formulas)}`",
        f"- stocks: `{len(stocks)}`",
        f"- execution_model: `{EXECUTION_MODEL_VERSION}`",
        f"- elapsed_sec: `{time.time() - started:.1f}`",
        "",
        "## Best Sell Rule By Formula",
        "",
    ]
    for formula_id, row in sorted(best_by_formula.items()):
        lines.append(
            f"- `{formula_id}`: `{row['sell_rule']}`, score `{float(row['score']):.2f}`, "
            f"win `{float(row['win_rate']) * 100:.1f}%`, avg_ret `{float(row['avg_ret']) * 100:.2f}%`, "
            f"avg_dd `{float(row['avg_dd']) * 100:.2f}%`"
        )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- First sell-rule audit compares fixed holding periods with formula exit signals capped at 60 trading days.",
            "- This is an audit artifact; production recommendation still uses the fixed-holding execution path until sell-rule selection is wired into caches and strategy cards.",
        ]
    )
    SELL_RULE_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"formula_sell_rule_audit:done rows={len(rows)} elapsed={time.time()-started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
