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


def test_dossier_rejects_b_share_and_bj_prefixes():
    con = _fixture_conn()
    client = _client(con)
    # B-share / BJ-like prefixes must not enter 沪深A dossier serve
    for code in ("900921", "920001"):
        r = client.get(f"/api/v3/stock/{code}/dossier")
        assert r.status_code == 404
        assert "沪深A" in r.json()["detail"]


def test_dossier_canonical_period_streak_not_fact_lag():
    """Formal-only sync: period streak must follow canonical, not stale fact."""
    con = _fixture_conn()
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR,
            holder_rank INTEGER, row_seq INTEGER, holder_name VARCHAR,
            holder_name_norm VARCHAR, holder_type VARCHAR,
            hold_ratio_float DOUBLE, change_status VARCHAR,
            hold_change_num DOUBLE, notice_date VARCHAR,
            shares_approx DOUBLE, is_exit_row BOOLEAN,
            available_at TIMESTAMPTZ
        )
        """
    )
    con.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
        ('600519', '20260714', 'free', 1, 1, '机构甲', '机构甲', '基金',
         5.2, '增持', 100, '20260721', 1.1e6, FALSE, TIMESTAMPTZ '2026-07-21 14:00:00+00'),
        ('600519', '20260331', 'free', 1, 1, '机构甲', '机构甲', '基金',
         5.0, '增持', 50, '20260425', 1.0e6, FALSE, TIMESTAMPTZ '2026-04-25 14:00:00+00')
        """
    )
    client = _client(con)
    body = client.get("/api/v3/stock/600519/dossier").json()
    assert body["holders"]["source"] == "canonical_top10_float_holders_period"
    assert body["holders"]["report_date"] == "20260714"
    assert body["holders"]["prev_report_date"] == "20260331"
    assert body["holders"]["rows"][0]["approx_periods_present"] == 2
    assert "legacy_fact_mirror_skipped_formal_only" in body["holders"]["gaps"]
    assert body["lineage"]["status"] == "attested_partial"


def test_dossier_institution_profile_honesty():
    """机构 deep-link only when a profile row exists; coverage stated honestly."""
    con = _fixture_conn()
    # feature_store attach: only 机构甲 has a profile, 机构乙 does not (~partial).
    con.execute("ATTACH ':memory:' AS fs")
    con.execute("CREATE TABLE fs.mart_inst_profile (holder VARCHAR)")
    con.execute("INSERT INTO fs.mart_inst_profile VALUES ('机构甲')")
    client = _client(con)
    body = client.get("/api/v3/stock/600519/dossier").json()
    rows = {r["holder_name_norm"]: r for r in body["holders"]["rows"]}
    assert rows["机构甲"]["has_institution_profile"] is True
    assert rows["机构乙"]["has_institution_profile"] is False
    prof = body["holders"]["institution_profile"]
    assert prof["holders_total"] == 2
    assert prof["holders_with_profile"] == 1
    assert prof["coverage"] == 0.5
    assert "institution_profile_partial_no_deep_link_when_absent" in body["holders"]["gaps"]
    assert body["lineage"]["institution_profile_coverage"]["coverage"] == 0.5


def test_dossier_episode_overlay_measured_only():
    """2F deepen: this-stock episode cycle/return; return measured only for closed."""
    con = _fixture_conn()
    con.execute("ATTACH ':memory:' AS fs")
    con.execute(
        """
        CREATE TABLE fs.fact_inst_episode (
            holder VARCHAR, stock VARCHAR, open_date VARCHAR, close_date VARCHAR,
            status VARCHAR, n_adds INTEGER, n_trims INTEGER,
            ret_c1 DOUBLE, alpha_c1 DOUBLE, seeded BOOLEAN, is_passive BOOLEAN
        )
        """
    )
    con.execute(
        """
        INSERT INTO fs.fact_inst_episode VALUES
        ('机构甲','600519','20240331','20250331','closed',2,1,0.30,0.12,FALSE,FALSE),
        ('机构乙','600519','20260331',NULL,'holding',0,0,0.99,0.99,FALSE,FALSE)
        """
    )
    client = _client(con)
    body = client.get("/api/v3/stock/600519/dossier").json()
    rows = {r["holder_name_norm"]: r for r in body["holders"]["rows"]}
    jia = rows["机构甲"]["episode"]
    assert jia["status"] == "closed"
    assert jia["return_measured"] is True
    assert jia["alpha_c1"] == 0.12
    yi = rows["机构乙"]["episode"]
    assert yi["status"] == "holding"
    # holding leg PnL must never be surfaced even if a stale ret_c1 exists
    assert yi["return_measured"] is False
    assert yi["ret_c1"] is None
    assert yi["alpha_c1"] is None
    assert body["holders"]["episode_overlay"]["holders_with_episode"] == 2
