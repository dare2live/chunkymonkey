"""Assignment-gap recon: remaining measurable rows, no primary cut."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import duck_mem
from services.data_sources.assignment_gap_recon import (
    DAILY_BASIC_ABSENT_FROM_FUYAO_SNAPSHOT,
    compare_holdernumber_sample,
    compare_index_closes,
    compare_top_inst_vs_miaoxiang,
    compare_valuation_snapshot,
    dim_to_ts_code,
    fuyao_dump_coverage,
    load_block_trade_rows,
    load_codes_for_day,
    load_dim_active_ts_codes,
    load_limit_up_codes,
    load_top_inst_raw_rows,
    normalize_cn_name,
    parse_fuyao_index_bars,
    parse_fuyao_tickers,
    product_mismatches,
    reject_banned_codeset_baseline,
    shanghai_day_from_ms,
    shanghai_midnight_ms,
    top_inst_reland_status,
)
from services.data_sources.recon_compare import compare_rows
from services.data_sources.sources.miaoxiang import clean_block_trade_row


def test_daily_fill_is_not_codeset_ruler():
    with pytest.raises(ValueError, match="raw_tushare_daily"):
        reject_banned_codeset_baseline("raw_tushare_daily")
    assert reject_banned_codeset_baseline("dim_active_a_stock") == "dim_active_a_stock"


def test_dim_to_ts_code_and_load():
    assert dim_to_ts_code("1", "SZ") == "000001.SZ"
    assert dim_to_ts_code("600519", "SH") == "600519.SH"
    assert dim_to_ts_code("430047", "BJ") is None
    con = duck_mem()
    con.execute(
        "CREATE TABLE dim_active_a_stock (stock_code VARCHAR, market VARCHAR)"
    )
    con.execute(
        "INSERT INTO dim_active_a_stock VALUES ('000001','SZ'), ('600519','SH'), ('430047','BJ')"
    )
    assert sorted(load_dim_active_ts_codes(con)) == ["000001.SZ", "600519.SH"]


def test_product_mismatches_are_measured_not_guesses():
    holder = [
        r
        for r in product_mismatches()
        if r["challenger"] == "RPT_F10_SHAREHOLDER_CHANGE"
    ]
    assert holder[0]["identity"] is False
    assert "季度差分" in holder[0]["reason"]
    assert "turnover_rate_f" in DAILY_BASIC_ABSENT_FROM_FUYAO_SNAPSHOT
    dump = fuyao_dump_coverage()
    assert dump["has_daily_basic"] is False
    assert dump["has_moneyflow"] is False
    assert all(r["primary_cut"] is False for r in product_mismatches())


def test_valuation_near_is_still_not_identity():
    fuyao = [{"thscode": "600519.SH", "pe_ttm": 20.0, "pb_mrq": 8.0, "ps_ttm": 10.0}]
    basic = [
        {
            "ts_code": "600519.SH",
            "pe_ttm": 20.0,
            "pb": 8.0,
            "ps_ttm": 10.0,
            "turnover_rate_f": 0.3,
        }
    ]
    body = compare_valuation_snapshot(fuyao, basic)
    assert body["field_match_rows"] == 1
    assert body["identity"] is False
    assert "turnover_rate_f" in body["daily_basic_absent_from_fuyao"]


def test_holdernumber_exact_count_is_not_primary_cut():
    local = {
        "ts_code": "600519.SH",
        "ann_date": "20260815",
        "end_date": "20260630",
        "holder_num": 296404,
    }
    mx = {
        "ts_code": "600519.SH",
        "ann_date": "20260815",
        "end_date": "20260630",
        "holder_num": 296404,
    }
    body = compare_holdernumber_sample(local, mx)
    assert body["holder_num_exact"] is True
    assert body["end_date_match"] is True
    assert body["identity"] is False
    assert body["primary_cut"] is False


def test_index_close_match_can_be_identity_on_sample():
    acc = [{"trade_date": "20260825", "close": 4552.03}]
    fy = [{"trade_date": "20260825", "close": 4552.03}]
    body = compare_index_closes(acc, fy)
    assert body["identity"] is True
    assert body["primary_cut"] is False
    assert body["grain_source"]
    assert body["left_collapse"] == 0
    assert body["right_collapse"] == 0
    miss = compare_index_closes(acc, [{"trade_date": "20260825", "close": 1.0}])
    assert miss["identity"] is False


def test_shanghai_ms_and_fuyao_parsers():
    ms = int(datetime(2026, 8, 25, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    assert shanghai_day_from_ms(ms) == "20260825"
    assert shanghai_midnight_ms("20260825") == ms
    codes = parse_fuyao_tickers(
        {"item": [{"thscode": "600519.SH", "asset_type": "a-share"}]}
    )
    assert codes == ["600519.SH"]
    bars = parse_fuyao_index_bars(
        {"item": [{"date_ms": ms, "close_price": 4552.03}]},
        ts_code="000300.SH",
    )
    assert bars[0]["trade_date"] == "20260825"
    assert normalize_cn_name("中信证券（山东）有限责任公司青岛分公司") == normalize_cn_name(
        "中信证券(山东)有限责任公司青岛分公司"
    )


def test_miaoxiang_block_keys_removed_not_a_stub():
    # 2026-09-11 grain 契约 S7: 四键投影 (ts_code, buyer, seller) 已被
    # compare_rows(domain="block_trade") 取代 (见 V7) —— 旧函数删干净, 不留 stub。
    with pytest.raises(ImportError):
        from services.data_sources.assignment_gap_recon import (  # noqa: F401
            miaoxiang_block_keys,
        )
    with pytest.raises(ImportError):
        from services.data_sources.assignment_gap_recon import (  # noqa: F401
            load_block_keys,
        )


def test_limit_and_top_list_loaders():
    con = duck_mem()
    con.execute(
        'CREATE TABLE fact_stock_limit_daily (trade_date VARCHAR, ts_code VARCHAR, "limit" VARCHAR)'
    )
    con.execute(
        "INSERT INTO fact_stock_limit_daily VALUES ('20260825','000001.SZ','U'), ('20260825','000002.SZ','D')"
    )
    assert load_limit_up_codes(con, "20260825") == ["000001.SZ"]
    con.execute(
        "CREATE TABLE raw_tushare_top_list (trade_date VARCHAR, ts_code VARCHAR)"
    )
    con.execute("INSERT INTO raw_tushare_top_list VALUES ('20260825','002445.SZ')")
    assert load_codes_for_day(
        con, "raw_tushare_top_list", "20260825", date_col="trade_date"
    ) == ["002445.SZ"]


# ---------------------------------------------------------------------------
# grain 契约 S7 (2026-09-11): recon_assignment_gaps.py 的 block_trade / top_inst
# 段改法 — 见 backend/scripts/reland_event_domain.py 头注与
# assignment_gap_recon.compare_top_inst_vs_miaoxiang / load_block_trade_rows 的
# docstring。用例编号 V7-V9 承接 grain 契约 r2 §4 S7 表 (V1-V6 在
# test_reland_event_domain.py — 那三个测的是 reland_event_domain.py 自己的函数,
# 这三个测的是本模块 + recon_assignment_gaps.py 实际用的对账函数)。
# ---------------------------------------------------------------------------


def _a1_block_trade_raw_row(**overrides):
    """S4 §1.2 的 A1 字面例子 (300308.SZ EQA, 与 test_miaoxiang_adapter.py 同源)。"""
    row = {
        "SECUCODE": "300308.SZ",
        "SECURITY_TYPE": "EQA",
        "TRADE_UNIT": "4",
        "TRADE_DATE": "2026-08-27 00:00:00",
        "DEAL_PRICE": 866.12,
        "DEAL_VOLUME": 14800,
        "DEAL_AMT": 12818600,
        "BUYER_NAME": "机构专用",
        "SELLER_NAME": "机构专用",
    }
    row.update(overrides)
    return row


def test_v7_block_trade_recon_compares_local_raw_rows_against_miaoxiang_mapping():
    con = duck_mem()
    con.execute(
        "CREATE TABLE raw_tushare_block_trade ("
        "ts_code VARCHAR, trade_date VARCHAR, price DOUBLE, vol DOUBLE, "
        "buyer VARCHAR, seller VARCHAR, seq INTEGER)"
    )
    # 本地: 妙想真的把同一笔全同六键的行返回了两次 (event 域合法形态), 落地时
    # 派生 seq 1/2。
    con.execute(
        "INSERT INTO raw_tushare_block_trade VALUES "
        "('300308.SZ','20260827',866.12,1.48,'机构专用','机构专用',1), "
        "('300308.SZ','20260827',866.12,1.48,'机构专用','机构专用',2)"
    )
    local_rows = load_block_trade_rows(con, "20260827", "raw_tushare_block_trade")
    assert len(local_rows) == 2
    assert "seq" not in local_rows[0]

    # 右侧: fake 妙想行 (A1 原始行 ×2), 不过 sync_runner._prepare_batch_df —— 直接是
    # clean_block_trade_row 的映射行。
    right_rows = [
        clean_block_trade_row(_a1_block_trade_raw_row(), trade_date="20260827"),
        clean_block_trade_row(_a1_block_trade_raw_row(), trade_date="20260827"),
    ]

    out = compare_rows(
        domain="block_trade",
        left_rows=local_rows,
        right_rows=right_rows,
        left_name="raw_tushare_block_trade",
        right_name="RPT_DATA_BLOCKTRADE",
    )
    assert out["identity"] is True
    assert out["right_collapse"] == 1
    assert out["matched"] == 2
    assert out["only_left"] == 0
    assert out["only_right"] == 0


def test_v8_compare_top_inst_vs_miaoxiang_not_comparable_without_board_rank_column():
    con = duck_mem()
    con.execute(
        "CREATE TABLE raw_tushare_top_inst ("
        "trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR, reason VARCHAR)"
    )
    con.execute(
        "INSERT INTO raw_tushare_top_inst VALUES ('20190102','600000.SH','甲','0','R1')"
    )
    out = compare_top_inst_vs_miaoxiang(con, "20190102", [])
    assert out == {
        "status": "not_comparable_pre_reland",
        "reason": top_inst_reland_status(con),
    }
    assert "board_rank" in out["reason"]
    assert "identity" not in out


def test_v8b_compare_top_inst_vs_miaoxiang_not_comparable_when_board_rank_all_null():
    con = duck_mem()
    con.execute(
        "CREATE TABLE raw_tushare_top_inst ("
        "trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR, "
        "reason VARCHAR, board_rank INTEGER)"
    )
    con.execute(
        "INSERT INTO raw_tushare_top_inst VALUES "
        "('20190102','600000.SH','甲','0','R1', NULL)"
    )
    out = compare_top_inst_vs_miaoxiang(con, "20190102", [])
    assert out["status"] == "not_comparable_pre_reland"
    assert "NULL" in out["reason"]
    assert "identity" not in out


def test_v9_compare_top_inst_vs_miaoxiang_identity_true_once_relanded():
    con = duck_mem()
    con.execute(
        "CREATE TABLE raw_tushare_top_inst ("
        "trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR, "
        "reason VARCHAR, board_rank INTEGER)"
    )
    con.execute(
        "INSERT INTO raw_tushare_top_inst VALUES "
        "('20190102','600000.SH','甲','0','R1', 1)"
    )
    miaoxiang_rows = [
        {
            "trade_date": "20190102",
            "ts_code": "600000.SH",
            "reason": "R1",
            "side": "0",
            "board_rank": 1,
        }
    ]
    out = compare_top_inst_vs_miaoxiang(con, "20190102", miaoxiang_rows)
    assert out["status"] == "compared"
    assert out["identity"] is True
    assert out["matched"] == 1


def test_load_top_inst_raw_rows_shape():
    con = duck_mem()
    con.execute(
        "CREATE TABLE raw_tushare_top_inst ("
        "trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, side VARCHAR, "
        "reason VARCHAR, board_rank INTEGER)"
    )
    con.execute(
        "INSERT INTO raw_tushare_top_inst VALUES "
        "('20190102','600000.SH','甲','0','R1', 3)"
    )
    rows = load_top_inst_raw_rows(con, "20190102")
    assert rows == [
        {
            "trade_date": "20190102",
            "ts_code": "600000.SH",
            "reason": "R1",
            "side": "0",
            "board_rank": 3,
        }
    ]
