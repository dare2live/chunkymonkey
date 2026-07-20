#!/usr/bin/env python3
"""Persist Phase C live nominal → writer smoke evidence (read-only DB).

Loads a small accepted ``canonical_nominal`` canary for decision_date ≤ daily
frontier, maps bars with contractual ``same_day_at 18:00`` available_at, runs
``write_tier12_batch``, and writes lineage artifacts under
``data/lineage/tier12_publish_batches/``.

Hard gates: WRITTEN_UNPUBLISHED / published=false / lineage present /
no future available_at on outputs. Does **not** accept-publish, cut over
consumers, loosen E gates, or claim Phase C publish-complete.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_tier12_writer_smoke.py
  PYTHONPATH=backend python backend/scripts/persist_tier12_writer_smoke.py \\
      --decision-date 20260717 --max-codes 20
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_access.resolver import connect_ro  # noqa: E402
from services.data_sources.nominal_ohlcv_schema import DATASET_ID  # noqa: E402
from services.tier12_nominal_canary import (  # noqa: E402
    assert_tier12_smoke_batch,
    load_accepted_nominal_canary,
    timed_inputs_from_nominal_rows,
)
from services.tier12_publish_writer import (  # noqa: E402
    TimedInput,
    load_tier12_publish_config,
    write_tier12_batch,
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


def _compact_summary(batch_dict: dict[str, Any], smoke: dict[str, Any]) -> dict[str, Any]:
    """Drop per-stock rows from the lineage summary (batch JSON keeps them)."""

    stocks = batch_dict.get("stock_states") or []
    market = batch_dict.get("market_context") or {}
    return {
        "kind": "tier12_writer_smoke_summary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_date": batch_dict.get("decision_date"),
        "status": batch_dict.get("status"),
        "published": batch_dict.get("published"),
        "pit_excluded_count": batch_dict.get("pit_excluded_count"),
        "stock_state_count": len(stocks),
        "smoke_gate": smoke,
        "stock_definition_version": smoke.get("definition_version"),
        "stock_config_hash": smoke.get("config_hash"),
        "stock_available_at": smoke.get("available_at"),
        "market_definition_version": market.get("definition_version"),
        "market_config_hash": market.get("config_hash"),
        "market_available_at": market.get("available_at"),
        "market_attestation_status": (
            (batch_dict.get("market_attestation") or {}).get("status")
        ),
        "market_trust_status": market.get("trust_status"),
        "market_risk_on": market.get("risk_on"),
        "market_details": {
            k: (market.get("details") or {}).get(k)
            for k in (
                "adv_n",
                "dec_n",
                "flat_n",
                "row_count_used",
                "skipped_off_board",
                "method",
                "b_pit_cutover_allowed",
            )
        },
        "notes": batch_dict.get("notes"),
        "not_claims": [
            "not_accepted_partition",
            "not_strategy_release",
            "not_publish_complete",
            "not_b_pit_cutover",
            "canary_scope_not_full_universe",
        ],
    }


def run_smoke(
    *,
    decision_date: str,
    max_codes: int,
    lookback_trading_days: int,
    inject_future_poison: bool,
    artifact_root: Path,
) -> dict[str, Any]:
    cfg = load_tier12_publish_config()
    conn = connect_ro("tushare_raw")
    try:
        canary = load_accepted_nominal_canary(
            conn,
            decision_date,
            lookback_trading_days=lookback_trading_days,
            max_codes=max_codes,
            board_prefixes=cfg.board_prefixes,
            available_at_mode="contractual",
        )
    finally:
        conn.close()

    inputs = timed_inputs_from_nominal_rows(
        canary.rows, available_at_mode="contractual"
    )
    poison_n = 0
    if inject_future_poison:
        # Would flip breadth if wrongly included; must be PIT-excluded.
        poison = TimedInput(
            entity_id="999999",
            trade_date=canary.decision_date,
            available_at="20991231T180000+0800",
            payload={
                "ts_code": "999999.SH",
                "close": 1.0,
                "pct_chg": -99.0,
            },
        )
        inputs = list(inputs) + [poison]
        poison_n = 1

    batch = write_tier12_batch(
        decision_date=canary.decision_date,
        inputs=inputs,
        config=cfg,
        emit_artifact=True,
        artifact_root=artifact_root,
    )
    smoke = assert_tier12_smoke_batch(batch, decision_date=canary.decision_date)
    if inject_future_poison and batch.pit_excluded_count < poison_n:
        raise ValueError(
            f"pit_excluded_count={batch.pit_excluded_count} "
            f"< poison_n={poison_n}"
        )

    batch_dict = batch.as_dict()
    summary = _compact_summary(batch_dict, smoke)
    summary["canary"] = canary.as_dict()
    summary["input_count"] = len(inputs)
    summary["poison_injected"] = poison_n
    summary["dataset_id"] = DATASET_ID
    summary["live_readiness_note"] = (
        "smoke used live read-only DuckDB; code commit does not upgrade "
        "Tier0 continuity BLOCKED/DEGRADED/UNVERIFIED → READY"
    )

    day = canary.decision_date
    summary_path = artifact_root / f"smoke_{day}.json"
    _write_json(summary_path, summary)
    summary["artifact_paths"] = {
        "batch": str((artifact_root / f"batch_{day}.json").relative_to(REPO)),
        "smoke": str(summary_path.relative_to(REPO)),
    }
    # Rewrite with paths filled.
    _write_json(summary_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-date", default=DEFAULT_DECISION)
    parser.add_argument("--max-codes", type=int, default=20)
    parser.add_argument("--lookback-trading-days", type=int, default=5)
    parser.add_argument(
        "--no-poison",
        action="store_true",
        help="skip future-available poison row (default: inject 1)",
    )
    parser.add_argument(
        "--artifact-dir",
        default=OUT_DIR_REL,
        help="repo-relative lineage output dir",
    )
    args = parser.parse_args(argv)

    root = Path(args.artifact_dir)
    if not root.is_absolute():
        root = REPO / root

    try:
        summary = run_smoke(
            decision_date=args.decision_date,
            max_codes=args.max_codes,
            lookback_trading_days=args.lookback_trading_days,
            inject_future_poison=not args.no_poison,
            artifact_root=root,
        )
    except Exception as exc:  # noqa: BLE001 — CLI fail-closed surface
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "decision_date": summary["decision_date"],
                "status": summary["status"],
                "published": summary["published"],
                "stock_state_count": summary["stock_state_count"],
                "pit_excluded_count": summary["pit_excluded_count"],
                "stock_config_hash": summary["stock_config_hash"],
                "market_config_hash": summary["market_config_hash"],
                "artifacts": summary.get("artifact_paths"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
