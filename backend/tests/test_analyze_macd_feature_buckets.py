from __future__ import annotations

from scripts.analyze_macd_feature_buckets import _enrich_macd_signals, _rolling_window_minima


def test_rolling_window_minima_returns_start_index_values() -> None:
    assert _rolling_window_minima([5.0, 4.0, 6.0, 3.0], 2) == [4.0, 4.0, 3.0, None]


def test_enrich_macd_signals_uses_cached_dates_and_window_minima() -> None:
    kl = {
        "000001": {
            "2026-05-01": 10.0,
            "2026-05-02": 11.0,
            "2026-05-03": 9.0,
            "2026-05-04": 12.0,
            "2026-05-05": 15.0,
        }
    }
    rows = [
        ("000001", "2026-05-01", "macd_golden_cross_above_zero", 1.0, 1.2, 1.5, 0.4, 0.6, "2"),
        ("000001", "2026-05-02", "macd_golden_cross_below_zero", 1.0, 1.3, 1.6, 0.3, 0.5, "5"),
        ("000001", "2026-05-03", "macd_golden_cross_above_zero", 1.0, None, 1.5, 0.4, 0.6, "2"),
        ("000001", "2026-05-05", "macd_golden_cross_above_zero", 1.0, 1.0, 1.0, 0.4, 0.6, "2"),
    ]

    enriched = _enrich_macd_signals(rows, kl, holding_days=2)

    assert len(enriched) == 2
    assert enriched[0]["dif_sign"] == "above_zero"
    assert enriched[0]["vol_bin"] == "平量"
    assert enriched[0]["amt_bin"] == "额温"
    assert enriched[0]["p60_bin"] == "深底"
    assert enriched[0]["stage"] == "2"
    assert round(enriched[0]["ret"], 6) == round((12.0 - 11.0) / 11.0, 6)
    assert round(enriched[0]["dd"], 6) == round((9.0 - 11.0) / 11.0, 6)
    assert enriched[1]["dif_sign"] == "below_zero"
    assert enriched[1]["stage"] == "?"
    assert round(enriched[1]["ret"], 6) == round((15.0 - 9.0) / 9.0, 6)
    assert round(enriched[1]["dd"], 6) == 0.0
