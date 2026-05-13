"""Phase δ D3 — 全公式 × 全历史 IC 落库。

对 fact_technical_trigger 中每个 (formula_id, date) 组合,
计算 5/10/30 日 forward return 的 Spearman IC, 写入 mart_signal_ic。

用法:
  PYTHONPATH=backend python backend/scripts/build_signal_ic_daily.py [--from 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date as _date, timedelta

from services.db import get_conn
from services.market_db import get_market_conn
from services.paper_engine.ddl import ensure_paper_tables
from services.paper_engine.signal_ic import spearman_ic


log = logging.getLogger("build_signal_ic_daily")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def build_ic_for_period(start: str, end: str) -> int:
    """对 [start, end] 间所有 (formula_id, date) 算 IC + 落库。返回行数。"""
    t0 = time.time()
    conn = get_conn()
    mkt = get_market_conn()
    try:
        ensure_paper_tables(conn)

        # 一次拉全市场 K 线 (内存索引: code → {date: close})
        log.info("加载全市场 K 线 (内存)...")
        kl_rows = mkt.execute(
            """
            SELECT code, date, close FROM v_price_kline_qfq
             WHERE adjust='qfq' AND freq='daily' AND date >= ? AND date <= ?
            """,
            [start, (_date.fromisoformat(end) + timedelta(days=45)).isoformat()],
        ).fetchall()
        kl_by_code: dict[str, dict[str, float]] = {}
        for c, d, cl in kl_rows:
            kl_by_code.setdefault(c, {})[str(d)] = float(cl) if cl else 0.0
        log.info(f"  K 线 {len(kl_rows):,} 行 / {len(kl_by_code):,} 股")

        # 拉所有信号 (含 date / formula / variant / strength)
        sig_rows = conn.execute(
            """
            SELECT stock_code, date, formula_id, formula_variant, strength
              FROM fact_technical_trigger
             WHERE date >= ? AND date <= ?
            """,
            [start, end],
        ).fetchall()
        log.info(f"  信号 {len(sig_rows):,} 行")

        # group by (date, formula_id, variant)
        from collections import defaultdict
        groups: dict[tuple, list[tuple]] = defaultdict(list)
        for sc, d, fid, fvar, strength in sig_rows:
            groups[(str(d), fid, fvar or fid)].append((sc, float(strength) if strength else 0.0))

        def _close_after(code: str, base_date: str, days: int) -> float | None:
            """简化: base_date + days 自然日, 找最近的可用日 (±2 日)。"""
            d0 = _date.fromisoformat(base_date)
            for offset in (0, -1, 1, -2, 2):
                target = (d0 + timedelta(days=int(days * 1.45) + offset)).isoformat()
                close = kl_by_code.get(code, {}).get(target)
                if close and close > 0:
                    return close
            return None

        # 逐组算 IC
        out_rows = []
        t_calc = time.time()
        for (snapshot_date, fid, fvar), signals in groups.items():
            if len(signals) < 5:
                continue
            scores = []
            entries = []
            codes_in = []
            for sc, strength in signals:
                entry = kl_by_code.get(sc, {}).get(snapshot_date)
                if entry and entry > 0:
                    scores.append(strength)
                    entries.append(entry)
                    codes_in.append(sc)
            if len(scores) < 5:
                continue
            rets_5 = [
                (_close_after(c, snapshot_date, 5) / e - 1) if _close_after(c, snapshot_date, 5) else None
                for c, e in zip(codes_in, entries)
            ]
            rets_10 = [
                (_close_after(c, snapshot_date, 10) / e - 1) if _close_after(c, snapshot_date, 10) else None
                for c, e in zip(codes_in, entries)
            ]
            rets_30 = [
                (_close_after(c, snapshot_date, 30) / e - 1) if _close_after(c, snapshot_date, 30) else None
                for c, e in zip(codes_in, entries)
            ]
            ic5 = spearman_ic(scores, rets_5)
            ic10 = spearman_ic(scores, rets_10)
            ic30 = spearman_ic(scores, rets_30)
            out_rows.append((snapshot_date, fid, fvar, len(signals), ic5, ic10, ic30))
        log.info(f"  IC 计算 {len(out_rows):,} 组合 ({time.time()-t_calc:.1f}s)")

        # 写库 (事务原子)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM mart_signal_ic WHERE snapshot_date >= ? AND snapshot_date <= ?",
                [start, end],
            )
            conn.executemany(
                """INSERT INTO mart_signal_ic
                   (snapshot_date, formula_id, formula_variant, n_signals,
                    ic_5d, ic_10d, ic_30d,
                    rank_ic_5d, rank_ic_10d, rank_ic_30d)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(d, fid, fvar, n, ic5, ic10, ic30, ic5, ic10, ic30)
                 for (d, fid, fvar, n, ic5, ic10, ic30) in out_rows],
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

        log.info(f"完成: {len(out_rows):,} 行 (总耗时 {time.time()-t0:.1f}s)")
        return len(out_rows)
    finally:
        conn.close()
        mkt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2024-01-01")
    parser.add_argument("--to", dest="to_date", default=_date.today().isoformat())
    args = parser.parse_args()
    build_ic_for_period(args.from_date, args.to_date)


if __name__ == "__main__":
    main()
