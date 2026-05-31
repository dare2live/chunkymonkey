import asyncio

from routers.updater_launcher import (
    UpdaterExecutionDeps,
    launch_group_update_request,
    run_background_update_task,
    run_full_update_background,
    run_group_update_background,
    run_single_update_background,
    run_smart_update_background,
)


def test_run_background_update_task_cleans_up_success():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

    class FakeLogger:
        def error(self, msg):
            events.append(("error", msg))

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("main")

    async def execute(conn):
        events.append(("execute", conn.name))

    asyncio.run(
        run_background_update_task(
            step_ids=["sync_raw"],
            execute=execute,
            get_conn=get_conn,
            fail_unfinished_steps=lambda *args: events.append(("fail", args)),
            cleanup=lambda conn: events.append(("cleanup", conn.name)),
            logger=FakeLogger(),
            error_message=lambda exc: f"error:{exc}",
            record_exception=lambda exc: events.append(("record", str(exc))),
        )
    )

    assert events == [
        ("get_conn", 120),
        ("execute", "main"),
        ("cleanup", "main"),
    ]


def test_run_background_update_task_marks_unfinished_and_records_failure():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

    class FakeLogger:
        def error(self, msg):
            events.append(("error", msg))

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("main")

    async def execute(conn):
        events.append(("execute", conn.name))
        raise RuntimeError("boom")

    def fail_unfinished(conn, step_ids, error):
        events.append(("fail", conn.name, tuple(step_ids), error))

    asyncio.run(
        run_background_update_task(
            step_ids=["sync_raw", "match_inst"],
            execute=execute,
            get_conn=get_conn,
            fail_unfinished_steps=fail_unfinished,
            cleanup=lambda conn: events.append(("cleanup", conn.name)),
            logger=FakeLogger(),
            error_message=lambda exc: f"caught:{exc}",
            record_exception=lambda exc: events.append(("record", type(exc).__name__, str(exc))),
        )
    )

    assert events == [
        ("get_conn", 120),
        ("execute", "main"),
        ("fail", "main", ("sync_raw", "match_inst"), "运行异常: boom"),
        ("error", "caught:boom"),
        ("record", "RuntimeError", "boom"),
        ("cleanup", "main"),
    ]


def test_run_smart_update_background_groups_launcher_dependencies():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(("close", self.name))

    class FakeLogger:
        def info(self, *args):
            events.append(("info", args[0]))

        def warning(self, *args):
            events.append(("warning", args[0]))

        def error(self, *args):
            events.append(("error", args[0]))

    conns = iter([FakeConn("main"), FakeConn("step")])

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return next(conns)

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 2}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    deps = UpdaterExecutionDeps(
        get_conn=get_conn,
        fail_unfinished_steps=lambda *args: events.append(("fail", args)),
        safe_finally_cleanup=lambda trigger, *, conn=None: events.append(("cleanup", trigger, conn.name)),
        record_last_exception=lambda trigger, exc: events.append(("record", trigger, str(exc))),
        logger=FakeLogger(),
        stopped_exception_type=RuntimeError,
        should_stop=lambda: False,
        check_connectivity=lambda: events.append("unexpected-connectivity"),
        update_step=update_step,
        update_steps=lambda *args: events.append(("bulk", args)),
        record_step_source_state=lambda conn, step_id, status, error: events.append(
            ("source", conn.name, step_id, status, error)
        ),
        resolve_step_result=lambda result: (
            result["status"],
            result["count"],
            result.get("error"),
        ),
        format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
        calibrate_data_completeness=lambda conn, step_id: events.append(("calibrate", conn.name, step_id)),
        touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
        mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
        step_budget_seconds=lambda step_id, *, conn: events.append(("budget", conn.name, step_id)) or None,
        prime_step_status_rows=lambda *args, **kwargs: events.append(("prime", args, kwargs)),
    )

    asyncio.run(
        run_smart_update_background(
            steps=[{"id": "sync_raw", "name": "Raw"}],
            steps_to_run=["sync_raw"],
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            deps=deps,
        )
    )

    assert ("get_conn", 120) in events
    assert ("stale", "main") in events
    assert ("budget", "main", "sync_raw") in events
    assert ("heartbeat", "sync_raw") in events
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("source", "main", "sync_raw", "completed", None) in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events
    assert ("cleanup", "smart_update", "main") in events
    assert "unexpected-connectivity" not in events


def test_run_full_update_background_groups_launcher_dependencies():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(("close", self.name))

    class FakeLogger:
        def info(self, *args):
            events.append(("info", args[0]))

        def warning(self, *args):
            events.append(("warning", args[0]))

        def error(self, *args):
            events.append(("error", args[0]))

    conns = iter([FakeConn("main"), FakeConn("step")])

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return next(conns)

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 8}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    deps = UpdaterExecutionDeps(
        get_conn=get_conn,
        fail_unfinished_steps=lambda *args: events.append(("fail", args)),
        safe_finally_cleanup=lambda trigger, *, conn=None: events.append(("cleanup", trigger, conn.name)),
        record_last_exception=lambda trigger, exc: events.append(("record", trigger, str(exc))),
        logger=FakeLogger(),
        stopped_exception_type=RuntimeError,
        should_stop=lambda: False,
        check_connectivity=lambda: events.append("unexpected-connectivity"),
        update_step=update_step,
        update_steps=lambda *args: events.append(("bulk", args)),
        record_step_source_state=lambda conn, step_id, status, error: events.append(
            ("source", conn.name, step_id, status, error)
        ),
        resolve_step_result=lambda result: (
            result["status"],
            result["count"],
            result.get("error"),
        ),
        format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
        calibrate_data_completeness=lambda conn, step_id: events.append(("calibrate", conn.name, step_id)),
        touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
        mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
        step_budget_seconds=lambda step_id, *, conn: events.append(("budget", conn.name, step_id)) or None,
        prime_step_status_rows=lambda conn, step_ids, *, inactive_mode: events.append(
            ("prime", conn.name, step_ids, inactive_mode)
        ),
    )

    asyncio.run(
        run_full_update_background(
            steps=[{"id": "sync_raw", "name": "Raw"}],
            step_ids=["sync_raw"],
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            deps=deps,
        )
    )

    assert ("get_conn", 120) in events
    assert ("stale", "main") in events
    assert ("prime", "main", ["sync_raw"], "idle") in events
    assert ("heartbeat", "sync_raw") in events
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("source", "main", "sync_raw", "completed", None) in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events
    assert ("cleanup", "full_update", "main") in events
    assert "unexpected-connectivity" not in events


def test_run_single_update_background_groups_launcher_dependencies():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

    class FakeLogger:
        def info(self, *args):
            events.append(("info", args[0]))

        def warning(self, *args):
            events.append(("warning", args[0]))

        def error(self, *args):
            events.append(("error", args[0]))

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("main")

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 4}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    deps = UpdaterExecutionDeps(
        get_conn=get_conn,
        fail_unfinished_steps=lambda *args: events.append(("fail", args)),
        safe_finally_cleanup=lambda trigger, *, conn=None: events.append(("cleanup", trigger, conn.name)),
        record_last_exception=lambda trigger, exc: events.append(("record", trigger, str(exc))),
        logger=FakeLogger(),
        stopped_exception_type=RuntimeError,
        should_stop=lambda: False,
        check_connectivity=lambda: events.append("unexpected-connectivity"),
        update_step=update_step,
        update_steps=lambda *args: events.append(("bulk", args)),
        record_step_source_state=lambda *args: events.append(("source", args)),
        resolve_step_result=lambda result: (
            result["status"],
            result["count"],
            result.get("error"),
        ),
        format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
        calibrate_data_completeness=lambda conn, step_id: events.append(("calibrate", conn.name, step_id)),
        touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
        mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
        step_budget_seconds=lambda step_id, *, conn: events.append(("budget", conn.name, step_id)) or None,
        prime_step_status_rows=lambda *args, **kwargs: events.append(("prime", args, kwargs)),
    )

    asyncio.run(
        run_single_update_background(
            requested_step_id="sync_raw",
            step_name="Raw",
            step_ids=["sync_raw"],
            step_index={"sync_raw": {"name": "Raw"}},
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            deps=deps,
        )
    )

    assert events[0] == ("get_conn", 120)
    assert ("runner", "main") in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events
    assert ("cleanup", "single_step:sync_raw", "main") in events
    assert "unexpected-connectivity" not in events


def test_run_group_update_background_groups_launcher_dependencies():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(("close", self.name))

    class FakeLogger:
        def info(self, *args):
            events.append(("info", args[0]))

        def warning(self, *args):
            events.append(("warning", args[0]))

        def error(self, *args):
            events.append(("error", args[0]))

    conns = iter([FakeConn("main"), FakeConn("step")])

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return next(conns)

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 3}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    deps = UpdaterExecutionDeps(
        get_conn=get_conn,
        fail_unfinished_steps=lambda *args: events.append(("fail", args)),
        safe_finally_cleanup=lambda trigger, *, conn=None: events.append(("cleanup", trigger, conn.name)),
        record_last_exception=lambda trigger, exc: events.append(("record", trigger, str(exc))),
        logger=FakeLogger(),
        stopped_exception_type=RuntimeError,
        should_stop=lambda: False,
        check_connectivity=lambda: events.append("unexpected-connectivity"),
        update_step=update_step,
        update_steps=lambda *args: events.append(("bulk", args)),
        record_step_source_state=lambda *args: events.append(("source", args)),
        resolve_step_result=lambda result: (
            result["status"],
            result["count"],
            result.get("error"),
        ),
        format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
        calibrate_data_completeness=lambda conn, step_id: events.append(("calibrate", conn.name, step_id)),
        touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
        mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
        step_budget_seconds=lambda step_id, *, conn: events.append(("budget", conn.name, step_id)) or None,
        prime_step_status_rows=lambda *args, **kwargs: events.append(("prime", args, kwargs)),
    )

    asyncio.run(
        run_group_update_background(
            run_name="数据同步组",
            steps_in_group=[{"id": "sync_raw", "name": "Raw"}],
            step_ids=["sync_raw"],
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            deps=deps,
        )
    )

    assert ("get_conn", 120) in events
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events
    assert ("cleanup", "数据同步组", "main") in events
    assert "unexpected-connectivity" not in events


def test_launch_group_update_request_returns_busy_without_side_effects():
    events = []

    result = launch_group_update_request(
        run_mode="sync",
        run_name="数据获取组",
        group_id="data",
        running=True,
        steps=[],
        hard_deps={},
        runners={},
        step_specs_for_group=lambda *args: events.append("unexpected-step-specs"),
        step_ids_for=lambda *args: events.append("unexpected-step-ids"),
        begin_run=lambda *args, **kwargs: events.append("unexpected-begin"),
        prime_run_step_status=lambda *args, **kwargs: events.append("unexpected-prime"),
        execution_deps=lambda: events.append("unexpected-deps"),
        create_task=lambda task: events.append(("unexpected-task", task)),
        logger=None,
    )

    assert result == {"ok": False, "message": "更新正在进行中"}
    assert events == []


def test_launch_group_update_request_returns_unknown_group_without_starting():
    events = []

    result = launch_group_update_request(
        run_mode="sync",
        run_name="数据获取组",
        group_id="missing",
        running=False,
        steps=[{"id": "sync_raw", "group": "data"}],
        hard_deps={},
        runners={},
        step_specs_for_group=lambda steps, group_id: [],
        step_ids_for=lambda specs: [],
        begin_run=lambda *args, **kwargs: events.append("unexpected-begin"),
        prime_run_step_status=lambda *args, **kwargs: events.append("unexpected-prime"),
        execution_deps=lambda: events.append("unexpected-deps"),
        create_task=lambda task: events.append(("unexpected-task", task)),
        logger=None,
    )

    assert result == {"ok": False, "error": "未知的分组: missing"}
    assert events == []


def test_launch_group_update_request_primes_and_schedules_background():
    events = []
    steps = [
        {"id": "sync_raw", "group": "data"},
        {"id": "match_inst", "group": "data"},
        {"id": "calc_returns", "group": "calc"},
    ]

    class FakeLogger:
        def info(self, msg):
            events.append(("info", msg))

    def step_specs_for_group(all_steps, group_id):
        events.append(("specs", group_id))
        return [step for step in all_steps if step["group"] == group_id]

    def step_ids_for(specs):
        ids = [step["id"] for step in specs]
        events.append(("ids", ids))
        return ids

    def run_group_background(**kwargs):
        events.append(("background", kwargs["run_name"], kwargs["step_ids"], kwargs["deps"]))
        return ("background-task", tuple(kwargs["step_ids"]))

    result = launch_group_update_request(
        run_mode="sync",
        run_name="数据获取组",
        group_id="data",
        running=False,
        steps=steps,
        hard_deps={"match_inst": ["sync_raw"]},
        runners={"sync_raw": object()},
        step_specs_for_group=step_specs_for_group,
        step_ids_for=step_ids_for,
        begin_run=lambda mode, *, step_ids: events.append(("begin", mode, step_ids)),
        prime_run_step_status=lambda step_ids, *, inactive_mode: events.append(
            ("prime", step_ids, inactive_mode)
        ),
        execution_deps=lambda: "deps",
        create_task=lambda task: events.append(("task", task)),
        logger=FakeLogger(),
        run_group_background=run_group_background,
    )

    assert result == {"ok": True, "steps": 2, "step_ids": ["sync_raw", "match_inst"]}
    assert events == [
        ("specs", "data"),
        ("ids", ["sync_raw", "match_inst"]),
        ("begin", "sync", ["sync_raw", "match_inst"]),
        ("prime", ["sync_raw", "match_inst"], "idle"),
        ("info", "[数据获取组] 已请求: 2 个步骤"),
        ("background", "数据获取组", ["sync_raw", "match_inst"], "deps"),
        ("task", ("background-task", ("sync_raw", "match_inst"))),
    ]
