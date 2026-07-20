"""Blocking Rule 10 commit-msg gate (owner: AGENTS.md + engineering governance).

Any risky staged file must carry the canonical independent-review verdict
``Codex-Reviewed: APPROVE`` or ``APPROVE_WITH_NOTES``. Generic Codex/agent text,
hex IDs and ``codex-review: skipped`` are not review evidence. An explicit
``REQUEST_CHANGES`` always blocks. This mirrors ``scripts/safe_commit.sh`` so a
direct ``git commit`` cannot bypass the reviewed delivery path.

WP1: L1 commits (docs/analysis/sandbox only, machine-classified) skip Rule 10.
Classification is fail-closed — unknown/missing policy → L3 → review required.
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

    # Reject + 提示
    print("=" * 80, file=sys.stderr)
    print("ERROR: Rule 10 requires Codex-Reviewed: APPROVE or APPROVE_WITH_NOTES.", file=sys.stderr)
    print("Generic Codex/agent references and skip reasons do not satisfy the gate.", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_codex_review.py <COMMIT_MSG_FILE>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
