from __future__ import annotations

from conftest import duck_mem
from services.event_engine import generate_events
from services.holder_availability import (
    backfill_institution_event_notice_sources,
    backfill_inst_holdings_notice_dates,
)


def _seed_notice_inputs(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE inst_holdings (
            institution_id TEXT,
            holder_name TEXT,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            notice_date TEXT,
            hold_amount DOUBLE,
            hold_change TEXT,
            hold_change_num DOUBLE
        );
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            holder_set TEXT,
            holder_name TEXT,
            notice_date TEXT,
            is_secondary_class BOOLEAN,
            is_exit_row BOOLEAN
        );
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            holder_name TEXT,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            notice_date TEXT,
            event_type TEXT,
            hold_amount DOUBLE,
            prev_hold_amount DOUBLE,
            change_amount DOUBLE,
            change_pct DOUBLE,
            created_at TEXT
        );
        INSERT INTO inst_holdings VALUES
            ('inst_a', 'holder_a', '000001', 'A', '20250331', NULL, 100.0, '新进', NULL),
            ('inst_a', 'holder_a', '000001', 'A', '20250630', NULL, 150.0, '加仓', 50.0);
        INSERT INTO fact_top10_holder_period VALUES
            ('000001', 'A', '20250331', 'free', 'holder_a', '20250430', FALSE, FALSE),
            ('000001', 'A', '20250630', 'free', 'holder_a', '20250831', FALSE, FALSE);
        """
    )


def test_backfill_inst_holdings_notice_dates_from_tdx_holder_period():
    with duck_mem() as conn:
        _seed_notice_inputs(conn)

        result = backfill_inst_holdings_notice_dates(conn)
        missing = conn.execute(
            "SELECT COUNT(*) AS n FROM inst_holdings WHERE notice_date IS NULL OR notice_date = ''"
        ).fetchone()["n"]

        assert result["updated_rows"] == 2
        assert missing == 0


def test_generate_events_falls_back_to_tdx_holder_notice_date():
    with duck_mem() as conn:
        _seed_notice_inputs(conn)

        count = generate_events(conn)
        notices = [
            row["notice_date"]
            for row in conn.execute("SELECT notice_date FROM fact_institution_event ORDER BY report_date").fetchall()
        ]

        assert count == 2
        assert notices == ["20250430", "20250831"]


def test_generate_events_preserves_notice_source_lineage():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE inst_holdings (
                institution_id TEXT,
                holder_name TEXT,
                stock_code TEXT,
                stock_name TEXT,
                report_date TEXT,
                notice_date TEXT,
                notice_date_source TEXT,
                source_notice_date TEXT,
                availability_deadline TEXT,
                hold_amount DOUBLE,
                hold_change TEXT,
                hold_change_num DOUBLE
            );
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                stock_name TEXT,
                report_date TEXT,
                holder_set TEXT,
                holder_name TEXT,
                notice_date TEXT,
                availability_source TEXT,
                is_secondary_class BOOLEAN,
                is_exit_row BOOLEAN
            );
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                holder_name TEXT,
                stock_code TEXT,
                stock_name TEXT,
                report_date TEXT,
                notice_date TEXT,
                notice_date_source TEXT,
                source_notice_date TEXT,
                availability_deadline TEXT,
                event_type TEXT,
                hold_amount DOUBLE,
                prev_hold_amount DOUBLE,
                change_amount DOUBLE,
                change_pct DOUBLE,
                created_at TEXT
            );
            INSERT INTO inst_holdings VALUES
                ('inst_a', 'holder_a', '000001', 'A', '20250331', NULL, NULL, NULL, NULL, 100.0, '新进', NULL),
                ('inst_a', 'holder_a', '000001', 'A', '20250630', NULL, NULL, NULL, NULL, 150.0, '加仓', 50.0);
            INSERT INTO fact_top10_holder_period VALUES
                ('000001', 'A', '20250331', 'free', 'holder_a', '20250428', 'regulatory_deadline', FALSE, FALSE),
                ('000001', 'A', '20250331', 'free', 'holder_a', '20250425', 'source_notice', FALSE, FALSE),
                ('000001', 'A', '20250630', 'free', 'holder_a', '20250831', 'regulatory_deadline', FALSE, FALSE);
            """
        )

        count = generate_events(conn)
        rows = conn.execute(
            """
            SELECT report_date, notice_date, notice_date_source,
                   source_notice_date, availability_deadline
              FROM fact_institution_event
             ORDER BY report_date
            """
        ).fetchall()

        assert count == 2
        assert [dict(row) for row in rows] == [
            {
                "report_date": "20250331",
                "notice_date": "20250425",
                "notice_date_source": "source_notice",
                "source_notice_date": "20250425",
                "availability_deadline": None,
            },
            {
                "report_date": "20250630",
                "notice_date": "20250831",
                "notice_date_source": "regulatory_deadline",
                "source_notice_date": None,
                "availability_deadline": "20250831",
            },
        ]


def test_backfill_institution_event_notice_sources_without_rebuilding_returns():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                holder_set TEXT,
                holder_name TEXT,
                notice_date TEXT,
                availability_source TEXT,
                is_secondary_class BOOLEAN,
                is_exit_row BOOLEAN
            );
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                holder_name TEXT,
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                notice_date_source TEXT,
                source_notice_date TEXT,
                availability_deadline TEXT,
                event_type TEXT,
                gain_60d DOUBLE
            );
            INSERT INTO fact_top10_holder_period VALUES
                ('000001', '20250331', 'free', 'holder_a', '20250430', 'regulatory_deadline', FALSE, FALSE);
            INSERT INTO fact_institution_event VALUES
                ('inst_a', 'holder_a', '000001', '20250331', '20250430', NULL, NULL, NULL, 'new_entry', 12.3);
            """
        )

        result = backfill_institution_event_notice_sources(conn)
        row = conn.execute(
            """
            SELECT notice_date_source, source_notice_date,
                   availability_deadline, gain_60d
              FROM fact_institution_event
            """
        ).fetchone()

        assert result["status"] == "ok"
        assert row["notice_date_source"] == "regulatory_deadline"
        assert row["source_notice_date"] is None
        assert row["availability_deadline"] == "20250430"
        assert row["gain_60d"] == 12.3
