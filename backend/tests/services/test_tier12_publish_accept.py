"""Phase C accepted publish path (fail-closed; not consumer cutover)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from services.tier12_publish_accept import (
    Tier12AcceptError,
    Tier12AcceptedPublish,
    accept_tier12_batch,
    load_tier12_write_batch,
)
from services.tier12_publish_contract import (
    MarketContextPublishEnvelope,
    PublishLineageReport,
    StockStateDaily,
    attest_market_context_publishable,
    attest_stock_state_publishable,
    config_hash_for,
)
from services.tier12_publish_writer import (
    TimedInput,
    Tier12WriteBatch,
    load_tier12_publish_config,
    write_tier12_batch,
)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"


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


def _good_batch() -> Tier12WriteBatch:
    cfg = load_tier12_publish_config(_CFG_PATH)
    return write_tier12_batch(
        decision_date="20260717",
        inputs=[
            _bar("600000", "20260716", close=10.0),
            _bar("600000", "20260717", close=11.0, pct_chg=10.0),
            _bar("000001", "20260717", close=9.0, pct_chg=-1.0),
        ],
        config=cfg,
    )


def test_accept_rejects_missing_lineage() -> None:
    batch = _good_batch()
    poisoned_stock = replace(
        batch.stock_states[0],
        definition_version=None,
        config_hash=None,
        available_at=None,
    )
    stocks = (poisoned_stock,) + batch.stock_states[1:]
    broken = replace(
        batch,
        stock_states=stocks,
        stock_attestations=tuple(attest_stock_state_publishable(r) for r in stocks),
    )
    with pytest.raises(Tier12AcceptError, match="missing_lineage|NOT_PUBLISHABLE"):
        accept_tier12_batch(broken)


def test_accept_rejects_already_published_without_accept() -> None:
    batch = _good_batch()
    forged = replace(batch, published=True, status="FORGED_PUBLISHED")
    with pytest.raises(Tier12AcceptError, match="already_published|WRITTEN_UNPUBLISHED"):
        accept_tier12_batch(forged)


def test_accept_rejects_pit_poison_on_outputs() -> None:
    batch = _good_batch()
    poisoned = replace(
        batch.stock_states[0],
        available_at="20260720T180000+0800",
    )
    stocks = (poisoned,) + batch.stock_states[1:]
    # Keep scaffold attestations so the failure is specifically PIT, not lineage.
    broken = replace(
        batch,
        stock_states=stocks,
        stock_attestations=tuple(attest_stock_state_publishable(r) for r in stocks),
    )
    with pytest.raises(Tier12AcceptError, match="pit_poison"):
        accept_tier12_batch(broken)


def test_accept_happy_path_sets_published_and_blocks_cutover(
    tmp_path: Path,
) -> None:
    batch = _good_batch()
    assert batch.published is False
    assert batch.status == "WRITTEN_UNPUBLISHED"
    assert batch.market_attestation is not None
    assert batch.market_attestation.status == "PUBLISHABLE_SCAFFOLD"

    accepted = accept_tier12_batch(
        batch,
        emit_artifact=True,
        artifact_root=tmp_path,
    )
    assert isinstance(accepted, Tier12AcceptedPublish)
    assert accepted.published is True
    assert accepted.status == "ACCEPTED"
    assert accepted.cutover_allowed is False
    assert accepted.decision_date == "20260717"
    assert accepted.stock_row_count == len(batch.stock_states)
    assert accepted.content_hash
    assert accepted.definition_version
    assert accepted.config_hash
    assert accepted.input_snapshot_id
    assert accepted.available_at
    assert "not_consumer_cutover" in accepted.notes
    assert "not_full_universe" in accepted.notes

    art = tmp_path / "accepted_20260717.json"
    assert art.is_file()
    payload = json.loads(art.read_text(encoding="utf-8"))
    assert payload["published"] is True
    assert payload["cutover_allowed"] is False
    assert payload["status"] == "ACCEPTED"
    # Smoke must not be silently rewritten as accepted.
    assert not (tmp_path / "smoke_20260717.json").exists()
    assert not (tmp_path / "batch_20260717.json").exists()


def test_accept_cutover_flag_hard_gate_even_if_requested() -> None:
    batch = _good_batch()
    accepted = accept_tier12_batch(batch, allow_consumer_cutover=True)
    assert accepted.published is True
    assert accepted.cutover_allowed is False
    assert "allow_consumer_cutover_ignored_hard_gate" in accepted.notes


def test_accept_does_not_upgrade_smoke_summary_flag() -> None:
    """Flipping published on a smoke summary must not count as accept."""

    smoke_like = {
        "kind": "tier12_writer_smoke_summary",
        "decision_date": "20260717",
        "status": "WRITTEN_UNPUBLISHED",
        "published": True,  # forged upgrade
        "stock_state_count": 1,
    }
    with pytest.raises(Tier12AcceptError, match="not_a_write_batch|smoke"):
        accept_tier12_batch(smoke_like)  # type: ignore[arg-type]


def test_load_and_accept_from_batch_artifact(tmp_path: Path) -> None:
    batch = _good_batch()
    batch_path = tmp_path / "batch_20260717.json"
    batch_path.write_text(
        json.dumps(batch.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    loaded = load_tier12_write_batch(batch_path)
    accepted = accept_tier12_batch(
        loaded, emit_artifact=True, artifact_root=tmp_path
    )
    assert accepted.published is True
    assert (tmp_path / "accepted_20260717.json").is_file()


def test_accept_rejects_empty_stock_states() -> None:
    batch = _good_batch()
    empty = replace(
        batch,
        stock_states=(),
        stock_attestations=(),
    )
    with pytest.raises(Tier12AcceptError, match="empty_stock_states"):
        accept_tier12_batch(empty)
