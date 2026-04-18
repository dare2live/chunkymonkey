import pandas as pd

from services.qlib_full_engine import (
    _inject_custom_factors_into_handler,
    _resolve_feature_name_aliases,
)
from services.scoring import (
    compute_composite_priority,
    derive_stock_gate_from_priority,
)


def test_compute_composite_priority_uses_configured_weights():
    raw, final = compute_composite_priority(
        90,
        30,
        30,
        30,
        weights={
            "discovery": 80,
            "quality": 10,
            "stage": 5,
            "forecast": 5,
        },
    )

    assert raw == 78.0
    assert final == 78.0


def test_derive_stock_gate_from_priority_prefers_stock_pool_result():
    gate, reason = derive_stock_gate_from_priority(
        "B池",
        68.2,
        "综合分达标但未通过 A 池门槛",
    )

    assert gate == "watch"
    assert "68.20" in reason
    assert "B池" in reason


def test_resolve_feature_name_aliases_maps_lightgbm_column_numbers():
    resolved = _resolve_feature_name_aliases(
        ["Column_0", "Column_2", "stage_score"],
        ["alpha_close", "qual_quality", "fin_roe"],
    )

    assert resolved == ["alpha_close", "fin_roe", "stage_score"]


def test_inject_custom_factors_uses_fetch_when_handler_data_is_lazy_loaded():
    class LazyHandler:
        def __init__(self, frame):
            self._data = pd.DataFrame()
            self._frame = frame

        def fetch(self, col_set="feature"):
            return self._frame.copy()

    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-04-30"), "SH600001"),
            (pd.Timestamp("2025-05-01"), "SH600001"),
        ],
        names=["datetime", "instrument"],
    )
    base_frame = pd.DataFrame(
        {("feature", "alpha_close"): [1.0, 1.1]},
        index=index,
    )
    custom_factors = pd.DataFrame(
        {"qual_quality": [55.0]},
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-04-30"), "SH600001")],
            names=["datetime", "instrument"],
        ),
    )

    handler = LazyHandler(base_frame)
    injected = _inject_custom_factors_into_handler(handler, custom_factors)

    assert injected == 1
    assert ("feature", "qual_quality") in handler._data.columns
    assert handler._data.loc[(pd.Timestamp("2025-05-01"), "SH600001"), ("feature", "qual_quality")] == 55.0


def test_inject_custom_factors_refreshes_processed_handler_views():
    class ProcessedHandler:
        def __init__(self, frame):
            self._data = frame
            self._infer = frame
            self._learn = frame.copy()
            self.process_calls = 0

        def process_data(self, with_fit=False):
            self.process_calls += 1
            self._infer = self._data
            self._learn = self._data.copy()

    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-04-30"), "SH600001"),
            (pd.Timestamp("2025-05-01"), "SH600001"),
        ],
        names=["datetime", "instrument"],
    )
    base_frame = pd.DataFrame(
        {("feature", "alpha_close"): [1.0, 1.1]},
        index=index,
    )
    custom_factors = pd.DataFrame(
        {"qual_quality": [55.0]},
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-04-30"), "SH600001")],
            names=["datetime", "instrument"],
        ),
    )

    handler = ProcessedHandler(base_frame)
    injected = _inject_custom_factors_into_handler(handler, custom_factors)

    assert injected == 1
    assert handler.process_calls == 1
    assert ("feature", "qual_quality") in handler._infer.columns
    assert ("feature", "qual_quality") in handler._learn.columns
