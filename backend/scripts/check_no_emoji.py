#!/usr/bin/env python3
"""Pre-commit hook: 禁止 emoji 出现在代码/yaml/markdown/commit body.

根因: 用户 firm preference (memory feedback_no_emoji): "项目全局不用 emoji (代码/UI/文档/commit message 全部)".

我反复在 docs / yaml / commit message 加 emoji (✅ ❌ 🚀 📊 ⚠️ 等). Rule 文字是被动的, hook 硬挡.

ban 范围:
  - 高 codepoint emoji: U+1F300-U+1FAFF (misc pictographs / emoticons / supplemental)
  - flag regional indicators: U+1F1E6-U+1F1FF
  - dingbats / box-drawing 中 emoji 化变体: U+2700-U+27BF
  - Compound emoji 序列 marker: ZWJ (U+200D) + Variation Selector-16 (U+FE0F)

NOT ban (项目已大量使用):
  - 中日韩字符 (CJK)
  - Greek 字母 ψ γ β
  - 箭头 → ← ↑ ↓ (U+2190-21FF)
  - ⛔ U+26D4 (项目历史红线标记)
  - ⚠ U+26A0 单字符 (无 VS16, doc 警告)

Whitelist (跳过检测):
  - backend/scripts/check_no_emoji.py 自己 (本 file 含 emoji 例)
  - external/third-party: 不检测
  - data/ models/ tmp/ 二进制
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import NamedTuple


# 高 codepoint emoji 主块
EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"      # Regional Indicator (flags)
    "\U0001F300-\U0001F5FF"      # Misc Symbols and Pictographs
    "\U0001F600-\U0001F64F"      # Emoticons
    "\U0001F680-\U0001F6FF"      # Transport and Map
    "\U0001F700-\U0001F77F"      # Alchemical
    "\U0001F780-\U0001F7FF"      # Geometric shapes ext (colored circles)
    "\U0001F800-\U0001F8FF"      # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"      # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"      # Chess
    "\U0001FA70-\U0001FAFF"      # Symbols and Pictographs Ext-A
    "\U0001FB00-\U0001FBFF"      # Symbols for Legacy Computing
    "\U0001F000-\U0001F0FF"      # Mahjong / Cards / Domino
    "]+",
    flags=re.UNICODE,
)

# Compound emoji 字符 (用 VS16 把基本字符变 emoji 风格)
VS16_RE = re.compile("️")
ZWJ_EMOJI_RE = re.compile("‍")

# 特定 emoji-likely 字符 (即使在 U+2700-27BF dingbats, ban 高频用错的)
SPECIFIC_BAN = re.compile(
    "["
    "✅"          # ✅
    "❌"          # ❌
    "✨"          # ✨
    "⭐"          # ⭐
    "⚡"          # ⚡
    "✊-✍"   # 拳头/手 系列
    "❤"          # ❤
    "]"
)

EXEMPT_PATHS = (
    "backend/scripts/check_no_emoji.py",      # 本 file 自己
    "data/",
    "models/",
    "tmp/",
)


class Violation(NamedTuple):
    file: str
    lineno: int
    pattern: str
    line: str


def is_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in EXEMPT_PATHS) or path.endswith((".png", ".jpg", ".duckdb", ".db"))


def get_staged_diff() -> list[tuple[str, list[tuple[int, str]]]]:
    """同 check_rule_compliance.get_staged_diff."""
    r = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return []
    diffs: list[tuple[str, list[tuple[int, str]]]] = []
    current_file: str | None = None
    current_lines: list[tuple[int, str]] = []
    current_lineno = 0
    for raw in r.stdout.splitlines():
        if raw.startswith("+++ b/"):
            if current_file and current_lines:
                diffs.append((current_file, current_lines))
            current_file = raw[6:]
            current_lines = []
        elif raw.startswith("@@"):
            m = re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@", raw)
            if m:
                current_lineno = int(m.group(1))
        elif raw.startswith("+") and not raw.startswith("+++"):
            current_lines.append((current_lineno, raw[1:]))
            current_lineno += 1
        elif raw.startswith(" "):
            current_lineno += 1
    if current_file and current_lines:
        diffs.append((current_file, current_lines))
    return diffs


def main() -> int:
    diffs = get_staged_diff()
    if not diffs:
        return 0

    violations: list[Violation] = []
    for path, lines in diffs:
        if is_exempt(path):
            continue
        for lineno, line in lines:
            for label, pat in (
                ("emoji-block", EMOJI_RE),
                ("vs16-style", VS16_RE),
                ("zwj-emoji", ZWJ_EMOJI_RE),
                ("specific-emoji", SPECIFIC_BAN),
            ):
                if pat.search(line):
                    violations.append(Violation(path, lineno, label, line.strip()))
                    break

    if not violations:
        return 0

    print("=" * 80, file=sys.stderr)
    print(f"ERROR: 发现 {len(violations)} 处 emoji 违规 (feedback_no_emoji)", file=sys.stderr)
    print(file=sys.stderr)
    for v in violations[:20]:
        print(f"  [{v.pattern}] {v.file}:{v.lineno}", file=sys.stderr)
        # 截断显示, 防超长
        snippet = v.line[:120] + ("..." if len(v.line) > 120 else "")
        print(f"    {snippet}", file=sys.stderr)
        print(file=sys.stderr)
    if len(violations) > 20:
        print(f"  ... 另 {len(violations) - 20} 处省略", file=sys.stderr)

    print("修法:", file=sys.stderr)
    print("  1. 删 emoji, 用文字描述 (推荐):", file=sys.stderr)
    print("     ✅ → [PASS] / 完成 / OK", file=sys.stderr)
    print("     ❌ → [FAIL] / 失败 / NG", file=sys.stderr)
    print("     ⚠️ → 警告: (NOTE: ⚠ 单字符 U+26A0 不禁, 仅 ⚠️ 含 VS16 emoji 化禁)", file=sys.stderr)
    print("  2. 误判 (e.g. 项目历史 char) → 改 check_no_emoji.py 的 EXEMPT_PATHS 或 SPECIFIC_BAN", file=sys.stderr)
    print(file=sys.stderr)
    print("根因: memory feedback_no_emoji — 项目全局不用 emoji.", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
