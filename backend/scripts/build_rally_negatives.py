"""主升浪 hard-negative 样本生成 -> fact_rally_entry_negative (结果倒推的对照组)。

owner=backend/services/rally_detect.py + analysis/data_validation_backtest_plan_20260619.md。
缘起 (A0 地基止血 #d): GT 9070 全正样本零负样本, 判别器无对照无法学"什么区分赢家入场点"。
框架=**hard-negative** (architect 定义 what-counts-as-correct): 负样本 = 同结构 pivot-low + 长底
  (base>=BASEMIN, 与正样本同 PIT setup) + forward 完整 + **未涨** (forward gain<GAIN)。holding PIT-setup
  恒定 -> 隔离"涨不涨"信号 (非全市场随机=只学"是不是低点"); 正是用户"从赢家反推前兆"的对照组。

无锁: 只读 K线(market) + GT/日历(smartmoney), 不碰 tushare_raw (续拉写锁)。
ST 排除: 留消费侧 PIT 硬门 (is_st_on, 与 universe 硬门一致; ST 是时变量不可一刀切删股, 见 CLAUDE §4.5)。
purge: 同股 GT 正样本 bottom 前后 MAXFWD 根内的 pivot 不取 (forward 窗与正样本重叠=污染)。
PIT: 入场特征只 base_days (底前盘整); 候选 pivot ±win 确认是 label 侧含 forward, 训练特征仍 <=i。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.data_loaders import load_kline  # noqa: E402
from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.rally_detect import (  # noqa: E402
    BASEMIN,
    BASE_LOOKBACK,
    GAIN,
    LOWWIN,
    MAXFWD,
    base_days_count,
    forward_complete,
    forward_max_gain,
    is_pivot_low,
)
from services.universe import is_active_a_share  # noqa: E402

_MANIFEST = get_database_manifest()
MARKET_DB = str(_MANIFEST.path_for("market"))
SMARTMONEY_DB = str(_MANIFEST.path_for("smartmoney"))
DST = "fact_rally_entry_negative"
START = "2019-01-01"  # rule-compliance: ok evidence=与 K线 tushare-qfq / GT 同起点


def _detect_stock_negatives(code, bars, pos_idx, trading_days, last_data_date):
    """单股 hard-negative pivot: 长底+forward完整+未涨, 排正样本及其前后 MAXFWD 根 (purge)。"""
    dates, highs, lows, closes = bars["date"], bars["high"], bars["low"], bars["close"]
    n = len(closes)
    out = []
    i = max(LOWWIN, 60)
    while i < n - MAXFWD // 5:   # 末端 forward 太短跳过 (与正样本 MINDUR 区相称)
        if not is_pivot_low(lows, i):
            i += 1
            continue
        # purge: 同股正样本 bottom 前后 MAXFWD 根内 (forward 窗重叠)
        if any(abs(i - j) < MAXFWD for j in pos_idx):
            i += 1
            continue
        base = base_days_count(closes, i, lows[i], BASE_LOOKBACK)
        if base < BASEMIN:
            i += 1
            continue
        if not forward_complete(str(dates[i]), trading_days, last_data_date, MAXFWD):
            i += 1
            continue
        gain = forward_max_gain(highs, lows, i, MAXFWD)
        if gain is None or gain >= GAIN:   # 涨了(>=GAIN)=正样本不是负样本; 无前瞻=不可判
            i += 1
            continue
        out.append((code, str(dates[i]), int(base)))
        i += LOWWIN   # 同股负样本间隔 >=LOWWIN 根, 防贴邻近重复 pivot
    return out


def main() -> int:
    mconn = connect(MARKET_DB, read_only=True)
    try:
        last_data_date = str(mconn.execute("SELECT max(date) FROM price_kline_qfq_tushare").fetchone()[0])
    finally:
        mconn.close()

    rconn = connect(SMARTMONEY_DB, read_only=True)
    try:
        trading_days = [r[0] for r in rconn.execute(
            "SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1 ORDER BY trade_date").fetchall()]
        gt_rows = rconn.execute(
            "SELECT stock_code, CAST(bottom_date AS VARCHAR) FROM fact_rally_ground_truth").fetchall()
    finally:
        rconn.close()
    pos_by_code: dict[str, set[str]] = {}
    for code, bd in gt_rows:
        pos_by_code.setdefault(code, set()).add(bd)
    print(f"[load] 数据边缘 {last_data_date}, 交易日 {len(trading_days)}, GT 正样本 {len(gt_rows):,} / {len(pos_by_code):,}股", flush=True)

    by_code = load_kline(START, None, 0)
    print(f"[load] K线 {len(by_code):,} 股 (扫 pivot hard-negative)", flush=True)

    negatives = []
    for code, bars in by_code.items():
        if not is_active_a_share(code):
            continue
        pos_dates = pos_by_code.get(code, set())
        dates = bars["date"]
        pos_idx = [k for k, d in enumerate(dates) if str(d) in pos_dates]
        negatives.extend(_detect_stock_negatives(code, bars, pos_idx, trading_days, last_data_date))
    n_stock = len({c for c, _, _ in negatives})
    print(f"[build] hard-negative {len(negatives):,} / {n_stock:,}股 "
          f"(pos:neg = 9070:{len(negatives)} ≈ 1:{len(negatives)/9070:.1f})", flush=True)

    built = datetime.now(timezone.utc).isoformat()
    wconn = connect(SMARTMONEY_DB, read_only=False)
    try:
        wconn.execute(f"DROP TABLE IF EXISTS {DST}")
        wconn.execute(
            f"CREATE TABLE {DST} ("
            "stock_code VARCHAR NOT NULL, entry_signal_date DATE NOT NULL, "
            "base_days INTEGER NOT NULL, fwd_complete BOOLEAN NOT NULL, "
            "is_true_rally BOOLEAN NOT NULL, fwd_window_len INTEGER NOT NULL, "
            "built_at TIMESTAMP NOT NULL, PRIMARY KEY (stock_code, entry_signal_date))")
        wconn.executemany(
            f"INSERT INTO {DST} VALUES (?,?,?,?,?,?,?)",
            [(c, d, bd, True, False, MAXFWD, built) for (c, d, bd) in negatives])
        wconn.execute(f"CREATE INDEX idx_{DST}_date ON {DST}(entry_signal_date)")
        wconn.execute("CHECKPOINT")
        chk = wconn.execute(
            f"SELECT count(*), count(DISTINCT stock_code), min(entry_signal_date), max(entry_signal_date) FROM {DST}").fetchone()
    finally:
        wconn.close()
    print(f"[done] {DST}: {chk[0]:,} hard-negative / {chk[1]:,}股 / {chk[2]}~{chk[3]} "
          f"(全 fwd_complete + 长底base>={BASEMIN} + 未涨<{GAIN*100:.0f}% + purge同股正样本±{MAXFWD}根)")
    print(f"[contract] is_true_rally=False; entry_signal_date PIT锚; base_days PIT特征; "
          f"ST 留消费侧 PIT 硬门 (is_st_on); 下游 UNION fact_rally_entry_pit(正) 训练判别器")
    return 0


if __name__ == "__main__":
    sys.exit(main())
