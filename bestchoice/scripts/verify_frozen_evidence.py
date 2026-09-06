"""Fail-closed verifier for the frozen BestChoice evidence bundle.

每个被封条目在 manifest 里声明 ``local_only``:

  local_only=false (代码)  必须存在, 必须 hash 相符。缺失 = FAIL。
  local_only=true  (26 MB 的 analysis/*.csv, 被 .gitignore 按体积排除)
                   **在时照常全量校验**(hash + 行列数 + 唯一键 + 内容断言);
                   **不在时记为 skipped 而不是 FAIL** —— 一个全新克隆里它们不可能存在,
                   在那里报 FAIL 不是"守住了", 是把判据放在答案不可能对的地方跑。

为什么是声明而不是"文件不在就放过": 后者会连真正被误删的**跟踪**文件一起放过。
声明与现实的一致性由 ``backend/tests/services/test_frozen_evidence_local_only.py``
钉住 —— local_only 集合必须逐字等于 git 忽略集合, 把一个跟踪文件标成 local_only 会让它红。

2026-09-06: 本文件此前对 local_only 条目也 FAIL, 导致 GitHub CI 自 2026-09-01
(那两个 CSV 因体积移出跟踪) 起**连续 10 次全红**, 9 个测试文件连坐, 而本地全绿。
"""
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


def _verify_file(relative_path: str, spec: dict[str, Any]) -> tuple[str, list[dict[str, str]] | None]:
    """返回 (status, rows)。status ∈ {"verified", "skipped_local_only_absent"}。"""
    path = ROOT / relative_path
    if not path.is_file():
        if spec.get("local_only") is True:
            # 全新克隆里它不可能存在 —— 记为跳过并在 main() 里打印, 不静默。
            return ("skipped_local_only_absent", None)
        _require(False, f"missing frozen artifact: {relative_path}")
    # 文件在 ⇒ 照常全量校验, local_only 不降低任何标准。
    _require(_sha256(path) == spec["sha256"], f"sha256 mismatch: {relative_path}")
    if path.suffix != ".csv":
        return ("verified", None)

    fields, rows = _read_csv(path)
    _require(len(fields) == spec["columns"], f"column count mismatch: {relative_path}")
    _require(len(rows) == spec["rows"], f"row count mismatch: {relative_path}")
    keys = [tuple(row[field] for field in spec["unique_key"]) for row in rows]
    _require(len(keys) == len(set(keys)), f"duplicate frozen key: {relative_path}")
    return ("verified", rows)


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    _require(manifest["status"] == "historical_frozen_challenger", "invalid status")

    adoption_rows: list[dict[str, str]] | None = None
    adoption_declared = False
    verified: list[str] = []
    skipped: list[str] = []
    for relative_path, spec in manifest["files"].items():
        status, rows = _verify_file(relative_path, spec)
        (verified if status == "verified" else skipped).append(relative_path)
        if relative_path.endswith("formula_local_optuna_batch_adoption.csv"):
            adoption_declared = True
            adoption_rows = rows

    # 「清单里没声明」是真错误; 「声明了但本地没有」是可跳过 —— 两者必须分开判,
    # 合在一起就等于让「清单被人删掉一条」也悄悄通过。
    _require(adoption_declared, "adoption evidence not declared")

    if adoption_rows is None:
        print(
            "verify_frozen_evidence: partial "
            f"(verified={len(verified)} skipped_local_only={len(skipped)}: {', '.join(skipped)}) "
            "—— 内容断言(采纳计数/候选计数/驳回理由)未跑, 因为它们依赖的 CSV 不在本机"
        )
        return
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
    print(
        f"verify_frozen_evidence: ok (verified={len(verified)}"
        + (f" skipped_local_only={len(skipped)}: {', '.join(skipped)}" if skipped else "")
        + ")"
    )


if __name__ == "__main__":
    main()
