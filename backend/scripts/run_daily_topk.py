#!/usr/bin/env python3
"""Phase 4: 每日 topK 推荐生成 (基于 mart_multidim_model 最佳模型)

输入
  - fact_feature_panel (features for scoring, 最新一天)
  - mart_multidim_model (best model_id from last training run)
  - data/multidim_models/<model_id>.pkl

输出
  - mart_daily_recommendation (snapshot_date, stock_code, rank, pred_score, percentile,
                               regime_flag, key_features_json)
  - 可选: 按 regime × topK 分组输出, 满足用户 "down regime 高管增持" 等语义 topK
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.market_db import canonical_kline_daily_qfq_sql
from services.model_feature_schema import (
    REGIME_FEATURE_COLS,
    feature_cols_from_json,
    ordered_feature_cols,
)
from services.model_explainer import explain_prediction_batch, top_contributors
from services.ml_lifecycle.registry import (
    get_default_champion_model_id,
    select_default_model_id,
)
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.recommendation_universe import (
    filter_investable_records,
    load_recommendation_universe_policy,
)
from services.schema_versions import record_actual_version

logger = logging.getLogger("daily_topk")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_daily_recommendation (
    snapshot_date TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    model_id      TEXT,
    rank_in_date  INTEGER,
    pred_score    REAL,
    percentile    REAL,
    regime_flag   TEXT,
    key_features_json TEXT,
    track_id      TEXT,
    is_primary    BOOLEAN,
    run_mode      TEXT DEFAULT 'champion',
    baseline_horizon_days INTEGER DEFAULT 60,
    selected_horizon_days INTEGER DEFAULT 60,
    selected_horizon_confidence REAL,
    horizon_selection_run_id TEXT,
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_dr_date ON mart_daily_recommendation(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_dr_rank ON mart_daily_recommendation(snapshot_date, rank_in_date);
ALTER TABLE mart_daily_recommendation ADD COLUMN IF NOT EXISTS run_mode TEXT DEFAULT 'champion';
ALTER TABLE mart_daily_recommendation ADD COLUMN IF NOT EXISTS baseline_horizon_days INTEGER DEFAULT 60;
ALTER TABLE mart_daily_recommendation ADD COLUMN IF NOT EXISTS selected_horizon_days INTEGER DEFAULT 60;
ALTER TABLE mart_daily_recommendation ADD COLUMN IF NOT EXISTS selected_horizon_confidence REAL;
ALTER TABLE mart_daily_recommendation ADD COLUMN IF NOT EXISTS horizon_selection_run_id TEXT;

CREATE TABLE IF NOT EXISTS mart_daily_topk_view_cache (
    snapshot_date TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    model_id      TEXT,
    rank_in_date  INTEGER,
    pred_score    REAL,
    percentile    REAL,
    regime_flag   TEXT,
    key_features_json TEXT,
    track_id      TEXT,
    is_primary    BOOLEAN,
    run_mode      TEXT DEFAULT 'champion',
    baseline_horizon_days INTEGER DEFAULT 60,
    selected_horizon_days INTEGER DEFAULT 60,
    selected_horizon_confidence REAL,
    horizon_selection_run_id TEXT,
    stock_name    TEXT,
    xueqiu_symbol TEXT,
    tdx_l1_name   TEXT,
    tdx_l2_name   TEXT,
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_topk_cache_date_rank
    ON mart_daily_topk_view_cache(snapshot_date DESC, rank_in_date);
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS run_mode TEXT DEFAULT 'champion';
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS baseline_horizon_days INTEGER DEFAULT 60;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS selected_horizon_days INTEGER DEFAULT 60;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS selected_horizon_confidence REAL;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS horizon_selection_run_id TEXT;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS xueqiu_symbol TEXT;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS tdx_l1_name TEXT;
ALTER TABLE mart_daily_topk_view_cache ADD COLUMN IF NOT EXISTS tdx_l2_name TEXT;

-- M8.5b: snapshot 级风险摘要 (top20). 不阻塞主轨上线, 只用于监控.
CREATE TABLE IF NOT EXISTS mart_daily_recommendation_risk (
    snapshot_date TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    track_id      TEXT,
    is_primary    BOOLEAN,
    top_size      INTEGER,
    top1_industry         TEXT,
    top1_industry_share   REAL,
    top3_industry_share   REAL,
    top20_amount_ma20_p25     REAL,
    top20_amount_ma20_median  REAL,
    overlap_with_primary  REAL,  -- Jaccard with track_id='primary' (NULL if self is primary)
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, model_id)
);

CREATE TABLE IF NOT EXISTS mart_model_explanation (
    model_id       TEXT NOT NULL,
    snapshot_date  TEXT NOT NULL,
    run_mode       TEXT NOT NULL,
    explainer_status TEXT NOT NULL,
    model_family   TEXT,
    row_count      INTEGER,
    feature_count  INTEGER,
    max_abs_error  DOUBLE,
    reason         TEXT,
    built_at       TEXT,
    PRIMARY KEY (model_id, snapshot_date, run_mode)
);

CREATE TABLE IF NOT EXISTS mart_daily_recommendation_explanation (
    snapshot_date TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    feature_name  TEXT NOT NULL,
    contribution  DOUBLE,
    contribution_pct DOUBLE,
    direction     TEXT,
    raw_value     TEXT,
    model_value   DOUBLE,
    base_value    DOUBLE,
    pred_score    DOUBLE,
    additivity_error DOUBLE,
    rank_in_date  INTEGER,
    run_mode      TEXT,
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, stock_code, model_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_daily_rec_explain_model_date
    ON mart_daily_recommendation_explanation(model_id, snapshot_date, rank_in_date);
"""

KLINE_DAILY_QFQ_SQL = canonical_kline_daily_qfq_sql(columns=("code", "date", "amount"))


FEATURE_COLS = ordered_feature_cols(include_dense_v2=True)


def load_model(model_id: str):
    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"
    path = model_dir / f"{model_id}.pkl"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path, 'rb') as f:
        return pickle.load(f)


def load_latest_model_id(conn, *, include_disabled: bool = False) -> str:
    """Default to lifecycle champion, never newest created_at challenger."""
    if not include_disabled:
        model_id, fallback = select_default_model_id(conn)
        if model_id:
            if fallback:
                logger.warning("lifecycle champion 缺失, fallback 到 mart_multidim_model 最新: %s", model_id)
            return model_id

    # M8.6 legacy fallback: 默认排除 disabled_by_default=true 的模型 (alpha158 / legacy 110).
    # 显式传 --model-id 不走这条路, 不受影响.
    cols = {r[0] for r in conn.execute("DESCRIBE mart_multidim_model").fetchall()}
    has_flag = "disabled_by_default" in cols
    if has_flag and not include_disabled:
        row = conn.execute("""
            SELECT model_id FROM mart_multidim_model
            WHERE COALESCE(disabled_by_default, false) = false
            ORDER BY created_at DESC LIMIT 1
        """).fetchone()
    else:
        row = conn.execute("""
            SELECT model_id FROM mart_multidim_model
            ORDER BY created_at DESC LIMIT 1
        """).fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model 无可用记录 (启用 --include-disabled-models 兼容旧 model)")
    return row[0]


def _table_columns(duck, table: str) -> set[str]:
    return {
        r[0] for r in duck.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table],
        ).fetchall()
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_model_feature_cols(conn, model_id: str, *, allow_legacy: bool) -> list[str]:
    cols = {r[0] for r in conn.execute("DESCRIBE mart_multidim_model").fetchall()}
    if "feature_cols_json" in cols:
        row = conn.execute(
            "SELECT feature_cols_json FROM mart_multidim_model WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        feature_cols = feature_cols_from_json(row["feature_cols_json"] if row else None)
        if feature_cols:
            return feature_cols
    if not allow_legacy:
        raise RuntimeError(
            f"模型 {model_id} 缺少 feature_cols_json; 如需兼容旧模型, 显式加 --allow-legacy-feature-order"
        )
    logger.warning("兼容旧模型: 使用代码内 FEATURE_COLS 顺序推理")
    return []


def get_top_features(model, feature_cols: list[str], top_k: int = 5) -> list[tuple[str, float]]:
    imp = model.feature_importance(importance_type='gain')
    importances = imp.tolist() if hasattr(imp, "tolist") else list(imp)
    pairs = sorted(zip(feature_cols, importances), key=lambda x: x[1], reverse=True)
    return pairs[:top_k]


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _feature_value(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _json_raw_feature_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def build_key_features_json(
    row: dict[str, Any],
    top_features: list[tuple[str, float]],
    *,
    top_contribution_rows: list[dict[str, Any]] | None = None,
    explanation_status: str | None = None,
    base_value: float | None = None,
    additivity_error: float | None = None,
) -> str:
    model_features = [
        {"name": name, "importance": float(importance or 0.0)}
        for name, importance in top_features
    ]
    stock_values = []
    for name, importance in top_features:
        raw_value = row.get(name)
        stock_values.append({
            "name": name,
            "importance": float(importance or 0.0),
            "raw_value": _json_raw_feature_value(raw_value),
            "model_value": _feature_value(raw_value),
        })
    payload = {
        "model_top_features": model_features,
        "stock_feature_values": stock_values,
    }
    if top_contribution_rows is not None:
        payload["stock_feature_contributions"] = top_contribution_rows
        payload["explanation_status"] = explanation_status
        payload["base_value"] = base_value
        payload["additivity_error"] = additivity_error
    return json.dumps(payload, ensure_ascii=False)


def _explanation_payload(
    row: dict[str, Any],
    explanation_row: dict[str, Any] | None,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not explanation_row:
        return []
    payload = []
    for item in top_contributors(explanation_row, limit=limit):
        feature_name = str(item.get("feature_name") or "")
        raw_value = row.get(feature_name)
        payload.append(
            {
                "name": feature_name,
                "raw_value": _json_raw_feature_value(raw_value),
                "model_value": _feature_value(raw_value),
                "contribution": float(item.get("contribution") or 0.0),
                "contribution_pct": float(item.get("contribution_pct") or 0.0),
                "direction": item.get("direction") or "flat",
            }
        )
    return payload


def write_recommendation_explanations(
    conn,
    *,
    output_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    feature_cols: list[str],
    explanation: dict[str, Any],
    built_at: str,
) -> int:
    if not output_rows:
        return 0
    snapshot_date = output_rows[0]["snapshot_date"]
    model_id = output_rows[0]["model_id"]
    run_mode = output_rows[0]["run_mode"]
    status = str(explanation.get("status") or "unsupported")
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_model_explanation
        (model_id, snapshot_date, run_mode, explainer_status, model_family,
         row_count, feature_count, max_abs_error, reason, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_id,
            snapshot_date,
            run_mode,
            status,
            explanation.get("model_family"),
            len(output_rows),
            len(feature_cols),
            explanation.get("max_abs_error"),
            explanation.get("reason"),
            built_at,
        ),
    )
    conn.execute(
        """
        DELETE FROM mart_daily_recommendation_explanation
         WHERE snapshot_date = ? AND model_id = ?
        """,
        (snapshot_date, model_id),
    )
    if status != "exact":
        return 0

    rows = []
    explanation_rows = explanation.get("rows") or []
    for rec, source_row, ex_row in zip(output_rows, source_rows, explanation_rows):
        if not ex_row:
            continue
        base_value = float(ex_row.get("base_value") or 0.0)
        additivity_error = float(ex_row.get("additivity_error") or 0.0)
        pred_score = float(ex_row.get("score") or rec["pred_score"])
        for item in ex_row.get("features") or []:
            if not isinstance(item, dict):
                continue
            feature_name = str(item.get("feature_name") or "")
            raw_value = source_row.get(feature_name)
            rows.append(
                (
                    snapshot_date,
                    rec["stock_code"],
                    model_id,
                    feature_name,
                    float(item.get("contribution") or 0.0),
                    float(item.get("contribution_pct") or 0.0),
                    item.get("direction") or "flat",
                    json.dumps(_json_raw_feature_value(raw_value), ensure_ascii=False),
                    _feature_value(raw_value),
                    base_value,
                    pred_score,
                    additivity_error,
                    int(rec["rank_in_date"]),
                    run_mode,
                    built_at,
                )
            )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_daily_recommendation_explanation
            (snapshot_date, stock_code, model_id, feature_name, contribution,
             contribution_pct, direction, raw_value, model_value, base_value,
             pred_score, additivity_error, rank_in_date, run_mode, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _rank_percentiles(values: list[float]) -> list[float]:
    """Use average-tie percentile rank semantics for prediction scores."""
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * n
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and indexed[end][1] == indexed[pos][1]:
            end += 1
        avg_rank = ((pos + 1) + end) / 2.0
        pct = avg_rank / n
        for idx in range(pos, end):
            ranks[indexed[idx][0]] = pct
        pos = end
    return ranks


def _percentile_linear(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _top_by_regime(rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    selected = []
    for row in rows:
        regime = str(row.get("regime_flag") or "")
        count = counts.get(regime, 0)
        if count >= top_k:
            continue
        selected.append(row)
        counts[regime] = count + 1
    return selected


def _xueqiu_symbol(stock_code: str | None) -> str | None:
    code = str(stock_code or "").strip()
    if not code:
        return None
    prefix = "SH" if code[0] in {"5", "6", "9"} else "SZ"
    return f"{prefix}{code}"


def _stock_identity_map(conn, stock_codes: list[str]) -> dict[str, dict[str, Any]]:
    codes = sorted({str(code) for code in stock_codes if code})
    if not codes:
        return {}
    placeholders = ", ".join(["?"] * len(codes))
    rows = conn.execute(
        f"""
        WITH name_ref AS (
            SELECT stock_code, stock_name, 1 AS source_priority
              FROM dim_active_a_stock
             WHERE stock_code IN ({placeholders})
               AND stock_name IS NOT NULL AND stock_name <> ''
            UNION ALL
            SELECT stock_code, stock_name, 2 AS source_priority
              FROM mart_stock_trend
             WHERE stock_code IN ({placeholders})
               AND stock_name IS NOT NULL AND stock_name <> ''
            UNION ALL
            SELECT stock_code, stock_name, 3 AS source_priority
              FROM fact_institution_event
             WHERE stock_code IN ({placeholders})
               AND stock_name IS NOT NULL AND stock_name <> ''
        ),
        stock_names AS (
            SELECT stock_code, stock_name
              FROM (
                SELECT stock_code, stock_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code
                           ORDER BY source_priority
                       ) AS rn
                  FROM name_ref
              )
             WHERE rn = 1
        )
        SELECT c.stock_code, sn.stock_name, ind.tdx_l1_name, ind.tdx_l2_name
          FROM (SELECT UNNEST(?::VARCHAR[]) AS stock_code) c
          LEFT JOIN stock_names sn ON sn.stock_code = c.stock_code
          LEFT JOIN dim_stock_tdx_industry ind ON ind.stock_code = c.stock_code
        """,
        [*codes, *codes, *codes, codes],
    ).fetchall()
    return {
        row["stock_code"]: {
            "stock_name": row["stock_name"],
            "tdx_l1_name": row["tdx_l1_name"],
            "tdx_l2_name": row["tdx_l2_name"],
            "xueqiu_symbol": _xueqiu_symbol(row["stock_code"]),
        }
        for row in rows
    }


def _latest_horizon_selection_run(conn) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_stock_horizon_selection
         GROUP BY run_id
         ORDER BY MAX(built_at) DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return row["run_id"] if row else None


def load_horizon_selection_map(conn, stock_codes: list[str]) -> tuple[str | None, dict[str, dict[str, Any]]]:
    try:
        table_exists = conn.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
             WHERE table_name = 'mart_stock_horizon_selection'
            """
        ).fetchone()[0]
    except Exception:
        table_exists = 0
    if not table_exists:
        return None, {}
    run_id = _latest_horizon_selection_run(conn)
    codes = sorted({str(code) for code in stock_codes if code})
    if not run_id or not codes:
        return run_id, {}
    placeholders = ", ".join(["?"] * len(codes))
    rows = conn.execute(
        f"""
        SELECT stock_code,
               baseline_horizon_days,
               selected_horizon_days,
               selected_horizon_confidence
          FROM mart_stock_horizon_selection
         WHERE run_id = ?
           AND stock_code IN ({placeholders})
        """,
        [run_id, *codes],
    ).fetchall()
    return run_id, {
        row["stock_code"]: {
            "baseline_horizon_days": int(row["baseline_horizon_days"] or 60),
            "selected_horizon_days": int(row["selected_horizon_days"] or 60),
            "selected_horizon_confidence": row["selected_horizon_confidence"],
        }
        for row in rows
    }


def write_topk_view_cache(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    snapshot_date = rows[0]["snapshot_date"]
    model_id = rows[0]["model_id"]
    identities = _stock_identity_map(conn, [row["stock_code"] for row in rows])
    conn.execute(
        """
        DELETE FROM mart_daily_topk_view_cache
         WHERE snapshot_date = ? AND model_id = ?
        """,
        (snapshot_date, model_id),
    )
    payload = []
    for row in rows:
        ident = identities.get(row["stock_code"], {})
        payload.append((
            row["snapshot_date"],
            row["stock_code"],
            row["model_id"],
            int(row["rank_in_date"]),
            float(row["pred_score"]),
            float(row["percentile"]),
            row.get("regime_flag"),
            row["key_features_json"],
            row["track_id"],
            bool(row["is_primary"]),
            row["run_mode"],
            int(row.get("baseline_horizon_days") or 60),
            int(row.get("selected_horizon_days") or 60),
            row.get("selected_horizon_confidence"),
            row.get("horizon_selection_run_id"),
            ident.get("stock_name"),
            ident.get("xueqiu_symbol") or _xueqiu_symbol(row["stock_code"]),
            ident.get("tdx_l1_name"),
            ident.get("tdx_l2_name"),
            row["built_at"],
        ))
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_daily_topk_view_cache
        (snapshot_date, stock_code, model_id, rank_in_date, pred_score, percentile,
         regime_flag, key_features_json, track_id, is_primary, run_mode,
         baseline_horizon_days, selected_horizon_days, selected_horizon_confidence,
         horizon_selection_run_id, stock_name, xueqiu_symbol, tdx_l1_name,
         tdx_l2_name, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def demote_existing_primary_recommendations(conn, *, snapshot_date: str, model_id: str) -> None:
    """Keep one primary model per snapshot date before writing champion TopK."""

    for table in ("mart_daily_recommendation", "mart_daily_topk_view_cache"):
        conn.execute(
            f"""
            UPDATE {table}
               SET is_primary = FALSE
             WHERE snapshot_date = ?
               AND model_id <> ?
               AND is_primary = TRUE
            """,
            (snapshot_date, model_id),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-id', default=None, help='指定 model_id (默认取最新)')
    parser.add_argument('--date', default=None, help='YYYY-MM-DD, 默认 panel 最新')
    parser.add_argument('--top-k', '--limit', dest='top_k', type=int, default=50)
    parser.add_argument('--mode', choices=['champion', 'shadow'], default='champion',
                        help='champion 写正式推荐; shadow 写影子推荐, 不作为默认推荐')
    parser.add_argument('--feature-table', default='fact_feature_panel',
                        help='评分使用的特征表, challenger 可用 fact_feature_panel_tdx_keep_challenger')
    parser.add_argument('--feature-set-id', default=None,
                        help='feature_table 有 feature_set_id 列时用于过滤')
    parser.add_argument('--by-regime', action='store_true',
                        help='按 regime_flag 分组各出 top-K')
    parser.add_argument('--allow-legacy-feature-order', action='store_true',
                        help='兼容 feature_cols_json 缺失的旧模型')
    parser.add_argument('--track-id', default=None,
                        help='M8.5: 写入 mart_daily_recommendation 的 track_id 标签 '
                             '(e.g. primary / shadow_dense_v2 / legacy_v1_110)')
    parser.add_argument('--is-primary', action='store_true',
                        help='M8.5: 标记为主推荐轨道, 前端默认展示这一轨')
    parser.add_argument('--include-disabled-models', action='store_true',
                        help='M8.6: 选最新模型时纳入 disabled_by_default=true (alpha158/legacy 110)')
    parser.add_argument('--skip-risk-summary', action='store_true',
                        help='跳过 mart_daily_recommendation_risk，用于每日关键链路压缩 TopK SLA')
    parser.add_argument('--quiet-preview', action='store_true',
                        help='不输出 Top 20 预览日志')
    args = parser.parse_args()
    run_started_at = utc_now_iso()
    run_t0 = time.perf_counter()
    timings: dict[str, float] = {}

    conn = get_conn()
    conn.executescript(DDL)

    if args.mode == 'shadow' and not args.model_id:
        raise RuntimeError("shadow 模式必须显式传 --model-id, 防止误用 champion")

    model_id = args.model_id or load_latest_model_id(conn, include_disabled=args.include_disabled_models)
    champion_id = get_default_champion_model_id(conn)
    selection_fallback = args.model_id is None and champion_id is None
    if args.mode == 'shadow' and champion_id and model_id == champion_id:
        raise RuntimeError("shadow 模式不能写 lifecycle champion, 避免覆盖正式推荐")
    if args.mode == 'champion' and champion_id and model_id != champion_id:
        raise RuntimeError("champion 模式只能写 lifecycle champion; challenger 请使用 --mode shadow")
    logger.info("使用模型 %s", model_id)
    model = load_model(model_id)
    stored_feature_cols = load_model_feature_cols(
        conn,
        model_id,
        allow_legacy=args.allow_legacy_feature_order,
    )
    n_trained = model.num_feature() if hasattr(model, 'num_feature') else None

    # 取 panel 的目标日期
    duck = conn.raw if hasattr(conn, 'raw') else conn
    feature_table_cols = _table_columns(duck, args.feature_table)
    has_feature_set_id = "feature_set_id" in feature_table_cols
    table_where = []
    table_params = []
    if has_feature_set_id and args.feature_set_id:
        table_where.append("feature_set_id = ?")
        table_params.append(args.feature_set_id)

    if args.date:
        target_date = args.date
    else:
        where_sql = (" WHERE " + " AND ".join(table_where)) if table_where else ""
        row = duck.execute(f"SELECT MAX(date) FROM {args.feature_table}{where_sql}", table_params).fetchone()
        target_date = row[0]
    logger.info("target_date=%s", target_date)

    # DuckDB 原生读取 + 按模型 feature_cols_json 决定是否 ATTACH Alpha158.
    # base/base_dense_v2/tdx_keep_v1 不能为推理无条件加载 64 个实验因子。
    from pathlib import Path as _Path
    alpha158_db = _Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb"
    a158_cols = []
    legacy_needs_alpha158 = (
        not stored_feature_cols
        and args.allow_legacy_feature_order
        and n_trained is not None
        and n_trained > len(FEATURE_COLS) + len(REGIME_FEATURE_COLS)
    )
    needs_alpha158 = any(col.startswith("a158_") for col in stored_feature_cols) or legacy_needs_alpha158
    if needs_alpha158 and alpha158_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{alpha158_db}' AS a158 (READ_ONLY)")
            a158_cols = [r[0] for r in duck.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_catalog='a158' AND table_name='fact_alpha158_panel' "
                "AND column_name LIKE 'a158_%'"
            ).fetchall()]
        except Exception as e:
            logger.warning("Alpha158 attach failed: %s", e)

    a158_sel = ""
    a158_join = ""
    if args.feature_table == "fact_feature_panel" and a158_cols:
        a158_sel = ", " + ", ".join(f"CAST(a.{_quote_ident(c)} AS FLOAT) AS {_quote_ident(c)}" for c in a158_cols)
        a158_join = "LEFT JOIN a158.fact_alpha158_panel a ON a.stock_code = p.stock_code AND a.date = CAST(p.date AS DATE)"

    where = ["p.date = ?"]
    params = [target_date]
    if has_feature_set_id and args.feature_set_id:
        where.append("p.feature_set_id = ?")
        params.append(args.feature_set_id)
    t_load = time.perf_counter()
    records = _records_from_cursor(duck.execute(
        f"SELECT p.*{a158_sel} FROM {args.feature_table} p {a158_join} WHERE {' AND '.join(where)}",
        params,
    ))
    timings["load_panel_s"] = round(time.perf_counter() - t_load, 3)
    if not records:
        logger.error("fact_feature_panel 里没有 %s 的行", target_date)
        conn.close()
        return
    panel_cols = set(records[0].keys())
    logger.info(
        "panel rows %d for %s (+ %d Alpha158 cols, needs_alpha158=%s)",
        len(records), target_date, len(a158_cols), needs_alpha158,
    )
    universe_policy = load_recommendation_universe_policy()
    t_universe = time.perf_counter()
    records, universe_summary = filter_investable_records(
        conn,
        records,
        policy=universe_policy,
    )
    timings["universe_filter_s"] = round(time.perf_counter() - t_universe, 3)
    logger.info(
        "investable universe policy=%s kept=%d excluded=%d reasons=%s",
        universe_summary["policy_id"],
        universe_summary["kept_rows"],
        universe_summary["excluded_rows"],
        universe_summary["excluded_by_reason"],
    )
    if not records:
        raise RuntimeError(f"investable universe policy {universe_summary['policy_id']} left no scoring rows")
    panel_cols = set(records[0].keys())

    # 训练使用了 regime-aware (one-hot), 评分需对齐
    if 'regime_flag' in panel_cols:
        for row in records:
            for flag in ['up', 'flat', 'down']:
                row[f'regime_{flag}'] = 1 if row.get('regime_flag') == flag else 0
        panel_cols.update(REGIME_FEATURE_COLS)

    if stored_feature_cols:
        missing = [c for c in stored_feature_cols if c not in panel_cols]
        if missing:
            raise RuntimeError(f"模型 {model_id} 需要的特征在 panel 中缺失: {missing}")
        feature_cols = stored_feature_cols
    else:
        # 训练时的顺序: FEATURE_COLS (按存在性过滤) + regime one-hot (如训练用了 regime_aware)
        candidate = [c for c in FEATURE_COLS if c in panel_cols]
        regime_onehot = [f for f in REGIME_FEATURE_COLS if f in panel_cols]
        # 先不加 one-hot; 对齐训练时特征数
        if n_trained == len(candidate):
            feature_cols = candidate
        elif n_trained == len(candidate) + len(regime_onehot):
            feature_cols = candidate + regime_onehot
        else:
            logger.warning("训练特征数 %s ≠ 候选 %d (含 regime %d)", n_trained, len(candidate), len(regime_onehot))
            feature_cols = candidate + regime_onehot

    logger.info("使用 %d 特征评分 (model n_feat=%s)", len(feature_cols), n_trained)
    if n_trained is not None and len(feature_cols) != n_trained:
        raise RuntimeError(f"特征数不匹配: model={n_trained}, panel={len(feature_cols)}")
    t_predict = time.perf_counter()
    X = [[_feature_value(row.get(col)) for col in feature_cols] for row in records]
    # LightGBM 对 Column_N 模型 predict 时忽略 feature_name, 按顺序吃 X
    predictions = model.predict(X, predict_disable_shape_check=False)
    pred_scores = [float(value) for value in predictions]
    percentiles = _rank_percentiles(pred_scores)
    for idx, row in enumerate(records):
        row['pred_score'] = pred_scores[idx]
        row['percentile'] = percentiles[idx]
    records.sort(key=lambda row: row['pred_score'], reverse=True)
    for idx, row in enumerate(records, 1):
        row['rank_in_date'] = idx
    timings["predict_rank_s"] = round(time.perf_counter() - t_predict, 3)

    # top features (模型级, 所有股共享)
    top_feats = get_top_features(model, feature_cols, top_k=8)

    # 写入
    track_id = args.track_id
    if not track_id:
        track_id = 'primary' if args.mode == 'champion' else f"shadow_{model_id}"
    is_primary = bool(args.is_primary) or (args.mode == 'champion' and model_id == champion_id)
    if args.mode == 'shadow':
        is_primary = False
    built_at = datetime.utcnow().isoformat()
    horizon_run_id, horizon_by_stock = load_horizon_selection_map(
        conn,
        [row.get("stock_code") for row in records],
    )
    t_explain = time.perf_counter()
    explanation_all = explain_prediction_batch(
        model,
        [[_feature_value(row.get(col)) for col in feature_cols] for row in records],
        feature_cols,
        dates=[target_date] * len(records),
        scores=[row["pred_score"] for row in records],
    )
    all_explanation_rows = explanation_all.get("rows") if explanation_all.get("status") == "exact" else []
    explanation_by_stock = {
        str(row.get("stock_code")): all_explanation_rows[idx]
        for idx, row in enumerate(records)
        if idx < len(all_explanation_rows)
    }
    timings["explain_s"] = round(time.perf_counter() - t_explain, 3)

    output_source_rows = list(records)
    output = [
        {
            'stock_code': row.get('stock_code'),
            'rank_in_date': row['rank_in_date'],
            'pred_score': row['pred_score'],
            'percentile': row['percentile'],
            'regime_flag': row.get('regime_flag'),
            'snapshot_date': target_date,
            'model_id': model_id,
            'key_features_json': "{}",
            'track_id': track_id,
            'is_primary': is_primary,
            'run_mode': args.mode,
            'baseline_horizon_days': int(
                (horizon_by_stock.get(str(row.get('stock_code'))) or {}).get('baseline_horizon_days')
                or 60
            ),
            'selected_horizon_days': int(
                (horizon_by_stock.get(str(row.get('stock_code'))) or {}).get('selected_horizon_days')
                or 60
            ),
            'selected_horizon_confidence': (
                (horizon_by_stock.get(str(row.get('stock_code'))) or {}).get('selected_horizon_confidence')
            ),
            'horizon_selection_run_id': horizon_run_id,
            'built_at': built_at,
        }
        for row in output_source_rows
    ]

    # 限制 top_k
    if not args.by_regime:
        output = output[:args.top_k]
        output_source_rows = output_source_rows[:args.top_k]
    else:
        selected = _top_by_regime(
            [
                {**out, "_source_row": src}
                for out, src in zip(output, output_source_rows)
            ],
            args.top_k,
        )
        output = [{k: v for k, v in row.items() if k != "_source_row"} for row in selected]
        output_source_rows = [row["_source_row"] for row in selected]

    selected_explanation_rows = [
        explanation_by_stock.get(str(row.get("stock_code")))
        for row in output_source_rows
    ]
    explanation_rows = [row for row in selected_explanation_rows if row is not None]
    selected_errors = [
        float(row.get("additivity_error") or 0.0)
        for row in explanation_rows
    ]
    explanation = {
        **explanation_all,
        "rows": selected_explanation_rows if explanation_all.get("status") == "exact" else [],
        "max_abs_error": max(selected_errors) if selected_errors else explanation_all.get("max_abs_error"),
    }
    for idx, (out, src) in enumerate(zip(output, output_source_rows)):
        ex_row = selected_explanation_rows[idx] if idx < len(selected_explanation_rows) else None
        out["key_features_json"] = build_key_features_json(
            src,
            top_feats,
            top_contribution_rows=_explanation_payload(src, ex_row),
            explanation_status=str(explanation.get("status") or "unsupported"),
            base_value=(float(ex_row["base_value"]) if ex_row else None),
            additivity_error=(float(ex_row["additivity_error"]) if ex_row else None),
        )

    logger.info("写入 %d 条推荐 (track_id=%s, is_primary=%s)",
                len(output), track_id, is_primary)
    t_write = time.perf_counter()
    if is_primary:
        demote_existing_primary_recommendations(conn, snapshot_date=target_date, model_id=model_id)
    conn.execute(
        """
        DELETE FROM mart_daily_recommendation
         WHERE snapshot_date = ? AND model_id = ?
        """,
        (target_date, model_id),
    )
    for r in output:
        conn.execute(
            """INSERT OR REPLACE INTO mart_daily_recommendation
               (snapshot_date, stock_code, model_id, rank_in_date, pred_score, percentile,
                regime_flag, key_features_json, track_id, is_primary, run_mode,
                baseline_horizon_days, selected_horizon_days, selected_horizon_confidence,
                horizon_selection_run_id, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r['snapshot_date'], r['stock_code'], r['model_id'],
             int(r['rank_in_date']), float(r['pred_score']), float(r['percentile']),
             r.get('regime_flag'), r['key_features_json'],
             r['track_id'], bool(r['is_primary']), r['run_mode'],
             int(r.get('baseline_horizon_days') or 60),
             int(r.get('selected_horizon_days') or 60),
             r.get('selected_horizon_confidence'),
             r.get('horizon_selection_run_id'),
             r['built_at']),
        )
    explanation_rows_written = write_recommendation_explanations(
        conn,
        output_rows=output,
        source_rows=output_source_rows,
        feature_cols=feature_cols,
        explanation=explanation,
        built_at=built_at,
    )
    conn.commit()
    timings["recommendation_write_s"] = round(time.perf_counter() - t_write, 3)
    t_cache = time.perf_counter()
    cache_rows = write_topk_view_cache(conn, output)
    conn.commit()
    timings["view_cache_write_s"] = round(time.perf_counter() - t_cache, 3)

    if not args.skip_risk_summary:
        # M8.5b/c: 算并写 snapshot 级风险摘要 (top20 / top_k 取较小, 默认 20)
        t_risk = time.perf_counter()
        risk_top_size = min(20, args.top_k)
        write_risk_summary(
            conn, duck,
            snapshot_date=target_date,
            model_id=model_id,
            track_id=track_id,
            is_primary=is_primary,
            top_size=risk_top_size,
            built_at=built_at,
        )
        timings["risk_summary_s"] = round(time.perf_counter() - t_risk, 3)
    record_actual_version(conn, "mart_daily_recommendation")
    record_actual_version(conn, "mart_model_explanation")
    record_actual_version(conn, "mart_daily_recommendation_explanation")
    if not args.skip_risk_summary:
        record_actual_version(conn, "mart_daily_recommendation_risk")
    record_actual_version(conn, "mart_daily_topk_view_cache")
    duration_s = time.perf_counter() - run_t0
    timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=f"daily_topk_{model_id}_{target_date}_{track_id}",
        pipeline_name="run_daily_topk",
        status="success",
        started_at=run_started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            args.feature_table,
            "mart_multidim_model",
            *(
                ["data/alpha158.duckdb:fact_alpha158_panel"]
                if needs_alpha158
                else []
            ),
        ],
        output_tables=[
            "mart_daily_recommendation",
            *([] if args.skip_risk_summary else ["mart_daily_recommendation_risk"]),
            "mart_daily_topk_view_cache",
        ],
        model_id=model_id,
        feature_group="stored_feature_cols",
        label_name=None,
        perf_summary={
            "target_date": str(target_date),
            "rows": len(records),
            "output_rows": len(output),
            "view_cache_rows": cache_rows,
            "explanation_rows": explanation_rows_written,
            "explanation_status": explanation.get("status"),
            "explanation_max_abs_error": explanation.get("max_abs_error"),
            "top_k": args.top_k,
            "mode": args.mode,
            "track_id": track_id,
            "is_primary": is_primary,
            "selection_fallback": selection_fallback,
            "horizon_selection_run_id": horizon_run_id,
            "universe_policy_id": universe_summary["policy_id"],
            "universe_input_rows": universe_summary["input_rows"],
            "universe_kept_rows": universe_summary["kept_rows"],
            "universe_excluded_rows": universe_summary["excluded_rows"],
            "universe_excluded_by_reason": universe_summary["excluded_by_reason"],
            "risk_summary_skipped": bool(args.skip_risk_summary),
            "n_features": len(feature_cols),
            "needs_alpha158": needs_alpha158,
            "alpha158_cols": len(a158_cols),
            "timings": timings,
        },
    )

    if not args.quiet_preview:
        # 输出 top-20 预览
        logger.info("=" * 60)
        logger.info(
            "Top 20 推荐 (snapshot=%s, regime=%s):",
            target_date,
            records[0].get('regime_flag') if records and 'regime_flag' in panel_cols else 'n/a',
        )
        for r in output[:20]:
            logger.info("  [%d] %s  score=%.4f  pct=%.3f  regime=%s",
                        r['rank_in_date'], r['stock_code'],
                        r['pred_score'], r['percentile'], r.get('regime_flag') or '-')

    conn.close()
    logger.info(
        "daily topK done model=%s mode=%s feature_table=%s feature_set_id=%s selection_fallback=%s",
        model_id, args.mode, args.feature_table, args.feature_set_id, selection_fallback,
    )


def write_risk_summary(conn, duck, *, snapshot_date: str, model_id: str,
                        track_id: str | None, is_primary: bool,
                        top_size: int, built_at: str) -> None:
    """M8.5b/c: 计算并落 mart_daily_recommendation_risk.

    字段: top1/top3 行业占比 (TDX L1) + amount_ma20 中位数/25 分位 +
    与主轨 top20 的 Jaccard overlap. 仅用于监控, 不阻塞推荐。
    """
    # 取本轨 top20 stock_codes
    top_codes = [r[0] for r in duck.execute("""
        SELECT stock_code FROM mart_daily_recommendation
        WHERE snapshot_date = ? AND model_id = ?
        ORDER BY rank_in_date LIMIT ?
    """, [snapshot_date, model_id, top_size]).fetchall()]
    if not top_codes:
        logger.warning("write_risk_summary: top_codes 为空, 跳过")
        return

    placeholders = ",".join(["?"] * len(top_codes))
    # ATTACH market.duckdb 取 amount + amount_ma20
    from pathlib import Path as _Path
    market_db = _Path(__file__).resolve().parent.parent.parent / "data" / "market.duckdb"
    if market_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")
        except Exception:
            pass

    # 行业占比 + amount_ma20 分位
    rows = duck.execute(f"""
        WITH px AS (
            SELECT code AS stock_code,
                   date,
                   AVG(amount) OVER (PARTITION BY code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM ({KLINE_DAILY_QFQ_SQL}) AS kline
            WHERE code IN ({placeholders})
              AND date <= ?
        ),
        latest_px AS (
            SELECT stock_code, MAX_BY(amount_ma20, date) AS amount_ma20
            FROM px GROUP BY stock_code
        )
        SELECT t.stock_code, ind.tdx_l1, ind.tdx_l1_name, lp.amount_ma20
        FROM (SELECT UNNEST([{placeholders}]) AS stock_code) t
        LEFT JOIN dim_stock_tdx_industry ind ON ind.stock_code = t.stock_code
        LEFT JOIN latest_px lp ON lp.stock_code = t.stock_code
    """, [*top_codes, snapshot_date, *top_codes]).fetchall()

    industry_counts: dict[str, int] = {}
    industry_l1_lookup: dict[str, str] = {}
    amounts: list[float] = []
    for r in rows:
        l1 = r[1] or "UNK"
        industry_counts[l1] = industry_counts.get(l1, 0) + 1
        if r[2]:
            industry_l1_lookup[l1] = r[2]
        if r[3] is not None:
            amounts.append(float(r[3]))

    n = len(rows) or 1
    sorted_inds = sorted(industry_counts.items(), key=lambda x: -x[1])
    top1_industry = sorted_inds[0][0] if sorted_inds else None
    top1_industry_name = industry_l1_lookup.get(top1_industry) if top1_industry else None
    top1_share = (sorted_inds[0][1] / n) if sorted_inds else 0.0
    top3_share = (sum(c for _, c in sorted_inds[:3]) / n) if sorted_inds else 0.0

    if amounts:
        amt_p25 = _percentile_linear(amounts, 25)
        amt_p50 = _percentile_linear(amounts, 50)
    else:
        amt_p25 = amt_p50 = None

    # 与主轨 overlap (若自己是主轨, NULL)
    overlap = None
    if not is_primary:
        primary_codes_rows = duck.execute("""
            SELECT stock_code FROM mart_daily_recommendation
            WHERE snapshot_date = ? AND is_primary = true
            ORDER BY rank_in_date LIMIT ?
        """, [snapshot_date, top_size]).fetchall()
        if primary_codes_rows:
            primary_set = {r[0] for r in primary_codes_rows}
            self_set = set(top_codes)
            inter = len(primary_set & self_set)
            union = len(primary_set | self_set)
            overlap = (inter / union) if union else None

    duck.execute("""
        INSERT OR REPLACE INTO mart_daily_recommendation_risk
        (snapshot_date, model_id, track_id, is_primary, top_size,
         top1_industry, top1_industry_share, top3_industry_share,
         top20_amount_ma20_p25, top20_amount_ma20_median,
         overlap_with_primary, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [snapshot_date, model_id, track_id, is_primary, top_size,
          top1_industry_name, top1_share, top3_share,
          amt_p25, amt_p50, overlap, built_at])
    conn.commit()
    logger.info(
        "risk summary: top1=%s(%.1f%%), top3=%.1f%%, amount_ma20 p25=%.0f p50=%.0f, overlap_primary=%s",
        top1_industry_name, top1_share * 100, top3_share * 100,
        amt_p25 or 0, amt_p50 or 0,
        f"{overlap:.3f}" if overlap is not None else "n/a (self is primary)",
    )


if __name__ == "__main__":
    main()
