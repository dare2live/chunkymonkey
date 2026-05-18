#!/usr/bin/env python3
"""Model Monitor Dashboard — 模型 + 策略全指标监控台 (用户 push back 2026-05-18).

用户原话: 'gcp 设计成手工触发吧, 系统里做好相关指标监控, 做个模型监控台之类的功能'

展示:
1. 当前 champion model_id + KPI (P3 ann/max_dd/月胜 / MSAF median/sharpe/hit)
2. 最近 8 个 OOS windows rank_ic 时序 (alpha decay 检测)
3. 模型 retrain 频率 + 上次 retrain 日期 + 下次建议 retrain (event-driven + quarterly)
4. Top-K daily portfolio (latest signal_date)
5. Stale data sources (SLA alert)
6. GCP 月度 cost burn + budget %
7. 交付标准 6 项 % audit

Usage:
    PYTHONPATH=backend python backend/scripts/model_monitor_dashboard.py
    PYTHONPATH=backend python backend/scripts/model_monitor_dashboard.py --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, date, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("model_monitor")


def get_current_champion(con) -> dict:
    """当前 champion model + 注册时间."""
    try:
        r = con.execute("""
            SELECT champion_id, model_id, promoted_at, reason
            FROM mart_champion_registry
            WHERE is_current = TRUE
            ORDER BY promoted_at DESC LIMIT 1
        """).fetchone()
        if r:
            return {"champion_id": r[0], "model_id": r[1], "promoted_at": str(r[2]), "reason": r[3]}
    except Exception as e:
        return {"error": str(e)}
    return {}


def get_latest_p3(con) -> dict:
    """最新 P3 acceptance verdict."""
    try:
        r = con.execute("""
            SELECT run_id, model_id, ann_ret, max_dd, monthly_win_rate, passed, built_at
            FROM mart_p3_acceptance_result
            WHERE ann_ret > 0
            ORDER BY built_at DESC LIMIT 1
        """).fetchone()
        if r:
            return {
                "run_id": r[0], "model_id": r[1],
                "ann_ret": round(r[2] * 100, 2), "max_dd": round(r[3] * 100, 2),
                "monthly_win_rate": round(r[4] * 100, 2), "passed": r[5],
                "built_at": r[6],
            }
    except Exception as e:
        return {"error": str(e)}
    return {}


def get_rank_ic_trend(con) -> dict:
    """最近 8 windows rank_ic — alpha decay 检测."""
    try:
        rows = con.execute("""
            SELECT test_start, rank_ic
            FROM mart_p0b_walkforward_eval
            WHERE rank_ic IS NOT NULL
            ORDER BY test_start DESC LIMIT 8
        """).fetchall()
        if not rows:
            return {}
        rows = list(reversed(rows))  # oldest → newest
        ics = [r[1] for r in rows]
        # Alpha decay: 最近 4 个连降?
        decay = False
        if len(ics) >= 4:
            recent = ics[-4:]
            decay = all(recent[i] > recent[i+1] for i in range(3))
        return {
            "n_windows": len(rows),
            "windows": [(str(r[0])[:10], round(r[1], 4)) for r in rows],
            "mean_ic": round(sum(ics) / len(ics), 4),
            "min_ic": round(min(ics), 4),
            "max_ic": round(max(ics), 4),
            "latest_ic": round(ics[-1], 4),
            "alpha_decay": "DECAY (4 连降)" if decay else "STABLE",
        }
    except Exception as e:
        return {"error": str(e)}


def get_retrain_recommendation(con) -> dict:
    """retrain 建议 (event-driven + quarterly fallback)."""
    today = date.today()
    is_quarter_start = today.day == 1 and today.month in (1, 4, 7, 10)
    trend = get_rank_ic_trend(con)
    alpha_decay = "DECAY" in str(trend.get("alpha_decay", ""))
    if alpha_decay:
        return {"trigger": "EVENT_DRIVEN", "reason": "alpha decay (rank_ic 4 连降)",
                "urgency": "HIGH", "next_check": "now"}
    if is_quarter_start:
        next_q = {1: 4, 4: 7, 7: 10, 10: 1}.get(today.month, 1)
        return {"trigger": "QUARTERLY", "reason": f"Q{(today.month-1)//3+1} 季初",
                "urgency": "MEDIUM", "next_check": f"{today.year}-{next_q:02d}-01"}
    days_to_quarter = sum(1 for d in [(4, 1), (7, 1), (10, 1), (1, 1)]
                          if (d[0], d[1]) > (today.month, today.day) or (today.month >= 10 and d[0] == 1))
    return {"trigger": "CACHED", "reason": "alpha stable + 非季初",
            "urgency": "LOW", "next_quarter": "估 ~3 month"}


def get_latest_top_k(con, n: int = 5) -> dict:
    """最新 signal_date top-K portfolio."""
    try:
        r = con.execute("""
            SELECT MAX(signal_date) FROM mart_p0b_oos_predictions
        """).fetchone()
        latest_sd = r[0] if r else None
        if not latest_sd:
            return {}
        rows = con.execute("""
            SELECT stock_code, score
            FROM mart_p0b_oos_predictions
            WHERE signal_date = ?
            ORDER BY score DESC LIMIT ?
        """, [latest_sd, n]).fetchall()
        return {
            "signal_date": str(latest_sd)[:10],
            "top_k": [(row[0], round(row[1], 4)) for row in rows],
        }
    except Exception as e:
        return {"error": str(e)}


def get_stale_sources(audit_path: Path) -> dict:
    """SLA stale source alerts (从 watermark audit)."""
    if not audit_path.exists():
        return {"error": "watermark_sla_latest.json 不存在"}
    d = json.loads(audit_path.read_text())
    stale = [s for s in d.get("sources", []) if s.get("alert")]
    return {
        "n_alerts": d.get("n_alerts", len(stale)),
        "stale_sources": [s.get("data_domain") for s in stale],
    }


def get_gcp_cost(report_path: Path) -> dict:
    """GCP 月度 cost burn + budget."""
    if not report_path.exists():
        return {"error": "gcp_cost_summary.json 不存在, 跑 bash gcp/cost_tracker.sh"}
    d = json.loads(report_path.read_text())
    return {
        "vm_status": d.get("vm_status"),
        "alert_level": d.get("alert_level"),
        "pct_of_budget": d.get("pct_of_budget"),
        "projected_month": d.get("projected_month_cost"),
        "remaining_hours_at_spot": d.get("remaining_hours_at_spot"),
    }


def get_delivery_readiness(report_path: Path) -> dict:
    """交付准备度 (audit_delivery_readiness.py 输出)."""
    if not report_path.exists():
        return {"error": "delivery_readiness.json 不存在, 跑 audit_delivery_readiness.py"}
    d = json.loads(report_path.read_text())
    return {
        "avg_pct": round(d.get("avg_pct", 0), 1),
        "ready": d.get("ready_for_delivery", False),
        "criteria": [(c["criterion"], c["pct"], c["verdict"]) for c in d.get("criteria", [])],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Model Monitor Dashboard")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(args.smartmoney_db, read_only=True)
    try:
        dashboard = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "champion": get_current_champion(con),
            "p3_latest": get_latest_p3(con),
            "rank_ic_trend": get_rank_ic_trend(con),
            "retrain_recommendation": get_retrain_recommendation(con),
            "latest_top_k": get_latest_top_k(con),
            "stale_sources": get_stale_sources(REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"),
            "gcp_cost": get_gcp_cost(REPO_ROOT / "data" / "reports" / "gcp_cost_summary.json"),
            "delivery_readiness": get_delivery_readiness(REPO_ROOT / "data" / "reports" / "delivery_readiness.json"),
        }
    finally:
        con.close()

    if args.json:
        print(json.dumps(dashboard, indent=2, ensure_ascii=False, default=str))
    else:
        log.info("=" * 70)
        log.info(f"ChunkyMonkey Model Monitor Dashboard @ {dashboard['ts']}")
        log.info("=" * 70)
        log.info("")
        log.info("--- Champion (current) ---")
        for k, v in dashboard["champion"].items():
            log.info(f"  {k}: {v}")
        log.info("")
        log.info("--- Latest P3 acceptance verdict ---")
        for k, v in dashboard["p3_latest"].items():
            log.info(f"  {k}: {v}")
        log.info("")
        log.info("--- RankIC Trend (alpha decay 检测) ---")
        rt = dashboard["rank_ic_trend"]
        log.info(f"  windows: {rt.get('n_windows')}, latest IC: {rt.get('latest_ic')}, mean IC: {rt.get('mean_ic')}")
        log.info(f"  Status: {rt.get('alpha_decay')}")
        for w in rt.get("windows", [])[-4:]:
            log.info(f"    {w[0]}: rank_ic={w[1]:+.4f}")
        log.info("")
        log.info("--- Retrain Recommendation ---")
        rr = dashboard["retrain_recommendation"]
        log.info(f"  trigger: {rr.get('trigger')}")
        log.info(f"  urgency: {rr.get('urgency')}")
        log.info(f"  reason: {rr.get('reason')}")
        log.info("")
        log.info("--- Latest Top-K Portfolio ---")
        tk = dashboard["latest_top_k"]
        log.info(f"  signal_date: {tk.get('signal_date')}")
        for stock, score in tk.get("top_k", []):
            log.info(f"    {stock} score={score}")
        log.info("")
        log.info("--- Stale Data Sources ---")
        ss = dashboard["stale_sources"]
        log.info(f"  n_alerts: {ss.get('n_alerts')}")
        if ss.get("stale_sources"):
            for s in ss["stale_sources"]:
                log.info(f"    {s}")
        log.info("")
        log.info("--- GCP Cost ---")
        gc = dashboard["gcp_cost"]
        log.info(f"  vm_status: {gc.get('vm_status')}")
        log.info(f"  alert: {gc.get('alert_level')}")
        log.info(f"  pct_of_budget: {gc.get('pct_of_budget')}%")
        log.info(f"  projected_month: ${gc.get('projected_month')}")
        log.info(f"  remaining_hours_at_spot: {gc.get('remaining_hours_at_spot')}h")
        log.info("")
        log.info("--- Delivery Readiness ---")
        dr = dashboard["delivery_readiness"]
        log.info(f"  avg_pct: {dr.get('avg_pct')}%")
        log.info(f"  ready: {dr.get('ready')}")
        for c in dr.get("criteria", []):
            log.info(f"    {c[0]:<24} {c[1]}% {c[2]}")
        log.info("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
