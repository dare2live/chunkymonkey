"""
sector_forecast_engine.py — 行业级 Qlib 预测特征层

把股票侧 Qlib 截面排序 / 行业内相对排序 / 风险收益性价比
与行业动量/轮动状态聚合为行业级前瞻信号，用于：
- 行业页展示
- ETF 挖掘页的“下一轮动板块”观察名单
- 后续行业级验证与快照
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from services.industry import industry_level_db_column, industry_level_nonempty_condition, industry_level_select
from services.qlib_full_engine import get_default_model_id
from services.utils import safe_float as _safe_float, clamp_score as _clamp_score

logger = logging.getLogger("cm-api")

SECTOR_LEVEL = 1


def _sector_expr(alias: str) -> str:
    return f"{alias}.{industry_level_db_column(SECTOR_LEVEL)}"


def ensure_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fact_sector_forecast_features (
            snapshot_date                    TEXT NOT NULL,
            model_id                         TEXT NOT NULL,
            sector_name                      TEXT NOT NULL,
            stock_count                      INTEGER DEFAULT 0,
            avg_qlib_score                   REAL,
            avg_qlib_percentile              REAL,
            avg_forecast_cross_section_score REAL,
            avg_forecast_20d_score           REAL,
            avg_forecast_industry_relative_score REAL,
            avg_forecast_60d_excess_score    REAL,
            avg_forecast_risk_adjusted_score REAL,
            high_conviction_count            INTEGER DEFAULT 0,
            sector_rotation_score            REAL,
            sector_rotation_rank             INTEGER,
            sector_rotation_rank_1m          INTEGER,
            sector_rotation_rank_3m          INTEGER,
            sector_rotation_bucket           TEXT,
            sector_trend_state               TEXT,
            sector_momentum_score            REAL,
            next_rotation_score              REAL,
            next_rotation_label              TEXT,
            next_rotation_reason             TEXT,
            updated_at                       TEXT,
            PRIMARY KEY (snapshot_date, model_id, sector_name)
        );
        CREATE INDEX IF NOT EXISTS idx_fsrf_score
            ON fact_sector_forecast_features(model_id, next_rotation_score DESC);

        CREATE TABLE IF NOT EXISTS dim_sector_forecast_latest (
            sector_name                      TEXT PRIMARY KEY,
            snapshot_date                    TEXT,
            model_id                         TEXT,
            stock_count                      INTEGER DEFAULT 0,
            avg_qlib_score                   REAL,
            avg_qlib_percentile              REAL,
            avg_forecast_cross_section_score REAL,
            avg_forecast_20d_score           REAL,
            avg_forecast_industry_relative_score REAL,
            avg_forecast_60d_excess_score    REAL,
            avg_forecast_risk_adjusted_score REAL,
            high_conviction_count            INTEGER DEFAULT 0,
            sector_rotation_score            REAL,
            sector_rotation_rank             INTEGER,
            sector_rotation_rank_1m          INTEGER,
            sector_rotation_rank_3m          INTEGER,
            sector_rotation_bucket           TEXT,
            sector_trend_state               TEXT,
            sector_momentum_score            REAL,
            next_rotation_score              REAL,
            next_rotation_label              TEXT,
            next_rotation_reason             TEXT,
            updated_at                       TEXT
        );
        """
    )
    for ddl in [
        "ALTER TABLE fact_sector_forecast_features ADD COLUMN avg_forecast_cross_section_score REAL",
        "ALTER TABLE fact_sector_forecast_features ADD COLUMN avg_forecast_industry_relative_score REAL",
        "ALTER TABLE dim_sector_forecast_latest ADD COLUMN avg_forecast_cross_section_score REAL",
        "ALTER TABLE dim_sector_forecast_latest ADD COLUMN avg_forecast_industry_relative_score REAL",
    ]:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()


def _resolve_forecast_source(conn, model_id: Optional[str] = None) -> Optional[dict]:
    ensure_tables(conn)
    preferred_model_id = get_default_model_id(conn, model_id=model_id)
    row = conn.execute(
        """
        SELECT model_id,
               snapshot_date,
               COUNT(*) AS stock_count,
               MAX(predict_date) AS predict_date
        FROM dim_stock_forecast_latest
        WHERE model_id IS NOT NULL
        GROUP BY model_id, snapshot_date
        ORDER BY
            CASE WHEN model_id = ? THEN 0 ELSE 1 END,
            CASE WHEN snapshot_date IS NULL OR snapshot_date = '' THEN 1 ELSE 0 END,
            snapshot_date DESC,
            stock_count DESC,
            model_id DESC
        LIMIT 1
        """,
        (preferred_model_id or "",),
    ).fetchone()
    return dict(row) if row else None


def build_sector_forecast_features(
    conn,
    snapshot_date: Optional[str] = None,
    model_id: Optional[str] = None,
) -> int:
    ensure_tables(conn)
    source = _resolve_forecast_source(conn, model_id=model_id)
    snapshot_date = snapshot_date or (source or {}).get("snapshot_date") or date.today().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    if not source:
        conn.execute("DELETE FROM dim_sector_forecast_latest")
        conn.commit()
        logger.info("[行业预测] 无可用股票预测快照，跳过构建")
        return 0

    model_id = source["model_id"]
    params = [model_id]
    snapshot_filter = ""
    if snapshot_date:
        snapshot_filter = " AND sf.snapshot_date = ?"
        params.append(snapshot_date)
    rows = conn.execute(
        f"""
        SELECT sf.tdx_l1_name AS sector_name,
               COUNT(*) AS stock_count,
               AVG(sf.qlib_score) AS avg_qlib_score,
               AVG(sf.qlib_percentile) AS avg_qlib_percentile,
             AVG(sf.forecast_20d_score) AS avg_forecast_cross_section_score,
               AVG(sf.forecast_20d_score) AS avg_forecast_20d_score,
             AVG(sf.forecast_60d_excess_score) AS avg_forecast_industry_relative_score,
               AVG(sf.forecast_60d_excess_score) AS avg_forecast_60d_excess_score,
               AVG(sf.forecast_risk_adjusted_score) AS avg_forecast_risk_adjusted_score,
               SUM(CASE WHEN sf.forecast_20d_score >= 75 THEN 1 ELSE 0 END) AS high_conviction_count,
               msm.rotation_score AS sector_rotation_score,
               msm.rotation_rank AS sector_rotation_rank,
               msm.rotation_rank_1m AS sector_rotation_rank_1m,
               msm.rotation_rank_3m AS sector_rotation_rank_3m,
               msm.rotation_bucket AS sector_rotation_bucket,
               msm.trend_state AS sector_trend_state,
               msm.momentum_score AS sector_momentum_score
        FROM dim_stock_forecast_latest sf
        LEFT JOIN mart_sector_momentum msm ON msm.sector_name = sf.tdx_l1_name
        WHERE sf.tdx_l1_name IS NOT NULL
          AND sf.tdx_l1_name != ''
          AND sf.model_id = ?{snapshot_filter}
        GROUP BY sf.tdx_l1_name
        HAVING COUNT(*) >= 5
        """,
        params,
    ).fetchall()

    conn.execute(
        "DELETE FROM fact_sector_forecast_features WHERE snapshot_date = ? OR model_id = ?",
        (snapshot_date, model_id),
    )

    inserted = 0
    for row in rows:
        item = dict(row)
        avg_q_pct = _safe_float(item.get("avg_qlib_percentile")) or 0.0
        avg_cross = _safe_float(item.get("avg_forecast_cross_section_score")) or avg_q_pct
        avg_relative = _safe_float(item.get("avg_forecast_industry_relative_score")) or avg_cross
        avg_risk = _safe_float(item.get("avg_forecast_risk_adjusted_score")) or 50.0
        momentum = _safe_float(item.get("sector_momentum_score")) or 50.0
        improve = (item.get("sector_rotation_rank_3m") or 99) - (item.get("sector_rotation_rank_1m") or 99)
        high_count = int(item.get("high_conviction_count") or 0)
        stock_count = int(item.get("stock_count") or 0)
        density = (high_count / stock_count) * 100.0 if stock_count else 0.0

        trend_bonus = {
            "recovering": 8.0,
            "bullish": 5.0,
            "neutral": 0.0,
            "weakening": -6.0,
            "bearish": -10.0,
        }.get(item.get("sector_trend_state") or "", 0.0)

        next_rotation_score = _clamp_score(
            avg_cross * 0.38
            + avg_relative * 0.24
            + avg_risk * 0.20
            + density * 0.10
            + ((momentum - 50.0) * 0.10)
            + max(min(improve * 2.5, 12.0), -8.0)
            + trend_bonus
        )

        label = "继续观察"
        bucket = item.get("sector_rotation_bucket") or ""
        if bucket == "leader" and next_rotation_score >= 72:
            label = "维持强势"
        elif bucket != "leader" and next_rotation_score >= 72:
            label = "下一个轮动候选"
        elif bucket == "blacklist" and next_rotation_score < 55:
            label = "继续回避"
        elif next_rotation_score >= 60:
            label = "预热观察"

        reasons = []
        if avg_cross >= 65:
            reasons.append("Qlib截面排序较强")
        if avg_relative >= 65:
            reasons.append("行业内相对排序靠前")
        if density >= 20:
            reasons.append("高置信股票开始增多")
        if improve >= 4:
            reasons.append("短期轮动名次改善")
        if item.get("sector_trend_state") == "recovering":
            reasons.append("行业趋势处于回升")
        if not reasons:
            reasons.append("Qlib与行业动量暂未共振")
        reason = "；".join(reasons[:3])

        conn.execute(
            """
            INSERT OR REPLACE INTO fact_sector_forecast_features (
                snapshot_date, model_id, sector_name, stock_count,
                avg_qlib_score, avg_qlib_percentile,
                avg_forecast_cross_section_score,
                avg_forecast_20d_score, avg_forecast_60d_excess_score,
                avg_forecast_industry_relative_score,
                avg_forecast_risk_adjusted_score, high_conviction_count,
                sector_rotation_score, sector_rotation_rank,
                sector_rotation_rank_1m, sector_rotation_rank_3m,
                sector_rotation_bucket, sector_trend_state, sector_momentum_score,
                next_rotation_score, next_rotation_label, next_rotation_reason, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                model_id,
                item.get("sector_name"),
                stock_count,
                item.get("avg_qlib_score"),
                item.get("avg_qlib_percentile"),
                avg_cross,
                item.get("avg_forecast_20d_score"),
                item.get("avg_forecast_60d_excess_score"),
                avg_relative,
                item.get("avg_forecast_risk_adjusted_score"),
                high_count,
                item.get("sector_rotation_score"),
                item.get("sector_rotation_rank"),
                item.get("sector_rotation_rank_1m"),
                item.get("sector_rotation_rank_3m"),
                item.get("sector_rotation_bucket"),
                item.get("sector_trend_state"),
                item.get("sector_momentum_score"),
                next_rotation_score,
                label,
                reason,
                now,
            ),
        )
        inserted += 1

    conn.execute("DELETE FROM dim_sector_forecast_latest")
    conn.execute(
        """
        INSERT INTO dim_sector_forecast_latest (
            sector_name, snapshot_date, model_id, stock_count,
            avg_qlib_score, avg_qlib_percentile,
            avg_forecast_cross_section_score,
            avg_forecast_20d_score, avg_forecast_60d_excess_score,
            avg_forecast_industry_relative_score,
            avg_forecast_risk_adjusted_score, high_conviction_count,
            sector_rotation_score, sector_rotation_rank,
            sector_rotation_rank_1m, sector_rotation_rank_3m,
            sector_rotation_bucket, sector_trend_state, sector_momentum_score,
            next_rotation_score, next_rotation_label, next_rotation_reason, updated_at
        )
        SELECT sector_name, snapshot_date, model_id, stock_count,
               avg_qlib_score, avg_qlib_percentile,
               avg_forecast_cross_section_score,
               avg_forecast_20d_score, avg_forecast_60d_excess_score,
               avg_forecast_industry_relative_score,
               avg_forecast_risk_adjusted_score, high_conviction_count,
               sector_rotation_score, sector_rotation_rank,
               sector_rotation_rank_1m, sector_rotation_rank_3m,
               sector_rotation_bucket, sector_trend_state, sector_momentum_score,
               next_rotation_score, next_rotation_label, next_rotation_reason, updated_at
        FROM fact_sector_forecast_features
        WHERE snapshot_date = ? AND model_id = ?
        """,
        (snapshot_date, model_id),
    )
    conn.commit()
    logger.info(f"[行业预测] 构建完成: {inserted} 个行业, 模型 {model_id}")
    return inserted


def get_latest_sector_forecast_snapshot(
    conn,
    *,
    limit: int = 12,
    auto_build: bool = True,
    model_id: Optional[str] = None,
) -> list[dict]:
    ensure_tables(conn)
    source = _resolve_forecast_source(conn, model_id=model_id)
    if not source:
        return []

    source_model_id = source.get("model_id")
    source_snapshot_date = source.get("snapshot_date") or date.today().strftime("%Y-%m-%d")
    latest_row = conn.execute(
        """
        SELECT model_id, snapshot_date, COUNT(*) AS row_count
        FROM dim_sector_forecast_latest
        GROUP BY model_id, snapshot_date
        ORDER BY
            CASE WHEN model_id = ? THEN 0 ELSE 1 END,
            snapshot_date DESC,
            row_count DESC,
            model_id DESC
        LIMIT 1
        """,
        (source_model_id,),
    ).fetchone()
    if auto_build and (
        not latest_row
        or latest_row["model_id"] != source_model_id
        or (latest_row["snapshot_date"] or "") != source_snapshot_date
    ):
        build_sector_forecast_features(conn, snapshot_date=source_snapshot_date, model_id=source_model_id)

    rows = conn.execute(
        """
        SELECT *
        FROM dim_sector_forecast_latest
        WHERE model_id = ?
        ORDER BY next_rotation_score DESC,
                 avg_forecast_cross_section_score DESC,
                 avg_qlib_percentile DESC,
                 sector_name
        LIMIT ?
        """,
        (source_model_id, max(int(limit or 0), 1)),
    ).fetchall()
    return [dict(row) for row in rows]
