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
import sys
from datetime import datetime

from services.data_sources.sources.tushare import TuShareAuthorizationError
from services.writer_lock import WriterLockBusyError, writer_lock

from .acquire import run_acquire
from .clean import run_clean
from .context import PipelineContext
from .process import run_process
from .preflight import PipelinePreflightError
from .stage_status import run_and_record
from .store import run_store

# 固定线性序 (blueprint §8.3: 不上通用 DAG; 仅独立阶段触发)
STAGES = {
    "acquire": run_acquire,   # ① 获取 →L0
    "clean": run_clean,       # ② 清洗 L0→L1
    "process": run_process,   # ③ 加工 L1→L2
    "store": run_store,       # ④ 存储/治理
}


def _upstream_refusal(stage: str) -> str | None:
    """件3: 上游阶段非 check_pass → 返 refusal 提示串; 否则 None (放行)。

    首阶段无上游=放行。状态读失败=放行 (best-effort 门, 非硬安全; 不因状态库不可达卡死运维)。
    """
    from .stage_status import STAGE_ORDER, upstream_ok, upstream_status
    if STAGE_ORDER.index(stage) == 0:
        return None
    try:
        from services.db_connection import get_conn
        conn = get_conn()
        try:
            ok = upstream_ok(conn, stage)
            up = upstream_status(conn, stage)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 状态读失败不阻断单跑 (best-effort 门)
        return None
    if ok:
        return None
    up_name = STAGE_ORDER[STAGE_ORDER.index(stage) - 1]
    up_status = up.get("status") if up else "?"
    return (
        f"REFUSE: 上游阶段 '{up_name}' 当前状态={up_status} (非 check_pass)。"
        f" 先跑上游 `chunkyctl pipeline {up_name}` 或加 --force 绕过。"
    )


def run_stage(
    stage: str,
    *,
    dry: bool = False,
    skip_sync: bool = False,
    date: str | None = None,
    force: bool = False,
) -> int:
    """单跑一个阶段。

    返回 0=干净; 1=本阶段 degraded; 2=上游未 pass; 3=授权阻断;
    4=writer busy; 5=日历地基阻断。

    不 reset DEGRADED_FLAG (那是 run.py 全链起跑的动作); 单阶段若 degraded 仍写 flag 告警 (诚实)。
    """
    if stage not in STAGES:
        raise SystemExit(f"unknown stage '{stage}' (choices: {', '.join(STAGES)})")
    # run-date = 运行日期标签 (log/report 命名), 非 trade end-date (同 run.py allowlist; end-date 各脚本内 calendar-gated)
    run_date = date or datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: run-date 标签非 end-date
    try:
        with writer_lock(owner=f"pipeline.stage.{stage}") as lease:
            # 件3: refusal 与阶段执行处于同一写窗口，避免检查后另一 writer 抢先改变状态。
            if not force:
                refusal = _upstream_refusal(stage)
                if refusal is not None:
                    print(refusal, file=sys.stderr)
                    return 2
            ctx = PipelineContext(
                dry=dry,
                skip_sync=skip_sync,
                date=run_date,
                writer_lease_id=str(lease.lease_id),
                writer_lock_fd=lease.lock_fd,
            )
            ctx.log(f"=== ChunkyMonkey pipeline stage '{stage}' {run_date} (独立触发) ===")
            ctx.log(f"  dry={int(ctx.dry)} skip_sync={int(ctx.skip_sync)} force={int(force)}")
            try:
                if stage == "acquire":
                    # 与全链 preflight 同一授权门；先于阶段状态记录，授权失败不伪装成已启动采集。
                    from .preflight import ensure_calendar_ready, ensure_tushare_authorized

                    ensure_calendar_ready(ctx, allow_repair=not dry and not skip_sync)
                    ensure_tushare_authorized(ctx)
                passed = run_and_record(ctx, stage, STAGES[stage])  # 件2: 跑 + best-effort 记状态
                if not passed or ctx.degraded_msgs:
                    ctx.log(f"=== stage '{stage}' DONE with degraded (见上; exit 1) ===")
                    return 1
                ctx.log(f"=== stage '{stage}' DONE (clean) ===")
                return 0
            except TuShareAuthorizationError as exc:
                ctx.degraded(f"AUTH BLOCK: {exc.reason} (stage 未启动; exit 3)")
                return 3
            except PipelinePreflightError as exc:
                ctx.degraded(f"PREFLIGHT BLOCK: {exc.reason} (stage 未启动; exit 5)")
                return 5
            finally:
                ctx.close()
    except WriterLockBusyError as exc:
        print(f"WRITER BLOCK: {exc} (stage 未启动; exit 4)", file=sys.stderr)
        return 4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="chunkyctl pipeline",
        description="单跑一个数据管线阶段 (独立触发; 全链编排用 scripts/daily_update.sh)",
    )
    ap.add_argument("stage", choices=list(STAGES), help="要单跑的阶段")
    ap.add_argument("--dry", action="store_true", help="dry-run, 不写 DB")
    ap.add_argument("--skip-sync", action="store_true", help="跳采集 (用现有数据)")
    ap.add_argument("--date", default=None, help="YYYYMMDD (默认今天; 显式传防跨午夜错位)")
    ap.add_argument("--force", action="store_true", help="绕过上游 check_pass 门 (运维明知上游状态时)")
    args = ap.parse_args(argv)
    return run_stage(args.stage, dry=args.dry, skip_sync=args.skip_sync, date=args.date, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
