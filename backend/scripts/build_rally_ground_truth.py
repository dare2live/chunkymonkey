"""build_rally_ground_truth — 结构型主升浪 D1 ground truth 重建 (生产级写入器)。

owner: 用户定 (2026-06-17) 主升浪形态 = 你那张图: 长期横盘底 + 多头排列 + 平滑拉升 + 底→顶>60%;
  universe 按 services.universe 硬真相源 (白名单 60/00/30/68, 排北交所/三板/ETF + ST + 退市)。
  替代旧 fact_rally_ground_truth (突破≥50% 读法 + 含北交所 3.1% 污染, 已 DROP 2026-06-17)。

监督式 episode-first (MASTER §5 结果倒推): D1 = 标出每股每次主升浪 (底=起涨点, 顶=卖点),
  作为后续 D2 入场点特征/D3 公式/D4 OOS 的标签 y。本表是新 D1 锚。

检测漏斗 (每层透明):
  L0 底→顶 swing: 显著波段底 (前后 LOWWIN 最低) → MAXFWD 内峰, 峰/底-1>=GAIN(60%), 峰距>=MINDUR。
  L1 universe: services.universe 硬门 — 白名单前缀 + 非 ST(PIT, raw_tushare_stock_st) + 非退市(末K线>=数据末-90日)。
  L2 多头排列: 拉升期内某日 MA5>MA10>MA20>MA30>MA60。
  L3 长底: 底前 120 日 >=BASEMIN 日收盘在 底*[0.85,1.25] (长期横盘)。
  L4 平滑: 拉升途中 close 路径 max_dd > DDFLOOR (-30%, 无深调)。

源: market.price_kline_qfq_tushare (K线真相源) + tushare_raw.raw_tushare_stock_st (PIT ST)。
写: smartmoney.fact_rally_ground_truth (L1_foundation)。
强制: services.universe.assert_universe_clean 硬门 (最终股票集 0 排除股, 否则 raise)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/build_rally_ground_truth.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=生产GT写入器, 多库ATTACH; manifest path; market/raw只读
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.universe import (
    ACTIVE_A_SHARE_PREFIXES,
    DELISTED_NO_TRADE_DAYS,
    assert_universe_clean,
    is_st_on,
    load_st_calendar,
)

log = logging.getLogger("build_gt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# --- 结构型主升浪参数 (形态以用户为准, MASTER §5; 全为结构常数非拟合值) ---
LOWWIN = 20      # rule-compliance: ok evidence=波段底确认窗(前后20日最低), 结构常数
MAXFWD = 250     # rule-compliance: ok evidence=底→顶前瞻上限(~1年完整主升浪), 结构常数
GAIN = 0.60      # rule-compliance: ok evidence=用户口述底→顶>60%(MASTER§5), 主升浪阈值
MINDUR = 20      # rule-compliance: ok evidence=峰距底>=20日排单日尖峰, 结构常数
BASEMIN = 40     # rule-compliance: ok evidence=底前120日>=40日在底附近=长底盘整(用户图特征), 结构常数
DDFLOOR = -0.30  # rule-compliance: ok evidence=拉升途中max_dd下限(平滑, 用户图无深调), 结构常数
BASE_LOOKBACK = 120  # rule-compliance: ok evidence=长底回看窗(底前120日), 结构常数
SCAN_START = "2019-01-01"  # rule-compliance: ok evidence=K线全史起点(market数据起点), 主升浪扫描窗
TAXONOMY_VERSION = "universe_v1_20260617"  # rule-compliance: ok evidence=universe规则版本戳(切分类源时换), 溯源常数


def _ma(c, w):
    return pd.Series(c).rolling(w).mean().to_numpy()


def detect_episodes(code, dates, highs, lows, closes, st_cal):
    """对单股扫结构型主升浪, 返回 dict 列表 (含漏斗每层 flag)。"""
    n = len(closes)
    if n < BASE_LOOKBACK:
        return []
    m5, m10, m20, m30, m60 = (_ma(closes, w) for w in (5, 10, 20, 30, 60))
    out = []
    covered = -1
    i = max(LOWWIN, 60)
    while i < n - MINDUR:
        if i <= covered:
            i += 1
            continue
        lo_win = lows[max(i - LOWWIN, 0): min(i + LOWWIN + 1, n)]
        if lows[i] == lo_win.min() and lows[i] > 0:
            fwd_hi = highs[i + 1: min(i + 1 + MAXFWD, n)]
            if len(fwd_hi):
                po = int(np.argmax(fwd_hi)) + 1
                pk = float(fwd_hi.max())
                gain = pk / lows[i] - 1.0
                if gain >= GAIN and po >= MINDUR:
                    pk_idx = i + po
                    path = closes[i: pk_idx + 1]
                    dd = float(np.min(path / np.maximum.accumulate(path) - 1)) if len(path) else 0.0
                    base = int(np.sum(
                        (closes[max(i - BASE_LOOKBACK, 0): i] <= lows[i] * 1.25)
                        & (closes[max(i - BASE_LOOKBACK, 0): i] >= lows[i] * 0.85)
                    ))
                    seg = slice(i, pk_idx + 1)
                    bull = bool(np.any(
                        (m5[seg] > m10[seg]) & (m10[seg] > m20[seg])
                        & (m20[seg] > m30[seg]) & (m30[seg] > m60[seg])
                    ))
                    # PIT ST: 拉升期任一抽样日 ST → 标记 (universe 硬门排除)
                    st_in = any(
                        is_st_on(code, str(dates[j]).replace("-", ""), st_cal)
                        for j in range(i, min(pk_idx + 1, n), 10)
                    )
                    out.append(dict(
                        stock_code=code, bottom_date=str(dates[i]), peak_date=str(dates[pk_idx]),
                        gain_to_peak_pct=round(gain, 4), peak_offset_days=po,
                        base_days=base, bull_aligned=bull, path_max_dd_pct=round(dd, 4),
                        st_in_episode=st_in,
                    ))
                    covered = pk_idx
        i += 1
    return out


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("smartmoney")), read_only=False)  # rule-compliance: ok evidence=写GT到smartmoney(L1); manifest path
    con.execute(f"ATTACH '{mf.path_for('market')}' AS mk (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")

    log.info("载入 K线 + PIT ST 日历...")
    arr = con.execute(
        f"SELECT code, date, high, low, close FROM mk.price_kline_qfq_tushare "
        f"WHERE date>='{SCAN_START}' AND close>0 ORDER BY code, date"
    ).fetchnumpy()
    # PIT ST 日历 (单一计算点, 经 universe.load_st_calendar; tr attach 内的 raw 表)
    st_rows = con.execute(
        "SELECT DISTINCT SUBSTR(ts_code,1,6) AS code, REPLACE(trade_date,'-','') AS d FROM tr.raw_tushare_stock_st"
    ).fetchall()
    st_cal: dict[str, set] = {}
    for code, d in st_rows:
        st_cal.setdefault(code, set()).add(d)

    data_end = con.execute("SELECT MAX(date) FROM mk.price_kline_qfq_tushare").fetchone()[0]
    de = str(data_end).replace("-", "")

    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])

    funnel = dict(L0_swing=0, L1_universe=0, L2_bull=0, L3_base=0, L4_smooth=0)
    keep = []
    log.info("扫 %d 股 结构型主升浪...", len(uniq))
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        code = str(uniq[ci])
        dts = arr["date"][s:e].astype(str)
        if len(dts) < BASE_LOOKBACK:
            continue
        in_uni = code[:2] in ACTIVE_A_SHARE_PREFIXES
        last_dt = dts[-1].replace("-", "")
        delisted = (pd.to_datetime(de) - pd.to_datetime(last_dt)).days > int(DELISTED_NO_TRADE_DAYS)
        eps = detect_episodes(
            code, dts,
            arr["high"][s:e].astype(float), arr["low"][s:e].astype(float), arr["close"][s:e].astype(float),
            st_cal,
        )
        for ep in eps:
            funnel["L0_swing"] += 1
            if not (in_uni and not delisted and not ep["st_in_episode"]):
                continue
            funnel["L1_universe"] += 1
            if not ep["bull_aligned"]:
                continue
            funnel["L2_bull"] += 1
            if ep["base_days"] < BASEMIN:
                continue
            funnel["L3_base"] += 1
            if ep["path_max_dd_pct"] <= DDFLOOR:
                continue
            funnel["L4_smooth"] += 1
            keep.append(ep)

    df = pd.DataFrame(keep)
    if df.empty:
        log.error("0 主升浪检出 — 中止 (检测/数据异常)")
        con.close()
        return

    # === universe 硬门: 最终股票集 0 排除股, 否则 raise (交易日历级真相源) ===
    assert_universe_clean(df["stock_code"].unique().tolist(), context="fact_rally_ground_truth")
    log.info("[universe 硬门] PASS — 0 排除股")

    built_at = datetime.now(timezone.utc)
    df = df[["stock_code", "bottom_date", "peak_date", "gain_to_peak_pct", "peak_offset_days",
             "base_days", "bull_aligned", "path_max_dd_pct"]].copy()
    df["is_true_rally"] = True
    df["fwd_window_len"] = MAXFWD
    df["taxonomy_version"] = TAXONOMY_VERSION
    df["built_at"] = built_at

    con.execute("DROP TABLE IF EXISTS fact_rally_ground_truth")
    con.execute("""
        CREATE TABLE fact_rally_ground_truth (
            stock_code        VARCHAR NOT NULL,
            bottom_date       DATE    NOT NULL,
            peak_date         DATE    NOT NULL,
            gain_to_peak_pct  DOUBLE  NOT NULL,
            peak_offset_days  INTEGER NOT NULL,
            base_days         INTEGER NOT NULL,
            bull_aligned      BOOLEAN NOT NULL,
            path_max_dd_pct   DOUBLE  NOT NULL,
            is_true_rally     BOOLEAN NOT NULL,
            fwd_window_len    INTEGER NOT NULL,
            taxonomy_version  VARCHAR NOT NULL,
            built_at          TIMESTAMP NOT NULL,
            PRIMARY KEY (stock_code, bottom_date)
        )
    """)
    con.register("gt_df", df)
    con.execute("INSERT INTO fact_rally_ground_truth SELECT * FROM gt_df")
    con.execute("CREATE INDEX idx_rally_gt_bottom ON fact_rally_ground_truth(bottom_date)")
    n_final = con.execute("SELECT COUNT(*) FROM fact_rally_ground_truth").fetchone()[0]
    n_stock = con.execute("SELECT COUNT(DISTINCT stock_code) FROM fact_rally_ground_truth").fetchone()[0]
    con.close()

    print(f"\n结构型主升浪 D1 ground truth 重建完成 (用户图样型: 长底+多头排列+平滑+底→顶>{GAIN*100:.0f}%)")
    print(f"  漏斗: L0 swing {funnel['L0_swing']:,} → L1 universe {funnel['L1_universe']:,} "
          f"→ L2 多头排列 {funnel['L2_bull']:,} → L3 长底 {funnel['L3_base']:,} → L4 平滑 {funnel['L4_smooth']:,}")
    print(f"  落库 fact_rally_ground_truth: {n_final:,} 主升浪 / {n_stock:,} 股 (universe 硬门 PASS)")
    print(f"  底→顶: 中位 {df['gain_to_peak_pct'].median()*100:.0f}% / 拉升中位 {df['peak_offset_days'].median():.0f}日 "
          f"/ 长底中位 {df['base_days'].median():.0f}日")
    yr = df["bottom_date"].str[:4]
    print(f"  分年(底): " + " ".join(f"{y}:{int((yr==y).sum())}" for y in sorted(yr.unique())))


if __name__ == "__main__":
    main()
