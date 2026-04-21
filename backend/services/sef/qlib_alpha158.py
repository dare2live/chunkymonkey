"""Alpha158 因子批量生成 + 覆盖率验证.

使用 Qlib 0.9.7 的 Alpha158 handler 生成 158 维横截面因子。
结果以 parquet 分月存储，DB 仅保留索引 + 覆盖率元数据。

存储路径: data/qlib_alpha158/year=YYYY/month=MM.parquet
索引表  : qlib_alpha158_index (SEF Phase I 建表)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cm-api.sef.alpha158")

_ALPHA158_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "qlib_alpha158"
_QLIB_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "qlib_data"


def _ensure_qlib_init():
    """Qlib 需要先 init 一次才能用 DatasetH / Alpha158。"""
    import qlib

    if getattr(qlib, "_INITED", False):
        return
    qlib.init(provider_uri=str(_QLIB_DATA_DIR), region="cn", expression_cache=None)
    qlib._INITED = True  # type: ignore[attr-defined]


def _ensure_index_table(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS qlib_alpha158_index (
            year_month          TEXT PRIMARY KEY,
            partition_path      TEXT,
            n_rows              INTEGER,
            n_stocks            INTEGER,
            n_dates             INTEGER,
            coverage_pct        REAL,
            generated_at        TEXT
        );
        """
    )
    conn.commit()


def generate_alpha158(
    conn: sqlite3.Connection,
    *,
    start_date: str = "2023-01-01",
    end_date: Optional[str] = None,
    instruments: str = "all",
    chunk_months: int = 3,
) -> dict:
    """批量生成 Alpha158 因子，分月 parquet 存储。

    Parameters
    ----------
    start_date, end_date
        YYYY-MM-DD。end_date 默认 Qlib 日历最后一天。
    instruments
        "all" 或股票列表。
    chunk_months
        每次生成多少个月（内存控制，默认 3）。
    """
    import pandas as pd
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D

    _ensure_qlib_init()
    _ensure_index_table(conn)
    _ALPHA158_DIR.mkdir(parents=True, exist_ok=True)

    cal = D.calendar(freq="day")
    if not len(cal):
        return {"error": "qlib calendar empty - run qlib_data_handler.dump_bin_from_db first"}

    if end_date is None:
        end_date = str(cal[-1])[:10]

    total_dates = D.calendar(start_time=start_date, end_time=end_date, freq="day")
    if not len(total_dates):
        return {"error": f"no trading days between {start_date} and {end_date}"}

    # D.instruments 返回 dict {"market":..., "filter_pipe":...}，要用 list_instruments 才拿股票清单
    try:
        inst_list = D.list_instruments(D.instruments(market=instruments), as_list=True)
        total_stocks = len(inst_list)
    except Exception:
        # 直接读 instruments 文件兜底
        inst_file = _QLIB_DATA_DIR / "instruments" / "all.txt"
        total_stocks = sum(1 for _ in inst_file.open()) if inst_file.exists() else 0

    logger.info(
        "[SEF] Alpha158 生成 start=%s end=%s 共 %d 交易日 x 约 %d 股 (instruments=%s)",
        start_date,
        end_date,
        len(total_dates),
        total_stocks,
        instruments,
    )

    result_stats = {
        "partitions": 0,
        "total_rows": 0,
        "total_stocks": 0,
        "total_dates": len(total_dates),
        "start_date": start_date,
        "end_date": end_date,
        "partition_files": [],
    }

    # 分月切片，避免一次 load 全部
    cur = pd.Timestamp(start_date).to_period("M")
    last = pd.Timestamp(end_date).to_period("M")
    all_stocks: set[str] = set()
    while cur <= last:
        block_end_period = min(cur + (chunk_months - 1), last)
        block_start = cur.start_time.strftime("%Y-%m-%d")
        block_end = block_end_period.end_time.strftime("%Y-%m-%d")

        try:
            handler = Alpha158(
                instruments=instruments,
                start_time=block_start,
                end_time=block_end,
                fit_start_time=block_start,
                fit_end_time=block_end,
                infer_processors=[],
                learn_processors=[],
            )
            df = handler.fetch(col_set="feature")
        except Exception as e:  # noqa: BLE001
            logger.exception("[SEF] Alpha158 生成失败: %s .. %s : %s", block_start, block_end, e)
            cur = block_end_period + 1
            continue

        if df is None or df.empty:
            cur = block_end_period + 1
            continue

        # df MultiIndex (datetime, instrument); reset + save
        df = df.reset_index()
        df.rename(columns={"datetime": "trade_date", "instrument": "stock_code"}, inplace=True)
        df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 10)

        # 一分区存一个月（便于后续月度过滤）
        for ym, block in df.groupby(df["trade_date"].str.slice(0, 7)):
            part_path = _ALPHA158_DIR / f"year={ym[:4]}" / f"month={ym[5:7]}.parquet"
            part_path.parent.mkdir(parents=True, exist_ok=True)
            block.to_parquet(part_path, index=False)

            stocks = block["stock_code"].nunique()
            dates = block["trade_date"].nunique()
            n_rows = len(block)
            coverage = round(stocks / total_stocks * 100, 2) if total_stocks else None

            conn.execute(
                """
                INSERT INTO qlib_alpha158_index(
                    year_month, partition_path, n_rows, n_stocks, n_dates,
                    coverage_pct, generated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(year_month) DO UPDATE SET
                    partition_path=excluded.partition_path,
                    n_rows=excluded.n_rows,
                    n_stocks=excluded.n_stocks,
                    n_dates=excluded.n_dates,
                    coverage_pct=excluded.coverage_pct,
                    generated_at=excluded.generated_at
                """,
                (
                    ym,
                    str(part_path),
                    n_rows,
                    stocks,
                    dates,
                    coverage,
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

            all_stocks.update(block["stock_code"].unique().tolist())
            result_stats["partitions"] += 1
            result_stats["total_rows"] += n_rows
            result_stats["partition_files"].append(str(part_path))

        cur = block_end_period + 1

    result_stats["total_stocks"] = len(all_stocks)
    if total_stocks:
        result_stats["coverage_pct"] = round(len(all_stocks) / total_stocks * 100, 2)
    logger.info("[SEF] Alpha158 生成完成：%s partitions, %s rows", result_stats["partitions"], result_stats["total_rows"])
    return result_stats


def alpha158_status(conn: sqlite3.Connection) -> dict:
    """检查当前 Alpha158 覆盖情况。"""
    _ensure_index_table(conn)
    rows = conn.execute(
        "SELECT year_month, n_rows, n_stocks, n_dates, coverage_pct FROM qlib_alpha158_index "
        "ORDER BY year_month"
    ).fetchall()
    return {
        "partitions": len(rows),
        "months": [r[0] for r in rows],
        "total_rows": sum(r[1] or 0 for r in rows),
        "avg_coverage_pct": (
            round(sum((r[4] or 0) for r in rows) / len(rows), 2) if rows else None
        ),
        "entries": [dict(zip(["year_month", "n_rows", "n_stocks", "n_dates", "coverage_pct"], r)) for r in rows],
    }
