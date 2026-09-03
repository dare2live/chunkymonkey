"""daily_update 管线编排器 — 获取→清洗→加工→存储 (2026-06-23 重设计入口)。

跑: PYTHONPATH=backend python -m services.pipeline.run [--dry] [--skip-sync]
(由 scripts/daily_update.sh 瘦 wrapper 调用, 后者只设 PATH + source .env。)

四阶段顺序固定 (依赖偏序): preflight gate → 获取(→L0) → 清洗(L0→L1) → 加工(L1→L2) → 存储(治理)。
授权失败在四阶段前硬阻断；普通步骤失败=degraded 续跑。
链尾 exit 派生自 typed run_outcome（报告 JSON 为真相源；exit 是 renderer）。
绝不静默吞错或把部分成功报告为 clean。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from services.data_sources.sources.tushare import TuShareAuthorizationError
from services.writer_lock import WriterLockBusyError, writer_lock

from .acquire import Tier0AcquireError, run_acquire
from .clean import run_clean
from .context import REPO, PipelineContext
from .preflight import PipelinePreflightError, run_preflight
from .process import run_process
from .run_outcome import (
    OUTCOME_HARD_FAIL,
    OUTCOME_INTEGRITY,
    OUTCOME_SOFT_WAITING,
    OUTCOME_SUCCESS,
    derive_run_outcome,
)
from .stage_status import run_and_record
from .store import run_store, write_report_and_alert


def _finalize_exit(
    ctx: PipelineContext,
    *,
    hard_exit_code: int | None = None,
    write_report: bool = True,
) -> int:
    """Derive exit from run_outcome; optionally persist report for early hard exits."""
    if write_report:
        info = write_report_and_alert(ctx, hard_exit_code=hard_exit_code)
        return int(info["run_outcome_exit_code"])
    derived = derive_run_outcome(ctx.degraded_msgs, hard_exit_code=hard_exit_code)
    return int(derived["exit_code"])


def _write_writer_block_report(run_date: str, exc: WriterLockBusyError) -> Path:
    """Minimal report when PipelineContext never starts (writer busy)."""
    reports = REPO / "data/reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"daily_{run_date}.json"
    info = derive_run_outcome(
        [f"WRITER BLOCK: {exc} (四阶段未启动; exit 2)"],
        hard_exit_code=2,
    )
    payload = {
        "date": run_date,
        "dry_run": 0,
        "scope": "data_foundation (L0/L1/L1k/snapshot)",
        "phase_status": {
            "preflight": "ERR",
            "post_acquire_sla": "ERR",
            "chain": "HARD_FAIL",
            "detail": "writer lock busy before pipeline context",
        },
        "degraded_total": 1,
        "degraded_msgs": [f"WRITER BLOCK: {exc} (四阶段未启动; exit 2)"],
        "run_outcome": info["run_outcome"],
        "run_outcome_label": info["run_outcome_label"],
        "run_outcome_reason": info["run_outcome_reason"],
        "run_outcome_exit_code": info["exit_code"],
        "run_outcome_classified": info["classified"],
        "alert_flags": {"sla_warn": False},
        "sla_warn": False,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return path


def _run_locked(
    args: argparse.Namespace,
    run_date: str,
    lease_id: str,
    lock_fd: int | None,
) -> int:
    """在 controller 持有唯一写窗口时执行完整管线。"""
    ctx = PipelineContext(
        dry=args.dry,
        skip_sync=args.skip_sync,
        date=run_date,
        writer_lease_id=lease_id,
        writer_lock_fd=lock_fd,
    )
    ctx.log(f"=== ChunkyMonkey daily update {run_date} ===")
    ctx.log(f"  dry={int(ctx.dry)} skip_sync={int(ctx.skip_sync)}")
    ctx.reset_degraded_flag()  # 链起跑清前次 flag

    try:
        try:
            run_preflight(ctx)                      # gate (授权 + SLA); 非4阶段之一不记状态
        except TuShareAuthorizationError as exc:
            # 2026-09-10 tushare 到期不续期整改: run_preflight 内部已把授权阻断吸收成
            # degraded 并继续 (SLA 检查/四阶段不因 tushare 熄火) —— 这里是防御性兜底,
            # 正常路径不应触发。仍降级而非硬 exit 3, 与 run_preflight/run_acquire 语义一致；
            # 消息不含 "AUTH BLOCK" 等 run_outcome._HARD_RE 关键字, 否则照样会被判 hard_fail。
            ctx.degraded(
                f"tushare authorization_blocked (preflight fallback): {exc.reason} "
                "(四阶段继续, 不熄火)"
            )
        except PipelinePreflightError as exc:
            ctx.degraded(f"PREFLIGHT BLOCK: {exc.reason} (四阶段未启动; exit 4)")
            rc = _finalize_exit(ctx, hard_exit_code=4)
            ctx.log(f"=== daily_update HARD_FAIL preflight (exit {rc}) ===")
            return rc
        try:
            run_and_record(ctx, "acquire", run_acquire)  # ① 获取 →L0  (+best-effort 记阶段状态)
        except TuShareAuthorizationError as exc:
            # 同上防御性兜底: run_acquire 内部(顶层探针 + drain 授权阻断)已吸收该异常并
            # 降级续跑非 tushare 域; 走到这里说明某处遗漏捕获, 仍不应熄火 clean/process/store。
            ctx.degraded(
                f"tushare authorization_blocked (acquire fallback): {exc.reason} "
                "(后续阶段继续, 不熄火)"
            )
        except Tier0AcquireError as exc:
            ctx.degraded(f"TIER0 BLOCK during acquire: {exc} (后续阶段未启动; exit 5)")
            rc = _finalize_exit(ctx, hard_exit_code=5)
            ctx.log(f"=== daily_update HARD_FAIL tier0 (exit {rc}) ===")
            return rc
        run_and_record(ctx, "clean", run_clean)      # ② 清洗 L0→L1
        run_and_record(ctx, "process", run_process)  # ③ 加工 L1→L2
        run_and_record(ctx, "store", run_store)      # ④ 存储/治理 (writes run_outcome)
        # Store already wrote the report; re-derive exit without rewriting.
        derived = derive_run_outcome(ctx.degraded_msgs)
        rc = int(derived["exit_code"])
        outcome = derived["run_outcome"]
        if outcome == OUTCOME_HARD_FAIL:
            ctx.log(f"=== daily_update HARD_FAIL ({len(ctx.degraded_msgs)} 项; exit {rc}) ===")
        elif outcome == OUTCOME_SOFT_WAITING:
            ctx.log(
                f"=== daily_update DONE soft_waiting_clock "
                f"({len(ctx.degraded_msgs)} 项; exit {rc}) ==="
            )
        elif outcome == OUTCOME_INTEGRITY:
            ctx.log(
                f"=== daily_update DONE integrity_observe "
                f"({len(ctx.degraded_msgs)} 项; exit {rc}) ==="
            )
        elif outcome == OUTCOME_SUCCESS:
            ctx.log(
                "=== daily_update DONE success "
                "(数据底座: preflight / 获取 / 清洗 / 加工 / 存储) ==="
            )
        else:
            # Fail closed on unknown renderer labels (must not invent soft_waiting).
            ctx.log(
                f"=== daily_update DONE {outcome} "
                f"({len(ctx.degraded_msgs)} 项; exit {rc}) ==="
            )
        return rc
    finally:
        ctx.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="daily_update 数据管线 (获取/清洗/加工/存储)")
    ap.add_argument("--dry", action="store_true", help="dry-run, 不写 DB")
    ap.add_argument("--skip-sync", action="store_true", help="跳采集 (用现有数据)")
    ap.add_argument("--date", default=None, help="YYYYMMDD (默认今天; 显式传防跨午夜错位)")
    args = ap.parse_args(argv)

    # run_date = 运行日期标签 (log/report 命名 + ingest snapshot 标记), 非 trade-date end-date;
    # 数据 end-date 全在被调脚本内自解析 (latest_completed_trade_date / calendar-gated)。
    run_date = args.date or datetime.now().strftime("%Y%m%d")  # Phase ψ.5 allowlist: run-date 标签非 end-date
    try:
        with writer_lock(owner="pipeline.run") as lease:
            return _run_locked(args, run_date, str(lease.lease_id), lease.lock_fd)
    except WriterLockBusyError as exc:
        # 此时 PipelineContext 尚未创建，不能清旧 alert flag/写一份看似启动过的链日志。
        report = _write_writer_block_report(run_date, exc)
        print(f"WRITER BLOCK: {exc} (四阶段未启动; exit 2)")
        print(f"run_outcome=hard_fail report={report}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
