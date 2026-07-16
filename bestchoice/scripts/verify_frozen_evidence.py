"""Fail-closed verifier for the frozen BestChoice evidence bundle."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "evidence_manifest.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AssertionError(f"missing CSV header: {path}")
        return reader.fieldnames, list(reader)


def _verify_file(relative_path: str, spec: dict[str, Any]) -> list[dict[str, str]] | None:
    path = ROOT / relative_path
    _require(path.is_file(), f"missing frozen artifact: {relative_path}")
    _require(_sha256(path) == spec["sha256"], f"sha256 mismatch: {relative_path}")
    if path.suffix != ".csv":
        return None

    fields, rows = _read_csv(path)
    _require(len(fields) == spec["columns"], f"column count mismatch: {relative_path}")
    _require(len(rows) == spec["rows"], f"row count mismatch: {relative_path}")
    keys = [tuple(row[field] for field in spec["unique_key"]) for row in rows]
    _require(len(keys) == len(set(keys)), f"duplicate frozen key: {relative_path}")
    return rows


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    _require(manifest["status"] == "historical_frozen_challenger", "invalid status")

    adoption_rows: list[dict[str, str]] | None = None
    for relative_path, spec in manifest["files"].items():
        rows = _verify_file(relative_path, spec)
        if relative_path.endswith("formula_local_optuna_batch_adoption.csv"):
            adoption_rows = rows

    if adoption_rows is None:
        raise RuntimeError("adoption evidence not declared")
    decision_counts = Counter(row["adoption_decision"] for row in adoption_rows)
    _require(
        dict(decision_counts) == manifest["adoption_decision_counts"],
        "adoption decision counts mismatch",
    )
    formula_counts = Counter(
        row["formula_id"]
        for row in adoption_rows
        if row["adoption_decision"] == "candidate"
    )
    normalized_counts = {
        formula_id: formula_counts.get(formula_id, 0)
        for formula_id in manifest["formula_ids"]
    }
    _require(
        normalized_counts == manifest["candidate_formula_counts"],
        "candidate formula counts mismatch",
    )
    _require(
        all(
            row["adoption_reason"].strip()
            for row in adoption_rows
            if row["adoption_decision"] == "reject"
        ),
        "reject row without reason",
    )
    print("verify_frozen_evidence: ok")


if __name__ == "__main__":
    main()
