from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from services.data_sources import margin_ingest
from services.data_sources.contracts import (
    dataset_contract_from_spec,
    load_dataset_contract,
)
from services.data_sources.margin_schema import (
    MARGIN_SCHEMA_CONTRACT,
    MARGIN_SCHEMA_HASH,
    MARGIN_SCHEMA_ID,
    margin_schema_contract_payload,
    schema_contract_hash,
)


NESTED_KEYS = {
    "dataset_id",
    "contract_version",
    "schema_id",
    "schema_hash",
    "coverage_start",
    "canonical_table",
    "owner",
    "writer",
    "criticality",
    "failure_policy",
    "allowed_fallbacks",
    "consumers",
    "retention",
    "rebuild_policy",
    "retirement_condition",
}


def _spec() -> dict:
    return {
        "source": "tushare",
        "api": "margin",
        "target_db": "tushare_raw",
        "target_table": "raw_tushare_margin",
        "grain": ["trade_date", "exchange_id"],
        "partition_by": ["trade_date"],
        "available_after": "t+1",
        "availability_policy": {
            "axis": "trading_day",
            "rule": "next_trading_session_at",
            "at": "09:00",
        },
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {"BSE": "20230213"},
        },
        "dataset_contract": {
            "dataset_id": "tier0.market_data.margin_exchange_daily",
            "contract_version": "2",
            "schema_id": MARGIN_SCHEMA_ID,
            "schema_hash": MARGIN_SCHEMA_HASH,
            "coverage_start": "20260715",
            "canonical_table": "canonical_margin_exchange_daily",
            "owner": "tier0.market_data",
            "writer": "services.data_sources.margin_acceptance",
            "criticality": "blocking",
            "failure_policy": "fail_closed",
            "allowed_fallbacks": [],
            "consumers": [
                "services.data_sources.margin_reconcile",
                "services.data_sources.margin_state",
            ],
            "retention": "permanent",
            "rebuild_policy": "replay_landing_then_refetch",
            "retirement_condition": "replacement_contract_and_consumer_cutover",
        },
    }


def _frozen_margin_transport_spec() -> dict:
    spec = _spec()
    spec.update(
        {
            "domain": "margin",
            "batch_mode": "by_trade_date",
            "date_param": "trade_date",
            "write_mode": "replace_partition",
            "split_by": {
                "param": "exchange_id",
                "values": ["SSE", "SZSE", "BSE"],
            },
        }
    )
    return spec


def test_dataset_contract_from_spec_builds_typed_margin_contract() -> None:
    contract = dataset_contract_from_spec("margin", _spec())

    assert contract.domain == "margin"
    assert contract.dataset_id == "tier0.market_data.margin_exchange_daily"
    assert contract.contract_version == "2"
    assert contract.schema_id == MARGIN_SCHEMA_ID
    assert contract.schema_hash == MARGIN_SCHEMA_HASH
    assert contract.coverage_start == "20260715"
    assert contract.source == "tushare"
    assert contract.api == "margin"
    assert contract.target_db == "tushare_raw"
    assert contract.canonical_table == "canonical_margin_exchange_daily"
    assert contract.compatibility_table == "raw_tushare_margin"
    assert not hasattr(contract, "target_table")
    assert contract.grain == ("trade_date", "exchange_id")
    assert contract.partition_by == "trade_date"
    assert contract.available_after == "t+1"
    assert contract.availability_policy.payload() == {
        "axis": "trading_day",
        "rule": "next_trading_session_at",
        "at": "09:00",
    }
    assert contract.batch_completeness.group_column == "exchange_id"
    assert contract.batch_completeness.group_transform == "identity"
    assert contract.batch_completeness.required_groups == ("SSE", "SZSE")
    assert contract.batch_completeness.required_groups_since == (("BSE", "20230213"),)
    assert contract.owner == "tier0.market_data"
    assert contract.writer == "services.data_sources.margin_acceptance"
    assert contract.consumers == (
        "services.data_sources.margin_reconcile",
        "services.data_sources.margin_state",
    )
    assert len(contract.contract_hash) == 64
    assert len(contract.config_hash) == 64


def test_typed_availability_rejects_conflicting_legacy_hint() -> None:
    spec = _spec()
    spec["available_after"] = "18:00"

    with pytest.raises(ValueError, match="conflicts with typed availability_policy"):
        dataset_contract_from_spec("margin", spec)


def test_contract_rejects_same_writer_surface_for_canonical_and_compatibility() -> None:
    spec = _spec()
    spec["target_table"] = spec["dataset_contract"]["canonical_table"]

    with pytest.raises(ValueError, match="different single writers"):
        dataset_contract_from_spec("margin", spec)


def test_load_dataset_contract_merges_default_target_db(tmp_path: Path) -> None:
    spec = _spec()
    spec.pop("target_db")
    path = tmp_path / "sync_registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {"defaults": {"target_db": "tushare_raw"}, "domains": {"margin": spec}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    contract = load_dataset_contract("margin", path)

    assert contract.target_db == "tushare_raw"


def test_repository_margin_entry_loads_as_the_formal_contract() -> None:
    contract = load_dataset_contract("margin")

    assert contract.dataset_id == "tier0.market_data.margin_exchange_daily"
    assert contract.writer == "services.data_sources.margin_acceptance"
    assert contract.failure_policy == "fail_closed"
    assert contract.allowed_fallbacks == ()
    assert contract.schema_id == MARGIN_SCHEMA_ID
    assert contract.schema_hash == MARGIN_SCHEMA_HASH


def test_frozen_margin_v2_transport_contract_is_exact_and_read_only() -> None:
    contract = margin_ingest.contract_for_spec(_frozen_margin_transport_spec())

    assert contract is not None
    assert contract.dataset_id == "tier0.market_data.margin_exchange_daily"
    assert contract.batch_completeness.required_groups_for("20230210") == (
        "SSE",
        "SZSE",
    )
    assert contract.batch_completeness.required_groups_for("20230213") == (
        "BSE",
        "SSE",
        "SZSE",
    )
    assert not hasattr(margin_ingest, "execute_partition")
    assert not hasattr(margin_ingest, "execute_partition_outcome")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("batch_mode", "by_date_range"),
        ("date_param", "end_date"),
        ("write_mode", "merge"),
        ("split_by", {"param": "exchange", "values": ["SSE", "SZSE", "BSE"]}),
        ("split_by", {"param": "exchange_id", "values": ["SSE", "SZSE"]}),
    ],
)
def test_frozen_margin_v2_rejects_transport_drift(key: str, value: object) -> None:
    spec = _frozen_margin_transport_spec()
    spec[key] = value

    with pytest.raises(ValueError, match="formal margin transport wiring drift"):
        margin_ingest.contract_for_spec(spec)


def test_repository_schema_fingerprint_matches_code_owned_semantics() -> None:
    contract = load_dataset_contract("margin")

    assert schema_contract_hash(MARGIN_SCHEMA_CONTRACT) == MARGIN_SCHEMA_HASH
    assert contract.schema_id == MARGIN_SCHEMA_ID
    assert contract.schema_hash == MARGIN_SCHEMA_HASH


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("schema_id", "schema id with spaces", "schema_id.*stable dotted"),
        ("schema_hash", "A" * 64, "schema_hash.*lowercase hex"),
        ("schema_hash", "a" * 63, "schema_hash.*64 lowercase hex"),
    ],
)
def test_dataset_contract_rejects_invalid_schema_identity(
    key: str, value: str, message: str
) -> None:
    spec = _spec()
    spec["dataset_contract"][key] = value

    with pytest.raises(ValueError, match=message):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("fields", 2, "unit"), "share"),
        (("fields", 5, "null_semantics"), "zero_fill"),
        (("time_semantics", "available_time"), "latest_snapshot"),
        (("primary_key", 1), "provider_code"),
        (("lineage", "input_snapshot"), "latest_rows"),
    ],
)
def test_schema_hash_changes_for_every_semantic_boundary(
    path: tuple[object, ...], replacement: str
) -> None:
    changed = margin_schema_contract_payload()
    cursor = changed
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement

    assert schema_contract_hash(changed) != MARGIN_SCHEMA_HASH


def test_semantic_schema_declares_key_units_nulls_times_and_lineage() -> None:
    fields = {
        field["name"]: field for field in MARGIN_SCHEMA_CONTRACT["fields"]
    }

    assert tuple(MARGIN_SCHEMA_CONTRACT["primary_key"]) == ("trade_date", "exchange_id")
    assert MARGIN_SCHEMA_CONTRACT["duplicate_policy"] == "reject"
    assert {fields[name]["unit"] for name in ("rzye", "rzmre", "rzche", "rqye", "rzrqye")} == {"CNY"}
    assert {fields[name]["unit"] for name in ("rqmcl", "rqyl")} == {"share"}
    assert fields["rqye"]["nullable"] is True
    assert "never_zero_fill" in fields["rqye"]["null_semantics"]
    assert fields["rzye"]["nullable"] is False
    assert set(MARGIN_SCHEMA_CONTRACT["time_semantics"]) == {
        "event_time",
        "effective_time",
        "observed_time",
        "available_time",
        "built_time",
    }
    assert (
        MARGIN_SCHEMA_CONTRACT["time_semantics"]["available_time"]["constraint"]
        == "available_at_equals_observed_at"
    )
    assert set(MARGIN_SCHEMA_CONTRACT["lineage"]) == {
        "landing",
        "input_snapshot",
        "source_batch",
        "definition",
        "accepted_pointer",
    }


def test_semantic_schema_constant_is_deeply_immutable() -> None:
    with pytest.raises(TypeError):
        MARGIN_SCHEMA_CONTRACT["fields"][0]["unit"] = "latest_guess"


def test_tushare_manifest_names_each_formal_margin_table_exactly() -> None:
    from services.database_manifest import get_database_manifest

    patterns = set(get_database_manifest().require("tushare_raw").table_patterns)

    assert {
        "ingest_batch",
        "landing_tushare_margin",
        "canonical_margin_exchange_daily",
        "accepted_partition",
    }.issubset(patterns)
    assert "canonical_*" not in patterns
    assert "landing_*" not in patterns


def test_nested_dataset_contract_rejects_unknown_key() -> None:
    spec = _spec()
    spec["dataset_contract"]["publish_dag"] = "execute_python"

    with pytest.raises(ValueError, match="unknown.*publish_dag"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize("key", sorted(NESTED_KEYS))
def test_nested_dataset_contract_rejects_every_missing_key(key: str) -> None:
    spec = _spec()
    del spec["dataset_contract"][key]

    with pytest.raises(ValueError, match=rf"missing.*{key}"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize(
    "key",
    [
        "source",
        "api",
        "target_db",
        "target_table",
        "grain",
        "partition_by",
        "available_after",
        "availability_policy",
        "batch_completeness",
    ],
)
def test_dataset_contract_rejects_missing_outer_contract_field(key: str) -> None:
    spec = _spec()
    del spec[key]

    with pytest.raises(ValueError, match=rf"missing.*{key}"):
        dataset_contract_from_spec("margin", spec)


def test_dataset_contract_rejects_duplicate_grain() -> None:
    spec = _spec()
    spec["grain"] = ["trade_date", "exchange_id", "trade_date"]

    with pytest.raises(ValueError, match="grain.*duplicate"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize("partition_by", [[], ["trade_date", "exchange_id"]])
def test_dataset_contract_requires_exactly_one_partition(partition_by: list[str]) -> None:
    spec = _spec()
    spec["partition_by"] = partition_by

    with pytest.raises(ValueError, match="partition_by.*exactly one"):
        dataset_contract_from_spec("margin", spec)


def test_dataset_contract_requires_partition_to_be_in_grain() -> None:
    spec = _spec()
    spec["partition_by"] = ["report_date"]

    with pytest.raises(ValueError, match="partition_by.*grain"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("owner", ""),
        ("writer", "  "),
        ("consumers", []),
        ("consumers", [""]),
    ],
)
def test_dataset_contract_rejects_empty_ownership_or_consumers(key: str, value: object) -> None:
    spec = _spec()
    spec["dataset_contract"][key] = value

    with pytest.raises(ValueError, match=key):
        dataset_contract_from_spec("margin", spec)


def test_dataset_contract_requires_fail_closed() -> None:
    spec = _spec()
    spec["dataset_contract"]["failure_policy"] = "warn"

    with pytest.raises(ValueError, match="failure_policy.*fail_closed"):
        dataset_contract_from_spec("margin", spec)


def test_dataset_contract_forbids_fallbacks() -> None:
    spec = _spec()
    spec["dataset_contract"]["allowed_fallbacks"] = ["legacy_raw"]

    with pytest.raises(ValueError, match="allowed_fallbacks.*empty"):
        dataset_contract_from_spec("margin", spec)


def test_contract_and_config_hashes_ignore_mapping_key_order() -> None:
    left = _spec()
    right = {
        key: deepcopy(left[key])
        for key in reversed(left)
    }
    right["dataset_contract"] = {
        key: right["dataset_contract"][key]
        for key in reversed(right["dataset_contract"])
    }
    right["batch_completeness"] = {
        key: right["batch_completeness"][key]
        for key in reversed(right["batch_completeness"])
    }
    right["batch_completeness"]["group_from"] = {
        key: right["batch_completeness"]["group_from"][key]
        for key in reversed(right["batch_completeness"]["group_from"])
    }

    left_contract = dataset_contract_from_spec("margin", left)
    right_contract = dataset_contract_from_spec("margin", right)

    assert left_contract.contract_hash == right_contract.contract_hash
    assert left_contract.config_hash == right_contract.config_hash


def test_contract_and_config_hashes_change_with_contract_content() -> None:
    left = _spec()
    right = _spec()
    right["dataset_contract"]["retention"] = "seven_years"

    left_contract = dataset_contract_from_spec("margin", left)
    right_contract = dataset_contract_from_spec("margin", right)

    assert left_contract.contract_hash != right_contract.contract_hash
    assert left_contract.config_hash != right_contract.config_hash


def test_contract_hashes_cover_typed_availability_policy() -> None:
    left = _spec()
    right = _spec()
    right["availability_policy"]["at"] = "09:01"

    left_contract = dataset_contract_from_spec("margin", left)
    right_contract = dataset_contract_from_spec("margin", right)

    assert left_contract.contract_hash != right_contract.contract_hash
    assert left_contract.config_hash != right_contract.config_hash


@pytest.mark.parametrize(
    "policy",
    [
        {"axis": "trading_day", "rule": "next_trading_session_at"},
        {
            "axis": "calendar_day",
            "rule": "next_trading_session_at",
            "at": "09:00",
        },
        {
            "axis": "trading_day",
            "rule": "next_trading_session_at",
            "at": "25:00",
        },
        {
            "axis": "trading_day",
            "rule": "next_trading_session_at",
            "at": "09:00",
            "domain_switch": "margin",
        },
    ],
)
def test_dataset_contract_rejects_malformed_availability_policy(policy) -> None:
    spec = _spec()
    spec["availability_policy"] = policy

    with pytest.raises(ValueError, match="availability"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize(
    ("surface", "replacement"),
    [
        ("canonical", "canonical_margin_exchange_daily_v2"),
        ("compatibility", "raw_tushare_margin_shadow"),
    ],
)
def test_contract_hashes_cover_canonical_and_compatibility_surfaces(
    surface: str, replacement: str
) -> None:
    left = _spec()
    right = _spec()
    if surface == "canonical":
        right["dataset_contract"]["canonical_table"] = replacement
    else:
        right["target_table"] = replacement

    left_contract = dataset_contract_from_spec("margin", left)
    right_contract = dataset_contract_from_spec("margin", right)

    assert left_contract.contract_hash != right_contract.contract_hash
    assert left_contract.config_hash != right_contract.config_hash


@pytest.mark.parametrize(
    ("surface", "value"),
    [
        ("canonical", "canonical_margin; DROP TABLE accepted_partition"),
        ("compatibility", "raw_tushare_margin where 1=1"),
        ("canonical", "schema.canonical_margin"),
    ],
)
def test_contract_rejects_unsafe_table_identifiers(surface: str, value: str) -> None:
    spec = _spec()
    if surface == "canonical":
        spec["dataset_contract"]["canonical_table"] = value
    else:
        spec["target_table"] = value

    with pytest.raises(ValueError, match="SQL table identifier"):
        dataset_contract_from_spec("margin", spec)


def test_required_groups_for_uses_the_configured_effective_date() -> None:
    spec = _spec()
    spec["batch_completeness"]["required_groups_since"]["BSE"] = "20240101"
    completeness = dataset_contract_from_spec("margin", spec).batch_completeness

    assert completeness.required_groups_for("20231229") == ("SSE", "SZSE")
    assert completeness.required_groups_for("20240101") == ("BSE", "SSE", "SZSE")


@pytest.mark.parametrize("bad_date", ["2026-07-15", "202607", "20260229"])
def test_dataset_contract_coverage_start_requires_a_real_compact_date(bad_date: str) -> None:
    spec = _spec()
    spec["dataset_contract"]["coverage_start"] = bad_date

    with pytest.raises(ValueError, match="coverage_start.*valid YYYYMMDD"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize(
    "bad_date",
    ["2023-02-13", "202302", "not-a-date", "20230229", "20231301", "00000000"],
)
def test_required_group_since_requires_a_real_compact_date(bad_date: str) -> None:
    spec = _spec()
    spec["batch_completeness"]["required_groups_since"]["BSE"] = bad_date

    with pytest.raises(ValueError, match="required_groups_since.*valid YYYYMMDD"):
        dataset_contract_from_spec("margin", spec)


@pytest.mark.parametrize("bad_partition", ["20230230", "20231301", "2023-02-13"])
def test_required_groups_for_rejects_an_invalid_partition(bad_partition: str) -> None:
    completeness = dataset_contract_from_spec("margin", _spec()).batch_completeness

    with pytest.raises(ValueError, match="partition.*valid YYYYMMDD"):
        completeness.required_groups_for(bad_partition)


@pytest.mark.parametrize(
    ("location", "bad_group"),
    [
        ("required", "sse"),
        ("required", " SSE"),
        ("since", "bse"),
        ("since", "B-SE"),
    ],
)
def test_batch_groups_require_canonical_tokens(location: str, bad_group: str) -> None:
    spec = _spec()
    if location == "required":
        spec["batch_completeness"]["required_groups"][0] = bad_group
    else:
        spec["batch_completeness"]["required_groups_since"] = {
            bad_group: "20230213"
        }

    with pytest.raises(ValueError, match="group.*canonical"):
        dataset_contract_from_spec("margin", spec)


def test_required_group_cannot_also_be_effective_dated() -> None:
    spec = _spec()
    spec["batch_completeness"]["required_groups_since"]["SSE"] = "20230213"

    with pytest.raises(ValueError, match="required group cannot also"):
        dataset_contract_from_spec("margin", spec)


def test_batch_group_transform_must_be_supported() -> None:
    spec = _spec()
    spec["batch_completeness"]["group_from"]["transform"] = "execute_python"

    with pytest.raises(ValueError, match="group.*transform"):
        dataset_contract_from_spec("margin", spec)
