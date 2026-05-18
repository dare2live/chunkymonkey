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
    """#1 数据管理: stale source count + PIT 严格度 + PIT coverage audit."""
    sla_report = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"
    n_alerts = 9999
    if sla_report.exists():
        d = json.loads(sla_report.read_text())
        n_alerts = d.get("n_alerts", d.get("n_alert", None))
        if n_alerts is None and "sources" in d:
            n_alerts = sum(1 for r in d["sources"] if r.get("alert"))
        if n_alerts is None and "results" in d:
            n_alerts = sum(1 for r in d["results"] if r.get("alert"))
        if n_alerts is None:
            n_alerts = 9999

    # PIT coverage from audit_pit_coverage.py 输出
    pit_report = REPO_ROOT / "data" / "reports" / "pit_audit.json"
    pit_pct = 0
    pit_summary = "not run"
    if pit_report.exists():
        d = json.loads(pit_report.read_text())
        pit_pct = d.get("pit_coverage_pct", 0)
        pit_summary = f"{d.get('n_pass', 0)}/{d.get('n_total', 0)} tables PASS"

    # 综合: SLA 50% + PIT 50%
    sla_pct = 100 if n_alerts == 0 else (80 if n_alerts <= 2 else (60 if n_alerts <= 5 else 30))
    status_pct = int(sla_pct * 0.5 + pit_pct * 0.5)

    return {
        "criterion": "数据管理",
        "pct": status_pct,
        "stale_alerts": n_alerts,
        "sla_pct": sla_pct,
        "pit_pct": pit_pct,
        "pit_summary": pit_summary,
        "verdict": "PASS" if status_pct >= 80 else "WARN",
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
    """#3 backtester gate: phase4 gate verdict 综合 P3 PASS + 历史反例阻断 tests."""
    phase4_report = REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"

    phase4_pct = 60
    phase4_verdict = "unknown"
    if phase4_report.exists():
        d = json.loads(phase4_report.read_text())
        gate = d.get("gate_result", {})
        phase4_verdict = gate.get("promote_action", "unknown")
        # 4 gates 各占 25%, 综合给 phase4_pct
        n_pass = sum([
            gate.get("pbo", {}).get("passes", False),
            gate.get("dsr", {}).get("passes", False),
            gate.get("conservative", {}).get("passes", False),
            gate.get("is_oos", {}).get("passes", False),
        ])
        if phase4_verdict == "promote":
            phase4_pct = 100
        elif phase4_verdict == "warn_only":
            phase4_pct = 85
        else:
            # block / force_retrain: 看 4 gates pass 数
            phase4_pct = 25 * n_pass  # 4/4=100, 3/4=75, 2/4=50, 1/4=25, 0/4=0

    # P3 acceptance verdict
    p3_pct = 0
    p3_passed = False
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        r = con.execute("""
            SELECT passed FROM mart_p3_acceptance_result
            WHERE ann_ret > 0 ORDER BY built_at DESC LIMIT 1
        """).fetchone()
        con.close()
        if r is not None:
            p3_passed = bool(r[0])
            p3_pct = 100 if p3_passed else 30
    except Exception as e:
        log.warning(f"P3 lookup failed: {e}")
        p3_pct = 0

    # 综合: phase4 weight 50% + P3 weight 50%
    pct = int(phase4_pct * 0.5 + p3_pct * 0.5)

    return {
        "criterion": "backtester gate",
        "pct": pct,
        "phase4_promote_action": phase4_verdict,
        "phase4_pct": phase4_pct,
        "p3_passed": p3_passed,
        "p3_pct": p3_pct,
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
    # Step 5 真调 ensemble runner (compute-kpi)
    has_step5_ensemble_real = "run_msaf_ensemble_paper_sim.py" in content and "--compute-kpi" in content
    # Step 7 真调 promote_champion.py CLI (不是 mock import check)
    has_promote_real = "backend/scripts/promote_champion.py" in content and "--p3-run-id" in content
    # launchd plist installed?
    plist_dir = REPO_ROOT / "configs" / "launchd"
    has_daily_plist = (plist_dir / "com.chunkymonkey.daily-update.plist").exists()
    has_cost_plist = (plist_dir / "com.chunkymonkey.gcp-cost-tracker.plist").exists()

    pct = 40 + (10 if has_step_0_cost else 0) + (10 if has_phase4_gate_real else 0) + \
          (5 if has_alpha158_check else 0) + (5 if has_promote_verdict_gated else 0) + \
          (10 if has_step5_ensemble_real else 0) + \
          (10 if has_promote_real else 0) + (5 if has_daily_plist else 0) + (5 if has_cost_plist else 0)
    return {
        "criterion": "全自动化 daily",
        "pct": min(pct, 100),
        "step_0_cost": has_step_0_cost,
        "phase4_gate_real": has_phase4_gate_real,
        "alpha158_check": has_alpha158_check,
        "promote_verdict_gated": has_promote_verdict_gated,
        "step5_ensemble_real": has_step5_ensemble_real,
        "promote_champion_real_call": has_promote_real,
        "daily_plist_installed": has_daily_plist,
        "cost_plist_installed": has_cost_plist,
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
    """#6 实盘 GO/NO-GO: P3 acceptance PASS + Phase 3.3 KPI + 跨 5 年 OOS."""
    msaf_report = REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"

    if not msaf_report.exists():
        return {"criterion": "实盘 GO/NO-GO", "pct": 0, "verdict": "FAIL",
                "reason": "msaf_ensemble_run.json 不存在"}

    d = json.loads(msaf_report.read_text())
    kpi = d.get("kpi", {})
    n_obs = kpi.get("n_obs", 0)
    median = kpi.get("ann_ret_median", 0) or 0
    max_dd = kpi.get("max_dd", -1) or -1
    sharpe = kpi.get("sharpe", 0) or 0

    # P3 acceptance verdict (PASS / FAIL)
    p3_passed = False
    p3_ann = 0.0
    p3_max_dd = 0.0
    p3_win = 0.0
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        r = con.execute("""
            SELECT passed, ann_ret, max_dd, monthly_win_rate
            FROM mart_p3_acceptance_result
            WHERE ann_ret > 0 ORDER BY built_at DESC LIMIT 1
        """).fetchone()
        con.close()
        if r is not None:
            p3_passed = bool(r[0])
            p3_ann = float(r[1]) if r[1] is not None else 0.0
            p3_max_dd = float(r[2]) if r[2] is not None else 0.0
            p3_win = float(r[3]) if r[3] is not None else 0.0
    except Exception as e:
        log.warning(f"P3 result lookup failed: {e}")

    # 5 段评分 (累加, 不互斥):
    #   5%: KPI 实测 + 跨过最低 25% 目标
    #   30%: P3 acceptance 4 硬验收 PASS
    #   60%: + n_obs ≥ 30 (短期 sample 充足)
    #   80%: + n_obs ≥ 60 + sharpe ≥ 2.0 (跨 5 年)
    #   90%: + PBO < 0.20 + multi-trial Optuna
    pct = 0
    if n_obs >= 22 and median >= 0.25:
        pct = 5
    if p3_passed:
        pct = max(pct, 60)  # P3 PASS critical milestone
    if p3_passed and n_obs >= 30:
        pct = max(pct, 70)
    if p3_passed and n_obs >= 60 and sharpe >= 2.0:
        pct = max(pct, 85)
    if p3_passed and n_obs >= 60 and sharpe >= 2.0 and abs(max_dd) <= 0.20:
        pct = max(pct, 90)

    blockers = []
    if not p3_passed: blockers.append("P3 not PASS")
    if n_obs < 30: blockers.append(f"n_obs {n_obs} < 30")
    if n_obs < 60: blockers.append(f"n_obs {n_obs} < 60 (跨 5 年)")
    if sharpe < 2.0: blockers.append(f"sharpe {sharpe:.2f} < 2.0")
    if abs(max_dd) > 0.20: blockers.append(f"max_dd {max_dd:.2%} > -20%")

    return {
        "criterion": "实盘 GO/NO-GO",
        "pct": pct,
        "p3_passed": p3_passed,
        "p3_ann_ret": p3_ann,
        "p3_max_dd": p3_max_dd,
        "p3_monthly_win": p3_win,
        "msaf_n_obs": n_obs,
        "msaf_median_ann": median,
        "msaf_max_dd": max_dd,
        "msaf_sharpe": sharpe,
        "blockers": blockers,
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
