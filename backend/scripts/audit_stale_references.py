"""陈旧引用审计 (Stale Reference Audit, SRA).

目的: 系统性发现"退役但代码里还在引用"的定时炸弹.

CLAUDE.md 规则 #11/#12 是从这个问题催生的:
  sqlite3 已退役但 27 个测试还在用 → 隐藏了 ANY_VALUE / information_schema 等
  DuckDB-only 特性的 bug, 测试通过, 生产炸.

本脚本用 5 层检测找类似问题, 不限于 sqlite3:

  Tier 1 — 标记扫描 (Marker scan)
    源文件/文档里 grep "退役|已退役|deprecated|废弃|removed at|legacy|TODO.*remove|
    TODO.*retire|替代|supersede" 等关键词. 每个命中提取上下文, 推出 "什么退役了 +
    什么替代它".

  Tier 2 — 反向引用 (Reverse reference)
    对 Tier 1 找到的每个退役 item, 全仓库 grep 看还有哪些活引用. 比较位置 vs
    类型 (代码 / 测试 / 注释 / 配置).

  Tier 3 — 模式扫描 (Pattern scan)
    硬编码的退役模式: 已下架的 import (mootdx / sqlite3 in tests), 已退役的
    URL / 表名 / API 路径, 旧别名.

  Tier 4 — 测试-生产配对 (Test/Prod parity)
    生产用 X, 测试用 Y. 比如生产 DuckDB, 测试 sqlite3.

  Tier 5 — Schema orphan
    DDL 创建的表但全仓没 SELECT/INSERT/UPDATE 引用; 删除候选.

输出: 控制台彩色摘要 + JSON 报告写到 /tmp/stale_audit.json.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import argparse
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
ROOT = REPO.parent
BACKEND = REPO / "backend"
PROJECT_ROOTS = {
    "chunky-monkey-v2": REPO,
    "tdxhub": ROOT / "tdxhub",
    "miaoxiang": ROOT / "miaoxiang",
}

# ─────────────────────────────────────────────────────────────────────
# Tier 1 — markers
# ─────────────────────────────────────────────────────────────────────

# 中文 + 英文 retirement 标记. 用 \b 不可靠 (中文无词边界), 直接子串匹配.
MARKER_PATTERNS = [
    (re.compile(r"已?退役"), "RETIRED"),
    (re.compile(r"已?废弃"), "DEPRECATED"),
    (re.compile(r"deprecat(?:ed|e|ion)", re.IGNORECASE), "DEPRECATED"),
    (re.compile(r"removed[_ ]at"), "REMOVED_MARKER"),
    (re.compile(r"replaced?[_ ]by"), "SUPERSEDED"),
    (re.compile(r"supersede(?:s|d)?", re.IGNORECASE), "SUPERSEDED"),
    (re.compile(r"替代|被替代|改用"), "SUPERSEDED"),
    (re.compile(r"TODO.*?(?:remove|delete|retire|drop)", re.IGNORECASE), "TODO_REMOVE"),
    (re.compile(r"FIXME.*?(?:old|legacy|stale)", re.IGNORECASE), "FIXME_OLD"),
    (re.compile(r"legacy[_ -]"), "LEGACY"),
    (re.compile(r"已下架"), "RETIRED"),
    (re.compile(r"P\d+\s*(?:迁|起|起退役|后)\b"), "PHASE_MIGRATION"),
]

# Tier 3 — 已知退役清单 (项目内显式退役过的东西).
# 此处条目应随 git 记录 + CLAUDE.md 同步更新.
KNOWN_RETIRED = [
    {
        "name": "mootdx",
        "kind": "package",
        "replaced_by": "tdxhub",
        "search": [r"\bimport\s+mootdx\b", r"\bfrom\s+mootdx\b"],
    },
    {
        "name": "sqlite3 (in backend/)",
        "kind": "library",
        "replaced_by": "DuckDB / services.duck_adapter",
        # 测试可以转 DuckDB, 但生产代码完全禁
        "search": [r"^\s*import\s+sqlite3\b", r"^\s*from\s+sqlite3\b"],
        "exclude_paths": ["backend/tests/conftest.py"],  # 仅注释提到
    },
    {
        "name": "market_raw_holdings",
        "kind": "db_table",
        "replaced_by": "fact_top10_holder_period",
        "search": [r"\bmarket_raw_holdings\b"],
        # 注释里的退役说明允许; 真 SQL 引用即定时炸弹.
    },
    # P12 (2026-04-28): RPT_F10_EH_FREEHOLDERS 不是完全退役, 而是从 primary
    # 降级为 tier-2 fallback (HolderResolver 兜底; 99.6% 不会触发).
    # 因此不在 KNOWN_RETIRED 里追踪 — 真的有 stale 引用应该手工 grep.
    {
        "name": "top_free_holders",
        "kind": "capability",
        "replaced_by": "holders_top10_float",
        "search": [r'"top_free_holders"', r"'top_free_holders'"],
    },
    # datacenter-web 是 eastmoney 的后端域名, akshare/miaoxiang 都用,
    # 不是项目自身 artifact, 不在 retire 范畴.
    {
        "name": "dim_stock (deprecated 2026-04-08)",
        "kind": "db_table",
        "replaced_by": "dim_active_a_stock",
        "search": [r"\bdim_stock\b(?!\w)"],  # not dim_stock_xxx
        "exclude_paths": [],
    },
    {
        "name": "dim_stock_industry (deprecated Phase 2)",
        "kind": "db_table",
        "replaced_by": "dim_stock_tdx_industry",
        "search": [r"\bdim_stock_industry\b"],
    },
    {
        "name": "fact_institution_event_industry_snapshot (retired Phase 3b-3)",
        "kind": "db_table",
        "replaced_by": "merged into fact_institution_event",
        "search": [r"\bfact_institution_event_industry_snapshot\b"],
    },
]


# ─────────────────────────────────────────────────────────────────────
# data structures
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Hit:
    file: str
    line: int
    text: str
    kind: str = "unknown"  # 'code' | 'comment' | 'docstring' | 'test'


@dataclass
class Finding:
    name: str
    kind: str
    replaced_by: Optional[str]
    hits: list[Hit] = field(default_factory=list)
    severity: str = "info"  # info / warn / critical

    def is_clean(self) -> bool:
        return all(h.kind in ("comment", "docstring") for h in self.hits)


@dataclass
class TechStackHit:
    project: str
    category: str
    file: str
    line: int
    marker: str
    text: str


TECH_STACK_PATTERNS = [
    # pandas denylist
    ("pandas", "pandas", re.compile(r"\bpandas\b")),
    ("pandas", "pd.", re.compile(r"\bpd\.")),
    ("pandas", "DataFrame", re.compile(r"\bDataFrame\b")),
    ("pandas", "read_sql_query", re.compile(r"\bread_sql_query\b")),
    ("pandas", ".to_sql(", re.compile(r"\.to_sql\s*\(")),
    ("pandas", ".df()", re.compile(r"\.df\s*\(")),
    # SQLite denylist
    ("sqlite", "sqlite", re.compile(r"\bsqlite\b", re.IGNORECASE)),
    ("sqlite", "sqlite3", re.compile(r"\bsqlite3\b")),
    ("sqlite", "sqlite_master", re.compile(r"\bsqlite_master\b")),
    ("sqlite", "AUTOINCREMENT", re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)),
    ("sqlite", "BEGIN IMMEDIATE", re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE)),
    ("sqlite", "row_factory", re.compile(r"\brow_factory\b")),
    ("sqlite", "PRAGMA", re.compile(r"\bPRAGMA\s+(?:table_info|foreign_keys|journal_mode|synchronous|cache_size|wal_checkpoint)\b", re.IGNORECASE)),
    ("old_db_path", "smartmoney.db", re.compile(r"\bsmartmoney\.db\b")),
    ("old_db_path", "market_data.db", re.compile(r"\bmarket_data\.db\b")),
    ("old_db_path", "etf.db", re.compile(r"\betf\.db\b")),
    ("old_db_path", ".sqlite", re.compile(r"\.sqlite\b", re.IGNORECASE)),
    # Source/link drift candidates. These are review queues, not automatic failures.
    ("old_source_route", "datacenter-web", re.compile(r"datacenter[-_]web", re.IGNORECASE)),
    ("old_source_route", "top_free_holders", re.compile(r"\btop_free_holders\b")),
    ("old_external_link", "github branch URL", re.compile(r"https?://[^\\s'\"<>]+/(?:tree|blob)/(?:master|main)\b")),
    # Allowed current fact bucket.
    ("duckdb_allowed", "duckdb", re.compile(r"\bduckdb\b", re.IGNORECASE)),
]

PHASE0_EXCLUDE_PARTS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "data", "logs", "mlruns", ".venv", "venv", "dist", "build",
}


# ─────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────

_RETIREMENT_NOTE_HINTS = (
    # 中文
    "替代", "退役", "废弃", "下架", "已删除", "迁移到", "迁去", "已迁",
    "改用", "原 ", "曾因", "Phase ", "后者会在", "由 ", "由miaoxiang",
    "迁:", "迁 :", "→", "之前", "之后", "兜底", "过渡", "不走", "不是独立",
    "历史", "不带", "曾用", "前身",
    # 英文
    " supersede", " replaced", " replace by", " removed at", " deprecated",
    "legacy", "TODO", "FIXME", "no longer", "switched to", "retired",
    "migrated", "migration", "transitional",
)

# 阶段标记: P3, P5, P6.1, P7 等
_PHASE_RE = re.compile(r"\bP\d+(?:\.\d+)?\b")
# 注释中的内嵌 # 注释 (代码尾注释)
_INLINE_HASH_RE = re.compile(r"(?:^|[^'\"])\s+#\s+\S")


def is_comment_or_docstring(line: str) -> bool:
    """Heuristic: 看该行是否更像注释/docstring/迁移说明 而不是活引用代码."""
    stripped = line.lstrip()
    if not stripped:
        return False
    # 显式行注释 / docstring 起始
    if stripped.startswith(("#", "//", "--", "'''", '"""')):
        return True
    # 含 retirement hint 关键词 → 极大概率是说明文字
    for hint in _RETIREMENT_NOTE_HINTS:
        if hint in stripped:
            return True
    # 行内 # comment (Python inline comment with retirement context)
    if _INLINE_HASH_RE.search(line) and _PHASE_RE.search(line):
        return True
    # P\d+ 阶段标记通常出现在迁移说明里
    if _PHASE_RE.search(stripped) and ("迁" in stripped or ":" in stripped or "起" in stripped):
        return True
    return False


def classify_file(path: Path) -> str:
    rel = str(path.relative_to(REPO))
    if "/tests/" in rel or rel.endswith("_test.py"):
        return "test"
    if rel.endswith((".md", ".txt", ".rst")):
        return "doc"
    if rel.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".sql", ".yml", ".yaml", ".json")):
        return "code"
    return "other"


def scan_files() -> list[Path]:
    out = []
    for ext in ("py", "md", "sql", "yaml", "yml", "json"):
        out.extend(BACKEND.rglob(f"*.{ext}"))
    out.extend((REPO / "scripts").rglob("*.py")) if (REPO / "scripts").exists() else None
    out.extend((REPO / "docs").rglob("*.md")) if (REPO / "docs").exists() else None
    if (REPO / "CLAUDE.md").exists():
        out.append(REPO / "CLAUDE.md")
    # 排除 __pycache__, .pytest_cache, data/, mlruns/
    return [p for p in out if not any(s in str(p) for s in (
        "__pycache__", ".pytest_cache", "/data/", "/mlruns/", ".git/"))]


def _phase0_skip(path: Path) -> bool:
    parts = set(path.parts)
    if parts & PHASE0_EXCLUDE_PARTS:
        return True
    if path.name in {".DS_Store"}:
        return True
    try:
        rel_repo = str(path.relative_to(REPO))
        if rel_repo in SELF_EXCLUDE_PATHS:
            return True
        if rel_repo.startswith("docs/audits/"):
            return True
    except ValueError:
        pass
    return False


def phase0_scan_files() -> list[tuple[str, Path]]:
    """Files scanned by the cross-project technical-stack audit."""

    allowed_ext = {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
        ".md", ".rst", ".txt", ".sql", ".yaml", ".yml", ".json",
        ".toml", ".lock", ".cfg", ".ini", ".sh", ".command",
    }
    out: list[tuple[str, Path]] = []
    for project, root in PROJECT_ROOTS.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or _phase0_skip(path):
                continue
            if path.suffix not in allowed_ext and path.name not in {"requirements.txt", "Dockerfile", "Makefile"}:
                continue
            out.append((project, path))
    return out


def _kind_for_phase0(path: Path, project_root: Path) -> str:
    rel = str(path.relative_to(project_root))
    if "/tests/" in f"/{rel}" or rel.startswith("tests/") or rel.endswith("_test.py"):
        return "test"
    if path.suffix.lower() in {".md", ".rst", ".txt"}:
        return "docs"
    return "runtime"


def _phase0_category(group: str, kind: str) -> str:
    if group in {"pandas", "sqlite"}:
        return f"{group}_{kind}"
    return group


def phase0_stack_scan() -> dict:
    """Plan Phase 0 scan: pandas/SQLite/old path/source/link baseline across three repos."""

    hits: list[TechStackHit] = []
    scanned = phase0_scan_files()
    for project, path in scanned:
        root = PROJECT_ROOTS[project]
        kind = _kind_for_phase0(path, root)
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        rel = str(path.relative_to(ROOT))
        for line_no, line in enumerate(lines, 1):
            for group, marker, pattern in TECH_STACK_PATTERNS:
                if not pattern.search(line):
                    continue
                category = _phase0_category(group, kind)
                hits.append(TechStackHit(
                    project=project,
                    category=category,
                    file=rel,
                    line=line_no,
                    marker=marker,
                    text=line.strip()[:180],
                ))

    summary: dict[str, int] = defaultdict(int)
    by_project: dict[str, int] = defaultdict(int)
    for hit in hits:
        summary[hit.category] += 1
        by_project[hit.project] += 1
    for required in (
        "pandas_runtime", "pandas_test", "pandas_docs",
        "sqlite_runtime", "sqlite_test", "sqlite_docs",
        "old_db_path", "old_source_route", "old_external_link",
        "duckdb_allowed",
    ):
        summary.setdefault(required, 0)

    return {
        "project_roots": {k: str(v) for k, v in PROJECT_ROOTS.items() if v.exists()},
        "scanned_files": len(scanned),
        "summary": dict(sorted(summary.items())),
        "by_project": dict(sorted(by_project.items())),
        "hits": [asdict(h) for h in hits],
    }


# 自审计脚本本身需排除 (它包含所有退役 item 名作为 registry 数据).
SELF_EXCLUDE_PATHS = (
    "backend/scripts/audit_stale_references.py",
)

# Registry files whose purpose is to name retired assets and their replacements.
# Literal retired table names here are retirement metadata, not live references.
RETIREMENT_METADATA_PATHS = (
    "backend/scripts/mark_deprecated_data_assets.py",
)

# 合法的退役 DDL/SQL 模式: DROP TABLE 删旧表, ALTER TABLE 改造, 等.
# 这些模式与 "RETIRED" 是配合关系, 不是定时炸弹.
_LEGITIMATE_RETIREMENT_PATTERNS = (
    re.compile(r"DROP\s+TABLE\s+IF\s+EXISTS\s+\w+", re.IGNORECASE),
    re.compile(r"DROP\s+INDEX\s+IF\s+EXISTS\s+\w+", re.IGNORECASE),
    # 反陈旧测试 (检查代码里是否引用了已退役的东西): 模式本身就是 audit
    re.compile(r"direct access to retired"),
    re.compile(r"\\b\b"),  # regex 字符串里的 \b\b 正则 — 测试或 audit 脚本里的反陈旧检查
)


def grep(pattern: re.Pattern, files: list[Path], *, exclude_paths: Optional[list[str]] = None) -> list[Hit]:
    exclude_paths = list(exclude_paths or []) + list(SELF_EXCLUDE_PATHS)
    hits: list[Hit] = []
    for f in files:
        rel = str(f.relative_to(REPO))
        if any(rel.startswith(ep) or rel == ep for ep in exclude_paths):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pattern.search(line):
                    # 合法的退役 DDL: 算 retirement-action, 不是 stale ref
                    if rel in RETIREMENT_METADATA_PATHS:
                        kind = "retirement_action"
                    elif any(p.search(line) for p in _LEGITIMATE_RETIREMENT_PATTERNS):
                        kind = "retirement_action"
                    elif is_comment_or_docstring(line):
                        kind = "comment"
                    elif "/tests/" in rel:
                        kind = "test"
                    elif rel.endswith(".md"):
                        kind = "doc"
                    else:
                        kind = "code"
                    hits.append(Hit(file=rel, line=i, text=line.strip()[:140], kind=kind))
        except Exception:
            pass
    return hits


# ─────────────────────────────────────────────────────────────────────
# Tier runners
# ─────────────────────────────────────────────────────────────────────

def tier1_marker_scan(files: list[Path]) -> dict[str, list[Hit]]:
    """Tier 1: 收集所有 retirement 标记位置."""
    out: dict[str, list[Hit]] = defaultdict(list)
    for pat, label in MARKER_PATTERNS:
        for h in grep(pat, files):
            out[label].append(h)
    return out


def tier3_known_retired_scan(files: list[Path]) -> list[Finding]:
    """Tier 3: 对 KNOWN_RETIRED 清单逐项 grep 活引用."""
    findings: list[Finding] = []
    for spec in KNOWN_RETIRED:
        all_hits: list[Hit] = []
        for pat_str in spec["search"]:
            pat = re.compile(pat_str)
            all_hits.extend(grep(pat, files, exclude_paths=spec.get("exclude_paths")))
        # 去重 (file, line)
        seen = set()
        uniq = []
        for h in all_hits:
            key = (h.file, h.line)
            if key not in seen:
                seen.add(key)
                uniq.append(h)
        # 算严重度: 真 stale = 排除注释/迁移说明/合法 DROP DDL/审计反模式测试 后还有引用
        live = [h for h in uniq if h.kind not in ("comment", "retirement_action")]
        if not live:
            severity = "info"
        elif any(h.kind == "code" for h in live):
            severity = "critical"
        else:
            severity = "warn"  # only doc/test refs
        findings.append(Finding(
            name=spec["name"],
            kind=spec["kind"],
            replaced_by=spec["replaced_by"],
            hits=uniq,
            severity=severity,
        ))
    return findings


def tier4_test_prod_parity(files: list[Path]) -> dict[str, list[Hit]]:
    """Tier 4: 测试用 X, 生产用 Y 的不一致."""
    out: dict[str, list[Hit]] = {}
    # sqlite3 in tests but DuckDB in prod
    test_sqlite = [
        h for h in grep(re.compile(r"^\s*import\s+sqlite3"), files)
        if h.file.startswith("backend/tests/")
        and not h.file.endswith("conftest.py")
    ]
    if test_sqlite:
        out["test_sqlite_vs_prod_duckdb"] = test_sqlite
    return out


# ─────────────────────────────────────────────────────────────────────
# Tier 5 — commented-out code blocks
# ─────────────────────────────────────────────────────────────────────

# 注释掉的 Python 代码模式. 排除中文注释/TODO/section 分隔符.
_COMMENTED_CODE_PATTERNS = [
    # 注释掉的赋值: `# foo = bar` (变量名 + = + 不是 ==)
    re.compile(r"^\s*#\s+([a-zA-Z_]\w*)\s*=\s*[^=]"),
    # 注释掉的函数调用: `# foo(...)`
    re.compile(r"^\s*#\s+[a-zA-Z_]\w*\s*\("),
    # 注释掉的关键字语句: import / from / def / class / if / for / while / return / raise / yield
    re.compile(r"^\s*#\s+(import|from\s+\w|def\s|class\s|if\s+\w|elif\s|else\s*:|for\s+\w|while\s+\w|return\b|raise\s|yield\s|with\s+\w|try\s*:|except\b)"),
    # 注释掉的常量赋值: `# CONST_X = ...`
    re.compile(r"^\s*#\s+([A-Z_][A-Z0-9_]+)\s*=\s*"),
    # 注释掉的 SQL: `# SELECT/INSERT/UPDATE/DELETE/CREATE/DROP/ALTER`
    re.compile(r"^\s*#\s+(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\s+", re.IGNORECASE),
    # 注释掉的 print/log 调用 (常见 debug 残留)
    re.compile(r"^\s*#\s+(print|logger\.\w+|log\.\w+|console\.\w+)\s*\("),
]

# 排除模式: 这些 # 行不算 commented-out code
_COMMENT_ALLOWLIST_PATTERNS = [
    re.compile(r"^\s*#\s*$"),  # 空注释
    re.compile(r"^\s*#\s*[=\-#*]{3,}"),  # section 分隔符 # === / # --- / # ###
    re.compile(r"^\s*#!"),  # shebang or magic
    re.compile(r"^\s*#\s*(TODO|FIXME|XXX|HACK|NOTE|BUG)\b", re.IGNORECASE),
    re.compile(r"^\s*#\s*type:"),  # type comment
    re.compile(r"^\s*#\s*pylint:"),
    re.compile(r"^\s*#\s*noqa"),
    re.compile(r"^\s*#\s*pragma:"),
]


def _has_chinese(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in s)


def tier5_commented_out_code(files: list[Path]) -> list[Hit]:
    """检测 # 注释掉的 Python 代码 (不是说明性中文注释)."""

    hits: list[Hit] = []
    for f in files:
        rel = str(f.relative_to(REPO))
        if any(rel.startswith(ep) for ep in SELF_EXCLUDE_PATHS):
            continue
        if not rel.endswith(".py"):
            continue
        try:
            for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if any(p.search(line) for p in _COMMENT_ALLOWLIST_PATTERNS):
                    continue
                # 含中文 → 是说明性注释, 排除
                if _has_chinese(line):
                    continue
                # 匹配任一 commented-code 模式
                if any(p.match(line) for p in _COMMENTED_CODE_PATTERNS):
                    hits.append(Hit(file=rel, line=i, text=line.strip()[:140], kind="dead_code"))
        except Exception:
            pass
    return hits


# ─────────────────────────────────────────────────────────────────────
# Tier 6 — dead branches (if False / if 0)
# ─────────────────────────────────────────────────────────────────────

_DEAD_BRANCH_PATTERNS = [
    re.compile(r"^\s*if\s+False\s*:"),
    re.compile(r"^\s*if\s+0\s*:"),
    re.compile(r"^\s*elif\s+False\s*:"),
    re.compile(r"^\s*while\s+False\s*:"),
    re.compile(r"^\s*if\s+__name__\s*==\s*['\"]__never__['\"]"),
]


def tier6_dead_branches(files: list[Path]) -> list[Hit]:
    """检测 if False / if 0 / while False 这些永远不执行的死分支."""

    hits: list[Hit] = []
    for f in files:
        rel = str(f.relative_to(REPO))
        if any(rel.startswith(ep) for ep in SELF_EXCLUDE_PATHS):
            continue
        if not rel.endswith(".py"):
            continue
        try:
            for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                for p in _DEAD_BRANCH_PATTERNS:
                    if p.match(line):
                        hits.append(Hit(file=rel, line=i, text=line.strip()[:140], kind="dead_branch"))
                        break
        except Exception:
            pass
    return hits


# ─────────────────────────────────────────────────────────────────────
# Tier 7 — file-level retirement markers
# ─────────────────────────────────────────────────────────────────────

# 文件头几行如果出现强烈的 "本文件/本模块已退役" 标记, 整个文件是删除候选
_FILE_RETIRED_MARKERS = [
    re.compile(r"^[\"#'*\s]*(.*)(本文件|本模块|本表|This\s+(?:file|module))(.*)(已退役|deprecat|retired|不再使用|不再维护)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[\"#'*\s]*(.*)整体退役", re.MULTILINE),
    re.compile(r"^[\"#'*\s]*(.*)(整表|整段|整体)退役", re.MULTILINE),
]


def tier7_retired_files(files: list[Path]) -> list[Hit]:
    """检测自我标记为退役的文件 (整文件级删除候选)."""

    hits: list[Hit] = []
    for f in files:
        rel = str(f.relative_to(REPO))
        if any(rel.startswith(ep) for ep in SELF_EXCLUDE_PATHS):
            continue
        if not rel.endswith((".py", ".md")):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            head = "\n".join(text.splitlines()[:30])
            for p in _FILE_RETIRED_MARKERS:
                m = p.search(head)
                if m:
                    line_no = head[: m.start()].count("\n") + 1
                    hits.append(Hit(file=rel, line=line_no, text=m.group(0).strip()[:140], kind="retired_file"))
                    break
        except Exception:
            pass
    return hits


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit stale references and technical-stack denylist hits.")
    parser.add_argument(
        "--output",
        default="/tmp/stale_audit.json",
        help="JSON report path. Default: /tmp/stale_audit.json",
    )
    parser.add_argument(
        "--phase0-only",
        action="store_true",
        help="Only run the cross-project Phase 0 technical-stack scan.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Always exit 0 after writing the report. Useful for first-pass baselines.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    phase0 = phase0_stack_scan()
    print(f"[SRA] phase0 stack scan: {phase0['scanned_files']} files across {len(phase0['project_roots'])} repos")
    for category, count in phase0["summary"].items():
        print(f"  {category}: {count}")

    if args.phase0_only:
        report = {"phase0_stack_scan": phase0}
        out_path = args.output
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nfull JSON report: {out_path}")
        return 0

    files = scan_files()
    print(f"[SRA] scanning {len(files)} files...")

    tier1 = tier1_marker_scan(files)
    tier3 = tier3_known_retired_scan(files)
    tier4 = tier4_test_prod_parity(files)
    tier5 = tier5_commented_out_code(files)
    tier6 = tier6_dead_branches(files)
    tier7 = tier7_retired_files(files)

    # ── 输出摘要 ──
    print("\n=== Tier 1: retirement markers in source ===")
    for label, hits in sorted(tier1.items()):
        non_comment = [h for h in hits if h.kind != "comment"]
        print(f"  {label}: total={len(hits)} non-comment={len(non_comment)}")
    print(f"  (full marker list: {sum(len(v) for v in tier1.values())} hits across {sum(1 for v in tier1.values() if v)} categories)")

    print("\n=== Tier 3: known-retired item references ===")
    critical = [f for f in tier3 if f.severity == "critical"]
    warn = [f for f in tier3 if f.severity == "warn"]
    info = [f for f in tier3 if f.severity == "info"]
    for f in critical + warn + info:
        sev_marker = {"critical": "🔴", "warn": "🟡", "info": "🟢"}.get(f.severity, "?")
        live = [h for h in f.hits if h.kind not in ("comment", "retirement_action")]
        # kind 分布
        kinds = defaultdict(int)
        for h in f.hits:
            kinds[h.kind] += 1
        print(f"  {sev_marker} {f.name} -> {f.replaced_by}")
        print(f"     hits: total={len(f.hits)}  by kind: {dict(kinds)}  live={len(live)}")
        if live[:5]:
            print(f"     ⚠ live refs (need fix):")
            for h in live[:5]:
                print(f"       {h.file}:{h.line}  ({h.kind})  {h.text[:100]}")

    print("\n=== Tier 4: test/prod engine parity ===")
    for label, hits in tier4.items():
        print(f"  🟡 {label}: {len(hits)} hits")
        for h in hits[:5]:
            print(f"     {h.file}:{h.line}")

    print(f"\n=== Tier 5: commented-out code ({len(tier5)} hits) ===")
    by_file = defaultdict(list)
    for h in tier5:
        by_file[h.file].append(h)
    for fn, hits in sorted(by_file.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"  🟠 {fn}: {len(hits)} commented-code lines")
        for h in hits[:3]:
            print(f"     L{h.line}: {h.text[:90]}")

    print(f"\n=== Tier 6: dead branches if False / if 0 ({len(tier6)} hits) ===")
    for h in tier6[:20]:
        print(f"  🟠 {h.file}:{h.line}  {h.text[:90]}")

    print(f"\n=== Tier 7: retired files ({len(tier7)} hits) ===")
    for h in tier7[:20]:
        print(f"  🔴 {h.file}:{h.line}  {h.text[:120]}")

    # ── JSON 报告 ──
    report = {
        "scanned_files": len(files),
        "phase0_stack_scan": phase0,
        "tier1_markers": {
            label: [asdict(h) for h in hits]
            for label, hits in tier1.items()
        },
        "tier3_known_retired": [asdict(f) for f in tier3],
        "tier4_parity": {
            label: [asdict(h) for h in hits]
            for label, hits in tier4.items()
        },
    }
    out_path = args.output
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nfull JSON report: {out_path}")

    # 退出码: 有 critical 时非零 (适合 CI)
    if args.no_fail:
        print("\nno-fail mode: report-only baseline written.")
        return 0
    if critical:
        print(f"\n❌ {len(critical)} critical stale references found. Fix before shipping.")
        return 1
    if warn or tier4:
        print(f"\n⚠ {len(warn)} warn-level stale references; {sum(len(v) for v in tier4.values())} parity issues.")
        return 0
    print("\n✓ no stale references detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
