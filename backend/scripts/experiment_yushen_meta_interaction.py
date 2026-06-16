"""experiment_yushen_meta_interaction — 明暗筹价交互族 + CNIR, meta-labeling 测 (主会话主导, 2026-06-17)。

owner: 用户设计(明×暗×筹码×价格交互族, 多种组合状态) + SOTA研究(转meta-labeling + CNIR剥反身性 + 指标precision非AUC)。
框架 (meta-labeling, López de Prado): PRIMARY=二次突破入场(候选集); SECONDARY=明暗筹价交互特征判"该不该下注"
  (true/false breakout), NOT 预测方向。指标=precision(下注→含成本盈) + 下注集含成本均值 vs 全集 + max_dd, **非AUC**。
明暗筹价交互族 (用户设计): M明盘净额比 / D暗盘比(OHLC路径X_8×sm-md) / 筹码(获利盘+变化+集中度) / 价格(SLHC缩量回踩/FLZZ放量滞涨/位置)。
  6态(M符号×D符号×|M|vs|D|: 共识/主力强势/明出暗进吸筹/一致出逃/拉高出货/诱多) one-hot + 连续交互项(M·sign(D)/|M|-|D|/M×筹码变化/D×SLHC)。
CNIR (SOTA研究): 资金类(M/D)逐signal_date截面回归 on 当期收益, 取残差剥反身性(系数只用当日截面=PIT)。
分 TRAIN(<2025-06)/OOS 看meta过滤的含成本增量; shuffle-null 防过拟合。复用 compute_features(M/D/筹码/SLHC/FLZZ) + entries_A/trade(DRY)。
源: 同 D3 join + 用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_meta_interaction.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读meta-labeling交互检验; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_rally_precursor_model import compute_features
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A, trade

log = logging.getLogger("meta_interaction")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)
BET_PCTL = 70           # rule-compliance: ok evidence=secondary下注阈值=概率top30%(meta过滤强度), 结构常数


def cnir_residual(df_all, col, ret_col="ret1"):
    """逐date截面回归 col ~ ret, 取残差(剥反身性, PIT: 只用当日截面)。"""
    def resid(g):
        x = g[ret_col].to_numpy(); y = g[col].to_numpy()
        m = ~(np.isnan(x) | np.isnan(y))
        if m.sum() < 10 or np.var(x[m]) < 1e-12:
            return pd.Series(y - np.nanmean(y) if m.sum() else y, index=g.index)
        b = np.cov(x[m], y[m])[0, 1] / np.var(x[m])
        a = np.mean(y[m]) - b * np.mean(x[m])
        return pd.Series(y - (a + b * x), index=g.index)
    return df_all.groupby("date", group_keys=False).apply(resid)


def main():
    try:
        import lightgbm as lgb
    except ImportError:
        raise SystemExit("lightgbm 未装")
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    log.info("载入大 join...")
    big = con.execute("""
        SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount,
               db.turnover_rate, db.volume_ratio, db.circ_mv,
               mf.buy_sm_amount, mf.buy_md_amount, mf.buy_lg_amount, mf.buy_elg_amount,
               mf.sell_sm_amount, mf.sell_md_amount, mf.sell_lg_amount, mf.sell_elg_amount,
               cy.winner_rate, cy.cost_5pct, cy.cost_50pct, cy.cost_95pct
        FROM price_kline_qfq_tushare k
        LEFT JOIN tr.raw_tushare_daily_basic db ON SUBSTR(db.ts_code,1,6)=k.code AND db.trade_date=REPLACE(k.date::VARCHAR,'-','')
        LEFT JOIN tr.raw_tushare_moneyflow mf ON SUBSTR(mf.ts_code,1,6)=k.code AND mf.trade_date=REPLACE(k.date::VARCHAR,'-','')
        LEFT JOIN tr.raw_tushare_cyq_perf cy ON SUBSTR(cy.ts_code,1,6)=k.code AND cy.trade_date=REPLACE(k.date::VARCHAR,'-','')
        WHERE k.date >= '2019-01-01' AND k.close>0 ORDER BY k.code, k.date
    """).df()
    con.close()

    # 1) 逐股 compute_features (得 main_ratio=M / dark_ratio=D / SLHC / FLZZ / winner_rate / concentration)
    log.info("逐股算 M/D/筹码/形态特征...")
    feat_frames = []
    for code, g in big.groupby("code"):
        g = compute_features(g.reset_index(drop=True))
        g["code"] = code
        g["ret1"] = g["close"].pct_change()
        feat_frames.append(g[["code", "date", "main_ratio", "dark_ratio", "ret1", "winner_rate", "concentration", "SLHC", "FLZZ", "VRN", "pos60"]])
    allf = pd.concat(feat_frames, ignore_index=True)

    # 2) CNIR 截面残差化 M/D (剥反身性)
    log.info("CNIR 截面残差化 M/D...")
    allf["M_resid"] = cnir_residual(allf, "main_ratio")
    allf["D_resid"] = cnir_residual(allf, "dark_ratio")
    # winner_rate 20日变化
    allf["winner_chg20"] = allf.groupby("code")["winner_rate"].diff(20)
    by_code = {code: g.reset_index(drop=True) for code, g in allf.groupby("code")}

    # 3) 二次突破入场 + 含成本 + 明暗筹价交互特征
    log.info("二次突破入场 + 交互特征...")
    rows = []
    for code, g in big.groupby("code"):
        c = g["close"].to_numpy()
        if len(c) < 160:
            continue
        o, h, v = g["open"].to_numpy(), g["high"].to_numpy(), g["volume"].to_numpy()
        dates = g["date"].astype(str).to_numpy()
        state = _weekly_state(dates, c)
        fg = by_code.get(code)
        if fg is None or len(fg) != len(g):
            continue
        Mr, Dr = fg["M_resid"].to_numpy(), fg["D_resid"].to_numpy()
        M, D = fg["main_ratio"].to_numpy(), fg["dark_ratio"].to_numpy()
        wr, wc, cc = fg["winner_rate"].to_numpy(), fg["winner_chg20"].to_numpy(), fg["concentration"].to_numpy()
        slhc, flzz, vrn, pos = fg["SLHC"].to_numpy(), fg["FLZZ"].to_numpy(), fg["VRN"].to_numpy(), fg["pos60"].to_numpy()
        for ei in entries_A(c, state, v):
            if ei < 25:
                continue
            sl = slice(max(ei - 4, 0), ei + 1)
            m5, d5 = np.nansum(Mr[sl]), np.nansum(Dr[sl])            # CNIR残差 5日累计
            m5raw, d5raw = np.nansum(M[sl]), np.nansum(D[sl])         # 原始 5日累计 (定6态)
            # 6态 (M符号×D符号×|M|vs|D|)
            st = 0
            if m5raw > 0 and d5raw > 0: st = 1                       # 共识看多
            elif m5raw > 0 and d5raw < 0: st = 2 if abs(m5raw) > abs(d5raw) else 5  # 主力强势 / 拉高出货
            elif m5raw < 0 and d5raw > 0: st = 3 if abs(d5raw) > abs(m5raw) else 6  # 明出暗进吸筹 / 诱多
            elif m5raw < 0 and d5raw < 0: st = 4                     # 一致出逃
            _, _, r = trade(o, h, c, state, ei)
            rows.append(dict(
                entry_date=dates[ei + 1] if ei + 1 < len(dates) else dates[ei], ret=r,
                M_resid5=m5, D_resid5=d5, M5=m5raw, D5=d5raw,
                MD_signprod=np.sign(m5raw) * np.sign(d5raw), absM_minus_absD=abs(m5raw) - abs(d5raw),
                M_x_winnerchg=m5raw * (wc[ei] if not np.isnan(wc[ei]) else 0),
                D_x_slhc=d5raw * float(slhc[max(ei - 4, 0):ei + 1].max()),
                winner_rate=wr[ei], winner_chg20=wc[ei], concentration=cc[ei],
                SLHC_10d=float(slhc[max(ei - 9, 0):ei + 1].sum()), FLZZ_10d=float(flzz[max(ei - 9, 0):ei + 1].sum()),
                VRN_t=vrn[ei], pos60=pos[ei],
                **{f"state_{k}": 1.0 if st == k else 0.0 for k in range(1, 7)},
            ))
    panel = pd.DataFrame(rows)
    panel["label"] = (panel["ret"] > 0).astype(int)  # meta-label: 该不该下注 = 含成本盈
    log.info("二次突破候选 %s (含成本盈 %.1f%%)", f"{len(panel):,}", panel["label"].mean() * 100)

    feat_cols = [c for c in panel.columns if c not in ("entry_date", "ret", "label")]
    panel = panel.replace([np.inf, -np.inf], np.nan)
    trm = (panel["entry_date"] < OOS_CUT).to_numpy()
    X, ylab, ret = panel[feat_cols].to_numpy(float), panel["label"].to_numpy(), panel["ret"].to_numpy()

    def ctor():
        return lgb.LGBMClassifier(n_estimators=150, num_leaves=15, learning_rate=0.03, min_child_samples=40,
                                  subsample=0.8, colsample_bytree=0.7, reg_lambda=1.0, class_weight="balanced", verbose=-1)
    m = ctor(); m.fit(X[trm], ylab[trm])
    p_te = m.predict_proba(X[~trm])[:, 1]
    ret_te = ret[~trm]
    thr = np.nanpercentile(p_te, BET_PCTL)
    bet = p_te >= thr
    # meta 过滤效果 (OOS): 下注集 vs 全集 含成本均值/胜率/precision
    all_mean, all_win = ret_te.mean(), (ret_te > 0).mean()
    bet_mean, bet_win = (ret_te[bet].mean(), (ret_te[bet] > 0).mean()) if bet.sum() > 5 else (np.nan, np.nan)
    # shuffle-null: 打乱label重训, OOS下注集均值分布
    rng = np.random.RandomState(20260617)  # rule-compliance: ok evidence=固定种子复现shuffle null
    null_betmeans = []
    for _ in range(20):  # rule-compliance: ok evidence=shuffle null 20次, 统计常数
        ysh = rng.permutation(ylab[trm])
        ms = ctor(); ms.fit(X[trm], ysh)
        ps = ms.predict_proba(X[~trm])[:, 1]
        bs = ps >= np.nanpercentile(ps, BET_PCTL)
        if bs.sum() > 5:
            null_betmeans.append(ret_te[bs].mean())
    null_mean, null_p95 = float(np.nanmean(null_betmeans)), float(np.nanpercentile(null_betmeans, 95))
    imp = sorted(zip(feat_cols, m.feature_importances_), key=lambda x: -x[1])[:8]

    print(f"\n明暗筹价交互族 + CNIR meta-labeling (PRIMARY=二次突破, SECONDARY=该不该下注; 指标=含成本非AUC)")
    print(f"  二次突破候选 {len(panel):,} (含成本盈 {panel['label'].mean()*100:.1f}%) | OOS测")
    print(f"  全集(所有二次突破) OOS: 含成本均值 {all_mean*100:+.2f}% 胜率 {all_win*100:.1f}%")
    print(f"  下注集(secondary top{100-BET_PCTL}%) OOS: 含成本均值 {bet_mean*100:+.2f}% 胜率 {bet_win*100:.1f}%  (n={int(bet.sum())})")
    print(f"  meta过滤增量 = {(bet_mean-all_mean)*100:+.2f}pp  vs shuffle-null下注集均值 {null_mean*100:+.2f}%/p95 {null_p95*100:+.2f}%")
    print(f"  特征重要性 top8: {[(f,int(i)) for f,i in imp]}")
    lift = bet_mean - all_mean
    real = (not np.isnan(bet_mean)) and (bet_mean > null_p95) and (lift > 0.003)
    print(f"\n  --- 裁决 (明暗筹价交互 meta 过滤是否真提升含成本) ---")
    if real:
        verdict = f"交互族meta过滤真有效: 下注集含成本{bet_mean*100:+.2f}% > 全集{all_mean*100:+.2f}%(+{lift*100:.2f}pp) 且 > shuffle-null p95{null_p95*100:+.2f}% → 明暗筹价交互能筛出更优二次突破子集; 下一步组合NAV(下注集)+板块context+Optuna调阈值"
    elif not np.isnan(bet_mean) and bet_mean > all_mean:
        verdict = f"交互族meta边际: 下注集{bet_mean*100:+.2f}%略>全集但不超shuffle p95{null_p95*100:+.2f}% → 过滤有微弱信号但不显著, 与入场空间整体≈regime-beta一致; 该转出场+仓位+meta(以含成本组合为目标)"
    else:
        verdict = f"交互族meta无效: 下注集{bet_mean*100:+.2f}%不优于全集{all_mean*100:+.2f}% → 明暗筹价交互(你设计的最后入场角度)也未筛出更优子集 = 入场空间穷尽证据压倒性, **转出场择时(CYQ出货预警)+仓位管理+meta-labeling**(研究+全部实证重心)"
    print(f"  → {verdict}")

    run_id = "yushen_meta_interaction_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_candidates": len(panel), "base_win": round(panel["label"].mean(), 4),
              "oos_all_mean": round(all_mean, 5), "oos_bet_mean": round(bet_mean, 5) if not np.isnan(bet_mean) else None,
              "meta_lift": round(lift, 5) if not np.isnan(lift) else None,
              "shuffle_null_p95": round(null_p95, 5), "top_features": [f for f, _ in imp], "summary": verdict[:150]}
    vlabel = "META_REAL" if real else ("META_MARGINAL" if (not np.isnan(bet_mean) and bet_mean > all_mean) else "META_NO_EDGE")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_meta_interaction", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_meta_interaction verdict={vlabel}")


if __name__ == "__main__":
    main()
