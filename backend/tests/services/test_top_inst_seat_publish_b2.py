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


def test_p8_single_day_stat_days_not_null_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1, stat_days="2")]
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


def test_p10_category_board_with_non_category_name_raises(tmp_path, monkeypatch) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, S1, 1, stat_days="9")]
    raw = _raw_conn(tmp_path, rows)
    sm = _sm_conn(tmp_path)
    monkeypatch.setattr(pub, "RAW_DB", raw)
    monkeypatch.setattr(pub, "SMARTMONEY_DB", sm)
    with pytest.raises(ValueError, match="category_board"):
        pub.publish_fact_top_inst_seat_daily(start="20260717", end="20260717")


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


def test_p11b_audit_all_known_returns_empty(tmp_path) -> None:
    rows = [_row("席位甲", "0", 100.0, 20.0, 80.0, R1, 1)]
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


def test_p12_loader_accepts_production_yaml() -> None:
    bc = pub.load_board_class()
    assert bc.windows == ("single_day", "multi_day")
    assert len(bc.reasons) == 62
    assert bc.investor_category_names == frozenset(
        {"自然人", "中小投资者", "其他自然人", "机构投资者", "深股通投资者"}
    )
    assert bc.anonymous_inst_names == frozenset({"机构专用"})


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
# P17: shared predicate constant
# ---------------------------------------------------------------------------


def test_p17_daily_metric_filter_sql_literal() -> None:
    assert pub.DAILY_METRIC_FILTER_SQL == (
        "board_window = 'single_day' AND seat_kind <> 'investor_category'"
    )


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
    assert counts.get("retired", 0) == 9


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
