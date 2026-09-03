"""血缘缝合器 — 从既有真相源派生血缘图 (不新增手填表, 设计原则#1)。

缝合 (T2 acquire+consume):
  1. 表节点 ← information_schema (manifest 内全 live 表, 真相源) + data_layers.yaml (layer 标签)
  2. acquire 边 ← sync_registry.yaml (source.api → target_table, 带 pit_anchor)
  3. SERVE 标注 ← data_access.yaml (哪些表是 SERVE entity 读层)
  4. consume 边 ← 确定性 git-grep fan-in (backend/assets/scripts 里词边界引用表名 = 消费方文件)

确定性 (drift 门): 库/表/文件全排序; 仅 tracked 文件 (git ls-files 范围); 无时间戳进图体。
"""
from __future__ import annotations

import fnmatch
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


# ── 登记表(无活库)表枚举 (#12(i), 2026-09-04): 提交门 build_lineage_graph(catalog=False)
# 只用这条链路, 不碰任何 .duckdb —— 纯暂存树函数, 写者持锁也能过。──────────────────────

def _manifest_table_patterns() -> dict[str, list[str]]:
    """database_manifest.yaml 每个非 skip 库声明的 table_patterns (纯配置读取, 不连库)。"""
    manifest = _load_yaml("database_manifest.yaml").get("databases", {})
    return {
        alias: list((spec or {}).get("table_patterns") or [])
        for alias, spec in manifest.items()
        if alias not in LINEAGE_DBS_SKIP
    }


def _default_registry_db(patterns_by_db: dict[str, list[str]]) -> str:
    """table_patterns 缺失的库 = 未按表名分区声明的 catch-all 库 (今天=smartmoney)。
    路由算法要求这个库恰好一个, 否则表名→库归属存在歧义, 拒绝静默猜测 (fail-closed,
    与本仓其余门的纪律一致: 查不了/猜不出不算过)。"""
    catch_alls = sorted(alias for alias, pats in patterns_by_db.items() if not pats)
    if len(catch_alls) != 1:
        raise RuntimeError(
            "lineage registry table routing ambiguous: database_manifest.yaml 里没有 "
            f"table_patterns 的库(catch-all)必须恰好一个, 现在是 {catch_alls} —— "
            "登记表枚举(catalog=False)拒绝在多个/零个候选间静默猜库"
        )
    return catch_alls[0]


def _match_manifest_db(table: str, patterns_by_db: dict[str, list[str]]) -> str | None:
    """按 database_manifest.table_patterns (含 * 通配符) 把表名路由到库; 多库同名 pattern
    撞车时取字典序首个 (确定性优先于"正确"—— 这种撞车本身该在 database_manifest 里修)。"""
    hits = sorted(
        alias for alias, patterns in patterns_by_db.items()
        if any(fnmatch.fnmatchcase(table, pat) for pat in patterns)
    )
    return hits[0] if hits else None


def _sync_registry_target_db_by_table() -> dict[str, str]:
    """sync_registry domains[*].target_table → target_db, 与 acquire 边算法同一路数据源
    (defaults → sources[source] → domain 字面量, 见 sync_registry.yaml 字段语义注释)。"""
    registry = _load_yaml("sync_registry.yaml")
    defaults = registry.get("defaults", {}) or {}
    sources_cfg = registry.get("sources", {}) or {}
    out: dict[str, str] = {}
    for spec in (registry.get("domains") or {}).values():
        spec = spec or {}
        target = spec.get("target_table")
        if not target:
            continue
        source = spec.get("source", "unknown")
        source_cfg = sources_cfg.get(source) or {}
        target_db = spec.get("target_db") or source_cfg.get("target_db") or defaults.get("target_db")
        if target_db:
            out.setdefault(target, target_db)
    return out


def _registry_table_specs() -> dict[str, str]:
    """登记表(无活库)枚举 table_name → db_alias, 供 catalog=False 建表节点 (#12(i))。

    来源与优先级:
      1. data_access.entities[*].(table, db) — 自带 db, 直采最高优先级
      2. database_manifest.table_patterns 里的字面量项(非通配符) — 自带 db
      3. sync_registry.domains[*].target_table ∪ data_layers.tables 的表名 — 按
         database_manifest.table_patterns(含通配符) 匹配 db, 找不到再退
         sync_registry 解出的 target_db, 最后落到 database_manifest 里唯一未声明
         table_patterns 的 catch-all 库 (今天=smartmoney)。

    刻意不用 brick_registry.outputs 做第四个表名来源: 实测 10 项 outputs 里 7 项
    (MarketContextSnapshot / StockStateDaily / kline_qfq / market_risk_on /
    pattern_event / project_board_adv_dec_ratio / stock_state_stage) 是别名或概念性
    产物, 不是物理表名, 无法确定库归属 —— 强行归并会把假节点塞进图 (与本文件
    "不新增手填表" 的设计原则#1 冲突), 已在 A1 交付报告 "方案与现实不符" 一节说明。
    """
    patterns_by_db = _manifest_table_patterns()
    default_db = _default_registry_db(patterns_by_db)
    sync_target_db = _sync_registry_target_db_by_table()
    layer_names = set(_table_layers())

    specs: dict[str, str] = {}

    entities = _load_yaml("data_access.yaml").get("entities", {}) or {}
    for spec in entities.values():
        spec = spec or {}
        table, db = spec.get("table"), spec.get("db")
        if table and db and db not in LINEAGE_DBS_SKIP:
            specs[table] = db

    for db_alias, patterns in sorted(patterns_by_db.items()):
        for pat in patterns:
            if "*" not in pat and pat not in specs:
                specs[pat] = db_alias

    for name in sorted(set(sync_target_db) | layer_names):
        if name in specs:
            continue
        db = _match_manifest_db(name, patterns_by_db) or sync_target_db.get(name) or default_db
        if db in LINEAGE_DBS_SKIP:
            continue
        specs[name] = db

    return specs


def catalog_drift() -> dict[str, list[str]]:
    """活库(information_schema)与登记表(catalog=False 枚举)的表集合差 (#12(i) runtime 雏形)。

    ghosts  = 活库存在但没有任何登记表声明它的表 (没人认领)
    orphans = 登记表声明了但活库不存在的表 (声明了没建 / 已删没退登记)
    两侧各自独立算 db 归属 (不假设一致), 用同一个 table:<db>.<table> id 空间比较。
    与 build_lineage_graph 共用全部私有 helper —— K4 check_datasets_registry 落地时
    "用同一个 builder 算" (方案 §7.5), 不是第二套实现。
    """
    live_ids = {
        _table_id(db_alias, t)
        for db_alias, tables in _live_tables_by_db().items()
        for t in tables
    }
    registry_ids = {_table_id(db, t) for t, db in _registry_table_specs().items()}
    return {
        "ghosts": sorted(live_ids - registry_ids),
        "orphans": sorted(registry_ids - live_ids),
    }


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


def build_lineage_graph(catalog: bool = False) -> LineageGraph:
    """catalog=False (默认, #12(i)): 表节点纯从登记表枚举, 不连任何 .duckdb —— 提交门
    lineage_drift 用这个模式, 是暂存树的纯函数, 写者持锁/日更建新表都不影响它。
    catalog=True (--with-catalog): 额外读 information_schema, 给交互式
    impact/provenance/dead 查询或诊断用; 活库 vs 登记表的差由 catalog_drift() 单独算,
    不靠这个模式的 node status 字段推断。
    """
    g = LineageGraph()
    layers = _table_layers()
    table_ids_by_name: dict[str, set[str]] = {}

    # --- 1. 表节点 (catalog=True: information_schema 真相源; catalog=False: 登记表) ---
    if catalog:
        for db_alias, tables in _live_tables_by_db().items():
            for t in tables:
                tid = _table_id(db_alias, t)
                table_ids_by_name.setdefault(t, set()).add(tid)
                g.add_node(Node(
                    id=tid,
                    kind="table",
                    attrs={"db": db_alias, "table": t, "layer": layers.get(t, "untagged"),
                           "status": "active"},
                ))
    else:
        for name, db_alias in sorted(_registry_table_specs().items()):
            tid = _table_id(db_alias, name)
            table_ids_by_name.setdefault(name, set()).add(tid)
            g.add_node(Node(
                id=tid,
                kind="table",
                attrs={"db": db_alias, "table": name, "layer": layers.get(name, "untagged"),
                       "status": "declared"},
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
                                   "status": "declared_not_live" if catalog else "declared"}))
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
