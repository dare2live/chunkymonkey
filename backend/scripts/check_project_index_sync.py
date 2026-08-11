#!/usr/bin/env python3
"""Pre-commit hook: 检查 staged 改动是否需要同步更新 PROJECT_INDEX.md / AGENTS.md.

根因 (Rule 9.5 沉淀):
  仅靠人工记忆多次在 commit 阶段遗漏更新 PROJECT_INDEX.md, 导致下次 session 启动
  时项目地图过时, 用户每次都要 push back. Rule 9.5 是被动文字, 没主动触发.

修法 (三层防护):
  1. **Pre-commit hook (硬强制)**: 此脚本 — 改了 service/script/yaml/AGENTS.md
     必须同步改 PROJECT_INDEX.md, 否则 reject commit
  2. **AGENTS.md / engineering governance commit checklist (中)**: self-check
  3. **controller plan 模板 (软)**: 每 phase 结束自动加 "update PROJECT_INDEX" todo

触发条件 (TRIGGERS): 修改这些文件之一 → PROJECT_INDEX.md 必须也在 staged 里
  - backend/services/         新 service / 模块
  - backend/scripts/build_    新 backfill / build 脚本
  - backend/scripts/optimize_ 新 Optuna 脚本
  - backend/scripts/run_      新 entry script
  - backend/scripts/backfill_ 新 backfill
  - backend/config/*.yaml     新 yaml config
  - AGENTS.md                 新 Rule

Bypass (不推荐): git commit --no-verify
"""
from __future__ import annotations

import subprocess
import sys


TRIGGERS = (
    "backend/services/",
    "backend/scripts/build_",
    "backend/scripts/optimize_",
    "backend/scripts/run_",
    "backend/scripts/backfill_",
    "backend/scripts/rebuild_",
    "backend/scripts/audit_",
    "backend/config/",
    "AGENTS.md",
)

# 文件类型例外: 测试 / __pycache__ / __init__ 不要求同步 index
EXEMPT_SUFFIXES = (
    "/__init__.py",
    "/__pycache__/",
)
EXEMPT_PREFIXES = (
    "backend/tests/",            # 单测改动不要求 index 同步
    "backend/services/__pycache__/",
)


def main() -> int:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        # git 不可用或 staged 空 — 不阻塞
        return 0
    staged = [f for f in result.stdout.strip().split("\n") if f]
    if not staged:
        return 0

    # 1. 哪些 staged 文件触发了 PROJECT_INDEX 同步要求?
    triggered_files: list[str] = []
    for f in staged:
        # 跳过例外
        if any(f.startswith(p) for p in EXEMPT_PREFIXES):
            continue
        if any(f.endswith(s) or s in f for s in EXEMPT_SUFFIXES):
            continue
        # 命中触发器?
        if any(f.startswith(t) for t in TRIGGERS):
            triggered_files.append(f)

    if not triggered_files:
        return 0   # 没触发, 通过

    # 2. 是否同时更新了 PROJECT_INDEX.md?
    if "PROJECT_INDEX.md" in staged:
        return 0   # 同步了, 通过

    # 3. Reject + 友好提示
    print("=" * 80, file=sys.stderr)
    print("ERROR: PROJECT_INDEX.md 未同步更新!", file=sys.stderr)
    print(file=sys.stderr)
    print("以下 staged 文件触发了文档同步要求:", file=sys.stderr)
    for f in triggered_files:
        print(f"  {f}", file=sys.stderr)
    print(file=sys.stderr)
    print("修法 (3 选 1):", file=sys.stderr)
    print("  1. 修改 PROJECT_INDEX.md 对应活索引节 (数据资产/模块/yaml/坑); 历史叙事写 commit message, 不进 INDEX changelog,", file=sys.stderr)
    print("     然后 `git add PROJECT_INDEX.md` 再 commit", file=sys.stderr)
    print("  2. 如果确实不需要 (例: 纯 bug fix 不影响结构), `git commit --no-verify` 跳过", file=sys.stderr)
    print("     注意: --no-verify 会绕过所有 hook, 慎用", file=sys.stderr)
    print("  3. 检查是否触发器太宽 — 改 backend/scripts/check_project_index_sync.py", file=sys.stderr)
    print(file=sys.stderr)
    print("根因: AGENTS.md / engineering governance rule 沉淀 — 不维护 PROJECT_INDEX = 下次 session 重新摸索", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
