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
import pickle
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn
from services.model_feature_schema import (
    REGIME_FEATURE_COLS,
    feature_cols_from_json,
    ordered_feature_cols,
)

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
    built_at      TEXT,
    PRIMARY KEY (snapshot_date, stock_code, model_id)
);
CREATE INDEX IF NOT EXISTS idx_dr_date ON mart_daily_recommendation(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_dr_rank ON mart_daily_recommendation(snapshot_date, rank_in_date);

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
"""


FEATURE_COLS = ordered_feature_cols(include_dense_v2=True)


def load_model(model_id: str):
    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"
    path = model_dir / f"{model_id}.pkl"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path, 'rb') as f:
        return pickle.load(f)


def load_latest_model_id(conn, *, include_disabled: bool = False) -> str:
    """M8.6: 默认排除 disabled_by_default=true 的模型 (alpha158 / legacy 110).
    显式传 --model-id 不走这条路, 不受影响."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mart_multidim_model)").fetchall()}
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


def load_model_feature_cols(conn, model_id: str, *, allow_legacy: bool) -> list[str]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mart_multidim_model)").fetchall()}
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
    pairs = sorted(zip(feature_cols, imp.tolist()), key=lambda x: x[1], reverse=True)
    return pairs[:top_k]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-id', default=None, help='指定 model_id (默认取最新)')
    parser.add_argument('--date', default=None, help='YYYY-MM-DD, 默认 panel 最新')
    parser.add_argument('--top-k', type=int, default=50)
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
    args = parser.parse_args()

    conn = get_conn()
    conn.executescript(DDL)

    model_id = args.model_id or load_latest_model_id(conn, include_disabled=args.include_disabled_models)
    logger.info("使用模型 %s", model_id)
    model = load_model(model_id)
    stored_feature_cols = load_model_feature_cols(
        conn,
        model_id,
        allow_legacy=args.allow_legacy_feature_order,
    )

    # 取 panel 的目标日期
    if args.date:
        target_date = args.date
    else:
        row = conn.execute("SELECT MAX(date) FROM fact_feature_panel").fetchone()
        target_date = row[0]
    logger.info("target_date=%s", target_date)

    # DuckDB 原生读取 + ATTACH alpha158 (对齐训练时 110 特征)
    duck = conn.raw if hasattr(conn, 'raw') else conn
    from pathlib import Path as _Path
    alpha158_db = _Path(__file__).resolve().parent.parent.parent / "data" / "alpha158.duckdb"
    a158_cols = []
    if alpha158_db.exists():
        try:
            duck.execute(f"ATTACH IF NOT EXISTS '{alpha158_db}' AS a158 (READ_ONLY)")
            a158_cols = [r[0] for r in duck.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_catalog='a158' AND table_name='fact_alpha158_panel' "
                "AND column_name LIKE 'a158_%'"
            ).fetchall()]
        except Exception as e:
            logger.warning("Alpha158 attach failed: %s", e)

    a158_sel = (", " + ", ".join(f"CAST(a.{c} AS FLOAT) AS {c}" for c in a158_cols)) if a158_cols else ""
    a158_join = "LEFT JOIN a158.fact_alpha158_panel a ON a.stock_code = p.stock_code AND a.date = CAST(p.date AS DATE)" if a158_cols else ""

    df = duck.execute(
        f"SELECT p.*{a158_sel} FROM fact_feature_panel p {a158_join} WHERE p.date = ?",
        [target_date],
    ).df()
    if df.empty:
        logger.error("fact_feature_panel 里没有 %s 的行", target_date)
        return
    logger.info("panel rows %d for %s (+ %d Alpha158 cols)", len(df), target_date, len(a158_cols))

    # 训练使用了 regime-aware (one-hot), 评分需对齐
    if 'regime_flag' in df.columns:
        for flag in ['up', 'flat', 'down']:
            df[f'regime_{flag}'] = (df['regime_flag'] == flag).astype(int)

    n_trained = model.num_feature() if hasattr(model, 'num_feature') else None
    if stored_feature_cols:
        missing = [c for c in stored_feature_cols if c not in df.columns]
        if missing:
            raise RuntimeError(f"模型 {model_id} 需要的特征在 panel 中缺失: {missing}")
        feature_cols = stored_feature_cols
    else:
        # 训练时的顺序: FEATURE_COLS (按存在性过滤) + regime one-hot (如训练用了 regime_aware)
        candidate = [c for c in FEATURE_COLS if c in df.columns]
        regime_onehot = [f for f in REGIME_FEATURE_COLS if f in df.columns]
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
    X = df[feature_cols].values
    # LightGBM 对 Column_N 模型 predict 时忽略 feature_name, 按顺序吃 X
    df['pred_score'] = model.predict(X, predict_disable_shape_check=False)
    df = df.sort_values('pred_score', ascending=False).reset_index(drop=True)
    df['rank_in_date'] = df.index + 1
    df['percentile'] = df['pred_score'].rank(pct=True)

    # top features (模型级, 所有股共享)
    top_feats = get_top_features(model, feature_cols, top_k=8)
    features_json = json.dumps({'model_top_features': [{'name': n, 'importance': v} for n, v in top_feats]},
                                 ensure_ascii=False)

    # 写入
    output = df[['stock_code', 'rank_in_date', 'pred_score', 'percentile', 'regime_flag']].copy()
    output['snapshot_date'] = target_date
    output['model_id'] = model_id
    output['key_features_json'] = features_json
    output['track_id'] = args.track_id
    output['is_primary'] = bool(args.is_primary)
    built_at = datetime.utcnow().isoformat()
    output['built_at'] = built_at

    # 限制 top_k
    if not args.by_regime:
        output = output.head(args.top_k)
    else:
        output = output.groupby('regime_flag', group_keys=False).head(args.top_k).reset_index(drop=True)

    logger.info("写入 %d 条推荐 (track_id=%s, is_primary=%s)",
                len(output), args.track_id, args.is_primary)
    # INSERT OR REPLACE
    for _, r in output.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO mart_daily_recommendation
               (snapshot_date, stock_code, model_id, rank_in_date, pred_score, percentile,
                regime_flag, key_features_json, track_id, is_primary, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r['snapshot_date'], r['stock_code'], r['model_id'],
             int(r['rank_in_date']), float(r['pred_score']), float(r['percentile']),
             r.get('regime_flag'), r['key_features_json'],
             r['track_id'], bool(r['is_primary']),
             r['built_at']),
        )
    conn.commit()

    # M8.5b/c: 算并写 snapshot 级风险摘要 (top20 / top_k 取较小, 默认 20)
    risk_top_size = min(20, args.top_k)
    write_risk_summary(
        conn, duck,
        snapshot_date=target_date,
        model_id=model_id,
        track_id=args.track_id,
        is_primary=bool(args.is_primary),
        top_size=risk_top_size,
        built_at=built_at,
    )

    # 输出 top-20 预览
    logger.info("=" * 60)
    logger.info("Top 20 推荐 (snapshot=%s, regime=%s):", target_date,
                df['regime_flag'].iloc[0] if 'regime_flag' in df.columns else 'n/a')
    for i, r in output.head(20).iterrows():
        logger.info("  [%d] %s  score=%.4f  pct=%.3f  regime=%s",
                    r['rank_in_date'], r['stock_code'],
                    r['pred_score'], r['percentile'], r.get('regime_flag') or '-')

    conn.close()


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
                   AVG(amount) OVER (PARTITION BY code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
              AND code IN ({placeholders})
              AND date <= ?
        ),
        latest_px AS (
            SELECT stock_code, MAX_BY(amount_ma20, amount_ma20) AS amount_ma20
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

    import numpy as np
    if amounts:
        amt_p25 = float(np.percentile(amounts, 25, method="linear"))
        amt_p50 = float(np.percentile(amounts, 50, method="linear"))
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
