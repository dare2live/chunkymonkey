#!/usr/bin/env python3
"""Phase 2-4 全量 orchestrator

等 price_kline_tdxhub 回填完成 (轮询 row 数稳定 > 3min), 然后串联
  1. build_feature_panel_duck (全市场)
  2. train_multidim_model (默认 compact base_dense_v2, Optuna 50 trials)
  3. run_daily_topk (最新日 top-100)
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.market_db import get_market_conn
from services.db import get_conn

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


def run_step(cmd: list[str], name: str, *, dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("▶ %s:  %s", name, " ".join(cmd))
    logger.info("=" * 60)
    if dry_run:
        logger.info("dry-run: 跳过执行 %s", name)
        return
    t0 = time.time()
    ret = subprocess.run(cmd, capture_output=False)
    dt = (time.time() - t0) / 60
    if ret.returncode != 0:
        logger.error("✗ %s FAIL (耗时 %.1f min)", name, dt)
        sys.exit(ret.returncode)
    logger.info("✓ %s 完成 (耗时 %.1f min)", name, dt)


def dry_run_checks(min_codes: int) -> None:
    logger.info("dry-run: 检查输入表、模块和输出目录")
    mkt = get_market_conn()
    try:
        rows, codes = mkt.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code) FROM price_kline_tdxhub WHERE freq='daily' AND adjust='qfq'"
        ).fetchone()
        if codes < min_codes:
            raise RuntimeError(f"price_kline_tdxhub code 覆盖不足: {codes} < {min_codes}")
        logger.info("market.price_kline_tdxhub rows=%d codes=%d", rows, codes)
    finally:
        mkt.close()

    conn = get_conn()
    try:
        for table in ("fact_feature_panel", "mart_multidim_model"):
            exists = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                (table,),
            ).fetchone()[0]
            logger.info("smart.%s exists=%s", table, bool(exists))
    finally:
        conn.close()

    model_dir = Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    logger.info("model_dir 可写: %s", model_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-wait', action='store_true')
    parser.add_argument('--min-codes', type=int, default=5000)
    parser.add_argument('--feature-start', default='2023-01-01')
    parser.add_argument('--trials', type=int, default=50)
    parser.add_argument('--top-k', type=int, default=100)
    parser.add_argument('--feature-group', default='base_dense_v2',
                        choices=['base', 'base_dense_v2', 'base_alpha158', 'base_dense_v2_alpha158', 'tdx_keep_v1', 'legacy_full'])
    parser.add_argument('--dry-run', action='store_true',
                        help='只检查依赖和将执行的命令, 不写库、不训练')
    args = parser.parse_args()

    if args.dry_run:
        dry_run_checks(args.min_codes)

    if not args.skip_wait and not args.dry_run:
        wait_for_price_kline(min_codes=args.min_codes, stable_seconds=180)

    # Step 1: feature panel
    run_step(
        [sys.executable, "-m", "backend.scripts.build_feature_panel_duck",
         "--start", args.feature_start],
        "feature_panel",
        dry_run=args.dry_run,
    )

    # Step 2: train + Optuna
    run_step(
        [sys.executable, "-m", "backend.scripts.train_multidim_model",
         "--start", args.feature_start,
         "--feature-group", args.feature_group,
         "--trials", str(args.trials),
         "--regime-aware"],
        "train_multidim",
        dry_run=args.dry_run,
    )

    # Step 3: daily topK
    run_step(
        [sys.executable, "-m", "backend.scripts.run_daily_topk",
         "--top-k", str(args.top_k),
         "--by-regime"],
        "run_daily_topk",
        dry_run=args.dry_run,
    )

    logger.info("━" * 60)
    logger.info("全流程完成")
    logger.info("━" * 60)


if __name__ == "__main__":
    main()
