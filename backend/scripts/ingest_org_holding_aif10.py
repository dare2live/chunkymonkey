"""机构持仓明细 aif10 ingest — 薄 CLI (手动 backfill / 调试 / E0 canary).

核心逻辑在 services.org_holding_aif10 (获取/清洗/存储 分层)。
日常采集由 pipeline acquire stage 的 _sync_org_holding step 驱动 (非本脚本)。
源决策: 用户 2026-06-24 拍板 aif10 例外扩展 (退役 tdx F10 fact_common_major_holder_stock)。

用法:
    python backend/scripts/ingest_org_holding_aif10.py --backfill                  # 全市场 K线范围 2018Q4+
    python backend/scripts/ingest_org_holding_aif10.py --start-period 2024-03-31   # 指定起始报告期
    python backend/scripts/ingest_org_holding_aif10.py --period 2026-03-31         # 单报告期
    python backend/scripts/ingest_org_holding_aif10.py --accept-legacy-partition 20190430
    python backend/scripts/ingest_org_holding_aif10.py --accept-legacy-partition 20190430 --stock-codes 600519,000001
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.org_holding_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
    accept_org_holding_partition_from_legacy,
    backfill,
    sync_period,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="全市场 K线范围 (2018Q4+)")
    ap.add_argument("--start-period", default=DEFAULT_START_PERIOD, help="最早报告期 (默认对齐K线 2018-12-31)")
    ap.add_argument("--end-period", default=None, help="最晚报告期 (默认 latest_plannable)")
    ap.add_argument("--period", default="", help="单报告期 (调试, 如 2026-03-31)")
    ap.add_argument(
        "--accept-legacy-partition",
        default="",
        help="E0 canary: formal land→accept one available_date from legacy (YYYYMMDD)",
    )
    ap.add_argument(
        "--stock-codes",
        default="",
        help="Optional canary subset: comma-separated stock_code filter",
    )
    ap.add_argument(
        "--rewrite-legacy",
        action="store_true",
        help="With --accept-legacy-partition, also rewrite legacy upsert",
    )
    args = ap.parse_args()

    if (
        not args.backfill
        and not args.period
        and not args.accept_legacy_partition
    ):
        print(
            "需 --backfill / --period / --accept-legacy-partition 之一",
            file=sys.stderr,
        )
        return 2

    conn = get_conn()
    try:
        if args.accept_legacy_partition:
            codes = [
                s.strip() for s in args.stock_codes.split(",") if s.strip()
            ] or None
            outcome = accept_org_holding_partition_from_legacy(
                conn,
                args.accept_legacy_partition,
                rewrite_legacy=bool(args.rewrite_legacy),
                stock_codes=codes,
            )
            print(
                "[org-holding-aif10] ACCEPT_LEGACY "
                f"status={outcome.status} partitions={outcome.partitions} "
                f"canonical_rows={outcome.canonical_rows} "
                f"batch_ids={outcome.batch_ids}"
            )
            return 0

        if args.period:
            result = sync_period(conn, args.period)
        else:
            result = backfill(
                conn, start_period=args.start_period, end_period=args.end_period
            )
    finally:
        conn.close()
    print(f"[org-holding-aif10] DONE {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
