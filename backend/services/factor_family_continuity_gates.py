"""Frequency-typed continuity / readiness gate matrix for factor families (RX prereg).

Authority: analysis/factor_family_governance_toplevel_20260724.md

v1 scope (structural + sync_registry wire; no DuckDB):
  - each family declares continuity_gate.mode aligned with frequency + availability_axis
  - trade_date daily families → calendar_gaps wire or typed defer (never silent shorten)
  - event / quarterly_period → forbid calendar_gaps full-trade-calendar expectation
  - defer/blocked families → inventory_defer|inventory_blocked|typed defer with reason
  - gate_matrix check ids map to known continuity categories
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.factor_family_inventory import (
    DEFAULT_INVENTORY,
    DEFAULT_SYNC_REGISTRY,
    FactorFamilyInventory,
    load_inventory,
)

DAILY_BATCH_MODES = frozenset({"by_trade_date", "by_date_range"})

CONTINUITY_MODES = frozenset(
    {
        "calendar_gaps",
        "derive_accepted_frontier",
        "event_notice_partitions",
        "period_gap_bounded",
        "type_b_publish_defer",
        "inventory_defer",
        "inventory_blocked",
    }
)

TRADE_DATE_AXIS_MARKERS = (
    "trade_date",
    "trade_date_eod",
)

EVENT_AXIS_MARKERS = (
    "notice_date",
    "ann_date",
)

PERIOD_AXIS_MARKERS = (
    "report_period",
    "plannable",
)

REQUIRED_CONTINUITY_GATE_FIELDS = frozenset({"mode"})

# gate_matrix `check` → continuity category (structural wiring SSOT)
GATE_CHECK_CONTINUITY_KIND = {
    "formal_daily_accepted_frontier_covers_snapshot_end": "calendar_gaps",
    "serve_derive_stock_form_wired": "derive_accepted_frontier",
    "market_context_decision_time_path": "derive_accepted_frontier",
    "type_b_moneyflow_fact_frontier": "type_b_publish_defer",
    "holders_notice_partition_holes_zero_in_window": "event_notice_partitions",
    "org_accepted_population_floor": "period_gap_bounded",
}

# Per-gate family mode expectations (multi-family gates may mix modes).
GATE_CHECK_FAMILY_MODES: dict[str, dict[str, str]] = {
    "formal_daily_accepted_frontier_covers_snapshot_end": {
        "price_volume_daily": "calendar_gaps",
    },
    "serve_derive_stock_form_wired": {
        "stock_state_form": "derive_accepted_frontier",
    },
    "market_context_decision_time_path": {
        "market_sensing_breadth": "derive_accepted_frontier",
    },
    "type_b_moneyflow_fact_frontier": {
        "vendor_flow_proxy": "type_b_publish_defer",
    },
    "holders_notice_partition_holes_zero_in_window": {
        "disclosure_holders_event": "event_notice_partitions",
    },
    "org_accepted_population_floor": {
        "org_disclosure_period": "period_gap_bounded",
    },
}


@dataclass(frozen=True)
class FamilyContinuityRow:
    family_id: str
    frequency: str
    availability_axis: str
    stack_eligibility: str
    mode: str
    wired: str | None
    defer_reason: str | None
    blocked_reason: str | None


def _load_sync_domains(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or DEFAULT_SYNC_REGISTRY
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    domains = raw.get("domains") if isinstance(raw, dict) else None
    if not isinstance(domains, dict):
        return {}
    return {str(k): v for k, v in domains.items() if isinstance(v, dict)}


def _continuity_block(spec: dict[str, Any]) -> dict[str, Any] | None:
    block = spec.get("continuity_gate")
    if block is None:
        return None
    if not isinstance(block, dict):
        return None
    return block


def infer_expected_mode(
    family_id: str,
    spec: dict[str, Any],
) -> str:
    """Derive expected continuity mode from frequency semantics (fail-closed on mismatch)."""
    freq = spec.get("frequency")
    axis = str(spec.get("availability_axis") or "")
    se = spec.get("stack_eligibility")

    if se == "blocked":
        return "inventory_blocked"
    if se == "defer":
        if family_id == "vendor_flow_proxy":
            return "type_b_publish_defer"
        if family_id == "org_disclosure_period":
            return "period_gap_bounded"
        return "inventory_defer"

    if freq == "on_demand":
        return "inventory_blocked"

    if freq == "quarterly_period":
        return "period_gap_bounded"

    if freq == "event":
        return "event_notice_partitions"

    if freq == "daily":
        if any(m in axis for m in EVENT_AXIS_MARKERS + PERIOD_AXIS_MARKERS):
            if not _axis_mentions_trade_date(axis):
                return "inventory_defer"
        sync_domains = spec.get("sync_domains") or []
        if sync_domains and _axis_mentions_trade_date(axis):
            return "calendar_gaps"
        if "decision_time" in axis or not sync_domains:
            return "derive_accepted_frontier"
        if _axis_mentions_trade_date(axis):
            return "calendar_gaps"
        return "derive_accepted_frontier"

    return "inventory_defer"


def _axis_mentions_trade_date(axis: str) -> bool:
    return any(m in axis for m in TRADE_DATE_AXIS_MARKERS)


def family_continuity_rows(
    inv: FactorFamilyInventory | None = None,
) -> list[FamilyContinuityRow]:
    inv = inv or load_inventory()
    rows: list[FamilyContinuityRow] = []
    for family_id, spec in inv.families.items():
        if not isinstance(spec, dict):
            continue
        block = _continuity_block(spec) or {}
        mode = str(block.get("mode") or infer_expected_mode(family_id, spec))
        rows.append(
            FamilyContinuityRow(
                family_id=family_id,
                frequency=str(spec.get("frequency") or ""),
                availability_axis=str(spec.get("availability_axis") or ""),
                stack_eligibility=str(spec.get("stack_eligibility") or ""),
                mode=mode,
                wired=(
                    str(block.get("wired"))
                    if block.get("wired") is not None
                    else None
                ),
                defer_reason=(
                    str(spec.get("defer_reason"))
                    if spec.get("defer_reason")
                    else None
                ),
                blocked_reason=(
                    str(spec.get("blocked_reason"))
                    if spec.get("blocked_reason")
                    else None
                ),
            )
        )
    return rows


def _forbidden_calendar_gaps(freq: str, axis: str) -> bool:
    if freq in {"event", "quarterly_period", "on_demand"}:
        return True
    if any(m in axis for m in EVENT_AXIS_MARKERS + PERIOD_AXIS_MARKERS):
        if "trade_date" not in axis:
            return True
    return False


def collect_gate_violations(
    inv: FactorFamilyInventory | None = None,
    *,
    sync_registry_path: Path | None = None,
) -> list[str]:
    inv = inv or load_inventory()
    viol: list[str] = []
    sync_domains = _load_sync_domains(sync_registry_path)

    for family_id, spec in inv.families.items():
        if not isinstance(spec, dict):
            viol.append(f"family {family_id}: body must be mapping")
            continue

        block = _continuity_block(spec)
        if block is None:
            viol.append(
                f"family {family_id}: missing continuity_gate (frequency-typed matrix required)"
            )
            continue
        if not isinstance(block, dict):
            viol.append(f"family {family_id}: continuity_gate must be mapping")
            continue
        missing = REQUIRED_CONTINUITY_GATE_FIELDS - set(block.keys())
        if missing:
            viol.append(
                f"family {family_id}: continuity_gate missing {sorted(missing)}"
            )

        mode = block.get("mode")
        if mode not in CONTINUITY_MODES:
            viol.append(
                f"family {family_id}: invalid continuity_gate.mode {mode!r}"
            )
            continue

        expected = infer_expected_mode(family_id, spec)
        if mode != expected:
            viol.append(
                f"family {family_id}: continuity_gate.mode={mode!r} "
                f"≠ frequency-typed expected {expected!r}"
            )

        freq = spec.get("frequency")
        axis = str(spec.get("availability_axis") or "")
        if freq == "daily" and any(
            m in axis for m in EVENT_AXIS_MARKERS + PERIOD_AXIS_MARKERS
        ):
            if not _axis_mentions_trade_date(axis):
                viol.append(
                    f"family {family_id}: frequency=daily incompatible with "
                    f"event/period axis {axis!r} without trade_date"
                )

        if mode == "calendar_gaps" and _forbidden_calendar_gaps(str(freq), axis):
            viol.append(
                f"family {family_id}: calendar_gaps forbidden for "
                f"frequency={freq!r} axis={axis!r}"
            )

        if mode != "calendar_gaps" and _axis_mentions_trade_date(axis):
            if freq == "daily" and spec.get("sync_domains") and mode not in {
                "derive_accepted_frontier",
                "type_b_publish_defer",
                "inventory_defer",
            }:
                pass  # trade_date daily acquire may use calendar_gaps only

        se = spec.get("stack_eligibility")
        if se == "defer" and mode not in {
            "inventory_defer",
            "type_b_publish_defer",
            "period_gap_bounded",
        }:
            viol.append(
                f"family {family_id}: stack_eligibility=defer requires "
                f"typed defer continuity mode (got {mode!r})"
            )
        if se == "defer" and not spec.get("defer_reason"):
            viol.append(
                f"family {family_id}: defer stack requires defer_reason"
            )
        if se == "blocked" and mode != "inventory_blocked":
            viol.append(
                f"family {family_id}: stack_eligibility=blocked requires "
                f"inventory_blocked (got {mode!r})"
            )
        if se == "blocked" and not spec.get("blocked_reason"):
            viol.append(
                f"family {family_id}: blocked stack requires blocked_reason"
            )

        if mode == "calendar_gaps":
            domains = spec.get("sync_domains") or []
            if not domains:
                viol.append(
                    f"family {family_id}: calendar_gaps requires non-empty sync_domains"
                )
            for dom in domains:
                entry = sync_domains.get(str(dom))
                if entry is None:
                    if str(dom) in {"holders_aif10", "org_holding"}:
                        continue
                    viol.append(
                        f"family {family_id}: calendar_gaps sync_domain {dom!r} "
                        "not in sync_registry (miaoxiang acquire must declare wired:)"
                    )
                    continue
                bm = entry.get("batch_mode")
                if bm not in DAILY_BATCH_MODES:
                    viol.append(
                        f"family {family_id}: sync_domain {dom!r} batch_mode={bm!r} "
                        "≠ by_trade_date (calendar_gaps wire mismatch)"
                    )

        if mode in {
            "event_notice_partitions",
            "period_gap_bounded",
        }:
            for dom in spec.get("sync_domains") or []:
                entry = sync_domains.get(str(dom))
                if entry is None:
                    continue
                bm = entry.get("batch_mode")
                if bm not in DAILY_BATCH_MODES:
                    continue
                if mode == "event_notice_partitions":
                    gt = entry.get("gap_tolerance")
                    if gt != "event_sparse":
                        viol.append(
                            f"family {family_id}: event family must not imply "
                            f"full trade calendar on {dom!r} "
                            f"(batch_mode={bm!r}; need event_sparse or non-daily wire)"
                        )
                if mode == "period_gap_bounded":
                    viol.append(
                        f"family {family_id}: period family must not wire "
                        f"trade-calendar domain {dom!r} (batch_mode={bm!r})"
                    )

        wired = block.get("wired")
        if mode == "calendar_gaps" and not wired:
            viol.append(
                f"family {family_id}: calendar_gaps requires continuity_gate.wired"
            )
        if mode in {
            "inventory_defer",
            "type_b_publish_defer",
            "inventory_blocked",
            "period_gap_bounded",
        }:
            if se in {"defer", "blocked"} and not wired:
                viol.append(
                    f"family {family_id}: defer/blocked continuity requires "
                    "continuity_gate.wired typed reason path"
                )

    for i, row in enumerate(inv.gate_matrix):
        if not isinstance(row, dict):
            continue
        check = row.get("check")
        if check not in GATE_CHECK_CONTINUITY_KIND:
            viol.append(
                f"gate_matrix[{i}] check={check!r}: unknown continuity kind "
                "(add to GATE_CHECK_CONTINUITY_KIND)"
            )
            continue
        expected_by_family = GATE_CHECK_FAMILY_MODES.get(check) or {}
        for fid, exp_mode in expected_by_family.items():
            spec = inv.families.get(fid)
            if not isinstance(spec, dict):
                viol.append(
                    f"gate_matrix[{i}] {check}: missing family {fid!r}"
                )
                continue
            block = _continuity_block(spec) or {}
            mode = block.get("mode") or infer_expected_mode(fid, spec)
            if mode != exp_mode:
                viol.append(
                    f"gate_matrix[{i}] {check}: family {fid} mode={mode!r} "
                    f"≠ expected {exp_mode!r}"
                )

    return viol


def gate_audit_report(
    inv: FactorFamilyInventory | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    inv = inv or load_inventory()
    viol = collect_gate_violations(inv, **kwargs)
    rows = family_continuity_rows(inv)
    by_mode: dict[str, list[str]] = {}
    for r in rows:
        by_mode.setdefault(r.mode, []).append(r.family_id)
    return {
        "verdict": "PASS" if not viol else "FAIL",
        "family_count": len(rows),
        "families_by_mode": by_mode,
        "violations": viol,
        "inventory_path": str(inv.path or DEFAULT_INVENTORY),
    }
