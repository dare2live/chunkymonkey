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


def test_frozen_domain_without_watermark_observes_instead_of_alerting():
    """冻结域缺批次时: 状态必须**可区分**于活域, 且不刷 alert。

    2026-09-07 改判据。本测试原名 ..._margin_..._alerts_on_no_acceptance, 断言
    status == "NO_COMPLETE_BATCH" / alert is True —— 那是 margin **当时还是活域**时的行为。
    margin 于 commit 01f8f41a2 按 tushare_sunset 台账切成 freeze 后必然假红。

    活域侧的覆盖没有丢: 紧接其后的
    ``test_registered_live_domain_without_watermark_still_alerts`` 用**自带的**合成 qspec
    覆盖同一条路径, 且不依赖任何真实域当下恰好是什么状态 —— 那才是这类测试该有的写法。

    这里改守一件**此前没有任何东西在守**的事: 冻结不能把缺数据变成静默。
    状态串必须带 FROZEN 前缀 (对人对机器都可区分), alert 必须关, observe_only 必须开 ——
    三者缺一, 「预期断流」和「真断流」就会长得一模一样。
    """
    queries = sla._sync_registry_queries()
    qspec = queries["sync:margin"]
    assert qspec.get("observe_only") is True, (
        "本测试的前提是 margin 已冻结; 若它被改回活域, 该改的是这个前提而不是断言"
    )

    raw = duck_mem()
    raw.execute("CREATE TABLE raw_tushare_margin(trade_date VARCHAR)")
    raw.execute("INSERT INTO raw_tushare_margin VALUES ('20991231')")

    result = sla._registered_domain_without_watermark_result(
        {"tushare_raw": raw},
        queries,
        "sync:margin",
        qspec,
        sla.date(2026, 7, 17),
    )

    assert result["status"].startswith("FROZEN_"), result["status"]
    assert result["probe_state"] == "no_complete_batch"
    assert result["actual_date"] is None
    assert result["alert"] is False
    assert result["observe_only"] is True


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


def test_cx4_observe_only_holds_exactly_for_execution_disabled_domains():
    """Knife 1b 的不变量版: observe_only **当且仅当** execution_policy.mode == disabled。

    2026-09-07 改判据。原测试断言 ``not queries["sync:margin"].get("observe_only")``
    外加 daily/stock_st 两个域名, 钉的是「margin 当时恰好是 enabled」这个**运行时状态**,
    不是它想守的不变量。margin 于 commit 01f8f41a2 按 tushare_sunset 台账 decision=freeze
    切成 disabled 后, 这条断言必然假红 —— 而 Knife 1b 想守的东西一个字没变。

    改成对**全部登记域**跑双条件, 两个方向都守 (来源: update_watermark_sla.py 里
    observe_only 的唯一赋值点, 条件就是 exec_pol["mode"] == "disabled"):
      - disabled 却没 observe_only → 冻结域每天刷 alert, **真断流混进预期断流**;
      - 非 disabled 却 observe_only → 活域滞后被静默吞掉, 正是 Knife 1b 要防的那件事。

    写成全域扫描而不是列名单, 是因为「清单型判据」对不在册的新域没有任何约束 ——
    新增或冻结任何域都自动进覆盖, 不需要有人记得回来改这份名单。
    """
    import yaml

    from services.data_sources.sync_runner import domain_spec

    reg = yaml.safe_load(
        (SCRIPT_PATH.resolve().parents[1] / "config" / "sync_registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    queries = sla._sync_registry_queries()

    violations = []
    for name in reg["domains"]:
        entry = queries.get(f"sync:{name}")
        if not isinstance(entry, dict):
            continue
        disabled = (
            domain_spec(reg, name).get("execution_policy") or {}
        ).get("mode") == "disabled"
        observe_only = bool(entry.get("observe_only"))
        if disabled != observe_only:
            violations.append(
                f"{name}: execution_disabled={disabled} 但 observe_only={observe_only}"
            )
    assert not violations, "observe_only 与 execution_policy 脱节:\n" + "\n".join(
        violations
    )

    # 双条件在两侧都必须真有样本, 否则「零违反」可能只是因为一侧是空集。
    modes = {
        name: (domain_spec(reg, name).get("execution_policy") or {}).get("mode")
        for name in reg["domains"]
    }
    assert any(m == "disabled" for m in modes.values()), "无冻结域, 本测试退化为空断言"
    assert any(m != "disabled" for m in modes.values()), "无活域, 本测试退化为空断言"


def test_cx4_retired_sync_orphan_watermark_tombs_purge():
    """Sunset sync:* orphans purge NO_QUERY_MAPPING residue — 但活域必须幸存.

    2026-08-23: sync:stk_holdernumber 从「应被清」挪到「应幸存」侧。它此前被列入
    RETIRED_WATERMARK_TOMBSTONES, 而 registry 里 execution_policy=None、运行时
    mode=enabled/reason=active, 每日实跑 2789 批次 / 344,453 行且数据新鲜, 并有活的
    消费链 (holdernumber_assist -> stock_dossier router)。后果是 sync_runner 正确写入的
    watermark 每轮被清, 使它成为 44 个域里唯一无新鲜度监控的域 (goal.md A2)。
    此处保留它的写入 + 断言它幸存, 正是为了锁住该修复不被回退。
    """
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
    }
    left = {
        str(row[0])
        for row in smart.execute(
            "SELECT data_domain FROM mart_data_source_watermark"
        ).fetchall()
    }
    # 活域 (moneyflow + stk_holdernumber) 都不许被墓碑清理波及。
    assert left == {"sync:moneyflow", "sync:stk_holdernumber"}


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


# ── 2026-09-18 (project cut_frozen_domain_verdicts, 实测 watermark_sla_20260918.json) ──
#
# 当天 n_alerts=2: industry_dc(DATA_STALE_VS_SLA, 派生自已冻结的 dc_member, 没继承
# observe_only) 与 sync:baostock_trade_cal(NO_QUERY_MAPPING, 域已从 registry 撤销但
# watermark 残留行还在)。两条形状不同, 分开修分开测。

def test_baostock_trade_cal_orphan_watermark_is_purged():
    """域已从 sync_registry.yaml 撤销(grep 全仓零命中), 残留 watermark 行走既有

    allowlist-purge 机制清掉 —— 与 stk_factor_pro 等六个同型, 不新发明判据。"""
    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    upsert_watermark(
        smart,
        {
            "data_domain": "sync:baostock_trade_cal",
            "source_name": "baostock",
            "source_tier": 2,
            "last_data_date": "20260101",
            "row_count": 1,
        },
    )
    # 活域同批次必须幸存 (隔离用例: 只有目标行被清, 不是整表清空)。
    upsert_watermark(
        smart,
        {
            "data_domain": "sync:moneyflow",
            "source_name": "tushare",
            "source_tier": 2,
            "last_data_date": "20260828",
            "row_count": 10,
        },
    )
    purged = sla._purge_retired_watermark_tombs(smart, dry_run=False)
    assert {row["data_domain"] for row in purged} == {"sync:baostock_trade_cal"}
    left = {
        str(row[0])
        for row in smart.execute("SELECT data_domain FROM mart_data_source_watermark").fetchall()
    }
    assert left == {"sync:moneyflow"}


def test_baostock_trade_cal_not_in_live_domain_specs():
    """新墓碑加进 allowlist 前必须先证明它不是活域, 否则会撞

    _purge_retired_watermark_tombs 的 live_keys 保护 (raise RuntimeError)。"""
    from services.source_watermarks import DOMAIN_SPECS

    live_keys = {(str(s["data_domain"]), str(s["source_name"])) for s in DOMAIN_SPECS}
    assert ("sync:baostock_trade_cal", "baostock") not in live_keys


def test_industry_dc_inherits_observe_only_when_upstream_frozen():
    """dc_member 冻结时, 派生自它的 industry_dc 必须跟着转 observe_only —— 否则派生面

    会为一个再也不会有新数据的上游永远报 DATA_STALE_VS_SLA。"""
    queries = {**sla.DATA_SOURCE_QUERIES}
    registry_queries = {
        "sync:dc_member": {
            "db": "tushare_raw",
            "observe_only": True,
            "observe_reason": "tushare_sunset_freeze",
        }
    }
    sla._apply_derived_observe_only(queries, registry_queries)

    entry = queries["industry_dc"]
    assert entry["observe_only"] is True, entry
    assert "dc_member" in entry["observe_reason"], entry
    assert "tushare_sunset_freeze" in entry["observe_reason"], entry
    # 原始查询字段不能丢 —— 这是"改状态不能连带丢功能"的隔离检查。
    assert entry["query"] == sla.DATA_SOURCE_QUERIES["industry_dc"]["query"]


def test_industry_dc_stays_alertable_when_upstream_enabled():
    """隔离用例: 其它全满足(映射存在、upstream 条目存在), 只有 upstream 未冻结 ——

    派生域必须保持 observe_only=False, 不能只要映射存在就一律放行。"""
    queries = {**sla.DATA_SOURCE_QUERIES}
    registry_queries = {"sync:dc_member": {"db": "tushare_raw", "observe_only": False}}
    sla._apply_derived_observe_only(queries, registry_queries)

    assert queries["industry_dc"].get("observe_only") is False
    assert "observe_reason" not in queries["industry_dc"]


def test_industry_dc_reverts_when_upstream_thaws_same_process():
    """退出规则: 上游解冻后, 同一进程内再跑一次必须自动改回 False —— 不是只加不减的

    单向开关(否则一次冻结记录就会永远压住这条域的告警, 变成另一种"永远绿")。"""
    queries = {**sla.DATA_SOURCE_QUERIES}
    frozen = {"sync:dc_member": {"db": "tushare_raw", "observe_only": True,
                                 "observe_reason": "tushare_sunset_freeze"}}
    sla._apply_derived_observe_only(queries, frozen)
    assert queries["industry_dc"]["observe_only"] is True

    thawed = {"sync:dc_member": {"db": "tushare_raw", "observe_only": False}}
    sla._apply_derived_observe_only(queries, thawed)
    assert queries["industry_dc"]["observe_only"] is False
    assert "observe_reason" not in queries["industry_dc"]


def test_apply_derived_observe_only_does_not_mutate_module_constant():
    """DATA_SOURCE_QUERIES["industry_dc"] 与 queries["industry_dc"] 起初是同一个 dict

    对象(浅拷贝) —— 必须用新 dict 替换而非原地改, 否则会把 observe_only 焊死进模块级
    常量, 污染同进程内其它调用/测试。"""
    frozen = {"sync:dc_member": {"db": "tushare_raw", "observe_only": True,
                                 "observe_reason": "tushare_sunset_freeze"}}
    queries = {**sla.DATA_SOURCE_QUERIES}
    sla._apply_derived_observe_only(queries, frozen)

    assert queries["industry_dc"]["observe_only"] is True
    assert not sla.DATA_SOURCE_QUERIES["industry_dc"].get("observe_only"), (
        "污染了模块级常量 —— 下一次调用会带着上一次的冻结状态, 与输入无关"
    )


def _direct_call_names_in_function_body(func) -> set[str]:
    """func 函数体**顶层语句**里直接调用的函数名集合 —— 只看 func_def.body 的直接子
    语句(``Expr(Call(...))`` / ``Assign(value=Call(...))``), 不递归进任何 If/For/While/
    Try/With 等控制流节点内部。

    2026-09-18 blocking finding 修复(project cut_frozen_domain_verdicts): 此前两条
    "wired into main" 测试都是 ``"_x(...)" in inspect.getsource(main)`` 纯子串匹配 ——
    把调用包进 ``if False:`` 死分支, 文本依旧能被 ``in`` 命中, 测试照样绿(变异实测两次
    独立验证)。子串匹配的盲区是"文本存在"不等于"会执行到"; 这里改用 AST 只认**函数体的
    直接语句**, 一旦调用被包进任何嵌套的 if/for/try, 它就不再是 func_def.body 的直接
    子节点, 检测不到, 从而与"死分支里的调用文本"精确区分开。"""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef), func_def
    names: set[str] = set()
    for stmt in func_def.body:
        value = None
        if isinstance(stmt, ast.Expr):
            value = stmt.value
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            value = stmt.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            names.add(value.func.id)
    return names


def test_apply_derived_observe_only_is_wired_into_main():
    """_apply_derived_observe_only 存在但没接进 main() 等于没用 —— AST 钉住它是 main()

    函数体的一条**顶层直接语句**(不是随便躲在某个死分支里的调用文本), 防止未来重构时
    函数还在但接线被顺手删掉、或被包进永不执行的分支(同型教训:
    usage-closure-cannot-be-inferred —— 闭包/接线不能靠人工检查记住, 必须有断言钉住;
    见 _direct_call_names_in_function_body docstring 里记录的两次独立变异存活证据)。
    """
    assert "_apply_derived_observe_only" in _direct_call_names_in_function_body(sla.main), (
        "main() 函数体的顶层语句里没有直接调用 _apply_derived_observe_only —— 哪怕调用"
        "文本出现在源码某处(例如被包进 `if False:` 死分支里), 也判定为没接线, "
        "派生域的 observe_only 永远不会被应用"
    )


def test_industry_dc_real_registry_shape_matches_20260918_audit():
    """用真实 sync_registry.yaml 复现 09-18 实测形状: dc_member 当前确实是

    execution_policy.mode=disabled, 应用后 industry_dc 确实拿到 observe_only=True。
    真库/真跑批不在本测试范围内 —— 只读 YAML, 不连 DuckDB。"""
    registry_queries = sla._sync_registry_queries()
    dc_member = registry_queries.get("sync:dc_member")
    assert dc_member is not None, "dc_member 不在 sync_registry.yaml 里 —— 本测试的前提已变"
    assert dc_member.get("observe_only") is True, (
        "本测试前提是 dc_member 当前已冻结; 若它被改回活域, 该改的是这个前提而不是断言"
    )

    queries = {**sla.DATA_SOURCE_QUERIES, **registry_queries}
    sla._apply_derived_observe_only(queries, registry_queries)
    assert queries["industry_dc"]["observe_only"] is True


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


# ── 冻结域 rollback 门 (2026-09-18 blocking finding 修复, project cut_frozen_domain_verdicts) ──
#
# check_continuity_integrity._frozen_watermark_anchor_max 把 mart_data_source_watermark
# 当作"冻结后不会再被移动"的独立锚点, 但这条可信性此前只是个未验证的假设: 本文件的
# rollback 分支(727/806 两处既有 observe_only 门唯独漏了这条)对 disabled 域一样会把
# verified_frontier 现查出的 actual 真实写回 watermark —— 而那个 actual 与
# check_completeness_ref 的 local_max 同源同险(都现查自域自己那张可能已被静默削尾的
# 原始表), 于是"冻结锚点"实际上会跟着一起被污染。_reconcile_status_and_apply 补上这道门:
# rollback + observe_only 时只记录 status, 不调用 _apply_watermark_reconcile; forward 不受
# 影响(前移不丢失任何已核实的水位信息, 是既有两处门都认可的安全方向)。

def test_reconcile_status_and_apply_blocks_rollback_when_observe_only():
    """隔离(其它全满足, 只违反"未冻结"这一条): rollback 候选 + observe_only=True
    —— 不许返回 should_apply=True, 状态改叫 FROZEN_ROLLBACK_OBSERVED 而不是
    INVALID_WATERMARK_FRONTIER(与 alert 应转 False 的既有两处 OBSERVE 状态同一命名族)。"""
    status, should_apply = sla._reconcile_status_and_apply("rollback", observe_only=True)
    assert status == "FROZEN_ROLLBACK_OBSERVED", status
    assert should_apply is False


def test_reconcile_status_and_apply_allows_rollback_when_not_observe_only():
    """隔离(其它全满足, 只违反"已冻结"这一条): 同样是 rollback 候选, 但域未冻结
    (enabled 孪生) —— 必须维持修复前的既有行为不变: INVALID_WATERMARK_FRONTIER 且真的写库,
    证明这道新门的作用域只盖 observe_only 域, 不是把 rollback 整体收紧。"""
    status, should_apply = sla._reconcile_status_and_apply("rollback", observe_only=False)
    assert status == "INVALID_WATERMARK_FRONTIER", status
    assert should_apply is True


def test_reconcile_status_and_apply_allows_forward_even_when_observe_only():
    """隔离(其它全满足, 只违反"是 rollback 方向"这一条): forward 候选即使域已冻结也照样
    放行 —— 前移不丢失任何已核实的水位信息, 该门只针对 rollback 方向, 不是把冻结域的
    reconcile 整体锁死。"""
    status, should_apply = sla._reconcile_status_and_apply("forward", observe_only=True)
    assert status == "STALE_WATERMARK", status
    assert should_apply is True


def test_reconcile_status_and_apply_none_when_no_candidate():
    """隔离(其它全满足, 只违反"存在 reconcile 候选"这一条): 无候选(None)时不论
    observe_only 取何值都必须原样透传 None/不应用, 不能凭空造出一个状态。"""
    assert sla._reconcile_status_and_apply(None, observe_only=True) == (None, False)
    assert sla._reconcile_status_and_apply(None, observe_only=False) == (None, False)


def _find_if_comparing_name_to_constant(func, var_name: str, constant_value: str):
    """在 func 源码里找 test **恰好结构等价于** ``var_name == constant_value`` 的 If 节点

    (AST 结构比对, 不是子串匹配), 查不到返回 None。用于区分"这个精确分支确实存在
    且可执行"与"这段比较文本恰好出现在源码某处"(后者哪怕分支被换成 `if False:` 也不受
    影响, 见 test_reconcile_status_and_apply_is_wired_into_main 修复背景)。"""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name) and test.left.id == var_name
            and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == constant_value
        ):
            return node
    return None


def _find_if_directly_guarding_call(func, call_name: str):
    """在 func 源码里找**最内层**、其 ``if`` 分支体(真分支, 不含 orelse)直接语句就是

    对 call_name 调用的 If 节点 —— 用于确认某个调用真的被放在一条可读出判断条件的
    if 分支之内, 而不是仅凭子串匹配"某个含 should_apply 的词在调用之前出现过"
    (旧测试的写法: `"should_apply" in src[:src.index("_apply_watermark_reconcile(")]`——
    只要 should_apply 这个词在源码里任何更早的位置出现过就能通过, 与它是否真的守着
    这次调用无关)。查不到时抛 AssertionError(调用方直接把这当成断言用)。"""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == call_name
            ):
                return node
    raise AssertionError(
        f"{getattr(func, '__qualname__', func)} 源码里找不到直接守着 "
        f"{call_name}(...) 调用(作为 if 真分支的直接语句)的 if 节点"
    )


def test_reconcile_status_and_apply_is_wired_into_main():
    """_reconcile_status_and_apply 存在但没接进 main() 等于没用。AST 结构比对钉住两件

    独立的事(2026-09-18 blocking finding 修复, project cut_frozen_domain_verdicts —
    旧版纯子串匹配对`if False:`包住整段分支的死代码完全免疫不了, 变异实测 48 项全绿):
    (1) `if reconcile_status == "FROZEN_ROLLBACK_OBSERVED":` 这个精确分支必须作为一条
    真实可执行的 If 节点存在(不是被替换成 `if False:` 或改写成别的判断), 且分支体内
    确实把 alert 关掉、绝不触碰 _apply_watermark_reconcile(这条分支存在的意义就是
    跳过写库); (2) _apply_watermark_reconcile 的真实调用点被一条测试里能读出
    `should_apply` 字样的 if 条件直接守着(而不是"should_apply 这个词在调用前的源码
    某处出现过")。两条各自独立失败, 合起来才是"main() 真的按 should_apply 分派"。"""
    import ast

    observe_if = _find_if_comparing_name_to_constant(
        sla.main, "reconcile_status", "FROZEN_ROLLBACK_OBSERVED"
    )
    assert observe_if is not None, (
        'main() 里找不到 `if reconcile_status == "FROZEN_ROLLBACK_OBSERVED":` 这个精确'
        "分支(AST 结构比对) —— 该分支若被替换成 `if False:` 或改写判断条件, 冻结域"
        "rollback 门就形同虚设"
    )
    observe_body_src = "\n".join(ast.unparse(stmt) for stmt in observe_if.body)
    assert "alert" in observe_body_src and "False" in observe_body_src, observe_body_src
    assert "_apply_watermark_reconcile" not in observe_body_src, (
        "FROZEN_ROLLBACK_OBSERVED 分支体内不应该出现 _apply_watermark_reconcile 调用 —— "
        "这条分支存在的意义就是跳过写库, 与 _apply_watermark_reconcile 的调用点必须互斥"
    )

    guard_if = _find_if_directly_guarding_call(sla.main, "_apply_watermark_reconcile")
    guard_src = ast.unparse(guard_if.test)
    assert "should_apply" in guard_src, (
        f"_apply_watermark_reconcile 的调用没有被含 should_apply 的 if 条件直接守住 "
        f"(实际条件: {guard_src!r})"
    )


def test_frozen_domain_rollback_does_not_poison_completeness_ref_anchor():
    """跨文件回归: update_watermark_sla 的 rollback 门与 check_continuity_integrity 的
    冻结锚点回退防线是同一个 blocking finding 的两半, 在同一个用例里真实调用两边的函数,
    不是各自文件内孤立打桩(见 check_continuity_integrity._frozen_watermark_anchor_max
    docstring "两个脚本口径一致"段落)。

    场景: moneyflow 冻结前最后一次真实同步定格在 20260828; 冻结后原始表被静默削尾,
    现查 actual 倒退到 20260827 且 verified_complete=True(min_rows_per_batch/
    batch_completeness 域的真实形状)。若旧代码(无 observe_only 门)对这个 disabled 域
    仍然真的执行 rollback, mart_data_source_watermark 会被这次 UPDATE 带偏成 20260827,
    锚点与被削尾后的现算值一起倒退, check_continuity_integrity 就再也测不出这次静默损坏
    (fail_frozen_regression 需要 anchor_max > local_max 才触发, 两者被一起拉平后条件
    永远不成立)。本刀修过的门挡住这次 rollback 之后, 锚点保持 20260828 不变,
    fail_frozen_regression 依然触发。"""
    import importlib.util

    cci_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "check_continuity_integrity.py"
    )
    cci_spec = importlib.util.spec_from_file_location(
        "check_continuity_integrity_cross_file_test", cci_path
    )
    cci = importlib.util.module_from_spec(cci_spec)
    assert cci_spec and cci_spec.loader
    cci_spec.loader.exec_module(cci)

    smart = duck_mem()
    ensure_source_watermark_schema(smart)
    frozen_last_data_date = "20260828"   # 域冻结前最后一次真实同步定格的水位
    upsert_watermark(
        smart,
        {
            "data_domain": "sync:moneyflow",
            "source_name": "tushare",
            "source_tier": 2,
            "last_data_date": frozen_last_data_date,
            "row_count": 10,
        },
    )

    poisoned_actual = "20260827"   # 冻结后原始表被静默削尾, 现查值倒退一天
    reconcile = sla._watermark_reconcile_direction(
        frozen_last_data_date, poisoned_actual, verified_complete=True,
    )
    assert reconcile == "rollback", "本用例前提: 现算值必须真的倒退, 否则测的不是这个场景"

    status, should_apply = sla._reconcile_status_and_apply(reconcile, observe_only=True)
    assert status == "FROZEN_ROLLBACK_OBSERVED", status
    assert should_apply is False

    if should_apply:  # 只在门被绕过(修复前的旧行为)时才会走到这里
        sla._apply_watermark_reconcile(
            smart, data_domain="sync:moneyflow", source_name="tushare", source_tier=2,
            actual_date=poisoned_actual, verified_frontier=None,
        )

    # check_continuity_integrity 侧: 锚点必须仍是冻结前的真实水位, 没被上面这次
    # (已被挡住的) rollback 带偏。
    anchor_max = cci._frozen_watermark_anchor_max(
        "moneyflow", "mart_data_source_watermark", smart,
    )
    assert anchor_max == frozen_last_data_date, (
        "锚点被 rollback 带偏了 —— observe_only 门没有真的挡住写库"
    )

    # 端到端: local_max(被削尾后的现算值, 比锚点早一天)配合这个未被污染的锚点,
    # completeness_ref 必须判 fail_frozen_regression, 不能被静默吸收成 observe。
    tds = [f"202608{d:02d}" for d in range(20, 32)]
    mine = duck_mem()
    mine.execute("create table canonical_nominal_ohlcv_daily (trade_date VARCHAR)")
    mine.execute("create table mine (trade_date VARCHAR)")
    for d in tds:
        mine.execute("insert into canonical_nominal_ohlcv_daily values (?)", [d])
        if d <= poisoned_actual:
            mine.execute("insert into mine values (?)", [d])

    spec = {
        "domain": "moneyflow", "db": "tushare_raw", "table": "mine",
        "freshness_date_column": "trade_date", "date_param": None,
        "execution_policy_mode": "disabled", "execution_policy_reason": "tushare_sunset_freeze",
        "watermark_table": "mart_data_source_watermark",
        "completeness_ref": {
            "kind": "same_day_row_count", "ref_domain": "daily",
            "tolerance": 0, "verified_since": tds[0], "evidence": "test",
        },
    }
    got = cci.check_completeness_ref(mine, spec, tds, tds[-1], anchor_conn=smart)
    assert got["status"] == "fail_frozen_regression", got


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


def test_future_data_date_is_unverified_not_zero_lag() -> None:
    """数据日期晚于 today → None(不可判定), **不是 0(零延迟)**.

    2026-08-23: 原实现是 `max(0, bisect差)`, 把"不可能"钳成了"完美"。实测(真实日历,
    today=2026-08-23): trading_days_since('20340430') 与 ('28240531') 都返 **0** ——
    下游据 `measured_days > sla` 判告警, 于是一个未来日期进了 watermark, 该域的停更
    监控就永久静默, 而且 0 读起来还是"最新鲜"(project_status 按 last_data_date 升序
    取最旧 12 个域巡检, 未来值会排到末尾, 从人工核查里一并消失)。
    返回 None 后走调用侧既有的 axis_unverified 通路 → SLA_UNVERIFIED + alert。
    """
    days = _cal("20260807", "20260810", "20260811")
    assert _cal_trading_days_since("20260812", _dt.date(2026, 8, 11), days) is None
    assert _cal_trading_days_since("28240531", _dt.date(2026, 8, 11), days) is None
    # 正常路径不受影响 —— 钳位本来就只在 day > today 时才生效
    assert _cal_trading_days_since("20260807", _dt.date(2026, 8, 11), days) == 2
    assert _cal_trading_days_since("20260811", _dt.date(2026, 8, 11), days) == 0


def test_calendar_not_covering_today_is_unverified() -> None:
    """today 超出日历覆盖(跨年没续订) → None; 否则真停更会被报成"落后 0 天".

    这条比未来日期那条更要紧: 它**不需要任何脏数据**。当 today 落在日历末端之后,
    它与任何近端日期的 bisect 位置都是同一个末端, 差恒为 0。实测(日历止于
    2026-12-31, today=2027-03-01): 数据停在 20261231 已真停更两个月, 却报"落后 0 个
    交易日"。而且一旦发生是**全部域同时**失效 —— 交易日历续订是人工定期任务。
    """
    days = _cal("20261229", "20261230", "20261231")  # 日历只到年底
    # 跨年后日历未续订: 数据其实停在去年最后一个交易日
    assert _cal_trading_days_since("20261231", _dt.date(2027, 3, 1), days) is None
    assert _cal_trading_days_since("20261229", _dt.date(2027, 1, 5), days) is None
    # today 仍在日历覆盖内时照常工作
    assert _cal_trading_days_since("20261229", _dt.date(2026, 12, 31), days) == 2


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

