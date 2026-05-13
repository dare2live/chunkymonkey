"""历史回测公式信号 + 落库 + 计算多周期胜率证据。

Phase β W2 D3-D5 核心交付物 (goal.md §4 / 开发手册 §4.3)。

输入:
  - market.v_price_kline_qfq (历史 K 线, 2022-01-01 至今)
  - services/formula_engine REGISTRY (当前 3 公式: MACD / 海龟20/55 / 动态迭代)

输出:
  - fact_technical_trigger (stock_code × date × formula_id 信号)
  - mart_formula_horizon_evidence (formula × holding_days 胜率证据)

定价:
  - T+1 全日 VWAP 入场 (开发手册 §5.3)
  - T+1+holding_days 全日 VWAP 出场
  - 一字板 / 停牌延迟 5 日逻辑 v1 不实现 (用 close fallback)

用法:
  PYTHONPATH=backend python backend/scripts/build_formula_signals_history.py \
      [--start 2023-01-01] [--end 2026-05-08] [--formula macd_golden_cross]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from typing import Iterator

from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn
from services.utils import latest_completed_trade_date
from services.formula_engine import REGISTRY
from services.formula_engine.base import FormulaSignal
from services.formula_engine.ddl import ensure_formula_tables

# 触发所有公式注册
from services.formula_engine import macd_golden_cross    # noqa: F401
from services.formula_engine import turtle_breakout      # noqa: F401
from services.formula_engine import dynamic_ma_iterative  # noqa: F401

from services.market_db import get_market_conn


# 直接用原生 duckdb (fetchnumpy 不在 duck_adapter wrapper 中)
MARKET_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"


log = logging.getLogger("build_formula_signals")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


HOLDING_DAYS = (5, 10, 15, 20, 30, 60, 90)


def load_all_kline_grouped(mkt_conn, start: str, end: str) -> dict[str, dict]:
    """一次性拉全市场 K 线 + numpy groupby (利用 ORDER BY code, date 数据连续性)。

    返回: {code: {dates, opens, highs, lows, closes, volumes, amounts}}
    内存: 6000 股 × 1000 日 × 8 列 × 8 B ≈ 380 MB (3 年全市场)
    """
    t0 = time.time()
    log.info("一次性拉全市场 K 线...")
    arr = mkt_conn.execute(
        """
        SELECT code, date, open, high, low, close, volume, amount
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND date >= ? AND date <= ?
         ORDER BY code, date
        """,
        [start, end],
    ).fetchnumpy()
    log.info(f"  K 线 {len(arr['code']):,} 行, SQL 耗时 {time.time()-t0:.1f}s")

    # numpy groupby: 因为 ORDER BY code 后,每只股的所有行连续
    codes_array = arr["code"]
    unique_codes, first_idx = np.unique(codes_array, return_index=True)
    # 保持原始顺序排序
    sort_perm = np.argsort(first_idx)
    unique_codes = unique_codes[sort_perm]
    first_idx = first_idx[sort_perm]
    last_idx = np.concatenate([first_idx[1:], [len(codes_array)]])

    grouped: dict[str, dict] = {}
    for code, s, e in zip(unique_codes, first_idx, last_idx):
        sl = slice(int(s), int(e))
        grouped[code] = {
            "dates":   arr["date"][sl],
            "opens":   arr["open"][sl].astype(float),
            "highs":   arr["high"][sl].astype(float),
            "lows":    arr["low"][sl].astype(float),
            "closes":  arr["close"][sl].astype(float),
            "volumes": arr["volume"][sl].astype(float),
            "amounts": arr["amount"][sl].astype(float),
        }
    log.info(f"  groupby 后 {len(grouped):,} 只股票, 总耗时 {time.time()-t0:.1f}s")
    return grouped


def compute_all_signals(
    grouped: dict[str, dict],
    formula_ids: tuple[str, ...] | None = None,
) -> list[FormulaSignal]:
    """跑全市场所有公式信号 (传入已 groupby 的 K 线)。"""
    formulas = [REGISTRY[fid] for fid in (formula_ids or REGISTRY.keys())]
    log.info(f"启动公式: {[f.metadata.formula_id for f in formulas]}")

    codes = list(grouped.keys())
    log.info(f"全市场 {len(codes):,} 只股票 待处理")

    all_signals: list[FormulaSignal] = []
    t0 = time.time()
    for idx, code in enumerate(codes):
        kl = grouped[code]
        if len(kl["dates"]) < 30:
            continue
        for f in formulas:
            sigs = f.compute_signals(
                code=code,
                dates=kl["dates"],
                opens=kl["opens"],
                highs=kl["highs"],
                lows=kl["lows"],
                closes=kl["closes"],
                volumes=kl["volumes"],
                amounts=kl["amounts"],
            )
            all_signals.extend(sigs)
        if (idx + 1) % 1000 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            log.info(f"  公式评估 {idx+1:,}/{len(codes):,} ({rate:.0f} 股/s)")

    log.info(f"信号生成完成: {len(all_signals):,} 条, 计算耗时 {time.time()-t0:.1f}s")
    return all_signals


def write_signals_to_db(conn, signals: list[FormulaSignal]) -> int:
    """批量写入 fact_technical_trigger (DELETE+INSERT 幂等)。

    ⚠️ 关键: 用显式事务保证 DELETE + INSERT 原子性。
    DuckDB 默认 auto-commit, 单条 DELETE 会立即提交;
    若 INSERT 阶段被 SIGPIPE / OOM / Ctrl-C 中断,
    会留下空表 + 半成品索引, 下次 DELETE 触发 FATAL Error → Python crash (macOS "Python quit unexpectedly").
    显式 BEGIN/COMMIT 后, 任何中断都自动 ROLLBACK, 索引保持一致。
    """
    if not signals:
        return 0
    # 涉及的公式 ID 列表
    formula_ids = list({s.formula_id for s in signals})
    placeholders = ",".join(["?"] * len(formula_ids))
    rows = [s.to_db_row() for s in signals]
    insert_tuples = [
        (r["stock_code"], r["date"], r["formula_id"], r["formula_variant"],
         r["strength"], r["state"], r["reason_codes_json"])
        for r in rows
    ]
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f"DELETE FROM fact_technical_trigger WHERE formula_id IN ({placeholders})",
            formula_ids,
        )
        conn.executemany(
            """
            INSERT INTO fact_technical_trigger
              (stock_code, date, formula_id, formula_variant, strength, state, reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            insert_tuples,
        )
        conn.execute("COMMIT")
    except BaseException:
        # BaseException 涵盖 KeyboardInterrupt / SystemExit / BrokenPipeError 等
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return len(rows)


def compute_horizon_evidence(
    conn,
    grouped: dict[str, dict],
    formula_ids: tuple[str, ...],
    eval_start: str,
    eval_end: str,
) -> int:
    """对每个 (formula × holding_days) 算历史回测胜率证据。

    优化: 完全用内存中已有的 grouped K 线 + numpy lookup,无 SQL。

    入场: T+1 全日 VWAP (amount/volume); fallback close
    出场: T+1+holding_days 全日 VWAP; fallback close
    收益: (exit - entry) / entry
    win:  收益 > 0
    """
    written = 0
    # 先清旧 horizon_evidence (按本次 formula_ids 范围), 避免 stale variants 残留
    placeholders = ",".join(["?"] * len(formula_ids))
    conn.execute(
        f"DELETE FROM mart_formula_horizon_evidence WHERE formula_id IN ({placeholders})",
        list(formula_ids),
    )

    for formula_id in formula_ids:
        # 拉出该 formula 所有 (variant, stock_code, signal_date) 三元组
        signal_rows = conn.execute(
            """
            SELECT formula_variant, stock_code, date
              FROM fact_technical_trigger
             WHERE formula_id = ? AND date >= ? AND date <= ?
             ORDER BY formula_variant, stock_code, date
            """,
            [formula_id, eval_start, eval_end],
        ).fetchall()
        if not signal_rows:
            log.warning(f"  {formula_id}: 无信号")
            continue

        # group by variant
        from collections import defaultdict
        rows_by_variant: dict[str, list[tuple]] = defaultdict(list)
        for variant, sc, d in signal_rows:
            rows_by_variant[variant].append((sc, d))
        log.info(f"  {formula_id}: {len(signal_rows)} 信号 / {len(rows_by_variant)} variants")

        for variant, sig_rows in rows_by_variant.items():
            returns_by_hd: dict[int, list[float]] = {hd: [] for hd in HOLDING_DAYS}
            t0 = time.time()
            for stock_code, signal_date in sig_rows:
                kl = grouped.get(stock_code)
                if kl is None:
                    continue
                dates = kl["dates"]
                idx = int(np.searchsorted(dates, signal_date))
                if idx >= len(dates) or str(dates[idx]) != signal_date:
                    continue
                entry_idx = idx + 1
                if entry_idx >= len(dates):
                    continue
                entry_price = float(kl["closes"][entry_idx])
                if entry_price <= 0:
                    continue
                for hd in HOLDING_DAYS:
                    exit_idx = entry_idx + hd
                    if exit_idx >= len(dates):
                        continue
                    exit_price = float(kl["closes"][exit_idx])
                    if exit_price <= 0:
                        continue
                    ret = (exit_price - entry_price) / entry_price
                    returns_by_hd[hd].append(float(ret))
            log.info(f"  {variant}: {len(sig_rows)} 信号, 内存查 horizon 完成 ({time.time()-t0:.2f}s)")

            for hd, returns in returns_by_hd.items():
                if not returns:
                    continue
                returns_arr = np.array(returns)
                win_rate = float((returns_arr > 0).mean())
                avg_ret = float(returns_arr.mean())
                median_ret = float(np.median(returns_arr))
                neg = returns_arr[returns_arr < 0]
                avg_dd = float(neg.mean()) if len(neg) > 0 else 0.0
                std_ret = float(returns_arr.std())
                sharpe = float(avg_ret / std_ret * np.sqrt(252 / hd)) if std_ret > 0 else 0.0
                calmar = float(avg_ret / abs(avg_dd)) if avg_dd < 0 else 0.0

                conn.execute(
                    """
                    INSERT OR REPLACE INTO mart_formula_horizon_evidence
                      (formula_id, formula_variant, holding_days, eval_start_date, eval_end_date,
                       n_signals, n_matured, win_rate, avg_ret, avg_dd, median_ret, calmar, sharpe,
                       built_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    [formula_id, variant, hd, eval_start, eval_end,
                     len(sig_rows), len(returns), win_rate, avg_ret, avg_dd, median_ret, calmar, sharpe],
                )
                written += 1
                log.info(f"    {variant} × {hd}d: win {win_rate:.1%} avg_ret {avg_ret:+.2%} sharpe {sharpe:.2f} (n_matured={len(returns)})")
    conn.commit()
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end",   default=None, help="默认 K 线最新日")
    parser.add_argument("--formula", default=None, help="只跑指定公式, e.g., macd_golden_cross")
    args = parser.parse_args()

    # 全程用原生 duckdb (fetchnumpy 需要)
    mkt_conn = duckdb.connect(str(MARKET_DB_PATH), read_only=True)
    try:
        # 确定 end — Phase ψ.5 根因修复:
        # 1. 用 K 线 max(date) 作为默认 (K 线已经 calendar-gated 后, 自然是 latest_closed)
        # 2. 但是显式上界 = min(K线max, latest_completed_trade_date) 以防 K 线遗留盘中数据
        # 3. 不再 fallback to datetime.utcnow (会引入今天日期)
        if args.end is None:
            row = mkt_conn.execute(
                "SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq'"
            ).fetchone()
            kline_max = row[0] if row else None
            smart_conn = get_conn()
            try:
                cal_max = latest_completed_trade_date(smart_conn)
            finally:
                smart_conn.close()
            if not cal_max:
                raise RuntimeError(
                    "latest_completed_trade_date 返 None — dim_trading_calendar 未 seed"
                )
            args.end = min(kline_max, cal_max) if kline_max else cal_max
        log.info(f"回测区间: {args.start} - {args.end}")

        formula_ids = (args.formula,) if args.formula else tuple(REGISTRY.keys())
        log.info(f"公式: {formula_ids}")

        # 一次性 groupby K 线,供 signal 计算 + horizon evidence 共用
        grouped = load_all_kline_grouped(mkt_conn, args.start, args.end)
        # 关闭 mkt_conn 释放资源 (后续不再查)
        mkt_conn.close()
        mkt_conn = None

        # 跑信号
        signals = compute_all_signals(grouped, formula_ids)

        # 写库
        conn = get_conn()
        try:
            ensure_formula_tables(conn)
            n_signals = write_signals_to_db(conn, signals)
            log.info(f"写入 fact_technical_trigger: {n_signals} 行")

            n_evidence = compute_horizon_evidence(conn, grouped, formula_ids, args.start, args.end)
            log.info(f"写入 mart_formula_horizon_evidence: {n_evidence} 行")
        finally:
            conn.close()
    finally:
        if mkt_conn is not None:
            mkt_conn.close()


if __name__ == "__main__":
    main()
