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
from typing import Any

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


def _load_msaf_horizon_ladder(reports_dir: Path, primary_report: dict) -> list[dict]:
    """Load MSAF horizon probe evidence without changing the live decision.

    The live GO/NO-GO metric stays on the primary 20d MSAF run. These probes
    answer a narrower operational question: whether shorter horizons solve the
    sample-size ladder without creating worse quality metrics.
    """
    rows: list[dict] = []

    def add_row(path: Path, payload: dict, *, primary: bool = False) -> None:
        kpi = payload.get("kpi") or {}
        args = payload.get("args") or {}
        horizon = kpi.get("horizon") or args.get("horizon")
        if not horizon:
            return
        n_obs = int(kpi.get("n_obs") or 0)
        sharpe = float(kpi.get("sharpe") or 0.0)
        max_dd = float(kpi.get("max_dd") or 0.0)
        row = {
            "horizon": str(horizon),
            "report": str(path),
            "primary": primary,
            "n_obs": n_obs,
            "ann_ret_cagr": kpi.get("ann_ret_cagr"),
            "ann_ret_median": kpi.get("ann_ret_median"),
            "sharpe": sharpe,
            "max_dd": max_dd,
            "hit_rate": kpi.get("hit_rate"),
            "sample_ready": n_obs >= SAMPLE_N_OBS_MIN,
            "perfect_sample_ready": n_obs >= PERFECT_N_OBS_MIN,
            "perfect_sharpe_ready": sharpe >= PERFECT_SHARPE_MIN,
            "perfect_dd_ready": abs(max_dd) <= PERFECT_MAX_DD_ABS_MAX,
        }
        row["perfect_ladder_ready"] = (
            row["perfect_sample_ready"]
            and row["perfect_sharpe_ready"]
            and row["perfect_dd_ready"]
        )
        rows.append(row)

    add_row(reports_dir / "msaf_ensemble_run.json", primary_report, primary=True)
    horizon_probe_paths = list(reports_dir.glob("msaf_ensemble_h*_probe_*.json"))
    horizon_probe_paths.sort()
    for path in horizon_probe_paths:
        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning(f"MSAF horizon probe parse failed: {path}: {e}")
            continue
        add_row(path, payload)
    # 2026-05-23 加 V4+BC ensemble horizon_ladder reports (新 ensemble strategies)
    v4_bc_ladder_paths = list(reports_dir.glob("v4_bc_ensemble_horizon_ladder*.json"))
    v4_bc_ladder_paths.sort()
    for path in v4_bc_ladder_paths:
        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning(f"V4+BC ensemble report parse failed: {path}: {e}")
            continue
        add_row(path, payload)

    def horizon_key(row: dict) -> int:
        h = str(row.get("horizon") or "999d").rstrip("d")
        try:
            return int(h)
        except ValueError:
            return 999

    deduped: dict[str, dict] = {}
    for row in rows:
        horizon = str(row["horizon"])
        old = deduped.get(horizon)
        if old is None or (not old.get("primary") and row.get("primary")):
            deduped[horizon] = row
    return sorted(deduped.values(), key=horizon_key)


def _weight_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _phase4_probe_gate_key(
    model_id: Any,
    horizon: Any,
    source_weight_override: dict,
    score_filter: dict | None = None,
    score_exposure: dict | None = None,
    position_sizing: dict | None = None,
    max_positions: Any = None,
    cash_overlay: dict | None = None,
) -> tuple[Any, ...] | None:
    if not model_id or not horizon:
        return None
    score_filter = score_filter or {}
    score_exposure = score_exposure or {}
    position_sizing = position_sizing or {}
    cash_overlay = cash_overlay or {}
    exposure_floor = _weight_value(score_exposure.get("score_exposure_floor"))
    exposure_ceiling = _weight_value(score_exposure.get("score_exposure_ceiling"))
    exposure_min = _weight_value(score_exposure.get("score_min_exposure")) if exposure_floor is not None else None
    return (
        str(model_id),
        str(horizon),
        _weight_value(source_weight_override.get("lambdamart_weight")),
        _weight_value(source_weight_override.get("sniper_weight")),
        _weight_value(source_weight_override.get("institution_weight")),
        _weight_value(score_filter.get("min_top_score")),
        _weight_value(score_filter.get("min_sniper_score")),
        exposure_floor,
        exposure_ceiling,
        exposure_min,
        _weight_value(position_sizing.get("rank_decay")),
        _safe_int(max_positions),
        _weight_value(cash_overlay.get("bull_cash_pct")),
        _weight_value(cash_overlay.get("neutral_cash_pct")),
        _weight_value(cash_overlay.get("bear_cash_pct")),
    )


def _load_msaf_phase4_probe_gates(reports_dir: Path) -> dict[tuple[Any, ...], dict]:
    """Load dedicated Phase4 gates for opt-in MSAF probe candidates."""
    rows: dict[tuple[Any, ...], dict] = {}
    gate_paths = list(reports_dir.glob("phase4_gate_msaf_*probe_*.json"))
    gate_paths.sort()
    for path in gate_paths:
        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning(f"MSAF Phase4 probe gate parse failed: {path}: {e}")
            continue
        gate = payload.get("gate_result") or {}
        source_weight_override = payload.get("source_weight_override") or {}
        score_filter = payload.get("score_filter") or {}
        score_exposure = payload.get("score_exposure") or {}
        position_sizing = payload.get("position_sizing") or {}
        key = _phase4_probe_gate_key(
            payload.get("model_id"),
            payload.get("primary_horizon"),
            source_weight_override,
            score_filter,
            score_exposure,
            position_sizing,
            payload.get("max_positions"),
            payload.get("cash_overlay"),
        )
        if key is None:
            continue
        pbo_detail = (gate.get("pbo") or {}).get("detail") or {}
        summary = {
            "report": str(path),
            "model_id": payload.get("model_id"),
            "primary_horizon": payload.get("primary_horizon"),
            "source_weight_override": source_weight_override,
            "score_filter": score_filter,
            "score_exposure": score_exposure,
            "position_sizing": position_sizing,
            "max_positions": payload.get("max_positions"),
            "cash_overlay": payload.get("cash_overlay") or {},
            "promote_action": gate.get("promote_action"),
            "all_pass": bool(gate.get("all_pass", False)),
            "pbo_passed": bool((gate.get("pbo") or {}).get("passes", False)),
            "pbo_value": pbo_detail.get("pbo"),
            "pbo_reason": (gate.get("pbo") or {}).get("reason"),
            "dsr_passed": bool((gate.get("dsr") or {}).get("passes", False)),
            "conservative_passed": bool((gate.get("conservative") or {}).get("passes", False)),
            "is_oos_passed": bool((gate.get("is_oos") or {}).get("passes", False)),
            "is_oos_proxy_mode": bool(((gate.get("is_oos") or {}).get("detail") or {}).get("proxy_mode", False)),
            "ann_normal": payload.get("ann_normal"),
            "ann_conservative": payload.get("ann_conservative"),
        }
        old = rows.get(key)
        if old is None or str(summary["report"]) > str(old.get("report")):
            rows[key] = summary
    return rows


def _load_msaf_risk_overlay_probes(reports_dir: Path, primary_report: dict) -> list[dict]:
    """Load opt-in MSAF cash overlay probes for #6 risk evidence."""
    primary_kpi = primary_report.get("kpi") or {}
    phase4_probe_gates = _load_msaf_phase4_probe_gates(reports_dir)
    rows: list[dict] = []
    probe_paths = {
        *reports_dir.glob("msaf_ensemble_*cash*_probe_*.json"),
        *reports_dir.glob("msaf_ensemble_*voltarget*_probe_*.json"),
        *reports_dir.glob("msaf_ensemble_*scorefloor*_probe_*.json"),
        *reports_dir.glob("msaf_ensemble_*scoreexposure*_probe_*.json"),
        *reports_dir.glob("msaf_ensemble_*lm*_sniper*_probe_*.json"),
        *reports_dir.glob("msaf_ensemble_*lmonly*_probe_*.json"),
    }
    sorted_probe_paths = sorted(probe_paths)
    for path in sorted_probe_paths:
        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning(f"MSAF cash overlay probe parse failed: {path}: {e}")
            continue
        kpi = payload.get("kpi") or {}
        if not kpi:
            continue
        args = payload.get("args") or {}
        cash_overlay = payload.get("cash_overlay") or {}
        volatility_target = payload.get("volatility_target") or {}
        score_filter = payload.get("score_filter") or {}
        score_exposure = payload.get("score_exposure") or {}
        position_sizing = payload.get("position_sizing") or {}
        source_weight_override = payload.get("source_weight_override") or {}
        row = {
            "report": str(path),
            "model_id": args.get("lambdamart_model_id"),
            "horizon": kpi.get("horizon") or args.get("horizon"),
            "cash_overlay": cash_overlay,
            "volatility_target": volatility_target,
            "score_filter": score_filter,
            "score_exposure": score_exposure,
            "position_sizing": position_sizing,
            "source_weight_override": source_weight_override,
            "max_positions": args.get("max_positions"),
            "n_obs": int(kpi.get("n_obs") or 0),
            "n_skip": int(kpi.get("n_skip") or 0),
            "ann_ret_cagr": kpi.get("ann_ret_cagr"),
            "ann_ret_median": kpi.get("ann_ret_median"),
            "sharpe": float(kpi.get("sharpe") or 0.0),
            "max_dd": float(kpi.get("max_dd") or 0.0),
            "hit_rate": kpi.get("hit_rate"),
            "avg_exposure": kpi.get("avg_exposure"),
            "min_realized_exposure": kpi.get("min_realized_exposure"),
        }
        if primary_kpi:
            row["delta_sharpe_vs_primary"] = row["sharpe"] - float(primary_kpi.get("sharpe") or 0.0)
            row["delta_max_dd_vs_primary"] = row["max_dd"] - float(primary_kpi.get("max_dd") or 0.0)
            row["delta_cagr_vs_primary"] = (
                float(row["ann_ret_cagr"] or 0.0) - float(primary_kpi.get("ann_ret_cagr") or 0.0)
            )
        row["sample_ready"] = row["n_obs"] >= SHIP_N_OBS_MIN
        row["perfect_sample_ready"] = row["n_obs"] >= PERFECT_N_OBS_MIN
        row["perfect_dd_ready"] = abs(row["max_dd"]) <= PERFECT_MAX_DD_ABS_MAX
        row["perfect_sharpe_ready"] = row["perfect_sample_ready"] and row["sharpe"] >= PERFECT_SHARPE_MIN
        row["perfect_ladder_ready"] = (
            row["perfect_sample_ready"]
            and row["perfect_dd_ready"]
            and row["perfect_sharpe_ready"]
        )
        gate_key = _phase4_probe_gate_key(
            row["model_id"],
            row["horizon"],
            source_weight_override,
            score_filter,
            score_exposure,
            position_sizing,
            row.get("max_positions"),
            cash_overlay,
        )
        row["phase4_gate"] = phase4_probe_gates.get(gate_key) if gate_key else None
        rows.append(row)
    return rows


def _summarize_source_weight_probes(rows: list[dict]) -> dict[str, Any]:
    source_rows = [
        row for row in rows
        if row.get("source_weight_override", {}).get("lambdamart_weight") is not None
    ]
    if not source_rows:
        return {
            "n_source_weight_probes": 0,
            "n_gate_pass": 0,
            "n_hard_promote_ready": 0,
            "n_proxy_candidate_ready": 0,
            "best_gate_pass": None,
            "best_hard_promote": None,
            "best_proxy_candidate": None,
            "best_strict_sharpe": None,
        }

    def compact(row: dict) -> dict:
        gate = row.get("phase4_gate") or {}
        return {
            "report": row.get("report"),
            "model_id": row.get("model_id"),
            "horizon": row.get("horizon"),
            "source_weight_override": row.get("source_weight_override") or {},
            "score_filter": row.get("score_filter") or {},
            "score_exposure": row.get("score_exposure") or {},
            "position_sizing": row.get("position_sizing") or {},
            "max_positions": row.get("max_positions"),
            "cash_overlay": row.get("cash_overlay") or {},
            "n_obs": row.get("n_obs"),
            "ann_ret_cagr": row.get("ann_ret_cagr"),
            "ann_ret_median": row.get("ann_ret_median"),
            "sharpe": row.get("sharpe"),
            "max_dd": row.get("max_dd"),
            "perfect_ladder_ready": row.get("perfect_ladder_ready"),
            "phase4_gate": gate or None,
        }

    gate_pass_rows = [row for row in source_rows if (row.get("phase4_gate") or {}).get("all_pass")]
    strict_rows = [
        row for row in source_rows
        if row.get("sample_ready")
        and row.get("perfect_sample_ready")
        and row.get("perfect_dd_ready")
        and row.get("sharpe", 0.0) >= PERFECT_SHARPE_MIN
    ]
    hard_promote_rows = [
        row for row in strict_rows
        if (row.get("phase4_gate") or {}).get("promote_action") == "promote"
    ]
    proxy_candidate_rows = [
        row for row in strict_rows
        if (row.get("phase4_gate") or {}).get("all_pass")
        and (row.get("phase4_gate") or {}).get("promote_action") == "warn_only_proxy"
    ]
    return {
        "n_source_weight_probes": len(source_rows),
        "n_gate_pass": len(gate_pass_rows),
        "n_hard_promote_ready": len(hard_promote_rows),
        "n_proxy_candidate_ready": len(proxy_candidate_rows),
        "best_gate_pass": compact(max(gate_pass_rows, key=lambda row: row.get("sharpe", 0.0))) if gate_pass_rows else None,
        "best_hard_promote": compact(max(hard_promote_rows, key=lambda row: row.get("sharpe", 0.0))) if hard_promote_rows else None,
        "best_proxy_candidate": compact(max(proxy_candidate_rows, key=lambda row: row.get("sharpe", 0.0))) if proxy_candidate_rows else None,
        "best_strict_sharpe": compact(max(strict_rows, key=lambda row: row.get("sharpe", 0.0))) if strict_rows else None,
    }


def _load_msaf_challenger_oos_probes(reports_dir: Path) -> list[dict]:
    """Load rejected/candidate MSAF model probes without changing live readiness."""
    rows: list[dict] = []
    probe_paths = list(reports_dir.glob("msaf_ensemble_*oos_probe_*.json"))
    probe_paths.sort()
    for path in probe_paths:
        try:
            payload = json.loads(path.read_text())
        except Exception as e:
            log.warning(f"MSAF challenger OOS probe parse failed: {path}: {e}")
            continue
        kpi = payload.get("kpi") or {}
        if not kpi:
            continue
        args = payload.get("args") or {}
        row = {
            "report": str(path),
            "model_id": args.get("lambdamart_model_id"),
            "prediction_table": payload.get("prediction_table"),
            "start": args.get("start"),
            "end": args.get("end"),
            "n_obs": int(kpi.get("n_obs") or 0),
            "n_skip": int(kpi.get("n_skip") or 0),
            "ann_ret_cagr": kpi.get("ann_ret_cagr"),
            "ann_ret_median": kpi.get("ann_ret_median"),
            "sharpe": float(kpi.get("sharpe") or 0.0),
            "max_dd": float(kpi.get("max_dd") or 0.0),
            "hit_rate": kpi.get("hit_rate"),
        }
        row["sample_ready"] = row["n_obs"] >= SHIP_N_OBS_MIN
        row["perfect_sample_ready"] = row["n_obs"] >= PERFECT_N_OBS_MIN
        row["perfect_dd_ready"] = abs(row["max_dd"]) <= PERFECT_MAX_DD_ABS_MAX
        row["perfect_sharpe_ready"] = row["perfect_sample_ready"] and row["sharpe"] >= PERFECT_SHARPE_MIN
        row["perfect_ladder_ready"] = (
            row["perfect_sample_ready"]
            and row["perfect_dd_ready"]
            and row["perfect_sharpe_ready"]
        )
        rows.append(row)
    return rows


def _gcp_controlled_idle_status() -> dict:
    status_path = REPO_ROOT / "data" / "reports" / "phase5_chain" / "status.json"
    if not status_path.exists():
        return {}
    try:
        data = json.loads(status_path.read_text())
    except Exception as e:
        log.warning(f"phase5_chain status parse failed: {e}")
        return {}
    if data.get("step") != "gcp_disabled":
        return {}
    return data


def _load_gcp_cost_summary() -> dict:
    cost_report = REPO_ROOT / "data" / "reports" / "gcp_cost_summary.json"
    if not cost_report.exists():
        return {}
    try:
        data = json.loads(cost_report.read_text())
    except Exception as e:
        log.warning(f"gcp_cost_summary parse failed: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _gcp_cost_summary_active(cost_info: dict) -> bool:
    vm_status = str(cost_info.get("vm_status") or "").upper()
    return vm_status not in {"", "TERMINATED", "STOPPED", "SUSPENDED", "UNKNOWN"}


def _score_gcp_cost_info(cost_info: dict) -> int:
    alert = cost_info.get("alert_level", "UNKNOWN")
    if alert == "OK":
        return 100
    if alert == "YELLOW":
        return 70
    return 50


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


def _load_latest_institution_eval() -> dict:
    """Load opt-in institution ensemble evaluation without changing production state."""
    reports_dir = REPO_ROOT / "data" / "reports"
    candidates = sorted(reports_dir.glob("msaf_ensemble_with_institution_eval_*.json"))
    if not candidates:
        return {}
    path = candidates[-1]
    try:
        payload = json.loads(path.read_text())
    except Exception as e:
        log.warning(f"institution eval parse failed: {path}: {e}")
        return {"path": str(path), "status": "parse_failed"}

    args = payload.get("args") or {}
    kpi = payload.get("kpi") or {}
    if not args.get("with_institution") or args.get("no_institution"):
        return {"path": str(path), "status": "not_institution_opt_in"}

    median_ann = kpi.get("ann_ret_median")
    cagr = kpi.get("ann_ret_cagr")
    sharpe = kpi.get("sharpe")
    max_dd = kpi.get("max_dd")
    n_obs = kpi.get("n_obs", 0)
    production_decision = "candidate"
    reject_reasons: list[str] = []
    if median_ann is None or median_ann < SHIP_ANN_RET_MIN:
        reject_reasons.append(f"median_ann {median_ann} < {SHIP_ANN_RET_MIN}")
    if cagr is None or cagr < 0:
        reject_reasons.append(f"cagr {cagr} < 0")
    if max_dd is None or abs(float(max_dd)) > SHIP_MAX_DD_ABS_MAX:
        reject_reasons.append(f"max_dd {max_dd} worse than -{SHIP_MAX_DD_ABS_MAX:.0%}")
    if n_obs < SHIP_N_OBS_MIN:
        reject_reasons.append(f"n_obs {n_obs} < {SHIP_N_OBS_MIN}")
    if reject_reasons:
        production_decision = "hold_reject"

    return {
        "path": str(path),
        "status": "evaluated",
        "production_decision": production_decision,
        "reject_reasons": reject_reasons,
        "kpi": {
            "ann_ret_median": median_ann,
            "ann_ret_cagr": cagr,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "n_obs": n_obs,
            "hit_rate": kpi.get("hit_rate"),
        },
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
    sources_wired = {source_name: False for source_name in SOURCES}
    sources_available = {source_name: False for source_name in SOURCES}
    con = None
    try:
        con = duckdb.connect(str(smart_db), read_only=True)
        mart_tables = [spec["mart_table"] for spec in SOURCES.values()]
        placeholders = ", ".join("?" for _ in mart_tables)
        mart_rows = con.execute(
            f"""
            SELECT table_name
              FROM information_schema.tables
             WHERE table_name IN ({placeholders})
            """,
            mart_tables,
        ).fetchall()
        existing_marts = {str(row[0]) for row in mart_rows}
        for source_name, spec in SOURCES.items():
            mart_table = spec["mart_table"]
            has_mart = mart_table in existing_marts
            ensemble_uses = mart_table in runner_text
            enabled_args = spec["enabled_args"]
            if enabled_args is None:
                arg_enabled = True  # always-on
            else:
                arg_enabled = all(
                    ensemble_args.get(k) == v for k, v in enabled_args.items()
                )
            sources_available[source_name] = has_mart and ensemble_uses
            sources_wired[source_name] = has_mart and ensemble_uses and arg_enabled
    except Exception as e:
        log.warning(f"source mart lookup failed: {e}")
    finally:
        if con is not None:
            con.close()

    source_evaluations = {}
    institution_eval = _load_latest_institution_eval()
    if institution_eval:
        source_evaluations["institution"] = institution_eval

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
        "sources_available": sources_available,
        "source_evaluations": source_evaluations,
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
    gcp_cost_info = _load_gcp_cost_summary()
    gcp_cost_report_active = _gcp_cost_summary_active(gcp_cost_info)
    gcp_controlled_idle = bool(_gcp_controlled_idle_status()) and not gcp_cost_report_active
    gcp_cost_report_ok = gcp_cost_info.get("alert_level") in {"OK", "YELLOW"}
    cost_control_satisfied = cost_loaded or gcp_controlled_idle or gcp_cost_report_ok

    # cron OR launchd counted as "loaded" (cron is FDA-free fallback)
    pct = 40 + (10 if has_step_0_cost else 0) + (10 if has_phase4_gate_real else 0) + \
          (5 if has_alpha158_check else 0) + (5 if has_promote_verdict_gated else 0) + \
          (10 if has_step5_ensemble_real else 0) + \
          (10 if has_promote_real else 0) + \
          (5 if daily_loaded else (2 if (has_daily_plist or cron_daily) else 0)) + \
          (5 if cost_control_satisfied else (2 if (has_cost_plist or cron_cost) else 0))
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
        "gcp_controlled_idle": gcp_controlled_idle,
        "gcp_cost_report_active": gcp_cost_report_active,
        "gcp_cost_report_alert": gcp_cost_info.get("alert_level"),
        "gcp_cost_report_vm_status": gcp_cost_info.get("vm_status"),
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
    """#5 GCP 成本控制: controlled-use idle state or cost_tracker budget check."""
    cost_info = _load_gcp_cost_summary()
    if _gcp_cost_summary_active(cost_info):
        pct = _score_gcp_cost_info(cost_info)
        return {
            "criterion": "GCP 成本控制",
            "pct": pct,
            "alert_level": cost_info.get("alert_level"),
            "pct_of_budget": cost_info.get("pct_of_budget"),
            "projected_month_cost": cost_info.get("projected_month_cost"),
            "remaining_budget_usd": cost_info.get("remaining_budget_usd"),
            "remaining_hours_at_spot": cost_info.get("remaining_hours_at_spot"),
            "vm_status": cost_info.get("vm_status"),
            "checked_at": cost_info.get("checked_at"),
            "policy": "controlled_use_requires_explicit_latch",
            "source": "gcp_cost_summary",
            "verdict": "PASS" if pct >= 80 else "WARN",
        }
    controlled_idle = _gcp_controlled_idle_status()
    if controlled_idle:
        return {
            "criterion": "GCP 成本控制",
            "pct": 100,
            "alert_level": "CONTROLLED_USE_IDLE",
            "pct_of_budget": None,
            "projected_month_cost": None,
            "vm_status": controlled_idle.get("status"),
            "policy": "controlled_use_requires_explicit_latch",
            "source": "phase5_chain_controlled_idle",
            "verdict": "PASS",
        }
    cost_report = REPO_ROOT / "data" / "reports" / "gcp_cost_summary.json"
    tracker_script = REPO_ROOT / "gcp" / "cost_tracker.sh"
    if not tracker_script.exists():
        return {"criterion": "GCP 成本控制", "pct": 50, "verdict": "WARN",
                "reason": "gcp/cost_tracker.sh 不存在"}

    pct = 80
    if cost_report.exists():
        pct = _score_gcp_cost_info(cost_info)

    return {
        "criterion": "GCP 成本控制",
        "pct": pct,
        "alert_level": cost_info.get("alert_level"),
        "pct_of_budget": cost_info.get("pct_of_budget"),
        "projected_month_cost": cost_info.get("projected_month_cost"),
        "remaining_budget_usd": cost_info.get("remaining_budget_usd"),
        "remaining_hours_at_spot": cost_info.get("remaining_hours_at_spot"),
        "vm_status": cost_info.get("vm_status"),
        "checked_at": cost_info.get("checked_at"),
        "source": "gcp_cost_summary" if cost_info else "tracker_script_present",
        "verdict": "PASS" if pct >= 80 else "WARN",
    }


def _load_phase4_live_evidence(phase4_report: Path, *, expected_model_id: str | None = None) -> dict:
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
        "phase4_model_id": None,
        "model_id_match": expected_model_id is None,
    }
    if not phase4_report.exists():
        return out

    try:
        d = json.loads(phase4_report.read_text())
        report_model_id = d.get("model_id")
        out["phase4_model_id"] = report_model_id
        if expected_model_id and report_model_id != expected_model_id:
            out.update({
                "pbo_reason": f"phase4 model_id mismatch: {report_model_id} != {expected_model_id}",
                "dsr_reason": f"phase4 model_id mismatch: {report_model_id} != {expected_model_id}",
                "model_id_match": False,
            })
            return out
        out["model_id_match"] = True
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
    reports_dir = REPO_ROOT / "data" / "reports"
    smart_db = REPO_ROOT / "data" / "smartmoney.duckdb"

    if not msaf_report.exists():
        return {"criterion": "实盘 GO/NO-GO", "pct": 0, "verdict": "FAIL",
                "reason": "msaf_ensemble_run.json 不存在"}

    d = json.loads(msaf_report.read_text())
    kpi = d.get("kpi", {})
    horizon_ladder = _load_msaf_horizon_ladder(reports_dir, d)
    risk_overlay_probes = _load_msaf_risk_overlay_probes(reports_dir, d)
    source_weight_probe_summary = _summarize_source_weight_probes(risk_overlay_probes)
    challenger_oos_probes = _load_msaf_challenger_oos_probes(reports_dir)
    expected_phase4_model_id = (d.get("args") or {}).get("lambdamart_model_id")
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

    phase4 = _load_phase4_live_evidence(phase4_report, expected_model_id=expected_phase4_model_id)
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
    next_milestones = list(scored["next_milestones"])
    for row in horizon_ladder:
        if row.get("primary"):
            continue
        if row["sample_ready"] and not row["perfect_ladder_ready"]:
            next_milestones.append(
                f"{row['horizon']} probe has n_obs={row['n_obs']} but "
                f"sharpe={row['sharpe']:.2f}, max_dd={row['max_dd']:.2%}; "
                "sample count alone does not solve perfect ladder"
            )
    for row in risk_overlay_probes:
        if row.get("score_filter", {}).get("min_top_score") and row["n_obs"] < SHIP_N_OBS_MIN:
            next_milestones.append(
                f"score floor probe has sharpe={row['sharpe']:.2f} but "
                f"n_obs={row['n_obs']}<{SHIP_N_OBS_MIN}; alpha-quality signal needs broader OOS"
            )
            continue
        if row.get("score_exposure", {}).get("score_exposure_floor") and not row["perfect_ladder_ready"]:
            next_milestones.append(
                f"score exposure probe preserves n_obs={row['n_obs']} but "
                f"sharpe={row['sharpe']:.2f}, max_dd={row['max_dd']:.2%}; "
                "conviction sizing alone is not enough"
            )
            continue
        if row.get("source_weight_override", {}).get("lambdamart_weight") is not None:
            gate = row.get("phase4_gate") or {}
            if gate and not gate.get("all_pass"):
                next_milestones.append(
                    f"source-weight probe has sharpe={row['sharpe']:.2f}, "
                    f"max_dd={row['max_dd']:.2%}, n_obs={row['n_obs']} "
                    f"but Phase4 gate {gate.get('promote_action') or 'failed'} "
                    f"({gate.get('pbo_reason') or 'gate failed'}); cannot promote"
                )
            elif gate and gate.get("promote_action") == "warn_only_proxy" and row["perfect_ladder_ready"]:
                next_milestones.append(
                    f"source-weight probe meets strict ladder with proxy Phase4 PASS "
                    f"(sharpe={row['sharpe']:.2f}, max_dd={row['max_dd']:.2%}, "
                    f"n_obs={row['n_obs']}); needs true train-log/OOS Phase4 gate before hard promote"
                )
            elif gate and gate.get("all_pass") and not row["perfect_ladder_ready"]:
                next_milestones.append(
                    f"source-weight probe has Phase4 proxy gate PASS but "
                    f"sharpe={row['sharpe']:.2f}, max_dd={row['max_dd']:.2%}, "
                    f"n_obs={row['n_obs']}; still short of strict perfect ladder"
                )
            elif not gate and not row["perfect_ladder_ready"]:
                next_milestones.append(
                    f"source-weight probe has sharpe={row['sharpe']:.2f}, "
                    f"max_dd={row['max_dd']:.2%}, n_obs={row['n_obs']}; "
                    "needs broader OOS and Phase4 gate before promotion"
                )
            continue
        if row["perfect_dd_ready"] and not row["perfect_sharpe_ready"]:
            probe_kind = "volatility target" if row.get("volatility_target", {}).get("target_ann_vol") else "cash overlay"
            next_milestones.append(
                f"{probe_kind} probe can satisfy max_dd "
                f"({row['max_dd']:.2%}) but sharpe={row['sharpe']:.2f}; "
                "need alpha quality or broader OOS, not only de-risking"
            )
    for row in challenger_oos_probes:
        if row["sample_ready"] and not row["perfect_ladder_ready"]:
            next_milestones.append(
                f"challenger OOS probe {row.get('model_id')} has n_obs={row['n_obs']} "
                f"but sharpe={row['sharpe']:.2f}, max_dd={row['max_dd']:.2%}; "
                "broader OOS alone does not solve live #6"
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
        "phase4_model_id": phase4["phase4_model_id"],
        "phase4_model_id_match": phase4["model_id_match"],
        "ship_baseline_passed": scored["ship_baseline_passed"],
        "perfect_ladder_ready": scored["perfect_ladder_ready"],
        "blockers": scored["blockers"],
        "next_milestones": next_milestones,
        "horizon_ladder": horizon_ladder,
        "risk_overlay_probes": risk_overlay_probes,
        "source_weight_probe_summary": source_weight_probe_summary,
        "challenger_oos_probes": challenger_oos_probes,
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
