"""主升浪 S3 — LightGBM walk-forward 重验 ML 假设 (预注册逐字实现).

预注册 owner = analysis/prereg_zhushenglang_s3_20260613.md (FROZEN 2026-06-13)。
判据常量必须与 prereg yaml 块逐字一致 — `--check-prereg` 机器验收。看到结果后改任何
常量/折法/超参/embargo = 触发谄媚死条款。

核心纪律 (prereg 死线):
  - embargo >= 180 交易日 (label 取 [t+1,t+180], 训练/测试前瞻窗重叠 = 标签泄漏)
  - 特征排除 forward_ret_*/close/键/源 meta (label 或非因果)
  - 标签置换对照 (折内 shuffle y → AUC 必退随机, 否则管道泄漏)

数据: fact_rally_ground_truth (S1 落库 读法B) + fact_feature_panel (68 PIT 特征), 全 read_only。

用法:
  PYTHONPATH=backend python backend/scripts/experiment_zhushenglang_s3.py
  PYTHONPATH=backend python backend/scripts/experiment_zhushenglang_s3.py --check-prereg
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

PREREG_PATH = REPO / "analysis" / "prereg_zhushenglang_s3_20260613.md"
SMART_DB = REPO / "data" / "smartmoney.duckdb"  # rule-compliance: ok evidence=read_only 判决实验, 复用现成特征面板
OUT_DIR = REPO / "analysis"

# ── 预注册冻结常量 (与 prereg yaml 块逐字对应; --check-prereg 机器验收) ──
PREREG = {
    "precision_mult": 1.3,   # J1: OOS top-decile precision >= 1.3x base rate
    "auc_floor": 0.55,       # J1: OOS AUC >= 0.55
    "auc_ceiling": 0.75,     # J2: OOS AUC <= 0.75 (异常高 leakage 红线)
    "shuffle_band": (0.45, 0.55),  # J2: 标签置换 AUC 必落此带 (否则管道泄漏)
}
EMBARGO_DAYS = 180           # 交易日, 冻结 (label horizon)
N_FOLDS = 3                  # 冻结
TOP_DECILE = 0.10            # top-decile precision, 冻结
LGBM_PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=31,
                   random_state=20260613, n_jobs=-1, verbosity=-1)  # 固定, 不判决轮 Optuna
EXCLUDE_COLS = {"stock_code", "date", "close", "kline_source_name", "kline_source_tier",
                "kline_is_fallback", "built_at", "forward_ret_5d", "forward_ret_10d",
                "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"}
# built_at = 运行时元数据 (排除, 防 run-time 泄漏); kline_is_fallback = 源质量 meta (非因果, 排除);
# regime_flag (VARCHAR bull/bear/flat) = 真特征, label 编码保留 (研究日志 hs300 regime)


def check_prereg_consistency() -> list[str]:
    text = PREREG_PATH.read_text(encoding="utf-8")
    p = []
    if f"precision_mult: {PREREG['precision_mult']}" not in text:
        p.append("precision_mult 与 prereg 不一致")
    if f"auc_floor: {PREREG['auc_floor']}" not in text:
        p.append("auc_floor 与 prereg 不一致")
    if f"auc_ceiling: {PREREG['auc_ceiling']}" not in text:
        p.append("auc_ceiling 与 prereg 不一致")
    if "embargo>=180" not in text.replace(" ", "") and "embargo >= 180" not in text:
        p.append("embargo 180 与 prereg 不一致")
    if "shuffle_auc_band: [0.45, 0.55]" not in text:
        p.append("shuffle 带与 prereg 不一致")
    if not re.search(r">=\s*3\s*折", text):
        p.append("折数 >=3 与 prereg 不一致")
    return p


def load_panel(con):
    """JOIN ground truth (2023+) 到 fact_feature_panel; 返回 (dates, X, y, feat_names)."""
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='fact_feature_panel'"
    ).fetchall()]
    feats = [c for c in cols if c not in EXCLUDE_COLS]
    feat_sql = ", ".join(f'fp."{c}"' for c in feats)
    import numpy as np
    df = con.execute(
        f"""
        SELECT fp.date,
               CASE WHEN gt.is_true_rally THEN 1 ELSE 0 END AS y,
               {feat_sql}
        FROM fact_rally_ground_truth gt
        JOIN fact_feature_panel fp
          ON fp.stock_code = gt.stock_code
         AND fp.date = substr(gt.event_date,1,4)||'-'||substr(gt.event_date,5,2)||'-'||substr(gt.event_date,7,2)
        WHERE gt.event_date >= '20230101'  -- rule-compliance: ok evidence=fact_feature_panel 起点 2023-01-03 实测, 2022 段无特征 (prereg 披露)
        ORDER BY fp.date
        """
    ).df()
    dates = df["date"].tolist()
    y = df["y"].to_numpy(dtype=float)
    import pandas as pd
    feat_df = df[feats].copy()
    for c in feats:
        if pd.api.types.is_bool_dtype(feat_df[c]):
            feat_df[c] = feat_df[c].astype(float)
        elif not pd.api.types.is_numeric_dtype(feat_df[c]):
            feat_df[c] = feat_df[c].astype("category").cat.codes.astype(float)  # label 编码 (regime_flag 等)
        else:
            feat_df[c] = feat_df[c].astype(float)
    X = feat_df.to_numpy(dtype=float)
    return dates, X, y, feats


def make_folds(dates):
    """expanding 训练 + embargo>=180 交易日的 N_FOLDS 折. 返回 [(train_mask, test_mask)]."""
    import numpy as np
    uniq = sorted(set(dates))
    n = len(uniq)
    pos = {d: i for i, d in enumerate(uniq)}  # 交易日序号 (面板内 distinct 日 = 交易日轴)
    didx = np.array([pos[d] for d in dates])
    # 末 N_FOLDS 段等宽测试, 每折训练 = [0, test_start - EMBARGO]
    test_w = (n - EMBARGO_DAYS) // (N_FOLDS + 1)  # 留首段给最早折的训练
    folds = []
    for k in range(N_FOLDS):
        test_hi = n - (N_FOLDS - 1 - k) * test_w   # 折 k 测试上界 (序号)
        test_lo = test_hi - test_w
        train_hi = test_lo - EMBARGO_DAYS
        if train_hi <= 0:
            continue
        tr = didx < train_hi
        te = (didx >= test_lo) & (didx < test_hi)
        if tr.sum() < 100 or te.sum() < 50:
            continue
        folds.append((tr, te, uniq[max(0, train_hi - 1)], uniq[test_lo], uniq[min(n - 1, test_hi - 1)]))
    return folds


def _fit_predict(Xtr, ytr, Xte):
    import lightgbm as lgb
    m = lgb.LGBMClassifier(**LGBM_PARAMS)
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


def _metrics(y_true, y_score):
    from sklearn.metrics import roc_auc_score
    import numpy as np
    auc = float(roc_auc_score(y_true, y_score)) if len(set(y_true)) > 1 else float("nan")
    k = max(1, int(len(y_score) * TOP_DECILE))
    top_idx = np.argsort(-y_score)[:k]
    prec = float(y_true[top_idx].mean())
    base = float(y_true.mean())
    return auc, prec, base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-prereg", action="store_true")
    ap.add_argument("--db", default=str(SMART_DB))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    problems = check_prereg_consistency()
    if problems:
        print("PREREG 一致性 FAIL:", problems)
        return 2
    if args.check_prereg:
        print("PREREG 一致性 PASS (常量与冻结文档逐字一致)")
        return 0

    import duckdb
    import numpy as np
    con = duckdb.connect(args.db, read_only=True)  # rule-compliance: ok evidence=read_only 判决实验
    dates, X, y, feats = load_panel(con)
    con.close()
    if len(y) < 1000:
        print(json.dumps({"verdict": "INVALID", "reason": f"样本不足 {len(y)}"}, ensure_ascii=False))
        return 3

    folds = make_folds(dates)
    if len(folds) < N_FOLDS:
        print(json.dumps({"verdict": "INVALID", "reason": f"embargo {EMBARGO_DAYS}d 下仅 {len(folds)} 折 < {N_FOLDS}"},
                         ensure_ascii=False))
        return 3

    rng = np.random.default_rng(20260613)
    fold_rows, shuffle_aucs = [], []
    for k, (tr, te, tr_end, te_lo, te_hi) in enumerate(folds):
        score = _fit_predict(X[tr], y[tr], X[te])
        auc, prec, base = _metrics(y[te], score)
        fold_rows.append({"fold": k, "train_end": tr_end, "test": [te_lo, te_hi],
                          "n_train": int(tr.sum()), "n_test": int(te.sum()),
                          "auc": round(auc, 4), "top_decile_prec": round(prec, 4),
                          "base_rate": round(base, 4),
                          "prec_mult": round(prec / base, 3) if base > 0 else None})
        # 标签置换对照: 折内 shuffle 训练 y → 必退随机 (管道泄漏硬证伪)
        y_sh = rng.permutation(y[tr])
        sh_score = _fit_predict(X[tr], y_sh, X[te])
        sh_auc, _, _ = _metrics(y[te], sh_score)
        shuffle_aucs.append(sh_auc)

    aucs = [f["auc"] for f in fold_rows]
    precs = [f["top_decile_prec"] for f in fold_rows]
    bases = [f["base_rate"] for f in fold_rows]
    mult = [f["prec_mult"] for f in fold_rows if f["prec_mult"] is not None]
    mean_auc = float(np.nanmean(aucs))
    mean_prec_mult = float(np.mean(mult)) if mult else 0.0
    mean_shuffle = float(np.nanmean(shuffle_aucs))

    j1 = mean_prec_mult >= PREREG["precision_mult"] and mean_auc >= PREREG["auc_floor"]
    lo, hi = PREREG["shuffle_band"]
    j2 = mean_auc <= PREREG["auc_ceiling"] and (lo <= mean_shuffle <= hi)
    pos_folds = sum(1 for f in fold_rows if f["prec_mult"] and f["prec_mult"] > 1.0)
    j3 = pos_folds > len(fold_rows) / 2   # 多数折 top-decile 超 base rate
    verdict = "GO" if (j1 and j2 and j3) else "REJECT"

    out = {
        "experiment": "zhushenglang_S3_lightgbm_walkforward",
        "prereg": PREREG_PATH.name,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(y), "n_features": len(feats), "base_rate_overall": round(float(y.mean()), 4),
        "embargo_trading_days": EMBARGO_DAYS, "n_folds": len(fold_rows),
        "folds": fold_rows,
        "J1_signal": {"mean_top_decile_prec_mult": round(mean_prec_mult, 3),
                      "need_mult": PREREG["precision_mult"], "mean_auc": round(mean_auc, 4),
                      "need_auc": PREREG["auc_floor"], "pass": j1},
        "J2_not_leakage": {"mean_auc": round(mean_auc, 4), "auc_ceiling": PREREG["auc_ceiling"],
                           "mean_shuffle_auc": round(mean_shuffle, 4), "shuffle_band": list(PREREG["shuffle_band"]),
                           "pass": j2},
        "J3_fold_consistency": {"positive_folds": pos_folds, "n_folds": len(fold_rows), "pass": j3},
        "note": "最新测试折 label 可能因数据截止 (2026-05-28 scan) 前瞻窗截断而 TRUE 偏少 (保守, 披露)",
        "verdict": verdict,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = Path(args.out_dir) / f"zhushenglang_s3_verdict_{stamp}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n判决已落盘: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
