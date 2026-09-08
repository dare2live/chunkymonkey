"""qfq builder (2026-09-08 self-computed-factor rewrite) — 全自带 fixture, 不碰宿主生产库。

覆盖: 单位换算 (vol×100/amount×1000) · 锚点=该股自己最后一行 (非全局 max) · 除权日 ratio
正确 · 血统三列(batch_id/ingested_at/factor_as_of)+新增两列(hfq_factor/config_hash)非空 ·
cross_check 四条判据均可红可绿。
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from conftest import duck_mem

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_price_kline_qfq_tushare", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
STOCK_A = [
    ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
    ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, 10.0, 5.0, 1100, 11500),
    # 除权日: prev_close(=10.5)/pre_close(=5.25) = ratio 2.0 (1:1 送转量级)
    ("000001.SZ", date(2024, 1, 4), 5.3, 6.1, 5.2, 6.0, 5.25, 14.3, 2000, 11800),
    ("000001.SZ", date(2024, 1, 5), 6.0, 6.3, 5.9, 6.2, 6.0, 3.33, 1500, 9200),
    # 该股自己最后一行 (锚点)
    ("000001.SZ", date(2024, 1, 8), 6.2, 6.7, 6.1, 6.6, 6.2, 6.45, 1800, 11700),
]
# 更早收盘的第二只股 (最后一行早于 A) —— 用来证锚点是"各自最后一行"而非全局 max 日期。
STOCK_B = [
    ("000002.SZ", date(2024, 1, 2), 19.8, 20.3, 19.7, 20.0, 19.5, 0.0, 500, 20000),
    ("000002.SZ", date(2024, 1, 3), 20.0, 20.4, 19.9, 20.0, 20.0, 0.0, 600, 12000),
    ("000002.SZ", date(2024, 1, 4), 20.0, 21.2, 19.9, 21.0, 20.0, 5.0, 700, 14500),
]


def _write_raw_db(raw_db_path: Path, rows: list[tuple]) -> None:
    raw = duck_mem()
    try:
        raw.execute(
            """
            CREATE TABLE canonical_nominal_ohlcv_daily (
                ts_code TEXT, trade_date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                pre_close DOUBLE, pct_chg DOUBLE, vol DOUBLE, amount DOUBLE
            )
            """
        )
        raw.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        raw.execute(f"ATTACH '{raw_db_path}' AS disk")
        raw.execute(
            "CREATE TABLE disk.canonical_nominal_ohlcv_daily AS "
            "SELECT * FROM canonical_nominal_ohlcv_daily"
        )
        raw.execute("DETACH disk")
    finally:
        raw.close()


def _build(tmp_path: Path, monkeypatch, rows: list[tuple], *, name: str = "raw"):
    """Wire mod.TUSHARE_DB to a fresh on-disk fixture, build_full into a fresh market db.

    Returns (mod, raw_db_path, market_db_path).
    """
    from services.duck_adapter import connect as duck_connect

    mod = _load_module()
    raw_db = tmp_path / f"{name}.duckdb"
    _write_raw_db(raw_db, rows)
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    mdb = tmp_path / f"{name}_market.duckdb"
    market = duck_connect(str(mdb), read_only=False)
    try:
        mod.build_full(market)
    finally:
        market.close()
    return mod, raw_db, mdb


def _reopen(mod, mdb: Path):
    """Fresh market connection with tr re-attached (sees any post-build mutation to raw)."""
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(str(mdb), read_only=False)
    conn.execute(f"ATTACH IF NOT EXISTS '{mod.TUSHARE_DB}' AS tr (READ_ONLY)")
    return conn


# --------------------------------------------------------------------------- A1-ish: CLI surface


def test_help_lists_expected_flags_only(capsys) -> None:
    mod = _load_module()
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--from-accepted" in out
    assert "--check-only" in out
    assert "--full" in out
    assert "--allow-legacy-fill" not in out
    assert "--incremental" not in out


def test_removed_flags_are_rejected() -> None:
    mod = _load_module()
    for bad in (["--allow-legacy-fill"], ["--incremental"]):
        with pytest.raises(SystemExit) as exc:
            mod.main(bad)
        assert exc.value.code == 2


def test_incremental_and_legacy_fill_machinery_removed() -> None:
    """全量是唯一路径 —— 增量/rewrite 判定/legacy nominal union 整段删除, 不留死代码。"""
    mod = _load_module()
    for name in (
        "build_incremental",
        "build_detail",
        "nominal_source_cte",
        "_NOMINAL_SOURCE_CTE",
        "_has_lineage_columns",
        "_table_exists",
    ):
        assert not hasattr(mod, name), f"{name} should have been deleted"


def test_factor_source_is_self_computed_not_raw_adj_factor() -> None:
    """因子来自 adjust_factor.hfq_sql (自算), 生成的 SQL 里不再出现 raw_tushare_adj_factor。"""
    mod = _load_module()
    cfg = mod.load_config()
    sql = mod.build_select_sql(cfg, batch_id="qfq:test", ingested_at="2026-01-01T00:00:00Z")
    assert "raw_tushare_adj_factor" not in sql
    assert "canonical_nominal_ohlcv_daily" in sql
    assert "hfq_factor" in sql
    assert "ratio_status" in sql


# --------------------------------------------------------------------------- build_full correctness


def test_unit_conversion_and_lineage_columns_all_non_null(tmp_path, monkeypatch) -> None:
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        # 单位换算: vol(手)×100=股, amount(千元)×1000=元 — 抽查 B 的第一行 (无除权, 好手算)。
        row = conn.execute(
            f"SELECT volume, amount FROM {mod.TARGET} WHERE code='000002' AND date='2024-01-02'"
        ).fetchone()
        assert row[0] == pytest.approx(500 * 100.0)
        assert row[1] == pytest.approx(20000 * 1000.0)

        # 血统三列 + 新增两列全表非空。
        nulls = conn.execute(
            f"SELECT count(*) FROM {mod.TARGET} WHERE batch_id IS NULL OR ingested_at IS NULL "
            "OR factor_as_of IS NULL OR hfq_factor IS NULL OR config_hash IS NULL"
        ).fetchone()[0]
        assert nulls == 0

        cfg = mod.load_config()
        chash = conn.execute(f"SELECT DISTINCT config_hash FROM {mod.TARGET}").fetchall()
        assert [r[0] for r in chash] == [cfg.config_hash]
    finally:
        conn.close()


def test_anchor_is_stock_own_last_row_not_global_max_date(tmp_path, monkeypatch) -> None:
    """回归旧 bug: 锚点曾是"因子表全局 per-code 最新行"(可能晚于该股自己最后一条 K 线),
    600069.SH 等 6 股末行因此偏离 nominal 最高 89.7%。新版锚点必须是该股自己最后一行 ——
    B 的最后一行 (2024-01-04) 早于 A 的最后一行 (2024-01-08); B 的 factor_as_of 必须钉在
    B 自己的 2024-01-04, 不是 A 的 2024-01-08 (全局 max)。
    """
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        a_anchor = conn.execute(
            f"SELECT factor_as_of FROM {mod.TARGET} WHERE code='000001' ORDER BY date DESC LIMIT 1"
        ).fetchone()[0]
        b_anchor = conn.execute(
            f"SELECT DISTINCT factor_as_of FROM {mod.TARGET} WHERE code='000002'"
        ).fetchall()
        assert str(a_anchor)[:10] == "2024-01-08"
        assert {str(r[0])[:10] for r in b_anchor} == {"2024-01-04"}

        # 每股末行 close 必须精确等于该股自己 nominal close (锚点定义的直接推论)。
        b_last_close = conn.execute(
            f"SELECT close FROM {mod.TARGET} WHERE code='000002' AND date='2024-01-04'"
        ).fetchone()[0]
        assert abs(float(b_last_close) - 21.0) < 1e-9
    finally:
        conn.close()


def test_ex_rights_day_ratio_and_qfq_price_correct(tmp_path, monkeypatch) -> None:
    """A 在 2024-01-04 有除权事件: prev_close(10.5)/pre_close(5.25)=2.0。
    hfq_factor 累乘: d1=1,d2=1,d3=2,d4=2,d5(锚)=2。qfq[t]=nominal[t]×hfq[t]/2。
    """
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A)
    conn = _reopen(mod, mdb)
    try:
        rows = {
            str(r[0]): (float(r[1]), float(r[2]), str(r[3]))
            for r in conn.execute(
                f"SELECT date, close, hfq_factor, ratio_status FROM {mod.TARGET} "
                "WHERE code='000001' ORDER BY date"
            ).fetchall()
        }
        assert rows["2024-01-02"][2] == "first_day"
        assert rows["2024-01-03"][2] == "no_event"
        assert rows["2024-01-04"][2] == "adjusted"
        assert rows["2024-01-04"][1] == pytest.approx(2.0)

        expected_qfq_close = {
            "2024-01-02": 10.0 * 1.0 / 2.0,
            "2024-01-03": 10.5 * 1.0 / 2.0,
            "2024-01-04": 6.0 * 2.0 / 2.0,
            "2024-01-05": 6.2 * 2.0 / 2.0,
            "2024-01-08": 6.6 * 2.0 / 2.0,
        }
        for d, expected in expected_qfq_close.items():
            assert rows[d][0] == pytest.approx(expected, abs=1e-9), d

        # open 也同一因子重标定 (不是只有 close) — 抽查除权日。
        open_d3 = conn.execute(
            f"SELECT open FROM {mod.TARGET} WHERE code='000001' AND date='2024-01-04'"
        ).fetchone()[0]
        assert float(open_d3) == pytest.approx(5.3 * 2.0 / 2.0, abs=1e-9)
    finally:
        conn.close()


def test_missing_pre_close_propagates_to_null_not_dropped_or_zero(tmp_path, monkeypatch) -> None:
    """红线3: pre_close 缺失 → 该股此后 hfq_factor/价格列全 NULL, 行不删、不填 0。"""
    broken = [
        ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
        ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, None, 5.0, 1100, 11500),
        ("000001.SZ", date(2024, 1, 4), 5.3, 6.1, 5.2, 6.0, 5.25, 14.3, 2000, 11800),
    ]
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, broken, name="broken")
    conn = _reopen(mod, mdb)
    try:
        n_rows = conn.execute(f"SELECT count(*) FROM {mod.TARGET} WHERE code='000001'").fetchone()[0]
        assert n_rows == 3  # 行保留, 不删

        d2 = conn.execute(
            f"SELECT close, hfq_factor, ratio_status FROM {mod.TARGET} "
            "WHERE code='000001' AND date='2024-01-03'"
        ).fetchone()
        assert d2[0] is None  # 价格列 NULL, 不填 0
        assert d2[1] is None
        assert d2[2] == "missing_input"

        d3 = conn.execute(
            f"SELECT close, hfq_factor FROM {mod.TARGET} WHERE code='000001' AND date='2024-01-04'"
        ).fetchone()
        assert d3[0] is None  # poison 向后传播, 即便 d3 自己 pre_close 正常
        assert d3[1] is None
    finally:
        conn.close()


# --------------------------------------------------------------------------- cross_check: 四条判据双向验证


def test_cross_check_all_four_pass_on_clean_fixture(tmp_path, monkeypatch) -> None:
    """绿: 干净 fixture 上四条判据全部过。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["set_missing_in_qfq"] == 0
        assert cc["set_extra_in_qfq"] == 0
        assert cc["anchor_close_mismatch_n"] == 0
        assert cc["null_factor_n"] == 0
        assert cc["qfq_max_date"] == cc["canonical_max_date"] == "2024-01-08"
    finally:
        conn.close()


def test_cross_check_set_equality_red_when_canonical_row_deleted_after_build(
    tmp_path, monkeypatch
) -> None:
    """红 (spec 指定注入法): qfq 已建好后, canonical 少了一天 → qfq 比 canonical 多出一条,
    集合不再相等 —— 这是"比集合不比 COUNT"要抓的那类退化 (行数可能凑巧没变, 成员变了)。
    """
    from services.duck_adapter import connect as duck_connect

    mod, raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "DELETE FROM canonical_nominal_ohlcv_daily WHERE ts_code='000002.SZ' AND trade_date=DATE '2024-01-02'"
        )
    finally:
        raw_w.close()

    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["set_extra_in_qfq"] >= 1
        assert cc["set_missing_in_qfq"] == 0
    finally:
        conn.close()


def test_cross_check_anchor_mismatch_red_when_last_row_corrupted(tmp_path, monkeypatch) -> None:
    """红: 直接改坏 qfq 表里某股末行 close (模拟锚点算错/写坏) → 判据 #2 抓到。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        conn.execute(
            f"UPDATE {mod.TARGET} SET close = close + 1.0 "
            "WHERE code='000001' AND date='2024-01-08'"
        )
        cc = mod.cross_check(conn)
        assert cc["anchor_close_mismatch_n"] >= 1
    finally:
        conn.close()


def test_cross_check_null_factor_red_when_pre_close_missing(tmp_path, monkeypatch) -> None:
    """红 (spec 指定注入法): 把某股某天 pre_close 置 NULL → hfq_factor NULL 计数 > 0。"""
    broken = [
        ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
        ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, None, 5.0, 1100, 11500),
    ]
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, broken, name="nullfactor")
    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["null_factor_n"] >= 1
    finally:
        conn.close()


def test_cross_check_max_date_red_when_canonical_gets_new_later_row(tmp_path, monkeypatch) -> None:
    """红: qfq 建完后 canonical 又推进一天 (未重建 qfq) → qfq_max_date 落后于 canonical。"""
    from services.duck_adapter import connect as duck_connect

    mod, raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2024-01-09', 6.6,6.8,6.5,6.7,6.6,1.5,1000,9000)"
        )
    finally:
        raw_w.close()

    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["qfq_max_date"] != cc["canonical_max_date"]
        assert cc["canonical_max_date"] == "2024-01-09"
        assert cc["qfq_max_date"] == "2024-01-08"
    finally:
        conn.close()


# --------------------------------------------------------------------------- main() end-to-end


def test_main_check_only_does_not_rebuild(tmp_path, monkeypatch, capsys) -> None:
    """--check-only 只对账, 不 DROP+CTAS —— 表内容 (batch_id) 应保持建表时的原值。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        batch_before = conn.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        conn.close()

    monkeypatch.setattr(mod, "MARKET_DB", str(mdb))
    rc = mod.main(["--check-only"])
    assert rc == 0

    conn = _reopen(mod, mdb)
    try:
        batch_after = conn.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        conn.close()
    assert batch_before == batch_after


def test_main_full_rebuild_from_scratch_returns_zero(tmp_path, monkeypatch) -> None:
    """main(['--from-accepted','--full']) 端到端: 空 market db → 建表 → verdict PASS → rc 0.

    MARKET_DB 重定向到 tmp_path (非生产路径), compact_market_after_ctas 据此自行跳过。
    """
    mod = _load_module()
    raw_db = tmp_path / "raw.duckdb"
    _write_raw_db(raw_db, STOCK_A + STOCK_B)
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))
    monkeypatch.setattr(mod, "MARKET_DB", str(tmp_path / "market.duckdb"))

    rc = mod.main(["--from-accepted", "--full"])
    assert rc == 0
