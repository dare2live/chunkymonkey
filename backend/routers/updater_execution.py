"""Shared execution helpers for updater route orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from routers.updater_plan import (
    selected_dependency_ids,
    selected_step_specs,
    skipped_step_ids_outside,
    step_ids_for,
    step_name_for,
)

from services.gap_queue import load_tracked_stock_names, mark_current_missing_as


KLINE_UNAVAILABLE_ERROR = "K线源不可用"
HARD_DEPENDENCY_ERROR = "硬依赖步骤未完成"
STOP_REQUESTED_ERROR = "用户已停止"


@dataclass
class StepRunProgress:
    """Track step outcomes for one updater route run."""

    completed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    stopped: set[str] = field(default_factory=set)

    def remaining(self, step_ids: Iterable[str], *, selected: set[str] | None = None) -> list[str]:
        return remaining_step_ids(
            step_ids,
            completed=self.completed,
            failed=self.failed,
            skipped=self.skipped,
            stopped=self.stopped,
            selected=selected,
        )

    def counts(self) -> dict[str, int]:
        return {
            "completed": len(self.completed),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
            "stopped": len(self.stopped),
        }


async def run_step_with_managed_connection(
    runner: Callable,
    *,
    get_conn: Callable,
    conn_timeout: int = 120,
    budget: float | int | None = None,
):
    """Run one step with its own DB connection and always close it."""

    step_conn = get_conn(timeout=conn_timeout)
    try:
        result = runner(step_conn)
        return await asyncio.wait_for(result, timeout=budget) if budget else await result
    finally:
        step_conn.close()


async def kline_connectivity_for_steps(
    step_ids: Iterable[str],
    *,
    check_connectivity: Callable,
) -> tuple[dict, bool]:
    """Return K-line source status only when the selected plan needs it."""

    if "sync_market_data" not in step_ids:
        return {}, True
    conn_status = await check_connectivity()
    return conn_status, bool(conn_status.get("kline_source", False))


def apply_step_result(
    conn,
    step_id: str,
    *,
    status: str,
    count: int,
    error_text,
    progress: StepRunProgress,
    update_step: Callable,
) -> str:
    """Persist a completed runner result and update progress bookkeeping."""

    finished_at = datetime.now().isoformat()
    update_kwargs = {"status": status, "finished_at": finished_at, "records": count}
    if error_text is not None:
        update_kwargs["error"] = error_text
    update_step(conn, step_id, **update_kwargs)
    if status == "skipped":
        progress.skipped.add(step_id)
        return "skipped"
    if status in {"failed", "blocked"}:
        progress.failed.add(step_id)
        return "failed"
    progress.completed.add(step_id)
    return "completed"


def mark_remaining_stopped(
    conn,
    step_ids: Iterable[str],
    *,
    progress: StepRunProgress,
    update_steps: Callable,
    selected: set[str] | None = None,
) -> list[str]:
    """Persist remaining runnable steps as stopped and update progress."""

    remaining = progress.remaining(step_ids, selected=selected)
    update_steps(conn, remaining, "stopped", STOP_REQUESTED_ERROR)
    progress.stopped.update(remaining)
    return remaining


def mark_step_running(
    conn,
    step_id: str,
    *,
    update_step: Callable,
    touch_heartbeat: Callable | None = None,
    reset_finished: bool = False,
    reset_error: bool = False,
) -> None:
    """Persist a step running transition and optionally update run heartbeat."""

    update = {"status": "running", "started_at": datetime.now().isoformat()}
    if reset_finished:
        update["finished_at"] = None
    if reset_error:
        update["error"] = None
    update_step(conn, step_id, **update)
    if touch_heartbeat is not None:
        touch_heartbeat(step_id)


def mark_step_stopped(
    conn,
    step_id: str,
    error_text,
    *,
    progress: StepRunProgress,
    update_step: Callable,
) -> None:
    """Persist a stopped step transition and update progress."""

    update_step(
        conn,
        step_id,
        status="stopped",
        finished_at=datetime.now().isoformat(),
        error=str(error_text)[:200],
    )
    progress.stopped.add(step_id)


def mark_step_failed(
    conn,
    step_id: str,
    error_text,
    *,
    progress: StepRunProgress,
    update_step: Callable,
) -> None:
    """Persist a failed step transition and update progress."""

    update_step(
        conn,
        step_id,
        status="failed",
        finished_at=datetime.now().isoformat(),
        error=str(error_text)[:200],
    )
    progress.failed.add(step_id)


def is_hard_dependency_blocked(
    hard_deps: Iterable[str],
    *,
    failed: set[str],
    skipped: set[str],
    stopped: set[str],
    selected: set[str],
) -> bool:
    """Return whether any selected hard dependency blocks the current step."""

    return any(
        dep in failed or dep in stopped or (dep in skipped and dep in selected)
        for dep in hard_deps
    )


def skip_if_hard_dependency_blocked(
    conn,
    step_id: str,
    hard_deps: Iterable[str],
    *,
    progress: StepRunProgress,
    selected: set[str],
    update_step: Callable,
    include_finished_at: bool = False,
) -> bool:
    """Persist a skipped hard-dependency block when one exists."""

    if not is_hard_dependency_blocked(
        hard_deps,
        failed=progress.failed,
        skipped=progress.skipped,
        stopped=progress.stopped,
        selected=selected,
    ):
        return False

    update = {"status": "skipped", "error": HARD_DEPENDENCY_ERROR}
    if include_finished_at:
        update["finished_at"] = datetime.now().isoformat()
    update_step(conn, step_id, **update)
    progress.skipped.add(step_id)
    return True


def remaining_step_ids(
    step_ids: Iterable[str],
    *,
    completed: set[str],
    failed: set[str],
    skipped: set[str],
    stopped: set[str] | None = None,
    selected: set[str] | None = None,
) -> list[str]:
    stopped = stopped or set()
    done = completed | failed | skipped | stopped
    return [
        step_id
        for step_id in step_ids
        if step_id not in done and (selected is None or step_id in selected)
    ]


def mark_kline_gap_queue_blocked(conn, conn_status: dict) -> None:
    stock_names = load_tracked_stock_names(conn)
    last_error = conn_status.get("message", "")
    mark_current_missing_as(
        conn,
        "daily_kline",
        status="blocked",
        reason="K线源不可用，当前未执行同步",
        last_error=last_error,
        stock_names=stock_names,
        commit=False,
    )
    mark_current_missing_as(
        conn,
        "monthly_kline",
        status="blocked",
        reason="K线源不可用，当前未执行同步",
        last_error=last_error,
        stock_names=stock_names,
        commit=True,
    )


def kline_unavailable_step_update(*, include_timestamps: bool = False) -> dict:
    update = {"status": "skipped", "error": KLINE_UNAVAILABLE_ERROR}
    if include_timestamps:
        now = datetime.now().isoformat()
        update["started_at"] = now
        update["finished_at"] = now
    return update


def skip_if_kline_unavailable(
    conn,
    step_id: str,
    *,
    kline_available: bool,
    conn_status: dict,
    progress: StepRunProgress,
    update_step: Callable,
    include_timestamps: bool = False,
) -> bool:
    """Persist K-line-unavailable skip bookkeeping for the market data step."""

    if step_id != "sync_market_data" or kline_available:
        return False

    mark_kline_gap_queue_blocked(conn, conn_status)
    update_step(
        conn,
        step_id,
        **kline_unavailable_step_update(include_timestamps=include_timestamps),
    )
    progress.skipped.add(step_id)
    return True


async def run_group_steps(
    conn,
    *,
    run_name: str,
    steps_in_group,
    step_ids: Iterable[str],
    hard_deps,
    runners,
    stopped_exception_type,
    should_stop: Callable[[], bool],
    get_conn: Callable,
    check_connectivity: Callable,
    update_step: Callable,
    update_steps: Callable,
    resolve_step_result: Callable,
    format_step_result_for_log: Callable,
    calibrate_data_completeness: Callable,
    logger,
) -> dict[str, int]:
    """Execute one updater group while preserving route-level bookkeeping."""

    step_ids = list(step_ids)
    selected = set(step_ids)

    conn_status, kline_available = await kline_connectivity_for_steps(
        step_ids,
        check_connectivity=check_connectivity,
    )

    progress = StepRunProgress()

    for step in steps_in_group:
        sid = step["id"]
        if should_stop():
            logger.info(f"[{run_name}] 用户停止")
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            break

        hard = selected_dependency_ids(hard_deps.get(sid, []), selected)

        if skip_if_hard_dependency_blocked(
            conn,
            sid,
            hard,
            progress=progress,
            selected=selected,
            update_step=update_step,
            include_finished_at=True,
        ):
            continue

        if skip_if_kline_unavailable(
            conn,
            sid,
            kline_available=kline_available,
            conn_status=conn_status,
            progress=progress,
            update_step=update_step,
        ):
            continue

        mark_step_running(conn, sid, update_step=update_step)
        logger.info(f"[{run_name}] 开始: {step['name']}")

        try:
            runner = runners[sid]
            result = await run_step_with_managed_connection(runner, get_conn=get_conn)

            status, count, error_text = resolve_step_result(result)
            outcome_state = apply_step_result(
                conn,
                sid,
                status=status,
                count=count,
                error_text=error_text,
                progress=progress,
                update_step=update_step,
            )
            outcome = format_step_result_for_log(status, count, error_text)

            if outcome_state == "skipped":
                logger.info(f"[{run_name}] 已最新: {step['name']} ({outcome})")
                continue
            if outcome_state == "failed":
                logger.error(f"[{run_name}] 失败: {step['name']}: {outcome}")
                continue

            calibrate_data_completeness(conn, sid)

            logger.info(f"[{run_name}] 完成: {step['name']} ({outcome})")
        except stopped_exception_type as exc:
            mark_step_stopped(conn, sid, exc, progress=progress, update_step=update_step)
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            break
        except Exception as exc:
            mark_step_failed(conn, sid, exc, progress=progress, update_step=update_step)
            logger.error(f"[{run_name}] 失败: {step['name']}: {exc}")

    return progress.counts()


async def run_all_steps(
    conn,
    *,
    steps,
    step_ids: Iterable[str],
    hard_deps,
    runners,
    stopped_exception_type,
    should_stop: Callable[[], bool],
    get_conn: Callable,
    check_connectivity: Callable,
    update_step: Callable,
    update_steps: Callable,
    record_step_source_state: Callable,
    resolve_step_result: Callable,
    format_step_result_for_log: Callable,
    calibrate_data_completeness: Callable,
    touch_heartbeat: Callable,
    mark_stale_running_steps_failed: Callable,
    prime_step_status_rows: Callable,
    logger,
) -> dict[str, int]:
    """Execute the full updater DAG while preserving status bookkeeping."""

    step_ids = list(step_ids)
    mark_stale_running_steps_failed(conn)
    prime_step_status_rows(conn, step_ids, inactive_mode="idle")

    progress = StepRunProgress()

    conn_status, kline_available = await kline_connectivity_for_steps(
        step_ids,
        check_connectivity=check_connectivity,
    )
    if not kline_available:
        logger.warning(f"[更新] K线源不可用 — {conn_status.get('message', '')}")

    selected = set(step_ids)
    for step in steps:
        if should_stop():
            logger.info("[更新] 用户停止")
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            break

        sid = step["id"]
        hard = hard_deps.get(sid, [])

        if skip_if_hard_dependency_blocked(
            conn,
            sid,
            hard,
            progress=progress,
            selected=selected,
            update_step=update_step,
        ):
            continue

        if skip_if_kline_unavailable(
            conn,
            sid,
            kline_available=kline_available,
            conn_status=conn_status,
            progress=progress,
            update_step=update_step,
        ):
            continue

        mark_step_running(
            conn,
            sid,
            update_step=update_step,
            touch_heartbeat=touch_heartbeat,
        )
        logger.info(f"[更新] 开始: {step['name']}")

        try:
            runner = runners[sid]
            result = await run_step_with_managed_connection(runner, get_conn=get_conn)

            status, count, error_text = resolve_step_result(result)
            record_step_source_state(conn, sid, status, error_text)
            outcome_state = apply_step_result(
                conn,
                sid,
                status=status,
                count=count,
                error_text=error_text,
                progress=progress,
                update_step=update_step,
            )
            outcome = format_step_result_for_log(status, count, error_text)
            if outcome_state == "skipped":
                logger.info(f"[更新] 已最新: {step['name']} ({outcome})")
                continue
            if outcome_state == "failed":
                logger.error(f"[更新] 失败: {step['name']}: {outcome}")
                continue

            calibrate_data_completeness(conn, sid)

            logger.info(f"[更新] 完成: {step['name']} ({outcome})")
        except stopped_exception_type as exc:
            record_step_source_state(conn, sid, "blocked", str(exc))
            mark_step_stopped(conn, sid, exc, progress=progress, update_step=update_step)
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            logger.info(f"[更新] 已停止: {step['name']}")
            break
        except Exception as exc:
            record_step_source_state(conn, sid, "failed", str(exc))
            mark_step_failed(conn, sid, exc, progress=progress, update_step=update_step)
            logger.error(f"[更新] 失败: {step['name']}: {exc}")

    result_counts = progress.counts()
    logger.info(
        "[更新] 全部完成: %d 成功, %d 失败, %d 跳过, %d 停止",
        result_counts["completed"],
        result_counts["failed"],
        result_counts["skipped"],
        result_counts["stopped"],
    )
    return result_counts


async def run_single_steps(
    conn,
    *,
    requested_step_id: str,
    step_ids: Iterable[str],
    step_index,
    hard_deps,
    runners,
    stopped_exception_type,
    should_stop: Callable[[], bool],
    check_connectivity: Callable,
    update_step: Callable,
    update_steps: Callable,
    resolve_step_result: Callable,
    format_step_result_for_log: Callable,
    calibrate_data_completeness: Callable,
    logger,
) -> dict[str, int]:
    """Execute one requested updater step plus its downstream chain."""

    step_ids = list(step_ids)
    selected = set(step_ids)

    conn_status, kline_available = await kline_connectivity_for_steps(
        step_ids,
        check_connectivity=check_connectivity,
    )

    progress = StepRunProgress()

    for sid in step_ids:
        if should_stop():
            logger.info("[单步] 用户停止")
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            break

        step_label = step_name_for(step_index, sid)
        hard = selected_dependency_ids(hard_deps.get(sid, []), selected)

        if skip_if_hard_dependency_blocked(
            conn,
            sid,
            hard,
            progress=progress,
            selected=selected,
            update_step=update_step,
            include_finished_at=True,
        ):
            logger.warning(f"[单步] 跳过: {step_label}: {HARD_DEPENDENCY_ERROR}")
            continue

        if skip_if_kline_unavailable(
            conn,
            sid,
            kline_available=kline_available,
            conn_status=conn_status,
            progress=progress,
            update_step=update_step,
            include_timestamps=True,
        ):
            logger.warning(f"[单步] 跳过: {step_label}: K线源不可用")
            continue

        mark_step_running(
            conn,
            sid,
            update_step=update_step,
            reset_finished=True,
            reset_error=True,
        )
        if sid == requested_step_id:
            logger.info(f"[单步] 开始: {step_label}")
        else:
            logger.info(f"[单步续跑] 开始: {step_label}")

        try:
            result = await runners[sid](conn)
            status, count, error_text = resolve_step_result(result)
            outcome_state = apply_step_result(
                conn,
                sid,
                status=status,
                count=count,
                error_text=error_text,
                progress=progress,
                update_step=update_step,
            )
            outcome = format_step_result_for_log(status, count, error_text)
            log_prefix = "单步续跑" if sid != requested_step_id else "单步"
            if outcome_state == "skipped":
                logger.info(f"[{log_prefix}] 已最新: {step_label} ({outcome})")
                continue
            if outcome_state == "failed":
                logger.error(f"[{log_prefix}] 失败: {step_label}: {outcome}")
                continue
            calibrate_data_completeness(conn, sid)
            logger.info(f"[{log_prefix}] 完成: {step_label}: {outcome}")
        except stopped_exception_type as exc:
            mark_step_stopped(conn, sid, exc, progress=progress, update_step=update_step)
            mark_remaining_stopped(
                conn,
                step_ids,
                progress=progress,
                update_steps=update_steps,
            )
            log_prefix = "单步续跑" if sid != requested_step_id else "单步"
            logger.info(f"[{log_prefix}] 已停止: {step_label}")
            break
        except Exception as exc:
            mark_step_failed(conn, sid, exc, progress=progress, update_step=update_step)
            log_prefix = "单步续跑" if sid != requested_step_id else "单步"
            logger.error(f"[{log_prefix}] 失败: {step_label}: {exc}")

    result_counts = progress.counts()
    logger.info(
        "[单步] 链路完成: %d 成功, %d 失败, %d 跳过, %d 停止",
        result_counts["completed"],
        result_counts["failed"],
        result_counts["skipped"],
        result_counts["stopped"],
    )
    return result_counts


async def run_smart_steps(
    conn,
    *,
    steps,
    steps_to_run: Iterable[str],
    hard_deps,
    runners,
    stopped_exception_type,
    should_stop: Callable[[], bool],
    get_conn: Callable,
    check_connectivity: Callable,
    update_step: Callable,
    update_steps: Callable,
    record_step_source_state: Callable,
    resolve_step_result: Callable,
    format_step_result_for_log: Callable,
    calibrate_data_completeness: Callable,
    touch_heartbeat: Callable,
    mark_stale_running_steps_failed: Callable,
    step_budget_seconds: Callable,
    logger,
) -> dict[str, int]:
    """Execute a smart updater plan while preserving route-level bookkeeping."""

    steps_to_run = list(steps_to_run)
    mark_stale_running_steps_failed(conn)

    conn_status, kline_available = await kline_connectivity_for_steps(
        steps_to_run,
        check_connectivity=check_connectivity,
    )

    progress = StepRunProgress()
    selected = set(steps_to_run)
    all_step_ids = step_ids_for(steps)

    progress.skipped.update(skipped_step_ids_outside(steps, selected))

    for step in selected_step_specs(steps, selected):
        if should_stop():
            logger.info("[智能更新] 用户停止")
            mark_remaining_stopped(
                conn,
                all_step_ids,
                progress=progress,
                update_steps=update_steps,
                selected=selected,
            )
            break

        sid = step["id"]
        hard = selected_dependency_ids(hard_deps.get(sid, []), selected)

        if skip_if_hard_dependency_blocked(
            conn,
            sid,
            hard,
            progress=progress,
            selected=selected,
            update_step=update_step,
        ):
            continue

        if skip_if_kline_unavailable(
            conn,
            sid,
            kline_available=kline_available,
            conn_status=conn_status,
            progress=progress,
            update_step=update_step,
        ):
            continue

        mark_step_running(
            conn,
            sid,
            update_step=update_step,
            touch_heartbeat=touch_heartbeat,
        )
        logger.info(f"[智能更新] 开始: {step['name']}")

        try:
            runner = runners[sid]
            budget = step_budget_seconds(sid, conn=conn)
            if budget is not None:
                logger.info(f"[智能更新] {step['name']} budget={budget}s (calendar-lag-aware)")
            result = await run_step_with_managed_connection(
                runner,
                get_conn=get_conn,
                budget=budget,
            )

            status, count, error_text = resolve_step_result(result)
            record_step_source_state(conn, sid, status, error_text)
            outcome_state = apply_step_result(
                conn,
                sid,
                status=status,
                count=count,
                error_text=error_text,
                progress=progress,
                update_step=update_step,
            )
            outcome = format_step_result_for_log(status, count, error_text)
            if outcome_state == "skipped":
                logger.info(f"[智能更新] 已最新: {step['name']} ({outcome})")
                continue
            if outcome_state == "failed":
                logger.error(f"[智能更新] 失败: {step['name']}: {outcome}")
                continue
            calibrate_data_completeness(conn, sid)
        except stopped_exception_type as exc:
            record_step_source_state(conn, sid, "blocked", str(exc))
            mark_step_stopped(conn, sid, exc, progress=progress, update_step=update_step)
            mark_remaining_stopped(
                conn,
                all_step_ids,
                progress=progress,
                update_steps=update_steps,
                selected=selected,
            )
            logger.info(f"[智能更新] 已停止: {step['name']}")
            break
        except asyncio.TimeoutError:
            budget = step_budget_seconds(sid, conn=conn)
            error_text = f"step budget timeout after {budget}s"
            record_step_source_state(conn, sid, "failed", error_text)
            mark_step_failed(conn, sid, error_text, progress=progress, update_step=update_step)
            logger.error(f"[智能更新] 超时: {step['name']}: {error_text}")
        except Exception as exc:
            record_step_source_state(conn, sid, "failed", str(exc))
            mark_step_failed(conn, sid, exc, progress=progress, update_step=update_step)
            logger.error(f"[智能更新] 失败: {step['name']}: {exc}")

    result_counts = progress.counts()
    logger.info(
        "[智能更新] 完成: %d 成功, %d 失败, %d 跳过, %d 停止",
        result_counts["completed"],
        result_counts["failed"],
        result_counts["skipped"],
        result_counts["stopped"],
    )
    return result_counts
