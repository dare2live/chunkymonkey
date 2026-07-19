from __future__ import annotations

from dataclasses import replace
from datetime import time
from pathlib import Path

import pytest
import yaml

from services.data_sources.availability import AvailabilityPolicy
from services.data_sources.contracts import BatchCompletenessContract, DatasetContract
from services.data_sources.population_scope import (
    ExternalAggregateScope,
    ProjectUniversePitScope,
    RawEvidenceScope,
    bind_execution_contract,
    require_same_execution_contract,
    verify_execution_contract,
)
from services.universe import UniversePolicy, load_universe_policy


def _dataset(
    *,
    grain: tuple[str, ...] = ("trade_date", "exchange_id"),
    partition_by: str = "trade_date",
) -> DatasetContract:
    return DatasetContract(
        domain="margin",
        dataset_id="tier0.market_data.margin_exchange_daily",
        contract_version="2",
        schema_id="tier0.market_data.margin_exchange_daily.canonical",
        schema_hash="1" * 64,
        coverage_start="20260715",
        owner="tier0.market_data",
        writer="services.data_sources.margin_acceptance",
        criticality="blocking",
        failure_policy="fail_closed",
        allowed_fallbacks=(),
        consumers=("services.data_sources.margin_state",),
        retention="permanent",
        rebuild_policy="replay_landing_then_refetch",
        retirement_condition="replacement_contract_and_consumer_cutover",
        source="tushare",
        api="margin",
        target_db="data/tushare_raw.duckdb",
        canonical_table="canonical_margin_exchange_daily",
        compatibility_table="raw_tushare_margin",
        grain=grain,
        partition_by=partition_by,
        available_after="t+1",
        availability_policy=AvailabilityPolicy(
            axis="trading_day",
            rule="next_trading_session_at",
            at=time(9, 0),
        ),
        batch_completeness=BatchCompletenessContract(
            group_column=grain[-1],
            group_transform="identity",
            required_groups=("SSE", "SZSE"),
            required_groups_since=(),
        ),
        contract_hash="2" * 64,
        config_hash="3" * 64,
    )


def _policy() -> UniversePolicy:
    return load_universe_policy()


def _policy_version(tmp_path: Path, version: int) -> UniversePolicy:
    source = Path(__file__).resolve().parents[2] / "config" / "universe_rules.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["policy"]["version"] = version
    target = tmp_path / f"universe-v{version}.yaml"
    target.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return load_universe_policy(target)


def _external_spec(**updates: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE", "BSE"],
        "population_label": "venue_reported_margin_population",
        "method": "tushare_margin_exchange_summary",
        "unit": "provider_declared_fields",
    }
    scope.update(updates)
    return {"population_scope": scope}


def _project_spec(**updates: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "kind": "project_universe_pit",
        "universe_policy_id": "active_a_share_trading_universe",
        "security_field": "ts_code",
        "as_of_field": "trade_date",
        "as_of_role": "observation_time",
    }
    scope.update(updates)
    return {"population_scope": scope}


def _raw_spec(**updates: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "kind": "raw_evidence",
        "population_label": "provider_response",
        "usage": "evidence_only",
    }
    scope.update(updates)
    return {"population_scope": scope}


def test_frozen_v2_without_population_scope_fails_closed():
    dataset = _dataset()
    assert dataset.contract_version == "2"

    with pytest.raises(ValueError, match="missing population_scope"):
        bind_execution_contract(dataset, {"dataset_contract": {"contract_version": "2"}}, None)


@pytest.mark.parametrize("bad_scope", [None, [], "external_aggregate"])
def test_population_scope_must_be_a_mapping(bad_scope):
    with pytest.raises(ValueError, match="population_scope must be a mapping"):
        bind_execution_contract(_dataset(), {"population_scope": bad_scope}, None)


def test_external_aggregate_binds_fixed_raw_landing_without_project_policy():
    dataset = _dataset()
    bound = bind_execution_contract(dataset, _external_spec(), None)

    assert bound.dataset is dataset
    assert bound.landing_scope == RawEvidenceScope("provider_response")
    assert bound.accepted_scope == ExternalAggregateScope(
        venue_field="exchange_id",
        venue_ids=("BSE", "SSE", "SZSE"),
        population_label="venue_reported_margin_population",
        method="tushare_margin_exchange_summary",
        unit="provider_declared_fields",
    )
    assert bound.universe_policy is None
    assert len(bound.execution_hash) == 64
    assert dataset.contract_hash == "2" * 64
    assert dataset.config_hash == "3" * 64


def test_external_aggregate_rejects_injected_project_policy():
    with pytest.raises(ValueError, match="external_aggregate.*policy must be None"):
        bind_execution_contract(_dataset(), _external_spec(), _policy())


@pytest.mark.parametrize("missing", ["venue_ids", "population_label", "method", "unit"])
def test_external_aggregate_requires_semantic_labels(missing):
    spec = _external_spec()
    del spec["population_scope"][missing]  # type: ignore[index]

    with pytest.raises(ValueError, match=rf"missing population_scope keys: {missing}"):
        bind_execution_contract(_dataset(), spec, None)


def test_external_aggregate_venue_field_must_be_in_dataset_grain():
    with pytest.raises(ValueError, match="venue_field must be in dataset grain"):
        bind_execution_contract(
            _dataset(), _external_spec(venue_field="venue"), None
        )


@pytest.mark.parametrize("bad", [None, [], ["SSE", "SSE"], ["sse"]])
def test_external_aggregate_venue_population_is_strict(bad):
    with pytest.raises(ValueError, match="population_scope.venue_ids"):
        bind_execution_contract(_dataset(), _external_spec(venue_ids=bad), None)


def test_external_aggregate_venue_population_changes_execution_hash():
    dataset = _dataset()
    two_venues = bind_execution_contract(
        dataset, _external_spec(venue_ids=["SSE", "SZSE"]), None
    )
    three_venues = bind_execution_contract(dataset, _external_spec(), None)

    assert two_venues.execution_hash != three_venues.execution_hash


def test_external_aggregate_venue_order_does_not_change_scope_or_hash():
    dataset = _dataset()
    configured = bind_execution_contract(dataset, _external_spec(), None)
    reordered = bind_execution_contract(
        dataset,
        _external_spec(venue_ids=["BSE", "SSE", "SZSE"]),
        None,
    )

    assert configured.accepted_scope == reordered.accepted_scope
    assert configured.execution_hash == reordered.execution_hash


def test_project_scope_requires_and_retains_exact_injected_policy():
    dataset = _dataset(grain=("ts_code", "trade_date"))
    policy = _policy()

    bound = bind_execution_contract(dataset, _project_spec(), policy)

    assert bound.dataset is dataset
    assert bound.accepted_scope == ProjectUniversePitScope(
        universe_policy_id=policy.policy_id,
        security_field="ts_code",
        as_of_field="trade_date",
        as_of_role="observation_time",
    )
    assert bound.universe_policy is policy


def test_project_scope_requires_an_injected_policy():
    with pytest.raises(ValueError, match="project_universe_pit.*policy is required"):
        bind_execution_contract(
            _dataset(grain=("ts_code", "trade_date")), _project_spec(), None
        )


def test_project_scope_rejects_policy_id_mismatch():
    with pytest.raises(ValueError, match="universe_policy_id.*does not match"):
        bind_execution_contract(
            _dataset(grain=("ts_code", "trade_date")),
            _project_spec(universe_policy_id="different_policy"),
            _policy(),
        )


@pytest.mark.parametrize("missing", ["security_field", "as_of_field", "as_of_role"])
def test_project_scope_requires_security_and_as_of_fields(missing):
    spec = _project_spec()
    del spec["population_scope"][missing]  # type: ignore[index]

    with pytest.raises(ValueError, match=rf"missing population_scope keys: {missing}"):
        bind_execution_contract(
            _dataset(grain=("ts_code", "trade_date")), spec, _policy()
        )


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"security_field": "stock_code"}, "security_field must be in dataset grain"),
        ({"as_of_field": "ann_date"}, "as_of_field must be in dataset grain"),
    ],
)
def test_project_scope_fields_must_be_in_dataset_grain(updates, match):
    with pytest.raises(ValueError, match=match):
        bind_execution_contract(
            _dataset(grain=("ts_code", "trade_date")),
            _project_spec(**updates),
            _policy(),
        )


def test_project_scope_rejects_availability_time_as_population_anchor():
    with pytest.raises(ValueError, match="as_of_role must be 'observation_time'"):
        bind_execution_contract(
            _dataset(
                grain=("ts_code", "report_date", "ann_date"),
                partition_by="report_date",
            ),
            _project_spec(as_of_field="ann_date", as_of_role="availability_time"),
            _policy(),
        )


def test_project_scope_rejects_exchange_aggregate_masquerading_as_securities():
    with pytest.raises(ValueError, match="security_field must be 'ts_code'"):
        bind_execution_contract(
            _dataset(grain=("trade_date", "exchange_id")),
            _project_spec(security_field="exchange_id"),
            _policy(),
        )


def test_project_scope_rejects_unknown_as_of_role():
    with pytest.raises(ValueError, match="unsupported.*as_of_role"):
        bind_execution_contract(
            _dataset(grain=("ts_code", "trade_date")),
            _project_spec(as_of_role="latest"),
            _policy(),
        )


def test_execution_hash_changes_with_population_scope_and_policy_hash(tmp_path):
    external_a = bind_execution_contract(_dataset(), _external_spec(), None)
    external_b = bind_execution_contract(
        _dataset(), _external_spec(method="different_method"), None
    )
    assert external_a.execution_hash != external_b.execution_hash

    project_dataset = _dataset(grain=("ts_code", "trade_date"))
    project_a = bind_execution_contract(project_dataset, _project_spec(), _policy())
    project_b = bind_execution_contract(
        project_dataset, _project_spec(), _policy_version(tmp_path, 4)
    )
    assert project_a.execution_hash != project_b.execution_hash


def test_execution_hash_binds_policy_version_and_dataset_config_hash(tmp_path):
    dataset = _dataset(grain=("ts_code", "trade_date"))
    policy = _policy()
    base = bind_execution_contract(dataset, _project_spec(), policy)

    new_policy_version = bind_execution_contract(
        dataset,
        _project_spec(),
        _policy_version(tmp_path, policy.policy_version + 1),
    )
    new_dataset_config = bind_execution_contract(
        replace(dataset, config_hash="6" * 64),
        _project_spec(),
        policy,
    )

    assert base.execution_hash != new_policy_version.execution_hash
    assert base.execution_hash != new_dataset_config.execution_hash


@pytest.mark.parametrize("field", ["contract_hash", "config_hash"])
def test_dataset_hashes_must_be_valid(field):
    dataset = replace(_dataset(), **{field: "fake"})
    with pytest.raises(ValueError, match=field):
        bind_execution_contract(dataset, _external_spec(), None)


def test_execution_contract_cannot_be_constructed_without_binder():
    from services.data_sources.population_scope import DatasetExecutionContract

    with pytest.raises(TypeError, match="bind_execution_contract"):
        DatasetExecutionContract(
            _dataset(),
            RawEvidenceScope("fake"),
            ProjectUniversePitScope(
                "wrong",
                "x",
                "x",
                "observation_time",
            ),
            None,
            "fake",
        )


def test_verify_rejects_tampered_execution_hash():
    bound = bind_execution_contract(_dataset(), _external_spec(), None)
    object.__setattr__(bound, "execution_hash", "0" * 64)

    with pytest.raises(ValueError, match="execution_hash does not match"):
        verify_execution_contract(bound)


def test_require_same_execution_contract_proves_object_identity():
    first = bind_execution_contract(_dataset(), _external_spec(), None)
    second = bind_execution_contract(_dataset(), _external_spec(), None)

    assert require_same_execution_contract(first, first) is first
    with pytest.raises(ValueError, match="identity mismatch"):
        require_same_execution_contract(first, second)
    with pytest.raises(ValueError, match="not propagated"):
        require_same_execution_contract(first, None)


@pytest.mark.parametrize(
    ("spec", "policy"),
    [
        (_external_spec(extra="forbidden"), None),
        (_project_spec(extra="forbidden"), _policy()),
    ],
)
def test_population_scope_unknown_keys_fail_closed(spec, policy):
    dataset = (
        _dataset(grain=("ts_code", "trade_date"))
        if policy is not None
        else _dataset()
    )
    with pytest.raises(ValueError, match="unknown population_scope keys: extra"):
        bind_execution_contract(dataset, spec, policy)


def test_raw_evidence_can_be_accepted_only_as_provider_evidence():
    bound = bind_execution_contract(_dataset(), _raw_spec(), None)

    assert bound.landing_scope == RawEvidenceScope("provider_response")
    assert bound.accepted_scope == RawEvidenceScope("provider_response")
    assert bound.universe_policy is None


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"population_label": "project_universe"}, "population_label"),
        ({"usage": "serve"}, "usage"),
    ],
)
def test_raw_evidence_cannot_masquerade_as_a_serving_population(updates, match):
    with pytest.raises(ValueError, match=match):
        bind_execution_contract(_dataset(), _raw_spec(**updates), None)
