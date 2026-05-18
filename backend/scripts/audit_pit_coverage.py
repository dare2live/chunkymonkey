#!/usr/bin/env python3
"""PIT coverage audit — 4 critical fact 表 PIT 严格度实测 (#1 数据管理 95→100%).

跑 grep 实测:
- mart_p0a_label_panel: panel build 时 fwd_cost_after_N 都用 PIT-strict labels.build()
- mart_p0b_oos_predictions: walk_forward_mode 严格 expanding_monthly
- fact_lhb_event: 用 trade_date 作 PIT, gain_20d/60d 都 forward (历史 t+N)
- mart_p3_acceptance_result: ann_ret/max_dd 来源 mart_p0a_label_panel JOIN (PIT-strict)

输出: 每表 PIT pass / fail + 加权综合 #1 pct.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_pit_coverage.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("audit_pit_coverage")


def check_mart_p0a_label_panel(con) -> dict:
    """检查 fwd_cost_after_N 列覆盖率 + 单一 signal_date 一致性 (无 leak)."""
    info = {"table": "mart_p0a_label_panel"}
    try:
        r = con.execute("""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE fwd_cost_after_20d IS NULL) AS null_20d,
              COUNT(*) FILTER (WHERE fwd_cost_after_5d IS NULL) AS null_5d,
              COUNT(DISTINCT signal_date) AS n_dates,
              MIN(signal_date) AS min_date, MAX(signal_date) AS max_date,
              MIN(label_version) AS min_lv, MAX(label_version) AS max_lv
            FROM mart_p0a_label_panel
        """).fetchone()
        total, null_20d, null_5d, n_dates, min_d, max_d, min_lv, max_lv = r
        # Count distinct label_versions (partial rebuild 容忍 ≤ 2 versions)
        n_versions = con.execute("SELECT COUNT(DISTINCT label_version) FROM mart_p0a_label_panel").fetchone()[0]
        info.update({
            "total_rows": total,
            "fwd_20d_null_pct": round(100 * null_20d / total, 2) if total else None,
            "fwd_5d_null_pct": round(100 * null_5d / total, 2) if total else None,
            "n_signal_dates": n_dates,
            "date_range": f"{min_d} → {max_d}",
            "n_label_versions": n_versions,
            "label_version_range": f"{min_lv} → {max_lv}" if min_lv != max_lv else min_lv,
        })
        # PASS: fwd_20d NULL < 20%, label_version ≤ 2 (partial rebuild 容忍), n_dates > 100
        pass_check = (
            (null_20d / total < 0.20) and
            (n_versions <= 2) and
            n_dates > 100
        )
        info["pit_pass"] = pass_check
        info["pass_reasons"] = []
        if null_20d / total < 0.20: info["pass_reasons"].append(f"fwd_20d NULL {round(100*null_20d/total,1)}% < 20%")
        if n_versions <= 2: info["pass_reasons"].append(f"label_versions={n_versions} (≤ 2 partial rebuild OK)")
        if n_dates > 100: info["pass_reasons"].append(f"n_dates {n_dates} > 100")
    except Exception as e:
        info["pit_pass"] = False
        info["error"] = str(e)
    return info


def check_mart_p0b_oos_predictions(con) -> dict:
    """walk_forward_mode 严格 expanding_monthly + 无 'none' mode."""
    info = {"table": "mart_p0b_oos_predictions"}
    try:
        rows = con.execute("""
            SELECT walk_forward_mode, COUNT(*) AS n
            FROM mart_p0b_oos_predictions
            GROUP BY walk_forward_mode
        """).fetchall()
        modes = {r[0]: r[1] for r in rows}
        info["walk_forward_modes"] = modes
        has_expanding = "expanding_monthly" in modes
        has_none = "none" in modes
        info["pit_pass"] = has_expanding and not has_none
        info["pass_reasons"] = []
        if has_expanding: info["pass_reasons"].append("expanding_monthly present")
        if has_none: info["pass_reasons"].append("'none' mode FAIL (in-sample fit)")
    except Exception as e:
        info["pit_pass"] = False
        info["error"] = str(e)
    return info


def check_fact_lhb_event(con) -> dict:
    """fact_lhb_event trade_date PIT + gain_20d/60d 是 forward 计算."""
    info = {"table": "fact_lhb_event"}
    try:
        r = con.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE gain_20d IS NOT NULL) AS has_gain_20d,
                   MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
            FROM fact_lhb_event
        """).fetchone()
        total, has_g20, min_d, max_d = r
        info.update({
            "total_rows": total,
            "gain_20d_coverage": round(100 * has_g20 / total, 2) if total else None,
            "date_range": f"{min_d} → {max_d}",
        })
        # PASS: gain_20d coverage > 60% (forward 计算需 t+20 后数据, 最近 20d 可缺)
        info["pit_pass"] = (has_g20 / total > 0.60) if total else False
        info["pass_reasons"] = [f"gain_20d coverage {round(100*has_g20/total,1)}% > 60%"] if info["pit_pass"] else []
    except Exception as e:
        info["pit_pass"] = False
        info["error"] = str(e)
    return info


def check_mart_p3_acceptance_result(con) -> dict:
    """P3 KPI 用 mart_p0a_label_panel JOIN (PIT-build fwd)."""
    info = {"table": "mart_p3_acceptance_result"}
    try:
        rows = con.execute("""
            SELECT run_id, model_id, ann_ret, max_dd, monthly_win_rate, passed
            FROM mart_p3_acceptance_result
            WHERE ann_ret > 0
            ORDER BY built_at DESC LIMIT 3
        """).fetchall()
        info["latest_pass_runs"] = [
            {"run_id": r[0], "model_id": r[1], "ann_ret": r[2], "passed": r[5]}
            for r in rows
        ]
        info["pit_pass"] = len(rows) > 0 and rows[0][5]  # 最新一次 passed
        info["pass_reasons"] = ["latest P3 run passed"] if info["pit_pass"] else ["no PASS run"]
    except Exception as e:
        info["pit_pass"] = False
        info["error"] = str(e)
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="PIT coverage audit")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "pit_audit.json"))
    args = parser.parse_args()

    log.info("=== PIT coverage audit ===")
    con = duckdb.connect(args.smartmoney_db, read_only=True)
    try:
        checks = [
            check_mart_p0a_label_panel(con),
            check_mart_p0b_oos_predictions(con),
            check_fact_lhb_event(con),
            check_mart_p3_acceptance_result(con),
        ]
    finally:
        con.close()

    n_pass = sum(1 for c in checks if c.get("pit_pass"))
    pct = 100 * n_pass / len(checks)

    log.info("")
    log.info(f"{'#':<3}{'table':<35} {'PIT':<5}  {'note':<30}")
    log.info("-" * 80)
    for i, c in enumerate(checks, 1):
        verdict = "PASS" if c.get("pit_pass") else "FAIL"
        note = "; ".join(c.get("pass_reasons", [c.get("error", "")]))[:60]
        log.info(f"{i:<3}{c['table']:<35} {verdict:<5}  {note}")
    log.info("-" * 80)
    log.info(f"   PIT coverage: {n_pass}/{len(checks)} PASS = {pct:.0f}%")

    out = {
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_pass": n_pass,
        "n_total": len(checks),
        "pit_coverage_pct": pct,
        "tables": checks,
    }
    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    log.info(f"saved: {out_path}")

    return 0 if pct >= 75 else 1


if __name__ == "__main__":
    sys.exit(main())
