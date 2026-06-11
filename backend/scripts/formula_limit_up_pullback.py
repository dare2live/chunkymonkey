#!/usr/bin/env python3
"""
涨停回调买入公式 — 裸公式 + 历史回测 + 参数扫描

模式:
  1. 放量大涨 (涨幅 >= pct_threshold, 量 >= vol_ratio × MA20)
  2. 凌厉缩量回调 (3-5 天, 快速不缓慢, 不跌破大涨日最低价)
  3. 收出十字星 (|close-open|/振幅 很小, 卖压耗尽)
  4. 十字星次日开盘买入
  5. 买后验证: 1-2 天内出现大涨, 且过程中不跌破首涨日最低价

Usage:
    PYTHONPATH=backend python backend/scripts/formula_limit_up_pullback.py
    PYTHONPATH=backend python backend/scripts/formula_limit_up_pullback.py --sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
MARKET_DB = DATA_DIR / "market.duckdb"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "analysis"

def _load_yaml_config() -> dict:
    cfg_path = CONFIG_DIR / "formula_limit_up_pullback.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}


DEFAULT_PARAMS = {
    "breakout_limit_ratio": 0.7,    # breakout 占涨停幅度比例 (主板 0.7×10%=7%, 创业板 0.7×20%=14%)
    "breakout_pct_min": 7.0,        # fallback: 不用 ratio 时的绝对阈值
    "breakout_vol_ratio": 1.5,
    "breakout_close_eq_high": False,

    # 前置形态: 先跌到近期低点, 再小涨 2-3 天
    "pre_pattern": True,
    "pre_low_lookback": 20,         # 近 N 天低点
    "pre_low_pct": 0.05,            # close 距低点 <= 5%
    "pre_recovery_min_days": 2,     # 小涨至少 N 天
    "pre_recovery_max_days": 5,     # 小涨不超过 N 天
    "pre_recovery_max_pct": 5.0,    # 小涨期间累计涨幅 <= 5% (不是大涨)

    "pullback_min_days": 3,
    "pullback_max_days": 5,
    "pullback_vol_shrink": 0.7,
    "pullback_above_breakout_low": True,

    "doji_body_ratio_max": 0.3,
    "doji_range_min": 0.005,

    "buy_offset": 1,

    "verify_rally": True,
    "verify_rally_pct": 5.0,
    "verify_rally_window": 2,

    "hold_days": [3, 5, 10, 15, 20],
    "tx_cost_bps": None,  # None = auto from paper_sim_config.yaml via get_default_tx_cost_bps()
}


def load_kline_data(conn, min_date: str | None = None, universe: set[str] | None = None) -> dict:
    """加载 K 线, 可选 universe 过滤 (排除 ST/退市/北交所)."""
    cfg = _load_yaml_config()
    if min_date is None:
        min_date = cfg.get("backtest", {}).get("start_date", "2023-01-01")  # from yaml: backtest.start_date
    sql = """
        SELECT code, date, open, high, low, close, volume, amount
        FROM price_kline_tdxhub
        WHERE freq='daily' AND adjust='qfq' AND date >= ?
        ORDER BY code, date
    """
    rows = conn.execute(sql, [min_date]).fetchall()
    if universe:
        rows = [r for r in rows if r[0] in universe]
    print(f"  loaded {len(rows):,} rows" + (f" (universe filtered: {len(universe)} stocks)" if universe else ""))

    stocks: dict[str, dict] = {}
    cur_code = None
    buf = []

    def flush(code, buf):
        if not buf:
            return
        stocks[code] = {
            "dates": [r[1] for r in buf],
            "open": np.array([r[2] for r in buf], dtype=np.float64),
            "high": np.array([r[3] for r in buf], dtype=np.float64),
            "low": np.array([r[4] for r in buf], dtype=np.float64),
            "close": np.array([r[5] for r in buf], dtype=np.float64),
            "volume": np.array([r[6] for r in buf], dtype=np.float64),
            "amount": np.array([r[7] for r in buf], dtype=np.float64),
        }

    for row in rows:
        code = row[0]
        if code != cur_code:
            if cur_code is not None:
                flush(cur_code, buf)
            cur_code = code
            buf = []
        buf.append(row)
    if cur_code is not None:
        flush(cur_code, buf)

    print(f"  {len(stocks)} stocks")
    return stocks


def _check_pre_pattern(i, close, low, params):
    """检查 breakout 前的形态: 先跌到近期低点 → 小涨 2-3 天."""
    lookback = params.get("pre_low_lookback", 20)
    low_pct = params.get("pre_low_pct", 0.05)
    rec_min = params.get("pre_recovery_min_days", 2)
    rec_max = params.get("pre_recovery_max_days", 5)
    rec_max_pct = params.get("pre_recovery_max_pct", 5.0)

    if i < lookback + rec_max:
        return False

    # 找近 lookback 天的最低收盘价
    seg_start = max(0, i - lookback - rec_max)
    seg_end = i  # 不含 breakout 当天
    recent_low = np.min(low[seg_start:seg_end])

    # 在 breakout 前 rec_min ~ rec_max 天内, 找到一个 "触底" 点
    # 然后从触底到 breakout 前一天, 要有小涨 (但不大涨)
    for offset in range(rec_min, rec_max + 1):
        bottom_i = i - offset
        if bottom_i < seg_start:
            continue

        # 触底: close 距近期低点 <= low_pct
        if close[bottom_i] > recent_low * (1 + low_pct):
            continue

        # 从 bottom_i+1 到 i-1 (breakout 前一天) 要小涨
        if bottom_i + 1 >= i:
            continue
        recovery_seg = close[bottom_i + 1: i]
        if len(recovery_seg) == 0:
            continue

        # 每天 close >= 前一天 close (至少大部分天阳线)
        prev_c = close[bottom_i: i - 1]
        up_days = np.sum(recovery_seg >= prev_c)
        if up_days < len(recovery_seg) * 0.5:
            continue

        # 累计涨幅不超过 rec_max_pct (小涨不是大涨)
        total_rec = (close[i - 1] / close[bottom_i] - 1) * 100
        if total_rec > rec_max_pct or total_rec < 0:
            continue

        return True

    return False


def detect_signals(dates, open_, high, low, close, volume, params, *, limit_up_pct: float = 0.10):
    """limit_up_pct: 该股票的涨停幅度 (主板 0.10, 创业板/科创板 0.20)."""
    n = len(close)
    if n < 30:
        return []

    breakout_ratio = params.get("breakout_limit_ratio", 0.7)  # from yaml: breakout 占涨停幅度的比例
    pct_min = breakout_ratio * limit_up_pct * 100  # 主板 7%, 创业板 14%
    vol_ratio = params["breakout_vol_ratio"]
    strict_seal = params["breakout_close_eq_high"]
    pb_min = params["pullback_min_days"]
    pb_max = params["pullback_max_days"]
    vol_shrink = params["pullback_vol_shrink"]
    above_low = params["pullback_above_breakout_low"]
    doji_body_max = params["doji_body_ratio_max"]
    doji_range_min = params["doji_range_min"]
    buy_offset = params["buy_offset"]
    use_pre = params.get("pre_pattern", False)

    prev_close = np.empty(n, dtype=np.float64)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    pct_change = (close / prev_close - 1.0) * 100.0

    vol_ma20 = np.full(n, np.nan)
    if n >= 20:
        kernel = np.ones(20) / 20.0
        vol_ma20[19:] = np.convolve(volume, kernel, mode="valid")

    signals = []
    i = 25

    while i < n:
        is_breakout = (
            pct_change[i] >= pct_min
            and np.isfinite(vol_ma20[i])
            and volume[i] >= vol_ratio * vol_ma20[i]
        )
        if strict_seal:
            is_breakout = is_breakout and abs(high[i] - close[i]) < close[i] * 0.001

        if not is_breakout:
            i += 1
            continue

        # 前置形态检查
        if use_pre and not _check_pre_pattern(i, close, low, params):
            i += 1
            continue

        breakout_i = i
        breakout_low = low[i]
        breakout_close = close[i]
        breakout_vol = volume[i]

        found = False
        last_gap = pb_max
        for gap in range(pb_min, min(pb_max + 1, n - breakout_i)):
            j = breakout_i + gap

            if above_low:
                seg_low = np.min(low[breakout_i + 1: j + 1])
                if seg_low < breakout_low * 0.99:
                    break

            pb_vol_mean = np.mean(volume[breakout_i + 1: j + 1])
            if pb_vol_mean > vol_shrink * breakout_vol:
                continue

            body = abs(close[j] - open_[j])
            shadow = high[j] - low[j]
            if shadow < doji_range_min * close[j]:
                continue
            body_ratio = body / shadow if shadow > 0 else 1.0
            if body_ratio > doji_body_max:
                continue

            buy_i = j + buy_offset
            if buy_i >= n:
                continue

            if above_low and low[buy_i] < breakout_low * 0.99:
                continue

            # 额外特征
            breakout_prev_close = prev_close[breakout_i]
            gain_at_breakout = breakout_close - breakout_prev_close
            gain_retained = (close[j] - breakout_prev_close) / gain_at_breakout if gain_at_breakout > 0 else 0
            # 量递减质量: 回调每天量是否递减
            pb_vols = volume[breakout_i + 1: j + 1]
            vol_mono_down = 0
            if len(pb_vols) >= 2:
                vol_mono_down = int(np.sum(pb_vols[1:] <= pb_vols[:-1])) / (len(pb_vols) - 1)
            # doji 当天量 vs breakout 量
            doji_vol_ratio = volume[j] / breakout_vol if breakout_vol > 0 else 0
            # 回调最低点距 breakout close 的幅度
            pb_min_close = np.min(close[breakout_i + 1: j + 1])
            pb_depth = (pb_min_close / breakout_close - 1) * 100

            signals.append({
                "breakout_idx": breakout_i,
                "breakout_date": str(dates[breakout_i]),
                "breakout_pct": round(float(pct_change[breakout_i]), 2),
                "breakout_vol_x": round(float(volume[breakout_i] / vol_ma20[breakout_i]), 2),
                "breakout_low": round(float(breakout_low), 3),
                "breakout_close": round(float(breakout_close), 3),
                "doji_idx": j,
                "doji_date": str(dates[j]),
                "doji_body_ratio": round(float(body_ratio), 3),
                "gap_days": gap,
                "buy_idx": buy_i,
                "buy_date": str(dates[buy_i]),
                "buy_price": float(open_[buy_i]),
                "gain_retained": round(float(gain_retained), 3),
                "vol_mono_down": round(float(vol_mono_down), 3),
                "doji_vol_ratio": round(float(doji_vol_ratio), 3),
                "pb_depth_pct": round(float(pb_depth), 2),
            })
            found = True
            last_gap = gap
            break

        i = breakout_i + last_gap + 1

    return signals


def backtest_signals(signals, code, close, open_, high, low, params):
    n = len(close)
    tx = params["tx_cost_bps"] / 10000.0
    do_verify = params.get("verify_rally", False)
    rally_pct = params.get("verify_rally_pct", 5.0)
    rally_window = params.get("verify_rally_window", 2)
    results = []

    for sig in signals:
        buy_i = sig["buy_idx"]
        buy_price = sig["buy_price"]
        brk_low = sig["breakout_low"]
        if buy_price <= 0:
            continue

        # 买后验证
        verified = False
        verify_day = None
        verify_pct = None
        for k in range(1, min(rally_window + 1, n - buy_i)):
            day_chg = (close[buy_i + k] / close[buy_i + k - 1] - 1) * 100
            if day_chg >= rally_pct:
                if k == 1:
                    verified = True
                    verify_day = 1
                    verify_pct = round(float(day_chg), 2)
                elif k == 2:
                    if low[buy_i + 1] >= brk_low * 0.99:
                        verified = True
                        verify_day = 2
                        verify_pct = round(float(day_chg), 2)
                break

        row = {
            "code": code,
            "breakout_date": sig["breakout_date"],
            "breakout_pct": sig["breakout_pct"],
            "breakout_vol_x": sig["breakout_vol_x"],
            "doji_date": sig["doji_date"],
            "doji_body_ratio": sig["doji_body_ratio"],
            "gap_days": sig["gap_days"],
            "buy_date": sig["buy_date"],
            "buy_price": round(buy_price, 3),
            "gain_retained": sig.get("gain_retained"),
            "vol_mono_down": sig.get("vol_mono_down"),
            "doji_vol_ratio": sig.get("doji_vol_ratio"),
            "pb_depth_pct": sig.get("pb_depth_pct"),
            "verified": verified,
            "verify_day": verify_day,
            "verify_pct": verify_pct,
        }

        for hp in params["hold_days"]:
            sell_i = buy_i + hp
            if sell_i >= n:
                row[f"ret_{hp}d"] = None
                continue
            ret = (close[sell_i] / buy_price - 1.0) - 2 * tx
            row[f"ret_{hp}d"] = round(float(ret) * 100, 2)

        if buy_i + 2 < n:
            max_h = np.max(high[buy_i + 1: min(buy_i + 11, n)])
            row["max_up_10d"] = round(float((max_h / buy_price - 1) * 100), 2)
            min_l = np.min(low[buy_i + 1: min(buy_i + 11, n)])
            row["max_dd_10d"] = round(float((min_l / buy_price - 1) * 100), 2)
        else:
            row["max_up_10d"] = None
            row["max_dd_10d"] = None

        results.append(row)

    return results


def print_stats(results, params, label=""):
    if label:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")

    n_total = len(results)
    n_verified = sum(1 for r in results if r["verified"])
    print(f"总信号: {n_total}, 验证通过 (1-2天内大涨): {n_verified} ({n_verified/n_total*100:.1f}%)" if n_total else "无信号")

    if not n_total:
        return {}

    stats = {}
    for subset_name, subset in [("全部信号", results), ("仅验证通过", [r for r in results if r["verified"]])]:
        if not subset:
            continue
        print(f"\n--- {subset_name} (n={len(subset)}) ---")
        for hp in params["hold_days"]:
            key = f"ret_{hp}d"
            vals = [r[key] for r in subset if r.get(key) is not None]
            if not vals:
                continue
            arr = np.array(vals)
            win = int(np.sum(arr > 0))
            m = float(np.mean(arr))
            md = float(np.median(arr))
            s = float(np.std(arr))
            wr = win / len(arr)
            print(f"  {hp:>2}d: mean={m:+.2f}%, median={md:+.2f}%, "
                  f"win={win}/{len(arr)} ({wr*100:.1f}%), std={s:.2f}%")
            stats[f"{subset_name}_{hp}d"] = {"mean": m, "win_rate": wr, "n": len(arr)}

        # 10 天极值
        ups = [r["max_up_10d"] for r in subset if r.get("max_up_10d") is not None]
        dds = [r["max_dd_10d"] for r in subset if r.get("max_dd_10d") is not None]
        if ups:
            print(f"  10d max_up: mean={np.mean(ups):.2f}%, max={np.max(ups):.2f}%")
            print(f"  10d max_dd: mean={np.mean(dds):.2f}%, worst={np.min(dds):.2f}%")

    # 按年分
    print(f"\n--- 按年 (5d, 全部) ---")
    by_year: dict[str, list] = {}
    for r in results:
        y = r["buy_date"][:4]
        if r.get("ret_5d") is not None:
            by_year.setdefault(y, []).append(r["ret_5d"])
    for y in sorted(by_year):
        arr = np.array(by_year[y])
        win = int(np.sum(arr > 0))
        print(f"  {y}: n={len(arr)}, mean={np.mean(arr):+.2f}%, win={win/len(arr)*100:.1f}%")

    # 按年 verified
    print(f"\n--- 按年 (5d, 仅验证) ---")
    by_year_v: dict[str, list] = {}
    for r in results:
        if not r["verified"]:
            continue
        y = r["buy_date"][:4]
        if r.get("ret_5d") is not None:
            by_year_v.setdefault(y, []).append(r["ret_5d"])
    for y in sorted(by_year_v):
        arr = np.array(by_year_v[y])
        win = int(np.sum(arr > 0))
        print(f"  {y}: n={len(arr)}, mean={np.mean(arr):+.2f}%, win={win/len(arr)*100:.1f}%")

    # 最近信号
    recent = sorted(results, key=lambda x: x["buy_date"], reverse=True)[:10]
    print(f"\n--- 最近 10 信号 ---")
    print(f"  {'code':<8} {'buy':<12} {'brk':<12} {'brk%':>5} {'gap':>3} "
          f"{'3d':>6} {'5d':>6} {'10d':>6} {'vfy':>4}")
    for r in recent:
        def fmt(v):
            return f"{v:+.1f}" if v is not None else "  -"
        print(f"  {r['code']:<8} {r['buy_date']:<12} {r['breakout_date']:<12} "
              f"{r['breakout_pct']:>5.1f} {r['gap_days']:>3} "
              f"{fmt(r.get('ret_3d')):>6} {fmt(r.get('ret_5d')):>6} "
              f"{fmt(r.get('ret_10d')):>6} "
              f"{'Y' if r['verified'] else 'N':>4}")

    return stats


def _load_clean_universe() -> set[str]:
    """加载 clean universe (排除 ST/退市/北交所), 与交易日历同等强度."""
    try:
        smart_conn = duckdb.connect(str(DATA_DIR / "smartmoney.duckdb"), read_only=True)
        from services.universe import get_active_universe
        universe = get_active_universe(smart_conn)
        smart_conn.close()
        return universe
    except (ImportError, OSError) as e:  # rule-compliance: ok evidence=universe-load-fallback
        print(f"  WARNING: universe filter failed ({e}), using all stocks")
        return set()


def _get_limit_up_pct(code: str) -> float:
    """按板块返回涨停幅度. 主板 10%, 创业板/科创板 20%."""
    from services.universe import get_limit_up_pct
    return get_limit_up_pct(code)


def run_scan(params: dict | None = None, quiet: bool = False):
    params = {**DEFAULT_PARAMS, **(params or {})}
    if not quiet:
        print(f"=== 回调十字星 scan ===")
        print(f"  breakout >= {params['breakout_pct_min']}%, vol >= {params['breakout_vol_ratio']}x, "
              f"pullback {params['pullback_min_days']}-{params['pullback_max_days']}d, "
              f"doji body <= {params['doji_body_ratio_max']}, "
              f"verify_rally={params.get('verify_rally')}")

    universe = _load_clean_universe()
    conn = duckdb.connect(str(MARKET_DB), read_only=True)
    stocks = load_kline_data(conn, universe=universe or None)

    # 强制前置审计 (与交易日历同强度)
    from services.backtest_preflight import enforce_backtest_preflight
    smart_conn = duckdb.connect(str(DATA_DIR / "smartmoney.duckdb"), read_only=True)
    enforce_backtest_preflight(
        stock_codes=list(stocks.keys()),
        conn=smart_conn,
        market_conn=conn,
        tx_cost_bps=params.get("tx_cost_bps"),  # None = auto from paper_sim_config.yaml
    )
    smart_conn.close()
    conn.close()

    all_results = []
    for code, data in stocks.items():
        sigs = detect_signals(
            data["dates"], data["open"], data["high"], data["low"],
            data["close"], data["volume"], params,
            limit_up_pct=_get_limit_up_pct(code),
        )
        if sigs:
            bt = backtest_signals(
                sigs, code, data["close"], data["open"],
                data["high"], data["low"], params,
            )
            all_results.extend(bt)

    if not quiet:
        stats = print_stats(all_results, params)

    # save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")  # Phase ψ.5 allowlist: 产物文件名时间戳非 trade_date
    out_path = OUTPUT_DIR / f"formula_limit_up_pullback_{ts}.json"
    summary = {
        "params": {k: (v if not isinstance(v, np.generic) else v.item()) for k, v in params.items()},
        "total_signals": len(all_results),
        "verified_signals": sum(1 for r in all_results if r["verified"]),
        "results": all_results,
    }
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    if not quiet:
        print(f"\n保存: {out_path}")

    return all_results


def run_sweep():
    """参数扫描找最优组合."""
    print("="*60)
    print("  参数扫描: 找最优组合")
    print("="*60)

    universe = _load_clean_universe()
    conn = duckdb.connect(str(MARKET_DB), read_only=True)
    stocks = load_kline_data(conn, universe=universe or None)
    conn.close()

    sweep_configs = [
        {"label": "A: base 无前置", "breakout_pct_min": 7.0, "pre_pattern": False},
        {"label": "B: base+前置形态", "breakout_pct_min": 7.0, "pre_pattern": True},
        {"label": "C: 涨停9.5%+前置", "breakout_pct_min": 9.5, "pre_pattern": True},
        {"label": "D: 高量2x+前置", "breakout_vol_ratio": 2.0, "pre_pattern": True},
        {"label": "E: 封板H=C+前置", "breakout_pct_min": 9.5, "breakout_close_eq_high": True, "pre_pattern": True},
        {"label": "F: 紧doji0.2+前置", "doji_body_ratio_max": 0.2, "pre_pattern": True},
        {"label": "G: 缩量0.5+前置", "pullback_vol_shrink": 0.5, "pre_pattern": True},
        {"label": "H: 高量2x+涨停9%+前置", "breakout_pct_min": 9.0, "breakout_vol_ratio": 2.0, "pre_pattern": True},
        {"label": "I: combo(9%+2x+0.2doji+前置)", "breakout_pct_min": 9.0, "breakout_vol_ratio": 2.0, "doji_body_ratio_max": 0.2, "pre_pattern": True},
        {"label": "J: 2-4d回调+前置", "pullback_min_days": 2, "pullback_max_days": 4, "pre_pattern": True},
    ]

    results_summary = []

    for cfg in sweep_configs:
        label = cfg.pop("label")
        params = {**DEFAULT_PARAMS, **cfg}

        all_results = []
        for code, data in stocks.items():
            sigs = detect_signals(
                data["dates"], data["open"], data["high"], data["low"],
                data["close"], data["volume"], params,
                limit_up_pct=_get_limit_up_pct(code),
            )
            if sigs:
                bt = backtest_signals(
                    sigs, code, data["close"], data["open"],
                    data["high"], data["low"], params,
                )
                all_results.extend(bt)

        n_total = len(all_results)
        n_verified = sum(1 for r in all_results if r["verified"])

        # 5d stats for all
        vals_5d = [r["ret_5d"] for r in all_results if r.get("ret_5d") is not None]
        mean_5d = float(np.mean(vals_5d)) if vals_5d else 0
        win_5d = float(np.mean(np.array(vals_5d) > 0)) if vals_5d else 0

        # 5d stats for verified only
        v_vals = [r["ret_5d"] for r in all_results if r["verified"] and r.get("ret_5d") is not None]
        v_mean = float(np.mean(v_vals)) if v_vals else 0
        v_win = float(np.mean(np.array(v_vals) > 0)) if v_vals else 0

        # 3d stats for verified
        v3_vals = [r["ret_3d"] for r in all_results if r["verified"] and r.get("ret_3d") is not None]
        v3_mean = float(np.mean(v3_vals)) if v3_vals else 0
        v3_win = float(np.mean(np.array(v3_vals) > 0)) if v3_vals else 0

        results_summary.append({
            "label": label,
            "n": n_total,
            "n_vfy": n_verified,
            "vfy%": n_verified / n_total * 100 if n_total else 0,
            "5d_mean": mean_5d,
            "5d_win": win_5d * 100,
            "v_5d_mean": v_mean,
            "v_5d_win": v_win * 100,
            "v_3d_mean": v3_mean,
            "v_3d_win": v3_win * 100,
        })

    print(f"\n{'='*100}")
    print(f"  {'配置':<40} {'N':>6} {'vfy':>5} {'vfy%':>5} "
          f"{'5d_m':>7} {'5d_w':>5} {'v5d_m':>7} {'v5d_w':>5} {'v3d_m':>7} {'v3d_w':>5}")
    print(f"{'='*100}")
    for s in results_summary:
        print(f"  {s['label']:<40} {s['n']:>6} {s['n_vfy']:>5} {s['vfy%']:>4.1f}% "
              f"{s['5d_mean']:>+6.2f}% {s['5d_win']:>4.1f}% "
              f"{s['v_5d_mean']:>+6.2f}% {s['v_5d_win']:>4.1f}% "
              f"{s['v_3d_mean']:>+6.2f}% {s['v_3d_win']:>4.1f}%")

    # save sweep
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")  # Phase ψ.5 allowlist: 产物文件名时间戳非 trade_date
    out_path = OUTPUT_DIR / f"formula_limit_up_pullback_sweep_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(results_summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nsweep 结果保存: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="涨停回调十字星公式")
    parser.add_argument("--sweep", action="store_true", help="参数扫描模式")
    parser.add_argument("--breakout-pct", type=float, default=None)
    parser.add_argument("--pullback-min", type=int, default=None)
    parser.add_argument("--pullback-max", type=int, default=None)
    parser.add_argument("--doji-body", type=float, default=None)
    parser.add_argument("--strict-seal", action="store_true")
    parser.add_argument("--vol-ratio", type=float, default=None)
    parser.add_argument("--no-verify", action="store_true", help="不做买后验证")
    args = parser.parse_args()

    if args.sweep:
        run_sweep()
        return

    params = dict(DEFAULT_PARAMS)
    if args.breakout_pct is not None:
        params["breakout_pct_min"] = args.breakout_pct
    if args.pullback_min is not None:
        params["pullback_min_days"] = args.pullback_min
    if args.pullback_max is not None:
        params["pullback_max_days"] = args.pullback_max
    if args.doji_body is not None:
        params["doji_body_ratio_max"] = args.doji_body
    if args.strict_seal:
        params["breakout_close_eq_high"] = True
    if args.vol_ratio is not None:
        params["breakout_vol_ratio"] = args.vol_ratio
    if args.no_verify:
        params["verify_rally"] = False

    run_scan(params)


if __name__ == "__main__":
    main()
