"""schema-init layer 门控 (2026-06-14 地基-reset)。

根治"删表后 app 启动 schema-init(CREATE IF NOT EXISTS) 空重建"的 recreation loop:
schema 文件的 CREATE/ALTER/INDEX 在执行前按 backend/config/data_layers.yaml 过滤,
只对**活层 (KEEP)** 的表执行; 非活层 (L2_feature/L3_model/L4_experiment, status=wiped) 的 DDL 滤除。
→ 删除"粘住", 不被启动重建。重建某层时改 data_layers.yaml 该表 layer 至活层即自动恢复 (声明式)。

owner = docs/MASTER_TOPLEVEL_DESIGN.md §4/§5 (分层与数据集契约)。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_REGISTRY = Path(__file__).resolve().parents[2] / "backend" / "config" / "data_layers.yaml"
# 活层 (保留+建表); 非这些层 = wiped, schema-init 不建
ACTIVE_LAYERS = {"L0_source", "L1_foundation", "L1k_kline_intermediate", "display", "infra"}


def _layered_tables() -> tuple[set[str], set[str]]:
    """返回 (活层表 keep, wiped层表 wiped[L2/L3/L4])。注册表缺失则 (空,空) = filter no-op。"""
    try:
        reg = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
        tbls = reg.get("tables") or {}
        keep = {t for t, l in tbls.items() if l in ACTIVE_LAYERS}
        wiped = {t for t, l in tbls.items() if l not in ACTIVE_LAYERS}
        return keep, wiped
    except Exception:  # noqa: BLE001 — 读不到放行 (不阻断启动)
        return set(), set()


def active_keep_tables() -> set[str]:
    return _layered_tables()[0]


def keep_stmt(stmt: str, keep: set[str] | None = None, wiped: set[str] | None = None) -> bool:
    """单条 DDL 语句是否该执行 (活层) — schema-init / MART_SCHEMA_MIGRATIONS 循环共用。
    滤除: (a) CREATE/ALTER 目标是非活层表; (b) 语句 FROM/JOIN/INTO 引用非活层真表。"""
    if keep is None:
        keep, wiped = _layered_tables()
    if not keep:
        return True  # 注册表空 → 放行
    wiped = wiped or set()
    s = stmt.strip()
    if not s:
        return False
    # 剥离前导 SQL 注释行 (-- ...): filter 按 ; split, 无分号的注释会粘到下一条语句;
    #   若不剥离, segment 以 -- 开头 → 下面 CREATE/ALTER target 正则匹配失败 → target=None
    #   → 退役表语句被误判 keep 而执行 (2026-06-28 实证: fact_institution_event 退役注释粘
    #   fact_setup_snapshot 索引, 致 init_db 在不存在的退役表上 CREATE INDEX 报错)。
    s = "\n".join(ln for ln in s.split("\n") if not ln.lstrip().startswith("--")).strip()
    if not s:
        return False  # 纯注释 segment 不执行
    target = None
    if re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX", s, re.I):
        m = re.search(r"\bON\s+[\"']?(\w+)", s, re.I)
        target = m.group(1) if m else None
    elif re.match(r"CREATE\s+TABLE", s, re.I):
        m = re.match(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)", s, re.I)
        target = m.group(1) if m else None
    elif re.match(r"ALTER\s+TABLE", s, re.I):
        m = re.match(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)", s, re.I)
        target = m.group(1) if m else None
    if target is not None and target not in keep:
        return False
    refs = re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"']?(\w+)", s, re.I)
    if any(r not in keep and (r in wiped or r.startswith(("mart_", "fact_", "dim_", "raw_", "v_"))) for r in refs):
        return False
    return True


def filter_schema_sql(sql: str) -> str:
    """schema DDL layer 门控 (整段 SQL): 逐语句过 keep_stmt; 非建表/非引用-非活层语句保留。"""
    keep, wiped = _layered_tables()
    if not keep:
        return sql
    out = [stmt.strip() for stmt in sql.split(";") if stmt.strip() and keep_stmt(stmt, keep, wiped)]
    return ";\n".join(out) + ";"
