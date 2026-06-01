#!/usr/bin/env python3
"""Portfolio sizer profile sensitivity audit.

Report whether small profile knob changes move the current candidate pool.
This is a diagnostic tool for evidence-gated tuning, not a strategy change.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_portfolio_sizer_profile_attrition import load_recommendation_candidates
from services.db import get_conn
from services.portfolio_sizer.attrition import summarize_profile_attrition
from services.portfolio_sizer.profiles import PROFILES, RiskProfile

logger = logging.getLogger("audit_portfolio_sizer_profile_sensitivity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def _latest_signal_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) AS max_date FROM fact_technical_trigger").fetchone()
    if not row:
        return None
    value = row["max_date"] if isinstance(row, dict) else row[0]
    return str(value) if value else None


def build_profile_variants(profile: RiskProfile) -> list[tuple[str, RiskProfile]]:
    """Build the small knob perturbations used by the sensitivity audit."""
    hold_plus_20 = tuple(sorted(set(profile.holding_days) | {20}))
    return [
        ("base", profile),
        ("hold+20", replace(profile, holding_days=hold_plus_20)),
        ("n_signals-2", replace(profile, min_n_signals=max(1, profile.min_n_signals - 2))),
        ("wilson-0.05", replace(profile, min_wilson_win=max(0.0, round(profile.min_wilson_win - 0.05, 2)))),
    ]


def summarize_profile_sensitivity(
    candidates: list[dict[str, Any]],
    profile: RiskProfile,
    *,
    max_examples: int = 5,
) -> dict[str, Any]:
    """Summarize the baseline and small-perturbation outcomes for one profile."""
    variant_summaries = []
    base_selected_rows = None

    for variant_id, variant_profile in build_profile_variants(profile):
        summary = summarize_profile_attrition(candidates, variant_profile, max_examples=max_examples)
        selected_rows = int(summary["selected_rows"])
        if base_selected_rows is None:
            base_selected_rows = selected_rows
        variant_summaries.append(
            {
                "variant_id": variant_id,
                "holding_days": list(variant_profile.holding_days),
                "min_n_signals": variant_profile.min_n_signals,
                "min_wilson_win": variant_profile.min_wilson_win,
                "selected_rows": selected_rows,
                "delta_selected_rows_vs_base": selected_rows - base_selected_rows,
                "selected_match_tiers": summary["selected_match_tiers"],
                "fail_reasons": summary["fail_reasons"],
                "fail_reasons_by_match_tier": summary["fail_reasons_by_match_tier"],
                "fail_holding_days_by_match_tier": summary["fail_holding_days_by_match_tier"],
                "selected_examples": summary["selected_examples"],
            }
        )

    return {
        "profile_id": profile.profile_id,
        "label": profile.label,
        "baseline_selected_rows": base_selected_rows if base_selected_rows is not None else 0,
        "variants": variant_summaries,
    }


def _render_profile_block(profile: dict[str, Any]) -> list[str]:
    lines = [f"## {profile['profile_id']} | {profile['label']}"]
    lines.append(f"- baseline_selected_rows: {profile['baseline_selected_rows']}")
    for variant in profile["variants"]:
        lines.append(
            f"- {variant['variant_id']}: selected_rows={variant['selected_rows']}"
            f" delta={variant['delta_selected_rows_vs_base']}"
            f" holding_days={variant['holding_days']}"
            f" min_n_signals={variant['min_n_signals']}"
            f" min_wilson_win={variant['min_wilson_win']}"
            f" tiers={variant['selected_match_tiers']}"
        )
    lines.append("")
    return lines


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Portfolio Sizer Profile Sensitivity",
        f"- signal_date: {result['signal_date']}",
        f"- raw_candidates: {result['raw_candidates']}",
        f"- raw_match_tiers: {result['raw_match_tiers']}",
        "",
    ]
    for profile in result["profiles"]:
        lines.extend(_render_profile_block(profile))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit portfolio_sizer profile sensitivity")
    parser.add_argument("--date", default=None, help="signal_date, default latest fact_technical_trigger date")
    parser.add_argument("--profiles", nargs="+", default=None, help="only audit selected profile ids")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--max-examples", type=int, default=5)
    args = parser.parse_args()

    conn = get_conn()
    try:
        signal_date = args.date or _latest_signal_date(conn)
        if not signal_date:
            raise SystemExit("no fact_technical_trigger data found")

        candidates = load_recommendation_candidates(conn, signal_date)
        raw_tier_counts = {}
        for row in candidates:
            key = row.get("match_tier") or "unknown"
            raw_tier_counts[key] = raw_tier_counts.get(key, 0) + 1

        profile_ids = args.profiles or ["short", "mid", "long"]
        profiles = []
        for profile_id in profile_ids:
            profile = PROFILES.get(profile_id)
            if profile is None:
                raise SystemExit(f"unknown profile: {profile_id}")
            profiles.append(
                summarize_profile_sensitivity(candidates, profile, max_examples=args.max_examples)
            )

        result = {
            "signal_date": signal_date,
            "raw_candidates": len(candidates),
            "raw_match_tiers": raw_tier_counts,
            "profiles": profiles,
        }

        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(_render_markdown(result))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
