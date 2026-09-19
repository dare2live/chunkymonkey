"""B2 top_inst seat publication (r2b grain contract, TDD).

fact_top_inst_seat_daily grain (七列, 业主 2026-09-11 批准 D1/D2/D3):
    (trade_date, ts_code, exalter, buy, sell, board_window, event_seq)

D1: same-day/same-seat multi-board rows with identical (exalter, buy, sell)
    fold into one event (anonymous "机构专用" the same way, may undercount).
D2: investor-category rows (自然人/中小投资者/... on "严重异常期间" boards) are
    published but excluded from the two daily metrics (seat_kind check).
D3: daily metrics only count single-day boards (board_window check).

Test cases P1-P17 follow scratchpad fable_grain_contract_r2b.md §3.1 literally.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import top_inst_seat_publish as pub
from services.duck_adapter import connect as duck_connect


_SH = ZoneInfo("Asia/Shanghai")

# Real reason strings from backend/config/lhb_board_class.yaml (r2b §0.2/§3.1).
R1 = "日涨幅偏离值达到7%的前五只证券"  # single_day
R2 = "日换手率达到20%的前5只证券"  # single_day
M1 = "连续三个交易日内，涨幅偏离值累计达到20%的证券"  # multi_day
S1 = "严重异常期间日收盘价格涨幅偏离值累计达到100%的证券"  # multi_day, investor_category_board
# v2 registrations (lhb_reason_class_r1.md §4.2/§4.3): SH and BJ 严重异常 category boards.
S2 = "有价格涨跌幅限制的连续10个交易日内收盘价格涨幅偏离值累计达到100%的证券"  # SH multi_day, cat
S3 = "北交所股票连续10个交易日内日收盘价涨跌幅偏离值累计达到+150%(-60%)"  # BJ multi_day, cat


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _raw_conn(tmp_path: Path, rows: list[tuple], *, name: str = "tushare_raw.duckdb") -> Path:
    """rows: (trade_date, ts_code, exalter, side, buy, sell, net_buy, reason,
    board_rank, stat_days).
    """
    path = tmp_path / name
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_top_inst (
            trade_date VARCHAR,
            ts_code VARCHAR,
            exalter VARCHAR,
            side VARCHAR,
            buy DOUBLE,
            sell DOUBLE,
            net_buy DOUBLE,
            reason VARCHAR,
            board_rank INTEGER,
            stat_days VARCHAR,
            seat_code VARCHAR
        )
        """
    )
    if rows:
        con.executemany(
            """
            INSERT INTO raw_tushare_top_inst
                (trade_date, ts_code, exalter, side, buy, sell, net_buy, reason,
                 board_rank, stat_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    con.close()
    return path


def _sm_conn(tmp_path: Path, *, name: str = "smartmoney.duckdb") -> Path:
    path = tmp_path / name
    con = duck_connect(str(path), read_only=False)
    con.close()
    return path


def _publish(tmp_path, monkeypatch, rows, *, start="20260717", end="20260717"):
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    out = pub.publish_fact_top_inst_seat_daily(start=start, end=end)
    return out, sm


def _rows(sm_path: Path) -> list[tuple]:
    con = duck_connect(str(sm_path), read_only=True)
    try:
        return con.execute(
            f"""
            SELECT trade_date, ts_code, exalter, buy, sell, event_seq, net_buy,
                   sides, board_count, reasons, board_window, seat_kind
            FROM {pub.TABLE}
            ORDER BY trade_date, ts_code, exalter, buy, sell, board_window, event_seq
            """
        ).fetchall()
    finally:
        con.close()


D = "20260717"
TS = "600001.SH"


def _row(exalter, side, buy, sell, net_buy, reason, board_rank, stat_days=None, ts_code=TS, trade_date=D):
    return (trade_date, ts_code, exalter, side, buy, sell, net_buy, reason, board_rank, stat_days)


# ---------------------------------------------------------------------------
# canonical_reason (r1.md §3.1/§4.2 assertions 1-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, canon",
    [
        ("日换手率达到30.47%", "日换手率达到{v}%"),
        ("日价格跌幅偏离值达到-9.52%", "日价格跌幅偏离值达到{v}%"),
        (
            "异常期间日均换手率放大196.25倍，并且累计换手率达到47.45%",
            "异常期间日均换手率放大{v}倍，并且累计换手率达到{v}%",
        ),
    ],
)
def test_canonical_reason_absorbs_halfwidth_decimals(raw, canon) -> None:
    assert pub.canonical_reason(raw) == canon


@pytest.mark.parametrize(
    "s",
    [
        "日换手率达到20%的前5只证券",  # integer threshold: not absorbed
        "北交所股票最近3个有成交的交易日以内收盘价涨跌幅偏离值累计达到+40%(-40%)",
        "北交所股票连续3个交易日内日收盘价涨跌幅偏离值累计达到+40%（-40%）",
        "日换手率达到３０.４７%",  # fullwidth digits: _DECIMAL_RE must stay ASCII-only
    ],
)
def test_canonical_reason_noop_on_non_halfwidth_decimal_strings(s) -> None:
    """r1.md §4.2 assertion 4 + §4.4 mutation guard: swapping ``[0-9]`` for
    ``\\d`` in _DECIMAL_RE would (in Python 3's default Unicode mode) also
    match fullwidth digits and wrongly absorb the last case here."""
    assert pub.canonical_reason(s) == s


# ---------------------------------------------------------------------------
# P1-P6: folding mechanics
# ---------------------------------------------------------------------------


def test_p1_cross_board_same_window_folds(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R2, 1),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 1
    assert out["grain"] == [
        "trade_date", "ts_code", "exalter", "buy", "sell", "board_window", "event_seq",
    ]
    got = _rows(sm)
    assert len(got) == 1
    r = got[0]
    assert r[2] == "席位甲" and r[3] == 100.0 and r[4] == 20.0
    assert r[5] == 1  # event_seq
    assert r[6] == 80.0  # net_buy
    assert r[7] == "0"  # sides
    assert r[8] == 2  # board_count
    assert r[9] == "|".join(sorted([R1, R2]))
    assert r[10] == "single_day"
    assert r[11] == "seat"


def test_p2_cross_side_same_content_folds(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "1", 100.0, 20.0, 80.0, R1, 2),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    got = _rows(sm)
    assert len(got) == 1
    assert got[0][7] == "0,1"  # sides
    assert got[0][8] == 1  # board_count (same reason)


def test_p2b_cross_side_different_amount_does_not_fold(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "1", 90.0, 20.0, 70.0, R1, 1),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 2


def test_p3_single_vs_multi_day_same_amount_isolated_by_window(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, M1, 1, stat_days="2"),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 2
    got = _rows(sm)
    windows = sorted(r[10] for r in got)
    assert windows == ["multi_day", "single_day"]
    for r in got:
        assert r[8] == 1  # board_count each


def test_p4_multi_day_different_amount_two_rows(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 300.0, 50.0, 250.0, M1, 1, stat_days="2"),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 2


def test_p5_anonymous_same_amount_two_single_day_boards_folds(tmp_path, monkeypatch) -> None:
    rows = [
        _row("机构专用", "0", 50.0, 0.0, 50.0, R1, 2),
        _row("机构专用", "0", 50.0, 0.0, 50.0, R2, 2),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 1
    got = _rows(sm)
    assert got[0][11] == "anonymous_inst"
    assert got[0][8] == 2  # board_count


def test_p5b_anonymous_same_board_different_rank_same_amount_two_events(tmp_path, monkeypatch) -> None:
    rows = [
        _row("机构专用", "0", 50.0, 0.0, 50.0, R1, 2),
        _row("机构专用", "0", 50.0, 0.0, 50.0, R1, 3),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 2
    got = _rows(sm)
    seqs = sorted(r[5] for r in got)
    assert seqs == [1, 2]


def test_p6_investor_category_board_folds_across_sides(tmp_path, monkeypatch) -> None:
    rows = [
        _row("自然人", "0", 3e9, 2.9e9, 1e8, S1, 1, stat_days="9"),
        _row("自然人", "1", 3e9, 2.9e9, 1e8, S1, 1, stat_days="9"),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 1
    got = _rows(sm)
    assert got[0][11] == "investor_category"
    assert got[0][10] == "multi_day"
    assert got[0][7] == "0,1"


# ---------------------------------------------------------------------------
# P7-P10: fail-closed cross checks
# ---------------------------------------------------------------------------


def test_p7_unknown_reason_raises_with_string_in_message(tmp_path, monkeypatch) -> None:
    unknown = "日涨幅达到99%的前五只证券"
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位乙", "0", 10.0, 0.0, 10.0, unknown, 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError) as ei:
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")
    assert unknown in str(ei.value)


def test_p7b_null_reason_raises_fail_closed(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位乙", "0", 10.0, 0.0, 10.0, None, 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="reason"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_t1b_unknown_after_normalization_message_has_both_forms(tmp_path, monkeypatch) -> None:
    """A raw reason whose *canonical* form is unregistered must still
    fail-closed, and the error must name both forms so a human can tell
    whether it's a brand-new template or a typo of a known one
    (lhb_reason_class_r1.md §4.2 assertion 7)."""
    unknown = "日价格振幅偏离值达到15.20%"
    canon = "日价格振幅偏离值达到{v}%"
    assert canon not in pub.load_board_class().reasons  # only "振幅达到{v}%" is registered
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位乙", "0", 10.0, 0.0, 10.0, unknown, 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError) as ei:
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")
    assert unknown in str(ei.value)
    assert canon in str(ei.value)


def test_p8_single_day_stat_days_not_null_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1, stat_days="2")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="stat_days"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_t3b_single_day_stat_days_not_null_via_template_raises(tmp_path, monkeypatch) -> None:
    """Same gate as P8, but the raw reason only matches after normalization
    (canonical_reason must run before the single_day/stat_days check, not
    only before the unknown-reason check)."""
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, "日换手率达到30.47%", 1, stat_days="2")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="stat_days"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_p9_category_name_on_non_category_board_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("自然人", "0", 100.0, 20.0, 80.0, R1, 1)]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="investor_category"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


@pytest.mark.parametrize("name", ["机构", "沪股通", "投资者分类"])
def test_t4bcd_new_category_name_on_non_category_board_raises(name, tmp_path, monkeypatch) -> None:
    """The three names newly registered by the v2 migration (r1.md §1.4/§2)
    must be governed by the same check as the original five -- each one
    alone on a non-category board (R1) must raise."""
    rows = [_row(name, "0", 100.0, 20.0, 80.0, R1, 1)]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="investor_category"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_p10_category_board_with_non_category_name_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, S1, 1, stat_days="9")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="category_board"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_t5b_category_board_with_non_category_name_sh_raises(tmp_path, monkeypatch) -> None:
    """Same check as P10 (deep-market SZSE 5.4.4 board), exercised on the
    SSE 5.4.3 category board (S2) instead."""
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, S2, 1, stat_days="10")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="category_board"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_t5c_category_board_with_non_category_name_bj_raises(tmp_path, monkeypatch) -> None:
    """Same check as P10, exercised on the BSE 2026 修订 5.4.3 category
    board (S3) instead."""
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, S3, 1, stat_days="4")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="category_board"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_t6_sh_category_board_positive(tmp_path, monkeypatch) -> None:
    """SSE 5.4.3 投资者分类交易统计, five category rows on one S2 board
    (r1.md §1.4 real example, 600721.SH 2026-08-12): all five publish as
    investor_category / multi_day, and none of them pass
    daily_metric_filter_sql() (D2/D3 展示!=指标)."""
    rows = [
        _row("自然人", "0", 4173032800.0, 0.0, 4173032800.0, S2, 1, stat_days="9"),
        _row("中小投资者", "0", 2405652600.0, 0.0, 2405652600.0, S2, 2, stat_days="9"),
        _row("其他自然人", "0", 1767380200.0, 0.0, 1767380200.0, S2, 3, stat_days="9"),
        _row("机构", "0", 1032214200.0, 0.0, 1032214200.0, S2, 4, stat_days="9"),
        _row("沪股通", "0", 0.0, 0.0, 0.0, S2, 5, stat_days="9"),
    ]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 5
    got = _rows(sm)
    assert len(got) == 5
    for r in got:
        assert r[10] == "multi_day"  # board_window
        assert r[11] == "investor_category"  # seat_kind
    con = duck_connect(str(sm), read_only=True)
    try:
        cnt = con.execute(
            f"SELECT COUNT(*) FROM {pub.TABLE} WHERE {pub.daily_metric_filter_sql()}"
        ).fetchone()[0]
    finally:
        con.close()
    assert cnt == 0


def test_t7_template_absorption_publishes_with_raw_reason_string(tmp_path, monkeypatch) -> None:
    """A raw reason whose canonical form is a registered template still
    publishes normally, and the published `reasons` column keeps the raw
    (un-normalized) string, not the canonical one (r1.md §4.2 assertion 6).
    """
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, "日换手率达到30.47%", 1)]
    out, sm = _publish(tmp_path, monkeypatch, rows)
    assert out["rows"] == 1
    got = _rows(sm)
    assert len(got) == 1
    r = got[0]
    assert r[10] == "single_day"  # board_window
    assert r[11] == "seat"  # seat_kind
    assert r[9] == "日换手率达到30.47%"  # reasons: raw string, not canonical


# ---------------------------------------------------------------------------
# P11: audit_unknown_reasons
# ---------------------------------------------------------------------------


def test_p11_audit_unknown_reasons_sorted_by_count_desc(tmp_path) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 2, trade_date="20260718"),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 3, trade_date="20260719"),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1, trade_date="19990101"),  # far outside any window
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由B", 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    con = duck_connect(str(raw), read_only=True)
    try:
        got = pub.audit_unknown_reasons(con, table="raw_tushare_top_inst")
    finally:
        con.close()
    assert got == [("未知理由A", 2), ("未知理由B", 1)]


def test_t12_audit_normalizes_template_row_before_unknown_check(tmp_path) -> None:
    """r1.md §4.2 assertion 8 literally: a template-matched raw reason
    ("日换手率达到30.47%" -> registered "日换手率达到{v}%") must NOT show up
    as unknown -- audit_unknown_reasons has to canonicalize before the
    membership check, not just compare raw strings against bc.reasons."""
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 2, trade_date="20260718"),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 3, trade_date="20260719"),
        _row("席位甲", "0", 10.0, 0.0, 10.0, "日换手率达到30.47%", 1, trade_date="20260720"),
        _row("席位甲", "0", 10.0, 0.0, 10.0, "日换手率达到30.47%", 1, trade_date="20260721"),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1, trade_date="19990101"),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由B", 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    con = duck_connect(str(raw), read_only=True)
    try:
        got = pub.audit_unknown_reasons(con, table="raw_tushare_top_inst")
    finally:
        con.close()
    assert got == [("未知理由A", 2), ("未知理由B", 1)]


def test_t14_audit_unknown_reasons_reports_raw_string_not_canonical(tmp_path) -> None:
    """audit_unknown_reasons must report the raw observed reason string for
    an unknown reason, not its canonicalized ``{v}`` template form -- a
    human reading the audit output needs the actual vendor decimal (e.g.
    "15.20%") to judge whether it's a brand-new template or a typo, and a
    canonicalized report would collapse every unregistered decimal variant
    of the same wording into one indistinguishable label."""
    unknown_decimal = "日价格振幅偏离值达到15.20%"
    canon = "日价格振幅偏离值达到{v}%"
    assert canon not in pub.load_board_class().reasons  # confirm still unregistered
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, unknown_decimal, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, unknown_decimal, 2, trade_date="20260718"),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    con = duck_connect(str(raw), read_only=True)
    try:
        got = pub.audit_unknown_reasons(con, table="raw_tushare_top_inst")
    finally:
        con.close()
    assert got == [(unknown_decimal, 2), ("未知理由A", 1)]


def test_p11b_audit_all_known_returns_empty(tmp_path) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 10.0, 0.0, 10.0, "日换手率达到30.47%", 1, trade_date="20260718"),
    ]
    raw = _raw_conn(tmp_path, rows)
    con = duck_connect(str(raw), read_only=True)
    try:
        got = pub.audit_unknown_reasons(con, table="raw_tushare_top_inst")
    finally:
        con.close()
    assert got == []


# ---------------------------------------------------------------------------
# P12: loader fail-closed
# ---------------------------------------------------------------------------


def _base_doc() -> dict:
    return yaml.safe_load(pub._DEFAULT_BOARD_CLASS_YAML.read_text(encoding="utf-8"))


def _write_doc(tmp_path: Path, doc: dict, *, name: str = "board_class.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return path


def test_p12a_unknown_window_value_raises(tmp_path) -> None:
    doc = _base_doc()
    first_reason = next(iter(doc["reasons"]))
    doc["reasons"][first_reason]["window"] = "weekly"
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_p12b_unknown_top_level_key_raises(tmp_path) -> None:
    doc = _base_doc()
    doc["extra"] = "nope"
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_p12c_empty_names_raises(tmp_path) -> None:
    doc = _base_doc()
    doc["seat_kinds"]["investor_category"]["names"] = []
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_p12d_missing_checks_key_raises(tmp_path) -> None:
    doc = _base_doc()
    del doc["checks"]["single_day_statistics_days_must_be_null"]
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_t11_investor_category_board_string_value_raises(tmp_path) -> None:
    """A reasons[...].investor_category_board that is not literally a YAML
    bool (e.g. the string "yes") must be rejected at load time -- this flag
    gates seat_kind classification (investor_category vs seat/anonymous_inst),
    and a truthy-but-not-bool value must not silently pass isinstance's type
    check."""
    text = pub._DEFAULT_BOARD_CLASS_YAML.read_text(encoding="utf-8")
    target = (
        '"严重异常期间日收盘价格涨幅偏离值累计达到100%的证券": '
        "{window: multi_day, investor_category_board: true}"
    )
    assert text.count(target) == 1
    path = tmp_path / "board_class.yaml"
    path.write_text(text.replace(target, target.replace("true", '"yes"')), encoding="utf-8")
    with pytest.raises(ValueError, match="investor_category_board"):
        pub.load_board_class(path)


def test_t11b_investor_category_board_int_value_raises(tmp_path) -> None:
    """Same gate as t11, exercised with an integer 1 instead of a string --
    YAML's int and bool are distinct scalar types (Python's ``isinstance(1,
    bool)`` is False) and neither should slip past the type check."""
    text = pub._DEFAULT_BOARD_CLASS_YAML.read_text(encoding="utf-8")
    target = (
        '"严重异常期间日收盘价格涨幅偏离值累计达到100%的证券": '
        "{window: multi_day, investor_category_board: true}"
    )
    assert text.count(target) == 1
    path = tmp_path / "board_class.yaml"
    path.write_text(text.replace(target, target.replace("true", "1")), encoding="utf-8")
    with pytest.raises(ValueError, match="investor_category_board"):
        pub.load_board_class(path)


# ---------------------------------------------------------------------------
# v2 loader fail-closed: canonical-key / duplicate-key / placeholder checks
# (lhb_reason_class_r1.md §3.2, T8/T9/T10)
# ---------------------------------------------------------------------------


def test_t8_noncanonical_reason_key_raises(tmp_path) -> None:
    """A reasons key that still contains a raw ASCII decimal observation
    (i.e. canonical_reason(key) != key) must be rejected at load time --
    r1.md §3.2 registers templates, not observed instances."""
    doc = _base_doc()
    doc["reasons"]["日换手率达到30.47%"] = {"window": "single_day"}
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_t9_duplicate_reason_key_raises(tmp_path) -> None:
    """A duplicate mapping key must fail-closed at load time instead of
    PyYAML's default silent last-write-wins (r1.md §3.2 defense 1). Written
    as literal YAML text -- a Python dict cannot hold a duplicate key, so
    round-tripping through yaml.safe_dump can never reproduce this case."""
    text = """
version: 2
windows: [single_day, multi_day]
seat_kinds:
  investor_category:
    names: [自然人, 中小投资者, 其他自然人, 机构投资者, 深股通投资者, 机构, 沪股通, 投资者分类]
  anonymous_inst:
    names: [机构专用]
checks:
  single_day_statistics_days_must_be_null: true
  investor_category_names_only_on_category_boards: true
  category_boards_only_investor_category_names: true
reasons:
  "日换手率达到20%的前5只证券": {window: single_day}
  "日换手率达到20%的前5只证券": {window: multi_day}
"""
    path = tmp_path / "dup.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_t10_illegal_placeholder_reason_key_raises(tmp_path) -> None:
    """A reasons key containing "{"/"}" that does not form the literal
    placeholder "{v}" must be rejected (r1.md §3.2 defense 3)."""
    doc = _base_doc()
    doc["reasons"]["日换手率达到{x}%"] = {"window": "single_day"}
    path = _write_doc(tmp_path, doc)
    with pytest.raises(ValueError):
        pub.load_board_class(path)


def test_p12_loader_accepts_production_yaml() -> None:
    """v2 migration (lhb_reason_class_r1.md §3.1/§3.4): 582 vendor reason
    strings collapse to 119 registered keys (108 exact + 11 templates), and
    investor_category grows from 5 names to 8 (SH 机构/沪股通, the vendor's
    lost-category placeholder 投资者分类). Counts come from the label table
    itself, not hardcoded, so this test does not silently drift with it."""
    bc = pub.load_board_class()
    assert bc.windows == ("single_day", "multi_day")
    assert len(bc.reasons) == 119
    assert bc.investor_category_names == frozenset(
        {
            "自然人", "中小投资者", "其他自然人", "机构投资者", "深股通投资者",
            "机构", "沪股通", "投资者分类",
        }
    )
    assert bc.anonymous_inst_names == frozenset({"机构专用"})


def test_t5d_sh_bj_severe_category_board_reasons_classified() -> None:
    """lhb_reason_class_r1.md §4.2 assertion 5: the SH severe-category-board
    template (S2's canonical form) and the BJ severe-category-board literal
    (S3's underlying non-decimal trigger wording) must both be registered as
    multi_day + investor_category_board=True, same as the SZSE family (S1)
    exercised elsewhere in this file."""
    bc = pub.load_board_class()
    assert bc.reasons["严重异常期间日收盘价格涨幅偏离值累计达到{v}%"] == ("multi_day", True)
    assert bc.reasons["北交所股票连续10个交易日内3次出现同向异常波动情形"] == ("multi_day", True)


def test_t5_62_old_keys_all_present_reading_from_git_head() -> None:
    """lhb_reason_class_r1.md §4.2 assertion 5, last clause: the 62 version-1
    keys must all still be registered under v2, compared as sets read from
    the pre-migration YAML (git HEAD of this file before the v2 patch) --
    not a hardcoded count, so this test cannot pass by coincidence if a
    future edit silently drops one of the pre-existing keys."""
    import subprocess

    repo_root = Path(__file__).resolve().parents[3]
    old_text = subprocess.run(
        ["git", "-C", str(repo_root), "show", "HEAD:backend/config/lhb_board_class.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout
    old_doc = yaml.safe_load(old_text)
    old_keys = set(old_doc["reasons"].keys())
    assert old_keys, "expected HEAD lhb_board_class.yaml to have a non-empty reasons map"

    bc = pub.load_board_class()
    missing = old_keys - set(bc.reasons.keys())
    assert not missing, f"v1 keys dropped by v2 migration: {sorted(missing)}"


# ---------------------------------------------------------------------------
# P13-P16: r2 P6-P9 carried forward with the seven-column grain
# ---------------------------------------------------------------------------


def test_p13_net_inconsistency_raises(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 81.0, R2, 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_p14_board_rank_null_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, R1, None)]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="board_rank NULL"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


def test_p15_idempotent_window_replace_and_grain_unique_index(tmp_path, monkeypatch) -> None:
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位甲", "0", 100.0, 20.0, 80.0, R2, 1),
    ]
    out1, sm = _publish(tmp_path, monkeypatch, rows)
    out2 = pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")
    assert out1["rows"] == out2["rows"] == 1

    con = duck_connect(str(sm), read_only=False)
    try:
        # Same 7-key grain, different lineage -- must violate the unique index.
        with pytest.raises(Exception):
            con.execute(
                f"""
                INSERT INTO {pub.TABLE} (
                    trade_date, ts_code, exalter, buy, sell, event_seq, net_buy,
                    sides, board_count, reasons, board_window, seat_kind,
                    available_at, source_table, built_at
                ) VALUES ('20260717','600001.SH','席位甲',100.0,20.0,1,999.0,
                          '0',1,'dup','single_day','seat',
                          now(), 'x', now())
                """
            )
    finally:
        con.close()


def test_p16_data_access_columns_and_vendor() -> None:
    from services.data_access.spec import load_registry

    reg = load_registry()
    ent = reg.entity("top_inst")
    assert ent.db == "smartmoney"
    assert ent.table == pub.TABLE
    for col in ("sides", "board_count", "event_seq", "board_window", "seat_kind"):
        assert col in ent.columns
    assert ent.vendor == "miaoxiang"


# ---------------------------------------------------------------------------
# P17: shared predicate function (D1-D3 constant + D4 项目股票池, 2026-09-12)
# ---------------------------------------------------------------------------


def test_p17_daily_metric_filter_sql_literal() -> None:
    """D2/D3 字面量 + D4 (项目股票池) 拼接; D4 的前缀白名单必须来自
    services.universe (下面用真实 sql_where_active_a_share() 算期望值,
    不在本测试里把前缀写死成第二份字面量)。"""
    from services.universe import sql_where_active_a_share

    assert pub.daily_metric_filter_sql() == (
        "board_window = 'single_day' AND seat_kind <> 'investor_category' AND "
        + sql_where_active_a_share("ts_code")
    )


def test_p17b_daily_metric_filter_sql_takes_column_param() -> None:
    """D4 那一段必须跟着调用方传入的列名走, 不能不管参数硬写 'ts_code'
    (两个日频消费方里这一列目前都无歧义可省, 但函数契约必须支持限定列名)。"""
    from services.universe import sql_where_active_a_share

    assert pub.daily_metric_filter_sql("t.ts_code") == (
        "board_window = 'single_day' AND seat_kind <> 'investor_category' AND "
        + sql_where_active_a_share("t.ts_code")
    )


# ---------------------------------------------------------------------------
# T13: CLI --audit-reasons (r2b §2.4 entry point, r1.md §4.1)
# ---------------------------------------------------------------------------


def test_t13_cli_audit_reasons_flag(tmp_path, monkeypatch, capsys) -> None:
    """--audit-reasons only-reads raw_tushare_top_inst, prints a JSON array
    of {reason, canonical, rows} for every unregistered reason (sorted by
    rows desc), and exits 1 iff that array is non-empty -- publish is never
    called."""
    import importlib.util
    import json

    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1, trade_date="20260718"),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由B", 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    monkeypatch.setattr(pub, "RAW_DB", raw)

    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "publish_fact_top_inst_seat_daily.py"
    )
    spec = importlib.util.spec_from_file_location(
        "publish_fact_top_inst_seat_daily_cli_t13", script_path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main(["--audit-reasons"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out == [
        {"reason": "未知理由A", "canonical": "未知理由A", "rows": 2},
        {"reason": "未知理由B", "canonical": "未知理由B", "rows": 1},
    ]


def test_t13b_cli_audit_reasons_flag_all_known_exits_zero(tmp_path, monkeypatch, capsys) -> None:
    import importlib.util
    import json

    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1)]
    raw = _raw_conn(tmp_path, rows)
    monkeypatch.setattr(pub, "RAW_DB", raw)

    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "publish_fact_top_inst_seat_daily.py"
    )
    spec = importlib.util.spec_from_file_location(
        "publish_fact_top_inst_seat_daily_cli_t13b", script_path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rc = mod.main(["--audit-reasons"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == []


def test_t13c_audit_from_raw_opens_the_raw_db_read_only_and_delegates(
    tmp_path, monkeypatch
) -> None:
    """服务层入口自己持连接: 只读打开 _raw_db_path() 指向的库, 结果原样来自
    audit_unknown_reasons。脚本层不再内联裸查 (SERVE 读层门 D1: 非成员消费者
    禁 duck_connect 内联)。"""
    rows = [
        _row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1),
        _row("席位乙", "0", 5.0, 0.0, 5.0, "未知理由A", 1),
    ]
    raw = _raw_conn(tmp_path, rows)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    seen: list[dict] = []
    real = pub.duck_connect

    def spy(path, **kw):
        seen.append({"path": str(path), **kw})
        return real(path, **kw)

    monkeypatch.setattr(pub, "duck_connect", spy)
    got = pub.audit_unknown_reasons_from_raw(table="raw_tushare_top_inst")
    assert got == [("未知理由A", 1)]
    assert seen == [{"path": str(raw), "read_only": True}]


def test_t13d_audit_from_raw_raises_when_raw_db_is_missing(tmp_path, monkeypatch) -> None:
    """raw 库不在时必须抛, 不能返回空列表 —— 「读不到」与「没有未知理由」不是
    一件事 (红线 3: 缺失只能传播为缺失)。"""
    missing = tmp_path / "absent_raw.duckdb"
    monkeypatch.setattr(pub, "RAW_DB", missing)
    with pytest.raises(FileNotFoundError) as ei:
        pub.audit_unknown_reasons_from_raw()
    assert str(missing) in str(ei.value)


# ---------------------------------------------------------------------------
# misc surface kept from the pre-r2b B2 strangler
# ---------------------------------------------------------------------------


def test_available_at_is_trade_date_1800_shanghai() -> None:
    at = pub.top_inst_seat_available_at("20260717")
    assert at == datetime(2026, 7, 17, 18, 0, tzinfo=_SH)


def test_legacy_plane_top_inst_is_compatibility() -> None:
    """Restored from pre-r2b B2 strangler (HEAD f89661e0): raw_tushare_top_inst
    stays a compatibility leaf, not upgraded to ssot, and the legacy-plane
    gate's global role counts are untouched by the r2b grain migration.

    retired 计数不钉死数字 (2026-09-18 cut_lineage_drift §2.3, 同一改法见
    test_legacy_raw_plane_s7.py::test_s7_inventory_role_counts_after_derive_pulse_knife):
    已 DROP 的墓碑条目 (express/fina_mainbz/stk_factor_pro) 同 commit 从
    legacy_raw_plane.yaml 删除后 retired 从 9 降到 6, "retired == N" 这种状态断言
    每次墓碑清理都要跟着改数字且不判任何东西。真正该守的不变量在
    test_s7_residual_ssot_map_is_typed_hard_stops_only 里, 这里只保留与
    raw_tushare_top_inst 本身相关的 ssot/compatibility 计数。
    """
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_top_inst"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "multi_consumer"
    assert meta["publication_surface"] == "fact_top_inst_seat_daily"
    assert mod.collect_violations() == []
    counts = mod.role_counts()
    assert counts["ssot"] == 14
    assert counts["compatibility"] == 22
    assert counts.get("retired", 0) > 0, "S7 inventory 至少应有一张 retired 表 (K3 停更批)"


def test_consumers_resolve_top_inst_off_raw_leaf(monkeypatch) -> None:
    """Restored from pre-r2b B2 strangler (HEAD f89661e0): pulse + institution_profile
    must resolve seat publication, not raw leaf. No assertion here names a
    column, so the r2b grain migration (side -> sides, +board_window/seat_kind)
    does not change this test's shape -- restored verbatim.
    """
    from services import market_pulse as mp
    from services import institution_profile as ip
    from services.data_access.spec import load_registry

    monkeypatch.setattr(mp, "_ACCESS_REG", None)
    monkeypatch.setattr(ip, "_ACCESS_REG", None)
    reg = load_registry()
    assert reg.entity("top_inst").db == "smartmoney"
    assert reg.entity("top_inst").table == "fact_top_inst_seat_daily"
    assert mp._tr_entity("top_inst") == "fact_top_inst_seat_daily"
    assert ip._tr_entity("top_inst") == "sm.fact_top_inst_seat_daily"
    # B1: dc_member observation-date PIT also off raw.
    assert mp._tr_entity("dc_member") == "fact_dc_member_daily"
