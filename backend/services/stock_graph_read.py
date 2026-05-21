"""股票图谱 read service (Project D MVP).

2026-05-22 用户新加: 主项目股票列表加 multi-tag + 关联弹窗, 找潜在关联 (产业链/龙一龙二/共振).
基于 Perception 7 mart (`mart_market_perception_*_daily`) + 主项目 `dim_stock_tdx_industry`.

物理边界:
- 仅 UI 查询层, **不接 ranker / panel / paper_sim / champion**
- 跟 Perception 物理隔离 — Perception 写 mart, 这里读 mart
- read_only=True 连接

设计 (跟 workbench_*_read pattern 一致):
- `get_stock_tags(conn, stock_code, snapshot_date=None)` — 返回该股 N 维标签
- `get_stock_related(conn, stock_code, snapshot_date=None, limit=20)` — 返回关联股票

标签维度 (从 mart_market_perception_stock_context_daily 24 cols 直接抽 + 主项目 dim 表):
- 行业 (industry, 申万一级)
- 主题 + 阶段 (theme_name, lifecycle_stage)
- 龙头身份 (leader/follower, 来自 leader_follower mart)
- 上下文状态 (context_state, e.g. 资金强但价格未反应)
- 风格 (style_bias)
- 拥挤 (crowding_risk_score 分档)
- 数据完整度 (data_completeness_score)

关联类型:
- same_industry (同申万一级)
- same_theme (同主题, 来自 theme_daily)
- leader_follower (5 日相对强弱边, 来自 leader_follower_daily)
- under_reaction_cohort (同"资金强价未反应"组, 来自 under_reaction_daily)
"""
from __future__ import annotations

from typing import Any


def _latest_snapshot_date(conn, table: str) -> str | None:
    try:
        row = conn.execute(f"SELECT MAX(snapshot_date) FROM {table}").fetchone()
        return str(row[0]) if row and row[0] is not None else None
    except Exception:  # rule-compliance: ok evidence=table-may-not-exist-graceful-fallback
        return None


def _table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()
        return bool(row and int(row[0]) > 0)
    except Exception:  # rule-compliance: ok evidence=metadata-query-fallback
        return False


def get_stock_tags(conn, stock_code: str, snapshot_date: str | None = None) -> dict:
    """Return multi-tag dict for stock_code.

    Tags pulled from:
      - dim_stock_tdx_industry (industry, always available)
      - mart_market_perception_stock_context_daily (theme/lifecycle/leader_follow/style/crowding, 6 days history MVP)
    """
    # Resolve snapshot_date
    if snapshot_date is None and _table_exists(conn, "mart_market_perception_stock_context_daily"):
        snapshot_date = _latest_snapshot_date(conn, "mart_market_perception_stock_context_daily")

    # Industry (主项目 dim, always available)
    industry: dict[str, Any] = {}
    if _table_exists(conn, "dim_stock_tdx_industry"):
        row = conn.execute(
            """
            SELECT tdx_l1_name, tdx_l2_name, tdx_l3_name, tdx_l1
              FROM dim_stock_tdx_industry
             WHERE stock_code = ?
             LIMIT 1
            """,
            [stock_code],
        ).fetchone()
        if row:
            industry = {
                "tdx_l1_name": row[0],
                "tdx_l2_name": row[1],
                "tdx_l3_name": row[2],
                "tdx_l1_code": row[3],
            }

    # Stock name (从 dim_active_a_stock)
    stock_name = None
    if _table_exists(conn, "dim_active_a_stock"):
        row = conn.execute(
            "SELECT stock_name FROM dim_active_a_stock WHERE stock_code = ? LIMIT 1",
            [stock_code],
        ).fetchone()
        if row:
            stock_name = row[0]

    # Perception context (聚合 7 mart 的 stock-level snapshot)
    context: dict[str, Any] = {}
    if snapshot_date and _table_exists(conn, "mart_market_perception_stock_context_daily"):
        row = conn.execute(
            """
            SELECT context_score, context_state, theme_name, lifecycle_stage,
                   leader_follow_score, leader_stock_code, under_reaction_score,
                   fund_anomaly_score, style_bias, style_rotation_score,
                   crowding_risk_score, overheat_reversal_risk,
                   data_completeness_score, missing_context_fields,
                   pit_cutoff_date, source_engines, built_at
              FROM mart_market_perception_stock_context_daily
             WHERE stock_code = ? AND snapshot_date = ?
             LIMIT 1
            """,
            [stock_code, snapshot_date],
        ).fetchone()
        if row:
            context = {
                "context_score": float(row[0]) if row[0] is not None else None,
                "context_state": row[1],
                "theme_name": row[2],
                "lifecycle_stage": row[3],
                "leader_follow_score": float(row[4]) if row[4] is not None else None,
                "leader_stock_code": row[5],
                "under_reaction_score": float(row[6]) if row[6] is not None else None,
                "fund_anomaly_score": float(row[7]) if row[7] is not None else None,
                "style_bias": row[8],
                "style_rotation_score": float(row[9]) if row[9] is not None else None,
                "crowding_risk_score": float(row[10]) if row[10] is not None else None,
                "overheat_reversal_risk": float(row[11]) if row[11] is not None else None,
                "data_completeness_score": float(row[12]) if row[12] is not None else None,
                "missing_context_fields": row[13],
                "pit_cutoff_date": str(row[14]) if row[14] is not None else None,
                "source_engines": row[15],
                "built_at": str(row[16]) if row[16] is not None else None,
            }

    # Build tags list (UI-ready chips)
    tags: list[dict[str, Any]] = []
    if industry.get("tdx_l1_name"):
        tags.append({
            "kind": "industry",
            "label": industry["tdx_l1_name"],
            "sub_label": industry.get("tdx_l2_name"),
            "source": "dim_stock_tdx_industry",
        })
    if context.get("theme_name"):
        tags.append({
            "kind": "theme",
            "label": context["theme_name"],
            "sub_label": context.get("lifecycle_stage"),
            "score": context.get("under_reaction_score"),  # placeholder, theme_score from theme_daily preferred
            "source": "mart_market_perception_theme_daily",
        })
    if context.get("context_state"):
        tags.append({
            "kind": "context",
            "label": context["context_state"],
            "score": context.get("context_score"),
            "source": "mart_market_perception_stock_context_daily",
        })
    if context.get("leader_stock_code"):
        tags.append({
            "kind": "leader_follow",
            "label": f"跟随 {context['leader_stock_code']}",
            "score": context.get("leader_follow_score"),
            "source": "mart_market_perception_leader_follower_daily",
        })
    if context.get("style_bias"):
        tags.append({
            "kind": "style",
            "label": context["style_bias"],
            "score": context.get("style_rotation_score"),
            "source": "mart_market_perception_style_daily",
        })
    if context.get("crowding_risk_score") is not None:
        crowd = context["crowding_risk_score"]
        crowd_label = "高拥挤" if crowd > 0.6 else ("中拥挤" if crowd > 0.4 else "低拥挤")
        tags.append({
            "kind": "crowding",
            "label": crowd_label,
            "score": crowd,
            "source": "mart_market_perception_stock_context_daily",
        })

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "snapshot_date": snapshot_date,
        "industry": industry,
        "context": context,
        "tags": tags,
        "_meta": {
            "tag_count": len(tags),
            "perception_data_available": bool(context),
        },
    }


def get_stock_related(conn, stock_code: str, snapshot_date: str | None = None, limit: int = 20) -> dict:
    """Return related stocks list for stock_code.

    Relation types:
      - same_industry (同申万一级, 默认权重 1.0)
      - leader_or_follower (来自 mart_market_perception_leader_follower_daily, weighted by diffusion_score)
      - same_theme (来自 mart_market_perception_theme_daily 同主题股票)
    """
    if snapshot_date is None and _table_exists(conn, "mart_market_perception_leader_follower_daily"):
        snapshot_date = _latest_snapshot_date(conn, "mart_market_perception_leader_follower_daily")

    related: list[dict[str, Any]] = []

    # 1. Same industry (主项目 dim_stock_tdx_industry, 不依赖 perception)
    if _table_exists(conn, "dim_stock_tdx_industry"):
        rows = conn.execute(
            """
            WITH target AS (
              SELECT tdx_l1 FROM dim_stock_tdx_industry WHERE stock_code = ? LIMIT 1
            )
            SELECT s.stock_code, s.stock_name, d.tdx_l1_name
              FROM dim_stock_tdx_industry d
              JOIN dim_active_a_stock s ON s.stock_code = d.stock_code
              JOIN target t ON t.tdx_l1 = d.tdx_l1
             WHERE d.stock_code != ?
             LIMIT ?
            """,
            [stock_code, stock_code, limit],
        ).fetchall()
        for r in rows:
            related.append({
                "stock_code": r[0],
                "stock_name": r[1],
                "industry": r[2],
                "relation": "same_industry",
                "weight": 1.0,
                "source": "dim_stock_tdx_industry",
            })

    # 2. Leader/follower edges
    if snapshot_date and _table_exists(conn, "mart_market_perception_leader_follower_daily"):
        rows = conn.execute(
            """
            SELECT
              CASE WHEN leader_stock_code = ? THEN follower_stock_code ELSE leader_stock_code END AS other,
              CASE WHEN leader_stock_code = ? THEN 'leader_of' ELSE 'follower_of' END AS rel,
              theme_name,
              diffusion_score
              FROM mart_market_perception_leader_follower_daily
             WHERE (leader_stock_code = ? OR follower_stock_code = ?)
               AND snapshot_date = ?
             ORDER BY diffusion_score DESC
             LIMIT ?
            """,
            [stock_code, stock_code, stock_code, stock_code, snapshot_date, limit],
        ).fetchall()
        for r in rows:
            related.append({
                "stock_code": r[0],
                "stock_name": None,  # 可后置 JOIN 补
                "theme": r[2],
                "relation": r[1],
                "weight": float(r[3]) if r[3] is not None else None,
                "source": "mart_market_perception_leader_follower_daily",
            })

    return {
        "stock_code": stock_code,
        "snapshot_date": snapshot_date,
        "related": related,
        "_meta": {
            "related_count": len(related),
            "by_relation": {
                "same_industry": sum(1 for x in related if x["relation"] == "same_industry"),
                "leader_of": sum(1 for x in related if x["relation"] == "leader_of"),
                "follower_of": sum(1 for x in related if x["relation"] == "follower_of"),
            },
        },
    }


def get_stock_graph(conn, stock_code: str, snapshot_date: str | None = None) -> dict:
    """Unified entry: 返回 stock_graph (tags + related)."""
    tags = get_stock_tags(conn, stock_code, snapshot_date)
    related = get_stock_related(conn, stock_code, snapshot_date or tags.get("snapshot_date"))
    return {
        "stock_code": stock_code,
        "stock_name": tags.get("stock_name"),
        "snapshot_date": tags.get("snapshot_date"),
        "tags": tags["tags"],
        "industry": tags["industry"],
        "context": tags["context"],
        "related": related["related"],
        "_meta": {
            **tags["_meta"],
            **related["_meta"],
        },
    }
