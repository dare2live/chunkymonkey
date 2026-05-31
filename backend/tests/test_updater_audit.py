import services.audit as audit_service
import routers.updater_audit as updater_audit
from routers.updater_audit import (
    _is_audit_snapshot_refreshing,
    _refresh_holder_audit_snapshot_sync,
    _schedule_holder_audit_snapshot_refresh,
    build_update_audit_payload,
)


class _Conn:
    def __init__(self, events):
        self.events = events

    def close(self):
        self.events.append("close")


class _Task:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


def test_refresh_holder_audit_snapshot_sync_closes_connection(monkeypatch):
    events = []

    def fake_get_conn(timeout):
        events.append(("get_conn", timeout))
        return _Conn(events)

    def fake_refresh(conn, *, source):
        events.append(("refresh", isinstance(conn, _Conn), source))

    monkeypatch.setattr(updater_audit, "get_conn", fake_get_conn)
    monkeypatch.setattr(audit_service, "refresh_quality_audit_snapshot", fake_refresh)

    _refresh_holder_audit_snapshot_sync("unit")

    assert events == [("get_conn", 120), ("refresh", True, "unit"), "close"]


def test_audit_snapshot_refreshing_reflects_task_state(monkeypatch):
    monkeypatch.setattr(updater_audit, "_audit_snapshot_refresh_task", None)
    assert not _is_audit_snapshot_refreshing()

    monkeypatch.setattr(updater_audit, "_audit_snapshot_refresh_task", _Task(done=False))
    assert _is_audit_snapshot_refreshing()

    monkeypatch.setattr(updater_audit, "_audit_snapshot_refresh_task", _Task(done=True))
    assert not _is_audit_snapshot_refreshing()


def test_schedule_holder_audit_snapshot_refresh_skips_duplicate_running_task(monkeypatch):
    created = []

    def fake_create_task(coro):
        coro.close()
        created.append("task")
        return _Task(done=False)

    monkeypatch.setattr(updater_audit.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(updater_audit, "_audit_snapshot_refresh_task", None)

    _schedule_holder_audit_snapshot_refresh("first")
    _schedule_holder_audit_snapshot_refresh("second")

    assert created == ["task"]


def test_build_update_audit_payload_preserves_route_shape_and_closes_connection(monkeypatch):
    events = []

    def fake_get_conn():
        events.append("get_conn")
        return _Conn(events)

    def fake_get_quality_audit(conn, *, force):
        events.append(("audit", isinstance(conn, _Conn), force))
        return {"ok": True}

    monkeypatch.setattr(updater_audit, "get_conn", fake_get_conn)
    monkeypatch.setattr(audit_service, "get_quality_audit", fake_get_quality_audit)
    monkeypatch.setattr(updater_audit, "_audit_snapshot_refresh_task", _Task(done=False))

    payload = build_update_audit_payload(force=True)

    assert payload == {"ok": True, "snapshot_refreshing": True}
    assert events == ["get_conn", ("audit", True, True), "close"]
