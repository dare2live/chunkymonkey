"""SERVE 读层验收门 — 数据模块顶层设计 §10 P1 gate 的可执行落地。

2026-07-08 系统性收口（历史结论已入 commit message（`chunkyctl history --grep`）；现行 owner:
docs/MASTER_TOPLEVEL_DESIGN.md + docs/engineering_governance.md + 本 gate/tests）:
原 D1/D2 门硬编码只扫 `backend/services/dossier.py` 一个文件 (2026-06-22 落地时的 P1
scope="只迁 dossier")。dossier.py 已随 2026-06-28 纯数据平台重建永久删除 (策略/serving
层整体退役, 不会再迁回), 该文件从此不存在导致 D1/D2 读空字符串必然 0 命中 —— 伪绿
(pass-by-vacuity), 与本项目 db_lifecycle_delete._live_surface() 曾踩过的结构性排除漏洞
同型。旧代码注释里其实早写着"D1 硬门只扫 dossier (P1 scope, 伪绿)"并造了一个更完整的
`scan_consumer_bypass()`(原 `--bypass-scan` 参数, 全量扫 backend/services+scripts, 靠
`data_module_members.yaml` roster 区分 builder[可读raw, 归 build-time PIT 门管] vs 薄消
费者[必须走 DataAccess.get, 归本门管]) —— 但这道更完整的检查此前只挂在 moth 断言里,
`moth assert` 已接入 safe_commit.sh (Step 2 moth 门; 2026-08-14 起其 blocking 子集另走 moth_invariants 硬门); CI 不跑 moth
的反而是伪绿的 D1/D2。本次收口: 退役 D1/D2, 把 `scan_consumer_bypass()` 提升为默认执法
的 D1(替代原两道门的职责), 不再需要额外 flag 才生效。

门:
  D1 no-consumer-bypass  : backend/services+scripts 全量扫描非成员消费者内联
                            FROM raw_*/price_kline*/duck_connect(/duckdb.connect( —
                            成员(data_module_members.yaml 登记的加工 builder)可读 raw,
                            非成员消费者必须走 DataAccess.get()(单概念单真相源, 不变量#4)
  D2 preflight-wired      : drivers/generic.py 调 resolver.preflight (schema 漂移自检接线)
  D3 lineage-complete     : data_access.yaml 每 entity 声明链齐全 (db+table+layer+vendor+asof_col+code_col),
                            追不到声明源=FAIL (可追溯=确定性走链)；table 以 raw_* 开头且 entity
                            不在 legacy_raw_plane.yaml data_access_raw_entity_allowlist → FAIL
                            （改指 v_<domain>_<grain>，不另立 D6）
  D4 feature-from-l2      : backend/scripts 0 个 experiment_/analyze_ 因子 runner
                            (L2-bypass 向量关闭: 实验只能在 sandbox, 按 README 读 L2 panel 不绕 L0 重算)
  D5 router-no-ad-hoc-raw : backend/routers/ 禁止内联 raw_* 读 (S6 serve 边界);
                            若有诚实 NONCONFORMING 残差须带 ``# serve-exempt:`` 理由
                            (S6 FIXED 后 live routers 应为零豁免)

跑: PYTHONPATH=backend python backend/scripts/check_serve_read_layer.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GENERIC = REPO / "backend" / "services" / "data_access" / "drivers" / "generic.py"
DATA_ACCESS_YAML = REPO / "backend" / "config" / "data_access.yaml"
LEGACY_RAW_PLANE_YAML = REPO / "backend" / "config" / "legacy_raw_plane.yaml"
SCRIPTS_DIR = REPO / "backend" / "scripts"
SERVICES_DIR = REPO / "backend" / "services"
ROUTERS_DIR = REPO / "backend" / "routers"
MEMBERS_YAML = REPO / "backend" / "config" / "data_module_members.yaml"

# entity 声明链必填字段 (D3): 缺一 = 追溯断链
REQUIRED_ENTITY_KEYS = ("db", "table", "layer", "vendor", "asof_col", "code_col")

INLINE_RAW_PATS = (r"FROM\s+raw_", r"FROM\s+price_kline\b", r"duck_connect\s*\(", r"duckdb\.connect\s*\(")

# S6: routers — flag ad-hoc raw table reads (alias-qualified included). Conn helpers
# alone are allowed; DataAccess / production_read remain the published read path.
ROUTER_RAW_PATS = (
    r"FROM\s+raw_",
    r"FROM\s+\w+\.raw_",
    r"JOIN\s+raw_",
    r"JOIN\s+\w+\.raw_",
)


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


def door_no_consumer_bypass() -> list[str]:
    """D1: 全 backend/services+scripts 非成员内联 raw 读 (不变量#4 违规)。

    成员 (data_module_members.yaml 登记的加工 builder, 可读 raw/写物化表) 豁免;
    非成员消费者内联裸查 = 违规, 必须改走 DataAccess.get()。
    """
    members = _load_members()
    retiring_names = set(members.get("source_retiring_temp_members", []))
    violations: list[str] = []
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
                continue  # 源退役临时成员: 删源后随之退役, 不算违规也不算长期成员
            if _is_member(p, members):
                continue
            violations.append(f"{rel}: 内联命中 {hits} (非成员消费者禁内联裸查, 应走 DataAccess.get 或登记进 data_module_members.yaml)")
    return violations


def door_preflight_wired() -> list[str]:
    code = _strip_comments_and_docstrings(_read(GENERIC))
    if not re.search(r"resolver\.preflight\s*\(", code):
        return ["drivers/generic.py 未调 resolver.preflight (schema 漂移自检未接线)"]
    return []


def _raw_entity_allowlist() -> set[str]:
    raw = _read(LEGACY_RAW_PLANE_YAML)
    if not raw:
        return set()
    doc = yaml.safe_load(raw) or {}
    names = doc.get("data_access_raw_entity_allowlist") or []
    if not isinstance(names, list):
        return set()
    return {str(x) for x in names}


def door_lineage_complete() -> list[str]:
    raw = _read(DATA_ACCESS_YAML)
    if not raw:
        return ["data_access.yaml 不存在"]
    doc = yaml.safe_load(raw) or {}
    entities = doc.get("entities", {})
    if not entities:
        return ["data_access.yaml entities 为空"]
    allowlist = _raw_entity_allowlist()
    bad = []
    for name, spec in entities.items():
        spec = spec or {}
        missing = [k for k in REQUIRED_ENTITY_KEYS if not spec.get(k)]
        if missing:
            bad.append(f"entity {name!r} 声明链缺字段 {missing} (追溯断链)")
        table = spec.get("table")
        if isinstance(table, str) and table.startswith("raw_") and name not in allowlist:
            bad.append(
                f"entity {name!r} table {table!r} still points at raw_*; "
                f"redirect to v_<domain>_<grain>"
            )
    return bad


def door_feature_from_l2() -> list[str]:
    # L2-bypass 向量 = experiment/IC 因子 runner 漏进 backend (绕 sandbox 直读 L0 重算因子)
    bad = []
    for p in SCRIPTS_DIR.glob("experiment_*.py"):
        bad.append(f"{p.name}: experiment runner 不许进 backend/scripts (实验入 sandbox 读 L2)")
    for p in SCRIPTS_DIR.glob("analyze_*.py"):
        bad.append(f"{p.name}: analyze runner 不许进 backend/scripts (探索入 sandbox)")
    return bad


def door_router_no_ad_hoc_raw() -> list[str]:
    """D5 (S6): routers must not introduce new ad-hoc raw_* SELECTs.

    Honest NONCONFORMING residuals (if any) require an explicit ``# serve-exempt:``
    rationale in-file (same token as D1). S6 FIXED: live routers should have zero
    exempts — drill/members/margin leaf lives in ``market_pulse_serve_read``.
    """
    if not ROUTERS_DIR.is_dir():
        return ["backend/routers/ missing"]
    violations: list[str] = []
    for p in sorted(ROUTERS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        raw = _read(p)
        if "# serve-exempt:" in raw:
            continue
        code = _strip_comments_and_docstrings(raw)
        hits = [pat for pat in ROUTER_RAW_PATS if re.search(pat, code, flags=re.IGNORECASE)]
        if hits:
            rel = str(p.relative_to(REPO))
            violations.append(
                f"{rel}: router 内联 raw 命中 {hits} "
                "(S6: 走 DataAccess/production_read, 或加 # serve-exempt: 理由)"
            )
    return violations


DOORS = [
    ("D1 no-consumer-bypass", door_no_consumer_bypass),
    ("D2 preflight-wired", door_preflight_wired),
    ("D3 lineage-complete", door_lineage_complete),
    ("D4 feature-from-l2", door_feature_from_l2),
    ("D5 router-no-ad-hoc-raw", door_router_no_ad_hoc_raw),
]


def main() -> int:
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
