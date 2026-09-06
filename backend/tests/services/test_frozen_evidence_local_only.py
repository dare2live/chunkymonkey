"""冻结证据清单的 `local_only` 声明必须与 git 跟踪现实一致。

## 这条测试为什么存在

`verify_frozen_evidence.py` 对 `local_only: true` 的条目在**文件不存在时跳过而不是 FAIL**——
因为那两个 26 MB 的 CSV 被 `.gitignore` 按体积排除, 全新克隆里不可能有它们。

但这个豁免必须**不能被滥用**: 把一个真正跟踪的文件标成 `local_only`, 它被误删时就会
静默通过。所以豁免的边界由本测试机器守住 —— `local_only` 集合必须**逐字等于** git 忽略集合。

反过来也守: 一个 gitignored 的条目**没有**标 `local_only`, 会让 CI 永远红
(2026-09-01 到 09-06 就是这个状态, 连红 10 次, 9 个测试文件连坐, 而本地全绿)。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "bestchoice" / "evidence_manifest.json"


def _tracked(rel: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--error-unmatch", f"bestchoice/{rel}"],
        capture_output=True,
    ).returncode == 0


def _manifest_files() -> dict[str, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]


def test_local_only_exactly_matches_untracked_set() -> None:
    """声明与现实逐字相等 —— 两个方向都验, 不只验一边。"""
    files = _manifest_files()
    assert files, "manifest.files 为空 —— 空清单会让验证器恒绿, fail closed"

    declared = {rel for rel, spec in files.items() if spec.get("local_only") is True}
    actual = {rel for rel in files if not _tracked(rel)}

    over = sorted(declared - actual)   # 标了 local_only 但其实是跟踪文件 = 私自削弱封印
    under = sorted(actual - declared)  # 是 gitignored 却没标 = 全新克隆里必红

    assert not over, (
        "这些条目是 git 跟踪的, 却被标成 local_only —— 它们被误删时验证器会静默放过:\n"
        + "".join(f"    {r}\n" for r in over)
    )
    assert not under, (
        "这些条目未被 git 跟踪, 却没标 local_only —— 全新克隆(CI)里必然 FAIL:\n"
        + "".join(f"    {r}\n" for r in under)
    )


def test_code_artifacts_are_never_local_only() -> None:
    """.py 是封印的本体, 永远必须跟踪且必须验 —— 它不能有豁免。"""
    bad = [
        rel for rel, spec in _manifest_files().items()
        if rel.endswith(".py") and spec.get("local_only") is True
    ]
    assert not bad, f"代码条目不许标 local_only: {bad}"


def test_every_entry_declares_a_sha256() -> None:
    """local_only 只放宽「不在时怎么办」, 不放宽「在时验什么」——sha256 一个都不能少。"""
    missing = [rel for rel, spec in _manifest_files().items() if not spec.get("sha256")]
    assert not missing, f"缺 sha256 的条目: {missing}"
