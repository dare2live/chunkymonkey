"""``backend/config/vendor_scope.yaml`` (v2) loader contract — offline only.

Every test injects its own minimal-valid YAML doc via ``tmp_path`` (see
``feedback-test-must-carry-its-own-fixture``) — no live network, no DuckDB.
Rule numbers (L1-L12) match ``scratchpad/b_share_exclusion_r1.md`` §3.1; each
test isolates exactly one gating condition (project rule: 每个门控条件一个
隔离用例) and asserts on the specific ``vendor_scope: L#`` prefix, not just
``ValueError``, so a mutation that disables the wrong check cannot pass this
test by accident.

L11/L12 are *static* rules (not enforced inside ``load_vendor_scope``): they
compare ``vendor_scope.yaml`` against ``sync_registry.yaml``. This file tests
the derivation formula in isolation with synthetic registries, and separately
asserts it holds for the two real, checked-in files (the actual "commit
turns red" enforcement point).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from services.data_sources.vendor_scope import (
    VendorExclusions,
    VendorScopeError,
    apply_response_excludes,
    load_vendor_scope,
    out_of_scope_code_patterns,
    vendor_exclusions,
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _minimal_valid_scope() -> dict:
    """A self-contained, loader-valid v2 doc every L-rule test mutates one
    field of. Uses a synthetic ``srcx.apiy`` key so tests never depend on
    (and cannot accidentally break by mutating) the real registry contents."""
    return {
        "version": 2,
        "security_code_columns": ["ts_code", "con_code"],
        "out_of_scope_classes": {
            "b_share": {
                "ruling": "test ruling text",
                "code_patterns": [
                    {"prefix": "900", "suffix": "SH"},
                    {"prefix": "200", "suffix": "SZ"},
                ],
            },
        },
        "dispositions": {
            "srcx.apiy": {
                "b_share": {
                    "mode": "population_disjoint",
                    "why": "test why",
                },
            },
        },
        "non_registry_sources": {},
    }


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "vendor_scope.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# control case — the fixture itself must be valid
# ---------------------------------------------------------------------------


def test_minimal_valid_scope_loads_successfully(tmp_path):
    path = _write(tmp_path, _minimal_valid_scope())
    scope = load_vendor_scope(path)
    assert scope.dispositions["srcx.apiy"]["b_share"].mode == "population_disjoint"
    assert scope.security_code_columns == frozenset({"ts_code", "con_code"})


# ---------------------------------------------------------------------------
# L1 — top-level key set
# ---------------------------------------------------------------------------


def test_l1_extra_top_level_key_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["extra_top_key"] = True
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L1"):
        load_vendor_scope(path)


def test_l1_missing_top_level_key_rejected(tmp_path):
    data = _minimal_valid_scope()
    del data["non_registry_sources"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L1"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L2 — version
# ---------------------------------------------------------------------------


def test_l2_wrong_version_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["version"] = 1
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L2"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L3 — security_code_columns
# ---------------------------------------------------------------------------


def test_l3_duplicate_column_name_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["security_code_columns"] = ["ts_code", "ts_code"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L3"):
        load_vendor_scope(path)


def test_l3_invalid_column_name_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["security_code_columns"] = ["Ts_Code"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L3"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L4 — out_of_scope_classes non-empty (also the §4.4 "object disappears" case)
# ---------------------------------------------------------------------------


def test_l4_empty_out_of_scope_classes_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["out_of_scope_classes"] = {}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L4"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L5 — class shape / ruling
# ---------------------------------------------------------------------------


def test_l5_class_missing_ruling_rejected(tmp_path):
    data = _minimal_valid_scope()
    del data["out_of_scope_classes"]["b_share"]["ruling"]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L5"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L6 — code_patterns shape
# ---------------------------------------------------------------------------


def test_l6_prefix_wrong_length_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["out_of_scope_classes"]["b_share"]["code_patterns"] = [{"prefix": "9000", "suffix": "SH"}]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L6"):
        load_vendor_scope(path)


def test_l6_invalid_suffix_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["out_of_scope_classes"]["b_share"]["code_patterns"] = [{"prefix": "900", "suffix": "SS"}]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L6"):
        load_vendor_scope(path)


def test_l6_duplicate_prefix_suffix_pair_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["out_of_scope_classes"]["b_share"]["code_patterns"] = [
        {"prefix": "900", "suffix": "SH"},
        {"prefix": "900", "suffix": "SH"},
    ]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L6"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L7 — C2 mutual exclusion against real UNIVERSE_POLICY (not mocked, per
# project rule: 不 mock universe 门)
# ---------------------------------------------------------------------------


def test_l7_prefix_collides_with_excluded_board_rejected(tmp_path):
    data = _minimal_valid_scope()
    # "92" (北交所) is a real key in universe_rules.yaml exclude.excluded_boards.
    data["out_of_scope_classes"]["b_share"]["code_patterns"] = [{"prefix": "92", "suffix": "SH"}]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L7"):
        load_vendor_scope(path)


def test_l7_prefix_collides_with_included_board_rejected(tmp_path):
    data = _minimal_valid_scope()
    # "60" (沪主板) is a real entry in universe_rules.yaml include.board_prefixes.
    data["out_of_scope_classes"]["b_share"]["code_patterns"] = [{"prefix": "60", "suffix": "SH"}]
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L7"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L8 — dispositions key format / dangling class reference
# ---------------------------------------------------------------------------


def test_l8_disposition_references_undeclared_class_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "unknown_class": {"mode": "population_disjoint", "why": "x"}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L8"):
        load_vendor_scope(path)


def test_l8_disposition_key_bad_format_rejected(tmp_path):
    data = _minimal_valid_scope()
    del data["dispositions"]["srcx.apiy"]
    data["dispositions"]["BadKeyNoDot"] = {"b_share": {"mode": "population_disjoint", "why": "x"}}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L8"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# L9 — per-mode exact key set + unknown mode + request_enumeration dangling
# code_source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "request_exclude"},
        {"mode": "response_exclude", "field": "SECURITY_TYPE"},
        {"mode": "code_exclude", "code_field": "SECUCODE", "checked_at": "2026-09-12"},
        {"mode": "request_enumeration"},
        {"mode": "population_disjoint"},
    ],
    ids=["request_exclude", "response_exclude", "code_exclude", "request_enumeration", "population_disjoint"],
)
def test_l9_missing_required_key_rejected(tmp_path, payload):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {"b_share": payload}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "request_exclude", "filters": ["x=y"], "extra": 1},
        {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["EQB"], "extra": 1},
        {
            "mode": "code_exclude",
            "code_field": "SECUCODE",
            "checked_at": "2026-09-12",
            "evidence": "x",
            "extra": 1,
        },
        {"mode": "request_enumeration", "code_source": "os.path", "extra": 1},
        {"mode": "population_disjoint", "why": "x", "extra": 1},
    ],
    ids=["request_exclude", "response_exclude", "code_exclude", "request_enumeration", "population_disjoint"],
)
def test_l9_extra_key_rejected(tmp_path, payload):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {"b_share": payload}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_unknown_mode_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {"b_share": {"mode": "delete_everything"}}
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_retired_no_vendor_axis_mode_is_now_unknown(tmp_path):
    """S1 (2026-09-19, spec_bshare_b2.md §2.4): ``no_vendor_axis`` was replaced
    by ``code_exclude`` — a config that still says ``no_vendor_axis`` must now
    fail closed as an unknown mode, not silently resolve to anything."""
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "no_vendor_axis", "checked_at": "2026-09-12", "evidence": "x"}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_request_enumeration_dangling_code_source_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "request_enumeration", "code_source": "totally.not.a.real.module.Path"}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_code_exclude_invalid_date_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SECUCODE",
            "checked_at": "2026-13-40",
            "evidence": "x",
        }
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_code_exclude_empty_evidence_rejected(tmp_path):
    """S1-A2: isolates the evidence check — key set, code_field, checked_at
    all satisfy their own gates, only evidence violates."""
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SECUCODE",
            "checked_at": "2026-09-12",
            "evidence": "   ",
        }
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# code_exclude's code_field — L10 mirror image: must BE a security-code
# identity column (the exact opposite requirement from response_exclude's
# L10, which forbids one) — spec_bshare_b2.md §2.2/§5.1 S1-A2.
# ---------------------------------------------------------------------------


def test_l10_code_exclude_field_not_code_identity_column_rejected(tmp_path):
    """Isolates the code_field-identity check: mode/checked_at/evidence all
    valid, only code_field is a non-identity (vendor category) field name."""
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SECURITY_TYPE",
            "checked_at": "2026-09-12",
            "evidence": "x",
        }
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


def test_l10_code_exclude_field_accepts_any_registered_code_like_name(tmp_path):
    """Control case for the above: SYMBOL/CODE are also in
    _CODE_LIKE_FIELD_NAMES, not just SECUCODE/SECURITY_CODE."""
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SYMBOL",
            "checked_at": "2026-09-12",
            "evidence": "x",
        }
    }
    path = _write(tmp_path, data)
    scope = load_vendor_scope(path)
    assert scope.dispositions["srcx.apiy"]["b_share"].code_field == "SYMBOL"


# ---------------------------------------------------------------------------
# L10 — response_exclude field/value must not be code-identity-shaped
# ---------------------------------------------------------------------------


def test_l10_response_exclude_field_is_code_identity_column_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECUCODE", "values": ["EQB"]}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


def test_l10_response_exclude_value_looks_like_code_prefix_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["900"]}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


def test_l10_response_exclude_value_looks_like_full_code_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["900901.SH"]}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


def test_l10_response_exclude_field_matches_custom_security_code_column_rejected(tmp_path):
    """L10's code-identity blocklist is data-driven off the configured
    security_code_columns, not a hardcoded default set."""
    data = _minimal_valid_scope()
    data["security_code_columns"] = ["ts_code", "my_custom_code"]
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "my_custom_code", "values": ["EQB"]}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


def test_l10_response_exclude_duplicate_values_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["EQB", "EQB"]}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L10"):
        load_vendor_scope(path)


# ---------------------------------------------------------------------------
# vendor_exclusions() consumption API
# ---------------------------------------------------------------------------


def test_vendor_exclusions_raises_for_unregistered_source_api(tmp_path):
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    with pytest.raises(VendorScopeError):
        vendor_exclusions("nope", "nope", scope=scope)


def test_vendor_exclusions_returns_response_excludes_for_response_exclude_mode(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["EQB"]}
    }
    scope = load_vendor_scope(_write(tmp_path, data))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.request_filters == ()
    assert exclusions.response_excludes == (("SECURITY_TYPE", frozenset({"EQB"})),)


def test_vendor_exclusions_empty_for_no_op_modes(tmp_path):
    """request_enumeration / population_disjoint: the disposition must exist
    (checked above) but the adapter has nothing to act on at landing time."""
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions == vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.request_filters == ()
    assert exclusions.response_excludes == ()
    assert exclusions.code_excludes == ()


# ---------------------------------------------------------------------------
# S1-A3: vendor_exclusions() for code_exclude (spec_bshare_b2.md §5.1)
# ---------------------------------------------------------------------------


def test_vendor_exclusions_returns_code_excludes_for_code_exclude_mode(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SECUCODE",
            "checked_at": "2026-09-12",
            "evidence": "x",
        }
    }
    scope = load_vendor_scope(_write(tmp_path, data))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.request_filters == ()
    assert exclusions.response_excludes == ()
    assert exclusions.code_excludes == (
        ("SECUCODE", r"^(200\d{3}(\.SZ)?|900\d{3}(\.SH)?)$"),
    )


def test_vendor_exclusions_code_exclude_does_not_populate_response_excludes(tmp_path):
    """Isolates the branch dispatch: a code_exclude disposition must not also
    show up under response_excludes (mutation target: merging the two
    branches would make both fields non-empty for the same disposition)."""
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {
            "mode": "code_exclude",
            "code_field": "SECUCODE",
            "checked_at": "2026-09-12",
            "evidence": "x",
        }
    }
    scope = load_vendor_scope(_write(tmp_path, data))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.response_excludes == ()


# ---------------------------------------------------------------------------
# S1-A4: apply_response_excludes() — response_exclude and code_exclude vectors
# ---------------------------------------------------------------------------


def test_apply_response_excludes_response_exclude_vector():
    exclusions = VendorExclusions(
        request_filters=(),
        response_excludes=(("SECURITY_TYPE_CODE", frozenset({"058001002"})),),
    )
    rows = [
        {"SECURITY_TYPE_CODE": "058001002", "SECUCODE": "900910.SH"},  # dropped: B股
        {"SECURITY_TYPE_CODE": "058001001", "SECUCODE": "600000.SH"},  # kept: A股
        {"SECURITY_TYPE_CODE": "058001008", "SECUCODE": "688001.SH"},  # kept: 科创板
        {"SECUCODE": "300001.SZ"},  # kept: field missing entirely
    ]
    survivors, excluded = apply_response_excludes(rows, exclusions)
    assert excluded == 1
    assert {r["SECUCODE"] for r in survivors} == {"600000.SH", "688001.SH", "300001.SZ"}


def test_apply_response_excludes_code_exclude_vector():
    """Vector aligned with spec_bshare_b2.md §5.1 S1-A4: drop 900925.SH /
    200017.SZ / bare 900925 (with-suffix, with-suffix, no-suffix); keep
    600900.SH (lookalike substring "900"), 9000011 (7 digits, too long),
    920001.BJ (北交所, wrong prefix+suffix pair) and 110001 (unrelated code)."""
    exclusions = VendorExclusions(
        request_filters=(),
        response_excludes=(),
        code_excludes=(("SECUCODE", r"^(200\d{3}(\.SZ)?|900\d{3}(\.SH)?)$"),),
    )
    rows = [
        {"SECUCODE": "900925.SH"},
        {"SECUCODE": "200017.SZ"},
        {"SECUCODE": "900925"},
        {"SECUCODE": "600900.SH"},
        {"SECUCODE": "9000011"},
        {"SECUCODE": "920001.BJ"},
        {"SECUCODE": "110001"},
        {"OTHER_FIELD": "no code field on this row"},
    ]
    survivors, excluded = apply_response_excludes(rows, exclusions)
    assert excluded == 3
    assert {r.get("SECUCODE") for r in survivors} == {
        "600900.SH", "9000011", "920001.BJ", "110001", None,
    }


def test_apply_response_excludes_fullmatch_not_search():
    """Mutation target named in the spec table: switching fullmatch to search
    would wrongly drop 600900.SH (contains "900" as a substring, not a
    matching prefix)."""
    exclusions = VendorExclusions(
        request_filters=(),
        response_excludes=(),
        code_excludes=(("SECUCODE", r"^(200\d{3}(\.SZ)?|900\d{3}(\.SH)?)$"),),
    )
    survivors, excluded = apply_response_excludes([{"SECUCODE": "600900.SH"}], exclusions)
    assert excluded == 0
    assert survivors == [{"SECUCODE": "600900.SH"}]


def test_apply_response_excludes_fullmatch_not_search_unanchored_pattern():
    """S1-A4 blocking-review fix: every real ``code_excludes`` regex comes
    from ``class_regex``, which always wraps the alternation in ``^(...)$``
    — on a single-line string, ``re.search`` on a ``^``-anchored pattern
    only ever matches starting at index 0, same as ``re.fullmatch``, so the
    test above cannot actually observe a fullmatch-vs-search swap (it stays
    green either way). ``code_excludes`` only requires a compilable regex
    string, so this test bypasses ``class_regex`` and uses the *unanchored*
    ``pattern_regex()`` output directly to exercise the real distinction the
    S1-A4 mutation table names."""
    from services.data_sources.out_of_scope_codes import pattern_regex

    regex = pattern_regex("900", "SH")  # r"900\d{3}(\.SH)?" -- no ^/$ anchors
    exclusions = VendorExclusions(
        request_filters=(),
        response_excludes=(),
        code_excludes=(("SECUCODE", regex),),
    )
    # "900925.SH" sits as a trailing substring of "8900925.SH": re.search
    # would find that substring and wrongly drop the row; re.fullmatch
    # requires the whole field to match and correctly keeps it (the leading
    # "8" is not part of any B股 code pattern).
    rows = [{"SECUCODE": "8900925.SH"}]
    survivors, excluded = apply_response_excludes(rows, exclusions)
    assert excluded == 0
    assert survivors == rows


def test_apply_response_excludes_returns_zero_and_unchanged_rows_when_nothing_to_drop():
    exclusions = VendorExclusions(request_filters=(), response_excludes=())
    rows = [{"SECUCODE": "600000.SH"}]
    survivors, excluded = apply_response_excludes(rows, exclusions)
    assert excluded == 0
    assert survivors == rows


def test_vendor_exclusions_aggregates_across_multiple_classes(tmp_path):
    data = _minimal_valid_scope()
    # "70"/"BJ" deliberately does not collide with any universe_rules.yaml
    # board prefix — this class exists only to prove aggregation, not to
    # describe a real security category.
    data["out_of_scope_classes"]["other_class"] = {
        "ruling": "synthetic ruling for aggregation test",
        "code_patterns": [{"prefix": "70", "suffix": "BJ"}],
    }
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "response_exclude", "field": "SECURITY_TYPE", "values": ["EQB"]},
        "other_class": {"mode": "request_exclude", "filters": ["x=y"]},
    }
    scope = load_vendor_scope(_write(tmp_path, data))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.request_filters == ("x=y",)
    assert exclusions.response_excludes == (("SECURITY_TYPE", frozenset({"EQB"})),)


# ---------------------------------------------------------------------------
# out_of_scope_code_patterns()
# ---------------------------------------------------------------------------


def test_out_of_scope_code_patterns_returns_all_classes_patterns(tmp_path):
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    patterns = out_of_scope_code_patterns(scope=scope)
    assert {(p.prefix, p.suffix) for p in patterns} == {("900", "SH"), ("200", "SZ")}


# ---------------------------------------------------------------------------
# C1 boundary — code_patterns/out_of_scope_code_patterns/out_of_scope_codes is
# canonical/audit only; adapters and sync_runner must never import either
# (static grep, same method as check_dead_references.py's import scans).
# Widened 2026-09-19 (S1-A9, spec_bshare_b2.md §5.1): the ``code_exclude``
# mode's formula module (``out_of_scope_codes``) must reach adapters only via
# ``vendor_exclusions()``, never by direct import — same boundary as
# ``out_of_scope_code_patterns()``, same enforcement mechanism.
# ---------------------------------------------------------------------------

_BANNED_SYMBOL_RE = re.compile(r"out_of_scope_code_patterns|out_of_scope_codes")


def test_adapters_and_sync_runner_do_not_reference_out_of_scope_code_patterns():
    offenders: list[str] = []
    sources_dir = _BACKEND_DIR / "services" / "data_sources" / "sources"
    candidate_paths = sorted(sources_dir.glob("*.py"))
    sync_runner_path = _BACKEND_DIR / "services" / "data_sources" / "sync_runner.py"
    if sync_runner_path.exists():
        candidate_paths.append(sync_runner_path)
    for p in candidate_paths:
        text = p.read_text(encoding="utf-8")
        if _BANNED_SYMBOL_RE.search(text):
            offenders.append(str(p.relative_to(_BACKEND_DIR)))
    assert offenders == [], (
        "C1 violation: out_of_scope_code_patterns/out_of_scope_codes is "
        f"canonical/audit-only vocabulary, referenced from landing-side module(s): {offenders}"
    )


# ---------------------------------------------------------------------------
# S1-A9 (spec_bshare_b2.md §5.1): the four aif10/miaoxiang landing-side
# writers must never hardcode the vendor field name, the B股 value, or a bare
# "900"/"200" literal — every one of those three facts must come only from
# vendor_scope.yaml via vendor_exclusions(). Assertion is limited to non-
# comment lines so miaoxiang.py's documentation table (which legitimately
# quotes SECURITY_TYPE_CODE / 900 / 200 as historical field-mapping evidence)
# does not trip it.
# ---------------------------------------------------------------------------

_S1A9_FILES = (
    "services/holders_aif10.py",
    "services/org_holding_aif10.py",
    "services/qfii_client.py",
    "services/data_sources/sources/miaoxiang.py",
)
# Quoted-literal form only (not bare \b900\b/\b200\b): these four production
# files legitimately contain unrelated integers/prose that happen to spell
# "900" or "200" (e.g. a page-size default, a truncated-string slice length,
# a percentage in a comment-adjacent docstring sentence) — a bare word-
# boundary scan false-positives on those. What must never appear is the
# vendor field name or its B股 value, or "900"/"200" written as the quoted
# string literal a code_field/prefix check would actually use.
_S1A9_LITERAL_RE = re.compile(r"""['"]058001002['"]|SECURITY_TYPE_CODE|['"]900['"]|['"]200['"]""")


def _non_comment_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Drop a trailing "# ..." comment but keep code before it — a literal
        # inside a string that happens to contain "#" is not a realistic risk
        # for these four files (no such string literals exist in them).
        code_part = line.split("#", 1)[0] if "#" in line else line
        out.append(code_part)
    return out


def test_aif10_writers_never_hardcode_b_share_vendor_literals():
    offenders: list[str] = []
    for rel in _S1A9_FILES:
        p = _BACKEND_DIR / rel
        text = p.read_text(encoding="utf-8")
        for lineno, code_line in enumerate(_non_comment_lines(text), start=1):
            if _S1A9_LITERAL_RE.search(code_line):
                offenders.append(f"{rel}:{lineno}: {code_line.strip()!r}")
    assert offenders == [], (
        "S1-A9 violation: B股 vendor literal hardcoded outside a comment in a "
        f"landing-side writer (must come only from vendor_scope.yaml): {offenders}"
    )


# ---------------------------------------------------------------------------
# S1-A1: out_of_scope_codes.class_regex() byte-identical to the pre-existing
# check_out_of_scope_rows.py formula — the S1->S2 transition-period parity
# check (S2 hasn't switched to importing this module yet in this cut).
# ---------------------------------------------------------------------------


def test_class_regex_matches_check_out_of_scope_rows_formula():
    from scripts.check_out_of_scope_rows import load_scan_config as _load_scan_cfg
    from services.data_sources.out_of_scope_codes import class_regex

    scan_cfg = _load_scan_cfg()
    b_share_scan = next(c for c in scan_cfg.classes if c.name == "b_share")

    scope = load_vendor_scope()
    b_share_vendor = scope.out_of_scope_classes["b_share"]

    assert class_regex(b_share_vendor.code_patterns) == b_share_scan.regex


def test_class_regex_formula_matches_hardcoded_literal():
    """S1-A1 blocking-review fix: the test above now compares two call
    sites that both import the *same* ``class_regex`` (S2 of this cut
    switched ``check_out_of_scope_rows.py`` to import it from
    ``out_of_scope_codes`` too, per spec_bshare_b2.md §5.2(c)) — a defect
    inside the shared formula itself (e.g. dropping ``sorted``) moves both
    sides identically, so that comparison stays green and is now circular
    for bugs in the formula. This test has no config-loading call site on
    either side of the assertion: it pins the formula's output to a literal
    string, so it is the only test left that can catch a bug in the formula
    itself (as opposed to drift between two independently-loading callers)."""
    from services.data_sources.out_of_scope_codes import CodePattern, class_regex

    patterns = (
        CodePattern(prefix="900", suffix="SH"),
        CodePattern(prefix="200", suffix="SZ"),
    )
    assert class_regex(patterns) == r"^(200\d{3}(\.SZ)?|900\d{3}(\.SH)?)$"


# ---------------------------------------------------------------------------
# S1-A10: real vendor_scope.yaml — mode/field/values for the five B2 keys.
# ---------------------------------------------------------------------------


def test_real_config_top_inst_is_code_exclude_on_secucode():
    scope = load_vendor_scope()
    dispo = scope.dispositions["miaoxiang.top_inst"]["b_share"]
    assert dispo.mode == "code_exclude"
    assert dispo.code_field == "SECUCODE"
    exclusions = vendor_exclusions("miaoxiang", "top_inst", scope=scope)
    assert exclusions.code_excludes == (
        ("SECUCODE", r"^(200\d{3}(\.SZ)?|900\d{3}(\.SH)?)$"),
    )


def test_real_config_top_list_is_response_exclude_on_security_type_code():
    scope = load_vendor_scope()
    dispo = scope.dispositions["miaoxiang.top_list"]["b_share"]
    assert dispo.mode == "response_exclude"
    assert dispo.field_name == "SECURITY_TYPE_CODE"
    assert dispo.values == frozenset({"058001002"})


@pytest.mark.parametrize(
    "key",
    ["aif10.holders_top10", "aif10.org_holding", "aif10.qfii_holders"],
)
def test_real_config_aif10_writers_are_response_exclude_on_security_type_code(key):
    scope = load_vendor_scope()
    dispo = scope.dispositions[key]["b_share"]
    assert dispo.mode == "response_exclude"
    assert dispo.field_name == "SECURITY_TYPE_CODE"
    assert dispo.values == frozenset({"058001002"})
    # L11 second half needs these keys claimed by non_registry_sources (they
    # are not real sync_registry.yaml domains).
    assert key in scope.non_registry_sources


def test_real_config_qfii_key_renamed_from_rpt_dmsk_holders():
    scope = load_vendor_scope()
    assert "aif10.RPT_DMSK_HOLDERS" not in scope.dispositions
    assert "aif10.RPT_DMSK_HOLDERS" not in scope.non_registry_sources
    assert "aif10.qfii_holders" in scope.dispositions
    assert "aif10.qfii_holders" in scope.non_registry_sources


# ---------------------------------------------------------------------------
# L11 / L12 — static registry cross-checks (isolated formula tests)
# ---------------------------------------------------------------------------


def _l11_required_dispositions(registry: dict, security_code_columns: frozenset[str]) -> set[str]:
    """Mirrors spec §3.1 L11 first half: S = {"<source>.<api>":
    execution_policy.mode == enabled and (grain ∪ {universe_filter_col}) ∩
    security_code_columns != ∅}."""
    defaults = registry.get("defaults") or {}
    default_mode = (defaults.get("execution_policy") or {}).get("mode")
    domains = registry.get("domains") or {}
    required: set[str] = set()
    for domain_cfg in domains.values():
        mode = (domain_cfg.get("execution_policy") or {}).get("mode", default_mode)
        cols = set(domain_cfg.get("grain") or [])
        ufc = domain_cfg.get("universe_filter_col")
        if ufc:
            cols.add(ufc)
        if mode == "enabled" and (cols & security_code_columns):
            required.add(f"{domain_cfg.get('source')}.{domain_cfg.get('api')}")
    return required


def _l12_bad_universe_filter_cols(registry: dict, security_code_columns: frozenset[str]) -> list[str]:
    domains = registry.get("domains") or {}
    return [
        name
        for name, domain_cfg in domains.items()
        if domain_cfg.get("universe_filter_col")
        and domain_cfg.get("universe_filter_col") not in security_code_columns
    ]


def test_l11_enabled_domain_with_code_grain_missing_from_dispositions_is_flagged():
    registry = {
        "defaults": {"execution_policy": {"mode": "enabled"}},
        "domains": {
            "fake_domain": {
                "source": "fake_source",
                "api": "fake_api",
                "execution_policy": {"mode": "enabled"},
                "grain": ["trade_date", "ts_code"],
            },
        },
    }
    required = _l11_required_dispositions(registry, frozenset({"ts_code"}))
    assert required == {"fake_source.fake_api"}


def test_l11_disabled_domain_with_code_grain_is_not_required():
    registry = {
        "defaults": {"execution_policy": {"mode": "enabled"}},
        "domains": {
            "fake_domain": {
                "source": "fake_source",
                "api": "fake_api",
                "execution_policy": {"mode": "disabled"},
                "grain": ["trade_date", "ts_code"],
            },
        },
    }
    required = _l11_required_dispositions(registry, frozenset({"ts_code"}))
    assert required == set()


def test_l11_second_half_flags_disposition_key_absent_from_registry_and_non_registry():
    S = {"real_source.real_api"}
    non_registry_keys = {"aif10.something"}
    dispositions_keys = {"real_source.real_api", "aif10.something", "ghost_source.ghost_api"}
    dead = dispositions_keys - (S | non_registry_keys)
    assert dead == {"ghost_source.ghost_api"}


def test_l12_universe_filter_col_outside_security_code_columns_is_flagged():
    registry = {
        "domains": {
            "fake_domain": {
                "source": "fake_source",
                "api": "fake_api",
                "universe_filter_col": "secucode",
            },
        },
    }
    bad = _l12_bad_universe_filter_cols(registry, frozenset({"ts_code", "con_code"}))
    assert bad == ["fake_domain"]


def test_l12_universe_filter_col_inside_security_code_columns_is_not_flagged():
    registry = {
        "domains": {
            "fake_domain": {
                "source": "fake_source",
                "api": "fake_api",
                "universe_filter_col": "ts_code",
            },
        },
    }
    bad = _l12_bad_universe_filter_cols(registry, frozenset({"ts_code", "con_code"}))
    assert bad == []


# ---------------------------------------------------------------------------
# Real files — the actual enforcement point. This is what turns red when
# someone enables a new registry domain with a security-code grain column
# and forgets to register a vendor_scope disposition for it (L11), or typos
# a universe_filter_col (L12).
# ---------------------------------------------------------------------------


def test_repo_vendor_scope_yaml_loads():
    scope = load_vendor_scope()
    assert "b_share" in scope.out_of_scope_classes
    assert scope.dispositions  # non-empty


def test_real_registry_satisfies_l11_and_l12():
    scope = load_vendor_scope()
    registry_path = _BACKEND_DIR / "config" / "sync_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))

    required = _l11_required_dispositions(registry, scope.security_code_columns)
    missing = required - set(scope.dispositions)
    assert missing == set(), f"enabled domains missing a vendor_scope disposition: {sorted(missing)}"

    dead = set(scope.dispositions) - (required | set(scope.non_registry_sources))
    assert dead == set(), (
        f"vendor_scope dispositions reference (source, api) outside sync_registry "
        f"and outside non_registry_sources: {sorted(dead)}"
    )

    bad_ufc = _l12_bad_universe_filter_cols(registry, scope.security_code_columns)
    assert bad_ufc == [], f"universe_filter_col not in security_code_columns for domains: {bad_ufc}"
