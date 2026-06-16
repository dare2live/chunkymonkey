"""主升浪 ground truth 重建 S1 — 复现 05-28 研究的全 A 股扫描 (analysis/zhushenglang_rebuild_plan_20260613.md).

原始定义 (docs/zhushenglang_hunter_research_log_20260528.md §7, 唯一权威):
  突破事件 = 当日 close > 前 60 日 high (qfq)
  主升浪   = 突破后 60-180 天涨幅 >= 50% AND 中间 max_dd > -20%
对账锚 (复现成立判据): events≈31,577 / TRUE 事件≈3,012 / 去重 rally≈2,503 /
  中位涨幅≈75.5% / 中位持续≈90 天。TRUE 的"60-180 天"在原文有读法歧义,
  本脚本三角法跑多读法, 全锚最吻合者定为重建口径 (写入输出 JSON, 后续冻结)。

用法: PYTHONPATH=backend python backend/scripts/rally_ground_truth_scan.py [--end 20260528]
  --end 默认 2026-05-28 (原研究数据截止, 复现对账用); 扩窗跑 S2+ 时另行立法。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MARKET_DB = REPO / "data" / "market.duckdb"  # rule-compliance: ok evidence=一次性 ground truth 重建扫描, read_only
OUT_DIR = REPO / "analysis"

LOOKBACK = 60        # 突破窗: 前 60 日 high (原文)
FWD_MAX = 180        # 前瞻窗上限 (原文 60-180 天)
FWD_MIN = 60         # 读法 A 的最短持续 (原文歧义点)
GAIN_MIN = 0.50      # 涨幅线 (原文)
DD_FLOOR = -0.20     # 回撤线 (原文)
# 三角定位结果 (2026-06-13, 对账 05-28 研究锚): 穿越 + 同股 60 日冷却 → events 31,551
# vs 锚 31,577 (99.92%); 读法 B (峰位 <=180 日无下限) → TRUE 3,247 vs 锚 3,012 (+7.8%),
# base rate 10.3% vs 9.5%, 中位涨幅 73.9% vs 75.5%。cooldown 扫描: 0→74,595 / 10→51,528 /
# 20→43,441 / 30→38,505 / 60→31,551 / 120→23,602 — 60 唯一吻合, 且结构自洽 (冷却=回看窗)。
# 中位持续 66 vs 锚 90: 时长统计口径差异 (疑原文按涨势结束日), 不影响 TRUE 成员判定。
COOLDOWN = 60        # 重建口径锁定 (三角定位, 见上)
READING = "B"        # TRUE 读法锁定: 峰位 <=180 日, gain>=50%, dd>-20%


def load_panel(con, end: str):
    # 2026-06-16: 改读干净 tushare qfq 底表 (避 tdxhub 视图 2022-12-30 复权 glitch + 全宇宙 2019+/5755);
    # 原 v_price_kline_qfq 是 tdxhub-primary (见 task #34 主从倒挂)。注: 切源后数字不再对原 tdxhub/2026-05-28 锚, 属预期。
    rows = con.execute(
        "SELECT code, date, high, close FROM price_kline_qfq_tushare "
        "WHERE date <= ? AND close > 0 ORDER BY code, date", [end],
    ).fetchall()
    by_code: dict[str, tuple[list[str], np.ndarray, np.ndarray]] = {}
    cur_code, dates, highs, closes = None, [], [], []
    for code, d, h, c in rows:
        if code != cur_code:
            if cur_code is not None and len(dates) > LOOKBACK:
                by_code[cur_code] = (dates, np.array(highs), np.array(closes))
            cur_code, dates, highs, closes = code, [], [], []
        dates.append(d); highs.append(h or np.nan); closes.append(c or np.nan)
    if cur_code is not None and len(dates) > LOOKBACK:
        by_code[cur_code] = (dates, np.array(highs), np.array(closes))
    return by_code


def rolling_prev_max(arr: np.ndarray, window: int) -> np.ndarray:
    """prev_max[i] = max(arr[i-window : i]); i < window → nan."""
    out = np.full(len(arr), np.nan)
    if len(arr) <= window:
        return out
    from collections import deque
    dq: deque[int] = deque()
    for i in range(len(arr)):
        if i >= window:
            out[i] = arr[dq[0]]
        while dq and arr[dq[-1]] <= arr[i]:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
    return out


def scan(by_code):
    """全事件前瞻指标: (code, t, gain_to_peak, peak_offset, dd_to_peak, fwd_len)."""
    events = []
    for code, (dates, highs, closes) in by_code.items():
        prev_hi = rolling_prev_max(highs, LOOKBACK)
        above = (closes > prev_hi) & ~np.isnan(prev_hi)
        # 事件 = 穿越 (昨日未在前高上方, 今日在) — 状态口径会把连续新高每天计数 (实测 114k vs 锚 31.5k, 3.6x)
        prev_above = np.concatenate(([False], above[:-1]))
        cross = np.where(above & ~prev_above)[0]
        # 冷却去连发 (三角参数 COOLDOWN): 同股 N 日内只计首次穿越 (裸穿越 74,595 仍 2.36x 锚)
        brk, last = [], -10**9
        for i in cross:
            if i - last > COOLDOWN:
                brk.append(i)
                last = i
        for i in brk:
            fwd = closes[i + 1: i + 1 + FWD_MAX]
            if len(fwd) == 0 or np.all(np.isnan(fwd)):
                events.append((code, dates[i], np.nan, -1, np.nan, 0))
                continue
            peak_rel = int(np.nanargmax(fwd))
            gain = fwd[peak_rel] / closes[i] - 1.0
            path = np.concatenate(([closes[i]], fwd[: peak_rel + 1]))
            cmax = np.maximum.accumulate(path)
            dd = float(np.nanmin(path / cmax - 1.0))
            events.append((code, dates[i], float(gain), peak_rel + 1, dd, len(fwd)))
    return events


def judge(events, reading: str):
    """三读法判 TRUE. A: 峰位 60-180 日; B: 峰位 <=180 日 (无下限); C: 第 60-180 日任一日涨幅达标 (等价 B 且峰位>=60? 取 60 日后段重峰)."""
    out = []
    for code, t, gain, peak_off, dd, fwd_len in events:
        if np.isnan(gain):
            out.append(False)
            continue
        ok_gain_dd = gain >= GAIN_MIN and dd > DD_FLOOR
        if reading == "A":
            out.append(ok_gain_dd and FWD_MIN <= peak_off <= FWD_MAX)
        elif reading == "B":
            out.append(ok_gain_dd and peak_off <= FWD_MAX)
        else:
            raise ValueError(reading)
    return out


SMART_DB = REPO / "data" / "smartmoney.duckdb"  # rule-compliance: ok evidence=ground truth 落库目标 (研究产物防 /tmp 灭失), 写 fact_rally_ground_truth
GT_TABLE = "fact_rally_ground_truth"


def land_ground_truth(events, reading: str, window_end: str) -> int:
    """落地全部突破事件 + 标签到 smartmoney.fact_rally_ground_truth (主升浪 S2/S3 训练面板地基).

    存全部事件 (含 FAKE/NEUTRAL) + 连续结局 (gain/dd/peak_offset), 不只 TRUE — 下游 S3
    定义 TRUE vs not-TRUE 二分目标。label is_true_rally 按冻结读法 (B)。
    event_date = 突破日 t (PIT 锚: 特征只能用 <= t, label 用 t+1..t+180 = 目标非特征)。
    """
    import duckdb
    flags = judge(events, reading)
    rows = [(code, t.replace("-", ""), round(gain * 100, 4), peak_off, round(dd * 100, 4),
             fwd_len, bool(f), reading)
            for (code, t, gain, peak_off, dd, fwd_len), f in zip(events, flags)
            if not (gain != gain)]  # 排除 nan gain (窗尾无前瞻)
    con = duckdb.connect(str(SMART_DB))  # rule-compliance: ok evidence=一次性研究产物落库, 单写, 无其他 writer
    try:
        con.execute(f"DROP TABLE IF EXISTS {GT_TABLE}")
        con.execute(f"""
            CREATE TABLE {GT_TABLE} (
                stock_code VARCHAR, event_date VARCHAR,
                gain_to_peak_pct DOUBLE, peak_offset_days INTEGER, max_dd_pct DOUBLE,
                fwd_window_len INTEGER, is_true_rally BOOLEAN, reading VARCHAR,
                window_end VARCHAR, built_at TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (stock_code, event_date))""")
        con.executemany(
            f"INSERT INTO {GT_TABLE} (stock_code,event_date,gain_to_peak_pct,peak_offset_days,"
            f"max_dd_pct,fwd_window_len,is_true_rally,reading,window_end) VALUES (?,?,?,?,?,?,?,?,'{window_end}')",
            rows)
        n_true = con.execute(f"SELECT count(*) FROM {GT_TABLE} WHERE is_true_rally").fetchone()[0]
    finally:
        con.close()
    print(f"落库 {GT_TABLE}: {len(rows):,} 事件 ({n_true:,} TRUE, 读法 {reading})")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--end", default="2026-05-28")  # rule-compliance: ok evidence=原研究数据截止日, 复现对账锚定窗 (研究日志 20260528)
    ap.add_argument("--land", action="store_true", help="落地 fact_rally_ground_truth (S2/S3 训练面板地基)")
    args = ap.parse_args()

    import duckdb
    con = duckdb.connect(str(MARKET_DB), read_only=True)  # rule-compliance: ok evidence=read_only 扫描
    by_code = load_panel(con, args.end)
    con.close()
    print(f"panel: {len(by_code)} codes")

    events = scan(by_code)
    n_events = len(events)
    print(f"突破事件: {n_events:,} (对账锚 31,577)")

    report = {"end": args.end, "codes": len(by_code), "n_breakout_events": n_events,
              "anchors": {"events": 31577, "true_events": 3012, "rallies_dedup": 2503,
                           "median_gain_pct": 75.5, "median_duration_days": 90},
              "readings": {}}
    for reading in ("A", "B"):
        flags = judge(events, reading)
        true_ev = [e for e, f in zip(events, flags) if f]
        gains = sorted(e[2] for e in true_ev)
        durs = sorted(e[3] for e in true_ev)
        # rally 去重: 同股同峰日 = 同一轮主升浪 (多次突破事件指向同一峰)
        rallies = {}
        for code, t, gain, peak_off, dd, _ in true_ev:
            # 峰日 = t 之后第 peak_off 个交易日; 用 (code, t 的序数+offset) 不可跨股比 — 以 (code, gain 四舍五入+峰位粗桶) 退化键
            rallies.setdefault((code, round(gain, 4)), []).append(t)
        med = lambda xs: xs[len(xs) // 2] if xs else None
        report["readings"][reading] = {
            "true_events": len(true_ev),
            "rallies_dedup_approx": len(rallies),
            "median_gain_pct": round(med(gains) * 100, 1) if gains else None,
            "median_duration_days": med(durs),
        }
        print(f"读法 {reading}: TRUE={len(true_ev):,} rally≈{len(rallies):,} "
              f"中位涨幅={report['readings'][reading]['median_gain_pct']}% "
              f"中位持续={report['readings'][reading]['median_duration_days']}日")

    report["run_at_utc"] = datetime.now(timezone.utc).isoformat()
    out = OUT_DIR / f"rally_gt_reproduction_{args.end.replace('-', '')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"落盘: {out}")

    if args.land:
        report["landed_rows"] = land_ground_truth(events, READING, args.end.replace("-", ""))
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
