"""血缘缝合器 — 从既有真相源派生血缘图 (不新增手填表, 设计原则#1)。

缝合 (T2 acquire+consume):
  1. 表节点 ← information_schema (8 库全 live 表, 真相源) + data_layers.yaml (layer 标签)
  2. acquire 边 ← sync_registry.yaml (source.api → target_table, 带 pit_anchor)
  3. SERVE 标注 ← data_access.yaml (哪些表是 SERVE entity 读层)
  4. consume 边 ← 确定性 git-grep fan-in (backend/assets/scripts 里词边界引用表名 = 消费方文件)

确定性 (drift 门): 库/表/文件全排序; 仅 tracked 文件 (git ls-files 范围); 无时间戳进图体。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from services.duck_adapter import connect as _duck_connect
from services.lineage.model import Edge, LineageGraph, Node

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "backend" / "config"

# consume 扫描范围 (tracked 文件, 词边界引用 = fan-in)
SCAN_DIRS = ["backend", "assets", "scripts"]
# 排除库 (派生/实验, 非数据底座血缘核心; alpha158=派生特征实验库)
LINEAGE_DBS_SKIP = {"alpha158", "experiment_store"}


def _load_yaml(name: str) -> dict[str, Any]:
    p = CONFIG / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _consumer_ctype(path: str) -> str:
    """按路径分类消费方类型 (确定性)。"""
    if "/tests/" in path or path.endswith("_test.py") or "/test_" in path:
        return "test"
    if path.startswith("assets/") or path.endswith((".js", ".html")):
        return "frontend"
    if path.startswith("backend/config/") or path.endswith((".yaml", ".yml")):
        return "config"
    if path.startswith("backend/scripts/") or path.startswith("scripts/"):
        return "script"
    if path.startswith("backend/services/"):
        return "service"
    if path.startswith("backend/routers/"):
        return "router"
    return "other"


def _live_tables_by_db() -> dict[str, list[str]]:
    """information_schema 枚举 8 库全 live 表 (真相源: 表是否存在)。"""
    manifest = _load_yaml("database_manifest.yaml").get("databases", {})
    out: dict[str, list[str]] = {}
    for alias in sorted(manifest):
        if alias in LINEAGE_DBS_SKIP:
            continue
        path = REPO / manifest[alias]["path"]
        if not path.exists():
            continue
        try:
            conn = _duck_connect(str(path), read_only=True)
            try:
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='main' ORDER BY table_name"
                ).fetchall()
                # 排除 _ 前缀瞬态表 (pipeline_lock 的 _lock_probe/_rw_probe 锁探针, 建/即删) —
                # 否则 build 时偶遇会进图 → graph.json 非确定性 (drift 门 flicker, 2026-06-26 实测)
                out[alias] = [r[0] for r in rows if not r[0].startswith("_")]
            finally:
                conn.close()
        except Exception:
            # 库被占用/锁 (单writer) → 跳过该库, 不崩 (血缘是投影, 缺一库降级非致命)
            out[alias] = []
    return out


def _table_layers() -> dict[str, str]:
    dl = _load_yaml("data_layers.yaml")
    return dict(dl.get("tables", {}) or {})


def _git_grep_consumers(table: str) -> list[str]:
    """确定性 fan-in: tracked 文件里词边界引用 <table> 的文件列表 (排序)。

    词边界 \\b 天然处理前缀碰撞 (raw_x 不匹配 raw_x_adj, 因 _ 是 word char 无边界)。
    """
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-w", table, "--", *SCAN_DIRS],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    if proc.returncode not in (0, 1):  # 0=命中 1=无命中; 其余=错误
        return []
    files = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return sorted(set(files))


def _git_grep_entity_consumers(entity: str) -> list[str]:
    """SERVE entity 别名消费方 (T3-a, 2026-06-26 修): DataAccess.get("entity") 用引号字符串,
    对表名 grep 不可见 → 表被误判无消费 (反例: holders_top10 entity 经 dossier 消费 raw_top10_floatholders)。
    grep 引号包裹的 entity 名, 排除 data_access 层自身 (定义/分发 entity 非消费方)。
    over-match 偏保守 = 多报潜在消费方, 删除决策更安全 (under-report 漏判才危险)。
    """
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-E", rf"""['"]{re.escape(entity)}['"]""", "--",
             "backend/services", "backend/routers", "backend/scripts", "assets",
             ":(exclude)backend/services/data_access"],
            cwd=str(REPO), capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    if proc.returncode not in (0, 1):
        return []
    return sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()})


def build_lineage_graph() -> LineageGraph:
    g = LineageGraph()

    # --- 1. 表节点 (information_schema 真相源 + layer 标签) ---
    live = _live_tables_by_db()
    layers = _table_layers()
    all_tables: set[str] = set()
    for db_alias, tables in live.items():
        for t in tables:
            all_tables.add(t)
            g.add_node(Node(
                id=f"table:{t}",
                kind="table",
                attrs={"db": db_alias, "layer": layers.get(t, "untagged"), "status": "active"},
            ))

    # --- 2. acquire 边 (sync_registry: source.api → target_table) ---
    domains = _load_yaml("sync_registry.yaml").get("domains", {})
    for dom in sorted(domains):
        spec = domains[dom] or {}
        target = spec.get("target_table")
        if not target:
            continue
        source = spec.get("source", "unknown")
        api = spec.get("api", dom)
        src_id = f"source:{source}.{api}"
        g.add_node(Node(id=src_id, kind="source_interface",
                        attrs={"source": source, "api": api, "domain": dom}))
        # 目标表可能不在 live (未回填/已删) — 仍建节点 (acquire 声明存在), 标 declared
        tid = f"table:{target}"
        if g.node(tid) is None:
            g.add_node(Node(id=tid, kind="table",
                            attrs={"db": "unknown", "layer": layers.get(target, "untagged"),
                                   "status": "declared_not_live"}))
            all_tables.add(target)
        g.add_edge(Edge(src=src_id, dst=tid, kind="acquire",
                        attrs={"pit_anchor": spec.get("pit_anchor", ""),
                               "grain": spec.get("grain", [])}))

    # --- 3. SERVE 标注 (data_access entity → table) + 建 table→entity 索引 (T3-a consume) ---
    entities = _load_yaml("data_access.yaml").get("entities", {})
    entity_by_table: dict[str, str] = {}
    for ent in sorted(entities):
        spec = entities[ent] or {}
        target = spec.get("table")
        if not target:
            continue
        tid = f"table:{target}"
        node = g.node(tid)
        if node is not None:
            node.attrs["serve_entity"] = ent
            node.attrs.setdefault("vendor", spec.get("vendor", ""))
        entity_by_table[target] = ent

    # --- 4. consume 边 (确定性 git-grep fan-in: 表名直引 ∪ SERVE entity 别名 T3-a) ---
    for table in sorted(all_tables):
        files = set(_git_grep_consumers(table))
        ent = entity_by_table.get(table)
        if ent:  # 表经 SERVE entity 消费的, 别名引用对表名 grep 不可见, 并入 (set 去重)
            files |= set(_git_grep_entity_consumers(ent))
        for fpath in sorted(files):
            cid = f"consumer:{fpath}"
            if g.node(cid) is None:
                g.add_node(Node(id=cid, kind="consumer",
                                attrs={"path": fpath, "ctype": _consumer_ctype(fpath)}))
            g.add_edge(Edge(src=f"table:{table}", dst=cid, kind="consume", attrs={}))

    return g
