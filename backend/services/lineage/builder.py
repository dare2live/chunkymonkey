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
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import duckdb
import yaml

from services.duck_adapter import audit_connect as _audit_connect
from services.lineage.model import Edge, LineageGraph, Node

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "backend" / "config"

# consume 扫描范围 (tracked 文件, 词边界引用 = fan-in)
SCAN_DIRS = ["backend", "assets", "scripts"]
# Experiment evidence is outside the Tier0 data-lineage projection.
LINEAGE_DBS_SKIP = {"experiment_store"}


class LiveCatalogUnreachable(RuntimeError):
    """活库某个库不可达 (缺文件被跑批锁住等) —— 调用方 (check_lineage_catalog_drift.py)
    据此 fail-open (UNVERIFIED, 不阻断)。是 RuntimeError 的子类, 与 _shared_bookkeeping_tables()
    的配置校验错误 (同样 RuntimeError, 但是配置真错了, 必须 fail-closed 报出来, 不能被同一个
    except 悄悄降级成"活库暂时查不到") 区分开——两者都是 RuntimeError, 但含义相反。"""


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
            # 2026-09-07: 走共享的只读审计入口 (短锁等待)。manifest 里有 7 个库 ——
            # 写者持锁时旧写法是**每库**等满 30 秒最坏 210 秒, 而本函数的调用方
            # (catalog_drift) 拿到异常后本就 fail-open, 等满只是白等。
            conn = _audit_connect(str(path))
            try:
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='main' ORDER BY table_name"
                ).fetchall()
                # 2026-09-18 cut_lineage_drift §2.4: 不再排除 `_` 前缀 —— 那条豁免的前提
                # ("建/即删" 瞬态锁探针) 对现存 `_lock_probe`/`_ep_*` 已不成立 (无 creator,
                # 或是持久草稿表), `_` 前缀已从"瞬态锁探针的巧合命名"退化成"谁都能借来
                # 永久隐身"的洞。真正的瞬态表改用 DuckDB TEMP TABLE (本刀 institution_profile
                # 的 `_ep_*` 同改): TEMP 表只在创建它的连接里可见、随连接关闭消失, 本函数
                # 每次都开一条新连接扫描, 天然看不到别的连接建的 TEMP 表, 不需要靠名字
                # 前缀二次过滤。
                out[alias] = [r[0] for r in rows]
            finally:
                conn.close()
        except Exception as exc:
            raise LiveCatalogUnreachable(
                f"lineage catalog scan failed for {alias} ({path}): {exc}"
            ) from exc
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


def _legacy_raw_plane_tables() -> tuple[str, ...]:
    """legacy_raw_plane.yaml 第四个声明源 (cut_lineage_drift §2.3): raw_tushare_* 物理面
    的完整清单 (它已经是这套清单——键集与活 raw_tushare_* 表实测一致)。取全部键, 不只
    role=retired —— 只取 retired 会让"改个 role 就从声明源里消失"变成一个可调的豁免。
    """
    return tuple(sorted((_load_yaml("legacy_raw_plane.yaml").get("tables") or {}).keys()))


def _shared_bookkeeping_tables() -> tuple[str, ...]:
    """database_manifest.yaml 顶层 shared_bookkeeping_tables 名单 (cut_lineage_drift §2.1):
    运行时记账表 (writer 以 conn 为参数, 一表一库假设不成立), 每个在线库可选出现一份。

    fail-closed: 名单成员必须在 data_layers.yaml 声明为 infra 层, 否则 RuntimeError ——
    封死"把非 infra 表塞进名单来藏错位"这条路 (L2_feature 等业务表进不来)。
    """
    raw = _load_yaml("database_manifest.yaml").get("shared_bookkeeping_tables")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError(
            "database_manifest.yaml.shared_bookkeeping_tables 必须是 list, 实际是 "
            f"{type(raw).__name__}"
        )
    names = tuple(str(item) for item in raw)
    layers = _table_layers()
    for name in names:
        layer = layers.get(name)
        if layer != "infra":
            raise RuntimeError(
                f"database_manifest.yaml.shared_bookkeeping_tables 成员 {name!r} 在 "
                f"data_layers.yaml 里的 layer 是 {layer!r}, 必须是 infra —— 拒绝把非 "
                "infra 表塞进名单来绕过登记漂移检查"
            )
    return names


def _registry_table_specs() -> dict[str, str]:
    """登记表(无活库)枚举 table_name → db_alias, 供 catalog=False 建表节点 (#12(i))。

    来源与优先级:
      1. data_access.entities[*].(table, db) — 自带 db, 直采最高优先级
      2. database_manifest.table_patterns 里的字面量项(非通配符) — 自带 db
      3. sync_registry.domains[*].target_table ∪ data_layers.tables 的表名 — 按
         database_manifest.table_patterns(含通配符) 匹配 db, 找不到再退
         sync_registry 解出的 target_db, 最后落到 database_manifest 里唯一未声明
         table_patterns 的 catch-all 库 (今天=smartmoney)。
      4. legacy_raw_plane.tables 的键 (cut_lineage_drift §2.3) — 同样按
         database_manifest.table_patterns 匹配 db (键全是 raw_tushare_*, 匹配不到时
         落 catch-all, 不假定必然命中 tushare_raw —— 若某张实际落在别的库, ghost+orphan
         会成对出现, 不会被这条路径悄悄放过)。优先级最低: 前三层已声明的名字不会被
         这层覆盖。

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

    for name in _legacy_raw_plane_tables():
        if name in specs:
            continue
        db = _match_manifest_db(name, patterns_by_db) or default_db
        if db in LINEAGE_DBS_SKIP:
            continue
        specs[name] = db

    return specs


def _ledger_has_table_drop(db_alias: str, table: str) -> bool:
    """目标库 mart_data_deletion_record 是否有该表的 table_drop 行 (cut_lineage_drift §2.2)。

    目标库文件不存在, 或目标库里连 mart_data_deletion_record 这张 ledger 表都没建过
    (= 从未做过生命周期删除) —— 两种情况都视作"无行", 不是错误: "这域压根没有过 ledger"
    是合法的正常状态, 不该被 fail-closed 纪律误伤成崩溃。

    返修 (blocking finding, 2026-09-19): 连接目标库这步必须走与 _live_tables_by_db
    相同的 fail-open 包法——被 catalog_drift() 每次 on_demand orphan 判定都会调用,
    目标库正被写锁占住时 (真实场景: tushare_raw 是分段写入最频繁的库, 见
    project memory「分段写库期间不开库不提交」) _audit_connect 会抛
    duckdb.IOException, 不是 RuntimeError 子类, 不包住就会让本函数裸 traceback 崩溃。
    内层 duckdb.CatalogException (ledger 表未建过, 视作无行) 仍在最内层单独捕获,
    不受外层影响——两者含义不同, 不能合并成一个 except。

    这里 raise 出来的 LiveCatalogUnreachable 由调用方 `_split_declared_unbuilt` 逐域
    单独 catch (不是靠它一路冒泡到 catalog_drift() 顶层)——本函数自己不知道、也不该
    知道其它域或其它库这次扫描是否已经成功, 只负责如实报告"这个目标库这次连不上"。
    """
    manifest = _load_yaml("database_manifest.yaml").get("databases", {}) or {}
    spec = manifest.get(db_alias) or {}
    path = spec.get("path")
    if not path:
        return False
    db_path = REPO / path
    if not db_path.exists():
        return False
    try:
        conn = _audit_connect(str(db_path))
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM mart_data_deletion_record "
                    "WHERE table_name = ? AND delete_scope = 'table_drop'",
                    [table],
                ).fetchone()
            except duckdb.CatalogException:
                # 目标库从未建过 mart_data_deletion_record —— 从未做过生命周期删除, 视作无行。
                return False
            return bool(row[0])
        finally:
            conn.close()
    except Exception as exc:
        raise LiveCatalogUnreachable(
            f"lineage catalog scan failed for {db_alias} ({db_path}): {exc}"
        ) from exc


def _split_declared_unbuilt(orphans: set[str]) -> tuple[set[str], set[str]]:
    """cut_lineage_drift §2.2: orphans 里 sync_policy=on_demand 且目标表从未被治理工具
    DROP 过 (目标库 mart_data_deletion_record 无该表 table_drop 行) 的那些, 改判
    declared_unbuilt (打印但不算漂移, exit 0); 其余 (非 on_demand, 或曾被删过) 仍是
    真 orphan —— "曾经存在过、被删过"的表不能靠改 sync_policy 就悄悄绿掉。

    返回 (剩余 orphans, declared_unbuilt)。

    返修 (blocking finding, 2026-09-19, builder.py:339): 每个 on_demand 域各自单独
    探测 (`_ledger_has_table_drop` 对 target_db 新开一条连接) —— 探测目标库与
    catalog_drift() 已经扫描过的活库集合(`live_by_db`)是不同的连接, 一个域的目标库
    (今天几乎总是 tushare_raw, 分段写入最频繁的库) 被写锁占住不代表其它库的扫描结果
    有问题。之前的写法让 `_ledger_has_table_drop` 抛出的 LiveCatalogUnreachable 直接
    穿透本函数、冒泡到 catalog_drift() 顶层, 于是调用方 check_lineage_catalog_drift.py
    的 fail-open 分支把"已经真实算出来的、其它库的 ghost/orphan"整体清空成 UNVERIFIED
    ——一个不相关库的临时锁, 冲掉了已经拿到的真漂移。这里改成逐域 try/except: 探测失败
    时这张表保持在 remaining(orphan) 里不动 (fail-closed, 不能因为探测不到就静默判成
    declared_unbuilt——那等于把"查不清"当"从没删过"处理), 也不让异常波及其它域或已经
    算好的 ghosts, 循环继续处理下一个域。
    """
    registry = _load_yaml("sync_registry.yaml")
    domains = registry.get("domains") or {}
    sources_cfg = registry.get("sources") or {}
    defaults = registry.get("defaults") or {}

    remaining = set(orphans)
    declared_unbuilt: set[str] = set()
    for spec in domains.values():
        spec = spec or {}
        target = spec.get("target_table")
        if not target or spec.get("sync_policy") != "on_demand":
            continue
        source = spec.get("source", "unknown")
        source_cfg = sources_cfg.get(source) or {}
        target_db = spec.get("target_db") or source_cfg.get("target_db") or defaults.get("target_db")
        if not target_db:
            continue
        tid = _table_id(target_db, target)
        if tid not in remaining:
            continue
        try:
            dropped = _ledger_has_table_drop(target_db, target)
        except LiveCatalogUnreachable:
            # 这个域的目标库探测不到 (写锁/缺文件等) —— 保持为 orphan, 不摘出, 也不
            # 让异常波及其它已经算好的域/库 (blocking finding 2026-09-19)。
            continue
        if dropped:
            continue  # 曾被治理工具删过 —— 仍是真 orphan, 不摘出
        remaining.discard(tid)
        declared_unbuilt.add(tid)
    return remaining, declared_unbuilt


def catalog_drift() -> dict[str, list[str]]:
    """活库(information_schema)与登记表(catalog=False 枚举)的表集合差 (#12(i) runtime 雏形)。

    ghosts          = 活库存在但没有任何登记表声明它的表 (没人认领)
    orphans         = 登记表声明了但活库不存在的表 (声明了没建 / 已删没退登记)
    declared_unbuilt = orphans 里 "on_demand 域从未取过、也从未被治理工具删过" 的表
                        (cut_lineage_drift §2.2) —— 打印但不算漂移。
    两侧各自独立算 db 归属 (不假设一致), 用同一个 table:<db>.<table> id 空间比较。
    与 build_lineage_graph 共用全部私有 helper —— K4 check_datasets_registry 落地时
    "用同一个 builder 算" (方案 §7.5), 不是第二套实现。

    shared_bookkeeping_tables (§2.1) 校验必须先于活库扫描执行: 它只需要 data_layers.yaml,
    fail-closed 的 RuntimeError 不该依赖活库是否可达。
    """
    shared_names = _shared_bookkeeping_tables()

    live_by_db = _live_tables_by_db()
    live_ids = {
        _table_id(db_alias, t)
        for db_alias, tables in live_by_db.items()
        for t in tables
    }
    registry_ids = {_table_id(db, t) for t, db in _registry_table_specs().items()}

    shared_set = set(shared_names)

    def _bare_table(tid: str) -> str:
        return tid.split(".", 1)[1]

    # §2.1: shared 名单成员按"每个在线库可选出现一份"单独判定, 不进入按名字→单库映射
    # 的常规比较 —— 无论 _registry_table_specs() 把它路由到哪一个库, 剔除两侧集合里
    # 它的全部副本 (任意库), 只在下面单独判它是不是"全库都不存在"。
    live_ids_cmp = {tid for tid in live_ids if _bare_table(tid) not in shared_set}
    registry_ids_cmp = {tid for tid in registry_ids if _bare_table(tid) not in shared_set}

    ghosts = live_ids_cmp - registry_ids_cmp
    orphans = registry_ids_cmp - live_ids_cmp

    for name in shared_names:
        if not any(name in tables for tables in live_by_db.values()):
            orphans.add(f"table:*.{name}")

    orphans, declared_unbuilt = _split_declared_unbuilt(orphans)

    return {
        "ghosts": sorted(ghosts),
        "orphans": sorted(orphans),
        "declared_unbuilt": sorted(declared_unbuilt),
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
    """catalog=False (默认, #12(i)): 表节点纯从登记表枚举, 不连任何 .duckdb ——
    纯函数, 写者持锁/日更建新表都不影响它, 所以 check_continuity_integrity 的下游消费方
    查询用这个模式现算 (2026-09-08 起; 此前是已退役的提交门 lineage_drift 在用)。
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
