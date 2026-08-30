"""血缘缝合器 — 从既有真相源派生血缘图 (不新增手填表, 设计原则#1)。

缝合 (T2 acquire+consume):
  1. 表节点 ← information_schema (manifest 内全 live 表, 真相源) + data_layers.yaml (layer 标签)
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
# Experiment evidence is outside the Tier0 data-lineage projection.
LINEAGE_DBS_SKIP = {"experiment_store"}


def _table_id(db_alias: str, table: str) -> str:
    """物理表身份必须含库别名；裸表名在多库中不唯一。"""
    return f"table:{db_alias}.{table}"


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
    """information_schema 枚举 manifest 内全 live 表 (真相源: 表是否存在)。"""
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
        except Exception as exc:
            raise RuntimeError(f"lineage catalog scan failed for {alias} ({path}): {exc}") from exc
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
    except Exception as exc:
        raise RuntimeError(f"lineage consumer scan could not run git grep: {exc}") from exc
    if proc.returncode not in (0, 1):  # 0=命中 1=无命中; 其余=错误
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"lineage consumer scan git grep failed: {detail}")
    files = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return sorted(set(files))


def _git_grep_entity_consumers(entity: str) -> list[str]:
    """SERVE entity 别名消费方 (T3-a, 2026-06-26 修): DataAccess.get("entity") 用引号字符串,
    对表名 grep 不可见 → 表被误判无消费 (例: holders_top10 entity 经 SERVE 消费 fact_top10_holder_period; 表名不出现在调用处)。
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
    except Exception as exc:
        raise RuntimeError(f"lineage entity scan could not run git grep: {exc}") from exc
    if proc.returncode not in (0, 1):
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"lineage entity scan git grep failed: {detail}")
    return sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()})


def build_lineage_graph() -> LineageGraph:
    g = LineageGraph()

    # --- 1. 表节点 (information_schema 真相源 + layer 标签) ---
    live = _live_tables_by_db()
    layers = _table_layers()
    table_ids_by_name: dict[str, set[str]] = {}
    for db_alias, tables in live.items():
        for t in tables:
            tid = _table_id(db_alias, t)
            table_ids_by_name.setdefault(t, set()).add(tid)
            g.add_node(Node(
                id=tid,
                kind="table",
                attrs={"db": db_alias, "table": t, "layer": layers.get(t, "untagged"),
                       "status": "active"},
            ))

    # --- 2. acquire 边 (sync_registry: source.api → target_table) ---
    registry = _load_yaml("sync_registry.yaml")
    defaults = registry.get("defaults", {}) or {}
    sources_cfg = registry.get("sources", {}) or {}
    domains = registry.get("domains", {})
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
        # target_db 曾整体挂在 defaults (2026-08-30 移入 sources.<source>); 查不到本域 source
        # 对应的 sources 配置 (未知 vendor / 无 sources 段) 才落回 defaults/字面量兜底。
        source_cfg = sources_cfg.get(source) or {}
        target_db = spec.get("target_db") or source_cfg.get("target_db") or defaults.get("target_db", "unknown")
        # 目标表可能不在 live (未回填/已删) — 仍建节点 (acquire 声明存在), 标 declared
        tid = _table_id(target_db, target)
        if g.node(tid) is None:
            g.add_node(Node(id=tid, kind="table",
                            attrs={"db": target_db, "table": target,
                                   "layer": layers.get(target, "untagged"),
                                   "status": "declared_not_live"}))
            table_ids_by_name.setdefault(target, set()).add(tid)
        g.add_edge(Edge(src=src_id, dst=tid, kind="acquire",
                        attrs={"pit_anchor": spec.get("pit_anchor", ""),
                               "grain": spec.get("grain", [])}))

    # --- 3. SERVE 标注 (data_access entity → table) + 建 table→entity 索引 (T3-a consume) ---
    entities = _load_yaml("data_access.yaml").get("entities", {})
    entities_by_table_id: dict[str, set[str]] = {}
    for ent in sorted(entities):
        spec = entities[ent] or {}
        target = spec.get("table")
        target_db = spec.get("db")
        if not target or not target_db:
            continue
        tid = _table_id(target_db, target)
        node = g.node(tid)
        if node is not None:
            serve_entities = set(node.attrs.get("serve_entities", []))
            serve_entities.add(ent)
            node.attrs["serve_entities"] = sorted(serve_entities)
            node.attrs.setdefault("vendor", spec.get("vendor", ""))
        entities_by_table_id.setdefault(tid, set()).add(ent)

    # --- 4. consume 边 (确定性 git-grep fan-in: 表名直引 ∪ SERVE entity 别名 T3-a) ---
    for table in sorted(table_ids_by_name):
        direct_files = set(_git_grep_consumers(table))
        # 裸 SQL/代码只写表名时无法判库；为避免删除漏报，保守挂到每个同名物理表。
        for tid in sorted(table_ids_by_name[table]):
            files = set(direct_files)
            # entity 带 db 声明，因此别名消费只挂到精确物理表，不扩散到同名其他库。
            for ent in sorted(entities_by_table_id.get(tid, set())):
                files |= set(_git_grep_entity_consumers(ent))
            for fpath in sorted(files):
                cid = f"consumer:{fpath}"
                if g.node(cid) is None:
                    g.add_node(Node(id=cid, kind="consumer",
                                    attrs={"path": fpath, "ctype": _consumer_ctype(fpath)}))
                g.add_edge(Edge(src=tid, dst=cid, kind="consume", attrs={}))

    return g
