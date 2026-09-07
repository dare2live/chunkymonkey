"""backend/services/adjust_factor.py 单测。

算法背景/已实测数字见该模块 docstring 与 backend/config/adjust_factor.yaml 头部注释。
本文件覆盖: config fail-closed、ratio/hfq 核心算法、缺失传播为缺失、ratio 越界标 unknown、
universe 复用(排除北交所)、qfq 现算视图、rebuild_all/build_latest 只追加不改写历史、
pct_chg 独立自检、reconcile_vs_tushare 三类归因。

内存 DB 走 ``conftest.duck_mem()``（与生产一致的 DuckDB 引擎，CLAUDE.md 红线 2 / 数据 6），
不用裸 ``duckdb.connect``。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import duck_mem
from services import adjust_factor as af


CANONICAL_DDL = """
CREATE TABLE canonical_nominal_ohlcv_daily (
    ts_code VARCHAR, trade_date DATE, close DOUBLE, pre_close DOUBLE, pct_chg DOUBLE
)
"""


def _load(con, rows):
    con.execute(CANONICAL_DDL)
    for r in rows:
        con.execute("INSERT INTO canonical_nominal_ohlcv_daily VALUES (?,?,?,?,?)", list(r))


def _hfq_rows(con, cfg=None):
    cfg = cfg or af.load_config()
    return con.execute(f"SELECT * FROM ({af.hfq_sql(cfg)}) ORDER BY ts_code, trade_date").fetchall()


# --------------------------------------------------------------------------- config: fail closed


def test_load_config_default_file_ok():
    cfg = af.load_config()
    assert cfg.ex_rights_threshold == 0.005
    assert cfg.ratio_min == 0.2
    assert cfg.ratio_max == 5.0
    assert cfg.reconciliation_authority == "self_computed_from_pre_close"
    assert len(cfg.config_hash) == 64  # sha256 hex


def test_load_config_hash_is_deterministic():
    assert af.load_config().config_hash == af.load_config().config_hash


def _valid_raw() -> dict:
    return {
        "version": 1,
        "ex_rights_threshold": 0.005,
        "ratio_bounds": {"min": 0.2, "max": 5.0},
        "start_policy": {
            "mode": "data_window_first_row",
            "listing_date_source": "raw_tushare_stock_basic.list_date",
            "listing_date_populated": False,
        },
        "universe": {
            "reuse_config": "backend/config/universe_rules.yaml",
            "reuse_field": "include.board_prefixes",
        },
        "source": {"db_alias": "tushare_raw", "nominal_table": "canonical_nominal_ohlcv_daily"},
        "tushare_reconciliation": {
            "enabled": True,
            "relative_diff_alert_threshold": 0.01,
            "authority": "self_computed_from_pre_close",
            "unresolved_disposition": "keep_self_computed_flag_unknown_cause",
        },
    }


def _write(tmp_path: Path, raw: dict) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_load_config_rejects_unknown_root_key(tmp_path):
    raw = _valid_raw()
    raw["mystery_key"] = 1
    with pytest.raises(af.AdjustFactorConfigError, match="unknown keys"):
        af.load_config(_write(tmp_path, raw))


def test_load_config_rejects_missing_root_key(tmp_path):
    raw = _valid_raw()
    del raw["ratio_bounds"]
    with pytest.raises(af.AdjustFactorConfigError, match="missing keys"):
        af.load_config(_write(tmp_path, raw))


def test_load_config_rejects_bad_ratio_bounds(tmp_path):
    raw = _valid_raw()
    raw["ratio_bounds"] = {"min": 1.5, "max": 5.0}  # min must be < 1
    with pytest.raises(af.AdjustFactorConfigError, match="ratio_bounds"):
        af.load_config(_write(tmp_path, raw))


def test_load_config_rejects_dangling_universe_reference(tmp_path):
    raw = _valid_raw()
    raw["universe"]["reuse_config"] = "backend/config/does_not_exist_12345.yaml"
    with pytest.raises(af.AdjustFactorConfigError, match="dangling reference"):
        af.load_config(_write(tmp_path, raw))


def test_load_config_rejects_unsupported_reconciliation_authority(tmp_path):
    raw = _valid_raw()
    raw["tushare_reconciliation"]["authority"] = "trust_tushare_instead"
    with pytest.raises(af.AdjustFactorConfigError, match="authority"):
        af.load_config(_write(tmp_path, raw))


# --------------------------------------------------------------------------- core algorithm


def test_first_day_ratio_is_one():
    con = duck_mem()
    _load(con, [("600000.SH", "2024-01-02", 10.0, 10.0, 0.0)])
    rows = _hfq_rows(con)
    assert len(rows) == 1
    assert rows[0]["ratio"] == 1.0
    assert rows[0]["ratio_status"] == "first_day"
    assert rows[0]["hfq_factor"] == 1.0
    assert rows[0]["hfq_close"] == 10.0


def test_no_event_day_ratio_is_one():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.0, 10.0, 0.0),
        ("600000.SH", "2024-01-03", 10.1, 10.0, 1.0),  # pre_close == prior close exactly
    ])
    rows = _hfq_rows(con)
    assert rows[1]["ratio_status"] == "no_event"
    assert rows[1]["ratio"] == 1.0
    assert rows[1]["hfq_factor"] == 1.0
    assert rows[1]["hfq_close"] == 10.1


def test_ex_rights_event_computes_ratio_and_cumulative_factor():
    # day2 close=10.10; day3 pre_close=9.18 (dividend cut) -> ratio = 10.10/9.18
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600000.SH", "2024-01-04", 9.10, 9.18, -0.87),
    ])
    rows = _hfq_rows(con)
    expected_ratio = 10.10 / 9.18
    assert rows[2]["ratio_status"] == "adjusted"
    assert rows[2]["ratio"] == pytest.approx(expected_ratio)
    assert rows[2]["hfq_factor"] == pytest.approx(expected_ratio)
    assert rows[2]["hfq_close"] == pytest.approx(9.10 * expected_ratio)
    # hfq only depends on t and earlier: day1/day2 factor untouched by day3's event
    assert rows[0]["hfq_factor"] == 1.0
    assert rows[1]["hfq_factor"] == 1.0


def test_threshold_is_config_driven_not_hardcoded(tmp_path):
    # a 0.006 gap: default threshold 0.005 treats it as an event; a looser 0.01 threshold
    # treats the exact same input as no_event. Proves the boundary comes from config.
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.000, 10.000, 0.0),
        ("600000.SH", "2024-01-03", 10.000, 9.994, 0.06),  # |10.000 - 9.994| = 0.006
    ])
    default_cfg = af.load_config()
    rows_default = _hfq_rows(con, default_cfg)
    assert rows_default[1]["ratio_status"] == "adjusted"

    raw = _valid_raw()
    raw["ex_rights_threshold"] = 0.01
    loose_cfg = af.load_config(_write(tmp_path, raw))
    rows_loose = _hfq_rows(con, loose_cfg)
    assert rows_loose[1]["ratio_status"] == "no_event"
    assert rows_loose[1]["ratio"] == 1.0


# --------------------------------------------------------------------------- 缺失传播为缺失


def test_missing_pre_close_poisons_all_later_rows_same_stock_only():
    con = duck_mem()
    _load(con, [
        ("600001.SH", "2024-01-02", 10.0, 10.0, 0.0),
        ("600001.SH", "2024-01-03", 10.1, 10.0, 1.0),
        ("600001.SH", "2024-01-04", 10.2, None, None),   # missing input
        ("600001.SH", "2024-01-05", 10.3, 10.2, 0.98),   # must stay unknown (poisoned)
        # a different stock in the same batch must be unaffected (partition isolation)
        ("600009.SH", "2024-01-02", 5.0, 5.0, 0.0),
        ("600009.SH", "2024-01-03", 5.1, 5.0, 2.0),
    ])
    rows = _hfq_rows(con)
    by_code = {}
    for r in rows:
        by_code.setdefault(r["ts_code"], []).append(r)

    a = by_code["600001.SH"]
    assert a[0]["hfq_factor"] == 1.0
    assert a[1]["hfq_factor"] == 1.0
    assert a[2]["ratio_status"] == "missing_input"
    assert a[2]["ratio"] is None
    assert a[2]["hfq_factor"] is None
    # day5's own ratio would compute cleanly (no gap) but must still be NULL: poison never resets
    assert a[3]["ratio"] == 1.0
    assert a[3]["ratio_status"] == "no_event"
    assert a[3]["hfq_factor"] is None

    b = by_code["600009.SH"]
    assert all(r["hfq_factor"] is not None for r in b)


def test_ratio_out_of_bounds_is_unknown_not_silently_accepted_or_dropped():
    con = duck_mem()
    _load(con, [
        ("600002.SH", "2024-01-02", 10.0, 10.0, 0.0),
        # prev_close=10.0 (from day1), pre_close=0.01 -> ratio=1000, way above ratio_bounds.max=5.0
        ("600002.SH", "2024-01-03", 0.02, 0.01, 100.0),
        ("600002.SH", "2024-01-04", 0.021, 0.02, 5.0),
    ])
    rows = _hfq_rows(con)
    assert rows[1]["ratio_status"] == "out_of_bounds"
    assert rows[1]["ratio"] is None
    assert rows[1]["hfq_factor"] is None
    # poisons forward same as missing_input
    assert rows[2]["hfq_factor"] is None
    # the row is NOT dropped from the result set (still 3 rows) -- unknown, not deleted
    assert len(rows) == 3


# --------------------------------------------------------------------------- universe reuse


def test_beijing_exchange_prefix_excluded_via_universe_reuse():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.0, 10.0, 0.0),
        ("920001.BJ", "2024-01-02", 5.0, 5.0, 0.0),  # 北交所 92x prefix
        ("831010.BJ", "2024-01-02", 3.0, 3.0, 0.0),  # 北交所 83x prefix
    ])
    rows = _hfq_rows(con)
    codes = {r["ts_code"] for r in rows}
    assert codes == {"600000.SH"}


def test_universe_exclusion_matches_services_universe_policy():
    # 不新写一份 universe 规则: adjust_factor 的排除结果必须与 services.universe 完全一致。
    from services.universe import ACTIVE_A_SHARE_PREFIXES

    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.0, 10.0, 0.0),  # 60 -> keep
        ("000001.SZ", "2024-01-02", 10.0, 10.0, 0.0),  # 00 -> keep
        ("300001.SZ", "2024-01-02", 10.0, 10.0, 0.0),  # 30 -> keep
        ("688001.SH", "2024-01-02", 10.0, 10.0, 0.0),  # 68 -> keep
        ("920001.BJ", "2024-01-02", 10.0, 10.0, 0.0),  # 92 -> drop
    ])
    rows = _hfq_rows(con)
    codes = {r["ts_code"] for r in rows}
    for code in codes:
        assert code[:2] in ACTIVE_A_SHARE_PREFIXES
    assert "920001.BJ" not in codes


# --------------------------------------------------------------------------- qfq (未来函数, 现算)


def test_qfq_matches_nominal_at_latest_adjustment_and_scales_earlier_rows():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600000.SH", "2024-01-04", 9.10, 9.18, -0.87),   # ex-rights event, factor jumps
        ("600000.SH", "2024-01-05", 9.20, 9.10, 1.1),     # no further event
    ])
    cfg = af.load_config()
    rows = con.execute(f"SELECT * FROM ({af.qfq_sql(cfg)}) ORDER BY trade_date").fetchall()
    # latest day's own qfq_close always equals its nominal close (division by itself)
    assert rows[-1]["qfq_close"] == pytest.approx(rows[-1]["close"])
    # a day at/after the last adjustment (factor unchanged since) is also nominal==qfq
    assert rows[2]["qfq_close"] == pytest.approx(rows[2]["close"])
    # a day before the adjustment must be scaled down by the same factor
    factor = 10.10 / 9.18
    assert rows[0]["qfq_close"] == pytest.approx(10.00 / factor)


# --------------------------------------------------------------------------- persistence: append-only


def test_rebuild_all_then_build_latest_never_rewrites_old_rows():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600000.SH", "2024-01-04", 9.10, 9.18, -0.87),
    ])
    cfg = af.load_config()
    n1 = af.rebuild_all(con, cfg=cfg)
    assert n1 == 3
    before = con.execute(
        f"SELECT ts_code, trade_date, ratio, ratio_status, hfq_factor FROM {af.TABLE} ORDER BY trade_date"
    ).fetchall()

    con.execute("INSERT INTO canonical_nominal_ohlcv_daily VALUES (?,?,?,?,?)",
                ["600000.SH", "2024-01-05", 9.20, 9.10, 0.22])
    n2 = af.build_latest(con, cfg=cfg)
    assert n2 == 1  # only the new day gets inserted

    after = con.execute(
        f"SELECT ts_code, trade_date, ratio, ratio_status, hfq_factor FROM {af.TABLE} ORDER BY trade_date"
    ).fetchall()
    assert [tuple(r) for r in after[: len(before)]] == [tuple(r) for r in before]
    assert len(after) == 4
    assert after[-1]["trade_date"].isoformat() == "2024-01-05"


def test_rebuild_all_result_is_row_status_not_write_lock_bypass():
    # DDL states writer intent (INSERT-only in normal operation); rebuild_all's DELETE+INSERT
    # is documented as "regenerate the same conclusion", not a historical rewrite -- verify the
    # regenerated rows for unchanged input are identical, not merely "some rows exist".
    con = duck_mem()
    _load(con, [("600000.SH", "2024-01-02", 10.0, 10.0, 0.0)])
    cfg = af.load_config()
    af.rebuild_all(con, cfg=cfg)
    first = con.execute(f"SELECT ts_code, trade_date, ratio, hfq_factor FROM {af.TABLE}").fetchall()
    af.rebuild_all(con, cfg=cfg)
    second = con.execute(f"SELECT ts_code, trade_date, ratio, hfq_factor FROM {af.TABLE}").fetchall()
    assert [tuple(r) for r in first] == [tuple(r) for r in second]


# --------------------------------------------------------------------------- validation #1: pct_chg self-check


def test_pct_chg_self_check_near_zero_when_pct_chg_is_internally_consistent():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),          # (10.10-10.00)/10.00*100
        ("600000.SH", "2024-01-04", 9.10, 9.18, (9.10 - 9.18) / 9.18 * 100),
    ])
    report = af.pct_chg_self_check(con)
    assert report.n_rows == 2  # two LAG-able transitions
    assert report.max_abs_resid_pp < 1e-6
    assert report.n_over_alert_pp == 0


def test_pct_chg_self_check_flags_internally_inconsistent_vendor_row():
    # pct_chg deliberately does not match (close-pre_close)/pre_close*100 for day3 --
    # mirrors the one real row found in production (603005.SH 2020-03-18).
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600000.SH", "2024-01-04", 9.10, 9.18, -5.0),  # true value is (9.10-9.18)/9.18*100 = -0.87
    ])
    report = af.pct_chg_self_check(con, alert_threshold_pp=0.01)
    assert report.n_over_alert_pp == 1
    assert report.worst_rows[0]["ts_code"] == "600000.SH"
    assert report.worst_rows[0]["trade_date"].isoformat() == "2024-01-04"
    assert abs(report.worst_rows[0]["resid"]) > 1.0


def test_pct_chg_self_check_empty_input_does_not_crash():
    con = duck_mem()
    con.execute(CANONICAL_DDL)
    report = af.pct_chg_self_check(con)
    assert report.n_rows == 0
    assert report.median_abs_resid_pp is None
    assert report.worst_rows == ()


# --------------------------------------------------------------------------- validation #2: tushare reconciliation


def _dividend_ddl(con):
    con.execute("""
        CREATE TABLE raw_tushare_dividend (
            ts_code VARCHAR, ex_date VARCHAR, div_proc VARCHAR, stk_div DOUBLE, cash_div DOUBLE
        )
    """)


def _adj_factor_ddl(con):
    con.execute("CREATE TABLE raw_tushare_adj_factor (ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")


def test_reconcile_classifies_stale_tushare_when_dividend_record_matches():
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600000.SH", "2024-01-04", 8.50, 8.42, -15.0),  # real ~20% stock-dividend style event
    ])
    _adj_factor_ddl(con)
    for d in ["20240102", "20240103", "20240104"]:
        # tushare's own factor never moves across the transition -> stale
        con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600000.SH", d, 2.0])
    _dividend_ddl(con)
    con.execute(
        "INSERT INTO raw_tushare_dividend VALUES (?,?,?,?,?)",
        ["600000.SH", "20240104", "实施", 1.2, 0.0],
    )
    report = af.reconcile_vs_tushare(con)
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.ts_code == "600000.SH"
    assert d.dividend_match is True
    assert d.matched_ex_date == "20240104"
    assert d.classification == af.CLASS_TUSHARE_STALE


def test_reconcile_classifies_unexplained_jump_when_our_ratio_is_one():
    con = duck_mem()
    _load(con, [
        ("600001.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600001.SH", "2024-01-03", 10.00, 10.00, 0.0),  # no gap at all: ratio stays 1.0
        ("600001.SH", "2024-01-04", 10.00, 10.00, 0.0),
    ])
    _adj_factor_ddl(con)
    con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600001.SH", "20240102", 4.0])
    con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600001.SH", "20240103", 4.4])  # unexplained jump
    con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600001.SH", "20240104", 4.4])
    _dividend_ddl(con)  # empty: no corroborating record anywhere
    report = af.reconcile_vs_tushare(con)
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.classification == af.CLASS_TUSHARE_UNEXPLAINED_JUMP
    assert d.dividend_match is False
    assert d.our_ratio_at_divergence == pytest.approx(1.0)


def test_reconcile_classifies_unresolved_when_gap_has_no_dividend_match():
    con = duck_mem()
    _load(con, [
        ("600002.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600002.SH", "2024-01-03", 8.00, 7.80, -20.0),  # real gap, our ratio != 1
        ("600002.SH", "2024-01-04", 8.10, 8.00, 1.25),
    ])
    _adj_factor_ddl(con)
    for d in ["20240102", "20240103", "20240104"]:
        con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600002.SH", d, 3.0])
    _dividend_ddl(con)  # empty: nothing to corroborate the gap
    report = af.reconcile_vs_tushare(con)
    assert len(report.divergences) == 1
    d = report.divergences[0]
    assert d.classification == af.CLASS_UNRESOLVED
    assert d.dividend_match is False
    assert d.our_ratio_at_divergence != pytest.approx(1.0)


def test_reconcile_does_not_flag_stock_that_tracks_tushare_closely():
    con = duck_mem()
    _load(con, [
        ("600003.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600003.SH", "2024-01-03", 10.10, 10.00, 1.0),
        ("600003.SH", "2024-01-04", 10.20, 10.10, 0.99),
    ])
    _adj_factor_ddl(con)
    for d in ["20240102", "20240103", "20240104"]:
        con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600003.SH", d, 5.0])
    _dividend_ddl(con)
    report = af.reconcile_vs_tushare(con)
    assert report.divergences == ()
    assert report.max_rel_diff < 0.01


def test_reconcile_never_mutates_hfq_authority():
    # authority=self_computed_from_pre_close: reconciliation is read-only diagnostics,
    # it must not touch the fact_adjust_factor_hfq_daily table at all.
    con = duck_mem()
    _load(con, [
        ("600000.SH", "2024-01-02", 10.00, 10.00, 0.0),
        ("600000.SH", "2024-01-03", 8.50, 8.42, -15.0),
    ])
    cfg = af.load_config()
    af.rebuild_all(con, cfg=cfg)
    before = con.execute(f"SELECT * FROM {af.TABLE}").fetchall()

    _adj_factor_ddl(con)
    con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600000.SH", "20240102", 1.0])
    con.execute("INSERT INTO raw_tushare_adj_factor VALUES (?,?,?)", ["600000.SH", "20240103", 1.0])
    _dividend_ddl(con)
    af.reconcile_vs_tushare(con, cfg)

    after = con.execute(f"SELECT * FROM {af.TABLE}").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
