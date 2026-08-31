"""fuyao meta-tickers-list capability: stock_basic candidate source.

Offline only — every REST call is monkeypatched, no network. This tests the
adapter *capability* added ahead of the stock_basic source cutover; it does
NOT touch sync_registry.yaml and does not assert anything about which source
stock_basic currently uses.
"""
from __future__ import annotations

import pytest

from services.data_sources.sources.fuyao import (
    META_TICKERS_LIST_PATH,
    TICKER_API_PATHS,
    TICKER_LIST_ASSET_TYPE,
    TICKER_LIST_PAGE_SIZE,
    FuyaoSource,
    normalize_ticker_rows,
    resolve_api_path,
    ticker_exchange_to_market,
)


def _item(thscode, ticker, name, exchange, asset_type="a-share"):
    return {
        "thscode": thscode,
        "ticker": ticker,
        "name": name,
        "exchange": exchange,
        "asset_type": asset_type,
        "currency": "CNY",
    }


# ── 1. api name / path registration ──────────────────────────────────────


def test_meta_tickers_list_api_registered() -> None:
    assert TICKER_API_PATHS["meta-tickers-list"] == META_TICKERS_LIST_PATH
    assert resolve_api_path("meta-tickers-list") == META_TICKERS_LIST_PATH
    assert resolve_api_path(META_TICKERS_LIST_PATH) == META_TICKERS_LIST_PATH


# ── 2. field mapping ──────────────────────────────────────────────────────


def test_field_mapping_thscode_ticker_name_exchange() -> None:
    rows = normalize_ticker_rows(
        [_item("600000.SH", "600000", "浦发银行", "SH")]
    )
    assert rows == [
        {
            "ts_code": "600000.SH",
            "symbol": "600000",
            "name": "浦发银行",
            "market": "SH",
        }
    ]


# ── 3. BJ -> 北交所, and downstream `market != '北交所'` keeps working ─────


def test_bj_exchange_maps_to_beijing_market_label() -> None:
    assert ticker_exchange_to_market("BJ") == "北交所"
    rows = normalize_ticker_rows(
        [_item("830799.BJ", "830799", "北交所股票", "BJ")]
    )
    assert rows[0]["market"] == "北交所"

    # Simulate the exact downstream predicate from
    # security_master.py:60-66 (`WHERE market != '北交所'`).
    survivors = [r for r in rows if r["market"] != "北交所"]
    assert survivors == []


def test_non_bj_market_is_never_the_beijing_label() -> None:
    for exchange in ("SH", "SZ", "sh", "sz", "", None):
        assert ticker_exchange_to_market(exchange) != "北交所"
    rows = normalize_ticker_rows(
        [
            _item("600000.SH", "600000", "浦发银行", "SH"),
            _item("000001.SZ", "000001", "平安银行", "SZ"),
        ]
    )
    assert all(r["market"] != "北交所" for r in rows)
    survivors = [r for r in rows if r["market"] != "北交所"]
    assert {r["ts_code"] for r in survivors} == {"600000.SH", "000001.SZ"}


# ── 4. asset_type filter ─────────────────────────────────────────────────


def test_non_a_share_asset_types_are_filtered_out() -> None:
    items = [
        _item("600000.SH", "600000", "浦发银行", "SH", asset_type="a-share"),
        _item("000300.SH", "000300", "沪深300", "SH", asset_type="a-share-index"),
        _item("510300.SH", "510300", "300ETF", "SH", asset_type="fund-etf"),
        _item("000001.SZ", "000001", "平安银行", "SZ", asset_type="a-share"),
    ]
    rows = normalize_ticker_rows(items)
    assert {r["ts_code"] for r in rows} == {"600000.SH", "000001.SZ"}
    assert TICKER_LIST_ASSET_TYPE == "a-share"


# ── 5. pagination: full, full, empty — exhaustive, no dup, no loss ────────


def test_pagination_pages_to_exhaustion_no_dup_no_loss(monkeypatch) -> None:
    import services.data_sources.sources.fuyao as fuyao_module

    monkeypatch.setattr(fuyao_module, "TICKER_LIST_PAGE_SIZE", 2)

    page1 = [
        _item("600000.SH", "600000", "浦发银行", "SH"),
        _item("000001.SZ", "000001", "平安银行", "SZ"),
    ]
    page2 = [
        _item("300750.SZ", "300750", "宁德时代", "SZ"),
        _item("830799.BJ", "830799", "北交所股票", "BJ"),
    ]
    page3: list[dict] = []
    pages = [page1, page2, page3]
    calls: list[dict] = []

    def rest(path, *, api_key, params, timeout=30.0):
        assert path == META_TICKERS_LIST_PATH
        assert api_key == "k"
        calls.append(dict(params))
        idx = len(calls) - 1
        items = pages[idx] if idx < len(pages) else []
        return {"item": items, "pagination": {}}

    src = FuyaoSource(rest=rest, api_key="k")
    rows = src.fetch_raw("meta-tickers-list")

    assert len(calls) == 3
    assert [c["offset"] for c in calls] == [0, 2, 4]
    assert all(c["limit"] == 2 for c in calls)
    assert all(c["asset_type"] == "a-share" for c in calls)
    assert all("page" not in c and "size" not in c for c in calls)

    got = [r["ts_code"] for r in rows]
    assert got == ["600000.SH", "000001.SZ", "300750.SZ", "830799.BJ"]
    assert len(got) == len(set(got))


# ── 6. unknown api -> KeyError listing known apis ─────────────────────────


def test_unknown_api_raises_key_error_listing_known_apis() -> None:
    with pytest.raises(KeyError) as excinfo:
        resolve_api_path("bogus-api")
    message = str(excinfo.value)
    assert "meta-tickers-list" in message
    assert "limit-up-pool" in message

    src = FuyaoSource(
        rest=lambda *a, **k: (_ for _ in ()).throw(AssertionError("rest")),
        api_key="k",
    )
    with pytest.raises(KeyError, match="meta-tickers-list"):
        src.fetch_raw("bogus-api")


# ── 7. zero-consumption tushare columns are absent, not fabricated ────────


def test_zero_consumption_columns_are_absent_not_fabricated() -> None:
    rows = normalize_ticker_rows(
        [_item("600000.SH", "600000", "浦发银行", "SH")]
    )
    zero_consumption_cols = {
        "area",
        "industry",
        "cnspell",
        "list_date",
        "act_name",
        "act_ent_type",
    }
    for row in rows:
        assert zero_consumption_cols.isdisjoint(row.keys())
        assert set(row.keys()) == {"ts_code", "symbol", "name", "market"}


# ── caller never controls pagination directly ──────────────────────────────


def test_caller_supplied_limit_offset_still_rejected() -> None:
    from services.data_sources.sources.fuyao import FuyaoPaginationError

    src = FuyaoSource(
        rest=lambda *a, **k: (_ for _ in ()).throw(AssertionError("rest")),
        api_key="k",
    )
    with pytest.raises(FuyaoPaginationError, match="page/size"):
        src.fetch_raw("meta-tickers-list", limit=500, offset=0)
