#!/usr/bin/env python3
"""Phase 2-4 全量 orchestrator

等 price_kline_tdxhub 回填完成 (轮询 row 数稳定 > 3min), 然后串联
  1. build_feature_panel (全市场)
  2. train_multidim_model (Optuna 50 trials)
  3. run_daily_topk (最新日 top-100)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.market_db import get_market_conn

logger = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def wait_for_price_kline(min_codes: int = 5000, stable_seconds: int = 180) -> None:
    """轮询 price_kline_tdxhub, 等待 code 数 >= min_codes 且数据 stable (无增长) 至少 N 秒."""
    last_count = 0
    last_codes = 0
    stable_start = None
    logger.info("等待 price_kline_tdxhub code 数 >= %d 且 stable %d 秒", min_codes, stable_seconds)
    while True:
        conn = get_market_conn()
        row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM price_kline_tdxhub").fetchone()
        conn.close()
        count, codes = row[0], row[1]

        if codes >= min_codes and count == last_count:
            if stable_start is None:
                stable_start = time.time()
            elif time.time() - stable_start >= stable_seconds:
                logger.info("price_kline 稳定: rows=%d codes=%d, 进入下一步", count, codes)
                return
        else:
            stable_start = None
            logger.info("进度: rows=%d codes=%d (last %d rows, +%d)", count, codes, last_count, count - last_count)
            last_count = count
            last_codes = codes
        time.sleep(60)


def run_step(cmd: list[str], name: str):
    logger.info("=" * 60)
    logger.info("▶ %s:  %s", name, " ".join(cmd))
    logger.info("=" * 60)
    t0 = time.time()
    ret = subprocess.run(cmd, capture_output=False)
    dt = (time.time() - t0) / 60
    if ret.returncode != 0:
        logger.error("✗ %s FAIL (耗时 %.1f min)", name, dt)
        sys.exit(ret.returncode)
    logger.info("✓ %s 完成 (耗时 %.1f min)", name, dt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-wait', action='store_true')
    parser.add_argument('--min-codes', type=int, default=5000)
    parser.add_argument('--feature-start', default='2023-01-01')
    parser.add_argument('--trials', type=int, default=50)
    parser.add_argument('--top-k', type=int, default=100)
    args = parser.parse_args()

    if not args.skip_wait:
        wait_for_price_kline(min_codes=args.min_codes, stable_seconds=180)

    # Step 1: feature panel
    run_step(
        ["python3", "-m", "backend.scripts.build_feature_panel",
         "--start", args.feature_start],
        "feature_panel",
    )

    # Step 2: train + Optuna
    run_step(
        ["python3", "-m", "backend.scripts.train_multidim_model",
         "--start", args.feature_start,
         "--trials", str(args.trials),
         "--regime-aware"],
        "train_multidim",
    )

    # Step 3: daily topK
    run_step(
        ["python3", "-m", "backend.scripts.run_daily_topk",
         "--top-k", str(args.top_k),
         "--by-regime"],
        "run_daily_topk",
    )

    logger.info("━" * 60)
    logger.info("全流程完成")
    logger.info("━" * 60)


if __name__ == "__main__":
    main()
