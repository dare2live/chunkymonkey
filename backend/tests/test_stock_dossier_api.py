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
        CREATE TABLE dim_active_a_stock (
            stock_code VARCHAR, stock_name VARCHAR, updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, holder_set VARCHAR,
            report_date VARCHAR, holder_rank INTEGER, holder_name VARCHAR,
            holder_name_norm VARCHAR, holder_type VARCHAR,
            hold_ratio_float DOUBLE, change_status VARCHAR,
            hold_change_num DOUBLE, notice_date VARCHAR,
            shares_approx DOUBLE, is_exit_row BOOLEAN,
            available_at TIMESTAMP, row_seq INTEGER
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
        ('600519', '20260721', '放量下跌', '中位放量下跌', '缩量下跌', '温和下跌',
         FALSE, 'mid', 'down', 'choppy', 'heavy', NULL,
         0.9, 0.3, 0.9, 0.8, NULL)
        """
    )
    con.execute(
        """
        INSERT INTO dim_active_a_stock VALUES
        ('600519', '贵州茅台', now())
        """
    )
    con.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
        ('600519', 'free', '20260331', 1, '机构甲', '机构甲', '基金',
         5.0, '增持', 1000, '20260425', 1e6, FALSE, TIMESTAMP '2026-04-25 18:00:00', 1),
        ('600519', 'free', '20251231', 1, '机构甲', '机构甲', '基金',
         4.5, '不变', 0, '20260120', 9e5, FALSE, TIMESTAMP '2026-01-20 18:00:00', 1),
        ('600519', 'free', '20260331', 2, '机构乙', '机构乙', 'QFII',
         2.0, '新进', NULL, '20260425', 4e5, FALSE, TIMESTAMP '2026-04-25 18:00:00', 1)
        """
    )
    con.execute(
        """
        INSERT INTO dim_stock_dc_industry VALUES
        ('600519', 'BK1', '白酒', 'BK2', '白酒Ⅱ', 'BK3', '白酒Ⅲ', now())
        """
    )
    return con


def _client(con, extra_overrides=None):
    app = FastAPI()
    app.include_router(dossier_api.router, prefix="/api/v3/stock")

    def _override():
        try:
            yield con
        finally:
            pass

    app.dependency_overrides[dossier_api.get_dossier_conn] = _override
    if extra_overrides:
        app.dependency_overrides.update(extra_overrides)
    return TestClient(app)


def test_dossier_mvp_layers_and_observation():
    con = _fixture_conn()
    client = _client(con)
    r = client.get("/api/v3/stock/600519/dossier")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["surface"] == "stock_dossier_cap_f_usable"
    assert body["usability"]["status"] == "usable"
    assert body["usability"]["tabs"]["holders"]["status"] == "ok"
    assert body["usability"]["tabs"]["moneyflow"]["status"] == "delegated"
    assert body["basic"]["stock_name"] == "贵州茅台"
    assert body["basic"]["industry"]["l3_name"] == "白酒Ⅲ"
    assert body["form_stage"]["form_name"] == "放量下跌"
    assert body["form_stage"]["axis_pos"] == "mid"
    assert body["observation"]["version"] == "stock_dossier_obs_v0"
    assert body["observation"]["text"]
    assert "放量下跌" in body["observation"]["text"]
    # Live axis vocabulary (trending/choppy, heavy/shrink/normal) — not clean/mixed/light.
    assert "结构嘈杂" in body["observation"]["text"]
    assert "放量" in body["observation"]["text"]
    assert body["form_stage"]["source"] in {
        "fact_stock_form_daily",
        "accepted_partition+fact_stock_form_daily",
    }
    assert body["holders"]["report_date"] == "20260331"
    assert len(body["holders"]["rows"]) == 2
    assert "holder_number" in body
    assert body["usability"]["tabs"]["holder_number"]["status"] in {"ok", "empty"}
    assert body["holder_number"]["status"] in {"ok", "empty"}
    row0 = body["holders"]["rows"][0]
    assert row0["change_status"] == "增持"
    assert row0["research_identity"]["holder_research_class"]["tags"] == []
    assert body["lhb_seats"]["gaps"] == ["lhb_seat_table_absent"]
    assert body["usability"]["tabs"]["lhb_seats"]["status"] == "empty"
    # No episode in fixture → per-row unknown, not MVP fog gaps.
    assert row0["return_pct"] is None
    assert "moneyflow_assist_not_in_mvp" not in body["gaps"]
    assert "holder_return_pct_unknown" not in body["gaps"]
    assert body["lineage"]["status"] == "attested_usable"


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
    """Formal-only sync: period streak must follow canonical tip."""
    con = _fixture_conn()
    con.execute("DELETE FROM canonical_top10_float_holders_period")
    con.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
        ('600519', 'free', '20260714', 1, '机构甲', '机构甲', '基金',
         5.2, '增持', 100, '20260721', 1.1e6, FALSE, TIMESTAMP '2026-07-21 14:00:00', 1),
        ('600519', 'free', '20260331', 1, '机构甲', '机构甲', '基金',
         5.0, '增持', 50, '20260425', 1.0e6, FALSE, TIMESTAMP '2026-04-25 14:00:00', 1)
        """
    )
    client = _client(con)
    body = client.get("/api/v3/stock/600519/dossier").json()
    assert body["holders"]["source"] == "canonical_top10_float_holders_period"
    assert body["holders"]["report_date"] == "20260714"
    assert body["holders"]["prev_report_date"] == "20260331"
    assert body["holders"]["rows"][0]["approx_periods_present"] == 2
    assert body["holders"]["source"] == "canonical_top10_float_holders_period"
    assert body["lineage"]["status"] == "attested_usable"
    assert body["lineage"]["stock_holder_assoc_readiness"] == "FIXED"


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
    assert rows["机构甲"]["institution_link_status"] == "profile"
    assert rows["机构乙"]["has_institution_profile"] is False
    assert rows["机构乙"]["institution_link_status"] == "none"
    prof = body["holders"]["institution_profile"]
    assert prof["holders_total"] == 2
    assert prof["holders_with_profile"] == 1
    assert prof["coverage"] == 0.5
    assert "institution_profile_absent_no_deep_link" in body["holders"]["gaps"]
    assert body["lineage"]["institution_profile_coverage"]["coverage"] == 0.5
    assert body["lineage"]["institution_join"] == "HONESTY_GATED"


def test_dossier_institution_link_status_rich_schema_and_episode_only():
    """Rich mart_inst_profile (low_sample/n_closed) + episode-only → typed statuses."""
    con = _fixture_conn()
    con.execute("ATTACH ':memory:' AS fs")
    con.execute(
        """
        CREATE TABLE fs.mart_inst_profile (
            holder VARCHAR, low_sample BOOLEAN, n_closed INTEGER
        )
        """
    )
    # 机构甲: profile exists but low_sample; 机构乙: no profile row.
    con.execute(
        "INSERT INTO fs.mart_inst_profile VALUES ('机构甲', TRUE, 3)"
    )
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
        ('机构乙','600519','20260331',NULL,'holding',0,0,NULL,NULL,FALSE,FALSE)
        """
    )
    client = _client(con)
    body = client.get("/api/v3/stock/600519/dossier").json()
    rows = {r["holder_name_norm"]: r for r in body["holders"]["rows"]}
    assert rows["机构甲"]["has_institution_profile"] is True
    assert rows["机构甲"]["institution_profile_low_sample"] is True
    assert rows["机构甲"]["institution_link_status"] == "profile_low_sample"
    assert rows["机构乙"]["has_institution_profile"] is False
    assert rows["机构乙"]["institution_link_status"] == "episode_only_no_profile"
    assert rows["机构乙"]["episode"] is not None
    prof = body["holders"]["institution_profile"]
    assert prof["holders_with_profile"] == 1
    assert prof["holders_episode_only"] == 1
    assert prof["holders_profile_low_sample"] == 1
    assert "institution_episode_without_profile_mart_row" in body["holders"]["gaps"]


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
    assert jia["holding_cycle_days"] == 365
    assert jia["holding_cycle_basis"] == "disclosure_open_to_close"
    assert rows["机构甲"]["return_pct"] == 0.12
    assert rows["机构甲"]["holding_cycle_days"] == 365
    yi = rows["机构乙"]["episode"]
    assert yi["status"] == "holding"
    # holding leg PnL must never be surfaced even if a stale ret_c1 exists
    assert yi["return_measured"] is False
    assert yi["ret_c1"] is None
    assert yi["alpha_c1"] is None
    assert rows["机构乙"]["return_pct"] is None
    assert yi["holding_cycle_basis"] == "disclosure_open_to_asof_holding"
    assert yi["holding_cycle_days"] is not None and yi["holding_cycle_days"] >= 0
    assert body["holders"]["episode_overlay"]["holders_with_episode"] == 2
    assert body["holders"]["episode_overlay"]["holders_return_measured"] == 1
    assert body["holders"]["episode_overlay"]["holders_cycle_known"] == 2


def test_dossier_lhb_seats_use_seat_research_class():
    con = _fixture_conn()
    con.execute(
        """
        CREATE TABLE fact_top_inst_seat_daily (
            trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR,
            net_buy DOUBLE, available_at TIMESTAMP, source_table VARCHAR, built_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        INSERT INTO fact_top_inst_seat_daily VALUES
        ('20260820', '600519.SH', '机构专用', '0', 50, now(), 'raw', now()),
        ('20260820', '600519.SH',
         '国盛证券有限责任公司宁波桑田路证券营业部', '0', 20, now(), 'raw', now())
        """
    )
    body = _client(con).get("/api/v3/stock/600519/dossier").json()
    assert body["usability"]["tabs"]["lhb_seats"]["status"] == "ok"
    rows = {r["exalter"]: r for r in body["lhb_seats"]["rows"]}
    assert rows["机构专用"]["seat_research_class"]["tags"] == ["inst_anonymous"]
    sangtian = rows["国盛证券有限责任公司宁波桑田路证券营业部"]
    assert sangtian["display_name"] == "章盟主"
    assert sangtian["alias_kind"] == "folk"


def _yield_conn(con):
    def _ov():
        try:
            yield con
        finally:
            pass

    return _ov


def test_holders_exited_not_mixed_into_top10():
    con = _fixture_conn()
    con.execute(
        """
        INSERT INTO canonical_top10_float_holders_period VALUES
        ('600519', 'free', '20260331', 9, '机构丙', '机构丙', '基金',
         1.1, '退出', -800, '20260425', 2e5, TRUE, TIMESTAMP '2026-04-25 18:00:00', 9)
        """
    )
    body = _client(con).get("/api/v3/stock/600519/dossier").json()
    names = [r["holder_name"] for r in body["holders"]["rows"]]
    exited = body["holders"]["exited"]
    assert "机构丙" not in names
    assert len(exited) == 1
    assert exited[0]["holder_name"] == "机构丙"
    assert body["holders"]["change_counts"]["退出"] == 1
    assert exited[0]["research_identity"]["holder_research_class"]["tags"] == []


def test_stock_list_identity_dim_and_unfiltered_facets():
    con = _fixture_conn()
    con.execute("CREATE SCHEMA IF NOT EXISTS ref")
    con.execute(
        "CREATE TABLE ref.dim_active_a_stock AS SELECT * FROM dim_active_a_stock"
    )
    con.execute(
        "INSERT INTO ref.dim_active_a_stock VALUES ('000001', '平安银行', now())"
    )
    client = _client(
        con,
        extra_overrides={dossier_api.get_list_conn: _yield_conn(con)},
    )
    all_rows = client.get("/api/v3/stock/list?limit=50").json()
    assert all_rows["status"] == "ok"
    assert all_rows["universe"] == "dim_active_a_stock"
    assert all_rows["total"] == 2
    ping = client.get("/api/v3/stock/list?q=平安").json()
    assert ping["total"] == 1
    assert ping["rows"][0]["stock_name"] == "平安银行"
    # facets are census of the form day, not scoped to q
    assert ping["facets"]["form_name"][0]["value"] == "放量下跌"
    assert ping["facets"]["form_name"][0]["count"] == 1


def test_kline_empty_is_typed_200():
    con = _fixture_conn()
    con.execute(
        """
        CREATE TABLE v_price_kline_qfq (
            code VARCHAR, date VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, volume DOUBLE, amount DOUBLE
        )
        """
    )
    client = _client(
        con,
        extra_overrides={dossier_api.get_kline_conn: _yield_conn(con)},
    )
    r = client.get("/api/v3/stock/600519/kline?days=20")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "empty"
    assert body["reason"] == "no_qfq_kline"
    assert body["rows"] == []
    assert body["adjust"] == "qfq"
    con.execute(
        "INSERT INTO v_price_kline_qfq VALUES "
        "('600519', '20260801', 1, 1.2, 0.9, 1.1, 100, 110),"
        "('600519', '20260802', 1.1, 1.3, 1.0, 1.2, 120, 130)"
    )
    body2 = client.get("/api/v3/stock/600519/kline?days=20").json()
    assert body2["status"] == "ok"
    assert body2["days"] == 2
    assert body2["rows"][0]["date"] == "20260801"
    assert body2["as_of"] == "20260802"

