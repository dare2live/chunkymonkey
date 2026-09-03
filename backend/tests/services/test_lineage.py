"""M5-T2 血缘路由中枢单测 — 合成图测 query 逻辑 + 真实 build 集成测 + 确定性 (drift 门前提)。"""
from __future__ import annotations

import json

import pytest

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
        "_duck_connect",
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
