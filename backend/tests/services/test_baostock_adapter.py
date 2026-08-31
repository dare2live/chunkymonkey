"""Baostock adapter contracts. Offline only: baostock is a raw-TCP binary protocol,
never installed/imported for real here — every test injects a fake ``bs_module``.
"""
from __future__ import annotations

import datetime
import socket
import threading

import pytest

from services.data_sources import sync_runner as sr
from services.data_sources.sources.baostock import (
    ACCOUNT_PERMISSION,
    API_FUNCTION_NAMES,
    BSERR_BLACKLIST_USER,
    BSERR_CONNECT_TIMEOUT,
    BSERR_NO_LOGIN,
    BSERR_PARAM_ERR,
    BSERR_PARSE_DATA_ERR,
    BSERR_SUCCESS,
    CALLER_ERROR,
    CLIENT_PARSE,
    TRANSIENT_NETWORK,
    BaostockConcurrencyError,
    BaostockIntegrityError,
    BaostockQueryError,
    BaostockSessionError,
    BaostockSource,
    _drain_rows,
    _login_with_bounded_timeout,
    classify_baostock_failure,
)


class _FakeResult:
    """Minimal stand-in for baostock's ResultData."""

    def __init__(self, pages, *, fields, error_code=BSERR_SUCCESS, error_msg=""):
        # pages: list of "pages", each a list of raw rows (list-of-str).
        # Rows are pre-flattened here; error_code/error_msg reflect the FINAL
        # state after all pages are drained (mirrors how the real client keeps
        # error_code from the last successful page unless a later fetch fails).
        self.fields = list(fields)
        self._pages = [list(p) for p in pages]
        self._page_idx = 0
        self.data = self._pages[0] if self._pages else []
        self.cur_row_num = 0
        self.error_code = BSERR_SUCCESS  # first-call success, like the real client
        self.error_msg = ""
        self._final_error_code = error_code
        self._final_error_msg = error_msg

    def next(self):
        if self.cur_row_num < len(self.data):
            return True
        self._page_idx += 1
        if self._page_idx < len(self._pages):
            self.data = self._pages[self._page_idx]
            self.cur_row_num = 0
            if not self.data:
                return False
            return True
        # No more pages: apply the terminal error_code/error_msg (may or may
        # not equal '0' depending on the scenario under test).
        self.error_code = self._final_error_code
        self.error_msg = self._final_error_msg
        return False

    def get_row_data(self):
        row = self.data[self.cur_row_num]
        self.cur_row_num += 1
        return row


class _FakeBaostock:
    """Fake ``baostock`` module: records call order, never touches a socket."""

    def __init__(self):
        self.calls: list[str] = []
        self.login_error_code = BSERR_SUCCESS
        self.query_results: dict[str, list[_FakeResult]] = {}
        self._query_call_idx: dict[str, int] = {}
        # 记录每个 api 最近一次实际收到的调用参数 (不连网, 靠这个断言 adapter 有没有
        # 悄悄改/补参数 —— 例如 query_trade_dates 的 end_date 默认值逻辑)。
        self.last_params: dict[str, dict] = {}

    def login(self):
        self.calls.append("login")
        result = _FakeResult([[]], fields=[])
        result.error_code = self.login_error_code
        result.data = []
        return result

    def logout(self):
        self.calls.append("logout")
        result = _FakeResult([[]], fields=[])
        result.error_code = BSERR_SUCCESS
        return result

    def queue_result(self, fn_name: str, result: _FakeResult) -> None:
        self.query_results.setdefault(fn_name, []).append(result)

    def _make_query(self, fn_name: str):
        def _query(**params):
            self.calls.append(fn_name)
            self.last_params[fn_name] = dict(params)
            idx = self._query_call_idx.get(fn_name, 0)
            results = self.query_results[fn_name]
            result = results[min(idx, len(results) - 1)]
            self._query_call_idx[fn_name] = idx + 1
            return result

        return _query

    def __getattr__(self, name):
        if name in API_FUNCTION_NAMES.values():
            return self._make_query(name)
        raise AttributeError(name)


# ---------------------------------------------------------------------------
# api name mapping table
# ---------------------------------------------------------------------------


def test_api_mapping_hits_all_required_apis() -> None:
    required = {
        "query_trade_dates",
        "query_history_k_data_plus",
        "query_stock_basic",
        "query_adjust_factor",
        "query_dividend_data",
        "query_stock_industry",
        "query_all_stock",
        "query_daily_history_k_AStock",
        "query_daily_adjust_factor",
    }
    assert required <= set(API_FUNCTION_NAMES)


def test_unknown_api_name_fails_closed() -> None:
    src = BaostockSource(bs_module=_FakeBaostock())
    with pytest.raises(KeyError, match="unknown api"):
        src.fetch_raw("query_definitely_not_real")


# ---------------------------------------------------------------------------
# socket.setdefaulttimeout around login, restored after
# ---------------------------------------------------------------------------


def test_login_sets_and_restores_socket_timeout(monkeypatch) -> None:
    sentinel_previous = object()
    monkeypatch.setattr(socket, "getdefaulttimeout", lambda: sentinel_previous)
    set_calls: list = []
    monkeypatch.setattr(socket, "setdefaulttimeout", lambda v: set_calls.append(v))

    bs = _FakeBaostock()
    result = _login_with_bounded_timeout(bs, timeout_seconds=15)

    assert result.error_code == BSERR_SUCCESS
    assert bs.calls == ["login"]
    # set(15) happens before login, then set(previous) happens after — in order.
    assert set_calls == [15.0, sentinel_previous]


def test_login_restores_timeout_even_on_failure(monkeypatch) -> None:
    sentinel_previous = object()
    monkeypatch.setattr(socket, "getdefaulttimeout", lambda: sentinel_previous)
    set_calls: list = []
    monkeypatch.setattr(socket, "setdefaulttimeout", lambda v: set_calls.append(v))

    class _ExplodingBs:
        def login(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _login_with_bounded_timeout(_ExplodingBs(), timeout_seconds=15)

    assert set_calls == [15.0, sentinel_previous]


def test_login_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        _login_with_bounded_timeout(_FakeBaostock(), timeout_seconds=0)
    with pytest.raises(ValueError):
        _login_with_bounded_timeout(_FakeBaostock(), timeout_seconds=-5)


# ---------------------------------------------------------------------------
# manual row draining (never rs.get_data())
# ---------------------------------------------------------------------------


def test_drain_rows_assembles_dicts_from_fields() -> None:
    result = _FakeResult(
        [[["600000.SH", "2020-07-01", "10.0"], ["000001.SZ", "2020-07-01", "20.0"]]],
        fields=["code", "date", "close"],
    )
    rows = _drain_rows(result)
    assert rows == [
        {"code": "600000.SH", "date": "2020-07-01", "close": "10.0"},
        {"code": "000001.SZ", "date": "2020-07-01", "close": "20.0"},
    ]


def test_drain_rows_paginates_across_pages() -> None:
    result = _FakeResult(
        [
            [["a"], ["b"]],
            [["c"]],
        ],
        fields=["code"],
    )
    rows = _drain_rows(result)
    assert [r["code"] for r in rows] == ["a", "b", "c"]


def test_drain_rows_never_calls_get_data() -> None:
    """Guards against regressions that reintroduce rs.get_data() (pandas>=2.0 crash)."""

    class _NoGetData(_FakeResult):
        def get_data(self):  # pragma: no cover - must never be invoked
            raise AssertionError("get_data() must never be called (pandas>=2.0 crash)")

    result = _NoGetData([[["x"]]], fields=["code"])
    rows = _drain_rows(result)
    assert rows == [{"code": "x"}]


def test_drain_rows_raises_when_final_error_code_not_success() -> None:
    result = _FakeResult(
        [[["a"]]],
        fields=["code"],
        error_code=BSERR_PARSE_DATA_ERR,
        error_msg="boom",
    )
    with pytest.raises(BaostockIntegrityError, match="error_code"):
        _drain_rows(result)


def test_drain_rows_raises_on_expected_count_mismatch() -> None:
    """This is the only way to catch next()'s silent truncation (error_code stays '0')."""
    result = _FakeResult([[["a"], ["b"]]], fields=["code"])
    with pytest.raises(BaostockIntegrityError, match="mismatch"):
        _drain_rows(result, expected_row_count=5)
    # Matching count passes cleanly.
    result2 = _FakeResult([[["a"], ["b"]]], fields=["code"])
    rows = _drain_rows(result2, expected_row_count=2)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# error classification
# ---------------------------------------------------------------------------


def test_classify_by_bare_code_string() -> None:
    assert classify_baostock_failure(BSERR_PARAM_ERR) == CALLER_ERROR
    assert classify_baostock_failure(BSERR_CONNECT_TIMEOUT) == TRANSIENT_NETWORK
    assert classify_baostock_failure(BSERR_PARSE_DATA_ERR) == CLIENT_PARSE
    assert classify_baostock_failure(BSERR_NO_LOGIN) == ACCOUNT_PERMISSION


def test_classify_blacklist_user_is_account_not_retryable_network() -> None:
    result = classify_baostock_failure(BSERR_BLACKLIST_USER)
    assert result == ACCOUNT_PERMISSION
    assert result != TRANSIENT_NETWORK


def test_classify_by_exception_with_code_attribute() -> None:
    exc = BaostockQueryError("boom", code=BSERR_BLACKLIST_USER)
    assert classify_baostock_failure(exc) == ACCOUNT_PERMISSION


def test_classify_unknown_exception_defaults_to_client_parse_not_retryable() -> None:
    assert classify_baostock_failure(ValueError("weird")) == CLIENT_PARSE


def test_classify_stdlib_network_exceptions_are_transient() -> None:
    assert classify_baostock_failure(TimeoutError("t")) == TRANSIENT_NETWORK
    assert classify_baostock_failure(ConnectionError("c")) == TRANSIENT_NETWORK
    assert classify_baostock_failure(OSError("o")) == TRANSIENT_NETWORK


# ---------------------------------------------------------------------------
# end-to-end fetch_raw against the fake module
# ---------------------------------------------------------------------------


def test_fetch_raw_logs_in_once_and_reuses_session() -> None:
    """query_trade_dates 输出经字段归一化 (2026-08-30 trade_cal 授权换源新增):
    baostock 原始两列 (calendar_date/is_trading_day) 转成 tushare trade_cal 形态
    (exchange/cal_date 紧凑8位/is_open int/pretrade_date) —— 见
    sources/baostock.py _normalize_trade_dates_rows。每次调用只在"本次拿到的行"里
    线性扫描前一个开市日, 两次调用各自只有一行, 故 pretrade_date 均为 None。"""
    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([[["2020-07-01", "1"]]], fields=["calendar_date", "is_trading_day"]),
    )
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([[["2020-07-02", "1"]]], fields=["calendar_date", "is_trading_day"]),
    )
    src = BaostockSource(bs_module=bs)
    rows1 = src.fetch_raw("query_trade_dates", start_date="2020-07-01")
    rows2 = src.fetch_raw("query_trade_dates", start_date="2020-07-02")
    assert rows1 == [
        {"exchange": "SSE", "cal_date": "20200701", "is_open": 1, "pretrade_date": None}
    ]
    assert rows2 == [
        {"exchange": "SSE", "cal_date": "20200702", "is_open": 1, "pretrade_date": None}
    ]
    assert bs.calls.count("login") == 1  # not re-logging in per fetch


def test_fetch_raw_relogs_in_on_session_dropped_signal() -> None:
    bs = _FakeBaostock()
    dropped = _FakeResult([[]], fields=[])
    dropped.error_code = BSERR_NO_LOGIN
    dropped.data = []
    recovered = _FakeResult([[["a"]]], fields=["code"])
    bs.queue_result("query_all_stock", dropped)
    bs.queue_result("query_all_stock", recovered)
    src = BaostockSource(bs_module=bs)
    rows = src.fetch_raw("query_all_stock", day="2020-07-01")
    assert rows == [{"code": "a"}]
    assert bs.calls.count("login") == 2  # initial + relogin after session-dropped signal
    assert bs.calls.count("query_all_stock") == 2


def test_fetch_raw_raises_on_query_error_code() -> None:
    bs = _FakeBaostock()
    bad = _FakeResult([[]], fields=[])
    bad.error_code = BSERR_PARAM_ERR
    bad.data = []
    bs.queue_result("query_stock_basic", bad)
    src = BaostockSource(bs_module=bs)
    with pytest.raises(BaostockQueryError, match=BSERR_PARAM_ERR):
        src.fetch_raw("query_stock_basic", code="600000")


def test_fetch_raw_login_failure_raises_session_error() -> None:
    bs = _FakeBaostock()
    bs.login_error_code = BSERR_BLACKLIST_USER
    src = BaostockSource(bs_module=bs)
    with pytest.raises(BaostockSessionError, match=BSERR_BLACKLIST_USER):
        src.fetch_raw("query_all_stock", day="2020-07-01")


def test_fetch_raw_import_error_points_to_requirements(monkeypatch) -> None:
    """No package installed: message must point at requirements.txt, not a raw ImportError."""
    from services.data_sources.sources import baostock as baostock_module

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "baostock":
            raise ImportError("no module named baostock")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    src = BaostockSource()
    with pytest.raises(baostock_module.BaostockImportError, match="requirements.txt"):
        src.fetch_raw("query_all_stock", day="2020-07-01")


# ---------------------------------------------------------------------------
# cross-thread rejection
# ---------------------------------------------------------------------------


def test_cross_thread_call_is_rejected() -> None:
    bs = _FakeBaostock()
    bs.queue_result("query_all_stock", _FakeResult([[["a"]]], fields=["code"]))
    src = BaostockSource(bs_module=bs)
    src.fetch_raw("query_all_stock", day="2020-07-01")  # binds owner thread

    errors: list[BaseException] = []

    def _call_from_other_thread():
        try:
            src.fetch_raw("query_all_stock", day="2020-07-02")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_call_from_other_thread)
    t.start()
    t.join()

    assert len(errors) == 1
    assert isinstance(errors[0], BaostockConcurrencyError)


# ---------------------------------------------------------------------------
# sync_runner._adapter() dispatch (no domain registration — dispatch only)
# ---------------------------------------------------------------------------


def test_adapter_dispatches_baostock_without_live_adapter_freeze() -> None:
    sr._BAOSTOCK_SOURCE = None
    src = sr._adapter("baostock")
    assert isinstance(src, BaostockSource)
    assert sr._adapter("baostock") is src


def test_baostock_is_not_any_domains_source_after_calendar_rule_switch() -> None:
    """Regression lock superseding the 2026-08-30 cut's "exactly one domain"
    scope guard. As of the 2026-08-31 authorized source switch, trade_cal moved
    OFF baostock ONTO calendar_rule — see formal_boundaries.py
    _FORMAL_BOUNDARIES["trade_cal"] and
    services/data_sources/sources/calendar_rule.py's module docstring for why:
    baostock got blacklisted by its own risk control mid-concurrency-probe on
    2026-08-31, and real-world testing of the three alternative vendor sources
    showed all of them structurally incapable of ever returning future trade
    dates. baostock therefore sources ZERO domains right now — not "exactly
    one" any more, but zero. This assertion is deliberately the opposite of the
    old invariant: its value is that if someone reverts trade_cal back onto
    baostock (e.g. "it's off the blacklist now, switch back"), this test breaks
    and forces them to read this docstring — and calendar_rule.py's — first.
    The on_demand ``baostock_trade_cal`` domain retired in the 2026-08-30 cut
    must also stay retired (it was never resurrected by this switch)."""
    import yaml
    from pathlib import Path

    registry_path = (
        Path(__file__).resolve().parents[3] / "backend" / "config" / "sync_registry.yaml"
    )
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    baostock_domains = [
        name
        for name, spec in (registry.get("domains") or {}).items()
        if spec.get("source") == "baostock"
    ]
    assert baostock_domains == []
    assert "baostock_trade_cal" not in (registry.get("domains") or {})


# ---------------------------------------------------------------------------
# registry: the formal trade_cal domain now resolves through calendar_rule
# (2026-08-31 authorized source switch, baostock -> calendar_rule — see
# formal_boundaries.py and calendar_rule.py's module docstring for why), while
# every table-name-shaped field is untouched — physical tables keep their
# tushare-prefixed legacy names by design.
# ---------------------------------------------------------------------------


def test_registry_trade_cal_domain_now_sources_from_calendar_rule() -> None:
    """2026-08-31 authorized source switch: baostock -> calendar_rule (see
    formal_boundaries.py _FORMAL_BOUNDARIES["trade_cal"] and calendar_rule.py's
    module docstring for the why). Everything table-shaped stays untouched —
    the switch is adapter-only, so calendar_builder.py and its 17 consumers
    never touch source/api and don't need to change."""
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "trade_cal")
    assert spec["source"] == "calendar_rule"
    assert spec["api"] == "query_trade_dates"
    assert spec["target_db"] == "tushare_raw"
    # Physical table/grain/batch_mode/fixed_params are unchanged by design — the
    # switch is adapter-only; calendar_builder.py and its 17 consumers never
    # touch source/api and don't need to change.
    assert spec["target_table"] == "raw_tushare_trade_cal"
    assert spec["grain"] == ["exchange", "cal_date"]
    assert spec["batch_mode"] == "full_refresh"
    assert spec["fixed_params"] == {"exchange": "SSE"}
    assert spec["page_limit"] == 6000
    # calendar_rule fetches from no network at all (see its module docstring:
    # "没有网络、没有账号、没有限流、不会被拉黑") — sources.calendar_rule carries
    # no rate_limit block in sync_registry.yaml, and none must leak into
    # trade_cal's spec (this also held for baostock's never-published quota
    # before it, for a different reason — belt-and-suspenders either way).
    assert "rate_limit" not in spec


# ---------------------------------------------------------------------------
# adapter: query_trade_dates-only default end_date (day-of-init: calendar has to
# reach into the future, and baostock only returns future dates when end_date is
# passed explicitly — see module docstring 范围声明 / fetch_raw comment).
# ---------------------------------------------------------------------------


def test_query_trade_dates_defaults_end_date_to_next_year(monkeypatch) -> None:
    fixed_today = datetime.date(2026, 8, 30)

    class _FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return fixed_today

    monkeypatch.setattr(datetime, "date", _FixedDate)

    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([[["2026-08-31", "0"]]], fields=["calendar_date", "is_trading_day"]),
    )
    src = BaostockSource(bs_module=bs)
    src.fetch_raw("query_trade_dates", start_date="1990-12-19")

    assert bs.last_params["query_trade_dates"] == {
        "start_date": "1990-12-19",
        "end_date": "2027-12-31",
    }


def test_query_trade_dates_respects_explicit_end_date() -> None:
    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([[["2026-08-31", "0"]]], fields=["calendar_date", "is_trading_day"]),
    )
    src = BaostockSource(bs_module=bs)
    src.fetch_raw("query_trade_dates", start_date="1990-12-19", end_date="2026-12-31")

    # Caller's explicit end_date must survive untouched, never overwritten.
    assert bs.last_params["query_trade_dates"] == {
        "start_date": "1990-12-19",
        "end_date": "2026-12-31",
    }


def test_end_date_default_does_not_leak_into_other_apis(monkeypatch) -> None:
    """The query_trade_dates-only default must not bleed into other api calls
    (e.g. query_history_k_data_plus, which has its own date param semantics)."""
    fixed_today = datetime.date(2026, 8, 30)

    class _FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return fixed_today

    monkeypatch.setattr(datetime, "date", _FixedDate)

    bs = _FakeBaostock()
    bs.queue_result(
        "query_history_k_data_plus",
        _FakeResult([[["600000.SH", "2020-07-01", "10.0"]]], fields=["code", "date", "close"]),
    )
    src = BaostockSource(bs_module=bs)
    src.fetch_raw(
        "query_history_k_data_plus",
        code="600000.SH",
        start_date="2020-07-01",
    )

    assert bs.last_params["query_history_k_data_plus"] == {
        "code": "600000.SH",
        "start_date": "2020-07-01",
    }
    assert "end_date" not in bs.last_params["query_history_k_data_plus"]


# ---------------------------------------------------------------------------
# adapter: query_trade_dates field normalization for the tushare-shaped calendar
# contract request (2026-08-30 trade_cal 授权换源新增). calendar_contract.
# request_for_page() sends exchange + compact 8-digit dates + limit/offset —
# baostock.query_trade_dates(start_date=None, end_date=None) accepts neither
# exchange/limit/offset nor compact dates. These tests lock in the translation
# in both directions plus the tushare-shaped output shape.
# ---------------------------------------------------------------------------


def test_query_trade_dates_strips_exchange_and_converts_compact_dates() -> None:
    """A calendar-contract-shaped request (exchange + compact dates + limit/offset)
    must reach baostock as bare dashed start_date/end_date only."""
    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult(
            [[["1990-12-19", "1"], ["1990-12-20", "0"]]],
            fields=["calendar_date", "is_trading_day"],
        ),
    )
    src = BaostockSource(bs_module=bs)
    src.fetch_raw(
        "query_trade_dates",
        exchange="SSE",
        start_date="19901219",
        end_date="20261231",
        limit=6000,
        offset=0,
    )

    assert bs.last_params["query_trade_dates"] == {
        "start_date": "1990-12-19",
        "end_date": "2026-12-31",
    }


def test_query_trade_dates_output_is_normalized_to_tushare_shape() -> None:
    """Output rows: exchange=SSE / cal_date compact / is_open int / pretrade_date
    is the previous open day (compact) within this call's fetched rows, None for
    the first row — equivalent to the LAG(...) IGNORE NULLS SQL in the module
    docstring, computed via one ascending linear scan."""
    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult(
            [
                [
                    ["1990-12-19", "1"],  # first open day: pretrade_date None
                    ["1990-12-20", "0"],  # closed: pretrade_date carries prior open day
                    ["1990-12-21", "1"],  # open: pretrade_date still prior open day (not self)
                    ["1990-12-22", "1"],  # open: pretrade_date is now 1990-12-21
                ]
            ],
            fields=["calendar_date", "is_trading_day"],
        ),
    )
    src = BaostockSource(bs_module=bs)
    rows = src.fetch_raw(
        "query_trade_dates", exchange="SSE", start_date="19901219", end_date="19901231"
    )

    assert rows == [
        {"exchange": "SSE", "cal_date": "19901219", "is_open": 1, "pretrade_date": None},
        {"exchange": "SSE", "cal_date": "19901220", "is_open": 0, "pretrade_date": "19901219"},
        {"exchange": "SSE", "cal_date": "19901221", "is_open": 1, "pretrade_date": "19901219"},
        {"exchange": "SSE", "cal_date": "19901222", "is_open": 1, "pretrade_date": "19901221"},
    ]


def test_query_trade_dates_applies_local_offset_limit_pagination() -> None:
    """baostock has no server-side offset (query_trade_dates always returns the
    whole [start_date, end_date] range) — the adapter must fetch the full range
    every call and slice [offset:offset+limit] locally, so multi-page contract
    callers (calendar_contract page_limit=6000) get correctly bounded pages
    without losing pretrade_date continuity across the page boundary."""
    bs = _FakeBaostock()
    full_range = [
        ["1990-12-19", "1"],
        ["1990-12-20", "0"],
        ["1990-12-21", "1"],
        ["1990-12-22", "1"],
    ]
    # Same full range queued twice: one physical call per contract page (baostock
    # doesn't remember state between calls; each call must ask for everything).
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([full_range], fields=["calendar_date", "is_trading_day"]),
    )
    bs.queue_result(
        "query_trade_dates",
        _FakeResult([full_range], fields=["calendar_date", "is_trading_day"]),
    )
    src = BaostockSource(bs_module=bs)

    page0 = src.fetch_raw(
        "query_trade_dates",
        exchange="SSE",
        start_date="19901219",
        end_date="19901231",
        limit=2,
        offset=0,
    )
    page1 = src.fetch_raw(
        "query_trade_dates",
        exchange="SSE",
        start_date="19901219",
        end_date="19901231",
        limit=2,
        offset=2,
    )

    assert [r["cal_date"] for r in page0] == ["19901219", "19901220"]
    assert [r["cal_date"] for r in page1] == ["19901221", "19901222"]
    # pretrade_date on page1's first row must chain from the pre-page1 history
    # (computed over the full fetched range before slicing), not reset to None.
    assert page1[0] == {
        "exchange": "SSE", "cal_date": "19901221", "is_open": 1, "pretrade_date": "19901219"
    }
    # limit/offset never reach the real baostock call (it has no such kwargs).
    assert bs.last_params["query_trade_dates"] == {
        "start_date": "1990-12-19",
        "end_date": "1990-12-31",
    }


def test_query_trade_dates_without_limit_returns_unsliced_normalized_rows() -> None:
    """A direct/manual call with no limit/offset (e.g. the old on_demand-domain
    call shape) must return every normalized row, not an empty/truncated slice."""
    bs = _FakeBaostock()
    bs.queue_result(
        "query_trade_dates",
        _FakeResult(
            [[["1990-12-19", "1"], ["1990-12-20", "0"]]],
            fields=["calendar_date", "is_trading_day"],
        ),
    )
    src = BaostockSource(bs_module=bs)
    rows = src.fetch_raw("query_trade_dates", start_date="1990-12-19", end_date="1990-12-31")

    assert [r["cal_date"] for r in rows] == ["19901219", "19901220"]
