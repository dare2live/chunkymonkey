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
    VendorScopeError,
    landing_tables_with_no_vendor_axis,
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
                    "mode": "no_vendor_axis",
                    "checked_at": "2026-09-12",
                    "evidence": "test evidence",
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
    assert scope.dispositions["srcx.apiy"]["b_share"].mode == "no_vendor_axis"
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
        {"mode": "no_vendor_axis", "checked_at": "2026-09-12"},
        {"mode": "request_enumeration"},
        {"mode": "population_disjoint"},
    ],
    ids=["request_exclude", "response_exclude", "no_vendor_axis", "request_enumeration", "population_disjoint"],
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
        {"mode": "no_vendor_axis", "checked_at": "2026-09-12", "evidence": "x", "extra": 1},
        {"mode": "request_enumeration", "code_source": "os.path", "extra": 1},
        {"mode": "population_disjoint", "why": "x", "extra": 1},
    ],
    ids=["request_exclude", "response_exclude", "no_vendor_axis", "request_enumeration", "population_disjoint"],
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


def test_l9_request_enumeration_dangling_code_source_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "request_enumeration", "code_source": "totally.not.a.real.module.Path"}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


def test_l9_no_vendor_axis_invalid_date_rejected(tmp_path):
    data = _minimal_valid_scope()
    data["dispositions"]["srcx.apiy"] = {
        "b_share": {"mode": "no_vendor_axis", "checked_at": "2026-13-40", "evidence": "x"}
    }
    path = _write(tmp_path, data)
    with pytest.raises(ValueError, match=r"vendor_scope: L9"):
        load_vendor_scope(path)


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
    """no_vendor_axis / request_enumeration / population_disjoint: the
    disposition must exist (checked above) but the adapter has nothing to act
    on at landing time."""
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    exclusions = vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions == vendor_exclusions("srcx", "apiy", scope=scope)
    assert exclusions.request_filters == ()
    assert exclusions.response_excludes == ()


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
# out_of_scope_code_patterns() / landing_tables_with_no_vendor_axis()
# ---------------------------------------------------------------------------


def test_out_of_scope_code_patterns_returns_all_classes_patterns(tmp_path):
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    patterns = out_of_scope_code_patterns(scope=scope)
    assert {(p.prefix, p.suffix) for p in patterns} == {("900", "SH"), ("200", "SZ")}


def test_landing_tables_with_no_vendor_axis_filters_by_mode_and_registry(tmp_path):
    scope = load_vendor_scope(_write(tmp_path, _minimal_valid_scope()))
    registry = {
        "sources": {"srcx": {"target_db": "some_db"}},
        "domains": {
            "d1": {"source": "srcx", "api": "apiy", "target_table": "raw_srcx_apiy"},
            "d2": {
                "source": "other",
                "api": "thing",
                "target_table": "raw_other_thing",
                "target_db": "other_db",
            },
        },
    }
    tables = landing_tables_with_no_vendor_axis(registry, scope=scope)
    assert tables == frozenset({("some_db", "raw_srcx_apiy")})


# ---------------------------------------------------------------------------
# C1 boundary — code_patterns/out_of_scope_code_patterns is canonical/audit
# only; adapters and sync_runner must never import it (static grep, same
# method as check_dead_references.py's import scans).
# ---------------------------------------------------------------------------

_BANNED_SYMBOL_RE = re.compile(r"out_of_scope_code_patterns")


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
        "C1 violation: out_of_scope_code_patterns is canonical/audit-only "
        f"vocabulary, referenced from landing-side module(s): {offenders}"
    )


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
