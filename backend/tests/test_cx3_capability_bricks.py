"""CX-3 capability bricks — briefing / sector membership / stock flow streak."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import decision_assist as assist_api
from services import daily_briefing as briefing
from services import sector_membership_serve as sms
from services import stock_flow_streak as sfs

_DATES = [f"202606{d:02d}" for d in range(1, 21)]  # latest 20260620


def _cal(con, *, through: str = "20260620") -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS dim_trading_calendar "
        "(trade_date VARCHAR PRIMARY KEY, is_trading BIGINT)"
    )
    for td in _DATES:
        if td > through:
            break
        con.execute(
            "INSERT OR IGNORE INTO dim_trading_calendar VALUES (?, 1)",
            [f"{td[:4]}-{td[4:6]}-{td[6:8]}"],
        )


def _sector_ddl(con) -> None:
    con.execute(
        """
        CREATE TABLE mart_sector_pulse_daily (
            chain VARCHAR, sector_code VARCHAR, sector_name VARCHAR,
            level VARCHAR, content_type VARCHAR, trade_date VARCHAR,
            pct_change DOUBLE, net_amount DOUBLE, flow_z DOUBLE,
            flow_streak BIGINT, cum_ratio_20d DOUBLE, flow_regime VARCHAR
        )
        """
    )


def _insert_strong_sector(con, *, sector_code: str, sector_name: str, chain: str) -> None:
    for i, td in enumerate(_DATES):
        con.execute(
            """
            INSERT INTO mart_sector_pulse_daily VALUES
            (?, ?, ?, 'L1', 'industry', ?, 0.2, 50000.0, 0.5, ?, ?, 'accum_in_silent')
            """,
            [chain, sector_code, sector_name, td, i + 1, 1.0 if i == 19 else None],
        )


def _member_ddl(con) -> None:
    con.execute(
        """
        CREATE TABLE fact_dc_member_daily (
            trade_date VARCHAR, ts_code VARCHAR, con_code VARCHAR, name VARCHAR,
            available_at TIMESTAMPTZ, source_table VARCHAR, built_at TIMESTAMPTZ
        )
        """
    )


def _form_ddl(con) -> None:
    con.execute(
        """
        CREATE TABLE fact_stock_form_daily (
            stock_code VARCHAR, trade_date VARCHAR, axis_pos VARCHAR, axis_trend VARCHAR,
            axis_purity VARCHAR, axis_vol VARCHAR, form_name VARCHAR, form_sub VARCHAR,
            weekly_name VARCHAR, monthly_name VARCHAR,
            is_breakout_event BOOLEAN, base_days INTEGER
        )
        """
    )


def _mf_dc_ddl(con) -> None:
    con.execute(
        """
        CREATE TABLE fact_stock_moneyflow_dc_daily (
            trade_date VARCHAR, ts_code VARCHAR, stock_code VARCHAR,
            net_amount DOUBLE, net_amount_rate DOUBLE, pct_change DOUBLE,
            available_at TIMESTAMPTZ, source_table VARCHAR, built_at TIMESTAMPTZ
        )
        """
    )


def _sw_attach(con) -> None:
    con.execute("ATTACH ':memory:' AS tr")
    con.execute(
        """
        CREATE TABLE tr.raw_tushare_index_member_all (
            l1_code VARCHAR, l1_name VARCHAR, l2_code VARCHAR, l2_name VARCHAR,
            l3_code VARCHAR, l3_name VARCHAR, ts_code VARCHAR, name VARCHAR,
            in_date VARCHAR, out_date VARCHAR, is_new VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE VIEW tr.v_sw_industry_pit AS
        SELECT * FROM tr.raw_tushare_index_member_all
        """
    )


def _briefing_fresh_conn():
    con = duck_mem()
    _cal(con)
    _sector_ddl(con)
    _member_ddl(con)
    _form_ddl(con)
    _sw_attach(con)
    _insert_strong_sector(con, sector_code="BK001", sector_name="行业A", chain="dc_industry")
    _insert_strong_sector(con, sector_code="BK101", sector_name="概念X", chain="dc_concept")
    _insert_strong_sector(con, sector_code="801010.SI", sector_name="申万农林", chain="sw_industry")
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES "
        "('20260620', 'BK001', '600001.SH', '甲', now(), 'raw', now())"
    )
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES "
        "('20260620', 'BK101', '600001.SH', '甲', now(), 'raw', now())"
    )
    con.execute(
        """
        INSERT INTO tr.raw_tushare_index_member_all VALUES
        ('801010.SI', '申万农林', 'L2X', 'L2', 'L3X', 'L3', '600001.SH', '甲',
         '20200101', NULL, 'Y')
        """
    )
    con.execute(
        """
        INSERT INTO fact_stock_form_daily VALUES
        ('600001', '20260620', 'low', 'up', 'trending', 'shrink',
         '震荡上行', '温和震荡上行', NULL, NULL, TRUE, 10)
        """
    )
    return con


def test_briefing_ok_aggregates_conclusion_and_why():
    con = _briefing_fresh_conn()
    out = briefing.build_daily_briefing(con, horizon=20)
    assert out["status"] == "ok"
    assert out["narrative"]
    assert "资金" in out["narrative"] or "形态" in out["narrative"] or "交集" in out["narrative"]
    ids = {s["id"] for s in out["sections"]}
    assert ids == {"moneyflow", "intersection", "screener"}
    assert out["inputs"]["moneyflow"]["trust"] == "trusted"
    assert out["inputs"]["intersection"]["trust"] == "trusted"
    assert out["inputs"]["screener"]["trust"] == "trusted"
    # At least one section carries real brick text (not stub).
    texts = [i["text"] for s in out["sections"] for i in s["items"]]
    assert any(texts)


def test_briefing_fail_closed_when_moneyflow_as_of_lags():
    con = _briefing_fresh_conn()
    # Calendar advances past board as_of beyond SLA → moneyflow untrusted.
    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    out = briefing.build_daily_briefing(con, horizon=20)
    assert out["status"] == "stale"
    assert out["narrative"] is None
    assert out["sections"] == []
    assert "moneyflow" in out["reason"]


def test_briefing_fail_closed_when_intersection_stale():
    con = duck_mem()
    _cal(con)
    _sector_ddl(con)
    _member_ddl(con)
    _form_ddl(con)
    _sw_attach(con)
    _insert_strong_sector(con, sector_code="BK001", sector_name="行业A", chain="dc_industry")
    _insert_strong_sector(con, sector_code="801010.SI", sector_name="申万农林", chain="sw_industry")
    # Concept chain only has earlier day → intersection mismatch → stale.
    con.execute(
        """
        INSERT INTO mart_sector_pulse_daily VALUES
        ('dc_concept', 'BK101', '概念X', 'L1', 'industry', '20260619',
         0.2, 50000.0, 0.5, 1, NULL, 'accum_in_silent')
        """
    )
    con.execute(
        """
        INSERT INTO fact_stock_form_daily VALUES
        ('600001', '20260620', 'low', 'up', 'trending', 'shrink',
         '震荡上行', '温和', NULL, NULL, TRUE, 10)
        """
    )
    out = briefing.build_daily_briefing(con, horizon=20)
    assert out["status"] == "stale"
    assert out["narrative"] is None
    assert "intersection" in out["reason"]


def test_stock_flow_streak_universe_inflow():
    con = duck_mem()
    _cal(con)
    _mf_dc_ddl(con)
    # 600001: 5 consecutive inflow days ending 20260620
    for i, td in enumerate(_DATES[-5:]):
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600001.SH', '600001', ?, 0.1, 0.2, now(), 'raw', now())
            """,
            [td, 100.0 + i],
        )
    # 600002: broken by a zero day → streak 1 only
    for td, net in zip(_DATES[-3:], [10.0, 0.0, 20.0]):
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600002.SZ', '600002', ?, 0.1, 0.2, now(), 'raw', now())
            """,
            [td, net],
        )
    out = sfs.build_stock_flow_streak_universe(
        con, direction="inflow", min_streak=5, limit=20,
    )
    assert out["status"] == "ok"
    assert out["as_of"] == "20260620"
    codes = {r["stock_code"] for r in out["rows"]}
    assert "600001" in codes
    assert "600002" not in codes
    row = out["rows"][0]
    assert row["streak_days"] == 5
    assert row["flow_streak"] == 5
    assert "连续5日净流入" in row["why"]


def test_stock_flow_streak_fail_closed_on_lag():
    con = duck_mem()
    _cal(con)
    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    _mf_dc_ddl(con)
    for td in _DATES[-5:]:
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600001.SH', '600001', 100.0, 0.1, 0.2, now(), 'raw', now())
            """,
            [td],
        )
    out = sfs.build_stock_flow_streak_universe(con, direction="inflow", min_streak=3)
    assert out["status"] == "stale"
    assert out["rows"] == []
    assert "as_of_lag" in out["reason"]


def test_compute_stock_flow_streak_signed():
    con = duck_mem()
    _mf_dc_ddl(con)
    for td in _DATES[-3:]:
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600001.SH', '600001', -50.0, -0.1, -0.2, now(), 'raw', now())
            """,
            [td],
        )
    block = sfs.compute_stock_flow_streak(con, "600001")
    assert block["flow_streak"] == -3
    assert block["direction"] == "outflow"


def test_sector_membership_dc_ok_and_stale():
    con = duck_mem()
    _cal(con)
    _member_ddl(con)
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES "
        "('20260620', 'BK001', '600001.SH', '甲', now(), 'raw', now())"
    )
    con.execute(
        "INSERT INTO fact_dc_member_daily VALUES "
        "('20260620', 'BK001', '600002.SZ', '乙', now(), 'raw', now())"
    )
    ok = sms.build_sector_membership(
        con, chain="dc_industry", sector_code="BK001",
    )
    assert ok["status"] == "ok"
    assert ok["count"] == 2
    assert {r["stock_code"] for r in ok["rows"]} == {"600001", "600002"}
    assert ok["membership_pit"] is True

    con.execute("INSERT INTO dim_trading_calendar VALUES ('2026-06-25', 1)")
    stale = sms.build_sector_membership(
        con, chain="dc_industry", sector_code="BK001",
    )
    assert stale["status"] == "stale"
    assert stale["rows"] == []


def test_api_cx3_endpoints_surface_names():
    con = _briefing_fresh_conn()
    _mf_dc_ddl(con)
    for td in _DATES[-5:]:
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600001.SH', '600001', 100.0, 0.1, 0.2, now(), 'raw', now())
            """,
            [td],
        )

    app = FastAPI()
    app.include_router(assist_api.router, prefix="/api/v3/decision")

    def _override():
        try:
            yield con
        finally:
            pass

    app.dependency_overrides[assist_api.get_assist_conn] = _override
    app.dependency_overrides[assist_api.get_membership_conn] = _override
    client = TestClient(app)

    b = client.get("/api/v3/decision/briefing/daily?horizon=20")
    assert b.status_code == 200
    assert b.json()["surface"] == "daily_briefing"
    assert b.json()["status"] == "ok"

    m = client.get("/api/v3/decision/sector/members?sector_code=BK001&chain=dc_industry")
    assert m.status_code == 200
    assert m.json()["surface"] == "decision_sector_membership"
    assert m.json()["status"] == "ok"

    s = client.get(
        "/api/v3/decision/moneyflow/stock_streak?direction=inflow&min_streak=5"
    )
    assert s.status_code == 200
    assert s.json()["surface"] == "stock_flow_streak_universe"
    assert s.json()["status"] == "ok"
