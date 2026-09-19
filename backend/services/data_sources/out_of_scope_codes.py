"""out_of_scope_codes — the one place a range-outside security category's
``code_patterns`` (e.g. B股 900.SH / 200.SZ) turns into a regex.

**Why this module exists (spec ``sandbox/acceptance_cuts_20260918/spec_bshare_b2.md``
§2.1/§5.1)**: before this module, the (prefix, suffix) → regex formula lived in
exactly one place, ``backend/scripts/check_out_of_scope_rows.py`` (``_pattern_regex`` /
the ``class_regex`` construction at what was L134-137,185) — a canonical/audit-only
script. Landing-side code (``vendor_scope.py`` / adapters) needed the identical
formula for the new ``code_exclude`` disposition mode (a vendor report with no
category axis at all — see ``vendor_scope.py`` module docstring, C1), and copying
the formula a second time would make the two copies driftable. This module is
that single source; both ``check_out_of_scope_rows.py`` and ``vendor_scope.py``
import from here (the CI-enforced parity test —
``test_vendor_scope.py::test_class_regex_matches_check_out_of_scope_rows_formula``
— guards against a future edit landing in one copy only).

**Zero dependency, deliberately**: this module must never import
``services.universe`` (or anything else project-specific) — ``vendor_scope.py``'s
C2 mutual-exclusion check needs ``UNIVERSE_POLICY`` to be resolvable, but the
canonical/audit script that also imports this module must not be made to depend
on ``universe_rules.yaml``'s availability just to compute a regex string.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodePattern:
    """One out-of-scope code segment, e.g. B股 900.SH / 200.SZ."""

    prefix: str
    suffix: str


def pattern_regex(prefix: str, suffix: str) -> str:
    """900/SH -> r'900\\d{3}(\\.SH)?' — anchors total length to 6 digits;
    the exchange suffix is optional but must match if present.

    Formula byte-identical to the one this module replaces
    (``check_out_of_scope_rows.py`` pre-S1 ``_pattern_regex``) — do not
    "simplify" it without updating the parity test.
    """
    digits = 6 - len(prefix)
    return rf"{prefix}\d{{{digits}}}(\.{suffix})?"


def class_regex(patterns) -> str:
    """A whole class's ``code_patterns`` (iterable of :class:`CodePattern`) ->
    one anchored alternation regex, e.g. ``"^(200\\d{3}(\\.SZ)?|900\\d{3}(\\.SH)?)$"``.

    Sort order matches the module this formula was lifted from: each pattern's
    *rendered regex string* is sorted (not the (prefix, suffix) pair) before
    joining — changing this to sort the pairs instead would silently reorder
    the alternation for classes whose prefixes and regex strings sort
    differently, which is exactly the kind of byte-level drift the parity
    test above exists to catch.
    """
    regexes = [pattern_regex(p.prefix, p.suffix) for p in patterns]
    return "^(" + "|".join(sorted(regexes)) + ")$"


__all__ = ["CodePattern", "class_regex", "pattern_regex"]
