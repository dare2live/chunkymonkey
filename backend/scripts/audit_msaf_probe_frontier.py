#!/usr/bin/env python3
"""Audit MSAF probe frontier for live GO/NO-GO promotion readiness."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "data" / "reports"

PERFECT_MIN_N_OBS = 60
PERFECT_MIN_SHARPE = 2.0
PERFECT_MAX_DD = -0.20


def _weight_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _strategy_key(
    model_id: Any,
    horizon: Any,
    source_weight_override: dict[str, Any],
    score_filter: dict[str, Any] | None = None,
    score_exposure: dict[str, Any] | None = None,
    position_sizing: dict[str, Any] | None = None,
    max_positions: Any = None,
    cash_overlay: dict[str, Any] | None = None,
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


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _gate_passed(gate_result: dict[str, Any], name: str) -> bool | None:
    section = gate_result.get(name) or {}
    if "passes" in section:
        return bool(section.get("passes"))
    if "passed" in section:
        return bool(section.get("passed"))
    return None


def _horizon_from_payload_or_name(payload: dict[str, Any], path: Path) -> str:
    kpi_horizon = ((payload.get("args") or {}).get("horizon") or payload.get("horizon"))
    if kpi_horizon:
        return str(kpi_horizon)
    match = re.search(r"_h(5|10|20)_", path.name)
    return f"{match.group(1)}d" if match else "20d"


def load_phase4_gates(reports_dir: Path) -> dict[tuple[Any, ...], dict[str, Any]]:
    gates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for path in sorted(reports_dir.glob("phase4_gate_msaf_*probe_*.json")):
        payload = _load_json(path)
        if not payload:
            continue
        source_weight_override = payload.get("source_weight_override") or {}
        key = _strategy_key(
            payload.get("model_id"),
            payload.get("primary_horizon"),
            source_weight_override,
            payload.get("score_filter"),
            payload.get("score_exposure"),
            payload.get("position_sizing"),
            payload.get("max_positions"),
            payload.get("cash_overlay"),
        )
        if not key:
            continue
        gate_result = payload.get("gate_result") or {}
        gates[key] = {
            "path": str(path),
            "all_pass": bool(gate_result.get("all_pass")),
            "promote_action": gate_result.get("promote_action"),
            "pbo_passed": _gate_passed(gate_result, "pbo"),
            "pbo_value": ((gate_result.get("pbo") or {}).get("detail") or {}).get("pbo"),
            "pbo_reason": (gate_result.get("pbo") or {}).get("reason"),
            "dsr_passed": _gate_passed(gate_result, "dsr"),
            "conservative_passed": _gate_passed(gate_result, "conservative"),
            "is_oos_passed": _gate_passed(gate_result, "is_oos"),
            "is_oos_proxy_mode": payload.get("is_oos_proxy_mode"),
        }
        gates[key]["hard_promote"] = gates[key]["promote_action"] == "promote"
        gates[key]["proxy_pass"] = (
            gates[key]["all_pass"] and gates[key]["promote_action"] == "warn_only_proxy"
        )
    return gates


def load_msaf_probes(reports_dir: Path) -> list[dict[str, Any]]:
    gates = load_phase4_gates(reports_dir)
    rows: list[dict[str, Any]] = []
    for path in sorted(reports_dir.glob("msaf_ensemble*probe*.json")):
        payload = _load_json(path)
        if not payload:
            continue
        kpi = payload.get("kpi") or {}
        source_weight_override = payload.get("source_weight_override") or {}
        score_filter = payload.get("score_filter") or {}
        score_exposure = payload.get("score_exposure") or {}
        position_sizing = payload.get("position_sizing") or {}
        cash_overlay = payload.get("cash_overlay") or {}
        horizon = _horizon_from_payload_or_name(payload, path)
        model_id = (payload.get("args") or {}).get("lambdamart_model_id") or payload.get("model_id")
        n_obs = _safe_int(kpi.get("n_obs"))
        sharpe = _safe_float(kpi.get("sharpe"))
        max_dd = _safe_float(kpi.get("max_dd"))
        perfect_ladder_ready = (
            n_obs is not None
            and sharpe is not None
            and max_dd is not None
            and n_obs >= PERFECT_MIN_N_OBS
            and sharpe >= PERFECT_MIN_SHARPE
            and max_dd >= PERFECT_MAX_DD
        )
        max_positions = (payload.get("args") or {}).get("max_positions")
        key = _strategy_key(
            model_id,
            horizon,
            source_weight_override,
            score_filter,
            score_exposure,
            position_sizing,
            max_positions,
            cash_overlay,
        )
        gate = gates.get(key) if key else None
        gate_all_pass = bool(gate and gate.get("all_pass"))
        gate_hard_promote = bool(gate and gate.get("hard_promote"))
        gate_proxy_pass = bool(gate and gate.get("proxy_pass"))
        rows.append(
            {
                "path": str(path),
                "model_id": model_id,
                "horizon": horizon,
                "source_weight_override": source_weight_override,
                "score_filter": score_filter,
                "score_exposure": score_exposure,
                "position_sizing": position_sizing,
                "cash_overlay": cash_overlay,
                "max_positions": _safe_int(max_positions),
                "n_obs": n_obs,
                "sharpe": sharpe,
                "max_dd": max_dd,
                "ann_ret_cagr": _safe_float(kpi.get("ann_ret_cagr")),
                "perfect_ladder_ready": perfect_ladder_ready,
                "phase4_gate": gate,
                "gate_all_pass": gate_all_pass,
                "gate_hard_promote": gate_hard_promote,
                "gate_proxy_pass": gate_proxy_pass,
                "promotable": bool(perfect_ladder_ready and gate_hard_promote),
                "proxy_candidate_ready": bool(perfect_ladder_ready and gate_proxy_pass),
                "sharpe_gap_to_perfect": None if sharpe is None else max(0.0, PERFECT_MIN_SHARPE - sharpe),
                "max_dd_gap_to_perfect": None if max_dd is None else max(0.0, PERFECT_MAX_DD - max_dd),
                "n_obs_gap_to_perfect": None if n_obs is None else max(0, PERFECT_MIN_N_OBS - n_obs),
            }
        )
    return rows


def summarize_frontier(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_rows = [
        row for row in rows
        if row.get("source_weight_override", {}).get("lambdamart_weight") is not None
    ]
    gate_pass = [row for row in source_rows if row.get("gate_all_pass")]
    strict_ready = [row for row in source_rows if row.get("perfect_ladder_ready")]
    promotable = [row for row in source_rows if row.get("promotable")]
    proxy_candidate_ready = [row for row in source_rows if row.get("proxy_candidate_ready")]
    strict_blocked_by_pbo = [
        row for row in strict_ready
        if (row.get("phase4_gate") or {}).get("pbo_passed") is False
    ]
    strict_nearest_pbo_pass = min(
        strict_blocked_by_pbo,
        key=lambda row: (row.get("phase4_gate") or {}).get("pbo_value") or 999.0,
        default=None,
    )
    gate_pass_nearest_strict = min(
        gate_pass,
        key=lambda row: (
            row.get("sharpe_gap_to_perfect") or 0.0,
            row.get("max_dd_gap_to_perfect") or 0.0,
            row.get("n_obs_gap_to_perfect") or 0,
        ),
        default=None,
    )
    best_gate_pass = max(gate_pass, key=lambda row: (row.get("sharpe") or -999.0), default=None)
    best_proxy_candidate = max(
        proxy_candidate_ready,
        key=lambda row: (row.get("sharpe") or -999.0),
        default=None,
    )
    best_strict = max(strict_ready, key=lambda row: (row.get("sharpe") or -999.0), default=None)
    best_overall_sharpe = max(source_rows, key=lambda row: (row.get("sharpe") or -999.0), default=None)
    strict_pbo_values = [
        (row.get("phase4_gate") or {}).get("pbo_value")
        for row in strict_blocked_by_pbo
        if (row.get("phase4_gate") or {}).get("pbo_value") is not None
    ]
    return {
        "n_source_weight_probes": len(source_rows),
        "n_gate_pass": len(gate_pass),
        "n_perfect_ladder_ready": len(strict_ready),
        "n_strict_blocked_by_pbo": len(strict_blocked_by_pbo),
        "n_promotable": len(promotable),
        "n_proxy_candidate_ready": len(proxy_candidate_ready),
        "promotable": promotable,
        "proxy_candidate_ready": proxy_candidate_ready,
        "strict_blocked_by_pbo": strict_blocked_by_pbo,
        "strict_nearest_pbo_pass": strict_nearest_pbo_pass,
        "strict_min_pbo_value": min(strict_pbo_values) if strict_pbo_values else None,
        "strict_min_pbo_gap_to_pass": (
            max(0.0, min(strict_pbo_values) - 0.20) if strict_pbo_values else None
        ),
        "gate_pass_nearest_strict_ladder": gate_pass_nearest_strict,
        "best_gate_pass": best_gate_pass,
        "best_proxy_candidate_ready": best_proxy_candidate,
        "best_strict_ladder": best_strict,
        "best_overall_sharpe": best_overall_sharpe,
        "verdict": (
            "PASS"
            if promotable
            else "PROXY_READY_NEEDS_TRUE_IS_OOS"
            if proxy_candidate_ready
            else "NO_PROMOTABLE_PROBE"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit MSAF source-weight probe frontier")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--json-out", type=Path, default=REPORTS_DIR / "msaf_probe_frontier_audit.json")
    args = parser.parse_args(argv)

    rows = load_msaf_probes(args.reports_dir)
    summary = summarize_frontier(rows)
    payload = {"summary": summary, "rows": rows}
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
