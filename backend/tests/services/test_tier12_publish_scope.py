"""Phase C accept publish_scope: canary vs project_universe (fail-closed)."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.tier12_consumer_cutover import (
    Tier12ConsumerCutoverConfig,
    resolve_tier12_consumer_cutover,
)
from services.tier12_publish_accept import Tier12AcceptError, accept_tier12_batch
from services.tier12_publish_writer import (
    TimedInput,
    load_tier12_publish_config,
    write_tier12_batch,
)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"


def _bar(
    code: str,
    trade_date: str,
    *,
    close: float = 10.0,
    pct_chg: float = 1.0,
) -> TimedInput:
    return TimedInput(
        entity_id=code,
        trade_date=trade_date,
        available_at=trade_date,
        payload={"close": close, "pct_chg": pct_chg, "ts_code": f"{code}.SH"},
    )


def _batch_n(n: int = 3):
    cfg = load_tier12_publish_config(_CFG_PATH)
    inputs = [_bar("600000", "20260716", close=10.0)]
    for i in range(n):
        code = f"{600000 + i:06d}"
        inputs.append(_bar(code, "20260717", close=11.0 + i, pct_chg=1.0))
    return write_tier12_batch(
        decision_date="20260717",
        inputs=inputs,
        config=cfg,
    )


def test_default_accept_is_canary_scope() -> None:
    accepted = accept_tier12_batch(_batch_n(2))
    assert accepted.publish_scope == "canary"
    assert accepted.population_kind is None
    assert accepted.universe_membership_size is None
    assert "not_full_universe" in accepted.notes
    assert "canary_or_fixture_scale_ok" in accepted.notes
    assert "project_universe_scope" not in accepted.notes


def test_project_universe_accept_requires_attestation() -> None:
    with pytest.raises(Tier12AcceptError, match="universe_attestation"):
        accept_tier12_batch(_batch_n(2), publish_scope="project_universe")


def test_project_universe_accept_parity_and_non_canary_notes() -> None:
    batch = _batch_n(3)
    written = len(batch.stock_states)
    membership = 150
    excluded = membership - written
    accepted = accept_tier12_batch(
        batch,
        publish_scope="project_universe",
        universe_attestation={
            "population_kind": "project_universe_pit",
            "membership_size": membership,
            "universe_policy_hash": "abc123",
            "coverage_excluded_count": excluded,
        },
    )
    assert accepted.publish_scope == "project_universe"
    assert accepted.population_kind == "project_universe_pit"
    assert accepted.universe_membership_size == membership
    assert accepted.coverage_excluded_count == excluded
    assert accepted.cutover_allowed is False
    assert "project_universe_scope" in accepted.notes
    assert "full_universe_attested" in accepted.notes
    assert "not_full_universe" not in accepted.notes
    assert "canary_or_fixture_scale_ok" not in accepted.notes


def test_project_universe_rejects_parity_mismatch() -> None:
    batch = _batch_n(3)
    with pytest.raises(Tier12AcceptError, match="coverage_parity"):
        accept_tier12_batch(
            batch,
            publish_scope="project_universe",
            universe_attestation={
                "population_kind": "project_universe_pit",
                "membership_size": 9999,
                "universe_policy_hash": "abc123",
                "coverage_excluded_count": 0,
            },
        )


def test_project_universe_rejects_tiny_membership() -> None:
    batch = _batch_n(3)
    with pytest.raises(Tier12AcceptError, match="membership_too_small"):
        accept_tier12_batch(
            batch,
            publish_scope="project_universe",
            universe_attestation={
                "population_kind": "project_universe_pit",
                "membership_size": 3,
                "universe_policy_hash": "abc123",
                "coverage_excluded_count": 0,
            },
        )


def test_canary_accept_still_blocked_from_project_universe_cutover() -> None:
    accepted = accept_tier12_batch(_batch_n(2))
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash=accepted.config_hash,
        acknowledge_canary_scope=False,
        claim_project_universe=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717", config=cfg, accepted=accepted
    )
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("canary" in r or "project_universe" in r for r in decision.reasons)


def test_full_universe_accept_can_reach_cutover_when_opted_in() -> None:
    """Opt-in cutover path for non-canary accept; explicit-disabled stays false."""

    batch = _batch_n(3)
    written = len(batch.stock_states)
    # Inflate attestation to clear membership_size>=100 while keeping parity.
    # Fixture-scale rows are fine when publish_scope + notes attest universe.
    membership = 120
    excluded = membership - written
    accepted = accept_tier12_batch(
        batch,
        publish_scope="project_universe",
        universe_attestation={
            "population_kind": "project_universe_pit",
            "membership_size": membership,
            "universe_policy_hash": "abc123",
            "coverage_excluded_count": excluded,
        },
    )
    payload = accepted.as_dict()
    assert payload["publish_scope"] == "project_universe"

    disabled = resolve_tier12_consumer_cutover(
        "20260717",
        config=Tier12ConsumerCutoverConfig(cutover_allowed=False),
        accepted=accepted,
    )
    assert disabled.cutover_allowed is False
    assert "config_cutover_allowed_false" in disabled.reasons

    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash=accepted.config_hash,
        acknowledge_canary_scope=False,
        claim_project_universe=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717", config=cfg, accepted=accepted
    )
    assert decision.cutover_allowed is True
    assert decision.status == "ACCEPTED_CUTOVER"
    assert decision.claim_project_universe is True
