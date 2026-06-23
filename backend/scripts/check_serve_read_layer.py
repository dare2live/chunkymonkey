"""SERVE 读层 P1 验收门 — 数据模块顶层设计 §10 P1 gate 的可执行落地。

计划 (analysis/data_module_toplevel_design_20260622.md line 308) 的 P1 验收标尺白纸黑字:
  "moth read-no-inline-table / read-no-self-asof / feature-from-l2 + preflight"。
本脚本把这 4 道门 + lineage-complete 收进**单一执法点** (controller 自持, 真 exit≠0, 可 red→green),
moth `serve-read-layer-p1-doors` 包它。任何一门红 = P1 未完成 / 回潮。

门 (consumer scope = P1 只迁了 dossier; signals_v2/routers 等未迁 consumer 属 P2/P3 债, 不在本门范围):
  D1 read-no-inline-table : dossier.py 0 内联 FROM raw_*/price_kline*/duck_connect (单概念单真相源, 不变量#4)
  D2 read-no-self-asof    : dossier.py 0 直接 .execute( → 不自写 asof SQL (PIT 只在读层 asof_gate 执行, 不变量#1)
  D3 preflight-wired      : drivers/generic.py 调 resolver.preflight (schema 漂移自检接线)
  D4 lineage-complete     : data_access.yaml 每 entity 声明链齐全 (db+table+layer+vendor+asof_col+code_col),
                            追不到声明源=FAIL (line 90 可追溯=确定性走链)
  D5 feature-from-l2      : backend/scripts 0 个 experiment_/analyze_ 因子 runner
                            (L2-bypass 向量关闭: 实验只能在 sandbox, 按 README 读 L2 panel 不绕 L0 重算)

跑: PYTHONPATH=backend python backend/scripts/check_serve_read_layer.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DOSSIER = REPO / "backend" / "services" / "dossier.py"
GENERIC = REPO / "backend" / "services" / "data_access" / "drivers" / "generic.py"
DATA_ACCESS_YAML = REPO / "backend" / "config" / "data_access.yaml"
SCRIPTS_DIR = REPO / "backend" / "scripts"

# entity 声明链必填字段 (D4): 缺一 = 追溯断链
REQUIRED_ENTITY_KEYS = ("db", "table", "layer", "vendor", "asof_col", "code_col")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _strip_comments_and_docstrings(src: str) -> str:
    """去掉 # 行注释 + 三引号 docstring/字符串, 只留真代码 — 防 docstring 里的
    'PIT: ann_date <= as_of' 叙述被门误判 (mythos §3 docstring 误伤)。"""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    lines = []
    for ln in src.splitlines():
        lines.append(re.sub(r"#.*$", "", ln))
    return "\n".join(lines)


def door_read_no_inline_table() -> list[str]:
    code = _strip_comments_and_docstrings(_read(DOSSIER))
    bad = []
    for pat in (r"FROM\s+raw_", r"FROM\s+price_kline", r"duck_connect\s*\(", r"duckdb\.connect\s*\("):
        n = len(re.findall(pat, code))
        if n:
            bad.append(f"dossier.py 命中 {pat!r} x{n} (consumer 禁内联裸查, 应走 DataAccess.get)")
    return bad


def door_read_no_self_asof() -> list[str]:
    code = _strip_comments_and_docstrings(_read(DOSSIER))
    bad = []
    # dossier 全委托 data_access → 0 直接 SQL 执行 = 物理上无法自写 asof
    n = len(re.findall(r"\.execute\s*\(", code)) + len(re.findall(r"\.sql\s*\(", code))
    if n:
        bad.append(f"dossier.py 命中 .execute(/.sql( x{n} (自写 SQL→可能自写 asof; PIT 应只在 asof_gate 执行)")
    return bad


def door_preflight_wired() -> list[str]:
    code = _strip_comments_and_docstrings(_read(GENERIC))
    if not re.search(r"resolver\.preflight\s*\(", code):
        return ["drivers/generic.py 未调 resolver.preflight (schema 漂移自检未接线)"]
    return []


def door_lineage_complete() -> list[str]:
    raw = _read(DATA_ACCESS_YAML)
    if not raw:
        return ["data_access.yaml 不存在"]
    doc = yaml.safe_load(raw) or {}
    entities = doc.get("entities", {})
    if not entities:
        return ["data_access.yaml entities 为空"]
    bad = []
    for name, spec in entities.items():
        spec = spec or {}
        missing = [k for k in REQUIRED_ENTITY_KEYS if not spec.get(k)]
        if missing:
            bad.append(f"entity {name!r} 声明链缺字段 {missing} (追溯断链)")
    return bad


def door_feature_from_l2() -> list[str]:
    # L2-bypass 向量 = experiment/IC 因子 runner 漏进 backend (绕 sandbox 直读 L0 重算因子)
    bad = []
    for p in SCRIPTS_DIR.glob("experiment_*.py"):
        bad.append(f"{p.name}: experiment runner 不许进 backend/scripts (实验入 sandbox 读 L2)")
    for p in SCRIPTS_DIR.glob("analyze_*.py"):
        bad.append(f"{p.name}: analyze runner 不许进 backend/scripts (探索入 sandbox)")
    return bad


# ── 不变量4 执法棘轮 (2026-06-23): 全量扫非成员消费者内联 raw 读 ──
# D1 硬门只扫 dossier (P1 scope, 伪绿). 本扫覆盖全 backend, 读 member roster 区分
# 成员(数据模块子模块, 可读 raw)vs 非成员消费者(必走 SERVE). WARN 默认 (--strict 升 exit1),
# 不动 D1-D5 硬门 (moth serve-read-layer-p1-doors 仍只验 dossier, 保绿).
MEMBERS_YAML = REPO / "backend" / "config" / "data_module_members.yaml"
SERVICES_DIR = REPO / "backend" / "services"
INLINE_RAW_PATS = (r"FROM\s+raw_", r"FROM\s+price_kline\b", r"duck_connect\s*\(", r"duckdb\.connect\s*\(")


def _load_members() -> dict:
    raw = _read(MEMBERS_YAML)
    return (yaml.safe_load(raw) or {}) if raw else {}


def _is_member(path: Path, members: dict) -> bool:
    rel = str(path.relative_to(REPO))
    name = path.name
    if any(rel.startswith(d) for d in members.get("member_dirs", [])):
        return True
    if path.parent == SCRIPTS_DIR and any(name.startswith(p) for p in members.get("member_script_prefixes", [])):
        return True
    if name in members.get("member_service_files", []):
        return True
    return False


def scan_consumer_bypass() -> tuple[list[str], list[str]]:
    """全 backend/services+scripts 非成员内联 raw 读 (不变量4 违规)。返回 (violations, retiring)。"""
    members = _load_members()
    retiring_names = set(members.get("source_retiring_temp_members", []))
    violations, retiring = [], []
    for base in (SERVICES_DIR, SCRIPTS_DIR):
        for p in sorted(base.rglob("*.py")):
            rel = str(p.relative_to(REPO))
            if "test" in rel or "sandbox" in rel:
                continue
            raw = _read(p)
            if "# serve-exempt:" in raw:   # evidence 豁免 (展示P4/源退役类, 带理由), 不计违规
                continue
            code = _strip_comments_and_docstrings(raw)
            hits = [pat for pat in INLINE_RAW_PATS if re.search(pat, code)]
            if not hits:
                continue
            if p.name in retiring_names:
                retiring.append(f"{rel} (源退役临时成员, 删源后退役)")
            elif _is_member(p, members):
                continue
            else:
                violations.append(f"{rel}: 内联命中 {hits}")
    return violations, retiring


DOORS = [
    ("D1 read-no-inline-table", door_read_no_inline_table),
    ("D2 read-no-self-asof", door_read_no_self_asof),
    ("D3 preflight-wired", door_preflight_wired),
    ("D4 lineage-complete", door_lineage_complete),
    ("D5 feature-from-l2", door_feature_from_l2),
]


def main() -> int:
    if "--bypass-scan" in sys.argv:
        strict = "--strict" in sys.argv
        violations, retiring = scan_consumer_bypass()
        for r in retiring:
            print(f"[RETIRE] {r}")
        for v in violations:
            print(f"[BYPASS] {v}")
        print(f"consumer_bypass_violations={len(violations)} (源退役临时={len(retiring)})")
        return 1 if (strict and violations) else 0   # WARN 默认 (不破 dossier 硬门); --strict 升 exit1
    total = 0
    for name, fn in DOORS:
        viol = fn()
        total += len(viol)
        if viol:
            print(f"[NO] {name}")
            for v in viol:
                print(f"     - {v}")
        else:
            print(f"[OK] {name}")
    print(f"violations={total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
