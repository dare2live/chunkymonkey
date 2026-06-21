"""technical_states 模块单测 (2026-06-21) — config 加载 / PIT 正确性 / 状态语义 / resample / 多TF。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.technical_states import compute, load_config, resample  # noqa: E402
from services.technical_states.classifier import classify_bar, classify_stock  # noqa: E402


def _series(n, start="2020-01-02"):
    import datetime
    d0 = datetime.date.fromisoformat(start)
    dates, dd = [], d0
    while len(dates) < n:
        if dd.weekday() < 5:
            dates.append(dd.isoformat())
        dd += datetime.timedelta(days=1)
    return dates


def test_config_loads():
    cfg = load_config()
    assert set(cfg["states"]) == {"低位横盘", "放量突破", "上升通道", "缩量上涨", "高位滞涨", "下跌通道", "缩量回踩"}
    assert "daily" in cfg["timeframes"] and "monthly" in cfg["timeframes"]
    assert cfg["timeframes"]["monthly"]["windows"]["er"] < cfg["timeframes"]["daily"]["windows"]["er"]  # 月线窗更短


def test_resample_weekly():
    dates = _series(20)
    o = h = l = c = [10.0 + i for i in range(20)]
    v = [100] * 20
    wd, wo, wh, wl, wc, wv = resample(dates, o, h, l, c, v, "W")
    assert len(wd) < len(dates)                       # 周 bar 少于日 bar
    assert wv[0] >= 100                               # 量是周内 sum


def test_pit_no_lookahead():
    """PIT 铁律: 加未来 bar 不改变历史 bar 的特征 (特征只用 ≤i)。"""
    n = 200
    dates = _series(n + 60)
    c = [10.0 * (1.005 ** i) for i in range(n + 60)]
    o = h = l = c
    v = [100000.0] * (n + 60)
    f_short = compute(dates[:n], o[:n], h[:n], l[:n], c[:n], v[:n])
    f_long = compute(dates, o, h, l, c, v)
    common = set(f_short) & set(f_long)
    assert len(common) > 20
    for d in list(common)[:30]:
        for k in ("ma_slope", "mom20", "er", "pctile"):
            a, b = f_short[d][k], f_long[d][k]
            if a == a and b == b:                     # 非 NaN
                assert abs(a - b) < 1e-9, f"{d} {k} 受未来影响 = look-ahead"


def test_classify_uptrend():
    """强单调上涨 → 主态=上升通道 (语义)。"""
    n = 200
    dates = _series(n)
    c = [10.0 * (1.012 ** i) for i in range(n)]       # 1.2%/日稳升
    o = h = l = c
    v = [100000.0] * n
    f = compute(dates, o, h, l, c, v)
    doms = [classify_bar(ff)["dominant"] for ff in list(f.values())[-30:]]
    assert doms.count("上升通道") >= 15, f"强升却没识别上升通道: {set(doms)}"


def test_classify_downtrend():
    n = 200
    dates = _series(n)
    c = [10.0 * (0.99 ** i) for i in range(n)]         # 1%/日跌
    o = h = l = c
    v = [100000.0] * n
    f = compute(dates, o, h, l, c, v)
    doms = [classify_bar(ff)["dominant"] for ff in list(f.values())[-30:]]
    assert doms.count("下跌通道") >= 15, f"持续跌却没识别下跌通道: {set(doms)}"


def test_classify_stock_multi_tf_keys():
    """多TF API 返回结构正确。"""
    n = 400
    dates = _series(n)
    c = [10.0 + (i % 50) * 0.1 for i in range(n)]
    o = h = l = c
    v = [100000.0] * n
    mtf = classify_stock(dates, o, h, l, c, v)
    assert len(mtf) > 0
    r = next(iter(mtf.values()))
    assert set(r) >= {"daily", "daily_sub", "weekly", "monthly", "mtf_aligned", "entropy"}
