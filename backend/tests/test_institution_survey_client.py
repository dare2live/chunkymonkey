"""机构调研同步测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import institution_survey_client as survey_client


def _survey_rows() -> list[dict]:
    return [
        {
            "代码": "300750",
            "名称": "宁德时代",
            "公告日期": "2026-04-22",
            "接待日期": "2026-04-20",
            "接待机构数量": 12,
            "接待方式": "现场调研",
            "接待人员": "董秘",
            "接待地点": "公司会议室",
        },
        {
            "代码": "600519",
            "名称": "贵州茅台",
            "公告日期": "2026-04-23",
            "接待日期": "2026-04-21",
            "接待机构数量": float("nan"),
            "接待方式": "",
            "接待人员": None,
            "接待地点": "线上",
        },
    ]


def test_sync_institution_surveys_accepts_records(monkeypatch):
    conn = duck_mem()
    monkeypatch.setattr(
        survey_client,
        "_fetch_from_eastmoney_skill",
        lambda start_date: _survey_rows(),
    )

    result = survey_client.sync_institution_surveys(conn, days_back=30)

    assert result["rows_fetched"] == 2
    assert result["rows_upserted"] == 2
    assert result["mart_rows"] == 2
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, inst_count, reception_type
        FROM raw_institution_surveys
        ORDER BY stock_code
        """
    ).fetchall()
    assert rows[0]["stock_code"] == "300750"
    assert rows[0]["inst_count"] == 12
    assert rows[1]["stock_code"] == "600519"
    assert rows[1]["inst_count"] is None
    assert rows[1]["reception_type"] is None


def test_sync_institution_surveys_handles_empty_records(monkeypatch):
    conn = duck_mem()
    monkeypatch.setattr(
        survey_client,
        "_fetch_from_eastmoney_skill",
        lambda start_date: [],
    )

    result = survey_client.sync_institution_surveys(conn, days_back=30)

    assert result["rows_fetched"] == 0
    assert result["rows_upserted"] == 0
    assert result["mart_rows"] == 0
