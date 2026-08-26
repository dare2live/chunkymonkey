from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from services.data_sources.nominal_ohlcv_schema import PROVIDER_FIELDS
from services.data_sources.security_day_partition import canonical_content_hash
from services.institution_follow_spec_paper import (
    FollowSpecPaperError,
    events_from_holdertrade_rows,
    simulate_follow_spec_paper,
)
from services.strategy_paper import FOLLOWER_PNL_SOURCE, DisclosureEvent
from services.strategy_spec import load_strategy_spec


DAYS = (
    "20250506",
    "20250507",
    "20250508",
    "20250509",
    "20250512",
    "20250513",
)
CODE = "000001.SZ"


class _RouterConn:
    def __init__(
        self,
        pointer_rows: list[tuple[Any, ...]],
        canonical_rows: list[tuple[Any, ...]],
        holdertrade_rows: list[tuple[Any, ...]],
    ) -> None:
        self._pointer_rows = pointer_rows
        self._canonical_rows = canonical_rows
        self._holdertrade_rows = holdertrade_rows
        self._rows: list[tuple[Any, ...]] = []
        self.queries: list[str] = []

    def execute(self, sql: str, _params=None):
        self.queries.append(sql)
        if "canonical_stk_holdertrade_announcement" in sql:
            self._rows = self._holdertrade_rows
        elif "canonical_nominal_ohlcv_daily" in sql:
            self._rows = self._canonical_rows
        else:
            self._rows = self._pointer_rows
        return self

    def fetchall(self):
        return list(self._rows)


def _bar(day: str, *, ts_code: str = CODE, close: float = 10.0) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "trade_date": date(int(day[:4]), int(day[4:6]), int(day[6:8])),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": 9.9,
        "change": close - 9.9,
        "pct_chg": (close / 9.9 - 1.0) * 100.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }


def _hash(day: str, *, close: float = 10.0) -> str:
    return canonical_content_hash([_bar(day, close=close)], PROVIDER_FIELDS)


def _canonical_tuple(day: str, *, ts_code: str = CODE, close: float = 10.0) -> tuple[Any, ...]:
    row = _bar(day, ts_code=ts_code, close=close)
    return tuple(row[field] for field in PROVIDER_FIELDS)


def _offline_bars() -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    prev = 10.0
    for day in DAYS:
        out[day] = [
            {
                "ts_code": CODE,
                "open": 10.0 if day != "20250513" else 11.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "pre_close": prev,
                "vol": 1000.0,
            }
        ]
        prev = 10.0
    return out


def _snap(days: list[str], *, holdertrade: list[str] | None = None) -> dict[str, Any]:
    hashes = {d: _hash(d) for d in days}
    return {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": hashes[d],
                        "row_count": 1,
                        "config_hash": "cfg1",
                        "contract_hash": "contract1",
                        "batch_id": f"batch-{d}",
                        "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                    }
                    for d in days
                ]
            },
            "stk_holdertrade": {
                "accepted": [{"partition": d} for d in (holdertrade or days[:1])]
            },
        }
    }


def test_offline_events_run_follow_spec_paper() -> None:
    fills = simulate_follow_spec_paper(
        ts_code=CODE,
        events=(
            DisclosureEvent(CODE, "20250506", "increase"),
            DisclosureEvent(CODE, "20250512", "decrease"),
        ),
        bars_by_day=_offline_bars(),
    )
    assert len(fills) == 1
    assert fills[0].status == "filled"
    assert fills[0].entry_date == "20250507"
    assert fills[0].exit_date == "20250513"
    assert fills[0].pnl_source == FOLLOWER_PNL_SOURCE
    assert fills[0].pnl_source != "alpha_c1"


def test_null_ann_date_rows_are_dropped() -> None:
    events = events_from_holdertrade_rows(
        (
            {"ts_code": CODE, "ann_date": None, "in_de": "IN"},
            {"ts_code": CODE, "ann_date": "20250506", "in_de": "IN"},
            {"ts_code": CODE, "ann_date": "", "in_de": "DE"},
        ),
        ts_code=CODE,
    )
    assert events == (DisclosureEvent(CODE, "20250506", "increase"),)


def test_holdout_event_is_refused() -> None:
    with pytest.raises(FollowSpecPaperError, match="holdout_partition_refused"):
        simulate_follow_spec_paper(
            ts_code=CODE,
            events=(DisclosureEvent(CODE, "20250601", "increase"),),
            bars_by_day=_offline_bars(),
        )


def test_snapshot_events_load_via_one_name_pointer() -> None:
    days = list(DAYS)
    conn = _RouterConn(
        [
            (day, f"batch-{day}", 1, _hash(day), "contract1", "cfg1")
            for day in days
        ],
        [_canonical_tuple(day) for day in days],
        (
            (CODE, "20250506", "IN"),
            (CODE, "20250512", "DE"),
        ),
    )
    fills = simulate_follow_spec_paper(
        ts_code=CODE,
        snapshot=_snap(days, holdertrade=["20250506", "20250512"]),
        conn=conn,
    )
    assert fills[0].pnl_source == FOLLOWER_PNL_SOURCE
    assert fills[0].status == "filled"
    holder_sql = next(
        sql for sql in conn.queries if "canonical_stk_holdertrade_announcement" in sql
    )
    bar_sql = next(sql for sql in conn.queries if "canonical_nominal_ohlcv_daily" in sql)
    assert "ts_code = ?" in holder_sql
    assert "ts_code = ?" in bar_sql
    assert "load_snapshot_bound_nominal_bars_by_day" not in "".join(conn.queries)


def test_holdout_holdertrade_partition_refused_before_query() -> None:
    conn = _RouterConn([], [], ((CODE, "20250601", "IN"),))
    snapshot = _snap(
        ["20250530"],
        holdertrade=["20250601"],
    )
    with pytest.raises(FollowSpecPaperError, match="holdout_partition_refused"):
        simulate_follow_spec_paper(ts_code=CODE, snapshot=snapshot, conn=conn)
    assert conn.queries == []


def test_follow_spec_paper_does_not_import_overnight_fills() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "institution_follow_spec_paper.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "simulate_paper_fills" not in text
    assert "load_snapshot_bound_nominal_bars_by_day" not in text
    assert "consume_single_touch" not in text
    assert "institution_follow_paper" not in text
    live = load_strategy_spec("institution_follow_v1")
    assert live.pnl_source == FOLLOWER_PNL_SOURCE
