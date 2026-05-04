import sys
import threading
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ingest_holders_tdxhub as ingest  # noqa: E402
from services.holders_resolver import ResolverResult  # noqa: E402


def _make_conn():
    con = duckdb.connect(":memory:")
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
    return con


def _make_result():
    fetched_at = "2026-05-05T01:30:00"
    return ResolverResult(
        holders=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "report_date": "20260331",
                "holder_set": "free",
                "holder_rank": 1,
                "row_seq": 1,
                "holder_name": "Holder A",
                "share_class": "A",
                "is_secondary_class": False,
                "is_exit_row": False,
                "shares_text": "1000股",
                "shares_approx": 1000,
                "shares_precision": "股",
                "hold_ratio": 1.5,
                "holder_type_or_nature": "机构",
                "change_status": "增持",
                "change_shares_text": "100股",
                "change_shares_approx": 100,
                "page_update_date": "2026-05-04",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
        periods=[{"report_date": "20260331"}],
        raw_text="灵通V9.0 holder fixture",
        raw_hash="abc123",
        page_update_date="2026-05-04",
        server_or_endpoint="fixture:7709",
        source="tdx_f10",
        source_tier=1,
        fetched_at=fetched_at,
        controlling={
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "SH",
            "primary_shareholder_label": "控股股东",
            "primary_shareholder_name": "Holder A",
            "primary_shareholder_ratio": 1.5,
            "primary_shareholder_raw": "Holder A 1.5%",
            "source": "tdx_f10",
            "raw_hash": "abc123",
            "fetched_at": fetched_at,
        },
        plans=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "announce_date": "20260501",
                "subject": "Holder A",
                "direction": "增持计划",
                "progress": "实施",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
        trades=[
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "SH",
                "change_date": "20260502",
                "holder_name": "Holder A",
                "shares_change": 100,
                "change_type": "二级市场买入",
                "source": "tdx_f10",
                "raw_hash": "abc123",
                "fetched_at": fetched_at,
            }
        ],
    )


def test_write_one_persists_records_and_is_idempotent():
    con = _make_conn()
    lock = threading.Lock()
    try:
        result = _make_result()
        stats = ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )

        assert stats["n_holders"] == 1
        holder = con.execute(
            "SELECT holder_name_norm, hold_ratio_float, hold_change, hold_change_num "
            "FROM fact_top10_holder_period"
        ).fetchone()
        assert holder == ("Holder Canon", 1.5, "加仓", 100.0)
        assert con.execute("SELECT COUNT(*) FROM raw_tdx_f10_holder_research").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_controlling_shareholder").fetchone()[0] == 1
        assert con.execute("SELECT plan_seq FROM fact_shareholder_plan").fetchone()[0] == 1
        assert con.execute("SELECT holder_name_norm, trade_seq FROM fact_shareholder_trade").fetchone() == (
            "Holder Canon",
            1,
        )

        ingest.write_one(
            con,
            stock_code="600519",
            stock_name="贵州茅台",
            market="SH",
            result=result,
            alias_map={"Holder A": "Holder Canon"},
            lock=lock,
        )
        assert con.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_plan").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM fact_shareholder_trade").fetchone()[0] == 1
    finally:
        con.close()
