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
    # Formal daily/ST: accepted_partition frontier (not legacy verified_complete_spec).
    assert queries["sync:daily"].get("formal_accepted_frontier")
    assert "accepted_partition" in queries["sync:daily"]["query"]
    assert queries["sync:fina_indicator"].get("no_probe")  # by_ts_code 季度域显式 no_probe


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

    # Knife 1b: margin is enabled/on_demand (not execution_disabled) → alertable.
    assert result["status"] == "NO_COMPLETE_BATCH"
    assert result["probe_state"] == "no_complete_batch"
    assert result["actual_date"] is None
    assert result["alert"] is True
    assert result["observe_only"] is False


def test_registered_live_domain_without_watermark_still_alerts():
    """Non-frozen sync domains keep fail-closed MISSING / NO_COMPLETE alerts."""
    queries = sla._sync_registry_queries()
    # moneyflow is live (not disabled) — invent a verified-empty probe path via
    # temporary qspec without observe_only.
    qspec = {
        "db": "tushare_raw",
        "verified_complete_spec": {
            "target_table": "raw_probe",
            "grain": ["ts_code", "trade_date"],
            "date_param": "trade_date",
            "min_rows_per_batch": 2,
        },
        "sla_days": 2,
    }
    raw = duck_mem()
    raw.execute("CREATE TABLE raw_probe (ts_code TEXT, trade_date TEXT)")
    result = sla._registered_domain_without_watermark_result(
        {"tushare_raw": raw},
        {"sync:x": qspec},
        "sync:x",
        qspec,
        sla.date(2026, 7, 17),
    )
    assert result["status"] == "NO_COMPLETE_BATCH"
    assert result["alert"] is True
    assert not result.get("observe_only")


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


def test_cx4_legacy_observer_is_typed_no_probe_not_alert():
    """unknown≠stale: strangler observer must not light sla_warn via NO_QUERY_MAPPING."""
    q = sla.DATA_SOURCE_QUERIES["holders_top10_float_legacy_observer"]
    assert q.get("no_probe") == "legacy_observer_not_publication_truth"
    probe = sla._query_actual_frontier({"smartmoney": object()}, sla.DATA_SOURCE_QUERIES,
                                       "holders_top10_float_legacy_observer")
    assert probe.state == "no_probe"
    assert sla._probe_gate(probe.state) == ("NO_PROBE_RULE", False)


def test_cx4_qfii_has_real_probe_and_disclosure_sla():
    assert "query" in sla.DATA_SOURCE_QUERIES["qfii_holding_quarterly"]
    assert sla.SLA_DAYS_OVERRIDE["qfii_holding_quarterly"] == 160
    smart = duck_mem()
    smart.execute(
        "CREATE TABLE raw_qfii_holding_quarterly(report_date VARCHAR, ts_code VARCHAR)"
    )
    smart.execute(
        "INSERT INTO raw_qfii_holding_quarterly VALUES ('2026-03-31', '600000.SH')"
    )
    probe = sla._query_actual_frontier(
        {"smartmoney": smart}, sla.DATA_SOURCE_QUERIES, "qfii_holding_quarterly"
    )
    assert probe.state == "observed"
    assert str(probe.actual_date).startswith("2026-03-31")
    # Within disclosure window: age 114d < 160+3 → not actionable stale.
    assert sla._days_since("2026-03-31", sla.date(2026, 7, 23)) == 114
    assert 114 <= sla.SLA_DAYS_OVERRIDE["qfii_holding_quarterly"] + 3


def test_cx4_margin_enabled_catchup_is_alertable_not_observe_only():
    """Knife 1b: enabled bounded catchup must not hide lag behind observe_only."""
    queries = sla._sync_registry_queries()
    assert not queries["sync:margin"].get("observe_only")
    # Live daily/ST must stay alertable (on_demand alone ≠ frozen).
    assert not queries["sync:daily"].get("observe_only")
    assert not queries["sync:stock_st"].get("observe_only")


def test_cx4_retired_sync_orphan_watermark_tombs_purge():
    """Sunset sync:* orphans (stk_factor_pro/express/…) purge NO_QUERY_MAPPING residue."""
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    for domain in (
        "sync:stk_factor_pro",
        "sync:express",
        "sync:fina_mainbz",
        "sync:stk_holdernumber",
    ):
        upsert_watermark(
            smart,
            {
                "data_domain": domain,
                "source_name": "tushare",
                "source_tier": 2,
                "last_data_date": "20260618",
                "row_count": 1,
            },
        )
    upsert_watermark(
        smart,
        {
            "data_domain": "sync:moneyflow",
            "source_name": "tushare",
            "source_tier": 2,
            "last_data_date": "20260722",
            "row_count": 10,
        },
    )
    purged = sla._purge_retired_watermark_tombs(smart, dry_run=False)
    purged_domains = {row["data_domain"] for row in purged}
    assert purged_domains == {
        "sync:stk_factor_pro",
        "sync:express",
        "sync:fina_mainbz",
        "sync:stk_holdernumber",
    }
    left = {
        str(row[0])
        for row in smart.execute(
            "SELECT data_domain FROM mart_data_source_watermark"
        ).fetchall()
    }
    assert left == {"sync:moneyflow"}


def test_cx4_retired_lhb_tombstone_purge_allowlist_only():
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    upsert_watermark(
        smart,
        {
            "data_domain": "lhb_daily",
            "source_name": "aif10_lhb",
            "source_tier": 2,
            "last_data_date": "2026-06-26",
            "row_count": 1,
        },
    )
    # Live holders row must survive.
    upsert_watermark(
        smart,
        {
            "data_domain": "holders_top10_float",
            "source_name": "miaoxiang",
            "source_tier": 1,
            "last_data_date": "20260717",
            "row_count": 10,
        },
    )
    purged = sla._purge_retired_watermark_tombs(smart, dry_run=False)
    assert len(purged) == 1
    assert purged[0]["data_domain"] == "lhb_daily"
    assert purged[0]["action"] == "deleted"
    left = smart.execute(
        "SELECT data_domain, source_name FROM mart_data_source_watermark "
        "ORDER BY data_domain"
    ).fetchall()
    assert [(r[0], r[1]) for r in left] == [("holders_top10_float", "miaoxiang")]


def test_cx4_refuses_tombstone_purge_if_domain_still_in_specs(monkeypatch):
    monkeypatch.setattr(
        sla,
        "RETIRED_WATERMARK_TOMBSTONES",
        frozenset({("holders_top10_float", "miaoxiang")}),
    )
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    with pytest.raises(RuntimeError, match="refusing tombstone purge"):
        sla._purge_retired_watermark_tombs(smart, dry_run=True)


def test_cx4_purge_dry_run_preserves_qfii_row():
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    upsert_watermark(
        smart,
        {
            "data_domain": "lhb_daily",
            "source_name": "aif10_lhb",
            "source_tier": 2,
            "last_data_date": "2026-06-26",
            "row_count": 1,
        },
    )
    upsert_watermark(
        smart,
        {
            "data_domain": "qfii_holding_quarterly",
            "source_name": "aif10_qfii",
            "source_tier": 2,
            "last_data_date": "2026-03-31",
            "row_count": 9,
        },
    )
    dry = sla._purge_retired_watermark_tombs(smart, dry_run=True)
    assert len(dry) == 1 and dry[0]["action"] == "would_delete"
    assert (
        smart.execute(
            "SELECT COUNT(*) FROM mart_data_source_watermark "
            "WHERE data_domain='lhb_daily' AND source_name='aif10_lhb'"
        ).fetchone()[0]
        == 1
    )
    sla._purge_retired_watermark_tombs(smart, dry_run=False)
    assert (
        smart.execute(
            "SELECT COUNT(*) FROM mart_data_source_watermark "
            "WHERE data_domain='qfii_holding_quarterly'"
        ).fetchone()[0]
        == 1
    )


def test_cx4_manual_domain_specs_have_sla_mapping():
    """Inventory gate: live DOMAIN_SPECS cannot silently fall into NO_QUERY_MAPPING."""
    sla._assert_manual_domain_sla_inventory()


def test_cx4_unknown_domain_still_alerts_no_mapping():
    """Kill: must not silence true unknown (no_mapping → alert)."""
    probe = sla._query_actual_frontier({}, {}, "totally_unknown_domain_cx4")
    assert probe.state == "no_mapping"
    assert sla._probe_gate(probe.state) == ("NO_QUERY_MAPPING", True)


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


# ── SLA 的轴 (2026-08-16) ────────────────────────────────────────────────
# 本文件此前没有任何一条覆盖 SLA **判定**本身(都在测 registry 契约与探测面),
# 这正是「声明交易日 / 实现自然日 + `+3` 补丁」能长期存活的原因。

import datetime as _dt

from services.calendar import trading_days_since as _cal_trading_days_since


def _cal(*days: str) -> list:
    return [_dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) for d in days]


def test_trading_day_distance_ignores_weekends_and_holidays() -> None:
    """交易日距离必须只数交易日 —— 周末/长假不算陈旧。

    旧实现用自然日 `(today - d).days` 近似, 再用 `+3` 补周末; 实测 2023-01-01~2026-08-14
    的 876 个交易日, 该近似让「落后 2 个交易日」在 **95.0%** 的日子里静默。
    """
    days = _cal("20260807", "20260810", "20260811")  # 周五, 下周一, 周二
    # 周五 → 周二 自然日隔 4 天, 但只过了 2 个交易日
    assert _cal_trading_days_since("20260807", _dt.date(2026, 8, 11), days) == 2
    assert sla._days_since("20260807", _dt.date(2026, 8, 11)) == 4, "对照: 自然日算术确实是 4"


def test_long_holiday_does_not_create_false_alert() -> None:
    """长假后首个交易日: 域完全合规(落后 1 交易日)不得判红。

    旧实现实测在 2023-01-01 以来制造 **15 次**这类误报, 全部落在长假后首个交易日。
    """
    days = _cal("20260130", "20260209")  # 中间隔春节
    assert _cal_trading_days_since("20260130", _dt.date(2026, 2, 9), days) == 1
    assert sla._days_since("20260130", _dt.date(2026, 2, 9)) == 10, "对照: 自然日龄 10 会误判"


def test_calendar_unavailable_is_unverified_not_pass() -> None:
    """日历取不到 → None, 由调用方判 UNVERIFIED; **绝不退回自然日冒充**。

    「查不了」不等于「没问题」—— 静默按自然日代算就是把无法判定伪装成通过。
    """
    assert _cal_trading_days_since("20260807", _dt.date(2026, 8, 11), None) is None


def test_two_axes_are_declared_and_quarterly_stays_calendar() -> None:
    """同一个裸数字在不同条目里是不同单位, 必须显式带轴。

    季报 override(100/160)按其注释是**自然日**(Mar31→Aug31 披露截止 ≈ 153d),
    而 tier 默认与 registry 的 `freshness_sla_trading_days` 是**交易日**。
    """
    assert sla.SLA_AXIS_OVERRIDE["holders_top10_float"] == sla.AXIS_CALENDAR
    assert sla.SLA_AXIS_OVERRIDE["qfii_holding_quarterly"] == sla.AXIS_CALENDAR
    assert sla.SLA_AXIS_OVERRIDE.get("sync:daily", sla.AXIS_TRADING) == sla.AXIS_TRADING


def test_weekend_buffer_patch_is_gone() -> None:
    """`+3` 缓冲必须消失 —— 它存在只是为了拿自然日近似交易日。

    保留它会让逐域声明的 SLA 值继续从不单独触发(原实现外层 `> sla` 里只套着
    `> sla + 3` 且无 else)。实测此刻就有 12 个域因此被静默放过。
    """
    # 只看**活代码**: 注释里会引用旧写法来解释改了什么, 那是说明不是判据。
    # (同款错我犯过一次 —— 门测试的正则连注释一起扫, 把散文当调用点。)
    code = "\n".join(
        ln for ln in SCRIPT_PATH.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "sla + 3" not in code, "周末缓冲补丁不得在活代码里复活"

