"""qfq builder must prefer accepted canonical over retired legacy raw daily."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_form_source_sql_joins_canonical_and_limit_without_raw() -> None:
    from services.technical_states import _SRC_TEMP_SQL

    assert "canonical_nominal_ohlcv_daily" in _SRC_TEMP_SQL
    assert "COALESCE(rd.close, can.close)" in _SRC_TEMP_SQL
    assert "substr(sl.ts_code, 1, 6) = k.code" in _SRC_TEMP_SQL
    assert "sl.ts_code = rd.ts_code" not in _SRC_TEMP_SQL


def test_market_pulse_nominal_daily_prefers_canonical() -> None:
    from services.market_pulse import _NOMINAL_DAILY_SQL

    assert "canonical_nominal_ohlcv_daily" in _NOMINAL_DAILY_SQL
    assert "raw_tushare_daily" in _NOMINAL_DAILY_SQL
    assert "NOT EXISTS" in _NOMINAL_DAILY_SQL
    assert _NOMINAL_DAILY_SQL.index("canonical_nominal_ohlcv_daily") < (
        _NOMINAL_DAILY_SQL.index("raw_tushare_daily")
    )
