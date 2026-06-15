"""L2 特征面板物化 — 把因子从实验内联移到 feature_store (分层/分模块/写锁隔离, 用户 2026-06-15)。

owner=docs/data_management_framework.md (L2_feature) + analysis/feature_layer_and_test_plan_20260615.md。
缘起 (根因): L2 wiped 后实验内联算因子直读 L0 (tushare_raw) -> 耦合 L0 + 撞 raw 回补写锁 + 因子不可复用/清。
正解: 因子按模块 PIT 物化进 `feature_store.duckdb fact_feature_panel` (独立库=写锁隔离), 探索读 L2 不读 L0 raw。

模块 (各独立, 单向 L0/market -> L2, 模块间不互读):
  技术 technical: mom_60 (动量) / reversal_20 (反转) / vol_20 (波动)   <- market price_kline_qfq_tushare (2019+)
  资金流 moneyflow: mf_trend_20 (大单净流入趋势)                       <- L0 raw_tushare_moneyflow (2020+, 盘后t-1)
  质量 quality: roe_dt_asof (扣非ROE as-of)                            <- L0 raw_tushare_fina_indicator (ann_date<=t)
PIT: 每因子只用 <=t 信息; 写锁隔离: 读 L0/market, 写 feature_store (拉 raw 不堵本 build, 本 build 不堵探索)。
grain: (code, date); 宽表 = 一行 code×date + 因子列。retention=wipeable (rebuild from L0/L1)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402  market K线 (2019+)
from scripts.experiment_moneyflow_trend_alpha import load_moneyflow, mf_trend_feature, in_universe  # noqa: E402
from scripts.experiment_fundamental_quality_alpha import load_quality_reports, asof_quality_series  # noqa: E402
from scripts.experiment_kline_momentum_regime import momentum_feature  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.duck_adapter import connect  # noqa: E402

FEATURE_DB = "data/feature_store.duckdb"  # rule-compliance: ok evidence=L2 feature_store 独立库 (database_manifest 规划拆分目标, 写锁隔离)
PANEL = "fact_feature_panel"
MOM_W, REV_W, VOL_W, MF_W = 60, 20, 20, 20  # rule-compliance: ok evidence=pre-reg 因子窗 (动量60/反转20/波动20/资金20, 与各实验同口径)
FACTOR_COLS = ["mom_60", "reversal_20", "vol_20", "mf_trend_20", "roe_dt_asof"]


def vol_feature(closes: list, window: int = VOL_W) -> list:
    """trailing 波动 (N日收益 std, PIT)。"""
    out: list = [None] * len(closes)
    rets = [None] + [(closes[i] / closes[i - 1] - 1.0) if closes[i] and closes[i - 1] else None
                     for i in range(1, len(closes))]
    for i in range(len(closes)):
        lo = i - window + 1
        if lo < 1:
            continue
        seg = [r for r in rets[lo:i + 1] if r is not None]
        if len(seg) >= 3:
            out[i] = float(np.std(seg, ddof=1))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")  # rule-compliance: ok evidence=K线 tushare-qfq 起点 (全多regime窗)
    args = ap.parse_args(argv)

    print("[load] K线(market) + moneyflow(L0) + fina(L0) ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    mf = load_moneyflow(args.start)
    reports = load_quality_reports()
    print(f"[load] K线 {len(by_code)} 股, moneyflow {len(mf)}, fina {len(reports)}", flush=True)

    rows: list[tuple] = []
    for code, bars in by_code.items():
        if not in_universe(code):
            continue
        dates, closes = bars["date"], bars["close"]
        mom = momentum_feature(closes, MOM_W)
        rev = feature_reversal(closes, lookback=REV_W)
        vol = vol_feature(closes, VOL_W)
        # 资金流 (盘后 t-1 已在 mf_trend 内 trailing; 对齐 K线日)
        if code in mf:
            net_s = [mf[code].get(d, (None, None))[0] for d in dates]
            flow_s = [mf[code].get(d, (None, None))[1] for d in dates]
            mft = mf_trend_feature(net_s, flow_s, MF_W)
        else:
            mft = [None] * len(dates)
        roe = asof_quality_series(dates, reports[code]) if code in reports else [None] * len(dates)
        for i, d in enumerate(dates):
            vals = (mom[i], rev[i], vol[i], mft[i], roe[i])
            if any(v is not None for v in vals):
                rows.append((code, d, *vals))
    print(f"[build] {len(rows):,} (code,date) 行, {len(FACTOR_COLS)} 因子列", flush=True)

    conn = connect(FEATURE_DB, read_only=False)
    try:
        cols_ddl = ", ".join(f"{c} DOUBLE" for c in FACTOR_COLS)
        conn.execute(f"DROP TABLE IF EXISTS {PANEL}")
        conn.execute(f"CREATE TABLE {PANEL} (code VARCHAR, date VARCHAR, {cols_ddl}, "
                     "PRIMARY KEY (code, date))")
        conn.executemany(f"INSERT INTO {PANEL} VALUES (?,?,?,?,?,?,?)", rows)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{PANEL}_date ON {PANEL}(date)")
        conn.execute("CHECKPOINT")
        r = conn.execute(f"SELECT min(date),max(date),count(*),count(DISTINCT code) FROM {PANEL}").fetchone()
        cov = {c: conn.execute(f"SELECT count(*) FROM {PANEL} WHERE {c} IS NOT NULL").fetchone()[0] for c in FACTOR_COLS}
    finally:
        conn.close()
    print(f"[done] {PANEL}: {r[0]}~{r[1]} | {r[2]:,}行 | {r[3]}股 (feature_store 独立库, 写锁隔离)")
    print(f"[coverage] 各因子非空: {cov}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
