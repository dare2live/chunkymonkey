"""M5-T2 血缘路由中枢单测 — 合成图测 query 逻辑 + 真实 build 集成测 + 确定性 (drift 门前提)。"""
from __future__ import annotations

import json

import pytest

from services.lineage import build_lineage_graph, dead_tables, impact, provenance
from services.lineage.model import Edge, LineageGraph, Node


def _synthetic() -> LineageGraph:
    """source(tushare.x)→table(raw_x)[acquire]; raw_x→consumer(svc.py)[consume]; raw_dead 无消费。"""
    g = LineageGraph()
    g.add_node(Node("source:tushare.x", "source_interface", {"source": "tushare", "api": "x"}))
    g.add_node(Node("table:raw_x", "table", {"db": "tushare_raw", "layer": "L0_source", "status": "active"}))
    g.add_node(Node("table:raw_dead", "table", {"db": "tushare_raw", "layer": "L0_source", "status": "active"}))
    g.add_node(Node("consumer:backend/services/svc.py", "consumer",
                    {"path": "backend/services/svc.py", "ctype": "service"}))
    g.add_edge(Edge("source:tushare.x", "table:raw_x", "acquire", {"pit_anchor": "trade_date"}))
    g.add_edge(Edge("table:raw_x", "consumer:backend/services/svc.py", "consume"))
    return g


# --- model 确定性 ---
def test_graph_deterministic_serialization():
    g = _synthetic()
    d1 = json.dumps(g.to_dict(generated_at=None), sort_keys=True, ensure_ascii=False)
    # 重建 (不同插入序) 应得同序列化
    g2 = LineageGraph()
    g2.add_node(Node("consumer:backend/services/svc.py", "consumer",
                     {"ctype": "service", "path": "backend/services/svc.py"}))
    g2.add_node(Node("table:raw_dead", "table", {"status": "active", "db": "tushare_raw", "layer": "L0_source"}))
    g2.add_node(Node("table:raw_x", "table", {"layer": "L0_source", "db": "tushare_raw", "status": "active"}))
    g2.add_node(Node("source:tushare.x", "source_interface", {"api": "x", "source": "tushare"}))
    g2.add_edge(Edge("table:raw_x", "consumer:backend/services/svc.py", "consume"))
    g2.add_edge(Edge("source:tushare.x", "table:raw_x", "acquire", {"pit_anchor": "trade_date"}))
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
    assert impact(g, "table:raw_x") == impact(g, "raw_x")


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


# --- query: dead (无消费方) ---
def test_dead_detects_unconsumed_table():
    g = _synthetic()
    dead = dead_tables(g)
    names = [d["table"] for d in dead]
    assert "raw_dead" in names      # 无消费 = 死
    assert "raw_x" not in names     # 有 service 消费 = 活


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
