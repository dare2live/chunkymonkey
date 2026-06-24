"""单测 — 机构持仓明细 aif10 service (services.org_holding_aif10).

覆盖: PIT 披露日锚 (真金白银防穿越) / 报告期枚举 / 字段映射 (real-shape fixture, mythos §12) /
幂等 upsert / grain 唯一性。不调真 API (fixture = 真实形态值)。
"""
from __future__ import annotations

import duckdb
import pytest

from services import org_holding_aif10 as m


# ── PIT 披露日锚 (报告期 -> 法定披露截止, 监管硬上界) ──────────────────
def test_disclosure_deadline_quarter_mapping():
    # evidence: A股定期报告法定披露截止 (Q1/年报 04-30, H1 08-31, Q3 10-31)
    assert m.disclosure_deadline("2026-03-31") == "2026-04-30"   # Q1
    assert m.disclosure_deadline("2026-06-30") == "2026-08-31"   # H1
    assert m.disclosure_deadline("2026-09-30") == "2026-10-31"   # Q3
    assert m.disclosure_deadline("2025-12-31") == "2026-04-30"   # 年报 -> 次年


def test_disclosure_deadline_never_before_report_period():
    # PIT 红线: 可用日必须 >= 报告期末 (绝不超前可见)
    for rd in ("2020-03-31", "2022-06-30", "2024-09-30", "2019-12-31"):
        assert m.disclosure_deadline(rd) > rd


def test_enumerate_quarter_ends_kline_aligned():
    qs = m.enumerate_quarter_ends("2018-12-31", "2019-12-31")
    assert qs == ["2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31"]


# ── 字段映射 (real-shape fixture: MAIN_ORGHOLDDETAIL 真实字段名+形态) ──
def _real_shape_raw():
    return [
        {
            "SECURITY_CODE": "600388", "SECUCODE": "600388.SH",
            "SECURITY_NAME_ABBR": "龙净环保", "REPORT_DATE": "2026-03-31 00:00:00",
            "ORG_TYPE": "07", "F9_ORGTYPE_NAME": "非金融类上市公司",
            "HOLDER_CODE": "10010626", "HOLDER_NAME": "紫金矿业集团股份有限公司",
            "FUND_CODE": None, "FUND_DERIVECODE": None,
            "TOTAL_SHARES": 267764576, "HOLD_VALUE": 4763531807.04,
            "TOTALSHARES_RATIO": 21.08305638, "FREESHARES_RATIO": 21.08305638,
            "FREE_MARKET_CAP": 4763531807.04, "FREE_SHARES": 267764576,
            "FSR_CHANGE": 0.0, "FSR_RATE_CHANGE": 0.0, "CHANGE_TYPE": "不变",
        },
        {
            "SECURITY_CODE": "600388", "SECUCODE": "600388.SH",
            "SECURITY_NAME_ABBR": "龙净环保", "REPORT_DATE": "2026-03-31 00:00:00",
            "ORG_TYPE": "05", "F9_ORGTYPE_NAME": "基金",
            "HOLDER_CODE": "70030012", "HOLDER_NAME": "某基金",
            "FUND_CODE": "000001", "FUND_DERIVECODE": "000001.OF",
            "TOTAL_SHARES": 1000000, "TOTALSHARES_RATIO": 0.08,
            "CHANGE_TYPE": "增加",
        },
        # 缺主键行 (HOLDER_CODE 空) -> 应被剔除
        {"SECURITY_CODE": "600388", "REPORT_DATE": "2026-03-31", "HOLDER_CODE": None},
    ]


def test_normalize_field_mapping_and_pit_anchor():
    rows = m._normalize_rows(_real_shape_raw())
    assert len(rows) == 2  # 缺主键的第3行剔除
    r0 = rows[0]
    assert r0["stock_code"] == "600388"
    assert r0["report_date"] == "2026-03-31"
    assert r0["available_date"] == "2026-04-30"   # PIT 锚 (季报 -> 披露截止)
    assert r0["org_type_name"] == "非金融类上市公司"
    assert r0["holder_name"] == "紫金矿业集团股份有限公司"
    assert r0["total_shares"] == pytest.approx(267764576.0)
    assert r0["total_shares_ratio"] == pytest.approx(21.08305638)
    assert r0["fund_derivecode"] == ""            # null fund_derivecode -> '' (grain 稳定)
    assert r0["source"] == "miaoxiang"
    assert rows[1]["fund_derivecode"] == "000001.OF"


def test_normalize_empty():
    assert m._normalize_rows([]) == []
    assert m._normalize_rows(None) == []


# ── 幂等 upsert + grain ───────────────────────────────────────────────
def test_upsert_idempotent_and_grain():
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    rows = m._normalize_rows(_real_shape_raw())
    assert m._upsert_rows(con, rows) == 2
    m._upsert_rows(con, rows)  # 幂等重写
    n = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    assert n == 2  # 不翻倍
    # 改值重写 -> 同 grain 覆盖 (非新增)
    rows[0]["total_shares"] = 999.0
    m._upsert_rows(con, rows)
    n2 = con.execute("SELECT COUNT(*) FROM raw_org_holding_aif10").fetchone()[0]
    v = con.execute(
        "SELECT total_shares FROM raw_org_holding_aif10 WHERE holder_code='10010626'"
    ).fetchone()[0]
    assert n2 == 2 and v == pytest.approx(999.0)
    con.close()


def test_available_date_no_null_when_quarter_end():
    con = duckdb.connect(":memory:")
    m.ensure_tables(con)
    m._upsert_rows(con, m._normalize_rows(_real_shape_raw()))
    miss = con.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE available_date IS NULL"
    ).fetchone()[0]
    assert miss == 0  # 标准季度末报告期 PIT 锚全覆盖
    con.close()
