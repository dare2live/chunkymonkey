"""Observe calendar lag for frozen on_demand domains (margin when disabled).

Kept out of ``acquire.py`` to avoid the god-file ratchet (>800 LOC).
"""
from __future__ import annotations

import json
from typing import Any

from .context import PipelineContext

FROZEN_ON_DEMAND_OBSERVE_DOMAINS: tuple[str, ...] = ("margin",)


def margin_hard_gate_required(registry: dict | None = None) -> bool:
    """True only when margin product trust is claimed as acquire-blocking.

    Knife 1b enables ``bounded_calendar_catchup`` land/accept while pulse
    ``rzrqye`` stays UNTRUSTED. Equating hard-gate to ``mode=enabled`` would
    re-deadlock daily_update (margin is on_demand, not in --all-due drain).
    Product thaw is a later knife with explicit shadow evidence.
    """

    from services.data_sources import sync_runner

    reg = registry if registry is not None else sync_runner.load_registry()
    spec = sync_runner.domain_spec(reg, "margin")
    policy = sync_runner.execution_policy_for_spec(spec)
    if policy.mode != "enabled":
        return False
    # Explicit product-blocking flag only (absent = catchup-only, not thaw).
    return bool(spec.get("product_blocking"))


def observe_frozen_on_demand_domains(ctx: PipelineContext) -> list[dict[str, Any]]:
    """Log calendar-eligible lag for still-disabled on_demand domains.

    When margin is enabled for bounded catchup (1b), acquire runs the catchup
    path instead — this observe helper stays for any residual disabled domain.
    """

    from services.data_sources import sync_runner
    from services.duck_adapter import connect

    registry = sync_runner.load_registry()
    outcomes: list[dict[str, Any]] = []
    conn = connect(ctx.db("tushare_raw"), read_only=True)
    try:
        for domain in FROZEN_ON_DEMAND_OBSERVE_DOMAINS:
            spec = sync_runner.domain_spec(registry, domain)
            policy = sync_runner.execution_policy_for_spec(spec)
            if policy.mode != "disabled":
                continue
            eligibility = sync_runner.eligible_end_date(spec, trigger_mode="manual")
            eligible_end = eligibility.eligible_end
            local_max = None
            table = str(spec.get("target_table") or "")
            for candidate in (
                "canonical_margin_exchange_daily",
                table,
                "raw_tushare_margin",
            ):
                if not candidate:
                    continue
                try:
                    row = conn.execute(
                        f'SELECT MAX(trade_date) FROM "{candidate}"'
                    ).fetchone()
                except Exception:  # noqa: BLE001 — table may be absent
                    continue
                if row and row[0]:
                    local_max = str(row[0]).replace("-", "")[:8]
                    break
            outcome = {
                "domain": domain,
                "action": "observe_frozen",
                "reason": f"execution_policy_{policy.mode}",
                "policy_reason": policy.reason,
                "eligible_end": eligible_end,
                "eligibility_reason": getattr(eligibility, "reason", None),
                "local_max": local_max,
                "catchup_blocked": True,
                "note": (
                    "calendar frontier known; land/accept still disabled; "
                    "not in --all-due; pulse rzrqye stays NULL/unknown on new days"
                ),
            }
            print(json.dumps(outcome, ensure_ascii=False, default=str))
            ctx.log(
                f"frozen {domain}: observe eligible_end={eligible_end} "
                f"local_max={local_max} reason={policy.reason} catchup_blocked"
            )
            outcomes.append(outcome)
    finally:
        conn.close()
    return outcomes
