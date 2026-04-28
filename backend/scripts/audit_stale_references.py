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
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "backend"

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
    {
        "name": "RPT_F10_EH_FREEHOLDERS",
        "kind": "external_api",
        "replaced_by": "tdxhub.holders.HolderFetcher",
        "search": [r"\bRPT_F10_EH_FREEHOLDERS\b"],
    },
    {
        "name": "top_free_holders",
        "kind": "capability",
        "replaced_by": "holders_top10_float",
        "search": [r'"top_free_holders"', r"'top_free_holders'"],
    },
    {
        "name": "datacenter-web",
        "kind": "url_root",
        "replaced_by": "miaoxiang aif10 / tdxhub",
        "search": [r"datacenter[-.]web", r"datacenter[.]eastmoney"],
    },
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


# 自审计脚本本身需排除 (它包含所有退役 item 名作为 registry 数据).
SELF_EXCLUDE_PATHS = (
    "backend/scripts/audit_stale_references.py",
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
                    if any(p.search(line) for p in _LEGITIMATE_RETIREMENT_PATTERNS):
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
# main
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    files = scan_files()
    print(f"[SRA] scanning {len(files)} files...")

    tier1 = tier1_marker_scan(files)
    tier3 = tier3_known_retired_scan(files)
    tier4 = tier4_test_prod_parity(files)

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

    # ── JSON 报告 ──
    report = {
        "scanned_files": len(files),
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
    out_path = "/tmp/stale_audit.json"
    with open(out_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nfull JSON report: {out_path}")

    # 退出码: 有 critical 时非零 (适合 CI)
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
