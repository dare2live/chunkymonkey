#!/usr/bin/env python3
"""Post-paper_sim validation: KPI + lineage_url e2e + baseline compare + leakage check.

读取 mart_paper_sim_kpi + 输出 markdown 报告. 仅 read_only, 不写库.

evidence: 实测 2026-05-20 champion baseline paper_sim 后跑 (criteria #2 90→95%)
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

DB = Path("data/smartmoney.duckdb").resolve()
LINEAGE_DIR = Path("data/reports/lineage").resolve()

BASELINES = [
    ("sizer_ablation_equal_20260517_114503_0a11b0", "sizer_ablation_equal"),
    ("swap_v1_20260516_133642_3b9baa", "swap_v1 best (sharpe 1.42)"),
    ("swap_v1_20260516_131621_6ba40e", "swap_v1 mid"),
    ("baseline_20260517_004955_3da9b3", "baseline (swap off)"),
]

LEAKAGE_GATES = {
    "sharpe_max": 5.0,
    "ann_max": 1.0,
    "win_max": 0.95,
    "rel_uplift_max": 0.50,  # vs swap_v1 best
}

PARETO_TARGETS = {
    "ann_ret_min": 0.30,
    "max_dd_min": -0.20,
    "monthly_win_min": 0.55,
    "excess_vs_hs300_min": 0.0,
}


def fmt_pct(v):
    return "N/A" if v is None else f"{v*100:+.2f}%"


def fmt_num(v):
    return "N/A" if v is None else f"{v:.3f}"


def main():
    if len(sys.argv) < 2:
        print("usage: validate_champion_paper_sim.py <sim_run_id_prefix>", file=sys.stderr)
        return 1
    prefix = sys.argv[1]
    conn = duckdb.connect(str(DB), read_only=True)

    # 1. KPI lookup (latest by built_at where sim_run_id LIKE prefix%)
    row = conn.execute(
        """
        SELECT sim_run_id, variant, period_start, period_end, n_days,
               annual_return, max_dd, sharpe, calmar, monthly_win_rate,
               total_return, excess_vs_hs300, information_ratio,
               user_criteria_pass, anti_churn_pass, robustness_pass, all_kpi_pass,
               avg_holding_days, annual_turnover, swap_count, swap_uplift_total,
               lineage_url, built_at
          FROM mart_paper_sim_kpi
         WHERE sim_run_id LIKE ?
         ORDER BY built_at DESC
         LIMIT 1
        """,
        [prefix + "%"],
    ).fetchone()

    if row is None:
        print(f"\nERROR: no KPI row matching sim_run_id LIKE '{prefix}%'\n")
        return 2

    cols = [d[0] for d in conn.description]
    k = dict(zip(cols, row))

    print()
    print("=" * 100)
    print(f"# Champion baseline paper_sim — KPI 报告")
    print("=" * 100)
    print()
    print(f"## 1. sim_run_id 信息")
    print()
    print(f"- sim_run_id: `{k['sim_run_id']}`")
    print(f"- variant   : `{k['variant']}`")
    print(f"- 期间      : {k['period_start']} → {k['period_end']} ({k['n_days']} 交易日)")
    print(f"- built_at  : {k['built_at']}")
    print()

    # 2. KPI 表
    print("## 2. KPI (champion baseline) vs Pareto target")
    print()
    print("| 指标 | 实测 | Pareto target | 判定 |")
    print("|---|---:|---:|---:|")
    ann_pass = k["annual_return"] is not None and k["annual_return"] >= PARETO_TARGETS["ann_ret_min"]
    dd_pass = k["max_dd"] is not None and k["max_dd"] >= PARETO_TARGETS["max_dd_min"]
    win_pass = k["monthly_win_rate"] is not None and k["monthly_win_rate"] >= PARETO_TARGETS["monthly_win_min"]
    exc_pass = k["excess_vs_hs300"] is not None and k["excess_vs_hs300"] > PARETO_TARGETS["excess_vs_hs300_min"]
    print(f"| 年化      | {fmt_pct(k['annual_return'])} | ≥ +30.00% | {'PASS' if ann_pass else 'FAIL'} |")
    print(f"| max_dd    | {fmt_pct(k['max_dd'])} | ≥ -20.00% | {'PASS' if dd_pass else 'FAIL'} |")
    print(f"| 月胜率    | {fmt_pct(k['monthly_win_rate'])} | ≥ +55.00% | {'PASS' if win_pass else 'FAIL'} |")
    print(f"| 超额 HS300 | {fmt_pct(k['excess_vs_hs300'])} | > 0 | {'PASS' if exc_pass else 'FAIL'} |")
    print(f"| Sharpe    | {fmt_num(k['sharpe'])} | — | — |")
    print(f"| Calmar    | {fmt_num(k['calmar'])} | — | — |")
    print(f"| IR        | {fmt_num(k['information_ratio'])} | — | — |")
    print(f"| 总收益    | {fmt_pct(k['total_return'])} | — | — |")
    print(f"| 平均持仓天 | {fmt_num(k['avg_holding_days'])} | ≥ 5 | — |")
    print(f"| 年化换手  | {fmt_num(k['annual_turnover'])}x | ≤ 8 | — |")
    print(f"| swap 次数 | {k['swap_count']} | — | — |")
    print(f"| swap uplift 总 | {fmt_num(k['swap_uplift_total'])} | > 0 | — |")
    print()
    print(f"3 类阻断: user_criteria={'PASS' if k['user_criteria_pass'] else 'FAIL'} "
          f"anti_churn={'PASS' if k['anti_churn_pass'] else 'FAIL'} "
          f"robustness={'PASS' if k['robustness_pass'] else 'FAIL'} "
          f"→ ALL: {'PASS' if k['all_kpi_pass'] else 'FAIL'}")
    print()

    # 3. baseline 对比
    print("## 3. 对比 4 个 baseline")
    print()
    print("| sim_run_id | ann_ret | max_dd | sharpe | monthly_win | excess_HS300 |")
    print("|---|---:|---:|---:|---:|---:|")
    print(f"| **CHAMPION ({k['sim_run_id']})** | {fmt_pct(k['annual_return'])} | {fmt_pct(k['max_dd'])} "
          f"| {fmt_num(k['sharpe'])} | {fmt_pct(k['monthly_win_rate'])} | {fmt_pct(k['excess_vs_hs300'])} |")
    baseline_swap_v1_best = None
    baseline_ids = [bid for bid, _ in BASELINES]
    baseline_rows = {}
    if baseline_ids:
        placeholders = ", ".join("?" for _ in baseline_ids)
        baseline_rows = {
            row[0]: row[1:]
            for row in conn.execute(
                f"""
                SELECT sim_run_id, annual_return, max_dd, sharpe, monthly_win_rate, excess_vs_hs300
                  FROM mart_paper_sim_kpi
                 WHERE sim_run_id IN ({placeholders})
                """,
                baseline_ids,
            ).fetchall()
        }
    for bid, blabel in BASELINES:
        brow = baseline_rows.get(bid)
        if brow is None:
            print(f"| {blabel} | NA | NA | NA | NA | NA |")
            continue
        if "swap_v1 best" in blabel:
            baseline_swap_v1_best = {
                "ann": brow[0], "dd": brow[1], "sharpe": brow[2],
                "win": brow[3], "exc": brow[4],
            }
        print(f"| {blabel} | {fmt_pct(brow[0])} | {fmt_pct(brow[1])} | {fmt_num(brow[2])} "
              f"| {fmt_pct(brow[3])} | {fmt_pct(brow[4])} |")
    print()

    # 4. leakage 守门
    print("## 4. leakage 守门 (绝对 + 相对 vs swap_v1 best)")
    print()
    warns = []
    if k["sharpe"] is not None and k["sharpe"] > LEAKAGE_GATES["sharpe_max"]:
        warns.append(f"sharpe={k['sharpe']:.3f} > {LEAKAGE_GATES['sharpe_max']}")
    if k["annual_return"] is not None and k["annual_return"] > LEAKAGE_GATES["ann_max"]:
        warns.append(f"ann_ret={k['annual_return']*100:.1f}% > {LEAKAGE_GATES['ann_max']*100:.0f}%")
    if k["monthly_win_rate"] is not None and k["monthly_win_rate"] > LEAKAGE_GATES["win_max"]:
        warns.append(f"monthly_win={k['monthly_win_rate']*100:.1f}% > {LEAKAGE_GATES['win_max']*100:.0f}%")
    if baseline_swap_v1_best and baseline_swap_v1_best["ann"] is not None and k["annual_return"] is not None:
        if baseline_swap_v1_best["ann"] > 0:
            uplift = (k["annual_return"] - baseline_swap_v1_best["ann"]) / abs(baseline_swap_v1_best["ann"])
            print(f"- 相对 swap_v1 best ann uplift: {uplift*100:+.1f}% (阈值 +{LEAKAGE_GATES['rel_uplift_max']*100:.0f}%)")
            if uplift > LEAKAGE_GATES["rel_uplift_max"]:
                warns.append(f"rel ann uplift={uplift*100:.1f}% > {LEAKAGE_GATES['rel_uplift_max']*100:.0f}%")
    if warns:
        print()
        print(f"### WARN — leakage 警报触发:")
        for w in warns:
            print(f"  - {w}")
        print()
        print(f"  → 必跑 PIT audit + ablation")
    else:
        print()
        print("OK — 无 leakage 信号触发 (sharpe / ann / win / 相对 uplift 全在阈值内)")
    print()

    # 5. lineage_url e2e 验证
    print("## 5. lineage_url e2e 验证")
    print()
    lurl = k["lineage_url"]
    if lurl is None:
        print(f"FAIL — lineage_url NULL")
    else:
        print(f"- lineage_url: `{lurl}`")
        if lurl.startswith("file://"):
            lpath = Path(lurl[len("file://"):])
            if lpath.exists():
                size = lpath.stat().st_size
                print(f"- 文件: `{lpath}` ({size:,} bytes)")
                txt = lpath.read_text(encoding="utf-8")
                # 检查关键 lineage 节点
                checks = [
                    ("mart_paper_sim_kpi", "KPI 表"),
                    ("mart_p0b_lambdamart_v6_predictions", "predictions"),
                    ("mart_p0a_feature_label_panel_v4", "panel"),
                    ("fact_feature_panel", "feature fact"),
                ]
                print()
                print(f"### lineage tree 节点完整性")
                print()
                print("| 节点 | 出现 |")
                print("|---|---|")
                for token, label in checks:
                    present = token in txt
                    print(f"| {label} ({token}) | {'YES' if present else 'NO'} |")
                print()
                # 头 30 行预览
                head30 = "\n".join(txt.splitlines()[:30])
                print(f"### lineage markdown 头 30 行预览")
                print()
                print("```")
                print(head30)
                print("```")
            else:
                print(f"FAIL — file 不存在: `{lpath}`")
    print()

    # 6. lineage_url column schema 验证
    print("## 6. mart_paper_sim_kpi schema 验证 lineage_url")
    cols_present = [r[0] for r in conn.execute('DESCRIBE mart_paper_sim_kpi').fetchall()]
    print(f"- lineage_url column present: {'YES' if 'lineage_url' in cols_present else 'NO'}")
    print()

    conn.close()
    return 0 if not warns else 3  # exit 3 = leakage warn


if __name__ == "__main__":
    sys.exit(main())
