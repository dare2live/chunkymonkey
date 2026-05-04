"""数据健康看板 API — W0.

GET /api/data_health/snapshot
    返回最近一次快照的全量摘要 (前端 view-data-mgmt 顶部健康条 + Tab 4 主输入)
GET /api/data_health/snapshot/by_layer
    按 layer 分组的统计
GET /api/data_health/snapshot/red
    仅 red 列表 (issue 详情)
GET /api/data_health/asset/{table_name}
    单表的完整画像 (dim_data_asset 声明 + mart_data_health 最新快照 + 历史 sparkline)
GET /api/data_health/sources
    数据源总览 (Tab 1 主输入): 按 upstream_source + source_tier 聚合

数据来源:
  - dim_data_asset: 注册声明 (manual/auto seed)
  - mart_data_health: 每日快照 (data_health_snapshot.py 脚本写)

无写操作; 仅读快照.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from services.db import get_conn

logger = logging.getLogger("cm-api")
router = APIRouter(prefix="/data_health", tags=["data_health"])


def _latest_snapshot_at(con) -> Optional[str]:
    row = con.execute(
        "SELECT MAX(snapshot_at) FROM mart_data_health"
    ).fetchone()
    return row[0] if row and row[0] else None


@router.get("/snapshot")
def get_snapshot() -> dict[str, Any]:
    """全局健康条 + Tab 4 主输入. 单端点替代 dashboard 散在 5 处的拼装."""

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        if snap_at is None:
            return {
                "snapshot_at": None,
                "summary": {"total": 0, "green": 0, "yellow": 0, "red": 0},
                "by_layer": {},
                "red_list": [],
                "fallback_active": [],
                "note": "no snapshot yet — run backend/scripts/data_health_snapshot.py",
            }

        # 主表查询 (join 声明 + 快照)
        rows = con.execute("""
            SELECT
                d.table_name, d.layer, d.purpose, d.writer_module,
                d.upstream_source, d.source_tier, d.expected_freshness, d.sla_hours,
                m.row_count, m.last_data_date, m.freshness_hours, m.freshness_ok,
                m.severity, m.issue_summary, m.source_tier_dist
            FROM dim_data_asset d
            LEFT JOIN mart_data_health m
              ON m.table_name = d.table_name AND m.snapshot_at = ?
            ORDER BY d.layer, d.table_name
        """, (snap_at,)).fetchall()

        items = []
        by_layer = defaultdict(lambda: {"green": 0, "yellow": 0, "red": 0, "unknown": 0, "total": 0})
        severity_total = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
        red_list = []
        fallback_active = []

        for r in rows:
            d = dict(r)
            sev = d.get("severity") or "unknown"
            severity_total[sev] = severity_total.get(sev, 0) + 1
            by_layer[d["layer"]][sev] = by_layer[d["layer"]].get(sev, 0) + 1
            by_layer[d["layer"]]["total"] += 1

            tier_dist = None
            if d.get("source_tier_dist"):
                try:
                    tier_dist = json.loads(d["source_tier_dist"])
                    # fallback active: tier > 1 比例 > 0
                    fallback_count = sum(int(v) for k, v in tier_dist.items()
                                         if k.isdigit() and int(k) > 1)
                    if fallback_count > 0:
                        fallback_active.append({
                            "table": d["table_name"],
                            "tier_distribution": tier_dist,
                            "fallback_rows": fallback_count,
                        })
                except Exception:
                    pass

            item = {
                "table_name": d["table_name"],
                "layer": d["layer"],
                "severity": sev,
                "row_count": d.get("row_count"),
                "last_data_date": d.get("last_data_date"),
                "freshness_hours": d.get("freshness_hours"),
                "sla_hours": d.get("sla_hours"),
                "expected_freshness": d.get("expected_freshness"),
                "writer_module": d.get("writer_module"),
                "upstream_source": d.get("upstream_source"),
                "source_tier": d.get("source_tier"),
                "issue_summary": d.get("issue_summary"),
                "source_tier_distribution": tier_dist,
            }
            items.append(item)
            if sev == "red":
                red_list.append(item)

        return {
            "snapshot_at": snap_at,
            "summary": {
                "total": sum(severity_total.values()),
                **severity_total,
            },
            "by_layer": dict(by_layer),
            "items": items,
            "red_list": red_list,
            "fallback_active": fallback_active,
        }
    finally:
        con.close()


@router.get("/snapshot/red")
def get_red_list() -> dict[str, Any]:
    """仅 red 列表 (issue 详情). 给前端 alert 弹窗用."""

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        if snap_at is None:
            return {"snapshot_at": None, "red_list": []}
        rows = con.execute("""
            SELECT
                d.table_name, d.layer, d.purpose, d.writer_module,
                d.expected_freshness, d.sla_hours,
                m.row_count, m.last_data_date, m.freshness_hours,
                m.severity, m.issue_summary
            FROM dim_data_asset d
            JOIN mart_data_health m
              ON m.table_name = d.table_name AND m.snapshot_at = ?
            WHERE m.severity = 'red'
            ORDER BY d.layer, d.table_name
        """, (snap_at,)).fetchall()
        return {
            "snapshot_at": snap_at,
            "red_list": [dict(r) for r in rows],
        }
    finally:
        con.close()


@router.get("/sources")
def get_sources_overview() -> dict[str, Any]:
    """数据源总览 (Tab 1): 按 upstream_source + source_tier 聚合.

    用 dim_data_asset 注册的声明 + mart_data_health 最新数据. 这里只展示
    active 的外部接入源 (tier 1-3); 派生/实验/已退役资产仍保留在
    snapshot 和 asset 视图里, 但不作为 source status 参与汇总.
    """

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        rows = con.execute(f"""
            SELECT
                d.upstream_source, d.source_tier,
                COUNT(*) AS asset_count,
                SUM(COALESCE(m.row_count, 0)) AS total_rows,
                SUM(CASE WHEN m.severity='red' THEN 1 ELSE 0 END) AS red_count,
                SUM(CASE WHEN m.severity='yellow' THEN 1 ELSE 0 END) AS yellow_count,
                SUM(CASE WHEN m.severity='green' THEN 1 ELSE 0 END) AS green_count,
                MAX(m.freshness_hours) AS max_freshness_h
            FROM dim_data_asset d
            LEFT JOIN mart_data_health m
              ON m.table_name = d.table_name
             AND m.snapshot_at = COALESCE(?, m.snapshot_at)
            WHERE d.upstream_source IS NOT NULL
              AND d.source_tier IN (1, 2, 3)
              AND COALESCE(d.deprecation_status, 'active') = 'active'
            GROUP BY d.upstream_source, d.source_tier
            ORDER BY d.source_tier, d.upstream_source
        """, (snap_at,)).fetchall()
        priorities = []
        try:
            priorities = [
                dict(r) for r in con.execute(
                    """
                    SELECT data_domain, preferred_source, fallback_1, fallback_2, reason
                      FROM dim_data_source_priority
                     ORDER BY data_domain
                    """
                ).fetchall()
            ]
        except Exception:
            priorities = []
        return {
            "snapshot_at": snap_at,
            "sources": [dict(r) for r in rows],
            "source_priorities": priorities,
        }
    finally:
        con.close()


@router.get("/asset/{table_name}")
def get_asset_detail(table_name: str) -> dict[str, Any]:
    """单表完整画像 (dim_data_asset 声明 + 最新 mart_data_health 快照 + 7 日趋势)."""

    con = get_conn()
    try:
        # 声明
        decl = con.execute("""
            SELECT * FROM dim_data_asset WHERE table_name = ?
        """, (table_name,)).fetchone()
        if not decl:
            raise HTTPException(404, f"asset not found: {table_name}")

        # 最新快照
        latest = con.execute("""
            SELECT * FROM mart_data_health WHERE table_name = ?
            ORDER BY snapshot_at DESC LIMIT 1
        """, (table_name,)).fetchone()

        # 7 日 row_count 趋势
        trend = con.execute("""
            SELECT snapshot_at, row_count, severity, freshness_hours
            FROM mart_data_health WHERE table_name = ?
            ORDER BY snapshot_at DESC LIMIT 30
        """, (table_name,)).fetchall()

        decl_dict = dict(decl)
        # parse JSON 字段
        for k in ("reader_modules", "fallback_chain", "consumed_by_views"):
            if decl_dict.get(k):
                try:
                    decl_dict[k] = json.loads(decl_dict[k])
                except Exception:
                    pass
        return {
            "asset": decl_dict,
            "latest_snapshot": dict(latest) if latest else None,
            "trend": [dict(r) for r in trend],
        }
    finally:
        con.close()


@router.get("/snapshot/by_layer")
def get_by_layer() -> dict[str, Any]:
    """按 layer 分组的健康统计 (前端 dashboard 简版)."""

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        if snap_at is None:
            return {"snapshot_at": None, "by_layer": {}}
        rows = con.execute("""
            SELECT
                d.layer,
                m.severity,
                COUNT(*) AS c
            FROM dim_data_asset d
            JOIN mart_data_health m
              ON m.table_name = d.table_name AND m.snapshot_at = ?
            GROUP BY d.layer, m.severity
            ORDER BY d.layer, m.severity
        """, (snap_at,)).fetchall()
        by_layer: dict[str, dict[str, int]] = defaultdict(lambda: {"green": 0, "yellow": 0, "red": 0, "total": 0})
        for r in rows:
            by_layer[r["layer"]][r["severity"]] = r["c"]
            by_layer[r["layer"]]["total"] += r["c"]
        return {"snapshot_at": snap_at, "by_layer": dict(by_layer)}
    finally:
        con.close()


@router.get("/lineage")
def get_lineage_registry() -> dict[str, Any]:
    """派生 SQL 谱系 (W3): 每个 mart_*/fact_* 派生表的输入/SQL/状态.

    用于 UI Tab 3 (派生层 / Pipeline 监视). 单一真相源:
    services/data_lineage/registry.py + mart_lineage 表 (运行时状态).
    """
    from services.data_lineage.registry import to_dicts

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        # 拉运行时状态
        runtime_rows = con.execute("""
            SELECT lineage_id, last_run_at, last_row_count, last_status,
                   last_error, last_runtime_s, sql_hash, updated_at
            FROM mart_lineage
        """).fetchall()
        runtime_by_id = {r["lineage_id"]: dict(r) for r in runtime_rows}

        # 输出表的最新 severity
        sev_by_table: dict[str, str] = {}
        if snap_at:
            for r in con.execute(
                "SELECT table_name, severity FROM mart_data_health WHERE snapshot_at = ?",
                (snap_at,),
            ).fetchall():
                sev_by_table[r["table_name"]] = r["severity"]

        out = []
        for spec_dict in to_dicts():
            lid = spec_dict["lineage_id"]
            rt = runtime_by_id.get(lid, {})
            out.append({
                **spec_dict,
                "output_severity": sev_by_table.get(spec_dict["output_table"], "unknown"),
                "last_run_at": rt.get("last_run_at"),
                "last_row_count": rt.get("last_row_count"),
                "last_status": rt.get("last_status") or "pending",
                "last_error": rt.get("last_error"),
                "last_runtime_s": rt.get("last_runtime_s"),
                "sql_hash_committed": rt.get("sql_hash"),
                "sql_hash_changed": (
                    rt.get("sql_hash") is not None
                    and rt.get("sql_hash") != spec_dict["sql_hash"]
                ),
            })
        return {
            "snapshot_at": snap_at,
            "lineage_count": len(out),
            "lineages": out,
        }
    finally:
        con.close()


@router.get("/models")
def get_model_lifecycle() -> dict[str, Any]:
    """模型生命周期 (W4): champion / challenger / retired 状态全部列出.

    用于 UI Tab 7 (模型生命周期). 单一真相源: mart_model_lifecycle.
    """
    from services.ml_lifecycle.registry import list_models, GATE_THRESHOLDS

    models = list_models()
    return {
        "champion": next((m for m in models if m["status"] == "champion"), None),
        "challengers": [m for m in models if m["status"] == "challenger"],
        "retired": [m for m in models if m["status"] == "retired"],
        "gate_thresholds": GATE_THRESHOLDS,
    }


@router.get("/drift")
def get_feature_drift() -> dict[str, Any]:
    """特征漂移最新快照 (W4): 每个特征的 PSI + severity.

    用于 UI Tab 5 (Drift). 单一真相源: mart_feature_drift.
    """
    con = get_conn()
    try:
        # 最新快照时间
        last_at_row = con.execute(
            "SELECT MAX(snapshot_at) FROM mart_feature_drift"
        ).fetchone()
        last_at = last_at_row[0] if last_at_row else None
        if last_at is None:
            return {
                "snapshot_at": None,
                "items": [],
                "summary": {"ok": 0, "warn": 0, "critical": 0, "unknown": 0},
                "note": "no drift snapshot yet — run backend/scripts/compute_feature_drift.py",
            }
        rows = con.execute("""
            SELECT model_id, feature, psi, n_train, n_recent, window_days, severity
              FROM mart_feature_drift
             WHERE snapshot_at = ?
             ORDER BY psi DESC NULLS LAST
        """, (last_at,)).fetchall()
        items = [dict(r) for r in rows]
        summary = {"ok": 0, "warn": 0, "critical": 0, "unknown": 0}
        for r in items:
            summary[r["severity"]] = summary.get(r["severity"], 0) + 1
        return {
            "snapshot_at": last_at,
            "items": items,
            "summary": summary,
        }
    finally:
        con.close()


@router.get("/clients")
def get_clients_registry() -> dict[str, Any]:
    """数据写入客户端登记表 (W2): 一个客户端 = 一组 (raw/dim/fact/mart) 表的写入器.

    用于 UI Tab 2 (clients): 列出所有 client + 它们写哪些表 + freshness/sla.
    单一真相源在 services/data_sources/clients_registry.py.
    """
    from services.data_sources.clients_registry import to_dicts, all_clients

    con = get_conn()
    try:
        snap_at = _latest_snapshot_at(con)
        # 把每张表的最新 severity 拼到 client 视图里
        sev_by_table: dict[str, str] = {}
        if snap_at:
            rows = con.execute(
                "SELECT table_name, severity FROM mart_data_health WHERE snapshot_at = ?",
                (snap_at,),
            ).fetchall()
            sev_by_table = {r["table_name"]: r["severity"] for r in rows}

        clients = to_dicts()
        for c in clients:
            agg = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
            for w in c["writes"]:
                sev = sev_by_table.get(w["table"], "unknown")
                agg[sev] = agg.get(sev, 0) + 1
                w["severity"] = sev
            c["health_summary"] = agg
            c["worst_severity"] = (
                "red"   if agg["red"]    > 0 else
                "yellow" if agg["yellow"] > 0 else
                "green" if agg["green"]  > 0 else "unknown"
            )
        return {
            "snapshot_at": snap_at,
            "client_count": len(clients),
            "clients": clients,
        }
    finally:
        con.close()
