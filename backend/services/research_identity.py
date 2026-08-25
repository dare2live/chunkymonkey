"""Compose named research overlays. Do not sum across namespaces."""
from __future__ import annotations

from typing import Any

from services.holder_capital_role import (
    classify_capital_role,
    load_holder_capital_role,
)
from services.holder_research_class import (
    classify_holder_name,
    load_holder_research_class,
)
from services.research_facet import FacetMatch, overlay_dict
from services.seat_research_class import classify_seat_name, load_seat_research_class

_STABILIZER = "national_team_stabilizer"
_FOREIGN_OWN = "foreign_own_funds"


def annotate_holder(holder_name: str) -> dict[str, Any]:
    holder_policy = load_holder_research_class()
    capital_policy = load_holder_capital_role()
    holder_hit = classify_holder_name(holder_name, policy=holder_policy)
    capital_hit = classify_capital_role(holder_name, policy=capital_policy)
    holder_overlay = overlay_dict(
        FacetMatch(
            key=holder_hit.holder_name,
            tags=holder_hit.tags,
            presets=holder_hit.presets,
            alias=None,
            alias_kind=None,
        ),
        holder_policy.facet,
    )
    return {
        "holder_research_class": holder_overlay,
        "holder_capital_role": overlay_dict(capital_hit, capital_policy),
        "trend_layers": {
            "national_team_stabilizer": _STABILIZER in holder_hit.presets,
            "foreign_own_funds": _FOREIGN_OWN in capital_hit.tags,
            "note": "named layers; do not sum holdings or flows across layers",
        },
    }


def annotate_seat(exalter: str) -> dict[str, Any]:
    policy = load_seat_research_class()
    hit = classify_seat_name(exalter, policy=policy)
    return {"seat_research_class": overlay_dict(hit, policy)}


__all__ = ["annotate_holder", "annotate_seat"]
