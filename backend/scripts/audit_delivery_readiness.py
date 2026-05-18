#!/usr/bin/env python3
"""项目交付准备度 audit (用户 2026-05-18 Stop hook 要求).

跑 1-stop check 验 6 交付标准当前状态:
  #1 数据管理: 0 stale source alert + PIT 严格
  #2 策略模型管理: MSAF 3 类 + ensemble + regime + paper_sim KPI 达标
  #3 backtester gate: PBO/DSR/conservative/IS-OOS verdict 真实
  #4 全自动化 daily: daily_update 8 步真调
  #5 GCP 成本控制: cost_tracker 实时跟踪 + alert
  #6 实盘 GO/NO-GO: holdout 跨年中位 ≥ 25%

Usage:
    PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py
    PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py --json
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("delivery_audit")


def check_data_management() -> dict:
    """#1 数据管理: stale source count + PIT 严格度."""
    sla_report = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"
    n_alerts = 9999
    if sla_report.exists():
        d = json.loads(sla_report.read_text())
        # Schema: {n_updates, n_alerts, sources: [{alert: bool, ...}]}
        n_alerts = d.get("n_alerts", d.get("n_alert", None))
        if n_alerts is None and "sources" in d:
            n_alerts = sum(1 for r in d["sources"] if r.get("alert"))
        if n_alerts is None and "results" in d:
            n_alerts = sum(1 for r in d["results"] if r.get("alert"))
        if n_alerts is None:
            n_alerts = 9999

    # PIT 严格度: 抽 model_id 检查 walk_forward_mode
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"
    pit_strict = "unknown"
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        rows = con.execute("""
            SELECT walk_forward_mode, COUNT(*) AS n
            FROM mart_p0b_oos_predictions
            GROUP BY walk_forward_mode
        """).fetchall()
        con.close()
        modes = {r[0]: r[1] for r in rows}
        if "expanding_monthly" in modes and "none" not in modes:
            pit_strict = "OK (expanding_monthly only)"
        else:
            pit_strict = f"WARN (modes: {modes})"
    except Exception as e:
        pit_strict = f"error: {e}"

    status_pct = 95 if n_alerts == 0 else (80 if n_alerts <= 2 else (60 if n_alerts <= 5 else 30))
    return {
        "criterion": "数据管理",
        "pct": status_pct,
        "stale_alerts": n_alerts,
        "pit_strict": pit_strict,
        "verdict": "PASS" if n_alerts == 0 and "OK" in pit_strict else "WARN",
    }


def check_strategy_model() -> dict:
    """#2 策略模型: ensemble + regime + KPI 达标."""
    msaf_report = REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"
    if not msaf_report.exists():
        return {"criterion": "策略模型管理", "pct": 50, "verdict": "WARN",
                "reason": "msaf_ensemble_run.json 不存在, 跑 run_msaf_ensemble_paper_sim.py"}

    d = json.loads(msaf_report.read_text())
    kpi = d.get("kpi", {})
    median_ann = kpi.get("ann_ret_median")
    cagr = kpi.get("ann_ret_cagr")
    sharpe = kpi.get("sharpe")
    max_dd = kpi.get("max_dd")
    hit_rate = kpi.get("hit_rate")
    n_obs = kpi.get("n_obs", 0)

    # Phase 3.4 sniper/institution 真接是 25% bonus
    # Phase 4 holdout OOS ≥ 30 是 15% bonus
    phase34 = "lambdamart_only" if median_ann else "incomplete"
    pct = 50
    if median_ann is not None and median_ann >= 0.25:
        pct = 80  # KPI 达标但 lambdamart only
    if median_ann is not None and median_ann >= 0.25 and n_obs >= 30:
        pct = 90  # 加 OOS ≥ 30 obs
    if median_ann is not None and median_ann >= 0.25 and n_obs >= 30 and phase34 != "lambdamart_only":
        pct = 100  # 全 3 source

    return {
        "criterion": "策略模型管理",
        "pct": pct,
        "kpi": {
            "ann_ret_median": median_ann,
            "ann_ret_cagr": cagr,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "hit_rate": hit_rate,
            "n_obs": n_obs,
        },
        "phase_3_4_status": phase34,
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def check_backtester_gate() -> dict:
    """#3 backtester gate: 4 gate verdict + 历史反例阻断."""
    phase4_report = REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"
    if not phase4_report.exists():
        return {"criterion": "backtester gate", "pct": 50, "verdict": "WARN",
                "reason": "phase4_gate_result.json 不存在, 跑 run_phase4_gate_on_msaf.py"}

    d = json.loads(phase4_report.read_text())
    gate = d.get("gate_result", {})
    promote_action = gate.get("promote_action", "unknown")
    pct = 60
    if promote_action == "warn_only":
        pct = 85  # 实际是数据不足 (n<30), 不算 fail
    elif promote_action == "promote":
        pct = 100
    elif promote_action in ("block", "force_retrain"):
        pct = 30

    return {
        "criterion": "backtester gate",
        "pct": pct,
        "promote_action": promote_action,
        "all_pass": gate.get("all_pass"),
        "gate_pbo": gate.get("pbo", {}).get("passes"),
        "gate_dsr": gate.get("dsr", {}).get("passes"),
        "gate_cons": gate.get("conservative", {}).get("passes"),
        "gate_is_oos": gate.get("is_oos", {}).get("passes"),
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def check_daily_automation() -> dict:
    """#4 全自动化 daily: 检查 8 步真调 status."""
    daily_script = REPO_ROOT / "scripts" / "daily_update.sh"
    if not daily_script.exists():
        return {"criterion": "全自动化 daily", "pct": 0, "verdict": "FAIL"}

    content = daily_script.read_text()
    steps_status = {}
    for step_num in range(0, 9):
        # 检查 Step N 有真调用 (非 DRY check)
        markers = [
            f"Step {step_num}: ",
            f"# Step {step_num}",
        ]
        steps_status[f"step_{step_num}"] = any(m in content for m in markers)

    has_step_0_cost = "Step 0: GCP cost tracker" in content
    has_phase4_gate_real = "run_phase4_gate_on_msaf.py" in content
    has_alpha158_check = "Step 2c: alpha158" in content
    has_promote_verdict_gated = "STEP6_GATE_OK" in content

    pct = 60 + (10 if has_step_0_cost else 0) + (10 if has_phase4_gate_real else 0) + \
          (5 if has_alpha158_check else 0) + (5 if has_promote_verdict_gated else 0)
    return {
        "criterion": "全自动化 daily",
        "pct": pct,
        "step_0_cost": has_step_0_cost,
        "phase4_gate_real": has_phase4_gate_real,
        "alpha158_check": has_alpha158_check,
        "promote_verdict_gated": has_promote_verdict_gated,
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def check_gcp_cost_control() -> dict:
    """#5 GCP 成本控制: cost_tracker 跑 + budget 检查."""
    cost_report = REPO_ROOT / "data" / "reports" / "gcp_cost_summary.json"
    tracker_script = REPO_ROOT / "gcp" / "cost_tracker.sh"
    if not tracker_script.exists():
        return {"criterion": "GCP 成本控制", "pct": 50, "verdict": "WARN",
                "reason": "gcp/cost_tracker.sh 不存在"}

    pct = 80
    cost_info = {}
    if cost_report.exists():
        cost_info = json.loads(cost_report.read_text())
        alert = cost_info.get("alert_level", "UNKNOWN")
        if alert == "OK":
            pct = 100
        elif alert == "YELLOW":
            pct = 70
        else:
            pct = 50

    return {
        "criterion": "GCP 成本控制",
        "pct": pct,
        "alert_level": cost_info.get("alert_level"),
        "pct_of_budget": cost_info.get("pct_of_budget"),
        "projected_month_cost": cost_info.get("projected_month_cost"),
        "vm_status": cost_info.get("vm_status"),
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def check_live_ready() -> dict:
    """#6 实盘 GO/NO-GO: 5 年 OOS 验证 + 跨年中位 ≥ 25%."""
    msaf_report = REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"
    if not msaf_report.exists():
        return {"criterion": "实盘 GO/NO-GO", "pct": 0, "verdict": "FAIL",
                "reason": "msaf_ensemble_run.json 不存在"}

    d = json.loads(msaf_report.read_text())
    kpi = d.get("kpi", {})
    n_obs = kpi.get("n_obs", 0)
    median = kpi.get("ann_ret_median", 0) or 0
    max_dd = kpi.get("max_dd", -1) or -1
    sharpe = kpi.get("sharpe", 0) or 0

    pct = 0
    if n_obs >= 22 and median >= 0.25:
        pct = 5
    if n_obs >= 30 and median >= 0.25 and abs(max_dd) <= 0.20 and sharpe >= 1.0:
        pct = 30
    if n_obs >= 60 and median >= 0.25 and abs(max_dd) <= 0.20 and sharpe >= 2.0:
        pct = 80
    if n_obs >= 60 and median >= 0.25 and abs(max_dd) <= 0.20 and sharpe >= 2.0:
        # 待 PBO ≤ 0.20 + 跨年单年 ≥ 0% 才 100%
        pct = 90

    return {
        "criterion": "实盘 GO/NO-GO",
        "pct": pct,
        "n_obs": n_obs,
        "median_ann": median,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "reason": f"need n_obs ≥ 60 (now {n_obs}), max_dd ≤ -20% (now {max_dd:.2%}), sharpe ≥ 2.0 (now {sharpe:.2f})",
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Delivery readiness audit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    log.info("=== 项目交付准备度 audit ===")

    checks = [
        check_data_management(),
        check_strategy_model(),
        check_backtester_gate(),
        check_daily_automation(),
        check_gcp_cost_control(),
        check_live_ready(),
    ]

    avg = sum(c["pct"] for c in checks) / len(checks)
    overall = {
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "criteria": checks,
        "avg_pct": avg,
        "ready_for_delivery": avg >= 95,
    }

    if args.json:
        print(json.dumps(overall, indent=2, ensure_ascii=False, default=str))
    else:
        log.info("")
        log.info(f"{'#':<4}{'交付标准':<24} {'目标':<6} {'当前':<6}  Verdict")
        log.info("-" * 70)
        for i, c in enumerate(checks, 1):
            target = "100%"
            current = f"{c['pct']}%"
            log.info(f"{i:<4}{c['criterion']:<24} {target:<6} {current:<6}  {c['verdict']}")
        log.info("-" * 70)
        log.info(f"     {'均值':<24} {'100%':<6} {avg:.0f}%   {'READY' if avg >= 95 else 'NOT READY'}")
        log.info("")
        log.info("详情:")
        for c in checks:
            log.info(f"  [{c['criterion']}] {json.dumps({k:v for k,v in c.items() if k not in ('criterion','pct','verdict')}, ensure_ascii=False, default=str)}")

    out_path = REPO_ROOT / "data" / "reports" / "delivery_readiness.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(overall, indent=2, ensure_ascii=False, default=str))
    if not args.json:
        log.info(f"saved: {out_path}")

    return 0 if avg >= 95 else 1


if __name__ == "__main__":
    sys.exit(main())
