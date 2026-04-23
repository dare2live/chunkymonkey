#!/usr/bin/env python3
"""C0 严格 point-in-time 最小基线特征矩阵（§1 P0.B / §2 Claude 2026-04-23 C0 方案）。

目标：完全剔除 §1 P0.1 修正段列出的 9 处污染源，只用事件日之前可见的字段。

特征源（全部 PIT）：
  EV 事件自身    premium_pct / premium_bucket / hold_amount / change_amount / event_type
  MG 两融        raw_margin_daily.trade_date < notice_date 最近一天 + 近 20d rz_buy/rq_sell 累积
  PX 价量        price_kline.date < notice_date 回算 MA/return/drawdown/volatility

排除：F1 Layer B / F3 forecast / F4 survey / F5 stage / F7 mart_institution_profile / research_inst_industry_performance

Label（事后）：
  label_gain_30d / label_gain_60d / label_max_drawdown_30d / label_max_drawdown_60d
  （来自 fact_institution_event 字段，已经是 tradable_date 起算的未来收益，无 lookahead）

性能：
  一次性把全量 price_kline 和 raw_margin_daily 加载到内存 DataFrame，按 code 分组；
  遍历事件逐条查对应子集算特征。预估内存 < 200 MB，全量 31k 事件 ~5 分钟。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn
from services.market_db import get_market_conn

logger = logging.getLogger("build_event_features_pit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_event_features_pit (
    institution_id           TEXT NOT NULL,
    stock_code               TEXT NOT NULL,
    notice_date              TEXT NOT NULL,
    report_date              TEXT NOT NULL,
    event_type               TEXT,
    tdx_l1_name              TEXT,
    tdx_l2_name              TEXT,

    -- EV 事件自身（天然 PIT）
    ev_premium_pct           REAL,
    ev_premium_bucket        TEXT,
    ev_hold_amount           REAL,
    ev_change_amount         REAL,

    -- MG 两融（事件日前 PIT）
    mg_rz_balance            REAL,
    mg_rz_buy_20d_sum        REAL,
    mg_rq_sell_20d_sum       REAL,
    mg_rz_balance_pct_20d    REAL,

    -- PX 价量（事件日前 PIT）
    px_close                 REAL,
    px_dist_ma20             REAL,
    px_dist_ma60             REAL,
    px_dist_ma120            REAL,
    px_dist_ma250            REAL,
    px_above_ma250           INTEGER,
    px_return_1m             REAL,
    px_return_3m             REAL,
    px_return_6m             REAL,
    px_volatility_20d        REAL,
    px_max_drawdown_60d      REAL,
    px_amount_ratio_20_120   REAL,

    -- Label
    label_gain_30d           REAL,
    label_gain_60d           REAL,
    label_max_drawdown_30d   REAL,
    label_max_drawdown_60d   REAL,

    ref_trade_date           TEXT,  -- 事件日前最近可用交易日（诊断用）
    computed_at              TEXT,
    PRIMARY KEY (institution_id, stock_code, notice_date, report_date)
);
CREATE INDEX IF NOT EXISTS idx_fefp_notice ON fact_event_features_pit(notice_date);
CREATE INDEX IF NOT EXISTS idx_fefp_stock ON fact_event_features_pit(stock_code);
"""


def _yyyymmdd_to_dash(s: str) -> Optional[str]:
    if not s or len(s) < 8:
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _compute_px_features(prices: pd.DataFrame, ref_date: str) -> dict:
    """prices: 该股票按 date 排序的 DataFrame；ref_date = 事件日前最近交易日（YYYY-MM-DD）。

    返回所有 PX 特征。若窗口不足返回 None 字段。
    """
    sub = prices[prices["date"] <= ref_date]
    if sub.empty:
        return {}
    last = sub.iloc[-1]
    close = float(last["close"]) if not pd.isna(last["close"]) else None
    if close is None or close <= 0:
        return {}

    closes = sub["close"].values.astype(float)
    volumes = sub["amount"].values.astype(float) if "amount" in sub.columns else None

    def _ma(window: int) -> Optional[float]:
        if len(closes) < window:
            return None
        return float(np.mean(closes[-window:]))

    def _ret(window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        return float(closes[-1] / closes[-window - 1] - 1)

    ma20, ma60, ma120, ma250 = _ma(20), _ma(60), _ma(120), _ma(250)
    out = {
        "px_close": close,
        "px_dist_ma20": (close / ma20 - 1) if ma20 else None,
        "px_dist_ma60": (close / ma60 - 1) if ma60 else None,
        "px_dist_ma120": (close / ma120 - 1) if ma120 else None,
        "px_dist_ma250": (close / ma250 - 1) if ma250 else None,
        "px_above_ma250": int(close > ma250) if ma250 else None,
        "px_return_1m": _ret(20),
        "px_return_3m": _ret(60),
        "px_return_6m": _ret(120),
    }
    if len(closes) >= 21:
        rets = np.diff(closes[-21:]) / closes[-21:-1]
        rets = rets[~np.isnan(rets)]
        out["px_volatility_20d"] = float(np.std(rets, ddof=1)) if len(rets) > 1 else None
    else:
        out["px_volatility_20d"] = None
    if len(closes) >= 60:
        window = closes[-60:]
        peak = np.maximum.accumulate(window)
        dd = window / peak - 1.0
        out["px_max_drawdown_60d"] = float(dd.min())
    else:
        out["px_max_drawdown_60d"] = None
    if volumes is not None and len(volumes) >= 120 and not np.all(np.isnan(volumes[-120:])):
        amt20 = np.nanmean(volumes[-20:])
        amt120 = np.nanmean(volumes[-120:])
        out["px_amount_ratio_20_120"] = float(amt20 / amt120) if amt120 and amt120 > 0 else None
    else:
        out["px_amount_ratio_20_120"] = None
    return out


def _compute_mg_features(margin: pd.DataFrame, ref_date: str) -> dict:
    """margin: 该股票按 trade_date 排序；ref_date = YYYY-MM-DD"""
    sub = margin[margin["trade_date"] <= ref_date]
    if sub.empty:
        return {}
    last = sub.iloc[-1]
    rz = float(last["rz_balance"]) if not pd.isna(last["rz_balance"]) else None
    out = {"mg_rz_balance": rz}
    recent20 = sub.iloc[-20:] if len(sub) >= 20 else sub
    out["mg_rz_buy_20d_sum"] = float(recent20["rz_buy"].fillna(0).sum()) if "rz_buy" in recent20.columns else None
    out["mg_rq_sell_20d_sum"] = float(recent20["rq_sell"].fillna(0).sum()) if "rq_sell" in recent20.columns else None
    if rz is not None and len(sub) >= 21:
        rz_20d_ago = float(sub.iloc[-21]["rz_balance"]) if not pd.isna(sub.iloc[-21]["rz_balance"]) else None
        if rz_20d_ago and rz_20d_ago > 0:
            out["mg_rz_balance_pct_20d"] = rz / rz_20d_ago - 1
        else:
            out["mg_rz_balance_pct_20d"] = None
    else:
        out["mg_rz_balance_pct_20d"] = None
    return out


def build(days: int = 0, dry_run: bool = False) -> pd.DataFrame:
    conn = get_conn()
    try:
        conn.executescript(TABLE_DDL)

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d") if days > 0 else "00000000"

        # 事件 + 行业 + label
        events = pd.read_sql_query("""
            SELECT fe.institution_id, fe.stock_code, fe.notice_date, fe.report_date,
                   fe.event_type, fe.premium_pct, fe.premium_bucket,
                   fe.hold_amount, fe.change_amount,
                   fe.gain_30d label_gain_30d, fe.max_drawdown_30d label_max_drawdown_30d,
                   fe.gain_60d label_gain_60d, fe.max_drawdown_60d label_max_drawdown_60d,
                   ind.tdx_l1_name, ind.tdx_l2_name
            FROM fact_institution_event fe
            LEFT JOIN dim_stock_tdx_industry ind ON fe.stock_code = ind.stock_code
            WHERE fe.event_type IN ('new_entry','increase')
              AND fe.notice_date IS NOT NULL AND fe.notice_date != ''
              AND fe.notice_date >= ?
        """, conn, params=(cutoff,))
        logger.info("事件 %d 条（new_entry/increase）", len(events))
        if events.empty:
            return events

        # 事件日转 YYYY-MM-DD，并取"前一交易日"作 ref_date（保守起见用事件日前的价格）
        # 简化：ref_date = 事件日 - 1 自然日，数据匹配时会用 <= 自动取最近交易日
        events["notice_dash"] = events["notice_date"].map(_yyyymmdd_to_dash)
        events["ref_date"] = (pd.to_datetime(events["notice_dash"]) - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")

        codes = sorted(events["stock_code"].astype(str).unique().tolist())
        logger.info("涉及 %d 只股票", len(codes))
    finally:
        pass  # keep conn open below

    # 预加载价格（market_data.db）
    logger.info("加载 price_kline...")
    mkt = get_market_conn()
    price_df = pd.read_sql_query(f"""
        SELECT code, date, close, amount FROM price_kline
        WHERE freq='daily' AND adjust='qfq'
          AND code IN ({','.join(['?']*len(codes))})
    """, mkt, params=codes)
    mkt.close()
    logger.info("price_kline 加载 %d 行", len(price_df))
    price_by_code = {c: g.sort_values("date").reset_index(drop=True) for c, g in price_df.groupby("code", sort=False)}

    # 预加载两融
    logger.info("加载 raw_margin_daily...")
    mg_df = pd.read_sql_query(f"""
        SELECT stock_code, trade_date, rz_balance, rz_buy, rq_sell FROM raw_margin_daily
        WHERE stock_code IN ({','.join(['?']*len(codes))})
    """, conn, params=codes)
    logger.info("raw_margin_daily 加载 %d 行", len(mg_df))
    # trade_date 格式是 YYYY-MM-DD
    margin_by_code = {c: g.sort_values("trade_date").reset_index(drop=True) for c, g in mg_df.groupby("stock_code", sort=False)}

    # 遍历事件
    computed_at = datetime.now().isoformat()
    rows = []
    n = len(events)
    for i, ev in enumerate(events.itertuples(index=False)):
        if i % 5000 == 0 and i > 0:
            logger.info("  进度 %d/%d", i, n)
        code = str(ev.stock_code)
        ref = ev.ref_date
        row = {
            "institution_id": ev.institution_id,
            "stock_code": code,
            "notice_date": ev.notice_date,
            "report_date": ev.report_date,
            "event_type": ev.event_type,
            "tdx_l1_name": ev.tdx_l1_name,
            "tdx_l2_name": ev.tdx_l2_name,
            "ev_premium_pct": ev.premium_pct,
            "ev_premium_bucket": ev.premium_bucket,
            "ev_hold_amount": ev.hold_amount,
            "ev_change_amount": ev.change_amount,
            "label_gain_30d": ev.label_gain_30d,
            "label_max_drawdown_30d": ev.label_max_drawdown_30d,
            "label_gain_60d": ev.label_gain_60d,
            "label_max_drawdown_60d": ev.label_max_drawdown_60d,
            "ref_trade_date": ref,
            "computed_at": computed_at,
        }
        # PX
        prices = price_by_code.get(code)
        if prices is not None:
            row.update(_compute_px_features(prices, ref))
        # MG
        margin = margin_by_code.get(code)
        if margin is not None:
            row.update(_compute_mg_features(margin, ref))
        rows.append(row)

    df = pd.DataFrame(rows)
    logger.info("特征 DataFrame: %d 行 x %d 列", len(df), len(df.columns))

    if not dry_run:
        conn.execute("DELETE FROM fact_event_features_pit WHERE notice_date >= ?", (cutoff,))
        # 对齐列
        all_cols = [
            "institution_id","stock_code","notice_date","report_date","event_type","tdx_l1_name","tdx_l2_name",
            "ev_premium_pct","ev_premium_bucket","ev_hold_amount","ev_change_amount",
            "mg_rz_balance","mg_rz_buy_20d_sum","mg_rq_sell_20d_sum","mg_rz_balance_pct_20d",
            "px_close","px_dist_ma20","px_dist_ma60","px_dist_ma120","px_dist_ma250","px_above_ma250",
            "px_return_1m","px_return_3m","px_return_6m","px_volatility_20d","px_max_drawdown_60d","px_amount_ratio_20_120",
            "label_gain_30d","label_gain_60d","label_max_drawdown_30d","label_max_drawdown_60d",
            "ref_trade_date","computed_at",
        ]
        for c in all_cols:
            if c not in df.columns:
                df[c] = None
        df = df[all_cols]
        ph = ",".join("?" * len(all_cols))
        records = [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False, name=None)]
        conn.executemany(
            f"INSERT OR REPLACE INTO fact_event_features_pit ({','.join(all_cols)}) VALUES ({ph})",
            records,
        )
        conn.commit()
        logger.info("写入 fact_event_features_pit %d 行", len(df))
    conn.close()
    return df


def report_coverage(df: pd.DataFrame):
    total = len(df)
    if total == 0:
        return
    logger.info("=== 列非空率 (%d 行) ===", total)
    families = {
        "EV event": ["ev_premium_pct","ev_premium_bucket","ev_hold_amount","ev_change_amount"],
        "MG margin": ["mg_rz_balance","mg_rz_buy_20d_sum","mg_rq_sell_20d_sum","mg_rz_balance_pct_20d"],
        "PX price": ["px_close","px_dist_ma20","px_dist_ma60","px_dist_ma120","px_dist_ma250",
                     "px_above_ma250","px_return_1m","px_return_3m","px_return_6m",
                     "px_volatility_20d","px_max_drawdown_60d","px_amount_ratio_20_120"],
        "label": ["label_gain_30d","label_gain_60d","label_max_drawdown_30d","label_max_drawdown_60d"],
    }
    for fam, cols in families.items():
        avgs = []
        for c in cols:
            if c in df.columns:
                pct = df[c].notna().sum() * 100.0 / total
                avgs.append(pct)
        if avgs:
            logger.info("  %s (%d 列): 平均 %.1f%%", fam, len(avgs), sum(avgs)/len(avgs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0, help="0=全量 / >0 近 N 天增量")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    df = build(days=args.days, dry_run=args.dry_run)
    report_coverage(df)


if __name__ == "__main__":
    main()
