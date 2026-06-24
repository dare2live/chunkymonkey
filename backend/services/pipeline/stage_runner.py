"""单阶段独立触发 (blueprint §8.1 阶段独立化门控 — 切片 a: 独立触发)。

daily_update (services.pipeline.run) 是便捷全链编排 (preflight→acquire→clean→process→store);
本模块给运维**单跑一个阶段** (如 acquire 失败修完源后只重跑 clean), 复用同一 PipelineContext + 阶段函数,
不连带全链。daily_update 退化为"按序链跑全部"的便捷入口, 非唯一入口。

用法 (经 scripts/chunkyctl pipeline 包装):
  scripts/chunkyctl pipeline acquire|clean|process|store [--dry] [--skip-sync] [--date YYYYMMDD]

边界 (奥卡姆, blueprint §8.3 只阶段状态机不上通用 DAG): 本切片只做**独立触发** (单跑某阶段)。
上游 ready 门控 (refuse-if-upstream-not-pass) + pipeline_stage_status 状态机 + 前端卡片 = §8 后续切片, 不在此。
"""
from __future__ import annotations

import argparse
from datetime import datetime

from .acquire import run_acquire
from .clean import run_clean
from .context import PipelineContext
from .process import run_process
from .store import run_store

# 固定线性序 (blueprint §8.3: 不上通用 DAG; 仅独立阶段触发)
STAGES = {
    "acquire": run_acquire,   # ① 获取 →L0
    "clean": run_clean,       # ② 清洗 L0→L1
    "process": run_process,   # ③ 加工 L1→L2
    "store": run_store,       # ④ 存储/治理
}


def run_stage(
    stage: str,
    *,
    dry: bool = False,
    skip_sync: bool = False,
    date: str | None = None,
) -> int:
    """单跑一个阶段。返回 0=干净; 1=本阶段有 degraded (运维需看)。

    不 reset DEGRADED_FLAG (那是 run.py 全链起跑的动作); 单阶段若 degraded 仍写 flag 告警 (诚实)。
    """
    if stage not in STAGES:
        raise SystemExit(f"unknown stage '{stage}' (choices: {', '.join(STAGES)})")
    # run-date = 运行日期标签 (log/report 命名), 非 trade end-date (同 run.py allowlist; end-date 各脚本内 calendar-gated)
    run_date = date or datetime.now().strftime("%Y%m%d")
    ctx = PipelineContext(dry=dry, skip_sync=skip_sync, date=run_date)
    ctx.log(f"=== ChunkyMonkey pipeline stage '{stage}' {run_date} (独立触发) ===")
    ctx.log(f"  dry={int(ctx.dry)} skip_sync={int(ctx.skip_sync)}")
    try:
        STAGES[stage](ctx)
        n_degraded = len(ctx.degraded_msgs)
        if n_degraded:
            ctx.log(f"=== stage '{stage}' DONE with {n_degraded} degraded (见上; exit 1) ===")
            return 1
        ctx.log(f"=== stage '{stage}' DONE (clean) ===")
        return 0
    finally:
        ctx.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="chunkyctl pipeline",
        description="单跑一个数据管线阶段 (独立触发; 全链编排用 scripts/daily_update.sh)",
    )
    ap.add_argument("stage", choices=list(STAGES), help="要单跑的阶段")
    ap.add_argument("--dry", action="store_true", help="dry-run, 不写 DB")
    ap.add_argument("--skip-sync", action="store_true", help="跳采集 (用现有数据)")
    ap.add_argument("--date", default=None, help="YYYYMMDD (默认今天; 显式传防跨午夜错位)")
    args = ap.parse_args(argv)
    return run_stage(args.stage, dry=args.dry, skip_sync=args.skip_sync, date=args.date)


if __name__ == "__main__":
    raise SystemExit(main())
