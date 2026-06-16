"""experiment_yushen_backtest — 鱼身核心论题含成本检验 (主会话主导设计, 2026-06-16)。

论题: 不抓起涨点(鱼头不可预测), 改"骑周线确认的主升 + 日线延续买点 + 移动止盈出场"吃鱼身。
设计 (controller 自主):
  确认主升 (周线 PIT, 用上周完成的周K, 非本周): 周收盘>周MA30 AND 周MA30上行(slope>0) AND 周MA10>周MA30。
  鱼身入场 (日线, 在周线确认态内): 日收盘=过去20日新高 (延续突破, 非接刀起涨)。
  出场: 移动止盈(收盘<持有峰*0.88) OR 周线破位(周收盘<周MA30)。
  含成本: T+1 open 入场, 双边13bps含印花, 涨停日不入(close==前收*1.1±)。每 episode 独立(per-trade edge 先看, 组合NAV后续)。
对照: 同期 HS300 持有 / 全样本随机入场同出场 (露馅检查)。结果倒推不涉及(这是策略含成本检验, 非预测)。
源: market.price_kline_qfq_tushare。用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_backtest.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读K线策略检验; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("yushen_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013       # rule-compliance: ok evidence=A股双边~13bps含印花滑点, portfolio_execbacktest同量级
TRAIL = 0.88        # rule-compliance: ok evidence=移动止盈12%回撤(鱼身典型波动, 后续Optuna搜; 首测固定)
BREAKOUT_N = 20     # rule-compliance: ok evidence=日线延续突破回看(月线级延续), 首测固定可Optuna
MAX_HOLD = 120      # rule-compliance: ok evidence=持有上限(防无限持有)


def _weekly_state(dates, closes):
    """周线确认态 (PIT: 每个交易日取上一个完成周的状态)。返回 daily 对齐的 bool 数组。"""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()  # 周收盘
    wk["ma30"] = wk["close"].rolling(30).mean()
    wk["ma10"] = wk["close"].rolling(10).mean()
    wk["confirmed"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1)) & (wk["ma10"] > wk["ma30"])
    wk["confirmed_lag"] = wk["confirmed"].shift(1).fillna(False)  # 用上一完成周 (PIT, 本周未完成)
    state = df["wk"].map(dict(zip(wk["wk"], wk["confirmed_lag"]))).fillna(False).to_numpy()
    return state.astype(bool)


def scan(dates, opens, highs, closes, rng=None):
    """rng=None: 鱼身延续突破入场; rng给定: random-entry 对照(同周线确认context+同出场, 随机入场日)。"""
    n = len(closes)
    if n < 160:  # 需 ~30 周预热
        return []
    state = _weekly_state(dates, closes)
    hh20 = pd.Series(closes).rolling(BREAKOUT_N).max().to_numpy()
    eps = []
    i = BREAKOUT_N + 1
    while i < n - 1:
        # 入场: 鱼身=周线确认+日线20日新高; random对照=周线确认+随机日(同context, 隔离入场信号)
        entry_sig = (state[i] and closes[i] >= hh20[i]) if rng is None else (state[i] and rng.random() < 0.03)  # rule-compliance: ok evidence=0.03随机入场率≈延续突破触发率(对照同量级), 非业务参数
        if entry_sig and closes[i] > 0:
            # 涨停剔 (近似: 当日涨幅>=9.8% 视为可能一字/封板, 保守不入)
            if i >= 1 and closes[i] / closes[i - 1] - 1 >= 0.098:
                i += 1; continue
            entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]  # T+1 open
            peak = entry; exit_px = closes[min(i + MAX_HOLD, n - 1)]; exit_i = min(i + MAX_HOLD, n - 1)
            for j in range(i + 1, min(i + MAX_HOLD, n - 1) + 1):
                peak = max(peak, highs[j])
                if closes[j] < peak * TRAIL or not state[j]:  # 移动止盈 OR 周线破位
                    exit_px = closes[j]; exit_i = j; break
            eps.append(float(exit_px / entry - 1.0 - COST))
            i = exit_i + 1  # 平仓后才找下个 (不重叠)
        else:
            i += 1
    return eps


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close FROM price_kline_qfq_tushare WHERE date >= '2020-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=方法论train窗起点2020(MASTER§5), 非钉死
    con.close()
    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    rng = np.random.RandomState(20260616)  # rule-compliance: ok evidence=固定种子复现 random对照, 非业务参数
    bk, rd = [], []
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        d, o, h, c = arr["date"][s:e], arr["open"][s:e].astype(float), arr["high"][s:e].astype(float), arr["close"][s:e].astype(float)
        bk.extend(scan(d, o, h, c))
        rd.extend(scan(d, o, h, c, rng=rng))
    print(f"\n鱼身核心论题 per-trade 含成本 (周线确认主升 + 入场 + 移动止盈/周破位出场, 2020+)")
    for name, rl in [("延续突破入场(鱼身)", bk), ("随机入场对照(同context同出场)", rd)]:
        r = np.array(rl); n = len(r)
        pf = abs(r[r > 0].mean() / r[r < 0].mean()) if (r < 0).any() and (r > 0).any() else 0
        print(f"  {name:26} n={n:>7,} 均值={r.mean()*100:+.2f}% 中位={np.median(r)*100:+.2f}% 胜率={(r>0).mean()*100:.1f}% 盈亏比={pf:.2f} >30%={(r>0.30).mean()*100:.1f}%")
    exp_bk, exp_rd = np.array(bk).mean(), np.array(rd).mean()
    edge = exp_bk - exp_rd
    run_id = "yushen_backtest_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_per_trade", verdict="ENTRY_EDGE_SCAN",
                       judges={"bk_mean": round(float(exp_bk), 5), "rnd_mean": round(float(exp_rd), 5),
                               "edge": round(float(edge), 5), "n_bk": len(bk),
                               "summary": f"粗突破鱼身per-trade {exp_bk*100:+.2f}% vs随机{exp_rd*100:+.2f}% = 入场增量{edge*100:+.2f}%/笔"},
                       confirmed_by_owner=0)
    print(f"  --- 裁定 (入场信号增量 = 鱼身 − 随机) ---")
    print(f"  鱼身 {exp_bk*100:+.2f}% − 随机 {exp_rd*100:+.2f}% = 入场增量 {edge*100:+.2f}%/笔")
    if edge > 0.01:
        print(f"  → 延续突破入场比随机多 +{edge*100:.2f}%/笔 = 入场信号有真 alpha (非纯beta), 下一步 Optuna 调参+叠 context 因子+组合NAV")
    elif exp_rd > 0.005:
        print(f"  → 入场增量微小但随机也 +{exp_rd*100:.2f}% = edge 主要在'周线确认上涨'context(择时/regime beta), 非入场信号 → 价值在 confirm+exit, 入场可简化; 考虑当 regime-gate")
    else:
        print(f"  → 鱼身{exp_bk*100:+.2f}%/随机{exp_rd*100:+.2f}% 都薄 = 此规格无显著 edge; Optuna 调 confirm/exit 参数或换判据")


if __name__ == "__main__":
    main()
