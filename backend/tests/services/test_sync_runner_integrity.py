"""sync_runner 写入完整性与原子性契约。"""
from __future__ import annotations

import pytest
import sys

from services.data_sources import sync_runner as sr
from services.duck_adapter import connect

_ENABLED_EXECUTION = {"mode": "enabled", "reason": "active"}


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


def test_min_rows_is_evaluated_after_dedup_without_dropping_universe_rows():
    """A4: min_rows 看 landing 去重后行数；BJ 不再被写前丢弃垫/挡门。"""
    conn = connect(":memory:")
    spec = _margin_spec(
        min_rows_per_batch=3,
        batch_completeness={},
        target_table="raw_demo_landing",
        universe_filter=True,
        grain=["ts_code", "trade_date"],
    )
    rows = [
        {"ts_code": "600000.SH", "trade_date": "20260714", "rzye": 1.0},
        {"ts_code": "600000.SH", "trade_date": "20260714", "rzye": 2.0},
        {"ts_code": "830001.BJ", "trade_date": "20260714", "rzye": 3.0},
    ]

    # dedup → 2 landing rows (SH + BJ) < min_rows=3
    with pytest.raises(sr.BatchCompletenessError, match="post_filter_rows=2 < min_rows=3"):
        sr._write_batch(conn, spec, rows)

    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_demo_landing'"
    ).fetchone()[0]
    assert exists == 0

    # With min_rows=2, BJ is preserved in landing.
    spec["min_rows_per_batch"] = 2
    written = sr._write_batch(conn, spec, rows)
    assert written == 2
    codes = {
        row[0]
        for row in conn.execute("SELECT ts_code FROM raw_demo_landing").fetchall()
    }
    assert codes == {"600000.SH", "830001.BJ"}


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
        "defaults": {
            "fetch_timeout_seconds": 120,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]},
            "execution_policy": _ENABLED_EXECUTION,
        },
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
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20260714"])
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


@pytest.mark.parametrize(
    "write_config",
    [{}, {"write_mode": "replace_partition", "partition_by": ["day"]}],
)
def test_non_conversion_insert_failure_rolls_back_delete_and_unregisters_view(
    write_config,
):
    """grain/整分区 DELETE 后的 INSERT 故障都必须回滚并清理临时 view。"""
    inner = connect(":memory:")
    inner.execute(
        "CREATE TABLE raw_atomic (day VARCHAR, k VARCHAR, v INTEGER, built_at VARCHAR)"
    )
    inner.executemany(
        "INSERT INTO raw_atomic VALUES (?, ?, ?, 'old')",
        [("20260714", "same", 1), ("20260714", "sibling", 2), ("20260711", "keep", 3)],
    )

    class _FailInsert:
        _con = inner._con

        def execute(self, sql, params=None):
            if str(sql).lstrip().upper().startswith("INSERT INTO RAW_ATOMIC"):
                raise RuntimeError("simulated disk write failure")
            return inner.execute(sql, params)

    spec = {
        "domain": "atomic",
        "target_table": "raw_atomic",
        "grain": ["day", "k"],
        **write_config,
    }
    with pytest.raises(RuntimeError, match="simulated disk write failure"):
        sr._write_batch(
            _FailInsert(),
            spec,
            [{"day": "20260714", "k": "same", "v": 9}],
        )

    rows = inner.execute(
        "SELECT day, k, v, built_at FROM raw_atomic ORDER BY day, k"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("20260711", "keep", 3, "old"),
        ("20260714", "same", 1, "old"),
        ("20260714", "sibling", 2, "old"),
    ]
    with pytest.raises(Exception):
        inner.execute("SELECT * FROM df").fetchall()


def test_first_publish_insert_failure_leaves_no_empty_target_table():
    """首批 CREATE 与 INSERT 必须同事务；写失败不能留下会被误认成已发布的空表。"""
    inner = connect(":memory:")

    class _FailFirstInsert:
        _con = inner._con

        def execute(self, sql, params=None):
            if str(sql).lstrip().upper().startswith("INSERT INTO RAW_FIRST_PUBLISH"):
                raise RuntimeError("simulated first publish failure")
            return inner.execute(sql, params)

    with pytest.raises(RuntimeError, match="simulated first publish failure"):
        sr._write_batch(
            _FailFirstInsert(),
            {
                "domain": "first_publish",
                "target_table": "raw_first_publish",
                "grain": ["day", "key"],
            },
            [{"day": "20260716", "key": "x", "value": 1}],
        )

    exists = inner.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='raw_first_publish'"
    ).fetchone()[0]
    assert exists == 0


def test_conversion_retry_failure_rolls_back_schema_and_old_rows():
    """真实 Conversion 后的第二次 INSERT 再失败，类型加宽和数据替换必须一起回滚。"""
    inner = connect(":memory:")
    inner.execute("CREATE TABLE raw_widen_atomic (k VARCHAR, value INTEGER)")
    inner.execute("INSERT INTO raw_widen_atomic VALUES ('old', 7)")

    class _FailRetryInsert:
        _con = inner._con
        insert_calls = 0

        def execute(self, sql, params=None):
            if str(sql).lstrip().upper().startswith("INSERT INTO RAW_WIDEN_ATOMIC"):
                self.insert_calls += 1
                if self.insert_calls == 2:
                    raise RuntimeError("simulated retry insert failure")
            return inner.execute(sql, params)

    wrapped = _FailRetryInsert()
    with pytest.raises(RuntimeError, match="simulated retry insert failure"):
        sr._write_batch(
            wrapped,
            {
                "domain": "widen_atomic",
                "target_table": "raw_widen_atomic",
                "grain": ["k"],
            },
            [{"k": "new", "value": 16_472_341_619.53}],
        )

    assert [tuple(row) for row in inner.execute(
        "SELECT k, value FROM raw_widen_atomic ORDER BY k"
    ).fetchall()] == [("old", 7)]
    assert inner.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='raw_widen_atomic' AND column_name='value'"
    ).fetchone()[0] == "INTEGER"
    with pytest.raises(Exception):
        inner.execute("SELECT * FROM df").fetchall()


def test_provider_payload_missing_grain_is_rejected_before_any_table_mutation():
    conn = connect(":memory:")
    with pytest.raises(ValueError, match="api 返回缺 grain 列.*ts_code"):
        sr._write_batch(
            conn,
            _margin_spec(target_table="raw_missing_grain"),
            [{"trade_date": "20260716", "rzye": 1}],
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='raw_missing_grain'"
    ).fetchone()[0] == 0


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


def test_replace_partition_removes_stale_grains_without_touching_other_days():
    """完整日重拉应替换整个日期分区，不能留下新批已撤销的旧 grain。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_partition "
        "(trade_date VARCHAR, exchange_id VARCHAR, value INTEGER, built_at VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO raw_partition VALUES (?, ?, ?, 'old')",
        [
            ("20260714", "SSE", 1),
            ("20260714", "SZSE", 1),
            ("20260714", "BSE", 1),
            ("20260714", "STALE", 1),
            ("20260711", "KEEP", 7),
        ],
    )
    spec = {
        "domain": "partition_probe",
        "target_table": "raw_partition",
        "grain": ["trade_date", "exchange_id"],
        "write_mode": "replace_partition",
        "partition_by": ["trade_date"],
        "min_rows_per_batch": 3,
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE", "BSE"],
        },
    }

    sr._write_batch(
        conn,
        spec,
        [
            {"trade_date": "20260714", "exchange_id": "SSE", "value": 2},
            {"trade_date": "20260714", "exchange_id": "SZSE", "value": 2},
            {"trade_date": "20260714", "exchange_id": "BSE", "value": 2},
        ],
    )

    rows = conn.execute(
        "SELECT trade_date, exchange_id, value FROM raw_partition "
        "ORDER BY trade_date, exchange_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("20260711", "KEEP", 7),
        ("20260714", "BSE", 2),
        ("20260714", "SSE", 2),
        ("20260714", "SZSE", 2),
    ]


def test_replace_partition_rejects_multi_partition_batch_without_touching_old_rows():
    """不同日期不能合并凑齐门槛后把两个完整旧分区同时写成半批。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_partition "
        "(trade_date VARCHAR, exchange_id VARCHAR, value INTEGER, built_at VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO raw_partition VALUES (?, ?, 1, 'old')",
        [
            ("20260711", "SSE"),
            ("20260711", "SZSE"),
            ("20260714", "SSE"),
            ("20260714", "SZSE"),
        ],
    )
    spec = {
        "domain": "partition_probe",
        "target_table": "raw_partition",
        "grain": ["trade_date", "exchange_id"],
        "write_mode": "replace_partition",
        "partition_by": ["trade_date"],
        "min_rows_per_batch": 2,
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
        },
    }

    with pytest.raises(sr.BatchCompletenessError, match="exactly one partition"):
        sr._write_batch(
            conn,
            spec,
            [
                {"trade_date": "20260711", "exchange_id": "SSE", "value": 9},
                {"trade_date": "20260714", "exchange_id": "SZSE", "value": 9},
            ],
        )

    rows = conn.execute(
        "SELECT trade_date, exchange_id, value, built_at FROM raw_partition "
        "ORDER BY trade_date, exchange_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("20260711", "SSE", 1, "old"),
        ("20260711", "SZSE", 1, "old"),
        ("20260714", "SSE", 1, "old"),
        ("20260714", "SZSE", 1, "old"),
    ]


@pytest.mark.parametrize("payload_date", [None, "20230210"])
def test_write_batch_rejects_response_partition_different_from_request(payload_date):
    """请求日是写授权边界；NULL/错日响应都不能删错分区或绕过条件组门。"""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_partition "
        "(trade_date VARCHAR, exchange_id VARCHAR, value INTEGER, built_at VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO raw_partition VALUES ('20260714', ?, 1, 'old')",
        [("SSE",), ("SZSE",), ("BSE",)],
    )
    spec = {
        "domain": "partition_probe",
        "target_table": "raw_partition",
        "grain": ["trade_date", "exchange_id"],
        "write_mode": "replace_partition",
        "partition_by": ["trade_date"],
        "date_param": "trade_date",
        "min_rows_per_batch": 2,
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {"BSE": "20230213"},
        },
    }

    with pytest.raises(sr.BatchCompletenessError, match="requested partition"):
        sr._write_batch(
            conn,
            spec,
            [
                {"trade_date": payload_date, "exchange_id": "SSE", "value": 9},
                {"trade_date": payload_date, "exchange_id": "SZSE", "value": 9},
            ],
            expected_partition={"trade_date": "20260714"},
        )

    rows = conn.execute(
        "SELECT exchange_id, value, built_at FROM raw_partition ORDER BY exchange_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("BSE", 1, "old"),
        ("SSE", 1, "old"),
        ("SZSE", 1, "old"),
    ]


def test_unknown_write_mode_is_rejected_instead_of_silent_merge():
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_partition "
        "(trade_date VARCHAR, exchange_id VARCHAR, value INTEGER, built_at VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO raw_partition VALUES ('20260714', ?, 1, 'old')",
        [("SSE",), ("STALE",)],
    )
    spec = {
        "domain": "partition_probe",
        "target_table": "raw_partition",
        "grain": ["trade_date", "exchange_id"],
        "write_mode": "replace_partiton",
        "partition_by": ["trade_date"],
    }

    with pytest.raises(ValueError, match="unsupported write_mode"):
        sr._write_batch(
            conn,
            spec,
            [{"trade_date": "20260714", "exchange_id": "SSE", "value": 9}],
        )

    assert [tuple(row) for row in conn.execute(
        "SELECT exchange_id, value, built_at FROM raw_partition ORDER BY exchange_id"
    ).fetchall()] == [("SSE", 1, "old"), ("STALE", 1, "old")]


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
        "defaults": {
            "fetch_timeout_seconds": 120,
            "execution_policy": _ENABLED_EXECUTION,
        },
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


def test_all_due_drain_selected_unsupported_domain_exits_nonzero(monkeypatch):
    reg = {
        "defaults": {
            "fetch_timeout_seconds": 120,
            "execution_policy": _ENABLED_EXECUTION,
        },
        "domains": {
            "unsupported_daily": {
                "batch_mode": "by_ts_code",
                "sync_policy": "manual_only",
            }
        }
    }
    monkeypatch.setattr(sr, "load_registry", lambda: reg)
    monkeypatch.setattr(
        sr,
        "drain_domain",
        lambda domain, **kwargs: {
            "domain": domain,
            "status": "unsupported",
            "batch_mode": "by_ts_code",
        },
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--all-due", "--drain"])

    assert sr.main() == 1


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
        "defaults": {
            "fetch_timeout_seconds": 120,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]},
            "execution_policy": _ENABLED_EXECUTION,
        },
        "domains": {
            # Non-formal probe domain: trade_cal itself is now formally write-walled.
            "probe_full_refresh": {
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

    result = sr.run_domain("probe_full_refresh", registry=reg)

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

    # Use an enabled legacy domain; formal daily/stock_st/trade_cal are disabled.
    monkeypatch.setattr(
        sr,
        "load_registry",
        lambda: {
            "defaults": {
                "execution_policy": _ENABLED_EXECUTION,
                "fetch_timeout_seconds": 120,
            },
            "domains": {
                "adj_factor": {
                    "source": "tushare",
                    "api": "adj_factor",
                    "target_table": "raw_tushare_adj_factor",
                    "grain": ["ts_code", "trade_date"],
                    "batch_mode": "by_trade_date",
                }
            },
        },
    )
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
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "adj_factor"])

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

    monkeypatch.setattr(
        sr,
        "load_registry",
        lambda: {
            "defaults": {
                "execution_policy": _ENABLED_EXECUTION,
                "fetch_timeout_seconds": 120,
            },
            "domains": {
                "adj_factor": {
                    "source": "tushare",
                    "api": "adj_factor",
                    "target_table": "raw_tushare_adj_factor",
                    "grain": ["ts_code", "trade_date"],
                    "batch_mode": "by_trade_date",
                }
            },
        },
    )
    monkeypatch.setattr(sr, "_authorization_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        sr,
        "_main_unlocked",
        lambda *_args: (_ for _ in ()).throw(TuShareAuthorizationError("auth_denied")),
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "adj_factor"])
    assert sr.main() == 3
    assert "auth_denied" in capsys.readouterr().out


def test_drain_loop_authorization_failure_is_not_swallowed(monkeypatch, capsys):
    """真实 drain 单域宽 catch 前必须让授权异常穿透，后续域不得继续。"""
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    reg = {
        "defaults": {
            "fetch_timeout_seconds": 120,
            "execution_policy": _ENABLED_EXECUTION,
        },
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
        "load_registry",
        lambda: {
            "defaults": {
                "execution_policy": _ENABLED_EXECUTION,
                "fetch_timeout_seconds": 120,
            },
            "domains": {
                "adj_factor": {
                    "source": "tushare",
                    "api": "adj_factor",
                    "target_table": "raw_tushare_adj_factor",
                    "grain": ["ts_code", "trade_date"],
                    "batch_mode": "by_trade_date",
                }
            },
        },
    )
    monkeypatch.setattr(
        sr,
        "_calendar_preflight",
        lambda _domains: (_ for _ in ()).throw(
            CalendarFoundationError("calendar_not_ready")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["sync_runner.py", "--domain", "adj_factor"])
    assert sr.main() == 4
    assert "calendar_blocked" in capsys.readouterr().out


def test_run_domain_records_database_write_failure(monkeypatch):
    reg = {
        "defaults": {
            "fetch_timeout_seconds": 120,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]},
            "execution_policy": _ENABLED_EXECUTION,
        },
        "domains": {
            # Avoid formal daily boundary; this proves legacy write-failure recording.
            "probe_daily": {
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
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20260715"])
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
    result = sr.run_domain("probe_daily", start="20260715", end="20260715", registry=reg)

    assert result["failed_batches"] == 1 and result["last_date"] is None
    assert recorded["ok"] is False and recorded["last_date"] is None


def test_formal_daily_sync_requires_explicit_bounds_before_provider_io(monkeypatch):
    """Authorized daily short window refuses unbounded sync before provider I/O."""

    for name in (
        "_adapter",
        "_target_conn",
        "_write_batch",
        "_fetch_paged",
        "_publish_security_day_accepted_partition",
    ):
        monkeypatch.setattr(
            sr,
            name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)),
        )
    with pytest.raises(sr.SyncWindowError, match="explicit --start/--end"):
        sr.run_domain("daily")


def test_formal_daily_authorized_single_day_uses_accepted_path(monkeypatch):
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "daily")
    assert spec["execution_policy"] == {
        "mode": "enabled",
        "reason": "authorized_manual_generation",
    }
    assert spec["sync_policy"] == "on_demand"

    called = {}

    def _fake_publish(domain, _spec, *, trade_date):
        called["domain"] = domain
        called["trade_date"] = trade_date
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "publication": "accepted_nominal_ohlcv_partition",
            "partition_value": trade_date,
        }

    monkeypatch.setattr(sr, "_publish_security_day_accepted_partition", _fake_publish)
    for name in ("_adapter", "_target_conn", "_write_batch", "_fetch_paged"):
        monkeypatch.setattr(
            sr,
            name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)),
        )

    result = sr.run_domain(
        "daily", start="20260717", end="20260717", registry=registry
    )
    assert called == {"domain": "daily", "trade_date": "20260717"}
    assert result["publication"] == "accepted_nominal_ohlcv_partition"
    assert result["failed_batches"] == 0


def test_formal_daily_authorized_short_window_publishes_each_trading_day(monkeypatch):
    """Short inclusive window expands via calendar and loops single-day accept."""

    registry = sr.load_registry()
    published: list[str] = []

    def _fake_publish(domain, _spec, *, trade_date):
        published.append(trade_date)
        return {
            "domain": domain,
            "status": "ok",
            "batches": 1,
            "rows": 10,
            "failed_batches": 0,
            "publication": "accepted_nominal_ohlcv_partition",
            "partition_value": trade_date,
        }

    monkeypatch.setattr(sr, "_publish_security_day_accepted_partition", _fake_publish)
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end=None: [
            d
            for d in (
                "20260713",
                "20260714",
                "20260715",
                "20260716",
                "20260717",
            )
            if start <= d <= (end or d)
        ],
    )
    for name in ("_adapter", "_target_conn", "_write_batch", "_fetch_paged"):
        monkeypatch.setattr(
            sr,
            name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)),
        )

    result = sr.run_domain(
        "daily", start="20260713", end="20260717", registry=registry
    )
    assert published == [
        "20260713",
        "20260714",
        "20260715",
        "20260716",
        "20260717",
    ]
    assert result["publication"] == "accepted_security_day_short_window"
    assert result["window_days_completed"] == 5
    assert result["rows"] == 50
    assert result["failed_batches"] == 0


def test_formal_daily_short_window_refuses_mass_backfill(monkeypatch):
    days = [f"20260{i:03d}" for i in range(1, 16)]  # 15 synthetic days
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: days)
    with pytest.raises(sr.SyncWindowError, match="refuse mass backfill"):
        sr._require_authorized_short_trade_date_window(
            "daily",
            backfill=False,
            resume=False,
            start="20260001",
            end="20260015",
            max_dates=None,
        )


def test_formal_stock_st_drain_is_inapplicable():
    result = sr.drain_domain("stock_st")
    assert result["status"] == "drain_inapplicable"
    assert "short_window" in result["reason"]
