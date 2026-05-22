from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.formula_parameter_search import _load_market_rows

ANALYSIS_DIR = ROOT / "analysis"
ADOPTION = ANALYSIS_DIR / "formula_local_optuna_batch_adoption.csv"
MERGE_PLAN = ANALYSIS_DIR / "formula_local_optuna_batch_merge_plan.csv"
REPLACEMENTS = ANALYSIS_DIR / "formula_local_optuna_batch_stock_best_replacements.csv"
STOCK_BEST = ANALYSIS_DIR / "stock_formula_best.csv"
RESEARCH_CACHE = ANALYSIS_DIR / "research_cache.duckdb"
INCREMENTAL_EVAL = ANALYSIS_DIR / "incremental_eval.duckdb"
DRIFT_TRIGGER = ANALYSIS_DIR / "drift_trigger.duckdb"
OUT_JSON = ANALYSIS_DIR / "formula_local_optuna_aggregate_audit.json"
OUT_MD = ANALYSIS_DIR / "formula_local_optuna_aggregate_audit.md"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _headers(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f).__next__())


def _duck_count(path: Path, table: str) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return int(con.execute(f"select count(*) from {table}").fetchone()[0] or 0)
    finally:
        con.close()


def _manifest(path: Path) -> dict[str, str]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return dict(con.execute("select key, value from cache_manifest").fetchall())
    finally:
        con.close()


def _missing_without_reason(rows: list[dict[str, str]]) -> int:
    return sum(
        1
        for row in rows
        if (
            row.get("baseline_status")
            and row.get("baseline_status") != "ok"
            and not row.get("baseline_investigation")
        )
        or (
            row.get("optuna_status")
            and row.get("optuna_status") != "ok"
            and not row.get("optuna_investigation")
        )
    )


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def main() -> None:
    adoption_rows = _rows(ADOPTION)
    merge_rows = _rows(MERGE_PLAN)
    replacement_rows = _rows(REPLACEMENTS)
    market_codes = [str(row["code"]).zfill(6) for row in _load_market_rows(0)]
    market_code_set = set(market_codes)
    adoption_codes = {row.get("stock_code", "") for row in adoption_rows if row.get("stock_code")}
    formula_ids = {row.get("formula_id", "") for row in adoption_rows if row.get("formula_id")}
    candidate_count = sum(1 for row in adoption_rows if row.get("adoption_decision") == "candidate")
    rejected_count = len(adoption_rows) - candidate_count
    formula_distribution = Counter(
        row.get("formula_id", "")
        for row in adoption_rows
        if row.get("adoption_decision") == "candidate"
    )
    research_manifest = _manifest(RESEARCH_CACHE)
    source_rows = json.loads(research_manifest.get("source_rows", "{}"))
    incremental_rows = _duck_count(INCREMENTAL_EVAL, "incremental_eval_state")
    dirty_rows = _duck_count(INCREMENTAL_EVAL, "incremental_eval_state where status != 'clean'")
    drift_rows = _duck_count(DRIFT_TRIGGER, "drift_trigger")
    stock_best_headers = set(_headers(STOCK_BEST))
    replacement_headers = set(_headers(REPLACEMENTS))
    missing_required_replacement_headers = sorted(stock_best_headers - replacement_headers)

    checks = [
        _check(
            "full_market_coverage",
            adoption_codes == market_code_set,
            f"covered={len(adoption_codes)} market_total={len(market_code_set)}",
        ),
        _check(
            "row_count_matches_stock_formula_grid",
            len(adoption_rows) == len(market_code_set) * len(formula_ids),
            f"rows={len(adoption_rows)} stocks={len(market_code_set)} formulas={len(formula_ids)}",
        ),
        _check(
            "merge_plan_matches_adoption",
            len(merge_rows) == len(adoption_rows),
            f"merge_rows={len(merge_rows)} adoption_rows={len(adoption_rows)}",
        ),
        _check(
            "replacements_match_candidates",
            len(replacement_rows) == candidate_count,
            f"replacements={len(replacement_rows)} candidates={candidate_count}",
        ),
        _check(
            "replacement_schema_compatible",
            not missing_required_replacement_headers,
            "missing_headers=" + ",".join(missing_required_replacement_headers),
        ),
        _check(
            "missing_rows_have_investigation",
            _missing_without_reason(adoption_rows) == 0,
            f"missing_without_reason={_missing_without_reason(adoption_rows)}",
        ),
        _check(
            "research_cache_source_rows_match",
            source_rows.get("adoption") == len(adoption_rows)
            and source_rows.get("merge_plan") == len(merge_rows),
            f"source_rows={source_rows}",
        ),
        _check(
            "incremental_eval_clean",
            incremental_rows == _duck_count(RESEARCH_CACHE, "research_cache") and dirty_rows == 0,
            f"incremental_rows={incremental_rows} dirty_rows={dirty_rows}",
        ),
        _check(
            "drift_trigger_current",
            drift_rows == incremental_rows,
            f"drift_rows={drift_rows} incremental_rows={incremental_rows}",
        ),
    ]
    passed = all(check["passed"] for check in checks)
    summary = {
        "passed": passed,
        "market_total": len(market_code_set),
        "adoption_rows": len(adoption_rows),
        "merge_plan_rows": len(merge_rows),
        "candidate_count": candidate_count,
        "rejected_count": rejected_count,
        "replacement_count": len(replacement_rows),
        "formula_distribution": dict(sorted(formula_distribution.items())),
        "research_cache_rows": _duck_count(RESEARCH_CACHE, "research_cache"),
        "incremental_eval_rows": incremental_rows,
        "drift_trigger_rows": drift_rows,
        "data_latest_date": research_manifest.get("data_latest_date"),
        "checks": checks,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Local Optuna Aggregate Audit",
        "",
        f"- passed: `{passed}`",
        f"- market_total: `{summary['market_total']}`",
        f"- adoption_rows: `{summary['adoption_rows']}`",
        f"- candidates: `{candidate_count}`",
        f"- replacements: `{len(replacement_rows)}`",
        f"- data_latest_date: `{summary['data_latest_date']}`",
        "",
        "## Candidate Formula Distribution",
        "",
    ]
    lines.extend(f"- `{key}`: `{value}`" for key, value in summary["formula_distribution"].items())
    lines.extend(["", "## Checks", ""])
    lines.extend(
        f"- [{'x' if check['passed'] else ' '}] `{check['name']}`: {check['detail']}"
        for check in checks
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        "formula_local_optuna_aggregate_audit: "
        f"passed={passed} rows={len(adoption_rows)} candidates={candidate_count} "
        f"replacements={len(replacement_rows)}"
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
