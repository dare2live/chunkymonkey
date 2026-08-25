"""Typed loader for holder_capital_role. Names live in YAML only."""
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
    Path(__file__).resolve().parent.parent / "config" / "holder_capital_role.yaml"
)
NAMESPACE = "holder_capital_role"
_OWN = "own_funds_account"
_DOMESTIC = "domestic_insurer_own"
_FOREIGN = "foreign_own_funds"
_REQUIRED = frozenset({"foreign_discretionary"})


class HolderCapitalRoleError(ResearchFacetError):
    """Capital-role policy YAML failed closed."""


def load_holder_capital_role(path: str | Path | None = None) -> FacetPolicy:
    return _load_holder_capital_role(str(Path(path or CONFIG_PATH).resolve()))


@lru_cache(maxsize=16)
def _load_holder_capital_role(resolved: str) -> FacetPolicy:
    return load_research_facet(
        resolved,
        expected_namespace=NAMESPACE,
        required_presets=_REQUIRED,
        error_cls=HolderCapitalRoleError,
    )


def classify_capital_role(
    holder_name: str,
    *,
    policy: FacetPolicy | None = None,
) -> FacetMatch:
    loaded = policy if policy is not None else load_holder_capital_role()
    base = classify_facet_key(holder_name, loaded, error_cls=HolderCapitalRoleError)
    extra = frozenset()
    if _OWN in base.tags and _DOMESTIC not in base.tags:
        extra = frozenset({_FOREIGN})
    if not extra:
        return base
    return classify_facet_key(
        holder_name, loaded, extra_tags=extra, error_cls=HolderCapitalRoleError
    )


__all__ = [
    "CONFIG_PATH",
    "NAMESPACE",
    "HolderCapitalRoleError",
    "classify_capital_role",
    "load_holder_capital_role",
]
