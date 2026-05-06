from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.tdx_f10_extra_client as extra_client
from services.tdx_f10_extra_client import (
    backfill_tdx_f10_shareholder_plans,
    build_tdx_f10_capability_matrix,
    _insert_fund_holding_rows,
    ensure_tables,
    sync_tdx_f10_extra_facts,
)


FIXTURE = (
    Path(__file__).resolve().parents[2].parent
    / "tdxhub"
    / "tests"
    / "fixtures"
    / "holders_b"
    / "sh_main_moutai_600519.txt"
)
FIXTURE_ICBC = (
    Path(__file__).resolve().parents[2].parent
    / "tdxhub"
    / "tests"
    / "fixtures"
    / "holders_b"
    / "sh_a_h_icbc_601398.txt"
)

FUND_FIXTURE_TEXT = """股东研究☆ ◇600519 贵州茅台 更新日期：2026-04-28◇ 通达信沪深京F10
【7.基金持股】截止日期：2025-12-31
┌────────────────────┬──────┬───────┬───────┐
│基金名称                                │持股数(股)│占流通A股比(%)│持股市值(元)│
├────────────────────┼──────┼───────┼───────┤
│中国工商银行股份有限公司－华泰柏瑞沪深300│456.64万  │0.36          │66.22亿     │
│交易型开放式指数证券投资基金            │          │              │            │
│国泰基金管理有限公司                    │12.34万   │0.01          │1.79亿      │
└────────────────────┴──────┴───────┴───────┘
"""


def _create_raw_table(conn):
    conn.execute(
        """
        CREATE TABLE raw_tdx_f10_holder_research (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            fetched_at TIMESTAMP,
            page_update_date TEXT,
            raw_text TEXT NOT NULL,
            raw_hash TEXT NOT NULL,
            bytes_len INTEGER,
            server TEXT,
            f10_format TEXT,
            parser_version TEXT,
            PRIMARY KEY (stock_code, raw_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_holder_alias (
            alias TEXT PRIMARY KEY,
            canonical_name TEXT
        )
        """
    )


def test_sync_tdx_f10_extra_facts_lands_format_b_sections():
    conn = duck_mem()
    try:
        _create_raw_table(conn)
        text = FIXTURE.read_text(encoding="utf-8")
        icbc_text = FIXTURE_ICBC.read_text(encoding="utf-8")
        rows = [
            ("600519", "贵州茅台", "SH", "2026-04-28", text, "fixture_hash", len(text), "b_shsjz"),
            ("601398", "工商银行", "SH", "2026-04-28", icbc_text, "icbc_hash", len(icbc_text), "b_shsjz"),
            ("600519", "贵州茅台", "SH", "2026-04-28", FUND_FIXTURE_TEXT, "fund_hash", len(FUND_FIXTURE_TEXT), "b_shsjz"),
        ]
        conn.executemany(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date,
             raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
             VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], "fixture", r[7], "v1") for r in rows],
        )

        result = sync_tdx_f10_extra_facts(conn)

        holder_row = conn.execute(
            """
            SELECT holder_count, holder_count_change_pct, avg_float_shares_change_pct
            FROM fact_holder_count_period
            WHERE stock_code = '600519' AND report_date = '2026-03-31'
            """
        ).fetchone()
        trade_row = conn.execute(
            """
            SELECT holder_name, shares_change, average_price, change_method
            FROM fact_shareholder_trade_tdx_b
            WHERE stock_code = '600519'
            ORDER BY trade_seq
            LIMIT 1
            """
        ).fetchone()
        plan_row = conn.execute(
            """
            SELECT subject, direction, progress, latest_announce_date,
                   first_announce_date, source_notice_date, source_available_date,
                   source_date_quality, target_amount_min, target_amount_max
            FROM fact_shareholder_plan_tdx_f10
            WHERE stock_code = '600519'
            ORDER BY source_available_date DESC
            LIMIT 1
            """
        ).fetchone()
        ctrl_row = conn.execute(
            """
            SELECT primary_name, actual_name, control_chain_text
            FROM fact_controlling_shareholder
            WHERE stock_code = '600519'
            """
        ).fetchone()
        common_row = conn.execute(
            """
            SELECT major_holder_name, peer_stock_code, shares, hold_ratio_text,
                   change_text, change_shares, net_profit_parent_text, net_profit_parent
            FROM fact_common_major_holder_stock
            WHERE stock_code = '601398' AND peer_stock_code = '601988'
              AND major_holder_name = '中央汇金投资有限责任公司'
            """
        ).fetchone()
        fund_row = conn.execute(
            """
            SELECT fund_name, shares, float_a_ratio_text, market_value_text, market_value
            FROM fact_fund_holding_tdx_f10
            WHERE raw_hash = 'fund_hash'
            ORDER BY row_seq
            LIMIT 1
            """
        ).fetchone()
        status_count = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_tdx_f10_extra_parse_status WHERE status = 'completed'"
        ).fetchone()["n"]
        second = sync_tdx_f10_extra_facts(conn)

        assert result["status"] == "completed"
        assert result["capability_matrix"]["capability_rows"] >= 7
        assert result["raw_rows"] == 3
        assert result["holder_count_rows"] >= 60
        assert result["trade_b_rows"] == 3
        assert result["shareholder_plan_rows"] == 1
        assert result["control_rows"] == 2
        assert result["common_major_holder_rows"] == 31
        assert result["fund_holding_rows"] == 2
        assert result["fund_holding_rejected_rows"] == 0
        assert holder_row["holder_count"] == 243_159
        assert holder_row["holder_count_change_pct"] == -4.98
        assert holder_row["avg_float_shares_change_pct"] == 5.24
        assert trade_row["holder_name"] == "中国贵州茅台酒厂（集团）有限责任公司"
        assert trade_row["shares_change"] == 1_274_200
        assert trade_row["average_price"] == 1443.14
        assert trade_row["change_method"] == "二级市场买卖"
        assert plan_row["subject"] == "中国贵州茅台酒厂（集团）有限责任公司"
        assert plan_row["direction"] == "增持计划"
        assert plan_row["progress"] == "完成"
        assert plan_row["latest_announce_date"] == "2025-12-30"
        assert plan_row["first_announce_date"] == "2025-08-30"
        assert plan_row["source_notice_date"] == "2025-12-30"
        assert plan_row["source_available_date"] == "2025-12-30"
        assert plan_row["source_date_quality"] == "parsed_latest_announce_date"
        assert plan_row["target_amount_min"] == 3_000_000_000
        assert plan_row["target_amount_max"] == 3_300_000_000
        assert "→90%中国贵州茅台酒厂" in ctrl_row["control_chain_text"]
        assert common_row["shares"] == 188_792_000_000
        assert common_row["hold_ratio_text"] == "58.59"
        assert common_row["change_text"] == "不变"
        assert common_row["change_shares"] == 0
        assert common_row["net_profit_parent_text"] == "2430.21亿"
        assert common_row["net_profit_parent"] == 243_021_000_000
        assert fund_row["fund_name"] == "中国工商银行股份有限公司－华泰柏瑞沪深300交易型开放式指数证券投资基金"
        assert fund_row["shares"] == 4_566_400
        assert fund_row["float_a_ratio_text"] == "0.36"
        assert fund_row["market_value_text"] == "66.22亿"
        assert fund_row["market_value"] == 6_622_000_000
        assert status_count == 3
        assert second["raw_rows"] == 0
    finally:
        conn.close()


def test_build_tdx_f10_capability_matrix_records_raw_and_fact_coverage():
    conn = duck_mem()
    try:
        _create_raw_table(conn)
        ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date,
             raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
             VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("600519", "贵州茅台", "SH", "2026-04-28", "raw text", "hash_1", 8, "fixture", "b_shsjz", "v1"),
        )
        conn.execute(
            """
            INSERT INTO fact_holder_count_period
            (stock_code, stock_name, market, report_date, holder_count,
             holder_count_change, holder_count_change_pct, avg_float_shares,
             avg_float_shares_change_pct, close_price, page_update_date,
             source, source_tier, raw_hash, fetched_at, updated_at)
            VALUES
            ('600519', '贵州茅台', 'SH', '2026-03-31', 100, 1, 1.0, 1000,
             0.1, 100.0, '2026-04-28', 'tdx_f10', 1, 'hash_1',
             '2026-04-28T10:00:00', '2026-04-28T10:00:00')
            """
        )

        result = build_tdx_f10_capability_matrix(conn)
        row = conn.execute(
            """
            SELECT status, coverage_stock_count, row_count, source_date_field,
                   availability_date_field
              FROM mart_tdx_f10_capability_matrix
             WHERE module_id = 'holder_count_history'
            """
        ).fetchone()
        plan_cap = conn.execute(
            """
            SELECT status, source_date_field, availability_date_field
              FROM mart_tdx_f10_capability_matrix
             WHERE module_id = 'shareholder_plan_tdx_f10'
            """
        ).fetchone()

        assert result["capability_rows"] >= 7
        assert row["status"] == "ready"
        assert row["coverage_stock_count"] == 1
        assert row["row_count"] == 1
        assert row["source_date_field"] == "page_update_date"
        assert row["availability_date_field"] == "fetched_at"
        assert plan_cap["status"] == "raw_only"
        assert plan_cap["source_date_field"] == "source_notice_date"
        assert plan_cap["availability_date_field"] == "source_available_date"
    finally:
        conn.close()


def test_backfill_tdx_f10_shareholder_plans_scans_only_plan_raw_rows():
    conn = duck_mem()
    try:
        _create_raw_table(conn)
        ensure_tables(conn)
        text = FIXTURE.read_text(encoding="utf-8")
        no_plan = "股东研究☆ ◇601398 工商银行 更新日期：2026-04-28◇ 通达信沪深京F10\n【2.股东增减持计划】 暂无数据\n"
        conn.executemany(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date,
             raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
             VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("600519", "贵州茅台", "SH", "2026-04-28", text, "plan_hash", len(text), "fixture", "b_shsjz", "v1"),
                ("601398", "工商银行", "SH", "2026-04-28", no_plan, "no_plan_hash", len(no_plan), "fixture", "b_shsjz", "v1"),
            ],
        )

        result = backfill_tdx_f10_shareholder_plans(conn)
        second = backfill_tdx_f10_shareholder_plans(conn)
        row = conn.execute(
            """
            SELECT source_available_date, source_date_quality
              FROM fact_shareholder_plan_tdx_f10
             WHERE stock_code = '600519'
            """
        ).fetchone()

        assert result["raw_rows"] == 1
        assert result["shareholder_plan_rows"] == 1
        assert second["raw_rows"] == 0
        assert row["source_available_date"] == "2025-12-30"
        assert row["source_date_quality"] == "parsed_latest_announce_date"
    finally:
        conn.close()


def test_insert_fund_holding_rows_rejects_disclaimer_and_missing_values():
    conn = duck_mem()
    try:
        ensure_tables(conn)
        inserted, rejected = _insert_fund_holding_rows(
            conn,
            [
                {
                    "stock_code": "688809",
                    "stock_name": "晶华微",
                    "market": "SH",
                    "report_date": "2025-12-31",
                    "report_date_text": "2025-12-31",
                    "fund_name": "1、本公司力求但不保证提供的任何信息",
                    "shares_text": "息的真实性、准确",
                    "shares": None,
                    "float_a_ratio_text": "性、完整性及原创",
                    "float_a_ratio": None,
                    "market_value_text": "性等，投资者使",
                    "market_value": None,
                    "source": "tdx_f10",
                    "raw_hash": "dirty_hash",
                    "row_seq": 1,
                },
                {
                    "stock_code": "688809",
                    "stock_name": "晶华微",
                    "market": "SH",
                    "report_date": "2025-12-31",
                    "report_date_text": "2025-12-31",
                    "fund_name": "华夏中证1000交易型开放式指数证券投资基金",
                    "shares_text": "0.97",
                    "shares": 9700,
                    "float_a_ratio_text": "0.02",
                    "float_a_ratio": 0.02,
                    "market_value_text": "30.99",
                    "market_value": 309900,
                    "source": "tdx_f10",
                    "raw_hash": "clean_hash",
                    "row_seq": 2,
                },
                {
                    "stock_code": "688809",
                    "stock_name": "晶华微",
                    "market": "SH",
                    "report_date": "2025-12-31",
                    "report_date_text": "2025-12-31",
                    "fund_name": "缺市值基金",
                    "shares_text": "100",
                    "shares": 100,
                    "market_value_text": "",
                    "market_value": None,
                    "source": "tdx_f10",
                    "raw_hash": "missing_hash",
                    "row_seq": 3,
                },
            ],
        )
        bad_rows = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM fact_fund_holding_tdx_f10
            WHERE shares IS NULL OR market_value IS NULL
               OR fund_name LIKE '%真实性%'
               OR shares_text LIKE '%真实性%'
               OR float_a_ratio_text LIKE '%真实性%'
               OR market_value_text LIKE '%真实性%'
            """
        ).fetchone()["n"]
        row_count = conn.execute("SELECT COUNT(*) AS n FROM fact_fund_holding_tdx_f10").fetchone()["n"]

        assert inserted == 1
        assert rejected == 2
        assert row_count == 1
        assert bad_rows == 0
    finally:
        conn.close()


def test_sync_tdx_f10_extra_records_skipped_non_format_b_as_terminal():
    conn = duck_mem()
    try:
        _create_raw_table(conn)
        text = "股东研究☆ ◇600000 浦发银行 更新日期：2026-04-28◇ 普通F10"
        conn.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date,
             raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
             VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("600000", "浦发银行", "SH", "2026-04-28", text, "non_b_hash", len(text), "fixture", "a", "v1"),
        )

        first = sync_tdx_f10_extra_facts(conn)
        second = sync_tdx_f10_extra_facts(conn)
        row = conn.execute(
            """
            SELECT status, status_reason, parser_version
            FROM raw_tdx_f10_extra_parse_status
            WHERE stock_code = '600000'
            """
        ).fetchone()

        assert first["raw_rows"] == 1
        assert first["skipped_non_format_b"] == 1
        assert second["raw_rows"] == 0
        assert row["status"] == "skipped_non_format_b"
        assert row["parser_version"] == "tdx_f10_extra_v2"
    finally:
        conn.close()


def test_sync_tdx_f10_extra_surfaces_fund_rejections(monkeypatch):
    conn = duck_mem()
    try:
        _create_raw_table(conn)
        text = "股东研究☆ ◇688809 晶华微 更新日期：2026-04-28◇ 通达信沪深京F10\n【7.基金持股】截止日期：2025-12-31\n"
        conn.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            (stock_code, stock_name, market, fetched_at, page_update_date,
             raw_text, raw_hash, bytes_len, server, f10_format, parser_version)
             VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("688809", "晶华微", "SH", "2026-04-28", text, "reject_hash", len(text), "fixture", "b_shsjz", "v1"),
        )
        monkeypatch.setattr(
            extra_client,
            "parse_fund_holdings_format_b",
            lambda *_args, **_kwargs: [
                {
                    "stock_code": "688809",
                    "report_date": "2025-12-31",
                    "report_date_text": "2025-12-31",
                    "fund_name": "1、本公司力求但不保证提供的任何信息",
                    "shares_text": "息的真实性、准确",
                    "shares": None,
                    "float_a_ratio_text": "性、完整性及原创",
                    "market_value_text": "性等，投资者使",
                    "market_value": None,
                    "source": "tdx_f10",
                    "raw_hash": "reject_hash",
                    "row_seq": 1,
                },
                {
                    "stock_code": "688809",
                    "report_date": "2025-12-31",
                    "report_date_text": "2025-12-31",
                    "fund_name": "华夏中证1000交易型开放式指数证券投资基金",
                    "shares_text": "0.97",
                    "shares": 9700,
                    "float_a_ratio_text": "0.02",
                    "float_a_ratio": 0.02,
                    "market_value_text": "30.99",
                    "market_value": 309900,
                    "source": "tdx_f10",
                    "raw_hash": "reject_hash",
                    "row_seq": 2,
                },
            ],
        )

        result = sync_tdx_f10_extra_facts(conn)
        row = conn.execute(
            """
            SELECT status, fund_holding_rows, fund_holding_rejected_rows, status_reason
            FROM raw_tdx_f10_extra_parse_status
            WHERE stock_code = '688809'
            """
        ).fetchone()

        assert result["status"] == "completed_with_rejections"
        assert result["fund_holding_rows"] == 1
        assert result["fund_holding_rejected_rows"] == 1
        assert row["status"] == "completed_with_rejections"
        assert row["fund_holding_rows"] == 1
        assert row["fund_holding_rejected_rows"] == 1
        assert row["status_reason"] == "fund_holding_rows_rejected"
    finally:
        conn.close()
