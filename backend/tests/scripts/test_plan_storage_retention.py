from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "plan_storage_retention.py"
SPEC = importlib.util.spec_from_file_location("plan_storage_retention", SCRIPT_PATH)
plan_storage_retention = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = plan_storage_retention
SPEC.loader.exec_module(plan_storage_retention)


class FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_open_retention_connection_uses_read_only_for_dry_run(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, bool]] = []
    fake_conn = FakeConn()
    db_path = tmp_path / "smartmoney.duckdb"

    monkeypatch.setattr(plan_storage_retention, "current_db_paths", lambda: (tmp_path, db_path))
    monkeypatch.setattr(
        plan_storage_retention,
        "duck_connect",
        lambda path, read_only=False: calls.append((path, read_only)) or fake_conn,
    )
    monkeypatch.setattr(
        plan_storage_retention,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("dry-run should not open write get_conn")),
    )

    with plan_storage_retention.open_retention_connection(read_only=True) as conn:
        assert conn is fake_conn

    assert calls == [(str(db_path), True)]
    assert fake_conn.closed is True


def test_open_retention_connection_uses_write_conn_for_execute(monkeypatch) -> None:
    fake_conn = FakeConn()

    monkeypatch.setattr(plan_storage_retention, "get_conn", lambda: fake_conn)
    monkeypatch.setattr(
        plan_storage_retention,
        "duck_connect",
        lambda path, read_only=False: (_ for _ in ()).throw(AssertionError("execute should use get_conn")),
    )

    with plan_storage_retention.open_retention_connection(read_only=False) as conn:
        assert conn is fake_conn

    assert fake_conn.closed is True
