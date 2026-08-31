"""Miaoxiang (妙想) adapter contracts for top_inst / top_list — offline only.

No live network, no sibling ``miaoxiang`` checkout required: every test injects
a fake client implementing ``get_v1`` (same signature as
``aif10_scraper.client.AIF10Client.get_v1``), matching this project's CI-shallow-
clone discipline (see ``feedback-test-must-carry-its-own-fixture`` lesson —
tests must not assume a host environment they don't carry with them).

Field-mapping fixtures below are lifted verbatim (key subset) from a real
``client.get_v1(..., extra_filters=["(TRADE_DATE='2026-08-25')"])`` response
captured 2026-08-31 (see module docstring in ``sources/miaoxiang.py`` for the
full mapping table and provenance).
"""
from __future__ import annotations

import pytest

from services.data_sources.sources.miaoxiang import (
    ALIAS,
    API_REPORT_NAMES,
    MAX_PAGES,
    PAGE_SIZE,
    REPORT_TOP_INST,
    REPORT_TOP_LIST,
    MiaoxiangMissingFieldError,
    MiaoxiangSource,
    MiaoxiangSourceError,
    MiaoxiangTruncationError,
    clean_top_inst_row,
    clean_top_list_row,
    compact_trade_date,
)


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
# api dispatch / caller-param rejection
# ---------------------------------------------------------------------------


def test_api_names_mirror_registry_values():
    assert set(API_REPORT_NAMES) == {"top_inst", "top_list"}
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


def test_top_inst_sort_columns_is_empty_per_registry_spec():
    client = _FakeClient([{"pages": 1, "count": 1, "data": [_top_inst_raw_row()]}])
    src = MiaoxiangSource(client=client)
    src.fetch_raw("top_inst", trade_date="20260825")
    call = client.calls[0]
    assert call["sort_columns"] == ""
    assert call["sort_types"] == "-1"


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
