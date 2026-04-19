"""
stock_forecast_engine.py — 股票预测特征中间事实层

把 Qlib 横截面预测结果拆成结构化研究分：
- Qlib 截面排序分
- 行业内相对排序分
- 波动收益性价比分

供评分、解释页和训练后回流统一复用。

兼容说明：
- 历史列 forecast_20d_score 实际承载 Qlib 截面排序分
- 历史列 forecast_60d_excess_score 实际承载 行业内相对排序分
新代码统一暴露语义化别名，并继续回写旧列，避免旧接口/旧数据读取中断。
"""

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from services.industry import industry_level_value, load_industry_map
from services.qlib_full_engine import ensure_tables as ensure_qlib_tables
from services.qlib_full_engine import get_default_model_id
from services.qlib_full_engine import sync_latest_predictions_to_stock_trend
from services.utils import safe_float as _safe_float, percentile_ranks as _percentile_ranks, clamp_score as _clamp_score

logger = logging.getLogger("cm-api")

FORECAST_CROSS_SECTION_SCORE_FIELD = "forecast_cross_section_score"
LEGACY_FORECAST_CROSS_SECTION_SCORE_FIELD = "forecast_20d_score"
FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD = "forecast_industry_relative_score"
LEGACY_FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD = "forecast_60d_excess_score"
FORECAST_INDUSTRY_GROUP_LEVEL2_PREFIX = "二级行业:"
FORECAST_INDUSTRY_GROUP_LEVEL1_PREFIX = "一级行业:"
FORECAST_INDUSTRY_GROUP_ALL_FALLBACK = "全市场回退"


def _build_industry_relative_group(level: str, name: Optional[str] = None) -> str:
    if level == "l2":
        return f"{FORECAST_INDUSTRY_GROUP_LEVEL2_PREFIX}{name or ''}"
    if level == "l1":
        return f"{FORECAST_INDUSTRY_GROUP_LEVEL1_PREFIX}{name or ''}"
    return FORECAST_INDUSTRY_GROUP_ALL_FALLBACK


def normalize_industry_relative_group(group: Optional[str]) -> Optional[str]:
    if group is None:
        return None
    text = str(group).strip()
    if not text:
        return text
    if text.startswith("SW2:"):
        return _build_industry_relative_group("l2", text[4:])
    if text.startswith("SW1:"):
        return _build_industry_relative_group("l1", text[4:])
    if text == "ALL_FALLBACK":
        return FORECAST_INDUSTRY_GROUP_ALL_FALLBACK
    return text


def apply_forecast_score_aliases(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    cross_section_score = _safe_float(item.get(FORECAST_CROSS_SECTION_SCORE_FIELD))
    if cross_section_score is None:
        cross_section_score = _safe_float(item.get(LEGACY_FORECAST_CROSS_SECTION_SCORE_FIELD))
    industry_relative_score = _safe_float(item.get(FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD))
    if industry_relative_score is None:
        industry_relative_score = _safe_float(item.get(LEGACY_FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD))

    item[FORECAST_CROSS_SECTION_SCORE_FIELD] = cross_section_score
    item[LEGACY_FORECAST_CROSS_SECTION_SCORE_FIELD] = cross_section_score
    item[FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD] = industry_relative_score
    item[LEGACY_FORECAST_INDUSTRY_RELATIVE_SCORE_FIELD] = industry_relative_score
    for group_field in ("industry_relative_group", "forecast_industry_relative_group"):
        if group_field in item:
            item[group_field] = normalize_industry_relative_group(item.get(group_field))
    return item


def ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fact_stock_forecast_features (
            snapshot_date                    TEXT NOT NULL,
            model_id                         TEXT NOT NULL,
            predict_date                     TEXT,
            stock_code                       TEXT NOT NULL,
            stock_name                       TEXT,
            tdx_l1                           TEXT,
            tdx_l2                           TEXT,
            qlib_score                       REAL,
            qlib_rank                        INTEGER,
            qlib_percentile                  REAL,
            industry_qlib_percentile         REAL,
            industry_relative_group          TEXT,
            volatility_20d                   REAL,
            max_drawdown_60d                 REAL,
            volatility_rank                  REAL,
            drawdown_rank                    REAL,
            forecast_cross_section_score     REAL,
            forecast_20d_score               REAL,
            forecast_industry_relative_score REAL,
            forecast_60d_excess_score        REAL,
            forecast_risk_adjusted_score     REAL,
            forecast_score_v1                REAL,
            forecast_reason                  TEXT,
            updated_at                       TEXT,
            PRIMARY KEY (snapshot_date, model_id, stock_code)
        );
        CREATE INDEX IF NOT EXISTS idx_fsff_model ON fact_stock_forecast_features(model_id, stock_code);

        CREATE TABLE IF NOT EXISTS dim_stock_forecast_latest (
            stock_code                       TEXT PRIMARY KEY,
            snapshot_date                    TEXT,
            model_id                         TEXT,
            predict_date                     TEXT,
            stock_name                       TEXT,
            tdx_l1                           TEXT,
            tdx_l2                           TEXT,
            qlib_score                       REAL,
            qlib_rank                        INTEGER,
            qlib_percentile                  REAL,
            industry_qlib_percentile         REAL,
            industry_relative_group          TEXT,
            volatility_20d                   REAL,
            max_drawdown_60d                 REAL,
            volatility_rank                  REAL,
            drawdown_rank                    REAL,
            forecast_cross_section_score     REAL,
            forecast_20d_score               REAL,
            forecast_industry_relative_score REAL,
            forecast_60d_excess_score        REAL,
            forecast_risk_adjusted_score     REAL,
            forecast_score_v1                REAL,
            forecast_reason                  TEXT,
            updated_at                       TEXT
        );
    """)
    for ddl in [
        "ALTER TABLE fact_stock_forecast_features ADD COLUMN forecast_cross_section_score REAL",
        "ALTER TABLE fact_stock_forecast_features ADD COLUMN forecast_industry_relative_score REAL",
        "ALTER TABLE dim_stock_forecast_latest ADD COLUMN forecast_cross_section_score REAL",
        "ALTER TABLE dim_stock_forecast_latest ADD COLUMN forecast_industry_relative_score REAL",
    ]:
        try:
            conn.execute(ddl)
        except Exception:
            pass
    conn.commit()


def build_stock_forecast_features(conn, snapshot_date: Optional[str] = None) -> int:
    ensure_qlib_tables(conn)
    ensure_tables(conn)
    snapshot_date = snapshot_date or date.today().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    model_id = get_default_model_id(conn)
    if not model_id:
        conn.execute("DELETE FROM dim_stock_forecast_latest")
        conn.commit()
        logger.info("[预测特征] 无可用 Qlib 模型，跳过构建")
        return 0

    sync_latest_predictions_to_stock_trend(conn, model_id=model_id)
    industry_map = load_industry_map(conn)

    pred_rows = conn.execute("""
        SELECT p.model_id, p.stock_code, p.stock_name, p.predict_date,
               p.qlib_score, p.qlib_rank, p.qlib_percentile,
               i.tdx_l1, i.tdx_l2,
               s.volatility_20d, s.max_drawdown_60d
        FROM qlib_predictions p
        LEFT JOIN dim_stock_tdx_industry i ON i.stock_code = p.stock_code
        LEFT JOIN dim_stock_stage_latest s ON s.stock_code = p.stock_code
        WHERE p.model_id = ?
    """, (model_id,)).fetchall()
    if not pred_rows:
        conn.execute("DELETE FROM dim_stock_forecast_latest")
        conn.commit()
        logger.info(f"[预测特征] 模型 {model_id} 无预测结果，跳过构建")
        return 0

    rows = []
    for row in pred_rows:
        item = dict(row)
        industry = industry_map.get(item["stock_code"]) or {}
        item["sw_level1"] = industry_level_value(industry, 1)
        item["sw_level2"] = industry_level_value(industry, 2)
        rows.append(item)
    by_group = {("all", "all"): list(rows)}
    for row in rows:
        if row.get("tdx_l2"):
            by_group.setdefault(("l2", row["tdx_l2"]), []).append(row)
        if row.get("tdx_l1"):
            by_group.setdefault(("l1", row["tdx_l1"]), []).append(row)

    group_sizes = {key: len(group_rows) for key, group_rows in by_group.items()}
    group_rank_map = {}
    for (level, name), group_rows in by_group.items():
        scores = [_safe_float(row.get("qlib_score")) for row in group_rows]
        ranks = _percentile_ranks(scores)
        for row, rank in zip(group_rows, ranks):
            if rank is not None:
                group_rank_map[(level, name, row["stock_code"])] = rank

    vol_ranks = _percentile_ranks([(-_safe_float(row.get("volatility_20d")) if _safe_float(row.get("volatility_20d")) is not None else None) for row in rows])
    dd_ranks = _percentile_ranks([(-_safe_float(row.get("max_drawdown_60d")) if _safe_float(row.get("max_drawdown_60d")) is not None else None) for row in rows])

    conn.execute("DELETE FROM fact_stock_forecast_features WHERE snapshot_date = ? OR model_id = ?", (snapshot_date, model_id))
    inserted = 0
    for idx, row in enumerate(rows):
        stock_code = row["stock_code"]
        tdx2 = row.get("tdx_l2")
        tdx1 = row.get("tdx_l1")
        if tdx2 and group_sizes.get(("l2", tdx2), 0) >= 15:
            industry_pct = group_rank_map.get(("l2", tdx2, stock_code))
            rel_group = f"TDX2:{tdx2}"
        elif tdx1 and group_sizes.get(("l1", tdx1), 0) >= 20:
            industry_pct = group_rank_map.get(("l1", tdx1, stock_code))
            rel_group = f"TDX1:{tdx1}"
        else:
            industry_pct = group_rank_map.get(("all", "all", stock_code))
            rel_group = FORECAST_INDUSTRY_GROUP_ALL_FALLBACK

        qlib_pct = _safe_float(row.get("qlib_percentile"))
        vol_rank = vol_ranks[idx]
        dd_rank = dd_ranks[idx]
        forecast_cross_section_score = _clamp_score(qlib_pct if qlib_pct is not None else 50.0)
        forecast_industry_relative_score = _clamp_score(
            industry_pct if industry_pct is not None else forecast_cross_section_score
        )
        forecast_20d_score = forecast_cross_section_score
        forecast_60d_excess_score = forecast_industry_relative_score
        risk_adjusted = _clamp_score(
            forecast_cross_section_score * 0.55
            + (vol_rank if vol_rank is not None else 50.0) * 0.25
            + (dd_rank if dd_rank is not None else 50.0) * 0.20
        )
        forecast_score_v1 = _clamp_score(
            forecast_cross_section_score * 0.40
            + forecast_industry_relative_score * 0.40
            + risk_adjusted * 0.20
        )

        reasons = []
        if forecast_cross_section_score >= 75:
            reasons.append("Qlib截面排序较强")
        if forecast_industry_relative_score >= 70:
            reasons.append("行业内相对排序靠前")
        if risk_adjusted >= 70:
            reasons.append("波动收益性价比较好")
        if not reasons:
            reasons.append("Qlib排序结构中性")
        forecast_reason = "；".join(reasons[:2])

        conn.execute("""
            INSERT OR REPLACE INTO fact_stock_forecast_features (
                snapshot_date, model_id, predict_date, stock_code, stock_name,
                tdx_l1, tdx_l2, qlib_score, qlib_rank, qlib_percentile,
                industry_qlib_percentile, industry_relative_group,
                volatility_20d, max_drawdown_60d, volatility_rank, drawdown_rank,
                forecast_cross_section_score,
                forecast_20d_score, forecast_60d_excess_score,
                forecast_industry_relative_score,
                forecast_risk_adjusted_score, forecast_score_v1, forecast_reason, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot_date,
            model_id,
            row.get("predict_date"),
            stock_code,
            row.get("stock_name"),
            tdx1,
            tdx2,
            row.get("qlib_score"),
            row.get("qlib_rank"),
            qlib_pct,
            industry_pct,
            rel_group,
            _safe_float(row.get("volatility_20d")),
            _safe_float(row.get("max_drawdown_60d")),
            vol_rank,
            dd_rank,
            forecast_cross_section_score,
            forecast_20d_score,
            forecast_60d_excess_score,
            forecast_industry_relative_score,
            risk_adjusted,
            forecast_score_v1,
            forecast_reason,
            now,
        ))
        inserted += 1

    conn.execute("DELETE FROM dim_stock_forecast_latest")
    conn.execute("""
        INSERT INTO dim_stock_forecast_latest (
            stock_code, snapshot_date, model_id, predict_date, stock_name,
            tdx_l1, tdx_l2, qlib_score, qlib_rank, qlib_percentile,
            industry_qlib_percentile, industry_relative_group,
            volatility_20d, max_drawdown_60d, volatility_rank, drawdown_rank,
            forecast_cross_section_score,
            forecast_20d_score, forecast_60d_excess_score,
            forecast_industry_relative_score,
            forecast_risk_adjusted_score, forecast_score_v1, forecast_reason, updated_at
        )
        SELECT stock_code, snapshot_date, model_id, predict_date, stock_name,
               tdx_l1, tdx_l2, qlib_score, qlib_rank, qlib_percentile,
               industry_qlib_percentile, industry_relative_group,
               volatility_20d, max_drawdown_60d, volatility_rank, drawdown_rank,
               forecast_cross_section_score,
               forecast_20d_score, forecast_60d_excess_score,
               forecast_industry_relative_score,
               forecast_risk_adjusted_score, forecast_score_v1, forecast_reason, updated_at
        FROM fact_stock_forecast_features
        WHERE snapshot_date = ? AND model_id = ?
    """, (snapshot_date, model_id))
    conn.commit()
    logger.info(f"[预测特征] 构建完成: {inserted} 只股票, 模型 {model_id}")
    return inserted
