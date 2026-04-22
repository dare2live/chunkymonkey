"""Walk-Forward 训练调度器 (Layer 6).

按月滚动:
- 训练窗 24 个月 → 测试窗 1 个月
- embargo 5 天防信息泄漏
- 每 fold 训练 Meta 模型，记录 OOS AUC / accuracy
- 结果写入 backtest_walk_forward 表

注意: 在 SEF 当前实现下，walk-forward 主要用于 Meta-Labeling 模型性能监控.
Layer 1 Cox 和 Layer 3 Bayesian 是状态聚合而非 ML 预测，不适用 W-F.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from ._dates import to_iso
from .meta_labeling import _assemble_training_dataset, _row_to_features
from .purged_cv import purged_walk_forward_splits

logger = logging.getLogger("cm-api.sef.walk_forward")


def run_walk_forward(
    conn: sqlite3.Connection,
    *,
    train_window_months: int = 18,
    test_window_months: int = 1,
    embargo_days: int = 5,
    min_pnl_threshold: float = 5.0,
    model_id_prefix: str = "meta_wf",
) -> dict:
    """在 closed chain 上跑 Walk-Forward Meta 模型评估."""
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import roc_auc_score, accuracy_score
    except ImportError as e:
        return {"error": f"sklearn missing: {e}"}

    rows = _assemble_training_dataset(conn, min_pnl_threshold=min_pnl_threshold)
    logger.info("[SEF WF] closed chain 样本 %d", len(rows))

    X_list, y_list, sample_times, ordered_rows = [], [], [], []
    for r in rows:
        vec = _row_to_features(r)
        if vec is None:
            continue
        start = to_iso(r["entry_date"])
        if not start:
            continue
        # Label span: 固定 120d（与 meta_labeling 一致）
        end_dt = datetime.strptime(start, "%Y-%m-%d") + timedelta(days=120)
        end = end_dt.strftime("%Y-%m-%d")
        X_list.append(vec)
        y_list.append(1 if (r["chain_follow_pnl"] or 0) > min_pnl_threshold else 0)
        sample_times.append([start, end])
        ordered_rows.append(r)

    order = np.argsort([s[0] for s in sample_times])
    X = np.array(X_list)[order]
    y = np.array(y_list)[order]
    st = [sample_times[i] for i in order]

    splits = purged_walk_forward_splits(
        st,
        train_window_months=train_window_months,
        test_window_months=test_window_months,
        embargo_days=embargo_days,
    )
    logger.info("[SEF WF] 折数 %d", len(splits))

    model_id = f"{model_id_prefix}_{datetime.utcnow().strftime('%Y%m%d')}"
    conn.execute("DELETE FROM backtest_walk_forward WHERE model_id = ?", (model_id,))

    fold_stats = []
    for i, (tr, te) in enumerate(splits):
        if len(tr) < 30 or len(te) < 5:
            continue
        y_tr = y[tr]
        if len(np.unique(y_tr)) < 2:
            continue
        clf = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42
        )
        clf.fit(X[tr], y_tr)
        probs = clf.predict_proba(X[te])[:, 1]
        preds = (probs > 0.5).astype(int)
        try:
            auc = float(roc_auc_score(y[te], probs))
        except ValueError:
            auc = None
        acc = float(accuracy_score(y[te], preds))
        hit = float((preds == y[te]).mean())

        fold_start = st[tr[0]][0]
        fold_end = st[te[-1]][0]
        # IR / turnover 在纯分类场景下记为 None（需要收益序列才能算）
        conn.execute(
            """
            INSERT INTO backtest_walk_forward(
                model_id, fold_id, fold_start, fold_end, n_samples,
                oos_ic, oos_rank_ic, oos_sharpe, oos_maxdd,
                oos_hit_rate, oos_turnover, oos_ir
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                model_id, i, fold_start, fold_end, int(len(te)),
                auc, auc, None, None,
                hit, None, None,
            ),
        )
        fold_stats.append({
            "fold": i,
            "n_test": len(te),
            "auc": auc,
            "hit_rate": hit,
        })
    conn.commit()

    aucs = [f["auc"] for f in fold_stats if f["auc"] is not None]
    hits = [f["hit_rate"] for f in fold_stats]

    report = {
        "model_id": model_id,
        "n_folds": len(splits),
        "n_folds_fitted": len(fold_stats),
        "oos_auc_mean": round(float(np.mean(aucs)), 4) if aucs else None,
        "oos_auc_median": round(float(np.median(aucs)), 4) if aucs else None,
        "oos_hit_rate_mean": round(float(np.mean(hits)), 4) if hits else None,
        "train_window_months": train_window_months,
        "test_window_months": test_window_months,
        "embargo_days": embargo_days,
    }
    logger.info("[SEF WF] %s", report)
    return report
