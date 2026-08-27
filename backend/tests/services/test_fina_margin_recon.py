"""Finance + margin recon: landing orphans, exchange ruler, stock-sum ≠ identity."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from services.data_sources.fina_margin_recon import (
    BANNED_EXCHANGE_BASELINE,
    BANNED_GPCW,
    compare_income_sample,
    compare_margin_totals,
    compact_yyyymmdd,
    fina_publication_status,
    load_exchange_margin,
    load_landing_balancesheet,
    load_landing_fina_indicator,
    load_landing_income,
    load_margin_detail_sum,
    miaoxiang_payload_rows,
    parse_miaoxiang_finance_rows,
    reject_gpcw_revival,
    reject_report_end_as_pit,
)


def test_income_and_balancesheet_are_not_accepted_publication():
    income = fina_publication_status("income")
    assert income["status"] == "sync_orphan"
    assert income["baseline"] is None
    assert income["landing"] == "raw_tushare_income"
    balancesheet = fina_publication_status("balancesheet")
    assert balancesheet["status"] == "sync_orphan"
    assert balancesheet["landing"] == "raw_tushare_balancesheet"
    with pytest.raises(ValueError, match="gpcw"):
        reject_gpcw_revival("raw_gpcw_detail")
    assert "raw_gpcw_detail" in BANNED_GPCW


def test_report_end_is_not_pit():
    with pytest.raises(ValueError, match="not report-period end"):
        reject_report_end_as_pit("end_date")
    with pytest.raises(ValueError, match="not report-period end"):
        reject_report_end_as_pit("REPORT_DATE")
    reject_report_end_as_pit("f_ann_date")
    reject_report_end_as_pit("NOTICE_DATE")


def test_empty_margin_is_not_a_match():
    report = compare_margin_totals(None, None)
    assert report["status"] == "empty_recon"
    assert report["identity"] is False
    assert report["jaccard"] is None


def test_equal_margin_sums_are_still_not_identity():
    report = compare_margin_totals(100.0, 100.0)
    assert report["abs_diff"] == 0
    assert report["identity"] is False
    assert report["relation"] == "stock_sum_is_not_exchange_publication"
    assert report["grain_left"] == "exchange_sse_szse"
    assert report["grain_right"] == "stock_margin_detail"


def test_load_exchange_rejects_compat_and_detail():
    con = duck_mem()
    with pytest.raises(ValueError, match="canonical_margin"):
        load_exchange_margin(con, "20260825", table="raw_tushare_margin")
    with pytest.raises(ValueError, match="not exchange"):
        load_exchange_margin(con, "20260825", table="raw_tushare_margin_detail")
    for name in BANNED_EXCHANGE_BASELINE:
        with pytest.raises(ValueError):
            load_exchange_margin(con, "20260825", table=name)


def test_hyphen_canonical_date_matches_compact_detail_and_drops_bse():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE canonical_margin_exchange_daily (
            trade_date DATE, exchange_id VARCHAR, rzrqye DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO canonical_margin_exchange_daily VALUES
        (DATE '2026-08-25', 'SSE', 100),
        (DATE '2026-08-25', 'SZSE', 50),
        (DATE '2026-08-25', 'BSE', 9)
        """
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_margin_detail (
            trade_date VARCHAR, ts_code VARCHAR, rzrqye DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO raw_tushare_margin_detail VALUES
        ('20260825', '600000.SH', 80),
        ('20260825', '000001.SZ', 40),
        ('20260825', '920000.BJ', 7)
        """
    )
    exchange = load_exchange_margin(con, "20260825")
    assert exchange["status"] == "ok"
    assert exchange["rzrqye"] == 150
    assert exchange["exchanges"] == ["SSE", "SZSE"]
    assert exchange["excluded_bse_rzrqye"] == 9
    detail = load_margin_detail_sum(con, "20260825")
    assert detail["n"] == 3
    assert detail["rzrqye"] == 127
    assert detail["rzrqye_ex_bj"] == 120
    assert detail["bj_rzrqye"] == 7
    hyphen = load_exchange_margin(con, "2026-08-25")
    assert hyphen["rzrqye"] == 150
    empty = load_margin_detail_sum(con, "20260824")
    assert empty["status"] == "empty_recon"
    assert empty["n"] == 0


def test_matching_income_fields_are_still_not_identity():
    landing = [
        {
            "ts_code": "600519.SH",
            "end_date": "20251231",
            "ann_date": "20260417",
            "total_revenue": 172_054_171_890.91,
            "n_income_attr_p": 82_320_067_101.68,
        }
    ]
    mx = [
        {
            "SECUCODE": "600519.SH",
            "REPORT_DATE": "2025-12-31 00:00:00",
            "NOTICE_DATE": "2026-04-17",
            "TOTAL_OPERATE_INCOME": 172_054_171_890.91,
            "PARENT_NETPROFIT": 82_320_067_101.68,
        }
    ]
    report = compare_income_sample(landing, mx)
    assert report["status"] == "compared"
    assert report["periods"] == 1
    body = report["per_period"][0]
    assert body["fields"]["total_revenue"]["match"] is True
    assert body["fields"]["n_income_attr_p"]["match"] is True
    assert body["pit_left"] == "f_ann_date"
    assert body["pit_right"] == "NOTICE_DATE"
    assert compact_yyyymmdd(date(2025, 12, 31)) == "20251231"


def test_ratio_abs_tol_does_not_mask_yoy():
    from services.data_sources.fina_margin_recon import compare_numeric_fields

    yoy = compare_numeric_fields(6.538, 6.336)
    assert yoy["status"] == "divergent"
    assert yoy["match"] is False
    roe = compare_numeric_fields(10.5687, 10.57)
    assert roe["match"] is True
    money = compare_numeric_fields(54702912385.23, 54702912385.23)
    assert money["match"] is True


def test_scale_mismatch_is_not_a_quiet_match():
    landing = [
        {
            "ts_code": "600519.SH",
            "end_date": "20251231",
            "ann_date": "20260417",
            "total_revenue": 172_054_171_890.91,
            "n_income_attr_p": 82_320_067_101.68,
        }
    ]
    mx = [
        {
            "SECUCODE": "600519.SH",
            "REPORT_DATE": "20251231",
            "NOTICE_DATE": "20260417",
            "TOTAL_OPERATE_INCOME": 17_205_417.189091,
            "PARENT_NETPROFIT": 8_232_006.710168,
        }
    ]
    report = compare_income_sample(landing, mx)
    body = report["per_period"][0]
    assert body["fields"]["total_revenue"]["status"] == "scale_mismatch"
    assert body["fields"]["total_revenue"]["scale"] == 10000.0
    assert body["fields"]["total_revenue"]["match"] is False
    assert body["identity"] is False


def test_empty_statement_recon_is_not_a_match():
    report = compare_income_sample([], [])
    assert report["status"] == "empty_recon"
    assert report["identity"] is False
    assert report["periods"] == 0


def test_load_income_picks_update_flag_1_and_rejects_gpcw():
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE raw_tushare_income (
            ts_code VARCHAR, end_date VARCHAR, f_ann_date VARCHAR,
            update_flag VARCHAR, total_revenue DOUBLE, n_income_attr_p DOUBLE
        )
        """
    )
    con.execute(
        """
        INSERT INTO raw_tushare_income VALUES
        ('600519.SH', '20260331', '20260425', '0', 1, 1),
        ('600519.SH', '20260331', '20260425', '1', 54702912385.23, 27242512886.45)
        """
    )
    rows = load_landing_income(con, ["600519.SH"])
    assert len(rows) == 1
    assert rows[0]["total_revenue"] == pytest.approx(54702912385.23)
    assert rows[0]["ann_date"] == "20260425"
    with pytest.raises(ValueError, match="gpcw"):
        load_landing_income(con, ["600519.SH"], table="raw_gpcw_detail")


def test_miaoxiang_v0_reads_result_data_not_missing_top_level():
    rows = miaoxiang_payload_rows(
        {
            "success": True,
            "data": None,
            "result": {
                "count": 1,
                "data": [
                    {
                        "SECUCODE": "600519.SH",
                        "REPORT_DATE": "2025-12-31",
                        "TOTAL_OPERATE_INCOME": 1.0,
                    }
                ],
            },
        }
    )
    assert len(rows) == 1
    assert rows[0]["SECUCODE"] == "600519.SH"
    assert miaoxiang_payload_rows({"data": []}) == []


def test_parse_miaoxiang_and_load_balance_contract_liab():
    parsed = parse_miaoxiang_finance_rows(
        [
            {
                "SECUCODE": "600519.SH",
                "REPORT_DATE": "2026-03-31",
                "NOTICE_DATE": "2026-04-25",
                "CONTRACT_LIAB": 3_027_195_224.08,
                "TOTAL_ASSETS": 319_918_844_905.58,
                "TOTAL_LIABILITIES": 38_782_958_469.89,
            }
        ]
    )
    assert parsed[0]["ts_code"] == "600519.SH"
    assert parsed[0]["end_date"] == "20260331"
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE raw_tushare_balancesheet (
            ts_code VARCHAR, end_date VARCHAR, f_ann_date VARCHAR,
            update_flag VARCHAR, total_assets DOUBLE, total_liab DOUBLE,
            contract_liab VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO raw_tushare_balancesheet VALUES "
        "('600519.SH', '20260331', '20260425', '0', 319918844905.58, "
        "38782958469.89, '3027195224.08')"
    )
    rows = load_landing_balancesheet(con, ["600519.SH"])
    assert rows[0]["contract_liab"] == pytest.approx(3_027_195_224.08)
    con.execute(
        """
        CREATE TABLE raw_tushare_fina_indicator (
            ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR,
            update_flag VARCHAR, roe DOUBLE, or_yoy DOUBLE,
            grossprofit_margin DOUBLE, debt_to_assets DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO raw_tushare_fina_indicator VALUES "
        "('600519.SH', '20260331', '20260425', '1', 10.5687, 6.538, 89.7592, 12.1227)"
    )
    fina = load_landing_fina_indicator(con, ["600519.SH"])
    assert fina[0]["roe"] == pytest.approx(10.5687)
    assert fina[0]["ann_date"] == "20260425"
