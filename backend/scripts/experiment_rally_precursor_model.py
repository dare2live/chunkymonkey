"""experiment_rally_precursor_model — 监督式结果倒推: 主升浪赢家前兆特征模型 (主会话主导, 2026-06-17)。

owner: 用户 2026-06-16 纠偏 + architect 审计 REVISE (从信号正推退回监督式结果倒推)。
方法论: 已知答案 = fact_rally_ground_truth (43202 突破事件, 4345 TRUE 主升浪赢家 / 38857 非赢家)。
  任务 = 在每个起涨点(event_date)算**前兆特征**(全 PIT <=event_date), 看**什么真正区分赢家 vs 非赢家** (抄答案, 非造前向信号)。
前兆特征 (用户给的通达信指标为配方蓝本, 适配日线; 非照搬):
  - VRN 换手率异常 = turnover_rate / MA(turnover_rate,20) (相对自身20日均值的"放大", 用户核心: 怎么比较换手率放大)。
  - 形态签名: FLZZ放量滞涨(VRN≥1.5&|涨幅|<1.5%&上影≥1.5%=出货/鱼尾) / GWBZ高位 / SLHC缩量回踩 / YGYQ二次启动确认(鱼头)。
  - 资金: 主力净额(tushare moneyflow lg+elg 净) + 暗盘资金(用户OHLC路径权重X_8 × sm/md分单级 日度近似 同花顺暗盘)。
  - 筹码: cyq winner_rate(获利盘) + 集中度(cost_95-cost_5)/cost_50。
裁决: 每特征 winner vs 非winner 的 AUC + top-decile lift(top十分位赢家率/基线10.1%), TRAIN(event<2024-07)/TEST(>=)分段看稳定性。
  全 PIT, 无前向信号无 peek。这是 D3 (前兆模型), 下一步据此建可交易策略 + 鱼头鱼尾出入场 + OOS 2025-06+。
源: market.price_kline_qfq_tushare + tushare_raw(daily_basic/moneyflow/cyq_perf) + smartmoney.fact_rally_ground_truth。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_rally_precursor_model.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读多表建监督前兆面板; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("rally_precursor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

TRAIN_TEST_SPLIT = "2024-07-01"  # rule-compliance: ok evidence=赢家事件内时间分段验稳定性(事件止2025-05), 非业务参数
VRN_MA = 20      # rule-compliance: ok evidence=用户通达信VRN=HSL1/MA(HSL1,20), 换手率异常基线窗
RANGE_N = 60     # rule-compliance: ok evidence=用户GWBZ/DWBZ 60日区间高低位
PRECURSOR_LOOKBACK = 10  # rule-compliance: ok evidence=前兆窗(起涨点前10交易日找YGYQ/FLZZ签名), 结构常数
BASE_RATE = 0.1006  # rule-compliance: ok evidence=4345/43202赢家基线率(lift分母), measured from fact_rally_ground_truth


def compute_features(df):
    """df: 单股 按date升序, 列含 open/high/low/close/volume/amount/turnover_rate/volume_ratio/circ_mv/
    买卖分单级amount/winner_rate/cost_*。返回加了通达信式前兆特征列的 df。"""
    c, o, h, l = df["close"].to_numpy(), df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    vol = df["volume"].to_numpy()
    n = len(df)
    ret = np.concatenate([[0.0], c[1:] / c[:-1] - 1.0])
    df["ret"] = ret
    # VRN 换手率异常 (相对自身20日均值)
    tr = df["turnover_rate"].ffill().fillna(0.0)
    hsl_ma = tr.rolling(VRN_MA, min_periods=5).mean()
    df["VRN"] = (tr / hsl_ma.replace(0, np.nan)).to_numpy()
    # ZDF1 / 上影
    df["ZDF1"] = ret * 100
    mco = np.maximum(c, o)
    df["YXBL"] = np.where(mco > 0, (h - mco) / mco * 100, 0.0)
    # 60日区间位置
    hh = pd.Series(h).rolling(RANGE_N, min_periods=20).max().to_numpy()
    ll = pd.Series(l).rolling(RANGE_N, min_periods=20).min().to_numpy()
    rng = hh - ll
    df["pos60"] = np.where(rng > 0, (c - ll) / rng, 0.5)
    df["GWBZ"] = (df["pos60"] >= 0.7).astype(float)
    df["DWBZ"] = (df["pos60"] <= 0.3).astype(float)
    # FLZZ 放量滞涨 (出货/鱼尾签名)
    df["FLZZ"] = ((df["VRN"] >= 1.5) & (df["ZDF1"].abs() < 1.5) & (df["YXBL"] >= 1.5)).astype(float)
    df["GWZZ"] = (df["FLZZ"].astype(bool) & (df["GWBZ"] > 0)).astype(float)  # 高位放量滞涨=鱼尾警报
    # DZR 大涨 + 缩量回踩 SLHC + 二次启动 YGYQ (鱼头签名)
    dzr = ret >= 0.05
    def hhv5(arr, mask):
        s = pd.Series(np.where(mask, arr, 0.0))
        return s.rolling(5, min_periods=1).max().to_numpy()
    HCJZ, HCVL, HCDJ = hhv5(c, dzr), hhv5(vol, dzr), hhv5(l, dzr)
    slhc = (HCJZ > 0) & (HCVL > 0) & (~dzr) & (vol < HCVL * 0.5) & (c < HCJZ) & (l >= HCDJ)
    df["SLHC"] = slhc.astype(float)
    vrn = df["VRN"].to_numpy()
    ygyq = slhc & np.concatenate([[False], slhc[:-1]]) & (c > o) & (c > np.concatenate([[0], h[:-1]])) & (vrn > np.concatenate([[0], vrn[:-1]]))
    df["YGYQ"] = ygyq.astype(float)
    # 资金: 主力净额比 + 暗盘资金比 (用户OHLC路径权重X_8 适配日线)
    tot = (df.get("buy_sm_amount", 0) + df.get("buy_md_amount", 0) + df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0)
           + df.get("sell_sm_amount", 0) + df.get("sell_md_amount", 0) + df.get("sell_lg_amount", 0) + df.get("sell_elg_amount", 0)).replace(0, np.nan)
    main_net = (df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0) - df.get("sell_lg_amount", 0) - df.get("sell_elg_amount", 0))
    df["main_ratio"] = (main_net / tot).fillna(0.0).to_numpy()
    # 暗盘 X_8 = OHLC路径权重 (X_7=6段路径和, cap 0.8)
    prevc = np.concatenate([[c[0]], c[:-1]])
    x1 = (o - prevc) / np.where(prevc > 0, prevc, 1)
    x2 = np.where(o > 0, (c - o) / o, 0); x3 = np.where(o > 0, (h - o) / o, 0)
    x4 = np.where(h > 0, (c - h) / h, 0); x5 = np.where(o > 0, (l - o) / o, 0); x6 = np.where(l > 0, (c - l) / l, 0)
    x7 = x1 + x2 + x3 + x4 + x5 + x6
    x8 = np.where(x7 >= 1, 0.8, x7)
    smmd_buy = (df.get("buy_sm_amount", 0) + df.get("buy_md_amount", 0)).to_numpy()
    smmd_sell = (df.get("sell_sm_amount", 0) + df.get("sell_md_amount", 0)).to_numpy()
    dark = np.where(x8 > 0, smmd_buy * x8, smmd_sell * x8)
    df["dark_ratio"] = (pd.Series(dark) / tot.reset_index(drop=True)).fillna(0.0).to_numpy()
    df["flow_ratio"] = df["main_ratio"] + df["dark_ratio"]
    # 筹码
    df["winner_rate"] = df.get("winner_rate", pd.Series(np.nan, index=df.index)).astype(float)
    c50 = df.get("cost_50pct", pd.Series(np.nan, index=df.index)).replace(0, np.nan)
    df["concentration"] = ((df.get("cost_95pct", np.nan) - df.get("cost_5pct", np.nan)) / c50).to_numpy()
    return df


def event_features(df, ei):
    """在 event idx ei 取 PIT 前兆特征 (全 <=ei)。"""
    if ei < 25:
        return None
    lb = slice(max(ei - PRECURSOR_LOOKBACK + 1, 0), ei + 1)
    vrn = df["VRN"].to_numpy()
    mr, dr, fr = df["main_ratio"].to_numpy(), df["dark_ratio"].to_numpy(), df["flow_ratio"].to_numpy()
    wr = df["winner_rate"].to_numpy()
    feat = {
        "VRN_t": vrn[ei], "VRN_5dmean": np.nanmean(vrn[max(ei - 4, 0):ei + 1]), "VRN_max10d": np.nanmax(vrn[lb]),
        "pos60_t": df["pos60"].to_numpy()[ei],
        "YGYQ_any10d": float(df["YGYQ"].to_numpy()[lb].max()),  # 鱼头二次启动签名
        "SLHC_cnt10d": float(df["SLHC"].to_numpy()[lb].sum()),
        "FLZZ_cnt10d": float(df["FLZZ"].to_numpy()[lb].sum()),  # 出货/鱼尾警报
        "GWZZ_any10d": float(df["GWZZ"].to_numpy()[lb].max()),  # 高位放量滞涨
        "main_5dsum": np.nansum(mr[max(ei - 4, 0):ei + 1]),
        "main_20dsum": np.nansum(mr[max(ei - 19, 0):ei + 1]),  # 主力20日持续吸筹
        "dark_5dsum": np.nansum(dr[max(ei - 4, 0):ei + 1]),
        "flow_5dsum": np.nansum(fr[max(ei - 4, 0):ei + 1]),
        "winner_rate_t": wr[ei],
        "winner_rate_chg20d": (wr[ei] - wr[ei - 20]) if (ei >= 20 and not np.isnan(wr[ei]) and not np.isnan(wr[ei - 20])) else np.nan,
        "concentration_t": df["concentration"].to_numpy()[ei],
        "ret20d": df["close"].to_numpy()[ei] / df["close"].to_numpy()[ei - 20] - 1 if ei >= 20 else np.nan,
        "ret60d": df["close"].to_numpy()[ei] / df["close"].to_numpy()[ei - 60] - 1 if ei >= 60 else np.nan,
        "volratio_t": df["volume_ratio"].to_numpy()[ei] if "volume_ratio" in df else np.nan,
    }
    return feat


def auc_rank(scores, labels):
    """rank-based AUC = P(score_pos > score_neg)。NaN 剔除。"""
    m = ~np.isnan(scores)
    s, y = scores[m], labels[m]
    if y.sum() == 0 or y.sum() == len(y) or len(y) < 30:
        return np.nan, m.sum()
    order = np.argsort(s)
    ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    n_pos = y.sum(); n_neg = len(y) - n_pos
    auc = (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return float(auc), int(m.sum())


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    log.info("载入事件 + 多表 join (price+daily_basic+moneyflow+cyq)...")
    events = con.execute("SELECT stock_code, event_date, is_true_rally, gain_to_peak_pct FROM sm.fact_rally_ground_truth").df()
    # 大 join: 以 price_kline 为基, LEFT JOIN 三表 (code+date 归一化)
    q = """
    SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount,
           db.turnover_rate, db.volume_ratio, db.circ_mv,
           mf.buy_sm_amount, mf.buy_md_amount, mf.buy_lg_amount, mf.buy_elg_amount,
           mf.sell_sm_amount, mf.sell_md_amount, mf.sell_lg_amount, mf.sell_elg_amount,
           cy.winner_rate, cy.cost_5pct, cy.cost_50pct, cy.cost_95pct
    FROM price_kline_qfq_tushare k
    LEFT JOIN tr.raw_tushare_daily_basic db ON SUBSTR(db.ts_code,1,6)=k.code AND db.trade_date=REPLACE(k.date::VARCHAR,'-','')
    LEFT JOIN tr.raw_tushare_moneyflow mf ON SUBSTR(mf.ts_code,1,6)=k.code AND mf.trade_date=REPLACE(k.date::VARCHAR,'-','')
    LEFT JOIN tr.raw_tushare_cyq_perf cy ON SUBSTR(cy.ts_code,1,6)=k.code AND cy.trade_date=REPLACE(k.date::VARCHAR,'-','')
    WHERE k.date >= '2018-10-01' AND k.close>0 ORDER BY k.code, k.date
    """
    big = con.execute(q).df()
    con.close()
    log.info("join 完成 %s 行; 按股算前兆特征...", f"{len(big):,}")

    ev_by_code = {code: g for code, g in events.groupby("stock_code")}
    rows = []
    for code, g in big.groupby("code"):
        if code not in ev_by_code or len(g) < 80:
            continue
        g = g.reset_index(drop=True)
        g = compute_features(g)
        date_to_idx = {d: i for i, d in enumerate(g["date"].astype(str))}
        for _, ev in ev_by_code[code].iterrows():
            ed = str(ev["event_date"])
            if len(ed) == 8 and ed.isdigit():  # 20190404 → 2019-04-04 (事件日YYYYMMDD, kline日YYYY-MM-DD)
                ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"
            ei = date_to_idx.get(ed)
            if ei is None:
                continue
            feat = event_features(g, ei)
            if feat is None:
                continue
            feat["is_win"] = int(ev["is_true_rally"])
            feat["event_date"] = str(ev["event_date"])
            rows.append(feat)
    panel = pd.DataFrame(rows)
    log.info("前兆面板: %s 事件 (%s 赢家)", f"{len(panel):,}", f"{int(panel['is_win'].sum()):,}")

    feat_cols = [c for c in panel.columns if c not in ("is_win", "event_date")]
    tr_mask = panel["event_date"] < TRAIN_TEST_SPLIT
    te_mask = ~tr_mask
    print(f"\n监督式主升浪前兆特征判别力 (赢家 vs 非赢家, AUC>0.5=有区分, top-decile lift vs 基线{BASE_RATE*100:.1f}%)")
    print(f"  面板 {len(panel):,} 事件 / TRAIN(<{TRAIN_TEST_SPLIT}) {int(tr_mask.sum()):,} / TEST {int(te_mask.sum()):,}")
    print(f"  {'特征':16}{'全AUC':>8}{'n':>8}{'TRAIN_AUC':>10}{'TEST_AUC':>10}{'top十分位赢家率':>14}{'lift':>7}")
    results = {}
    for fc in feat_cols:
        s = panel[fc].to_numpy(dtype=float)
        y = panel["is_win"].to_numpy()
        auc_all, n = auc_rank(s, y)
        auc_tr, _ = auc_rank(s[tr_mask.to_numpy()], y[tr_mask.to_numpy()])
        auc_te, _ = auc_rank(s[te_mask.to_numpy()], y[te_mask.to_numpy()])
        # top decile lift (按特征值高端)
        m = ~np.isnan(s)
        lift, tdr = np.nan, np.nan
        if m.sum() > 100:
            thr = np.nanpercentile(s[m], 90)
            top = m & (s >= thr)
            if top.sum() > 10:
                tdr = y[top].mean()
                lift = tdr / BASE_RATE
        results[fc] = dict(auc_all=auc_all, auc_tr=auc_tr, auc_te=auc_te, top_decile_rate=tdr, lift=lift, n=n)
        print(f"  {fc:16}{auc_all:>8.3f}{n:>8,}{auc_tr:>10.3f}{auc_te:>10.3f}{(tdr*100 if not np.isnan(tdr) else 0):>13.1f}%{(lift if not np.isnan(lift) else 0):>7.2f}")

    # 裁决: 哪些前兆特征 train+test AUC 都明显偏离 0.5 (稳定判别)
    strong = {fc: r for fc, r in results.items()
              if not np.isnan(r["auc_tr"]) and not np.isnan(r["auc_te"])
              and abs(r["auc_tr"] - 0.5) > 0.03 and abs(r["auc_te"] - 0.5) > 0.03
              and np.sign(r["auc_tr"] - 0.5) == np.sign(r["auc_te"] - 0.5)}
    print(f"\n  --- 裁决 (监督式: 前兆能否抄出赢家答案) ---")
    if strong:
        ranked = sorted(strong.items(), key=lambda kv: abs(kv[1]["auc_all"] - 0.5), reverse=True)
        print(f"  {len(strong)} 个前兆特征 TRAIN+TEST 同向稳定判别 (|AUC-0.5|>0.03):")
        for fc, r in ranked[:8]:
            direc = "高值利好赢家" if r["auc_all"] > 0.5 else "高值利空(低值利好)"
            print(f"    {fc}: 全AUC{r['auc_all']:.3f} (train{r['auc_tr']:.3f}/test{r['auc_te']:.3f}) lift{r['lift']:.2f} — {direc}")
        verdict = f"答案可抄(部分): {len(strong)}个前兆稳定判别赢家, 最强={ranked[0][0]}(AUC{ranked[0][1]['auc_all']:.3f}). 下一步: 组合这些前兆建打分→候选池排名→鱼头入场/鱼尾出场→含成本OOS 2025-06+ (可Optuna/Modal调权重阈值)"
    else:
        verdict = "前兆单特征均弱判别(|AUC-0.5|<0.03稳定): 单因子抄不出答案, 须多因子非线性组合(LightGBM)或前兆窗/形态定义再精化; 或赢家在买点确实难判(印证旧AUC0.51), edge靠出场/分层非买点选股"
    print(f"  → {verdict}")

    run_id = "rally_precursor_model_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_events": len(panel), "n_win": int(panel["is_win"].sum()), "n_strong_features": len(strong),
              "features": {fc: {"auc_all": round(r["auc_all"], 4) if not np.isnan(r["auc_all"]) else None,
                                "auc_tr": round(r["auc_tr"], 4) if not np.isnan(r["auc_tr"]) else None,
                                "auc_te": round(r["auc_te"], 4) if not np.isnan(r["auc_te"]) else None,
                                "lift": round(r["lift"], 3) if not np.isnan(r["lift"]) else None} for fc, r in results.items()},
              "summary": verdict[:150]}
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="rally_precursor_supervised", verdict="PRECURSOR_DISCRIMINATION", judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=rally_precursor_supervised")


if __name__ == "__main__":
    main()
