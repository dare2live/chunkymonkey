"""C1 角度1: 全 62 干净特征 vs forward_ret_20d 的 walk-forward RankIC 排名.

每日 RankIC = corr(rank(feat), rank(forward_ret_20d)) over stocks on that date (Spearman).
跨日平均 RankIC + IC_IR (= mean/std of daily IC) + 正/负 IC 日占比.
按 |IC_IR| 降序取 top 15. 标 |RankIC|>0.15 异常高 (§4.2 泄漏嫌疑).

PIT 由干净特征集保证 (label 列已排除). DB 只读.
"""
import importlib.util
import json
import duckdb

DB = "data/smartmoney.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
LABEL = "forward_ret_20d"
MIN_PAIRS_PER_DAY = 30  # 单日至少 30 只有效配对才计入 (避免稀疏日噪音 IC)

# --- 干净特征集 = panel 全列 - builder excluded - close ---
spec = importlib.util.spec_from_file_location("b", "backend/scripts/build_feature_panel_duck.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
EXCL = set(mod.MODEL_INPUT_EXCLUDED_COLS) | {"close"}

con = duckdb.connect(DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
cols = con.execute(
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_name='fact_feature_panel' ORDER BY ordinal_position"
).fetchall()

clean_all = [c for c, _ in cols if c not in EXCL]
# regime_flag 是 VARCHAR 类别变量, 无法算 Spearman corr, 单独标记
numeric_types = {"FLOAT", "DOUBLE", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL"}
clean_numeric = [c for c, t in cols if c not in EXCL and t in numeric_types]
clean_nonnumeric = [c for c in clean_all if c not in clean_numeric]

print(f"[info] clean total={len(clean_all)}  numeric={len(clean_numeric)}  "
      f"non-numeric(skipped)={clean_nonnumeric}")

results = []
for feat in clean_numeric:
    # 每日: 在该 date 上, 对同时非空的 (feat, label) 配对算 rank 后 corr.
    # DuckDB corr() 配 rank() over PARTITION BY date 实现 Spearman RankIC.
    q = f"""
    WITH base AS (
        SELECT date, {feat} AS f, {LABEL} AS y
        FROM fact_feature_panel
        WHERE {feat} IS NOT NULL AND {LABEL} IS NOT NULL
    ),
    ranked AS (
        SELECT date,
               CAST(rank() OVER (PARTITION BY date ORDER BY f) AS DOUBLE) AS rf,
               CAST(rank() OVER (PARTITION BY date ORDER BY y) AS DOUBLE) AS ry
        FROM base
    ),
    daily AS (
        SELECT date, corr(rf, ry) AS ic, count(*) AS n
        FROM ranked
        GROUP BY date
        HAVING count(*) >= {MIN_PAIRS_PER_DAY}
    ),
    daily_valid AS (
        SELECT date, ic, n FROM daily
        WHERE ic IS NOT NULL AND NOT isnan(ic) AND NOT isinf(ic)
    )
    SELECT
        avg(ic)                                              AS mean_ic,
        stddev_samp(ic)                                      AS std_ic,
        count(*)                                             AS n_days,
        avg(CASE WHEN ic > 0 THEN 1.0 ELSE 0.0 END)          AS pos_day_frac,
        avg(CASE WHEN ic < 0 THEN 1.0 ELSE 0.0 END)          AS neg_day_frac,
        avg(n)                                               AS avg_pairs_per_day
    FROM daily_valid
    """
    row = con.execute(q).fetchone()
    mean_ic, std_ic, n_days, pos_frac, neg_frac, avg_pairs = row
    if mean_ic is None or n_days is None or n_days == 0:
        continue
    ic_ir = (mean_ic / std_ic) if (std_ic and std_ic > 0) else None
    abs_ic = abs(mean_ic)
    if abs_ic > 0.15:
        note = "异常高泄漏嫌疑"
    elif abs_ic >= 0.02:
        note = "诚实信号"
    else:
        note = "弱"
    results.append({
        "feature": feat,
        "rank_ic": round(mean_ic, 5),
        "std_ic": round(std_ic, 5) if std_ic is not None else None,
        "ic_ir": round(ic_ir, 4) if ic_ir is not None else None,
        "n_days": int(n_days),
        "pos_day_frac": round(pos_frac, 4),
        "neg_day_frac": round(neg_frac, 4),
        "avg_pairs_per_day": round(avg_pairs, 1),
        "abs_rank_ic": round(abs_ic, 5),
        "note": note,
    })

con.close()

# 排名 1: 按 |IC_IR| 降序 (主表)
by_ir = sorted(
    [r for r in results if r["ic_ir"] is not None],
    key=lambda r: abs(r["ic_ir"]), reverse=True,
)
# 排名 2: 按 |RankIC| 降序 (查泄漏嫌疑用)
by_absic = sorted(results, key=lambda r: r["abs_rank_ic"], reverse=True)

suspects = [r["feature"] for r in results if r["abs_rank_ic"] > 0.15]

out = {
    "label": LABEL,
    "min_pairs_per_day": MIN_PAIRS_PER_DAY,
    "clean_feature_count": len(clean_all),
    "numeric_evaluated": len(clean_numeric),
    "non_numeric_skipped": clean_nonnumeric,
    "top15_by_abs_ic_ir": by_ir[:15],
    "top10_by_abs_rank_ic": by_absic[:10],
    "leakage_suspects_absic_gt_0p15": suspects,
    "all_features_full": by_ir,  # 全量留档
}

with open("analysis/c1_rankic_walkforward_20260613.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("\n=== TOP 15 by |IC_IR| ===")
print(f"{'feature':<42}{'rank_ic':>10}{'ic_ir':>10}{'posfrac':>9}{'ndays':>7}  note")
for r in by_ir[:15]:
    print(f"{r['feature']:<42}{r['rank_ic']:>10}{r['ic_ir']:>10}"
          f"{r['pos_day_frac']:>9}{r['n_days']:>7}  {r['note']}")

print("\n=== TOP 10 by |RankIC| (泄漏排查) ===")
for r in by_absic[:10]:
    print(f"{r['feature']:<42} rankIC={r['rank_ic']:>9}  ic_ir={r['ic_ir']}  {r['note']}")

print(f"\nleakage suspects (|RankIC|>0.15): {suspects}")
print("\nwrote analysis/c1_rankic_walkforward_20260613.json")
