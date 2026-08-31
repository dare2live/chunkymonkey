"""TdxhubSource — sync_runner adapter for ``daily`` (日K) 授权换源 tushare -> tdxhub.

一律 monkeypatch 假 client + 假 ``fetch_unadjusted_bars`` (禁真实网络, CI 跑不了网络)。
覆盖点对齐任务要求: 四个已实测除权样本、无事件/送转股场景、amount 换算、11-key 严格
契约、trade_date 紧凑字符串形态、单票失败不拖垮整批、成功率阈值 raise、新股首日退化、
未知 api 报 KeyError、xdxr 进程内缓存 (进程内 + 跨进程落盘两级, 六种磁盘故障姿态降级)、
``extra_ts_codes`` 北交所代码注入 (去重/校验)。

模块级 ``_isolate_xdxr_cache_path`` autouse fixture 把 ``TDXHUB_XDXR_CACHE_PATH``
重定向到每个测试自己的 ``tmp_path`` —— 对本文件里全部测试生效 (含加固前已存在的
15 个用例), 因为新增的磁盘缓存二级层是 ``TdxhubSource._xdxr_events`` 内部实现细节,
连既有的 ``fetch_raw``/``_adjusted_pre_close`` 直调测试都会经过它、间接写盘。不隔离
会写脏真实 ``data/scratch/tdxhub_xdxr_cache.json`` (已实测复现: 同一份同 code 不同
``target``/不同期望值的参数化用例互相读到对方落盘的缓存, 断言跨用例串味) ——这正是
本任务背景提到的"主机记忆落盘时两个测试没隔离导致复跑必红"同一类教训。
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

import services.data_sources.sources.tdxhub as tdxhub_adapter
from services.data_sources.sources.tdxhub import TdxhubDailyBatchError, TdxhubSource


@pytest.fixture(autouse=True)
def _isolate_xdxr_cache_path(monkeypatch, tmp_path):
    """详见模块 docstring —— 对本文件全部测试生效, 绝不碰真实
    ``data/scratch/tdxhub_xdxr_cache.json``。个别新测试需要精确控制缓存文件
    内容/路径时, 会在测试体内再显式 ``monkeypatch.setenv`` 一次覆盖这里的
    默认值, 不影响其它测试各自独立的 ``tmp_path``。"""
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(tmp_path / "xdxr_cache_autouse.json"))


def _write_xdxr_cache_file(path: Path, payload: dict) -> None:
    """测试专用: 直接把 payload 写成 xdxr 磁盘缓存文件, 绕开
    ``_persist_xdxr_cache_entry`` 走真实取数路径, 用来精确摆好
    ``cached_at``/结构损坏等场景。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


# ---------------------------------------------------------------------------
# 12. xdxr 落盘缓存 — cached_at > target 时命中, 底层 xdxr 零调用
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_hit_when_cached_after_target(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    target = date(2026, 8, 28)
    cached_event = _xdxr_event(target, fenhong=0.7)
    _write_xdxr_cache_file(
        cache_path, {"600869": {"cached_at": "20260829", "events": [cached_event]}}
    )
    # 若真去查网络会拿到一个不同的 (错的) 事件 —— 断言必须走缓存, 不能碰到它。
    client = FakeQuotesClient(xdxr_by_code={"600869": [_xdxr_event(target, fenhong=99.0)]})
    source = TdxhubSource(client_factory=lambda: client)

    events = source._xdxr_events(client, "600869", target)

    assert events == [cached_event]
    assert client.xdxr_calls.get("600869", 0) == 0


# ---------------------------------------------------------------------------
# 13. cached_at == target / cached_at < target 都必须重查 (两个独立用例)
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_miss_when_cached_at_equals_target(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    target = date(2026, 8, 28)
    _write_xdxr_cache_file(cache_path, {"600869": {"cached_at": "20260828", "events": []}})
    fresh_event = _xdxr_event(target, fenhong=0.7)
    client = FakeQuotesClient(xdxr_by_code={"600869": [fresh_event]})
    source = TdxhubSource(client_factory=lambda: client)

    events = source._xdxr_events(client, "600869", target)

    assert events == [fresh_event]
    assert client.xdxr_calls.get("600869", 0) == 1


def test_xdxr_disk_cache_miss_when_cached_before_target(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    target = date(2026, 8, 28)
    _write_xdxr_cache_file(cache_path, {"600869": {"cached_at": "20260827", "events": []}})
    fresh_event = _xdxr_event(target, fenhong=0.7)
    client = FakeQuotesClient(xdxr_by_code={"600869": [fresh_event]})
    source = TdxhubSource(client_factory=lambda: client)

    events = source._xdxr_events(client, "600869", target)

    assert events == [fresh_event]
    assert client.xdxr_calls.get("600869", 0) == 1


# ---------------------------------------------------------------------------
# 14. 缓存跨"进程"复用: 写一次 -> 新建实例(模拟新进程) -> 命中且不调 xdxr
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_survives_new_instance_simulating_new_process(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(tdxhub_adapter, "_today", lambda: date(2026, 8, 30))
    target = date(2020, 1, 1)  # 舒服地早于钉死的"今天", 不依赖真实墙钟日期
    event = _xdxr_event(target, fenhong=1.23)
    client = FakeQuotesClient(xdxr_by_code={"600869": [event]})

    first_source = TdxhubSource(client_factory=lambda: client)
    first_events = first_source._xdxr_events(client, "600869", target)
    assert first_events == [event]
    assert client.xdxr_calls["600869"] == 1
    assert cache_path.exists()

    # 新实例 = 模拟新进程: 进程内 dict 缓存是空的, 只能靠落盘缓存命中。
    second_source = TdxhubSource(client_factory=lambda: client)
    second_events = second_source._xdxr_events(client, "600869", target)

    assert second_events == [event]
    assert client.xdxr_calls["600869"] == 1  # 没有第二次网络调用


# ---------------------------------------------------------------------------
# 15. 缓存文件损坏 ("{{{") -> 静默降级, 正常取数, 不抛异常
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_corrupt_json_downgrades_silently(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    cache_path.write_text("{{{", encoding="utf-8")
    target = date(2026, 8, 28)
    ts_code = "600869.SH"
    bars = {
        ts_code: [
            # 前一交易日的 bar 必须存在, pre_close 才会走 _adjusted_pre_close
            # (进而调用 xdxr) 而不是退化成"新股首日"用 open 兜底。
            _bar(
                ts_code, date(2026, 8, 27),
                open_=9.9, high=10.0, low=9.8, close=10.0, vol=500.0, amount=5_000_000.0,
            ),
            _bar(
                ts_code, target,
                open_=10.0, high=10.5, low=9.5, close=10.2, vol=100.0, amount=1000.0,
            ),
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(
        stocks_by_market={1: [{"code": "600869"}]},
        xdxr_by_code={"600869": [_xdxr_event(target, fenhong=0.1)]},
    )
    source = TdxhubSource(client_factory=lambda: client)

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1
    assert client.xdxr_calls.get("600869", 0) == 1


# ---------------------------------------------------------------------------
# 16. 缓存文件顶层是 list 而非 dict -> 同样降级
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_non_dict_top_level_downgrades_silently(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    target = date(2026, 8, 28)
    event = _xdxr_event(target, fenhong=0.2)
    client = FakeQuotesClient(xdxr_by_code={"600869": [event]})
    source = TdxhubSource(client_factory=lambda: client)

    events = source._xdxr_events(client, "600869", target)

    assert events == [event]
    assert client.xdxr_calls["600869"] == 1


# ---------------------------------------------------------------------------
# 17. 单个 code 的缓存条目结构损坏 -> 只该条 miss, 其它 code 仍命中
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_single_entry_corruption_isolated(monkeypatch, tmp_path):
    cache_path = tmp_path / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    target = date(2026, 8, 28)
    good_event = _xdxr_event(target, fenhong=0.3)
    _write_xdxr_cache_file(
        cache_path,
        {
            "600869": "not-a-dict-entry",  # 结构损坏: 不是 dict
            "002484": {"cached_at": "20260829", "events": [good_event]},  # 完好
        },
    )
    fresh_event_for_broken_code = _xdxr_event(target, fenhong=9.9)
    client = FakeQuotesClient(
        xdxr_by_code={
            "600869": [fresh_event_for_broken_code],
            "002484": [_xdxr_event(target, fenhong=0.1)],  # 不应被查到 (缓存命中)
        }
    )
    source = TdxhubSource(client_factory=lambda: client)

    got_600869 = source._xdxr_events(client, "600869", target)
    got_002484 = source._xdxr_events(client, "002484", target)

    assert got_600869 == [fresh_event_for_broken_code]
    assert client.xdxr_calls["600869"] == 1  # 该条损坏 -> miss -> 重查
    assert got_002484 == [good_event]
    assert client.xdxr_calls.get("002484", 0) == 0  # 未损坏 -> 命中缓存, 不重查


# ---------------------------------------------------------------------------
# 18. 无写权限时不抛异常 (取数照常成功)
# ---------------------------------------------------------------------------


def test_xdxr_disk_cache_write_failure_does_not_raise(monkeypatch, tmp_path):
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    cache_path = readonly_dir / "xdxr.json"
    monkeypatch.setenv("TDXHUB_XDXR_CACHE_PATH", str(cache_path))
    os.chmod(readonly_dir, 0o555)  # r-xr-xr-x: 目录本身不可写, mkstemp 必失败
    try:
        target = date(2026, 8, 28)
        ts_code = "600869.SH"
        bars = {
            ts_code: [
                # 前一交易日的 bar 必须存在, 才会真的走到 _persist_xdxr_cache_entry
                # (否则 pre_close 退化成"新股首日"用 open 兜底, 根本不碰磁盘写)。
                _bar(
                    ts_code, date(2026, 8, 27),
                    open_=9.9, high=10.0, low=9.8, close=10.0, vol=500.0, amount=5_000_000.0,
                ),
                _bar(
                    ts_code, target,
                    open_=10.0, high=10.5, low=9.5, close=10.2, vol=100.0, amount=1000.0,
                ),
            ]
        }
        _install_fake_bars(monkeypatch, bars)
        client = FakeQuotesClient(
            stocks_by_market={1: [{"code": "600869"}]},
            xdxr_by_code={"600869": [_xdxr_event(target, fenhong=0.05)]},
        )
        source = TdxhubSource(client_factory=lambda: client)

        rows = source.fetch_raw("daily", trade_date="20260828")

        assert len(rows) == 1
        assert client.xdxr_calls.get("600869", 0) == 1
        assert not cache_path.exists()
    finally:
        os.chmod(readonly_dir, 0o755)  # 还原, 让 tmp_path 的自动清理能删掉它


# ---------------------------------------------------------------------------
# 19. extra_ts_codes 注入的北交所代码出现在 universe 里, 走正常取数路径拿到行
# ---------------------------------------------------------------------------


def test_extra_ts_codes_bj_appears_in_universe_and_is_fetched(monkeypatch):
    target = date(2026, 8, 28)
    bj_code = "920002.BJ"
    bars = {
        bj_code: [
            _bar(
                bj_code, target,
                open_=5.0, high=5.2, low=4.9, close=5.1, vol=200.0, amount=1020.0,
            )
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(stocks_by_market={})  # 沪深 universe 为空, 只有注入的北交所
    source = TdxhubSource(client_factory=lambda: client, extra_ts_codes=[bj_code])

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1
    assert rows[0]["ts_code"] == bj_code
    assert rows[0]["close"] == pytest.approx(5.1)


# ---------------------------------------------------------------------------
# 20. extra_ts_codes 与 stocks() 返回的代码重复时去重, 不产生重复行
# ---------------------------------------------------------------------------


def test_extra_ts_codes_deduped_against_universe(monkeypatch):
    target = date(2026, 8, 28)
    ts_code = "600869.SH"
    bars = {
        ts_code: [
            _bar(
                ts_code, target,
                open_=10.0, high=10.5, low=9.5, close=10.2, vol=100.0, amount=1000.0,
            )
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(stocks_by_market={1: [{"code": "600869"}]})
    source = TdxhubSource(client_factory=lambda: client, extra_ts_codes=[ts_code])

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1
    assert rows[0]["ts_code"] == ts_code


def test_extra_ts_codes_dedup_also_within_extra_list_itself(monkeypatch):
    target = date(2026, 8, 28)
    bj_code = "920002.BJ"
    bars = {
        bj_code: [
            _bar(
                bj_code, target,
                open_=5.0, high=5.2, low=4.9, close=5.1, vol=200.0, amount=1020.0,
            )
        ]
    }
    _install_fake_bars(monkeypatch, bars)
    client = FakeQuotesClient(stocks_by_market={})
    source = TdxhubSource(client_factory=lambda: client, extra_ts_codes=[bj_code, bj_code])

    rows = source.fetch_raw("daily", trade_date="20260828")

    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 21. 非法 extra_ts_codes 构造时 ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_code",
    [
        "920002",  # 无交易所后缀
        "abc.BJ",  # 代码段不是数字
        "92000.BJ",  # 代码段不是 6 位
        "920002.SZH",  # 交易所后缀非法
        "",
    ],
)
def test_extra_ts_codes_invalid_format_raises_at_construction(bad_code):
    with pytest.raises(ValueError):
        TdxhubSource(client_factory=lambda: FakeQuotesClient(), extra_ts_codes=[bad_code])
