from __future__ import annotations

import logging

import pandas as pd

from conftest import duck_mem
from scripts import probe_source_capability as probe


class _ConnProxy:
    def __init__(self, conn):
        self._conn = conn

    def close(self) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_probe_source_capability_summarizes_records(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        assert prefer_source == "akshare"
        assert kwargs == {"stock": "600519", "market": "sh"}
        return (
            [
                {"日期": "2026-05-28", "主力净流入-净额": 1.0},
                {"日期": "2026-05-29", "主力净流入-净额": 2.0},
            ],
            "akshare",
        )

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow",
        {"stock": "600519", "market": "sh"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["source_used"] == "akshare"
    assert report["row_count"] == 2
    assert report["columns"] == ["日期", "主力净流入-净额"]
    assert report["date_range"] == {"field": "日期", "min": "2026-05-28", "max": "2026-05-29"}


def test_probe_source_capability_summarizes_dataframe(monkeypatch) -> None:
    df = pd.DataFrame(
        [
            {"trade_date": "2026-05-29", "main_net_amount": 12.0},
        ]
    )

    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        return (df, "akshare")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow_rank",
        {"indicator": "5日"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["type"] == "DataFrame"
    assert report["row_count"] == 1
    assert report["columns"] == ["trade_date", "main_net_amount"]
    assert report["date_range"] == {"field": "trade_date", "min": "2026-05-29", "max": "2026-05-29"}


def test_probe_source_capability_summarizes_rank_snapshot(monkeypatch) -> None:
    df = pd.DataFrame(
        [
            {"序号": 1, "代码": "600519", "最新价": 1234.5},
        ]
    )

    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow_rank_snapshot"
        assert prefer_source == "akshare"
        assert kwargs == {"symbol": "即时"}
        return (df, "akshare")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow_rank_snapshot",
        {"symbol": "即时"},
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["type"] == "DataFrame"
    assert report["row_count"] == 1
    assert report["columns"] == ["序号", "代码", "最新价"]


def test_probe_source_capability_marks_blocked_on_error(monkeypatch) -> None:
    def fake_resolve(*_args, **_kwargs):
        raise RuntimeError("proxy blocked")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_source_capability(
        "individual_fund_flow",
        {"stock": "600519", "market": "sh"},
        prefer_source="akshare",
    )

    assert report["status"] == "blocked"
    assert report["error_type"] == "RuntimeError"
    assert report["error"] == "proxy blocked"


def test_probe_source_capability_quiets_registry_warnings(monkeypatch, caplog) -> None:
    registry_logger = logging.getLogger("data_sources.registry")
    original_level = registry_logger.level

    def fake_resolve(*_args, **_kwargs):
        logging.getLogger("data_sources.registry").warning("[registry] noisy fallback")
        raise RuntimeError("proxy blocked")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    with caplog.at_level(logging.WARNING, logger="data_sources.registry"):
        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
        )

    assert report["status"] == "blocked"
    assert report["error"] == "proxy blocked"
    assert not any(record.name == "data_sources.registry" for record in caplog.records)
    assert registry_logger.level == original_level


def test_probe_source_capability_persists_blocked_status(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_resolve(*_args, **_kwargs):
            raise RuntimeError("proxy blocked")

        monkeypatch.setattr(probe, "resolve", fake_resolve)
        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))

        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "blocked"
        assert report["persisted"]["status"] == "open"
        row = conn.execute(
            """
            SELECT data_domain, source_name, source_tier, stock_code,
                   error_type, last_error, status
              FROM mart_data_source_failure_queue
            """
        ).fetchone()
        assert tuple(row) == (
            "order_flow_fund_flow",
            "akshare",
            3,
            "600519",
            "RuntimeError",
            "proxy blocked",
            "open",
        )
    finally:
        conn.close()


def test_probe_source_capability_downgrades_persist_failure_on_blocked(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_resolve(*_args, **_kwargs):
            raise RuntimeError("proxy blocked")

        def fake_record_source_failure(*_args, **_kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(probe, "resolve", fake_resolve)
        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "record_source_failure", fake_record_source_failure)

        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "blocked"
        assert report["persisted"]["status"] == "error"
        assert report["persisted"]["error_type"] == "RuntimeError"
        assert report["persisted"]["error"] == "db locked"
    finally:
        conn.close()


def test_probe_source_capability_resolves_existing_failure_on_success(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_blocked(*_args, **_kwargs):
            raise RuntimeError("proxy blocked")

        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "resolve", fake_blocked)
        probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        def fake_ok(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow"
            assert prefer_source == "akshare"
            return ([{"日期": "2026-05-29"}], "akshare")

        monkeypatch.setattr(probe, "resolve", fake_ok)
        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "ok"
        assert report["persisted"]["status"] == "resolved"
        row = conn.execute(
            """
            SELECT status, resolved_at
              FROM mart_data_source_failure_queue
             WHERE data_domain = 'order_flow_fund_flow'
               AND source_name = 'akshare'
               AND stock_code = '600519'
            """
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None
    finally:
        conn.close()


def test_probe_source_capability_downgrades_persist_failure_on_success(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow"
            assert prefer_source == "akshare"
            return ([{"日期": "2026-05-29"}], "akshare")

        def fake_resolve_source_failures(*_args, **_kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "resolve", fake_resolve)
        monkeypatch.setattr(probe, "resolve_source_failures", fake_resolve_source_failures)

        report = probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            data_domain="order_flow_fund_flow",
            source_name="akshare",
            source_tier=3,
            stock_code="600519",
        )

        assert report["status"] == "ok"
        assert report["persisted"]["status"] == "error"
        assert report["persisted"]["error_type"] == "RuntimeError"
        assert report["persisted"]["error"] == "db locked"
    finally:
        conn.close()


def _need027_exact_flow_row(date: str = "2026-06-04") -> dict[str, object]:
    return {
        "日期": date,
        "主力净流入-净额": 1.0,
        "超大单净流入-净额": 2.0,
        "大单净流入-净额": 3.0,
        "中单净流入-净额": 4.0,
        "小单净流入-净额": 5.0,
    }


def test_probe_source_capability_batch_does_not_persist_by_default(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        assert prefer_source == "akshare"
        return ([_need027_exact_flow_row()], "akshare")

    def fail_get_conn():
        raise AssertionError("default batch probe must not open writable DB connection")

    monkeypatch.setattr(probe, "resolve", fake_resolve)
    monkeypatch.setattr(probe, "get_conn", fail_get_conn)

    report = probe.probe_source_capability_batch(
        [
            {
                "case_id": "case_600519",
                "capability": "individual_fund_flow",
                "kwargs": {"stock": "600519", "market": "sh"},
            }
        ],
        prefer_source="akshare",
    )

    assert report["status"] == "ok"
    assert report["ok_count"] == 1
    assert report["results"][0]["case_id"] == "case_600519"
    assert "persisted" not in report["results"][0]


def test_probe_case_level_persist_status_is_rejected() -> None:
    try:
        probe._parse_cases_json(
            '[{"capability": "individual_fund_flow", "persist_status": true}]'
        )
    except ValueError as exc:
        assert "case-level persist_status is not supported" in str(exc)
    else:
        raise AssertionError("case-level persist_status should not bypass the CLI latch")


def test_need027_probe_cases_load_from_config(tmp_path) -> None:
    config = tmp_path / "tdx_data_need_coverage.yaml"
    config.write_text(
        """
needs:
  - need_id: "need_027"
    source_probe_cases:
      - case_id: "case_600519"
        capability: "individual_fund_flow"
        kwargs:
          stock: "600519"
          market: "sh"
        stock_code: "600519"
""",
        encoding="utf-8",
    )

    cases = probe._load_need027_probe_cases(config)

    assert cases == [
        {
            "case_id": "case_600519",
            "capability": "individual_fund_flow",
            "kwargs": {"stock": "600519", "market": "sh"},
            "stock_code": "600519",
        }
    ]


def test_need027_exact_flow_gate_passes_for_exact_batch(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        assert prefer_source == "akshare"
        return (
            [
                _need027_exact_flow_row("2026-06-03"),
                _need027_exact_flow_row("2026-06-04"),
            ],
            "akshare",
        )

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "case_600519",
                "capability": "individual_fund_flow",
                "kwargs": {"stock": "600519", "market": "sh"},
                "stock_code": "600519",
            },
            {
                "case_id": "case_000001",
                "capability": "individual_fund_flow",
                "kwargs": {"stock": "000001", "market": "sz"},
                "stock_code": "000001",
            },
        ]
    )

    assert report["verdict"] == "PASS"
    assert report["status"] == "source_probe_passed"
    assert report["production_eligibility"] == "blocked"
    assert report["production_promotion"] == "not_allowed_from_probe_only"
    assert report["next_gate"] == "writer_watermark_pit_freshness_gate_required"
    assert report["post_probe_gates"]["field_mapping"]["status"] == "pass"
    assert report["post_probe_gates"]["date_coverage"]["status"] == "pass"
    assert report["post_probe_gates"]["pit_key"]["status"] == "required"
    assert report["next_actions"] == [
        "run_writer_watermark_pit_freshness_failure_queue_gates"
    ]
    assert report["exact_flow"]["probe_count"] == 2
    assert report["exact_flow"]["valid_count"] == 2
    assert report["exact_flow"]["failure_reasons"] == {}
    assert report["exact_flow"]["source_groups"]["akshare"]["production_blockers"] == {
        "post_probe_gate_required": 2
    }
    for item in report["batch"]["results"]:
        assert item["need027_exact_flow_validation"]["status"] == "ok"
        assert item["need027_exact_flow_validation"]["controller_blocker"] == "post_probe_gate_required"
        assert item["need027_exact_flow_validation"]["column_coverage"]["missing_groups"] == []


def test_need027_exact_flow_gate_accepts_tushare_moneyflow(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "moneyflow"
        assert prefer_source == "tushare"
        assert kwargs == {"ts_code": "600519.SH"}
        return (
            [
                {
                    "trade_date": "20260605",
                    "main_net_amount": 1.0,
                    "super_large_net_amount": 2.0,
                    "large_net_amount": 3.0,
                    "medium_net_amount": 4.0,
                    "small_net_amount": 5.0,
                },
            ],
            "tushare",
        )

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "tushare_600519",
                "capability": "moneyflow",
                "prefer_source": "tushare",
                "data_domain": "order_flow_fund_flow",
                "source_name": "tushare",
                "source_tier": 2,
                "kwargs": {"ts_code": "600519.SH"},
                "stock_code": "600519",
            },
        ]
    )

    assert report["verdict"] == "PASS"
    assert "moneyflow" in report["exact_capabilities"]
    result = report["batch"]["results"][0]
    assert result["need027_exact_flow_validation"]["status"] == "ok"
    assert result["source_name"] == "tushare"
    assert result["source_tier"] == 2


def test_need027_exact_flow_gate_passes_when_one_source_group_is_complete(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        if capability == "individual_fund_flow" and prefer_source == "akshare":
            return (
                [
                    {
                        "日期": "2026-06-05",
                        "主力净流入-净额": 1.0,
                        "超大单净流入-净额": 2.0,
                        "大单净流入-净额": 3.0,
                        "中单净流入-净额": 4.0,
                        "小单净流入-净额": 5.0,
                    }
                ],
                "akshare",
            )
        if capability == "moneyflow" and prefer_source == "tushare":
            raise RuntimeError("TuShare token missing")
        raise AssertionError(f"unexpected route {capability}/{prefer_source}/{kwargs}")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "akshare_600519",
                "capability": "individual_fund_flow",
                "prefer_source": "akshare",
                "source_name": "akshare",
                "source_tier": 3,
                "kwargs": {"stock": "600519", "market": "sh"},
            },
            {
                "case_id": "tushare_600519",
                "capability": "moneyflow",
                "prefer_source": "tushare",
                "source_name": "tushare",
                "source_tier": 2,
                "kwargs": {"ts_code": "600519.SH"},
            },
        ]
    )

    assert report["verdict"] == "PASS"
    assert report["status"] == "source_probe_passed"
    assert report["exact_flow"]["probe_count"] == 2
    assert report["exact_flow"]["valid_count"] == 1
    assert report["exact_flow"]["blocked_count"] == 1
    assert report["exact_flow"]["valid_source_groups"] == ["akshare"]
    assert report["exact_flow"]["blocked_source_groups"] == ["tushare"]
    assert report["exact_flow"]["source_groups"]["akshare"]["status"] == "ok"
    assert report["exact_flow"]["source_groups"]["tushare"]["status"] == "blocked"
    assert report["exact_flow"]["source_groups"]["tushare"]["controller_blockers"] == {
        "tushare_token_missing": 1
    }
    result = report["batch"]["results"][1]
    assert result["need027_exact_flow_validation"]["controller_blocker"] == "tushare_token_missing"
    assert result["need027_exact_flow_validation"]["next_action"] == "provide_token_and_rerun_no_persist_probe"


def test_need027_gate_classifies_akshare_remote_disconnected(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        assert prefer_source == "akshare"
        raise RuntimeError(
            "所有 1 个源都失败了 (capability=individual_fund_flow): "
            "ConnectionError: ('Connection aborted.', "
            "RemoteDisconnected('Remote end closed connection without response'))"
        )

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "akshare_600519",
                "capability": "individual_fund_flow",
                "prefer_source": "akshare",
                "source_name": "akshare",
                "source_tier": 3,
                "kwargs": {"stock": "600519", "market": "sh"},
            },
        ]
    )

    assert report["verdict"] == "BLOCKED"
    assert report["post_probe_gates"]["pit_key"]["status"] == "not_checked"
    assert report["exact_flow"]["source_groups"]["akshare"]["controller_blockers"] == {
        "akshare_remote_disconnected": 1
    }
    assert report["exact_flow"]["source_groups"]["akshare"]["next_actions"] == [
        "retry_source_probe_or_choose_stable_candidate_source"
    ]
    validation = report["batch"]["results"][0]["need027_exact_flow_validation"]
    assert validation["controller_blocker"] == "akshare_remote_disconnected"
    assert validation["next_action"] == "retry_source_probe_or_choose_stable_candidate_source"


def test_need027_gate_blocks_rank_snapshot_even_when_snapshot_ok(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        if capability == "individual_fund_flow_rank_snapshot":
            return ([{"序号": 1, "代码": "600519", "净流入": 1.0}], "akshare")
        raise AssertionError(f"unexpected capability {capability}")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "snapshot_only",
                "capability": "individual_fund_flow_rank_snapshot",
                "kwargs": {"symbol": "即时"},
            }
        ]
    )

    assert report["verdict"] == "BLOCKED"
    assert report["status"] == "blocked"
    assert report["exact_flow"]["probe_count"] == 0
    assert report["non_exact_probe_count"] == 1
    assert report["rank_snapshot_policy"] == "research_side_only_not_exact_flow_evidence"
    validation = report["batch"]["results"][0]["need027_exact_flow_validation"]
    assert validation["status"] == "ignored"
    assert validation["reason"] == "not exact-flow capability"
    assert validation["controller_blocker"] == "not_exact_flow_capability"
    assert validation["next_action"] == "ignore_for_need027_exact_flow_gate"


def test_need027_persist_status_does_not_resolve_malformed_exact_flow(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_blocked(*_args, **_kwargs):
            raise RuntimeError("exact flow blocked")

        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "resolve", fake_blocked)
        probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            stock_code="600519",
        )

        def fake_malformed_exact(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow"
            return ([{"主力净流入-净额": 1.0}], "akshare")

        monkeypatch.setattr(probe, "resolve", fake_malformed_exact)
        report = probe.probe_need027_exact_flow_gate(
            [
                {
                    "case_id": "malformed_600519",
                    "capability": "individual_fund_flow",
                    "kwargs": {"stock": "600519", "market": "sh"},
                    "stock_code": "600519",
                }
            ],
            persist_status=True,
        )

        assert report["verdict"] == "BLOCKED"
        result = report["batch"]["results"][0]
        assert result["status"] == "ok"
        assert result["need027_exact_flow_validation"]["status"] == "blocked"
        assert result["persisted"]["status"] == "open"
        rows = conn.execute(
            """
            SELECT error_type, status, resolved_at
              FROM mart_data_source_failure_queue
             WHERE data_domain = 'order_flow_fund_flow'
               AND source_name = 'akshare'
               AND stock_code = '600519'
             ORDER BY error_type
            """
        ).fetchall()
        assert rows
        assert {row["status"] for row in rows} == {"open"}
        assert all(row["resolved_at"] is None for row in rows)
        assert "need027_exact_flow_validation_failed" in {row["error_type"] for row in rows}
    finally:
        conn.close()


def test_rank_snapshot_persistence_does_not_resolve_exact_flow_failure(monkeypatch) -> None:
    conn = duck_mem()
    try:
        def fake_blocked(*_args, **_kwargs):
            raise RuntimeError("exact flow blocked")

        monkeypatch.setattr(probe, "get_conn", lambda: _ConnProxy(conn))
        monkeypatch.setattr(probe, "resolve", fake_blocked)
        probe.probe_source_capability(
            "individual_fund_flow",
            {"stock": "600519", "market": "sh"},
            prefer_source="akshare",
            persist_status=True,
            stock_code="600519",
        )

        def fake_rank_snapshot(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow_rank_snapshot"
            return ([{"序号": 1, "代码": "600519", "净流入": 1.0}], "akshare")

        monkeypatch.setattr(probe, "resolve", fake_rank_snapshot)
        report = probe.probe_source_capability(
            "individual_fund_flow_rank_snapshot",
            {"symbol": "即时"},
            prefer_source="akshare",
            persist_status=True,
        )

        assert report["status"] == "ok"
        assert report["persisted"]["status"] == "resolved"
        assert report["persisted"]["data_domain"] == "stock_fund_flow_rank_snapshot"
        exact_row = conn.execute(
            """
            SELECT data_domain, status, resolved_at
              FROM mart_data_source_failure_queue
             WHERE data_domain = 'order_flow_fund_flow'
               AND source_name = 'akshare'
               AND stock_code = '600519'
            """
        ).fetchone()
        assert exact_row["status"] == "open"
        assert exact_row["resolved_at"] is None
    finally:
        conn.close()


def test_need027_gate_blocks_missing_date_range_and_exact_columns(monkeypatch) -> None:
    def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
        assert capability == "individual_fund_flow"
        return ([{"主力净流入-净额": 1.0}], "akshare")

    monkeypatch.setattr(probe, "resolve", fake_resolve)

    report = probe.probe_need027_exact_flow_gate(
        [
            {
                "case_id": "missing_fields",
                "capability": "individual_fund_flow",
                "kwargs": {"stock": "600519", "market": "sh"},
                "stock_code": "600519",
            }
        ]
    )

    assert report["verdict"] == "BLOCKED"
    assert report["exact_flow"]["blocked_count"] == 1
    assert report["exact_flow"]["failure_reasons"] == {
        "missing_date_range": 1,
        "missing_exact_flow_columns": 1,
    }
    validation = report["batch"]["results"][0]["need027_exact_flow_validation"]
    assert validation["status"] == "blocked"
    assert validation["controller_blockers"] == [
        "missing_date_range",
        "missing_exact_flow_columns",
    ]
    assert validation["next_action"] == "fix_field_or_date_mapping_before_source_promotion"
    assert validation["column_coverage"]["missing_groups"] == [
        "super_large",
        "large",
        "medium",
        "small",
    ]


def test_parse_cases_json_rejects_non_list() -> None:
    try:
        probe._parse_cases_json('{"capability": "individual_fund_flow"}')
    except ValueError as exc:
        assert "--cases-json must decode to a list" in str(exc)
    else:
        raise AssertionError("non-list cases JSON should be rejected")
