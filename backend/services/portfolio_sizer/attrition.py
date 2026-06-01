"""Portfolio sizer profile attrition diagnostics."""
from __future__ import annotations

from collections import Counter
from typing import Any

from services.portfolio_sizer.profiles import RiskProfile
from services.portfolio_sizer.sizing import evaluate_candidate, select_candidates
from services.sentiment.factor_registry import get_eligible_factors


ATTRITION_STAGES = ("hp", "n_signals", "avg_ret", "fund_stage", "wilson", "kelly")


def summarize_profile_attrition(
    candidates: list[dict[str, Any]],
    profile: RiskProfile,
    *,
    max_examples: int = 5,
) -> dict[str, Any]:
    """Summarize where candidates are filtered out for one profile.

    The summary is intentionally mechanical: it reports cumulative pass counts
    after each gate, the first failure reason counts, and the final selected
    rows after the same dedup / cap logic used by ``rank_and_size``.
    """
    stage_reached = Counter()
    fail_reasons = Counter()
    enriched: list[dict[str, Any]] = []
    eligible_factors = get_eligible_factors(profile.profile_id)

    for candidate in candidates:
        scored, fail_reason, trace = evaluate_candidate(candidate, profile, eligible_factors)
        for stage in ATTRITION_STAGES:
            if trace.get(stage):
                stage_reached[stage] += 1
        if fail_reason:
            fail_reasons[fail_reason] += 1
            continue
        if scored is not None:
            enriched.append(scored)

    selected = select_candidates([dict(row) for row in enriched], profile)
    selected_match_tiers = Counter(row.get("match_tier") or "unknown" for row in selected)

    return {
        "profile_id": profile.profile_id,
        "label": profile.label,
        "input_rows": len(candidates),
        "stage_reached": dict(stage_reached),
        "fail_reasons": dict(fail_reasons),
        "after_filter_rows": len(enriched),
        "selected_rows": len(selected),
        "selected_match_tiers": dict(selected_match_tiers),
        "selected_examples": [
            {
                "stock_code": row.get("stock_code"),
                "formula_variant": row.get("formula_variant"),
                "match_tier": row.get("match_tier"),
                "holding_days": row.get("holding_days"),
                "n_signals": row.get("n_signals"),
                "wilson_win": round(float(row.get("wilson_win") or 0.0), 4),
                "score": round(float(row.get("score") or 0.0), 4),
            }
            for row in selected[:max_examples]
        ],
    }
