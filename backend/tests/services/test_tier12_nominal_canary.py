"""Phase C nominal canary → writer smoke (offline fixture; fail-closed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.tier12_nominal_canary import (
    CONTRACTUAL_AVAILABLE_AT_POLICY,
    assert_tier12_smoke_batch,
    contractual_nominal_available_at,
    timed_inputs_from_nominal_rows,
)
from services.tier12_publish_writer import (
    TimedInput,
    load_tier12_publish_config,
    write_tier12_batch,
)

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "tier12_nominal_canary.json"
)
_CFG = Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_contractual_available_at_uses_domain_same_day_1800() -> None:
    assert contractual_nominal_available_at("20260717") == "20260717T180000+0800"
    assert CONTRACTUAL_AVAILABLE_AT_POLICY == "contractual_same_day_at_1800"


def test_timed_inputs_fail_closed_on_missing_close() -> None:
    with pytest.raises(ValueError, match="close/pct_chg"):
        timed_inputs_from_nominal_rows(
            [{"ts_code": "600000.SH", "trade_date": "20260717", "pct_chg": 1.0}]
        )


def test_fixture_canary_through_writer_stays_unpublished_with_lineage() -> None:
    """Offline-reproducible smoke: current writer + fixture shaped like canonical."""

    fx = _fixture()
    decision = fx["decision_date"]
    inputs = timed_inputs_from_nominal_rows(fx["rows"], available_at_mode="contractual")
    # Poison uses explicit future available_at (raw-style TimedInput).
    poison = [
        TimedInput(
            entity_id=str(r["ts_code"]).split(".", 1)[0],
            trade_date=str(r["trade_date"]),
            available_at=str(r["available_at"]),
            payload={
                "ts_code": r["ts_code"],
                "close": float(r["close"]),
                "pct_chg": float(r["pct_chg"]),
            },
        )
        for r in fx["poison_future"]
    ]
    cfg = load_tier12_publish_config(_CFG)
    base = write_tier12_batch(decision_date=decision, inputs=inputs, config=cfg)
    long = write_tier12_batch(
        decision_date=decision, inputs=inputs + poison, config=cfg
    )

    assert long.pit_excluded_count == len(poison)
    assert base.as_dict()["stock_states"] == long.as_dict()["stock_states"]
    assert base.as_dict()["market_context"] == long.as_dict()["market_context"]

    smoke = assert_tier12_smoke_batch(long, decision_date=decision)
    assert smoke["status"] == "WRITTEN_UNPUBLISHED"
    assert smoke["published"] is False
    assert smoke["stock_state_count"] >= 3
    assert smoke["definition_version"]
    assert smoke["config_hash"]
    assert smoke["available_at"].startswith(decision)
    # PIT: contractual input available_at day == trade_date <= decision.
    assert all(
        i.available_at.startswith(i.trade_date) for i in inputs
    )


def test_smoke_gate_rejects_future_output_available_at() -> None:
    cfg = load_tier12_publish_config(_CFG)
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=timed_inputs_from_nominal_rows(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260717",
                    "close": 10.0,
                    "pct_chg": 1.0,
                }
            ]
        ),
        config=cfg,
    )
    # Tamper: simulate a bad writer stamping future availability.
    bad_stocks = []
    for r in batch.stock_states:
        bad_stocks.append(
            type(r)(
                **{
                    **r.as_dict(),
                    "available_at": "20260720T160000+0800",
                    "details": dict(r.details),
                }
            )
        )
    tampered = type(batch)(
        decision_date=batch.decision_date,
        stock_states=tuple(bad_stocks),
        market_context=batch.market_context,
        stock_attestations=batch.stock_attestations,
        market_attestation=batch.market_attestation,
        pit_excluded_count=batch.pit_excluded_count,
        status=batch.status,
        published=batch.published,
        notes=batch.notes,
    )
    with pytest.raises(ValueError, match="future_available_at"):
        assert_tier12_smoke_batch(tampered, decision_date="20260717")


def test_smoke_gate_rejects_published_true() -> None:
    cfg = load_tier12_publish_config(_CFG)
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=timed_inputs_from_nominal_rows(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260717",
                    "close": 10.0,
                    "pct_chg": 1.0,
                }
            ]
        ),
        config=cfg,
    )
    tampered = type(batch)(
        decision_date=batch.decision_date,
        stock_states=batch.stock_states,
        market_context=batch.market_context,
        stock_attestations=batch.stock_attestations,
        market_attestation=batch.market_attestation,
        pit_excluded_count=batch.pit_excluded_count,
        status="WRITTEN_UNPUBLISHED",
        published=True,
        notes=batch.notes,
    )
    with pytest.raises(ValueError, match="published="):
        assert_tier12_smoke_batch(tampered, decision_date="20260717")
