"""Phase η++++ — 情绪因子 IC 诚实验证.

目的: 用户选 A 路线 — 跑 IC 验证再决定是否进 6 维桶.

测试因子 (2 个):
  1. lhb_score (龙虎榜事件分数): inst_buy_seats × 10 + net_buy/1e8
     来源: fact_lhb_event (52K 行, 2023-2026, 充足)
  2. survey_count_60d (调研机构数 60 日滚动)
     来源: raw_institution_surveys (10K 行, 2025-2026, 1 年, 偏少但可用)

测试 horizon: 5 / 10 / 20 / 60 日 forward return

IC 计算:
  - 每个 event_date 上, 因子值 X_t 与未来 N 日收益 Y_{t+N} 做 Spearman 相关
  - 每日 IC = corr(X_t over all stocks, Y over all stocks)
  - 总 IC = 所有 event_date 上 IC 的均值
  - 显著性: |IC| > 0.03 算有用, > 0.05 不错, > 0.08 很好

判定标准:
  - IC > 0.03 AND p_pos_pct > 55% (因子值越大未来收益越大) → 推荐进 6 维桶
  - 否则 → 不推荐
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn


log = logging.getLogger("validate_sentiment_ic")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
HORIZONS = [5, 10, 20, 60]


def spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 相关 (秩相关)."""
    if len(x) < 5 or len(y) < 5:
        return np.nan
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 5:
        return np.nan
    x_r = np.argsort(np.argsort(x[mask]))
    y_r = np.argsort(np.argsort(y[mask]))
    if x_r.std() == 0 or y_r.std() == 0:
        return np.nan
    return float(np.corrcoef(x_r, y_r)[0, 1])


def load_forward_returns(codes: list[str], dates: list[str]) -> dict[tuple[str, str], dict[int, float]]:
    """加载 (code, event_date) → {horizon: ret} 映射."""
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    log.info(f"加载 {len(codes)} 股 {len(dates)} 日 K 线...")
    code_set = set(codes)
    date_set = set(dates)

    # 先拉每股的全部 daily K 线 (后续 in-mem 算 forward return)
    placeholders = ",".join(["?"] * len(code_set))
    rows = mkt.execute(
        f"""
        SELECT code, date, close
          FROM v_price_kline_qfq
         WHERE freq='daily' AND adjust='qfq' AND code IN ({placeholders})
           AND date >= '2023-01-01'
         ORDER BY code, date
        """,
        list(code_set),
    ).fetchall()
    mkt.close()

    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c, d, cl in rows:
        by_code[c].append((d, float(cl)))

    result: dict[tuple[str, str], dict[int, float]] = {}
    for code, kl in by_code.items():
        date_to_idx = {d: i for i, (d, _) in enumerate(kl)}
        for ed in date_set:
            if ed not in date_to_idx:
                continue
            i0 = date_to_idx[ed]
            # T+1 (or use T 收盘? 用 T+1 close 防止前视)
            i_buy = i0 + 1
            if i_buy >= len(kl):
                continue
            buy_price = kl[i_buy][1]
            if buy_price <= 0:
                continue
            rets: dict[int, float] = {}
            for h in HORIZONS:
                i_sell = i_buy + h
                if i_sell >= len(kl):
                    rets[h] = np.nan
                else:
                    rets[h] = (kl[i_sell][1] - buy_price) / buy_price
            result[(code, ed)] = rets
    log.info(f"  forward return 表: {len(result):,} (code,event) 对")
    return result


def compute_daily_ic(events: list[dict], factor_key: str,
                     fwd_rets: dict, horizons: list[int]) -> dict[int, list[tuple[str, float, int]]]:
    """对每个 event_date 算横截面 IC.

    Returns:
        {horizon: [(event_date, ic_value, n_stocks), ...]}
    """
    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_date[e["event_date"]].append(e)

    out: dict[int, list[tuple[str, float, int]]] = {h: [] for h in horizons}
    for ed, ev_list in by_date.items():
        if len(ev_list) < 5:
            continue  # 横截面样本太少
        for h in horizons:
            xs, ys = [], []
            for e in ev_list:
                key = (e["stock_code"], ed)
                if key not in fwd_rets:
                    continue
                ret = fwd_rets[key].get(h)
                if ret is None or np.isnan(ret):
                    continue
                xs.append(e[factor_key])
                ys.append(ret)
            if len(xs) < 5:
                continue
            ic = spearman_ic(np.array(xs), np.array(ys))
            if not np.isnan(ic):
                out[h].append((ed, ic, len(xs)))
    return out


def summarize_ic(daily_ics: list[tuple[str, float, int]], label: str) -> dict:
    """汇总 daily IC: mean / std / IR / pos_pct / n_days."""
    if not daily_ics:
        return {"label": label, "n_days": 0}
    ics = np.array([x[1] for x in daily_ics])
    ic_mean = float(ics.mean())
    ic_std = float(ics.std())
    ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
    pos_pct = float((ics > 0).mean())
    n_obs = int(np.sum([x[2] for x in daily_ics]))
    return {
        "label": label,
        "n_days": len(daily_ics),
        "n_obs_total": n_obs,
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ic_ir": ic_ir,
        "ic_pos_pct": pos_pct,
        "verdict": (
            "推荐进桶 ✓" if abs(ic_mean) >= 0.03 and pos_pct >= 0.55 else
            "弱信号 ?" if abs(ic_mean) >= 0.02 else
            "无效, 砍 ✗"
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# 因子 1: 龙虎榜
# ─────────────────────────────────────────────────────────────────────

def test_lhb_ic():
    conn = get_conn()
    log.info("=== 龙虎榜 IC 测试 ===")
    rows = conn.execute(
        """
        SELECT stock_code, trade_date,
               COALESCE(inst_buy_seats, 0) AS seats,
               COALESCE(net_buy, 0) AS net_buy,
               COALESCE(is_inst_net_buy, 0) AS is_inst,
               COALESCE(net_buy_pct, 0) AS net_buy_pct,
               COALESCE(turnover_rate, 0) AS turnover_rate
          FROM fact_lhb_event
         WHERE trade_date >= '2023-06-01'
        """
    ).fetchall()
    log.info(f"  fact_lhb_event 读 {len(rows):,} 行")

    # 构造多个因子
    events = []
    for sc, td, seats, nb, is_inst, nbp, tor in rows:
        events.append({
            "stock_code": sc,
            "event_date": td,
            "lhb_score":     seats * 10.0 + nb / 1e8,    # 原 stable_score
            "lhb_seats":     float(seats),                # 单独看席位数
            "lhb_net_buy":   float(nb),                   # 单独看净买入
            "lhb_net_buy_pct": float(nbp),                # 单独看净买入占比
            "lhb_is_inst":   float(is_inst),              # 机构净买 0/1
        })
    conn.close()

    # 取唯一 (code, date) (避免一日多上榜)
    seen = set()
    uniq = []
    for e in events:
        k = (e["stock_code"], e["event_date"])
        if k in seen: continue
        seen.add(k); uniq.append(e)
    log.info(f"  唯一 (code,date) 事件: {len(uniq):,}")

    codes = list({e["stock_code"] for e in uniq})
    dates = list({e["event_date"] for e in uniq})
    fwd = load_forward_returns(codes, dates)

    results = []
    for factor in ["lhb_score", "lhb_seats", "lhb_net_buy", "lhb_net_buy_pct", "lhb_is_inst"]:
        daily_ic_by_h = compute_daily_ic(uniq, factor, fwd, HORIZONS)
        for h in HORIZONS:
            r = summarize_ic(daily_ic_by_h[h], f"{factor} × {h}d")
            results.append(r)
    return results


# ─────────────────────────────────────────────────────────────────────
# 因子 2: 调研热度
# ─────────────────────────────────────────────────────────────────────

def test_survey_ic():
    conn = get_conn()
    log.info("=== 调研热度 IC 测试 ===")
    rows = conn.execute(
        """
        SELECT stock_code, survey_date, COALESCE(inst_count, 0)
          FROM raw_institution_surveys
         WHERE survey_date >= '2025-04-23'
         ORDER BY stock_code, survey_date
        """
    ).fetchall()
    conn.close()
    log.info(f"  raw_institution_surveys 读 {len(rows):,} 行")
    if len(rows) < 100:
        log.warning("  样本过少, 跳过")
        return []

    # 按 (stock_code, date) 派生滚动窗口
    # 每个调研日 → 计算过去 30d / 60d 内该股的调研次数 + 累计机构数
    from datetime import date as _date
    by_code: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for sc, sd, ic in rows:
        by_code[sc].append((sd, int(ic or 0)))

    events = []
    for sc, lst in by_code.items():
        for i, (sd, ic) in enumerate(lst):
            # 算窗口
            d0 = _date.fromisoformat(sd)
            from datetime import timedelta
            d_30 = (d0 - timedelta(days=30)).isoformat()
            d_60 = (d0 - timedelta(days=60)).isoformat()
            count_30d = sum(1 for d, _ in lst if d_30 <= d <= sd)
            count_60d = sum(1 for d, _ in lst if d_60 <= d <= sd)
            inst_60d = sum(ic2 for d, ic2 in lst if d_60 <= d <= sd)
            events.append({
                "stock_code": sc,
                "event_date": sd,
                "survey_count_30d": float(count_30d),
                "survey_count_60d": float(count_60d),
                "survey_inst_60d":  float(inst_60d),
            })
    log.info(f"  调研事件 (含派生窗口): {len(events):,}")

    # 取唯一 (code, date)
    seen = set()
    uniq = []
    for e in events:
        k = (e["stock_code"], e["event_date"])
        if k in seen: continue
        seen.add(k); uniq.append(e)
    log.info(f"  唯一 (code,date): {len(uniq):,}")

    codes = list({e["stock_code"] for e in uniq})
    dates = list({e["event_date"] for e in uniq})
    fwd = load_forward_returns(codes, dates)

    results = []
    for factor in ["survey_count_30d", "survey_count_60d", "survey_inst_60d"]:
        daily_ic_by_h = compute_daily_ic(uniq, factor, fwd, HORIZONS)
        for h in HORIZONS:
            r = summarize_ic(daily_ic_by_h[h], f"{factor} × {h}d")
            results.append(r)
    return results


def print_results(title: str, results: list[dict]) -> None:
    print(f"\n{'='*108}")
    print(f"  {title}")
    print(f"{'='*108}")
    print(f"{'因子':<32} {'horizon':>4} {'n_days':>7} {'n_obs':>8} {'IC':>8} {'IC_std':>8} {'IC_IR':>7} {'pos_pct':>8}  判定")
    for r in results:
        if r.get("n_days", 0) == 0:
            print(f"{r['label']:<32} (无数据)")
            continue
        label = r["label"]
        parts = label.rsplit(" × ", 1)
        factor = parts[0]
        h = parts[1]
        print(f"{factor:<32} {h:>5} {r['n_days']:>7} {r['n_obs_total']:>8} "
              f"{r['ic_mean']:>+8.4f} {r['ic_std']:>8.4f} {r['ic_ir']:>+7.3f} "
              f"{r['ic_pos_pct']*100:>7.1f}%  {r['verdict']}")


def main():
    t0 = time.time()
    lhb_results = test_lhb_ic()
    survey_results = test_survey_ic()
    print_results("龙虎榜因子 IC", lhb_results)
    print_results("调研热度因子 IC", survey_results)
    print(f"\n=== 总耗时 {time.time()-t0:.0f}s ===\n")
    print("判定阈值: |IC| ≥ 0.03 + pos_pct ≥ 55% → 推荐进 6 维桶")
    print("         |IC| ≥ 0.02 → 弱信号, 可不进桶但作辅助过滤")
    print("         其他 → 砍掉, 不在主链路")


if __name__ == "__main__":
    main()
