"""血缘查询 — 路由中枢的 killer use cases (设计 §4)。

- impact(table): 删/迁前自动 fan-in (替代手 grep, 根治 tdx 迁移漏判)
- provenance(table): 溯源到采集接口 + PIT 锚
- dead_tables(): 无消费方的表 (已落库未用 = 停采候选 / 档B 待挖)
"""
from __future__ import annotations

from typing import Any

from services.lineage.model import LineageGraph


def _norm(table: str) -> str:
    return table if table.startswith("table:") else f"table:{table}"


def impact(graph: LineageGraph, table: str) -> dict[str, Any]:
    """删/改 <table> 影响的全部消费方 (fan-in), 按 ctype 分组。"""
    tid = _norm(table)
    node = graph.node(tid)
    consumers: dict[str, list[str]] = {}
    for e in graph.edges_from(tid, kind="consume"):
        c = graph.node(e.dst)
        ctype = (c.attrs.get("ctype") if c else None) or "other"
        path = (c.attrs.get("path") if c else e.dst) or e.dst
        consumers.setdefault(ctype, []).append(path)
    for k in consumers:
        consumers[k] = sorted(set(consumers[k]))
    total = sum(len(v) for v in consumers.values())
    return {
        "table": tid.split(":", 1)[1],
        "exists": node is not None,
        "status": (node.attrs.get("status") if node else "unknown"),
        "db": (node.attrs.get("db") if node else None),
        "layer": (node.attrs.get("layer") if node else None),
        "serve_entity": (node.attrs.get("serve_entity") if node else None),
        "consumer_count": total,
        "consumers_by_type": consumers,
    }


def provenance(graph: LineageGraph, table: str) -> dict[str, Any]:
    """<table> 从哪采集来 + PIT 锚 (溯源)。"""
    tid = _norm(table)
    node = graph.node(tid)
    sources = []
    for e in graph.edges_to(tid, kind="acquire"):
        s = graph.node(e.src)
        sources.append({
            "source_interface": e.src.split(":", 1)[1] if ":" in e.src else e.src,
            "source": (s.attrs.get("source") if s else None),
            "api": (s.attrs.get("api") if s else None),
            "pit_anchor": e.attrs.get("pit_anchor", ""),
            "grain": e.attrs.get("grain", []),
        })
    return {
        "table": tid.split(":", 1)[1],
        "exists": node is not None,
        "db": (node.attrs.get("db") if node else None),
        "layer": (node.attrs.get("layer") if node else None),
        "acquired_from": sorted(sources, key=lambda x: x["source_interface"]),
        "acquired": len(sources) > 0,
    }


def dead_tables(graph: LineageGraph) -> list[dict[str, Any]]:
    """无任何消费方的 live 派生表 (已落库未用) — 停采候选 / 档B 待挖 backlog。

    排除: declared_not_live (未回填的声明表, 非"采了没用")。
    **L0 源永不"死" (2026-06-26 修, 反例: raw_tushare_trade_cal/income 被误判死)**: 有 acquire 边
    (从 vendor 同步) 或 raw_ 前缀 = 源-of-record, re-sync 重建, retention=permanent — "能删必删"
    只适用派生表 (mart/fact/dim 加工层), 不适用 L0 vendor 镜像。L0 真消费方常经 SERVE entity 别名
    (dossier.get("fundamentals")) 对表名 grep 不可见, 不能据"无直接 grep 消费"判其死。
    """
    out = []
    for n in graph.nodes_of_kind("table"):
        if n.attrs.get("status") == "declared_not_live":
            continue
        tname = n.id.split(":", 1)[1]
        # L0 源 (有 vendor 采集边 或 raw_ 镜像前缀) 不进死表候选 — 永不删类
        if graph.edges_to(n.id, kind="acquire") or tname.startswith("raw_"):
            continue
        consumers = graph.edges_from(n.id, kind="consume")
        # 只被 config/test 引用 (无 service/script/router 真消费) 也算 "未真正消费" 的弱信号
        real = [e for e in consumers
                if (graph.node(e.dst).attrs.get("ctype") if graph.node(e.dst) else "") in
                ("service", "script", "router", "frontend")]
        if not real:
            out.append({
                "table": n.id.split(":", 1)[1],
                "db": n.attrs.get("db"),
                "layer": n.attrs.get("layer"),
                "ref_count": len(consumers),   # 0 = 全无引用; >0 = 仅 config/test 引用
            })
    return sorted(out, key=lambda x: (x["db"] or "", x["table"]))
