#!/usr/bin/env python3
"""
回调十字星 Optuna 寻优 — 分维度逐个优化 → 综合

Round 1: 四轮单目标 (win_rate / mean_ret / sharpe / profit_loss_ratio) × 100 trials
Round 2: 综合 composite_score × 200 trials (缩窄空间)
Round 3: 逐年鲁棒性验证

Usage:
    PYTHONPATH=backend python backend/scripts/optuna_pullback_doji.py
    PYTHONPATH=backend python backend/scripts/optuna_pullback_doji.py --round r1_winrate --trials 100
    PYTHONPATH=backend python backend/scripts/optuna_pullback_doji.py --round all
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date as dt_date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import optuna
import yaml

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
MARKET_DB = DATA_DIR / "market.duckdb"
SMART_DB = DATA_DIR / "smartmoney.duckdb"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "analysis"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.formula_limit_up_pullback import detect_signals, load_kline_data, _load_yaml_config
from services.backtest.result import TradeResult

# from yaml: backtest cost model
COMMISSION = 0.00025
STAMP_DUTY = 0.0005
SLIPPAGE = 0.001
LIMIT_UP_PCT_DEFAULT = 0.097  # 主板 fallback, 实际按 get_limit_up_pct(code) 取


def _load_aux_data():
    """一次性加载辅助数据到内存 (PIT verified)."""
    sconn = duckdb.connect(str(SMART_DB), read_only=True)
    ind_map = {r[0]: r[1] for r in sconn.execute(
        "SELECT stock_code, tdx_l1_name FROM dim_stock_tdx_industry").fetchall()}
    mcap = {}
    for r in sconn.execute(
        "SELECT stock_code, trade_date, mcap_decile FROM fact_market_cap_decile_daily "
        "WHERE trade_date >= '2023-01-01'"  # from yaml: backtest.start_date
    ).fetchall():
        mcap.setdefault(r[0], {})[str(r[1])[:10]] = r[2]
    capflow = {}
    for r in sconn.execute(
        "SELECT stock_code, trade_date, lhb_count_30d FROM fact_capital_flow_pit_daily "
        "WHERE trade_date >= '2023-01-01'"  # from yaml: backtest.start_date
    ).fetchall():
        capflow.setdefault(r[0], {})[str(r[1])[:10]] = r[2]
    sect_mom = {}
    for r in sconn.execute(
        "SELECT sector_name, date, ret_5d FROM fact_sector_momentum_daily"
    ).fetchall():
        sect_mom[(r[0], str(r[1])[:10])] = r[2]
    sconn.close()
    return {"ind": ind_map, "mcap": mcap, "capflow": capflow, "sect_mom": sect_mom}


def _get_aux(code, date_str, aux, stocks_data):
    """查辅助指标 (PIT safe: 只查 <= date_str 的数据)."""
    ind = aux["ind"].get(code, "未知")
    mc = aux["mcap"].get(code, {}).get(date_str)
    if mc is None:
        for d in range(1, 10):
            try:
                mc = aux["mcap"].get(code, {}).get(
                    str(dt_date.fromisoformat(date_str) - timedelta(days=d)))
                if mc is not None:
                    break
            except (ValueError, KeyError):  # rule-compliance: ok evidence=date-parse-fallback
                pass
    lhb = aux["capflow"].get(code, {}).get(date_str)
    sect5 = aux["sect_mom"].get((ind, date_str))

    pp60 = tvr = None
    if code in stocks_data:
        d = stocks_data[code]
        dates_str = [str(x)[:10] for x in d["dates"]]
        try:
            bi = dates_str.index(date_str)
        except ValueError:
            bi = None
        if bi is not None and bi >= 60:
            h60 = np.max(d["high"][max(0, bi - 60): bi + 1])
            l60 = np.min(d["low"][max(0, bi - 60): bi + 1])
            pp60 = (d["close"][bi] - l60) / (h60 - l60) if h60 > l60 else 0.5
            ma60v = np.mean(d["volume"][bi - 60: bi])
            tvr = d["volume"][bi] / ma60v if ma60v > 0 else 1.0
    return {"ind": ind, "mcap": mc, "lhb": lhb, "sect5": sect5, "pp60": pp60, "tvr": tvr}


def _apply_aux_filter(sig_aux, params):
    """辅助过滤: 返回 True = 保留."""
    if params.get("use_pp60") and sig_aux["pp60"] is not None:
        if sig_aux["pp60"] < params.get("pp60_min", 0):
            return False
    if params.get("use_tvr") and sig_aux["tvr"] is not None:
        if sig_aux["tvr"] < params.get("tvr_min", 0) or sig_aux["tvr"] > params.get("tvr_max", 99):
            return False
    if params.get("use_lhb") and sig_aux["lhb"] is not None:
        if sig_aux["lhb"] > params.get("lhb_max", 99):
            return False
    if params.get("use_sect") and sig_aux["sect5"] is not None:
        if sig_aux["sect5"] < params.get("sect_min", -99):
            return False
    return True


def _execute_trade(code, buy_i, data, params):
    """模拟单笔交易, 含止损/止盈/trailing + 真实成本. 返回 TradeResult."""
    from services.universe import get_limit_up_pct
    n = len(data["close"])
    o, h, l, c = data["open"], data["high"], data["low"], data["close"]

    if buy_i >= n:
        return None
    buy_price_raw = o[buy_i]
    if buy_price_raw <= 0:
        return None
    limit_pct = get_limit_up_pct(code) - 0.003  # 略低于涨停幅度判封板
    if buy_i > 0 and buy_price_raw >= c[buy_i - 1] * (1 + limit_pct):
        return None

    buy_price = buy_price_raw * (1 + SLIPPAGE + COMMISSION)
    hp = params.get("hold_days", 5)
    stop = params.get("stop_pct", -0.08)
    target = params.get("target_pct", 0.15)
    trailing = params.get("trailing_pct", 0.05)

    peak = buy_price
    exit_reason = "hp_expired"
    sell_i = min(buy_i + hp, n - 1)
    max_dd = 0.0

    for k in range(1, hp + 1):
        di = buy_i + k
        if di >= n:
            sell_i = n - 1
            exit_reason = "data_truncated"
            break
        day_high = h[di]
        day_low = l[di]
        peak = max(peak, day_high)
        dd = (day_low - buy_price) / buy_price
        max_dd = min(max_dd, dd)

        if dd <= stop:
            sell_i = di
            exit_reason = "stop_loss"
            break
        trail_stop = peak * (1 - trailing)
        if day_low <= trail_stop and peak > buy_price * 1.01:
            sell_i = di
            exit_reason = "trailing_stop"
            break
        if (day_high - buy_price) / buy_price >= target:
            sell_i = di
            exit_reason = "target_hit"
            break

    sell_price = c[sell_i] * (1 - STAMP_DUTY - COMMISSION - SLIPPAGE)
    gross_ret = (c[sell_i] / buy_price_raw) - 1
    net_ret = (sell_price / buy_price) - 1

    return TradeResult(
        stock_code=code,
        signal_date=str(data["dates"][buy_i - 1]) if buy_i > 0 else str(data["dates"][0]),
        buy_date=str(data["dates"][buy_i]),
        buy_price=buy_price,
        sell_date=str(data["dates"][sell_i]),
        sell_price=sell_price,
        holding_days=sell_i - buy_i,
        exit_reason=exit_reason,
        gross_ret=gross_ret,
        net_ret=net_ret,
        max_drawdown=max_dd,
    )


def _build_params_from_trial(trial, round_name):
    """从 Optuna trial 构造完整参数."""
    p = {}
    p["breakout_pct_min"] = trial.suggest_float("breakout_pct", 5.0, 12.0)
    p["breakout_vol_ratio"] = trial.suggest_float("vol_ratio", 1.0, 3.0)
    p["pullback_min_days"] = trial.suggest_int("pb_min", 2, 4)
    p["pullback_max_days"] = trial.suggest_int("pb_max", 3, 7)
    p["pullback_vol_shrink"] = trial.suggest_float("vol_shrink", 0.3, 0.9)
    p["doji_body_ratio_max"] = trial.suggest_float("doji_body", 0.1, 0.5)
    p["doji_range_min"] = trial.suggest_float("doji_range", 0.002, 0.01)
    p["buy_offset"] = trial.suggest_int("buy_offset", 1, 3)
    p["breakout_close_eq_high"] = False
    p["pullback_above_breakout_low"] = True
    p["pre_pattern"] = False

    p["hold_days"] = trial.suggest_categorical("hp", [3, 5, 7, 10, 15])
    p["stop_pct"] = trial.suggest_float("stop_pct", -0.12, -0.02)
    p["target_pct"] = trial.suggest_float("target_pct", 0.05, 0.30)
    p["trailing_pct"] = trial.suggest_float("trailing_pct", 0.02, 0.10)

    p["use_pp60"] = trial.suggest_categorical("use_pp60", [True, False])
    if p["use_pp60"]:
        p["pp60_min"] = trial.suggest_float("pp60_min", 0.5, 0.95)
    p["use_tvr"] = trial.suggest_categorical("use_tvr", [True, False])
    if p["use_tvr"]:
        p["tvr_min"] = trial.suggest_float("tvr_min", 1.0, 4.0)
        p["tvr_max"] = trial.suggest_float("tvr_max", 3.0, 8.0)
    p["use_lhb"] = trial.suggest_categorical("use_lhb", [True, False])
    if p["use_lhb"]:
        p["lhb_max"] = trial.suggest_int("lhb_max", 0, 2)
    p["use_sect"] = trial.suggest_categorical("use_sect", [True, False])
    if p["use_sect"]:
        p["sect_min"] = trial.suggest_float("sect_min", -0.05, 0.05)

    return p


def run_optuna_round(
    round_name: str,
    stocks_data: dict,
    aux: dict,
    n_trials: int = 100,
    seed: int = 42,
):
    """执行单轮 Optuna 寻优."""
    print(f"\n{'='*70}")
    print(f"  Round: {round_name} | trials={n_trials} | seed={seed}")
    print(f"{'='*70}")

    def objective(trial):
        params = _build_params_from_trial(trial, round_name)

        all_trades = []
        for code, data in stocks_data.items():
            sigs = detect_signals(
                data["dates"], data["open"], data["high"], data["low"],
                data["close"], data["volume"], params,
            )
            for sig in sigs:
                buy_date_str = sig["buy_date"]
                sig_aux = _get_aux(code, sig["breakout_date"][:10], aux, stocks_data)
                if not _apply_aux_filter(sig_aux, params):
                    continue
                trade = _execute_trade(code, sig["buy_idx"], data, params)
                if trade is not None:
                    all_trades.append(trade)

        if len(all_trades) < 5:
            return -999.0

        rets = np.array([t.net_ret for t in all_trades])
        n_win = int(np.sum(rets > 0))
        win_rate = n_win / len(rets)
        mean_ret = float(np.mean(rets))
        std_ret = float(np.std(rets))
        losses = rets[rets <= 0]
        avg_win = float(np.mean(rets[rets > 0])) if n_win > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else -0.001

        trial.set_user_attr("n_trades", len(all_trades))
        trial.set_user_attr("win_rate", round(win_rate, 4))
        trial.set_user_attr("mean_ret", round(mean_ret * 100, 3))
        trial.set_user_attr("sharpe", round(mean_ret / std_ret if std_ret > 0 else 0, 4))

        if round_name == "r1_winrate":
            return win_rate
        elif round_name == "r1_return":
            return mean_ret
        elif round_name == "r1_sharpe":
            return mean_ret / std_ret if std_ret > 0 else 0
        elif round_name == "r1_plratio":
            pl = abs(avg_win / avg_loss) if avg_loss != 0 else 1
            return pl * math.sqrt(max(win_rate, 0.01))
        elif round_name == "r2_composite":
            sharpe = mean_ret / std_ret if std_ret > 0 else 0
            dds = np.array([t.max_drawdown for t in all_trades])
            calmar = mean_ret / abs(np.mean(dds)) if np.mean(dds) != 0 else 0
            down_std = float(np.std(losses)) if len(losses) > 1 else 0.001
            sortino = mean_ret / down_std if down_std > 0 else 0
            score = calmar * 0.35 + sortino * 0.25 + sharpe * 0.15 + win_rate * 0.10
            score *= math.log(1 + len(all_trades))
            return score
        return mean_ret

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_trial
    print(f"\n  Best trial #{best.number}: value={best.value:.4f}")
    print(f"  n_trades={best.user_attrs.get('n_trades')}, "
          f"win_rate={best.user_attrs.get('win_rate')}, "
          f"mean_ret={best.user_attrs.get('mean_ret')}%, "
          f"sharpe={best.user_attrs.get('sharpe')}")
    print(f"  Params:")
    for k, v in sorted(best.params.items()):
        print(f"    {k}: {v}")

    return study


def main():
    parser = argparse.ArgumentParser(description="回调十字星 Optuna 寻优")
    parser.add_argument("--round", default="all",
                        choices=["r1_winrate", "r1_return", "r1_sharpe", "r1_plratio",
                                 "r2_composite", "all"],
                        help="寻优轮次")
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    print("加载 K 线数据...")
    conn = duckdb.connect(str(MARKET_DB), read_only=True)
    stocks_data = load_kline_data(conn)
    conn.close()

    print("加载辅助数据...")
    aux = _load_aux_data()
    print(f"  ind={len(aux['ind'])}, mcap={len(aux['mcap'])}, capflow={len(aux['capflow'])}")

    rounds_to_run = []
    if args.round == "all":
        rounds_to_run = [
            ("r1_winrate", args.trials),
            ("r1_return", args.trials),
            ("r1_sharpe", args.trials),
            ("r1_plratio", args.trials),
        ]
    else:
        rounds_to_run = [(args.round, args.trials)]

    all_studies = {}
    for rname, n_trials in rounds_to_run:
        study = run_optuna_round(rname, stocks_data, aux, n_trials=n_trials, seed=args.seed)
        all_studies[rname] = study

    # Save results
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    results = {}
    for rname, study in all_studies.items():
        best = study.best_trial
        results[rname] = {
            "best_value": round(best.value, 6),
            "best_params": best.params,
            "user_attrs": best.user_attrs,
            "n_trials": len(study.trials),
        }
    out_path = OUTPUT_DIR / f"optuna_pullback_doji_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果保存: {out_path}")

    # Summary table
    print(f"\n{'='*80}")
    print(f"  Round 1 汇总")
    print(f"{'='*80}")
    print(f"  {'Round':<15} {'best':>8} {'n_trades':>8} {'win%':>6} {'ret%':>7} {'sharpe':>7} {'buy_off':>7}")
    for rname, study in all_studies.items():
        b = study.best_trial
        ua = b.user_attrs
        bo = b.params.get("buy_offset", "?")
        print(f"  {rname:<15} {b.value:>8.4f} {ua.get('n_trades','?'):>8} "
              f"{ua.get('win_rate',0)*100:>5.1f}% {ua.get('mean_ret',0):>+6.2f}% "
              f"{ua.get('sharpe',0):>6.3f}  offset={bo}")


if __name__ == "__main__":
    main()
