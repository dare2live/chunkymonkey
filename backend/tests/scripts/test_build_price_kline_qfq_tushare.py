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
