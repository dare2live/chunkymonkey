import sys
from pathlib import Path

from conftest import duck_mem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_price_kline_tdxhub as builder  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def stocks_records(self, market):
        if market == 1:
            return [{"code": "600001"}, {"code": "900001"}]
        if market == 0:
            return [{"code": "000001"}, {"code": "300001"}, {"code": "200001"}]
        return []

    def bars_records(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["start"] == 0:
            return [
                {
                    "datetime": "2026-05-04T00:00:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
                {
                    "datetime": "2026-05-04T10:00:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
            ]
        return []


def test_pull_and_normalize_price_records():
    client = FakeClient()

    records = builder.pull_one_stock(client, "1", pages=2)
    normalized = builder.normalize(records, "batch-1")

    assert len(records) == 2
    assert client.calls[0]["symbol"] == "1"
    assert "adjust" not in client.calls[0]
    assert normalized == [
        {
            "code": "000001",
            "date": "2026-05-04",
            "freq": "daily",
            "adjust": "qfq",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "amount": 10500.0,
            "factor": 1.0,
            "source": "tdxhub",
            "batch_id": "batch-1",
        }
    ]


def test_pull_one_stock_can_fetch_raw_incremental_without_adjust():
    client = FakeClient()

    records = builder.pull_one_stock(client, "1", pages=1, adjust=None)

    assert len(records) == 2
    assert "adjust" not in client.calls[0]


def test_retry_helpers_use_shared_tdx_retry(monkeypatch):
    clients = []

    def fake_retry(operation, **kwargs):
        client = FakeClient()
        clients.append(client)
        return operation(client), "tdxhub_test:7709"

    monkeypatch.setattr(builder, "call_tdx_quotes_with_retry", fake_retry)

    stock_list = builder.load_a_stock_list_with_retry()
    records, source = builder.pull_one_stock_with_retry("000001", pages=1)
    normalized = builder.normalize(records, "batch-1", source_name=source)

    assert stock_list == [("600001", 1), ("000001", 0), ("300001", 0)]
    assert source == "tdxhub_test:7709"
    assert normalized[0]["source"] == "tdxhub_test:7709"
    assert "adjust" not in clients[-1].calls[0]


def test_adjusted_records_mode_is_rejected_before_tdx_retry(monkeypatch):
    monkeypatch.setattr(
        builder,
        "call_tdx_quotes_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not run")),
    )

    try:
        builder.pull_one_stock_with_retry("000001", pages=1, adjust="qfq")
    except NotImplementedError as exc:
        assert "does not support adjusted qfq" in str(exc)
    else:
        raise AssertionError("adjusted records mode should be rejected")


def test_load_local_active_a_stock_list_filters_supported_a_share_codes(monkeypatch):
    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE dim_active_a_stock (
            stock_code TEXT,
            stock_name TEXT,
            market TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO dim_active_a_stock VALUES (?, ?, ?, ?, ?)",
        [
            ("600001", "沪市主板", "SH", "unit", "2026-05-07"),
            ("688001", "科创板", "SH", "unit", "2026-05-07"),
            ("000001", "深市主板", "SZ", "unit", "2026-05-07"),
            ("300001", "创业板", "SZ", "unit", "2026-05-07"),
            ("430001", "北交所", "BJ", "unit", "2026-05-07"),
            ("159001", "ETF", "SZ", "unit", "2026-05-07"),
        ],
    )
    monkeypatch.setattr(builder, "get_business_conn", lambda: conn)

    stock_list, source = builder.load_local_active_a_stock_list(min_rows=1)

    assert source == "dim_active_a_stock"
    assert stock_list == [
        ("000001", 0),
        ("300001", 0),
        ("600001", 1),
        ("688001", 1),
    ]


def test_load_local_active_a_stock_list_reports_insufficient_cache(monkeypatch):
    conn = duck_mem()
    conn.execute("CREATE TABLE dim_active_a_stock (stock_code TEXT, market TEXT)")
    conn.execute("INSERT INTO dim_active_a_stock VALUES ('000001', 'SZ')")
    monkeypatch.setattr(builder, "get_business_conn", lambda: conn)

    stock_list, source = builder.load_local_active_a_stock_list(min_rows=2)

    assert stock_list == []
    assert source == "dim_active_a_stock_insufficient:1"


def test_main_skip_existing_exits_before_tdx_when_local_preflight_has_no_stale(monkeypatch):
    conn = duck_mem()
    conn.executescript(builder.TABLE_DDL)
    conn.execute(
        """
        INSERT INTO price_kline_tdxhub (
            code, date, freq, adjust, open, high, low, close,
            volume, amount, factor, source, batch_id
        ) VALUES (
            '000001', '2026-05-06', 'daily', 'qfq', 10, 11, 9, 10.5,
            1000, 10500, 1.0, 'tdxhub', 'unit'
        )
        """
    )
    monkeypatch.setattr(builder, "get_market_conn", lambda: conn)
    monkeypatch.setattr(builder, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        builder,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0)], "dim_active_a_stock"),
    )
    monkeypatch.setattr(
        builder,
        "open_quotes_client_with_retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("TDXHub stock list should not be fetched")),
    )
    monkeypatch.setattr(sys, "argv", ["build_price_kline_tdxhub.py", "--skip-existing"])

    builder.main()


def test_main_buffers_incremental_rows_before_duckdb_write(monkeypatch):
    conn = duck_mem()
    conn.executescript(builder.TABLE_DDL)
    write_sizes = []
    original_write_batch = builder.write_batch

    def fake_fetch(code, **_kwargs):
        return (
            [
                {
                    "code": code,
                    "date": "2026-05-06",
                    "freq": "daily",
                    "adjust": "qfq",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000.0,
                    "amount": 10500.0,
                    "factor": 1.0,
                    "source": "tdxhub_unit_raw_incremental",
                    "batch_id": "unit",
                }
            ],
            "tdxhub_unit_raw_incremental",
            [{"server": ("1.1.1.1", 7709), "ok": True, "elapsed_sec": 0.02}],
        )

    def spy_write_batch(write_conn, rows):
        write_sizes.append(len(rows))
        return original_write_batch(write_conn, rows)

    class CloseTrackingConn:
        def __init__(self, inner):
            self.inner = inner
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            self.closed = True

    wrapped_conn = CloseTrackingConn(conn)
    monkeypatch.setattr(builder, "get_market_conn", lambda: wrapped_conn)
    monkeypatch.setattr(builder, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        builder,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0), ("000002", 0)], "dim_active_a_stock"),
    )
    monkeypatch.setattr(builder, "fetch_one_stock_normalized_with_attempts", fake_fetch)
    monkeypatch.setattr(builder, "write_batch", spy_write_batch)
    monkeypatch.setattr(
        builder,
        "open_quotes_client_with_retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("TDXHub stock list should not be fetched")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_price_kline_tdxhub.py",
            "--skip-existing",
            "--workers",
            "1",
            "--write-batch-rows",
            "99",
            "--log-every",
            "99",
        ],
    )

    builder.main()

    assert write_sizes == [2]
    health_row = conn.execute(
        """
        SELECT success_count, source_run_id
          FROM mart_tdx_server_health
         WHERE server_host = '1.1.1.1' AND capability = 'kline_daily_raw'
        """
    ).fetchone()
    assert health_row["success_count"] == 2
    assert str(health_row["source_run_id"]).startswith("tdxhub_")
    assert wrapped_conn.closed is True


def test_resolve_kline_worker_count_parallelizes_qfq_by_default():
    assert builder.resolve_kline_worker_count(
        explicit_workers=None,
        env_workers=0,
        pull_adjust="qfq",
    ) == builder.DEFAULT_QFQ_WORKERS
    assert builder.resolve_kline_worker_count(
        explicit_workers=None,
        env_workers=0,
        pull_adjust=None,
    ) == builder.DEFAULT_RAW_INCREMENTAL_WORKERS
    assert builder.resolve_kline_worker_count(
        explicit_workers=2,
        env_workers=9,
        pull_adjust="qfq",
    ) == 2
    assert builder.resolve_kline_worker_count(
        explicit_workers=None,
        env_workers=9,
        pull_adjust="qfq",
    ) == 9


def test_main_parallel_raw_fetch_rotates_across_server_pool(monkeypatch):
    conn = duck_mem()
    conn.executescript(builder.TABLE_DDL)
    prefer_last_success_values = []

    def fake_fetch(code, **kwargs):
        prefer_last_success_values.append(kwargs["prefer_last_success"])
        return (
            [
                {
                    "code": code,
                    "date": "2026-05-06",
                    "freq": "daily",
                    "adjust": "qfq",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000.0,
                    "amount": 10500.0,
                    "factor": 1.0,
                    "source": "tdxhub_unit",
                    "batch_id": "unit",
                }
            ],
            "tdxhub_unit",
            [{"server": ("1.1.1.1", 7709), "ok": True, "elapsed_sec": 0.02}],
        )

    monkeypatch.setattr(builder, "get_market_conn", lambda: conn)
    monkeypatch.setattr(builder, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        builder,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0), ("000002", 0)], "dim_active_a_stock"),
    )
    monkeypatch.setattr(
        builder,
        "open_quotes_client_with_retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("stock list network should not run")),
    )
    monkeypatch.setattr(builder, "fetch_one_stock_normalized_with_attempts", fake_fetch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_price_kline_tdxhub.py",
            "--skip-existing",
            "--workers",
            "2",
            "--write-batch-rows",
            "99",
            "--log-every",
            "99",
        ],
    )

    builder.main()

    assert prefer_last_success_values == [False, False]


def test_main_rejects_unsupported_full_adjusted_rebuild(monkeypatch):
    conn = duck_mem()
    conn.executescript(builder.TABLE_DDL)
    monkeypatch.setattr(builder, "get_market_conn", lambda: conn)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_price_kline_tdxhub.py", "--limit", "1"],
    )

    try:
        builder.main()
    except RuntimeError as exc:
        assert "does not support adjusted qfq" in str(exc)
    else:
        raise AssertionError("full adjusted rebuild should be rejected")


def test_main_rejects_truncate_raw_refill_before_delete(monkeypatch):
    conn = duck_mem()
    conn.executescript(builder.TABLE_DDL)
    conn.execute(
        """
        INSERT INTO price_kline_tdxhub (
            code, date, freq, adjust, open, high, low, close,
            volume, amount, factor, source, batch_id
        ) VALUES (
            '000001', '2026-05-06', 'daily', 'qfq', 10, 11, 9, 10.5,
            1000, 10500, 1.0, 'unit', 'unit'
        )
        """
    )

    class CloseTrackingConn:
        def __init__(self, inner):
            self.inner = inner
            self.closed = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            self.closed = True

    wrapped_conn = CloseTrackingConn(conn)
    monkeypatch.setattr(builder, "get_market_conn", lambda: wrapped_conn)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_price_kline_tdxhub.py", "--skip-existing", "--truncate"],
    )

    try:
        builder.main()
    except RuntimeError as exc:
        assert "Refusing to truncate" in str(exc)
    else:
        raise AssertionError("raw truncate refill should be rejected")

    rows = conn.execute("SELECT COUNT(*) FROM price_kline_tdxhub").fetchone()[0]
    assert rows == 1
    assert wrapped_conn.closed is True


def test_write_batch_uses_records():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        rows = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-04",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                }
            ],
            "batch-1",
        )

        assert builder.write_batch(conn, rows) == 1
        assert builder.write_batch(conn, rows) == 1

        saved = conn.execute(
            "SELECT code, date, close, source, batch_id FROM price_kline_tdxhub"
        ).fetchall()
        assert [tuple(row) for row in saved] == [("000001", "2026-05-04", 10.5, "tdxhub", "batch-1")]
    finally:
        conn.close()


def test_write_batch_does_not_require_primary_key_constraint():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE price_kline_tdxhub (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                factor REAL,
                source TEXT,
                batch_id TEXT,
                ingested_at TEXT
            );
            INSERT INTO price_kline_tdxhub VALUES
                ('000001', '2026-05-04', 'daily', 'qfq', 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 'old', 'old-batch', '2026-05-04');
            """
        )
        rows = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-04",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                }
            ],
            "batch-1",
        )

        assert builder.write_batch(conn, rows) == 1
        saved = conn.execute(
            "SELECT code, date, close, source, batch_id FROM price_kline_tdxhub"
        ).fetchall()

        assert [tuple(row) for row in saved] == [("000001", "2026-05-04", 10.5, "tdxhub", "batch-1")]
    finally:
        conn.close()


def test_incremental_filter_uses_per_stock_latest_date():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        existing = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-03",
                    "open": 9,
                    "high": 10,
                    "low": 8,
                    "close": 9.5,
                    "vol": 900,
                    "amount": 9000,
                    "factor": 1,
                }
            ],
            "batch-old",
        )
        builder.write_batch(conn, existing)

        latest = builder.load_latest_dates(conn)
        rows = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-03",
                    "open": 9,
                    "high": 10,
                    "low": 8,
                    "close": 9.5,
                    "vol": 900,
                    "amount": 9000,
                    "factor": 1,
                },
                {
                    "code": "000001",
                    "datetime": "2026-05-04",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
                {
                    "code": "000002",
                    "datetime": "2026-05-01",
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "vol": 2000,
                    "amount": 41000,
                    "factor": 1,
                },
            ],
            "batch-new",
        )

        filtered = builder.filter_after_latest(rows, latest)

        assert [(row["code"], row["date"]) for row in filtered] == [
            ("000001", "2026-05-04"),
            ("000002", "2026-05-01"),
        ]
    finally:
        conn.close()


def test_filter_stale_stock_list_uses_target_date():
    stock_list = [("000001", 0), ("000002", 0), ("600001", 1)]
    latest_dates = {
        "000001": "2026-04-30",
        "000002": "2026-04-29",
    }

    assert builder.filter_stale_stock_list(stock_list, latest_dates, "2026-04-30") == [
        ("000002", 0),
        ("600001", 1),
    ]


def test_choose_incremental_target_date_prefers_calendar(monkeypatch):
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE price_kline (
                code TEXT, date TEXT, freq TEXT, adjust TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO price_kline VALUES ('000001', '2026-04-30', 'daily', 'qfq')"
        )
        monkeypatch.setattr(builder, "load_calendar_target_date", lambda: "2026-05-04")

        assert builder.choose_incremental_target_date(conn) == (
            "2026-05-04",
            "dim_trading_calendar",
        )
    finally:
        conn.close()


def test_choose_incremental_target_date_falls_back_when_calendar_missing(monkeypatch):
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE price_kline (
                code TEXT, date TEXT, freq TEXT, adjust TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO price_kline VALUES ('000001', '2026-04-30', 'daily', 'qfq')"
        )
        monkeypatch.setattr(builder, "load_calendar_target_date", lambda: None)

        assert builder.choose_incremental_target_date(conn) == (
            "2026-04-30",
            "fallback_price_kline",
        )
    finally:
        conn.close()


def test_choose_incremental_target_date_respects_cli_override(monkeypatch):
    conn = duck_mem()
    try:
        monkeypatch.setattr(
            builder,
            "load_calendar_target_date",
            lambda: (_ for _ in ()).throw(AssertionError("calendar should not be read")),
        )

        assert builder.choose_incremental_target_date(conn, "2026-04-29") == (
            "2026-04-29",
            "cli",
        )
    finally:
        conn.close()


def test_load_xdxr_gap_codes_finds_events_after_latest_before_target():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE price_xdxr (code TEXT, date TEXT, category INTEGER)")
        conn.executemany(
            "INSERT INTO price_xdxr VALUES (?, ?, ?)",
            [
                ("000001", "2026-04-23", 1),
                ("000001", "2026-04-28", 1),
                ("000002", "2026-05-06", 1),
                ("600001", "2026-04-30", 9),
            ],
        )

        assert builder.load_xdxr_gap_codes(
            conn,
            {
                "000001": "2026-04-23",
                "000002": "2026-04-23",
                "600001": "2026-04-29",
            },
            "2026-04-30",
        ) == {"000001", "600001"}
    finally:
        conn.close()


def test_load_xdxr_gap_events_returns_price_adjusting_events_only():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE price_xdxr (
                code TEXT,
                date TEXT,
                category INTEGER,
                name TEXT,
                fenhong REAL,
                peigujia REAL,
                songzhuangu REAL,
                peigu REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO price_xdxr VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("000001", "2026-04-28", 1, "除权除息", 1.0, 0.0, 4.0, 0.0),
                ("000001", "2026-04-28", 9, "转配股上市", None, None, None, None),
                ("000002", "2026-05-06", 1, "除权除息", 1.0, 0.0, 0.0, 0.0),
            ],
        )

        events = builder.load_xdxr_gap_events(
            conn,
            {"000001": "2026-04-23", "000002": "2026-04-23"},
            "2026-04-30",
        )

        assert list(events) == ["000001"]
        assert events["000001"][0]["fenhong"] == 1.0
        assert events["000001"][0]["songzhuangu"] == 4.0
    finally:
        conn.close()


def test_compute_xdxr_adjustment_factor_uses_tdx_per_ten_fields():
    event = {"fenhong": 1.0, "songzhuangu": 4.0, "peigu": 0.0, "peigujia": 0.0}

    factor = builder.compute_xdxr_adjustment_factor(100.0, event)

    assert round(factor, 8) == round((100.0 - 0.1) / (100.0 * 1.4), 8)


def test_apply_xdxr_adjustment_events_is_idempotent_and_adjusts_history():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        rows = builder.normalize(
            [
                {"code": "000001", "datetime": "2026-04-22", "open": 90, "high": 110, "low": 80, "close": 95, "factor": 1},
                {"code": "000001", "datetime": "2026-04-23", "open": 100, "high": 110, "low": 90, "close": 100, "factor": 1},
            ],
            "batch-old",
        )
        builder.write_batch(conn, rows)
        event = {
            "code": "000001",
            "date": "2026-04-24",
            "category": 1,
            "fenhong": 1.0,
            "songzhuangu": 4.0,
            "peigu": 0.0,
            "peigujia": 0.0,
        }

        applied = builder.apply_xdxr_adjustment_events(
            conn, "000001", [event], source_name="tdxhub_raw", batch_id="batch-new"
        )
        first_close = conn.execute(
            "SELECT close FROM price_kline_tdxhub WHERE code='000001' AND date='2026-04-23'"
        ).fetchone()[0]
        applied_again = builder.apply_xdxr_adjustment_events(
            conn, "000001", [event], source_name="tdxhub_raw", batch_id="batch-new"
        )
        second_close = conn.execute(
            "SELECT close FROM price_kline_tdxhub WHERE code='000001' AND date='2026-04-23'"
        ).fetchone()[0]
        event_rows = conn.execute(
            "SELECT COUNT(*) FROM price_kline_tdxhub_adjustment_event WHERE code='000001'"
        ).fetchone()[0]

        assert len(applied) == 1
        assert len(applied_again) == 1
        assert abs(first_close - 100.0 * applied[0]["adjust_factor"]) < 1e-4
        assert second_close == first_close
        assert event_rows == 1
    finally:
        conn.close()


def test_ordered_xdxr_events_sorts_by_event_date():
    events = [
        {"date": "2026-04-24", "code": "000001"},
        {"date": "", "code": "000002"},
        {"date": "2026-04-22", "code": "000003"},
    ]

    ordered = builder._ordered_xdxr_events(events)

    assert [event["code"] for event in ordered] == ["000002", "000003", "000001"]


def test_calibrate_xdxr_factor_from_fallback_when_formula_materially_differs():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE price_kline (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT,
                close REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO price_kline VALUES (?, ?, 'daily', 'qfq', ?)",
            [
                ("000001", "2026-04-24", 67.6),
                ("000001", "2026-04-27", 68.276),
            ],
        )
        raw_rows = [
            {"date": "2026-04-24", "close": 100.0},
            {"date": "2026-04-27", "close": 101.0},
        ]

        factor, source = builder.calibrate_xdxr_factor_from_fallback(
            conn,
            "000001",
            "2026-04-28",
            raw_rows,
            formula_factor=0.66,
        )

        assert source == "fallback_calibrated"
        assert round(factor, 4) == 0.676
    finally:
        conn.close()


def test_recalibrate_existing_xdxr_adjustments_from_fallback_updates_history():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        conn.execute(
            """
            CREATE TABLE price_kline (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT,
                close REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO price_kline_tdxhub (
                code, date, freq, adjust, open, high, low, close, factor, source, batch_id
            ) VALUES ('000001', '2026-04-27', 'daily', 'qfq', 66.0, 66.0, 66.0, 66.0, 0.66, 'tdxhub', 'old')
            """
        )
        conn.execute(
            """
            INSERT INTO price_kline VALUES ('000001', '2026-04-27', 'daily', 'qfq', 67.6)
            """
        )
        conn.execute(
            """
            INSERT INTO price_kline_tdxhub_adjustment_event (
                code, event_date, event_hash, adjust_factor, prev_close, source, batch_id
            ) VALUES ('000001', '2026-04-28', 'hash1', 0.66, 100.0, 'tdxhub_raw', 'batch')
            """
        )

        result = builder.recalibrate_existing_xdxr_adjustments_from_fallback(
            conn,
            start_date="2026-04-28",
            end_date="2026-04-28",
        )
        row = conn.execute(
            "SELECT close, factor FROM price_kline_tdxhub WHERE code='000001'"
        ).fetchone()
        event = conn.execute(
            "SELECT adjust_factor, source FROM price_kline_tdxhub_adjustment_event WHERE code='000001'"
        ).fetchone()

        assert result == {"checked": 1, "changed": 1}
        assert abs(row[0] - 67.6) < 1e-5
        assert abs(row[1] - 0.676) < 1e-5
        assert event[1].endswith("_fallback_calibrated")
    finally:
        conn.close()


def test_adjust_rows_for_xdxr_events_applies_future_event_factors_only():
    rows = [
        {
            "code": "000001",
            "date": "2026-04-23",
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 100.0,
            "factor": 1.0,
            "source": "tdxhub_raw_incremental",
        },
        {
            "code": "000001",
            "date": "2026-04-24",
            "open": 71.0,
            "high": 80.0,
            "low": 70.0,
            "close": 75.0,
            "factor": 1.0,
            "source": "tdxhub_raw_incremental",
        },
    ]
    event = {"date": "2026-04-24", "adjust_factor": 0.7}

    adjusted, count = builder.adjust_rows_for_xdxr_events(rows, [event])

    assert count == 1
    assert adjusted[0]["close"] == 70.0
    assert adjusted[0]["factor"] == 0.7
    assert adjusted[0]["source"].endswith("_xdxr_adjusted")
    assert adjusted[1]["close"] == 75.0


def test_adjust_rows_for_xdxr_events_combines_future_event_factors_by_date():
    rows = [
        {"code": "000001", "date": "2026-04-23", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "factor": 1.0, "source": "tdxhub_raw"},
        {"code": "000001", "date": "2026-04-24", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "factor": 1.0, "source": "tdxhub_raw"},
        {"code": "000001", "date": "2026-04-26", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "factor": 1.0, "source": "tdxhub_raw"},
    ]
    events = [
        {"date": "2026-04-26", "adjust_factor": 0.5},
        {"date": "2026-04-24", "adjust_factor": 0.7},
    ]

    adjusted, count = builder.adjust_rows_for_xdxr_events(rows, events)

    assert count == 2
    assert adjusted[0]["close"] == 35.0
    assert adjusted[0]["factor"] == 0.35
    assert adjusted[1]["close"] == 50.0
    assert adjusted[1]["factor"] == 0.5
    assert adjusted[2]["close"] == 100.0


def test_future_xdxr_factor_by_date_uses_strict_future_events():
    rows = [
        {"date": "2026-04-23"},
        {"date": "2026-04-24"},
        {"date": "2026-04-26"},
    ]
    event_factors = [
        ("2026-04-26", 0.5),
        ("2026-04-24", 0.7),
    ]

    factors = builder._future_xdxr_factor_by_date(rows, event_factors)

    assert factors == {
        "2026-04-23": 0.35,
        "2026-04-24": 0.5,
        "2026-04-26": 1.0,
    }


def test_filter_raw_incremental_qfq_safe_drops_xdxr_gap_codes():
    rows = [
        {"code": "000001", "date": "2026-04-28"},
        {"code": "000002", "date": "2026-04-28"},
    ]

    kept, dropped = builder.filter_raw_incremental_qfq_safe(rows, {"000001"})

    assert kept == [{"code": "000002", "date": "2026-04-28"}]
    assert dropped == 1
