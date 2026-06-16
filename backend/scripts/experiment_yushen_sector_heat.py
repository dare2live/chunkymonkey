"""experiment_yushen_sector_heat — 二次突破 × 板块L2同期热度 条件检验 (主会话主导, 2026-06-17)。

owner: 分层实测(板块L2基础率15.6pp=最强维) + SOTA研究(可交易信号是板块同期热度/轮动非静态membership)。
核心论题检验: "二次突破入场 IN 资金正涌入的热门L2板块" 是否比 "冷板块里同样的突破" edge 显著更高且跨regime稳定?
  = 鱼身延续 + 板块轮动加持。若是 → 板块L2热度是真条件层(meta-labeling 的强 context); 若否 → 板块只是基础率幸存非可交易。
板块L2热度 (PIT, 全 <=t): 每(申万L2, date) 算 sector_mom=成员20日收益均值 + sector_flow=成员主力净额比均值, 当日截面rank。
入场: 复用 二次突破 entries_A + trade (DRY, experiment_yushen_clean_baseline)。含成本13bps。
分 TRAIN(<2025-06 多为平/震荡市) / OOS(>=2025-06 小盘牛) 看 regime 稳定性 + 随机入场对照(同热度tier隔离beta)。
源: market.price_kline_qfq_tushare + tushare_raw.moneyflow + smartmoney.dim_stock_sw_industry(tdx_l2_name)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_sector_heat.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读板块热度条件检验; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A, trade  # DRY

log = logging.getLogger("sector_heat")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)
MOM_N = 20             # rule-compliance: ok evidence=板块动量回看(月级), 结构常数
RAND_RATE = 0.02       # rule-compliance: ok evidence=随机入场率(对照基线), 派生非业务参数


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    sec = con.execute("SELECT stock_code, tdx_l2_name AS sector FROM sm.dim_stock_sw_industry").df()
    sec_map = dict(zip(sec["stock_code"], sec["sector"]))
    log.info("载入 price + moneyflow join...")
    big = con.execute("""
        SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume,
               mf.buy_lg_amount, mf.buy_elg_amount, mf.sell_lg_amount, mf.sell_elg_amount,
               mf.buy_sm_amount, mf.buy_md_amount, mf.sell_sm_amount, mf.sell_md_amount
        FROM price_kline_qfq_tushare k
        LEFT JOIN tr.raw_tushare_moneyflow mf ON SUBSTR(mf.ts_code,1,6)=k.code AND mf.trade_date=REPLACE(k.date::VARCHAR,'-','')
        WHERE k.date >= '2019-01-01' AND k.close>0 ORDER BY k.code, k.date
    """).df()
    con.close()
    big["sector"] = big["code"].map(sec_map)
    big = big[big["sector"].notna()].copy()

    # 板块L2热度 (PIT): 每股20日收益 + 主力净额比 → 按(sector,date)聚合 → 当日截面rank
    log.info("算板块L2同期热度 (PIT)...")
    big["ret20"] = big.groupby("code")["close"].pct_change(MOM_N)
    tot = (big["buy_lg_amount"] + big["buy_elg_amount"] + big["sell_lg_amount"] + big["sell_elg_amount"]
           + big["buy_sm_amount"] + big["buy_md_amount"] + big["sell_sm_amount"] + big["sell_md_amount"]).replace(0, np.nan)
    big["main_ratio"] = ((big["buy_lg_amount"] + big["buy_elg_amount"] - big["sell_lg_amount"] - big["sell_elg_amount"]) / tot).fillna(0.0)
    secday = big.groupby(["sector", "date"]).agg(mom=("ret20", "mean"), flow=("main_ratio", "mean")).reset_index()
    secday["heat_mom"] = secday.groupby("date")["mom"].rank(pct=True)   # 当日截面: 该L2板块动量分位 (0冷~1热)
    secday["heat_flow"] = secday.groupby("date")["flow"].rank(pct=True)  # 该L2板块资金分位
    heat_map = {(r.sector, r.date): (r.heat_mom, r.heat_flow) for r in secday.itertuples()}

    # 入场: 二次突破 + 随机对照; 各 entry 取其板块当日热度
    rng = np.random.RandomState(20260617)  # rule-compliance: ok evidence=固定种子复现随机对照
    rows = []
    for code, g in big.groupby("code"):
        g = g.reset_index(drop=True)
        c = g["close"].to_numpy();
        if len(c) < 160:
            continue
        o, h, v = g["open"].to_numpy(), g["high"].to_numpy(), g["volume"].to_numpy()
        dates = g["date"].astype(str).to_numpy()
        sector = g["sector"].iloc[0]
        state = _weekly_state(dates, c)
        # 二次突破入场
        for ei in entries_A(c, state, v):
            hm = heat_map.get((sector, dates[ei]), (np.nan, np.nan))
            _, _, r = trade(o, h, c, state, ei)
            rows.append((dates[ei + 1] if ei + 1 < len(dates) else dates[ei], "二次突破", hm[0], hm[1], r))
        # 随机对照 (周线确认内随机日)
        i = 21
        while i < len(c) - 1:
            if state[i] and c[i] > 0 and rng.random() < RAND_RATE and not (i >= 1 and c[i] / c[i - 1] - 1 >= 0.098):
                hm = heat_map.get((sector, dates[i]), (np.nan, np.nan))
                _, exit_i, r = trade(o, h, c, state, i)
                rows.append((dates[i + 1] if i + 1 < len(dates) else dates[i], "随机", hm[0], hm[1], r))
                i = exit_i + 1
            else:
                i += 1
    panel = pd.DataFrame(rows, columns=["entry_date", "kind", "heat_mom", "heat_flow", "ret"]).dropna(subset=["heat_mom"])
    log.info("入场样本 %s (二次突破 %s / 随机 %s)", f"{len(panel):,}",
             f"{int((panel['kind']=='二次突破').sum()):,}", f"{int((panel['kind']=='随机').sum()):,}")

    def tier(x):
        return np.where(x >= 0.67, "热", np.where(x >= 0.33, "中", "冷"))
    panel["heat_tier"] = tier(panel["heat_mom"].to_numpy())
    panel["seg"] = np.where(panel["entry_date"] < OOS_CUT, "TRAIN", "OOS")

    print(f"\n二次突破 × 板块L2同期热度 条件检验 (含成本13bps; 热度=该L2板块当日动量截面分位)")
    print(f"  样本 {len(panel):,} | 全样本二次突破均值{panel[panel.kind=='二次突破'].ret.mean()*100:+.2f}% 随机{panel[panel.kind=='随机'].ret.mean()*100:+.2f}%")
    print(f"  {'regime':7}{'热度tier':8}{'二次突破n':>9}{'均值':>8}{'胜率':>7}{'随机n':>8}{'随机均值':>9}{'入场增量':>9}")
    res = {}
    for seg in ["TRAIN", "OOS"]:
        for ht in ["热", "中", "冷"]:
            sb = panel[(panel.seg == seg) & (panel.heat_tier == ht) & (panel.kind == "二次突破")]["ret"].to_numpy()
            rd = panel[(panel.seg == seg) & (panel.heat_tier == ht) & (panel.kind == "随机")]["ret"].to_numpy()
            if len(sb) < 20:
                continue
            inc = sb.mean() - (rd.mean() if len(rd) else 0)
            res[(seg, ht)] = dict(n=len(sb), mean=float(sb.mean()), win=float((sb > 0).mean()), rn=len(rd), rmean=float(rd.mean()) if len(rd) else 0.0, inc=float(inc))
            print(f"  {seg:7}{ht:8}{len(sb):>9,}{sb.mean()*100:>+7.2f}%{(sb>0).mean()*100:>6.1f}%{len(rd):>8,}{(rd.mean()*100 if len(rd) else 0):>+8.2f}%{inc*100:>+8.2f}%")

    # 裁决: 热板块 vs 冷板块 二次突破增量差 (跨regime); 热板块入场增量是否显著>冷
    def inc_of(seg, ht):
        return res.get((seg, ht), {}).get("inc", np.nan)
    hot_tr, cold_tr = inc_of("TRAIN", "热"), inc_of("TRAIN", "冷")
    hot_oos, cold_oos = inc_of("OOS", "热"), inc_of("OOS", "冷")
    print(f"\n  --- 裁决 (板块L2热度是否是可交易条件层) ---")
    print(f"  热板块入场增量: TRAIN{hot_tr*100:+.2f}% / OOS{hot_oos*100:+.2f}%  |  冷板块: TRAIN{cold_tr*100:+.2f}% / OOS{cold_oos*100:+.2f}%")
    hot_better_tr = (not np.isnan(hot_tr) and not np.isnan(cold_tr) and hot_tr - cold_tr > 0.01)
    hot_better_oos = (not np.isnan(hot_oos) and not np.isnan(cold_oos) and hot_oos - cold_oos > 0.01)
    if hot_better_tr and hot_better_oos:
        verdict = f"板块L2热度=可交易条件层: 热板块二次突破入场增量 TRAIN+OOS 双段均显著>冷板块(差TRAIN{(hot_tr-cold_tr)*100:+.1f}pp/OOS{(hot_oos-cold_oos)*100:+.1f}pp) → 鱼身延续须叠板块热度过滤(只在热L2板块的突破入场); 下一步 meta-labeling secondary 必含板块热度"
    elif hot_better_oos or hot_better_tr:
        verdict = f"板块L2热度部分有效(单段): 仅{'OOS' if hot_better_oos else 'TRAIN'}段热>冷, 另段不稳 → 热度有信号但regime依赖, 须更多窗/配资金流(heat_flow)再验, 谨慎当条件层"
    else:
        verdict = f"板块L2热度未显著提升入场增量: 热冷板块二次突破增量无稳定差 → 静态板块基础率15.6pp主要是题材幸存非可交易同期热度; 转个股层鱼身延续+出场, 板块热度降为弱context"
    print(f"  → {verdict}")

    run_id = "yushen_sector_heat_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n": len(panel), "hot_inc_train": round(hot_tr, 5) if not np.isnan(hot_tr) else None,
              "hot_inc_oos": round(hot_oos, 5) if not np.isnan(hot_oos) else None,
              "cold_inc_train": round(cold_tr, 5) if not np.isnan(cold_tr) else None,
              "cold_inc_oos": round(cold_oos, 5) if not np.isnan(cold_oos) else None,
              "cells": {f"{s}_{h}": res[(s, h)] for (s, h) in res}, "summary": verdict[:150]}
    vlabel = "SECTOR_HEAT_TRADEABLE" if (hot_better_tr and hot_better_oos) else ("SECTOR_HEAT_PARTIAL" if (hot_better_oos or hot_better_tr) else "SECTOR_HEAT_WEAK")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_sector_heat", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_sector_heat verdict={vlabel}")


if __name__ == "__main__":
    main()
