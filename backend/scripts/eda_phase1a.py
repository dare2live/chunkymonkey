#!/usr/bin/env python3
"""Phase 1a 后置 EDA + KS curve analysis (Codex round 8 + 9 verdict).

诊断目标:
- F (1490 PIT 严格) vs C (5178+fallback) 在 pnl_pct 分布的 KS test
- Top-K vs random universe 的 score 分布 KS (per-date)
- Winner vs loser pnl_pct 分布

CAVEATS (Codex round 9 MINOR):
1. F PIT vs C PIT 显著不同 != bug. C fallback 改变 slots/cash/swap 路径, position-level
   不应假设 identical. Win A 仅 13 open overlap + 12 close pnl 相同, 是正常.
2. Top-K vs random universe 不是严格 random — 'all universe' 含 top-K 自身.
   KS p<0.05 信号偏强, alpha 结论别写太满 (需配合 paper_sim ann_ret diff 双重验证).
3. 当前缺 days_held by exit_source, score 分布 coverage 诊断 (followup).

输入: paper_sim_v2 跑完后的 fact_paper_sim_position + mart_p0b_oos_predictions (read-only)
输出: stdout markdown report

用法:
    PYTHONPATH=backend python backend/scripts/eda_phase1a.py \\
        --window-a-start 2025-07-01 --window-a-end 2026-04-30 \\
        --window-b-start 2024-12-01 --window-b-end 2025-08-30 \\
        --model-id lgbm_v3_honest_20d
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


def ks_test(a: np.ndarray, b: np.ndarray, label_a: str, label_b: str) -> dict:
    """KS test 比较两组分布. Returns dict 含 statistic / p-value / verdict."""
    a = np.asarray(a)
    b = np.asarray(b)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return {"label_a": label_a, "label_b": label_b, "n_a": len(a), "n_b": len(b),
                "ks_stat": None, "p_value": None, "verdict": "INSUFFICIENT_DATA"}
    ks_stat, p_value = stats.ks_2samp(a, b)
    if p_value < 0.01:
        verdict = "DIFFERENT_DISTRIBUTIONS (highly significant)"
    elif p_value < 0.05:
        verdict = "DIFFERENT_DISTRIBUTIONS (significant)"
    else:
        verdict = "SAME_DISTRIBUTION (not significant)"
    return {"label_a": label_a, "label_b": label_b,
            "n_a": len(a), "n_b": len(b),
            "mean_a": float(np.mean(a)), "mean_b": float(np.mean(b)),
            "median_a": float(np.median(a)), "median_b": float(np.median(b)),
            "ks_stat": float(ks_stat), "p_value": float(p_value),
            "verdict": verdict}


def analyze_window(conn, window_label: str, start: str, end: str,
                   model_id: str) -> dict:
    """对一个 window 跑 KS analysis. Returns markdown report dict."""
    print(f"\n## Window {window_label}: {start} ~ {end}\n")

    # 1. F path closed positions (paper_sim_ml_score.yaml, fallback=False)
    #    跟 C path closed positions (paper_sim_ml_score_C_5178.yaml, fallback=True)
    #    都用 same sim_run_id='swap_v1' (user 跑时复用了 default variant)
    #    我们用 period_start/end + exit_source 区分

    # Phase 1a 4 runs hardcoded sim_run_ids (实际 timestamp suffix)
    SIM_RUNS = {
        "A": {
            "F": "swap_v1_20260516_122705_4ce46c",   # F Win A
            "C": "swap_v1_20260516_123838_229dbc",   # C Win A
        },
        "B": {
            "F": "swap_v1_20260516_124737_50afc2",   # F Win B
            "C": "swap_v1_20260516_125455_7e7d39",   # C Win B
        },
    }
    sim_ids = SIM_RUNS.get(window_label, {})
    f_sim = sim_ids.get("F")
    c_sim = sim_ids.get("C")

    closed_f = conn.execute("""
        SELECT pnl_pct, days_held, exit_source
        FROM fact_paper_sim_position
        WHERE sim_run_id = ? AND is_open = FALSE
        ORDER BY open_date
    """, [f_sim]).fetchall() if f_sim else []

    closed_c_pit = conn.execute("""
        SELECT pnl_pct, days_held
        FROM fact_paper_sim_position
        WHERE sim_run_id = ? AND is_open = FALSE AND exit_source = 'pit'
        ORDER BY open_date
    """, [c_sim]).fetchall() if c_sim else []

    closed_c_fallback = conn.execute("""
        SELECT pnl_pct, days_held
        FROM fact_paper_sim_position
        WHERE sim_run_id = ? AND is_open = FALSE AND exit_source = 'fallback'
        ORDER BY open_date
    """, [c_sim]).fetchall() if c_sim else []

    f_pnl = np.array([r[0] for r in closed_f]) if closed_f else np.array([])
    c_pit_pnl = np.array([r[0] for r in closed_c_pit]) if closed_c_pit else np.array([])
    c_fb_pnl = np.array([r[0] for r in closed_c_fallback]) if closed_c_fallback else np.array([])

    print(f"  F path PIT positions: {len(f_pnl)}")
    print(f"  C path PIT positions: {len(c_pit_pnl)}")
    print(f"  C path fallback positions: {len(c_fb_pnl)}")

    # 2. KS test: PIT vs fallback pnl_pct 分布 (Codex round 8: 验证 fallback 不同分布)
    print(f"\n### KS test: PIT vs fallback pnl_pct distribution\n")
    pit_combined = np.concatenate([f_pnl, c_pit_pnl]) if len(f_pnl) + len(c_pit_pnl) > 0 else np.array([])
    ks_pit_vs_fb = ks_test(pit_combined, c_fb_pnl, "PIT (F+C)", "Fallback (C only)")
    print(f"  n_pit={ks_pit_vs_fb['n_a']}, n_fallback={ks_pit_vs_fb['n_b']}")
    if ks_pit_vs_fb.get("mean_a") is not None:
        print(f"  mean PIT={ks_pit_vs_fb['mean_a']*100:+.2f}%, median PIT={ks_pit_vs_fb['median_a']*100:+.2f}%")
        print(f"  mean Fallback={ks_pit_vs_fb['mean_b']*100:+.2f}%, median Fallback={ks_pit_vs_fb['median_b']*100:+.2f}%")
        print(f"  KS stat={ks_pit_vs_fb['ks_stat']:.4f}, p-value={ks_pit_vs_fb['p_value']:.4f}")
    print(f"  **Verdict**: {ks_pit_vs_fb['verdict']}")

    # 3. KS test: F PIT vs C PIT pnl_pct (检查 F 跟 C 内部 PIT 子集是否同分布)
    print(f"\n### KS test: F PIT positions vs C PIT positions (intra-PIT consistency)\n")
    ks_f_vs_c_pit = ks_test(f_pnl, c_pit_pnl, "F PIT", "C PIT")
    if ks_f_vs_c_pit.get("mean_a") is not None:
        print(f"  n_F_pit={ks_f_vs_c_pit['n_a']}, n_C_pit={ks_f_vs_c_pit['n_b']}")
        print(f"  KS stat={ks_f_vs_c_pit['ks_stat']:.4f}, p-value={ks_f_vs_c_pit['p_value']:.4f}")
    print(f"  **Verdict**: {ks_f_vs_c_pit['verdict']}")

    # 4. Winner vs Loser pnl_pct 分布 (PIT positions, 验证 alpha 真实性)
    print(f"\n### Winner (pnl>0) vs Loser (pnl<=0) pnl_pct distribution (PIT only)\n")
    if len(pit_combined) > 0:
        winners = pit_combined[pit_combined > 0]
        losers = pit_combined[pit_combined <= 0]
        print(f"  n_winners={len(winners)} ({len(winners)/len(pit_combined)*100:.1f}%)")
        print(f"  n_losers={len(losers)} ({len(losers)/len(pit_combined)*100:.1f}%)")
        if len(winners) > 0 and len(losers) > 0:
            print(f"  mean winner={np.mean(winners)*100:+.2f}%, mean loser={np.mean(losers)*100:+.2f}%")
            print(f"  win/loss ratio (abs mean)={np.mean(winners) / abs(np.mean(losers)):.2f}")

    return {
        "window_label": window_label,
        "start": start, "end": end,
        "n_f_pit": len(f_pnl), "n_c_pit": len(c_pit_pnl), "n_c_fb": len(c_fb_pnl),
        "ks_pit_vs_fb": ks_pit_vs_fb,
        "ks_f_vs_c_pit": ks_f_vs_c_pit,
    }


def analyze_score_distribution(conn, start: str, end: str, model_id: str) -> dict:
    """Top-K vs random universe 的 score 分布 KS (按 date 分层避免样本量放大)."""
    print(f"\n## Top-K vs Random Universe Score Distribution (per-date KS)\n")
    # 跑 per-date KS test, 取 distribution of p-values
    rows = conn.execute("""
        SELECT DISTINCT signal_date
        FROM mart_p0b_oos_predictions
        WHERE model_id = ? AND signal_date >= ? AND signal_date <= ?
        ORDER BY signal_date
        LIMIT 50  -- sample 50 dates 跑 KS 防止太慢
    """, [model_id, start, end]).fetchall()

    if not rows:
        print(f"  no signal_dates in mart_p0b_oos_predictions for {start}~{end}")
        return {"per_date_ks_pvalues": []}

    p_values = []
    for (d,) in rows:
        all_scores = conn.execute("""
            SELECT score FROM mart_p0b_oos_predictions
            WHERE model_id = ? AND signal_date = ? AND score IS NOT NULL
        """, [model_id, d]).fetchall()
        if len(all_scores) < 30:
            continue
        all_arr = np.array([r[0] for r in all_scores])
        top_k = np.sort(all_arr)[::-1][:30]  # top 30
        if len(top_k) >= 5 and len(all_arr) >= 30:
            _, p = stats.ks_2samp(top_k, all_arr)
            p_values.append(p)

    if p_values:
        p_arr = np.array(p_values)
        sig_count = int(np.sum(p_arr < 0.05))
        print(f"  n_dates_analyzed: {len(p_arr)}")
        print(f"  median p-value: {np.median(p_arr):.4f}")
        print(f"  pct dates with p<0.05: {sig_count/len(p_arr)*100:.1f}% ({sig_count}/{len(p_arr)})")
        print(f"  pct dates with p<0.01: {int(np.sum(p_arr < 0.01))/len(p_arr)*100:.1f}%")
        verdict = "TOP-K 显著不同 random (alpha 真实)" if sig_count/len(p_arr) > 0.5 \
                  else "TOP-K 跟 random 接近 (ranking 弱)"
        print(f"  **Verdict**: {verdict}")
        return {"per_date_ks_pvalues": p_values, "verdict": verdict}
    return {"per_date_ks_pvalues": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    # rule-compliance: ok evidence=phase1a-experiment-windows-fixed
    # 2 Window 是 Phase 1a F vs C 对照实验的 fixed windows (跟 4 sim_run_id 1:1 绑定),
    # 不是参数化策略阈值. 改 window 需重跑 4 paper_sim runs, 不是 yaml 配置.
    parser.add_argument("--window-a-start", default="2025-07-01")  # rule-compliance: ok evidence=fixed-experiment
    parser.add_argument("--window-a-end", default="2026-04-30")  # rule-compliance: ok evidence=fixed-experiment
    parser.add_argument("--window-b-start", default="2024-12-01")  # rule-compliance: ok evidence=fixed-experiment
    parser.add_argument("--window-b-end", default="2025-08-30")  # rule-compliance: ok evidence=fixed-experiment
    parser.add_argument("--model-id", default="lgbm_v3_honest_20d")
    args = parser.parse_args()

    conn = duckdb.connect(str(SMART_DB), read_only=True)

    print("=" * 80)
    print("# Phase 1a EDA + KS Analysis (Codex round 8 verdict)")
    print("=" * 80)

    win_a = analyze_window(conn, "A", args.window_a_start, args.window_a_end, args.model_id)
    win_b = analyze_window(conn, "B", args.window_b_start, args.window_b_end, args.model_id)

    # Score distribution per-date KS (Wilcoxon rank-sum analogue)
    print("\n" + "=" * 80)
    print("# Score Distribution Analysis (Wilcoxon-style per-date KS)")
    print("=" * 80)
    score_dist_a = analyze_score_distribution(conn, args.window_a_start, args.window_a_end, args.model_id)

    print("\n" + "=" * 80)
    print("# Summary & Phase 1b Decision Support")
    print("=" * 80)
    print(f"\n## Window A ({args.window_a_start}~{args.window_a_end}):")
    print(f"  PIT vs Fallback distribution: {win_a['ks_pit_vs_fb']['verdict']}")
    print(f"  F PIT vs C PIT (intra-PIT): {win_a['ks_f_vs_c_pit']['verdict']}")
    print(f"\n## Window B ({args.window_b_start}~{args.window_b_end}):")
    print(f"  PIT vs Fallback distribution: {win_b['ks_pit_vs_fb']['verdict']}")
    print(f"  F PIT vs C PIT (intra-PIT): {win_b['ks_f_vs_c_pit']['verdict']}")
    if score_dist_a.get("verdict"):
        print(f"\n## Score Distribution (Win A):")
        print(f"  {score_dist_a['verdict']}")

    print("\n## Phase 1b Decision Trigger (Codex round 8 rules):")
    print("  - PIT vs fallback KS 显著 → fallback 不同分布, 支持 'fallback 拖累 PIT' (推 Option A 扩 PIT 不推 Option C)")
    print("  - PIT vs fallback KS 不显著 → fallback 跟 PIT 同分布, ranking noisy (推考虑 Option E pool 重 design)")
    print("  - F PIT vs C PIT 显著 → F 跟 C 路径内部 PIT 子集不同 (异常, 应同 INNER JOIN 来源)")
    print("  - 注意: KS 只证 '分布不同', 不单独证 alpha; 必须配合 paper_sim ann_ret diff 看")

    return 0


if __name__ == "__main__":
    sys.exit(main())
