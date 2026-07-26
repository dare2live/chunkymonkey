"""Cap 5B 形态/阶段选股面 — filter honesty + freshness gate (fact_stock_form_daily)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import stock_screener as screener_api
from services import stock_screener as ss

_FORM_DDL = """
CREATE TABLE fact_stock_form_daily (
    stock_code VARCHAR, trade_date VARCHAR, axis_pos VARCHAR, axis_trend VARCHAR,
    axis_purity VARCHAR, axis_vol VARCHAR, axis_volregime VARCHAR,
    axis_pos_memb DOUBLE, axis_trend_memb DOUBLE, axis_purity_memb DOUBLE,
    axis_vol_memb DOUBLE, axis_volregime_memb DOUBLE,
    form_name VARCHAR, form_sub VARCHAR, weekly_name VARCHAR, monthly_name VARCHAR,
    is_breakout_event BOOLEAN, base_days INTEGER, buyable BOOLEAN, sellable BOOLEAN,
    is_one_word BOOLEAN, built_at TIMESTAMP
)
"""
_DIM_DDL = """
CREATE TABLE dim_active_a_stock (
    stock_code VARCHAR, stock_name VARCHAR, updated_at TIMESTAMP
)
"""
_CAL_DDL = """
CREATE TABLE dim_trading_calendar (trade_date VARCHAR PRIMARY KEY, is_trading BIGINT)
"""

_DATES = [f"202606{d:02d}" for d in range(1, 21)]  # 20 trading days, latest 20260620


def _insert_form(con, *, stock_code: str, trade_date: str, axis_pos: str, axis_trend: str,
                  axis_purity: str, axis_vol: str, form_name: str, form_sub: str,
                  is_breakout_event: bool = False) -> None:
    con.execute(
        """
        INSERT INTO fact_stock_form_daily
        (stock_code, trade_date, axis_pos, axis_trend, axis_purity, axis_vol,
         form_name, form_sub, is_breakout_event)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [stock_code, trade_date, axis_pos, axis_trend, axis_purity, axis_vol,
         form_name, form_sub, is_breakout_event],
    )


def _base_conn(*, calendar_through: str = "20260620") -> object:
    con = duck_mem()
    con.execute(_FORM_DDL)
    con.execute(_DIM_DDL)
    con.execute(_CAL_DDL)
    for td in _DATES:
        if td > calendar_through:
            break
        con.execute("INSERT INTO dim_trading_calendar VALUES (?, 1)", [f"{td[:4]}-{td[4:6]}-{td[6:8]}"])
    return con


def _fresh_conn():
    con = _base_conn()
    _insert_form(
        con, stock_code="600001", trade_date="20260620", axis_pos="low",
        axis_trend="up", axis_purity="trending", axis_vol="shrink",
        form_name="震荡上行", form_sub="温和震荡上行", is_breakout_event=True,
    )
    _insert_form(
        con, stock_code="600002", trade_date="20260620", axis_pos="high",
        axis_trend="down", axis_purity="choppy", axis_vol="heavy",
        form_name="放量下跌", form_sub="高位放量下跌",
    )
    con.execute("INSERT INTO dim_active_a_stock VALUES ('600001', '甲公司', now())")
    con.execute("INSERT INTO dim_active_a_stock VALUES ('600002', '乙公司', now())")
    return con


def test_form_name_filter_matches_and_has_why_sentence():
    con = _fresh_conn()
    out = ss.build_form_stage_screen(con, form_names=["震荡上行"])
    assert out["status"] == "ok"
    assert out["as_of"] == "20260620"
    assert out["count"] == 1
    row = out["rows"][0]
    assert row["stock_code"] == "600001"
    assert row["stock_name"] == "甲公司"
    assert "震荡上行" in row["why"]
    assert "低位" in row["why"]


def test_axis_filter_excludes_non_matching_stock():
    con = _fresh_conn()
    out = ss.build_form_stage_screen(con, axis_pos="low")
    codes = {r["stock_code"] for r in out["rows"]}
    assert codes == {"600001"}
    assert "600002" not in codes


def test_breakout_event_filter():
    con = _fresh_conn()
    out = ss.build_form_stage_screen(con, is_breakout_event=True)
    assert {r["stock_code"] for r in out["rows"]} == {"600001"}


def test_no_filters_returns_all_hs_a_rows_at_latest_as_of():
    con = _fresh_conn()
    out = ss.build_form_stage_screen(con)
    assert out["status"] == "ok"
    assert out["count"] == 2


def test_stale_when_as_of_lags_calendar_beyond_sla():
    con = _base_conn(calendar_through="20260625")
    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    _insert_form(
        con, stock_code="600001", trade_date="20260620", axis_pos="low",
        axis_trend="up", axis_purity="trending", axis_vol="shrink",
        form_name="震荡上行", form_sub="温和震荡上行",
    )
    out = ss.build_form_stage_screen(con)
    assert out["status"] == "stale"
    assert "as_of_lag" in out["reason"]
    assert out["rows"] == []


def test_no_match_yields_empty_ok_not_error():
    con = _fresh_conn()
    out = ss.build_form_stage_screen(con, form_names=["不存在的形态"])
    assert out["status"] == "ok"
    assert out["rows"] == []
    assert out["reason"] == "no_stock_matches_filters"


def test_options_returns_live_facet_counts():
    con = _fresh_conn()
    out = ss.build_options(con)
    assert out["status"] == "ok"
    assert out["as_of"] == "20260620"
    form_values = {f["value"] for f in out["facets"]["form_name"]}
    assert form_values == {"震荡上行", "放量下跌"}
    axis_pos_values = {f["value"]: f["count"] for f in out["facets"]["axis_pos"]}
    assert axis_pos_values == {"low": 1, "high": 1}


def test_options_stale_when_calendar_lags():
    con = _base_conn(calendar_through="20260625")
    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    _insert_form(
        con, stock_code="600001", trade_date="20260620", axis_pos="low",
        axis_trend="up", axis_purity="trending", axis_vol="shrink",
        form_name="震荡上行", form_sub="温和震荡上行",
    )
    out = ss.build_options(con)
    assert out["status"] == "stale"
    assert out["facets"] == {}


def _app_with_conn(con):
    app = FastAPI()
    app.include_router(screener_api.router, prefix="/api/v3/screener")

    def _override():
        yield con

    app.dependency_overrides[screener_api.get_screener_conn] = _override
    return TestClient(app)


def test_api_options_and_form_stage_and_bad_axis():
    con = _fresh_conn()
    client = _app_with_conn(con)

    r = client.get("/api/v3/screener/options")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    r2 = client.get("/api/v3/screener/form_stage?axis_pos=low")
    assert r2.status_code == 200
    body = r2.json()
    assert body["surface"] == "stock_screener_form_stage"
    assert body["rows"][0]["stock_code"] == "600001"

    r3 = client.get("/api/v3/screener/form_stage?axis_pos=bogus")
    assert r3.status_code == 400


def test_api_multi_form_name_query():
    con = _fresh_conn()
    client = _app_with_conn(con)
    r = client.get(
        "/api/v3/screener/form_stage?form_name=震荡上行&form_name=放量下跌"
    )
    assert r.status_code == 200
    codes = {row["stock_code"] for row in r.json()["rows"]}
    assert codes == {"600001", "600002"}
