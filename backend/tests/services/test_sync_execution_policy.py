from __future__ import annotations

from argparse import Namespace
import json

import pytest

from services.data_sources import margin_ingest
from services.data_sources import margin_acceptance
from services.data_sources import sync_runner as sr
from services.data_sources.margin_schema import MarginAcceptanceError


def _registry() -> dict:
    return {
        "defaults": {
            "target_db": "tushare_raw",
            "fetch_timeout_seconds": 120,
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {
            "daily": {
                "source": "tushare",
                "api": "daily",
                "target_table": "raw_tushare_daily",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "partition_by": ["trade_date"],
                "available_after": "18:00",
                "data_start": "20200102",
            },
            "margin": {
                "source": "tushare",
                "api": "margin",
                "target_table": "raw_tushare_margin",
                "grain": ["trade_date", "exchange_id"],
                "batch_mode": "by_trade_date",
                "partition_by": ["trade_date"],
                "available_after": "t+1",
                "data_start": "20190102",
                "execution_policy": {
                    "mode": "disabled",
                    "reason": "scope_blocked",
                },
            },
        },
    }


def _args(*, drain: bool = False, all_due: bool = False) -> Namespace:
    return Namespace(
        domain=None if all_due else "margin",
        all_due=all_due,
        backfill=False,
        resume=False,
        start=None,
        end=None,
        drain=drain,
        max_dates=None,
    )


def _forbidden(name: str):
    def fail(*_args, **_kwargs):
        pytest.fail(f"disabled execution reached forbidden side effect: {name}")

    return fail


def test_live_margin_v2_is_disabled_but_read_only_contract_still_loads():
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "margin")

    assert spec["dataset_contract"]["contract_version"] == "2"
    assert spec["execution_policy"] == {
        "mode": "disabled",
        "reason": "scope_blocked",
    }
    assert spec["population_scope"] == {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE", "BSE"],
        "population_label": "venue_reported_margin_population",
        "method": "tushare_margin_exchange_summary",
        "unit": "provider_declared_fields",
    }
    contract = margin_ingest.contract_for_spec(spec)
    assert contract is not None
    assert contract.contract_version == "2"


def test_live_trade_calendar_legacy_writer_is_disabled_before_any_side_effect(
    monkeypatch,
):
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "trade_cal")

    assert spec["execution_policy"] == {
        "mode": "disabled",
        "reason": "accepted_generation_pending",
    }
    assert spec["calendar_generation"] == {
        "contract_version": "1",
        "coverage_start": "19901219",
        "required_through_rule": "observed_year_end",
        "timezone": "Asia/Shanghai",
        "availability_rule": "response_completed",
        "canonicalization_version": "1",
    }
    assert spec["population_scope"] == {
        "kind": "external_aggregate",
        "venue_field": "exchange",
        "venue_ids": ["SSE"],
        "population_label": "sse_trading_calendar",
        "method": "tushare_trade_cal",
        "unit": "calendar_day_status",
    }

    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.ExecutionPolicyError,
        match="trade_cal.*accepted_generation_pending",
    ) as caught:
        sr.run_domain("trade_cal", registry=registry)

    assert caught.value.reason == "accepted_generation_pending"


@pytest.mark.parametrize(
    ("writer", "args"),
    [
        (margin_acceptance.land_margin_batch, (object(),)),
        (margin_acceptance.accept_margin_batch, ("batch",)),
        (margin_acceptance.recover_margin_batch, ("batch",)),
    ],
)
def test_direct_margin_writer_functions_cannot_mutate_frozen_live_db(writer, args):
    class _Cursor:
        def fetchall(self):
            return [(0, "tushare_raw", str(margin_acceptance._FROZEN_LIVE_DB))]

    class _LiveConnection:
        def execute(self, sql, *_args, **_kwargs):
            assert sql == "PRAGMA database_list"
            return _Cursor()

    with pytest.raises(MarginAcceptanceError, match="live writes are frozen"):
        writer(_LiveConnection(), *args)


@pytest.mark.parametrize("entrypoint", ["run", "drain"])
def test_programmatic_margin_entrypoints_block_before_calendar_provider_or_db(
    monkeypatch, entrypoint
):
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(sr.ExecutionPolicyError, match="margin.*scope_blocked"):
        if entrypoint == "run":
            sr.run_domain("margin", registry=_registry())
        else:
            sr.drain_domain("margin", registry=_registry())


@pytest.mark.parametrize(
    "args",
    [
        _args(),
        _args(drain=True),
        _args(all_due=True),
    ],
    ids=["explicit-domain", "drain", "all-due"],
)
def test_cli_entrypoints_block_before_calendar_lock_auth_provider_or_db(
    monkeypatch, capsys, args
):
    import services.writer_lock as writer_lock_module

    monkeypatch.setattr(sr, "_parse_cli_args", lambda: args)
    monkeypatch.setattr(sr, "load_registry", _registry)
    monkeypatch.setattr(sr, "_calendar_preflight", _forbidden("calendar"))
    monkeypatch.setattr(
        sr,
        "_preflight_explicit_operation_windows",
        _forbidden("operation-window calendar"),
    )
    monkeypatch.setattr(sr, "_authorization_preflight", _forbidden("authorization"))
    monkeypatch.setattr(sr, "_adapter", _forbidden("adapter"))
    monkeypatch.setattr(sr, "_target_conn", _forbidden("target db"))
    monkeypatch.setattr(writer_lock_module, "writer_lock", _forbidden("writer lock"))

    assert sr.main() == 6
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "execution_blocked",
        "domain": "margin",
        "mode": "disabled",
        "reason": "scope_blocked",
        "error": "domain=margin execution disabled: scope_blocked",
    }


def test_main_unlocked_cannot_bypass_execution_policy(monkeypatch):
    monkeypatch.setattr(sr, "_calendar_preflight", _forbidden("calendar"))
    monkeypatch.setattr(sr, "run_domain", _forbidden("run_domain"))

    with pytest.raises(sr.ExecutionPolicyError, match="margin.*scope_blocked"):
        sr._main_unlocked(_args(), _registry(), ["margin"])


def test_automatic_domain_inventory_matches_all_due_and_fails_closed():
    registry = _registry()
    registry["domains"]["manual_repair"] = {
        "sync_policy": "on_demand",
        "execution_policy": {"mode": "enabled", "reason": "manual_only"},
    }
    assert sr.automatic_domains(registry) == ["daily", "margin"]

    registry["domains"]["broken"] = None
    with pytest.raises(ValueError, match="domain entry.*broken.*mapping"):
        sr.automatic_domains(registry)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        pytest.param("missing", "missing execution_policy", id="missing-key"),
        (None, "execution_policy must be a mapping"),
        ({"mode": "disabled"}, "missing execution_policy keys: reason"),
        (
            {"mode": "disabled", "reason": "scope_blocked", "extra": True},
            "unknown execution_policy keys: extra",
        ),
        (
            {"mode": "paused", "reason": "scope_blocked"},
            "unsupported execution policy mode='paused'",
        ),
        (
            {"mode": "disabled", "reason": "Scope Blocked"},
            "execution policy reason contains malformed value",
        ),
    ],
)
def test_execution_policy_is_strict_and_typed(raw, match):
    spec = {"domain": "example"}
    if raw != "missing":
        spec["execution_policy"] = raw
    with pytest.raises(sr.ExecutionPolicyError, match=match):
        sr.execution_policy_for_spec(spec)


def test_explicit_empty_registry_does_not_fall_back_to_live_registry(monkeypatch):
    monkeypatch.setattr(sr, "load_registry", _forbidden("live registry fallback"))

    with pytest.raises(KeyError):
        sr.run_domain("margin", registry={})


def _enabled_formal_margin_registry(*, population_scope=None) -> dict:
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["execution_policy"] = {"mode": "enabled", "reason": "active"}
    if population_scope is not None:
        spec["population_scope"] = population_scope
    else:
        spec.pop("population_scope", None)
    return {"defaults": {}, "domains": {"margin": spec}}


def test_enabled_formal_dataset_missing_population_scope_blocks_before_runtime(
    monkeypatch,
):
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="population scope invalid.*missing population_scope",
    ):
        sr.run_domain("margin", registry=_enabled_formal_margin_registry())


def test_enabled_margin_cannot_delete_contract_and_fall_into_legacy_runner(monkeypatch):
    registry = _enabled_formal_margin_registry(population_scope={})
    registry["domains"]["margin"].pop("dataset_contract")
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="dataset contract invalid.*missing or mismatched dataset_contract",
    ) as caught:
        sr.run_domain("margin", registry=registry)
    assert caught.value.reason == "invalid_dataset_contract"


def test_parsed_scope_cannot_be_discarded_by_legacy_formal_runner(monkeypatch):
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))
    scope = {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE", "BSE"],
        "population_label": "venue_reported_margin_population",
        "method": "tushare_margin_exchange_summary",
        "unit": "provider_declared_fields",
    }

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="DatasetExecutionContract is not propagated",
    ) as caught:
        sr.run_domain(
            "margin",
            registry=_enabled_formal_margin_registry(population_scope=scope),
        )

    assert caught.value.reason == "execution_contract_not_propagated"


def test_future_non_margin_formal_dataset_cannot_bypass_population_gate(monkeypatch):
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["execution_policy"] = {"mode": "enabled", "reason": "active"}
    spec["dataset_contract"] = {
        **spec["dataset_contract"],
        "dataset_id": "tier0.market_data.future_formal_daily",
        "schema_id": "tier0.market_data.future_formal_daily.canonical",
        "canonical_table": "canonical_future_formal_daily",
    }
    spec.pop("population_scope", None)
    registry = {"defaults": {}, "domains": {"future_formal": spec}}
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="population scope invalid.*missing population_scope",
    ) as caught:
        sr.run_domain("future_formal", registry=registry)

    assert caught.value.domain == "future_formal"
    assert caught.value.reason == "invalid_population_scope"


def test_enabled_policy_resolves_without_side_effects():
    policy = sr.execution_policy_for_spec(
        {
            "domain": "daily",
            "execution_policy": {"mode": "enabled", "reason": "active"},
        }
    )

    assert policy == sr.DomainExecutionPolicy(mode="enabled", reason="active")
