"""Phase η+++++ — 排除规则回测 (technical_stage × survey_bin × forward_return).

回答用户质疑: 是否 "已充分演绎 × 调研狂" 的股票历史上仍然推高股价?

设计:
  数据源:
    - 历史 (technical_stage, survey_bin, stock, date)
    - 未来 5/20/60 日收益 (T+1 buy → T+1+N sell)
  分桶: technical_stage × survey_bin (5 × 4 = 20 个组合)
  指标: 每桶 mean_ret / median_ret / win_rate / n / std / sharpe-like

判定规则:
  如果某 (stage, sbin) 桶:
    - mean_ret < -0.02 AND win_rate < 45% → 应排除 (负 alpha)
    - mean_ret > +0.02 AND win_rate > 55% → 应保留 (正 alpha)
    - 其他 → 中性, 保留

实测 → 写入 profiles.py 的 exclude_tech_stages.

注:
  fundamental_stage 的派生函数依赖 _latest 表 (mart_stock_trend, dim_*_latest),
  历史不可重建, 因此本回测仅用 technical_stage (有 2 年完整历史) + survey_bin.
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


log = logging.getLogger("validate_exclusion_rules")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
HORIZONS = [5, 20, 60]


def load_event_panel(start_date: str | None = None) -> list[dict]:
    """构造 (stock_code, date, technical_stage, survey_bin) 事件面板."""
    conn = get_conn()
    where = f"WHERE t.date >= '{start_date}'" if start_date else ""
    rows = conn.execute(f"""
        SELECT t.stock_code, t.date, t.stage AS technical_stage,
               COALESCE(s.survey_bin, '冷') AS survey_bin,
               COALESCE(s.survey_count_60d, 0) AS survey_count_60d
          FROM fact_stock_technical_stage t
          LEFT JOIN mart_stock_survey_features s
            ON s.stock_code = t.stock_code AND s.as_of_date = t.date
        {where}
    """).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "stock_code": r[0], "date": r[1],
            "technical_stage": r[2] or "?",
            "survey_bin": r[3],
            "survey_count_60d": r[4] or 0,
        })
    return out


def load_forward_returns(events: list[dict]) -> dict[tuple[str, str], dict[int, float]]:
    """计算 (code, date) → {h: fwd_ret} (T+1 buy → T+1+h sell)."""
    codes = list({e["stock_code"] for e in events})
    log.info(f"  加载 {len(codes):,} 股 K 线...")
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    placeholders = ",".join(["?"] * len(codes))
    rows = mkt.execute(
        f"""SELECT code, date, close FROM v_price_kline_qfq
            WHERE freq='daily' AND adjust='qfq' AND code IN ({placeholders})
              AND date >= '2025-01-01'
            ORDER BY code, date""",
        codes,
    ).fetchall()
    mkt.close()

    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c, d, cl in rows:
        by_code[c].append((d, float(cl)))

    event_dates = defaultdict(set)
    for e in events:
        event_dates[e["stock_code"]].add(e["date"])

    out: dict[tuple[str, str], dict[int, float]] = {}
    for code, kl in by_code.items():
        d_to_i = {d: i for i, (d, _) in enumerate(kl)}
        for ed in event_dates.get(code, ()):
            if ed not in d_to_i:
                continue
            i_buy = d_to_i[ed] + 1
            if i_buy >= len(kl):
                continue
            buy = kl[i_buy][1]
            if buy <= 0:
                continue
            rets = {}
            for h in HORIZONS:
                i_sell = i_buy + h
                rets[h] = (kl[i_sell][1] - buy) / buy if i_sell < len(kl) else np.nan
            out[(code, ed)] = rets
    return out


def cross_tab(events: list[dict], fwd: dict, h: int) -> dict[tuple[str, str], dict]:
    """按 (technical_stage × survey_bin) 分桶, 每桶算 stats."""
    buckets = defaultdict(list)
    for e in events:
        key = (e["technical_stage"], e["survey_bin"])
        ret = fwd.get((e["stock_code"], e["date"]), {}).get(h)
        if ret is not None and not np.isnan(ret):
            buckets[key].append(ret)

    out = {}
    for key, rets in buckets.items():
        rets_arr = np.array(rets)
        n = len(rets_arr)
        if n < 30:
            continue
        out[key] = {
            "n": n,
            "mean_ret":  float(rets_arr.mean()),
            "median_ret": float(np.median(rets_arr)),
            "std_ret":   float(rets_arr.std()),
            "win_rate":  float((rets_arr > 0).mean()),
            "sharpe":    float(rets_arr.mean() / rets_arr.std()) if rets_arr.std() > 0 else 0.0,
        }
    return out


def judge_exclusion(stats: dict) -> str:
    """根据 stats 判定: exclude / neutral / boost."""
    m = stats["mean_ret"]
    w = stats["win_rate"]
    if m < -0.02 and w < 0.45:
        return "EXCLUDE 🔴"
    if m > 0.02 and w > 0.55:
        return "BOOST 🟢"
    if m > 0 and w > 0.50:
        return "neutral+"
    return "neutral"


def print_matrix(matrix: dict, horizon: int) -> None:
    """打印 5 × 4 矩阵."""
    print(f"\n{'='*128}")
    print(f"  (technical_stage × survey_bin) × forward_return_{horizon}d  矩阵")
    print(f"{'='*128}")
    print(f"{'stage':<8} {'sbin':<6} {'n':>7} {'mean_ret':>10} {'median':>9} {'std':>8} {'win':>7} {'sharpe':>8}  判定")
    print("-" * 128)
    # 按 (stage, sbin) 排序展示
    stages = sorted({k[0] for k in matrix.keys()})
    sbins = ["冷", "温", "热", "狂"]
    for stage in stages:
        for sbin in sbins:
            key = (stage, sbin)
            if key not in matrix:
                continue
            s = matrix[key]
            verdict = judge_exclusion(s)
            print(f"{stage:<8} {sbin:<6} {s['n']:>7,} "
                  f"{s['mean_ret']*100:>+9.2f}% {s['median_ret']*100:>+8.2f}% "
                  f"{s['std_ret']*100:>7.2f}% {s['win_rate']*100:>6.1f}% "
                  f"{s['sharpe']:>+8.3f}  {verdict}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-23",
                        help="grid 起始日 (默认 = survey_features 起始日)")
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== 排除规则回测 (since {args.start}) ===")

    log.info("加载事件面板 (stage × sbin) ...")
    events = load_event_panel(args.start)
    log.info(f"  事件: {len(events):,} 条")

    log.info("加载 forward returns ...")
    fwd = load_forward_returns(events)
    log.info(f"  forward returns: {len(fwd):,} (code,date) 对")

    matrices = {}
    for h in HORIZONS:
        m = cross_tab(events, fwd, h)
        matrices[h] = m
        print_matrix(m, h)

    # 汇总判定: 哪些 (stage, sbin) 该排除?
    print(f"\n{'='*128}")
    print("  汇总判定 (3 horizon 综合)")
    print(f"{'='*128}")
    exclusion_votes = defaultdict(list)
    for h, m in matrices.items():
        for key, s in m.items():
            v = judge_exclusion(s)
            exclusion_votes[key].append((h, v, s["mean_ret"], s["win_rate"]))

    print(f"{'stage':<8} {'sbin':<6} {'5d':>16} {'20d':>16} {'60d':>16}  最终建议")
    print("-" * 128)
    final_excludes = []
    final_boosts = []
    for key in sorted(exclusion_votes.keys()):
        votes = exclusion_votes[key]
        d = {h: (v, m, w) for h, v, m, w in votes}
        row = f"{key[0]:<8} {key[1]:<6}"
        excl = 0; boost = 0
        for h in HORIZONS:
            if h not in d:
                row += f"  {'(n<30)':>14}"
            else:
                v, m, w = d[h]
                row += f"  {v[:8]:>8}{m*100:>+6.1f}%"
                if "EXCLUDE" in v: excl += 1
                if "BOOST" in v: boost += 1
        # 多数表决
        if excl >= 2:
            final = "🔴 排除"
            final_excludes.append(key)
        elif boost >= 2:
            final = "🟢 加权"
            final_boosts.append(key)
        else:
            final = "中性"
        print(row + f"  {final}")

    print(f"\n=== 实测结论 ===")
    print(f"应排除 (3 horizon 中 ≥2 个为 EXCLUDE) [{len(final_excludes)} 组合]:")
    for k in final_excludes:
        print(f"  - stage={k[0]} × survey_bin={k[1]}")
    print(f"应加权 (≥2 个 BOOST) [{len(final_boosts)} 组合]:")
    for k in final_boosts:
        print(f"  - stage={k[0]} × survey_bin={k[1]}")
    print(f"\n=== 耗时 {time.time()-t0:.0f}s ===")


if __name__ == "__main__":
    main()
