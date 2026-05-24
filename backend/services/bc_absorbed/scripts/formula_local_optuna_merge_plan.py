from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.formula_local_optuna_adoption import _decision


ANALYSIS_DIR = ROOT / "analysis"
DEFAULT_INPUT = ANALYSIS_DIR / "formula_local_optuna_adoption_candidates.csv"
STOCK_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
OUT_PLAN = ANALYSIS_DIR / "formula_local_optuna_merge_plan.csv"
OUT_REPLACEMENTS = ANALYSIS_DIR / "formula_local_optuna_stock_best_replacements.csv"
OUT_MD = ANALYSIS_DIR / "formula_local_optuna_merge_plan.md"

STOCK_BEST_FIELDS = [
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
]

PLAN_FIELDS = [
    "stock_code",
    "formula_id",
    "merge_decision",
    "merge_reason",
    "old_variant_id",
    "new_variant_id",
    "old_sell_rule",
    "new_sell_rule",
    "old_score",
    "new_score",
    "score_delta",
    "old_validation_score",
    "new_validation_score",
    "validation_score_delta",
    "new_signal_count",
    "new_validation_signal_count",
    "new_win_rate",
    "new_validation_win_rate",
    "new_avg_ret",
    "new_validation_avg_ret",
    "trials",
    "validation_ratio",
    "replacement_params",
]


def _to_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except Exception:
        return default


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"missing {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _load_stock_best(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = _read_csv(path)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("stock_code") or "").zfill(6)
        formula_id = str(row.get("formula_id") or "")
        if code and formula_id:
            out[(code, formula_id)] = row
    return out


def _replacement_variant_id(row: dict[str, Any]) -> str:
    trials = str(row.get("trials") or "").strip() or "na"
    return f"local_optuna_t{trials}_vsplit"


def _replacement_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "formula_id": row.get("formula_id"),
        "variant_id": _replacement_variant_id(row),
        "stock_code": str(row.get("stock_code") or "").zfill(6),
        "sell_rule": row.get("optuna_sell_rule"),
        "holding_days": row.get("optuna_holding_days"),
        "signal_count": row.get("optuna_signal_count"),
        "win_rate": row.get("optuna_win_rate"),
        "avg_ret": row.get("optuna_avg_ret"),
        "avg_dd": row.get("optuna_avg_dd"),
        "calmar": row.get("optuna_calmar"),
        "delay_buy_rate": row.get("optuna_delay_buy_rate"),
        "delay_sell_rate": row.get("optuna_delay_sell_rate"),
        "score": row.get("optuna_score"),
        "params": row.get("optuna_params") or "{}",
    }


def _plan_row(row: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    decision, reason = _decision(row)
    if decision != "candidate":
        return {
            "stock_code": str(row.get("stock_code") or "").zfill(6),
            "formula_id": row.get("formula_id"),
            "merge_decision": "reject",
            "merge_reason": reason,
        }
    return {
        "stock_code": str(row.get("stock_code") or "").zfill(6),
        "formula_id": row.get("formula_id"),
        "merge_decision": "replace",
        "merge_reason": "passes_adoption_guardrails",
        "old_variant_id": (old or {}).get("variant_id"),
        "new_variant_id": _replacement_variant_id(row),
        "old_sell_rule": (old or {}).get("sell_rule"),
        "new_sell_rule": row.get("optuna_sell_rule"),
        "old_score": row.get("baseline_score") or (old or {}).get("score"),
        "new_score": row.get("optuna_score"),
        "score_delta": row.get("score_delta"),
        "old_validation_score": row.get("baseline_validation_score"),
        "new_validation_score": row.get("optuna_validation_score"),
        "validation_score_delta": row.get("validation_score_delta"),
        "new_signal_count": row.get("optuna_signal_count"),
        "new_validation_signal_count": row.get("optuna_validation_signal_count"),
        "new_win_rate": row.get("optuna_win_rate"),
        "new_validation_win_rate": row.get("optuna_validation_win_rate"),
        "new_avg_ret": row.get("optuna_avg_ret"),
        "new_validation_avg_ret": row.get("optuna_validation_avg_ret"),
        "trials": row.get("trials"),
        "validation_ratio": row.get("validation_ratio"),
        "replacement_params": row.get("optuna_params"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k)) for k in fields})


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dry-run merge plan for validated local Optuna candidates.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--stock-best", type=Path, default=STOCK_BEST)
    parser.add_argument("--plan-output", type=Path, default=OUT_PLAN)
    parser.add_argument("--replacement-output", type=Path, default=OUT_REPLACEMENTS)
    parser.add_argument("--report", type=Path, default=OUT_MD)
    args = parser.parse_args()

    rows = _read_csv(args.input)
    stock_best = _load_stock_best(args.stock_best)
    plan_rows = []
    replacement_rows = []
    for row in rows:
        code = str(row.get("stock_code") or "").zfill(6)
        formula_id = str(row.get("formula_id") or "")
        old = stock_best.get((code, formula_id))
        plan = _plan_row(row, old)
        plan_rows.append(plan)
        if plan.get("merge_decision") == "replace":
            replacement_rows.append(_replacement_row(row))

    plan_rows.sort(
        key=lambda r: (
            r.get("merge_decision") == "replace",
            _to_float(r.get("score_delta"), float("-inf")),
            _to_float(r.get("validation_score_delta"), float("-inf")),
        ),
        reverse=True,
    )
    replacement_rows.sort(key=lambda r: (str(r.get("stock_code") or ""), str(r.get("formula_id") or "")))
    _write_csv(args.plan_output, plan_rows, PLAN_FIELDS)
    _write_csv(args.replacement_output, replacement_rows, STOCK_BEST_FIELDS)

    report_lines = [
        "# Formula Local Optuna Merge Plan",
        "",
        f"- input: `{args.input}`",
        f"- stock_best: `{args.stock_best}`",
        f"- plan_output: `{args.plan_output}`",
        f"- replacement_output: `{args.replacement_output}`",
        f"- rows: `{len(rows)}`",
        f"- replacements: `{len(replacement_rows)}`",
        "",
        "## Replacements",
        "",
        "| stock | formula | old_variant | new_variant | old_score | new_score | delta | val_delta |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in [r for r in plan_rows if r.get("merge_decision") == "replace"][:20]:
        report_lines.append(
            f"| `{row['stock_code']}` | `{row['formula_id']}` | `{row.get('old_variant_id') or ''}` | "
            f"`{row.get('new_variant_id') or ''}` | {_to_float(row.get('old_score'), 0.0):.2f} | "
            f"{_to_float(row.get('new_score'), 0.0):.2f} | {_to_float(row.get('score_delta'), 0.0):.2f} | "
            f"{_to_float(row.get('validation_score_delta'), 0.0):.2f} |"
        )
    report_lines.extend(
        [
            "",
            "## Policy",
            "",
            "- This script is dry-run only and does not modify `analysis/stock_formula_best.csv`.",
            "- Replacement rows preserve the production schema and use `local_optuna_t<trials>_vsplit` as `variant_id`.",
            "- Only rows passing the adoption guardrails are emitted as replacements.",
        ]
    )
    args.report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"formula_local_optuna_merge_plan: rows={len(rows)} replacements={len(replacement_rows)}")


if __name__ == "__main__":
    main()
