"""P4c champion model 单测."""
from __future__ import annotations

import duckdb
import pytest

from services.portfolio.champion import (
    CHAMPION_DDL,
    ChampionRecord,
    compare_challenger,
    get_current_champion,
    register_champion,
    validate_champion_kpi_completeness,
)


def _make_conn():
    conn = duckdb.connect(":memory:")
    conn.execute(CHAMPION_DDL)
    return conn


def _full_rec(champ_id="ch1") -> ChampionRecord:
    return ChampionRecord(
        champion_id=champ_id, model_id="lgbm_v1", model_version="p0b_v1",
        feature_version="p0a_v1", label_version="p0a_v1",
        rank_ic=0.05, ann_ret=0.35, max_dd=-0.15,
        monthly_win_rate=0.65, excess_vs_hs300=0.20,
        turnover=2.0, tx_cost_pct=0.05, capacity_concentration=0.20,
    )


def test_validate_kpi_complete_passes():
    missing = validate_champion_kpi_completeness(_full_rec())
    assert missing == []


def test_validate_missing_rank_ic_fails():
    rec = ChampionRecord(
        champion_id="ch1", model_id="m", model_version="v", feature_version="v", label_version="v",
        rank_ic=float("nan"), ann_ret=0.30, max_dd=-0.20,
        monthly_win_rate=0.60, excess_vs_hs300=0.05,
        turnover=1.0, tx_cost_pct=0.03, capacity_concentration=0.10,
    )
    missing = validate_champion_kpi_completeness(rec)
    assert "rank_ic" in missing


def test_register_complete_succeeds():
    conn = _make_conn()
    rec = _full_rec()
    ok = register_champion(conn, rec, promote=True, reason="initial")
    assert ok is True
    cur = get_current_champion(conn)
    assert cur is not None
    assert cur["champion_id"] == "ch1"
    assert cur["is_current_champion"] is True
    assert cur["promoted_reason"] == "initial"


def test_register_incomplete_rejects():
    conn = _make_conn()
    bad = ChampionRecord(
        champion_id="ch_bad", model_id="m", model_version="v",
        feature_version="v", label_version="v",
        rank_ic=float("nan"),  # 缺
        ann_ret=0.30, max_dd=-0.20,
        monthly_win_rate=0.60, excess_vs_hs300=0.05,
        turnover=1.0, tx_cost_pct=0.03, capacity_concentration=0.10,
    )
    ok = register_champion(conn, bad, promote=False)
    assert ok is False
    cur = conn.execute("SELECT COUNT(*) FROM mart_champion_model").fetchone()[0]
    assert cur == 0


def test_promote_sets_single_champion():
    conn = _make_conn()
    register_champion(conn, _full_rec("ch1"), promote=True, reason="initial")
    register_champion(conn, _full_rec("ch2"), promote=True, reason="upgrade")
    # Only ch2 should be current
    cur = get_current_champion(conn)
    assert cur["champion_id"] == "ch2"
    n_curr = conn.execute(
        "SELECT COUNT(*) FROM mart_champion_model WHERE is_current_champion = TRUE"
    ).fetchone()[0]
    assert n_curr == 1


def test_register_without_promote():
    conn = _make_conn()
    register_champion(conn, _full_rec("ch1"), promote=False)
    cur = get_current_champion(conn)
    assert cur is None  # 没 promote → 没 current


def test_compare_challenger_no_champion():
    conn = _make_conn()
    challenger = _full_rec("c1")
    cmp = compare_challenger(conn, challenger)
    assert cmp["verdict"] == "no_champion_yet"


def test_compare_challenger_with_champion():
    conn = _make_conn()
    champ = _full_rec("champ1")
    register_champion(conn, champ, promote=True, reason="initial")
    # Challenger with better RankIC
    challenger = ChampionRecord(
        champion_id="ch_new", model_id="lgbm_v2", model_version="p0b_v2",
        feature_version="p0a_v1", label_version="p0a_v1",
        rank_ic=0.07, ann_ret=0.40, max_dd=-0.13,
        monthly_win_rate=0.70, excess_vs_hs300=0.25,
        turnover=2.0, tx_cost_pct=0.05, capacity_concentration=0.20,
    )
    cmp = compare_challenger(conn, challenger)
    assert cmp["verdict"] == "compare"
    assert cmp["rank_ic_champion"] == 0.05
    assert cmp["rank_ic_challenger"] == 0.07
    assert abs(cmp["rank_ic_delta"] - 0.02) < 1e-9
