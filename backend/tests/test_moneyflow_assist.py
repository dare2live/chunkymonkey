"""Cap A moneyflow assist — behavior map + horizon honesty + board ratio."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import decision_assist as assist_api
from services import moneyflow_assist as mfa


def test_behavior_map_latent_and_chase_guards():
    cfg = mfa.load_cfg()
    latent = mfa.behavior_from_regime("accum_in_silent", window_return_pct=0.2, cfg=cfg)
    assert latent["behavior"] == "latent"
    assert "潜伏" in latent["behavior_zh"]

    # chase requires non-positive floor breach → unknown when return missing/≤0
    chase_ok = mfa.behavior_from_regime("surge_in", window_return_pct=1.5, cfg=cfg)
    assert chase_ok["behavior"] == "chase"
    chase_bad = mfa.behavior_from_regime("surge_in", window_return_pct=-0.1, cfg=cfg)
    assert chase_bad["behavior"] == "unknown"

    # distribute with strong rally → unknown
    dist_bad = mfa.behavior_from_regime("surge_out", window_return_pct=3.0, cfg=cfg)
    assert dist_bad["behavior"] == "unknown"
    dist_ok = mfa.behavior_from_regime("accum_out_silent", window_return_pct=-0.5, cfg=cfg)
    assert dist_ok["behavior"] == "distribute"
    # incomplete window return must not keep distribute (honesty asymmetry fix)
    dist_none = mfa.behavior_from_regime("accum_out_silent", window_return_pct=None, cfg=cfg)
    assert dist_none["behavior"] == "unknown"


def test_incomplete_horizon_board_behavior_unknown():
    con = duck_mem()
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
    # Only 3 days but request horizon=5 → status unknown; must not show 出货
    for i, td in enumerate(["20260601", "20260602", "20260603"]):
        con.execute(
            """
            INSERT INTO mart_sector_pulse_daily VALUES
            ('dc_industry', 'BK009', '短史板块', 'L1', 'industry', ?,
             -0.2, -50000.0, -1.0, ?, NULL, 'accum_out_silent')
            """,
            [td, -(i + 1)],
        )
    out = mfa.build_sector_board(con, chain="dc_industry", horizon=5, limit=10)
    assert out["rows"]
    row = out["rows"][0]
    assert row["horizon"]["status"] == "unknown"
    assert row["behavior"]["behavior"] == "unknown"
    assert row["conclusion"] and "不形成结论" in row["conclusion"]


def test_horizon_incomplete_is_unknown():
    nets = [1.0, 2.0, None, 4.0]  # not full for h=4
    out = mfa._horizon_metrics(nets, [0.1, 0.1, 0.1, 0.1], horizon=4, sector_mv=1e6)
    assert out["status"] == "unknown"
    assert out["cum_net"] is None
    assert out["relative_ratio_pct"] is None


def test_implied_mv_and_20d_ratio_consistency():
    # 20d cum_net=2e6, ratio=1.0% → mv=2e8
    mv = mfa._implied_sector_mv(2e6, 1.0)
    assert mv == pytest.approx(2e8)
    nets = [1e5] * 20
    pcts = [0.1] * 20
    m = mfa._horizon_metrics(nets, pcts, horizon=20, sector_mv=mv)
    assert m["status"] == "known"
    assert m["relative_ratio_pct"] == pytest.approx(1.0)


def _board_fixture():
    con = duck_mem()
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
    # 20 days of quiet inflow + flat-ish price → latent
    for i in range(20):
        td = f"202606{i+1:02d}" if i < 30 else f"202607{i-29:02d}"
        # use sequential YYYYMMDD-ish: 20260601.. then wrap — simpler fixed list
        pass
    dates = [f"202606{d:02d}" for d in range(1, 21)]  # 20 days
    for i, td in enumerate(dates):
        # sector_mv implied = 1e8; each day net=5e4 → cum_net_20=1e6 → ratio=1.0%
        con.execute(
            """
            INSERT INTO mart_sector_pulse_daily VALUES
            ('dc_industry', 'BK001', '测试行业', 'L1', 'industry', ?,
             0.2, 50000.0, 0.5, ?, ?, 'accum_in_silent')
            """,
            [td, i + 1, 1.0 if i == 19 else None],
        )
    # Only latest day needs cum_ratio_20d for implied mv path
    con.execute(
        """
        UPDATE mart_sector_pulse_daily SET cum_ratio_20d = 1.0
        WHERE sector_code = 'BK001' AND trade_date = '20260620'
        """
    )
    return con


def test_sector_board_maps_latent_and_horizon():
    con = _board_fixture()
    out = mfa.build_sector_board(con, chain="dc_industry", horizon=20, limit=10)
    assert out["status"] == "ok"
    assert out["as_of"] == "20260620"
    assert out["rows"]
    row = out["rows"][0]
    assert row["behavior"]["behavior"] == "latent"
    assert row["horizon"]["status"] == "known"
    assert row["horizon"]["relative_ratio_pct"] == pytest.approx(1.0)
    assert row["conclusion"] and "潜伏" in row["conclusion"]


def test_board_api_and_stock_hs_a_gate(monkeypatch):
    con = _board_fixture()
    con.execute(
        """
        CREATE TABLE fact_stock_moneyflow_dc_daily (
            trade_date VARCHAR, ts_code VARCHAR, stock_code VARCHAR,
            net_amount DOUBLE, net_amount_rate DOUBLE, pct_change DOUBLE,
            available_at TIMESTAMP, source_table VARCHAR, built_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE dim_stock_segment_daily (
            stock_code VARCHAR, trade_date VARCHAR, circ_mv DOUBLE
        )
        """
    )
    con.execute(
        """
        CREATE TABLE dim_stock_dc_industry (
            stock_code VARCHAR, tdx_l1 VARCHAR, tdx_l1_name VARCHAR,
            tdx_l2 VARCHAR, tdx_l2_name VARCHAR, tdx_l3 VARCHAR, tdx_l3_name VARCHAR
        )
        """
    )
    for i, td in enumerate([f"202606{d:02d}" for d in range(1, 21)]):
        con.execute(
            """
            INSERT INTO fact_stock_moneyflow_dc_daily VALUES
            (?, '600519.SH', '600519', 100.0, 0.5, 0.2, now(), 'raw', now())
            """,
            [td],
        )
    con.execute(
        "INSERT INTO dim_stock_segment_daily VALUES ('600519', '20260620', 10000.0)"
    )
    con.execute(
        """
        INSERT INTO dim_stock_dc_industry VALUES
        ('600519', 'BK0', '白酒', 'BK0', '白酒', 'BK001', '测试行业')
        """
    )

    app = FastAPI()
    app.include_router(assist_api.router, prefix="/api/v3/decision")

    def _override():
        yield con

    app.dependency_overrides[assist_api.get_assist_conn] = _override
    client = TestClient(app)

    r = client.get("/api/v3/decision/moneyflow/board?chain=dc_industry&horizon=20")
    assert r.status_code == 200
    body = r.json()
    assert body["surface"] == "moneyflow_decision_assist"
    assert body["rows"][0]["behavior"]["behavior"] == "latent"

    r2 = client.get("/api/v3/decision/moneyflow/stock/600519")
    assert r2.status_code == 200
    stock = r2.json()
    assert stock["stock_code"] == "600519"
    assert stock["planes"]["moneyflow_dc"]["status"] == "ok"
    assert stock["sector_context"]["sector_code"] == "BK001"
    assert stock["behavior"]["behavior"] == "latent"

    # BJ rejected
    r3 = client.get("/api/v3/decision/moneyflow/stock/920001")
    assert r3.status_code == 404
