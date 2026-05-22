"""Tests for feature_join_v5 — Pattern 10 time-availability leak fix."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.labels import feature_join_v5


def test_v5_module_constants():
    assert feature_join_v5.FEATURE_PANEL_VERSION_V5 == "p0a_v5"
    cols = [c[0] for c in feature_join_v5.V5_NEW_COLS]
    # v5 drops these 3 from v4 V5_NEW_COLS list (mcap_decile/beta_60d/beta_60d_zscore)
    for dropped in ("mcap_decile", "beta_60d", "beta_60d_zscore"):
        assert dropped not in cols, f"v5 should not include {dropped} in V5_NEW_COLS (Pattern 10 leak)"


def test_v5_sql_excludes_leaky_cols():
    sql = feature_join_v5._FEATURE_JOIN_SQL_V5
    # SQL should not SELECT the 3 v4 JOIN-table leaky cols
    assert "mcd.mcap_decile" not in sql, "v5 SQL should drop mcap_decile from JOIN"
    assert "ib.beta_60d" not in sql, "v5 SQL should drop beta_60d from JOIN"
    assert "ib.beta_60d_zscore" not in sql, "v5 SQL should drop beta_60d_zscore from JOIN"
    # inst_quality_max + inst_holder_cnt 不在 v3 base, 无需 EXCLUDE (实测 panel v3 102 cols 不含)
    # Just ensure leaky cols not SELECTed at all
    assert "inst_quality_max" not in sql.split("FROM")[0], "v5 SELECT should not include inst_quality_max"
    assert "inst_holder_cnt" not in sql.split("FROM")[0], "v5 SELECT should not include inst_holder_cnt"
    # SQL should target mart_p0a_feature_label_panel_v5
    assert "INSERT INTO mart_p0a_feature_label_panel_v5" in sql
    # v4 JOIN tables not actively joined (comment refs may remain for documentation)
    assert "LEFT JOIN fact_market_cap_decile_daily" not in sql, "v5 should not JOIN mcap_decile table"
    assert "LEFT JOIN fact_industry_beta_daily" not in sql, "v5 should not JOIN industry_beta table"
    # PIT JOINs still kept
    assert "fact_capital_flow_pit_daily" in sql
    assert "mart_stock_industry_pit" in sql
    assert "observed_snapshot" in sql


def test_v5_build_function_signature():
    # Ensure callable + has expected kwargs
    import inspect
    sig = inspect.signature(feature_join_v5.build_p0a_feature_label_panel_v5)
    params = list(sig.parameters.keys())
    assert "db_path" in params
    assert "signal_dates" in params
    assert "stock_codes" in params
