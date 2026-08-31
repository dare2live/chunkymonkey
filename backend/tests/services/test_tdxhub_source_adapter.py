"""TdxhubSource — sync_runner adapter for ``daily`` (日K) 授权换源 tushare -> tdxhub.

一律 monkeypatch 假 client + 假 ``fetch_unadjusted_bars`` (禁真实网络, CI 跑不了网络)。
覆盖点对齐任务要求: 四个已实测除权样本、无事件/送转股场景、amount 换算、11-key 严格
契约、trade_date 紧凑字符串形态、单票失败不拖垮整批、成功率阈值 raise、新股首日退化、
未知 api 报 KeyError、xdxr 进程内缓存。
"""
from __future__ import annotations

from datetime import date

import pytest

import services.data_sources.sources.tdxhub as tdxhub_adapter
from services.data_sources.sources.tdxhub import TdxhubDailyBatchError, TdxhubSource

EXPECTED_DAILY_KEYS = {
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
}


# ---------------------------------------------------------------------------
# 假客户端/假 fetch_unadjusted_bars — 全程不碰网络, 也不碰真 tdxhub 包。
# ---------------------------------------------------------------------------


class FakeQuotesClient:
    """伪造 ``quotes_client()`` 返回的对象: 只暴露本 adapter 用到的两个方法。"""

    def __init__(
        self,
        *,
        stocks_by_market: dict[int, list[dict]] | None = None,
        xdxr_by_code: dict[str, list[dict]] | None = None,
    ) -> None:
        self.stocks_by_market = stocks_by_market or {}
        self.xdxr_by_code = xdxr_by_code or {}
        self.xdxr_calls: dict[str, int] = {}

    def stocks(self, market: int) -> list[dict]:
        return self.stocks_by_market.get(market, [])

    def xdxr(self, code: str) -> list[dict]:
        self.xdxr_calls[code] = self.xdxr_calls.get(code, 0) + 1
        return self.xdxr_by_code.get(code, [])


def _install_fake_bars(
    monkeypatch: pytest.MonkeyPatch,
    bars_by_ts_code: dict[str, list[tuple]],
    *,
    raise_for: set[str] | None = None,
) -> None:
    """替换模块内 ``fetch_unadjusted_bars`` 名字 (在 sources/tdxhub.py 里是
    ``from ... import fetch_unadjusted_bars``, 所以要 monkeypatch 到本模块上,
    不是 tdxhub_kline_recon 那个原始模块)。行为镜像真实语义: 按 [start, end]
    过滤该 ts_code 的已知 bars, 升序 (调用方就是这么假定的)。
    """
    raise_for = raise_for or set()

    def _fake(client, ts_code, *, start, end, offset=None, adjust=None):
        if ts_code in raise_for:
            raise RuntimeError(f"boom fetching {ts_code}")
        bars = bars_by_ts_code.get(ts_code, [])
        return sorted(
            (b for b in bars if start <= b[1] <= end),
            key=lambda b: b[1],
        )

    monkeypatch.setattr(tdxhub_adapter, "fetch_unadjusted_bars", _fake)


def _bar(
    ts_code: str,
    d: date,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    vol: float,
    amount: float,
) -> tuple:
    return (ts_code, d, open_, high, low, close, vol, amount)


def _xdxr_event(
    target: date,
    *,
    fenhong: float = 0.0,
    songzhuangu: float = 0.0,
    peigu: float = 0.0,
    peigujia: float = 0.0,
    category: int = 1,
    name: str = "除权除息",
) -> dict:
    return {
        "year": target.year,
        "month": target.month,
        "day": target.day,
        "category": category,
        "name": name,
        "fenhong": fenhong,
        "songzhuangu": songzhuangu,
        "peigu": peigu,
        "peigujia": peigujia,
    }


def _parse_compact(text: str) -> date:
    return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))


# ---------------------------------------------------------------------------
# 1. 四个已实测除权样本 (逐个断言精确值)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, exch, trade_date, prev_close, fenhong, expected",
    [
        ("600869", "SH", "20240726", 3.53, 0.70, 3.46),
        ("002484", "SZ", "20250530", 18.91, 2.60, 18.65),
        ("002484", "SZ", "20240617", 14.31, 2.60, 14.05),
        ("600869", "SH", "20200630", 4.01, 0.12, 4.00),
    ],
)
def test_pre_close_documented_xdxr_samples(code, exch, trade_date, prev_close, fenhong, expected):
    target = _parse_compact(trade_date)
    ts_code = f"{code}.{exch}"
    client = FakeQuotesClient(xdxr_by_code={code: [_xdxr_event(target, fenhong=fenhong)]})
    source = TdxhubSource(client_factory=lambda: client)

    got = source._adjusted_pre_close(client, ts_code, target, prev_close)

    assert got == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. 无除权事件时 pre_close == prev_close
# ---------------------------------------------------------------------------


def test_pre_close_no_event_equals_prev_close():
    target = date(2026, 8, 28)
    client = FakeQuotesClient(xdxr_by_code={"600000": []})
    source = TdxhubSource(client_factory=lambda: client)

    got = source._adjusted_pre_close(client, "600000.SH", target, 12.34)

    assert got == pytest.approx(12.34)


def test_pre_close_event_on_different_date_ignored():
    target = date(2026, 8, 28)
    other_day_event = _xdxr_event(date(2026, 8, 27), fenhong=1.0)
    client = FakeQuotesClient(xdxr_by_code={"600000": [other_day_event]})
    source = TdxhubSource(client_factory=lambda: client)

    got = source._adjusted_pre_close(client, "600000.SH", target, 12.34)

    assert got == pytest.approx(12.34)


# ---------------------------------------------------------------------------
# 3. 送转股场景: 10 送 10 (songzhuangu=10) -> pre_close == prev_close / 2
# ---------------------------------------------------------------------------


def test_pre_close_stock_dividend_ten_for_ten_halves():
    target = date(2026, 8, 28)
    client = FakeQuotesClient(
        xdxr_by_code={"000001": [_xdxr_event(target, songzhuangu=10.0)]}
    )
    source = TdxhubSource(client_factory=lambda: client)

    got = source._adjusted_pre_close(client, "000001.SZ", target, 20.00)

    assert got == pytest.approx(10.00)


# ---------------------------------------------------------------------------
# 4/5/6. amount /1000, vol 不变, 恰好 11 个 key, trade_date 是紧凑字符串
# ---------------------------------------------------------------------------


def test_daily_row_amount_scaled_vol_untouched_11_keys_and_compact_trade_date(monkeypatch):
    target = date(2026, 8, 28)
    ts_code = "600869.SH"
    bars = {
        ts_code: [
            _bar(
                ts_code,
                date(2026, 8, 27),
                open_=9.9,
                high=10.0,
                low=9.8,
                close=10.0,
                vol=500.0,
                amount=5_000_000.0,
            ),
            _bar(
                ts_code,
                target,
                open_=10.0,
                high=10.5,
                low=9.9,
                close=10.3,
                vol=1234.0,
                amount=12_340_000.0,
            ),
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(stocks_by_market={1: [{"code": "600869"}]}, xdxr_by_code={})
    source = TdxhubSource(client_factory=lambda: client)

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == EXPECTED_DAILY_KEYS
    assert row["vol"] == 1234.0
    assert row["amount"] == pytest.approx(12_340_000.0 / 1000.0)
    assert row["trade_date"] == "20260828"
    assert isinstance(row["trade_date"], str)
    assert row["ts_code"] == ts_code
    assert row["pre_close"] == pytest.approx(10.0)
    assert row["change"] == pytest.approx(round(10.3 - 10.0, 4))
    assert row["pct_chg"] == pytest.approx(round((10.3 - 10.0) / 10.0 * 100, 4))


# ---------------------------------------------------------------------------
# 7. 单只票抛异常不影响其它票, 成功率高于阈值时正常返回
# ---------------------------------------------------------------------------


def _ten_code_universe(target: date, *, failing_index: int | None = None):
    """10 只沪市票, 均以 ``60`` 开头, 每只只需一根当日 bar。"""
    codes = [f"6000{i:02d}" for i in range(1, 11)]
    ts_codes = [f"{c}.SH" for c in codes]
    bars: dict[str, list[tuple]] = {}
    for i, ts_code in enumerate(ts_codes):
        bars[ts_code] = [
            _bar(
                ts_code,
                target,
                open_=10.0 + i,
                high=10.5 + i,
                low=9.5 + i,
                close=10.2 + i,
                vol=100.0 + i,
                amount=1_000.0 + i,
            )
        ]
    raise_for = set()
    if failing_index is not None:
        raise_for = {ts_codes[failing_index]}
    stocks = [{"code": c} for c in codes]
    return ts_codes, bars, raise_for, stocks


def test_single_symbol_exception_does_not_crash_batch_above_threshold(monkeypatch):
    target = date(2026, 8, 28)
    ts_codes, bars, raise_for, stocks = _ten_code_universe(target, failing_index=4)
    _install_fake_bars(monkeypatch, bars, raise_for=raise_for)
    client = FakeQuotesClient(stocks_by_market={1: stocks})
    source = TdxhubSource(client_factory=lambda: client)

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 9
    returned_codes = {row["ts_code"] for row in rows}
    assert ts_codes[4] not in returned_codes
    assert returned_codes == set(ts_codes) - {ts_codes[4]}


# ---------------------------------------------------------------------------
# 8. 成功率低于阈值时 raise
# ---------------------------------------------------------------------------


def test_low_success_rate_raises_batch_error(monkeypatch):
    target = date(2026, 8, 28)
    # 2 只票, 1 只失败 -> 成功率 0.5 < 默认阈值 0.9
    codes = ["600001", "600002"]
    ts_codes = [f"{c}.SH" for c in codes]
    bars = {
        ts_codes[0]: [
            _bar(ts_codes[0], target, open_=10.0, high=10.5, low=9.5, close=10.2, vol=100.0, amount=1000.0)
        ]
    }
    _install_fake_bars(monkeypatch, bars, raise_for={ts_codes[1]})
    client = FakeQuotesClient(stocks_by_market={1: [{"code": c} for c in codes]})
    source = TdxhubSource(client_factory=lambda: client)

    with pytest.raises(TdxhubDailyBatchError):
        source.fetch_raw("daily", trade_date="20260828")


def test_success_rate_exactly_at_threshold_does_not_raise(monkeypatch):
    target = date(2026, 8, 28)
    ts_codes, bars, raise_for, stocks = _ten_code_universe(target, failing_index=0)
    _install_fake_bars(monkeypatch, bars, raise_for=raise_for)
    client = FakeQuotesClient(stocks_by_market={1: stocks})
    source = TdxhubSource(client_factory=lambda: client, min_success_rate=0.9)

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 9  # 9/10 == 0.9, not < 0.9


# ---------------------------------------------------------------------------
# 9. 新股上市首日 (取不到前一根) 时 pre_close == open, 不崩
# ---------------------------------------------------------------------------


def test_ipo_first_day_pre_close_falls_back_to_open(monkeypatch):
    target = date(2026, 8, 28)
    ts_code = "600999.SH"
    bars = {
        ts_code: [
            _bar(
                ts_code,
                target,
                open_=15.88,
                high=19.06,
                low=15.88,
                close=19.06,
                vol=5000.0,
                amount=90_000_000.0,
            )
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(stocks_by_market={1: [{"code": "600999"}]})
    source = TdxhubSource(client_factory=lambda: client)

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1
    row = rows[0]
    assert row["pre_close"] == pytest.approx(row["open"])
    assert row["pre_close"] == pytest.approx(15.88)


# ---------------------------------------------------------------------------
# 10. 未知 api 抛 KeyError
# ---------------------------------------------------------------------------


def test_unknown_api_raises_key_error():
    source = TdxhubSource(client_factory=lambda: FakeQuotesClient())

    with pytest.raises(KeyError):
        source.fetch_raw("nope", trade_date="20260828")


# ---------------------------------------------------------------------------
# 11. xdxr 进程内缓存: 同一 code 查两次, 底层 xdxr 只被调用一次
# ---------------------------------------------------------------------------


def test_xdxr_cached_per_code_within_instance():
    target = date(2026, 8, 28)
    client = FakeQuotesClient(xdxr_by_code={"600869": [_xdxr_event(target, fenhong=0.5)]})
    source = TdxhubSource(client_factory=lambda: client)

    first = source._xdxr_events(client, "600869")
    second = source._xdxr_events(client, "600869")

    assert first == second
    assert client.xdxr_calls["600869"] == 1


def test_xdxr_cache_isolated_per_code():
    target = date(2026, 8, 28)
    client = FakeQuotesClient(
        xdxr_by_code={
            "600869": [_xdxr_event(target, fenhong=0.5)],
            "002484": [_xdxr_event(target, fenhong=2.6)],
        }
    )
    source = TdxhubSource(client_factory=lambda: client)

    source._xdxr_events(client, "600869")
    source._xdxr_events(client, "002484")
    source._xdxr_events(client, "600869")

    assert client.xdxr_calls["600869"] == 1
    assert client.xdxr_calls["002484"] == 1
