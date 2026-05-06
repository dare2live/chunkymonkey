import json

from services.data_processing_monitor import ProcessingToolStats
from services.kline_source import clean_price_rows, normalize_price_rows


def test_normalize_price_rows_maps_vol_and_rejects_invalid_ohlcv_amount():
    stats = ProcessingToolStats(
        tool_name="unit_normalize",
        policy_id="unit",
        source_name="tdxhub",
    )

    rows = normalize_price_rows(
        [
            {
                "datetime": "2026-04-23",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "vol": 1000,
                "amount": 10500,
            },
            {
                "datetime": "2026-04-24",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "vol": 5.877471754111438e-39,
                "amount": 5.877471754111438e-39,
            },
            {
                "datetime": "2026-04-25",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
            },
        ],
        "tdxhub",
        stats=stats,
    )

    assert rows == [
        {
            "date": "2026-04-23",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "amount": 10500.0,
        }
    ]
    assert stats.input_rows == 3
    assert stats.accepted_rows == 1
    assert stats.rejected_rows == 2
    assert stats.reason_counts["invalid_volume"] == 1
    assert stats.reason_counts["invalid_amount"] == 1
    assert stats.reason_counts["missing_amount"] == 1


def test_clean_price_rows_preserves_code_and_records_issue_samples():
    rows, stats = clean_price_rows(
        [
            {
                "code": "000001",
                "date": "2026-05-04",
                "freq": "daily",
                "adjust": "qfq",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
                "amount": 10500,
            },
            {
                "code": "000002",
                "date": "2026-05-04",
                "freq": "daily",
                "adjust": "qfq",
                "open": 10,
                "high": 9,
                "low": 11,
                "close": 10.5,
                "volume": 1000,
                "amount": 10500,
            },
        ],
        source="tdxhub",
    )

    assert rows[0]["code"] == "000001"
    assert rows[0]["freq"] == "daily"
    assert rows[0]["adjust"] == "qfq"
    assert stats.accepted_rows == 1
    assert stats.rejected_rows == 1
    assert stats.issue_samples["invalid_ohlc_high_low"][0]["code"] == "000002"


def test_processing_tool_stats_reason_counts_are_json_ready():
    _, stats = clean_price_rows(
        [
            {
                "date": "2026-05-04",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 0,
                "amount": 10500,
            }
        ],
        source="tdxhub",
    )

    assert json.loads(json.dumps(stats.reason_counts)) == {"invalid_volume": 1}
