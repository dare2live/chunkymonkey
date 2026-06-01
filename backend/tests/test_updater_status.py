import asyncio
from datetime import datetime

from routers.updater_status import (
    build_finished_run_context,
    build_noop_run_context,
    build_run_context,
    build_smart_plan_response,
    build_smart_update_plan,
    build_update_status_payload,
    build_update_status_response,
    prepare_smart_update_plan,
    touch_run_context_heartbeat,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor(self.rows)

    def close(self):
        self.closed = True


def test_build_update_status_payload_preserves_route_shape_for_running_context():
    rows = [
        {
            "step_id": "sync_raw",
            "step_name": "同步原始数据",
            "status": "running",
            "records": None,
            "started_at": "2026-05-27T10:00:00",
            "finished_at": None,
            "step_order": 1,
        }
    ]
    conn = _Conn(rows)

    payload = build_update_status_payload(
        conn,
        running=True,
        stop_requested=False,
        run_context={
            "mode": "smart",
            "step_ids": ["sync_raw"],
            "step_progress": {
                "done": 50,
                "total": 100,
                "err": 1,
                "message": "raw_fetch 50/100 · written=49 · err=1",
            },
        },
        last_run_context=None,
        ui_logs=[{"message": "hello"}],
        last_exception={"message": "boom"},
        server_time="2026-05-27T10:01:00",
    )

    assert conn.calls == [("SELECT * FROM step_status ORDER BY step_order", None)]
    assert payload["running"] is True
    assert payload["stop_requested"] is False
    assert payload["run_context"] == {
        "mode": "smart",
        "step_ids": ["sync_raw"],
        "step_progress": {
            "done": 50,
            "total": 100,
            "err": 1,
            "message": "raw_fetch 50/100 · written=49 · err=1",
        },
    }
    assert payload["last_run_context"] is None
    assert payload["steps"][0]["records"] == 0
    assert payload["summary"]["kind"] == "running"
    assert payload["summary"]["active_step_ids"] == ["sync_raw"]
    assert payload["summary"]["step_progress"]["done"] == 50
    assert "进度：50/100 / err=1 / raw_fetch 50/100 · written=49 · err=1" in payload["summary"]["message"]
    assert payload["logs"] == [{"message": "hello"}]
    assert payload["last_exception"] == {"message": "boom"}
    assert payload["server_time"] == "2026-05-27T10:01:00"


def test_build_update_status_payload_returns_noop_summary_without_last_exception():
    conn = _Conn([])

    payload = build_update_status_payload(
        conn,
        running=False,
        stop_requested=False,
        run_context=None,
        last_run_context={"mode": "smart", "noop": True, "message": "fresh"},
        ui_logs=[],
        last_exception=None,
        server_time="2026-05-27T10:02:00",
    )

    assert payload["summary"]["kind"] == "noop"
    assert payload["summary"]["message"] == "fresh"
    assert payload["steps"] == []
    assert payload["last_exception"] is None


def test_run_context_helpers_build_touch_and_finish_contexts():
    start = datetime(2026, 5, 27, 10, 0, 0)
    heartbeat = datetime(2026, 5, 27, 10, 1, 0)
    finished = datetime(2026, 5, 27, 10, 2, 0)

    context = build_run_context(
        "single",
        step_id="sync_raw",
        step_name="同步原始数据",
        step_ids=("sync_calendar", "sync_raw"),
        now=start,
    )

    assert context == {
        "mode": "single",
        "step_id": "sync_raw",
        "step_name": "同步原始数据",
        "step_ids": ["sync_calendar", "sync_raw"],
        "started_at": "2026-05-27T10:00:00",
        "heartbeat_at": "2026-05-27T10:00:00",
    }

    touched = touch_run_context_heartbeat(
        context,
        "match_inst",
        {"done": 50, "total": 100},
        now=heartbeat,
    )
    assert touched is context
    assert context["step_id"] == "match_inst"
    assert context["heartbeat_at"] == "2026-05-27T10:01:00"
    assert context["step_progress"] == {"done": 50, "total": 100}

    terminal = build_finished_run_context(context, {"result": "ok"}, now=finished)
    assert terminal["finished_at"] == "2026-05-27T10:02:00"
    assert terminal["result"] == "ok"
    assert context.get("finished_at") is None


def test_noop_run_context_helper_uses_terminal_shape():
    now = datetime(2026, 5, 27, 11, 0, 0)

    context = build_noop_run_context("smart", "数据已是最新", now=now)

    assert context == {
        "mode": "smart",
        "step_id": None,
        "step_name": None,
        "step_ids": [],
        "started_at": "2026-05-27T11:00:00",
        "finished_at": "2026-05-27T11:00:00",
        "noop": True,
        "message": "数据已是最新",
    }


def test_build_update_status_response_syncs_catalog_and_closes_conn():
    conn = _Conn([])
    catalog_calls = []

    def get_conn():
        return conn

    def sync_catalog(active_conn):
        catalog_calls.append(active_conn)

    payload = build_update_status_response(
        get_conn=get_conn,
        sync_step_status_catalog=sync_catalog,
        running=False,
        stop_requested=False,
        run_context=None,
        last_run_context=None,
        ui_logs=[],
        last_exception=None,
    )

    assert catalog_calls == [conn]
    assert conn.calls == [("SELECT * FROM step_status ORDER BY step_order", None)]
    assert conn.closed is True
    assert payload["running"] is False
    assert payload["summary"]["kind"] == "idle"


def test_build_smart_plan_response_adds_budgets_and_closes_conn():
    conn = _Conn([])

    def get_conn():
        return conn

    def build_smart_plan(active_conn):
        assert active_conn is conn
        return {
            "steps": ["sync_raw", "build_external_attention"],
            "skip_reasons": {},
            "reason": ["raw stale"],
        }

    payload = build_smart_plan_response(
        get_conn=get_conn,
        build_smart_plan=build_smart_plan,
        critical_only=True,
    )

    assert conn.closed is True
    assert payload["ok"] is True
    assert payload["plan"]["steps"] == ["sync_raw"]
    assert payload["plan"]["skip_reasons"]["build_external_attention"].startswith(
        "daily critical sync skips"
    )
    assert payload["plan"]["budgets"] == {"sync_raw": 60}
    assert payload["plan"]["estimated_budget_s"] == 60


def test_build_smart_update_plan_closes_conn_after_plan_preflight():
    conn = _Conn([])
    seen = {}

    def get_conn():
        return conn

    async def sync_calendar(active_conn):
        seen["calendar_conn"] = active_conn
        return {"status": "completed"}

    def build_smart_plan(active_conn, **kwargs):
        seen["plan_conn"] = active_conn
        assert kwargs == {"use_cache": False}
        return {"steps": ["sync_raw"], "skip_reasons": {}, "reason": ["raw stale"]}

    result = asyncio.run(
        build_smart_update_plan(
            get_conn=get_conn,
            critical_only=False,
            sync_calendar=sync_calendar,
            build_smart_plan=build_smart_plan,
            ensure_calendar_step_for_data_fetch=lambda steps: ["sync_calendar", *steps],
        )
    )

    assert seen == {"calendar_conn": conn, "plan_conn": conn}
    assert conn.closed is True
    assert result["ok"] is True
    assert result["steps_to_run"] == ["sync_calendar", "sync_raw"]


def test_prepare_smart_update_plan_stops_on_calendar_failure():
    calls = []

    async def sync_calendar(conn):
        calls.append(("calendar", conn))
        return {"status": "failed", "error": "calendar down"}

    def build_smart_plan(*args, **kwargs):
        raise AssertionError("build_smart_plan should not run")

    result = asyncio.run(
        prepare_smart_update_plan(
            "conn",
            critical_only=False,
            sync_calendar=sync_calendar,
            build_smart_plan=build_smart_plan,
            ensure_calendar_step_for_data_fetch=lambda steps: steps,
        )
    )

    assert calls == [("calendar", "conn")]
    assert result == {
        "ok": False,
        "message": "calendar down",
        "calendar_preflight": {"status": "failed", "error": "calendar down"},
        "steps_to_run": [],
    }


def test_prepare_smart_update_plan_reports_noop_plan():
    async def sync_calendar(conn):
        return {"status": "completed"}

    def build_smart_plan(conn, **kwargs):
        assert kwargs == {"use_cache": False}
        return {"steps": [], "skip_reasons": {}, "reason": []}

    result = asyncio.run(
        prepare_smart_update_plan(
            "conn",
            critical_only=False,
            sync_calendar=sync_calendar,
            build_smart_plan=build_smart_plan,
            ensure_calendar_step_for_data_fetch=lambda steps: steps,
        )
    )

    assert result["ok"] is True
    assert result["noop"] is True
    assert result["message"] == "数据已是最新，无需更新"
    assert result["plan"]["steps"] == []
    assert result["steps_to_run"] == []


def test_prepare_smart_update_plan_applies_critical_filter_and_calendar_step():
    async def sync_calendar(conn):
        return {"status": "completed"}

    def build_smart_plan(conn, **kwargs):
        assert kwargs == {"use_cache": False}
        return {
            "steps": ["sync_raw", "build_external_attention"],
            "skip_reasons": {},
            "reason": ["raw stale"],
        }

    def ensure_calendar_step(steps):
        assert steps == ["sync_raw"]
        return ["sync_calendar", *steps]

    result = asyncio.run(
        prepare_smart_update_plan(
            "conn",
            critical_only=True,
            sync_calendar=sync_calendar,
            build_smart_plan=build_smart_plan,
            ensure_calendar_step_for_data_fetch=ensure_calendar_step,
        )
    )

    assert result["ok"] is True
    assert result["noop"] is False
    assert result["steps_to_run"] == ["sync_calendar", "sync_raw"]
    assert result["plan"]["steps"] == ["sync_calendar", "sync_raw"]
    assert result["plan"]["skip_reasons"]["build_external_attention"].startswith(
        "daily critical sync skips"
    )
    assert result["plan"]["critical_only_removed_steps"] == ["build_external_attention"]
    assert result["plan"]["budgets"]["sync_calendar"] == 30
