#!/usr/bin/env python3
"""Pre-commit hook: commit-msg stage — 强制 code-relevant commit 引用 Codex review (CLAUDE Rule 10).

根因 (用户 2026-05-16 push): "让 codex review 这事儿建成 hook 了么".

CLAUDE Rule 10 要求: 任何代码阶段性 commit 必须先 Codex review.
豁免: 纯 markdown / 改名/路径替换 / 修错别字.

本 hook 轻量验证 (不 auto-invoke Codex, 不 block 60-100s):
1. 检测 commit 是否含 code-relevant 文件 (backend/services/scripts/config/tests/...)
2. 如是 → commit message body 必须含 Codex review evidence:
   `Codex-Reviewed: APPROVE[_WITH_NOTES]`, agent ID, "codex review",
   or `codex-review: skipped reason=...`
3. 否则 reject 提醒 dev invoke `codex:rescue --model gpt-5.5 --effort xhigh`

精度: 实际是否 invoke 真 Codex 不强检 (信任 dev), 但 message 留痕便于 audit.

Bypass: 显式 `codex-review: skipped reason=<typo|rename|markdown|trivial>` 在 message body.
强烈不建议 `--no-verify`.

Codex 引用 keywords (任一即 PASS):
- "codex " (大小写, 含空格防误命中变量名)
- "Codex " (大写)
- "agent " + alphanumeric ID hint
- 6-char hex pattern (e.g. "aa2d79d2", "acf91c1f") — 历史 codex agent ID 格式
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


# 触发文件 (这些路径有改动 → 强制 Codex review evidence)
CODE_PATH_PREFIXES = (
    "backend/services/",
    "backend/scripts/optimize_",
    "backend/scripts/run_",
    "backend/scripts/build_",
    "backend/scripts/train_",
    "backend/scripts/audit_",
    "backend/scripts/check_",
    "backend/scripts/backfill_",
    "backend/scripts/rebuild_",
    "backend/config/",
    "backend/tests/",
)


EXEMPT_PATH_SUFFIXES = (
    ".md",  # markdown doc only
    ".txt",
    ".html",
)


EXEMPT_BODY_MARKERS: tuple[str, ...] = ()


CODEX_REVIEW_KEYWORDS = (
    "codex ",
    "Codex ",
    "agent ",       # subagent 调用
    "codex-rescue",
    "ad2e09e7",     # 历史 agent IDs (Codex pattern 8-char hex)
    "acf91c1f",
    "aa2d79d2",
    "aa4a41ca",
    "ac61258a",     # generic 8-char hex hint
)

APPROVED_CODEX_REVIEW_RE = re.compile(
    r"Codex-Reviewed:[ \t]*(APPROVE_WITH_NOTES|APPROVE)([ \t]|\(|$)"
)
REQUEST_CHANGES_REVIEW_RE = re.compile(
    r"Codex-Reviewed:[ \t]*REQUEST_CHANGES([ \t]|\(|$)"
)
SKIP_REASON_RE = re.compile(
    r"(?:#\s*)?codex-review:\s*skipped reason=(?P<reason>.+)",
    re.IGNORECASE,
)
MIN_SKIP_REASON_CHARS = 8


def get_staged_files() -> list[str]:
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.strip().split("\n") if f]


def needs_codex_review(staged: list[str]) -> bool:
    """是否 code-relevant (排除纯 doc / markdown commit)."""
    has_code = False
    for f in staged:
        # exempt suffixes (markdown / txt / html)
        if any(f.endswith(s) for s in EXEMPT_PATH_SUFFIXES):
            continue
        if any(f.startswith(p) for p in CODE_PATH_PREFIXES):
            has_code = True
            break
    return has_code


def has_codex_reference(body: str) -> bool:
    """body 是否含 Codex 引用 (agent ID / 'codex' / 'agent')."""
    if any(kw in body for kw in CODEX_REVIEW_KEYWORDS):
        return True
    # 8-char hex ID pattern (Codex agent ID 格式)
    if re.search(r"\b[a-f0-9]{8}\b", body):
        return True
    return False


def has_approved_codex_review(body: str) -> bool:
    """body 是否含当前 Rule 10 canonical approval trailer."""
    return APPROVED_CODEX_REVIEW_RE.search(body) is not None


def has_rejected_codex_review(body: str) -> bool:
    """body 是否显式记录了 REQUEST_CHANGES verdict."""
    return REQUEST_CHANGES_REVIEW_RE.search(body) is not None


def has_meaningful_skip_reason(body: str) -> bool:
    """body 是否含足够具体的 skip reason, 兼容有无 leading #."""
    for match in SKIP_REASON_RE.finditer(body):
        reason = match.group("reason").strip()
        if len(reason) >= MIN_SKIP_REASON_CHARS:
            return True
    return False


def main(msg_path: str) -> int:
    msg = Path(msg_path).read_text(encoding="utf-8")
    body = msg

    if has_rejected_codex_review(body):
        print("=" * 80, file=sys.stderr)
        print("ERROR: Codex review verdict is REQUEST_CHANGES.", file=sys.stderr)
        print("修法: 先消除 review objections, 再提交 APPROVE / APPROVE_WITH_NOTES。", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        return 1

    # bypass marker
    if any(m in body for m in EXEMPT_BODY_MARKERS) or has_meaningful_skip_reason(body):
        return 0

    # ignore comment lines
    body_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    body_only = "\n".join(body_lines)

    staged = get_staged_files()
    if not needs_codex_review(staged):
        return 0   # 纯 doc / markdown, 跳过

    if has_approved_codex_review(body_only) or has_codex_reference(body_only):
        return 0

    # Reject + 提示
    print("=" * 80, file=sys.stderr)
    # 2026-06-12 用户决议: Codex review 强制解除 — 降级为信息提示, 永不阻塞
    print("INFO: commit 无 Codex review evidence (2026-06-12 决议: 不再强制)", file=sys.stderr)
    return 0
    print(file=sys.stderr)
    print("Staged code files:", file=sys.stderr)
    code_files = [f for f in staged if any(f.startswith(p) for p in CODE_PATH_PREFIXES)
                  and not any(f.endswith(s) for s in EXEMPT_PATH_SUFFIXES)]
    for f in code_files[:10]:
        print(f"  {f}", file=sys.stderr)
    if len(code_files) > 10:
        print(f"  ... 另 {len(code_files) - 10} 文件", file=sys.stderr)
    print(file=sys.stderr)
    print("CLAUDE Rule 10 要求: 任何代码 commit 必须先 Codex review.", file=sys.stderr)
    print("Codex 调用方式:", file=sys.stderr)
    print("  /codex:rescue --model gpt-5.5 --effort xhigh <prompt>", file=sys.stderr)
    print(file=sys.stderr)
    print("commit message body 必须含以下任一 evidence:", file=sys.stderr)
    print("  - 'Codex-Reviewed: APPROVE' 或 'Codex-Reviewed: APPROVE_WITH_NOTES'", file=sys.stderr)
    print("  - 'Codex <agent_id>' (e.g. Codex ad2e09e7)", file=sys.stderr)
    print("  - 'codex review' / 'codex-rescue' / 'agent <ID>'", file=sys.stderr)
    print("  - 8-char hex agent ID pattern", file=sys.stderr)
    print(file=sys.stderr)
    print("Bypass (慎用, 加在 message body 内):", file=sys.stderr)
    print("  'codex-review: skipped reason=typo'", file=sys.stderr)
    print("  'codex-review: skipped reason=rename'", file=sys.stderr)
    print("  'codex-review: skipped reason=markdown'", file=sys.stderr)
    print("  'codex-review: skipped reason=trivial'", file=sys.stderr)
    print(file=sys.stderr)
    print("根因: CLAUDE Rule 10 — code 阶段性 commit 必须 Codex review.", file=sys.stderr)
    print("无 Codex review 的 code commit 下次 session 重读时无法 audit 是否经过 critical 检查.", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: check_codex_review.py <COMMIT_MSG_FILE>", file=sys.stderr)
        sys.exit(0)
    sys.exit(main(sys.argv[1]))
