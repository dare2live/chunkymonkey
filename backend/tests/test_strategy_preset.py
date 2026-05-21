from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import strategy_preset


class _Rows:
    def __init__(self, one):
        self._one = one

    def fetchone(self):
        return self._one


class _PresetConn:
    def __init__(self):
        self.executed = []
        self.seed_rows = None
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT COUNT(*) FROM dim_strategy_preset" in sql:
            return _Rows((0,))
        return _Rows(None)

    def executemany(self, sql, rows):
        self.seed_sql = sql
        self.seed_rows = rows

    def close(self):
        self.closed = True


def test_ensure_table_batches_default_preset_seed(monkeypatch):
    conn = _PresetConn()
    monkeypatch.setattr(strategy_preset, "get_conn", lambda: conn)

    strategy_preset._ensure_table()

    assert len(conn.seed_rows) == 3
    assert [row[0] for row in conn.seed_rows] == ["稳健型", "激进型", "试验型"]
    assert [row[2] for row in conn.seed_rows] == [True, False, False]
    assert json.loads(conn.seed_rows[0][1])["backtest"]["rebalance"] == "weekly"
    assert "INSERT INTO dim_strategy_preset" in conn.seed_sql
    assert conn.closed is True
