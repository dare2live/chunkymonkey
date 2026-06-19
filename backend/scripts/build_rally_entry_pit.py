"""主升浪 GT entry-PIT 侧物化 — fact_rally_ground_truth -> fact_rally_entry_pit (只 PIT 入场态, 剥 outcome)。

owner=backend/config/rally_gt_columns.yaml + analysis/data_validation_backtest_plan_20260619.md。
缘起 (A0 地基止血 #c): GT 混存 PIT 入场态 + forward outcome (gain/peak/dd/bull_aligned)。结果倒推训练
  X 只能用 <=bottom 信息, outcome 当 X = leakage 死。本表剥出 entry-PIT 侧, 下游 JOIN fact_feature_panel
  ON (code, entry_signal_date) 取完整 PIT 因子向量; outcome 仍留 GT 表供 label/eval。

新列 fwd_complete: bottom_date + fwd_window_len 交易日后是否仍 <= 数据边缘 (price_kline max date)。
  False = 右删失 (forward 窗未完整观测), 训练/OOS 切分需谨慎 (负样本 generator + purge 用)。
  pre-calendar (2023 前) bottom 的 forward 窗早已完整 -> True。
PIT: entry_signal_date = bottom_date (决策点); 入场特征只 base_days (底前盘整); 无任何 forward 列。
"""
from __future__ import annotations

import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect  # noqa: E402

_MANIFEST = get_database_manifest()
MARKET_DB = str(_MANIFEST.path_for("market"))
SMARTMONEY_DB = str(_MANIFEST.path_for("smartmoney"))
SRC = "fact_rally_ground_truth"
DST = "fact_rally_entry_pit"


def _compute(episodes: list[tuple], trading_days: list[str], last_data_date: str) -> list[tuple]:
    """episodes=[(code, bottom_date, base_days, fwd_window_len, is_true_rally)] -> 加 fwd_complete 的行。"""
    cal_min = trading_days[0] if trading_days else None
    out: list[tuple] = []
    for code, bottom, base_days, fwd_len, is_rally in episodes:
        b = str(bottom)
        if cal_min is None or b < cal_min:
            fwd_complete = True                      # pre-calendar: forward 窗早已完整观测
        else:
            pos = bisect_right(trading_days, b)       # 第一个 > bottom 的交易日下标
            end_idx = pos + int(fwd_len) - 1          # fwd_len-th 交易日 (bottom 后第1日=pos)
            fwd_complete = (end_idx < len(trading_days)
                            and trading_days[end_idx] <= last_data_date)
        out.append((code, b, int(base_days), bool(fwd_complete), bool(is_rally), int(fwd_len)))
    return out


def main() -> int:
    mconn = connect(MARKET_DB, read_only=True)
    try:
        last_data_date = mconn.execute(
            "SELECT max(date) FROM price_kline_qfq_tushare").fetchone()[0]
    finally:
        mconn.close()
    print(f"[edge] price_kline 数据边缘 = {last_data_date}", flush=True)

    rconn = connect(SMARTMONEY_DB, read_only=True)
    try:
        trading_days = [r[0] for r in rconn.execute(
            "SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1 ORDER BY trade_date").fetchall()]
        episodes = rconn.execute(
            f"SELECT stock_code, bottom_date, base_days, fwd_window_len, is_true_rally "
            f"FROM {SRC} ORDER BY stock_code, bottom_date").fetchall()
    finally:
        rconn.close()
    print(f"[load] 交易日历 {len(trading_days)} 日 ({trading_days[0]}~{trading_days[-1]}), "
          f"GT {len(episodes):,} episode", flush=True)

    rows = _compute(episodes, trading_days, str(last_data_date))
    n_complete = sum(1 for r in rows if r[3])
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
            [(c, b, bd, fc, ir, fl, built) for (c, b, bd, fc, ir, fl) in rows])
        wconn.execute(f"CREATE INDEX idx_{DST}_date ON {DST}(entry_signal_date)")
        wconn.execute("CHECKPOINT")
        chk = wconn.execute(
            f"SELECT count(*), count(DISTINCT stock_code), "
            f"min(entry_signal_date), max(entry_signal_date), "
            f"sum(CASE WHEN fwd_complete THEN 1 ELSE 0 END) FROM {DST}").fetchone()
    finally:
        wconn.close()

    print(f"[done] {DST}: {chk[0]:,} episode / {chk[1]:,} 股 / {chk[2]}~{chk[3]} "
          f"| fwd_complete {chk[4]:,} ({chk[4]/chk[0]*100:.1f}%)")
    print(f"[contract] entry_signal_date=bottom_date(PIT锚); pit_feature=base_days; "
          f"outcome(gain/peak/dd/bull_aligned)留 GT 表禁做 X (rally_gt_columns.yaml)")
    if n_complete != chk[4]:
        print(f"[WARN] 计算 fwd_complete={n_complete} != 落库 {chk[4]}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
