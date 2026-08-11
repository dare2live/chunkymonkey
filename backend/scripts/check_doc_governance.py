"""文档治理执法器 — 新旧文档混用的机械防线 (2026-06-12, 用户点名建机制).

默认语义 (docs/README.md 状态标头契约节为 owner):
  - 控制面 (goal.md / AGENTS.md / PROJECT_INDEX.md / docs/*.md) = live
  - CLAUDE.md 是 legacy Claude artifact, 不属于 Codex 活控制面
  - FEATURE_MAP.md = generated projection: 参与引用/CLI 漂移检查, 不计人类 active docs 数量
  - analysis/project_state_ledger.md = 唯一 query-only 历史索引
  - 其余 analysis/*.md = evidence-only，前 10 行必须显式声明，禁止成为 live/self owner

检查项 (FAIL 退出码 1):
  C1 goal.md 行数 <= 上限 (薄入口契约)
  C2 docs/*.md 必须精确匹配四文件 authority allowlist
  C3 非 ledger 的 analysis/*.md 必须是 evidence-only，且不得声明 live/self-owner/待主会话
  C4 控制面文档引用的 analysis/* 必须存在 (防幽灵引用误导)
  C5 superseded-by 指向的文件必须存在 (防断链)
  C6 控制面文档引用 retired/superseded 文件 → WARN (叙述历史合法, 当 owner 引用须人审)
  C7 控制面文档引用不存在的脚本命令 → WARN
  C8 控制面文档引用 chunkyctl 自身注册为 retired 的子命令 → FAIL
  C9 BestChoice 必须精确等于冻结证据包，不得复活独立 goal/agent/handoff/runtime

PASS 的完整契约是 fails=0 且 warns=0。WARN 会显示 verdict=WARN 并返回非零，不能冒充绿门。

用法: PYTHONPATH=backend python backend/scripts/check_doc_governance.py [--root <repo>]
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

GOAL_MAX_LINES = 132  # 当前 compact controller board 121 行，保留约 10% 变更余量
DOCS_ALLOWLIST = {
    "README.md",
    "MASTER_TOPLEVEL_DESIGN.md",
    "engineering_governance.md",
    "strategy_validation_contract.md",
}
BESTCHOICE_ALLOWLIST = {
    ".gitignore",
    "FROZEN.md",
    "README.md",
    "analysis/formula_local_optuna_batch_adoption.csv",
    "analysis/stock_formula_best.csv",
    "evidence_manifest.json",
    "execution_model.py",
    "formula_engine.py",
    "scripts/execution_model_smoke.py",
    "scripts/formula_engine_smoke.py",
    "scripts/verify_frozen_evidence.py",
}
_HEADER_SCAN_LINES = 10
_STATUS_RE = re.compile(r">\s*状态\s*[:：]\s*(live|retired|superseded-by\s*[:：]?\s*(\S+))", re.I)
_EVIDENCE_ONLY_RE = re.compile(r"\bevidence[-_ ]only\b", re.I)
_LIVE_STATUS_RE = re.compile(r">\s*状态\s*[:：]\s*live\b", re.I)
_SELF_OWNER_RE = re.compile(
    r"\bself[-_ ]?owner\b|\bowner\s*[:=]\s*self\b|本文\s*(?:是|为|拥有)\s*(?:当前|现行)?\s*owner",
    re.I,
)
_PENDING_MAIN_SESSION_RE = re.compile(r"待(?:当前)?主会话")
_ANALYSIS_REF_RE = re.compile(r"(?<![\w/])analysis/[\w\-./]+\.(?:md|py|json|yaml)")  # 负向回顾: 不匹配绝对路径/跨仓路径中段
_SCRIPT_REF_RE = re.compile(
    r"\b((?:audit|build|check|probe|run|backfill|modal|seed|update|optimize|refresh|generate|gen|migrate)_[\w]+\.py)\b"
)  # C6: 常见命令式脚本名；全路径引用另由 check_doc_drift 覆盖
# 同一份文档里若已用**存在的全路径**引用了某个文件，那它就不是「悬空的命令名」——
# 该维度归 check_doc_drift（本正则的注释一直这么写，但实现漏了这一步）。
# 反例 (2026-08-11 实测): MASTER §5.4 写 `backend/services/pipeline/run_outcome.py`，
# 前缀 `run_` 命中命令名启发式，而它是 service 模块不在 scripts/ 下 → 假 WARN。
_FULL_PATH_REF_RE = re.compile(r"(?<![\w/])((?:backend|frontend|scripts)/[\w\-./]+\.py)\b")
_CHUNKYCTL_COMMAND_RE = re.compile(r"\b(?:scripts/)?chunkyctl\s+([a-z][\w-]*)\b")
_FEATURE_MAP_COMMAND_RE = re.compile(r"^\|\s*`([a-z][\w-]*)`\s*\|")
# BOARD.md 于 2026-08-11 P2.3 退役 (L2 状态改现查, 零文件)。
_GENERATED_DOCS = ("FEATURE_MAP.md",)
_ROOT_ACTIVE_DOCS = ("AGENTS.md", "goal.md", "PROJECT_INDEX.md")


def active_doc_paths(root: Path) -> list[Path]:
    """Return current policy/contract docs only; dated ``analysis/`` stays evidence."""
    paths = [root / name for name in _ROOT_ACTIVE_DOCS if (root / name).exists()]
    docs_dir = root / "docs"
    if docs_dir.exists():
        paths.extend(sorted(docs_dir.glob("*.md")))
    return paths


def governed_doc_paths(root: Path) -> list[Path]:
    """Human-owned live docs plus generated projections checked for drift."""
    paths = active_doc_paths(root)
    paths.extend(root / name for name in _GENERATED_DOCS if (root / name).exists())
    return paths


def _feature_map_commands(text: str) -> set[str]:
    """Read the generated chunkyctl command table, not unrelated map tables."""
    commands: set[str] = set()
    in_chunkyctl_section = False
    for line in text.splitlines():
        if line.strip() == "### chunkyctl 子命令":
            in_chunkyctl_section = True
            continue
        if in_chunkyctl_section and line.startswith("### "):
            break
        if in_chunkyctl_section:
            match = _FEATURE_MAP_COMMAND_RE.match(line)
            if match:
                commands.add(match.group(1))
    return commands


def _retired_chunkyctl_commands(root: Path) -> set[str]:
    """Read the CLI's own ``_RETIRED`` registry without importing or executing it."""
    cli = root / "backend" / "scripts" / "chunkyctl.py"
    try:
        tree = ast.parse(cli.read_text(encoding="utf-8"), filename=str(cli))
    except (OSError, SyntaxError):
        return set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "_RETIRED" for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return set()
        return {str(item) for item in value}
    return set()


def _status_of(path: Path) -> tuple[str, str | None]:
    """返回 (状态, superseded 目标). 无标头 = 目录默认语义."""
    try:
        head = "\n".join(path.read_text(encoding="utf-8", errors="ignore").splitlines()[:_HEADER_SCAN_LINES])
    except OSError:
        return ("unreadable", None)
    m = _STATUS_RE.search(head)
    if m:
        if m.group(1).lower().startswith("superseded"):
            return ("superseded", m.group(2))
        return (m.group(1).lower(), None)
    return ("default", None)


def _analysis_header_failure(path: Path, root: Path) -> str | None:
    if path.name == "project_state_ledger.md":
        return None
    rel = path.relative_to(root).as_posix()
    try:
        head = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore").splitlines()[:_HEADER_SCAN_LINES]
        )
    except OSError as exc:
        return f"C3 {rel} header 不可读: {exc}"
    violations: list[str] = []
    if not _EVIDENCE_ONLY_RE.search(head):
        violations.append("缺 evidence-only 声明")
    if _LIVE_STATUS_RE.search(head):
        violations.append("声明 live")
    if _SELF_OWNER_RE.search(head):
        violations.append("声明 self-owner")
    if _PENDING_MAIN_SESSION_RE.search(head):
        violations.append("声明待主会话，形成悬空第二真相源")
    if not violations:
        return None
    return f"C3 {rel} analysis 只能是 evidence-only: {', '.join(violations)}"


def run(root: Path) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []

    goal = root / "goal.md"
    if goal.exists():
        n = len(goal.read_text(encoding="utf-8", errors="ignore").splitlines())
        if n > GOAL_MAX_LINES:
            fails.append(f"C1 goal.md {n} 行 > {GOAL_MAX_LINES} (薄入口契约破裂 — 完成项移 ledger)")

    docs_dir = root / "docs"
    if docs_dir.exists():
        actual_docs = {path.name for path in docs_dir.glob("*.md")}
        extra_docs = sorted(actual_docs - DOCS_ALLOWLIST)
        missing_docs = sorted(DOCS_ALLOWLIST - actual_docs)
        if extra_docs or missing_docs:
            fails.append(
                "C2 docs/ authority allowlist 漂移 "
                f"extra={extra_docs} missing={missing_docs} (合并进四个 owner，不新增平行活文档)"
            )

    bestchoice_dir = root / "bestchoice"
    if bestchoice_dir.exists():
        actual_bestchoice = {
            path.relative_to(bestchoice_dir).as_posix()
            for path in bestchoice_dir.rglob("*")
            if path.is_file()
        }
        extra_bestchoice = sorted(actual_bestchoice - BESTCHOICE_ALLOWLIST)
        missing_bestchoice = sorted(BESTCHOICE_ALLOWLIST - actual_bestchoice)
        if extra_bestchoice or missing_bestchoice:
            fails.append(
                "C9 BestChoice 冻结证据包漂移 "
                f"extra={extra_bestchoice} missing={missing_bestchoice} "
                "(禁止第二 control plane/runtime；变更 challenger 必须重发 manifest)"
            )

    control_plane = governed_doc_paths(root)
    retired_commands = _retired_chunkyctl_commands(root)

    retired_like: set[str] = set()
    for f in (root / "analysis").glob("*.md") if (root / "analysis").exists() else []:
        header_failure = _analysis_header_failure(f, root)
        if header_failure:
            fails.append(header_failure)
        status, target = _status_of(f)
        rel = f.relative_to(root).as_posix()
        if status == "superseded":
            retired_like.add(rel)
            if target and not (root / target.strip("`")).exists():
                fails.append(f"C5 {rel} 的 superseded-by 目标不存在: {target}")
        elif status == "retired":
            retired_like.add(rel)

    # C6: 控制面 doc 不引用不存在的脚本命令 (2026-06-16 补盲区: reset 删 audit_docs_graph 等致 doc→脚本悬空累积 17 处无门拦;
    #     原 C3 只查 analysis/, 不查 backend/scripts 命令名)。脚本命名约定前缀 = 命令引用, 在 backend/scripts 或 scripts/ 必存在。
    script_dirs = [root / "backend" / "scripts", root / "scripts"]
    for doc in control_plane:
        text = doc.read_text(encoding="utf-8", errors="ignore")
        rel_doc = doc.relative_to(root).as_posix()
        for ref in set(_ANALYSIS_REF_RE.findall(text)):
            if not (root / ref).exists():
                fails.append(f"C4 {rel_doc} 引用不存在的 {ref} (幽灵引用)")
            elif ref in retired_like:
                warns.append(f"C6 {rel_doc} 引用已退役/被取代的 {ref} — 历史叙述合法, 当 owner 引用须改指现行文件")
        # 豁免必须**按行**判定 (2026-08-11 独立审查 finding #1): 按整份文档收集
        # basename 会让「同名但确实悬空的裸命令」被另一处无关的合法全路径顺带放行 ——
        # 实测反例: 文档里既写 `backend/services/foo/audit_thing.py` (存在), 又另起一行
        # 写 `audit_thing.py --check` (scripts/ 下不存在), 结果零 WARN。
        # 归属关系只在同一行成立: 全路径与它的裸名指的是同一个东西。
        for lineno, line in enumerate(text.splitlines(), 1):
            covered_on_this_line = {
                path.rsplit("/", 1)[-1]
                for path in set(_FULL_PATH_REF_RE.findall(line))
                if (root / path).is_file()
            }
            for ref in set(_SCRIPT_REF_RE.findall(line)):
                if ref in covered_on_this_line:
                    continue  # 同行的全路径且真实存在 → 交给 check_doc_drift
                if not any((d / ref).exists() for d in script_dirs):
                    # WARN 级 (2026-06-16 立, 暂不 FAIL): 先暴露 reset 删脚本致 doc→命令悬空
                    # backlog (25+ 处), doc 清理收口后翻 FAIL 守门。
                    warns.append(
                        f"C7 {rel_doc}:{lineno} 引用不存在脚本命令 {ref} "
                        "(reset 删/改名后悬空; 清理 backlog, 收口后翻 FAIL)"
                    )
        referenced_commands = set(_CHUNKYCTL_COMMAND_RE.findall(text))
        if rel_doc == "FEATURE_MAP.md":
            referenced_commands.update(_feature_map_commands(text))
        for command in referenced_commands & retired_commands:
            fails.append(f"C8 {rel_doc} 引用退役 CLI: chunkyctl {command} (改指当前可执行入口)")

    return fails, warns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)
    fails, warns = run(Path(args.root))
    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")
    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    print(f"doc-governance verdict={verdict} fails={len(fails)} warns={len(warns)}")
    return 1 if (fails or warns) else 0


if __name__ == "__main__":
    sys.exit(main())
