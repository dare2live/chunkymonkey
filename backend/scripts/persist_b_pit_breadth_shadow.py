#!/usr/bin/env python3
"""Persist B-pit breadth shadow remeasure (project_universe_pit vs unfiltered).

Read-only over accepted K∩ST partitions. Never flips mart/consumer cutover.
``cutover_allowed`` stays false even if every day MATCH — match alone is not
a cutover gate.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_b_pit_breadth_shadow.py
  PYTHONPATH=backend python backend/scripts/persist_b_pit_breadth_shadow.py \\
      --start 20260116 --end 20260717
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
from services.data_sources.observation_population import (  # noqa: E402
    ObservationPopulationUnavailable,
    resolve_traded_on_observation_date,
)
from services.data_sources.project_universe_breadth import (  # noqa: E402
    BreadthShadowDayMeasure,
    ProjectUniverseBreadthUnavailable,
    aggregate_breadth_shadow_window,
    measure_breadth_shadow_day,
)
from services.institution_follow_nominal_bars import (  # noqa: E402
    load_nominal_bars_by_day,
)
from services.universe import load_universe_policy  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "data/lineage/b_pit_breadth_shadow"
NOMINAL_DATASET = "tier0.market_data.nominal_ohlcv_daily"
ST_DATASET = "tier0.security_identity.stock_st_daily"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _compact(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def list_frontier_eligible_days(conn, *, start: str = "", end: str = "") -> list[str]:
    """K∩ST accepted partition intersection, optionally clipped."""

    rows = conn.execute(
        """
        SELECT partition_value FROM accepted_partition
         WHERE dataset_id = ?
        INTERSECT
        SELECT partition_value FROM accepted_partition
         WHERE dataset_id = ?
        ORDER BY 1
        """,
        [NOMINAL_DATASET, ST_DATASET],
    ).fetchall()
    days = [_compact(r[0] if not hasattr(r, "keys") else r["partition_value"]) for r in rows]
    days = [d for d in days if len(d) == 8]
    s = _compact(start)
    e = _compact(end)
    if s:
        days = [d for d in days if d >= s]
    if e:
        days = [d for d in days if d <= e]
    return days


def remeasure_window(
    *,
    start: str = "",
    end: str = "",
    decision_time: datetime | None = None,
) -> dict[str, Any]:
    pol = load_universe_policy()
    cutoff = decision_time or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    conn = connect_ro("tushare_raw")
    try:
        days = list_frontier_eligible_days(conn, start=start, end=end)
        if not days:
            raise ValueError("no_frontier_eligible_k_st_intersection_days")

        measures: list[BreadthShadowDayMeasure] = []
        errors: list[dict[str, Any]] = []
        bars_by_day = load_nominal_bars_by_day(conn, days)
        for day in days:
            try:
                mem = resolve_traded_on_observation_date(day, cutoff, pol)
                bars = bars_by_day.get(day) or []
                measures.append(measure_breadth_shadow_day(mem, rows=bars))
            except (ObservationPopulationUnavailable, ProjectUniverseBreadthUnavailable, ValueError) as exc:
                errors.append(
                    {
                        "trade_date": day,
                        "error": str(exc),
                        "ratios_match": False,
                        "cutover_allowed": False,
                    }
                )

        window = aggregate_breadth_shadow_window(measures, errors=errors)
        frontier = measures[-1] if measures else None
        payload = {
            "kind": "b_pit_breadth_shadow_remeasure",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "population_kind_formal": "project_universe_pit",
            "legacy_proxy_kind": "accepted_canonical_unfiltered",
            "universe_policy_id": pol.policy_id,
            "universe_policy_hash": pol.config_hash,
            "eligible_day_count_requested": len(days),
            "cutover_allowed": False,
            "cutover_note": (
                "shadow remeasure never enables mart cutover; "
                "requires MATCH + separate explicit gate evidence"
            ),
            "window": {
                k: v
                for k, v in window.as_dict().items()
                if k != "days"
            },
            "frontier_day": window.frontier_day,
            "frontier": frontier.as_dict() if frontier else None,
            "days": list(window.days),
            "notes": [
                "B-pit PARTIAL until cutover gate with MATCH+strong evidence",
                "pulse mart not required for this shadow path",
                "ST-only day 20260720 excluded (no K partition)",
            ],
        }
        return payload
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="", help="YYYYMMDD inclusive")
    ap.add_argument("--end", default="", help="YYYYMMDD inclusive")
    ap.add_argument(
        "--out-dir",
        default=str(REPO / OUT_DIR_REL),
        help=f"artifact directory (default {OUT_DIR_REL})",
    )
    args = ap.parse_args()

    payload = remeasure_window(start=args.start, end=args.end)
    out_dir = Path(args.out_dir)
    _write_json(out_dir / "manifest.json", payload)
    summary = {
        "kind": payload["kind"],
        "generated_at": payload["generated_at"],
        "cutover_allowed": False,
        "window": payload["window"],
        "frontier_day": payload["frontier_day"],
        "frontier_compare": (
            (payload.get("frontier") or {}).get("compare")
            if payload.get("frontier")
            else None
        ),
        "artifact": str((out_dir / "manifest.json").relative_to(REPO)),
    }
    _write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
