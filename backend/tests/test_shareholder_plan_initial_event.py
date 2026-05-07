from __future__ import annotations

from conftest import duck_mem
from services.shareholder_plan_initial_event import (
    MART_TABLE,
    build_shareholder_plan_initial_event,
)


def _seed_source(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE fact_shareholder_plan_tdx_f10 (
            stock_code TEXT,
            stock_name TEXT,
            source_notice_date TEXT,
            source_available_date TEXT,
            source_date_quality TEXT,
            subject TEXT,
            direction TEXT,
            start_date TEXT,
            end_date TEXT,
            target_shares_min BIGINT,
            target_shares BIGINT,
            target_ratio DOUBLE,
            target_amount_min BIGINT,
            target_amount_max BIGINT,
            trade_method TEXT,
            reason TEXT,
            first_announce_date TEXT,
            announce_date TEXT,
            latest_announce_date TEXT,
            progress TEXT,
            page_update_date TEXT,
            source TEXT,
            source_tier SMALLINT,
            raw_hash TEXT,
            row_seq INTEGER
        );
        INSERT INTO fact_shareholder_plan_tdx_f10 VALUES
            ('000001', '测试股票A', '20250105', '20250201',
             'parsed_latest_announce_date', '股东甲', 'increase',
             '20250210', '20250810', 100, 200, 1.2, 1000, 2000,
             '集中竞价', '看好公司', '20250105', NULL, '20250120',
             '进行中', '20250121', 'tdx_f10', 1, 'hash_old', 1),
            ('000001', '测试股票A', '20250105', '20250301',
             'parsed_latest_announce_date', '股东甲', 'increase',
             '20250210', '20250810', 100, 200, 1.2, 1000, 2000,
             '集中竞价', '看好公司', '20250105', NULL, '20250215',
             '完成', '20250216', 'tdx_f10', 1, 'hash_new', 2),
            ('000002', '测试股票B', '20250110', '20250110',
             'parsed_latest_announce_date', '股东乙', 'decrease',
             '20250211', '20250811', NULL, 300, 2.5, NULL, NULL,
             '大宗交易', '资金需要', NULL, '20250110', '20250110',
             '进行中', '20250111', 'tdx_f10', 1, 'hash_b', 1),
            ('000003', '测试股票C', '20990101', '20990101',
             'parsed_latest_announce_date', '股东丙', 'increase',
             '20990201', '20990801', NULL, 400, 3.0, NULL, NULL,
             '集中竞价', '测试未来日期', '20990101', NULL, '20990101',
             '进行中', '20990102', 'tdx_f10', 1, 'hash_future', 1);
        """
    )


def test_build_shareholder_plan_initial_event_dedupes_to_initial_notice_grain() -> None:
    with duck_mem() as conn:
        _seed_source(conn)

        result = build_shareholder_plan_initial_event(conn)

        assert result["status"] == "completed"
        assert result["source_rows"] == 4
        assert result["inserted_rows"] == 2
        assert result["future_source_notice_rows"] == 1
        assert result["duplicate_dropped_rows"] == 1

        rows = conn.execute(
            f"""
            SELECT stock_code, source_notice_date, source_available_date,
                   source_date_quality, source_row_grain, latest_progress,
                   latest_state_available_date, raw_hash
              FROM {MART_TABLE}
             ORDER BY stock_code
            """
        ).fetchall()

        assert [row["stock_code"] for row in rows] == ["000001", "000002"]
        assert rows[0]["source_notice_date"] == "2025-01-05"
        assert rows[0]["source_available_date"] == "2025-01-05"
        assert rows[0]["source_date_quality"] == "parsed_first_announce_date_initial_event"
        assert rows[0]["source_row_grain"] == "initial_shareholder_plan_notice"
        assert rows[0]["latest_progress"] == "完成"
        assert rows[0]["latest_state_available_date"] == "2025-03-01"
        assert rows[0]["raw_hash"] == "hash_new"
        assert rows[1]["source_notice_date"] == "2025-01-10"
        assert rows[1]["source_date_quality"] == "parsed_announce_date_initial_event"


def test_build_shareholder_plan_initial_event_handles_missing_source_table() -> None:
    with duck_mem() as conn:
        result = build_shareholder_plan_initial_event(conn)

        assert result["status"] == "missing_source"
        assert result["inserted_rows"] == 0
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {MART_TABLE}").fetchone()
        assert row["n"] == 0
