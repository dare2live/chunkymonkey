"""Observe calendar lag for frozen on_demand domains (margin).

Kept out of ``acquire.py`` to avoid the god-file ratchet (>800 LOC).
"""
from __future__ import annotations

import json
from typing import Any

from .context import PipelineContext

FROZEN_ON_DEMAND_OBSERVE_DOMAINS: tuple[str, ...] = ("margin",)


def margin_hard_gate_required(registry: dict | None = None) -> bool:
    """True only when margin is enabled for live acquire gating.

    Frozen ``mode=disabled`` (scope_blocked) must not deadlock daily_update.
    Explicit margin sync remains blocked by sync_runner execution policy.
    """

    from services.data_sources import sync_runner

    reg = registry if registry is not None else sync_runner.load_registry()
    spec = sync_runner.domain_spec(reg, "margin")
    return sync_runner.execution_policy_for_spec(spec).mode == "enabled"


def observe_frozen_on_demand_domains(ctx: PipelineContext) -> list[dict[str, Any]]:
    """Log calendar-eligible lag for frozen on_demand domains (margin).

    Owner distinction: calendar-driven *incremental* catchup ≠ mass refresh, but
    margin v2 live land/accept is retired (wrong-scope BSE-in-canonical). Until a
    population-scope correction knife, we record eligible_end vs local_max as
    typed ``observe_frozen`` — not all-due pull, not product thaw, not silent skip.
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
                    "calendar frontier known; v2 land/accept frozen (scope_blocked); "
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
