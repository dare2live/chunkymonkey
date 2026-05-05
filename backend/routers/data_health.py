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


def _table_exists(con, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _has_column(con, table: str, column: str) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.columns
         WHERE table_name = ? AND column_name = ?
        """,
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _safe_json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


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
            from services.source_watermarks import ensure_source_watermark_schema
            from services.source_watermarks import list_source_failures

            ensure_source_watermark_schema(con)
            watermarks = [
                dict(r) for r in con.execute(
                    """
                    SELECT data_domain, source_name, source_tier,
                           last_success_at, last_data_date, last_raw_hash,
                           next_check_at, consecutive_failures, fallback_active,
                           fallback_reason, row_count, parser_version, updated_at
                      FROM mart_data_source_watermark
                     ORDER BY source_tier, data_domain, source_name
                    """
                ).fetchall()
            ]
            failure_queue = list_source_failures(con, status="open", limit=200)
        except Exception:
            watermarks = []
            failure_queue = []
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
            "watermarks": watermarks,
            "failure_queue": failure_queue,
        }
    finally:
        con.close()


@router.get("/pipeline-manifest")
def get_pipeline_manifest(limit: int = 50) -> dict[str, Any]:
    """Latest batch/model pipeline runs from mart_pipeline_run_manifest."""

    con = get_conn()
    try:
        from services.pipeline_manifest import ensure_pipeline_manifest_schema

        ensure_pipeline_manifest_schema(con)
        rows = con.execute(
            """
            SELECT run_id, pipeline_name, status, started_at, ended_at, duration_s,
                   commit_sha, command, cwd,
                   input_tables_json, output_tables_json,
                   input_row_counts_json, output_row_counts_json,
                   model_id, feature_group, label_name, holding_period,
                   gate_result, blockers_json, perf_summary_json, created_at
              FROM mart_pipeline_run_manifest
             ORDER BY COALESCE(started_at, created_at) DESC
             LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for key in (
                "input_tables_json",
                "output_tables_json",
                "input_row_counts_json",
                "output_row_counts_json",
                "blockers_json",
                "perf_summary_json",
            ):
                if item.get(key):
                    try:
                        item[key[:-5] if key.endswith("_json") else key] = json.loads(item[key])
                    except Exception:
                        item[key[:-5] if key.endswith("_json") else key] = item[key]
            items.append(item)
        return {"runs": items, "count": len(items)}
    finally:
        con.close()


PERFORMANCE_BUDGETS_S = {
    "sync": 60.0,
    "watermarks": 5.0,
    "topk": 1.0,
    "health": 15.0,
    "drift": 15.0,
    "audit": 15.0,
    "train_full": 60.0,
    "walkforward_2fold": 60.0,
    "holding_topk": 15.0,
}


def _load_manifest_rows(con, *, limit: int = 80) -> list[dict[str, Any]]:
    from services.pipeline_manifest import ensure_pipeline_manifest_schema

    ensure_pipeline_manifest_schema(con)
    rows = con.execute(
        """
        SELECT run_id, pipeline_name, status, started_at, ended_at, duration_s,
               model_id, feature_group, gate_result, blockers_json, perf_summary_json
          FROM mart_pipeline_run_manifest
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT ?
        """,
        (max(1, min(int(limit), 200)),),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["blockers"] = _safe_json(item.pop("blockers_json", None), [])
        item["perf_summary"] = _safe_json(item.pop("perf_summary_json", None), {})
        out.append(item)
    return out


def _phase_actual_s(phase: dict[str, Any]) -> float | None:
    for key in ("phase_elapsed_s", "elapsed_s", "duration_s"):
        if phase.get(key) is not None:
            try:
                return float(phase[key])
            except Exception:
                return None
    return None


@router.get("/performance")
def get_performance_overview(limit: int = 80) -> dict[str, Any]:
    """Budget vs actual performance summary from mart_pipeline_run_manifest."""

    con = get_conn()
    try:
        rows = _load_manifest_rows(con, limit=limit)
        latest_daily = next((r for r in rows if r["pipeline_name"] == "cron_daily"), None)
        latest_benchmark = next((r for r in rows if r["pipeline_name"] == "benchmark_model_pipeline"), None)
        latest_evidence = next((r for r in rows if r["pipeline_name"] == "build_challenger_evidence_bundle"), None)

        phase_rows = []
        if latest_daily:
            for phase in (latest_daily.get("perf_summary") or {}).get("phases") or []:
                name = phase.get("phase") or phase.get("name")
                actual_s = _phase_actual_s(phase)
                budget_s = PERFORMANCE_BUDGETS_S.get(str(name))
                phase_rows.append({
                    "phase": name,
                    "status": phase.get("status"),
                    "actual_s": actual_s,
                    "budget_s": budget_s,
                    "over_budget": bool(actual_s is not None and budget_s is not None and actual_s > budget_s),
                })

        benchmark_steps = []
        if latest_benchmark:
            for step in (latest_benchmark.get("perf_summary") or {}).get("steps") or []:
                actual_s = _phase_actual_s(step)
                benchmark_steps.append({
                    "name": step.get("name"),
                    "kind": step.get("kind"),
                    "status": step.get("status"),
                    "actual_s": actual_s,
                    "command": step.get("command"),
                })

        evidence_steps = []
        if latest_evidence:
            for step in (latest_evidence.get("perf_summary") or {}).get("steps") or []:
                actual_s = _phase_actual_s(step)
                evidence_steps.append({
                    "name": step.get("name"),
                    "status": step.get("status"),
                    "actual_s": actual_s,
                    "returncode": step.get("returncode"),
                })

        return {
            "budgets_s": PERFORMANCE_BUDGETS_S,
            "latest_daily": latest_daily,
            "daily_phases": phase_rows,
            "latest_benchmark": latest_benchmark,
            "benchmark_steps": benchmark_steps,
            "latest_evidence": latest_evidence,
            "evidence_steps": evidence_steps,
            "recent_runs": rows[: min(20, len(rows))],
        }
    finally:
        con.close()


@router.get("/source-watermarks")
def get_source_watermarks(refresh: bool = False) -> dict[str, Any]:
    """Source-domain freshness and fallback state."""

    con = get_conn()
    try:
        from services.source_watermarks import (
            ensure_source_watermark_schema,
            refresh_known_source_watermarks,
        )

        ensure_source_watermark_schema(con)
        if refresh:
            refresh_known_source_watermarks(con)
        rows = con.execute(
            """
            SELECT data_domain, source_name, source_tier,
                   last_success_at, last_data_date, last_raw_hash,
                   next_check_at, consecutive_failures, fallback_active,
                   fallback_reason, row_count, parser_version, updated_at
              FROM mart_data_source_watermark
             ORDER BY source_tier, data_domain, source_name
            """
        ).fetchall()
        return {"watermarks": [dict(r) for r in rows], "count": len(rows)}
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
    latest_gate = None
    with get_conn() as con:
        gate_by_model: dict[str, dict[str, Any]] = {}
        if _table_exists(con, "mart_tdx_keep_promotion_gate"):
            gate_rows = con.execute("""
                SELECT *
                  FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY challenger_model_id
                               ORDER BY evaluated_at DESC
                           ) AS rn
                      FROM mart_tdx_keep_promotion_gate
                  )
                 WHERE rn = 1
                 ORDER BY evaluated_at DESC
            """).fetchall()
            for row in gate_rows:
                gate = dict(row)
                gate.pop("rn", None)
                gate["gate_results"] = _safe_json(gate.get("gate_results_json"), [])
                gate["blockers"] = _safe_json(gate.get("blockers_json"), [])
                gate_by_model[gate["challenger_model_id"]] = gate
            latest_gate = next(iter(gate_by_model.values()), None)

        has_daily_rec = _table_exists(con, "mart_daily_recommendation")
        has_run_mode = has_daily_rec and _has_column(con, "mart_daily_recommendation", "run_mode")
        evidence_by_model: dict[str, dict[str, Any]] = {}
        if _table_exists(con, "mart_challenger_evidence_bundle"):
            evidence_rows = con.execute("""
                SELECT *
                  FROM (
                    SELECT evidence_run_id, model_id, status, gate_run_id,
                           gate_status, blockers_json, started_at, ended_at,
                           duration_s,
                           ROW_NUMBER() OVER (
                               PARTITION BY model_id
                               ORDER BY started_at DESC
                           ) AS rn
                      FROM mart_challenger_evidence_bundle
                  )
                 WHERE rn = 1
            """).fetchall()
            for row in evidence_rows:
                evidence = dict(row)
                evidence.pop("rn", None)
                evidence["blockers"] = _safe_json(evidence.get("blockers_json"), [])
                evidence_by_model[evidence["model_id"]] = evidence
        for model in models:
            model_id = model.get("model_id")
            if model_id in gate_by_model:
                model["promotion_gate"] = gate_by_model[model_id]
            if model_id in evidence_by_model:
                model["evidence_bundle"] = evidence_by_model[model_id]
            if model.get("status") != "challenger" or not has_daily_rec:
                continue
            shadow_filter = "AND COALESCE(run_mode, '') = 'shadow'" if has_run_mode else ""
            row = con.execute(f"""
                SELECT MAX(snapshot_date) AS snapshot_date, COUNT(*) AS row_count
                  FROM mart_daily_recommendation
                 WHERE model_id = ? {shadow_filter}
            """, (model_id,)).fetchone()
            model["shadow_topk"] = dict(row) if row else None
    return {
        "champion": next((m for m in models if m["status"] == "champion"), None),
        "challengers": [m for m in models if m["status"] == "challenger"],
        "retired": [m for m in models if m["status"] == "retired"],
        "latest_gate": latest_gate,
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
