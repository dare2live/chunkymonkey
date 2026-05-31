"""陈旧引用审计 (Stale Reference Audit, SRA).

目的: 系统性发现"退役但代码里还在引用"的定时炸弹.

CLAUDE.md 规则 #11/#12 是从这个问题催生的:
  旧测试数据库替身曾隐藏 ANY_VALUE / information_schema 等 DuckDB-only 特性的 bug,
  测试通过, 生产炸.

本脚本用 5 层检测找类似问题:

  Tier 1 — 标记扫描 (Marker scan)
    源文件/文档里 grep "退役|已退役|deprecated|废弃|removed at|legacy|TODO.*remove|
    TODO.*retire|替代|supersede" 等关键词. 每个命中提取上下文, 推出 "什么退役了 +
    什么替代它".

  Tier 2 — 反向引用 (Reverse reference)
    对 Tier 1 找到的每个退役 item, 全仓库 grep 看还有哪些活引用. 比较位置 vs
    类型 (代码 / 测试 / 注释 / 配置).

  Tier 3 — 模式扫描 (Pattern scan)
    硬编码的退役模式: 已下架的 import、已退役的
    URL / 表名 / API 路径, 旧别名.

  Tier 4 — 测试-生产配对 (Test/Prod parity)
    生产用 X, 测试用 Y.

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
import ast
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
ROOT = REPO.parent
BACKEND = REPO / "backend"
PROJECT_ROOTS = {
    "chunkymonkey": REPO,
    "tdxhub": ROOT / "tdxhub",
    "miaoxiang": ROOT / "miaoxiang",
}

_LEGACY_DB_TOKEN = "sqli" + "te"
_LEGACY_DB3_TOKEN = _LEGACY_DB_TOKEN + "3"
_LEGACY_MASTER = _LEGACY_DB_TOKEN + "_master"
_AUTO_PK = "AUTOIN" + "CREMENT"
_BEGIN_LOCKED = "BEGIN " + "IMMEDIATE"
_ROW_FACTORY_TOKEN = "row" + "_" + "factory"
_P_DIRECTIVE = "PRA" + "GMA"
_P_TABLE_INFO = "table" + "_" + "info"
_OLD_BIZ_DB = "smartmoney" + ".db"
_OLD_MKT_DB = "market_data" + ".db"
_OLD_ETF_DB = "etf" + ".db"
_TABULAR_GROUP = "pan" + "das"
_TABULAR_ALIAS = "p" + "d" + "."
_TABULAR_FRAME = "Data" + "Frame"
_READ_SQL_QUERY = "read" + "_sql" + "_query"
_TO_SQL_CALL = "." + "to" + "_sql" + "("
_DF_CALL = "." + "df" + "()"

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
        "name": "legacy test DB import (in backend/)",
        "kind": "library",
        "replaced_by": "DuckDB / services.duck_adapter",
        # 测试可以转 DuckDB, 但生产代码完全禁
        "search": [rf"^\s*import\s+{_LEGACY_DB3_TOKEN}\b", rf"^\s*from\s+{_LEGACY_DB3_TOKEN}\b"],
        # clean_stale_running.py is an Optuna SQLite-storage repair tool, not the retired app DB path.
        "exclude_paths": ["backend/tests/conftest.py", "backend/scripts/clean_stale_running.py"],
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
        "replaced_by": "dim_active_a_stock",  # rule-compliance: ok evidence=audit-metadata
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
    # Tabular-runtime denylist.
    (_TABULAR_GROUP, _TABULAR_GROUP, re.compile(rf"\b{_TABULAR_GROUP}\b")),
    (_TABULAR_GROUP, _TABULAR_ALIAS, re.compile(r"\bp" + r"d\.")),
    (_TABULAR_GROUP, _TABULAR_FRAME, re.compile(rf"\b{_TABULAR_FRAME}\b")),
    (_TABULAR_GROUP, _READ_SQL_QUERY, re.compile(rf"\b{_READ_SQL_QUERY}\b")),
    (_TABULAR_GROUP, _TO_SQL_CALL, re.compile(r"\.to" + r"_sql\s*\(")),
    (_TABULAR_GROUP, _DF_CALL, re.compile(r"\." + r"df\s*\(")),
    # Legacy SQL denylist.
    (_LEGACY_DB_TOKEN, _LEGACY_DB_TOKEN, re.compile(rf"\b{_LEGACY_DB_TOKEN}\b", re.IGNORECASE)),
    (_LEGACY_DB_TOKEN, _LEGACY_DB3_TOKEN, re.compile(rf"\b{_LEGACY_DB3_TOKEN}\b")),
    (_LEGACY_DB_TOKEN, _LEGACY_MASTER, re.compile(rf"\b{_LEGACY_MASTER}\b")),
    (_LEGACY_DB_TOKEN, _AUTO_PK, re.compile(rf"\b{_AUTO_PK}\b", re.IGNORECASE)),
    (_LEGACY_DB_TOKEN, _BEGIN_LOCKED, re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE)),
    (_LEGACY_DB_TOKEN, _ROW_FACTORY_TOKEN, re.compile(rf"\b{_ROW_FACTORY_TOKEN}\b")),
    (_LEGACY_DB_TOKEN, _P_DIRECTIVE, re.compile(rf"\b{_P_DIRECTIVE}\s+(?:{_P_TABLE_INFO}|foreign_keys|journal_mode|synchronous|cache_size|wal_checkpoint)\b", re.IGNORECASE)),
    ("old_db_path", _OLD_BIZ_DB, re.compile(r"\bsmartmoney\.db\b")),
    ("old_db_path", _OLD_MKT_DB, re.compile(r"\bmarket_data\.db\b")),
    ("old_db_path", _OLD_ETF_DB, re.compile(r"\betf\.db\b")),
    ("old_db_path", "." + _LEGACY_DB_TOKEN, re.compile(rf"\.{_LEGACY_DB_TOKEN}\b", re.IGNORECASE)),
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
        out.extend(_phase0_project_files(project, root, allowed_ext))
    return out


def _kind_for_phase0(path: Path, project_root: Path) -> str:
    rel = str(path.relative_to(project_root))
    if rel.startswith(("akshareindex/raw/site/", "help.tdx/quant/")):
        return "docs"
    if "/tests/" in f"/{rel}" or rel.startswith("tests/") or rel.endswith("_test.py"):
        return "test"
    if path.suffix.lower() in {".md", ".rst", ".txt"}:
        return "docs"
    return "runtime"


def _phase0_category(group: str, kind: str) -> str:
    if group in {_TABULAR_GROUP, _LEGACY_DB_TOKEN}:
        return f"{group}_{kind}"
    return group


def _is_phase0_candidate(path: Path, allowed_ext: set[str]) -> bool:
    if not path.is_file() or _phase0_skip(path):
        return False
    return path.suffix in allowed_ext or path.name in {"requirements.txt", "Dockerfile", "Makefile"}


def _phase0_project_files(project: str, root: Path, allowed_ext: set[str]) -> list[tuple[str, Path]]:
    if not root.exists():
        return []
    return [(project, path) for path in root.rglob("*") if _is_phase0_candidate(path, allowed_ext)]


@lru_cache(maxsize=None)
def _read_lines_cached(path_str: str) -> tuple[str, ...]:
    return tuple(Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines())


def _read_lines(path: Path) -> tuple[str, ...]:
    return _read_lines_cached(str(path))


def _clear_read_cache() -> None:
    _read_lines_cached.cache_clear()


def _phase0_hits_for_line(project: str, rel: str, kind: str, line_no: int, line: str) -> list[TechStackHit]:
    hits: list[TechStackHit] = []
    for group, marker, pattern in TECH_STACK_PATTERNS:
        if not pattern.search(line):
            continue
        hits.append(TechStackHit(
            project=project,
            category=_phase0_category(group, kind),
            file=rel,
            line=line_no,
            marker=marker,
            text=line.strip()[:180],
        ))
    return hits


def _phase0_hits_for_file(project: str, path: Path) -> list[TechStackHit]:
    root = PROJECT_ROOTS[project]
    kind = _kind_for_phase0(path, root)
    rel = str(path.relative_to(ROOT))
    hits: list[TechStackHit] = []
    for line_no, line in enumerate(_read_lines(path), 1):
        hits.extend(_phase0_hits_for_line(project, rel, kind, line_no, line))
    return hits


def phase0_stack_scan() -> dict:
    """Plan Phase 0 scan: tabular/legacy-SQL/old path/source/link baseline across three repos."""

    hits: list[TechStackHit] = []
    scanned = phase0_scan_files()
    for project, path in scanned:
        hits.extend(_phase0_hits_for_file(project, path))

    summary: dict[str, int] = defaultdict(int)
    by_project: dict[str, int] = defaultdict(int)
    for hit in hits:
        summary[hit.category] += 1
        by_project[hit.project] += 1
    for required in (
        f"{_TABULAR_GROUP}_runtime", f"{_TABULAR_GROUP}_test", f"{_TABULAR_GROUP}_docs",
        f"{_LEGACY_DB_TOKEN}_runtime", f"{_LEGACY_DB_TOKEN}_test", f"{_LEGACY_DB_TOKEN}_docs",
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
    "backend/services/data_deprecation.py",
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


def _is_excluded_relpath(rel: str, exclude_paths: list[str]) -> bool:
    return any(rel.startswith(ep) or rel == ep for ep in exclude_paths)


def _is_retirement_action(rel: str, line: str) -> bool:
    return rel in RETIREMENT_METADATA_PATHS or any(
        pattern.search(line) for pattern in _LEGITIMATE_RETIREMENT_PATTERNS
    )


def _classify_hit_kind(rel: str, line: str) -> str:
    if _is_retirement_action(rel, line):
        return "retirement_action"
    if is_comment_or_docstring(line):
        return "comment"
    if "/tests/" in rel:
        return "test"
    if rel.endswith(".md"):
        return "doc"
    return "code"


def _grep_file(pattern: re.Pattern, path: Path) -> list[Hit]:
    rel = str(path.relative_to(REPO))
    hits: list[Hit] = []
    for i, line in enumerate(_read_lines(path), 1):
        if pattern.search(line):
            hits.append(Hit(file=rel, line=i, text=line.strip()[:140], kind=_classify_hit_kind(rel, line)))
    return hits


def grep(pattern: re.Pattern, files: list[Path], *, exclude_paths: Optional[list[str]] = None) -> list[Hit]:
    exclude_paths = list(exclude_paths or []) + list(SELF_EXCLUDE_PATHS)
    hits: list[Hit] = []
    for f in files:
        rel = str(f.relative_to(REPO))
        if _is_excluded_relpath(rel, exclude_paths):
            continue
        hits.extend(_grep_file(pattern, f))
    return hits


# ─────────────────────────────────────────────────────────────────────
# Tier runners
# ─────────────────────────────────────────────────────────────────────

def tier1_marker_scan(files: list[Path]) -> dict[str, list[Hit]]:
    """Tier 1: 收集所有 retirement 标记位置."""
    out: dict[str, list[Hit]] = defaultdict(list)
    for pat, label in MARKER_PATTERNS:
        out[label].extend(grep(pat, files))
    return out


def _dedupe_hits(hits: list[Hit]) -> list[Hit]:
    seen = set()
    uniq = []
    for h in hits:
        key = (h.file, h.line)
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq


def _severity_for_hits(hits: list[Hit]) -> str:
    live = [h for h in hits if h.kind not in ("comment", "retirement_action")]
    if not live:
        return "info"
    if any(h.kind == "code" for h in live):
        return "critical"
    return "warn"


def _hits_for_retired_spec(spec: dict, files: list[Path]) -> list[Hit]:
    hits: list[Hit] = []
    for pat_str in spec["search"]:
        hits.extend(grep(re.compile(pat_str), files, exclude_paths=spec.get("exclude_paths")))
    return _dedupe_hits(hits)


def tier3_known_retired_scan(files: list[Path]) -> list[Finding]:
    """Tier 3: 对 KNOWN_RETIRED 清单逐项 grep 活引用."""
    findings: list[Finding] = []
    for spec in KNOWN_RETIRED:
        uniq = _hits_for_retired_spec(spec, files)
        findings.append(Finding(
            name=spec["name"],
            kind=spec["kind"],
            replaced_by=spec["replaced_by"],
            hits=uniq,
            severity=_severity_for_hits(uniq),
        ))
    return findings


def tier4_test_prod_parity(files: list[Path]) -> dict[str, list[Hit]]:
    """Tier 4: 测试用 X, 生产用 Y 的不一致."""
    out: dict[str, list[Hit]] = {}
    # Legacy test DB import while production uses DuckDB.
    test_legacy_db_import = [
        h for h in grep(re.compile(rf"^\s*import\s+{_LEGACY_DB3_TOKEN}"), files)
        if h.file.startswith("backend/tests/")
        and not h.file.endswith("conftest.py")
    ]
    if test_legacy_db_import:
        out["test_legacy_db_vs_prod_duckdb"] = test_legacy_db_import
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
    re.compile(r"^\s*#\s*from\s+yaml\s*:", re.IGNORECASE),
]

_EXPLANATORY_COMMENT_MARKERS = ("→", "≈", "×", "≤", "≥", "—", "–")
_EXPLANATORY_ASSIGNMENT_RE = re.compile(r"^[a-zA-Z_]\w*(?:\s*\([^)]*\))?\s*=")
_EXPLANATORY_IDENTIFIER_NOTE_RE = re.compile(r"^[a-zA-Z_]\w*\s*\([^)]*\)")
_TITLECASE_PROSE_RE = re.compile(r"^[A-Z][a-z]+\s+")
_SQL_COMMENT_RE = re.compile(
    r"^(SELECT\b.+\bFROM\b|INSERT\s+INTO\b|UPDATE\b.+\bSET\b|DELETE\s+FROM\b|"
    r"CREATE\s+(?:TABLE|VIEW|INDEX)\b|DROP\s+(?:TABLE|VIEW|INDEX)\b|ALTER\s+TABLE\b)",
    re.IGNORECASE,
)


def _has_chinese(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in s)


def _comment_body(line: str) -> str:
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return ""
    return stripped[1:].strip()


def _looks_like_explanatory_comment(line: str) -> bool:
    body = _comment_body(line)
    if not body:
        return False
    lower_body = body.lower()
    if any(marker in body for marker in _EXPLANATORY_COMMENT_MARKERS) or "->" in body:
        return True
    if _TITLECASE_PROSE_RE.match(body):
        return True
    if _EXPLANATORY_ASSIGNMENT_RE.match(body) and (
        "," in body or "%" in body or ":" in body or body.count("=") > 1
    ):
        return True
    if _EXPLANATORY_IDENTIFIER_NOTE_RE.match(body) and (
        "," in body or " from " in lower_body or " col" in lower_body or any(ch.isdigit() for ch in body)
    ):
        return True
    return False


def _looks_like_commented_python(body: str) -> bool:
    candidate = body
    if body.rstrip().endswith(":"):
        candidate = f"{body}\n    pass"
    try:
        ast.parse(candidate)
    except SyntaxError:
        return False
    return True


def _looks_like_commented_code(body: str) -> bool:
    return _looks_like_commented_python(body) or bool(_SQL_COMMENT_RE.match(body))


def _is_self_excluded(rel: str) -> bool:
    return any(rel.startswith(ep) for ep in SELF_EXCLUDE_PATHS)


def _is_python_relpath(rel: str) -> bool:
    return rel.endswith(".py")


def _commented_code_hit(rel: str, line_no: int, line: str) -> Hit | None:
    if any(pattern.search(line) for pattern in _COMMENT_ALLOWLIST_PATTERNS):
        return None
    if _has_chinese(line) or _looks_like_explanatory_comment(line):
        return None
    if any(pattern.match(line) for pattern in _COMMENTED_CODE_PATTERNS) and _looks_like_commented_code(
        _comment_body(line)
    ):
        return Hit(file=rel, line=line_no, text=line.strip()[:140], kind="dead_code")
    return None


def _commented_code_hits_for_file(path: Path) -> list[Hit]:
    rel = str(path.relative_to(REPO))
    if _is_self_excluded(rel) or not _is_python_relpath(rel):
        return []

    hits: list[Hit] = []
    for i, line in enumerate(_read_lines(path), 1):
        hit = _commented_code_hit(rel, i, line)
        if hit:
            hits.append(hit)
    return hits


def tier5_commented_out_code(files: list[Path]) -> list[Hit]:
    """检测 # 注释掉的 Python 代码 (不是说明性中文注释)."""

    hits: list[Hit] = []
    for f in files:
        hits.extend(_commented_code_hits_for_file(f))
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


def _dead_branch_hit(rel: str, line_no: int, line: str) -> Hit | None:
    if any(pattern.match(line) for pattern in _DEAD_BRANCH_PATTERNS):
        return Hit(file=rel, line=line_no, text=line.strip()[:140], kind="dead_branch")
    return None


def _dead_branch_hits_for_file(path: Path) -> list[Hit]:
    rel = str(path.relative_to(REPO))
    if _is_self_excluded(rel) or not _is_python_relpath(rel):
        return []

    hits: list[Hit] = []
    for i, line in enumerate(_read_lines(path), 1):
        hit = _dead_branch_hit(rel, i, line)
        if hit:
            hits.append(hit)
    return hits


def tier6_dead_branches(files: list[Path]) -> list[Hit]:
    """检测 if False / if 0 / while False 这些永远不执行的死分支."""

    hits: list[Hit] = []
    for f in files:
        hits.extend(_dead_branch_hits_for_file(f))
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


def _retired_marker_match(head: str):
    for pattern in _FILE_RETIRED_MARKERS:
        match = pattern.search(head)
        if match:
            return match
    return None


def _retired_file_hit_for_file(path: Path) -> Hit | None:
    rel = str(path.relative_to(REPO))
    if _is_self_excluded(rel) or not rel.endswith((".py", ".md")):
        return None

    head = "\n".join(_read_lines(path)[:30])
    match = _retired_marker_match(head)
    if not match:
        return None
    line_no = head[: match.start()].count("\n") + 1
    return Hit(file=rel, line=line_no, text=match.group(0).strip()[:140], kind="retired_file")


def tier7_retired_files(files: list[Path]) -> list[Hit]:
    """检测自我标记为退役的文件 (整文件级删除候选)."""

    hits: list[Hit] = []
    for f in files:
        hit = _retired_file_hit_for_file(f)
        if hit:
            hits.append(hit)
    return hits


def _live_hits(hits: list[Hit]) -> list[Hit]:
    return [h for h in hits if h.kind not in ("comment", "retirement_action")]


def _kind_counts(hits: list[Hit]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for hit in hits:
        counts[hit.kind] += 1
    return dict(counts)


def _findings_by_severity(findings: list[Finding], severity: str) -> list[Finding]:
    return [finding for finding in findings if finding.severity == severity]


def _ordered_findings(findings: list[Finding]) -> list[Finding]:
    return (
        _findings_by_severity(findings, "critical")
        + _findings_by_severity(findings, "warn")
        + _findings_by_severity(findings, "info")
    )


def _print_tier1_summary(tier1: dict[str, list[Hit]]) -> None:
    print("\n=== Tier 1: retirement markers in source ===")
    sorted_items = sorted(tier1.items())
    for label, hits in sorted_items:
        non_comment = [h for h in hits if h.kind != "comment"]
        print(f"  {label}: total={len(hits)} non-comment={len(non_comment)}")
    print(
        f"  (full marker list: {sum(len(v) for v in tier1.values())} "
        f"hits across {sum(1 for v in tier1.values() if v)} categories)"
    )


def _print_live_refs(live: list[Hit]) -> None:
    if not live[:5]:
        return
    print("     [LIVE] refs need fix:")
    for hit in live[:5]:
        print(f"       {hit.file}:{hit.line}  ({hit.kind})  {hit.text[:100]}")


def _print_known_retired_finding(finding: Finding) -> None:
    sev_marker = {"critical": "[CRITICAL]", "warn": "[WARN]", "info": "[INFO]"}.get(
        finding.severity, "[?]"
    )
    live = _live_hits(finding.hits)
    print(f"  {sev_marker} {finding.name} -> {finding.replaced_by}")
    print(f"     hits: total={len(finding.hits)}  by kind: {_kind_counts(finding.hits)}  live={len(live)}")
    _print_live_refs(live)


def _print_tier3_summary(tier3: list[Finding]) -> None:
    print("\n=== Tier 3: known-retired item references ===")
    for finding in _ordered_findings(tier3):
        _print_known_retired_finding(finding)


def _print_parity_entry(label: str, hits: list[Hit]) -> None:
    print(f"  [WARN] {label}: {len(hits)} hits")
    for hit in hits[:5]:
        print(f"     {hit.file}:{hit.line}")


def _print_tier4_summary(tier4: dict[str, list[Hit]]) -> None:
    print("\n=== Tier 4: test/prod engine parity ===")
    for label, hits in tier4.items():
        _print_parity_entry(label, hits)


def _hits_by_file(hits: list[Hit]) -> dict[str, list[Hit]]:
    by_file: dict[str, list[Hit]] = defaultdict(list)
    for hit in hits:
        by_file[hit.file].append(hit)
    return by_file


def _print_file_group(file_name: str, hits: list[Hit]) -> None:
    print(f"  [COMMENTED] {file_name}: {len(hits)} commented-code lines")
    for hit in hits[:3]:
        print(f"     L{hit.line}: {hit.text[:90]}")


def _print_tier5_summary(tier5: list[Hit]) -> None:
    print(f"\n=== Tier 5: commented-out code ({len(tier5)} hits) ===")
    sorted_groups = sorted(_hits_by_file(tier5).items(), key=lambda item: -len(item[1]))[:15]
    for file_name, hits in sorted_groups:
        _print_file_group(file_name, hits)


def _print_limited_hits(title: str, hits: list[Hit], marker: str, limit: int, text_len: int) -> None:
    print(title)
    for hit in hits[:limit]:
        print(f"  {marker} {hit.file}:{hit.line}  {hit.text[:text_len]}")


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


def _build_full_report(
    *,
    scanned_files: int,
    phase0: dict,
    tier1: dict[str, list[Hit]],
    tier3: list[Finding],
    tier4: dict[str, list[Hit]],
    tier5: list[Hit],
    tier6: list[Hit],
    tier7: list[Hit],
) -> dict:
    critical = [finding for finding in tier3 if finding.severity == "critical"]
    warn = [finding for finding in tier3 if finding.severity == "warn"]
    tier4_hit_count = sum(len(hits) for hits in tier4.values())
    return {
        "scanned_files": scanned_files,
        "summary": {
            "critical_known_retired": len(critical),
            "warn_known_retired": len(warn),
            "tier4_parity_hits": tier4_hit_count,
            "tier5_commented_out_code_hits": len(tier5),
            "tier6_dead_branch_hits": len(tier6),
            "tier7_retired_file_hits": len(tier7),
        },
        "phase0_stack_scan": phase0,
        "tier1_markers": {
            label: [asdict(hit) for hit in hits]
            for label, hits in tier1.items()
        },
        "tier3_known_retired": [asdict(finding) for finding in tier3],
        "tier4_parity": {
            label: [asdict(hit) for hit in hits]
            for label, hits in tier4.items()
        },
        "tier5_commented_out_code": [asdict(hit) for hit in tier5],
        "tier6_dead_branches": [asdict(hit) for hit in tier6],
        "tier7_retired_files": [asdict(hit) for hit in tier7],
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    _clear_read_cache()
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
    critical = [f for f in tier3 if f.severity == "critical"]
    warn = [f for f in tier3 if f.severity == "warn"]
    _print_tier1_summary(tier1)
    _print_tier3_summary(tier3)
    _print_tier4_summary(tier4)
    _print_tier5_summary(tier5)
    _print_limited_hits(
        f"\n=== Tier 6: dead branches if False / if 0 ({len(tier6)} hits) ===",
        tier6,
        "[DEAD]",
        20,
        90,
    )
    _print_limited_hits(
        f"\n=== Tier 7: retired files ({len(tier7)} hits) ===",
        tier7,
        "[RETIRED]",
        20,
        120,
    )

    # ── JSON 报告 ──
    report = _build_full_report(
        scanned_files=len(files),
        phase0=phase0,
        tier1=tier1,
        tier3=tier3,
        tier4=tier4,
        tier5=tier5,
        tier6=tier6,
        tier7=tier7,
    )
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
        print(f"\n[FAIL] {len(critical)} critical stale references found. Fix before shipping.")
        return 1
    if warn or tier4:
        print(f"\n[WARN] {len(warn)} warn-level stale references; {sum(len(v) for v in tier4.values())} parity issues.")
        return 0
    print("\n[PASS] no stale references detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
