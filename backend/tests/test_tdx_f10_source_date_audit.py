from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.tdx_f10_source_date_audit import (  # noqa: E402
    audit_tdx_f10_source_date_sections,
    split_f10_sections,
)


F10_SAMPLE = """股东研究☆ ◇000001 测试银行 更新日期：2026-05-05◇ 通达信沪深京F10
【1.控股股东与实际控股人】 截止日期:2025-12-31
【2.股东增减持计划】
最新公告日期：2026-04-30，变动方向：拟减持，进度：进行中
【3.重要股东持股变动】
变动日期：2026-04-12
【4.股东变化】
截至日期：2026-03-31 十大股东情况
"""


def _create_raw_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE raw_tdx_f10_holder_research (
            stock_code TEXT,
            stock_name TEXT,
            raw_hash TEXT,
            raw_text TEXT,
            fetched_at TEXT
        )
        """
    )


def test_split_f10_sections_keeps_header_and_numbered_sections() -> None:
    sections = split_f10_sections(F10_SAMPLE)

    assert [section["section_id"] for section in sections] == ["header", "1", "2", "3", "4"]
    assert sections[0]["section_name"] == "page_header"
    assert sections[2]["section_name"] == "股东增减持计划"


def test_audit_tdx_f10_source_date_sections_classifies_source_notice_candidates() -> None:
    with duck_mem() as conn:
        _create_raw_table(conn)
        conn.execute(
            """
            INSERT INTO raw_tdx_f10_holder_research
            VALUES ('000001', '测试银行', 'hash_a', ?, '2026-05-05T00:00:00')
            """,
            (F10_SAMPLE,),
        )

        result = audit_tdx_f10_source_date_sections(
            conn,
            run_id="tdx_f10_source_audit_unit",
        )
        rows = {
            (row["section_id"], row["pattern_name"]): dict(row)
            for row in conn.execute(
                """
                SELECT section_id, pattern_name, date_role,
                       source_notice_candidate, occurrence_count,
                       min_date, max_date
                  FROM mart_tdx_f10_source_date_section_audit
                 WHERE run_id = 'tdx_f10_source_audit_unit'
                """
            ).fetchall()
        }

        assert result["raw_rows"] == 1
        assert result["audit_rows"] == 5
        assert result["source_notice_candidate_occurrences"] == 1
        assert rows[("header", "page_update_date")]["date_role"] == "page_update_availability"
        assert rows[("1", "cutoff_date")]["date_role"] == "fact_period_date"
        assert rows[("2", "latest_announce_date")]["date_role"] == "source_notice_date"
        assert rows[("2", "latest_announce_date")]["source_notice_candidate"] is True
        assert rows[("4", "cutoff_date")]["date_role"] == "fact_period_date"
        assert rows[("4", "cutoff_date")]["source_notice_candidate"] is False
