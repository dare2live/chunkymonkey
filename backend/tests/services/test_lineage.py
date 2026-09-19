"""M5-T2 血缘路由中枢单测 — 合成图测 query 逻辑 + 真实 build 集成测 + 确定性 (drift 门前提)。"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import duckdb
import pytest
import yaml

from services.lineage import build_lineage_graph, dead_tables, impact, provenance
from services.lineage import builder
from services.lineage.model import Edge, LineageGraph, Node


def _synthetic() -> LineageGraph:
    """source(tushare.x)→table(raw_x)[acquire]; raw_x→consumer(svc.py)[consume];
    raw_dead = L0 raw 无消费 (不该判死, L0 永不删); mart_dead = 派生表无消费 (真死)。"""
    g = LineageGraph()
    g.add_node(Node("source:tushare.x", "source_interface", {"source": "tushare", "api": "x"}))
    g.add_node(Node("table:tushare_raw.raw_x", "table", {"db": "tushare_raw", "table": "raw_x", "layer": "L0_source", "status": "active"}))
    g.add_node(Node("table:tushare_raw.raw_dead", "table", {"db": "tushare_raw", "table": "raw_dead", "layer": "L0_source", "status": "active"}))
    g.add_node(Node("table:smartmoney.mart_dead", "table", {"db": "smartmoney", "table": "mart_dead", "layer": "L2_feature", "status": "active"}))
    g.add_node(Node("consumer:backend/services/svc.py", "consumer",
                    {"path": "backend/services/svc.py", "ctype": "service"}))
    g.add_edge(Edge("source:tushare.x", "table:tushare_raw.raw_x", "acquire", {"pit_anchor": "trade_date"}))
    g.add_edge(Edge("table:tushare_raw.raw_x", "consumer:backend/services/svc.py", "consume"))
    return g


# --- model 确定性 ---
def test_graph_deterministic_serialization():
    g = _synthetic()
    d1 = json.dumps(g.to_dict(generated_at=None), sort_keys=True, ensure_ascii=False)
    # 重建 (不同插入序) 应得同序列化
    g2 = LineageGraph()
    g2.add_node(Node("consumer:backend/services/svc.py", "consumer",
                     {"ctype": "service", "path": "backend/services/svc.py"}))
    g2.add_node(Node("table:tushare_raw.raw_dead", "table", {"status": "active", "db": "tushare_raw", "table": "raw_dead", "layer": "L0_source"}))
    g2.add_node(Node("table:smartmoney.mart_dead", "table", {"status": "active", "layer": "L2_feature", "db": "smartmoney", "table": "mart_dead"}))
    g2.add_node(Node("table:tushare_raw.raw_x", "table", {"layer": "L0_source", "db": "tushare_raw", "table": "raw_x", "status": "active"}))
    g2.add_node(Node("source:tushare.x", "source_interface", {"api": "x", "source": "tushare"}))
    g2.add_edge(Edge("table:tushare_raw.raw_x", "consumer:backend/services/svc.py", "consume"))
    g2.add_edge(Edge("source:tushare.x", "table:tushare_raw.raw_x", "acquire", {"pit_anchor": "trade_date"}))
    d2 = json.dumps(g2.to_dict(generated_at=None), sort_keys=True, ensure_ascii=False)
    assert d1 == d2  # 插入序无关, 确定性 (drift 门前提)


def test_roundtrip_from_dict():
    g = _synthetic()
    d = g.to_dict(generated_at="ts")
    g2 = LineageGraph.from_dict(d)
    assert json.dumps(g2.to_dict(generated_at=None), sort_keys=True) == \
           json.dumps(g.to_dict(generated_at=None), sort_keys=True)


# --- query: impact (killer fan-in) ---
def test_impact_lists_consumers():
    g = _synthetic()
    imp = impact(g, "raw_x")
    assert imp["exists"] is True
    assert imp["consumer_count"] == 1
    assert imp["consumers_by_type"]["service"] == ["backend/services/svc.py"]


def test_impact_accepts_prefixed_id():
    g = _synthetic()
    assert impact(g, "table:tushare_raw.raw_x") == impact(g, "tushare_raw.raw_x")


def test_impact_missing_table():
    g = _synthetic()
    imp = impact(g, "nonexistent_table")
    assert imp["exists"] is False and imp["consumer_count"] == 0


# --- query: provenance (溯源) ---
def test_provenance_traces_to_source():
    g = _synthetic()
    prov = provenance(g, "raw_x")
    assert prov["acquired"] is True
    assert prov["acquired_from"][0]["source"] == "tushare"
    assert prov["acquired_from"][0]["api"] == "x"
    assert prov["acquired_from"][0]["pit_anchor"] == "trade_date"


def test_provenance_unacquired():
    g = _synthetic()
    assert provenance(g, "raw_dead")["acquired"] is False


# --- query: dead (无消费方的派生表; L0 源永不死) ---
def test_dead_detects_unconsumed_table():
    g = _synthetic()
    dead = dead_tables(g)
    names = [d["table"] for d in dead]
    assert "mart_dead" in names     # 派生表无消费 = 真死
    assert "raw_x" not in names     # 有 service 消费 = 活
    # L0 源排除 (2026-06-26 修): raw_dead 无消费但 raw_ 前缀 = L0 源, 永不判死 (re-sync 重建)
    assert "raw_dead" not in names


def test_dead_excludes_l0_acquired_table():
    """有 acquire 边的表 (从 vendor 同步) = L0 源, 即便无下游消费也不判死。"""
    g = LineageGraph()
    g.add_node(Node("source:tushare.y", "source_interface", {"source": "tushare", "api": "y"}))
    # 非 raw_ 前缀但有 acquire 边 (e.g. canonical 直接 sync 的表)
    g.add_node(Node("table:smartmoney.dim_synced", "table", {"db": "smartmoney", "table": "dim_synced", "layer": "L1_foundation", "status": "active"}))
    g.add_edge(Edge("source:tushare.y", "table:smartmoney.dim_synced", "acquire", {}))
    assert "dim_synced" not in [d["table"] for d in dead_tables(g)]  # acquire 边 → L0 源不死


def test_builder_keeps_same_table_name_in_two_databases(monkeypatch):
    """跨库同名表必须保留两节点；裸名直引保守挂两边，entity 别名只挂精确 db。

    catalog=True 显式传参 (#12(i), 2026-09-04): 这条用例专测 live-catalog 面
    (_live_tables_by_db 的两库同名场景), 提交门已改用 catalog=False(默认) 不再
    读活库 —— 显式传 catalog=True 让这条用例继续测它本来要测的东西, 不受默认值
    翻转影响。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "market": ["same_name"],
        "smartmoney": ["same_name"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {"same_name": "L2_feature"})

    def fake_yaml(name: str):
        if name == "sync_registry.yaml":
            return {
                "defaults": {"target_db": "market"},
                "domains": {
                    "same": {
                        "source": "vendor",
                        "api": "same",
                        "target_table": "same_name",
                        "grain": ["id"],
                    }
                },
            }
        if name == "data_access.yaml":
            return {
                "entities": {
                    "smart_same": {
                        "db": "smartmoney",
                        "table": "same_name",
                        "vendor": "internal",
                    }
                }
            }
        return {}

    monkeypatch.setattr(builder, "_load_yaml", fake_yaml)
    monkeypatch.setattr(
        builder,
        "_git_grep_consumers",
        lambda table: ["backend/services/direct.py"] if table == "same_name" else [],
    )
    monkeypatch.setattr(
        builder,
        "_git_grep_entity_consumers",
        lambda entity: ["backend/services/entity_user.py"] if entity == "smart_same" else [],
    )

    graph = builder.build_lineage_graph(catalog=True)
    market = "table:market.same_name"
    smart = "table:smartmoney.same_name"
    assert graph.node(market) is not None
    assert graph.node(smart) is not None
    assert {edge.dst for edge in graph.edges_from(market, "consume")} == {
        "consumer:backend/services/direct.py"
    }
    assert {edge.dst for edge in graph.edges_from(smart, "consume")} == {
        "consumer:backend/services/direct.py",
        "consumer:backend/services/entity_user.py",
    }
    assert [edge.dst for edge in graph.edges_from("source:vendor.same", "acquire")] == [market]

    ambiguous = impact(graph, "same_name")
    assert ambiguous["ambiguous"] is True
    assert ambiguous["qualified_tables"] == ["market.same_name", "smartmoney.same_name"]
    assert ambiguous["consumer_count"] == 2


@pytest.mark.parametrize(
    ("scan", "value"),
    [
        (builder._git_grep_consumers, "some_table"),
        (builder._git_grep_entity_consumers, "some_entity"),
    ],
)
def test_consumer_scan_fails_closed_when_git_grep_errors(monkeypatch, scan, value):
    """Git/index 不可用时不得把扫描错误伪装成零消费者。"""
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: builder.subprocess.CompletedProcess(
            args=args[0],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        ),
    )

    with pytest.raises(RuntimeError, match="git grep failed"):
        scan(value)


def test_catalog_scan_fails_closed_when_database_cannot_be_read(tmp_path, monkeypatch):
    db_path = tmp_path / "broken.duckdb"
    db_path.touch()
    monkeypatch.setattr(
        builder,
        "_load_yaml",
        lambda name: {
            "databases": {"market": {"path": str(db_path)}}
        } if name == "database_manifest.yaml" else {},
    )
    monkeypatch.setattr(
        builder,
        "_audit_connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("catalog locked")),
    )

    with pytest.raises(RuntimeError, match="lineage catalog scan failed for market"):
        builder._live_tables_by_db()


# --- catalog=False (#12(i), 2026-09-04): 登记表(无活库)表枚举 ---

def _fake_yaml_for_registry(overrides: dict) -> "callable":
    def fake_yaml(name: str):
        return overrides.get(name, {})
    return fake_yaml


def test_registry_mode_never_touches_live_catalog(monkeypatch):
    """catalog=False (默认) 绝不能调 _live_tables_by_db —— 这是提交门"写者持锁也能过"
    的机制来源, 用一个会 raise 的替身证明这条路径真的不会被调到。"""
    def _boom():
        raise AssertionError("catalog=False must not touch the live catalog")

    monkeypatch.setattr(builder, "_live_tables_by_db", _boom)
    monkeypatch.setattr(builder, "_table_layers", lambda: {"dim_x": "L1_foundation"})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"main": {}, "other": {"table_patterns": ["raw_y"]}}},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {"dim_x": "L1_foundation"}},
        "data_access.yaml": {"entities": {}},
    }))
    monkeypatch.setattr(builder, "_git_grep_consumers", lambda table: [])
    monkeypatch.setattr(builder, "_git_grep_entity_consumers", lambda entity: [])

    g = builder.build_lineage_graph()  # default catalog=False
    assert g.node("table:main.dim_x") is not None
    assert g.node("table:main.dim_x").attrs["status"] == "declared"


def test_registry_mode_routes_by_manifest_table_patterns(monkeypatch):
    """database_manifest.table_patterns (含通配符) 把表路由到非默认库; 没被任何
    pattern 命中的表落回唯一没声明 table_patterns 的 catch-all 库。"""
    monkeypatch.setattr(builder, "_table_layers", lambda: {
        "raw_vendor_x": "L0_source", "dim_unrouted": "L1_foundation",
    })
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {},  # catch-all: 唯一没有 table_patterns 的库
            "vendor_raw": {"table_patterns": ["raw_vendor_*"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {"raw_vendor_x": "L0_source", "dim_unrouted": "L1_foundation"}},
        "data_access.yaml": {"entities": {}},
    }))
    monkeypatch.setattr(builder, "_git_grep_consumers", lambda table: [])
    monkeypatch.setattr(builder, "_git_grep_entity_consumers", lambda entity: [])

    specs = builder._registry_table_specs()
    assert specs["raw_vendor_x"] == "vendor_raw"      # 通配符命中
    assert specs["dim_unrouted"] == "main"             # 没命中任何 pattern -> catch-all


def test_registry_mode_ambiguous_catch_all_raises(monkeypatch):
    """table_patterns 缺失的库不是恰好一个 (0 个或 >=2 个) 时必须 fail-closed, 不许
    静默猜库。"""
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"a": {}, "b": {}}},
    }))
    with pytest.raises(RuntimeError, match="ambiguous"):
        builder._registry_table_specs()


def test_registry_mode_data_access_db_overrides_pattern_routing(monkeypatch):
    """data_access.entities 自带 db, 优先级最高 (即便 database_manifest 的 pattern
    会把它路由去另一个库)。"""
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {}, "vendor_raw": {"table_patterns": ["v_shared"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {"e1": {"table": "v_shared", "db": "main"}}},
    }))
    specs = builder._registry_table_specs()
    assert specs["v_shared"] == "main"


def test_catalog_drift_reports_ghosts_and_orphans(monkeypatch):
    """catalog_drift() 用 table:<db>.<table> id 空间独立算两侧, 不经过 LineageGraph
    (K4 datasets_registry 雏形; §12(i) runtime lineage_catalog_drift 的核心函数)。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": ["dim_registered", "dim_ghost"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {"dim_registered": "L1_foundation", "dim_missing": "L1_foundation"})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"main": {}}},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {"dim_registered": "L1_foundation", "dim_missing": "L1_foundation"}},
        "data_access.yaml": {"entities": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == ["table:main.dim_ghost"]
    assert drift["orphans"] == ["table:main.dim_missing"]


# ═══════════════════════════════════════════════════════════════════════════
# cut_lineage_drift (2026-09-18): shared_bookkeeping_tables (§2.1) / on_demand
# declared_unbuilt (§2.2) / legacy_raw_plane 第四声明源 (§2.3) / `_` 前缀不再隐身
# (§2.4, H1)。每条一个"其它全满足只违反它"的隔离用例, 表里写了变异的逐条做过手工
# 变异验证 (cp 备份 → 改坏生产代码 → 跑对应用例 → 记红在哪个节点 → mv 恢复)。
# ═══════════════════════════════════════════════════════════════════════════


# ── A1-A5: shared_bookkeeping_tables ────────────────────────────────────────


def test_A1_shared_bookkeeping_member_live_in_any_db_is_not_ghost(monkeypatch):
    """A1: 名单成员在任意活库出现都不是 ghost (两库都有 ingest_batch, 无其它表)。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": ["ingest_batch"], "aux": ["ingest_batch"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {"ingest_batch": "infra"})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {
            "shared_bookkeeping_tables": ["ingest_batch"],
            # aux 声明一个不会命中的 pattern, 只是为了让 main 是唯一的 catch-all
            # (_default_registry_db 要求恰好一个) —— 这条与本用例意图无关, 只是
            # 满足路由算法的前置条件。
            "databases": {"main": {}, "aux": {"table_patterns": ["never_matches_*"]}},
        },
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {"ingest_batch": "infra"}},
        "data_access.yaml": {"entities": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == []
    assert drift["orphans"] == []


def test_A2_shared_bookkeeping_non_infra_member_fails_closed(monkeypatch):
    """A2: 名单成员的 layer 不是 infra → RuntimeError (fail-closed, 不需要活库)。"""
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"shared_bookkeeping_tables": ["dim_x"]},
        "data_layers.yaml": {"tables": {"dim_x": "L1_foundation"}},
    }))
    with pytest.raises(RuntimeError, match="infra"):
        builder.catalog_drift()


def test_A3_shared_bookkeeping_member_missing_everywhere_is_orphan(monkeypatch):
    """A3: 名单成员在所有活库都不存在 → orphan `table:*.<name>` (名单成员是墓碑)。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {"ghost_ledger": "infra"})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {
            "shared_bookkeeping_tables": ["ghost_ledger"],
            "databases": {"main": {}},
        },
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {"ghost_ledger": "infra"}},
        "data_access.yaml": {"entities": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["orphans"] == ["table:*.ghost_ledger"]


def test_A4_shared_bookkeeping_absent_key_behaves_exactly_like_before(monkeypatch):
    """A4 (回归锁): 不加 shared_bookkeeping_tables 键 → 与
    test_catalog_drift_reports_ghosts_and_orphans 逐字相同的结果 (不做变异)。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": ["dim_registered", "dim_ghost"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {
        "dim_registered": "L1_foundation", "dim_missing": "L1_foundation",
    })
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"main": {}}},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {
            "dim_registered": "L1_foundation", "dim_missing": "L1_foundation",
        }},
        "data_access.yaml": {"entities": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == ["table:main.dim_ghost"]
    assert drift["orphans"] == ["table:main.dim_missing"]
    assert drift["declared_unbuilt"] == []


def test_A5_real_manifest_has_no_literal_copy_of_shared_names():
    """A5: 真 database_manifest.yaml 里, shared_bookkeeping_tables 的任何名字不再
    出现在任何库的 table_patterns 里 (一个参数只在一处定义, 不留字面量副本)。"""
    raw = yaml.safe_load(
        (builder.CONFIG / "database_manifest.yaml").read_text(encoding="utf-8")
    )
    shared = set(raw.get("shared_bookkeeping_tables") or [])
    assert shared, "real database_manifest.yaml 应该声明 shared_bookkeeping_tables"
    for alias, spec in (raw.get("databases") or {}).items():
        patterns = set((spec or {}).get("table_patterns") or [])
        overlap = shared & patterns
        assert not overlap, f"{alias}.table_patterns 不应再含 shared 名单字面量: {overlap}"


# ── B1-B5: on_demand + ledger → declared_unbuilt ────────────────────────────


def _on_demand_fixture_yaml(*, target_db_path: str, sync_policy: str | None = "on_demand"):
    domain_spec = {
        "source": "fuyao", "api": "v_pool", "target_table": "raw_v_pool",
        "target_db": "main",
    }
    if sync_policy is not None:
        domain_spec["sync_policy"] = sync_policy
    return _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"main": {"path": target_db_path}}},
        "sync_registry.yaml": {
            "defaults": {}, "sources": {}, "domains": {"v_pool": domain_spec},
        },
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
    })


def test_B1_on_demand_never_fetched_no_drop_ledger_is_declared_unbuilt(tmp_path, monkeypatch):
    """B1: on_demand 域、表不存在、目标库 ledger 无 table_drop 行 → declared_unbuilt,
    不在 orphans, checker (间接由 catalog_drift 本身) 视作非漂移。"""
    db_path = tmp_path / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE mart_data_deletion_record (
            table_name VARCHAR, delete_scope VARCHAR
        )
    """)
    conn.close()
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _on_demand_fixture_yaml(target_db_path=str(db_path)))
    drift = builder.catalog_drift()
    assert drift["orphans"] == []
    assert drift["declared_unbuilt"] == ["table:main.raw_v_pool"]


def test_B2_on_demand_but_ledger_has_table_drop_row_is_orphan(tmp_path, monkeypatch):
    """B2: 同 B1 但 ledger 有 (raw_v_pool, table_drop) 行 → 仍是 orphan (曾被删过的表
    不能靠这条规则悄悄绿掉)。"""
    db_path = tmp_path / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE mart_data_deletion_record (
            table_name VARCHAR, delete_scope VARCHAR
        )
    """)
    conn.execute("INSERT INTO mart_data_deletion_record VALUES ('raw_v_pool', 'table_drop')")
    conn.close()
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _on_demand_fixture_yaml(target_db_path=str(db_path)))
    drift = builder.catalog_drift()
    assert drift["orphans"] == ["table:main.raw_v_pool"]
    assert drift["declared_unbuilt"] == []


def test_B3_non_on_demand_missing_table_is_orphan_regardless_of_ledger(tmp_path, monkeypatch):
    """B3: 非 on_demand 域、表不存在 → orphan, ledger 有无都无关 (sync_policy 缺省)。"""
    db_path = tmp_path / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE mart_data_deletion_record (
            table_name VARCHAR, delete_scope VARCHAR
        )
    """)
    conn.close()
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(
        builder, "_load_yaml",
        _on_demand_fixture_yaml(target_db_path=str(db_path), sync_policy=None),
    )
    drift = builder.catalog_drift()
    assert drift["orphans"] == ["table:main.raw_v_pool"]
    assert drift["declared_unbuilt"] == []


def test_B4_on_demand_table_exists_neither_side(tmp_path, monkeypatch):
    """B4 (回归锁): on_demand 域、表存在 (拉过一次) → 两边都不出现。"""
    db_path = tmp_path / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE raw_v_pool (id INTEGER)")
    conn.close()
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": ["raw_v_pool"]})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _on_demand_fixture_yaml(target_db_path=str(db_path)))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == []
    assert drift["orphans"] == []
    assert drift["declared_unbuilt"] == []


def test_B5_ledger_table_itself_missing_is_treated_as_no_rows(tmp_path, monkeypatch):
    """B5: 目标库文件存在但连 mart_data_deletion_record 这张表都没建过 (从未做过
    生命周期删除) → 视作无行, 不 raise, 归 declared_unbuilt。"""
    db_path = tmp_path / "main.duckdb"
    duckdb.connect(str(db_path)).close()  # 建一个空库, 不建 ledger 表
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _on_demand_fixture_yaml(target_db_path=str(db_path)))
    drift = builder.catalog_drift()
    assert drift["orphans"] == []
    assert drift["declared_unbuilt"] == ["table:main.raw_v_pool"]


def test_B6_ledger_probe_fail_open_under_real_write_lock(tmp_path, monkeypatch):
    """B6: `_ledger_has_table_drop` 自己单独开的连接被真实写锁挡住时必须 fail-open
    (raise LiveCatalogUnreachable), 不能让 uncaught duckdb.IOException 裸着冒出来。

    返修 (blocking finding, 2026-09-19) 后本用例改为直接调用
    `_ledger_has_table_drop`, 不再经过 `catalog_drift()`——`catalog_drift()` 现在
    由调用方 `_split_declared_unbuilt` 逐域 catch 这个异常 (见 test_B7), 不会再让它
    冒泡出来, 所以"catalog_drift() 会抛 LiveCatalogUnreachable" 已经不是
    `_ledger_has_table_drop` 这一层该断言的行为; 这条用例专注隔离
    `_ledger_has_table_drop` 自己的 fail-open 包法有没有退化。用真实第二进程持锁,
    不 mock RuntimeError——mock 只能证明分支存在, 证明不了 DuckDB 锁语义真的触发它。

    变异: 把 builder.py 里 `_ledger_has_table_drop` 的 `try: conn = _audit_connect(...)`
    外层 `except Exception as exc: raise LiveCatalogUnreachable(...)` 包法去掉 (还原
    成裸 `conn = _audit_connect(str(db_path))`) → 本用例改为断言会抛出裸
    duckdb.IOException, 原断言 (raises LiveCatalogUnreachable) 必然红。
    """
    db_path = tmp_path / "main.duckdb"
    duckdb.connect(str(db_path)).close()

    held_flag = tmp_path / "lock_held.flag"
    release_flag = tmp_path / "lock_release.flag"
    script = (
        "import duckdb, time, os\n"
        f"conn = duckdb.connect(r'{db_path}', read_only=False)\n"
        f"open(r'{held_flag}', 'w').close()\n"
        f"while not os.path.exists(r'{release_flag}'):\n"
        "    time.sleep(0.05)\n"
        "conn.close()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 10
        while not held_flag.exists():
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("lock holder subprocess never signalled in time")
            if proc.poll() is not None:
                raise RuntimeError(f"lock holder subprocess died early: {proc.stdout.read()}")
            time.sleep(0.05)

        # 独立确认: 常规只读连接此刻确实会被拒 (证明锁真的生效, 不是空气锁)。
        with pytest.raises(Exception, match="[Ll]ock"):
            duckdb.connect(str(db_path), read_only=True)

        monkeypatch.setenv("CHUNKYMONKEY_AUDIT_LOCK_TIMEOUT", "1")
        monkeypatch.setattr(
            builder, "_load_yaml", _on_demand_fixture_yaml(target_db_path=str(db_path))
        )
        with pytest.raises(builder.LiveCatalogUnreachable):
            builder._ledger_has_table_drop("main", "raw_v_pool")
    finally:
        release_flag.write_text("release", encoding="utf-8")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_B7_ghost_survives_when_unrelated_on_demand_probe_is_locked(tmp_path, monkeypatch):
    """B7 (blocking finding 返修回归, 2026-09-19, builder.py:339): 活库扫描已经真实
    产出至少一个 ghost 之后, 另一个与该 ghost 毫不相关的 on_demand 域目标库
    ("locked_db") 被写锁占住 —— 之前的写法里 `_ledger_has_table_drop` 抛出的
    LiveCatalogUnreachable 会一路冒泡穿出 `_split_declared_unbuilt` 与
    `catalog_drift()`, 调用方 (check_lineage_catalog_drift.py) 据此把已经算好的
    ghost 整体清空成 UNVERIFIED/exit 0。断言 catalog_drift() 现在正常返回
    (不抛异常), 已经算出来的 ghost 还在, 探测不到的 on_demand 目标表保持 orphan
    (不静默判成 declared_unbuilt——"查不清"不等于"从没删过")。

    隔离手法: "main" 库 (ghost 所在库) 的活库扫描用 monkeypatch 直接给结果, 不真的
    连库; 真实第二进程只对 "locked_db" (on_demand 域的目标库) 持锁, 与 "main" 完全
    是两个物理文件——证明锁定的是不相关的库, 不是 ghost 所在的库自己。

    变异: 把 `_split_declared_unbuilt` 里新加的 `except LiveCatalogUnreachable:
    continue` 去掉 (还原成裸调用 `_ledger_has_table_drop(target_db, target)`) →
    本用例断言的"不抛异常"必然红 (变回抛 builder.LiveCatalogUnreachable, ghost
    永远看不到)。
    """
    locked_db_path = tmp_path / "locked.duckdb"
    duckdb.connect(str(locked_db_path)).close()

    held_flag = tmp_path / "lock_held.flag"
    release_flag = tmp_path / "lock_release.flag"
    script = (
        "import duckdb, time, os\n"
        f"conn = duckdb.connect(r'{locked_db_path}', read_only=False)\n"
        f"open(r'{held_flag}', 'w').close()\n"
        f"while not os.path.exists(r'{release_flag}'):\n"
        "    time.sleep(0.05)\n"
        "conn.close()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 10
        while not held_flag.exists():
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("lock holder subprocess never signalled in time")
            if proc.poll() is not None:
                raise RuntimeError(f"lock holder subprocess died early: {proc.stdout.read()}")
            time.sleep(0.05)

        # 独立确认: 常规只读连接此刻确实会被拒 (证明锁真的生效, 不是空气锁)。
        with pytest.raises(Exception, match="[Ll]ock"):
            duckdb.connect(str(locked_db_path), read_only=True)

        monkeypatch.setenv("CHUNKYMONKEY_AUDIT_LOCK_TIMEOUT", "1")
        # main 的活库扫描直接给结果 (不真的连库): dim_ghost_survivor 没被任何登记源
        # 声明 -> 真 ghost。
        monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": ["dim_ghost_survivor"]})
        monkeypatch.setattr(builder, "_table_layers", lambda: {})
        monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
            "database_manifest.yaml": {"databases": {
                "main": {},  # catch-all: 唯一没有 table_patterns 的库
                "locked_db": {"path": str(locked_db_path), "table_patterns": ["raw_v_pool"]},
            }},
            "sync_registry.yaml": {
                "defaults": {}, "sources": {},
                "domains": {"v_pool": {
                    "source": "fuyao", "api": "v_pool", "target_table": "raw_v_pool",
                    "target_db": "locked_db", "sync_policy": "on_demand",
                }},
            },
            "data_layers.yaml": {"tables": {}},
            "data_access.yaml": {"entities": {}},
        }))

        drift = builder.catalog_drift()
    finally:
        release_flag.write_text("release", encoding="utf-8")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    assert drift["ghosts"] == ["table:main.dim_ghost_survivor"]
    assert drift["orphans"] == ["table:locked_db.raw_v_pool"]
    assert drift["declared_unbuilt"] == []


# ── C1-C4: legacy_raw_plane.yaml 第四声明源 ─────────────────────────────────


def test_C1_legacy_raw_plane_table_declared_only_there_and_live_is_clean(monkeypatch):
    """C1: 只在 legacy 声明 raw_tushare_k3 (不在 data_layers/sync_registry); manifest
    vendor_raw.table_patterns: [raw_tushare_*]; 库有表 → 两边空。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": [], "vendor_raw": ["raw_tushare_k3"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {}, "vendor_raw": {"table_patterns": ["raw_tushare_*"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
        "legacy_raw_plane.yaml": {"tables": {"raw_tushare_k3": {"role": "retired"}}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == []
    assert drift["orphans"] == []


def test_C2_legacy_raw_plane_table_declared_but_not_live_is_orphan(monkeypatch):
    """C2: legacy 键 + 活库无 → orphan。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {"main": [], "vendor_raw": []})
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {}, "vendor_raw": {"table_patterns": ["raw_tushare_*"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
        "legacy_raw_plane.yaml": {"tables": {"raw_tushare_k3": {"role": "retired"}}},
    }))
    drift = builder.catalog_drift()
    assert drift["orphans"] == ["table:vendor_raw.raw_tushare_k3"]


def test_C3_live_table_absent_from_legacy_plane_stays_ghost(monkeypatch):
    """C3 (回归锁): 活库有 + legacy 空 (无该键) + 其它源也无 → ghost, 与今天行为相同。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": [], "vendor_raw": ["raw_tushare_k3"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {}, "vendor_raw": {"table_patterns": ["raw_tushare_*"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
        "legacy_raw_plane.yaml": {"tables": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == ["table:vendor_raw.raw_tushare_k3"]


def test_C4_legacy_raw_plane_file_absent_behaves_like_today(monkeypatch):
    """C4 (回归锁): legacy_raw_plane.yaml 整个键都不给 (等价文件缺失) → 与今天相同。"""
    monkeypatch.setattr(builder, "_live_tables_by_db", lambda: {
        "main": [], "vendor_raw": ["raw_tushare_k3"],
    })
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {
            "main": {}, "vendor_raw": {"table_patterns": ["raw_tushare_*"]},
        }},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
        # 故意不给 "legacy_raw_plane.yaml" 这个键 -> _fake_yaml_for_registry 回退 {}
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == ["table:vendor_raw.raw_tushare_k3"]


# ── E1: 真配置不变量 (不 monkeypatch, 不开生产库) ───────────────────────────


def test_E1_real_config_invariants_for_shared_and_legacy_sources():
    """E1: 真 shared_bookkeeping_tables 每个名字在真 data_layers.yaml 是 infra；
    真 legacy_raw_plane.tables 每个键匹配 raw_tushare_* 且按真 database_manifest
    路由到 tushare_raw；每个 role=retired 键 ∉ 真 sync_registry target_table 集合。
    """
    manifest_raw = yaml.safe_load(
        (builder.CONFIG / "database_manifest.yaml").read_text(encoding="utf-8")
    )
    data_layers_raw = yaml.safe_load(
        (builder.CONFIG / "data_layers.yaml").read_text(encoding="utf-8")
    )
    legacy_raw = yaml.safe_load(
        (builder.CONFIG / "legacy_raw_plane.yaml").read_text(encoding="utf-8")
    )
    sync_registry_raw = yaml.safe_load(
        (builder.CONFIG / "sync_registry.yaml").read_text(encoding="utf-8")
    )

    layers = data_layers_raw.get("tables") or {}
    shared = manifest_raw.get("shared_bookkeeping_tables") or []
    assert shared, "real database_manifest.yaml 应该声明 shared_bookkeeping_tables"
    for name in shared:
        assert layers.get(name) == "infra", f"{name}: 在 data_layers.yaml 里必须是 infra"

    patterns_by_db = {
        alias: list((spec or {}).get("table_patterns") or [])
        for alias, spec in (manifest_raw.get("databases") or {}).items()
    }
    legacy_tables = legacy_raw.get("tables") or {}
    assert legacy_tables, "real legacy_raw_plane.yaml 应该有非空 tables"
    for name, meta in legacy_tables.items():
        assert name.startswith("raw_tushare_"), f"{name}: legacy 键必须是 raw_tushare_*"
        routed = builder._match_manifest_db(name, patterns_by_db)
        assert routed == "tushare_raw", f"{name}: 应路由到 tushare_raw, 实得 {routed}"

    sync_targets = {
        (spec or {}).get("target_table")
        for spec in (sync_registry_raw.get("domains") or {}).values()
    }
    for name, meta in legacy_tables.items():
        if (meta or {}).get("role") != "retired":
            continue
        assert name not in sync_targets, (
            f"{name}: role=retired 但仍是 sync_registry 的 target_table"
        )


# ── H1: `_` 前缀不再天然隐身 (§2.4) ──────────────────────────────────────────


def test_H1_underscore_prefixed_table_no_longer_hides_from_ghosts(tmp_path, monkeypatch):
    """H1: fixture 库有 `_scratch` 表且无任何登记源声明它 → 它必须出现在 ghosts
    (2026-06-26 加的"建/即删瞬态锁探针"豁免已删——`_lock_probe` 等表现在没有
    creator, 已从"瞬态巧合命名"退化成"谁都能借来永久隐身"的洞)。"""
    db_path = tmp_path / "main.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE _scratch (id INTEGER)")
    conn.close()
    monkeypatch.setattr(builder, "_table_layers", lambda: {})
    monkeypatch.setattr(builder, "_load_yaml", _fake_yaml_for_registry({
        "database_manifest.yaml": {"databases": {"main": {"path": str(db_path)}}},
        "sync_registry.yaml": {"defaults": {}, "sources": {}, "domains": {}},
        "data_layers.yaml": {"tables": {}},
        "data_access.yaml": {"entities": {}},
    }))
    drift = builder.catalog_drift()
    assert drift["ghosts"] == ["table:main._scratch"]
    assert drift["orphans"] == []


# --- 集成: 真实 build (确定性 + killer 用例) ---
def test_real_build_invariants_and_determinism():
    g = build_lineage_graph()
    assert len(g.nodes_of_kind("table")) > 0
    assert len([e for e in g.edges if e.kind == "acquire"]) > 0
    assert len([e for e in g.edges if e.kind == "consume"]) > 0
    # 确定性: 连跑两次图体逐字一致 (drift 门前提, mythos §13)
    g2 = build_lineage_graph()
    assert json.dumps(g.to_dict(generated_at=None), sort_keys=True, ensure_ascii=False) == \
           json.dumps(g2.to_dict(generated_at=None), sort_keys=True, ensure_ascii=False)


def test_real_impact_known_table_has_consumers():
    """price_kline_qfq_tushare (回测主源) 必有消费方 — 真实 fan-in 非空。"""
    g = build_lineage_graph()
    imp = impact(g, "price_kline_qfq_tushare")
    if not imp["exists"]:
        pytest.skip("price_kline_qfq_tushare 不在当前库 (env 无 market.duckdb)")
    assert imp["consumer_count"] > 0
