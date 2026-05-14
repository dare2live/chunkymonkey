#!/usr/bin/env python3
"""P0b + P1 ablation final report — 跨 horizon + feature group 综合报告.

读取 mart_p0b_walkforward_eval (各 horizon runs) + mart_p1_ablation_result
(feature group ablation) → 输出 markdown 表 + 推荐结论.

PLAN_V3 §3 数据决定的决策点 #2 #3 #5 综合输出.

用法:
    PYTHONPATH=backend python backend/scripts/p0b_final_report.py
    PYTHONPATH=backend python backend/scripts/p0b_final_report.py --output report.md
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_final_report")


def _horizon_summary(conn) -> list[dict]:
    """从 mart_p0b_walkforward_eval 聚合各 model_id (一 horizon 一 model_id) 的 RankIC."""
    cur = conn.execute("""
        SELECT model_id,
               COUNT(*) AS n_windows,
               AVG(rank_ic) AS rank_ic_mean,
               STDDEV(rank_ic) AS rank_ic_std,
               AVG(rank_ic_ir) AS ic_ir_mean,
               SUM(n_test) AS total_test_rows,
               MIN(test_start) AS first_test,
               MAX(test_end) AS last_test
        FROM mart_p0b_walkforward_eval
        GROUP BY model_id
        ORDER BY model_id
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ablation_summary(conn) -> list[dict]:
    """从 mart_p1_ablation_result 拿最近 run_id 的 summary."""
    try:
        cur = conn.execute("""
            WITH latest AS (
                SELECT MAX(run_id) AS run_id FROM mart_p1_ablation_result
            )
            SELECT a.experiment_name, a.n_features, a.n_windows,
                   a.rank_ic, a.rank_ic_ir, a.delta_vs_baseline,
                   a.label_field, a.n_estimators
            FROM mart_p1_ablation_result a
            JOIN latest l ON l.run_id = a.run_id
            ORDER BY a.rank_ic DESC NULLS LAST
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"No P1 ablation result yet: {e}")
        return []


def format_report(horizon_results, ablation_results) -> str:
    """Markdown report."""
    lines = ["# P0b + P1 Ablation Final Report", "",
             "## Phase v3.2 — PLAN_V3 §99 决策点 #2/#3/#5 综合", "",
             "**Acceptance gate**: stitched OOS RankIC ≥ 0.03 AND n_dates ≥ 30", "",
             "## A. Horizon Ablation (mart_p0b_walkforward_eval)", ""]
    if horizon_results:
        lines += ["| model_id | n_windows | RankIC mean | RankIC std | IC IR | n_test | Gate |",
                  "|---|---:|---:|---:|---:|---:|:---:|"]
        for r in horizon_results:
            ric = r.get("rank_ic_mean") or 0
            gate = "PASS" if ric >= 0.03 else "FAIL"
            lines.append(
                f"| {r['model_id']} | {r['n_windows']} | "
                f"{ric:.4f} | {r.get('rank_ic_std') or 0:.4f} | "
                f"{r.get('ic_ir_mean') or 0:.4f} | {r.get('total_test_rows') or 0:,} | {gate} |"
            )
    else:
        lines.append("(No horizon eval rows in DB)")

    lines += ["", "## B. Feature Group Ablation (mart_p1_ablation_result, 最近 run)", ""]
    if ablation_results:
        lines += ["| experiment | n_features | n_windows | RankIC | IC IR | Δ baseline |",
                  "|---|---:|---:|---:|---:|---:|"]
        for r in ablation_results:
            ric = r.get("rank_ic") or 0
            delta = r.get("delta_vs_baseline") or 0
            sign = "+" if delta >= 0 else ""
            lines.append(
                f"| {r['experiment_name']} | {r['n_features']} | {r['n_windows']} | "
                f"{ric:.4f} | {r.get('rank_ic_ir') or 0:.4f} | {sign}{delta:.4f} |"
            )
    else:
        lines.append("(No ablation rows yet — run scripts/run_p1_ablation.py)")

    # Verdict
    lines += ["", "## Verdict", ""]
    best_horizon = max(horizon_results, key=lambda r: r.get("rank_ic_mean") or -1, default=None)
    if best_horizon:
        ric = best_horizon.get("rank_ic_mean") or 0
        lines.append(f"- **Best horizon**: `{best_horizon['model_id']}` RankIC={ric:.4f}")
        if ric >= 0.03:
            lines.append(f"  → **Gate PASS** ✓, 可进 P0c selector refactor")
        else:
            lines.append(f"  → **Gate FAIL** ✗, RankIC < 0.03 阈值")
            lines.append(f"  → 必须扩特征 (PLAN_V3 §3 #3 机构路径 A/B / #4 公式触发 / #9 行业中性)")

    if ablation_results:
        baseline = next((r for r in ablation_results if r["experiment_name"].startswith("baseline")), None)
        best_drop = max((r for r in ablation_results if r["experiment_name"].startswith("drop_")),
                        key=lambda r: r.get("rank_ic") or -1, default=None)
        best_only = max((r for r in ablation_results if r["experiment_name"].startswith("only_")),
                        key=lambda r: r.get("rank_ic") or -1, default=None)
        if baseline:
            lines.append(f"- **Baseline (全 feature)**: RankIC={baseline.get('rank_ic') or 0:.4f}")
        if best_drop:
            lines.append(f"- **Best drop**: `{best_drop['experiment_name']}` "
                         f"RankIC={best_drop.get('rank_ic') or 0:.4f} "
                         f"(Δ={best_drop.get('delta_vs_baseline') or 0:+.4f})")
        if best_only:
            lines.append(f"- **Best single group (only-one)**: `{best_only['experiment_name']}` "
                         f"RankIC={best_only.get('rank_ic') or 0:.4f}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None, help="写 markdown 文件 (default stdout)")
    args = parser.parse_args()

    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        horizon = _horizon_summary(conn)
        ablation = _ablation_summary(conn)
    finally:
        conn.close()

    report = format_report(horizon, ablation)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        log.info(f"Report written to {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
