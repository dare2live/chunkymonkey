"""血缘图数据模型 — 确定性序列化 (drift 门可逐字比对, mythos §13)。

节点 kind: source_interface | table | consumer  (T2 范围; raw_field/derived_field/display 押后 T3/T4)
边 kind: acquire (source→table) | consume (table→consumer)

确定性保证: to_dict 输出 nodes 按 id 排序、edges 按 (kind,src,dst) 排序、attrs 键排序;
meta.generated_at 单独存放, drift 比对时剔除 (连跑两次图体必逐字一致)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    id: str                      # 全局唯一, 形如 'table:raw_tushare_moneyflow' / 'source:tushare.moneyflow'
    kind: str                    # source_interface | table | consumer
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "attrs": _sorted_attrs(self.attrs)}


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str                    # acquire | consume
    attrs: dict[str, Any] = field(default_factory=dict)

    def _key(self) -> tuple:
        return (self.kind, self.src, self.dst)

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "kind": self.kind, "attrs": _sorted_attrs(self.attrs)}


def _sorted_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """键排序 + 列表内排序 (确定性); 标量原样。"""
    out: dict[str, Any] = {}
    for k in sorted(attrs):
        v = attrs[k]
        if isinstance(v, (list, tuple, set)):
            try:
                out[k] = sorted(v)
            except TypeError:
                out[k] = list(v)
        else:
            out[k] = v
    return out


class LineageGraph:
    """节点 + 边集合; 唯一 id 去重; 确定性 to_dict。"""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple, Edge] = {}

    # --- 构建 ---
    def add_node(self, node: Node) -> None:
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
        else:
            # 合并 attrs (后写补充, 不覆盖已有非空键), kind 冲突 = 真相源不一致, 留首个 + 记 conflict
            merged = dict(existing.attrs)
            for k, v in node.attrs.items():
                if k not in merged or merged[k] in (None, "", [], {}):
                    merged[k] = v
            self._nodes[node.id] = Node(id=node.id, kind=existing.kind, attrs=merged)

    def add_edge(self, edge: Edge) -> None:
        self._edges[edge._key()] = edge

    # --- 访问 ---
    @property
    def nodes(self) -> list[Node]:
        return [self._nodes[k] for k in sorted(self._nodes)]

    @property
    def edges(self) -> list[Edge]:
        return [self._edges[k] for k in sorted(self._edges)]

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def edges_from(self, node_id: str, kind: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id and (kind is None or e.kind == kind)]

    def edges_to(self, node_id: str, kind: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.dst == node_id and (kind is None or e.kind == kind)]

    def nodes_of_kind(self, kind: str) -> list[Node]:
        return [n for n in self.nodes if n.kind == kind]

    # --- 序列化 (确定性) ---
    def to_dict(self, *, generated_at: str | None = None) -> dict[str, Any]:
        return {
            "meta": {
                "version": 1,
                "generated_at": generated_at,   # drift 比对剔除此键
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
            },
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LineageGraph":
        g = cls()
        for n in d.get("nodes", []):
            g.add_node(Node(id=n["id"], kind=n["kind"], attrs=dict(n.get("attrs") or {})))
        for e in d.get("edges", []):
            g.add_edge(Edge(src=e["src"], dst=e["dst"], kind=e["kind"], attrs=dict(e.get("attrs") or {})))
        return g
