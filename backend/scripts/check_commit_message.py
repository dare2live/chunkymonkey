#!/usr/bin/env python3
"""Pre-commit hook: commit-msg stage — 校验 commit message 含 Rule 9.7 self-check 关键词.

根因: Claude 写 commit message 容易写"fix bug" 这种空说明, 不反映 5-question self-check.

Rule 9.7 要求 commit 前确认:
  1. PROJECT_INDEX.md 同步了吗?
  2. 测试新加了吗?
  3. 数据 / 跑批 commit log 截图 / 数字 写进 commit message 了吗?
  4. CLAUDE.md / Rule 9 反例表加了吗?
  5. Rule 9.1 真金白银 self-check (策略相关 commit)

Hook 检测 commit message body 中是否至少含**任一**关键词组:

GROUP A — 工程类 (任意 commit 都该有):
  - 测试: "test pass" / "测试通过" / "n/n passed"
  - 防回退: "测试 / unit / 单测"
  - 兼容: "fallback" / "向后兼容" / "backward"

GROUP B — 数据/策略类 (涉数据的 commit 该有):
  - PIT: "PIT" / "point-in-time" / "as_of"
  - OOS: "OOS" / "walk-forward" / "expanding"
  - 数据: 行/份 数字 (e.g. "10K 行 / 5,000 行 / 800 day")
  - measured: "实测" / "evidence" / "backtest"

GROUP C — 反 Rule 自检 (出错则强制):
  - "magic" / "拍脑袋" 出现 → 必须有 "evidence" / "yaml" / "measured" / "rule-compliance: ok"
  - "TODO" / "FIXME" / "skip" 出现 → 警告但不 reject

至少触发 GROUP A *任意* + (如果 diff 涉 backend/services/ 则 GROUP B *任意*).
否则 reject + 提示该写什么.

Bypass: 显式 `# commit-msg: minimal` 在 message 体内 (但严格不建议, e.g. revert / 紧急修复).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


GROUP_A_KEYWORDS = (
    # 测试 / 防回退
    "test pass", "测试通过", "passed", "n/n", "防回退",
    "单测", "unit test", "integration test", "回归测试",
    # 兼容性 / 行为
    "fallback", "向后兼容", "backward", "不破坏",
    "compatible", "deprecat",
    # 重构 / 修复 类
    "refactor", "重构", "fix", "修复", "修法",
)

GROUP_B_KEYWORDS = (
    # PIT
    "pit", "point-in-time", "as_of", "asof", "as-of",
    # OOS / walk-forward
    "oos", "walk-forward", "walkforward", "expanding", "rolling",
    # 数字单位 (实测数据)
    " 行", "k row", "k 行", " day", " 天",
    # 实测
    "实测", "evidence", "backtest", "measured", "audit",
    # KPI
    "annual", "年化", "sharpe", "max_dd", "calmar",
)

# GROUP D: post-fix-audit trigger (Rule 9.2 #7, 用户 2026-05-15 push back)
# Subject/body 含这些词 → 必须含 cleanup 证据
POST_FIX_TRIGGER_KEYWORDS = (
    "fix", "修复", "bug", "leakage",
    "cleanup", "清理", "drop", "remove", "revert",
    "kill", "stale", "残留",
)
POST_FIX_PROOF_KEYWORDS = (
    "post-fix-audit", "post-fix audit",
    "cleanup verified", "无残留", "无 stale", "no stale",
    "stale artifact 已清", "cleanup_leakage_data",
    "alter drop column", "delete from",
    "rule 9.2 #7",
)

# Trigger files — 这些路径有改动 → 强制要求 GROUP B 关键词
DATA_STRATEGY_PATH_PREFIXES = (
    "backend/services/",
    "backend/scripts/optimize_",
    "backend/scripts/run_",
    "backend/scripts/build_",
    "backend/scripts/backfill_",
    "backend/scripts/rebuild_",
    "backend/scripts/audit_",
    "backend/config/",
)

EXEMPT_BODY_MARKERS = (
    "# commit-msg: minimal",
    "# commit-msg: ok",
)


def get_staged_files() -> list[str]:
    """commit-msg stage 时已 staged, 用 git diff --cached 查."""
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.strip().split("\n") if f]


def needs_group_b(staged: list[str]) -> bool:
    """是否触发 GROUP B 关键词要求 (数据/策略类 commit)."""
    return any(
        any(f.startswith(p) for p in DATA_STRATEGY_PATH_PREFIXES)
        for f in staged
    )


def main(msg_path: str) -> int:
    msg = Path(msg_path).read_text(encoding="utf-8")
    body = msg.lower()

    # bypass marker
    if any(m in body for m in EXEMPT_BODY_MARKERS):
        return 0

    # ignore comment lines (开头 #) — 这是 git commit template 提示
    body_lines = [
        ln for ln in body.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    body_only = "\n".join(body_lines)

    # 长度 sanity (subject 至少 10 char)
    subject = next((ln for ln in body_lines if ln.strip()), "").strip()
    if len(subject) < 10:
        print("ERROR: commit subject 太短 (<10 char). 写清楚做了什么.", file=sys.stderr)
        return 1

    # GROUP A 必须有
    has_a = any(kw in body_only for kw in GROUP_A_KEYWORDS)

    staged = get_staged_files()
    require_b = needs_group_b(staged)
    has_b = any(kw in body_only for kw in GROUP_B_KEYWORDS) if require_b else True

    # GROUP D: post-fix-audit (Rule 9.2 #7) — 含 fix/leakage/drop/cleanup 必须含 cleanup 证据
    # 2026-06-11: 英文触发词改词边界匹配 — 子串匹配把 fixed_params/prefix/dropdown/killer
    # 等标识符误判为 fix/drop/kill 触发 (实证: fixed_params 误触发 GROUP D)
    import re as _re

    def _kw_hit(kw: str, text: str) -> bool:
        if kw.isascii():
            return _re.search(rf"\b{_re.escape(kw)}\b", text) is not None
        return kw in text

    triggered_d = any(_kw_hit(kw, body_only) for kw in POST_FIX_TRIGGER_KEYWORDS)
    has_d_proof = any(kw in body_only for kw in POST_FIX_PROOF_KEYWORDS) if triggered_d else True

    if has_a and has_b and has_d_proof:
        return 0

    # Reject + 提示
    print("=" * 80, file=sys.stderr)
    print("ERROR: commit message 缺 Rule 9.7 self-check 关键词", file=sys.stderr)
    print(file=sys.stderr)
    if not has_a:
        print("MISSING GROUP A (工程类): 至少一个关键词", file=sys.stderr)
        print(f"  候选: {', '.join(GROUP_A_KEYWORDS[:8])}, ...", file=sys.stderr)
        print("  例: '67/67 测试通过', '加 fallback 防回退'", file=sys.stderr)
        print(file=sys.stderr)
    if require_b and not has_b:
        print("MISSING GROUP B (数据/策略类): 改了 service / script / config → 必须有", file=sys.stderr)
        print(f"  候选: {', '.join(GROUP_B_KEYWORDS[:8])}, ...", file=sys.stderr)
        print("  例: 'PIT 干净', 'OOS sharpe 0.39', '4.8M 行 backfill'", file=sys.stderr)
        print(file=sys.stderr)
    if triggered_d and not has_d_proof:
        triggered_kws = [kw for kw in POST_FIX_TRIGGER_KEYWORDS if kw in body_only]
        print("MISSING GROUP D (post-fix-audit): commit 含 fix/leakage/drop/cleanup 关键词,", file=sys.stderr)
        print(f"  触发: {', '.join(triggered_kws[:5])}", file=sys.stderr)
        print(f"  必须含: {', '.join(POST_FIX_PROOF_KEYWORDS[:6])}", file=sys.stderr)
        print("  例: 'cleanup verified 无残留', 'post-fix-audit 已 5 步走完', 'cleanup_leakage_data 0 row'", file=sys.stderr)
        print("  根因: Rule 9.2 #7 — fix 后必须主动清 stale artifact (DB row / panel cols / model_id / cache).", file=sys.stderr)
        print("        skill: /post-fix-audit 走 5 步.", file=sys.stderr)
        print(file=sys.stderr)
    print("修法 (3 选 1):", file=sys.stderr)
    print("  1. 重写 commit message body, 加 self-check 关键词", file=sys.stderr)
    print("  2. 如果确实是琐碎修改 (e.g. typo / format), 加 '# commit-msg: minimal' 在 body 内", file=sys.stderr)
    print("  3. `--no-verify` 跳 (慎用, 跳所有 hook)", file=sys.stderr)
    print(file=sys.stderr)
    print("根因: Rule 9.7 commit 前 5-question self-check.", file=sys.stderr)
    print("空说明的 commit 下次 session 重读时无法理解为啥这么改 → 文档债.", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 直接调用 (没传 msg path) — 不阻塞, 提示用法
        print("Usage: check_commit_message.py <COMMIT_MSG_FILE>", file=sys.stderr)
        sys.exit(0)
    sys.exit(main(sys.argv[1]))
