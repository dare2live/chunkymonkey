from __future__ import annotations

from services.duck_adapter import connect
from services.gap_queue import mark_gap_failed, mark_gap_resolved, upsert_gap_state


def _make_conn():
    conn = connect(":memory:")
    conn.execute(
        """
        CREATE TABLE market_gap_queue (
            dataset         TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            stock_name      TEXT,
            status          TEXT DEFAULT 'pending',
            reason          TEXT,
            last_error      TEXT,
            source_attempts INTEGER DEFAULT 0,
            first_seen_at   TEXT,
            last_attempt_at TEXT,
            resolved_at     TEXT,
            updated_at      TEXT,
            PRIMARY KEY (dataset, stock_code)
        )
        """
    )
    return conn


def test_gap_queue_upsert_is_idempotent_and_preserves_first_seen():
    conn = _make_conn()
    try:
        upsert_gap_state(
            conn,
            "monthly_kline",
            "600825",
            stock_name="贵州茅台",
            status="pending",
            reason="awaiting backfill",
            commit=False,
        )
        first = conn.execute(
            """
            SELECT stock_name, status, reason, source_attempts,
                   first_seen_at, last_attempt_at, resolved_at
              FROM market_gap_queue
             WHERE dataset = 'monthly_kline' AND stock_code = '600825'
            """
        ).fetchone()
        assert first[0] == "贵州茅台"
        assert first[1] == "pending"
        assert first[2] == "awaiting backfill"
        assert first[3] == 0
        assert first[4]
        assert first[5] is None
        assert first[6] is None

        mark_gap_failed(
            conn,
            "monthly_kline",
            "600825",
            stock_name="贵州茅台(更新)",
            last_error="timeout",
            commit=False,
        )
        second = conn.execute(
            """
            SELECT stock_name, status, reason, last_error, source_attempts,
                   first_seen_at, last_attempt_at, resolved_at
              FROM market_gap_queue
             WHERE dataset = 'monthly_kline' AND stock_code = '600825'
            """
        ).fetchone()
        assert second[0] == "贵州茅台(更新)"
        assert second[1] == "retrying"
        assert second[2] == "在线补数失败，等待后续重试"
        assert second[3] == "timeout"
        assert second[4] == 1
        assert second[5] == first[4]
        assert second[6]
        assert second[7] is None

        mark_gap_resolved(
            conn,
            "monthly_kline",
            "600825",
            reason="已补齐",
            commit=False,
        )
        third = conn.execute(
            """
            SELECT status, reason, last_error, source_attempts,
                   first_seen_at, last_attempt_at, resolved_at
              FROM market_gap_queue
             WHERE dataset = 'monthly_kline' AND stock_code = '600825'
            """
        ).fetchone()
        assert third[0] == "resolved"
        assert third[1] == "已补齐"
        assert third[2] is None
        assert third[3] == 1
        assert third[4] == first[4]
        assert third[5] == second[6]
        assert third[6]
        assert conn.execute("SELECT COUNT(*) FROM market_gap_queue").fetchone()[0] == 1
    finally:
        conn.close()
