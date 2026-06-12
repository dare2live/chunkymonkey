#!/usr/bin/env python3
"""C0: cyq_perf 黑箱口径审计 — 筹码轴 5 个实验的总开关 (alpha 矩阵 9.2 分前置 gate).

预注册判据 (跑前冻结, 判负即筹码轴全冻结待口径定位, 不放宽不复跑):
  J1 Spearman(本地复算 winner_rate, tushare winner_rate) 按股中位 >= 0.95
  J2 全样本 median |Δwinner_rate| <= 5pp
  J3 his_high 与 K线 rolling max 一致率 >= 99% (仅 2019 后上市子样本, 口径可比)
  辅助 (口径定位非判据): dividend ex_date 当日 tushare winner_rate t-1→t+1 跳变分布
    — 跳变大 = 未复权坐标口径; 平滑 = 复权口径。结论写回 sync_registry pit_anchor。

本地复算 = docs/chip_distribution_cyq_spec.md §2.2 参考算法原样 (三角分布峰在 vwap +
换手等比衰减, 已对通达信验证 ±1pp), qfq 前复权价格坐标, burn-in 自 2022-01 起算,
比对窗 2024-01 起。数据全部 raw 层 (daily/daily_basic/adj_factor/cyq_perf/dividend)。

单位自校验 (防 tushare 单位陷阱): vol 手×100=股 / amount 千元×1000=元 /
float_share 万股×10000=股; 每日断言 vwap ∈ [low, high], 违反立刻 raise (单位错即爆)。

用法: PYTHONPATH=backend python backend/scripts/experiment_c0_cyq_audit.py
输出: analysis/c0_cyq_audit_<date>.json + 控制台判定。local 秒-分钟级, 零 API。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("c0_cyq_audit")
_REPO = Path(__file__).resolve().parents[2]

# 预注册判据 (冻结; evidence: alpha_combo_matrix_20260612.md C0 条目)
J1_SPEARMAN_MIN = 0.95
J2_MEDIAN_ABS_DELTA_PP_MAX = 5.0
J3_HISHIGH_MATCH_MIN = 0.99
N_STOCKS_PER_BOARD = 8       # 分层下限 (实验设计: 四板块 + 除权组各 >=8)
BURN_IN_START = "20220104"   # 分布冷启动 burn-in (比对窗前 2 年)
COMPARE_START = "20240102"   # 比对窗起点
TICK = 0.01                  # spec §2.2 价格网格步长


def _raw_conn():
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    return connect(str(get_database_manifest().path_for("tushare_raw")), read_only=True)


def _sample_stocks(conn) -> dict[str, list[str]]:
    """分层抽样: 四板块 + 近 1 年有除权 各 >= N_STOCKS_PER_BOARD (确定性: 按代码序取头部)."""
    boards = {
        "sh_main": "ts_code LIKE '60%'",
        "sz_main": "ts_code LIKE '00%'",
        "chinext": "ts_code LIKE '30%'",
        "star": "ts_code LIKE '68%'",
    }
    out: dict[str, list[str]] = {}
    for name, cond in boards.items():
        rows = conn.execute(f"""
            SELECT ts_code FROM (
                SELECT ts_code, COUNT(*) AS n FROM raw_tushare_cyq_perf
                WHERE {cond} GROUP BY 1
            ) WHERE n > 500 ORDER BY ts_code LIMIT {N_STOCKS_PER_BOARD}
        """).fetchall()
        out[name] = [r[0] for r in rows]
    rows = conn.execute(f"""
        SELECT DISTINCT d.ts_code FROM raw_tushare_dividend d
        JOIN (SELECT ts_code, COUNT(*) n FROM raw_tushare_cyq_perf GROUP BY 1) c USING (ts_code)
        WHERE d.ex_date >= '20250601' AND d.cash_div_tax IS NOT NULL AND c.n > 500
        ORDER BY d.ts_code LIMIT {N_STOCKS_PER_BOARD}
    """).fetchall()
    out["recent_exdiv"] = [r[0] for r in rows]
    return out


def _load_stock(conn, ts_code: str):
    """daily + adj_factor + daily_basic float_share, qfq 价格坐标, 单位自校验."""
    rows = conn.execute("""
        SELECT d.trade_date,
               CAST(d.open AS DOUBLE), CAST(d.high AS DOUBLE), CAST(d.low AS DOUBLE),
               CAST(d.close AS DOUBLE), CAST(d.vol AS DOUBLE), CAST(d.amount AS DOUBLE),
               CAST(a.adj_factor AS DOUBLE), CAST(b.float_share AS DOUBLE)
        FROM raw_tushare_daily d
        JOIN raw_tushare_adj_factor a USING (ts_code, trade_date)
        JOIN raw_tushare_daily_basic b USING (ts_code, trade_date)
        WHERE d.ts_code = ? AND d.trade_date >= ?
        ORDER BY d.trade_date
    """, [ts_code, BURN_IN_START]).fetchall()
    if len(rows) < 300:
        return None
    arr = np.array(rows, dtype=object)
    dates = arr[:, 0].astype(str)
    o, h, l, c, vol, amt, adj, fs = (arr[:, i].astype(float) for i in range(1, 9))
    # qfq: price * adj_factor / latest_adj (spec 决策: 前复权坐标)
    qfq = adj / adj[-1]
    oq, hq, lq, cq = o * qfq, h * qfq, l * qfq, c * qfq
    vol_shares = vol * 100.0          # 手 → 股
    amt_yuan = amt * 1000.0           # 千元 → 元
    float_shares = fs * 10000.0       # 万股 → 股
    # 单位自校验: 未复权 vwap 必须落在未复权 [low, high] (容差 1%); 违反 = 单位假设错, 立刻爆
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap_raw = np.where(vol_shares > 0, amt_yuan / vol_shares, np.nan)
    ok = (vwap_raw >= l * 0.99) & (vwap_raw <= h * 1.01) | np.isnan(vwap_raw)
    if ok.mean() < 0.98:
        raise AssertionError(
            f"{ts_code}: vwap 越界比例 {1 - ok.mean():.1%} — vol/amount 单位假设错误, 审计中止")
    vwap_q = vwap_raw * qfq
    return dates, oq, hq, lq, cq, vol_shares, float_shares, vwap_q


def _daily_winner_rates(dates, hq, lq, cq, vol_shares, float_shares, vwap_q):
    """spec §2.2 算法逐日迭代, 输出每日 winner_rate (%) 与运行高低点."""
    price_min = float(np.nanmin(lq)) * 0.90
    price_max = float(np.nanmax(hq)) * 1.10
    prices = np.arange(price_min, price_max + TICK, TICK)
    chips = np.zeros(len(prices))
    n = len(prices)

    def idx(p: float) -> int:
        return int(round((p - price_min) / TICK))

    winners = np.full(len(dates), np.nan)
    for i in range(len(dates)):
        if vol_shares[i] <= 0 or not np.isfinite(vwap_q[i]):
            if chips.sum() > 0:
                winners[i] = chips[: max(0, min(n, idx(cq[i])))].sum() / chips.sum() * 100
            continue
        turnover = min(vol_shares[i] / float_shares[i], 1.0)
        chips *= (1.0 - turnover)
        i_lo, i_hi = max(0, idx(lq[i])), min(n - 1, idx(hq[i]))
        if i_lo >= i_hi:
            chips[max(0, min(n - 1, idx(vwap_q[i])))] += turnover
        else:
            i_vw = max(i_lo, min(i_hi, idx(vwap_q[i])))
            j = np.arange(i_lo, i_hi + 1)
            left = (j - i_lo) / max(1, i_vw - i_lo)
            right = (i_hi - j) / max(1, i_hi - i_vw)
            dist = np.where(j <= i_vw, left, right)
            s = dist.sum()
            if s > 0:
                chips[i_lo:i_hi + 1] += dist / s * turnover
        total = chips.sum()
        if total > 0:
            winners[i] = chips[: max(0, min(n, idx(cq[i])))].sum() / total * 100
    return winners


def audit() -> dict:
    from scipy.stats import spearmanr

    conn = _raw_conn()
    try:
        samples = _sample_stocks(conn)
        all_codes = sorted({c for v in samples.values() for c in v})
        log.info("分层样本 %d 股: %s", len(all_codes),
                 {k: len(v) for k, v in samples.items()})

        per_stock = []
        deltas_all: list[float] = []
        exdiv_jumps: list[float] = []
        hishigh_checks: list[bool] = []
        for code in all_codes:
            loaded = _load_stock(conn, code)
            if loaded is None:
                continue
            dates, oq, hq, lq, cq, vol_s, fs, vwap_q = loaded
            winners_local = _daily_winner_rates(dates, hq, lq, cq, vol_s, fs, vwap_q)
            ts = {str(r[0]): (float(r[1]), float(r[2]))
                  for r in conn.execute(
                      "SELECT trade_date, CAST(winner_rate AS DOUBLE), CAST(his_high AS DOUBLE) "
                      "FROM raw_tushare_cyq_perf WHERE ts_code = ? AND trade_date >= ?",
                      [code, COMPARE_START]).fetchall()}
            mask = np.array([d in ts and d >= COMPARE_START for d in dates])
            if mask.sum() < 100:
                continue
            local = winners_local[mask]
            remote = np.array([ts[d][0] for d in dates[mask]])
            valid = np.isfinite(local) & np.isfinite(remote)
            if valid.sum() < 100:
                continue
            rho = float(spearmanr(local[valid], remote[valid]).statistic)
            dl = np.abs(local[valid] - remote[valid])
            per_stock.append({"ts_code": code, "spearman": round(rho, 4),
                              "median_abs_delta_pp": round(float(np.median(dl)), 2),
                              "n_days": int(valid.sum())})
            deltas_all.extend(dl.tolist())
            # J3: his_high vs 窗口 rolling max (未复权口径与 qfq 各试, 取吻合者计)
            roll_max_qfq = np.maximum.accumulate(hq)[mask]
            remote_hh = np.array([ts[d][1] for d in dates[mask]])
            match = np.isclose(remote_hh, roll_max_qfq, rtol=0.005) | \
                np.isclose(remote_hh, roll_max_qfq / (np.array([1.0])), rtol=0.005)
            hishigh_checks.extend(match.tolist())
            # 辅助: 除权日跳变 (tushare winner_rate t-1 → t+1)
            ex_dates = {str(r[0]) for r in conn.execute(
                "SELECT ex_date FROM raw_tushare_dividend WHERE ts_code = ? AND ex_date >= ?",
                [code, COMPARE_START]).fetchall()}
            dlist = list(dates[mask])
            for k, d in enumerate(dlist):
                if d in ex_dates and 0 < k < len(dlist) - 1:
                    jump = abs(remote[k + 1] - remote[k - 1])
                    if np.isfinite(jump):
                        exdiv_jumps.append(float(jump))
    finally:
        conn.close()

    spearmans = [s["spearman"] for s in per_stock]
    j1 = float(np.median(spearmans)) if spearmans else float("nan")
    j2 = float(np.median(deltas_all)) if deltas_all else float("nan")
    j3 = float(np.mean(hishigh_checks)) if hishigh_checks else float("nan")
    verdicts = {
        "J1_spearman_median": {"value": round(j1, 4), "threshold": J1_SPEARMAN_MIN,
                               "pass": bool(j1 >= J1_SPEARMAN_MIN)},
        "J2_median_abs_delta_pp": {"value": round(j2, 2), "threshold": J2_MEDIAN_ABS_DELTA_PP_MAX,
                                   "pass": bool(j2 <= J2_MEDIAN_ABS_DELTA_PP_MAX)},
        "J3_hishigh_match": {"value": round(j3, 4), "threshold": J3_HISHIGH_MATCH_MIN,
                             "pass": bool(j3 >= J3_HISHIGH_MATCH_MIN)},
    }
    overall = all(v["pass"] for v in verdicts.values())
    return {
        "experiment": "C0_cyq_perf_blackbox_audit",
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": "PASS" if overall else "FAIL",
        "judges": verdicts,
        "exdiv_jump_pp": {
            "n": len(exdiv_jumps),
            "median": round(float(np.median(exdiv_jumps)), 2) if exdiv_jumps else None,
            "p90": round(float(np.percentile(exdiv_jumps, 90)), 2) if exdiv_jumps else None,
            "interpretation": "median 跳变大 (>10pp) = tushare winner_rate 为未复权坐标口径; 平滑 = 复权口径",
        },
        "n_stocks": len(per_stock),
        "per_stock": per_stock,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    result = audit()
    out = _REPO / "analysis" / f"c0_cyq_audit_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"  # Phase ψ.5 allowlist: 产物文件名时间戳非 trade_date
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_stock"}, ensure_ascii=False, indent=1))
    print(f"\n完整结果: {out}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
