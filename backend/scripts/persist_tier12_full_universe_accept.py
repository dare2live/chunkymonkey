#!/usr/bin/env python3
"""Persist Phase C full-universe Tier1/2 writer → accepted publish.

Loads ``traded_on_observation_date`` membership from accepted Tier0 facts,
maps contractual-available_at nominal bars, runs ``write_tier12_batch``, then
``accept_tier12_batch(publish_scope=project_universe)``.

Hard gates:
  - membership unavailable → fail closed
  - coverage exclusions recorded (never silent pad)
  - accept artifact cutover_allowed stays false (accept path hard gate)
  - never flips consumer_cutover.cutover_allowed yaml
  - cutover-aware post-check: when yaml cutover is ON, resolver must
    reach ACCEPTED_CUTOVER for the newly accepted day; when OFF, stay LEGACY

Usage:
  PYTHONPATH=backend python backend/scripts/persist_tier12_full_universe_accept.py
  PYTHONPATH=backend python backend/scripts/persist_tier12_full_universe_accept.py \\
      --decision-date 20260717
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tier12_nominal_canary import assert_tier12_smoke_batch  # noqa: E402
from services.tier12_project_universe import (  # noqa: E402
    load_accepted_nominal_project_universe,
    timed_inputs_for_project_universe,
)
from services.tier12_publish_accept import (  # noqa: E402
    Tier12AcceptError,
    accept_tier12_batch,
)
from services.tier12_publish_writer import (  # noqa: E402
    load_form_rows_exact_day,
    load_tier12_publish_config,
    write_tier12_batch,
)
from services.tier12_consumer_cutover import (  # noqa: E402
    load_tier12_consumer_cutover_config,
    resolve_tier12_consumer_cutover,
)

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "data/lineage/tier12_publish_batches"
DEFAULT_DECISION = "20260717"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def assert_post_accept_cutover_gate(*, cut_cfg: Any, cut_decision: Any) -> None:
    """Cutover-aware post-accept check (never flips yaml cutover)."""

    if bool(getattr(cut_cfg, "cutover_allowed", False)):
        if (
            str(getattr(cut_decision, "status", "") or "") != "ACCEPTED_CUTOVER"
            or not bool(getattr(cut_decision, "cutover_allowed", False))
        ):
            raise ValueError(
                "cutover_on_but_resolver_not_accepted "
                f"status={getattr(cut_decision, 'status', None)} "
                f"reasons={list(getattr(cut_decision, 'reasons', ()) or ())}"
            )
        return
    if bool(getattr(cut_decision, "cutover_allowed", False)):
        raise ValueError(
            "consumer_cutover_must_remain_false_under_default_config "
            f"status={getattr(cut_decision, 'status', None)}"
        )


def run_full_universe_accept(
    *,
    decision_date: str,
    lookback_trading_days: int,
    artifact_root: Path,
) -> dict[str, Any]:
    cfg = load_tier12_publish_config()
    cut_cfg = load_tier12_consumer_cutover_config()
    # Cutover-ON is allowed: re-accept under production cutover must work.
    # This script never flips consumer_cutover.cutover_allowed.

    load = load_accepted_nominal_project_universe(
        decision_date,
        lookback_trading_days=lookback_trading_days,
    )
    inputs = timed_inputs_for_project_universe(load)
    form_by_code: dict[str, dict[str, Any]] = {}
    if cfg.form_source:
        form_by_code = load_form_rows_exact_day(
            load.decision_date, table=cfg.form_source
        )
    batch = write_tier12_batch(
        decision_date=load.decision_date,
        inputs=inputs,
        config=cfg,
        emit_artifact=True,
        artifact_root=artifact_root,
        form_by_code=form_by_code,
    )
    smoke = assert_tier12_smoke_batch(batch, decision_date=load.decision_date)

    # Writer may omit codes that lacked decision-day bars (already in exclusions)
    # or that failed entity/payload gates. Reconcile parity for accept.
    written_codes = {str(r.stock_code) for r in batch.stock_states}
    membership_entity = {
        str(c).split(".", 1)[0]: str(c) for c in load.membership_codes
    }
    # Exclusions already recorded as ts_code; add writer-side misses with reason.
    excluded = {e.ts_code: e.reason for e in load.exclusions}
    for entity, ts_code in membership_entity.items():
        if ts_code in excluded:
            continue
        if entity not in written_codes:
            excluded[ts_code] = "writer_no_stock_state_row"

    coverage_excluded_count = len(excluded)
    if len(batch.stock_states) + coverage_excluded_count != load.membership_size:
        raise ValueError(
            "coverage_parity_failed_before_accept "
            f"written={len(batch.stock_states)} excluded={coverage_excluded_count} "
            f"membership={load.membership_size}"
        )

    attestation = {
        "population_kind": "project_universe_pit",
        "membership_size": load.membership_size,
        "universe_policy_hash": load.universe_policy_hash,
        "coverage_excluded_count": coverage_excluded_count,
        "universe_policy_id": load.universe_policy_id,
        "decision_time": load.decision_time,
    }
    try:
        accepted = accept_tier12_batch(
            batch,
            publish_scope="project_universe",
            universe_attestation=attestation,
            emit_artifact=True,
            artifact_root=artifact_root,
        )
    except Tier12AcceptError as exc:
        raise ValueError(f"accept_failed: {exc}") from exc

    if accepted.cutover_allowed is not False:
        raise ValueError("accept_must_keep_cutover_allowed_false")
    if accepted.publish_scope != "project_universe":
        raise ValueError("accept_publish_scope_not_project_universe")

    cut_decision = resolve_tier12_consumer_cutover(
        accepted.decision_date,
        accepted=accepted,
        artifact_root=artifact_root,
    )
    assert_post_accept_cutover_gate(cut_cfg=cut_cfg, cut_decision=cut_decision)

    day = accepted.decision_date
    coverage = {
        "kind": "tier12_full_universe_coverage",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_date": day,
        "population_kind": "project_universe_pit",
        "universe_membership_size": load.membership_size,
        "written_stock_row_count": accepted.stock_row_count,
        "coverage_excluded_count": coverage_excluded_count,
        "exclusion_reason_counts": {
            reason: sum(1 for r in excluded.values() if r == reason)
            for reason in sorted(set(excluded.values()))
        },
        "exclusions_sample": [
            {"ts_code": code, "reason": reason}
            for code, reason in sorted(excluded.items())[:50]
        ],
        "lookback_days": list(load.lookback_days),
        "universe_policy_id": load.universe_policy_id,
        "universe_policy_hash": load.universe_policy_hash,
        "input_row_count": len(inputs),
        "pit_excluded_count": batch.pit_excluded_count,
        "smoke_gate": smoke,
        "notes": [
            "coverage_gaps_excluded_with_reason",
            "not_silent_pad",
            "not_consumer_cutover",
            "not_strategy_release",
            "not_phase_c_complete",
        ],
    }
    coverage_path = artifact_root / f"coverage_full_universe_{day}.json"
    _write_json(coverage_path, coverage)

    summary = {
        "kind": "tier12_full_universe_accept_summary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_date": day,
        "status": accepted.status,
        "published": accepted.published,
        "cutover_allowed": accepted.cutover_allowed,
        "publish_scope": accepted.publish_scope,
        "population_kind": accepted.population_kind,
        "universe_membership_size": accepted.universe_membership_size,
        "stock_row_count": accepted.stock_row_count,
        "coverage_excluded_count": accepted.coverage_excluded_count,
        "content_hash": accepted.content_hash,
        "definition_version": accepted.definition_version,
        "config_hash": accepted.config_hash,
        "input_snapshot_id": accepted.input_snapshot_id,
        "available_at": accepted.available_at,
        "consumer_cutover_default": {
            "cutover_allowed": cut_decision.cutover_allowed,
            "status": cut_decision.status,
            "reasons": list(cut_decision.reasons),
        },
        "market_trust_status": (
            accepted.market_context.trust_status if accepted.market_context else None
        ),
        "market_details": (
            dict(accepted.market_context.details or {})
            if accepted.market_context
            else {}
        ),
        "artifacts": {
            "batch": str((artifact_root / f"batch_{day}.json").relative_to(REPO)),
            "accepted": str(
                (artifact_root / f"accepted_{day}.json").relative_to(REPO)
            ),
            "coverage": str(coverage_path.relative_to(REPO)),
        },
        "not_claims": [
            "not_consumer_cutover",
            "not_strategy_release",
            "not_phase_c_complete",
            "continuity_not_upgraded_by_accept",
        ],
    }
    summary_path = artifact_root / f"full_universe_accept_{day}.json"
    _write_json(summary_path, summary)
    summary["artifacts"]["summary"] = str(summary_path.relative_to(REPO))
    _write_json(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-date", default=DEFAULT_DECISION)
    parser.add_argument("--lookback-trading-days", type=int, default=5)
    parser.add_argument("--artifact-dir", default=OUT_DIR_REL)
    args = parser.parse_args(argv)

    root = Path(args.artifact_dir)
    if not root.is_absolute():
        root = REPO / root

    try:
        summary = run_full_universe_accept(
            decision_date=args.decision_date,
            lookback_trading_days=args.lookback_trading_days,
            artifact_root=root,
        )
    except Exception as exc:  # noqa: BLE001 — CLI fail-closed surface
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
