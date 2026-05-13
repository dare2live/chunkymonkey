"""Phase η++++ — 调研特征每日 mart 构建.

I/O 层 entry point:
  1. 读: raw_institution_surveys (按 stock_code 分组)
  2. 读: dim_trading_calendar (取交易日序列)
  3. 计算: services.sentiment.survey_builder.build_survey_features (纯函数)
  4. 校验: services.sentiment.validators (raise 不静默)
  5. 写: mart_stock_survey_features (atomic, 整体 DELETE+INSERT)

usage:
  PYTHONPATH=backend python backend/scripts/build_survey_features.py [--start 2025-04-01] [--end 2026-05-12]
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date as _date

from services.db import get_conn
from services.sentiment.ddl import MART_STOCK_SURVEY_FEATURES_DDL
from services.sentiment.survey_builder import bin_distribution, build_survey_features


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_survey_features")


def _load_events(conn, start: str | None) -> dict[str, list[tuple[str, int]]]:
    """读 raw_institution_surveys → {stock_code: [(date, inst_count) ...]}."""
    sql = """
        SELECT stock_code, survey_date, COALESCE(inst_count, 0) AS ic
          FROM raw_institution_surveys
         WHERE survey_date >= COALESCE(?, '1970-01-01')
         ORDER BY stock_code, survey_date
    """
    rows = conn.execute(sql, [start]).fetchall()
    out: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for sc, sd, ic in rows:
        if not sc or not sd:
            continue
        out[sc].append((sd, int(ic or 0)))
    return dict(out)


def _load_trading_dates(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """SELECT trade_date FROM dim_trading_calendar
            WHERE is_trading=1 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date""",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="grid 起 (默认 events 最早日)")
    parser.add_argument("--end",   default=None, help="grid 止 (默认今日)")
    args = parser.parse_args()

    t0 = time.time()
    conn = get_conn()
    try:
        # DDL 用 ddl.py 一处定义的 DDL, 不在脚本里重写
        conn.executescript(MART_STOCK_SURVEY_FEATURES_DDL)

        # 1. 读 events
        log.info("读 raw_institution_surveys ...")
        events = _load_events(conn, args.start)
        if not events:
            log.warning("无调研数据, 跳过.")
            return
        n_events = sum(len(v) for v in events.values())
        all_dates = sorted({d for lst in events.values() for d, _ in lst})
        actual_start = args.start or all_dates[0]
        actual_end = args.end or _date.today().isoformat()
        log.info(f"  events: {n_events:,} 条 / {len(events):,} 股, 日期 {all_dates[0]} → {all_dates[-1]}")
        log.info(f"  grid 范围: {actual_start} → {actual_end}")

        # 2. 取交易日 (减少 grid 行数)
        log.info("读 dim_trading_calendar ...")
        trading_dates = _load_trading_dates(conn, actual_start, actual_end)
        log.info(f"  交易日: {len(trading_dates):,} 天")

        # 3. orchestrator (纯函数)
        log.info("构建 survey features (含校验) ...")
        rows = build_survey_features(
            events_by_stock=events,
            grid_start=actual_start,
            grid_end=actual_end,
            trading_dates=trading_dates,
            validate=True,
        )
        log.info(f"  feature rows: {len(rows):,}")
        dist = bin_distribution(rows)
        log.info(f"  桶分布: {dist}")

        # 4. 写库 (atomic)
        log.info("写库 (DELETE + INSERT) ...")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM mart_stock_survey_features WHERE as_of_date BETWEEN ? AND ?",
                [actual_start, actual_end],
            )
            if rows:
                conn.executemany(
                    """INSERT INTO mart_stock_survey_features
                       (stock_code, as_of_date, survey_count_30d, survey_count_60d,
                        survey_inst_30d, survey_inst_60d, survey_bin)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [(r.stock_code, r.as_of_date, r.survey_count_30d, r.survey_count_60d,
                      r.survey_inst_30d, r.survey_inst_60d, r.survey_bin) for r in rows],
                )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 5. 报告
        print(f"\n{'='*90}")
        print(f"  mart_stock_survey_features 构建完成")
        print(f"{'='*90}")
        print(f"  feature rows: {len(rows):,}")
        print(f"  桶分布: {dist}")
        print(f"  耗时: {time.time()-t0:.1f}s")
        print(f"{'='*90}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
