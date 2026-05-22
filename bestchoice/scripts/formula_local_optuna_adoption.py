from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


ANALYSIS_DIR = ROOT / "analysis"
SAMPLES_CSV = ANALYSIS_DIR / "formula_local_optuna_samples.csv"
OUT_CSV = ANALYSIS_DIR / "formula_local_optuna_adoption_candidates.csv"
OUT_MD = ANALYSIS_DIR / "formula_local_optuna_adoption_candidates.md"

MIN_BASELINE_SCORE = 0.0
MIN_SIGNAL_COUNT = 6
MIN_SCORE_DELTA = 3.0
MIN_WIN_RATE = 0.45
MIN_AVG_RET = 0.0
MIN_TRIALS = 20
MIN_VALIDATION_SIGNAL_COUNT = 3
MIN_VALIDATION_WIN_RATE = 0.45
MIN_VALIDATION_AVG_RET = 0.0
MIN_VALIDATION_SCORE_DELTA = 0.0


def _to_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int | None = None) -> int | None:
    try:
        if v in (None, ""):
            return default
        return int(float(v))
    except Exception:
        return default


def _fmt_float(v: Any, default: float = 0.0) -> float:
    parsed = _to_float(v)
    return default if parsed is None else parsed


def _fmt_int(v: Any, default: int = 0) -> int:
    parsed = _to_int(v)
    return default if parsed is None else parsed


def _decision(row: dict[str, Any]) -> tuple[str, str]:
    trials = _to_int(row.get("trials"))
    baseline_status = str(row.get("baseline_status") or "")
    optuna_status = str(row.get("optuna_status") or "")
    baseline_score = _to_float(row.get("baseline_score"), float("-inf"))
    score_delta = _to_float(row.get("score_delta"), float("-inf"))
    signal_count = _to_int(row.get("optuna_signal_count"))
    win_rate = _to_float(row.get("optuna_win_rate"))
    avg_ret = _to_float(row.get("optuna_avg_ret"))
    validation_score_delta = _to_float(row.get("validation_score_delta"), float("-inf"))
    validation_signal_count = _to_int(row.get("optuna_validation_signal_count"))
    validation_win_rate = _to_float(row.get("optuna_validation_win_rate"))
    validation_avg_ret = _to_float(row.get("optuna_validation_avg_ret"))

    reasons: list[str] = []
    if baseline_status != "ok":
        reasons.append(f"baseline_status={baseline_status or 'missing'}")
        baseline_investigation = str(row.get("baseline_investigation") or row.get("baseline_reason") or "")
        if baseline_investigation:
            reasons.append(f"baseline_investigation={baseline_investigation}")
    if optuna_status != "ok":
        reasons.append(f"optuna_status={optuna_status or 'missing'}")
        optuna_investigation = str(row.get("optuna_investigation") or row.get("optuna_reason") or "")
        if optuna_investigation:
            reasons.append(f"optuna_investigation={optuna_investigation}")
    if trials is None:
        reasons.append("missing_metric=trials")
    elif trials < MIN_TRIALS:
        reasons.append(f"trials<{MIN_TRIALS}")
    if baseline_status == "ok":
        if baseline_score is None:
            reasons.append("missing_metric=baseline_score")
        elif baseline_score < MIN_BASELINE_SCORE:
            reasons.append(f"baseline_score<{MIN_BASELINE_SCORE}")
    if optuna_status == "ok" and baseline_status == "ok":
        if score_delta is None:
            reasons.append("missing_metric=score_delta")
        elif score_delta < MIN_SCORE_DELTA:
            reasons.append(f"delta<{MIN_SCORE_DELTA}")
    if optuna_status == "ok":
        if signal_count is None:
            reasons.append("missing_metric=optuna_signal_count")
        elif signal_count < MIN_SIGNAL_COUNT:
            reasons.append(f"signals<{MIN_SIGNAL_COUNT}")
        if win_rate is None:
            reasons.append("missing_metric=optuna_win_rate")
        elif win_rate < MIN_WIN_RATE:
            reasons.append(f"win_rate<{MIN_WIN_RATE}")
        if avg_ret is None:
            reasons.append("missing_metric=optuna_avg_ret")
        elif avg_ret <= MIN_AVG_RET:
            reasons.append("avg_ret<=0")
        if validation_signal_count is None:
            reasons.append("missing_metric=optuna_validation_signal_count")
        elif validation_signal_count < MIN_VALIDATION_SIGNAL_COUNT:
            reasons.append(f"validation_signals<{MIN_VALIDATION_SIGNAL_COUNT}")
        if validation_win_rate is None:
            reasons.append("missing_metric=optuna_validation_win_rate")
        elif validation_win_rate < MIN_VALIDATION_WIN_RATE:
            reasons.append(f"validation_win_rate<{MIN_VALIDATION_WIN_RATE}")
        if validation_avg_ret is None:
            reasons.append("missing_metric=optuna_validation_avg_ret")
        elif validation_avg_ret <= MIN_VALIDATION_AVG_RET:
            reasons.append("validation_avg_ret<=0")
    if optuna_status == "ok" and baseline_status == "ok":
        if validation_score_delta is None:
            reasons.append("missing_metric=validation_score_delta")
        elif validation_score_delta < MIN_VALIDATION_SCORE_DELTA:
            reasons.append(f"validation_delta<{MIN_VALIDATION_SCORE_DELTA}")

    if reasons:
        return "reject", "; ".join(reasons)
    return "candidate", "passes_guardrails"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Apply local Optuna adoption guardrails to sample or batch CSV files.")
    parser.add_argument("--input", type=Path, default=SAMPLES_CSV)
    parser.add_argument("--output", type=Path, default=OUT_CSV)
    parser.add_argument("--report", type=Path, default=OUT_MD)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"missing {args.input}")
    rows: list[dict[str, Any]] = []
    with args.input.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            decision, reason = _decision(row)
            row["adoption_decision"] = decision
            row["adoption_reason"] = reason
            rows.append(row)

    rows.sort(
        key=lambda r: (
            r.get("adoption_decision") == "candidate",
            _fmt_float(r.get("score_delta"), float("-inf")),
            _fmt_float(r.get("optuna_score"), float("-inf")),
        ),
        reverse=True,
    )

    fieldnames = list(rows[0].keys()) if rows else []
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    candidates = [r for r in rows if r.get("adoption_decision") == "candidate"]
    rejected = [r for r in rows if r.get("adoption_decision") != "candidate"]
    args.report.write_text(
        "\n".join(
            [
                "# Formula Local Optuna Adoption Candidates",
                "",
                f"- input: `{args.input}`",
                f"- output: `{args.output}`",
                "",
                "## Guardrails",
                "",
                f"- baseline_score >= `{MIN_BASELINE_SCORE}`",
                "- baseline_status must be `ok`; missing baseline rows are rejected and investigated via status/reason fields.",
                "- optuna_status must be `ok`; missing Optuna results are rejected and investigated via status/reason fields.",
                f"- optuna_signal_count >= `{MIN_SIGNAL_COUNT}`",
                f"- score_delta >= `{MIN_SCORE_DELTA}`",
                f"- optuna_win_rate >= `{MIN_WIN_RATE}`",
                f"- optuna_avg_ret > `{MIN_AVG_RET}`",
                f"- trials >= `{MIN_TRIALS}`",
                f"- optuna_validation_signal_count >= `{MIN_VALIDATION_SIGNAL_COUNT}`",
                f"- optuna_validation_win_rate >= `{MIN_VALIDATION_WIN_RATE}`",
                f"- optuna_validation_avg_ret > `{MIN_VALIDATION_AVG_RET}`",
                f"- validation_score_delta >= `{MIN_VALIDATION_SCORE_DELTA}`",
                "",
                "## Summary",
                "",
                f"- rows: `{len(rows)}`",
                f"- candidates: `{len(candidates)}`",
                f"- rejected: `{len(rejected)}`",
                "",
                "## Candidates",
                "",
                "| stock | formula | baseline | optuna | delta | val_delta | signals | val_signals | win | val_win | avg_ret | val_ret | sell_rule |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
                *[
                    f"| `{r['stock_code']}` | `{r['formula_id']}` | {_fmt_float(r['baseline_score']):.2f} | "
                    f"{_fmt_float(r['optuna_score']):.2f} | {_fmt_float(r['score_delta']):.2f} | "
                    f"{_fmt_float(r['validation_score_delta']):.2f} | "
                    f"{_fmt_int(r['optuna_signal_count'])} | {_fmt_int(r['optuna_validation_signal_count'])} | "
                    f"{_fmt_float(r['optuna_win_rate']):.2%} | {_fmt_float(r['optuna_validation_win_rate']):.2%} | "
                    f"{_fmt_float(r['optuna_avg_ret']):.2%} | {_fmt_float(r['optuna_validation_avg_ret']):.2%} | "
                    f"`{r['optuna_sell_rule']}` |"
                    for r in candidates
                ],
                "",
                "## Rejection Reason Counts",
                "",
                "```json",
                json.dumps(_reason_counts(rejected), ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
                "## Production Merge Policy",
                "",
                "- Do not merge rows marked `reject` into `stock_formula_best.csv`.",
                "- Candidate rows pass a chronological validation split, but still require a full-market production run before replacement.",
                "- Rows with `baseline_status!=ok` are discovery or data-quality leads, not scored improvements.",
                "- Missing metrics are reported as `missing_metric=...`; they are never treated as zero-value backtest results.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"formula_local_optuna_adoption: rows={len(rows)} candidates={len(candidates)} rejected={len(rejected)}")


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in str(row.get("adoption_reason") or "").split("; "):
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return counts


if __name__ == "__main__":
    main()
