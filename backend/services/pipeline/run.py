"""daily_update 管线编排器 — 获取→清洗→加工→存储 (2026-06-23 重设计入口)。

跑: PYTHONPATH=backend python -m services.pipeline.run [--dry] [--skip-sync]
(由 scripts/daily_update.sh 瘦 wrapper 调用, 后者只设 PATH + source .env。)

四阶段顺序固定 (依赖偏序): preflight gate → 获取(→L0) → 清洗(L0→L1) → 加工(L1→L2) → 存储(治理)。
每步失败=degraded 续跑 (宪法第5条), 链尾汇总+告警送达。绝不静默吞错。
"""
from __future__ import annotations

import argparse
from datetime import datetime

from .acquire import run_acquire
from .clean import run_clean
from .context import PipelineContext
from .preflight import run_preflight
from .process import run_process
from .stage_status import run_and_record
from .store import run_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="daily_update 数据管线 (获取/清洗/加工/存储)")
    ap.add_argument("--dry", action="store_true", help="dry-run, 不写 DB")
    ap.add_argument("--skip-sync", action="store_true", help="跳采集 (用现有数据)")
    ap.add_argument("--date", default=None, help="YYYYMMDD (默认今天; 显式传防跨午夜错位)")
    args = ap.parse_args(argv)

    # run_date = 运行日期标签 (log/report 命名 + ingest snapshot 标记), 非 trade-date end-date;
    # 数据 end-date 全在被调脚本内自解析 (latest_completed_trade_date / calendar-gated)。
    run_date = args.date or datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: run-date 标签非 end-date
    ctx = PipelineContext(dry=args.dry, skip_sync=args.skip_sync, date=run_date)
    ctx.log(f"=== ChunkyMonkey daily update {run_date} ===")
    ctx.log(f"  dry={int(ctx.dry)} skip_sync={int(ctx.skip_sync)}")
    ctx.reset_degraded_flag()  # 链起跑清前次 flag

    try:
        run_preflight(ctx)                          # gate (job 契约 + SLA); 非4阶段之一不记状态
        run_and_record(ctx, "acquire", run_acquire)  # ① 获取 →L0  (+best-effort 记阶段状态)
        run_and_record(ctx, "clean", run_clean)      # ② 清洗 L0→L1
        run_and_record(ctx, "process", run_process)  # ③ 加工 L1→L2
        run_and_record(ctx, "store", run_store)      # ④ 存储/治理
        ctx.log("=== daily_update DONE (数据底座: preflight / 获取 / 清洗 / 加工 / 存储) ===")
        return 0
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
