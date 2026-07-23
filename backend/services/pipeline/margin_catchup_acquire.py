"""Acquire-stage bounded margin v3 catchup (on_demand, not --all-due)."""
from __future__ import annotations

import json
from typing import Any

from .context import PipelineContext


def run_margin_bounded_catchup(ctx: PipelineContext) -> list[dict[str, Any]]:
    """Catch up calendar-eligible margin days under contract v3.

    Plans ``[max(coverage_start, local_max+1) .. eligible_end]`` capped by the
    sync_runner window. Skips when disabled / already current / no gap.
    Never enters ``--all-due``. Pulse ``rzrqye`` stays UNTRUSTED.
    """

    from services.data_sources import margin_ingest, sync_runner
    from services.duck_adapter import connect

    registry = sync_runner.load_registry()
    spec = sync_runner.domain_spec(registry, "margin")
    policy = sync_runner.execution_policy_for_spec(spec)
    if policy.mode != "enabled":
        return []
    if str(spec.get("sync_policy") or "") != "on_demand":
        raise RuntimeError(
            "margin acquire catchup requires sync_policy=on_demand"
        )

    eligibility = sync_runner.eligible_end_date(spec, trigger_mode="manual")
    eligible_end = eligibility.eligible_end
    if eligible_end is None:
        outcome = {
            "domain": "margin",
            "action": "skip",
            "reason": "no_eligible_end",
            "eligibility_reason": eligibility.reason,
        }
        ctx.log(f"margin catchup SKIP (no eligible_end; {eligibility.reason})")
        return [outcome]

    contract = margin_ingest.contract_for_spec(spec)
    if contract is None or str(contract.contract_version) < "3":
        outcome = {
            "domain": "margin",
            "action": "skip",
            "reason": "contract_not_v3",
        }
        ctx.log("margin catchup SKIP (contract_version<3)")
        return [outcome]

    coverage_start = str(contract.coverage_start).replace("-", "")
    local_max = None
    conn = connect(ctx.db("tushare_raw"), read_only=True)
    try:
        for candidate in (
            "canonical_margin_exchange_daily",
            str(spec.get("target_table") or ""),
            "raw_tushare_margin",
        ):
            if not candidate:
                continue
            try:
                row = conn.execute(
                    f'SELECT MAX(trade_date) FROM "{candidate}"'
                ).fetchone()
            except Exception:  # noqa: BLE001
                continue
            if row and row[0]:
                local_max = str(row[0]).replace("-", "")[:8]
                break
    finally:
        conn.close()

    start = coverage_start
    if local_max is not None and local_max >= coverage_start:
        # Next trading day after local evidence.
        probe = sync_runner.trading_days(local_max, eligible_end)
        after = [d for d in probe if d > local_max]
        if not after:
            outcome = {
                "domain": "margin",
                "action": "skip",
                "reason": "latest_eligible_already_present",
                "eligible_end": eligible_end,
                "local_max": local_max,
                "contract_version": str(contract.contract_version),
            }
            print(json.dumps(outcome, ensure_ascii=False))
            ctx.log(
                f"margin catchup SKIP local_max={local_max} "
                f"eligible_end={eligible_end}"
            )
            return [outcome]
        start = after[0]

    if start > eligible_end:
        outcome = {
            "domain": "margin",
            "action": "skip",
            "reason": "start_after_eligible_end",
            "start": start,
            "eligible_end": eligible_end,
            "local_max": local_max,
        }
        return [outcome]

    try:
        result = sync_runner.run_domain(
            "margin",
            start=start,
            end=eligible_end,
            registry=registry,
            trigger_mode="manual",
        )
    except Exception as exc:  # noqa: BLE001 — typed acquire soft degrade
        outcome = {
            "domain": "margin",
            "action": "error",
            "reason": "catchup_failed",
            "start": start,
            "eligible_end": eligible_end,
            "local_max": local_max,
            "error": str(exc)[:400],
        }
        print(json.dumps(outcome, ensure_ascii=False))
        ctx.degraded(f"margin bounded catchup failed: {exc}")
        return [outcome]

    outcome = {
        "domain": "margin",
        "action": "land_then_accept",
        "reason": "bounded_calendar_catchup",
        "start": start,
        "eligible_end": eligible_end,
        "local_max_before": local_max,
        "status": result.get("status"),
        "rows": result.get("rows"),
        "last_date": result.get("last_date"),
        "failed_batches": result.get("failed_batches"),
        "contract_version": result.get("contract_version"),
        "transport": "land_then_accept",
    }
    print(json.dumps(outcome, ensure_ascii=False, default=str))
    ctx.log(
        f"margin catchup {outcome['status']} start={start} "
        f"eligible_end={eligible_end} last={outcome.get('last_date')} "
        f"rows={outcome.get('rows')}"
    )
    if int(result.get("failed_batches") or 0) > 0:
        ctx.degraded(
            "margin bounded catchup partial/fail "
            f"failed_batches={result.get('failed_batches')}"
        )
    return [outcome]
