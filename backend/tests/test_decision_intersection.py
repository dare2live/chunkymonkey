"""Cap 4D 交集最强股 — 3-chain honesty (freshness gate + why sentence)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import decision_assist as assist_api
from services import decision_intersection as di

_SECTOR_DDL = """
CREATE TABLE mart_sector_pulse_daily (
    chain VARCHAR, sector_code VARCHAR, sector_name VARCHAR,
    level VARCHAR, content_type VARCHAR, trade_date VARCHAR,
    pct_change DOUBLE, net_amount DOUBLE, flow_z DOUBLE,
    flow_streak BIGINT, cum_ratio_20d DOUBLE, flow_regime VARCHAR
)
"""
_MEMBER_DDL = """
CREATE TABLE fact_dc_member_daily (
    trade_date VARCHAR, ts_code VARCHAR, con_code VARCHAR, name VARCHAR,
    available_at TIMESTAMPTZ, source_table VARCHAR, built_at TIMESTAMPTZ
)
"""
_CAL_DDL = """
CREATE TABLE dim_trading_calendar (trade_date VARCHAR PRIMARY KEY, is_trading BIGINT)
"""
_SW_DDL = """
ATTACH ':memory:' AS tr;
CREATE TABLE tr.raw_tushare_index_member_all (
    l1_code VARCHAR, l1_name VARCHAR, l2_code VARCHAR, l2_name VARCHAR,
    l3_code VARCHAR, l3_name VARCHAR, ts_code VARCHAR, name VARCHAR,
    in_date VARCHAR, out_date VARCHAR, is_new VARCHAR
);
CREATE VIEW tr.v_sw_industry_pit AS
SELECT l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
       ts_code, name, in_date, out_date, is_new
FROM tr.raw_tushare_index_member_all;
"""

_DATES = [f"202606{d:02d}" for d in range(1, 21)]  # 20 trading days, latest 20260620


def _insert_strong_sector(con, *, sector_code: str, sector_name: str, chain: str, level: str = "L1") -> None:
    for i, td in enumerate(_DATES):
        con.execute(
            """
            INSERT INTO mart_sector_pulse_daily VALUES
            (?, ?, ?, ?, 'industry', ?, 0.2, 50000.0, 0.5, ?, ?, 'accum_in_silent')
            """,
            [chain, sector_code, sector_name, level, td, i + 1, 1.0 if i == 19 else None],
        )


def _insert_member(con, *, sector_code: str, trade_date: str, con_code: str, name: str) -> None:
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES (?, ?, ?, ?, now(), 'raw', now())",
        [trade_date, sector_code, con_code, name],
    )


def _insert_sw_member(con, *, l1_code: str, l1_name: str, ts_code: str, name: str) -> None:
    con.execute(
        """
        INSERT INTO tr.raw_tushare_index_member_all VALUES
        (?, ?, 'L2X', 'L2名', 'L3X', 'L3名', ?, ?, '20200101', NULL, 'Y')
        """,
        [l1_code, l1_name, ts_code, name],
    )


def _base_conn(*, calendar_through: str = "20260620") -> object:
    con = duck_mem()
    con.execute(_SECTOR_DDL)
    con.execute(_MEMBER_DDL)
    con.execute(_CAL_DDL)
    con.execute(_SW_DDL)
    for i, td in enumerate(_DATES):
        if td > calendar_through:
            break
        con.execute("INSERT INTO dim_trading_calendar VALUES (?, 1)", [f"{td[:4]}-{td[4:6]}-{td[6:8]}"])
    return con


def _fresh_intersection_conn():
    con = _base_conn()
    _insert_strong_sector(con, sector_code="BK001", sector_name="行业A", chain="dc_industry")
    _insert_strong_sector(con, sector_code="BK101", sector_name="概念X", chain="dc_concept")
    _insert_strong_sector(con, sector_code="801010.SI", sector_name="申万农林", chain="sw_industry")
    # 甲 (600001.SH) is a member of ALL three strong sectors → intersection hit.
    _insert_member(con, sector_code="BK001", trade_date="20260620", con_code="600001.SH", name="甲")
    _insert_member(con, sector_code="BK001", trade_date="20260620", con_code="600002.SZ", name="乙")
    _insert_member(con, sector_code="BK101", trade_date="20260620", con_code="600001.SH", name="甲")
    _insert_member(con, sector_code="BK101", trade_date="20260620", con_code="600003.SZ", name="丙")
    _insert_sw_member(con, l1_code="801010.SI", l1_name="申万农林", ts_code="600001.SH", name="甲")
    _insert_sw_member(con, l1_code="801010.SI", l1_name="申万农林", ts_code="600004.SH", name="丁")
    return con


def test_intersection_hit_has_why_sentence_and_three_chains():
    con = _fresh_intersection_conn()
    out = di.build_intersection_strongest(con, horizon=20, limit=10)
    assert out["status"] == "ok"
    assert out["as_of"] == {
        "dc_industry": "20260620",
        "dc_concept": "20260620",
        "sw_industry": "20260620",
    }
    assert out["chains"] == ["dc_industry", "dc_concept", "sw_industry"]
    assert out["count"] == 1
    row = out["rows"][0]
    assert row["stock_code"] == "600001"
    assert row["stock_name"] == "甲"
    assert row["industry_sectors"][0]["sector_name"] == "行业A"
    assert row["concept_sectors"][0]["sector_name"] == "概念X"
    assert row["sw_sectors"][0]["sector_name"] == "申万农林"
    assert "行业A" in row["why"] and "概念X" in row["why"] and "申万农林" in row["why"]


def test_non_intersecting_members_excluded():
    con = _fresh_intersection_conn()
    out = di.build_intersection_strongest(con, horizon=20, limit=10)
    codes = {r["stock_code"] for r in out["rows"]}
    assert "600002" not in codes  # only in industry chain
    assert "600003" not in codes  # only in concept chain
    assert "600004" not in codes  # only in sw chain


def test_stale_when_chain_as_of_mismatch():
    con = _base_conn()
    _insert_strong_sector(con, sector_code="BK001", sector_name="行业A", chain="dc_industry")
    _insert_strong_sector(con, sector_code="801010.SI", sector_name="申万农林", chain="sw_industry")
    # Concept chain lags one day behind → mismatch → fail-closed.
    con.execute(
        """
        INSERT INTO mart_sector_pulse_daily VALUES
        ('dc_concept', 'BK101', '概念X', 'L1', 'industry', '20260619', 0.2, 50000.0, 0.5, 1, NULL, 'accum_in_silent')
        """
    )
    out = di.build_intersection_strongest(con, horizon=20, limit=10)
    assert out["status"] == "stale"
    assert "mismatch" in out["reason"]
    assert out["rows"] == []


def test_stale_when_as_of_lags_calendar_beyond_sla():
    # Calendar knows about a later trading day than the sector board covers.
    con = _base_conn(calendar_through="20260625")
    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    _insert_strong_sector(con, sector_code="BK001", sector_name="行业A", chain="dc_industry")
    _insert_strong_sector(con, sector_code="BK101", sector_name="概念X", chain="dc_concept")
    _insert_strong_sector(con, sector_code="801010.SI", sector_name="申万农林", chain="sw_industry")
    out = di.build_intersection_strongest(con, horizon=20, limit=10)
    assert out["status"] == "stale"
    assert "as_of_lag" in out["reason"]


def test_no_strong_sectors_yields_empty_ok_not_error():
    con = _base_conn()
    # All chains present but neutral (never mapped to chase/latent) → no strong sectors.
    for i, td in enumerate(_DATES):
        for chain, code, name in (
            ("dc_industry", "BK001", "行业A"),
            ("dc_concept", "BK101", "概念X"),
            ("sw_industry", "801010.SI", "申万农林"),
        ):
            con.execute(
                """
                INSERT INTO mart_sector_pulse_daily VALUES
                (?, ?, ?, 'L1', 'industry', ?, 0.0, 0.0, 0.0, 0, NULL, 'neutral')
                """,
                [chain, code, name, td],
            )
    out = di.build_intersection_strongest(con, horizon=20, limit=10)
    assert out["status"] == "ok"
    assert out["rows"] == []
    assert out["reason"] == "no_strong_sector_intersection_this_window"


def test_invalid_horizon_rejected():
    con = _fresh_intersection_conn()
    with pytest.raises(ValueError):
        di.build_intersection_strongest(con, horizon=7, limit=10)


def test_stock_lookup_hit_and_miss():
    con = _fresh_intersection_conn()
    hit = di.build_intersection_for_stock(con, stock_code="600001", horizon=20)
    assert hit["in_intersection"] is True
    assert hit["detail"]["stock_code"] == "600001"

    miss = di.build_intersection_for_stock(con, stock_code="600002", horizon=20)
    assert miss["in_intersection"] is False
    assert miss["detail"] is None


def _app_with_conn(con):
    app = FastAPI()
    app.include_router(assist_api.router, prefix="/api/v3/decision")

    def _override():
        yield con

    app.dependency_overrides[assist_api.get_assist_conn] = _override
    return TestClient(app)


def test_api_intersection_strongest_and_stock_and_bj_gate():
    con = _fresh_intersection_conn()
    client = _app_with_conn(con)

    r = client.get("/api/v3/decision/intersection/strongest?horizon=20")
    assert r.status_code == 200
    body = r.json()
    assert body["surface"] == "decision_intersection_strongest"
    assert body["rows"][0]["stock_code"] == "600001"
    assert body["rows"][0]["sw_sectors"]

    r2 = client.get("/api/v3/decision/intersection/stock/600001?horizon=20")
    assert r2.status_code == 200
    assert r2.json()["in_intersection"] is True

    r3 = client.get("/api/v3/decision/intersection/stock/920001")
    assert r3.status_code == 404

    r4 = client.get("/api/v3/decision/intersection/strongest?horizon=999")
    assert r4.status_code == 400
