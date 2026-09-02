"""Tier1 accept enrich: fact_stock_form_daily exact-day join (fail-closed)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.tier12_publish_contract import (
    StockStateDaily,
    config_hash_for,
    stock_state_from_form_row,
)
from services.tier12_publish_writer import (
    TimedInput,
    Tier12PublishConfig,
    load_form_rows_exact_day,
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


def _enrich_cfg(**overrides) -> Tier12PublishConfig:
    raw = yaml.safe_load(_CFG_PATH.read_text(encoding="utf-8"))
    stock = dict(raw["stock_state"])
    stock["definition_version"] = "stock_state_stage_pattern_v1"
    stock["form_source"] = "fact_stock_form_daily"
    stock.update(overrides)
    raw["stock_state"] = stock
    return Tier12PublishConfig.from_mapping(raw)


def test_stock_state_daily_carries_form_fields() -> None:
    row = StockStateDaily(
        stock_code="600000",
        trade_date="20260717",
        axis_trend="up",
        axis_pos="high",
        form_name="温和上涨",
        is_breakout_event=True,
        definition_version="stock_state_stage_pattern_v1",
        config_hash="abc",
        input_snapshot_id="snap",
        eligible_universe_id="univ",
        available_at="20260717T160000+0800",
    )
    d = row.as_dict()
    assert d["form_name"] == "温和上涨"
    assert d["axis_pos"] == "high"
    assert d["is_breakout_event"] is True
    assert d["axis_trend"] == "up"


def test_form_row_bridge_includes_form_fields_without_inventing_lineage() -> None:
    bridged = stock_state_from_form_row(
        {
            "stock_code": "000001",
            "trade_date": "20260717",
            "axis_trend": "down",
            "axis_pos": "mid",
            "form_name": "震荡整理",
            "is_breakout_event": False,
        }
    )
    assert bridged.form_name == "震荡整理"
    assert bridged.axis_pos == "mid"
    assert bridged.is_breakout_event is False
    assert bridged.definition_version is None
    assert bridged.config_hash is None


def test_writer_enriches_form_exact_day_without_overwriting_axis_trend() -> None:
    cfg = _enrich_cfg()
    form_by_code = {
        "600000": {
            "form_name": "温和上涨",
            "axis_pos": "high",
            "axis_trend": "down",  # must NOT overlay writer trend
            "is_breakout_event": True,
        }
    }
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=[
            _bar("600000", "20260716", close=10.0),
            _bar("600000", "20260717", close=11.0, pct_chg=10.0),
        ],
        config=cfg,
        form_by_code=form_by_code,
    )
    row = batch.stock_states[0]
    assert row.axis_trend == "up"  # writer closes trend, not form
    assert row.form_name == "温和上涨"
    assert row.axis_pos == "high"
    assert row.is_breakout_event is True
    assert row.definition_version == "stock_state_stage_pattern_v1"
    assert "form_source" in row.details
    assert row.details["form_source"] == "fact_stock_form_daily"


def test_writer_missing_exact_day_form_stays_null_no_asof_pad() -> None:
    cfg = _enrich_cfg()
    batch = write_tier12_batch(
        decision_date="20260717",
        inputs=[_bar("600000", "20260717", close=11.0, pct_chg=1.0)],
        config=cfg,
        form_by_code={},  # exact-day miss
    )
    row = batch.stock_states[0]
    assert row.form_name is None
    assert row.axis_pos is None
    assert row.is_breakout_event is False  # scaffold default when form absent


def test_load_form_rows_frontier_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCon:
        def execute(self, sql, params=None):
            class _R:
                def fetchone(self_inner):
                    if "MAX(trade_date)" in sql:
                        return ("20260716",)
                    return (0,)

                def fetchall(self_inner):
                    return []

            return _R()

        def close(self):
            return None

    monkeypatch.setattr(
        "services.tier12_publish_writer.connect_ro",
        lambda *_a, **_k: _FakeCon(),
    )
    with pytest.raises(ValueError, match="form_frontier"):
        load_form_rows_exact_day("20260717")


def test_stock_config_hash_includes_form_source() -> None:
    cfg = _enrich_cfg()
    payload = cfg.stock_config_for_hash()
    assert payload["form_source"] == "fact_stock_form_daily"
    assert payload["definition_version"] == "stock_state_stage_pattern_v1"
    h1 = config_hash_for(payload)
    cfg2 = _enrich_cfg(form_source="")
    h2 = config_hash_for(cfg2.stock_config_for_hash())
    assert h1 != h2


def test_live_yaml_pins_v1_form_source() -> None:
    cfg = load_tier12_publish_config(_CFG_PATH)
    assert cfg.stock_definition_version == "stock_state_stage_pattern_v1"
    assert cfg.form_source == "fact_stock_form_daily"


def test_writer_requires_form_by_code_when_form_source_configured() -> None:
    cfg = _enrich_cfg()
    with pytest.raises(ValueError, match="form_source_requires_form_by_code"):
        write_tier12_batch(
            decision_date="20260717",
            inputs=[_bar("600000", "20260717", close=11.0)],
            config=cfg,
        )
