"""M5 血缘路由中枢 (lineage routing hub) — 数据模块的字典 + 总指挥 (T2: acquire+consume 段)。

owner: analysis/data_lineage_routing_hub_design_20260624.md (设计 spec) + goal.md Active Priority Board 阶段三。

T2 范围 (用户 2026-06-25 批准先造): 缝合 sync_registry (acquire 源→表) + data_access (SERVE consume)
+ 确定性 FROM/JOIN/引用扫描 (表→消费方 fan-in) → 可查 lineage 图 (impact/provenance/dead)。
根治痛点: 删/迁表前自动 fan-in (替代手 grep, 本 session tdx 迁移反复手工漏判的根因)。

不是新真相源 — 是既有 registry + 代码的**投影/缝合** (设计原则#1 派生不手维护)。
transform (字段→组合字段, T3) + display (消费方→展示, T4) 段押后, 不在 T2。
"""
from services.lineage.model import LineageGraph, Node, Edge  # noqa: F401
from services.lineage.builder import build_lineage_graph  # noqa: F401
from services.lineage.query import impact, provenance, dead_tables  # noqa: F401
