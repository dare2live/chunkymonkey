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
    compare_sets,
    compare_valuation_snapshot,
    dim_to_ts_code,
    fuyao_dump_coverage,
    load_codes_for_day,
    load_dim_active_ts_codes,
    load_limit_up_codes,
    miaoxiang_block_keys,
    miaoxiang_seat_keys,
    normalize_cn_name,
    parse_fuyao_index_bars,
    parse_fuyao_tickers,
    product_mismatches,
    reject_banned_codeset_baseline,
    shanghai_day_from_ms,
    shanghai_midnight_ms,
)


def test_daily_fill_is_not_codeset_ruler():
    with pytest.raises(ValueError, match="raw_tushare_daily"):
        reject_banned_codeset_baseline("raw_tushare_daily")
    assert reject_banned_codeset_baseline("dim_active_a_stock") == "dim_active_a_stock"


def test_empty_sets_are_not_identity():
    report = compare_sets(
        [], [], grain="g", left_name="a", right_name="b", same_product=True
    )
    assert report["status"] == "empty_recon"
    assert report["identity"] is False
    assert report["primary_cut"] is False


def test_equal_codeset_is_identity_only_when_same_product():
    codes = ["600519.SH", "000001.SZ"]
    same = compare_sets(
        codes,
        codes,
        grain="listed_hs_a",
        left_name="dim",
        right_name="fuyao",
        same_product=True,
    )
    assert same["identity"] is True
    other = compare_sets(
        codes, codes, grain="g", left_name="sw", right_name="ths", same_product=False
    )
    assert other["identity"] is False
    extra = compare_sets(
        codes,
        codes + ["430047.BJ"],
        grain="listed_hs_a",
        left_name="dim",
        right_name="fuyao",
        same_product=True,
    )
    assert extra["identity"] is False
    assert extra["only_right"] == 1


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
    assert miaoxiang_seat_keys(
        [
            {
                "SECUCODE": "000017.SZ",
                "OPERATEDEPT_NAME": "东方证券杭州",
                "TRADE_DIRECTION": "0",
            }
        ]
    ) == [("000017.SZ", "东方证券杭州", "0")]
    assert normalize_cn_name("中信证券（山东）有限责任公司青岛分公司") == normalize_cn_name(
        "中信证券(山东)有限责任公司青岛分公司"
    )
    assert miaoxiang_block_keys(
        [
            {
                "SECUCODE": "600791.SH",
                "BUYER_NAME": "中信证券（山东）青岛",
                "SELLER_NAME": "中信证券（山东）青岛",
            }
        ]
    ) == [("600791.SH", "中信证券(山东)青岛", "中信证券(山东)青岛")]


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
