"""风险因子计算 — P1.6 (2026-04-28).

从 market.duckdb canonical K-line relation 算每只股的 risk profile:
- vol_30d / vol_60d / vol_120d: 日收益率标准差 (年化)
- max_drawdown_60d / max_drawdown_120d: 最大回撤 (从峰值跌幅)
- sharpe_30d / sharpe_60d: 夏普 (年化超额收益 / vol)
- skew_60d / kurt_60d: 偏度 / 峰度 (尾部风险)
- mom_30d / mom_120d: 动量 (相对涨跌)

不算 beta — 需要基准 (沪深 300) + 全历史 K 线, 单独 P3 做.

输出表: fact_risk_factors (P0.1 schema_version 已声明 v1 略, 这里 ensure 时加).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

logger = logging.getLogger("cm-api.risk_factors")

TRADING_DAYS_PER_YEAR = 252


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fact_risk_factors (
            stock_code   TEXT NOT NULL,
            calc_date    TEXT NOT NULL,
            vol_30d      DOUBLE,
            vol_60d      DOUBLE,
            vol_120d     DOUBLE,
            max_dd_60d   DOUBLE,
            max_dd_120d  DOUBLE,
            sharpe_30d   DOUBLE,
            sharpe_60d   DOUBLE,
            skew_60d     DOUBLE,
            kurt_60d     DOUBLE,
            mom_30d      DOUBLE,
            mom_120d     DOUBLE,
            n_bars       INTEGER,
            ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, calc_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rf_date ON fact_risk_factors(calc_date)")
    conn.commit()


def _series_stats(closes: list[float]) -> dict:
    """收益率序列统计. closes 时间正序 (oldest first)."""
    out = {}
    if len(closes) < 2:
        return {}
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] and closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if not rets:
        return {}

    def _vol(window):
        if len(rets) < window:
            return None
        sub = rets[-window:]
        n = len(sub)
        mean = sum(sub) / n
        var = sum((x - mean) ** 2 for x in sub) / (n - 1) if n > 1 else 0
        return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)

    def _sharpe(window):
        if len(rets) < window:
            return None
        sub = rets[-window:]
        n = len(sub)
        mean = sum(sub) / n
        var = sum((x - mean) ** 2 for x in sub) / (n - 1) if n > 1 else 0
        sd = math.sqrt(var)
        if sd == 0:
            return None
        return (mean * TRADING_DAYS_PER_YEAR) / (sd * math.sqrt(TRADING_DAYS_PER_YEAR))

    def _max_dd(window):
        if len(closes) < window:
            return None
        sub = closes[-window:]
        peak = sub[0]
        max_dd = 0.0
        for p in sub:
            if p > peak:
                peak = p
            if peak > 0:
                dd = (peak - p) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    def _mom(window):
        if len(closes) < window:
            return None
        if closes[-window] <= 0:
            return None
        return closes[-1] / closes[-window] - 1

    out["vol_30d"] = _vol(30)
    out["vol_60d"] = _vol(60)
    out["vol_120d"] = _vol(120)
    out["max_dd_60d"] = _max_dd(60)
    out["max_dd_120d"] = _max_dd(120)
    out["sharpe_30d"] = _sharpe(30)
    out["sharpe_60d"] = _sharpe(60)
    out["mom_30d"] = _mom(30)
    out["mom_120d"] = _mom(120)

    # skew / kurt (近 60 天)
    if len(rets) >= 60:
        sub = rets[-60:]
        n = len(sub)
        mean = sum(sub) / n
        m2 = sum((x - mean) ** 2 for x in sub) / n
        m3 = sum((x - mean) ** 3 for x in sub) / n
        m4 = sum((x - mean) ** 4 for x in sub) / n
        if m2 > 0:
            out["skew_60d"] = m3 / (m2 ** 1.5)
            out["kurt_60d"] = m4 / (m2 ** 2) - 3
    return out


def calc_risk_factors(conn, *, lookback_days: int = 250, max_stocks: int | None = None) -> dict:
    """全市场跑 risk factors. 跑近 lookback_days K 线."""
    ensure_table(conn)
    t0 = time.time()

    # K 线在独立 market.duckdb, 用 market_db.get_market_conn (单独连接).
    # smartmoney 主库连接 conn 不能 ATTACH (会冲突 lock).
    from services.market_db import get_canonical_kline_qfq_relation, get_market_conn
    kline_relation = get_canonical_kline_qfq_relation()
    try:
        market_conn = get_market_conn()
    except Exception as exc:
        logger.warning(f"[risk_factors] 打开 market.duckdb 失败: {exc}")
        return {"status": "error", "error": str(exc)}

    # 探查 K 线表
    try:
        market_conn.execute(f"SELECT 1 FROM {kline_relation} LIMIT 1").fetchone()
    except Exception as exc:
        market_conn.close()
        return {"status": "no_kline_table", "error": str(exc)}

    # 拿活跃股清单 (smartmoney 库)
    try:
        rows = conn.execute("SELECT stock_code FROM dim_active_a_stock").fetchall()
        stocks = [r[0] for r in rows]
    except Exception:
        stocks = []
    if max_stocks:
        stocks = stocks[:max_stocks]

    if not stocks:
        market_conn.close()
        return {"status": "no_active_stocks"}

    n_processed = 0
    n_written = 0
    today_iso = None
    for sc in stocks:
        try:
            # 拉近 lookback_days 个交易日的收盘价
            df_rows = market_conn.execute(f"""
                SELECT date, close FROM {kline_relation}
                WHERE code = ? AND freq = 'daily' AND adjust = 'qfq'
                  AND close IS NOT NULL AND close > 0
                ORDER BY date DESC LIMIT ?
            """, [sc, lookback_days]).fetchall()
            if len(df_rows) < 30:
                continue
            df_rows = list(reversed(df_rows))  # 转正序
            closes = [float(r[1]) for r in df_rows]
            calc_date = str(df_rows[-1][0])[:10]
            if today_iso is None:
                today_iso = calc_date

            stats = _series_stats(closes)
            if not stats:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO fact_risk_factors
                   (stock_code, calc_date, vol_30d, vol_60d, vol_120d,
                    max_dd_60d, max_dd_120d, sharpe_30d, sharpe_60d,
                    skew_60d, kurt_60d, mom_30d, mom_120d, n_bars)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    sc, calc_date,
                    stats.get("vol_30d"), stats.get("vol_60d"), stats.get("vol_120d"),
                    stats.get("max_dd_60d"), stats.get("max_dd_120d"),
                    stats.get("sharpe_30d"), stats.get("sharpe_60d"),
                    stats.get("skew_60d"), stats.get("kurt_60d"),
                    stats.get("mom_30d"), stats.get("mom_120d"),
                    len(closes),
                ],
            )
            n_written += 1
        except Exception as exc:
            logger.debug(f"[risk_factors] {sc}: {exc}")
        n_processed += 1
    market_conn.close()
    conn.commit()

    # P0.1 schema_version
    try:
        from services.schema_versions import record_actual_version
        record_actual_version(conn, "fact_risk_factors", "v1")
    except Exception:
        pass

    elapsed = time.time() - t0
    logger.info(
        f"[risk_factors] {n_written}/{n_processed} 只股 / {elapsed:.1f}s, calc_date={today_iso}"
    )
    return {
        "status": "ok",
        "n_processed": n_processed,
        "n_written": n_written,
        "calc_date": today_iso,
        "elapsed_s": round(elapsed, 2),
    }
