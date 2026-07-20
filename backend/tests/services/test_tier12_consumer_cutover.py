"""Phase C consumer cutover gate (fail-closed; default false)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.tier12_consumer_cutover import (
    Tier12ConsumerCutoverConfig,
    Tier12ConsumerCutoverDecision,
    Tier12ConsumerCutoverError,
    load_tier12_consumer_cutover_config,
    resolve_tier12_consumer_cutover,
)
from services.tier12_publish_accept import accept_tier12_batch
from services.tier12_publish_contract import config_hash_for
from services.tier12_publish_writer import (
    TimedInput,
    load_tier12_publish_config,
    write_tier12_batch,
)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"
_DEF_V = "stock_state_stage_pattern_v0"


def _bar(
    code: str,
    trade_date: str,
    *,
    available_at: str | None = None,
    close: float = 10.0,
    pct_chg: float = 1.0,
) -> TimedInput:
    return TimedInput(
        entity_id=code,
        trade_date=trade_date,
        available_at=available_at if available_at is not None else trade_date,
        payload={"close": close, "pct_chg": pct_chg, "ts_code": f"{code}.SH"},
    )


def _accepted_canary(tmp_path: Path):
    cfg = load_tier12_publish_config(_CFG_PATH)
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=[
            _bar("600000", "20260716", close=10.0),
            _bar("600000", "20260717", close=11.0, pct_chg=10.0),
            _bar("000001", "20260717", close=9.0, pct_chg=-1.0),
        ],
        config=cfg,
    )
    return accept_tier12_batch(
        batch, emit_artifact=True, artifact_root=tmp_path
    )


def _full_universe_accepted_dict(canary) -> dict:
    """Synthetic non-canary accept attestation (fixture-scale, not live)."""

    payload = canary.as_dict()
    notes = [
        n
        for n in payload["notes"]
        if n
        not in {
            "not_full_universe",
            "canary_or_fixture_scale_ok",
            "not_consumer_cutover",
        }
    ]
    notes.extend(
        [
            "full_universe_attested_fixture",
            "consumer_cutover_gate_fixture",
        ]
    )
    payload["notes"] = notes
    payload["stock_row_count"] = 5000
    return payload


def test_default_config_cutover_false() -> None:
    cfg = load_tier12_consumer_cutover_config(_CFG_PATH)
    assert cfg.cutover_allowed is False
    assert cfg.acknowledge_canary_scope is False
    assert cfg.claim_project_universe is False


def test_resolver_defaults_to_legacy_even_with_accepted(tmp_path: Path) -> None:
    accepted = _accepted_canary(tmp_path)
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        accepted=accepted,
        artifact_root=tmp_path,
    )
    assert isinstance(decision, Tier12ConsumerCutoverDecision)
    assert decision.cutover_allowed is False
    assert decision.source == "legacy_scaffold"
    assert decision.status == "LEGACY"
    assert "config_cutover_allowed_false" in decision.reasons
    # Gate must not silently promote accepted files as production truth.
    assert decision.accepted_payload is None


def test_reject_enable_without_accept(tmp_path: Path) -> None:
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=_DEF_V,
        expected_config_hash="deadbeef",
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is False
    assert decision.source == "legacy_scaffold"
    assert decision.status == "BLOCKED"
    assert any("missing_accept" in r for r in decision.reasons)


def test_reject_unpublished_accept(tmp_path: Path) -> None:
    accepted = _accepted_canary(tmp_path)
    forged = accepted.as_dict()
    forged["published"] = False
    forged["status"] = "WRITTEN_UNPUBLISHED"
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash=accepted.config_hash,
        acknowledge_canary_scope=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        accepted=forged,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("published_false" in r for r in decision.reasons)


def test_reject_definition_or_config_hash_mismatch(tmp_path: Path) -> None:
    accepted = _accepted_canary(tmp_path)
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash="0" * 64,
        acknowledge_canary_scope=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        accepted=accepted,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("config_hash_mismatch" in r for r in decision.reasons)


def test_reject_canary_accept_as_full_universe_cutover(tmp_path: Path) -> None:
    accepted = _accepted_canary(tmp_path)
    # Opt-in without canary acknowledgment, and/or claim project-universe.
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash=accepted.config_hash,
        acknowledge_canary_scope=False,
        claim_project_universe=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        accepted=accepted,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is False
    assert decision.status == "BLOCKED"
    assert any("canary" in r for r in decision.reasons)
    assert any(
        "project_universe" in r or "full_universe" in r for r in decision.reasons
    )


def test_canary_scoped_cutover_requires_ack_and_forbids_universe_claim(
    tmp_path: Path,
) -> None:
    accepted = _accepted_canary(tmp_path)
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=accepted.definition_version,
        expected_config_hash=accepted.config_hash,
        acknowledge_canary_scope=True,
        claim_project_universe=False,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        accepted=accepted,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is True
    assert decision.source == "accepted_partition"
    assert decision.status == "CANARY_SCOPED"
    assert decision.claim_project_universe is False
    assert decision.accepted_payload is not None
    assert decision.accepted_payload["published"] is True
    assert "canary_scope_acknowledged" in decision.notes


def test_green_full_universe_cutover_when_all_gates_pass(tmp_path: Path) -> None:
    canary = _accepted_canary(tmp_path)
    full = _full_universe_accepted_dict(canary)
    art = tmp_path / "accepted_20260717.json"
    art.write_text(
        json.dumps(full, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=full["definition_version"],
        expected_config_hash=full["config_hash"],
        acknowledge_canary_scope=False,
        claim_project_universe=True,
    )
    decision = resolve_tier12_consumer_cutover(
        "20260717",
        config=cfg,
        artifact_root=tmp_path,
    )
    assert decision.cutover_allowed is True
    assert decision.source == "accepted_partition"
    assert decision.status == "ACCEPTED_CUTOVER"
    assert decision.claim_project_universe is True
    assert decision.accepted_payload is not None
    assert "config_explicit_opt_in" in decision.notes


def test_silent_accepted_file_read_without_gate_raises(tmp_path: Path) -> None:
    """Research/UI must not treat accepted JSON as production without resolver."""

    _accepted_canary(tmp_path)
    with pytest.raises(Tier12ConsumerCutoverError, match="resolver|gate"):
        from services.tier12_consumer_cutover import (
            load_accepted_partition_as_production_truth,
        )

        load_accepted_partition_as_production_truth(
            "20260717", artifact_root=tmp_path
        )


def test_expected_hashes_align_with_publish_config() -> None:
    """Guard: expected stock config_hash must match publish typed policy."""

    pub = load_tier12_publish_config(_CFG_PATH)
    expected = config_hash_for(pub.stock_config_for_hash())
    cut = load_tier12_consumer_cutover_config(_CFG_PATH)
    # Default cutover config may leave expected_config_hash empty (fail closed
    # until explicit opt-in fills it); when filled it must match.
    if cut.expected_config_hash:
        assert cut.expected_config_hash == expected
    if cut.expected_definition_version:
        assert cut.expected_definition_version == pub.stock_definition_version
