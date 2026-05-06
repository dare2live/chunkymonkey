import asyncio

import pytest

from routers import updater


pytestmark = pytest.mark.contract


def test_all_data_fetch_steps_require_calendar_hard_dependency():
    missing = sorted(
        step
        for step in updater.CALENDAR_DATA_FETCH_STEPS
        if "sync_calendar" not in updater.HARD_DEPS.get(step, [])
    )

    assert missing == []


def test_calendar_preflight_is_inserted_for_every_single_fetch_step():
    for step in sorted(updater.CALENDAR_DATA_FETCH_STEPS):
        step_ids = updater._ensure_calendar_step_for_data_fetch([step])

        assert step_ids[0] == "sync_calendar"
        assert step_ids.count("sync_calendar") == 1
        assert step_ids[1] == step


def test_smart_update_stops_before_plan_when_calendar_unavailable(monkeypatch):
    class FakeConn:
        def close(self):
            pass

    async def failed_calendar_preflight(conn):
        return {
            "status": "failed",
            "error": "calendar unavailable",
            "calendar": {"needs_refresh": True},
        }

    def build_smart_plan_should_not_run(*args, **kwargs):
        raise AssertionError("build_smart_plan must not run before calendar preflight succeeds")

    import services.audit as audit

    monkeypatch.setattr(updater, "_is_running", False)
    monkeypatch.setattr(updater, "_step_sync_calendar", failed_calendar_preflight)
    monkeypatch.setattr(updater, "get_conn", lambda *args, **kwargs: FakeConn())
    monkeypatch.setattr(audit, "build_smart_plan", build_smart_plan_should_not_run)

    result = asyncio.run(updater.smart_update())

    assert result["ok"] is False
    assert result["message"] == "calendar unavailable"
    assert result["calendar_preflight"]["calendar"]["needs_refresh"] is True
    assert updater._is_running is False
