"""northbound_research 服务层测试 —— 自带 fixture, 不碰 data/smartmoney.duckdb。

DataAccess.get() 支持注入 conn (测试路径), 生产 entity 声明 (table/columns/asof_col/code_col)
仍读真实的 backend/config/data_access.yaml —— 这正是要测的契约本身(本页读那份声明去拿
canonical_top10_float_holders_period), 只是执行连接换成本文件自建的 :memory: 库,
schema 与生产表已注册的 12 列一致 (holders_top10 entity, 2026-09-07 补 holder_code/is_holder_org
之后的版本)。
"""
from __future__ import annotations

import duckdb
import pytest

from services import northbound_research as nr

HKSCC = "10671586"
OTHER_ORG = "90000001"

_COLUMNS = (
    "stock_code", "report_date", "notice_date", "holder_set", "holder_rank",
    "holder_name_norm", "holder_code", "is_holder_org", "hold_ratio_float",
    "change_status", "hold_change_num", "is_exit_row",
)


def _mk_conn(rows: list[tuple]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_set VARCHAR, holder_rank INTEGER, holder_name_norm VARCHAR,
            holder_code VARCHAR, is_holder_org BOOLEAN, hold_ratio_float DOUBLE,
            change_status VARCHAR, hold_change_num DOUBLE, is_exit_row BOOLEAN
        )
        """
    )
    if rows:
        conn.executemany(
            f"INSERT INTO canonical_top10_float_holders_period ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            rows,
        )
    return conn


def _row(stock_code, report_date, notice_date, rank, holder_code, is_org, ratio,
         is_exit=False, name_norm="占位"):
    return (
        stock_code, report_date, notice_date, "free", rank, name_norm,
        holder_code, is_org, ratio, None, None, is_exit,
    )


# ── config loader ──────────────────────────────────────────────────────────

def test_load_config_ok():
    cfg = nr.load_config()
    assert cfg.hkscc_holder_code == "10671586"
    assert cfg.holder_set == "free"
    assert "10015776" in cfg.excluded_holder_codes
    assert set(cfg.quarter_end_suffixes) == {"03-31", "06-30", "09-30", "12-31"}
    assert cfg.tabs["market_flow"] == "blocked"
    assert cfg.tabs["stock_series"] == "enabled"


def test_load_config_unknown_top_key_fails(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\ndata_access_entity: holders_top10\n"
        "hkscc: {holder_code: '1', display_name: 'x', holder_set: free}\n"
        "quarter_end_suffixes: ['03-31']\n"
        "disclosure_settle_days: {quarterly_days: 1, annual_days: 1}\n"
        "tabs: {stock_series: enabled, market_breadth: enabled, market_flow: blocked}\n"
        "not_a_real_key: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未知键"):
        nr.load_config(bad)


def test_load_config_unknown_tabs_value_fails(tmp_path):
    bad = tmp_path / "bad_tabs.yaml"
    bad.write_text(
        "version: 1\ndata_access_entity: holders_top10\n"
        "hkscc: {holder_code: '1', display_name: 'x', holder_set: free}\n"
        "quarter_end_suffixes: ['03-31']\n"
        "disclosure_settle_days: {quarterly_days: 1, annual_days: 1}\n"
        "tabs: {stock_series: sort_of, market_breadth: enabled, market_flow: blocked}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="不在合法取值"):
        nr.load_config(bad)


def test_load_config_missing_hkscc_key_fails(tmp_path):
    bad = tmp_path / "bad_hkscc.yaml"
    bad.write_text(
        "version: 1\ndata_access_entity: holders_top10\n"
        "hkscc: {holder_code: '1', display_name: 'x', holder_set: free, extra_key: 1}\n"
        "quarter_end_suffixes: ['03-31']\n"
        "disclosure_settle_days: {quarterly_days: 1, annual_days: 1}\n"
        "tabs: {stock_series: enabled, market_breadth: enabled, market_flow: blocked}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hkscc 未知键"):
        nr.load_config(bad)


# ── stock_series ───────────────────────────────────────────────────────────

def test_stock_series_dedupes_to_latest_notice_date_version():
    # 同一 (股, 期, rank) 因供应商重新公告出现两个 notice_date 版本, 第二版比例更新过。
    rows = [
        _row("600519", "20260630", "20260810", 3, HKSCC, True, 4.10),
        _row("600519", "20260630", "20260815", 3, HKSCC, True, 4.30),  # 更晚的版本, 应该赢
    ]
    conn = _mk_conn(rows)
    out = nr.stock_series("600519", conn=conn)
    assert len(out["periods"]) == 1
    p = out["periods"][0]
    assert p["status"] == "held"
    assert p["hold_ratio_pct"] == 4.30
    assert p["notice_date"] == "2026-08-15"


def test_stock_series_marks_exit_status():
    rows = [
        _row("600519", "20260331", "20260425", 2, HKSCC, True, 4.69),
        _row("600519", "20260630", "20260815", 1, HKSCC, True, 4.30, is_exit=True),
        _row("600519", "20260630", "20260815", 3, OTHER_ORG, True, 2.0),  # 证明当期确有披露
    ]
    conn = _mk_conn(rows)
    out = nr.stock_series("600519", conn=conn)
    by_period = {p["report_date"]: p for p in out["periods"]}
    assert by_period["2026-03-31"]["status"] == "held"
    exited = by_period["2026-06-30"]
    assert exited["status"] == "exited_this_period"
    assert exited["hold_ratio_pct"] is None
    assert exited["last_known_hold_ratio_pct"] == 4.30


def test_stock_series_not_in_top10_when_other_holder_present():
    rows = [_row("600519", "20260630", "20260815", 1, OTHER_ORG, True, 3.0)]
    conn = _mk_conn(rows)
    out = nr.stock_series("600519", conn=conn)
    assert out["periods"][0]["status"] == "not_in_top10"
    assert out["periods"][0]["hold_ratio_pct"] is None


def test_stock_series_ratio_unknown_when_null_not_zero():
    rows = [_row("600519", "20260630", "20260815", 3, HKSCC, True, None)]
    conn = _mk_conn(rows)
    out = nr.stock_series("600519", conn=conn)
    p = out["periods"][0]
    assert p["status"] == "held"
    assert p["hold_ratio_pct"] is None  # 红线3: NULL 不是 0


def test_stock_series_empty_stock_returns_note_not_crash():
    conn = _mk_conn([_row("000001", "20260630", "20260815", 1, OTHER_ORG, True, 1.0)])
    out = nr.stock_series("999999", conn=conn)
    assert out["periods"] == []
    assert out["note"] is not None


def test_stock_series_rejects_blank_code():
    with pytest.raises(ValueError):
        nr.stock_series("   ")


# ── market_breadth ─────────────────────────────────────────────────────────

def test_market_breadth_filters_non_quarter_end_dates():
    rows = [
        _row("600519", "20260630", "20260815", 3, HKSCC, True, 4.30),
        _row("000001", "20260630", "20260815", 5, OTHER_ORG, True, 2.0),
        # 非标准季末的补充披露噪声日, 不应进入季度序列 (b1 核验报告 §3)
        _row("000002", "20260628", "20260701", 1, HKSCC, True, 9.99),
    ]
    conn = _mk_conn(rows)
    out = nr.market_breadth(conn=conn)
    report_dates = {p["report_date"] for p in out["periods"]}
    assert report_dates == {"2026-06-30"}


def test_market_breadth_coverage_pct_and_disclosure_status():
    rows = [
        _row("600519", "20260331", "20260425", 2, HKSCC, True, 4.69),
        _row("000001", "20260331", "20260420", 5, OTHER_ORG, True, 2.0),
        _row("000002", "20260331", "20260420", 6, OTHER_ORG, True, 1.0),
    ]
    conn = _mk_conn(rows)
    out = nr.market_breadth(as_of="2026-09-08", conn=conn)
    p = out["periods"][0]
    assert p["report_date"] == "2026-03-31"
    assert p["n_disclosed_any_top10"] == 3
    assert p["n_with_hkscc"] == 1
    assert p["coverage_pct"] == pytest.approx(33.3, abs=0.05)
    assert p["notice_date_min"] == "2026-04-20"
    assert p["notice_date_max"] == "2026-04-25"
    # 2026-09-08 距 2026-03-31 已远超季报 settle 阈值, 应判定 settled
    assert p["disclosure_status"] == "settled"


def test_market_breadth_disclosure_status_unknown_without_reference_date():
    rows = [_row("600519", "20260331", "20260425", 2, HKSCC, True, 4.69)]
    conn = _mk_conn(rows)
    # 不传 as_of 且注入 conn -> DataAccess.get 不加默认 PIT 上界, provenance.as_of 为 None ->
    # 判不了"是否仍在披露窗口内", 必须标 unknown, 不能瞎猜 settled/in_progress。
    out = nr.market_breadth(conn=conn)
    assert out["periods"][0]["disclosure_status"] == "unknown"


def test_market_breadth_empty_when_no_rows():
    conn = _mk_conn([])
    out = nr.market_breadth(conn=conn)
    assert out["periods"] == []


# ── market_flow_status (Tab3, 治理阻塞, 不读表) ─────────────────────────────

def test_market_flow_status_blocked_by_default():
    out = nr.market_flow_status()
    assert out["available"] is False
    assert "retire" in out["reason"]
    assert "tushare_sunset.yaml" in out["blocking_reference"]
