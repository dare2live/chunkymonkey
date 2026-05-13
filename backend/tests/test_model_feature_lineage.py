import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from services.model_feature_lineage import (  # noqa: E402
    all_registered_feature_names,
    lineage_for_feature,
    write_model_feature_lineage,
)
from services.model_feature_schema import feature_cols_to_json, tdx_keep_challenger_feature_cols  # noqa: E402


def test_registered_model_features_have_lineage_specs():
    missing = [
        feature
        for feature in sorted(all_registered_feature_names())
        if lineage_for_feature(feature).lineage_status == "missing"
    ]
    assert missing == []


def test_transformed_cross_sectional_features_inherit_base_lineage():
    spec = lineage_for_feature("vol_std_20d_xs_bucket5")

    assert spec.lineage_status == "known"
    assert spec.feature_group == "price_technical"
    assert spec.source_table == "v_price_kline_qfq"
    assert spec.pit_required is False
    assert "vol_std_20d" in spec.notes


def test_write_model_feature_lineage_uses_model_feature_cols_json():
    conn = duck_mem()
    try:
        feature_cols = tdx_keep_challenger_feature_cols()
        conn.execute(
            """
            CREATE TABLE mart_multidim_model (
                model_id TEXT PRIMARY KEY,
                feature_cols_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES (?, ?)",
            ("model_1", feature_cols_to_json(feature_cols)),
        )

        result = write_model_feature_lineage(conn, model_id="model_1")

        assert result["status"] == "passed"
        assert result["features"] == len(feature_cols)
        assert result["missing"] == 0
        rows = conn.execute(
            """
            SELECT feature_name, source_table, source_tier, pit_required
              FROM mart_model_feature_lineage
             WHERE model_id = 'model_1'
               AND feature_name IN ('forecast_profit_yoy_mid', 'rz_balance')
             ORDER BY feature_name
            """
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "feature_name": "forecast_profit_yoy_mid",
                "source_table": "raw_gpcw_detail",
                "source_tier": 1,
                "pit_required": True,
            },
            # rz_balance lineage row removed Phase ψ.5 — raw_margin_daily deprecated
        ]
    finally:
        conn.close()
