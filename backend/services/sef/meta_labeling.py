"""Meta-Labeling (Lopez de Prado 2018, Ch.3).

两阶段模型:
- Primary: V6 硬规则已给出 follow/skip (用 fact_institution_event.follow_gate)
- Meta: 学"Primary 说 follow 的那些信号里，哪些真赚钱"

Meta 的 label = chain_follow_pnl > 阈值（默认 5pp）
Meta 的 feature = [bayesian μ_post, σ_post, capability α_median, expert_level,
                   halflife_days, style_r2, industry_l1_onehot, …]

用 Purged K-Fold + Embargo 交叉验证，避免 label 重叠泄漏。

输出: 新表 `mart_meta_label_model`（模型版本 + CV 指标）+
       回填 model_signals_log（如果存在对应信号）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger("cm-api.sef.meta")


def _ensure_tables(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mart_meta_label_model (
            model_version       TEXT PRIMARY KEY,
            trained_at          TEXT,
            n_samples           INTEGER,
            n_features          INTEGER,
            cv_folds            INTEGER,
            cv_auc_mean         REAL,
            cv_auc_std          REAL,
            cv_accuracy_mean    REAL,
            precision_follow    REAL,
            recall_follow       REAL,
            feature_importance  TEXT,
            hyperparams         TEXT
        );
        CREATE TABLE IF NOT EXISTS mart_meta_label_predictions (
            stock_code          TEXT NOT NULL,
            institution_id      TEXT NOT NULL,
            notice_date         TEXT NOT NULL,
            model_version       TEXT NOT NULL,
            primary_action      TEXT,  -- 'follow' / 'watch' / 'skip'
            meta_prob_follow    REAL,
            meta_action         TEXT,
            pnl_realized        REAL,
            PRIMARY KEY(stock_code, institution_id, notice_date, model_version)
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1) 特征 + label
# ---------------------------------------------------------------------------


def _assemble_training_dataset(
    conn: sqlite3.Connection, *, min_pnl_threshold: float = 5.0
) -> tuple:
    """从 fact_chain_alpha_truth + mart_institution_capability + mart_institution_style 拼特征.

    label = 1 if chain_follow_pnl > min_pnl_threshold else 0
    """
    rows = conn.execute(
        """
        SELECT
            t.institution_id, t.stock_code, t.entry_date, t.eval_date,
            t.chain_follow_pnl, t.chain_follow_max_dd, t.chain_days,
            t.tb_label, t.industry_l1, t.industry_l2,
            cap.alpha_median AS cap_alpha,
            cap.alpha_se AS cap_se,
            cap.sample_count AS cap_n,
            cap.expert_level AS cap_level,
            cap.alpha_halflife_days AS cap_halflife,
            sty.style_r2 AS sty_r2,
            sty.style_alpha_pure AS sty_alpha,
            rhc.entry_premium_pct AS entry_prem,
            rhc.entry_follow_price AS entry_price
        FROM fact_chain_alpha_truth t
        LEFT JOIN mart_institution_capability cap
            ON cap.institution_id = t.institution_id
           AND cap.industry_level = 'L2'
           AND cap.industry_code = t.industry_l2
        LEFT JOIN mart_institution_style sty ON sty.institution_id = t.institution_id
        LEFT JOIN research_holding_chains rhc
            ON rhc.institution_id = t.institution_id
           AND rhc.stock_code = t.stock_code
           AND rhc.chain_id = t.research_chain_id
        WHERE t.chain_follow_pnl IS NOT NULL
          AND t.status = 'closed'   -- open 链 eval_date=today，会让 Purging 剔光训练集
        """
    ).fetchall()
    return rows


def _row_to_features(r: sqlite3.Row) -> Optional[list[float]]:
    """把一行 SQL row 转成 numeric feature vector."""
    def f(v):
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # industry_l1 one-hot (T01..T13)
    l1 = r["industry_l1"] if "industry_l1" in r.keys() else None
    industries = [f"T{str(i).zfill(2)}" for i in range(1, 14)]
    onehot = [1.0 if l1 == ind else 0.0 for ind in industries]

    vec = [
        f(r["cap_alpha"]),
        f(r["cap_se"]),
        f(r["cap_n"]),
        f(r["cap_level"]),
        f(r["cap_halflife"]),
        f(r["sty_r2"]),
        f(r["sty_alpha"]),
        f(r["entry_prem"]),
        f(r["entry_price"]),
        f(r["chain_days"]),
    ] + onehot
    return vec


FEATURE_NAMES = [
    "cap_alpha", "cap_se", "cap_n", "cap_level", "cap_halflife",
    "sty_r2", "sty_alpha", "entry_prem", "entry_price", "chain_days",
] + [f"ind_T{str(i).zfill(2)}" for i in range(1, 14)]


# ---------------------------------------------------------------------------
# 2) 训练 + 评估
# ---------------------------------------------------------------------------


def train_meta_model(
    conn: sqlite3.Connection,
    *,
    min_pnl_threshold: float = 5.0,
    cv_folds: int = 5,
    embargo_days: int = 5,
) -> dict:
    """用 Purged K-Fold 评估，输出模型版本 + 指标，并保存模型."""
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import roc_auc_score, accuracy_score, precision_score, recall_score
    except ImportError as e:
        logger.warning("[SEF Meta] sklearn 缺失: %s", e)
        return {"error": str(e)}

    from datetime import datetime, timedelta

    from .purged_cv import PurgedKFold
    from ._dates import to_iso

    _ensure_tables(conn)
    rows = _assemble_training_dataset(conn, min_pnl_threshold=min_pnl_threshold)
    logger.info("[SEF Meta] 训练样本 %d", len(rows))

    # 构造 X, y, sample_times
    # 关键：label span 固定为 entry + 120d (Triple Barrier horizon)，
    # 而不是 eval_date。否则 closed chain 横跨多年会让 Purging 清空训练集.
    LABEL_SPAN_DAYS = 120
    X_list, y_list, sample_times = [], [], []
    for r in rows:
        vec = _row_to_features(r)
        if vec is None:
            continue
        start = to_iso(r["entry_date"])
        if not start:
            continue
        end_dt = datetime.strptime(start, "%Y-%m-%d") + timedelta(days=LABEL_SPAN_DAYS)
        end = end_dt.strftime("%Y-%m-%d")
        X_list.append(vec)
        y_list.append(1 if (r["chain_follow_pnl"] or 0) > min_pnl_threshold else 0)
        sample_times.append([start, end])

    # PurgedKFold 要求按 start 升序
    order = np.argsort([s[0] for s in sample_times])
    X_list = [X_list[i] for i in order]
    y_list = [y_list[i] for i in order]
    sample_times = [sample_times[i] for i in order]
    rows = [rows[i] for i in order]

    if len(X_list) < 100:
        return {"error": f"insufficient samples: {len(X_list)}"}

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)
    logger.info("[SEF Meta] X shape=%s, positive ratio=%.2f%%",
                X.shape, y.mean() * 100)

    # Purged K-Fold CV
    cv = PurgedKFold(n_splits=cv_folds, embargo_days=embargo_days)
    aucs, accs = [], []
    all_importance: list[np.ndarray] = []
    y_oof_true: list[int] = []
    y_oof_prob: list[float] = []
    for train_idx, test_idx in cv.split(sample_times):
        if len(train_idx) < 20 or len(test_idx) < 5:
            continue
        if len(np.unique(y[train_idx])) < 2:
            continue
        clf = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42
        )
        clf.fit(X[train_idx], y[train_idx])
        probs = clf.predict_proba(X[test_idx])[:, 1]
        preds = (probs > 0.5).astype(int)
        try:
            aucs.append(float(roc_auc_score(y[test_idx], probs)))
        except ValueError:
            pass
        accs.append(float(accuracy_score(y[test_idx], preds)))
        all_importance.append(clf.feature_importances_)
        y_oof_true.extend(y[test_idx].tolist())
        y_oof_prob.extend(probs.tolist())

    # 最终模型用全量训练（用于推理）
    clf_final = GradientBoostingClassifier(
        n_estimators=80, max_depth=3, learning_rate=0.1, random_state=42
    )
    clf_final.fit(X, y)

    # 整体指标（OOF）
    from sklearn.metrics import precision_score, recall_score

    if y_oof_true:
        y_arr = np.array(y_oof_true)
        y_p = np.array(y_oof_prob)
        y_preds = (y_p > 0.5).astype(int)
        prec = float(precision_score(y_arr, y_preds, zero_division=0))
        rec = float(recall_score(y_arr, y_preds, zero_division=0))
    else:
        prec = rec = None

    importance = np.mean(all_importance, axis=0) if all_importance else clf_final.feature_importances_
    feat_imp_dict = {name: float(v) for name, v in zip(FEATURE_NAMES, importance)}

    version = f"meta_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_meta_label_model(
            model_version, trained_at, n_samples, n_features, cv_folds,
            cv_auc_mean, cv_auc_std, cv_accuracy_mean,
            precision_follow, recall_follow,
            feature_importance, hyperparams
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            version,
            datetime.utcnow().isoformat(timespec="seconds"),
            len(X_list), X.shape[1], cv_folds,
            float(np.mean(aucs)) if aucs else None,
            float(np.std(aucs)) if aucs else None,
            float(np.mean(accs)) if accs else None,
            prec, rec,
            json.dumps(feat_imp_dict),
            json.dumps({"n_estimators": 80, "max_depth": 3, "learning_rate": 0.1}),
        ),
    )
    conn.commit()

    # 把 OOF 预测写进 predictions 表
    for i, ri in enumerate(rows[: len(X_list)]):
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_meta_label_predictions(
                stock_code, institution_id, notice_date, model_version,
                primary_action, meta_prob_follow, meta_action, pnl_realized
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                ri["stock_code"], ri["institution_id"], ri["entry_date"], version,
                "follow",
                float(clf_final.predict_proba([X[i]])[0, 1]),
                "follow" if clf_final.predict([X[i]])[0] == 1 else "skip",
                float(ri["chain_follow_pnl"] or 0),
            ),
        )
    conn.commit()

    report = {
        "model_version": version,
        "n_samples": len(X_list),
        "n_features": X.shape[1],
        "positive_ratio": float(y.mean()),
        "cv_auc_mean": float(np.mean(aucs)) if aucs else None,
        "cv_auc_std": float(np.std(aucs)) if aucs else None,
        "cv_accuracy_mean": float(np.mean(accs)) if accs else None,
        "precision_follow_oof": prec,
        "recall_follow_oof": rec,
        "top_features": sorted(feat_imp_dict.items(), key=lambda x: -x[1])[:5],
    }
    logger.info("[SEF Meta] 完成: %s", report)
    return report
