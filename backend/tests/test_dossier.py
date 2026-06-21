"""dossier 服务单测 (2026-06-21 P2) — 纯函数: 趋势线着色 / 调参耦合 / overrides 解析。

DB 依赖部分 (load_one/interpret_stock/screen_pattern) 走集成 smoke (真实股 600800), 此处测纯逻辑。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from routers.dossier import _parse_overrides  # noqa: E402
from services.dossier import _effective_cfg, trend_series  # noqa: E402


def test_trend_series_colors_by_state():
    """趋势线按主态着色 (bull→up / bear→down / 横盘→flat) + 降采样。"""
    ohlcv = {"date": [f"2020-01-{i:02d}" for i in range(1, 13)], "close": list(range(10, 22))}
    daily_cls = {f"2020-01-{i:02d}": {"dominant": ("上升通道" if i % 2 else "下跌通道")} for i in range(1, 13)}
    out = trend_series(ohlcv, daily_cls, max_points=240)
    assert len(out) == 12
    colors = {p["color"] for p in out}
    assert "up" in colors and "down" in colors           # 双态都映射
    assert all(set(p) == {"date", "close", "color", "state"} for p in out)


def test_trend_series_downsamples():
    """点数 > max_points 时降采样。"""
    n = 1000
    ohlcv = {"date": [str(i) for i in range(n)], "close": [float(i) for i in range(n)]}
    out = trend_series(ohlcv, {}, max_points=100)
    assert len(out) <= 110                                # ~100 (step=n//100)
    assert all(p["color"] == "flat" for p in out)         # 无分类 → flat


def test_effective_cfg_applies_coupling():
    """调参经边界耦合同步 → effective config 改的是阈值; 原 config 不变。"""
    eff, notes = _effective_cfg({"上升通道.均线斜率": 7.0})
    up = [c for c in eff["状态"]["上升通道"]["条件"] if c["指标"] == "均线斜率"][0]
    dn = [c for c in eff["状态"]["下跌通道"]["条件"] if c["指标"] == "均线斜率"][0]
    assert up["阈值"] == 7.0 and dn["阈值"] == -7.0        # 互补对称镜像
    assert any("镜像" in n for n in notes)


def test_parse_overrides():
    """ov 串 → dict; 非法项忽略。"""
    assert _parse_overrides("上升通道.均线斜率:3.0,放量突破.量比:2.5") == {"上升通道.均线斜率": 3.0, "放量突破.量比": 2.5}
    assert _parse_overrides("") is None
    assert _parse_overrides("坏项:abc") is None           # float 失败 → 跳过 → 空 → None
