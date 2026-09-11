"""Two semantically different recon comparisons, split out of a single prior
helper that used to answer both questions at once with one Python ``set``.

Background (see git history / task spec for the full incident): the old
helper threw both sides into a Python ``set`` and reported an ``identity``
verdict regardless of whether the caller's keys actually matched the grain a
domain is registered under. A caller that dropped grain columns (e.g.
``block_trade`` compared without ``price``/``vol``) got a false
``identity=true`` — the 4-key projection collapsed 13.56% of rows into
duplicates that a full-grain compare would have kept apart.

This module gives the two questions separate functions and separate output
shapes:

* :func:`compare_codeset` — "do these two code/key sets overlap?" This is a
  set operation. Its output **never** has an ``identity`` key — code-set
  overlap is not a claim that two tables are the same product at the same
  grain, and no caller should be able to read one out of this dict.
* :func:`compare_rows` — "do these two row collections agree at the grain a
  domain is actually registered under?" Grain is resolved from a real
  registry (``sync_registry.yaml`` via ``sync_runner.domain_spec``, or the
  mart-grain table in ``check_grain_uniqueness.MART_GRAINS``) — callers
  cannot pass an ad-hoc grain list, which is exactly the mistake that caused
  the incident. Row multiplicity is tracked with ``Counter`` (not ``set``),
  so a "collapse" (more raw rows than distinct grain keys on either side) is
  visible and gates ``identity`` off.

:func:`assert_report_identity_invariant` is the structural backstop: it
walks an arbitrary nested report and refuses any dict claiming
``identity=True`` unless it also carries a truthy ``grain_source`` and zero
collapse on both sides. ``compare_codeset`` output can never trip this check
because it has no ``identity`` key to begin with.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

SAMPLE_LIMIT = 20


def _canon(value: Any) -> Any:
    """Canonicalize one grain-column value for cross-source key comparison.

    Deliberately narrow: only the representational differences that are
    known to occur between a DuckDB row and a vendor JSON row for the same
    logical value (datetime/date objects vs compact strings, Decimal vs
    float, incidental whitespace). No unit conversion, no name
    normalization — those are product-specific decisions that belong in the
    caller's row-loading code, not buried in a generic comparator.
    """
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        return value.strip()
    return value


def compare_codeset(
    left: Iterable[Any],
    right: Iterable[Any],
    *,
    what: str,
    left_name: str,
    right_name: str,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Set-overlap comparison of two code/key collections.

    No ``identity`` verdict — set overlap alone never proves two sources are
    the same product at the same grain (that is exactly what the incident
    this module fixes got wrong). Use :func:`compare_rows` when the question
    is "are these the same rows at the registered grain".
    """
    left_s = {x for x in left if x not in (None, "")}
    right_s = {x for x in right if x not in (None, "")}
    only_left = sorted(left_s - right_s)
    only_right = sorted(right_s - left_s)
    both = left_s & right_s
    union = left_s | right_s
    if not left_s and not right_s:
        status = "empty_recon"
        jaccard = None
    else:
        status = "compared"
        jaccard = (len(both) / len(union)) if union else None
    out = {
        "status": status,
        "comparison": "codeset",
        "what": what,
        "left": left_name,
        "right": right_name,
        "left_n": len(left_s),
        "right_n": len(right_s),
        "intersection": len(both),
        "only_left": len(only_left),
        "only_right": len(only_right),
        "only_left_sample": only_left[:sample_limit],
        "only_right_sample": only_right[:sample_limit],
        "jaccard": jaccard,
        "primary_cut": False,
    }
    assert "identity" not in out and "same_product" not in out
    return out


def _resolve_grain(
    domain: str, *, grain_source: str, registry: dict[str, Any] | None
) -> list[str]:
    if grain_source == "sync_registry":
        from services.data_sources.sync_runner import domain_spec, load_registry

        spec = domain_spec(registry or load_registry(), domain)
        grain = spec["grain"]
    elif grain_source == "mart_grains":
        from scripts.check_grain_uniqueness import MART_GRAINS

        match = [g for (_db, table, g) in MART_GRAINS if table == domain]
        if not match:
            raise KeyError(
                f"mart_grains: no grain registered for domain/table {domain!r}"
            )
        grain = match[0]
    else:
        raise ValueError(
            f"unknown grain_source {grain_source!r}; expected "
            "'sync_registry' or 'mart_grains'"
        )
    if not isinstance(grain, list) or not grain:
        raise ValueError(
            f"{domain}: grain from {grain_source} must be a non-empty list, "
            f"got {grain!r}"
        )
    return list(grain)


def _grain_key(
    row: Mapping[str, Any], grain: Sequence[str], *, domain: str, side: str
) -> tuple[Any, ...]:
    missing = [c for c in grain if c not in row]
    if missing:
        raise KeyError(
            f"{domain}: {side} row missing grain column(s) {missing}"
        )
    return tuple(_canon(row[c]) for c in grain)


def compare_rows(
    *,
    domain: str,
    left_rows: Sequence[Mapping[str, Any]],
    right_rows: Sequence[Mapping[str, Any]],
    left_name: str,
    right_name: str,
    grain_source: str = "sync_registry",
    sample_limit: int = SAMPLE_LIMIT,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Row-multiplicity comparison at a domain's registered grain.

    Grain is never caller-supplied — it is resolved from a real truth
    source (``sync_registry.yaml`` grain declarations, or the mart-grain
    table used by ``check_grain_uniqueness``) so a caller cannot repeat the
    incident that motivated this module (comparing at a narrower ad-hoc key
    than the domain is actually registered under).
    """
    grain = _resolve_grain(domain, grain_source=grain_source, registry=registry)

    left_keys = [
        _grain_key(r, grain, domain=domain, side="left") for r in left_rows
    ]
    right_keys = [
        _grain_key(r, grain, domain=domain, side="right") for r in right_rows
    ]
    left_counter: Counter = Counter(left_keys)
    right_counter: Counter = Counter(right_keys)

    matched = sum((left_counter & right_counter).values())
    only_left_diff = left_counter - right_counter
    only_right_diff = right_counter - left_counter
    only_left = sum(only_left_diff.values())
    only_right = sum(only_right_diff.values())

    left_rows_n = len(left_rows)
    right_rows_n = len(right_rows)
    left_keys_n = len(left_counter)
    right_keys_n = len(right_counter)
    left_collapse = left_rows_n - left_keys_n
    right_collapse = right_rows_n - right_keys_n

    def _sample(diff: Counter) -> list[dict[str, Any]]:
        items = sorted(diff.items(), key=lambda kv: repr(kv[0]))[:sample_limit]
        return [
            {
                "key": list(key),
                "left": left_counter.get(key, 0),
                "right": right_counter.get(key, 0),
            }
            for key, _count in items
        ]

    only_left_sample = _sample(only_left_diff)
    only_right_sample = _sample(only_right_diff)

    status = "empty_recon" if (left_rows_n == 0 and right_rows_n == 0) else "compared"
    identity = bool(
        status == "compared"
        and only_left == 0
        and only_right == 0
        and matched > 0
        and left_collapse == 0
        and right_collapse == 0
    )

    return {
        "status": status,
        "comparison": "rows",
        "domain": domain,
        "grain": grain,
        "grain_source": grain_source,
        "left": left_name,
        "right": right_name,
        "left_rows": left_rows_n,
        "right_rows": right_rows_n,
        "left_keys": left_keys_n,
        "right_keys": right_keys_n,
        "left_collapse": left_collapse,
        "right_collapse": right_collapse,
        "matched": matched,
        "only_left": only_left,
        "only_right": only_right,
        "only_left_sample": only_left_sample,
        "only_right_sample": only_right_sample,
        "identity": identity,
        "primary_cut": False,
    }


def assert_report_identity_invariant(report: Any) -> None:
    """Refuse any nested dict claiming ``identity=True`` without the rigor
    that verdict requires: a declared ``grain_source`` and zero collapse on
    both sides. Recurses through nested dict/list structures so it can be
    called once on a whole assembled report.
    """

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            if node.get("identity") is True:
                grain_source = node.get("grain_source")
                left_collapse = node.get("left_collapse")
                right_collapse = node.get("right_collapse")
                if not grain_source or left_collapse != 0 or right_collapse != 0:
                    raise ValueError(
                        "identity=True without grain-source rigor at "
                        f"{path or '<root>'}: grain_source={grain_source!r} "
                        f"left_collapse={left_collapse!r} "
                        f"right_collapse={right_collapse!r}"
                    )
            for key, value in node.items():
                _walk(value, f"{path}/{key}" if path else str(key))
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(report, "")


__all__ = [
    "SAMPLE_LIMIT",
    "assert_report_identity_invariant",
    "compare_codeset",
    "compare_rows",
]
