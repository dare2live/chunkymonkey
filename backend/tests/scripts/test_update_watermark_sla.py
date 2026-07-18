"""update_watermark_sla registry 驱动条目单测 — sync:* 域防线契约 (复审 HIGH 闭环)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import duck_mem
from services.data_sources.batch_integrity import VerifiedBatchFrontier
from services.source_watermarks import ensure_source_watermark_schema, upsert_watermark

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_watermark_sla.py"
SPEC = importlib.util.spec_from_file_location("update_watermark_sla", SCRIPT_PATH)
sla = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = sla
SPEC.loader.exec_module(sla)


def test_sync_registry_queries_cover_all_domains():
    """registry 注册即入防线: 每个域必须有条目 (可 probe 或显式 no_probe), 零静默缺席."""
    import yaml

    reg = yaml.safe_load(
        (SCRIPT_PATH.resolve().parents[1] / "config" / "sync_registry.yaml").read_text())
    queries = sla._sync_registry_queries()
    for name in reg["domains"]:
        assert f"sync:{name}" in queries, f"sync:{name} 不在 SLA 防线 — 注册域静默缺席"


@pytest.mark.parametrize(
    "payload",
    [
        "domains: [not-a-mapping]\n",
        "version: 1\n",
        "domains:\n  margin: broken\n",
    ],
)
def test_sync_registry_queries_rejects_unverifiable_registry(tmp_path: Path, payload: str):
    registry = tmp_path / "sync_registry.yaml"
    registry.write_text(payload, encoding="utf-8")

    with pytest.raises(Exception, match="sync_registry.*unverified"):
        sla._sync_registry_queries(registry_path=registry)


def test_main_registry_failure_removes_stale_artifact_and_exits_nonzero(
    tmp_path: Path, monkeypatch
):
    output = tmp_path / "watermark_sla.json"
    output.write_text('{"stale": true}', encoding="utf-8")

    def _registry_failure():
        raise RuntimeError("sync_registry unverified: injected")

    monkeypatch.setattr(sla, "_sync_registry_queries", _registry_failure)
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_watermark_sla.py", "--json-output", str(output)],
    )

    assert sla.main() != 0
    assert not output.exists()


def test_daily_domains_probe_trade_date_quarterly_no_probe():
    queries = sla._sync_registry_queries()
    q = queries["sync:moneyflow"]
    assert "trade_date" in q["query"] and q["db"] == "tushare_raw"
    assert q["sla_days"] is not None  # registry per-domain SLA 优先于 tier 默认
    assert queries["sync:daily"].get("verified_complete_spec")
    assert queries["sync:fina_mainbz"].get("no_probe")  # by_ts_code 季度域显式 no_probe


def test_margin_sla_uses_accepted_state_not_legacy_raw_max():
    queries = sla._sync_registry_queries()
    assert queries["sync:margin"].get("accepted_margin") is True
    assert "query" not in queries["sync:margin"]

    raw = duck_mem()
    raw.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
    raw.execute("INSERT INTO raw_tushare_margin VALUES ('20991231')")

    probe = sla._query_actual_frontier(
        {"tushare_raw": raw}, queries, "sync:margin"
    )

    assert probe.state == "no_complete_batch"
    assert probe.actual_date is None


def test_margin_sla_uses_registry_contract_snapshot(monkeypatch):
    from services.data_sources import margin_state

    queries = sla._sync_registry_queries()
    planned = queries["sync:margin"]["_margin_contract"]
    frontier = VerifiedBatchFrontier(
        last_date="20260716", row_count=3, last_success_at="2026-07-16"
    )
    seen = []
    monkeypatch.setattr(
        margin_state,
        "load_margin_accepted_state",
        lambda _conn, *, contract=None: seen.append(contract)
        or SimpleNamespace(frontier=frontier),
    )

    probe = sla._query_actual_frontier(
        {"tushare_raw": object()}, queries, "sync:margin"
    )

    assert probe.state == "verified"
    assert probe.actual_date == "20260716"
    assert len(seen) == 1
    assert seen[0] is planned


def test_registered_margin_without_watermark_probes_and_alerts_on_no_acceptance():
    queries = sla._sync_registry_queries()
    raw = duck_mem()
    raw.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
    raw.execute("INSERT INTO raw_tushare_margin VALUES ('20991231')")

    result = sla._registered_domain_without_watermark_result(
        {"tushare_raw": raw},
        queries,
        "sync:margin",
        queries["sync:margin"],
        sla.date(2026, 7, 17),
    )

    assert result["status"] == "NO_COMPLETE_BATCH"
    assert result["probe_state"] == "no_complete_batch"
    assert result["actual_date"] is None
    assert result["alert"] is True


def test_accepted_margin_sla_audits_projection_without_mutating_it():
    frontier = VerifiedBatchFrontier(
        last_date="20260715",
        row_count=3,
        last_success_at="2026-07-16T01:05:00+00:00",
    )
    assert sla._accepted_projection_drift(
        watermark_date="2026-07-15",
        watermark_row_count=3,
        watermark_parser_version="margin_accepted_contract_1",
        frontier=frontier,
        expected_parser_version="margin_accepted_contract_1",
    ) == []
    assert sla._accepted_projection_drift(
        watermark_date="20260716",
        watermark_row_count=99,
        watermark_parser_version="sync_runner_v1",
        frontier=frontier,
        expected_parser_version="margin_accepted_contract_1",
    ) == [
        "last_data_date=20260716!=20260715",
        "row_count=99!=3",
        "parser_version='sync_runner_v1'!='margin_accepted_contract_1'",
    ]


def test_min_rows_only_domain_uses_verified_frontier_instead_of_raw_max_date():
    raw = duck_mem()
    raw.execute("CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT, built_at TEXT)")
    raw.executemany(
        "INSERT INTO raw_probe VALUES (?, ?, ?)",
        [
            ("600000.SH", "20260709", "2026-07-10T00:00:00Z"),
            ("000001.SZ", "20260709", "2026-07-10T00:00:00Z"),
            ("300001.SZ", "20260709", "2026-07-10T00:00:00Z"),
            ("600000.SH", "20260710", "2026-07-11T00:00:00Z"),
        ],
    )
    verified_spec = {
        "target_table": "raw_probe",
        "grain": ["ts_code", "trade_date"],
        "date_param": "trade_date",
        "min_rows_per_batch": 3,
    }

    probe = sla._query_actual_frontier(
        {"tushare_raw": raw},
        {
            "sync:x": {
                "db": "tushare_raw",
                "query": "SELECT MAX(trade_date) FROM raw_probe",
                "verified_complete_spec": verified_spec,
            }
        },
        "sync:x",
    )

    assert probe.state == "verified"
    assert probe.actual_date == "20260709"
    assert probe.verified_frontier is not None
    assert probe.verified_frontier.row_count == 3


def test_query_actual_returns_none_when_db_unreachable():
    """库不可达 → None (调用方标 DB_LOCKED_UNVERIFIED), 不抛不伪装."""
    queries = {"sync:x": {"db": "tushare_raw", "query": "SELECT 1"}}
    assert sla._query_actual_max_date({"tushare_raw": None}, queries, "sync:x") is None
    assert sla._probe_gate("db_unavailable") == ("DB_LOCKED_UNVERIFIED", True)


def test_no_mapping_is_a_blocking_probe_failure():
    assert sla._probe_gate("no_mapping") == ("NO_QUERY_MAPPING", True)


def test_verified_probe_empty_and_query_error_fail_closed():
    raw = duck_mem()
    raw.execute("CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT)")
    spec = {
        "target_table": "raw_probe",
        "grain": ["ts_code", "trade_date"],
        "date_param": "trade_date",
        "min_rows_per_batch": 2,
        "batch_completeness": {
            "group_from": {"column": "ts_code", "transform": "exchange_suffix"},
            "required_groups": ["SH", "SZ"],
        },
    }

    empty_probe = sla._query_actual_frontier(
        {"tushare_raw": raw},
        {"sync:x": {"db": "tushare_raw", "verified_complete_spec": spec}},
        "sync:x",
    )
    assert empty_probe.state == "no_complete_batch"
    assert sla._probe_gate(empty_probe.state) == ("NO_COMPLETE_BATCH", True)

    broken_probe = sla._query_actual_frontier(
        {"tushare_raw": raw},
        {
            "sync:x": {
                "db": "tushare_raw",
                "verified_complete_spec": {**spec, "date_param": "missing_date"},
            }
        },
        "sync:x",
    )
    assert broken_probe.state == "probe_error"
    assert sla._probe_gate(broken_probe.state) == ("PROBE_ERROR", True)


def test_verified_frontier_can_correct_invalid_watermark_backward_only_with_proof():
    assert sla._watermark_reconcile_direction(
        "20260714", "20260709", verified_complete=True
    ) == "rollback"
    assert sla._watermark_reconcile_direction(
        "20260714", "20260709", verified_complete=False
    ) is None
    assert sla._watermark_reconcile_direction(
        "20260708", "20260709", verified_complete=False
    ) == "forward"


def test_verified_frontier_excludes_partial_latest_batch_and_repairs_metadata():
    raw = duck_mem()
    raw.execute(
        "CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT, built_at TEXT)"
    )
    raw.executemany(
        "INSERT INTO raw_probe VALUES (?, ?, ?)",
        [
            ("600000.SH", "20260709", "2026-07-10T06:48:49+00:00"),
            ("000001.SZ", "20260709", "2026-07-10T06:48:49+00:00"),
            ("600000.SH", "20260710", "2026-07-15T02:31:00+00:00"),
        ],
    )
    spec = {
        "target_table": "raw_probe",
        "grain": ["ts_code", "trade_date"],
        "date_param": "trade_date",
        "min_rows_per_batch": 2,
        "batch_completeness": {
            "group_from": {"column": "ts_code", "transform": "exchange_suffix"},
            "required_groups": ["SH", "SZ"],
        },
    }
    queries = {
        "sync:probe": {
            "db": "tushare_raw",
            "verified_complete_spec": spec,
        }
    }

    probe = sla._query_actual_frontier(
        {"tushare_raw": raw}, queries, "sync:probe"
    )
    actual_date, frontier = probe.actual_date, probe.verified_frontier

    assert actual_date == "20260709"
    assert frontier is not None and frontier.row_count == 2
    assert str(frontier.last_success_at).startswith("2026-07-10T06:48:49")

    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    upsert_watermark(
        smart,
        {
            "data_domain": "sync:probe",
            "source_name": "tushare",
            "source_tier": 2,
            "last_success_at": "2026-07-15T11:54:54+00:00",
            "last_data_date": "20260710",
            "row_count": 0,
        },
    )
    sla._apply_watermark_reconcile(
        smart,
        data_domain="sync:probe",
        source_name="tushare",
        source_tier=2,
        actual_date=actual_date,
        verified_frontier=frontier,
    )
    row = smart.execute(
        "SELECT last_data_date, row_count, last_success_at "
        "FROM mart_data_source_watermark WHERE data_domain='sync:probe'"
    ).fetchone()
    assert row[0] == "20260709" and row[1] == 2
    assert str(row[2]).startswith("2026-07-10 06:48:49")


def test_reconcile_updates_only_exact_watermark_primary_key_and_clears_unverified_time():
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    for tier in (1, 2):
        upsert_watermark(
            smart,
            {
                "data_domain": "sync:probe",
                "source_name": "tushare",
                "source_tier": tier,
                "last_success_at": "2026-07-15T11:54:54+00:00",
                "last_data_date": "20260714",
                "row_count": 99,
            },
        )

    sla._apply_watermark_reconcile(
        smart,
        data_domain="sync:probe",
        source_name="tushare",
        source_tier=2,
        actual_date="20260709",
        verified_frontier=VerifiedBatchFrontier("20260709", 2, None),
    )

    rows = smart.execute(
        "SELECT source_tier, last_data_date, row_count, last_success_at "
        "FROM mart_data_source_watermark ORDER BY source_tier"
    ).fetchall()
    assert tuple(rows[0][i] for i in range(3)) == (1, "20260714", 99)
    assert str(rows[0][3]).startswith("2026-07-15 11:54:54")
    assert tuple(rows[1][i] for i in range(3)) == (2, "20260709", 2)
    assert rows[1][3] is None
