"""Typed loader for seat_research_class. Seat names live in YAML only."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from services.research_facet import (
    FacetMatch,
    FacetPolicy,
    ResearchFacetError,
    classify_facet_key,
    load_research_facet,
)

CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "seat_research_class.yaml"
)
NAMESPACE = "seat_research_class"
_REQUIRED = frozenset({"trend_seat_daily"})


class SeatResearchClassError(ResearchFacetError):
    """Seat policy YAML failed closed."""


def load_seat_research_class(path: str | Path | None = None) -> FacetPolicy:
    return _load_seat_research_class(str(Path(path or CONFIG_PATH).resolve()))


@lru_cache(maxsize=16)
def _load_seat_research_class(resolved: str) -> FacetPolicy:
    return load_research_facet(
        resolved,
        expected_namespace=NAMESPACE,
        required_presets=_REQUIRED,
        error_cls=SeatResearchClassError,
    )


def classify_seat_name(
    exalter: str,
    *,
    policy: FacetPolicy | None = None,
) -> FacetMatch:
    loaded = policy if policy is not None else load_seat_research_class()
    return classify_facet_key(exalter, loaded, error_cls=SeatResearchClassError)


__all__ = [
    "CONFIG_PATH",
    "NAMESPACE",
    "SeatResearchClassError",
    "classify_seat_name",
    "load_seat_research_class",
]
