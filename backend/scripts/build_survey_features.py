"""Phase η++++ — 调研特征每日 mart 构建.

I/O 层 entry point:
  1. 读: raw_institution_surveys (按 stock_code 分组)
  2. 读: dim_trading_calendar (取交易日序列)
  3. 计算: services.sentiment.survey_builder.build_survey_features (纯函数)
  4. 校验: services.sentiment.validators (raise 不静默)
  5. 写: mart_stock_survey_features (atomic, 窗口 DELETE+INSERT)

usage:
  PYTHONPATH=backend python backend/scripts/build_survey_features.py [--start 2025-04-01] [--write-start 2026-05-21] [--end 2026-05-29]
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date as _date, timedelta

from services.db import get_conn
from services.sentiment.configs import WINDOWS, WindowConfig
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


def _lookback_start(write_start: str, win_cfg: WindowConfig = WINDOWS) -> str:
    long_days = int(win_cfg.survey_long_days)
    if long_days <= 0:
        raise ValueError(f"survey_long_days must be positive, got {long_days}")
    return (_date.fromisoformat(write_start) - timedelta(days=long_days - 1)).isoformat()


def _resolve_windows(
    *,
    arg_start: str | None,
    arg_write_start: str | None,
    arg_end: str | None,
    earliest_event_date: str,
    default_end: str,
    win_cfg: WindowConfig = WINDOWS,
) -> tuple[str, str, str]:
    """Return (read_start, write_start, write_end).

    ``read_start`` may be earlier than ``write_start`` so rolling 30/60-day
    features can refresh a narrow output window without losing lookback events.
    """
    write_end = arg_end or default_end
    write_start = arg_write_start or arg_start or earliest_event_date
    read_start = arg_start or (
        _lookback_start(write_start, win_cfg) if arg_write_start else write_start
    )
    if read_start > write_start:
        raise ValueError(f"--start ({read_start}) cannot be after --write-start ({write_start})")
    if write_start > write_end:
        raise ValueError(f"write window start ({write_start}) cannot be after end ({write_end})")
    return read_start, write_start, write_end


def _require_non_empty_window(
    *,
    label: str,
    count: int,
    write_start: str,
    write_end: str,
    allow_empty_window: bool,
) -> None:
    if count > 0 or allow_empty_window:
        return
    raise RuntimeError(
        f"写入窗口 {write_start} → {write_end} {label} 为 0；"
        "为避免误删 mart_stock_survey_features，默认中止；"
        "如确认为空窗口，显式传 --allow-empty-window"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="事件读取起点；无 --write-start 时也作为写入起点")
    parser.add_argument("--write-start", default=None, help="写入窗口起点；事件读取会自动包含配置长窗口 lookback")
    parser.add_argument("--end",   default=None, help="写入窗口止 (默认最近已收盘交易日)")
    parser.add_argument(
        "--allow-empty-window",
        action="store_true",
        help="允许写入窗口产出 0 行并执行删除；默认 fail-closed 防误删",
    )
    args = parser.parse_args()

    t0 = time.time()
    conn = get_conn()
    try:
        # DDL 用 ddl.py 一处定义的 DDL, 不在脚本里重写
        conn.executescript(MART_STOCK_SURVEY_FEATURES_DDL)

        # 1. 读 events
        default_end = args.end
        if not default_end:
            from services.utils import latest_closed_or_raise
            default_end = latest_closed_or_raise()  # Phase ψ.5: calendar-gated
        earliest_row = conn.execute(
            "SELECT MIN(survey_date) FROM raw_institution_surveys WHERE survey_date IS NOT NULL"
        ).fetchone()
        if not earliest_row or not earliest_row[0]:
            log.warning("无调研数据, 跳过.")
            return
        read_start, write_start, write_end = _resolve_windows(
            arg_start=args.start,
            arg_write_start=args.write_start,
            arg_end=args.end,
            earliest_event_date=str(earliest_row[0]),
            default_end=default_end,
        )

        log.info("读 raw_institution_surveys ...")
        events = _load_events(conn, read_start)
        n_events = sum(len(v) for v in events.values())
        if events:
            all_dates = sorted({d for lst in events.values() for d, _ in lst})
            date_range = f"{all_dates[0]} → {all_dates[-1]}"
        else:
            date_range = "read window 内无事件"
        log.info(f"  events: {n_events:,} 条 / {len(events):,} 股, 日期 {date_range}")
        log.info(f"  read 起点: {read_start}; write 窗口: {write_start} → {write_end}")

        # 2. 取交易日 (减少 grid 行数)
        log.info("读 dim_trading_calendar ...")
        trading_dates = _load_trading_dates(conn, write_start, write_end)
        log.info(f"  交易日: {len(trading_dates):,} 天")
        _require_non_empty_window(
            label="交易日数",
            count=len(trading_dates),
            write_start=write_start,
            write_end=write_end,
            allow_empty_window=args.allow_empty_window,
        )

        # 3. orchestrator (纯函数)
        log.info("构建 survey features (含校验) ...")
        rows = build_survey_features(
            events_by_stock=events,
            grid_start=write_start,
            grid_end=write_end,
            trading_dates=trading_dates,
            validate=True,
        )
        log.info(f"  feature rows: {len(rows):,}")
        _require_non_empty_window(
            label="产出行数",
            count=len(rows),
            write_start=write_start,
            write_end=write_end,
            allow_empty_window=args.allow_empty_window,
        )
        dist = bin_distribution(rows)
        log.info(f"  桶分布: {dist}")

        # 4. 写库 (atomic)
        log.info("写库 (DELETE + INSERT) ...")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM mart_stock_survey_features WHERE as_of_date BETWEEN ? AND ?",
                [write_start, write_end],
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
