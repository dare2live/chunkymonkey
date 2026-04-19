"""
stock_validation.py

四层股票评分体系的验证报告：
- 当前分池结构与近20日反馈
- 新旧排序对比
- 股票级异常项
- 数据审计摘要
"""

from __future__ import annotations

from datetime import datetime
import logging

from services.audit import run_quality_audit
from services.industry import industry_level_db_column, industry_level_expr
from services.qlib_full_engine import get_model_summary


logger = logging.getLogger("cm-api")

SECTOR_LEVEL = 1


def _safe_round(value, digits: int = 2):
    if value is None:
        return None
    return round(float(value), digits)


def _pool_order(value: str) -> int:
    return {
        "A池": 0,
        "B池": 1,
        "C池": 2,
        "D池": 3,
    }.get(value or "", 9)


def _serialize_rows(rows, fields: list[str]) -> list[dict]:
    result = []
    for row in rows:
        item = {}
        for field in fields:
            value = row[field]
            if isinstance(value, float):
                value = _safe_round(value)
            item[field] = value
        result.append(item)
    return result


def _table_column_names(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] if hasattr(row, "keys") else row[1] for row in rows}


def _normalize_sector(sector: str | None) -> str | None:
    value = str(sector or "").strip()
    return value or None


def _sector_exists_clause(
    alias: str,
    sector: str | None,
    *,
    level1_col: str | None = None,
    fallback_to_dim_industry: bool = False,
) -> tuple[str, tuple]:
    normalized = _normalize_sector(sector)
    if not normalized:
        return "", ()
    if snapshot_level1_col:
        return (
            f"""
              AND (
                  (COALESCE({alias}.{snapshot_level1_col}, '') != '' AND {alias}.{snapshot_level1_col} = ?)
                  OR (
                      COALESCE({alias}.{snapshot_level1_col}, '') = ''
                      AND EXISTS (
                          SELECT 1
                          FROM dim_stock_tdx_industry sector_ctx
                          WHERE sector_ctx.stock_code = {alias}.stock_code
                            AND sector_ctx.tdx_l1_name = ?
                      )
                  )
                """,
                (normalized, normalized),
            )
        return (f" AND COALESCE({level1_expr}, '') = ?", (normalized,))
    return (
        f"""
          AND EXISTS (
              SELECT 1
                            FROM dim_stock_industry sector_ctx
              WHERE sector_ctx.stock_code = {alias}.stock_code
                AND sector_ctx.tdx_l1_name = ?
          )
        """,
        (normalized,),
    )


def _load_pool_feedback(conn, sector: str | None = None) -> list[dict]:
    sector_clause, sector_params = _sector_exists_clause("t", sector)
    rows = conn.execute(
        f"""
        SELECT COALESCE(priority_pool, '未分池') AS priority_pool,
               COUNT(*) AS total,
               SUM(CASE WHEN setup_tag IS NOT NULL THEN 1 ELSE 0 END) AS setup_count,
               SUM(CASE WHEN composite_cap_reason IS NOT NULL AND composite_cap_reason != '' THEN 1 ELSE 0 END) AS capped_count,
               SUM(CASE WHEN external_attention_score IS NOT NULL OR external_attention_signal IS NOT NULL THEN 1 ELSE 0 END) AS attention_covered_count,
               SUM(CASE WHEN external_attention_signal IN ('外部确认增强', '关注度抬升', '调研活跃') THEN 1 ELSE 0 END) AS attention_boosted_count,
               SUM(CASE WHEN external_attention_signal = '热度拥挤' THEN 1 ELSE 0 END) AS attention_crowded_count,
               AVG(discovery_score) AS avg_discovery_score,
               AVG(company_quality_score) AS avg_quality_score,
               AVG(stage_score) AS avg_stage_score,
               AVG(forecast_score) AS avg_forecast_score,
               AVG(composite_priority_score) AS avg_composite_score,
               AVG(external_attention_score) AS avg_attention_score,
               AVG(external_crowding_penalty) AS avg_crowding_penalty,
               AVG(composite_priority_score - raw_composite_priority_score) AS avg_score_delta,
               AVG(price_20d_pct) AS avg_price_20d_pct
        FROM mart_stock_trend t
        WHERE 1 = 1
        {sector_clause}
        GROUP BY COALESCE(priority_pool, '未分池')
        ORDER BY
            CASE COALESCE(priority_pool, '未分池')
                WHEN 'A池' THEN 0
                WHEN 'B池' THEN 1
                WHEN 'C池' THEN 2
                WHEN 'D池' THEN 3
                ELSE 9
            END
        """,
        sector_params,
    ).fetchall()
    return _serialize_rows(
        rows,
        [
            "priority_pool",
            "total",
            "setup_count",
            "capped_count",
            "attention_covered_count",
            "attention_boosted_count",
            "attention_crowded_count",
            "avg_discovery_score",
            "avg_quality_score",
            "avg_stage_score",
            "avg_forecast_score",
            "avg_composite_score",
            "avg_attention_score",
            "avg_crowding_penalty",
            "avg_score_delta",
            "avg_price_20d_pct",
        ],
    )


def _load_snapshot_pool_replay(conn, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause("s", sector, snapshot_level1_col="snapshot_tdx_l1_name")
    coverage_row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_rows,
               SUM(CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN 1 ELSE 0 END) AS scored_rows,
               COUNT(DISTINCT snapshot_date) AS snapshot_dates,
               COUNT(DISTINCT CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN snapshot_date END) AS scored_snapshot_dates,
               MIN(CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN snapshot_date END) AS first_scored_snapshot_date,
               MAX(CASE WHEN priority_pool IS NOT NULL AND priority_pool != '' THEN snapshot_date END) AS last_scored_snapshot_date
        FROM fact_setup_snapshot s
        WHERE 1 = 1
        {sector_clause}
        """,
        sector_params,
    ).fetchone()

    baseline_row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN matured_10d = 1 AND gain_10d IS NOT NULL THEN 1 ELSE 0 END) AS matured_10d_count,
               AVG(CASE WHEN matured_10d = 1 THEN gain_10d END) AS avg_gain_10d,
               AVG(CASE WHEN matured_10d = 1 AND gain_10d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_10d,
               AVG(CASE WHEN matured_10d = 1 THEN max_drawdown_10d END) AS avg_drawdown_10d,
               SUM(CASE WHEN matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS matured_30d_count,
               AVG(CASE WHEN matured_30d = 1 THEN gain_30d END) AS avg_gain_30d,
               AVG(CASE WHEN matured_30d = 1 AND gain_30d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_30d,
               AVG(CASE WHEN matured_30d = 1 THEN max_drawdown_30d END) AS avg_drawdown_30d,
               SUM(CASE WHEN matured_60d = 1 AND gain_60d IS NOT NULL THEN 1 ELSE 0 END) AS matured_60d_count,
               AVG(CASE WHEN matured_60d = 1 THEN gain_60d END) AS avg_gain_60d,
               AVG(CASE WHEN matured_60d = 1 AND gain_60d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_60d,
               AVG(CASE WHEN matured_60d = 1 THEN max_drawdown_60d END) AS avg_drawdown_60d
        FROM fact_setup_snapshot s
        WHERE priority_pool IS NOT NULL AND priority_pool != ''
        {sector_clause}
        """,
        sector_params,
    ).fetchone()

    rows = conn.execute(
        f"""
        SELECT priority_pool,
               COUNT(*) AS total,
               COUNT(DISTINCT snapshot_date) AS snapshot_days,
               AVG(composite_priority_score) AS avg_composite_score,
               SUM(CASE WHEN matured_10d = 1 AND gain_10d IS NOT NULL THEN 1 ELSE 0 END) AS matured_10d_count,
               AVG(CASE WHEN matured_10d = 1 THEN gain_10d END) AS avg_gain_10d,
               AVG(CASE WHEN matured_10d = 1 AND gain_10d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_10d,
               AVG(CASE WHEN matured_10d = 1 THEN max_drawdown_10d END) AS avg_drawdown_10d,
               SUM(CASE WHEN matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS matured_30d_count,
               AVG(CASE WHEN matured_30d = 1 THEN gain_30d END) AS avg_gain_30d,
               AVG(CASE WHEN matured_30d = 1 AND gain_30d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_30d,
               AVG(CASE WHEN matured_30d = 1 THEN max_drawdown_30d END) AS avg_drawdown_30d,
               SUM(CASE WHEN matured_60d = 1 AND gain_60d IS NOT NULL THEN 1 ELSE 0 END) AS matured_60d_count,
               AVG(CASE WHEN matured_60d = 1 THEN gain_60d END) AS avg_gain_60d,
               AVG(CASE WHEN matured_60d = 1 AND gain_60d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_60d,
               AVG(CASE WHEN matured_60d = 1 THEN max_drawdown_60d END) AS avg_drawdown_60d
        FROM fact_setup_snapshot s
        WHERE priority_pool IS NOT NULL AND priority_pool != ''
        {sector_clause}
        GROUP BY priority_pool
        ORDER BY
            CASE priority_pool
                WHEN 'A池' THEN 0
                WHEN 'B池' THEN 1
                WHEN 'C池' THEN 2
                WHEN 'D池' THEN 3
                ELSE 9
            END
        """,
        sector_params,
    ).fetchall()

    history_rows = conn.execute(
        f"""
        SELECT snapshot_date,
               priority_pool,
               COUNT(*) AS total,
               AVG(composite_priority_score) AS avg_composite_score,
               SUM(CASE WHEN matured_30d = 1 AND gain_30d IS NOT NULL THEN 1 ELSE 0 END) AS matured_30d_count,
               AVG(CASE WHEN matured_30d = 1 THEN gain_30d END) AS avg_gain_30d,
               AVG(CASE WHEN matured_30d = 1 AND gain_30d > 0 THEN 1.0 ELSE NULL END) * 100 AS win_rate_30d
        FROM fact_setup_snapshot s
        WHERE priority_pool IS NOT NULL AND priority_pool != ''
        {sector_clause}
        GROUP BY snapshot_date, priority_pool
        ORDER BY snapshot_date DESC,
                 CASE priority_pool
                     WHEN 'A池' THEN 0
                     WHEN 'B池' THEN 1
                     WHEN 'C池' THEN 2
                     WHEN 'D池' THEN 3
                     ELSE 9
                 END
        LIMIT 48
        """,
        sector_params,
    ).fetchall()

    fields = [
        "priority_pool",
        "total",
        "snapshot_days",
        "avg_composite_score",
        "matured_10d_count",
        "avg_gain_10d",
        "win_rate_10d",
        "avg_drawdown_10d",
        "matured_30d_count",
        "avg_gain_30d",
        "win_rate_30d",
        "avg_drawdown_30d",
        "matured_60d_count",
        "avg_gain_60d",
        "win_rate_60d",
        "avg_drawdown_60d",
        "uplift_vs_baseline_30d",
        "uplift_vs_baseline_60d",
    ]
    history_fields = [
        "snapshot_date",
        "priority_pool",
        "total",
        "avg_composite_score",
        "matured_30d_count",
        "avg_gain_30d",
        "win_rate_30d",
    ]
    coverage = {
        "total_rows": int(coverage_row["total_rows"] or 0),
        "scored_rows": int(coverage_row["scored_rows"] or 0),
        "snapshot_dates": int(coverage_row["snapshot_dates"] or 0),
        "scored_snapshot_dates": int(coverage_row["scored_snapshot_dates"] or 0),
        "first_scored_snapshot_date": coverage_row["first_scored_snapshot_date"],
        "last_scored_snapshot_date": coverage_row["last_scored_snapshot_date"],
    }
    baseline = {
        "matured_10d_count": int(baseline_row["matured_10d_count"] or 0),
        "avg_gain_10d": _safe_round(baseline_row["avg_gain_10d"]),
        "win_rate_10d": _safe_round(baseline_row["win_rate_10d"]),
        "avg_drawdown_10d": _safe_round(baseline_row["avg_drawdown_10d"]),
        "matured_30d_count": int(baseline_row["matured_30d_count"] or 0),
        "avg_gain_30d": _safe_round(baseline_row["avg_gain_30d"]),
        "win_rate_30d": _safe_round(baseline_row["win_rate_30d"]),
        "avg_drawdown_30d": _safe_round(baseline_row["avg_drawdown_30d"]),
        "matured_60d_count": int(baseline_row["matured_60d_count"] or 0),
        "avg_gain_60d": _safe_round(baseline_row["avg_gain_60d"]),
        "win_rate_60d": _safe_round(baseline_row["win_rate_60d"]),
        "avg_drawdown_60d": _safe_round(baseline_row["avg_drawdown_60d"]),
    }
    by_pool = _serialize_rows(rows, fields[:-2])
    for item in by_pool:
        item["uplift_vs_baseline_30d"] = (
            _safe_round(item["avg_gain_30d"] - baseline["avg_gain_30d"])
            if item.get("avg_gain_30d") is not None and baseline.get("avg_gain_30d") is not None
            else None
        )
        item["uplift_vs_baseline_60d"] = (
            _safe_round(item["avg_gain_60d"] - baseline["avg_gain_60d"])
            if item.get("avg_gain_60d") is not None and baseline.get("avg_gain_60d") is not None
            else None
        )
    return {
        "coverage": coverage,
        "baseline": baseline,
        "by_pool": by_pool,
        "history": _serialize_rows(history_rows, history_fields),
    }


def _load_snapshot_rank_compare(conn, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause("fact_setup_snapshot", sector, snapshot_level1_col="snapshot_tdx_l1_name")
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT snapshot_date,
                   stock_code,
                   stock_name,
                   gain_30d,
                   max_drawdown_30d,
                   ROW_NUMBER() OVER (
                       PARTITION BY snapshot_date
                       ORDER BY
                           CASE COALESCE(priority_pool, '')
                               WHEN 'A池' THEN 0
                               WHEN 'B池' THEN 1
                               WHEN 'C池' THEN 2
                               WHEN 'D池' THEN 3
                               ELSE 9
                           END,
                           COALESCE(composite_priority_score, 0) DESC,
                           stock_code
                   ) AS composite_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY snapshot_date
                       ORDER BY COALESCE(action_score, 0) DESC, stock_code
                   ) AS legacy_rank
            FROM fact_setup_snapshot
            WHERE composite_priority_score IS NOT NULL
              AND action_score IS NOT NULL
              AND matured_30d = 1
              AND gain_30d IS NOT NULL
              {sector_clause}
        )
        SELECT *
        FROM ranked
        ORDER BY snapshot_date DESC, composite_rank, legacy_rank
        """,
        sector_params,
    ).fetchall()

    if not rows:
        return {
            "summary": [],
            "history": [],
            "matured_snapshot_dates": 0,
        }

    topns = (10, 20, 50)
    method_aggs = {
        topn: {
            "composite": {"count": 0, "sum_gain": 0.0, "sum_win": 0, "sum_dd": 0.0, "snapshot_dates": set()},
            "legacy": {"count": 0, "sum_gain": 0.0, "sum_win": 0, "sum_dd": 0.0, "snapshot_dates": set()},
        }
        for topn in topns
    }
    overlap_sets = {
        topn: {}
        for topn in topns
    }
    history_aggs = {}

    for row in rows:
        item = dict(row)
        snapshot_date = item["snapshot_date"]
        history_aggs.setdefault(snapshot_date, {
            "snapshot_date": snapshot_date,
            "composite": {"count": 0, "sum_gain": 0.0, "sum_win": 0, "sum_dd": 0.0},
            "legacy": {"count": 0, "sum_gain": 0.0, "sum_win": 0, "sum_dd": 0.0},
            "top20_overlap": set(),
            "top20_composite": set(),
            "top20_legacy": set(),
        })

        gain = float(item["gain_30d"])
        drawdown = float(item["max_drawdown_30d"]) if item["max_drawdown_30d"] is not None else 0.0
        for topn in topns:
            if item["composite_rank"] <= topn:
                agg = method_aggs[topn]["composite"]
                agg["count"] += 1
                agg["sum_gain"] += gain
                agg["sum_win"] += 1 if gain > 0 else 0
                agg["sum_dd"] += drawdown
                agg["snapshot_dates"].add(snapshot_date)
                overlap_sets[topn].setdefault(snapshot_date, {"composite": set(), "legacy": set()})
                overlap_sets[topn][snapshot_date]["composite"].add(item["stock_code"])
            if item["legacy_rank"] <= topn:
                agg = method_aggs[topn]["legacy"]
                agg["count"] += 1
                agg["sum_gain"] += gain
                agg["sum_win"] += 1 if gain > 0 else 0
                agg["sum_dd"] += drawdown
                agg["snapshot_dates"].add(snapshot_date)
                overlap_sets[topn].setdefault(snapshot_date, {"composite": set(), "legacy": set()})
                overlap_sets[topn][snapshot_date]["legacy"].add(item["stock_code"])

        if item["composite_rank"] <= 20:
            agg = history_aggs[snapshot_date]["composite"]
            agg["count"] += 1
            agg["sum_gain"] += gain
            agg["sum_win"] += 1 if gain > 0 else 0
            agg["sum_dd"] += drawdown
            history_aggs[snapshot_date]["top20_composite"].add(item["stock_code"])
        if item["legacy_rank"] <= 20:
            agg = history_aggs[snapshot_date]["legacy"]
            agg["count"] += 1
            agg["sum_gain"] += gain
            agg["sum_win"] += 1 if gain > 0 else 0
            agg["sum_dd"] += drawdown
            history_aggs[snapshot_date]["top20_legacy"].add(item["stock_code"])

    summary = []
    for topn in topns:
        per_date = overlap_sets[topn]
        overlap_total = sum(
            len(values["composite"] & values["legacy"])
            for values in per_date.values()
        )
        for method in ("composite", "legacy"):
            agg = method_aggs[topn][method]
            count = agg["count"]
            summary.append({
                "topn": topn,
                "method": method,
                "sample_count": count,
                "snapshot_days": len(agg["snapshot_dates"]),
                "avg_gain_30d": _safe_round(agg["sum_gain"] / count) if count else None,
                "win_rate_30d": _safe_round(agg["sum_win"] * 100.0 / count) if count else None,
                "avg_drawdown_30d": _safe_round(agg["sum_dd"] / count) if count else None,
                "overlap_count": overlap_total if method == "composite" else None,
            })

    history = []
    for snapshot_date in sorted(history_aggs.keys(), reverse=True)[:12]:
        item = history_aggs[snapshot_date]
        composite = item["composite"]
        legacy = item["legacy"]
        history.append({
            "snapshot_date": snapshot_date,
            "composite_count": composite["count"],
            "composite_avg_gain_30d": _safe_round(composite["sum_gain"] / composite["count"]) if composite["count"] else None,
            "composite_win_rate_30d": _safe_round(composite["sum_win"] * 100.0 / composite["count"]) if composite["count"] else None,
            "legacy_count": legacy["count"],
            "legacy_avg_gain_30d": _safe_round(legacy["sum_gain"] / legacy["count"]) if legacy["count"] else None,
            "legacy_win_rate_30d": _safe_round(legacy["sum_win"] * 100.0 / legacy["count"]) if legacy["count"] else None,
            "top20_overlap": len(item["top20_composite"] & item["top20_legacy"]),
        })

    return {
        "summary": summary,
        "history": history,
        "matured_snapshot_dates": len(history_aggs),
    }


def _load_rank_compare(conn, limit: int = 120, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause("mart_stock_trend", sector)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT stock_code,
                   stock_name,
                   priority_pool,
                   stock_archetype,
                   action_score,
                   composite_priority_score,
                   ROW_NUMBER() OVER (
                       ORDER BY
                           CASE COALESCE(priority_pool, '')
                               WHEN 'A池' THEN 0
                               WHEN 'B池' THEN 1
                               WHEN 'C池' THEN 2
                               WHEN 'D池' THEN 3
                               ELSE 9
                           END,
                           COALESCE(composite_priority_score, 0) DESC,
                           stock_code
                   ) AS composite_rank,
                   ROW_NUMBER() OVER (
                       ORDER BY COALESCE(action_score, 0) DESC, stock_code
                   ) AS legacy_rank
            FROM mart_stock_trend
            WHERE action_score IS NOT NULL OR composite_priority_score IS NOT NULL
              {sector_clause}
        )
        SELECT *
        FROM ranked
        WHERE composite_rank <= ? OR legacy_rank <= ?
        ORDER BY composite_rank, legacy_rank
        """,
        sector_params + (limit, limit),
    ).fetchall()

    items = []
    top_sets = {20: {"composite": set(), "legacy": set()}, 50: {"composite": set(), "legacy": set()}, 100: {"composite": set(), "legacy": set()}}
    for row in rows:
        item = dict(row)
        item["action_score"] = _safe_round(item.get("action_score"))
        item["composite_priority_score"] = _safe_round(item.get("composite_priority_score"))
        item["rank_delta"] = int(item["legacy_rank"] - item["composite_rank"])
        items.append(item)
        for topn in (20, 50, 100):
            if item["composite_rank"] <= topn:
                top_sets[topn]["composite"].add(item["stock_code"])
            if item["legacy_rank"] <= topn:
                top_sets[topn]["legacy"].add(item["stock_code"])

    overlap = {
        f"top{topn}": len(top_sets[topn]["composite"] & top_sets[topn]["legacy"])
        for topn in (20, 50, 100)
    }

    promoted = [
        item for item in items
        if item.get("rank_delta", 0) >= 10
        and item.get("action_score") is not None
        and item.get("composite_priority_score") is not None
        and (item["composite_rank"] <= limit or item["legacy_rank"] <= limit)
    ]
    promoted.sort(key=lambda item: (-item["rank_delta"], item["composite_rank"], item["stock_code"]))

    demoted = [
        item for item in items
        if item.get("rank_delta", 0) <= -10
        and item.get("action_score") is not None
        and item.get("composite_priority_score") is not None
        and (item["composite_rank"] <= limit or item["legacy_rank"] <= limit)
    ]
    demoted.sort(key=lambda item: (item["rank_delta"], item["legacy_rank"], item["stock_code"]))

    return {
        "overlap": overlap,
        "promoted": promoted[:12],
        "demoted": demoted[:12],
    }


def _load_anomalies(conn, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause("mart_stock_trend", sector)
    mart_columns = _table_column_names(conn, "mart_stock_trend")
    quality_source_sql = (
        "company_quality_score_source"
        if "company_quality_score_source" in mart_columns
        else "'stock_scoring_v2' AS company_quality_score_source"
    )
    quality_snapshot_sql = (
        "quality_feature_snapshot_date"
        if "quality_feature_snapshot_date" in mart_columns
        else "NULL AS quality_feature_snapshot_date"
    )
    common_fields = [
        "stock_code",
        "stock_name",
        "priority_pool",
        "stock_archetype",
        "discovery_score",
        "company_quality_score",
        "company_quality_score_source",
        "quality_feature_snapshot_date",
        "stage_score",
        "forecast_score",
        "raw_composite_priority_score",
        "composite_priority_score",
        "priority_pool_reason",
        "composite_cap_reason",
    ]

    capped_rows = conn.execute(
        f"""
        SELECT stock_code, stock_name, priority_pool, stock_archetype,
                         discovery_score, company_quality_score, {quality_source_sql}, {quality_snapshot_sql},
                             stage_score, forecast_score,
               raw_composite_priority_score, composite_priority_score, priority_pool_reason, composite_cap_reason
        FROM mart_stock_trend
        WHERE raw_composite_priority_score >= 75
          AND (COALESCE(priority_pool, '') != 'A池' OR composite_cap_reason IS NOT NULL)
          {sector_clause}
        ORDER BY COALESCE(raw_composite_priority_score, 0) DESC, stock_code
        LIMIT 12
        """,
        sector_params,
    ).fetchall()

    forecast_rows = conn.execute(
        f"""
        SELECT stock_code, stock_name, priority_pool, stock_archetype,
                         discovery_score, company_quality_score, {quality_source_sql}, {quality_snapshot_sql},
                             stage_score, forecast_score,
               raw_composite_priority_score, composite_priority_score, priority_pool_reason, composite_cap_reason
        FROM mart_stock_trend
        WHERE forecast_score >= 70
          AND stage_score < 40
          {sector_clause}
        ORDER BY forecast_score DESC, stage_score ASC, stock_code
        LIMIT 12
        """,
        sector_params,
    ).fetchall()

    quality_rows = conn.execute(
        f"""
        SELECT stock_code, stock_name, priority_pool, stock_archetype,
                         discovery_score, company_quality_score, {quality_source_sql}, {quality_snapshot_sql},
                             stage_score, forecast_score,
               raw_composite_priority_score, composite_priority_score, priority_pool_reason, composite_cap_reason
        FROM mart_stock_trend
        WHERE company_quality_score < 45
          AND COALESCE(stock_archetype, '') != '周期/事件驱动型'
          AND composite_priority_score >= 60
          {sector_clause}
        ORDER BY composite_priority_score DESC, stock_code
        LIMIT 12
        """,
        sector_params,
    ).fetchall()

    return {
        "capped_high_raw": _serialize_rows(capped_rows, common_fields),
        "forecast_stage_conflict": _serialize_rows(forecast_rows, common_fields),
        "quality_gate_conflict": _serialize_rows(quality_rows, common_fields),
        "counts": {
            "capped_high_raw": conn.execute(
                f"""
                SELECT COUNT(*)
                FROM mart_stock_trend
                WHERE raw_composite_priority_score >= 75
                  AND (COALESCE(priority_pool, '') != 'A池' OR composite_cap_reason IS NOT NULL)
                  {sector_clause}
                """,
                sector_params,
            ).fetchone()[0],
            "forecast_stage_conflict": conn.execute(
                f"""
                SELECT COUNT(*)
                FROM mart_stock_trend
                WHERE forecast_score >= 70
                  AND stage_score < 40
                  {sector_clause}
                """,
                sector_params,
            ).fetchone()[0],
            "quality_gate_conflict": conn.execute(
                f"""
                SELECT COUNT(*)
                FROM mart_stock_trend
                WHERE company_quality_score < 45
                  AND COALESCE(stock_archetype, '') != '周期/事件驱动型'
                  AND composite_priority_score >= 60
                  {sector_clause}
                """,
                sector_params,
            ).fetchone()[0],
        },
    }


def _load_audit_snapshot(conn) -> dict:
    audit = run_quality_audit(conn)
    layers = audit.get("layers") or {}
    financial = layers.get("financial") or {}
    trends = layers.get("trends") or {}
    sector = layers.get("sector_momentum") or {}
    current_rel = layers.get("current_relationship") or {}
    return {
        "audit_score": audit.get("score"),
        "latest_notice": (layers.get("raw") or {}).get("latest_notice"),
        "trend_count": trends.get("count"),
        "trend_scored": trends.get("scored"),
        "financial_research_ready": financial.get("research_history_ready"),
        "financial_research_gap": financial.get("research_history_gap"),
        "indicator_research_ready": financial.get("indicator_research_ready"),
        "indicator_research_gap": financial.get("indicator_research_gap"),
        "stage_feature_count": sector.get("stage_feature_count"),
        "forecast_feature_count": sector.get("forecast_feature_count"),
        "industry_context_count": sector.get("industry_context_count"),
        "industry_missing_current": current_rel.get("industry_missing_stocks"),
    }


def _load_archetype_distribution(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT COALESCE(stock_archetype, '待分类') AS stock_archetype,
               COUNT(*) AS total,
               SUM(CASE WHEN priority_pool = 'A池' THEN 1 ELSE 0 END) AS a_pool_count,
               AVG(company_quality_score) AS avg_quality_score,
               AVG(stage_score) AS avg_stage_score,
               AVG(composite_priority_score) AS avg_composite_score
        FROM mart_stock_trend
        GROUP BY COALESCE(stock_archetype, '待分类')
        ORDER BY COUNT(*) DESC, stock_archetype
        """
    ).fetchall()
    return _serialize_rows(
        rows,
        [
            "stock_archetype",
            "total",
            "a_pool_count",
            "avg_quality_score",
            "avg_stage_score",
            "avg_composite_score",
        ],
    )


def _load_qlib_summary(conn) -> dict:
    summary = get_model_summary(conn)
    if not summary:
        return {}
    factor_groups = summary.get("factor_groups") or []
    top_factors = summary.get("top_factors") or []
    train_params = summary.get("train_params") or {}
    enabled_parts = []
    if train_params.get("use_alpha158"):
        enabled_parts.append("Alpha158")
    if train_params.get("use_financial"):
        enabled_parts.append("financial")
    if train_params.get("use_institution"):
        enabled_parts.append("institution")
    if train_params.get("use_turtle"):
        enabled_parts.append("turtle")
    if train_params.get("use_quality"):
        enabled_parts.append("quality")
    if train_params.get("use_stage"):
        enabled_parts.append("stage")
    if train_params.get("use_northbound"):
        enabled_parts.append("northbound")
    summary["feature_stack_label"] = " + ".join(enabled_parts) if enabled_parts else "未标注"
    summary["factor_group_top"] = factor_groups[:3]
    summary["top_factors"] = top_factors[:5]
    return summary


def _load_quality_source_summary(conn) -> dict:
    mart_columns = _table_column_names(conn, "mart_stock_trend")
    if "company_quality_score_source" not in mart_columns:
        total_row = conn.execute("SELECT COUNT(*) AS total_stock_count FROM mart_stock_trend").fetchone()
        total_stock_count = int((total_row["total_stock_count"] if total_row else 0) or 0)
        return {
            "total_stock_count": total_stock_count,
            "quality_feature_v1_count": 0,
            "stock_scoring_v2_count": total_stock_count,
            "other_source_count": 0,
            "latest_snapshot_date": None,
        }

    snapshot_expr = "quality_feature_snapshot_date" if "quality_feature_snapshot_date" in mart_columns else "NULL"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_stock_count,
               SUM(CASE WHEN company_quality_score_source = 'quality_feature_v1' THEN 1 ELSE 0 END) AS quality_feature_v1_count,
               SUM(CASE WHEN COALESCE(company_quality_score_source, 'stock_scoring_v2') = 'stock_scoring_v2' THEN 1 ELSE 0 END) AS stock_scoring_v2_count,
               SUM(CASE WHEN company_quality_score_source IS NOT NULL AND company_quality_score_source NOT IN ('quality_feature_v1', 'stock_scoring_v2') THEN 1 ELSE 0 END) AS other_source_count,
               MAX(CASE WHEN company_quality_score_source = 'quality_feature_v1' THEN {snapshot_expr} ELSE NULL END) AS latest_snapshot_date
        FROM mart_stock_trend
        """
    ).fetchone()
    return {
        "total_stock_count": int(row["total_stock_count"] or 0),
        "quality_feature_v1_count": int(row["quality_feature_v1_count"] or 0),
        "stock_scoring_v2_count": int(row["stock_scoring_v2_count"] or 0),
        "other_source_count": int(row["other_source_count"] or 0),
        "latest_snapshot_date": row["latest_snapshot_date"],
    }


def get_stock_scorecard_stats(conn) -> dict:
    pools = _load_pool_feedback(conn)
    snapshot_replay = _load_snapshot_pool_replay(conn)
    archetypes = _load_archetype_distribution(conn)
    qlib_summary = _load_qlib_summary(conn)
    attention_calibration = _load_attention_calibration(conn)
    quality_source_summary = _load_quality_source_summary(conn)

    pool_map = {item.get("priority_pool"): item for item in pools}
    coverage = snapshot_replay.get("coverage") or {}
    baseline = snapshot_replay.get("baseline") or {}
    return {
        "summary": {
            "stock_count": sum(int(item.get("total") or 0) for item in pools),
            "setup_count": sum(int(item.get("setup_count") or 0) for item in pools),
            "capped_count": sum(int(item.get("capped_count") or 0) for item in pools),
            "a_pool_count": int((pool_map.get("A池") or {}).get("total") or 0),
            "b_pool_count": int((pool_map.get("B池") or {}).get("total") or 0),
            "c_pool_count": int((pool_map.get("C池") or {}).get("total") or 0),
            "d_pool_count": int((pool_map.get("D池") or {}).get("total") or 0),
            "snapshot_scored_rows": int(coverage.get("scored_rows") or 0),
            "snapshot_scored_dates": int(coverage.get("scored_snapshot_dates") or 0),
            "first_scored_snapshot_date": coverage.get("first_scored_snapshot_date"),
            "last_scored_snapshot_date": coverage.get("last_scored_snapshot_date"),
        },
        "current_pools": pools,
        "archetypes": archetypes,
        "qlib_summary": qlib_summary,
        "attention_calibration": attention_calibration,
        "quality_source_summary": quality_source_summary,
        "snapshot_replay": {
            "coverage": coverage,
            "baseline": baseline,
            "by_pool": snapshot_replay.get("by_pool") or [],
        },
    }


def _load_attention_calibration(conn) -> dict:
    coverage_row = conn.execute(
        """
        SELECT COUNT(DISTINCT snapshot_date) AS snapshot_days,
               MAX(snapshot_date) AS latest_snapshot_date,
               COUNT(*) AS snapshot_rows
        FROM fact_stock_attention_snapshot
        """
    ).fetchone()

    summary_row = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM mart_stock_trend) AS total_stock_count,
               COUNT(*) AS covered_stock_count,
               SUM(CASE WHEN external_attention_signal IS NOT NULL THEN 1 ELSE 0 END) AS signaled_stock_count,
               SUM(CASE WHEN external_attention_signal IN ('外部确认增强', '关注度抬升', '调研活跃') THEN 1 ELSE 0 END) AS boosted_signal_count,
               SUM(CASE WHEN external_attention_signal = '热度拥挤' THEN 1 ELSE 0 END) AS crowded_signal_count,
               SUM(CASE WHEN composite_priority_score > raw_composite_priority_score THEN 1 ELSE 0 END) AS score_up_count,
               SUM(CASE WHEN composite_priority_score < raw_composite_priority_score THEN 1 ELSE 0 END) AS score_down_count,
               SUM(CASE WHEN raw_composite_priority_score < 75 AND composite_priority_score >= 75 THEN 1 ELSE 0 END) AS promoted_to_a_count,
               SUM(CASE WHEN raw_composite_priority_score < 60 AND composite_priority_score >= 60 AND COALESCE(external_attention_score, 0) >= 70 THEN 1 ELSE 0 END) AS promoted_to_b_count,
               SUM(CASE WHEN raw_composite_priority_score >= 75 AND composite_priority_score < 75 AND COALESCE(external_crowding_penalty, 0) >= 6 THEN 1 ELSE 0 END) AS demoted_by_crowding_count,
               AVG(external_attention_score) AS avg_attention_score,
               AVG(external_crowding_penalty) AS avg_crowding_penalty,
               AVG(composite_priority_score - raw_composite_priority_score) AS avg_score_delta
        FROM mart_stock_trend
        WHERE external_attention_score IS NOT NULL OR external_attention_signal IS NOT NULL
        """
    ).fetchone()

    signal_rows = conn.execute(
        """
        WITH base AS (
            SELECT COALESCE(external_attention_signal, '未触发') AS signal_label,
                   external_attention_score,
                   external_crowding_penalty,
                   composite_priority_score,
                   raw_composite_priority_score,
                   price_20d_pct,
                   company_quality_score,
                   stage_score,
                   CASE WHEN raw_composite_priority_score < 75 AND composite_priority_score >= 75 THEN 1 ELSE 0 END AS promoted_to_a,
                   CASE WHEN raw_composite_priority_score >= 75 AND composite_priority_score < 75 AND COALESCE(external_crowding_penalty, 0) >= 6 THEN 1 ELSE 0 END AS demoted_by_crowding
            FROM mart_stock_trend
            WHERE external_attention_score IS NOT NULL OR external_attention_signal IS NOT NULL
        )
        SELECT signal_label,
               COUNT(*) AS total,
               AVG(external_attention_score) AS avg_attention_score,
               AVG(external_crowding_penalty) AS avg_crowding_penalty,
               AVG(composite_priority_score - raw_composite_priority_score) AS avg_score_delta,
               AVG(price_20d_pct) AS avg_price_20d_pct,
               AVG(CASE WHEN price_20d_pct IS NULL THEN NULL WHEN price_20d_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate_20d,
               AVG(company_quality_score) AS avg_quality_score,
               AVG(stage_score) AS avg_stage_score,
               SUM(promoted_to_a) AS promoted_to_a_count,
               SUM(demoted_by_crowding) AS demoted_by_crowding_count
        FROM base
        GROUP BY signal_label
        ORDER BY CASE signal_label
                     WHEN '外部确认增强' THEN 0
                     WHEN '关注度抬升' THEN 1
                     WHEN '调研活跃' THEN 2
                     WHEN '热度拥挤' THEN 3
                     ELSE 9
                 END
        """
    ).fetchall()

    score_band_rows = conn.execute(
        """
        WITH base AS (
            SELECT CASE
                       WHEN external_attention_score >= 75 THEN '75+'
                       WHEN external_attention_score >= 65 THEN '65-75'
                       WHEN external_attention_score >= 55 THEN '55-65'
                       ELSE '<55'
                   END AS band_label,
                   external_attention_score,
                   external_crowding_penalty,
                   composite_priority_score,
                   raw_composite_priority_score,
                   price_20d_pct,
                   CASE WHEN raw_composite_priority_score < 75 AND composite_priority_score >= 75 THEN 1 ELSE 0 END AS promoted_to_a,
                   CASE WHEN raw_composite_priority_score >= 75 AND composite_priority_score < 75 AND COALESCE(external_crowding_penalty, 0) >= 6 THEN 1 ELSE 0 END AS demoted_by_crowding
            FROM mart_stock_trend
            WHERE external_attention_score IS NOT NULL
        )
        SELECT band_label,
               COUNT(*) AS total,
               AVG(external_attention_score) AS avg_attention_score,
               AVG(external_crowding_penalty) AS avg_crowding_penalty,
               AVG(composite_priority_score - raw_composite_priority_score) AS avg_score_delta,
               AVG(price_20d_pct) AS avg_price_20d_pct,
               AVG(CASE WHEN price_20d_pct IS NULL THEN NULL WHEN price_20d_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate_20d,
               SUM(promoted_to_a) AS promoted_to_a_count,
               SUM(demoted_by_crowding) AS demoted_by_crowding_count
        FROM base
        GROUP BY band_label
        ORDER BY CASE band_label
                     WHEN '75+' THEN 0
                     WHEN '65-75' THEN 1
                     WHEN '55-65' THEN 2
                     ELSE 9
                 END
        """
    ).fetchall()

    penalty_band_rows = conn.execute(
        """
        WITH base AS (
            SELECT CASE
                       WHEN external_crowding_penalty >= 8 THEN '8+'
                       WHEN external_crowding_penalty >= 6 THEN '6-8'
                       WHEN external_crowding_penalty >= 3 THEN '3-6'
                       ELSE '<3'
                   END AS band_label,
                   external_attention_score,
                   external_crowding_penalty,
                   composite_priority_score,
                   raw_composite_priority_score,
                   price_20d_pct,
                   CASE WHEN composite_priority_score > raw_composite_priority_score THEN 1 ELSE 0 END AS score_up,
                   CASE WHEN composite_priority_score < raw_composite_priority_score THEN 1 ELSE 0 END AS score_down
            FROM mart_stock_trend
            WHERE external_crowding_penalty IS NOT NULL
        )
        SELECT band_label,
               COUNT(*) AS total,
               AVG(external_attention_score) AS avg_attention_score,
               AVG(external_crowding_penalty) AS avg_crowding_penalty,
               AVG(composite_priority_score - raw_composite_priority_score) AS avg_score_delta,
               AVG(price_20d_pct) AS avg_price_20d_pct,
               AVG(CASE WHEN price_20d_pct IS NULL THEN NULL WHEN price_20d_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate_20d,
               SUM(score_up) AS score_up_count,
               SUM(score_down) AS score_down_count
        FROM base
        GROUP BY band_label
        ORDER BY CASE band_label
                     WHEN '8+' THEN 0
                     WHEN '6-8' THEN 1
                     WHEN '3-6' THEN 2
                     ELSE 9
                 END
        """
    ).fetchall()

    signal_fields = [
        "signal_label",
        "total",
        "avg_attention_score",
        "avg_crowding_penalty",
        "avg_score_delta",
        "avg_price_20d_pct",
        "win_rate_20d",
        "avg_quality_score",
        "avg_stage_score",
        "promoted_to_a_count",
        "demoted_by_crowding_count",
    ]
    band_fields = [
        "band_label",
        "total",
        "avg_attention_score",
        "avg_crowding_penalty",
        "avg_score_delta",
        "avg_price_20d_pct",
        "win_rate_20d",
    ]
    score_bands = _serialize_rows(score_band_rows, band_fields + ["promoted_to_a_count", "demoted_by_crowding_count"])
    penalty_bands = _serialize_rows(penalty_band_rows, band_fields + ["score_up_count", "score_down_count"])
    signal_distribution = _serialize_rows(signal_rows, signal_fields)

    summary = {
        "total_stock_count": int(summary_row["total_stock_count"] or 0),
        "covered_stock_count": int(summary_row["covered_stock_count"] or 0),
        "coverage_ratio": _safe_round(
            (float(summary_row["covered_stock_count"] or 0) / float(summary_row["total_stock_count"] or 1)) * 100,
            1,
        ) if int(summary_row["total_stock_count"] or 0) > 0 else None,
        "signaled_stock_count": int(summary_row["signaled_stock_count"] or 0),
        "boosted_signal_count": int(summary_row["boosted_signal_count"] or 0),
        "crowded_signal_count": int(summary_row["crowded_signal_count"] or 0),
        "score_up_count": int(summary_row["score_up_count"] or 0),
        "score_down_count": int(summary_row["score_down_count"] or 0),
        "promoted_to_a_count": int(summary_row["promoted_to_a_count"] or 0),
        "promoted_to_b_count": int(summary_row["promoted_to_b_count"] or 0),
        "demoted_by_crowding_count": int(summary_row["demoted_by_crowding_count"] or 0),
        "avg_attention_score": _safe_round(summary_row["avg_attention_score"]),
        "avg_crowding_penalty": _safe_round(summary_row["avg_crowding_penalty"]),
        "avg_score_delta": _safe_round(summary_row["avg_score_delta"]),
        "snapshot_days": int(coverage_row["snapshot_days"] or 0),
        "latest_snapshot_date": coverage_row["latest_snapshot_date"],
        "latest_snapshot_rows": int(coverage_row["snapshot_rows"] or 0),
        "replay_ready_20d": int(coverage_row["snapshot_days"] or 0) >= 20,
        "replay_ready_60d": int(coverage_row["snapshot_days"] or 0) >= 60,
    }

    hints = []
    if summary["snapshot_days"] < 20:
        hints.append(
            f"外部关注快照历史仅 {summary['snapshot_days']} 天，当前只能做横截面校验，暂时不能把结果当作严格 20/60 日前瞻回放。"
        )
    if summary["promoted_to_a_count"] <= 10:
        hints.append("“外部确认增强”当前样本仍很少，Attention ≥ 72 的高门槛先保持保守，不宜提前放宽。")

    score_band_map = {item.get("band_label"): item for item in score_bands}
    top_band = score_band_map.get("75+") or {}
    mid_band = score_band_map.get("65-75") or {}
    low_band = score_band_map.get("<55") or {}
    if (
        top_band.get("avg_price_20d_pct") is not None
        and low_band.get("avg_price_20d_pct") is not None
        and float(top_band.get("avg_price_20d_pct") or 0) > float(low_band.get("avg_price_20d_pct") or 0)
    ):
        hints.append("Attention 分层已把当前强弱显著分开：高分层股票的近 20 日反馈明显好于低分层。")
    if (
        top_band.get("avg_price_20d_pct") is not None
        and mid_band.get("avg_price_20d_pct") is not None
        and float(top_band.get("avg_price_20d_pct") or 0) > float(mid_band.get("avg_price_20d_pct") or 0)
        and mid_band.get("avg_price_20d_pct") is not None
        and low_band.get("avg_price_20d_pct") is not None
        and float(mid_band.get("avg_price_20d_pct") or 0) > float(low_band.get("avg_price_20d_pct") or 0)
    ):
        hints.append("当前 55 / 65 / 75 三段 Attention 阈值具备明显层次感，暂时没有看到需要合并档位的证据。")

    penalty_band_map = {item.get("band_label"): item for item in penalty_bands}
    high_penalty = penalty_band_map.get("8+") or {}
    low_penalty = penalty_band_map.get("<3") or {}
    if (
        high_penalty.get("avg_score_delta") is not None
        and float(high_penalty.get("avg_score_delta") or 0) <= -6
    ):
        hints.append("拥挤惩罚 8+ 已经形成明显分数压降，当前更像防追高节流阀，而不是单纯的看空标签。")
    if (
        high_penalty.get("avg_price_20d_pct") is not None
        and low_penalty.get("avg_price_20d_pct") is not None
        and float(high_penalty.get("avg_price_20d_pct") or 0) > float(low_penalty.get("avg_price_20d_pct") or 0)
    ):
        hints.append("高拥挤组近期往往正是涨幅最大的股票，说明 crowding penalty 的职责是限制追高，而不是否定趋势本身。")

    return {
        "summary": summary,
        "methodology": "当前先做横截面校验：用 mart_stock_trend 的外部确认分、热度折扣、最终分差与近 20 日价格反馈，判断阈值是否把强弱分开；真正的 attention 前瞻回放要等快照历史积累到 20/60 日窗口后再打开。",
        "hints": hints,
        "signal_distribution": signal_distribution,
        "score_bands": score_bands,
        "penalty_bands": penalty_bands,
    }


def _load_attention_pool_linkage(conn, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause("t", sector)
    summary_row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN external_attention_signal IS NOT NULL THEN 1 ELSE 0 END) AS attention_signaled_count,
               SUM(CASE WHEN external_attention_signal IN ('外部确认增强', '关注度抬升', '调研活跃') THEN 1 ELSE 0 END) AS boosted_signal_count,
               SUM(CASE WHEN external_attention_signal = '热度拥挤' THEN 1 ELSE 0 END) AS crowded_signal_count,
               SUM(CASE WHEN raw_composite_priority_score < 75 AND composite_priority_score >= 75 THEN 1 ELSE 0 END) AS promoted_to_a_count,
               SUM(CASE WHEN raw_composite_priority_score < 60 AND composite_priority_score >= 60 AND COALESCE(external_attention_score, 0) >= 70 THEN 1 ELSE 0 END) AS promoted_to_b_count,
               SUM(CASE WHEN raw_composite_priority_score >= 75 AND composite_priority_score < 75 AND COALESCE(external_crowding_penalty, 0) >= 6 THEN 1 ELSE 0 END) AS demoted_by_crowding_count,
               SUM(CASE WHEN priority_pool = 'A池' AND external_attention_signal = '外部确认增强' THEN 1 ELSE 0 END) AS a_pool_confirm_count,
               SUM(CASE WHEN priority_pool IN ('A池', 'B池') AND external_attention_signal = '热度拥挤' THEN 1 ELSE 0 END) AS ab_pool_crowded_count,
               AVG(CASE WHEN external_attention_signal IN ('外部确认增强', '关注度抬升', '调研活跃') THEN composite_priority_score - raw_composite_priority_score END) AS boosted_avg_delta,
               AVG(CASE WHEN external_attention_signal = '热度拥挤' THEN composite_priority_score - raw_composite_priority_score END) AS crowded_avg_delta
        FROM mart_stock_trend t
        WHERE 1 = 1
        {sector_clause}
        """,
        sector_params,
    ).fetchone()

    sample_fields = [
        "stock_code",
        "stock_name",
        "priority_pool",
        "stock_archetype",
        "raw_composite_priority_score",
        "composite_priority_score",
        "external_attention_score",
        "external_crowding_penalty",
        "external_attention_signal",
        "price_20d_pct",
        "priority_pool_reason",
        "composite_cap_reason",
    ]

    promoted_rows = conn.execute(
        f"""
        SELECT stock_code,
               stock_name,
               priority_pool,
               stock_archetype,
               raw_composite_priority_score,
               composite_priority_score,
               external_attention_score,
               external_crowding_penalty,
               external_attention_signal,
               price_20d_pct,
               priority_pool_reason,
               composite_cap_reason
        FROM mart_stock_trend t
        WHERE 1 = 1
          {sector_clause}
          AND (
              (raw_composite_priority_score < 75 AND composite_priority_score >= 75)
              OR (raw_composite_priority_score < 60 AND composite_priority_score >= 60 AND COALESCE(external_attention_score, 0) >= 70)
          )
        ORDER BY COALESCE(composite_priority_score - raw_composite_priority_score, 0) DESC,
                 COALESCE(external_attention_score, 0) DESC,
                 stock_code
        LIMIT 12
        """,
        sector_params,
    ).fetchall()

    crowded_rows = conn.execute(
        f"""
        SELECT stock_code,
               stock_name,
               priority_pool,
               stock_archetype,
               raw_composite_priority_score,
               composite_priority_score,
               external_attention_score,
               external_crowding_penalty,
               external_attention_signal,
               price_20d_pct,
               priority_pool_reason,
               composite_cap_reason
        FROM mart_stock_trend t
        WHERE 1 = 1
          {sector_clause}
          AND COALESCE(external_crowding_penalty, 0) >= 6
          AND (
              composite_priority_score < raw_composite_priority_score
              OR COALESCE(composite_cap_reason, '') LIKE '%热度%'
              OR COALESCE(priority_pool_reason, '') LIKE '%热度%'
              OR external_attention_signal = '热度拥挤'
          )
        ORDER BY COALESCE(external_crowding_penalty, 0) DESC,
                 COALESCE(composite_priority_score - raw_composite_priority_score, 0) ASC,
                 stock_code
        LIMIT 12
        """,
        sector_params,
    ).fetchall()

    return {
        "summary": {
            "attention_signaled_count": int(summary_row["attention_signaled_count"] or 0),
            "boosted_signal_count": int(summary_row["boosted_signal_count"] or 0),
            "crowded_signal_count": int(summary_row["crowded_signal_count"] or 0),
            "promoted_to_a_count": int(summary_row["promoted_to_a_count"] or 0),
            "promoted_to_b_count": int(summary_row["promoted_to_b_count"] or 0),
            "demoted_by_crowding_count": int(summary_row["demoted_by_crowding_count"] or 0),
            "a_pool_confirm_count": int(summary_row["a_pool_confirm_count"] or 0),
            "ab_pool_crowded_count": int(summary_row["ab_pool_crowded_count"] or 0),
            "boosted_avg_delta": _safe_round(summary_row["boosted_avg_delta"]),
            "crowded_avg_delta": _safe_round(summary_row["crowded_avg_delta"]),
        },
        "promoted_samples": _serialize_rows(promoted_rows, sample_fields),
        "crowded_samples": _serialize_rows(crowded_rows, sample_fields),
    }


def _load_turtle_validation(conn, sector: str | None = None) -> dict:
    sector_clause, sector_params = _sector_exists_clause(
        "x",
        sector,
        level1_col=industry_level_db_column(SECTOR_LEVEL),
        fallback_to_dim_industry=True,
    )
    total_clause, total_params = _sector_exists_clause("t", sector)
    empty = {
        "summary": {
            "total_stock_count": 0,
            "covered_stock_count": 0,
            "coverage_ratio": None,
            "breakout_trigger_count": 0,
            "watchlist_count": 0,
            "exit_trigger_count": 0,
            "avg_execution_score": None,
            "avg_breakout_score": None,
            "avg_risk_score": None,
            "avg_price_20d_pct": None,
        },
        "methodology": "用 dim_stock_turtle_latest 的当前海龟执行特征做横截面验证，观察突破、待突破和退出状态是否把强弱股票区分开。",
        "hints": [],
        "state_distribution": [],
        "system_distribution": [],
        "score_bands": [],
    }
    try:
        total_row = conn.execute(
            f"""
            SELECT COUNT(*) AS total_stock_count
            FROM mart_stock_trend t
            WHERE 1 = 1
            {total_clause}
            """,
            total_params,
        ).fetchone()
        summary_row = conn.execute(
            f"""
            SELECT COUNT(*) AS covered_stock_count,
                 SUM(CASE WHEN x.turtle_setup_state IN ('S1突破触发', 'S2突破触发') THEN 1 ELSE 0 END) AS breakout_trigger_count,
                 SUM(CASE WHEN x.turtle_setup_state IN ('S1待突破', 'S2待突破') THEN 1 ELSE 0 END) AS watchlist_count,
                 SUM(CASE WHEN x.turtle_setup_state IN ('10日退出触发', '20日退出触发') THEN 1 ELSE 0 END) AS exit_trigger_count,
                 AVG(x.turtle_execution_score_v1) AS avg_execution_score,
                 AVG(x.turtle_breakout_score) AS avg_breakout_score,
                 AVG(x.turtle_risk_score) AS avg_risk_score,
                   AVG(t.price_20d_pct) AS avg_price_20d_pct
            FROM dim_stock_turtle_latest x
            LEFT JOIN mart_stock_trend t ON t.stock_code = x.stock_code
            WHERE 1 = 1
            {sector_clause}
            """,
            sector_params,
        ).fetchone()
        state_rows = conn.execute(
            f"""
             SELECT x.turtle_setup_state,
                   COUNT(*) AS total,
                 AVG(x.turtle_execution_score_v1) AS avg_execution_score,
                 AVG(x.turtle_breakout_score) AS avg_breakout_score,
                 AVG(x.turtle_risk_score) AS avg_risk_score,
                 AVG(x.stage_score_v1) AS avg_stage_score,
                 AVG(x.forecast_score_v1) AS avg_forecast_score,
                   AVG(t.price_20d_pct) AS avg_price_20d_pct,
                   SUM(CASE WHEN t.priority_pool = 'A池' THEN 1 ELSE 0 END) AS a_pool_count,
                   SUM(CASE WHEN t.priority_pool = 'D池' THEN 1 ELSE 0 END) AS d_pool_count
            FROM dim_stock_turtle_latest x
            LEFT JOIN mart_stock_trend t ON t.stock_code = x.stock_code
            WHERE 1 = 1
            {sector_clause}
             GROUP BY x.turtle_setup_state
             ORDER BY CASE x.turtle_setup_state
                         WHEN 'S2突破触发' THEN 0
                         WHEN 'S1突破触发' THEN 1
                         WHEN 'S2待突破' THEN 2
                         WHEN 'S1待突破' THEN 3
                         WHEN '10日退出触发' THEN 4
                         WHEN '20日退出触发' THEN 5
                         WHEN '等待形态' THEN 9
                         ELSE 10
                     END
            """,
            sector_params,
        ).fetchall()
        system_rows = conn.execute(
            f"""
             SELECT x.preferred_system,
                   COUNT(*) AS total,
                 AVG(x.turtle_execution_score_v1) AS avg_execution_score,
                 AVG(x.turtle_breakout_score) AS avg_breakout_score,
                 AVG(x.turtle_risk_score) AS avg_risk_score,
                   AVG(t.price_20d_pct) AS avg_price_20d_pct
            FROM dim_stock_turtle_latest x
            LEFT JOIN mart_stock_trend t ON t.stock_code = x.stock_code
            WHERE 1 = 1
            {sector_clause}
             GROUP BY x.preferred_system
             ORDER BY CASE x.preferred_system
                         WHEN 'S2' THEN 0
                         WHEN 'S1' THEN 1
                         WHEN '观察' THEN 9
                         ELSE 10
                     END
            """,
            sector_params,
        ).fetchall()
        band_rows = conn.execute(
            f"""
            WITH base AS (
                SELECT CASE
                           WHEN x.turtle_execution_score_v1 >= 75 THEN '75+'
                           WHEN x.turtle_execution_score_v1 >= 60 THEN '60-75'
                           WHEN x.turtle_execution_score_v1 >= 45 THEN '45-60'
                           ELSE '<45'
                       END AS band_label,
                       x.turtle_setup_state,
                       x.turtle_execution_score_v1,
                       t.price_20d_pct
                FROM dim_stock_turtle_latest x
                LEFT JOIN mart_stock_trend t ON t.stock_code = x.stock_code
                WHERE 1 = 1
                {sector_clause}
            )
            SELECT band_label,
                   COUNT(*) AS total,
                   AVG(turtle_execution_score_v1) AS avg_execution_score,
                   AVG(price_20d_pct) AS avg_price_20d_pct,
                   SUM(CASE WHEN turtle_setup_state IN ('S1突破触发', 'S2突破触发') THEN 1 ELSE 0 END) AS breakout_trigger_count,
                   SUM(CASE WHEN turtle_setup_state IN ('10日退出触发', '20日退出触发') THEN 1 ELSE 0 END) AS exit_trigger_count
            FROM base
            GROUP BY band_label
            ORDER BY CASE band_label
                         WHEN '75+' THEN 0
                         WHEN '60-75' THEN 1
                         WHEN '45-60' THEN 2
                         ELSE 9
                     END
            """,
            sector_params,
        ).fetchall()
    except Exception:
        logger.warning("[股票验证] 海龟验证加载失败", exc_info=True)
        return empty

    total_stock_count = int((total_row["total_stock_count"] if total_row else 0) or 0)
    covered_stock_count = int((summary_row["covered_stock_count"] if summary_row else 0) or 0)
    summary = {
        "total_stock_count": total_stock_count,
        "covered_stock_count": covered_stock_count,
        "coverage_ratio": _safe_round(covered_stock_count / total_stock_count * 100, 1) if total_stock_count else None,
        "breakout_trigger_count": int(summary_row["breakout_trigger_count"] or 0),
        "watchlist_count": int(summary_row["watchlist_count"] or 0),
        "exit_trigger_count": int(summary_row["exit_trigger_count"] or 0),
        "avg_execution_score": _safe_round(summary_row["avg_execution_score"]),
        "avg_breakout_score": _safe_round(summary_row["avg_breakout_score"]),
        "avg_risk_score": _safe_round(summary_row["avg_risk_score"]),
        "avg_price_20d_pct": _safe_round(summary_row["avg_price_20d_pct"]),
    }
    state_distribution = _serialize_rows(
        state_rows,
        [
            "turtle_setup_state",
            "total",
            "avg_execution_score",
            "avg_breakout_score",
            "avg_risk_score",
            "avg_stage_score",
            "avg_forecast_score",
            "avg_price_20d_pct",
            "a_pool_count",
            "d_pool_count",
        ],
    )
    system_distribution = _serialize_rows(
        system_rows,
        [
            "preferred_system",
            "total",
            "avg_execution_score",
            "avg_breakout_score",
            "avg_risk_score",
            "avg_price_20d_pct",
        ],
    )
    score_bands = _serialize_rows(
        band_rows,
        [
            "band_label",
            "total",
            "avg_execution_score",
            "avg_price_20d_pct",
            "breakout_trigger_count",
            "exit_trigger_count",
        ],
    )

    state_map = {item.get("turtle_setup_state"): item for item in state_distribution}
    hints = []
    if summary["coverage_ratio"] is not None and summary["coverage_ratio"] < 90:
        hints.append("海龟特征覆盖率仍未打满，当前结果更适合做研究观察，不应直接当作全量执行信号。")
    breakout_row = state_map.get("S2突破触发") or state_map.get("S1突破触发") or {}
    wait_row = state_map.get("S1待突破") or state_map.get("S2待突破") or state_map.get("等待形态") or {}
    exit_row = state_map.get("20日退出触发") or state_map.get("10日退出触发") or {}
    if breakout_row.get("avg_price_20d_pct") is not None and wait_row.get("avg_price_20d_pct") is not None:
        if float(breakout_row["avg_price_20d_pct"] or 0) > float(wait_row["avg_price_20d_pct"] or 0):
            hints.append("海龟突破状态已把近期更强的股票区分出来，当前可作为执行层优先级参考。")
    if exit_row.get("avg_price_20d_pct") is not None and wait_row.get("avg_price_20d_pct") is not None:
        if float(exit_row["avg_price_20d_pct"] or 0) < float(wait_row["avg_price_20d_pct"] or 0):
            hints.append("退出状态对应的近20日表现更弱，说明退出通道在当前横截面上具备风险过滤价值。")

    return {
        "summary": summary,
        "methodology": "用 dim_stock_turtle_latest 的当前海龟执行特征做横截面验证，观察突破、待突破和退出状态是否把强弱股票区分开。",
        "hints": hints,
        "state_distribution": state_distribution,
        "system_distribution": system_distribution,
        "score_bands": score_bands,
    }


def get_stock_validation_report(conn, sector: str | None = None) -> dict:
    normalized_sector = _normalize_sector(sector)
    pools = _load_pool_feedback(conn, normalized_sector)
    snapshot_replay = _load_snapshot_pool_replay(conn, normalized_sector)
    anomalies = _load_anomalies(conn, normalized_sector)
    attention_linkage = _load_attention_pool_linkage(conn, normalized_sector)
    turtle_validation = _load_turtle_validation(conn, normalized_sector)
    audit = _load_audit_snapshot(conn)
    qlib_summary = _load_qlib_summary(conn)

    total = sum(int(item.get("total") or 0) for item in pools)
    pool_map = {item.get("priority_pool"): item for item in pools}
    capped_total = sum(int(item.get("capped_count") or 0) for item in pools)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "stock_count": total,
        "a_pool_count": int((pool_map.get("A池") or {}).get("total") or 0),
        "b_pool_count": int((pool_map.get("B池") or {}).get("total") or 0),
        "c_pool_count": int((pool_map.get("C池") or {}).get("total") or 0),
        "d_pool_count": int((pool_map.get("D池") or {}).get("total") or 0),
        "capped_total": capped_total,
        "anomaly_total": sum(int(value or 0) for value in (anomalies.get("counts") or {}).values()),
        "audit_score": audit.get("audit_score"),
        "snapshot_scored_rows": snapshot_replay["coverage"].get("scored_rows", 0),
        "snapshot_scored_dates": snapshot_replay["coverage"].get("scored_snapshot_dates", 0),
        "attention_signaled_count": int((attention_linkage.get("summary") or {}).get("attention_signaled_count") or 0),
        "attention_boosted_count": int((attention_linkage.get("summary") or {}).get("boosted_signal_count") or 0),
        "attention_crowded_count": int((attention_linkage.get("summary") or {}).get("crowded_signal_count") or 0),
        "attention_promoted_to_a_count": int((attention_linkage.get("summary") or {}).get("promoted_to_a_count") or 0),
        "attention_promoted_to_b_count": int((attention_linkage.get("summary") or {}).get("promoted_to_b_count") or 0),
        "crowding_demoted_count": int((attention_linkage.get("summary") or {}).get("demoted_by_crowding_count") or 0),
        "a_pool_confirm_count": int((attention_linkage.get("summary") or {}).get("a_pool_confirm_count") or 0),
        "ab_pool_crowded_count": int((attention_linkage.get("summary") or {}).get("ab_pool_crowded_count") or 0),
        "turtle_covered_count": int((turtle_validation.get("summary") or {}).get("covered_stock_count") or 0),
        "turtle_breakout_trigger_count": int((turtle_validation.get("summary") or {}).get("breakout_trigger_count") or 0),
        "turtle_watchlist_count": int((turtle_validation.get("summary") or {}).get("watchlist_count") or 0),
        "turtle_exit_trigger_count": int((turtle_validation.get("summary") or {}).get("exit_trigger_count") or 0),
        "qlib_prediction_count": int(qlib_summary.get("prediction_count") or 0),
        "qlib_model_id": qlib_summary.get("model_id"),
        "qlib_predict_date": qlib_summary.get("predict_date"),
    }

    return {
        "scope": {
            "sector": normalized_sector,
            "mode": "sector" if normalized_sector else "all",
            "audit_scope": "global",
        },
        "summary": summary,
        "pool_feedback": pools,
        "attention_linkage": attention_linkage,
        "turtle_validation": turtle_validation,
        "snapshot_pool_replay": snapshot_replay,
        "anomalies": anomalies,
        "audit": audit,
        "qlib_summary": qlib_summary,
    }
