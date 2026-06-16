"""experiment_rally_precursor_stratified — 分层条件化前兆判别 (主会话主导, 2026-06-17)。

owner: 用户 2026-06-16 纠偏("抽样验证区分形态了么/分层了么/分板块了么")。
硬伤: 前 precursor/combo 把 43202 事件**混池**算 AUC(0.52≈随机) —— 没区分形态/市值/板块。
  混池把 cohort 条件化信号冲销(F0/F1教训: range_pos 全样本-0.043 但分层中盘高波-0.054→大盘低波+0.011)。
本实验测**分层后判别力是否回来**: 在 市值tier × 位置形态tier(9 cell) 内 + 按申万一级行业, 测前兆特征区分赢家。
  若某些 cell 判别力 train+test 稳定远超 0.52 = 混池假象证实(信号是条件化的); 报全部 cell 不挑樱桃(防选择偏差)。
全 PIT(前兆<=event_date)。复用 D3 compute_features/event_features(DRY)。市值/板块作分层轴(板块用当前快照做分组, 非PIT择股, 仅分层caveat)。
源: 同 D3 + smartmoney.dim_stock_sw_industry(申万一级)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_rally_precursor_stratified.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读分层判别; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_rally_precursor_model import compute_features, event_features

log = logging.getLogger("rally_strat")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

TRAIN_TEST_SPLIT = "2024-07-01"  # rule-compliance: ok evidence=hold-out切点同D3
BASE_RATE = 0.1006  # rule-compliance: ok evidence=4345/43202赢家基线, measured
KEY_FEATS = ["main_20dsum", "main_5dsum", "winner_rate_chg20d", "flow_5dsum", "concentration_t"]  # rule-compliance: ok evidence=D3最强弱正信号特征(单测AUC>0.51), 重点查它们分层后


def auc_rank(scores, labels):
    m = ~np.isnan(scores)
    s, y = scores[m], labels[m]
    if len(y) < 30 or y.sum() < 5 or y.sum() == len(y):
        return np.nan
    order = np.argsort(s)
    ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    npos, nneg = y.sum(), len(y) - y.sum()
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def best_abs_auc(panel, mask, y):
    """cell 内 KEY_FEATS 的最大 |AUC-0.5| 特征 + 其 train/test AUC。"""
    best = (None, 0.5, np.nan, np.nan)
    for f in KEY_FEATS:
        s = panel[f].to_numpy(dtype=float)[mask]
        yy = y[mask]
        a = auc_rank(s, yy)
        if np.isnan(a):
            continue
        if abs(a - 0.5) > abs(best[1] - 0.5):
            trm = (panel["event_date"].to_numpy()[mask] < TRAIN_TEST_SPLIT)
            best = (f, a, auc_rank(s[trm], yy[trm]), auc_rank(s[~trm], yy[~trm]))
    return best


def build_panel_strat(mf):
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    con.execute(f"ATTACH '{mf.path_for('smartmoney')}' AS sm (READ_ONLY)")
    events = con.execute("SELECT stock_code, event_date, is_true_rally FROM sm.fact_rally_ground_truth").df()
    sector = con.execute("SELECT stock_code, tdx_l2_name AS sector FROM sm.dim_stock_sw_industry").df()  # 申万二级(131桶,概念级粒度); L1只31桶太粗(项目06-11 ANOVA已定L2主口径)
    sec_map = dict(zip(sector["stock_code"], sector["sector"]))
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
    ev_by_code = {code: g for code, g in events.groupby("stock_code")}
    rows = []
    for code, g in big.groupby("code"):
        if code not in ev_by_code or len(g) < 80:
            continue
        g = compute_features(g.reset_index(drop=True))
        cmv = g["circ_mv"].to_numpy()
        d2i = {d: i for i, d in enumerate(g["date"].astype(str))}
        for _, ev in ev_by_code[code].iterrows():
            ed = str(ev["event_date"])
            if len(ed) == 8 and ed.isdigit():
                ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"
            ei = d2i.get(ed)
            if ei is None:
                continue
            feat = event_features(g, ei)
            if feat is None:
                continue
            feat["is_win"] = int(ev["is_true_rally"])
            feat["event_date"] = ed
            feat["circ_mv"] = cmv[ei] if not np.isnan(cmv[ei]) else np.nan
            feat["sector"] = sec_map.get(code, "未知")
            rows.append(feat)
    return pd.DataFrame(rows)


def main():
    mf = get_database_manifest()
    log.info("建分层前兆面板...")
    panel = build_panel_strat(mf)
    y = panel["is_win"].to_numpy()
    log.info("面板 %s 事件 (%s 赢家)", f"{len(panel):,}", f"{int(y.sum()):,}")

    # 分层轴: 市值tier (circ_mv 三分位) × 位置形态tier (pos60_t)
    cmv = panel["circ_mv"].to_numpy(dtype=float)
    valid_cmv = ~np.isnan(cmv)
    q33, q67 = np.nanpercentile(cmv, [33, 67])
    cap_tier = np.where(cmv <= q33, "小盘", np.where(cmv <= q67, "中盘", "大盘"))
    pos = panel["pos60_t"].to_numpy(dtype=float)
    pos_tier = np.where(pos <= 0.3, "低位", np.where(pos <= 0.7, "中位", "高位"))

    print(f"\n分层条件化前兆判别 (混池基线 AUC≈0.52; 分层后是否回来? 报全cell不挑樱桃)")
    print(f"  全样本基线赢家率 {y.mean()*100:.1f}%")
    print(f"\n  [市值 × 位置形态] 9 cell:")
    print(f"  {'cell':16}{'n':>7}{'赢家率':>8}{'最强特征':>16}{'cell_AUC':>9}{'train':>8}{'test':>8}")
    cell_rows = []
    for ct in ["小盘", "中盘", "大盘"]:
        for pt in ["低位", "中位", "高位"]:
            m = (cap_tier == ct) & (pos_tier == pt) & valid_cmv
            if m.sum() < 50:
                continue
            wr = y[m].mean()
            f, a, atr, ate = best_abs_auc(panel, m, y)
            cell_rows.append(dict(cell=f"{ct}×{pt}", n=int(m.sum()), win=wr, feat=f, auc=a, auc_tr=atr, auc_te=ate))
            print(f"  {ct+'×'+pt:16}{int(m.sum()):>7,}{wr*100:>7.1f}%{str(f):>16}{(a if a else 0):>9.3f}{(atr if atr else 0):>8.3f}{(ate if ate else 0):>8.3f}")

    # 按申万一级行业: 赢家率 + 最强特征判别 (板块异质性)
    print(f"\n  [申万二级行业] 赢家率 top/bottom + 板块内判别 (异质性):")
    sec_rows = []
    for sec, idx in panel.groupby("sector").groups.items():
        m = np.zeros(len(panel), bool); m[panel.index.get_indexer(idx)] = True
        if m.sum() < 120:  # rule-compliance: ok evidence=申万L2 131桶, min样本120保留足够cell, 结构常数
            continue
        wr = y[m].mean()
        f, a, atr, ate = best_abs_auc(panel, m, y)
        sec_rows.append(dict(sector=sec, n=int(m.sum()), win=wr, feat=f, auc=a, auc_tr=atr, auc_te=ate))
    sec_rows.sort(key=lambda r: -r["win"])
    for r in sec_rows[:5] + sec_rows[-3:]:
        print(f"  {r['sector']:14}{r['n']:>7,}{r['win']*100:>7.1f}%  最强{str(r['feat']):>14} AUC{(r['auc'] or 0):.3f}(tr{(r['auc_tr'] or 0):.2f}/te{(r['auc_te'] or 0):.2f})")

    # 裁决: 有没有 cell train+test 稳定判别远超混池
    strong_cells = [r for r in cell_rows if r["auc_tr"] and r["auc_te"]
                    and abs(r["auc_tr"] - 0.5) > 0.05 and abs(r["auc_te"] - 0.5) > 0.05
                    and np.sign(r["auc_tr"] - 0.5) == np.sign(r["auc_te"] - 0.5)]
    win_spread = max(r["win"] for r in cell_rows) - min(r["win"] for r in cell_rows) if cell_rows else 0
    sec_spread = (max(r["win"] for r in sec_rows) - min(r["win"] for r in sec_rows)) if sec_rows else 0
    print(f"\n  --- 裁决 (分层是否解锁混池冲销的信号) ---")
    print(f"  cell赢家率跨度 {win_spread*100:.1f}pp (混池10.1%) / 板块赢家率跨度 {sec_spread*100:.1f}pp")
    if strong_cells:
        sc = max(strong_cells, key=lambda r: abs(r["auc"] - 0.5))
        verdict = (f"分层解锁: {len(strong_cells)}个cell判别力train+test稳定|AUC-0.5|>0.05(混池≈0.52被冲销). "
                   f"最强 {sc['cell']} {sc['feat']} AUC{sc['auc']:.3f}(tr{sc['auc_tr']:.2f}/te{sc['auc_te']:.2f}). "
                   f"赢家率跨度cell{win_spread*100:.0f}pp/板块{sec_spread*100:.0f}pp=形态/市值/板块强异质. 下一步: cell内建带闸组合模型+板块热度条件化")
    elif win_spread > 0.08 or sec_spread > 0.08:
        verdict = (f"赢家率强异质(cell跨度{win_spread*100:.0f}pp/板块{sec_spread*100:.0f}pp)但cell内单特征仍弱判别: "
                   f"形态/市值/板块决定**基础胜率**(选对cell=选对池), 但cell内选股仍需多因子组合; 混池确实冲销了基础率差异")
    else:
        verdict = f"分层后cell内仍弱判别 且 赢家率跨度小({win_spread*100:.0f}pp): 形态/市值/板块未显著解锁; 买点判别在各层都难, edge更可能在板块热度择时/出场"
    print(f"  → {verdict}")

    run_id = "rally_precursor_stratified_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_events": len(panel), "win_spread_cell": round(win_spread, 4), "win_spread_sector": round(sec_spread, 4),
              "n_strong_cells": len(strong_cells),
              "cells": [{"cell": r["cell"], "n": r["n"], "win": round(r["win"], 4), "feat": r["feat"],
                         "auc": round(r["auc"], 4) if r["auc"] else None, "auc_te": round(r["auc_te"], 4) if r["auc_te"] else None} for r in cell_rows],
              "top_sectors": [{"sector": r["sector"], "win": round(r["win"], 4)} for r in sec_rows[:5]],
              "summary": verdict[:150]}
    vlabel = "STRAT_UNLOCKS" if strong_cells else ("BASERATE_HETERO" if (win_spread > 0.08 or sec_spread > 0.08) else "STRAT_NO_UNLOCK")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="rally_precursor_stratified", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=rally_precursor_stratified verdict={vlabel}")


if __name__ == "__main__":
    main()
