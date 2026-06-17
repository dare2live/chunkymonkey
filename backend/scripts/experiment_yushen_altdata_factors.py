"""experiment_yushen_altdata_factors — 另类数据(龙虎榜+券商预期)买点因子检验 (主会话主导, 2026-06-17)。

owner: 用户决议"补另类数据再挖入场" — 价量/筹码因子已测尽(8角度≈regime-beta), 试**信息维度不同的另类数据**:
  机构席位(龙虎榜)/券商盈利预期上调(report_rc) 是否带价量没有的"机构conviction"信号, 能否改善二次突破买点。
核心假设: 二次突破 + 前兆窗内(机构席位净买 / 券商集中上调评级目标价) = 机构确认 → 更可能真主升浪/含成本盈。
PIT: 另类数据前兆只用 [entry-N, entry-1] (信号日前, 公告类锚 ann/report_date <= 信号日)。二次突破 entries_A + trade(含成本)复用。
指标: 各 alt 因子对"含成本盈"的 AUC/lift + "有机构确认 vs 无"的含成本均值/胜率(TRAIN/OOS) + shuffle null。
源: market K线 + tushare_raw(top_list/top_inst龙虎榜 已有2018-2026 / report_rc券商研报 504k已有)。stk_surv机构调研待注册补(其后加)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_altdata_factors.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from bisect import bisect_left, bisect_right

import duckdb  # rule-compliance: ok evidence=只读另类数据买点因子; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A, trade

log = logging.getLogger("altdata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)
LHB_WIN = 20    # rule-compliance: ok evidence=龙虎榜前兆窗(月级), 结构常数
RPT_WIN = 30    # rule-compliance: ok evidence=券商研报前兆窗(月级, 研报更稀), 结构常数
BUY_RATINGS = ("买入", "增持", "强烈推荐", "推荐", "强推", "优于大市", "跑赢行业")  # rule-compliance: ok evidence=券商看多评级词表(tushare rating字段中文), 业务词典


def _ymd(s):
    s = str(s)
    return s.replace("-", "")


def build_event_index(df, code_col, date_col):
    """{code6: (sorted_dates[YYYYMMDD], 附带行索引)} 供 trailing 窗 bisect。"""
    df = df.copy()
    df["code6"] = df[code_col].str[:6]
    df["d"] = df[date_col].astype(str)
    out = {}
    for code, g in df.sort_values("d").groupby("code6"):
        out[code] = (g["d"].to_numpy(), g)
    return out


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    log.info("载入 K线 + 另类数据(龙虎榜/券商研报)...")
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date>='2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留周线预热(同鱼身系列)
    toplist = con.execute("SELECT ts_code, trade_date, net_rate FROM tr.raw_tushare_top_list").df()
    topinst = con.execute("SELECT ts_code, trade_date, exalter, net_buy FROM tr.raw_tushare_top_inst").df()
    rpt = con.execute("SELECT ts_code, report_date, rating, tp FROM tr.raw_tushare_report_rc").df()
    surv = con.execute("SELECT ts_code, surv_date, rece_org FROM tr.raw_tushare_stk_surv").df()  # 机构调研(2021-08+, tinyshare解封)
    con.close()

    # 另类数据按 code 索引 (trailing 窗 bisect)
    lhb_idx = build_event_index(toplist, "ts_code", "trade_date")
    topinst["is_inst"] = topinst["exalter"].fillna("").str.contains("机构")
    inst_idx = build_event_index(topinst[topinst["is_inst"]], "ts_code", "trade_date")
    rpt["is_buy"] = rpt["rating"].fillna("").apply(lambda x: any(b in str(x) for b in BUY_RATINGS))
    rpt_idx = build_event_index(rpt, "ts_code", "report_date")
    surv_idx = build_event_index(surv, "ts_code", "surv_date")

    def trailing(idx, code, ed_ymd, win_days, valcol=None, valfn=None):
        """code 在 [ed - win_days(自然日近似), ed-1] 的事件数 + 可选值聚合。"""
        if code not in idx:
            return 0, 0.0
        dates, g = idx[code]
        # 自然日窗近似: ed_ymd 往前 win_days*1.5 自然日 (含周末)
        lo = (pd.to_datetime(ed_ymd) - pd.Timedelta(days=int(win_days * 1.6))).strftime("%Y%m%d")
        a, b = bisect_left(dates, lo), bisect_right(dates, str(int(ed_ymd) - 1))
        n = b - a
        if n == 0:
            return 0, 0.0
        if valcol:
            v = float(np.nansum(g.iloc[a:b][valcol].to_numpy(dtype=float)))
            return n, v
        return n, 0.0

    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    log.info("扫二次突破 + 另类数据前兆...")
    rows = []
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci]); code = str(uniq[ci])
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        o, h, v = arr["open"][s:e].astype(float), arr["high"][s:e].astype(float), arr["volume"][s:e].astype(float)
        dates = arr["date"][s:e].astype(str)
        state = _weekly_state(dates, c)
        for ei in entries_A(c, state, v):
            ed = _ymd(dates[ei])  # 信号日(决策日T); 另类数据前兆只用 <=T-1
            lhb_n, _ = trailing(lhb_idx, code, ed, LHB_WIN)
            _, lhb_net = trailing(lhb_idx, code, ed, LHB_WIN, valcol="net_rate")
            inst_n, inst_net = trailing(inst_idx, code, ed, LHB_WIN, valcol="net_buy")
            rpt_n, _ = trailing(rpt_idx, code, ed, RPT_WIN)
            rdates, rg = rpt_idx.get(code, (np.array([]), None))
            buy_n = 0; tp_upside = np.nan
            if rpt_n > 0 and rg is not None:
                lo = (pd.to_datetime(ed) - pd.Timedelta(days=int(RPT_WIN * 1.6))).strftime("%Y%m%d")
                a, b = bisect_left(rdates, lo), bisect_right(rdates, str(int(ed) - 1))
                sub = rg.iloc[a:b]
                buy_n = int(sub["is_buy"].sum())
                tps = sub["tp"].dropna().to_numpy(dtype=float)
                if len(tps) and c[ei] > 0:
                    tp_upside = float(np.nanmax(tps) / c[ei] - 1)
            # 机构调研前兆 (surv_cnt=调研次数, surv_org=不同接待机构数=关注广度)
            surv_n, _ = trailing(surv_idx, code, ed, RPT_WIN)
            surv_org = 0
            sdates, sg = surv_idx.get(code, (np.array([]), None))
            if surv_n > 0 and sg is not None:
                lo = (pd.to_datetime(ed) - pd.Timedelta(days=int(RPT_WIN * 1.6))).strftime("%Y%m%d")
                a, b = bisect_left(sdates, lo), bisect_right(sdates, str(int(ed) - 1))
                surv_org = int(sg.iloc[a:b]["rece_org"].nunique())
            _, _, r = trade(o, h, c, state, ei)
            rows.append(dict(entry_date=dates[ei + 1] if ei + 1 < len(dates) else dates[ei], ret=r,
                             lhb_cnt=lhb_n, lhb_net=lhb_net, inst_cnt=inst_n, inst_net=inst_net,
                             rpt_cnt=rpt_n, rpt_buy=buy_n, tp_upside=tp_upside if not np.isnan(tp_upside) else 0.0,
                             surv_cnt=surv_n, surv_org=surv_org,
                             has_inst=1 if inst_n > 0 else 0, has_lhb=1 if lhb_n > 0 else 0,
                             has_buyrpt=1 if buy_n > 0 else 0, has_surv=1 if surv_n > 0 else 0))
    panel = pd.DataFrame(rows)
    panel["label"] = (panel["ret"] > 0).astype(int)
    panel["seg"] = np.where(panel["entry_date"] < OOS_CUT, "TRAIN", "OOS")
    panel["any_conf"] = ((panel["has_inst"] + panel["has_lhb"] + panel["has_buyrpt"] + panel["has_surv"]) > 0).astype(int)
    log.info("二次突破候选 %s (含成本盈 %.1f%%)", f"{len(panel):,}", panel["label"].mean() * 100)

    def auc(s, y):
        m = ~np.isnan(s); s, y = s[m], y[m]
        if len(y) < 30 or y.sum() < 5 or y.sum() == len(y):
            return np.nan
        o = np.argsort(s); rk = np.empty(len(s)); rk[o] = np.arange(1, len(s) + 1)
        return float((rk[y == 1].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (len(y) - y.sum())))

    print(f"\n另类数据(机构调研+龙虎榜+券商预期 3源)买点因子检验 (PRIMARY=二次突破, 看机构conviction是否改善; 含成本)")
    print(f"  二次突破候选 {len(panel):,} (含成本盈 {panel['label'].mean()*100:.1f}%) | 有任一机构确认占 {panel['any_conf'].mean()*100:.1f}%")
    print(f"  {'alt因子':12}{'全AUC':>8}{'覆盖率':>8}")
    y = panel["label"].to_numpy()
    ALT_FEATS = ["lhb_cnt", "lhb_net", "inst_cnt", "inst_net", "rpt_cnt", "rpt_buy", "tp_upside", "surv_cnt", "surv_org", "has_inst", "has_lhb", "has_buyrpt", "has_surv"]
    for f in ALT_FEATS:
        sv = panel[f].to_numpy(dtype=float)
        cov = (sv != 0).mean()
        print(f"  {f:12}{(auc(sv, y) or 0):>8.3f}{cov*100:>7.1f}%")
    # 有机构确认 vs 无: 含成本均值/胜率 (分regime)
    print(f"\n  {'regime':7}{'分组':16}{'n':>7}{'含成本均值':>10}{'胜率':>7}")
    res = {}
    for seg in ["TRAIN", "OOS"]:
        for grp, mask in [("有机构确认", panel.any_conf == 1), ("无确认", panel.any_conf == 0),
                          ("机构席位上榜", panel.has_inst == 1), ("券商买入上调", panel.has_buyrpt == 1),
                          ("机构密集调研", panel.has_surv == 1)]:
            sub = panel[(panel.seg == seg) & mask]["ret"].to_numpy()
            if len(sub) < 20:
                continue
            res[(seg, grp)] = dict(n=len(sub), mean=float(sub.mean()), win=float((sub > 0).mean()))
            print(f"  {seg:7}{grp:16}{len(sub):>7,}{sub.mean()*100:>+9.2f}%{(sub>0).mean()*100:>6.1f}%")
    # 裁决: 有确认 vs 无确认 含成本增量 (TRAIN+OOS 稳定?)
    def gap(seg):
        a = res.get((seg, "有机构确认"), {}).get("mean", np.nan)
        b = res.get((seg, "无确认"), {}).get("mean", np.nan)
        return a - b
    g_tr, g_oos = gap("TRAIN"), gap("OOS")
    print(f"\n  --- 裁决 (另类数据机构确认是否改善买点) ---")
    print(f"  有确认−无确认 含成本增量: TRAIN{g_tr*100:+.2f}pp / OOS{g_oos*100:+.2f}pp")
    real = (not np.isnan(g_tr) and not np.isnan(g_oos) and g_tr > 0.01 and g_oos > 0.01)
    if real:
        verdict = f"另类数据机构确认改善买点: 有确认 vs 无确认 含成本增量 TRAIN{g_tr*100:+.1f}/OOS{g_oos*100:+.1f}pp 双段正 → 龙虎榜机构席位/券商上调是真信息增量(价量没有的); 下一步 +stk_surv机构调研 + 组合NAV + meta secondary"
    elif (not np.isnan(g_oos) and g_oos > 0.01) or (not np.isnan(g_tr) and g_tr > 0.01):
        verdict = f"另类数据部分有效(单段): 仅一段确认>无 → 机构信号regime依赖, 须+stk_surv+更多窗再验"
    else:
        verdict = f"另类数据机构确认未改善买点: 有确认vs无 含成本无稳定增量 → 龙虎榜/券商预期对二次突破买点也无增量 = 入场空间含另类数据仍≈beta, 强化转出场/仓位裁决"
    print(f"  → {verdict}")

    # meta-labeling secondary: 3源alt因子组合 LightGBM → 下注集(top30%)含成本 vs 全集 + shuffle-null
    # 平市信号在TRAIN, 故 train早段(<2024-06)拟合, 在 平市holdout(2024-06..OOS_CUT) + OOS(bull) 分别验
    meta = {}
    try:
        import lightgbm as lgb
        FLAT_SPLIT = "2024-06-01"  # rule-compliance: ok evidence=TRAIN内早/晚分界(平市holdout验alt信号), 结构常数
        ed_arr = panel["entry_date"].to_numpy()
        trm = ed_arr < FLAT_SPLIT
        X = panel[ALT_FEATS].to_numpy(float)
        yv, ret = panel["label"].to_numpy(), panel["ret"].to_numpy()
        if trm.sum() > 200 and yv[trm].sum() > 20:
            def ctor():
                return lgb.LGBMClassifier(n_estimators=120, num_leaves=15, learning_rate=0.03, min_child_samples=40,
                                          subsample=0.8, colsample_bytree=0.7, reg_lambda=1.0, class_weight="balanced", verbose=-1)
            m = ctor(); m.fit(X[trm], yv[trm])
            rng = np.random.RandomState(20260617)  # rule-compliance: ok evidence=固定种子复现shuffle null
            for seg_name, seg_mask in [("平市holdout", (ed_arr >= FLAT_SPLIT) & (ed_arr < OOS_CUT)), ("OOS牛市", ed_arr >= OOS_CUT)]:
                if seg_mask.sum() < 50:
                    continue
                p = m.predict_proba(X[seg_mask])[:, 1]
                rr = ret[seg_mask]
                bet = p >= np.nanpercentile(p, 70)
                all_m, bet_m = float(rr.mean()), float(rr[bet].mean()) if bet.sum() > 5 else np.nan
                nulls = []
                for _ in range(20):  # rule-compliance: ok evidence=shuffle null 20次, 统计常数
                    ms = ctor(); ms.fit(X[trm], rng.permutation(yv[trm]))
                    ps = ms.predict_proba(X[seg_mask])[:, 1]
                    bs = ps >= np.nanpercentile(ps, 70)
                    if bs.sum() > 5:
                        nulls.append(float(rr[bs].mean()))
                np95 = float(np.nanpercentile(nulls, 95)) if nulls else np.nan
                meta[seg_name] = dict(all_mean=round(all_m, 5), bet_mean=round(bet_m, 5) if not np.isnan(bet_m) else None,
                                      null_p95=round(np95, 5) if not np.isnan(np95) else None,
                                      real=bool(not np.isnan(bet_m) and not np.isnan(np95) and bet_m > np95 and bet_m - all_m > 0.003))
        print(f"\n  --- meta-labeling secondary (3源alt因子组合, 下注集top30% vs 全集, shuffle-null) ---")
        for seg_name, mr in meta.items():
            print(f"  {seg_name}: 全集{mr['all_mean']*100:+.2f}% → 下注集{(mr['bet_mean'] or 0)*100:+.2f}% (shuffle p95 {(mr['null_p95'] or 0)*100:+.2f}%) {'真增量' if mr['real'] else '不显著'}")
    except ImportError:
        print("\n  (lightgbm 未装, 跳过 meta-labeling secondary)")

    run_id = "yushen_altdata_factors_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n": len(panel), "any_conf_cov": round(panel["any_conf"].mean(), 4),
              "gap_train": round(g_tr, 5) if not np.isnan(g_tr) else None, "gap_oos": round(g_oos, 5) if not np.isnan(g_oos) else None,
              "cells": {f"{s}_{g}": res[(s, g)] for (s, g) in res}, "meta_labeling": meta, "summary": verdict[:150]}
    vlabel = "ALTDATA_REAL" if real else ("ALTDATA_PARTIAL" if ((not np.isnan(g_oos) and g_oos > 0.01) or (not np.isnan(g_tr) and g_tr > 0.01)) else "ALTDATA_NO_EDGE")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_altdata_factors", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_altdata_factors verdict={vlabel}")


if __name__ == "__main__":
    main()
