import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import financial_client


async def _async_zero(*args, **kwargs):
    return 0


def _build_raw_record(stock_code: str, report_date: str, *, ingested_at: Optional[str] = None) -> dict:
    record = {column: None for column in financial_client.RAW_FINANCIAL_COLUMNS}
    record.update({
        "stock_code": stock_code,
        "report_date": report_date,
        "report_type": "latest_snapshot",
        "source_file": "test",
        "ingested_at": ingested_at or datetime.now().isoformat(),
    })
    return record


def _fake_optional_modules() -> dict[str, object]:
    return {
        "services.capital_client": types.SimpleNamespace(sync_capital_behavior_data=_async_zero),
    }


class _FakeAkFrame:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.empty = not self._rows

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._rows)


def _fake_balance_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "资产总计": "100",
            "负债合计": "40",
            "归属于母公司股东权益合计": "60",
            "流动资产合计": "50",
            "流动负债合计": "20",
            "实收资本(或股本)": "10",
            "合同负债": "5",
            "存货": "3",
            "未分配利润": "9",
        }
    ])


def _fake_income_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "营业总收入": "88",
            "营业成本": "33",
            "营业利润": "20",
            "归属于母公司所有者的净利润": "18",
            "基本每股收益": "1.2",
        }
    ])


def _fake_cashflow_frame(report_date: str = "2025-09-30"):
    return _FakeAkFrame([
        {
            "报告日": report_date,
            "公告日期": "2025-10-30",
            "类型": "合并报表",
            "是否审计": "是",
            "经营活动产生的现金流量净额": "12",
        }
    ])


def _create_history_candidate_tables(conn):
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE excluded_stocks (stock_code TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE mart_current_relationship (stock_code TEXT)")
    conn.execute("CREATE TABLE mart_stock_trend (stock_code TEXT PRIMARY KEY)")


def test_resolve_history_candidate_limit_caps_large_backlogs_to_safe_batch():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        conn.execute("CREATE TABLE mart_stock_trend (stock_code TEXT PRIMARY KEY)")
        stock_codes = [f"{index:06d}" for index in range(1, 1201)]
        conn.executemany(
            "INSERT INTO mart_stock_trend (stock_code) VALUES (?)",
            [(code,) for code in stock_codes],
        )
        conn.commit()

        limit = financial_client._resolve_history_candidate_limit(conn, stock_codes)

        assert limit == financial_client.FIN_HISTORY_BATCH_SIZE
    finally:
        conn.close()


def test_resolve_history_candidate_limit_keeps_small_explicit_batches():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        limit = financial_client._resolve_history_candidate_limit(conn, ["000001", "000002", "000003"])
        assert limit == 3
    finally:
        conn.close()


def test_select_history_candidates_skips_recent_history_cooldown():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        _create_history_candidate_tables(conn)
        conn.execute("INSERT INTO dim_active_a_stock (stock_code) VALUES ('000001')")
        conn.execute("INSERT INTO mart_stock_trend (stock_code) VALUES ('000001')")
        conn.executemany(
            "INSERT INTO raw_gpcw_financial (stock_code, report_date, ingested_at) VALUES (?, ?, ?)",
            [
                ("000001", "2025-09-30", "2026-04-17T10:00:00"),
                ("000001", "2025-12-31", "2026-04-17T10:00:00"),
            ],
        )
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO financial_sync_state (
                stock_code, history_rows, last_report_date, last_history_at,
                history_status, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("000001", 2, "2025-12-31", now, "failed", "failed", now),
        )
        conn.commit()

        candidates = financial_client._select_history_candidates(
            conn,
            stock_codes=["000001"],
            limit=1,
        )

        assert candidates == []
    finally:
        conn.close()


def test_snapshot_state_update_preserves_history_phase_status():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        financial_client._apply_history_backfill(
            conn,
            ["000001"],
            [],
            {"000001": {"status": "failed", "error": "history_boom"}},
            "2026-04-17T14:30:00",
        )
        financial_client._upsert_snapshot_state(
            conn,
            "000001",
            "2026-04-17T14:31:00",
            snapshot_at="2026-04-17T14:31:00",
            status="ok",
        )
        conn.commit()

        state = conn.execute(
            """
            SELECT status, error, history_status, history_error,
                   snapshot_status, snapshot_error
            FROM financial_sync_state
            WHERE stock_code = ?
            """,
            ("000001",),
        ).fetchone()

        assert state["status"] == "ok"
        assert state["error"] is None
        assert state["history_status"] == "failed"
        assert state["history_error"] == "history_boom"
        assert state["snapshot_status"] == "ok"
        assert state["snapshot_error"] is None
    finally:
        conn.close()


def test_fetch_sina_history_batch_retries_transient_empty_response():
    call_state = {"count": 0}

    def fake_fetch(*, stock, symbol):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if symbol == "资产负债表":
            return _fake_balance_frame()
        if symbol == "利润表":
            return _fake_income_frame()
        if symbol == "现金流量表":
            return _fake_cashflow_frame()
        raise AssertionError(f"unexpected symbol {symbol}")

    fake_ak = types.SimpleNamespace(stock_financial_report_sina=fake_fetch)

    with mock.patch.dict(sys.modules, {"akshare": fake_ak}, clear=False), mock.patch.object(
        financial_client,
        "FIN_HISTORY_SOURCE_RETRY_ATTEMPTS",
        2,
    ), mock.patch.object(financial_client.time, "sleep", return_value=None):
        records, states = financial_client._fetch_sina_history_batch(["000001"])

    assert len(records) == 1
    assert records[0]["stock_code"] == "000001"
    assert records[0]["report_date"] == "2025-09-30"
    assert states["000001"]["status"] == "ok"
    assert states["000001"]["history_rows"] == 1
    assert call_state["count"] == 4


def test_fetch_sina_history_batch_keeps_partial_rows_when_one_statement_fails():
    def fake_fetch(*, stock, symbol):
        if symbol == "资产负债表":
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if symbol == "利润表":
            return _fake_income_frame()
        if symbol == "现金流量表":
            return _fake_cashflow_frame()
        raise AssertionError(f"unexpected symbol {symbol}")

    fake_ak = types.SimpleNamespace(stock_financial_report_sina=fake_fetch)

    with mock.patch.dict(sys.modules, {"akshare": fake_ak}, clear=False), mock.patch.object(
        financial_client,
        "FIN_HISTORY_SOURCE_RETRY_ATTEMPTS",
        1,
    ), mock.patch.object(financial_client.time, "sleep", return_value=None):
        records, states = financial_client._fetch_sina_history_batch(["000001"])

    assert len(records) == 1
    assert records[0]["stock_code"] == "000001"
    assert records[0]["report_date"] == "2025-09-30"
    assert states["000001"]["status"] == "partial"
    assert "资产负债表" in states["000001"]["error"]
    assert states["000001"]["last_report_date"] == "2025-09-30"


@pytest.mark.asyncio
async def test_sync_financial_data_skips_recent_snapshot_successes():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        financial_client._upsert_raw_financial(conn, _build_raw_record("000001", "2025-12-31"))
        financial_client._update_snapshot_state(conn, ["000001"], datetime.now().isoformat())
        conn.commit()

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=[]), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            side_effect=AssertionError("recent snapshot should be skipped"),
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(conn, stock_codes=["000001"])

        assert total == 0
        state = conn.execute(
            "SELECT status, last_snapshot_at FROM financial_sync_state WHERE stock_code = ?",
            ("000001",),
        ).fetchone()
        assert state["status"] == "ok"
        assert state["last_snapshot_at"] is not None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sync_financial_data_marks_missing_report_date_as_failed():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    try:
        snapshot_payload = {
            "000001": {
                "updated_date": None,
                "zongzichan": "100",
                "jingzichan": "50",
            },
            "000002": {
                "updated_date": "2026-04-13",
                "zongzichan": "200",
                "jingzichan": "120",
            },
        }

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=[]), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            return_value=snapshot_payload,
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(conn, stock_codes=["000001", "000002"])

        assert total == 1

        rows = conn.execute(
            "SELECT stock_code, report_date FROM raw_gpcw_financial ORDER BY stock_code, report_date"
        ).fetchall()
        assert [(row["stock_code"], row["report_date"]) for row in rows] == [("000002", "2025-12-31")]

        states = conn.execute(
            "SELECT stock_code, status, error, last_snapshot_at FROM financial_sync_state ORDER BY stock_code"
        ).fetchall()
        state_map = {row["stock_code"]: dict(row) for row in states}
        assert state_map["000001"]["status"] == "failed"
        assert state_map["000001"]["error"] == "missing_snapshot_report_date"
        assert state_map["000001"]["last_snapshot_at"] is None
        assert state_map["000002"]["status"] == "ok"
        assert state_map["000002"]["error"] is None
        assert state_map["000002"]["last_snapshot_at"] is not None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sync_financial_data_reports_progress_snapshots():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    progress = []
    try:
        financial_client._upsert_raw_financial(conn, _build_raw_record("000002", "2025-12-31"))
        conn.commit()

        history_records = [_build_raw_record("000001", "2025-12-31")]
        snapshot_payload = {
            "000002": {
                "updated_date": "2026-04-13",
                "zongzichan": "200",
                "jingzichan": "120",
            },
        }

        with mock.patch.object(financial_client, "_select_history_candidates", return_value=["000001"]), mock.patch.object(
            financial_client,
            "_fetch_sina_history_batch",
            return_value=(history_records, {"000001": {"status": "ok", "history_rows": 1, "last_report_date": "2025-12-31"}}),
        ), mock.patch.object(
            financial_client,
            "_select_snapshot_candidates",
            return_value=(["000002"], 1),
        ), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            return_value=snapshot_payload,
        ), mock.patch.dict(sys.modules, _fake_optional_modules(), clear=False):
            total = await financial_client.sync_financial_data(
                conn,
                stock_codes=["000001", "000002"],
                progress_callback=progress.append,
            )

        assert total == 2
        assert progress
        assert any(item["history_backfill"]["status"] == "running" for item in progress)
        assert any(item["snapshot_sync"]["status"] == "running" for item in progress)
        final = progress[-1]
        assert final["summary"]["status"] == "completed"
        assert final["summary"]["records"] == 2
        assert final["history_backfill"]["rows"] == 1
        assert final["snapshot_sync"]["rows"] == 1
        assert final["snapshot_sync"]["skipped_recent"] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sync_financial_data_daily_critical_skips_research_stages():
    conn = duck_mem()
    financial_client.ensure_tables(conn)
    progress = []
    try:
        with mock.patch.object(
            financial_client,
            "_select_history_candidates",
            side_effect=AssertionError("daily critical sync should not select history backfill"),
        ), mock.patch.object(
            financial_client,
            "_select_snapshot_candidates",
            return_value=(["000001"], 5),
        ), mock.patch.object(
            financial_client,
            "_fetch_latest_snapshot_batch",
            return_value={},
        ):
            total = await financial_client.sync_financial_data(
                conn,
                stock_codes=["000001"],
                progress_callback=progress.append,
                include_history=False,
                include_capital=False,
                include_indicator=False,
                history_batch_limit=0,
            )

        assert total == 0
        final = progress[-1]
        assert final["history_backfill"]["status"] == "skipped"
        assert final["capital_behavior"]["status"] == "skipped"
        assert final["financial_indicator"]["status"] == "retired"  # 2026-06-19 akshare financial_indicator 退役
        assert final["snapshot_sync"]["status"] == "partial"
        assert final["snapshot_sync"]["failed_codes"] == 1
        assert final["snapshot_sync"]["skipped_recent"] == 5
    finally:
        conn.close()


# ============================================================
# calc_financial_derived — tushare 周期模型派生 (2026-06-26 通达信全删 单元4 迁移)
# ============================================================

def _seed_tushare_financial(conn):
    """注合成 tr.* tushare 源 (attach=False 时 tr=schema)。
    600519: 2 期 (FY 20251231 + Q1 20260331); 故意制造各陷阱供单测守:
      - fina 20260331 有 update_flag 0/1 双版本 → 验去重选 update_flag=1 (非旧 0 版的错值)
      - gross_margin 列填【金额】巨值 (陷阱), grossprofit_margin 填毛利率% → 验用对列
      - roe(季报累计) 与 roe_yearly(年化) 不同 → 验用 roe_yearly
      - income 只有 20251231 (无 Q1) → 验 contract 共同最新期 INTERSECT 回退到 20251231
      - stk_holdernumber 20260331 有双 ann_date → 验 holder 去重 ann_date DESC
    """
    conn.execute("CREATE SCHEMA tr")
    conn.execute("""
        CREATE TABLE tr.raw_tushare_fina_indicator (
            ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR,
            roe DOUBLE, roe_yearly DOUBLE, debt_to_assets DOUBLE, current_ratio DOUBLE,
            grossprofit_margin DOUBLE, gross_margin DOUBLE, netprofit_margin DOUBLE,
            tr_yoy DOUBLE, netprofit_yoy DOUBLE, ocf_to_profit DOUBLE)
    """)
    conn.executemany(
        "INSERT INTO tr.raw_tushare_fina_indicator VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # FY 20251231
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", 34.46, 34.46, 16.42, 5.09, 91.18, 1.539e11, 50.53, -1.20, -4.53, 53.59),
            # Q1 20260331 — 正确版 (update_flag=1)
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", 10.57, 42.27, 12.12, 7.06, 89.76, 4.84e10, 52.22, 6.34, 1.47, 71.69),
            # Q1 20260331 — 旧错版 (update_flag=0, 全是错值; 去重必须丢弃它)
            ("600519.SH", "20260331", "20260425", "0", "2026-04-20", 99.99, 99.99, 99.99, 0.01, 1.00, 1.0, 1.0, 9.99, 9.99, 9.99),
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_balancesheet (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR, contract_liab VARCHAR)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_balancesheet VALUES (?,?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", "100"),
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", "120"),  # bs 有 Q1 但 income 没有
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_income (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR, revenue DOUBLE)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_income VALUES (?,?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", 2000.0),  # FY (12个月)
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", 500.0),   # Q1 (3个月, 故意给 — 验 contract 非FY期必 NULL 不被它污染)
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_daily_basic (ts_code VARCHAR, trade_date VARCHAR, float_share DOUBLE, total_share DOUBLE)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_daily_basic VALUES (?,?,?,?)",
        [
            ("600519.SH", "20260620", 12.50, 12.60),
            ("600519.SH", "20260623", 12.51, 12.61),  # 最新日
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_stk_holdernumber (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, built_at VARCHAR, holder_num VARCHAR)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_stk_holdernumber VALUES (?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "2026-04-17", "255892"),
            ("600519.SH", "20260331", "20260417", "2026-04-17", "243159"),  # 真值 (ann 更晚)
            ("600519.SH", "20260331", "20260410", "2026-04-10", "999999"),  # 旧 ann, 去重必丢
        ],
    )


def test_calc_financial_derived_tushare_period_model_mapping():
    """守迁移核心映射 + 两处真金白银修复 (roe_yearly / grossprofit_margin) + 去重 + 单位 + INTERSECT。"""
    conn = duck_mem()
    try:
        _seed_tushare_financial(conn)
        fact_count = financial_client.calc_financial_derived(conn, attach=False)

        dim = conn.execute("""
            SELECT roe, debt_ratio, current_ratio, gross_margin, net_margin,
                   revenue_yoy, profit_yoy, ocf_to_profit, contract_to_revenue,
                   holder_count, holder_count_change_pct, float_shares, total_shares,
                   latest_report_date, history_rows
            FROM dim_financial_latest WHERE stock_code='600519'
        """).fetchone()
        d = dict(zip(
            ["roe","debt_ratio","current_ratio","gross_margin","net_margin","revenue_yoy",
             "profit_yoy","ocf_to_profit","contract_to_revenue","holder_count",
             "holder_count_change_pct","float_shares","total_shares","latest_report_date","history_rows"],
            dim,
        ))

        # 修复1: roe 用 roe_yearly(42.27) 非季报累计 roe(10.57) 也非旧错版(99.99)
        assert abs(d["roe"] - 0.4227) < 1e-4, f"roe 应=roe_yearly/100=0.4227, 实={d['roe']}"
        # 修复2: gross 用 grossprofit_margin(89.76%) 非 gross_margin【金额】陷阱
        assert abs(d["gross_margin"] - 0.8976) < 1e-4, f"gross 应=grossprofit_margin/100=0.8976, 实={d['gross_margin']}"
        # current_ratio 不除 (倍数)
        assert abs(d["current_ratio"] - 7.06) < 1e-4, f"current_ratio 不除, 应=7.06, 实={d['current_ratio']}"
        assert abs(d["debt_ratio"] - 0.1212) < 1e-4
        assert abs(d["net_margin"] - 0.5222) < 1e-4
        assert abs(d["revenue_yoy"] - 0.0634) < 1e-4
        assert abs(d["profit_yoy"] - 0.0147) < 1e-4
        assert abs(d["ocf_to_profit"] - 0.7169) < 1e-4
        # contract 锁最新年报期(FY/1231): 即使 Q1(20260331) bs+income 都有, 也只用 FY 期 contract_liab(100)/FY revenue(2000)=0.05
        # (FY-restriction 修复期间口径混合 BLOCKER: 不会用 Q1 的 contract_liab(120)/Q1 revenue(500)=0.24 那种虚高跨期不可比值)
        assert abs(d["contract_to_revenue"] - 0.05) < 1e-4, f"contract 应锁FY期=0.05, 实={d['contract_to_revenue']}"
        # holder 去重(ann DESC 取 243159 非旧 999999) + 环比方向 (243159-255892)/255892
        assert d["holder_count"] == 243159
        assert abs(d["holder_count_change_pct"] - ((243159 - 255892) / 255892)) < 1e-6
        # float/total ×10000 (万股→股), 取最新日 20260623
        assert abs(d["float_shares"] - 125100.0) < 1e-3, f"float 应=12.51×10000, 实={d['float_shares']}"
        assert abs(d["total_shares"] - 126100.0) < 1e-3
        # 最新期格式 + history_rows
        assert d["latest_report_date"] == "2026-03-31"
        assert d["history_rows"] == 2  # 20251231 + 20260331 (去重后 distinct end_date)

        # fact 周期历史: 2 期 (去重后), float/total/holder_change 故意 NULL
        facts = conn.execute("""
            SELECT report_date, report_season, roe, gross_margin, contract_to_revenue,
                   holder_count_change_pct, float_shares, total_shares
            FROM fact_financial_derived WHERE stock_code='600519' ORDER BY report_date
        """).fetchall()
        assert fact_count == 2 and len(facts) == 2, f"fact 应 2 期 (去重), 实={fact_count}"
        fact_q1 = [f for f in facts if f[0] == "2026-03-31"][0]
        assert fact_q1[1] == "Q1"
        assert abs(fact_q1[2] - 0.4227) < 1e-4  # fact roe 也用 roe_yearly
        # fact Q1 contract: 即使 income 有 20260331(revenue=500), 非 FY 期也必 NULL (FY-restriction BLOCKER 修复)
        assert fact_q1[4] is None, "fact Q1(非年报期) contract 应 NULL — FY-restriction, 不算部分年度比率"
        # fact 的 point-in-time 列故意 NULL (无消费方)
        assert fact_q1[5] is None and fact_q1[6] is None and fact_q1[7] is None
        fact_fy = [f for f in facts if f[0] == "2025-12-31"][0]
        assert fact_fy[1] == "Q4"
        assert abs(fact_fy[4] - 0.05) < 1e-4  # FY contract = 100/2000
    finally:
        conn.close()


def test_calc_financial_derived_shadow_suffix_isolates_live():
    """write_suffix='_shadow' 只写影子表, 不碰 live dim/fact (promote 前验证隔离)。"""
    conn = duck_mem()
    try:
        _seed_tushare_financial(conn)
        financial_client.calc_financial_derived(conn, attach=False, write_suffix="_shadow")
        # 影子表有数据
        assert conn.execute("SELECT COUNT(*) FROM dim_financial_latest_shadow").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fact_financial_derived_shadow").fetchone()[0] == 2
        # live 表未被写 (ensure_tables 建了空表)
        assert conn.execute("SELECT COUNT(*) FROM dim_financial_latest").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_financial_derived").fetchone()[0] == 0
    finally:
        conn.close()
