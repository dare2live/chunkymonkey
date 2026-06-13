"""诊断 S3 (zhushenglang_S3) fold0 AUC 0.8368 由什么驱动 — 真动量 edge vs 特征前瞻泄漏.

复用 backend/scripts/experiment_zhushenglang_s3.py 的 load_panel + make_folds (逐字 import),
仅在 fold0 上做:
  1. LightGBM gain importance top15
  2. leave-one-group-out OOS AUC ablation (逐组剔除)
  3. 关键判定: 剔除全部动量族 / 剔除 follow_net_return 标签族后的 AUC

只读 DB. 产出 JSON 到 stdout + analysis/diag_s3_auc_drivers_20260613.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import lightgbm as lgb  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from scripts.experiment_zhushenglang_s3 import (  # noqa: E402
    load_panel,
    make_folds,
    LGBM_PARAMS,
    SMART_DB,
)


def feature_group(name: str) -> str:
    """把 67 特征分到诊断组. 关键: follow_net_return_* 单列一组 (前瞻标签嫌疑)."""
    if name.startswith("follow_net_return_"):
        return "follow_net_return(LABEL?)"
    if name.endswith("_rank"):
        return "rank"
    if name.endswith("_rel") or name.startswith("hs300_") or "_tdx_l1_rel" in name:
        return "rel/index"
    if name.startswith("ret_") or name in ("momentum_diff",):
        return "momentum_ret"
    if name.startswith("ma_ratio_") or name.startswith("range_pos_"):
        return "trend_pos"
    if name.startswith("vol_") or name in ("vol_z20d",):
        return "vol"
    if name.startswith("k") and name in ("kmid", "klen", "kup", "klow", "ksft"):
        return "kbar"
    if name.startswith("rz_") or "rz_balance" in name:
        return "margin_rz"
    if (
        name.startswith("inst_")
        or name.startswith("exec_")
        or name.startswith("lhb_")
        or name.startswith("jgdy")
        or name.startswith("dzjy")
        or name.startswith("days_since_exec")
        or name.startswith("days_since_lhb")
    ):
        return "smartmoney_inst"
    if name.startswith("shareholder_plan") or name.startswith("days_since_shareholder"):
        return "shareholder_plan"
    if name.endswith("_qoq"):
        return "holder_qoq"
    if name in ("yjyg_lower_pct", "yjyg_upper_pct", "roe", "eps_basic"):
        return "fundamental"
    if name == "regime_flag":
        return "regime"
    if name == "amount_chg_5d":
        return "momentum_ret"  # amount momentum
    return "other"


def fit_auc(Xtr, ytr, Xte, yte, cols_idx):
    m = lgb.LGBMClassifier(**LGBM_PARAMS)
    m.fit(Xtr[:, cols_idx], ytr)
    score = m.predict_proba(Xte[:, cols_idx])[:, 1]
    return float(roc_auc_score(yte, score)), m


def main() -> int:
    con = duckdb.connect(str(SMART_DB), read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    dates, X, y, feats = load_panel(con)
    con.close()
    folds = make_folds(dates)
    tr, te, tr_end, te_lo, te_hi = folds[0]
    Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]

    feats = list(feats)
    groups: dict[str, list[int]] = {}
    for i, f in enumerate(feats):
        groups.setdefault(feature_group(f), []).append(i)

    # ---- 1. full-feature fold0 baseline + gain importance ----
    all_idx = np.arange(len(feats))
    base_auc, model = fit_auc(Xtr, ytr, Xte, yte, all_idx)
    gains = model.booster_.feature_importance(importance_type="gain")
    order = np.argsort(-gains)
    top15 = [
        {"feature": feats[i], "group": feature_group(feats[i]),
         "gain": float(round(gains[i], 1)),
         "gain_pct": float(round(100 * gains[i] / gains.sum(), 2))}
        for i in order[:15]
    ]

    # ---- 2. leave-one-group-out AUC ablation ----
    logo = []
    for g, idxs in groups.items():
        keep = [i for i in range(len(feats)) if i not in set(idxs)]
        auc_wo, _ = fit_auc(Xtr, ytr, Xte, yte, np.array(keep))
        logo.append({
            "group_dropped": g,
            "n_feats_in_group": len(idxs),
            "feats": [feats[i] for i in idxs],
            "auc_without_group": round(auc_wo, 4),
            "auc_drop": round(base_auc - auc_wo, 4),
        })
    logo.sort(key=lambda r: -r["auc_drop"])

    # ---- 3. 关键组合剔除 ----
    momentum_groups = {"momentum_ret", "trend_pos", "rank", "rel/index"}
    mom_idx = set()
    for g in momentum_groups:
        mom_idx.update(groups.get(g, []))
    keep_no_mom = np.array([i for i in range(len(feats)) if i not in mom_idx])
    auc_no_mom, _ = fit_auc(Xtr, ytr, Xte, yte, keep_no_mom)

    follow_idx = set(groups.get("follow_net_return(LABEL?)", []))
    keep_no_follow = np.array([i for i in range(len(feats)) if i not in follow_idx])
    auc_no_follow, _ = fit_auc(Xtr, ytr, Xte, yte, keep_no_follow)

    # 只用 follow_net_return 族
    if follow_idx:
        auc_only_follow, _ = fit_auc(Xtr, ytr, Xte, yte, np.array(sorted(follow_idx)))
    else:
        auc_only_follow = None

    # 剔除 follow + momentum 双杀
    drop_both = mom_idx | follow_idx
    keep_clean = np.array([i for i in range(len(feats)) if i not in drop_both])
    auc_clean, _ = fit_auc(Xtr, ytr, Xte, yte, keep_clean)

    out = {
        "fold0": {"train_end": tr_end, "test": [te_lo, te_hi],
                  "n_train": int(tr.sum()), "n_test": int(te.sum()),
                  "base_rate_test": round(float(yte.mean()), 4)},
        "fold0_full_auc": round(base_auc, 4),
        "top15_gain": top15,
        "leave_one_group_out": logo,
        "key_ablations": {
            "drop_momentum_family (ret/trend/rank/rel)": {
                "groups": sorted(momentum_groups), "n_dropped": len(mom_idx),
                "auc": round(auc_no_mom, 4), "auc_drop": round(base_auc - auc_no_mom, 4)},
            "drop_follow_net_return": {
                "n_dropped": len(follow_idx),
                "auc": round(auc_no_follow, 4), "auc_drop": round(base_auc - auc_no_follow, 4)},
            "only_follow_net_return": {
                "n_feats": len(follow_idx),
                "auc": round(auc_only_follow, 4) if auc_only_follow else None},
            "drop_follow_and_momentum": {
                "n_dropped": len(drop_both),
                "auc": round(auc_clean, 4), "auc_drop": round(base_auc - auc_clean, 4)},
        },
        "group_membership": {g: [feats[i] for i in idxs] for g, idxs in sorted(groups.items())},
    }
    out_path = REPO / "analysis" / "diag_s3_auc_drivers_20260613.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
