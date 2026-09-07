"""十大流通股东 aif10 ingest — 薄 CLI (手动 backfill / 调试).

核心逻辑在 services.holders_aif10 (获取/清洗/加工/存储 分层)。
日常采集由 pipeline acquire 的 by_notice 增量驱动（非本脚本、非逐码全史）。
本 CLI 是显式 repair/backfill 刀：`--symbols` / `--backfill` 才允许 per-stock 全期。

用法:
    python backend/scripts/ingest_holders_aif10.py --symbols 600388,000001        # 指定股
    python backend/scripts/ingest_holders_aif10.py --backfill                       # 全市场 (K线范围 20181231+)
    python backend/scripts/ingest_holders_aif10.py --start-period 20181231 --limit 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.holders_aif10 import (  # noqa: E402
    DEFAULT_START_PERIOD,
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
        help="RETIRED: fact plane dropped 2026-07-26; flag kept only to fail closed",
    )
    args = ap.parse_args()

    if args.accept_legacy_partition:
        print(
            "holders_compat_retired: --accept-legacy-partition forbidden after "
            "fact_top10_holder_period DROP; use provider sync / forward land",
            file=sys.stderr,
        )
        return 2

    conn = get_conn()
    try:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
        if symbols is None and not args.backfill and not args.limit:
            print("需 --symbols / --backfill / --limit 之一", file=sys.stderr)
            return 2

        result = sync_holders_aif10(
            conn, symbols=symbols, start_period=args.start_period, limit=args.limit,
        )
    finally:
        conn.close()

    ok = int(result.get("ok") or 0)
    fail = int(result.get("fail") or 0)
    rows = int(result.get("rows_written") or 0)

    # 2026-09-07: 此前无论结果如何都 print DONE 并 return 0。
    # sync_holders_aif10 把异常逐股 catch 进 result["errors"](上限 20 条) 后正常返回,
    # 于是「5,447 只股全失败、写了 0 行」与「全部成功」在退出码上完全一样 ——
    # 实测撞到过: delete_scope 未定义导致每只股 NameError, 函数照常返回、CLI 照常 DONE。
    # 判据放在这里而不是函数里: 函数的逐股容错本身是对的(单只股失败不该中断全场),
    # 错的是**没有任何一层把「一只都没成」翻译成失败**。
    if fail and not ok:
        print(
            f"[aif10-holders] FAILED 全部 {fail} 只股均失败, 写入 0 行; "
            f"前几条错误: {result.get('errors', [])[:5]}",
            file=sys.stderr,
        )
        return 1
    if ok and not rows:
        print(
            f"[aif10-holders] FAILED {ok} 只股报告成功却写入 0 行 —— "
            "成功计数与落库量脱节, 不当成功处理",
            file=sys.stderr,
        )
        return 1
    print(f"[aif10-holders] DONE {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
