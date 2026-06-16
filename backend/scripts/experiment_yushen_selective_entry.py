"""experiment_yushen_selective_entry — 鱼身入场信号有效性裁决 (主会话主导, 2026-06-16)。

决定性问题: 粗突破入场(见20日新高就追)random对照证明只比随机多 +0.13%/笔 = 入场几乎无 alpha。
研究日志声称真 edge 在"二次突破/回调确认"(突破日 3.5% → 二次突破 42.3%)。本实验用同一出场+成本+
random 对照, 量三种入场各自"比随机的增量":
  A 粗突破:   周线确认 + 日收盘>=20日新高 (基线, 已知 +0.13%)
  B 二次突破: 60日新高突破(出箱) → 浅回调不破位+缩量回踩 → 再破前峰(二次突破)入场 (结构化, 无拟合参数)
  C 随机:     周线确认 + 随机日 (同 context, 隔离入场信号)
裁决: 若 B 比随机增量 >> A(+0.13%), 鱼身价格行为入场论题活, 继续调参+因子过滤; 若 B 也微小,
  价格行为入场判死, 转 stage-conditional 因子排名 (突破候选池内用因子/筹码/预期排名)。
PIT: 周线用上一完成周(非本周); 二次突破全用 <=当日 K线; T+1 open 入场; 涨停日不入; 双边13bps。
不涉结果倒推(这是固定结构规则的入场信号检验, 非预测/拟合)。OOS 切 2025-06 单列(防 look-ahead)。
源: market.price_kline_qfq_tushare。用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_selective_entry.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读K线入场信号检验; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("yushen_selective")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013        # rule-compliance: ok evidence=A股双边~13bps含印花滑点, 同 yushen_backtest
TRAIL = 0.88         # rule-compliance: ok evidence=移动止盈12%回撤, 同 yushen_backtest (出场口径不变, 只换入场)
MAX_HOLD = 120       # rule-compliance: ok evidence=持有上限, 同
BREAKOUT_N = 20      # rule-compliance: ok evidence=A 粗突破日线延续回看, 同 yushen_backtest
BASE_N = 60          # rule-compliance: ok evidence=B 首突破出箱回看(季度级底盘), 结构常数非拟合
RETR = 0.08          # rule-compliance: ok evidence=B 回调判据(从峰回撤>=8%算一次回调), 结构常数非拟合
HOLD_TOL = 0.05      # rule-compliance: ok evidence=B 不破位容差(回调不跌破突破位*0.95), 结构常数非拟合
MAX_BASE = 60        # rule-compliance: ok evidence=B 二次突破须在首突破后60交易日内完成, 结构常数非拟合
OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5监督式范式), 非钉死规避


def _weekly_state(dates, closes):
    """周线确认态 (PIT: 每交易日取上一完成周状态)。"""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()
    wk["ma30"] = wk["close"].rolling(30).mean()
    wk["ma10"] = wk["close"].rolling(10).mean()
    wk["confirmed"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1)) & (wk["ma10"] > wk["ma30"])
    wk["confirmed_lag"] = wk["confirmed"].shift(1).fillna(False)
    return df["wk"].map(dict(zip(wk["wk"], wk["confirmed_lag"]))).fillna(False).to_numpy().astype(bool)


def _exit_ret(opens, highs, closes, state, i, n):
    """从信号日 i 入场(T+1 open), 移动止盈/周破位出场, 返回含成本收益 + 出场索引。"""
    entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
    peak = entry
    exit_px = closes[min(i + MAX_HOLD, n - 1)]
    exit_i = min(i + MAX_HOLD, n - 1)
    for j in range(i + 1, min(i + MAX_HOLD, n - 1) + 1):
        peak = max(peak, highs[j])
        if closes[j] < peak * TRAIL or not state[j]:
            exit_px = closes[j]
            exit_i = j
            break
    return float(exit_px / entry - 1.0 - COST), exit_i


def _limit_up(closes, i):
    return i >= 1 and closes[i] / closes[i - 1] - 1 >= 0.098


def scan_crude(dates, opens, highs, closes, vols, state, hh20):
    """A 粗突破: 周线确认 + 20日新高。"""
    n = len(closes)
    out = []
    i = BREAKOUT_N + 1
    while i < n - 1:
        if state[i] and closes[i] >= hh20[i] and closes[i] > 0 and not _limit_up(closes, i):
            r, exit_i = _exit_ret(opens, highs, closes, state, i, n)
            out.append((str(dates[i + 1]), r))
            i = exit_i + 1
        else:
            i += 1
    return out


def scan_second_breakout(dates, opens, highs, closes, vols, state):
    """B 二次突破: 出箱突破 → 浅回调不破位+缩量 → 再破前峰入场。状态机, 全 PIT。"""
    n = len(closes)
    if n < BASE_N + 5:
        return []
    hh_base = pd.Series(closes).rolling(BASE_N).max().to_numpy()
    out = []
    i = BASE_N + 1
    while i < n - 1:
        # 1) 首突破: 周线确认 + 收盘=60日新高 (出箱)
        if not (state[i] and closes[i] >= hh_base[i] and closes[i] > 0):
            i += 1
            continue
        b1 = i
        brk_level = closes[b1]
        vol_brk = vols[max(b1 - 2, 0):b1 + 1].mean() if vols is not None else 0.0
        peak = closes[b1]
        pulled_back = False
        j = b1 + 1
        entered = False
        # complexity: 内层被 MAX_BASE(60) 限界 → 整体 O(n*60)=O(n) per stock, 非 O(n^2); 状态机(突破→回调→二次突破)本质顺序, 不可两指针化
        while j < min(b1 + MAX_BASE, n - 1):
            peak = max(peak, highs[j])
            # 破位则模式失败, 从这里重新扫
            if closes[j] < brk_level * (1 - HOLD_TOL):
                i = j
                break
            # 2) 回调: 从峰回撤>=RETR 且仍站在突破位上方
            if not pulled_back and closes[j] <= peak * (1 - RETR) and closes[j] > brk_level * (1 - HOLD_TOL):
                pulled_back = True
                pb_lo = j
            # 3) 二次突破: 回调后再破前峰 + 周线仍确认 (缩量: 回调段量 < 突破段量)
            if pulled_back and closes[j] >= peak and state[j] and not _limit_up(closes, j):
                vol_pb = vols[pb_lo:j + 1].mean() if (vols is not None and j > pb_lo) else vol_brk
                if vols is None or vol_pb <= vol_brk * 1.0:  # 缩量回踩: 回调段均量不放大
                    r, exit_i = _exit_ret(opens, highs, closes, state, j, n)
                    out.append((str(dates[j + 1]), r))
                    i = exit_i + 1
                    entered = True
                    break
            j += 1
        if not entered:
            # 未在窗口内触发二次突破 → 跳过本次首突破继续 (i 可能已被破位重置)
            if i == b1:
                i = b1 + 1
    return out


def scan_random(dates, opens, highs, closes, state, rng, rate):
    """C 随机对照: 周线确认 + 随机日 (同 context, 隔离入场信号)。"""
    n = len(closes)
    out = []
    i = BREAKOUT_N + 1
    while i < n - 1:
        if state[i] and closes[i] > 0 and rng.random() < rate and not _limit_up(closes, i):
            r, exit_i = _exit_ret(opens, highs, closes, state, i, n)
            out.append((str(dates[i + 1]), r))
            i = exit_i + 1
        else:
            i += 1
    return out


def _stats(trades, oos_cut):
    """trades=[(entry_date_str, ret)]; 返回 (全样本 stats, OOS stats)。"""
    def agg(rl):
        r = np.array([x[1] for x in rl])
        n = len(r)
        if n == 0:
            return dict(n=0, mean=0, med=0, win=0, pf=0, p30=0)
        pf = abs(r[r > 0].mean() / r[r < 0].mean()) if (r < 0).any() and (r > 0).any() else 0
        return dict(n=n, mean=r.mean(), med=float(np.median(r)), win=(r > 0).mean(), pf=pf, p30=(r > 0.30).mean())
    alls = agg(trades)
    oos = agg([t for t in trades if t[0] >= oos_cut])
    return alls, oos


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date >= '2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留30周线预热, 测2020+(MASTER§5)
    con.close()
    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    rng = np.random.RandomState(20260616)  # rule-compliance: ok evidence=固定种子复现random对照, 非业务参数

    crude, second, rand = [], [], []
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        d = arr["date"][s:e]
        o = arr["open"][s:e].astype(float)
        h = arr["high"][s:e].astype(float)
        c = arr["close"][s:e].astype(float)
        v = arr["volume"][s:e].astype(float)
        if len(c) < 160:
            continue
        state = _weekly_state(d, c)
        hh20 = pd.Series(c).rolling(BREAKOUT_N).max().to_numpy()
        crude.extend(scan_crude(d, o, h, c, v, state, hh20))
        second.extend(scan_second_breakout(d, o, h, c, v, state))
    # random rate 匹配二次突破频率 (per-trade mean 与频率无关, 但匹配 n 求公平)
    confirmed_days = sum(int(_weekly_state(arr["date"][int(first[ci]):int(last[ci])], arr["close"][int(first[ci]):int(last[ci])].astype(float)).sum()) for ci in range(len(uniq)))
    rate = min(0.05, max(0.005, len(second) / max(confirmed_days, 1)))  # rule-compliance: ok evidence=随机入场率匹配二次突破频率(对照同量级), 派生非业务参数
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        d = arr["date"][s:e]
        o = arr["open"][s:e].astype(float)
        h = arr["high"][s:e].astype(float)
        state = _weekly_state(d, c)
        rand.extend(scan_random(d, o, h, c, state, rng, rate))

    print(f"\n鱼身入场信号有效性裁决 (周线确认 context 内, 同移动止盈/周破位出场, 含成本13bps)")
    print(f"  {'入场':14} {'n':>8} {'均值':>8} {'中位':>8} {'胜率':>7} {'盈亏比':>7} {'>30%':>7} | OOS(2025-06+) 均值/胜率/n")
    rows = {}
    for name, tr in [("A粗突破(20高)", crude), ("B二次突破", second), ("C随机对照", rand)]:
        alls, oos = _stats(tr, OOS_CUT)
        rows[name] = (alls, oos)
        print(f"  {name:14} {alls['n']:>8,} {alls['mean']*100:>+7.2f}% {alls['med']*100:>+7.2f}% {alls['win']*100:>6.1f}% {alls['pf']:>7.2f} {alls['p30']*100:>6.1f}% | {oos['mean']*100:>+6.2f}% / {oos['win']*100:>5.1f}% / {oos['n']:,}")

    rnd_mean = rows["C随机对照"][0]["mean"]
    crude_inc = rows["A粗突破(20高)"][0]["mean"] - rnd_mean
    second_inc = rows["B二次突破"][0]["mean"] - rnd_mean
    rnd_oos = rows["C随机对照"][1]["mean"]
    second_oos_inc = rows["B二次突破"][1]["mean"] - rnd_oos

    # 小样本显著性: bootstrap 增量分布 (二次突破 − 随机), 报 p(增量>0) + 5% 下界
    def boot_inc(b_tr, r_tr, cut=None, iters=4000):  # rule-compliance: ok evidence=4000次bootstrap重采样, 统计常数非业务参数
        b = np.array([x[1] for x in b_tr if (cut is None or x[0] >= cut)])
        r = np.array([x[1] for x in r_tr if (cut is None or x[0] >= cut)])
        if len(b) < 10 or len(r) < 10:
            return None
        brng = np.random.RandomState(424242)  # rule-compliance: ok evidence=固定种子复现bootstrap, 非业务参数
        diffs = np.array([brng.choice(b, len(b)).mean() - brng.choice(r, len(r)).mean() for _ in range(iters)])
        return dict(p_pos=float((diffs > 0).mean()), lo5=float(np.percentile(diffs, 5)), med=float(np.median(diffs)))

    bt_all = boot_inc(second, rand)
    bt_oos = boot_inc(second, rand, cut=OOS_CUT)
    print(f"\n  --- 裁决 (入场信号增量 = 入场 − 随机, 隔离 regime/beta) ---")
    print(f"  A 粗突破增量 = {crude_inc*100:+.2f}%/笔  (已知基线, 近无 alpha)")
    print(f"  B 二次突破增量(全) = {second_inc*100:+.2f}%/笔   B二次突破增量(OOS) = {second_oos_inc*100:+.2f}%/笔")
    if bt_all:
        print(f"  bootstrap 全样本: p(增量>0)={bt_all['p_pos']:.3f}  5%下界={bt_all['lo5']*100:+.2f}%  中位={bt_all['med']*100:+.2f}%")
    if bt_oos:
        print(f"  bootstrap OOS  : p(增量>0)={bt_oos['p_pos']:.3f}  5%下界={bt_oos['lo5']*100:+.2f}%  中位={bt_oos['med']*100:+.2f}%")
    verdict = ""
    if second_inc > 0.02 and second_inc > crude_inc * 2 and second_oos_inc > 0:
        verdict = f"B二次突破增量 {second_inc*100:+.2f}% >> A {crude_inc*100:+.2f}% 且OOS同向 → 价格行为入场论题活: 下一步 Optuna 调 BASE_N/RETR/出场 + 叠 context 因子 + 组合NAV"
    elif second_inc <= crude_inc + 0.005:
        verdict = f"B二次突破增量 {second_inc*100:+.2f}% ≈ A粗突破, 都贴随机 → 价格行为入场判死(R1墙): edge 全在'周线确认'beta, 转 stage-conditional 因子排名(突破候选池内用因子/筹码/预期排名)"
    else:
        verdict = f"B二次突破增量 {second_inc*100:+.2f}% 中等(>A 但 <2%): 弱信号, 须叠 context 因子放大才可能过 KPI; 单靠价格行为不足"
    print(f"  → {verdict}")

    run_id = "yushen_selective_entry_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {
        "crude_inc": round(crude_inc, 5), "second_inc_all": round(second_inc, 5), "second_inc_oos": round(second_oos_inc, 5),
        "rnd_mean": round(rnd_mean, 5), "n_second": rows["B二次突破"][0]["n"], "n_second_oos": rows["B二次突破"][1]["n"],
        "boot_all_p_pos": (bt_all or {}).get("p_pos"), "boot_oos_p_pos": (bt_oos or {}).get("p_pos"),
        "summary": f"二次突破入场增量(全){second_inc*100:+.2f}%/(OOS){second_oos_inc*100:+.2f}% vs 粗突破{crude_inc*100:+.2f}%; n_second={rows['B二次突破'][0]['n']}(小样本)",
    }
    with open_store() as store:
        record_verdict(
            store,
            run_id=run_id,
            family="yushen_entry_alpha",
            verdict="ENTRY_ALPHA_SCAN",
            judges=judges,
            confirmed_by_owner=0,
        )
    print(f"\n  [experiment_store] 已留档 family=yushen_entry_alpha run_id={run_id} (confirmed_by_owner=0)")


if __name__ == "__main__":
    main()
