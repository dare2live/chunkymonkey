import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import migrate_holders_to_tdxhub as subject


def _create_source(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE raw_text (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                fetched_at TEXT,
                raw_len INTEGER,
                raw_hash TEXT,
                server TEXT,
                raw_text TEXT
            );
            CREATE TABLE holders (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                report_date TEXT,
                holder_set TEXT,
                holder_rank INTEGER,
                row_seq INTEGER,
                holder_name TEXT,
                share_class TEXT,
                shares_text TEXT,
                shares_approx BIGINT,
                shares_precision TEXT,
                hold_ratio DOUBLE,
                holder_type_or_nature TEXT,
                change_status TEXT,
                change_shares_text TEXT,
                change_shares_approx BIGINT,
                is_exit_row BOOLEAN,
                is_secondary_class BOOLEAN,
                page_update_date TEXT,
                source TEXT,
                raw_hash TEXT,
                fetched_at TEXT
            );
            CREATE TABLE controlling (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                primary_shareholder_label TEXT,
                primary_shareholder_name TEXT,
                primary_shareholder_ratio DOUBLE,
                primary_shareholder_raw TEXT,
                actual_controller_name TEXT,
                actual_controller_ratio DOUBLE,
                actual_controller_raw TEXT,
                page_update_date TEXT,
                source TEXT,
                raw_hash TEXT,
                fetched_at TEXT
            );
            CREATE TABLE plans (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                announce_date TEXT,
                subject TEXT,
                direction TEXT,
                progress TEXT,
                start_date TEXT,
                end_date TEXT,
                target_shares_text TEXT,
                target_shares BIGINT,
                target_ratio_text TEXT,
                target_ratio DOUBLE,
                reason TEXT,
                narrative TEXT,
                page_update_date TEXT,
                source TEXT,
                raw_hash TEXT,
                fetched_at TEXT
            );
            CREATE TABLE trades (
                stock_code TEXT,
                stock_name TEXT,
                market TEXT,
                change_date TEXT,
                holder_name TEXT,
                shares_before_text TEXT,
                shares_before BIGINT,
                shares_change_text TEXT,
                shares_change BIGINT,
                shares_after_text TEXT,
                shares_after BIGINT,
                ratio_after DOUBLE,
                change_type TEXT,
                page_update_date TEXT,
                source TEXT,
                raw_hash TEXT,
                fetched_at TEXT
            );
            """
        )
        fetched_at = "2026-05-05T01:30:00"
        con.execute(
            "INSERT INTO raw_text VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("600519", "贵州茅台", "SH", fetched_at, 20, "hash1", "server-a", "灵通V9.0 raw"),
        )
        con.execute(
            """
            INSERT INTO holders VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "600519", "贵州茅台", "SH", "20260331", "free", 1, 1,
                "Holder A", "A", "1000股", 1000, "股", 1.5, "机构",
                "增持", "100股", 100, False, False, "2026-05-04",
                "tdx_f10", "hash1", fetched_at,
            ),
        )
        con.execute(
            "INSERT INTO controlling VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "600519", "贵州茅台", "SH", "控股股东", "Holder A", 1.5,
                "Holder A 1.5%", None, None, None, "2026-05-04",
                "tdx_f10", "hash1", fetched_at,
            ),
        )
        con.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "600519", "贵州茅台", "SH", "20260501", "Holder A",
                "增持计划", "实施", None, None, None, None, None, None,
                "reason", "narrative", "2026-05-04", "tdx_f10", "hash1", fetched_at,
            ),
        )
        con.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "600519", "贵州茅台", "SH", "20260502", "Holder A",
                None, None, "100股", 100, None, None, 1.6,
                "二级市场买入", "2026-05-04", "tdx_f10", "hash1", fetched_at,
            ),
        )
    finally:
        con.close()


def _create_target(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE raw_tdx_f10_holder_research (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                market TEXT,
                fetched_at TIMESTAMP NOT NULL,
                page_update_date DATE,
                raw_text TEXT NOT NULL,
                raw_hash VARCHAR(64) NOT NULL,
                bytes_len INTEGER,
                server TEXT,
                f10_format TEXT,
                parser_version TEXT DEFAULT 'v1',
                PRIMARY KEY (stock_code, raw_hash)
            );
            CREATE TABLE dim_holder_alias (
                alias TEXT PRIMARY KEY,
                canonical_name TEXT,
                category TEXT,
                note TEXT,
                created_at TEXT
            );
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                market TEXT,
                report_date TEXT NOT NULL,
                holder_set TEXT NOT NULL,
                holder_rank INTEGER NOT NULL,
                row_seq INTEGER NOT NULL DEFAULT 1,
                holder_name TEXT NOT NULL,
                holder_name_norm TEXT,
                share_class TEXT,
                is_secondary_class BOOLEAN DEFAULT FALSE,
                is_exit_row BOOLEAN DEFAULT FALSE,
                shares_text TEXT,
                shares_approx BIGINT,
                shares_precision TEXT,
                hold_amount REAL,
                hold_ratio_float DOUBLE,
                hold_ratio_total DOUBLE,
                hold_ratio REAL,
                hold_market_cap REAL,
                holder_type TEXT,
                share_nature TEXT,
                change_status TEXT,
                change_shares_text TEXT,
                change_shares_approx BIGINT,
                hold_change TEXT,
                hold_change_num REAL,
                notice_date TEXT,
                effective_date TEXT,
                page_update_date TEXT,
                source TEXT NOT NULL,
                source_tier SMALLINT NOT NULL,
                raw_hash TEXT,
                fetched_at TEXT,
                created_at TEXT,
                UNIQUE(stock_code, report_date, holder_set, source, is_exit_row, holder_rank, row_seq, share_class)
            );
            CREATE TABLE fact_controlling_shareholder (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                market TEXT,
                primary_label TEXT,
                primary_name TEXT,
                primary_ratio DOUBLE,
                primary_raw TEXT,
                actual_name TEXT,
                actual_ratio DOUBLE,
                actual_raw TEXT,
                page_update_date TEXT,
                source TEXT NOT NULL,
                source_tier SMALLINT NOT NULL,
                raw_hash TEXT,
                fetched_at TEXT,
                PRIMARY KEY (stock_code, source)
            );
            CREATE TABLE fact_shareholder_plan (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                market TEXT,
                announce_date TEXT,
                subject TEXT,
                direction TEXT,
                progress TEXT,
                start_date TEXT,
                end_date TEXT,
                target_shares_text TEXT,
                target_shares BIGINT,
                target_ratio_text TEXT,
                target_ratio DOUBLE,
                reason TEXT,
                narrative TEXT,
                page_update_date TEXT,
                source TEXT NOT NULL,
                source_tier SMALLINT NOT NULL,
                raw_hash TEXT,
                fetched_at TEXT,
                plan_seq INTEGER
            );
            CREATE TABLE fact_shareholder_trade (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                market TEXT,
                change_date TEXT,
                holder_name TEXT,
                holder_name_norm TEXT,
                shares_before_text TEXT,
                shares_before BIGINT,
                shares_change_text TEXT,
                shares_change BIGINT,
                shares_after_text TEXT,
                shares_after BIGINT,
                ratio_after DOUBLE,
                change_type TEXT,
                page_update_date TEXT,
                source TEXT NOT NULL,
                source_tier SMALLINT NOT NULL,
                raw_hash TEXT,
                fetched_at TEXT,
                trade_seq INTEGER
            );
            """
        )
        con.execute(
            "INSERT INTO dim_holder_alias VALUES (?, ?, ?, ?, ?)",
            ("Holder A", "Holder Canon", "test", None, "2026-05-05"),
        )
    finally:
        con.close()


def test_migrate_holders_uses_direct_sql_and_is_idempotent(monkeypatch, tmp_path):
    source = tmp_path / "source.duckdb"
    target = tmp_path / "target.duckdb"
    _create_source(source)
    _create_target(target)
    monkeypatch.setattr(subject, "init_db", lambda: None)

    counts = subject.run_migration(str(source), str(target))
    second = subject.run_migration(str(source), str(target))

    con = duckdb.connect(str(target), read_only=True)
    try:
        holder = con.execute(
            """
            SELECT holder_name_norm, hold_ratio_float, hold_change, hold_change_num
            FROM fact_top10_holder_period
            """
        ).fetchone()
        trade = con.execute(
            "SELECT holder_name_norm, trade_seq FROM fact_shareholder_trade"
        ).fetchone()

        assert counts["raw"] == 1
        assert counts["holders"] == 1
        assert counts["plans"] == 1
        assert counts["trades"] == 1
        assert second["raw"] == 0
        assert second["holders"] == 0
        assert holder == ("Holder Canon", 1.5, "加仓", 100.0)
        assert trade == ("Holder Canon", 1)
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_plan").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_controlling_shareholder").fetchone()[0] == 1
    finally:
        con.close()
