"""Phase ε+ — 构建 blended (反馈环融合后) daily-topk。

读 mart_daily_recommendation 最新一日, 用 fact_technical_trigger + mart_formula_weight_history
混合算出 blended_score 重排, 写 mart_daily_blended_recommendation。

用法:
  PYTHONPATH=backend python backend/scripts/build_blended_recommendation_daily.py [--date 2026-05-06]
"""
from __future__ import annotations

import argparse
import logging

from services.db import get_conn
from services.selection.blended_recommendation import build_blended_for_date


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_blended_recommendation_daily")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="snapshot_date, 默认最新")
    parser.add_argument("--top-k", type=int, default=100)
    args = parser.parse_args()

    conn = get_conn()
    try:
        if args.date:
            target = args.date
        else:
            r = conn.execute("SELECT MAX(snapshot_date) FROM mart_daily_recommendation").fetchone()
            target = r[0] if r and r[0] else None
            if not target:
                log.error("无 mart_daily_recommendation 数据")
                return
        log.info(f"build blended for {target}")
        n = build_blended_for_date(conn, target, top_k=args.top_k)
        log.info(f"完成: {n} 行")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
