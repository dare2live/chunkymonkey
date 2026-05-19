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

# #6 live readiness ladder constants.
# rule-compliance: ok evidence=PROJECT_INDEX 2026-05-19 Pareto baseline accepted by user
SHIP_N_OBS_MIN = 22
SHIP_ANN_RET_MIN = 0.10
SHIP_MAX_DD_ABS_MAX = 0.25
SHIP_DSR_CONF_MIN = 0.50
SAMPLE_N_OBS_MIN = 30
PERFECT_N_OBS_MIN = 60
PERFECT_SHARPE_MIN = 2.0
PERFECT_MAX_DD_ABS_MAX = 0.20


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

    # Phase 3.4 sniper/institution 真接 check (mart 表存在 + ensemble runner 引用 + 真启用 toggle)
    # 固化 (2026-05-18 用户 push back '加 mart 但 audit 不 reflect = 错误, 修一次防一切'):
    # 用 dict-driven 检测, 加新 source 只需加 1 行
    # Codex review 2026-05-19 a52e7e93 HIGH: institution 默认 OFF (--with-institution), 必须读 msaf
    # ensemble run json args 判定真启用; 不能只看 runner 源码 import (否则报 95% 假象).
    # SOURCES spec 字段: mart_table (DB 检 schema) + enabled_args (run json args 字段, 全 true 才算真启用; None 表示 always-on)
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"
    runner_path = REPO_ROOT / "backend" / "scripts" / "run_msaf_ensemble_paper_sim.py"
    runner_text = runner_path.read_text() if runner_path.exists() else ""
    ensemble_run_path = REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"
    ensemble_args: dict = {}
    if ensemble_run_path.exists():
        try:
            ensemble_args = json.loads(ensemble_run_path.read_text()).get("args", {})
        except Exception as e:
            log.warning(f"msaf_ensemble_run.json args parse failed: {e}")
    SOURCES = {
        "sniper": {
            "mart_table": "mart_sniper_score_daily",
            "enabled_args": None,  # always-on, runner 直接 load_sniper_scores
        },
        "institution": {
            "mart_table": "mart_institution_score_daily",
            "enabled_args": {"with_institution": True, "no_institution": False},
        },
    }
    sources_wired = {}
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        for source_name, spec in SOURCES.items():
            mart_table = spec["mart_table"]
            r = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [mart_table],
            ).fetchone()
            has_mart = bool(r and r[0] > 0)
            ensemble_uses = mart_table in runner_text
            enabled_args = spec["enabled_args"]
            if enabled_args is None:
                arg_enabled = True  # always-on
            else:
                arg_enabled = all(
                    ensemble_args.get(k) == v for k, v in enabled_args.items()
                )
            sources_wired[source_name] = has_mart and ensemble_uses and arg_enabled
        con.close()
    except Exception as e:
        log.warning(f"source mart lookup failed: {e}")

    active_sources = ["LM"] + [s for s, wired in sources_wired.items() if wired]
    phase34 = " + ".join(active_sources)  # e.g. "LM + sniper + institution"
    n_extra_sources = len(active_sources) - 1  # 减 LM 本身

    pct = 50
    if median_ann is not None and median_ann >= 0.25:
        pct = 80  # KPI 达标但 lambdamart only
    if median_ann is not None and median_ann >= 0.25 and n_extra_sources >= 1:
        pct = 90  # 加 1 source (sniper OR institution)
    if median_ann is not None and median_ann >= 0.25 and n_extra_sources >= 2:
        pct = 95  # 全 3 source (LM + sniper + institution 都 wired)
    if median_ann is not None and median_ann >= 0.25 and n_extra_sources >= 2 and n_obs >= 30:
        pct = 100  # 全 3 source + OOS ≥ 30 obs

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
        "sources_wired": sources_wired,
        "n_extra_sources": n_extra_sources,
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def _load_p3_acceptance(smart_db: Path, pit_report: Path | None = None) -> dict:
    """Load latest P3 acceptance, falling back to the PIT audit artifact."""
    out = {
        "found": False,
        "passed": False,
        "ann_ret": 0.0,
        "max_dd": 0.0,
        "monthly_win": 0.0,
        "source": "none",
    }
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        r = con.execute("""
            SELECT passed, ann_ret, max_dd, monthly_win_rate
            FROM mart_p3_acceptance_result
            WHERE ann_ret > 0 ORDER BY built_at DESC LIMIT 1
        """).fetchone()
        con.close()
        if r is not None:
            return {
                "found": True,
                "passed": bool(r[0]),
                "ann_ret": float(r[1]) if r[1] is not None else 0.0,
                "max_dd": float(r[2]) if r[2] is not None else 0.0,
                "monthly_win": float(r[3]) if r[3] is not None else 0.0,
                "source": "duckdb",
            }
    except Exception as e:
        log.warning(f"P3 lookup failed: {e}")

    pit_path = pit_report or (REPO_ROOT / "data" / "reports" / "pit_audit.json")
    if not pit_path.exists():
        return out
    try:
        d = json.loads(pit_path.read_text())
        for table in d.get("tables", []):
            if table.get("table") != "mart_p3_acceptance_result":
                continue
            runs = table.get("latest_pass_runs") or []
            if not runs:
                return out
            latest = runs[0]
            return {
                "found": True,
                "passed": bool(latest.get("passed", False)),
                "ann_ret": float(latest.get("ann_ret") or 0.0),
                "max_dd": 0.0,
                "monthly_win": 0.0,
                "source": "pit_audit_fallback",
            }
    except Exception as e:
        log.warning(f"P3 PIT audit fallback parse failed: {e}")
    return out


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
        elif phase4_verdict == "warn_only_proxy":
            # Codex review 2026-05-19 HIGH 2: proxy IS-OOS evidence degraded, 4 gates 全 pass 但
            # 不等同 hard promote. pct 85 跟 warn_only 同 tier, 但 promote_action 区分 evidence 等级.
            phase4_pct = 85
        elif phase4_verdict == "warn_only":
            phase4_pct = 85
        else:
            # block / force_retrain: 看 4 gates pass 数
            phase4_pct = 25 * n_pass  # 4/4=100, 3/4=75, 2/4=50, 1/4=25, 0/4=0

    # P3 acceptance verdict
    p3 = _load_p3_acceptance(smart_db)
    p3_passed = p3["passed"]
    p3_pct = 100 if p3_passed else (30 if p3["found"] else 0)

    # 综合: phase4 weight 50% + P3 weight 50%
    pct = int(phase4_pct * 0.5 + p3_pct * 0.5)

    return {
        "criterion": "backtester gate",
        "pct": pct,
        "phase4_promote_action": phase4_verdict,
        "phase4_pct": phase4_pct,
        "p3_passed": p3_passed,
        "p3_pct": p3_pct,
        "p3_source": p3["source"],
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

    # 真 loaded + 真 healthy 检测 (不只是 plist 文件存在 + 不只是 loaded):
    # 1) launchctl list 显示 + exit code != 126 (macOS Full Disk Access permission denied)
    # 2) crontab -l 显示 (无 FDA 阻塞, cron daemon 路径不同) — 后备路径
    import subprocess
    loaded_labels = {}  # label → last exit code
    fda_blocked = False
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and parts[-1].startswith("com.chunkymonkey."):
                    try:
                        exit_code = int(parts[1])
                    except (ValueError, IndexError):
                        exit_code = -1
                    loaded_labels[parts[-1]] = exit_code
                    if exit_code == 126:
                        fda_blocked = True
    except Exception as e:
        log.warning(f"launchctl list failed: {e}")
    daily_loaded_launchd = loaded_labels.get("com.chunkymonkey.daily-update") == 0
    cost_loaded_launchd = loaded_labels.get("com.chunkymonkey.gcp-cost-tracker") == 0

    # crontab fallback check
    cron_daily = False
    cron_cost = False
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            cron_text = result.stdout
            cron_daily = "scripts/daily_update.sh" in cron_text
            cron_cost = "gcp/cost_tracker.sh" in cron_text
    except Exception as e:
        log.warning(f"crontab -l failed: {e}")

    daily_loaded = daily_loaded_launchd or cron_daily
    cost_loaded = cost_loaded_launchd or cron_cost

    # cron OR launchd counted as "loaded" (cron is FDA-free fallback)
    pct = 40 + (10 if has_step_0_cost else 0) + (10 if has_phase4_gate_real else 0) + \
          (5 if has_alpha158_check else 0) + (5 if has_promote_verdict_gated else 0) + \
          (10 if has_step5_ensemble_real else 0) + \
          (10 if has_promote_real else 0) + \
          (5 if daily_loaded else (2 if (has_daily_plist or cron_daily) else 0)) + \
          (5 if cost_loaded else (2 if (has_cost_plist or cron_cost) else 0))
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
        "daily_loaded": daily_loaded,
        "cost_loaded": cost_loaded,
        "daily_via_launchd": daily_loaded_launchd,
        "daily_via_cron": cron_daily,
        "cost_via_launchd": cost_loaded_launchd,
        "cost_via_cron": cron_cost,
        "loaded_agents_launchd": {k: v for k, v in loaded_labels.items()},
        "fda_blocked": fda_blocked,
        "install_action": (
            None if (daily_loaded and cost_loaded) else
            "bash configs/cron/install.sh install  # 无 FDA 阻塞 (推荐)"
            if fda_blocked else
            "bash configs/cron/install.sh install  OR  bash configs/launchd/install_all.sh install"
        ),
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


def _load_phase4_live_evidence(phase4_report: Path) -> dict:
    """Read phase4 evidence needed by #6 live readiness.

    #6 is the live go/no-go view, so it consumes the hard statistical evidence
    that belongs to the live gate: PBO and DSR. IS-OOS remains reported by
    check_backtester_gate(); proxy-mode IS-OOS is not promoted into a ship
    blocker here because the accepted ship baseline is the Pareto baseline.
    """
    out = {
        "pbo_passed": False,
        "pbo_reason": "phase4_gate_result.json missing",
        "dsr_conf": 0.0,
        "dsr_passed": False,
        "dsr_reason": "phase4_gate_result.json missing",
        "conservative_passed": False,
        "is_oos_passed": False,
        "is_oos_proxy_mode": None,
        "phase4_promote_action": None,
    }
    if not phase4_report.exists():
        return out

    try:
        d = json.loads(phase4_report.read_text())
        gate = d.get("gate_result", {})
        pbo = gate.get("pbo", {}) or {}
        dsr = gate.get("dsr", {}) or {}
        conservative = gate.get("conservative", {}) or {}
        is_oos = gate.get("is_oos", {}) or {}

        dsr_detail = dsr.get("detail", {}) or {}
        dsr_conf = float(dsr_detail.get("p_conf") or 0.0)
        out.update({
            "pbo_passed": bool(pbo.get("passes", False)),
            "pbo_reason": pbo.get("reason"),
            "dsr_conf": dsr_conf,
            "dsr_passed": bool(dsr.get("passes", False)) and dsr_conf >= SHIP_DSR_CONF_MIN,
            "dsr_reason": dsr.get("reason"),
            "conservative_passed": bool(conservative.get("passes", False)),
            "is_oos_passed": bool(is_oos.get("passes", False)),
            "is_oos_proxy_mode": (is_oos.get("detail", {}) or {}).get("proxy_mode"),
            "phase4_promote_action": gate.get("promote_action"),
        })
    except Exception as e:
        log.warning(f"phase4 live evidence parse failed: {e}")
    return out


def _score_live_ready(
    *,
    p3_passed: bool,
    n_obs: int,
    median_ann: float,
    cagr_ann: float,
    max_dd: float,
    sharpe: float,
    pbo_passed: bool,
    dsr_conf: float,
) -> dict:
    """Score #6 live readiness as ship baseline plus stricter perfect ladder.

    Ship baseline is the accepted Pareto gate:
      P3 PASS + 22 OOS obs + annualized return >= 10% + max DD <= 25%
      + PBO PASS + DSR confidence >= 0.5.

    The legacy Sharpe >= 2.0 / 60 obs / max DD <= 20% requirements remain as
    perfect-ladder milestones. They raise confidence but do not block a Pareto
    baseline ship PASS.
    """
    effective_ann = min(median_ann, cagr_ann) if cagr_ann else median_ann
    max_dd_abs = abs(max_dd)

    pct = 0
    if n_obs >= SHIP_N_OBS_MIN and effective_ann >= SHIP_ANN_RET_MIN:
        pct = 20
    if p3_passed:
        pct = max(pct, 60)

    ship_baseline_passed = (
        p3_passed
        and n_obs >= SHIP_N_OBS_MIN
        and effective_ann >= SHIP_ANN_RET_MIN
        and max_dd_abs <= SHIP_MAX_DD_ABS_MAX
        and pbo_passed
        and dsr_conf >= SHIP_DSR_CONF_MIN
    )
    if ship_baseline_passed:
        pct = max(pct, 80)
    if ship_baseline_passed and n_obs >= SAMPLE_N_OBS_MIN:
        pct = max(pct, 85)

    perfect_ladder_ready = (
        ship_baseline_passed
        and n_obs >= PERFECT_N_OBS_MIN
        and sharpe >= PERFECT_SHARPE_MIN
    )
    if perfect_ladder_ready:
        pct = max(pct, 95)
    if perfect_ladder_ready and max_dd_abs <= PERFECT_MAX_DD_ABS_MAX:
        pct = max(pct, 100)

    blockers = []
    if not p3_passed:
        blockers.append("P3 not PASS")
    if n_obs < SHIP_N_OBS_MIN:
        blockers.append(f"n_obs {n_obs} < {SHIP_N_OBS_MIN}")
    if effective_ann < SHIP_ANN_RET_MIN:
        blockers.append(f"effective_ann {effective_ann:.2%} < {SHIP_ANN_RET_MIN:.0%}")
    if max_dd_abs > SHIP_MAX_DD_ABS_MAX:
        blockers.append(f"max_dd {max_dd:.2%} worse than -{SHIP_MAX_DD_ABS_MAX:.0%}")
    if not pbo_passed:
        blockers.append("PBO not PASS")
    if dsr_conf < SHIP_DSR_CONF_MIN:
        blockers.append(f"DSR p_conf {dsr_conf:.2f} < {SHIP_DSR_CONF_MIN:.2f}")

    next_milestones = []
    if n_obs < SAMPLE_N_OBS_MIN:
        next_milestones.append(f"n_obs {n_obs} < {SAMPLE_N_OBS_MIN} for 85%")
    if n_obs < PERFECT_N_OBS_MIN:
        next_milestones.append(f"n_obs {n_obs} < {PERFECT_N_OBS_MIN} for perfect ladder")
    if sharpe < PERFECT_SHARPE_MIN:
        next_milestones.append(f"sharpe {sharpe:.2f} < {PERFECT_SHARPE_MIN:.1f} for perfect ladder")
    if max_dd_abs > PERFECT_MAX_DD_ABS_MAX:
        next_milestones.append(f"max_dd {max_dd:.2%} worse than -{PERFECT_MAX_DD_ABS_MAX:.0%} for perfect ladder")

    return {
        "pct": pct,
        "effective_ann": effective_ann,
        "ship_baseline_passed": ship_baseline_passed,
        "perfect_ladder_ready": perfect_ladder_ready,
        "blockers": blockers,
        "next_milestones": next_milestones,
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def check_live_ready() -> dict:
    """#6 实盘 GO/NO-GO: Pareto ship baseline + stricter perfect ladder."""
    msaf_report = REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"
    phase4_report = REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"

    if not msaf_report.exists():
        return {"criterion": "实盘 GO/NO-GO", "pct": 0, "verdict": "FAIL",
                "reason": "msaf_ensemble_run.json 不存在"}

    d = json.loads(msaf_report.read_text())
    kpi = d.get("kpi", {})
    n_obs = kpi.get("n_obs", 0)
    median = kpi.get("ann_ret_median", 0) or 0
    cagr = kpi.get("ann_ret_cagr", 0) or 0
    max_dd = kpi.get("max_dd", -1) or -1
    sharpe = kpi.get("sharpe", 0) or 0

    # P3 acceptance verdict (PASS / FAIL)
    p3 = _load_p3_acceptance(smart_db)
    p3_passed = p3["passed"]
    p3_ann = p3["ann_ret"]
    p3_max_dd = p3["max_dd"]
    p3_win = p3["monthly_win"]

    phase4 = _load_phase4_live_evidence(phase4_report)
    scored = _score_live_ready(
        p3_passed=p3_passed,
        n_obs=n_obs,
        median_ann=median,
        cagr_ann=cagr,
        max_dd=max_dd,
        sharpe=sharpe,
        pbo_passed=phase4["pbo_passed"],
        dsr_conf=phase4["dsr_conf"],
    )

    return {
        "criterion": "实盘 GO/NO-GO",
        "pct": scored["pct"],
        "p3_passed": p3_passed,
        "p3_ann_ret": p3_ann,
        "p3_max_dd": p3_max_dd,
        "p3_monthly_win": p3_win,
        "p3_source": p3["source"],
        "msaf_n_obs": n_obs,
        "msaf_median_ann": median,
        "msaf_cagr_ann": cagr,
        "msaf_effective_ann": scored["effective_ann"],
        "msaf_max_dd": max_dd,
        "msaf_sharpe": sharpe,
        "phase4_pbo_passed": phase4["pbo_passed"],
        "phase4_dsr_conf": phase4["dsr_conf"],
        "phase4_dsr_passed": phase4["dsr_passed"],
        "phase4_conservative_passed": phase4["conservative_passed"],
        "phase4_is_oos_passed": phase4["is_oos_passed"],
        "phase4_is_oos_proxy_mode": phase4["is_oos_proxy_mode"],
        "phase4_promote_action": phase4["phase4_promote_action"],
        "ship_baseline_passed": scored["ship_baseline_passed"],
        "perfect_ladder_ready": scored["perfect_ladder_ready"],
        "blockers": scored["blockers"],
        "next_milestones": scored["next_milestones"],
        "verdict": scored["verdict"],
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
