"""十大流通股东历史回填 —— 数据源是 Phase A 的 staging 快照, 不重新向供应商取数。

# 为什么存在这个脚本

生产 canonical 的历史是空的: 2019-2024 每年只覆盖 1,391-2,000 只股 (约三分之一),
2025 起才跳到 5,184。判例引擎恰恰要那段历史。

Phase A (commit 214516ce2) 已经把全市场取完并存进 staging: 5,212 个批次全部 ok,
2,629,407 行, 覆盖 2003-2026, 墙钟 58 分钟。重新跑一遍 ``ingest_holders_aif10.py
--backfill`` 会再向供应商打 5,447 次请求、再花一小时, 拿到同一批数据。所以本脚本
把 staging 当数据源, 其余一切走既有路径。

# 它没有另写一套清洗/落库逻辑

注入点是 ``aif10_scraper.fetch_all_pages`` —— 也就是 ``_fetch_raw`` 唯一的外部依赖。
换掉它之后 ``sync_holders_aif10`` 的编排 (``_clean`` -> ``_derive_exits`` -> ``_write``
-> land/accept) 与日更**逐字相同**, 本脚本不碰其中任何一步。桩打在外部依赖上,
不打在被测/被用的函数上, 与 test_holders_aif10.py 里的做法一致。

# 范围: report_date >= 20181231

``_clean`` 自带 ``start_period`` 过滤, 默认 ``DEFAULT_START_PERIOD = "20181231"``,
其注释写明依据是「K线起点 2019-01-02, 不抓更早」。staging 里 2019 年前有 1,213,817 行
(占 46%), 那段没有 K 线可配对 —— casebook_outcome_day 是按 K 线算的固定窗口结果,
没有前向收益就无法进判例引擎。这与 report_rc 那次的教训同型: 当时回填了 2010-2019
八十万行, 因唯一消费方按精确日期 JOIN K 线而永久用不上。故本次只接受 2018Q4 起
(2018 年报在 2019 年披露, notice_date 落在 K 线范围内, 可用)。

# PIT

可用日锚是 ``_default_event_instant(partition)``, 由 notice_date (来自 UPDATE_DATE) 推出,
不是 ``fetched_at``。实测 staging 里 UPDATE_DATE 与 NOTICE_DATE 99.82% 同日,
不同的 4,728 行中 UPDATE_DATE **一律晚于** NOTICE_DATE (负向零行) —— 取 UPDATE_DATE
是保守方向。59 行两者皆空, 走 ``fetched_at_observed`` 兜底, 与日更路径同一处理。

用法:
    python backend/scripts/backfill_holders_top10_from_staging.py --limit 50   # 先试点
    python backend/scripts/backfill_holders_top10_from_staging.py              # 全量
"""
from __future__ import annotations

import argparse
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ingest_holders_raw import RAW_ROWS_TABLE  # noqa: E402
from services.db import get_conn  # noqa: E402
from services.db_connection import current_db_paths  # noqa: E402
from services.duck_adapter import audit_connect  # noqa: E402
from services.holders_aif10 import DEFAULT_START_PERIOD, sync_holders_aif10  # noqa: E402

# 不设默认 staging 路径: 用哪一份快照回填是**决定**不是默认值 —— 快照是运行时产物
# (规则 12: 运行时状态不写进手写文件), 且写进生产库这件事应当在命令行上留下它读了哪份证据。


def _install_staging_scraper(staging_path: Path) -> tuple[types.ModuleType, list[str]]:
    """把 aif10_scraper 换成从 staging 读的桩, 返回 (模块, 可用股票清单)。

    只读打开 —— 审计/回填一律 read_only=True (项目规则 6)。
    """
    con = audit_connect(str(staging_path))
    cols = [r[0] for r in con.execute(f"DESCRIBE {RAW_ROWS_TABLE}").fetchall()]
    payload_cols = [c for c in cols if c not in {"fetch_id", "row_ordinal"}]
    stocks = [
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT SECURITY_CODE FROM {RAW_ROWS_TABLE} "
            "WHERE SECURITY_CODE IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]

    quoted = ", ".join(f'"{c}"' for c in payload_cols)

    def fetch_all_pages(_report, *, secucode, page_size=500, max_pages=0, client=None):
        del _report, page_size, max_pages, client
        code = str(secucode).split(".")[0]
        rows = con.execute(
            f"SELECT {quoted} FROM {RAW_ROWS_TABLE} WHERE SECURITY_CODE = ? "
            "ORDER BY row_ordinal",
            [code],
        ).fetchall()
        return [dict(zip(payload_cols, r)) for r in rows]

    mod = types.ModuleType("aif10_scraper")
    mod.fetch_all_pages = fetch_all_pages
    mod.default_client = object()
    sys.modules["aif10_scraper"] = mod
    return con, stocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--staging",
        required=True,
        help="Phase A staging 快照的 DuckDB 文件路径, 必填 —— 见模块 docstring。",
    )
    ap.add_argument("--start-period", default=DEFAULT_START_PERIOD)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只 (试点)")
    ap.add_argument("--progress-every", type=int, default=200)
    ap.add_argument(
        "--db",
        default="",
        help=f"写入目标库 (默认 {current_db_paths()[1]})。"
        "试点跑在生产副本上时用它, 生产写不传。",
    )
    args = ap.parse_args()

    if args.db:
        # 覆盖写目标 —— current_db_paths() 读的就是 services.db 上的这两个属性。
        import services.db as _db

        target = Path(args.db).resolve()
        _db.DB_PATH = target
        _db.DB_DIR = target.parent
        print(f"[backfill] 写目标被覆盖为 {target}")

    staging = Path(args.staging)
    if not staging.exists():
        print(f"staging 不存在: {staging}", file=sys.stderr)
        return 2

    staging_con, stocks = _install_staging_scraper(staging)
    if args.limit:
        stocks = stocks[: args.limit]
    print(
        f"[backfill] staging={staging.name} 股票={len(stocks):,} "
        f"start_period={args.start_period}"
    )

    conn = get_conn()
    t0 = time.time()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM canonical_top10_float_holders_period"
        ).fetchone()[0]
        result = sync_holders_aif10(
            conn,
            symbols=stocks,
            start_period=args.start_period,
            progress_every=args.progress_every,
            delete_scope="stocks_in_batch",
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM canonical_top10_float_holders_period"
        ).fetchone()[0]
        codes = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM canonical_top10_float_holders_period"
        ).fetchone()[0]
    finally:
        conn.close()
        staging_con.close()

    ok = int(result.get("ok") or 0)
    fail = int(result.get("fail") or 0)
    print(
        f"[backfill] ok={ok} fail={fail} "
        f"canonical {before:,} -> {after:,} (+{after - before:,}) "
        f"股票数={codes:,} 用时={time.time() - t0:.0f}s"
    )
    if result.get("errors"):
        print(f"[backfill] 前几条错误: {result['errors'][:5]}", file=sys.stderr)

    # 与 ingest_holders_aif10.py 同一判据: 全失败 / 成功但零写入都不当成功。
    if fail and not ok:
        print(f"[backfill] FAILED 全部 {fail} 只均失败", file=sys.stderr)
        return 1
    if ok and after == before:
        print("[backfill] FAILED 报告成功却零净写入", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
