"""命令行入口: 全量重建 fact_holder_event.

使用:
    # 全量重建 (free + all)
    python backend/scripts/rebuild_holder_events.py

    # 仅重建 free
    python backend/scripts/rebuild_holder_events.py --holder-set free

何时跑:
    1. 每次 ingest_holders_tdxhub.py 结束后 (上游 fact_top10_holder_period 有新数据).
    2. 解析器/派生 SQL 改动后 (CLAUDE.md 派生层重算原则).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from services.db import get_conn  # noqa: E402
from services.holders_event import rebuild_holder_events  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--holder-set", choices=["free", "all"], default=None,
                   help="仅重建指定 set; 默认全部")
    args = p.parse_args()

    conn = get_conn()
    result = rebuild_holder_events(conn, holder_set=args.holder_set)
    conn.close()

    print("\n=== fact_holder_event rebuild ===")
    for k, v in sorted(result.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
