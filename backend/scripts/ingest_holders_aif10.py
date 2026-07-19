"""十大流通股东 aif10 ingest — 薄 CLI (手动 backfill / 调试).

核心逻辑在 services.holders_aif10 (获取/清洗/加工/存储 分层)。
日常采集由 pipeline acquire stage 的 _sync_holders_aif10 step 驱动 (非本脚本)。

用法:
    python backend/scripts/ingest_holders_aif10.py --symbols 600388,000001        # 指定股
    python backend/scripts/ingest_holders_aif10.py --backfill                       # 全市场 (K线范围 20181231+)
    python backend/scripts/ingest_holders_aif10.py --start-period 20181231 --limit 50
    python backend/scripts/ingest_holders_aif10.py --accept-legacy-partition 20260717  # E0 canary
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.holders_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
    accept_holders_top10_partition_from_legacy,
    sync_holders_aif10,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔股票代码 (调试); 空=全universe")
    ap.add_argument("--backfill", action="store_true", help="全市场 (K线范围)")
    ap.add_argument("--start-period", default=DEFAULT_START_PERIOD, help="最早报告期 (默认对齐K线 20181231)")
    ap.add_argument("--limit", type=int, default=0, help="限股票数 (调试)")
    ap.add_argument(
        "--accept-legacy-partition",
        default="",
        help="E0 canary: formal land→accept one notice_date from legacy (YYYYMMDD)",
    )
    ap.add_argument(
        "--rewrite-legacy",
        action="store_true",
        help="With --accept-legacy-partition, also rewrite legacy (stock-wide DELETE)",
    )
    args = ap.parse_args()

    conn = get_conn()
    try:
        if args.accept_legacy_partition:
            outcome = accept_holders_top10_partition_from_legacy(
                conn,
                args.accept_legacy_partition,
                rewrite_legacy=bool(args.rewrite_legacy),
            )
            print(
                "[aif10-holders] ACCEPT_LEGACY "
                f"status={outcome.status} partitions={outcome.partitions} "
                f"canonical_rows={outcome.canonical_rows} "
                f"batch_ids={outcome.batch_ids}"
            )
            return 0

        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
        if symbols is None and not args.backfill and not args.limit:
            print("需 --symbols / --backfill / --limit / --accept-legacy-partition 之一", file=sys.stderr)
            return 2

        result = sync_holders_aif10(
            conn, symbols=symbols, start_period=args.start_period, limit=args.limit,
        )
    finally:
        conn.close()
    print(f"[aif10-holders] DONE {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
