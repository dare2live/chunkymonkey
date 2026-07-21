"""Stock dossier API tests — fixture DB, no live DuckDB dependency."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import stock_dossier as dossier_api


def _fixture_conn():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE fact_stock_form_daily (
            stock_code VARCHAR, trade_date VARCHAR,
            form_name VARCHAR, form_sub VARCHAR,
            weekly_name VARCHAR, monthly_name VARCHAR,
            is_breakout_event BOOLEAN,
            axis_pos VARCHAR, axis_trend VARCHAR, axis_purity VARCHAR,
            axis_vol VARCHAR, axis_volregime VARCHAR,
            axis_pos_memb DOUBLE, axis_trend_memb DOUBLE,
            axis_purity_memb DOUBLE, axis_vol_memb DOUBLE,
            base_days INTEGER
        )
        """
    )
    con.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code VARCHAR, stock_name VARCHAR, holder_set VARCHAR,
            report_date VARCHAR, holder_rank INTEGER, holder_name VARCHAR,
            holder_name_norm VARCHAR, holder_type VARCHAR,
            hold_ratio_float DOUBLE, change_status VARCHAR,
            hold_change_num DOUBLE, notice_date VARCHAR,
            shares_approx DOUBLE, is_exit_row BOOLEAN
        )
        """
    )
    con.execute(
        """
        CREATE TABLE dim_stock_dc_industry (
            stock_code VARCHAR, tdx_l1 VARCHAR, tdx_l1_name VARCHAR,
            tdx_l2 VARCHAR, tdx_l2_name VARCHAR, tdx_l3 VARCHAR,
            tdx_l3_name VARCHAR, updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        INSERT INTO fact_stock_form_daily VALUES
        ('600519', '20260721', '放量下跌', '中位放量下跌', '震荡下行', '下跌通道',
         FALSE, 'mid', 'down', 'choppy', 'heavy', NULL,
         0.9, 0.3, 0.9, 0.8, NULL)
        """
    )
    con.execute(
        """
        INSERT INTO fact_top10_holder_period VALUES
        ('600519', '贵州茅台', 'free', '20260331', 1, '机构甲', '机构甲', '基金',
         5.0, '增持', 1000, '20260425', 1e6, FALSE),
        ('600519', '贵州茅台', 'free', '20251231', 1, '机构甲', '机构甲', '基金',
         4.5, '不变', 0, '20260120', 9e5, FALSE),
        ('600519', '贵州茅台', 'free', '20260331', 2, '机构乙', '机构乙', 'QFII',
         2.0, '新进', NULL, '20260425', 4e5, FALSE)
        """
    )
    con.execute(
        """
        INSERT INTO dim_stock_dc_industry VALUES
        ('600519', 'BK1', '白酒', 'BK2', '白酒Ⅱ', 'BK3', '白酒Ⅲ', now())
        """
    )
    return con


def _client(con):
    app = FastAPI()
    app.include_router(dossier_api.router, prefix="/api/v3/stock")

    def _override():
        try:
            yield con
        finally:
            pass

    app.dependency_overrides[dossier_api.get_dossier_conn] = _override
    return TestClient(app)


def test_dossier_mvp_layers_and_observation():
    con = _fixture_conn()
    client = _client(con)
    r = client.get("/api/v3/stock/600519/dossier")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["surface"] == "stock_dossier_mvp_partial"
    assert body["basic"]["stock_name"] == "贵州茅台"
    assert body["basic"]["industry"]["l3_name"] == "白酒Ⅲ"
    assert body["form_stage"]["form_name"] == "放量下跌"
    assert body["form_stage"]["axis_pos"] == "mid"
    assert body["observation"]["version"] == "stock_dossier_obs_v0"
    assert body["observation"]["text"]
    assert "放量下跌" in body["observation"]["text"]
    assert body["holders"]["report_date"] == "20260331"
    assert len(body["holders"]["rows"]) == 2
    row0 = body["holders"]["rows"][0]
    assert row0["change_status"] == "增持"
    assert row0["return_pct"] is None
    assert "holder_return_pct_unknown" in body["gaps"]
    assert "moneyflow_assist_not_in_mvp" in body["gaps"]


def test_dossier_bad_code_and_404():
    con = _fixture_conn()
    client = _client(con)
    assert client.get("/api/v3/stock/ABC/dossier").status_code == 400
    assert client.get("/api/v3/stock/999999/dossier").status_code == 404
