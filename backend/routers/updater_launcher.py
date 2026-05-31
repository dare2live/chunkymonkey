"""Background launcher helpers for updater routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from routers.updater_execution import (
    run_all_steps,
    run_group_steps,
    run_single_steps,
    run_smart_steps,
)


@dataclass(frozen=True)
class UpdaterExecutionDeps:
    """Route-owned callbacks needed by shared updater launchers."""

    get_conn: Callable
    fail_unfinished_steps: Callable
    safe_finally_cleanup: Callable
    record_last_exception: Callable
    logger: object
    stopped_exception_type: type[BaseException]
    should_stop: Callable[[], bool]
    check_connectivity: Callable
    update_step: Callable
    update_steps: Callable
    record_step_source_state: Callable
    resolve_step_result: Callable
    format_step_result_for_log: Callable
    calibrate_data_completeness: Callable
    touch_heartbeat: Callable
    mark_stale_running_steps_failed: Callable
    step_budget_seconds: Callable
    prime_step_status_rows: Callable


async def run_background_update_task(
    *,
    step_ids: Iterable[str],
    execute: Callable,
    get_conn: Callable,
    fail_unfinished_steps: Callable,
    cleanup: Callable,
    logger,
    error_message: Callable[[BaseException], str],
    record_exception: Callable[[BaseException], None] | None = None,
    conn_timeout: int = 120,
) -> None:
    """Run one background updater launcher with shared failure and cleanup flow."""

    step_ids = list(step_ids)
    conn = get_conn(timeout=conn_timeout)
    try:
        await execute(conn)
    except Exception as exc:
        fail_unfinished_steps(conn, step_ids, f"运行异常: {exc}")
        logger.error(error_message(exc))
        if record_exception is not None:
            record_exception(exc)
    finally:
        cleanup(conn)


async def run_smart_update_background(
    *,
    steps,
    steps_to_run: Iterable[str],
    hard_deps,
    runners,
    deps: UpdaterExecutionDeps,
) -> None:
    """Run the smart updater background task with route callbacks grouped."""

    steps_to_run = list(steps_to_run)
    await run_background_update_task(
        step_ids=steps_to_run,
        get_conn=deps.get_conn,
        fail_unfinished_steps=deps.fail_unfinished_steps,
        cleanup=lambda conn: deps.safe_finally_cleanup("smart_update", conn=conn),
        logger=deps.logger,
        error_message=lambda exc: f"[智能更新] 异常: {exc}",
        record_exception=lambda exc: deps.record_last_exception("smart_update", exc),
        execute=lambda conn: run_smart_steps(
            conn,
            steps=steps,
            steps_to_run=steps_to_run,
            hard_deps=hard_deps,
            runners=runners,
            stopped_exception_type=deps.stopped_exception_type,
            should_stop=deps.should_stop,
            get_conn=deps.get_conn,
            check_connectivity=deps.check_connectivity,
            update_step=deps.update_step,
            update_steps=deps.update_steps,
            record_step_source_state=deps.record_step_source_state,
            resolve_step_result=deps.resolve_step_result,
            format_step_result_for_log=deps.format_step_result_for_log,
            calibrate_data_completeness=deps.calibrate_data_completeness,
            touch_heartbeat=deps.touch_heartbeat,
            mark_stale_running_steps_failed=deps.mark_stale_running_steps_failed,
            step_budget_seconds=deps.step_budget_seconds,
            logger=deps.logger,
        ),
    )


async def run_full_update_background(
    *,
    steps,
    step_ids: Iterable[str],
    hard_deps,
    runners,
    deps: UpdaterExecutionDeps,
) -> None:
    """Run the full updater DAG background task with route callbacks grouped."""

    step_ids = list(step_ids)
    await run_background_update_task(
        step_ids=step_ids,
        get_conn=deps.get_conn,
        fail_unfinished_steps=deps.fail_unfinished_steps,
        cleanup=lambda conn: deps.safe_finally_cleanup("full_update", conn=conn),
        logger=deps.logger,
        error_message=lambda exc: f"[更新] 异常: {exc}",
        record_exception=lambda exc: deps.record_last_exception("full_update", exc),
        execute=lambda conn: run_all_steps(
            conn,
            steps=steps,
            step_ids=step_ids,
            hard_deps=hard_deps,
            runners=runners,
            stopped_exception_type=deps.stopped_exception_type,
            should_stop=deps.should_stop,
            get_conn=deps.get_conn,
            check_connectivity=deps.check_connectivity,
            update_step=deps.update_step,
            update_steps=deps.update_steps,
            record_step_source_state=deps.record_step_source_state,
            resolve_step_result=deps.resolve_step_result,
            format_step_result_for_log=deps.format_step_result_for_log,
            calibrate_data_completeness=deps.calibrate_data_completeness,
            touch_heartbeat=deps.touch_heartbeat,
            mark_stale_running_steps_failed=deps.mark_stale_running_steps_failed,
            prime_step_status_rows=deps.prime_step_status_rows,
            logger=deps.logger,
        ),
    )


async def run_single_update_background(
    *,
    requested_step_id: str,
    step_name: str,
    step_ids: Iterable[str],
    step_index,
    hard_deps,
    runners,
    deps: UpdaterExecutionDeps,
) -> None:
    """Run one requested step chain with route callbacks grouped."""

    step_ids = list(step_ids)
    await run_background_update_task(
        step_ids=step_ids,
        get_conn=deps.get_conn,
        fail_unfinished_steps=deps.fail_unfinished_steps,
        cleanup=lambda conn: deps.safe_finally_cleanup(f"single_step:{requested_step_id}", conn=conn),
        logger=deps.logger,
        error_message=lambda exc: f"[单步] {step_name} 失败: {exc}",
        record_exception=lambda exc: deps.record_last_exception(f"single_step:{requested_step_id}", exc),
        execute=lambda conn: run_single_steps(
            conn,
            requested_step_id=requested_step_id,
            step_ids=step_ids,
            step_index=step_index,
            hard_deps=hard_deps,
            runners=runners,
            stopped_exception_type=deps.stopped_exception_type,
            should_stop=deps.should_stop,
            check_connectivity=deps.check_connectivity,
            update_step=deps.update_step,
            update_steps=deps.update_steps,
            resolve_step_result=deps.resolve_step_result,
            format_step_result_for_log=deps.format_step_result_for_log,
            calibrate_data_completeness=deps.calibrate_data_completeness,
            logger=deps.logger,
        ),
    )


async def run_group_update_background(
    *,
    run_name: str,
    steps_in_group,
    step_ids: Iterable[str],
    hard_deps,
    runners,
    deps: UpdaterExecutionDeps,
) -> None:
    """Run one updater group background task with route callbacks grouped."""

    step_ids = list(step_ids)
    await run_background_update_task(
        step_ids=step_ids,
        get_conn=deps.get_conn,
        fail_unfinished_steps=deps.fail_unfinished_steps,
        cleanup=lambda conn: deps.safe_finally_cleanup(run_name, conn=conn),
        logger=deps.logger,
        error_message=lambda exc: f"[{run_name}] 异常: {exc}",
        record_exception=lambda exc: deps.record_last_exception(run_name, exc),
        execute=lambda conn: run_group_steps(
            conn,
            run_name=run_name,
            steps_in_group=steps_in_group,
            step_ids=step_ids,
            hard_deps=hard_deps,
            runners=runners,
            stopped_exception_type=deps.stopped_exception_type,
            should_stop=deps.should_stop,
            get_conn=deps.get_conn,
            check_connectivity=deps.check_connectivity,
            update_step=deps.update_step,
            update_steps=deps.update_steps,
            resolve_step_result=deps.resolve_step_result,
            format_step_result_for_log=deps.format_step_result_for_log,
            calibrate_data_completeness=deps.calibrate_data_completeness,
            logger=deps.logger,
        ),
    )


def launch_group_update_request(
    *,
    run_mode: str,
    run_name: str,
    group_id: str,
    running: bool,
    steps,
    hard_deps,
    runners,
    step_specs_for_group: Callable,
    step_ids_for: Callable,
    begin_run: Callable,
    prime_run_step_status: Callable,
    execution_deps: Callable[[], UpdaterExecutionDeps],
    create_task: Callable,
    logger,
    run_group_background: Callable | None = None,
) -> dict:
    """Build and schedule one grouped update request for the route shell."""

    if running:
        return {"ok": False, "message": "更新正在进行中"}

    steps_in_group = step_specs_for_group(steps, group_id)
    step_ids = list(step_ids_for(steps_in_group))
    if not step_ids:
        return {"ok": False, "error": f"未知的分组: {group_id}"}

    begin_run(run_mode, step_ids=step_ids)
    prime_run_step_status(step_ids, inactive_mode="idle")
    logger.info(f"[{run_name}] 已请求: {len(step_ids)} 个步骤")

    launcher = run_group_background or run_group_update_background
    create_task(
        launcher(
            run_name=run_name,
            steps_in_group=steps_in_group,
            step_ids=step_ids,
            hard_deps=hard_deps,
            runners=runners,
            deps=execution_deps(),
        )
    )
    return {"ok": True, "steps": len(step_ids), "step_ids": step_ids}
