import asyncio

import routers.updater_execution as updater_execution
from routers.updater_execution import (
    HARD_DEPENDENCY_ERROR,
    KLINE_UNAVAILABLE_ERROR,
    STOP_REQUESTED_ERROR,
    StepRunProgress,
    apply_step_result,
    is_hard_dependency_blocked,
    kline_connectivity_for_steps,
    kline_unavailable_step_update,
    mark_remaining_stopped,
    mark_step_failed,
    mark_step_running,
    mark_step_stopped,
    remaining_step_ids,
    run_all_steps,
    run_group_steps,
    run_single_steps,
    run_smart_steps,
    run_step_with_managed_connection,
    skip_if_hard_dependency_blocked,
    skip_if_kline_unavailable,
)


def test_is_hard_dependency_blocked_only_for_selected_skipped_dependencies():
    assert is_hard_dependency_blocked(
        ["sync_raw"],
        failed=set(),
        skipped={"sync_raw"},
        stopped=set(),
        selected={"sync_raw", "match_inst"},
    )
    assert not is_hard_dependency_blocked(
        ["sync_raw"],
        failed=set(),
        skipped={"sync_raw"},
        stopped=set(),
        selected={"match_inst"},
    )


def test_remaining_step_ids_preserves_order_and_ignores_completed_states():
    remaining = remaining_step_ids(
        ["a", "b", "c", "d"],
        completed={"a"},
        failed={"b"},
        skipped=set(),
        stopped={"d"},
    )

    assert remaining == ["c"]


def test_remaining_step_ids_can_scope_to_selected_steps():
    remaining = remaining_step_ids(
        ["a", "b", "c"],
        completed=set(),
        failed=set(),
        skipped=set(),
        selected={"b"},
    )

    assert remaining == ["b"]


def test_step_run_progress_reports_remaining_and_counts():
    progress = StepRunProgress()
    progress.completed.add("a")
    progress.failed.add("b")
    progress.skipped.add("d")

    assert progress.remaining(["a", "b", "c", "d"], selected={"b", "c", "d"}) == ["c"]
    assert progress.counts() == {
        "completed": 1,
        "failed": 1,
        "skipped": 1,
        "stopped": 0,
    }


def test_run_step_with_managed_connection_closes_connection():
    events = []

    class FakeConn:
        def close(self):
            events.append("close")

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn()

    async def runner(conn):
        events.append(("runner", isinstance(conn, FakeConn)))
        return {"count": 3}

    result = asyncio.run(run_step_with_managed_connection(runner, get_conn=get_conn))

    assert result == {"count": 3}
    assert events == [("get_conn", 120), ("runner", True), "close"]


def test_run_step_with_managed_connection_applies_budget(monkeypatch):
    timeouts = []

    async def fake_wait_for(coro, timeout):
        timeouts.append(timeout)
        return await coro

    monkeypatch.setattr(updater_execution.asyncio, "wait_for", fake_wait_for)

    class FakeConn:
        def close(self):
            pass

    async def runner(conn):
        return 9

    result = asyncio.run(
        run_step_with_managed_connection(
            runner,
            get_conn=lambda timeout: FakeConn(),
            budget=17,
        )
    )

    assert result == 9
    assert timeouts == [17]


def test_kline_connectivity_for_steps_skips_probe_when_market_data_not_selected():
    calls = []

    async def check_connectivity():
        calls.append("probe")
        return {"kline_source": False}

    conn_status, available = asyncio.run(
        kline_connectivity_for_steps(
            ["sync_raw", "match_inst"],
            check_connectivity=check_connectivity,
        )
    )

    assert conn_status == {}
    assert available is True
    assert calls == []


def test_kline_connectivity_for_steps_probes_market_data_once():
    calls = []

    async def check_connectivity():
        calls.append("probe")
        return {"kline_source": False, "message": "down"}

    conn_status, available = asyncio.run(
        kline_connectivity_for_steps(
            ["sync_raw", "sync_market_data"],
            check_connectivity=check_connectivity,
        )
    )

    assert conn_status == {"kline_source": False, "message": "down"}
    assert available is False
    assert calls == ["probe"]


def test_apply_step_result_updates_status_and_progress():
    calls = []

    def update_step(conn, step_id, **kwargs):
        calls.append((conn, step_id, kwargs))

    progress = StepRunProgress()
    outcome = apply_step_result(
        "conn",
        "sync_raw",
        status="completed",
        count=7,
        error_text=None,
        progress=progress,
        update_step=update_step,
    )

    assert outcome == "completed"
    assert progress.completed == {"sync_raw"}
    assert progress.failed == set()
    assert progress.skipped == set()
    assert calls[0][0] == "conn"
    assert calls[0][1] == "sync_raw"
    assert calls[0][2]["status"] == "completed"
    assert calls[0][2]["records"] == 7
    assert calls[0][2]["finished_at"]
    assert "error" not in calls[0][2]


def test_apply_step_result_tracks_skipped_and_failed_states():
    calls = []

    def update_step(conn, step_id, **kwargs):
        calls.append((step_id, kwargs))

    progress = StepRunProgress()

    assert apply_step_result(
        None,
        "sync_qfii",
        status="skipped",
        count=0,
        error_text="数据已是最新",
        progress=progress,
        update_step=update_step,
    ) == "skipped"
    assert progress.skipped == {"sync_qfii"}
    assert calls[-1][1]["error"] == "数据已是最新"

    assert apply_step_result(
        None,
        "sync_lhb",
        status="blocked",
        count=0,
        error_text=HARD_DEPENDENCY_ERROR,
        progress=progress,
        update_step=update_step,
    ) == "failed"
    assert progress.failed == {"sync_lhb"}
    assert calls[-1][1]["status"] == "blocked"


def test_mark_remaining_stopped_persists_and_tracks_remaining_steps():
    calls = []

    def update_steps(conn, step_ids, status, error):
        calls.append((conn, step_ids, status, error))

    progress = StepRunProgress(completed={"sync_raw"}, failed={"sync_lhb"})

    remaining = mark_remaining_stopped(
        "conn",
        ["sync_raw", "sync_lhb", "match_inst", "sync_market_data"],
        progress=progress,
        update_steps=update_steps,
        selected={"match_inst", "sync_market_data"},
    )

    assert remaining == ["match_inst", "sync_market_data"]
    assert progress.stopped == {"match_inst", "sync_market_data"}
    assert calls == [
        (
            "conn",
            ["match_inst", "sync_market_data"],
            "stopped",
            STOP_REQUESTED_ERROR,
        )
    ]


def test_skip_if_hard_dependency_blocked_persists_skip_once_blocked():
    calls = []

    def update_step(conn, step_id, **kwargs):
        calls.append((conn, step_id, kwargs))

    progress = StepRunProgress(failed={"sync_raw"})

    blocked = skip_if_hard_dependency_blocked(
        "conn",
        "match_inst",
        ["sync_raw"],
        progress=progress,
        selected={"sync_raw", "match_inst"},
        update_step=update_step,
        include_finished_at=True,
    )

    assert blocked
    assert progress.skipped == {"match_inst"}
    assert calls[0][0] == "conn"
    assert calls[0][1] == "match_inst"
    assert calls[0][2]["status"] == "skipped"
    assert calls[0][2]["error"] == HARD_DEPENDENCY_ERROR
    assert calls[0][2]["finished_at"]


def test_skip_if_hard_dependency_blocked_ignores_unselected_skips():
    calls = []
    progress = StepRunProgress(skipped={"sync_raw"})

    blocked = skip_if_hard_dependency_blocked(
        None,
        "match_inst",
        ["sync_raw"],
        progress=progress,
        selected={"match_inst"},
        update_step=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert not blocked
    assert calls == []
    assert progress.skipped == {"sync_raw"}


def test_mark_step_running_persists_transition_and_heartbeat():
    calls = []
    heartbeats = []

    def update_step(conn, step_id, **kwargs):
        calls.append((conn, step_id, kwargs))

    mark_step_running(
        "conn",
        "sync_raw",
        update_step=update_step,
        touch_heartbeat=heartbeats.append,
        reset_finished=True,
        reset_error=True,
    )

    assert calls[0][0] == "conn"
    assert calls[0][1] == "sync_raw"
    assert calls[0][2]["status"] == "running"
    assert calls[0][2]["started_at"]
    assert calls[0][2]["finished_at"] is None
    assert calls[0][2]["error"] is None
    assert heartbeats == ["sync_raw"]


def test_mark_step_stopped_and_failed_persist_progress():
    calls = []

    def update_step(conn, step_id, **kwargs):
        calls.append((conn, step_id, kwargs))

    progress = StepRunProgress()

    mark_step_stopped(
        "conn",
        "sync_raw",
        "用户手动停止",
        progress=progress,
        update_step=update_step,
    )
    mark_step_failed(
        "conn",
        "sync_lhb",
        RuntimeError("同步失败"),
        progress=progress,
        update_step=update_step,
    )

    assert progress.stopped == {"sync_raw"}
    assert progress.failed == {"sync_lhb"}
    assert calls[0][1] == "sync_raw"
    assert calls[0][2]["status"] == "stopped"
    assert calls[0][2]["error"] == "用户手动停止"
    assert calls[0][2]["finished_at"]
    assert calls[1][1] == "sync_lhb"
    assert calls[1][2]["status"] == "failed"
    assert calls[1][2]["error"] == "同步失败"
    assert calls[1][2]["finished_at"]


def test_kline_unavailable_step_update_keeps_status_shape():
    update = kline_unavailable_step_update(include_timestamps=True)

    assert update["status"] == "skipped"
    assert update["error"] == KLINE_UNAVAILABLE_ERROR
    assert update["started_at"]
    assert update["finished_at"]


def test_skip_if_kline_unavailable_ignores_other_steps_and_available_source(monkeypatch):
    calls = []

    monkeypatch.setattr(
        updater_execution,
        "mark_kline_gap_queue_blocked",
        lambda *args: calls.append(("gap", args)),
    )

    progress = StepRunProgress()

    assert not skip_if_kline_unavailable(
        "conn",
        "sync_raw",
        kline_available=False,
        conn_status={"message": "down"},
        progress=progress,
        update_step=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
    )
    assert not skip_if_kline_unavailable(
        "conn",
        "sync_market_data",
        kline_available=True,
        conn_status={"message": "ok"},
        progress=progress,
        update_step=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
    )

    assert calls == []
    assert progress.skipped == set()


def test_skip_if_kline_unavailable_marks_gap_queue_update_and_progress(monkeypatch):
    gap_calls = []
    update_calls = []

    monkeypatch.setattr(
        updater_execution,
        "mark_kline_gap_queue_blocked",
        lambda conn, conn_status: gap_calls.append((conn, conn_status)),
    )

    def update_step(conn, step_id, **kwargs):
        update_calls.append((conn, step_id, kwargs))

    progress = StepRunProgress()

    skipped = skip_if_kline_unavailable(
        "conn",
        "sync_market_data",
        kline_available=False,
        conn_status={"message": "tdxhub unavailable"},
        progress=progress,
        update_step=update_step,
        include_timestamps=True,
    )

    assert skipped
    assert gap_calls == [("conn", {"message": "tdxhub unavailable"})]
    assert progress.skipped == {"sync_market_data"}
    assert update_calls[0][0] == "conn"
    assert update_calls[0][1] == "sync_market_data"
    assert update_calls[0][2]["status"] == "skipped"
    assert update_calls[0][2]["error"] == KLINE_UNAVAILABLE_ERROR
    assert update_calls[0][2]["started_at"]
    assert update_calls[0][2]["finished_at"]


def test_run_group_steps_executes_runner_and_calibrates_completed_step():
    events = []

    class FakeConn:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(("close", self.name))

    class FakeLogger:
        def info(self, message):
            events.append(("info", message))

        def error(self, message):
            events.append(("error", message))

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("step")

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 5}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    counts = asyncio.run(
        run_group_steps(
            FakeConn("main"),
            run_name="测试组",
            steps_in_group=[{"id": "sync_raw", "name": "Raw"}],
            step_ids=["sync_raw"],
            hard_deps={},
            runners={"sync_raw": runner},
            stopped_exception_type=RuntimeError,
            should_stop=lambda: False,
            get_conn=get_conn,
            check_connectivity=lambda: None,
            update_step=update_step,
            update_steps=lambda *args: events.append(("bulk", args)),
            resolve_step_result=lambda result: (
                result["status"],
                result["count"],
                result.get("error"),
            ),
            format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
            calibrate_data_completeness=lambda conn, step_id: events.append(
                ("calibrate", conn.name, step_id)
            ),
            logger=FakeLogger(),
        )
    )

    assert counts == {"completed": 1, "failed": 0, "skipped": 0, "stopped": 0}
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events


def test_run_single_steps_uses_route_connection_and_chain_bookkeeping():
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

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 4}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    counts = asyncio.run(
        run_single_steps(
            FakeConn("main"),
            requested_step_id="sync_raw",
            step_ids=["sync_raw"],
            step_index={"sync_raw": {"name": "Raw"}},
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            stopped_exception_type=RuntimeError,
            should_stop=lambda: False,
            check_connectivity=lambda: None,
            update_step=update_step,
            update_steps=lambda *args: events.append(("bulk", args)),
            resolve_step_result=lambda result: (
                result["status"],
                result["count"],
                result.get("error"),
            ),
            format_step_result_for_log=lambda status, count, error: f"{status}:{count}:{error}",
            calibrate_data_completeness=lambda conn, step_id: events.append(
                ("calibrate", conn.name, step_id)
            ),
            logger=FakeLogger(),
        )
    )

    assert counts == {"completed": 1, "failed": 0, "skipped": 0, "stopped": 0}
    assert ("runner", "main") in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events


def test_run_smart_steps_tracks_outside_plan_and_budgeted_runner():
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

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("step")

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 6}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    counts = asyncio.run(
        run_smart_steps(
            FakeConn("main"),
            steps=[
                {"id": "sync_raw", "name": "Raw"},
                {"id": "match_inst", "name": "Match"},
            ],
            steps_to_run=["sync_raw"],
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            stopped_exception_type=RuntimeError,
            should_stop=lambda: False,
            get_conn=get_conn,
            check_connectivity=lambda: None,
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
            calibrate_data_completeness=lambda conn, step_id: events.append(
                ("calibrate", conn.name, step_id)
            ),
            touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
            mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
            step_budget_seconds=lambda step_id, *, conn: events.append(
                ("budget", conn.name, step_id)
            )
            or 13,
            logger=FakeLogger(),
        )
    )

    assert counts == {"completed": 1, "failed": 0, "skipped": 1, "stopped": 0}
    assert ("stale", "main") in events
    assert ("budget", "main", "sync_raw") in events
    assert ("heartbeat", "sync_raw") in events
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("source", "main", "sync_raw", "completed", None) in events
    assert ("update", "main", "sync_raw", "running") in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events


def test_run_all_steps_executes_full_dag_bookkeeping():
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

    def get_conn(timeout):
        events.append(("get_conn", timeout))
        return FakeConn("step")

    async def runner(conn):
        events.append(("runner", conn.name))
        return {"status": "completed", "count": 8}

    def update_step(conn, step_id, **kwargs):
        events.append(("update", conn.name, step_id, kwargs["status"]))

    counts = asyncio.run(
        run_all_steps(
            FakeConn("main"),
            steps=[{"id": "sync_raw", "name": "Raw"}],
            step_ids=["sync_raw"],
            hard_deps={"sync_raw": []},
            runners={"sync_raw": runner},
            stopped_exception_type=RuntimeError,
            should_stop=lambda: False,
            get_conn=get_conn,
            check_connectivity=lambda: None,
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
            calibrate_data_completeness=lambda conn, step_id: events.append(
                ("calibrate", conn.name, step_id)
            ),
            touch_heartbeat=lambda step_id: events.append(("heartbeat", step_id)),
            mark_stale_running_steps_failed=lambda conn: events.append(("stale", conn.name)),
            prime_step_status_rows=lambda conn, step_ids, *, inactive_mode: events.append(
                ("prime", conn.name, step_ids, inactive_mode)
            ),
            logger=FakeLogger(),
        )
    )

    assert counts == {"completed": 1, "failed": 0, "skipped": 0, "stopped": 0}
    assert ("stale", "main") in events
    assert ("prime", "main", ["sync_raw"], "idle") in events
    assert ("heartbeat", "sync_raw") in events
    assert ("runner", "step") in events
    assert ("close", "step") in events
    assert ("source", "main", "sync_raw", "completed", None) in events
    assert ("update", "main", "sync_raw", "completed") in events
    assert ("calibrate", "main", "sync_raw") in events
