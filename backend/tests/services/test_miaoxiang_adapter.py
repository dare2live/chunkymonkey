"""Miaoxiang (妙想) adapter contracts for block_trade / top_inst / top_list — offline only.

No live network, no sibling ``miaoxiang`` checkout required: every test injects
a fake client implementing ``get_v1`` (same signature as
``aif10_scraper.client.AIF10Client.get_v1``), matching this project's CI-shallow-
clone discipline (see ``feedback-test-must-carry-its-own-fixture`` lesson —
tests must not assume a host environment they don't carry with them).

Field-mapping fixtures below are lifted verbatim (key subset) from real
``client.get_v1(...)`` responses captured 2026-08-31 / 2026-08-27 (see module
docstring in ``sources/miaoxiang.py`` for the full mapping table and provenance).
"""
from __future__ import annotations

import logging

import pytest

from services.data_sources.pagination_integrity import assess_paginated_land
from services.data_sources.sources.miaoxiang import (
    ALIAS,
    API_REPORT_NAMES,
    MAX_PAGES,
    PAGE_SIZE,
    REPORT_BLOCK_TRADE,
    REPORT_TOP_INST,
    REPORT_TOP_LIST,
    MiaoxiangBadNumberError,
    MiaoxiangMissingFieldError,
    MiaoxiangSource,
    MiaoxiangSourceError,
    MiaoxiangTruncationError,
    MiaoxiangUnknownUnitError,
    _float,
    clean_block_trade_row,
    clean_top_inst_row,
    clean_top_list_row,
    compact_trade_date,
)
from services.data_sources.vendor_scope import VendorExclusions, VendorScopeError


# ---------------------------------------------------------------------------
# fixtures — real-shaped vendor rows (captured 2026-08-25 / 000017.SZ)
# ---------------------------------------------------------------------------


def _top_inst_raw_row(**overrides) -> dict:
    row = {
        "TRADE_ID": "100401198",
        "OPERATEDEPT_NAME": "东方证券股份有限公司杭州龙井路证券营业部",
        "TRADE_DATE": "2026-08-25 00:00:00",
        "RANK": 1,
        "TRADE_DIRECTION": "0",
        "OPERATEDEPT_CODE": "10086482",
        "BUY_AMT_REAL": 34959741,
        "SELL_AMT_REAL": 0,
        "SECURITY_CODE": "000017",
        "SECURITY_NAME_ABBR": "深中华A",
        "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "BUY_RATIO": 6.170038473246,
        "SELL_RATIO": 0,
        "SECUCODE": "000017.SZ",
        "NET": 34959741,
        "NET_BUY": -416771.400000006,  # stock-day-level net; must NOT be used for net_buy
    }
    row.update(overrides)
    return row


def _top_list_raw_row(**overrides) -> dict:
    row = {
        "TRADE_DATE": "2026-08-25 00:00:00",
        "DEAL_AMOUNT_RATIO": 39.697193764704,
        "BILLBOARD_DEAL_AMT": 224926249.4,
        "FREE_MARKET_CAP": 4185965919.98,
        "SECUCODE": "000017.SZ",
        "SECURITY_CODE": "000017",
        "CLOSE_PRICE": 8.6,
        "CHANGE_RATE": 9.9744,
        "TURNOVERRATE": 13.1245,
        "SECURITY_NAME_ABBR": "深中华A",
        "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "BILLBOARD_SELL_AMT": 112671510.4,
        "BILLBOARD_BUY_AMT": 112254739,
        "BILLBOARD_NET_AMT": -416771.400000006,
        "DEAL_NET_RATIO": -0.073555910284,
        "ACCUM_AMOUNT": 566604911,
    }
    row.update(overrides)
    return row


def _block_trade_raw_row(**overrides) -> dict:
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


class _FakeClient:
    """Records every call; serves canned per-page responses for one report."""

    def __init__(self, pages: list[dict]):
        self._pages = list(pages)
        self.calls: list[dict] = []

    def get_v1(self, report_name, **kwargs):
        self.calls.append({"report_name": report_name, **kwargs})
        idx = len(self.calls) - 1
        if idx >= len(self._pages):
            return {"pages": len(self._pages), "data": [], "count": 0}
        return self._pages[idx]


class _ExplodingClient:
    def get_v1(self, *_a, **_k):
        raise AssertionError("client.get_v1 must not be called")


# ---------------------------------------------------------------------------
# field mapping
# ---------------------------------------------------------------------------


def test_clean_top_inst_row_maps_all_fields():
    out = clean_top_inst_row(_top_inst_raw_row(), trade_date="20260825")
    assert out == {
        "trade_date": "20260825",
        "ts_code": "000017.SZ",
        "exalter": "东方证券股份有限公司杭州龙井路证券营业部",
        "side": "0",
        "buy": 34959741.0,
        "buy_rate": 6.170038473246,
        "sell": 0.0,
        "sell_rate": 0.0,
        "net_buy": 34959741.0,  # from NET, not NET_BUY (stock-day net)
        "reason": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "board_rank": 1,
        "stat_days": None,
        "seat_code": "10086482",
    }
    assert "built_at" not in out  # sync_runner stamps this centrally


def test_clean_top_inst_row_sell_side():
    row = _top_inst_raw_row(
        TRADE_DIRECTION="1",
        BUY_AMT_REAL=0,
        SELL_AMT_REAL=10363860,
        NET=-10363860,
        OPERATEDEPT_NAME="东莞证券股份有限公司东莞南城分公司",
    )
    out = clean_top_inst_row(row, trade_date="20260825")
    assert out["side"] == "1"
    assert out["buy"] == 0.0
    assert out["sell"] == 10363860.0
    assert out["net_buy"] == -10363860.0


def test_clean_top_list_row_maps_all_fields():
    out = clean_top_list_row(_top_list_raw_row(), trade_date="20260825")
    assert out == {
        "trade_date": "20260825",
        "ts_code": "000017.SZ",
        "name": "深中华A",
        "close": 8.6,
        "pct_change": 9.9744,
        "turnover_rate": 13.1245,
        "amount": 566604911.0,
        "l_sell": 112671510.4,
        "l_buy": 112254739.0,
        "l_amount": 224926249.4,
        "net_amount": -416771.400000006,
        "net_rate": -0.073555910284,
        "amount_rate": 39.697193764704,
        "float_values": 4185965919.98,
        "reason": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
    }
    assert "built_at" not in out


def test_clean_top_inst_row_missing_grain_field_fails_closed():
    row = _top_inst_raw_row()
    del row["OPERATEDEPT_NAME"]
    with pytest.raises(MiaoxiangMissingFieldError, match="OPERATEDEPT_NAME"):
        clean_top_inst_row(row, trade_date="20260825")


def test_clean_top_inst_row_bad_side_value_fails_closed():
    row = _top_inst_raw_row(TRADE_DIRECTION="2")
    with pytest.raises(MiaoxiangMissingFieldError, match="side"):
        clean_top_inst_row(row, trade_date="20260825")


def test_clean_top_list_row_missing_secucode_fails_closed():
    row = _top_list_raw_row()
    del row["SECUCODE"]
    with pytest.raises(MiaoxiangMissingFieldError):
        clean_top_list_row(row, trade_date="20260825")


def test_top_list_missing_explanation_none():
    row = _top_list_raw_row(EXPLANATION=None)
    with pytest.raises(MiaoxiangMissingFieldError):
        clean_top_list_row(row, trade_date="20260825")


def test_top_list_missing_explanation_empty():
    row = _top_list_raw_row(EXPLANATION="")
    with pytest.raises(MiaoxiangMissingFieldError):
        clean_top_list_row(row, trade_date="20260825")


# ---------------------------------------------------------------------------
# _float — fail-closed numeric parsing
# ---------------------------------------------------------------------------


def test_float_none():
    assert _float(None) is None


def test_float_empty():
    assert _float("") is None


def test_float_numeric_string():
    assert _float("12.5") == 12.5


def test_float_int():
    assert _float(12) == 12.0


def test_float_garbage():
    with pytest.raises(MiaoxiangBadNumberError):
        _float("--")


def test_float_nan():
    with pytest.raises(MiaoxiangBadNumberError):
        _float("nan")


def test_float_inf():
    with pytest.raises(MiaoxiangBadNumberError):
        _float("inf")


def test_float_bool():
    with pytest.raises(MiaoxiangBadNumberError):
        _float(True)


def test_clean_top_inst_row_normalizes_null_amount_to_zero():
    """Real-world regression (found via live 20260825 full-day reconciliation,
    2026-08-31): a one-sided seat (only buys, never sells, or vice versa) comes
    back from the vendor with the *other* side's BUY_AMT_REAL/SELL_AMT_REAL/
    BUY_RATIO/SELL_RATIO as JSON null — hit 130/650 rows (20%) on 20260825.
    tushare represents the identical "no activity on this side" case as 0.0,
    not NULL/None. Left as None, SUM(net_buy) style aggregations downstream
    (market_pulse.py) would silently be at risk of NULL propagation."""
    row = _top_inst_raw_row(
        TRADE_DIRECTION="1",
        BUY_AMT_REAL=None,
        BUY_RATIO=None,
        SELL_AMT_REAL=5343810,
        SELL_RATIO=2.075663579525,
        NET=-5343810,
    )
    out = clean_top_inst_row(row, trade_date="20260825")
    assert out["buy"] == 0.0
    assert out["buy_rate"] == 0.0
    assert out["sell"] == 5343810.0
    assert out["net_buy"] == -5343810.0


# ---------------------------------------------------------------------------
# block_trade field mapping
# ---------------------------------------------------------------------------


def test_clean_block_trade_row_eqa():
    """A1: EQA type with standard fields."""
    out = clean_block_trade_row(_block_trade_raw_row(), trade_date="20260827")
    assert out == {
        "ts_code": "300308.SZ",
        "trade_date": "20260827",
        "price": 866.12,
        "vol": pytest.approx(1.48),
        "amount": pytest.approx(1281.86),
        "buyer": "机构专用",
        "seller": "机构专用",
        "security_type": "EQA",
        "trade_unit": "4",
        "vendor_market": None,
    }
    assert "seq" not in out


def test_clean_block_trade_row_bd0():
    """A2: BD0 (convertible bond) with special vol calculation."""
    out = clean_block_trade_row(
        _block_trade_raw_row(
            SECUCODE="123076.SZ",
            SECURITY_TYPE="BD0",
            TRADE_UNIT="1",
            DEAL_PRICE=138.9,
            DEAL_VOLUME=500,
            DEAL_AMT=694500,
        ),
        trade_date="20260827",
    )
    assert out["ts_code"] == "123076.SZ"
    assert out["security_type"] == "BD0"
    assert out["vol"] == pytest.approx(0.5)  # 500*10/1e4
    assert out["amount"] == pytest.approx(69.45)  # 694500/1e4


def test_clean_block_trade_row_fdo():
    """A3: FDO (fund) type."""
    out = clean_block_trade_row(
        _block_trade_raw_row(
            SECUCODE="511990.SH",
            SECURITY_TYPE="FDO",
            TRADE_UNIT="3",
            DEAL_PRICE=100.0,
            DEAL_VOLUME=3000000,
            DEAL_AMT=299994000,
        ),
        trade_date="20260827",
    )
    assert out["ts_code"] == "511990.SH"
    assert out["security_type"] == "FDO"
    assert out["vol"] == pytest.approx(300.0)  # 3000000/1e4
    assert out["amount"] == pytest.approx(29999.4)  # 299994000/1e4


def test_clean_block_trade_row_unknown_security_type():
    """A4 (2026-09-11 revised): an unrecognized (SECURITY_TYPE, TRADE_UNIT) pair
    fails closed instead of silently landing vol=None on a grain column."""
    with pytest.raises(MiaoxiangUnknownUnitError):
        clean_block_trade_row(
            _block_trade_raw_row(
                SECURITY_TYPE="XYZ",
                DEAL_VOLUME=100,
                DEAL_AMT=1000,
            ),
            trade_date="20260827",
        )


# ---------------------------------------------------------------------------
# block_trade — (SECURITY_TYPE, TRADE_UNIT) volume-factor table
# ---------------------------------------------------------------------------


def test_block_trade_factor_eqa_4():
    out = clean_block_trade_row(
        _block_trade_raw_row(SECURITY_TYPE="EQA", TRADE_UNIT="4", DEAL_VOLUME=131476),
        trade_date="20260827",
    )
    assert out["vol"] == pytest.approx(13.1476)


def test_block_trade_factor_eqa_3_reits():
    out = clean_block_trade_row(
        _block_trade_raw_row(SECURITY_TYPE="EQA", TRADE_UNIT="3", DEAL_VOLUME=2411000),
        trade_date="20260827",
    )
    assert out["vol"] == pytest.approx(241.1)


def test_block_trade_factor_fdo_3():
    out = clean_block_trade_row(
        _block_trade_raw_row(SECURITY_TYPE="FDO", TRADE_UNIT="3", DEAL_VOLUME=1000000),
        trade_date="20260827",
    )
    assert out["vol"] == pytest.approx(100.0)


def test_block_trade_factor_bd0_1():
    out = clean_block_trade_row(
        _block_trade_raw_row(SECURITY_TYPE="BD0", TRADE_UNIT="1", DEAL_VOLUME=5000),
        trade_date="20260827",
    )
    assert out["vol"] == pytest.approx(5.0)


def test_block_trade_unknown_pair_type_known_unit_unknown():
    """A recognized SECURITY_TYPE (EQA) paired with a TRADE_UNIT not in the
    table for it ('1' is only registered for BD0) must still fail closed."""
    with pytest.raises(MiaoxiangUnknownUnitError):
        clean_block_trade_row(
            _block_trade_raw_row(SECURITY_TYPE="EQA", TRADE_UNIT="1"),
            trade_date="20260827",
        )


def test_block_trade_unknown_pair_type_unknown():
    with pytest.raises(MiaoxiangUnknownUnitError):
        clean_block_trade_row(
            _block_trade_raw_row(SECURITY_TYPE="XYZ", TRADE_UNIT="4"),
            trade_date="20260827",
        )


def test_block_trade_unknown_pair_eqb_3():
    """EQB is only registered with TRADE_UNIT '4' — '3' is not a known pair."""
    with pytest.raises(MiaoxiangUnknownUnitError):
        clean_block_trade_row(
            _block_trade_raw_row(SECURITY_TYPE="EQB", TRADE_UNIT="3"),
            trade_date="20260827",
        )


def test_block_trade_missing_trade_unit_none():
    """A missing TRADE_UNIT must fail the required-field check itself, not
    merely raise *some* MiaoxiangMissingFieldError subclass — MiaoxiangUnknownUnitError
    is also a MiaoxiangMissingFieldError, and would equally fire if TRADE_UNIT
    ever silently fell through the required-field gate into the (SECURITY_TYPE,
    TRADE_UNIT) lookup as ''."""
    row = _block_trade_raw_row(TRADE_UNIT=None)
    with pytest.raises(MiaoxiangMissingFieldError) as excinfo:
        clean_block_trade_row(row, trade_date="20260827")
    assert "TRADE_UNIT" in str(excinfo.value)
    assert not isinstance(excinfo.value, MiaoxiangUnknownUnitError)
    assert not isinstance(excinfo.value, MiaoxiangBadNumberError)


def test_block_trade_missing_trade_unit_empty():
    """Same as above for an empty-string TRADE_UNIT."""
    row = _block_trade_raw_row(TRADE_UNIT="")
    with pytest.raises(MiaoxiangMissingFieldError) as excinfo:
        clean_block_trade_row(row, trade_date="20260827")
    assert "TRADE_UNIT" in str(excinfo.value)
    assert not isinstance(excinfo.value, MiaoxiangUnknownUnitError)
    assert not isinstance(excinfo.value, MiaoxiangBadNumberError)


def test_block_trade_price_garbage_raises():
    """Wiring check: DEAL_PRICE going through the now-strict _float raises
    MiaoxiangBadNumberError, with every other field left legitimate."""
    row = _block_trade_raw_row(DEAL_PRICE="--")
    with pytest.raises(MiaoxiangBadNumberError):
        clean_block_trade_row(row, trade_date="20260827")


def test_clean_block_trade_row_missing_buyer():
    """A5: Missing buyer name fails closed."""
    row = _block_trade_raw_row(BUYER_NAME="")
    with pytest.raises(MiaoxiangMissingFieldError, match="BUYER_NAME"):
        clean_block_trade_row(row, trade_date="20260827")


def test_clean_block_trade_row_mismatched_date():
    """A6: Row date differs from request date fails closed."""
    row = _block_trade_raw_row(TRADE_DATE="2026-08-26 00:00:00")
    with pytest.raises(MiaoxiangMissingFieldError, match="row date.*request date"):
        clean_block_trade_row(row, trade_date="20260827")


def test_clean_block_trade_fetch_raw_integration():
    """A7: fetch_raw integration for block_trade via _FakeClient."""
    page1 = {
        "pages": 1,
        "count": 1,
        "data": [_block_trade_raw_row()],
    }
    client = _FakeClient([page1])
    src = MiaoxiangSource(client=client)
    rows = src.fetch_raw("block_trade", trade_date="20260827")

    assert len(rows) == 1
    assert rows[0]["ts_code"] == "300308.SZ"
    assert rows[0]["vol"] == pytest.approx(1.48)

    call = client.calls[0]
    assert call["report_name"] == REPORT_BLOCK_TRADE
    assert call["sort_columns"] == "SECURITY_CODE,DEAL_PRICE,DEAL_VOLUME,BUYER_NAME,SELLER_NAME"
    assert call["sort_types"] == "1,1,1,1,1"
    assert call["extra_filters"] == ["(TRADE_DATE='2026-08-27')"]


def test_block_trade_vendor_market_present():
    """A1: vendor_market field present with valid exchange code (CNSESH)."""
    row = _block_trade_raw_row(TRADE_MARKET_OLD="CNSESH")
    out = clean_block_trade_row(row, trade_date="20260827")
    assert out["vendor_market"] == "CNSESH"


def test_block_trade_vendor_market_missing():
    """A2: vendor_market field absent from row -> None, no exception."""
    row = _block_trade_raw_row()
    if "TRADE_MARKET_OLD" in row:
        del row["TRADE_MARKET_OLD"]
    out = clean_block_trade_row(row, trade_date="20260827")
    assert out["vendor_market"] is None


def test_block_trade_vendor_market_unknown_value():
    """A3: vendor_market field with unknown exchange code (CNSEXX) -> passthrough, no exception."""
    row = _block_trade_raw_row(TRADE_MARKET_OLD="CNSEXX")
    out = clean_block_trade_row(row, trade_date="20260827")
    assert out["vendor_market"] == "CNSEXX"


def test_block_trade_vendor_market_empty_string():
    """A4: vendor_market field with empty string -> None (per _text behavior)."""
    row = _block_trade_raw_row(TRADE_MARKET_OLD="")
    out = clean_block_trade_row(row, trade_date="20260827")
    assert out["vendor_market"] is None


def test_clean_top_inst_row_with_new_columns():
    """A8: top_inst now includes board_rank, stat_days, reason, and seat_code columns."""
    row = _top_inst_raw_row(STATISTICS_DAYS="2")
    out = clean_top_inst_row(row, trade_date="20260825")
    assert out["board_rank"] == 1
    assert out["stat_days"] == "2"
    assert out["reason"] == "连续三个交易日内，涨幅偏离值累计达到20%的证券"
    # seat_code tests
    assert clean_top_inst_row(_top_inst_raw_row(OPERATEDEPT_NAME="自然人", OPERATEDEPT_CODE="10000128629"), trade_date="20260825")["seat_code"] == "10000128629"
    assert clean_top_inst_row(_top_inst_raw_row(OPERATEDEPT_CODE=None), trade_date="20260825")["seat_code"] is None


def test_clean_top_inst_row_rank_variations():
    """A9: RANK must be int and >= 1."""
    # Missing RANK
    row = _top_inst_raw_row()
    del row["RANK"]
    with pytest.raises(MiaoxiangMissingFieldError, match="RANK"):
        clean_top_inst_row(row, trade_date="20260825")

    # Non-integer RANK
    with pytest.raises(MiaoxiangMissingFieldError, match="not a valid integer"):
        clean_top_inst_row(_top_inst_raw_row(RANK="x"), trade_date="20260825")

    # RANK < 1
    with pytest.raises(MiaoxiangMissingFieldError, match="must be >= 1"):
        clean_top_inst_row(_top_inst_raw_row(RANK=0), trade_date="20260825")


def test_top_inst_sort_columns_use_natural_key_order():
    """A10: top_inst now uses natural key order for sorting."""
    client = _FakeClient([{"pages": 1, "count": 1, "data": [_top_inst_raw_row()]}])
    src = MiaoxiangSource(client=client)
    src.fetch_raw("top_inst", trade_date="20260825")
    call = client.calls[0]
    assert call["sort_columns"] == "SECUCODE,EXPLANATION,TRADE_DIRECTION,RANK"
    assert call["sort_types"] == "1,1,1,1"


# ---------------------------------------------------------------------------
# api dispatch / caller-param rejection
# ---------------------------------------------------------------------------


def test_api_names_mirror_registry_values():
    """A11: API names include block_trade."""
    assert set(API_REPORT_NAMES) == {"block_trade", "top_inst", "top_list"}
    assert API_REPORT_NAMES["block_trade"] == REPORT_BLOCK_TRADE
    assert API_REPORT_NAMES["top_inst"] == REPORT_TOP_INST
    assert API_REPORT_NAMES["top_list"] == REPORT_TOP_LIST


def test_unknown_api_fails_closed():
    src = MiaoxiangSource(client=_ExplodingClient())
    with pytest.raises(MiaoxiangSourceError, match="unknown api"):
        src.fetch_raw("not_a_real_domain", trade_date="20260825")


@pytest.mark.parametrize("bad_kwarg", ["limit", "offset", "page", "page_size"])
def test_caller_paging_kwargs_are_rejected(bad_kwarg):
    src = MiaoxiangSource(client=_ExplodingClient())
    with pytest.raises(MiaoxiangSourceError, match="internal"):
        src.fetch_raw("top_inst", trade_date="20260825", **{bad_kwarg: 1})


def test_missing_trade_date_fails_closed():
    src = MiaoxiangSource(client=_ExplodingClient())
    with pytest.raises(MiaoxiangSourceError, match="trade_date"):
        src.fetch_raw("top_inst")


def test_bad_trade_date_fails_closed():
    src = MiaoxiangSource(client=_ExplodingClient())
    with pytest.raises(MiaoxiangSourceError, match="trade_date"):
        src.fetch_raw("top_inst", trade_date="20260231")  # not a real day


def test_trade_date_accepts_dashed_and_compact():
    assert compact_trade_date("2026-08-25") == "20260825"
    assert compact_trade_date("20260825") == "20260825"
    assert compact_trade_date("2026-08-25 00:00:00") == "20260825"


# ---------------------------------------------------------------------------
# pagination — single page
# ---------------------------------------------------------------------------


def test_single_page_fetch_returns_mapped_rows_and_stamps_dashed_filter():
    page1 = {
        "pages": 1,
        "count": 1,
        "data": [_top_inst_raw_row()],
    }
    client = _FakeClient([page1])
    src = MiaoxiangSource(client=client)
    rows = src.fetch_raw("top_inst", trade_date="20260825")

    assert len(rows) == 1
    assert rows[0]["ts_code"] == "000017.SZ"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["report_name"] == REPORT_TOP_INST
    assert call["page"] == 1
    assert call["page_size"] == PAGE_SIZE
    assert call["extra_filters"] == ["(TRADE_DATE='2026-08-25')"]
    assert call["secucode"] is None
    assert call["columns"] == "ALL"


def test_top_list_uses_its_own_sort_columns():
    client = _FakeClient([{"pages": 1, "count": 1, "data": [_top_list_raw_row()]}])
    src = MiaoxiangSource(client=client)
    src.fetch_raw("top_list", trade_date="20260825")
    call = client.calls[0]
    assert call["report_name"] == REPORT_TOP_LIST
    assert call["sort_columns"] == "SECURITY_CODE,TRADE_DATE"
    assert call["sort_types"] == "1,-1"


# ---------------------------------------------------------------------------
# pagination — multi page
# ---------------------------------------------------------------------------


def test_multi_page_fetch_accumulates_all_rows_in_page_order():
    rows_p1 = [_top_inst_raw_row(OPERATEDEPT_NAME=f"dept-{i}") for i in range(2)]
    rows_p2 = [_top_inst_raw_row(OPERATEDEPT_NAME=f"dept-{i}") for i in range(2, 3)]
    client = _FakeClient(
        [
            {"pages": 2, "count": 3, "data": rows_p1},
            {"pages": 2, "count": 3, "data": rows_p2},
        ]
    )
    src = MiaoxiangSource(client=client)
    rows = src.fetch_raw("top_inst", trade_date="20260825")

    assert len(rows) == 3
    assert [r["exalter"] for r in rows] == ["dept-0", "dept-1", "dept-2"]
    assert [c["page"] for c in client.calls] == [1, 2]


def test_pagination_stops_on_empty_page_even_if_pages_field_lies():
    """A page returning no data must stop the loop regardless of `pages`."""
    client = _FakeClient(
        [
            {"pages": 5, "count": 1, "data": [_top_inst_raw_row()]},
            {"pages": 5, "count": 1, "data": []},
        ]
    )
    src = MiaoxiangSource(client=client)
    rows = src.fetch_raw("top_inst", trade_date="20260825")
    assert len(rows) == 1
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# empty result — legitimate, not a failure
# ---------------------------------------------------------------------------


def test_empty_day_returns_empty_list_without_error():
    """e.g. a day with zero institutional-seat top_inst rows (allow_empty_batch
    in the registry today) — an empty result is real signal, not a failure."""
    client = _FakeClient([{"pages": 0, "count": 0, "data": []}])
    src = MiaoxiangSource(client=client)
    rows = src.fetch_raw("top_inst", trade_date="20260825")
    assert rows == []


# ---------------------------------------------------------------------------
# fail-closed: truncation / runaway pagination
# ---------------------------------------------------------------------------


def test_truncated_landing_raises_instead_of_returning_partial_rows():
    """Vendor declares count=2000 but reports pages=1 (landed only 100) — a
    silent-truncation shape this adapter must not swallow."""
    client = _FakeClient(
        [{"pages": 1, "count": 2000, "data": [_top_inst_raw_row() for _ in range(100)]}]
    )
    src = MiaoxiangSource(client=client)
    with pytest.raises(MiaoxiangTruncationError, match="truncated"):
        src.fetch_raw("top_inst", trade_date="20260825")


def test_runaway_pagination_hits_max_pages_and_fails_closed():
    """`pages` never catches up to the current page and data never empties —
    must not spin forever; must fail loud, not return a partial silent list."""

    class _NeverEndingClient:
        def __init__(self):
            self.calls = 0

        def get_v1(self, report_name, **kwargs):
            self.calls += 1
            return {"pages": 999, "count": 999999, "data": [_top_inst_raw_row()]}

    client = _NeverEndingClient()
    src = MiaoxiangSource(client=client)
    with pytest.raises(MiaoxiangTruncationError, match="exceeded"):
        src.fetch_raw("top_inst", trade_date="20260825")
    assert client.calls == MAX_PAGES


# ---------------------------------------------------------------------------
# fail-closed: missing/zero provider count on a non-empty land
# ---------------------------------------------------------------------------


def test_fetch_report_day_rows_with_zero_count_raises():
    """assess_paginated_land's `expected_count > 0` gate is a no-op when count
    is 0/missing (pagination_integrity.py:40-49) — a dedicated check must catch
    a non-empty land riding on an untrustworthy zero count."""
    client = _FakeClient([{"pages": 1, "count": 0, "data": [_top_inst_raw_row()]}])
    src = MiaoxiangSource(client=client)
    with pytest.raises(MiaoxiangTruncationError):
        src._fetch_report_day(
            REPORT_TOP_INST, "20260825", sort_columns="SECUCODE", sort_types="1"
        )


def test_fetch_report_day_empty_zero_count_ok():
    """A genuinely empty day (0 rows, count 0) is not truncation."""
    client = _FakeClient([{"pages": 0, "count": 0, "data": []}])
    src = MiaoxiangSource(client=client)
    rows = src._fetch_report_day(
        REPORT_TOP_INST, "20260825", sort_columns="SECUCODE", sort_types="1"
    )
    assert rows == []


def test_fetch_report_day_count_matches_ok():
    row = _top_inst_raw_row()
    client = _FakeClient([{"pages": 1, "count": 1, "data": [row]}])
    src = MiaoxiangSource(client=client)
    rows = src._fetch_report_day(
        REPORT_TOP_INST, "20260825", sort_columns="SECUCODE", sort_types="1"
    )
    assert rows == [row]


# ---------------------------------------------------------------------------
# dependency injection
# ---------------------------------------------------------------------------


def test_injected_client_bypasses_factory_entirely():
    factory_calls = []

    def factory():
        factory_calls.append(1)
        raise AssertionError("factory must not be invoked when client is given")

    client = _FakeClient([{"pages": 1, "count": 1, "data": [_top_inst_raw_row()]}])
    src = MiaoxiangSource(client=client, client_factory=factory)
    src.fetch_raw("top_inst", trade_date="20260825")
    assert factory_calls == []


def test_client_factory_used_lazily_when_no_client_given():
    built = []

    def factory():
        fake = _FakeClient([{"pages": 1, "count": 1, "data": [_top_inst_raw_row()]}])
        built.append(fake)
        return fake

    src = MiaoxiangSource(client_factory=factory)
    assert built == []  # not constructed at __init__ time
    rows = src.fetch_raw("top_inst", trade_date="20260825")
    assert len(built) == 1
    assert len(rows) == 1
    # second call reuses the same lazily-built client (no re-factory call)
    src.fetch_raw("top_inst", trade_date="20260825")
    assert len(built) == 1


def test_alias_is_miaoxiang():
    assert ALIAS == "miaoxiang"


# ---------------------------------------------------------------------------
# vendor_scope wiring — B股/EQB 排除 (owner ruling 2026-09-12). The v2
# vendor_scope.yaml shape, its L1-L12 loader rules, and vendor_exclusions()
# itself are tested in test_vendor_scope.py; this file only tests that
# MiaoxiangSource.fetch_raw wires that API in correctly (接线测试).
# ---------------------------------------------------------------------------


def test_unregistered_api_disposition_fails_closed(monkeypatch):
    """An api that passes the known-report-name check but has no vendor_scope
    disposition must not be treated as "no exclusions" — it must fail closed.
    Mutation target: removing the try/except VendorScopeError wrapping in
    fetch_raw makes this raise VendorScopeError instead of MiaoxiangSourceError
    (or not raise at all if the lookup were skipped), so this test alone
    catches that regression."""

    def _raise_unregistered(*_args, **_kwargs):
        raise VendorScopeError("no disposition registered for 'miaoxiang.top_inst'")

    monkeypatch.setattr(
        "services.data_sources.sources.miaoxiang.vendor_exclusions",
        _raise_unregistered,
    )
    src = MiaoxiangSource(client=_ExplodingClient())
    with pytest.raises(MiaoxiangSourceError, match="no vendor_scope disposition"):
        src.fetch_raw("top_inst", trade_date="20260825")


def test_block_trade_fetch_excludes_eqb_before_clean(caplog):
    eqa_row = _block_trade_raw_row()
    eqb_row = _block_trade_raw_row(SECUCODE="900926.SH", SECURITY_TYPE="EQB", TRADE_UNIT="4")
    client = _FakeClient([{"pages": 1, "count": 2, "data": [eqa_row, eqb_row]}])
    src = MiaoxiangSource(client=client)

    with caplog.at_level(logging.INFO, logger="services.data_sources.sources.miaoxiang"):
        rows = src.fetch_raw("block_trade", trade_date="20260827")

    assert len(rows) == 1
    assert rows[0]["security_type"] == "EQA"
    assert "excluded 1 rows" in caplog.text


def test_block_trade_fetch_eqb_not_excluded_raises(monkeypatch):
    """If vendor_scope stops excluding EQB, the EQB row falls through to
    clean_block_trade_row and hits the now-unregistered (EQB, '4') pair —
    MiaoxiangUnknownUnitError, not a silent NULL vol."""
    monkeypatch.setattr(
        "services.data_sources.sources.miaoxiang.vendor_exclusions",
        lambda *a, **k: VendorExclusions(request_filters=(), response_excludes=()),
    )
    eqb_row = _block_trade_raw_row(SECUCODE="900926.SH", SECURITY_TYPE="EQB", TRADE_UNIT="4")
    client = _FakeClient([{"pages": 1, "count": 1, "data": [eqb_row]}])
    src = MiaoxiangSource(client=client)
    with pytest.raises(MiaoxiangUnknownUnitError):
        src.fetch_raw("block_trade", trade_date="20260827")


def test_block_trade_fetch_exclusion_keeps_truncation_check():
    """Truncation must be judged against the rows landed *before* vendor_scope
    exclusion, not after.

    Page 1 (of 1) declares count=1000 and lands exactly 1000 rows in one go —
    600 EQB + 400 EQA, every row individually valid. Judged against the
    pre-exclusion land (1000 landed == 1000 expected), this is not truncated
    at all. Only if vendor_scope exclusion were wrongly moved *before* the
    truncation check — so the check saw just the 400 surviving EQA rows
    against a still-1000 expected count — would ``assess_paginated_land`` see
    400<1000 and fail closed with ``MiaoxiangTruncationError`` (see the
    companion fact in ``test_truncation_tolerance_flags_post_exclusion_count``
    below: 400 landed against 1000 expected *is* judged truncated on its own,
    so this test would go red under that ordering bug)."""
    eqb_rows = [
        _block_trade_raw_row(SECUCODE="900926.SH", SECURITY_TYPE="EQB", TRADE_UNIT="4")
        for _ in range(600)
    ]
    eqa_rows = [_block_trade_raw_row() for _ in range(400)]
    client = _FakeClient([{"pages": 1, "count": 1000, "data": eqb_rows + eqa_rows}])
    src = MiaoxiangSource(client=client)

    rows = src.fetch_raw("block_trade", trade_date="20260827")

    assert len(rows) == 400
    assert all(r["security_type"] == "EQA" for r in rows)


def test_truncation_tolerance_flags_post_exclusion_count():
    """Standalone fact the ordering test above depends on: judged in
    isolation, 400 landed rows against a declared count of 1000 *is*
    truncated under the pagination-integrity tolerance (500-row floor is
    well below the 600-row shortfall here). This is what makes the previous
    test able to tell the two orderings apart — if vendor_scope exclusion
    ran before the truncation check instead of after it, that test's landed
    count would drop to exactly this 400-vs-1000 shape and raise
    MiaoxiangTruncationError."""
    verdict = assess_paginated_land(
        expected_count=1000,
        landed_rows=400,
        page_size=PAGE_SIZE,
    )
    assert verdict.truncated is True
