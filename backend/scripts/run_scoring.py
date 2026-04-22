#!/usr/bin/env python3
"""独立补跑机构评分 / 股票评分。

用途：当 updater DAG 因中断、依赖跳过等原因没跑到 calc_inst_scores / calc_stock_scores
时，手动补跑，避免整条 DAG 重跑。

用法：
  python -m backend.scripts.run_scoring --both         # 默认，两者都跑
  python -m backend.scripts.run_scoring --inst         # 只补机构评分
  python -m backend.scripts.run_scoring --stock        # 只补股票评分
  python -m backend.scripts.run_scoring --dry-run      # 只打印前后对账，不 commit（函数内部仍会 commit，慎用）
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.scoring import calculate_institution_scores, calculate_stock_scores

logger = logging.getLogger("run_scoring")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def _snapshot_inst(conn):
    row = conn.execute(
        "SELECT COUNT(*) n, COUNT(quality_score) q, COUNT(followability_score) f, "
        "COUNT(score_basis) b FROM mart_institution_profile"
    ).fetchone()
    return {"total": row["n"], "quality_nn": row["q"], "follow_nn": row["f"], "basis_nn": row["b"]}


def _snapshot_stock(conn):
    row = conn.execute(
        "SELECT COUNT(*) n, COUNT(composite_priority_score) c, "
        "SUM(CASE WHEN stock_gate='follow' THEN 1 ELSE 0 END) g_follow, "
        "SUM(CASE WHEN stock_gate='watch'  THEN 1 ELSE 0 END) g_watch,  "
        "SUM(CASE WHEN stock_gate='avoid'  THEN 1 ELSE 0 END) g_avoid "
        "FROM mart_stock_trend"
    ).fetchone()
    return {
        "total": row["n"],
        "composite_nn": row["c"],
        "gate_follow": row["g_follow"],
        "gate_watch": row["g_watch"],
        "gate_avoid": row["g_avoid"],
    }


def main():
    parser = argparse.ArgumentParser(description="独立补跑评分")
    parser.add_argument("--inst", action="store_true", help="只补机构评分")
    parser.add_argument("--stock", action="store_true", help="只补股票评分")
    parser.add_argument("--both", action="store_true", help="机构+股票都跑（默认）")
    args = parser.parse_args()

    run_inst = args.inst or args.both or (not args.inst and not args.stock)
    run_stock = args.stock or args.both or (not args.inst and not args.stock)

    conn = get_conn()
    try:
        if run_inst:
            before = _snapshot_inst(conn)
            logger.info("[机构评分] BEFORE: %s", before)
            n = calculate_institution_scores(conn)
            after = _snapshot_inst(conn)
            logger.info("[机构评分] 评分机构数=%d", n)
            logger.info("[机构评分] AFTER : %s", after)

        if run_stock:
            before = _snapshot_stock(conn)
            logger.info("[股票评分] BEFORE: %s", before)
            n = calculate_stock_scores(conn)
            after = _snapshot_stock(conn)
            logger.info("[股票评分] 评分股票数=%d", n)
            logger.info("[股票评分] AFTER : %s", after)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
