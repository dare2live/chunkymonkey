"""QFII 季度持股同步测试。"""

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import qfii_client


def _make_rows(symbol: str) -> list[dict]:
    """构造 stock_gdfx_holding_detail_em 单 symbol 的返回，模拟 UBS/摩根士丹利的 2025Q4 持仓。"""
    base = {
        "新进": {
            "序号": 1,
            "股东名称": "UBS AG",
            "股东类型": "QFII",
            "股票代码": "000411",
            "股票简称": "英特集团",
            "报告期": "2025-12-31",
            "期末持股-数量": 1978553,
            "期末持股-数量变化": None,
            "期末持股-数量变化比例": None,
            "期末持股-持股变动": "新进",
            "期末持股-流通市值": 25226550.75,
            "公告日": "2026-04-23",
            "股东排名": 9,
        },
        "增加": {
            "序号": 1,
            "股东名称": "MORGAN STANLEY",
            "股东类型": "QFII",
            "股票代码": "002218",
            "股票简称": "拓日新能",
            "报告期": "2025-12-31",
            "期末持股-数量": 5762088,
            "期末持股-数量变化": 800000,
            "期末持股-数量变化比例": 16.12,
            "期末持股-持股变动": "增加",
            "期末持股-流通市值": 25007461.92,
            "公告日": "2026-04-20",
            "股东排名": 10,
        },
    }
    return [dict(base.get(symbol, base["新进"]))]


def test_enumerate_quarter_ends_basic():
    out = qfii_client.enumerate_quarter_ends("2024-06-30", "2025-12-31")
    assert out == [
        "2024-06-30", "2024-09-30", "2024-12-31",
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]


def test_latest_plannable_is_ended_period():
    assert qfii_client.latest_plannable_report_date(today=date(2026, 4, 22)) == "2026-03-31"
    assert qfii_client.latest_plannable_report_date(today=date(2026, 1, 10)) == "2025-12-31"
    assert qfii_client.latest_plannable_report_date(today=date(2026, 8, 27)) == "2026-06-30"


def test_normalize_rows_parses_required_columns():
    rows = qfii_client._normalize_rows(_make_rows("新进") + _make_rows("增加"))
    assert len(rows) == 2
    first = rows[0]
    assert first["stock_code"] == "000411"
    assert first["holder_name"] == "UBS AG"
    assert first["report_date"] == "2025-12-31"
    assert first["change_type"] == "新进"
    assert first["hold_shares"] == 1978553
    assert first["notice_date"] == "2026-04-23"


def test_normalize_rows_raises_on_missing_column():
    try:
        qfii_client._normalize_rows([{"股东名称": "UBS"}])
    except RuntimeError as exc:
        assert "qfii_columns_missing" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_sync_qfii_quarter_upserts_all_symbols(monkeypatch):
    conn = duck_mem()
    qfii_client.ensure_tables(conn)

    calls = []

    async def _fake_fetch(report_date: str, retries: int = 3):
        calls.append(report_date)
        return _make_rows(qfii_client.QFII_SYMBOLS[0]) + _make_rows(qfii_client.QFII_SYMBOLS[1])

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))

    assert result["status"] == "ok"
    assert result["written_rows"] == 2
    assert calls == ["2025-12-31"]

    rows = conn.execute(
        "SELECT stock_code, holder_name, change_type, notice_date, source "
        "FROM raw_qfii_holding_quarterly ORDER BY stock_code"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["stock_code"] == "000411"
    assert rows[0]["change_type"] == "新进"
    assert rows[0]["source"] == qfii_client.QFII_SOURCE
    assert rows[0]["notice_date"] == "2026-04-23"


def test_sync_qfii_quarter_is_idempotent(monkeypatch):
    conn = duck_mem()
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        return _make_rows("新进")

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))
    asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))

    count = conn.execute("SELECT COUNT(*) FROM raw_qfii_holding_quarterly").fetchone()[0]
    assert count == 1  # 重复同步不产生重复行


def test_sync_qfii_quarter_handles_source_failure(monkeypatch):
    conn = duck_mem()
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        raise RuntimeError("qfii_source_failed:2025-12-31:新进:boom")

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))
    assert result["status"] == "source_unavailable"
    assert result["written_rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_qfii_holding_quarterly").fetchone()[0] == 0


def test_backfill_iterates_quarters(monkeypatch):
    conn = duck_mem()
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        # 每个季度返回一个不同的 holder 以避免主键冲突
        rows = _make_rows("新进")
        rows[0]["报告期"] = report_date
        rows[0]["股东名称"] = f"UBS AG {report_date}"
        return rows

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(
        qfii_client.backfill_qfii_history(conn, "2025-03-31", "2025-12-31")
    )
    assert result["status"] == "ok"
    assert result["written_rows"] == 4  # Q1 + Q2 + Q3 + Q4
    quarters = conn.execute(
        "SELECT DISTINCT report_date FROM raw_qfii_holding_quarterly ORDER BY report_date"
    ).fetchall()
    assert [r["report_date"] for r in quarters] == [
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]


def test_sync_qfii_incremental_refreshes_existing_period(monkeypatch):
    conn = duck_mem()
    qfii_client.ensure_tables(conn)
    monkeypatch.setattr(
        qfii_client, "latest_plannable_report_date", lambda today=None: "2026-06-30"
    )
    calls = []

    async def _fake_quarter(_conn, report_date):
        calls.append(report_date)
        return {"status": "ok", "written_rows": 12}

    monkeypatch.setattr(qfii_client, "sync_qfii_quarter", _fake_quarter)
    conn.execute(
        "INSERT INTO raw_qfii_holding_quarterly "
        "(report_date, stock_code, holder_name) VALUES ('2026-06-30', '600000', 'UBS')"
    )
    conn.commit()
    out = asyncio.run(qfii_client.sync_qfii_incremental(conn))
    assert calls == ["2026-06-30"]
    assert out["status"] == "completed"
    assert out["existing_before"] == 1
    assert out["written"] == 12


def test_qfii_sync_wired_in_pipeline_acquire():
    # 2026-06-24 旧 updater DAG 退役; QFII 增量改走 pipeline acquire 调 service
    from services.qfii_client import sync_qfii_incremental
    from services.pipeline import acquire
    assert callable(sync_qfii_incremental)
    assert hasattr(acquire, "_sync_qfii")


# ── S1-A8 (spec_bshare_b2.md §5.1, 2026-09-19): vendor_scope B股 排除 ───────
# 用真实 (不打桩) vendor_scope.yaml —— aif10.qfii_holders disposition。落点在
# _fetch_qfii_by_symbol 内 rename_map 之前 (改名后 SECURITY_TYPE_CODE 这个供应商
# 字段名就没了, 必须在改名前排除)。


def _qfii_a_row(**overrides):
    row = {
        "HOLDER_NAME": "UBS AG",
        "HOLDER_NEWTYPE": "QFII",
        "RANK": 9,
        "SECURITY_CODE": "000411",
        "SECURITY_NAME_ABBR": "英特集团",
        "END_DATE": "2025-12-31",
        "HOLD_NUM": 1978553,
        "HOLD_NUM_CHANGE": None,
        "HOLD_RATIO_CHANGE": None,
        "HOLDNUM_CHANGE_NAME": "新进",
        "HOLDER_MARKET_CAP": 25226550.75,
        "NOTICE_DATE": "2026-04-23",
    }
    row.update(overrides)
    return row


def _qfii_b_row(**overrides):
    """真实形态 aif10 B股行 (spec_bshare_b2.md §1.1 P4 实测: SECURITY_TYPE_CODE=
    058001002, 沪 900937)。"""
    row = _qfii_a_row(SECURITY_CODE="900937", SECURITY_NAME_ABBR="沪B股东")
    row["SECURITY_TYPE_CODE"] = "058001002"
    row.update(overrides)
    return row


def test_fetch_qfii_by_symbol_excludes_b_share_before_rename(monkeypatch):
    a_row = _qfii_a_row()
    b_row = _qfii_b_row()
    monkeypatch.setattr(
        qfii_client, "_fetch_qfii_aif10", lambda *_a, **_k: [a_row, b_row]
    )
    out = qfii_client._fetch_qfii_by_symbol("20251231", "新进")
    assert len(out) == 1
    assert out[0]["股票代码"] == "000411"
    # 中文列名齐全 (rename 成功执行, 不是意外落在过滤之外的原始 dict)
    for col in qfii_client._COL_REQUIRED:
        assert col in out[0]


def test_fetch_qfii_by_symbol_all_b_share_day_returns_empty(monkeypatch):
    """隔离用例: 整批只有 B 股时必须返回 [] 而不是报 qfii_columns_missing
    (排除必须先于 rename, 排除后为空时也不该走进 _normalize_rows 之外的报错路径)。"""
    monkeypatch.setattr(
        qfii_client, "_fetch_qfii_aif10", lambda *_a, **_k: [_qfii_b_row()]
    )
    out = qfii_client._fetch_qfii_by_symbol("20251231", "新进")
    assert out == []


def test_fetch_qfii_by_symbol_drops_vendor_excluded_before_rename(monkeypatch):
    """S1-A8 ordering guarantee, white-box: ``_drop_vendor_excluded`` must see
    the vendor's own (English) field names, not the rename_map's Chinese
    target names. A purely black-box before/after-rename test cannot
    discriminate this for the current rename_map (``SECURITY_TYPE_CODE`` is
    not a rename_map source key, so ``dict.get(key, key)`` happens to leave it
    untouched either way) — this test asserts the ordering positionally by
    inspecting what keys actually reach the exclusion call."""
    seen_keysets: list[set] = []

    def _spy(raw):
        seen_keysets.append(set().union(*(row.keys() for row in raw)) if raw else set())
        return raw

    monkeypatch.setattr(qfii_client, "_fetch_qfii_aif10", lambda *_a, **_k: [_qfii_a_row()])
    monkeypatch.setattr(qfii_client, "_drop_vendor_excluded", _spy)
    qfii_client._fetch_qfii_by_symbol("20251231", "新进")
    assert seen_keysets, "_drop_vendor_excluded was never called"
    assert "SECURITY_CODE" in seen_keysets[0]
    assert "股票代码" not in seen_keysets[0]


def test_drop_vendor_excluded_does_not_swallow_vendor_scope_error(monkeypatch):
    """S1-A11: unregistered acquiring path must fail closed."""
    from services.data_sources.vendor_scope import VendorScopeError

    def _raise(*_a, **_k):
        raise VendorScopeError("no disposition registered for 'aif10.qfii_holders'")

    monkeypatch.setattr("services.data_sources.vendor_scope.vendor_exclusions", _raise)
    with pytest.raises(VendorScopeError):
        qfii_client._drop_vendor_excluded([_qfii_a_row()])
