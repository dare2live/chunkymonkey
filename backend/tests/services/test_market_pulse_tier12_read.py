"""Pulse/UI Tier1/2 production-read boundary (cutover default false)."""
from __future__ import annotations

import json
from pathlib import Path

from services.market_pulse_tier12_read import (
    attest_pulse_tier12_production_read,
    overlay_pulse_form_from_production_read,
)
from services.tier12_consumer_cutover import Tier12ConsumerCutoverConfig
from services.tier12_publish_accept import accept_tier12_batch
from services.tier12_publish_writer import (
    TimedInput,
    load_tier12_publish_config,
    write_tier12_batch,
)

_CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "tier12_publish.yaml"


def _bar(code: str, trade_date: str, *, close: float = 10.0, pct_chg: float = 1.0):
    return TimedInput(
        entity_id=code,
        trade_date=trade_date,
        available_at=trade_date,
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
        form_by_code={},
    )
    return accept_tier12_batch(batch, emit_artifact=True, artifact_root=tmp_path)


def _full_universe_accepted_dict(canary) -> dict:
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
            "project_universe_scope",
            "full_universe_attested",
            "full_universe_attested_fixture",
            "consumer_cutover_gate_fixture",
        ]
    )
    payload["notes"] = notes
    payload["publish_scope"] = "project_universe"
    payload["population_kind"] = "project_universe_pit"
    payload["stock_row_count"] = 5000
    payload["universe_membership_size"] = 5000
    payload["coverage_excluded_count"] = 0
    return payload


def test_pulse_attest_default_canary_accept_fails_closed(tmp_path: Path) -> None:
    """Default yaml (ON, claim project-universe) refuses a canary accept."""

    _accepted_canary(tmp_path)
    att = attest_pulse_tier12_production_read(
        "20260717",
        artifact_root=tmp_path,
    )
    assert att["uses_legacy"] is True
    assert att["cutover_allowed"] is False
    assert att["status"] == "BLOCKED"
    assert att["source"] == "legacy_scaffold"
    assert any("canary" in r for r in att["reasons"])
    assert "pulse_ui_attestation" in att["notes"]


def test_pulse_attest_default_live_partition_accepted_cutover() -> None:
    """Default on-disk yaml + live 20260717 accept → ACCEPTED_CUTOVER attestation."""

    att = attest_pulse_tier12_production_read("20260717")
    assert att["uses_legacy"] is False
    assert att["cutover_allowed"] is True
    assert att["status"] == "ACCEPTED_CUTOVER"
    assert att["source"] == "accepted_partition"
    assert att["claim_project_universe"] is True
    assert "pulse_ui_attestation" in att["notes"]


def test_pulse_form_overlay_leaves_legacy_rows(tmp_path: Path) -> None:
    _accepted_canary(tmp_path)
    rows = [
        {
            "stock_code": "600000",
            "form_name": "低位横盘",
            "is_breakout_event": True,
        }
    ]
    out, read = overlay_pulse_form_from_production_read(
        rows,
        "20260717",
        artifact_root=tmp_path,
    )
    assert read is not None and read.uses_legacy is True
    assert out[0]["form_name"] == "低位横盘"
    assert out[0]["is_breakout_event"] is True
    assert "tier12_form_source" not in out[0]


def test_pulse_form_overlay_uses_accepted_when_cutover_passes(tmp_path: Path) -> None:
    canary = _accepted_canary(tmp_path)
    full = _full_universe_accepted_dict(canary)
    (tmp_path / "accepted_20260717.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cfg = Tier12ConsumerCutoverConfig(
        cutover_allowed=True,
        expected_definition_version=full["definition_version"],
        expected_config_hash=full["config_hash"],
        claim_project_universe=True,
    )
    rows = [
        {
            "stock_code": "600000",
            "form_name": "legacy_form",
            "is_breakout_event": False,
        }
    ]
    out, read = overlay_pulse_form_from_production_read(
        rows,
        "20260717",
        config=cfg,
        artifact_root=tmp_path,
    )
    assert read is not None
    assert read.uses_legacy is False
    assert read.source == "accepted_partition"
    assert out[0]["tier12_form_source"] == "accepted_partition"
    # Accepted canary writer may omit form_name; overlay must not keep legacy.
    assert out[0]["form_name"] != "legacy_form"


def test_default_yaml_expected_hash_matches_publish_when_filled() -> None:
    from services.tier12_consumer_cutover import load_tier12_consumer_cutover_config
    from services.tier12_publish_contract import config_hash_for
    from services.tier12_publish_writer import load_tier12_publish_config

    pub = load_tier12_publish_config(_CFG_PATH)
    cut = load_tier12_consumer_cutover_config(_CFG_PATH)
    expected = config_hash_for(pub.stock_config_for_hash())
    assert cut.cutover_allowed is True
    assert cut.expected_definition_version == pub.stock_definition_version
    # Hash may be pre-filled for opt-in readiness; must match live publish policy.
    if cut.expected_config_hash:
        assert cut.expected_config_hash == expected
