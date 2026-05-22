#!/usr/bin/env python3
"""Pre-commit hook: 检测 staged diff 里的 Rule 6/5/7 反 pattern.

根因 (用户 push back #N):
  我 (Claude) 即使 CLAUDE.md 写了 "Rule 6 拍脑袋默认是 anti-pattern", 写代码时仍违反 —
  e.g. Phase ψ.β.5 L2 vol-aware 的 sigma=2.0/3.0/1.0 + bounds [-0.20, -0.05] 全是估算.
  Rule 文字是被动的, 没硬护栏.

修法 (跟 PROJECT_INDEX hook 同套路, 技术层硬挡):
  扫 staged diff 找 4 类高风险 pattern, 必须有 `# evidence:` / `# from yaml:` /
  `# measured:` 同行/上一行注释, 否则 reject commit:

  1. Rule 6 magic alpha weight    (alpha 权重 hardcoded)
     例: `weight = 0.15`  →  必须 `# evidence: backtest commit abc1234` 或 `# from yaml: ensemble_alphas`
  2. Rule 6 magic sigma           (sigma 倍数)
     例: `stop_sigma = 2.0`  →  必须 `# measured: optuna study xyz789`
  3. Rule 6 magic multiplier      (regime / boost / threshold multiplier)
     例: `bear_multiplier = 0.3`  →  必须 evidence
  4. Rule 5 try/except: pass      (静默 bypass 反 Rule)
  5. Rule 7/9 hardcoded date      (业务代码硬编码 YYYY-MM-DD)
  6. Rule 7/9 hardcoded stock_code (业务代码硬编码 60xxxx 6 位数字字符串)

Whitelist (跳过检测):
  - yaml 文件本身 (config 就是 yaml-backed)
  - backend/tests/ (测试可能 fix value)
  - backend/config/ (config 就是阈值, 加进 yaml 就是合规)
  - migrations / fixtures / mock data

Bypass: 在违规行同行/上一行加 `# rule-compliance: ok evidence=<source>` 注释.
强烈不建议 `--no-verify`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import NamedTuple


# 文件类型 whitelist: 这些路径不检测
EXEMPT_PATH_PREFIXES = (
    "backend/tests/",
    "backend/config/",       # config 文件本身就是 yaml-backed
    "frontend/",
    "docs/",
    "design/",
    "bestchoice/",            # 2026-05-22 BC migrated from sibling repo, own dev discipline
)
EXEMPT_PATH_SUFFIXES = (
    "/conftest.py",
    "/__init__.py",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".html",
    ".css",
    ".jsx",
    ".tsx",
    ".sql",
)


# Evidence 注释关键词 — 任一出现在违规行的同行或上一行就放行
EVIDENCE_KEYWORDS = (
    "rule-compliance: ok",      # 显式 bypass
    "evidence:",                # backtest 数据 hash
    "measured:",                # 实测数据
    "from yaml:",               # yaml 外置
    "from config:",
    "from optuna",              # Optuna 寻优产物
    "calibrated:",              # 校准
    "@pytest.",                 # 测试 fixture
)


class Violation(NamedTuple):
    file: str
    lineno: int
    rule: str
    pattern: str
    line: str


# Rule 6 — magic numbers in 关键 context. 注意只 match 赋值不 match 比较 (==).
# (?<![\w.]) prefix prevents matching `xxx.weight=0.5` (probably ORM 字段名)
# Note: bounds 检测在赋值右值是 list/tuple 时, 例 `bounds = [0.1, 0.2]`
PATTERNS = (
    # Rule 6: alpha weight (排除 'w' 单字母 — 太泛会误判)
    (
        "Rule 6 magic alpha weight",
        re.compile(r"\b(weight|alpha_weight|alpha_w)\s*=\s*\d+\.\d+"),
    ),
    # Rule 6: sigma 倍数 (匹配任何 \w*sigma\w*)
    (
        "Rule 6 magic sigma multiplier",
        re.compile(r"\b\w*sigma\w*\s*=\s*\d+\.\d+"),
    ),
    # Rule 6: regime / boost / mode multiplier
    (
        "Rule 6 magic multiplier",
        re.compile(r"\b\w*multiplier\s*=\s*\d+\.\d+"),
    ),
    # Rule 6: threshold (但 if x > 0.5: 也会触发 — 限制为赋值)
    (
        "Rule 6 magic threshold",
        re.compile(r"\b\w*_?threshold\s*=\s*\d+\.\d+"),
    ),
    # Rule 5: try/except: pass — 静默 bypass
    (
        "Rule 5 silent except pass",
        re.compile(r"^\s*except\s*[\w.,\s]*:\s*$"),  # 需结合下一行 pass 检测
    ),
    # Rule 7: hardcoded YYYY-MM-DD 在业务代码 (services/ scripts/)
    (
        "Rule 7 hardcoded date",
        re.compile(r"[\"']\d{4}-\d{2}-\d{2}[\"']"),
    ),
    # Rule 7: hardcoded 6 位股票代码字符串 in service code (60xxxx/00xxxx/30xxxx)
    (
        "Rule 7 hardcoded stock_code",
        re.compile(r"[\"'](6\d{5}|0\d{5}|3\d{5})[\"']"),
    ),
)


def is_exempt(path: str) -> bool:
    if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
        return True
    if any(path.endswith(s) for s in EXEMPT_PATH_SUFFIXES):
        return True
    return False


def has_evidence(curr_line: str, prev_line: str) -> bool:
    """检查违规行的同行 # 注释 或 上一行 (仅当上一行是纯注释行) 是否有 evidence 关键词."""
    # 1. 同行 # 注释
    if "#" in curr_line:
        comment = curr_line.split("#", 1)[1].lower()
        if any(kw in comment for kw in EVIDENCE_KEYWORDS):
            return True
    # 2. 上一行必须是 *纯注释行* (以 # 开头) 才算作 evidence
    #    防止上一行业务代码的 # 注释 误豁免下一行
    prev_stripped = prev_line.strip()
    if prev_stripped.startswith("#"):
        prev_comment = prev_stripped[1:].lower()
        if any(kw in prev_comment for kw in EVIDENCE_KEYWORDS):
            return True
    return False


def get_staged_diff() -> list[tuple[str, list[tuple[int, str]]]]:
    """返回 [(path, [(lineno, line), ...]), ...] — staged 新增行 (+ prefix)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return []
    diffs: list[tuple[str, list[tuple[int, str]]]] = []
    current_file: str | None = None
    current_lines: list[tuple[int, str]] = []
    current_lineno = 0
    for raw in result.stdout.splitlines():
        if raw.startswith("+++ b/"):
            if current_file and current_lines:
                diffs.append((current_file, current_lines))
            current_file = raw[6:]
            current_lines = []
        elif raw.startswith("@@"):
            # @@ -old,oldlen +new,newlen @@
            m = re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@", raw)
            if m:
                current_lineno = int(m.group(1))
        elif raw.startswith("+") and not raw.startswith("+++"):
            current_lines.append((current_lineno, raw[1:]))
            current_lineno += 1
        elif raw.startswith(" "):
            current_lineno += 1
        # "-" 行 / "---" header 不影响新 lineno
    if current_file and current_lines:
        diffs.append((current_file, current_lines))
    return diffs


def _is_in_docstring_or_comment(line: str, in_docstring: bool) -> tuple[bool, bool]:
    """返回 (是否应跳过本行, 处理本行后的 in_docstring 状态).

    简化判断 (不处理 raw / b / f string 三引号):
    - 行 strip 后以 # 开头 → 纯注释行 → 跳过 (不更新 docstring 状态)
    - 行包含 \"\"\" → 切换 in_docstring 状态. 同一行里 2 个 \"\"\" 视为单行 docstring (整行跳过)
    - 当前 in_docstring=True → 跳过本行
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        return True, in_docstring
    triple_count = line.count('"""') + line.count("'''")
    if triple_count == 0:
        return in_docstring, in_docstring
    if triple_count >= 2:
        # 单行 docstring 起止 — 整行跳过, in_docstring 状态不变
        return True, in_docstring
    # triple_count == 1 → 切换状态. 跳过本行 (起始或结束行)
    new_state = not in_docstring
    return True, new_state


def main() -> int:
    diffs = get_staged_diff()
    if not diffs:
        return 0

    violations: list[Violation] = []
    for path, lines in diffs:
        if is_exempt(path):
            continue
        # 只检 Python 文件 (业务代码主体)
        if not path.endswith(".py"):
            continue
        # 用整文件 (不仅 staged) 跟踪 docstring 状态 — 但 staged diff 只给 added lines,
        # 没有完整 context. 我们做 best-effort: 用 staged 行本身判断, 进入/退出 docstring
        # 在同一个 hunk 时正确; 跨 hunk 可能误判, 但 false-positive 风险低 (反正 evidence 注释豁免可救).
        in_docstring = False
        for i, (lineno, line) in enumerate(lines):
            skip, in_docstring = _is_in_docstring_or_comment(line, in_docstring)
            if skip:
                continue
            prev_line = lines[i - 1][1] if i > 0 else ""
            for rule_name, pat in PATTERNS:
                if not pat.search(line):
                    continue
                # 特殊处理 Rule 5: except 单独行 → 看后续是不是 pass
                if "silent except" in rule_name:
                    if i + 1 < len(lines) and lines[i + 1][1].strip().startswith("pass"):
                        violations.append(Violation(path, lineno, rule_name, pat.pattern, line.strip()))
                    continue
                # 注释豁免
                if has_evidence(line, prev_line):
                    continue
                violations.append(Violation(path, lineno, rule_name, pat.pattern, line.strip()))

    if not violations:
        return 0

    print("=" * 80, file=sys.stderr)
    print(f"ERROR: 发现 {len(violations)} 个 Rule 违规 (Rule 6/5/7 反 pattern):", file=sys.stderr)
    print(file=sys.stderr)
    for v in violations:
        print(f"  [{v.rule}]", file=sys.stderr)
        print(f"    {v.file}:{v.lineno}", file=sys.stderr)
        print(f"    {v.line}", file=sys.stderr)
        print(file=sys.stderr)
    print("修法 (3 选 1):", file=sys.stderr)
    print("  1. 把 magic number 外置到 yaml (推荐, Rule 6 干净):", file=sys.stderr)
    print("     - 数值进 backend/config/*.yaml", file=sys.stderr)
    print("     - 业务代码读 yaml, 不 hardcode", file=sys.stderr)
    print("  2. 加 evidence 注释 (业务代码同行/上一行):", file=sys.stderr)
    print("     - `# evidence: backtest commit <hash>` (有 Optuna / 实测 数据支撑)", file=sys.stderr)
    print("     - `# measured: optuna study <id>` (寻优产物)", file=sys.stderr)
    print("     - `# from yaml: <section>` (yaml 兜底默认)", file=sys.stderr)
    print("     - `# rule-compliance: ok evidence=<source>` (显式 bypass, 慎用)", file=sys.stderr)
    print("  3. 如果误判 (e.g. enum 值 / range 参数 / unit test fixture):", file=sys.stderr)
    print("     改 backend/scripts/check_rule_compliance.py 的 PATTERNS / EVIDENCE_KEYWORDS", file=sys.stderr)
    print(file=sys.stderr)
    print("根因: CLAUDE.md Rule 6 (Measured not Estimated) — 任何参数/阈值/权重必须有 backtest 证据.", file=sys.stderr)
    print("拍脑袋默认是 anti-pattern (反例见 Rule 6 表).", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
