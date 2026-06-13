"""C1 — RankIC 横截面诊断 + horizon 衰减剖面.

干净特征集 = fact_feature_panel 全列 - MODEL_INPUT_EXCLUDED_COLS - close (62 列).
regime_flag (VARCHAR) 非数值, 从 IC 计算排除 (61 数值特征参与排名 IC).

角度1: 每个特征算 daily RankIC = corr(rank(feat), rank(forward_ret_20d)) 跨日平均 + IC_IR.
角度2: 对 top10 IC_IR 候选, 算 RankIC vs forward_ret_{5,20,60,90}d 衰减矩阵.

纪律 (S3): |RankIC|>0.15 单因子 = §4.2 红线 → 报泄漏嫌疑, 不当 alpha.
PIT 已由干净特征集保证 (label 列已排除)。
"""
import importlib.util
import duckdb
import numpy as np
import pandas as pd

DB = "data/smartmoney.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
BUILDER = "backend/scripts/build_feature_panel_duck.py"


def clean_features():
    s = importlib.util.spec_from_file_location("b", BUILDER)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    excl = set(m.MODEL_INPUT_EXCLUDED_COLS) | {"close"}
    con = duckdb.connect(DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='fact_feature_panel' ORDER BY ordinal_position"
    ).fetchall()
    con.close()
    feats = [(c, t) for c, t in cols if c not in excl]
    numeric = [c for c, t in feats if t != "VARCHAR"]
    nonnum = [c for c, t in feats if t == "VARCHAR"]
    return numeric, nonnum, len(feats)


def daily_rankic(df, feat, label):
    """每日 spearman corr(feat, label) 跨日平均 + IC_IR + N天."""
    sub = df[["date", feat, label]].dropna()
    ics = []
    for d, g in sub.groupby("date"):
        if len(g) < 30:  # SQL LIMIT-style 最小截面 30 只
            continue
        rf = g[feat].rank()
        rl = g[label].rank()
        if rf.std() == 0 or rl.std() == 0:
            continue
        ic = np.corrcoef(rf, rl)[0, 1]
        if np.isfinite(ic):
            ics.append(ic)
    if len(ics) < 10:
        return None
    ics = np.array(ics)
    mean_ic = ics.mean()
    std_ic = ics.std(ddof=1)
    ir = mean_ic / std_ic if std_ic > 0 else np.nan
    return {
        "feature": feat,
        "rank_ic": round(float(mean_ic), 5),
        "ic_std": round(float(std_ic), 5),
        "ic_ir": round(float(ir), 4),
        "n_days": len(ics),
    }


def main():
    numeric, nonnum, n_clean = clean_features()
    print(f"clean feats={n_clean}, numeric={len(numeric)}, nonnum(excl from IC)={nonnum}")

    con = duckdb.connect(DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    label_cols = ["forward_ret_5d", "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"]
    sel = ["date"] + numeric + label_cols
    df = con.execute(
        f"SELECT {', '.join(sel)} FROM fact_feature_panel"
    ).fetch_df()
    con.close()
    print(f"loaded rows={len(df)}, days={df['date'].nunique()}")

    # 角度1: 全 61 数值特征 vs forward_ret_20d
    rows = []
    for f in numeric:
        r = daily_rankic(df, f, "forward_ret_20d")
        if r:
            rows.append(r)
    res = pd.DataFrame(rows)
    res["abs_ic"] = res["rank_ic"].abs()
    res["abs_ir"] = res["ic_ir"].abs()
    res = res.sort_values("abs_ir", ascending=False).reset_index(drop=True)

    print("\n=== 角度1: RankIC vs forward_ret_20d (sorted by |IC_IR|) ===")
    print(res.to_string())

    # top10 by |IC_IR|
    top10 = res.head(10)["feature"].tolist()
    print(f"\nTOP10 by |IC_IR|: {top10}")

    # 角度2: horizon 衰减矩阵
    print("\n=== 角度2: horizon x RankIC 矩阵 (top10 候选) ===")
    hmat = []
    for f in top10:
        row = {"feature": f}
        for lab in label_cols:
            r = daily_rankic(df, f, lab)
            h = lab.replace("forward_ret_", "")
            row[h] = r["rank_ic"] if r else np.nan
            row[h + "_ir"] = r["ic_ir"] if r else np.nan
        hmat.append(row)
    hdf = pd.DataFrame(hmat)
    print(hdf.to_string())

    # 红线扫描
    print("\n=== §4.2 红线扫描 (|RankIC|>0.15 = 泄漏嫌疑) ===")
    leak = res[res["abs_ic"] > 0.15]
    print(leak[["feature", "rank_ic", "ic_ir", "n_days"]].to_string() if len(leak) else "无单因子 |RankIC|>0.15")

    res.to_csv("analysis/c1_rankic_20d_20260613.csv", index=False)
    hdf.to_csv("analysis/c1_horizon_matrix_20260613.csv", index=False)
    print("\nsaved CSVs")


if __name__ == "__main__":
    main()
