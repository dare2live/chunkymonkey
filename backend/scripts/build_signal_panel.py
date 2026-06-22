"""L2 signal_panel 物化 — b分表 事件信号面 (平行 fact_feature_panel 连续因子面)。

数据模块顶层设计 v2 §8.0 (data_module_toplevel_design_20260622.md) + 用户 2026-06-22 "b分表" 裁决:
  L2 连续因子 (fact_feature_panel) 与 事件信号 (fact_signal_panel) **分两张同键并列 panel**,
  键均 (code, date)。signal_assembler 同键 JOIN 两表取 (排名因子 + 公式条件门)。语义干净不混 dtype。

signal_panel 放**公式事件信号** (布尔/事件), 区别于 segment_panel 的 form 态 (Weinstein 形态分类)。
首个公式: macd_golden_cross (canonical; 参数走 config/formula_macd_golden_cross.yaml 不 hardcode)。
加公式 = 加列 (invariant#3 可扩展分层), 不加表。

PIT: EMA 递推天然只用 ≤i; 金叉 = DIF[i]>DEA[i] AND DIF[i-1]<=DEA[i-1] (当 bar 确认, 不回贴)。
分层: builder 是 lesson 允许的 L0/L1-read 点 (走 data_loaders.load_kline, 与 feature_panel 同); 探索读物化后 panel。
edge-gating: 本 builder 建**结构 + 单一确定性公式** (档A 地基); 公式库全量铺开属档B (edge confirmed 后)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from services.data_loaders import load_kline, in_active_universe
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

PANEL = "fact_signal_panel"
_FORMULA_CFG = Path(__file__).resolve().parents[1] / "config" / "formula_macd_golden_cross.yaml"
# build 默认全史起点 = tushare qfq 主源数据起点
_DATA_START = "2019-01-01"   # rule-compliance: ok evidence=price_kline_qfq_tushare 数据起点 2019-01-02 (与 feature_panel 同口径全史)


def _ema(vals: list[float], period: int) -> list[float]:
    """递推 EMA (PIT: e[i] 只用 ≤i)。"""
    k = 2.0 / (period + 1)
    out: list[float] = []
    e: float | None = None
    for v in vals:
        e = float(v) if e is None else float(v) * k + e * (1 - k)
        out.append(e)
    return out


def macd_golden_cross(closes: list[float], fast: int, slow: int, signal: int) -> list[bool]:
    """MACD 金叉事件 (DIF 上穿 DEA)。PIT: 第 i bar 只用 ≤i。"""
    if len(closes) < 2:
        return [False] * len(closes)
    ef, es = _ema(closes, fast), _ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema(dif, signal)
    out = [False] * len(closes)
    for i in range(1, len(closes)):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            out[i] = True
    return out


def build(start: str = _DATA_START, end: str | None = None, limit_stocks: int = 0) -> dict:
    cfg = yaml.safe_load(_FORMULA_CFG.read_text(encoding="utf-8"))
    fast, slow, sig = int(cfg["fast_period"]), int(cfg["slow_period"]), int(cfg["signal_period"])
    by_code = load_kline(start, end, limit_stocks=limit_stocks)   # {code:{date,close,...}}
    rows: list[tuple] = []
    n_stock = 0
    for code, d in by_code.items():
        if not in_active_universe(code):     # universe 硬门 (排北交所/三板)
            continue
        dates, closes = d["date"], d["close"]
        if not dates:
            continue
        gc = macd_golden_cross(closes, fast, slow, sig)
        for i, dt in enumerate(dates):
            rows.append((code, str(dt), bool(gc[i])))
        n_stock += 1
    fp = str(get_database_manifest().path_for("feature_store"))
    conn = duck_connect(fp, read_only=False)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {PANEL}")
        conn.execute(
            f"CREATE TABLE {PANEL} (code VARCHAR, date VARCHAR, macd_golden_cross BOOLEAN, "
            "PRIMARY KEY (code, date))")
        if rows:
            conn.executemany(f"INSERT INTO {PANEL} VALUES (?,?,?)", rows)
        n = conn.execute(f"SELECT COUNT(*) FROM {PANEL}").fetchone()[0]
        n_gc = conn.execute(f"SELECT COUNT(*) FROM {PANEL} WHERE macd_golden_cross").fetchone()[0]
    finally:
        conn.close()
    return {"stocks": n_stock, "rows": n, "golden_cross_events": n_gc}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=_DATA_START)
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit-stocks", type=int, default=0)
    a = ap.parse_args()
    print(build(start=a.start, end=a.end, limit_stocks=a.limit_stocks))
