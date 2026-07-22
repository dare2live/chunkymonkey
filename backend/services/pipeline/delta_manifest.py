"""CX-1/CX-2 typed delta manifest + process plan + latency budget evaluation.

Single calculation point for acquire→process selective recompute decisions.
Does NOT build a DAG/event-bus: linear run.py remains the orchestrator; this
module is a typed audit artifact consumed by process + store + workbench.

CX-2: ``delta.state_changes`` is populated by read-only sensors
(``state_sensors``) — never a Tier0 writer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .state_sensors import any_state_changed, state_change_force_reasons

REPO = Path(__file__).resolve().parents[3]
BUDGETS_PATH = REPO / "backend/config/pipeline_latency_budgets.yaml"
DC_AS_OF_PATH = REPO / "data/reports/dc_industry_view_as_of.json"
SCHEMA_VERSION = 1

# Domains whose drain/accept advance forces DC industry view rebuild.
_DEFAULT_DC_PROVENANCE = frozenset(
    {
        "dc_index",
        "dc_member",
        "dc_daily",
        "sync:dc_index",
        "sync:dc_member",
        "sync:dc_daily",
    }
)


def empty_manifest(*, run_date: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_date": run_date,
        "acquire_summary": {
            "drain": [],
            "formal": [],
            "incremental": [],
            "dim_refresh": [],
        },
        "delta": {
            "advanced_partitions": [],
            "dc_source_frontier": None,
            "dc_frontier_advanced": None,
            "late_window_policy": "always_run",
            "state_changes": {},
        },
        "process_plan": {},
        "process_outcome": {},
        "stage_timing_s": {},
        "latency_budgets": {},
        "budget_status": {},
    }


def load_latency_budgets(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or BUDGETS_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    budgets = dict(raw.get("budgets_s") or {})
    provenance = (
        raw.get("dc_provenance_domains")
        or raw.get("DC_PROVENANCE_DOMAINS")
        or sorted(_DEFAULT_DC_PROVENANCE)
    )
    return {
        "schema_version": int(raw.get("schema_version") or 1),
        "budgets_s": budgets,
        "dc_provenance_domains": frozenset(str(x) for x in provenance),
    }


def read_dc_as_of(path: Path | None = None) -> str | None:
    marker = path or DC_AS_OF_PATH
    if not marker.exists():
        return None
    try:
        import json

        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    frontier = data.get("source_frontier")
    return str(frontier) if frontier else None


def write_dc_as_of(source_frontier: str, *, path: Path | None = None) -> None:
    import json
    from datetime import datetime, timezone

    marker = path or DC_AS_OF_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "source_frontier": str(source_frontier),
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _domain_advanced(item: dict[str, Any]) -> bool:
    """True when a drain/formal record implies published/accepted content advanced."""
    action = str(item.get("action") or item.get("status") or "").lower()
    if action in {"accepted", "drained", "land_then_accept"}:
        return True
    if action == "ok" and int(item.get("refilled_rows") or 0) > 0:
        return True
    if int(item.get("refilled_rows") or 0) > 0:
        return True
    if int(item.get("rows_written") or 0) > 0:
        return True
    return False


def build_advanced_partitions(
    *,
    formal: list[dict[str, Any]],
    drain: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in formal:
        if str(row.get("action") or "") != "accepted":
            continue
        out.append(
            {
                "dataset_id": row.get("dataset_id"),
                "partition_value": row.get("eligible_end"),
                "provenance": f"formal:{row.get('domain')}",
                "domain": row.get("domain"),
            }
        )
    for row in drain:
        if not _domain_advanced(row):
            continue
        domain = str(row.get("domain") or row.get("name") or "")
        out.append(
            {
                "dataset_id": row.get("dataset_id"),
                "partition_value": row.get("partition_value")
                or row.get("eligible_end")
                or row.get("max_date"),
                "provenance": f"drain:{domain}" if domain else "drain",
                "domain": domain,
                "refilled_rows": row.get("refilled_rows"),
            }
        )
    return out


def dc_provenance_hit(
    advanced: list[dict[str, Any]],
    *,
    provenance_domains: frozenset[str] | None = None,
) -> bool:
    domains = provenance_domains or _DEFAULT_DC_PROVENANCE
    for row in advanced:
        domain = str(row.get("domain") or "")
        prov = str(row.get("provenance") or "")
        tokens = {domain, prov, prov.split(":", 1)[-1] if ":" in prov else prov}
        if tokens & set(domains):
            return True
        # also match bare names inside provenance like drain:dc_member
        for token in list(tokens):
            for d in domains:
                if d in token:
                    return True
    return False


def decide_dc_action(
    *,
    current_frontier: str | None,
    previous_frontier: str | None,
    advanced_partitions: list[dict[str, Any]],
    provenance_domains: frozenset[str] | None = None,
    force_run: bool = False,
) -> dict[str, Any]:
    """Return {action, reason, dc_frontier_advanced} for build_dc_industry_view."""
    if force_run:
        return {
            "action": "run",
            "reason": "force_run",
            "dc_frontier_advanced": True,
        }
    if current_frontier is None:
        return {
            "action": "run",
            "reason": "dc_frontier_unknown",
            "dc_frontier_advanced": None,
        }
    if previous_frontier is None:
        return {
            "action": "run",
            "reason": "dc_as_of_missing_first_publish",
            "dc_frontier_advanced": True,
        }
    frontier_advanced = str(current_frontier) != str(previous_frontier)
    if frontier_advanced:
        return {
            "action": "run",
            "reason": "dc_frontier_advanced",
            "dc_frontier_advanced": True,
        }
    if dc_provenance_hit(advanced_partitions, provenance_domains=provenance_domains):
        return {
            "action": "run",
            "reason": "dc_provenance_advanced",
            "dc_frontier_advanced": False,
        }
    return {
        "action": "skip",
        "reason": "dc_frontier_unchanged",
        "dc_frontier_advanced": False,
    }


def plan_process_steps(
    *,
    dc_decision: dict[str, Any],
    state_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Typed process plan. Pulse late window is ALWAYS run (kill criterion).

    CX-2: when state sensors report changes, segments/technical_states cite
    ``state_change:*`` reasons so future skip logic cannot silently ignore
    ST/holder/delist. DC skip remains frontier-driven (ST ≠ DC provenance).
    T+1 / limit / suspend constraints are untouched here.
    """
    force = state_change_force_reasons(state_changes)
    st_or_delist = bool(
        (state_changes or {}).get("stock_st", {}).get("changed")
        or (state_changes or {}).get("delist", {}).get("changed")
    )
    seg_reason = (
        "state_change_triggered:" + ",".join(force)
        if st_or_delist
        else "build_latest_idempotent"
    )
    form_reason = (
        "state_change_triggered:" + ",".join(force)
        if st_or_delist
        else "build_latest_idempotent"
    )
    return {
        "dc_industry_view": {
            "action": dc_decision["action"],
            "reason": dc_decision["reason"],
        },
        "segments": {
            "action": "run",
            "reason": seg_reason,
        },
        "market_pulse": {
            "action": "run",
            "reason": "late_window_mandatory",
        },
        "technical_states": {
            "action": "run",
            "reason": form_reason,
        },
        "state_change_force": force,
        "any_state_changed": any_state_changed(state_changes),
    }


def evaluate_budget_status(
    *,
    stage_timing_s: dict[str, Any],
    process_plan: dict[str, Any],
    budgets_s: dict[str, Any],
) -> dict[str, str]:
    """Observational budget status (pass/fail/unknown). Never aborts the chain."""
    status: dict[str, str] = {}
    process_s = stage_timing_s.get("process")
    clean_s = stage_timing_s.get("clean")
    acquire_s = stage_timing_s.get("acquire")

    dc_action = str((process_plan.get("dc_industry_view") or {}).get("action") or "")
    if process_s is None:
        status["process"] = "unknown"
    elif dc_action == "skip":
        budget = budgets_s.get("process_empty_increment_s")
        status["process"] = (
            "pass"
            if budget is not None and float(process_s) <= float(budget)
            else ("fail" if budget is not None else "unknown")
        )
    else:
        budget = budgets_s.get("process_with_dc_rebuild_s")
        status["process"] = (
            "pass"
            if budget is not None and float(process_s) <= float(budget)
            else ("fail" if budget is not None else "unknown")
        )

    clean_budget = budgets_s.get("clean_qfq_from_accepted_s")
    if clean_s is None or clean_budget is None:
        status["clean"] = "unknown"
    else:
        status["clean"] = "pass" if float(clean_s) <= float(clean_budget) else "fail"

    acquire_budget = budgets_s.get("acquire_soft_ceiling_s")
    if acquire_s is None or acquire_budget is None:
        status["acquire"] = "unknown"
    else:
        status["acquire"] = (
            "pass" if float(acquire_s) <= float(acquire_budget) else "fail"
        )
    return status


def finalize_manifest_for_report(
    manifest: dict[str, Any] | None,
    *,
    stage_timing_s: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge timings/budgets into a report-ready copy."""
    out = dict(manifest or empty_manifest(run_date=""))
    budgets = load_latency_budgets()
    timings = dict(stage_timing_s or out.get("stage_timing_s") or {})
    out["stage_timing_s"] = timings
    out["latency_budgets"] = dict(budgets["budgets_s"])
    out["budget_status"] = evaluate_budget_status(
        stage_timing_s=timings,
        process_plan=dict(out.get("process_plan") or {}),
        budgets_s=budgets["budgets_s"],
    )
    return out


def probe_dc_source_frontier() -> str | None:
    """Read-only MAX(trade_date) probe for DC raw frontier. None on any failure."""
    try:
        from services.database_manifest import get_database_manifest
        from services.duck_adapter import connect
        from services.taxonomy_config import source_index_type
    except Exception:
        return None
    try:
        manifest = get_database_manifest()
        smart = str(manifest.path_for("smartmoney"))
        traw = str(manifest.path_for("tushare_raw"))
        conn = connect(
            smart,
            read_only=True,
            attach={"traw": {"path": traw, "read_only": True}},
        )
    except Exception:
        return None
    try:
        industry_type = source_index_type("dc_industry")
        concept_type = source_index_type("dc_concept")
        industry_last, concept_last = conn.execute(
            """
            SELECT MAX(CASE WHEN idx_type = ? THEN trade_date END),
                   MAX(CASE WHEN idx_type = ? THEN trade_date END)
            FROM traw.raw_tushare_dc_index
            WHERE idx_type IN (?, ?)
            """,
            [industry_type, concept_type, industry_type, concept_type],
        ).fetchone()
        member_last = conn.execute(
            "SELECT MAX(trade_date) FROM traw.raw_tushare_dc_member"
        ).fetchone()[0]
        frontier = (industry_last, concept_last, member_last)
        if any(value is None for value in frontier) or len(set(frontier)) != 1:
            return None
        return str(industry_last)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:  # rule-compliance: ok evidence=best-effort close; probe returns None on any failure
            pass
