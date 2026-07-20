#!/usr/bin/env python3
"""Persist Phase C Tier1/2 accepted publish attestation (canary/fixture scale).

Loads a writer ``batch_YYYYMMDD.json`` (not smoke summary), runs
``accept_tier12_batch``, and writes ``accepted_YYYYMMDD.json`` under the
lineage dir. Does **not** cut over consumers, claim full-universe
publish-complete, loosen E gates, or StrategyRelease.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_tier12_accepted_publish.py
  PYTHONPATH=backend python backend/scripts/persist_tier12_accepted_publish.py \\
      --batch data/lineage/tier12_publish_batches/batch_20260717.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.tier12_publish_accept import (  # noqa: E402
    Tier12AcceptError,
    accept_tier12_batch,
    load_tier12_write_batch,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = (
    REPO / "data" / "lineage" / "tier12_publish_batches" / "batch_20260717.json"
)
OUT_DIR_REL = "data/lineage/tier12_publish_batches"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--artifact-dir", default=OUT_DIR_REL)
    args = parser.parse_args(argv)

    batch_path = args.batch
    if not batch_path.is_absolute():
        batch_path = REPO / batch_path
    root = Path(args.artifact_dir)
    if not root.is_absolute():
        root = REPO / root

    try:
        batch = load_tier12_write_batch(batch_path)
        accepted = accept_tier12_batch(
            batch,
            emit_artifact=True,
            artifact_root=root,
        )
    except (Tier12AcceptError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    out: dict[str, Any] = {
        "ok": True,
        "decision_date": accepted.decision_date,
        "status": accepted.status,
        "published": accepted.published,
        "cutover_allowed": accepted.cutover_allowed,
        "batch_id": accepted.batch_id,
        "stock_row_count": accepted.stock_row_count,
        "content_hash": accepted.content_hash,
        "definition_version": accepted.definition_version,
        "config_hash": accepted.config_hash,
        "input_snapshot_id": accepted.input_snapshot_id,
        "available_at": accepted.available_at,
        "artifact": str((root / f"accepted_{accepted.decision_date}.json").relative_to(REPO)),
        "source_batch": str(batch_path.relative_to(REPO)),
        "not_claims": [
            "not_consumer_cutover",
            "not_full_universe",
            "not_strategy_release",
            "not_b_pit_cutover",
            "canary_scale_only",
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
