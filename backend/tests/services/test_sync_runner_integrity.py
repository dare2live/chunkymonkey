"""sync_runner 写入完整性与原子性契约。"""
from __future__ import annotations

import pytest
import sys

from services.data_sources import sync_runner as sr
from services.duck_adapter import connect


@pytest.fixture(autouse=True)
def _successful_cli_authorization(monkeypatch):
    monkeypatch.setattr(sr, "_authorization_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(sr, "_calendar_preflight", lambda _domains: None)


def _margin_spec(**overrides):
    spec = {
        "domain": "margin_detail",
        "source": "tushare",
        "api": "margin_detail",
        "target_table": "raw_tushare_margin_detail",
        "grain": ["ts_code", "trade_date"],
        "batch_mode": "by_trade_date",
        "data_start": "20190102",
        "universe_filter": True,
        "min_rows_per_batch": 1,
        "batch_completeness": {
            "group_from": {"column": "ts_code", "transform": "exchange_suffix"},
            "required_groups": ["SH", "SZ"],
        },
    }
    spec.update(overrides)
    return spec


def test_margin_detail_missing_required_exchange_group_does_not_replace_existing_rows():
    """供应方只返 SH 时必须零写入；旧的完整批次不能被 partial 覆盖。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_tushare_margin_detail "
        "(ts_code VARCHAR, trade_date VARCHAR, rzye DOUBLE, built_at VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_margin_detail VALUES "
        "('600000.SH', '20260714', 10.0, 'old'), "
        "('000001.SZ', '20260714', 20.0, 'old')"
    )

    with pytest.raises(sr.BatchCompletenessError, match="required_groups.*SZ"):
        sr._write_batch(
            conn,
            _margin_spec(),
            [{"ts_code": "600000.SH", "trade_date": "20260714", "rzye": 99.0}],
        )

    rows = conn.execute(
        "SELECT ts_code, rzye FROM raw_tushare_margin_detail ORDER BY ts_code"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("000001.SZ", 20.0), ("600000.SH", 10.0)]


def test_margin_bse_write_contract_uses_margin_business_start_and_is_atomic():
    """2023-02-13 前两所可写；两融启动日起缺 BSE 必须拒写并保留旧完整批。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_tushare_margin "
        "(trade_date VARCHAR, exchange_id VARCHAR, rzye DOUBLE, built_at VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO raw_tushare_margin VALUES ('20230213', ?, 10.0, 'old')",
        [("SSE",), ("SZSE",), ("BSE",)],
    )
    spec = {
        "domain": "margin",
        "target_table": "raw_tushare_margin",
        "grain": ["trade_date", "exchange_id"],
        "date_param": "trade_date",
        "min_rows_per_batch": 2,
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {"BSE": "20230213"},
        },
    }

    assert sr._write_batch(
        conn,
        spec,
        [
            {"trade_date": "20230210", "exchange_id": "SSE", "rzye": 1.0},
            {"trade_date": "20230210", "exchange_id": "SZSE", "rzye": 2.0},
        ],
    ) == 2

    with pytest.raises(sr.BatchCompletenessError, match="required_groups missing=\\['BSE'\\]"):
        sr._write_batch(
            conn,
            spec,
            [
                {"trade_date": "20230213", "exchange_id": "SSE", "rzye": 99.0},
                {"trade_date": "20230213", "exchange_id": "SZSE", "rzye": 99.0},
            ],
        )

    rows = conn.execute(
        "SELECT exchange_id, rzye, built_at FROM raw_tushare_margin "
        "WHERE trade_date='20230213' ORDER BY exchange_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("BSE", 10.0, "old"),
        ("SSE", 10.0, "old"),
        ("SZSE", 10.0, "old"),
    ]


def test_min_rows_is_evaluated_after_dedup_and_universe_filter():
    """被去重或被 universe 排除的行不能帮助批次跨过 min_rows 门。"""
    conn = connect(":memory:")
    spec = _margin_spec(
        min_rows_per_batch=2,
        batch_completeness={},
        target_table="raw_demo_post_filter",
    )
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260714", "rzye": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260714", "rzye": 2.0},
        {"ts_code": "830001.BJ", "trade_date": "20260714", "rzye": 3.0},
    ]

    with pytest.raises(sr.BatchCompletenessError, match="post_filter_rows=1 < min_rows=2"):
        sr._write_batch(conn, spec, rows)

    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_demo_post_filter'"
    ).fetchone()[0]
    assert exists == 0


def test_run_domain_records_incomplete_batch_without_advancing_watermark(monkeypatch):
    """完整性失败属于可追踪失败批，不得写库或拿失败日期推进 watermark。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_tushare_margin_detail "
        "(ts_code VARCHAR, trade_date VARCHAR, rzye DOUBLE, built_at VARCHAR)"
    )
    conn.execute(
        "INSERT INTO raw_tushare_margin_detail VALUES "
        "('000001.SZ', '20260714', 20.0, 'old')"
    )
    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {"margin_detail": _margin_spec()},
    }

    class _Adapter:
        def fetch_raw(self, api, **params):
            return [{"ts_code": "600000.SH", "trade_date": params["trade_date"], "rzye": 99.0}]

    recorded = {}
    monkeypatch.setattr(sr, "_adapter", lambda source: _Adapter())
    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(sr, "_target_conn", lambda spec: _NoClose(conn))
    monkeypatch.setattr(sr, "_trading_days", lambda start, end=None: ["20260714"])
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kwargs: recorded.update(kwargs))
    monkeypatch.setattr(sr.time, "sleep", lambda seconds: None)

    result = sr.run_domain(
        "margin_detail", start="20260714", end="20260714", registry=reg
    )

    assert result["failed_batches"] == 1
    assert result["last_date"] is None
    assert recorded["last_date"] is None and recorded["ok"] is False
    old = conn.execute(
        "SELECT rzye FROM raw_tushare_margin_detail WHERE ts_code='000001.SZ'"
    ).fetchone()[0]
    assert old == 20.0


def test_non_conversion_insert_failure_rolls_back_delete_and_unregisters_view():
    """任意 INSERT 故障都必须保住被替换旧行，并清理临时 DataFrame view。"""
    inner = connect(":memory:")
    inner.execute("CREATE TABLE raw_atomic (k VARCHAR, v INTEGER, built_at VARCHAR)")
    inner.execute("INSERT INTO raw_atomic VALUES ('same', 1, 'old')")

    class _FailInsert:
        _con = inner._con

        def execute(self, sql, params=None):
            if str(sql).lstrip().upper().startswith("INSERT INTO RAW_ATOMIC"):
                raise RuntimeError("simulated disk write failure")
            return inner.execute(sql, params)

    spec = {"domain": "atomic", "target_table": "raw_atomic", "grain": ["k"]}
    with pytest.raises(RuntimeError, match="simulated disk write failure"):
        sr._write_batch(_FailInsert(), spec, [{"k": "same", "v": 2}])

    row = inner.execute("SELECT k, v, built_at FROM raw_atomic").fetchone()
    assert tuple(row) == ("same", 1, "old")
    with pytest.raises(Exception):
        inner.execute("SELECT * FROM df").fetchall()


def test_replace_snapshot_removes_rows_absent_from_new_full_refresh():
    """full-refresh 快照不能只按新 grain MERGE，否则上游已删除行会永久残留。"""
    conn = connect(":memory:")
    spec = {
        "domain": "calendar",
        "target_table": "raw_calendar",
        "grain": ["exchange", "cal_date"],
        "write_mode": "replace_snapshot",
    }
    sr._write_batch(
        conn,
        spec,
        [
            {"exchange": "SSE", "cal_date": "20260714", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260715", "is_open": 1},
        ],
    )
    sr._write_batch(
        conn,
        spec,
        [{"exchange": "SSE", "cal_date": "20260715", "is_open": 0}],
    )

    assert [tuple(r) for r in conn.execute(
        "SELECT exchange, cal_date, is_open FROM raw_calendar"
    ).fetchall()] == [("SSE", "20260715", 0)]


def test_by_ts_code_explicit_window_is_applied_to_every_stock(monkeypatch):
    import services.universe as universe_mod

    monkeypatch.setattr(
        universe_mod, "get_active_universe", lambda conn, include_st=False: {"600519", "000001"}
    )

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _Conn())
    batches = sr._by_ts_code_batches(
        {"domain": "stk_factor_pro"}, start="20260710", end="20260714"
    )
    assert batches == [
        {"ts_code": "000001.SZ", "start_date": "20260710", "end_date": "20260714"},
        {"ts_code": "600519.SH", "start_date": "20260710", "end_date": "20260714"},
    ]


def test_all_due_skips_on_demand_domains(monkeypatch):
    reg = {
        "domains": {
            "daily": {"batch_mode": "by_trade_date"},
            "stk_factor_pro": {"batch_mode": "by_ts_code", "sync_policy": "on_demand"},
        }
    }
    calls = []
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: calls.append(domain)
        or {"domain": domain, "failed_batches": 0},
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due"])

    assert sr.main() == 0
    assert calls == ["daily"]


def test_full_refresh_merges_fixed_params_and_uses_freshness_column(monkeypatch):
    conn = connect(":memory:")
    calls = []

    class _Adapter:
        def fetch_raw(self, api, **params):
            calls.append(params)
            return [
                {"exchange": "SSE", "cal_date": "20260715", "is_open": 1},
                {"exchange": "SSE", "cal_date": "20261231", "is_open": 1},
            ]

    class _NoClose:
        def __getattr__(self, name):
            return getattr(conn, name)

        def close(self):
            pass

    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "trade_cal": {
                "source": "tushare",
                "api": "trade_cal",
                "target_table": "raw_trade_cal",
                "grain": ["exchange", "cal_date"],
                "batch_mode": "full_refresh",
                "fixed_params": {"exchange": "SSE"},
                "page_limit": 6000,
                "write_mode": "replace_snapshot",
                "freshness_date_column": "cal_date",
            }
        },
    }
    recorded = {}
    monkeypatch.setattr(sr, "_adapter", lambda source: _Adapter())
    monkeypatch.setattr(sr, "_target_conn", lambda spec: _NoClose())
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))
    monkeypatch.setattr(sr.time, "sleep", lambda seconds: None)

    result = sr.run_domain("trade_cal", registry=reg)

    assert calls == [{"exchange": "SSE", "limit": 6000, "offset": 0}]
    assert result["last_date"] == "20261231"
    assert recorded["last_date"] == "20261231"


def test_fetch_authorization_error_is_not_retried():
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    class _Denied:
        calls = 0

        def fetch_raw(self, api, **params):
            self.calls += 1
            raise TuShareAuthorizationError("auth_expired")

    adapter = _Denied()
    spec = {
        "domain": "daily",
        "api": "daily",
        "retry": {"max_attempts": 3, "backoff_seconds": [0, 0, 0]},
    }
    with pytest.raises(TuShareAuthorizationError):
        sr._fetch_with_retry(adapter, spec, {"trade_date": "20260715"})
    assert adapter.calls == 1


def test_cli_authorization_failure_exits_three_before_domain_execution(monkeypatch, capsys):
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    ran = []
    monkeypatch.setattr(
        sr,
        "_authorization_preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TuShareAuthorizationError("auth_expired")
        ),
        raising=False,
    )
    monkeypatch.setattr(sr, "_main_unlocked", lambda *_args: ran.append(True) or 0)
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "daily"])

    assert sr.main() == 3
    assert ran == []
    output = capsys.readouterr().out
    assert "auth_expired" in output
    assert "token" not in output.lower()


def test_cli_help_does_not_acquire_authorization(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        sr,
        "_authorization_preflight",
        lambda *_args: calls.append("user"),
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--help"])

    with pytest.raises(SystemExit) as caught:
        sr.main()

    assert caught.value.code == 0
    assert calls == []
    assert "--domain" in capsys.readouterr().out


def test_cli_mid_run_authorization_failure_also_exits_three(monkeypatch, capsys):
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    monkeypatch.setattr(sr, "_authorization_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        sr,
        "_main_unlocked",
        lambda *_args: (_ for _ in ()).throw(TuShareAuthorizationError("auth_denied")),
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "daily"])
    assert sr.main() == 3
    assert "auth_denied" in capsys.readouterr().out


def test_drain_loop_authorization_failure_is_not_swallowed(monkeypatch, capsys):
    """真实 drain 单域宽 catch 前必须让授权异常穿透，后续域不得继续。"""
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    reg = {
        "domains": {
            "daily": {"batch_mode": "by_trade_date"},
            "later": {"batch_mode": "by_trade_date"},
        }
    }
    calls = []

    def _drain(domain, **_kwargs):
        calls.append(domain)
        raise TuShareAuthorizationError("auth_denied")

    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(sr, "drain_domain", _drain)
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])

    assert sr.main() == 3
    assert calls == ["daily"]
    assert "auth_denied" in capsys.readouterr().out


def test_cli_calendar_failure_exits_four_before_domain_execution(monkeypatch, capsys):
    from services.data_sources.sync_preconditions import CalendarFoundationError

    monkeypatch.setattr(
        sr,
        "_calendar_preflight",
        lambda _domains: (_ for _ in ()).throw(
            CalendarFoundationError("calendar_not_ready")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "daily"])
    assert sr.main() == 4
    assert "calendar_blocked" in capsys.readouterr().out


def test_run_domain_records_database_write_failure(monkeypatch):
    reg = {
        "defaults": {"retry": {"max_attempts": 1, "backoff_seconds": [0]}},
        "domains": {
            "daily": {
                "source": "tushare",
                "api": "daily",
                "target_table": "raw_daily",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "data_start": "20260715",
            }
        },
    }
    monkeypatch.setattr(sr, "_adapter", lambda source: object())
    monkeypatch.setattr(sr, "_trading_days", lambda start, end=None: ["20260715"])
    monkeypatch.setattr(sr, "_fetch_paged", lambda *args: [
        {"ts_code": "000001.SZ", "trade_date": "20260715"}
    ])
    monkeypatch.setattr(sr, "_write_batch", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("disk full private detail")
    ))
    recorded = {}
    monkeypatch.setattr(sr, "_record_outcome", lambda spec, **kw: recorded.update(kw))

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(sr, "_target_conn", lambda spec: _Conn())
    result = sr.run_domain("daily", start="20260715", end="20260715", registry=reg)

    assert result["failed_batches"] == 1 and result["last_date"] is None
    assert recorded["ok"] is False and recorded["last_date"] is None
