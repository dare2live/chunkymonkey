"""Project-universe nominal loader + coverage exclusion (unit, no live DB)."""
from __future__ import annotations

from services.tier12_project_universe import (
    CoverageExclusion,
    ProjectUniverseNominalLoad,
    _coverage_exclusions,
)


def test_coverage_exclusions_record_missing_decision_day() -> None:
    membership = ("600000.SH", "000001.SZ", "300001.SZ")
    rows = [
        {
            "ts_code": "600000.SH",
            "trade_date": "20260717",
            "close": 10.0,
            "pct_chg": 1.0,
        },
        {
            "ts_code": "600000.SH",
            "trade_date": "20260716",
            "close": 9.0,
            "pct_chg": 0.5,
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": "20260716",
            "close": 8.0,
            "pct_chg": -1.0,
        },
    ]
    kept, excluded = _coverage_exclusions(membership, rows, "20260717")
    assert kept == ("600000.SH",)
    assert len(excluded) == 2
    assert {e.ts_code for e in excluded} == {"000001.SZ", "300001.SZ"}
    assert all(e.reason == "missing_decision_day_bar" for e in excluded)


def test_universe_attestation_shape() -> None:
    load = ProjectUniverseNominalLoad(
        decision_date="20260717",
        lookback_days=("20260716", "20260717"),
        membership_codes=("600000.SH", "000001.SZ"),
        membership_size=2,
        rows=(),
        codes_with_decision_bar=("600000.SH",),
        exclusions=(
            CoverageExclusion("000001.SZ", "missing_decision_day_bar"),
        ),
        available_at_mode="contractual",
        available_at_policy="contractual_same_day_at_1800",
        universe_policy_id="active_a_share_trading_universe",
        universe_policy_hash="deadbeef",
        population_kind="project_universe_pit",
        decision_time="2026-07-20T10:00:00+08:00",
        notes=("phase_c_project_universe_nominal",),
    )
    att = load.universe_attestation()
    assert att["population_kind"] == "project_universe_pit"
    assert att["membership_size"] == 2
    assert att["coverage_excluded_count"] == 1
    assert att["universe_policy_hash"] == "deadbeef"
