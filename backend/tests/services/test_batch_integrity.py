"""Registry completeness truth must match the write-path normalization contract."""
from __future__ import annotations

from conftest import duck_mem
from services.data_sources.batch_integrity import (
    complete_batch_dates,
    latest_complete_batch,
)


def _spec(**overrides):
    spec = {
        "target_table": "raw_probe",
        "grain": ["ts_code", "trade_date"],
        "date_param": "trade_date",
        "min_rows_per_batch": 3,
        "batch_completeness": {
            "group_from": {"column": "ts_code", "transform": "exchange_suffix"},
            "required_groups": ["SH", "SZ"],
        },
    }
    spec.update(overrides)
    return spec


def test_complete_batch_dates_counts_distinct_registry_grain():
    conn = duck_mem()
    conn.execute("CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT, built_at TEXT)")
    conn.executemany(
        "INSERT INTO raw_probe VALUES (?, '20260709', '2026-07-10T00:00:00Z')",
        [("600000.SH",), ("600000.SH",), ("000001.SZ",), ("000001.SZ",)],
    )

    assert complete_batch_dates(conn, _spec()) == set()


def test_complete_batch_dates_counts_full_landing_population_including_bj():
    """A4: landing completeness ignores universe_filter; BJ residue counts."""
    conn = duck_mem()
    conn.execute("CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT, built_at TEXT)")
    conn.executemany(
        "INSERT INTO raw_probe VALUES (?, '20260709', '2026-07-10T00:00:00Z')",
        [("600000.SH",), ("000001.SZ",), ("830001.BJ",)],
    )

    assert complete_batch_dates(conn, _spec(universe_filter=True)) == {"20260709"}


def test_complete_batch_dates_normalizes_timestamp_and_frontier_metadata():
    conn = duck_mem()
    conn.execute(
        "CREATE TABLE raw_probe (ts_code TEXT, trade_date TIMESTAMP, built_at TIMESTAMP)"
    )
    conn.executemany(
        "INSERT INTO raw_probe VALUES (?, TIMESTAMP '2026-07-09 00:00:00', "
        "TIMESTAMP '2026-07-10 06:48:49')",
        [("600000.SH",), ("000001.SZ",)],
    )
    spec = _spec(min_rows_per_batch=2)

    assert complete_batch_dates(conn, spec) == {"20260709"}
    frontier = latest_complete_batch(conn, spec)
    assert frontier is not None
    assert frontier.last_date == "20260709"
    assert frontier.row_count == 2
    assert str(frontier.last_success_at).startswith("2026-07-10 06:48:49")
