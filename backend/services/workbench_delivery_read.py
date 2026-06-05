"""Workbench read model for operational GO/NO-GO delivery status."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _criterion_map(readiness: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in readiness.get("criteria") or []:
        if isinstance(row, dict) and row.get("criterion"):
            out[str(row["criterion"])] = row
    return out


def _gate_summary(gate_payload: dict[str, Any]) -> dict[str, Any]:
    gate = gate_payload.get("gate_result") or {}
    return {
        "model_id": gate_payload.get("model_id"),
        "challenger_id": gate_payload.get("challenger_id"),
        "run_at": gate_payload.get("run_at"),
        "promote_action": gate.get("promote_action"),
        "all_pass": gate.get("all_pass"),
        "n_obs_20d": gate_payload.get("n_obs_20d"),
        "n_obs_5d": gate_payload.get("n_obs_5d"),
        "pbo": _single_gate(gate.get("pbo")),
        "dsr": _single_gate(gate.get("dsr")),
        "conservative": _single_gate(gate.get("conservative")),
        "is_oos": _single_gate(gate.get("is_oos")),
    }


def _single_gate(row: Any) -> dict[str, Any]:
    row = row or {}
    detail = row.get("detail") or {}
    return {
        "passes": row.get("passes"),
        "reason": row.get("reason"),
        "p_conf": detail.get("p_conf"),
        "proxy_mode": detail.get("proxy_mode"),
    }


def build_workbench_delivery_readiness(
    *,
    repo_root: str | Path = REPO_ROOT,
    challenger_model_id: str = "lgbm_phase5_gcp_20260520T010718",
) -> dict[str, Any]:
    root = Path(repo_root)
    reports = root / "data" / "reports"
    readiness = _read_json(reports / "delivery_readiness.json")
    criteria = _criterion_map(readiness)
    strategy = criteria.get("策略模型管理", {})
    live = criteria.get("实盘 GO/NO-GO", {})
    backtester = criteria.get("backtester gate", {})
    compute_backend = criteria.get("计算后端控制", {})
    decision = _read_json(reports / f"decision_{challenger_model_id}.json")
    challenger_gate = _read_json(reports / f"phase4_gate_{challenger_model_id}.json")
    live_gate = _read_json(reports / "phase4_gate_result.json")
    institution_eval = (strategy.get("source_evaluations") or {}).get("institution") or {}

    blockers = []
    for item in live.get("blockers") or []:
        blockers.append({"scope": "live", "text": item})
    for item in live.get("next_milestones") or []:
        blockers.append({"scope": "milestone", "text": item})
    for item in decision.get("fail_reasons") or []:
        blockers.append({"scope": "challenger", "text": item})
    for item in institution_eval.get("reject_reasons") or []:
        blockers.append({"scope": "institution", "text": item})

    ready = bool(readiness.get("ready_for_delivery"))
    return {
        "ok": True,
        "ready_for_delivery": ready,
        "verdict": "READY" if ready else "NOT_READY",
        "avg_pct": readiness.get("avg_pct"),
        "criteria": readiness.get("criteria") or [],
        "live_go_no_go": {
            "pct": live.get("pct"),
            "verdict": live.get("verdict"),
            "ship_baseline_passed": live.get("ship_baseline_passed"),
            "perfect_ladder_ready": live.get("perfect_ladder_ready"),
            "msaf_n_obs": live.get("msaf_n_obs"),
            "msaf_sharpe": live.get("msaf_sharpe"),
            "msaf_max_dd": live.get("msaf_max_dd"),
            "msaf_effective_ann": live.get("msaf_effective_ann"),
            "phase4_promote_action": live.get("phase4_promote_action"),
            "phase4_pbo_passed": live.get("phase4_pbo_passed"),
            "phase4_dsr_conf": live.get("phase4_dsr_conf"),
            "phase4_is_oos_passed": live.get("phase4_is_oos_passed"),
            "phase4_is_oos_proxy_mode": live.get("phase4_is_oos_proxy_mode"),
            "next_milestones": live.get("next_milestones") or [],
            "horizon_ladder": live.get("horizon_ladder") or [],
            "risk_overlay_probes": live.get("risk_overlay_probes") or [],
            "source_weight_probe_summary": live.get("source_weight_probe_summary") or {},
            "challenger_oos_probes": live.get("challenger_oos_probes") or [],
        },
        "backtester": {
            "pct": backtester.get("pct"),
            "phase4_promote_action": backtester.get("phase4_promote_action"),
            "phase4_pct": backtester.get("phase4_pct"),
            "p3_passed": backtester.get("p3_passed"),
        },
        "sources": {
            "wired": strategy.get("sources_wired") or {},
            "available": strategy.get("sources_available") or {},
            "institution_evaluation": institution_eval,
        },
        "challenger": {
            "model_id": challenger_model_id,
            "decision": decision,
            "gate": _gate_summary(challenger_gate) if challenger_gate else {},
        },
        "live_gate": _gate_summary(live_gate) if live_gate else {},
        "compute_backend": {
            "active_backends": compute_backend.get("active_backends") or [],
            "planned_backends": compute_backend.get("planned_backends") or [],
            "job_families": compute_backend.get("job_families") or [],
            "source": compute_backend.get("source"),
        },
        "blockers": blockers,
        "read_model": {
            "endpoint": "/api/workbench/delivery-readiness",
            "source_mode": "local_reports",
            "reports": [
                str(reports / "delivery_readiness.json"),
                str(reports / "phase4_gate_result.json"),
                str(reports / f"phase4_gate_{challenger_model_id}.json"),
                str(reports / f"decision_{challenger_model_id}.json"),
            ],
        },
    }
