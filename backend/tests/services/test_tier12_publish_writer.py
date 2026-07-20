"""Phase C writer + PIT truncation (fail-closed; not publish-complete)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.tier12_publish_contract import config_hash_for
from services.tier12_publish_writer import (
    TimedInput,
    Tier12PublishConfig,
    Tier12WriteBatch,
    load_tier12_publish_config,
    pit_truncate_inputs,
    write_tier12_batch,
)

_CFG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"
)


def _cfg() -> Tier12PublishConfig:
    return load_tier12_publish_config(_CFG_PATH)


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


def test_pit_truncate_drops_inputs_available_after_decision_date() -> None:
    kept = pit_truncate_inputs(
        [
            _bar("600000", "20260716", available_at="20260716"),
            _bar("600000", "20260717", available_at="20260717"),
            _bar("600000", "20260717", available_at="20260718"),  # future avail
            _bar("600000", "20260718", available_at="20260718"),
        ],
        decision_date="20260717",
    )
    assert [i.available_at for i in kept] == ["20260716", "20260717"]


def test_pit_truncate_missing_available_at_fails_closed() -> None:
    with pytest.raises(ValueError, match="available_at"):
        pit_truncate_inputs(
            [
                TimedInput(
                    entity_id="600000",
                    trade_date="20260717",
                    available_at="",
                    payload={"close": 1.0},
                )
            ],
            decision_date="20260717",
        )


def test_writer_pit_invariance_stock_state_and_market_context() -> None:
    """Adding future-available inputs must 0-diff decision-date outputs (PIT硬门)."""
    decision = "20260717"
    # History makes trend up (close rises); same-day breadth risk-on (adv>dec).
    base = [
        _bar("600000", "20260710", close=10.0, pct_chg=0.0),
        _bar("600000", "20260711", close=10.5, pct_chg=5.0),
        _bar("600000", "20260714", close=11.0, pct_chg=4.0),
        _bar("600000", "20260715", close=11.5, pct_chg=4.0),
        _bar("600000", "20260716", close=12.0, pct_chg=4.0),
        _bar("600000", "20260717", close=12.5, pct_chg=4.0),
        _bar("000001", "20260717", close=9.0, pct_chg=2.0),
        _bar("000002", "20260717", close=8.0, pct_chg=-1.0),
    ]
    # If wrongly included, these would flip trend to down and breadth to risk-off.
    future = [
        _bar(
            "600000",
            "20260717",
            available_at="20260720",
            close=1.0,
            pct_chg=-90.0,
        ),
        _bar(
            "000003",
            "20260717",
            available_at="20260720",
            close=7.0,
            pct_chg=-50.0,
        ),
        _bar(
            "000004",
            "20260717",
            available_at="20260720",
            close=6.0,
            pct_chg=-40.0,
        ),
        _bar(
            "000005",
            "20260717",
            available_at="20260720",
            close=5.0,
            pct_chg=-30.0,
        ),
    ]

    cfg = _cfg()
    short = write_tier12_batch(decision_date=decision, inputs=base, config=cfg)
    long = write_tier12_batch(
        decision_date=decision, inputs=base + future, config=cfg
    )

    assert short.pit_excluded_count == 0
    assert long.pit_excluded_count == len(future)
    assert short.published is False and long.published is False
    assert short.as_dict()["stock_states"] == long.as_dict()["stock_states"]
    assert short.as_dict()["market_context"] == long.as_dict()["market_context"]

    # Sanity: base path actually produced meaningful outputs (test not vacuous).
    by_code = {r.stock_code: r for r in short.stock_states}
    assert by_code["600000"].axis_trend == "up"
    assert short.market_context is not None
    assert short.market_context.risk_on is True


def test_writer_stamps_lineage_and_stays_unpublished() -> None:
    cfg = _cfg()
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=[
            _bar("600000", "20260716", close=10.0),
            _bar("600000", "20260717", close=11.0, pct_chg=10.0),
            _bar("000001", "20260717", close=9.0, pct_chg=-1.0),
        ],
        config=cfg,
    )
    assert isinstance(batch, Tier12WriteBatch)
    assert batch.published is False
    assert batch.status == "WRITTEN_UNPUBLISHED"
    assert all(r.available_at for r in batch.stock_states)
    assert all(r.definition_version for r in batch.stock_states)
    assert all(r.config_hash for r in batch.stock_states)
    assert all(r.input_snapshot_id for r in batch.stock_states)
    assert batch.market_context is not None
    assert batch.market_context.config_hash == config_hash_for(
        cfg.market_context_config_for_hash()
    )
    assert all(a.published is False for a in batch.stock_attestations)
    assert batch.market_attestation is not None
    assert batch.market_attestation.published is False
    assert batch.market_attestation.status == "PUBLISHABLE_SCAFFOLD"


def test_writer_fail_closed_when_config_missing_definition() -> None:
    raw = yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))
    raw["stock_state"]["definition_version"] = ""
    with pytest.raises(ValueError, match="definition_version"):
        Tier12PublishConfig.from_mapping(raw)


def test_writer_never_marks_published_even_if_config_asks() -> None:
    raw = yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))
    raw["publish"]["allow_published"] = True
    cfg = Tier12PublishConfig.from_mapping(raw)
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=[_bar("600000", "20260717", close=10.0, pct_chg=1.0)],
        config=cfg,
    )
    # Hard gate: scaffold writer cannot become StrategyRelease / accepted publish.
    assert batch.published is False
    assert batch.status == "WRITTEN_UNPUBLISHED"
    assert "not_accepted_partition" in batch.notes
