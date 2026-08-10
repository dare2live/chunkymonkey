"""Rule 10 commit-msg gate (owner: AGENTS.md + engineering governance).

2026-08-10 裁决 — 本门只保留能被验证的那一半：

* ``Codex-Reviewed: REQUEST_CHANGES`` **仍然阻断**。它有信息量：没有人会「忘记」
  写下一个否定裁决，写下它就意味着确有未消除的异议，忽略它是实质风险。
* 缺少 ``APPROVE`` / ``APPROVE_WITH_NOTES`` **不再阻断**，降为提示。

为什么取消 APPROVE 作为通过条件：本门的唯一输入是提交者自己写的 commit
message（见 ``safe_commit.sh`` Step 4.5 与 ``.git/hooks/commit-msg``），它做的
全部事情是在那段文本里正则匹配一行字符串。它无法验证审查是否发生、审查者是
谁、是否独立于提交者。按本项目自己的判据（committer 自写 justification + 无
复核 = 摆设），把它当红线门有三个坏结果：

1. 挡不住不做审查的人 —— 补一行字即过；
2. 只挡住不愿假称「审过了」的诚实提交者；
3. 最糟的一层：制造「所有 L3 改动都经过独立审查」的**虚假保证**，反而让人不
   再去做真正的审查。比不检查更糟。

原则：**一件事若无法机器验证，就不要用机器门假装验证它 —— 写进规则，别写进
闸。** 真要强制独立审查，enforcement 必须落在提交者够不到的地方（CI / PR 侧
reviewer），本地 commit-msg hook 天然做不到。

安全性不依赖本门：PIT / leakage / continuity / lineage / population / calendar
等门读的是代码与数据，提交者无法用措辞影响它们。

仍然阻断的另一项：staged 范围无法确定时 fail-closed（返回 2）——那是客观事实，
不是自述。

WP1: L1 commits (docs/analysis/sandbox only, machine-classified) skip Rule 10.
Classification is fail-closed — unknown/missing policy → L3.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


RISKY_SUFFIXES = {".py", ".yaml", ".yml", ".sql", ".md"}
RISKY_EXACT = {".gitignore", "data/lineage/graph.json"}

APPROVED_CODEX_REVIEW_RE = re.compile(
    r"(?m)^[ \t]*Codex-Reviewed:[ \t]*(APPROVE_WITH_NOTES|APPROVE)(?:[ \t]|\(|$)"
)
REQUEST_CHANGES_REVIEW_RE = re.compile(
    r"(?m)^[ \t]*Codex-Reviewed:[ \t]*REQUEST_CHANGES(?:[ \t]|\(|$)"
)


class StagedFileScanError(RuntimeError):
    """The gate could not determine the staged scope and must fail closed."""


def get_staged_files() -> list[str]:
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTD"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        detail = r.stderr.strip() or f"exit {r.returncode}"
        raise StagedFileScanError(f"git staged-file scan failed: {detail}")
    return [f for f in r.stdout.strip().split("\n") if f]


def needs_codex_review(staged: list[str]) -> bool:
    """Match the risky staged set enforced by ``safe_commit.sh``."""
    for f in staged:
        suffix = Path(f).suffix.lower()
        if suffix in RISKY_SUFFIXES or f in RISKY_EXACT:
            return True
        if f.startswith("scripts/") and suffix == ".sh":
            return True
    return False


def commit_tier_for_staged(staged: list[str]) -> str:
    """Return L1/L2/L3 for staged paths; any classifier failure → L3."""
    try:
        from scripts.classify_commit_tier import classify
    except ImportError:
        try:
            from classify_commit_tier import classify  # type: ignore
        except ImportError:
            return "L3"
    try:
        result = classify(staged, scan_content=True)
        tier = result.get("tier")
        return tier if tier in {"L1", "L2", "L3"} else "L3"
    except Exception:  # noqa: BLE001 — fail closed
        return "L3"


def has_approved_codex_review(body: str) -> bool:
    """Return true only for the canonical Rule 10 approval trailer."""
    return APPROVED_CODEX_REVIEW_RE.search(body) is not None


def has_rejected_codex_review(body: str) -> bool:
    """Return true for an explicit blocking reviewer verdict."""
    return REQUEST_CHANGES_REVIEW_RE.search(body) is not None


def main(msg_path: str) -> int:
    body = Path(msg_path).read_text(encoding="utf-8")
    try:
        staged = get_staged_files()
    except StagedFileScanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Rule 10 cannot prove the staged scope; refusing the commit.", file=sys.stderr)
        return 2

    if has_rejected_codex_review(body):
        print("=" * 80, file=sys.stderr)
        print("ERROR: Codex review verdict is REQUEST_CHANGES.", file=sys.stderr)
        print("修法: 先消除 review objections, 再提交 APPROVE / APPROVE_WITH_NOTES。", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        return 1

    # L1 (docs/analysis/sandbox) skips Rule 10; REQUEST_CHANGES already blocked above.
    if commit_tier_for_staged(staged) == "L1":
        return 0

    if not needs_codex_review(staged):
        return 0

    # ignore comment lines
    body_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    body_only = "\n".join(body_lines)

    if has_approved_codex_review(body_only):
        return 0

    # 提示（不阻断）— 裁决理由见模块 docstring。
    print("=" * 80, file=sys.stderr)
    print("NOTE: Rule 10 — staged 含 L2/L3 风险文件，message 未带 Codex-Reviewed 裁决行。", file=sys.stderr)
    print("  建议对 .py/.yaml/.sql 改动做一次**独立**审查（$chunkymonkey-review-gate），", file=sys.stderr)
    print("  并把 verdict 与审查者身份写进 message —— 那对下次接手的人有用。", file=sys.stderr)
    print("  不阻断的理由: 本门只能匹配提交者自写的字符串, 无法验证审查是否真发生;", file=sys.stderr)
    print("  阻断只会卡住诚实的提交者, 并制造'都审过了'的虚假保证。REQUEST_CHANGES 仍阻断。", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_codex_review.py <COMMIT_MSG_FILE>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
