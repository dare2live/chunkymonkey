"""qfq builder must prefer accepted canonical over retired legacy raw daily."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from conftest import duck_mem

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_price_kline_qfq_tushare", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nominal_source_prefers_canonical_over_legacy_raw() -> None:
    mod = _load_module()
    cte = mod._NOMINAL_SOURCE_CTE
    assert "canonical_nominal_ohlcv_daily" in cte
    assert "raw_tushare_daily" in cte
    assert "NOT EXISTS" in cte
    # Canonical branch listed before raw UNION ALL fill.
    assert cte.index("canonical_nominal_ohlcv_daily") < cte.index("raw_tushare_daily")


def test_s5_from_accepted_nominal_excludes_legacy_raw() -> None:
    """S5: --from-accepted derives from accepted canonical only (no raw fill)."""

    mod = _load_module()
    default_cte = mod.nominal_source_cte(from_accepted=False)
    accepted_cte = mod.nominal_source_cte(from_accepted=True)
    assert "canonical_nominal_ohlcv_daily" in accepted_cte
    assert "raw_tushare_daily" not in accepted_cte
    assert "UNION ALL" not in accepted_cte
    assert "SUBSTR(c.ts_code, 1, 2) IN" in accepted_cte
    assert "SUBSTR(c.ts_code, 1, 2) IN" in default_cte
    assert "'60'" in accepted_cte and "'92'" not in accepted_cte
    assert "raw_tushare_daily" in default_cte


def test_build_writes_physical_lineage_columns(tmp_path: Path, monkeypatch) -> None:
    """Rebuild path must stamp batch_id / ingested_at / factor_as_of (PIT lineage)."""

    mod = _load_module()
    raw_db = tmp_path / "tushare_raw.duckdb"
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    raw = duck_mem()
    try:
        raw.execute(
            """
            CREATE TABLE canonical_nominal_ohlcv_daily (
                ts_code TEXT, trade_date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                vol DOUBLE, amount DOUBLE
            )
            """
        )
        raw.execute(
            """
            CREATE TABLE raw_tushare_adj_factor (
                ts_code TEXT, trade_date TEXT, adj_factor DOUBLE
            )
            """
        )
        raw.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2026-05-04', 10, 11, 9, 10.5, 100, 1050)"
        )
        raw.execute(
            "INSERT INTO raw_tushare_adj_factor VALUES "
            "('000001.SZ', '20260504', 1.0), "
            "('000001.SZ', '20260505', 1.1)"
        )
        raw.execute(f"ATTACH '{raw_db}' AS disk")
        raw.execute("CREATE TABLE disk.canonical_nominal_ohlcv_daily AS SELECT * FROM canonical_nominal_ohlcv_daily")
        raw.execute("CREATE TABLE disk.raw_tushare_adj_factor AS SELECT * FROM raw_tushare_adj_factor")
        raw.execute("DETACH disk")
    finally:
        raw.close()

    market = duck_mem()
    try:
        n = mod.build(
            market,
            from_accepted=True,
            batch_id="qfq:testbatch:from_accepted",
            ingested_at="2026-07-21T10:00:00Z",
        )
        assert n == 1
        row = market.execute(
            "SELECT code, date, close, batch_id, ingested_at::VARCHAR, factor_as_of "
            f"FROM {mod.TARGET}"
        ).fetchone()
        assert row[0] == "000001"
        assert row[1] == "2026-05-04"
        # qfq = 10.5 * 1.0 / 1.1
        assert abs(row[2] - (10.5 * 1.0 / 1.1)) < 1e-9
        assert row[3] == "qfq:testbatch:from_accepted"
        assert "2026-07-21" in str(row[4])
        assert str(row[5])[:10] == "2026-05-05"
        nulls = market.execute(
            f"SELECT count(*) FROM {mod.TARGET} "
            "WHERE batch_id IS NULL OR ingested_at IS NULL OR factor_as_of IS NULL"
        ).fetchone()[0]
        assert nulls == 0
    finally:
        market.close()


def test_main_compacts_market_after_successful_full_rebuild(monkeypatch) -> None:
    """Post-CTAS compact is default for full; --no-compact / env escape skips it.
    Incremental compacts only when free_blocks% is at/above COMPACT_FREE_PCT.
    """

    mod = _load_module()
    calls: list[dict] = []

    def _fake_detail(conn, *, from_accepted=True, batch_id=None, ingested_at=None, mode="auto"):
        resolved = "full" if mode in ("full", "auto") else "incremental"
        if mode == "incremental":
            resolved = "incremental"
        return {
            "mode": resolved if mode != "auto" else "full",
            "rows": 8_000_000,
            "batch_id": "qfq:test",
            "rewritten_codes": 0,
            "appended_rows": 0,
        }

    def _fake_cross_check(conn):
        return {
            "n_rows": 8_000_000,
            "n_codes": 5_100,
            "max_date": "2026-07-22",
            "n_bad_price": 0,
            "n_missing_lineage": 0,
        }

    class _Conn:
        def execute(self, *_a, **_k):
            class _R:
                def fetchone(self):
                    return ("2019-01-02", "2026-07-22", 5100, 1, "2026-07-22", "2026-07-22")

            return _R()

        def close(self):
            return None

    def _fake_compact(*, remove_bak=True):
        calls.append({"remove_bak": remove_bak})
        return 0

    monkeypatch.setattr(mod, "connect", lambda *_a, **_k: _Conn())
    monkeypatch.setattr(mod, "build_detail", _fake_detail)
    monkeypatch.setattr(mod, "cross_check", _fake_cross_check)
    monkeypatch.setattr(mod, "compact_market_after_ctas", _fake_compact)
    monkeypatch.setattr(mod, "market_free_block_pct", lambda: 0.03)
    monkeypatch.delenv("CHUNKY_QFQ_SKIP_COMPACT", raising=False)

    assert mod.main(["--from-accepted", "--full"]) == 0
    assert calls == [{"remove_bak": True}]

    calls.clear()
    assert mod.main(["--from-accepted", "--full", "--no-compact"]) == 0
    assert calls == []

    monkeypatch.setenv("CHUNKY_QFQ_SKIP_COMPACT", "1")
    assert mod.main(["--from-accepted", "--full"]) == 0
    assert calls == []

    monkeypatch.delenv("CHUNKY_QFQ_SKIP_COMPACT", raising=False)
    calls.clear()

    def _fake_incr(conn, *, from_accepted=True, batch_id=None, ingested_at=None, mode="auto"):
        return {
            "mode": "incremental",
            "rows": 8_000_000,
            "batch_id": "qfq:test",
            "rewritten_codes": 0,
            "appended_rows": 10,
        }

    monkeypatch.setattr(mod, "build_detail", _fake_incr)
    monkeypatch.setattr(mod, "market_free_block_pct", lambda: 0.03)
    assert mod.main(["--from-accepted", "--incremental"]) == 0
    assert calls == []  # incremental below compact band

    monkeypatch.setattr(mod, "market_free_block_pct", lambda: 25.0)
    assert mod.main(["--from-accepted", "--incremental"]) == 0
    assert calls == [{"remove_bak": True}]  # incremental above compact band


def test_incremental_rewrites_when_latest_factor_changes(tmp_path: Path, monkeypatch) -> None:
    """Latest-adj: factor change must rewrite history — no silent stale levels."""

    mod = _load_module()
    raw_db = tmp_path / "tushare_raw.duckdb"
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    raw = duck_mem()
    try:
        raw.execute(
            """
            CREATE TABLE canonical_nominal_ohlcv_daily (
                ts_code TEXT, trade_date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                vol DOUBLE, amount DOUBLE
            )
            """
        )
        raw.execute(
            """
            CREATE TABLE raw_tushare_adj_factor (
                ts_code TEXT, trade_date TEXT, adj_factor DOUBLE
            )
            """
        )
        raw.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2026-05-04', 10, 11, 9, 10.0, 100, 1050),"
            "('000001.SZ', DATE '2026-05-05', 10, 11, 9, 11.0, 100, 1100)"
        )
        raw.execute(
            "INSERT INTO raw_tushare_adj_factor VALUES "
            "('000001.SZ', '20260504', 1.0)"
        )
        raw.execute(f"ATTACH '{raw_db}' AS disk")
        raw.execute(
            "CREATE TABLE disk.canonical_nominal_ohlcv_daily AS "
            "SELECT * FROM canonical_nominal_ohlcv_daily"
        )
        raw.execute(
            "CREATE TABLE disk.raw_tushare_adj_factor AS "
            "SELECT * FROM raw_tushare_adj_factor"
        )
        raw.execute("DETACH disk")
    finally:
        raw.close()

    from services.duck_adapter import connect as duck_connect

    mdb = tmp_path / "market.duckdb"
    market = duck_connect(str(mdb), read_only=False)
    try:
        d1 = mod.build_detail(
            market,
            from_accepted=True,
            mode="full",
            batch_id="qfq:t1:full",
            ingested_at="2026-07-21T10:00:00Z",
        )
        assert d1["mode"] == "full"
        c1 = market.execute(
            f"SELECT close, factor_as_of FROM {mod.TARGET} WHERE date='2026-05-04'"
        ).fetchone()
        assert abs(float(c1[0]) - 10.0) < 1e-9
        assert str(c1[1])[:10] == "2026-05-04"
    finally:
        market.close()

    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "INSERT INTO raw_tushare_adj_factor VALUES ('000001.SZ', '20260505', 1.1)"
        )
    finally:
        raw_w.close()

    market = duck_connect(str(mdb), read_only=False)
    try:
        d2 = mod.build_detail(
            market,
            from_accepted=True,
            mode="incremental",
            batch_id="qfq:t2:incr",
            ingested_at="2026-07-21T11:00:00Z",
        )
        assert d2["mode"] == "incremental"
        assert int(d2["rewritten_codes"] or 0) >= 1
        rows = {
            str(r[0]): (float(r[1]), str(r[2])[:10])
            for r in market.execute(
                f"SELECT date, close, factor_as_of FROM {mod.TARGET} ORDER BY date"
            ).fetchall()
        }
        assert abs(rows["2026-05-04"][0] - (10.0 * 1.0 / 1.1)) < 1e-9
        assert rows["2026-05-04"][1] == "2026-05-05"
        assert abs(rows["2026-05-05"][0] - 11.0) < 1e-9
    finally:
        market.close()


def test_incremental_appends_without_touching_history(tmp_path: Path, monkeypatch) -> None:
    """Unchanged latest factor → append new day only; prior close untouched."""

    mod = _load_module()
    raw_db = tmp_path / "tushare_raw.duckdb"
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    raw = duck_mem()
    try:
        raw.execute(
            """
            CREATE TABLE canonical_nominal_ohlcv_daily (
                ts_code TEXT, trade_date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                vol DOUBLE, amount DOUBLE
            )
            """
        )
        raw.execute(
            "CREATE TABLE raw_tushare_adj_factor (ts_code TEXT, trade_date TEXT, adj_factor DOUBLE)"
        )
        raw.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2026-05-04', 10, 11, 9, 10.0, 100, 1050)"
        )
        raw.execute(
            "INSERT INTO raw_tushare_adj_factor VALUES ('000001.SZ', '20260504', 1.0)"
        )
        raw.execute(f"ATTACH '{raw_db}' AS disk")
        raw.execute(
            "CREATE TABLE disk.canonical_nominal_ohlcv_daily AS "
            "SELECT * FROM canonical_nominal_ohlcv_daily"
        )
        raw.execute(
            "CREATE TABLE disk.raw_tushare_adj_factor AS SELECT * FROM raw_tushare_adj_factor"
        )
        raw.execute("DETACH disk")
    finally:
        raw.close()

    from services.duck_adapter import connect as duck_connect

    mdb = tmp_path / "market.duckdb"
    market = duck_connect(str(mdb), read_only=False)
    try:
        mod.build_detail(
            market,
            from_accepted=True,
            mode="full",
            batch_id="qfq:a1",
            ingested_at="2026-07-21T10:00:00Z",
        )
        hist = market.execute(
            f"SELECT close, batch_id FROM {mod.TARGET} WHERE date='2026-05-04'"
        ).fetchone()
        c_before = float(hist[0])
        batch_before = str(hist[1])
    finally:
        market.close()

    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2026-05-05', 10, 11, 9, 12.0, 100, 1200)"
        )
        # Same f_latest value (1.0) on a newer adj date → append, not rewrite.
        raw_w.execute(
            "INSERT INTO raw_tushare_adj_factor VALUES ('000001.SZ', '20260505', 1.0)"
        )
    finally:
        raw_w.close()

    market = duck_connect(str(mdb), read_only=False)
    try:
        d = mod.build_detail(
            market,
            from_accepted=True,
            mode="incremental",
            batch_id="qfq:a2",
            ingested_at="2026-07-21T12:00:00Z",
        )
        assert d["mode"] == "incremental"
        assert int(d["rewritten_codes"] or 0) == 0
        assert int(d["appended_rows"] or 0) == 1
        hist2 = market.execute(
            f"SELECT close, batch_id FROM {mod.TARGET} WHERE date='2026-05-04'"
        ).fetchone()
        assert abs(float(hist2[0]) - c_before) < 1e-12
        assert str(hist2[1]) == batch_before == "qfq:a1"
        n = market.execute(f"SELECT count(*) FROM {mod.TARGET}").fetchone()[0]
        assert int(n) == 2
    finally:
        market.close()


def test_form_source_sql_joins_canonical_and_limit_without_raw() -> None:
    from services.technical_states import src_temp_sql

    default_sql = src_temp_sql(from_accepted=False)
    assert "canonical_nominal_ohlcv_daily" in default_sql
    assert "COALESCE(rd.close, can.close)" in default_sql
    assert "substr(sl.ts_code, 1, 6) = k.code" in default_sql
    assert "sl.ts_code = rd.ts_code" not in default_sql


def test_market_pulse_nominal_daily_prefers_canonical() -> None:
    from services.market_pulse import _NOMINAL_DAILY_SQL

    assert "canonical_nominal_ohlcv_daily" in _NOMINAL_DAILY_SQL
    assert "raw_tushare_daily" in _NOMINAL_DAILY_SQL
    assert "NOT EXISTS" in _NOMINAL_DAILY_SQL
    assert _NOMINAL_DAILY_SQL.index("canonical_nominal_ohlcv_daily") < (
        _NOMINAL_DAILY_SQL.index("raw_tushare_daily")
    )
