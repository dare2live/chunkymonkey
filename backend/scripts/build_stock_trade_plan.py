"""Phase γ D4 — 生成 mart_stock_trade_plan。

对 daily-topk 推荐股, 拉最近 60 日 K 线, 算 ATR_14 + 20/55 日 entry levels +
2N stop, 调用 services/trade_plan/builder 生成 8 字段 trade plan。

不依赖 fact_stock_turtle_features (有版本/build 延迟问题), 直接从 v_price_kline_qfq 算。

用法:
  PYTHONPATH=backend python backend/scripts/build_stock_trade_plan.py [--date 2026-05-12]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from typing import Any

import numpy as np

from services.db import get_conn
from services.market_db import get_market_conn
from services.picture.ddl import ensure_picture_tables
from services.trade_plan.builder import build_trade_plan


log = logging.getLogger("build_stock_trade_plan")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _today_iso() -> str:
    """Phase ψ.5: snapshot_date 走 calendar (跟 K 线 / 信号一致), 不允许 wall-clock fallback."""
    from services.utils import latest_closed_or_raise
    return latest_closed_or_raise()


def compute_turtle_features(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> dict[str, float]:
    """从 K 线算 ATR_14 + 20/55 日 breakout levels + 2N stop。

    Args:
        highs, lows, closes: 1D 数组, 按日期升序, 长度 ≥ 56

    Returns:
        {atr_14, entry_level_20, entry_level_55, stop_level_20_2n, latest_close}
        若数据不足返回 {}
    """
    n = len(closes)
    if n < 56:
        return {}
    # True Range
    prev_close = np.roll(closes, 1)
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - prev_close),
        np.abs(lows - prev_close),
    ])
    tr[0] = highs[0] - lows[0]
    # ATR_14 = 14 日 TR 简单均值 (Wilder smoothed 更精确但简单均够用)
    atr_14 = float(np.mean(tr[-14:]))

    entry_20 = float(np.max(highs[-20:]))
    entry_55 = float(np.max(highs[-55:]))
    latest_close = float(closes[-1])
    stop_20_2n = latest_close - 2 * atr_14

    return {
        "atr_14": atr_14,
        "atr_14_pct": atr_14 / latest_close if latest_close > 0 else None,
        "entry_level_20": entry_20,
        "entry_level_55": entry_55,
        "stop_level_20_2n": stop_20_2n,
        "latest_close": latest_close,
    }


def build_stock_trade_plan(
    target_date: str | None = None,
    conn=None,
    mkt_conn=None,
    top_k_limit: int = 200,
) -> int:
    """主 entry。返回写入行数。"""
    if not target_date:
        target_date = _today_iso()
    log.info(f"target_date = {target_date}, top_k_limit={top_k_limit}")

    t0 = time.time()
    owns_conn = conn is None
    owns_mkt = mkt_conn is None
    if conn is None:
        conn = get_conn()
    if mkt_conn is None:
        mkt_conn = get_market_conn()
    try:
        ensure_picture_tables(conn)

        # 1. 拉 daily-topk 推荐股
        topk = conn.execute(
            """
            SELECT stock_code FROM mart_daily_recommendation
             WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM mart_daily_recommendation)
             ORDER BY rank_in_date LIMIT ?
            """,
            [top_k_limit],
        ).fetchall()
        codes = [r[0] for r in topk]
        log.info(f"  daily-topk: {len(codes)} 股")

        # 2. 一次拉所有这些股票最近 60 日 K 线
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            kline_rows = mkt_conn.execute(
                f"""
                SELECT code, date, high, low, close
                  FROM v_price_kline_qfq
                 WHERE adjust='qfq' AND freq='daily'
                   AND code IN ({placeholders})
                   AND date <= ?
                 ORDER BY code, date
                """,
                codes + [target_date],
            ).fetchall()
        else:
            kline_rows = []

        # 3. 按 code groupby
        by_code: dict[str, list[tuple]] = {}
        for r in kline_rows:
            by_code.setdefault(r[0], []).append(r)
        log.info(f"  K 线 加载: {len(by_code):,} 股")

        # 4. 加载 expected_horizon_days (基于 mart_stage_formula_fitness 最佳行)
        # 简化: 全用 20 (Phase γ Plan agent 决策)
        # 后续优化: 按 (stock 当前 stage, 命中公式) 查 fitness 表

        # 5. 对每股算 trade plan
        rows_to_write = []
        for code in codes:
            kls = by_code.get(code, [])
            if len(kls) < 56:
                continue
            arr_high = np.array([float(r[2]) for r in kls])
            arr_low = np.array([float(r[3]) for r in kls])
            arr_close = np.array([float(r[4]) for r in kls])
            tf = compute_turtle_features(arr_high, arr_low, arr_close)
            if not tf:
                continue
            plan = build_trade_plan(
                close=tf["latest_close"],
                atr_14=tf["atr_14"],
                atr_14_pct=tf["atr_14_pct"],
                entry_level_20=tf["entry_level_20"],
                entry_level_55=tf["entry_level_55"],
                stop_level_20_2n=tf["stop_level_20_2n"],
                expected_horizon_days=20,
                entry_basis="turtle_20",
            )
            if plan["entry_target_price"] is None:
                continue
            rows_to_write.append((
                code, target_date, "v1",
                plan["entry_target_price"], plan["entry_aggressive_price"], plan["entry_max_price"],
                plan["exit_target_1_price"], plan["exit_target_2_price"], plan["exit_stop_price"],
                plan["risk_reward_ratio"], plan["expected_horizon_days"],
                plan["atr_14"], plan["entry_basis"],
                json.dumps(plan["reason_codes_json"], ensure_ascii=False),
            ))

        log.info(f"  生成 trade plan: {len(rows_to_write)} 行")

        # 6. 写库 (事务原子)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM mart_stock_trade_plan WHERE plan_date = ? AND model_id = 'v1'",
                [target_date],
            )
            conn.executemany(
                """
                INSERT INTO mart_stock_trade_plan
                  (stock_code, plan_date, model_id,
                   entry_target_price, entry_aggressive_price, entry_max_price,
                   exit_target_1_price, exit_target_2_price, exit_stop_price,
                   risk_reward_ratio, expected_horizon_days,
                   atr_14, entry_basis, reason_codes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_to_write,
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        log.info(f"完成: {len(rows_to_write)} 行 (总耗时 {time.time()-t0:.1f}s)")
        return len(rows_to_write)
    finally:
        if owns_conn:
            conn.close()
        if owns_mkt:
            mkt_conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--top-k", type=int, default=200)
    args = parser.parse_args()
    build_stock_trade_plan(args.date, top_k_limit=args.top_k)


if __name__ == "__main__":
    main()
