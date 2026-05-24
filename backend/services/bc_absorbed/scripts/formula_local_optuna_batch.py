from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import optuna


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution_model import EXECUTION_MODEL_VERSION
from scripts.formula_local_optuna import (
    DEFAULT_CODES,
    VALIDATION_RATIO,
    _evaluate_rule,
    _fmt,
    _load_current_best,
    _optimize_one,
    _parse_json_obj,
    _stable_seed,
    _stock_by_code,
)
from scripts.formula_parameter_search import FORMULA_VARIANTS, _load_market_rows


ANALYSIS_DIR = ROOT / "analysis"
OUT_CSV = ANALYSIS_DIR / "formula_local_optuna_batch.csv"
OUT_MD = ANALYSIS_DIR / "formula_local_optuna_batch.md"

FIELDNAMES = [
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


def _read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if not row.get("baseline_investigation"):
            row["baseline_investigation"] = _investigation_payload(
                str(row.get("baseline_status") or ""),
                str(row.get("baseline_reason") or ""),
            )
        if not row.get("optuna_investigation"):
            row["optuna_investigation"] = _investigation_payload(
                str(row.get("optuna_status") or ""),
                str(row.get("optuna_reason") or ""),
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in FIELDNAMES})


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


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except Exception:
        return default


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


def _build_row(
    *,
    stock: dict[str, Any],
    code: str,
    formula_id: str,
    baseline: dict[str, Any] | None,
    trials: int,
    max_signals: int,
    seed: int,
) -> dict[str, Any]:
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
                max_signals=max_signals,
            )
            if baseline_eval.get("status") != "ok":
                baseline_status = f"baseline_eval_{baseline_eval.get('status') or 'failed'}"
                baseline_reason = str(baseline_eval.get("reason") or "")

    best = _optimize_one(
        stock,
        formula_id,
        trials=trials,
        seed=seed,
        max_signals=max_signals,
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
    row: dict[str, Any] = {
        "stock_code": code,
        "formula_id": formula_id,
        "trials": trials,
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
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable batched local Optuna audits without touching production best rows.")
    parser.add_argument("--codes", nargs="*", help="Explicit stock codes. Defaults to market slice.")
    parser.add_argument("--formulas", nargs="*", choices=sorted(FORMULA_VARIANTS), default=list(FORMULA_VARIANTS))
    parser.add_argument("--trials", type=int, default=24)
    parser.add_argument("--max-signals-per-stock", type=int, default=120)
    parser.add_argument("--max-stocks", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260520)
    parser.add_argument("--output", type=Path, default=OUT_CSV)
    parser.add_argument("--report", type=Path, default=OUT_MD)
    parser.add_argument("--resume", action="store_true", help="Keep existing rows and skip completed stock/formula pairs.")
    args = parser.parse_args()
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    if args.codes:
        codes = [str(c).zfill(6) for c in args.codes]
    else:
        market_rows = _load_market_rows(0)
        codes = [str(r["code"]).zfill(6) for r in market_rows]
        if args.offset > 0:
            codes = codes[args.offset :]
        if args.max_stocks > 0:
            codes = codes[: args.max_stocks]
    if not codes:
        codes = DEFAULT_CODES

    existing = _read_existing(args.output) if args.resume else []
    completed = {(str(r.get("stock_code") or ""), str(r.get("formula_id") or "")) for r in existing}
    rows = list(existing)
    current_best = _load_current_best()
    stocks = _stock_by_code(codes)
    started = time.time()
    total_tasks = len(codes) * len(args.formulas)
    done = 0

    for code in codes:
        stock = stocks.get(code)
        if not stock:
            continue
        for formula_id in args.formulas:
            key = (code, formula_id)
            if args.resume and key in completed:
                continue
            row = _build_row(
                stock=stock,
                code=code,
                formula_id=formula_id,
                baseline=current_best.get(key),
                trials=args.trials,
                max_signals=args.max_signals_per_stock,
                seed=_stable_seed(args.seed, code, formula_id),
            )
            rows.append(row)
            done += 1
            print(
                f"formula_local_optuna_batch:done {done}/{total_tasks} {code} {formula_id} "
                f"baseline_status={row['baseline_status']} optuna_status={row['optuna_status']}",
                flush=True,
            )

    rows.sort(
        key=lambda r: (
            _to_float(r.get("score_delta"), float("-inf")),
            _to_float(r.get("optuna_score"), float("-inf")),
        ),
        reverse=True,
    )
    _write_csv(args.output, rows)
    candidates = [r for r in rows if _to_float(r.get("score_delta"), float("-inf")) >= 3.0]
    args.report.write_text(
        "\n".join(
            [
                "# Formula Local Optuna Batch",
                "",
                f"- rows: `{len(rows)}`",
                f"- new_rows: `{done}`",
                f"- codes_requested: `{len(codes)}`",
                f"- formulas: `{', '.join(args.formulas)}`",
                f"- trials: `{args.trials}`",
                f"- max_signals_per_stock: `{args.max_signals_per_stock}`",
                f"- validation_ratio: `{VALIDATION_RATIO}`",
                f"- execution_model: `{EXECUTION_MODEL_VERSION}`",
                f"- elapsed_sec: `{time.time() - started:.1f}`",
                "",
                "## Missing Status Counts",
                "",
                "```json",
                json.dumps(_missing_reason_counts(rows), ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
                "## Top Raw Deltas",
                "",
                "| stock | formula | baseline | optuna | delta | validation_delta | status |",
                "|---|---|---:|---:|---:|---:|---|",
                *[
                    f"| `{r['stock_code']}` | `{r['formula_id']}` | {_to_float(r.get('baseline_score')):.2f} | "
                    f"{_to_float(r.get('optuna_score')):.2f} | {_to_float(r.get('score_delta')):.2f} | "
                    f"{_to_float(r.get('validation_score_delta')):.2f} | "
                    f"`{r.get('baseline_status')}/{r.get('optuna_status')}` |"
                    for r in rows[:12]
                ],
                "",
                "## Notes",
                "",
                "- This batch artifact is for full-market expansion planning only.",
                "- It does not write to production `analysis/stock_formula_best.csv`.",
                "- Missing baseline/Optuna rows are preserved as investigation leads and are not filled with default metrics.",
                "- Run `scripts/formula_local_optuna_adoption.py --input <batch.csv>` to apply adoption guardrails.",
                f"- raw_delta_rows_ge_3: `{len(candidates)}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"formula_local_optuna_batch:done rows={len(rows)} new_rows={done} elapsed={time.time()-started:.1f}s")


if __name__ == "__main__":
    main()
