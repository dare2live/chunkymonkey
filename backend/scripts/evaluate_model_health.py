#!/usr/bin/env python3
"""W6 模型健康评估：Calibration ECE + Decile Lift + PSI（§19.3 / §29.6）。

为最新 Qlib 事件模型补齐"非黑盒五件套"剩余三项：
  1. Calibration ECE：预测概率与实际正样本率的期望校准误差（10 bins）
  2. Decile Lift：Top-10% 预测的实际正样本率 / 全体正样本率的倍数
  3. PSI：每个特征在 train 与 holdout 的分布漂移分数（<0.1 稳定 / 0.1-0.2 轻微 / >0.2 显著漂移）

结果回写 qlib_model_evaluation：
  - 新增列：calibration_ece / lift_top_decile / psi_json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn
from scripts.train_event_qlib import EXCLUDE_COLS

logger = logging.getLogger("evaluate_model_health")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def ensure_columns(conn):
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(qlib_model_evaluation)").fetchall()}
    for col, ddl in [
        ("calibration_ece", "ALTER TABLE qlib_model_evaluation ADD COLUMN calibration_ece REAL"),
        ("lift_top_decile", "ALTER TABLE qlib_model_evaluation ADD COLUMN lift_top_decile REAL"),
        ("psi_json", "ALTER TABLE qlib_model_evaluation ADD COLUMN psi_json TEXT"),
    ]:
        if col not in existing:
            conn.execute(ddl)
    conn.commit()


def calibration_ece(y_true_bin: np.ndarray, scores_0_100: np.ndarray, n_bins: int = 10) -> dict:
    """Expected Calibration Error + 校准曲线。

    scores 期望是 0-100 分位（event_action_score）；把它视为"被模型认为正样本的概率 × 100"的近似。
    严格意义上校准需要真概率输出；这里用分位作代理。
    """
    if len(y_true_bin) == 0 or len(np.unique(y_true_bin)) < 2:
        return {"ece": None, "bins": []}
    scores = scores_0_100 / 100.0
    bin_edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    bins_info = []
    n_total = len(scores)
    for i in range(n_bins):
        mask = (scores >= bin_edges[i]) & (scores < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = (scores >= bin_edges[i]) & (scores <= bin_edges[i + 1])
        count = int(mask.sum())
        if count == 0:
            bins_info.append(dict(lo=round(bin_edges[i], 2), hi=round(bin_edges[i + 1], 2), n=0, pred=None, actual=None, gap=None))
            continue
        pred_mean = float(scores[mask].mean())
        actual_rate = float(y_true_bin[mask].mean())
        gap = abs(pred_mean - actual_rate)
        ece += (count / n_total) * gap
        bins_info.append(dict(lo=round(bin_edges[i], 2), hi=round(bin_edges[i + 1], 2),
                              n=count, pred=round(pred_mean, 3), actual=round(actual_rate, 3),
                              gap=round(gap, 3)))
    return {"ece": round(ece, 4), "bins": bins_info}


def decile_lift(y_true_bin: np.ndarray, scores: np.ndarray) -> dict:
    """按 scores 降序分 10 桶，返回每桶的正样本率 + top decile lift"""
    if len(y_true_bin) == 0 or y_true_bin.mean() == 0:
        return {"top_decile_lift": None, "deciles": []}
    order = np.argsort(-scores)
    y_sorted = y_true_bin[order]
    n = len(y_sorted)
    base_rate = float(y_true_bin.mean())
    deciles = []
    for i in range(10):
        lo = i * n // 10
        hi = (i + 1) * n // 10
        seg = y_sorted[lo:hi]
        if len(seg) == 0:
            continue
        rate = float(seg.mean())
        deciles.append(dict(decile=i + 1, n=int(len(seg)), pos_rate=round(rate, 3),
                            lift=round(rate / base_rate, 2)))
    top_decile_lift = deciles[0]["lift"] if deciles else None
    return {"top_decile_lift": top_decile_lift, "base_rate": round(base_rate, 3), "deciles": deciles}


def population_stability(train_vals: np.ndarray, hold_vals: np.ndarray, n_bins: int = 10) -> float:
    """PSI：基于 train 分位数切 bin，计算 PSI = sum((p_hold - p_train) * log(p_hold/p_train))"""
    train_vals = train_vals[~np.isnan(train_vals)]
    hold_vals = hold_vals[~np.isnan(hold_vals)]
    if len(train_vals) < 50 or len(hold_vals) < 20:
        return None
    if np.unique(train_vals).size < 3:
        return None
    edges = np.quantile(train_vals, np.linspace(0, 1, n_bins + 1))
    edges = np.unique(edges)
    if len(edges) < 3:
        return None
    edges[0] = -np.inf
    edges[-1] = np.inf
    train_hist, _ = np.histogram(train_vals, bins=edges)
    hold_hist, _ = np.histogram(hold_vals, bins=edges)
    p_train = train_hist / train_hist.sum()
    p_hold = hold_hist / hold_hist.sum()
    eps = 1e-6
    p_train = np.clip(p_train, eps, None)
    p_hold = np.clip(p_hold, eps, None)
    psi = float(((p_hold - p_train) * np.log(p_hold / p_train)).sum())
    return round(psi, 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", help="目标 model_id（默认最新 holdout eval）")
    parser.add_argument("--follow-threshold", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        ensure_columns(conn)

        # 找 model_id
        if args.model_id:
            model_id = args.model_id
        else:
            row = conn.execute(
                "SELECT model_id FROM qlib_model_evaluation WHERE eval_dataset='holdout' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                logger.error("无已训练模型"); return
            model_id = row["model_id"]
        logger.info("目标 model_id = %s", model_id)

        # 拿 predictions + 真实 label
        preds = pd.read_sql_query(
            "SELECT p.institution_id, p.stock_code, p.notice_date, p.report_date, "
            "       p.event_action_score, p.predicted_gain, p.split, "
            "       f.label_gain_60d "
            "FROM qlib_event_prediction p "
            "LEFT JOIN fact_event_features f ON "
            "  f.institution_id=p.institution_id AND f.stock_code=p.stock_code "
            "  AND f.notice_date=p.notice_date AND f.report_date=p.report_date "
            "WHERE p.model_id = ?",
            conn, params=(model_id,))
        logger.info("加载 predictions: %d 行（train+holdout）", len(preds))

        hold = preds[preds["split"] == "holdout"].copy()
        train = preds[preds["split"] == "train"].copy()
        hold = hold[hold["label_gain_60d"].notna()]
        train = train[train["label_gain_60d"].notna()]
        logger.info("有 label：train %d, holdout %d", len(train), len(hold))

        # Calibration + Lift（holdout）
        y_bin_hold = (hold["label_gain_60d"] > args.follow_threshold).astype(int).values
        scores_hold = hold["event_action_score"].values.astype(float)
        cal_hold = calibration_ece(y_bin_hold, scores_hold, n_bins=10)
        lift_hold = decile_lift(y_bin_hold, scores_hold)
        logger.info("[holdout] ECE=%.4f  top-decile lift=%s (base=%s)",
                    cal_hold["ece"] or 0, lift_hold["top_decile_lift"], lift_hold.get("base_rate"))
        for d in lift_hold["deciles"][:3]:
            logger.info("  decile %d: n=%d pos_rate=%.3f lift=%.2f", d["decile"], d["n"], d["pos_rate"], d["lift"])

        # PSI：fact_event_features 的每个数值列，比较 train/holdout 分布
        feat_df = pd.read_sql_query("SELECT * FROM fact_event_features", conn)
        feat_df = feat_df.sort_values("notice_date").reset_index(drop=True)
        cut = int(len(feat_df) * 0.8)
        train_f = feat_df.iloc[:cut]
        hold_f = feat_df.iloc[cut:]
        numeric_cols = [c for c in feat_df.columns
                        if c not in EXCLUDE_COLS and pd.api.types.is_numeric_dtype(feat_df[c])]
        psi_scores = {}
        for c in numeric_cols:
            psi = population_stability(train_f[c].values, hold_f[c].values)
            if psi is not None:
                psi_scores[c] = psi
        # 排序 + 标记等级
        psi_sorted = sorted(psi_scores.items(), key=lambda x: -x[1])
        logger.info("=== PSI Top 10（≥0.2 显著漂移）===")
        drift_count = 0
        for c, v in psi_sorted[:10]:
            level = "DRIFT" if v > 0.2 else "mild" if v > 0.1 else "stable"
            if v > 0.2:
                drift_count += 1
            logger.info("  %-40s psi=%.3f  %s", c, v, level)
        logger.info("PSI > 0.2 的特征数：%d / %d", drift_count, len(psi_scores))

        # 写回 qlib_model_evaluation
        if not args.dry_run:
            conn.execute(
                "UPDATE qlib_model_evaluation SET calibration_ece=?, lift_top_decile=?, psi_json=? "
                "WHERE model_id=? AND eval_dataset='holdout'",
                (cal_hold["ece"], lift_hold["top_decile_lift"],
                 json.dumps({"psi_scores": psi_scores,
                             "calibration_bins": cal_hold["bins"],
                             "lift_deciles": lift_hold["deciles"]}, ensure_ascii=False),
                 model_id),
            )
            conn.commit()
            logger.info("已回写 qlib_model_evaluation（holdout）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
