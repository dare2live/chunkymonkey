from __future__ import annotations

from argparse import Namespace
import json

import pytest

from services.data_sources import margin_ingest
from services.data_sources import margin_acceptance
from services.data_sources import sync_runner as sr
from services.data_sources.margin_schema import MarginAcceptanceError


def _registry() -> dict:
    return {
        "defaults": {
            "target_db": "tushare_raw",
            "fetch_timeout_seconds": 120,
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {
            "daily": {
                "source": "tushare",
                "api": "daily",
                "target_table": "raw_tushare_daily",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "partition_by": ["trade_date"],
                "available_after": "18:00",
                "data_start": "20200102",
            },
            "margin": {
                "source": "tushare",
                "api": "margin",
                "target_table": "raw_tushare_margin",
                "grain": ["trade_date", "exchange_id"],
                "batch_mode": "by_trade_date",
                "partition_by": ["trade_date"],
                "available_after": "t+1",
                "data_start": "20190102",
                "execution_policy": {
                    "mode": "disabled",
                    "reason": "scope_blocked",
                },
            },
        },
    }


def _args(*, drain: bool = False, all_due: bool = False) -> Namespace:
    return Namespace(
        domain=None if all_due else "margin",
        all_due=all_due,
        backfill=False,
        resume=False,
        start=None,
        end=None,
        drain=drain,
        max_dates=None,
    )


def _forbidden(name: str):
    def fail(*_args, **_kwargs):
        pytest.fail(f"disabled execution reached forbidden side effect: {name}")

    return fail


def test_live_margin_v3_bounded_catchup_stays_out_of_all_due():
    """守: margin(v3, SSE+SZSE)绝不进 --all-due 批量重放, 只走 on_demand 有界
    追赶。这是这条测试的名字承诺的不变量, 不是"execution_policy 字面等于
    某个具体 dict"。

    2026-09-07 tushare_sunset freeze 把 margin 从 enabled/bounded_calendar_catchup
    转 disabled/tushare_sunset_freeze, 原字面断言假红。disabled 比
    enabled+on_demand 更强地满足"不进 --all-due"这条属性——禁用的域根本不会被
    任何调度路径执行, 包括 --all-due——所以断言换成直接验证这个属性本身
    (`"margin" not in sr.automatic_domains(registry)`, 下方已有), 不再钉字面
    值(下次冻结原因换个词还会假红)。execution_policy 本身仍必须是良构策略,
    复用生产代码自己的校验(execution_policy_for_spec 校验不过会抛异常),
    不重复拍第二套字面判据。
    """
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "margin")

    assert spec["dataset_contract"]["contract_version"] == "3"
    policy = sr.execution_policy_for_spec(spec)  # raises if malformed
    assert policy.mode in ("enabled", "disabled")
    # Catchup is on_demand — never --all-due / daily_update drain deadlock.
    assert spec.get("sync_policy") == "on_demand"
    assert "margin" not in sr.automatic_domains(registry)
    assert spec["population_scope"] == {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE"],
        "population_label": "sse_szse_venue_reported_margin",
        "method": "tushare_margin_exchange_summary_sse_szse",
        "unit": "provider_declared_fields",
    }
    assert spec["split_by"]["values"] == ["SSE", "SZSE"]
    assert spec["batch_completeness"]["required_groups_since"] in ({}, None) or (
        spec["batch_completeness"]["required_groups_since"] == {}
    )
    contract = margin_ingest.contract_for_spec(spec)
    assert contract is not None
    assert contract.contract_version == "3"
    assert contract.coverage_start == "20260717"
    assert not spec.get("product_blocking")


def test_live_trade_calendar_authorized_manual_generation_uses_formal_path(
    monkeypatch,
):
    registry = sr.load_registry()
    spec = sr.domain_spec(registry, "trade_cal")

    assert spec["execution_policy"] == {
        "mode": "enabled",
        "reason": "authorized_manual_generation",
    }
    assert spec["calendar_generation"] == {
        "contract_version": "1",
        "coverage_start": "19901219",
        "required_through_rule": "observed_year_end",
        "timezone": "Asia/Shanghai",
        "availability": {
            "axis": "provider_response",
            "rule": "response_completed",
            "at": "response_completed_at",
        },
        "canonicalization_version": "1",
    }
    assert spec["population_scope"] == {
        "kind": "external_aggregate",
        "venue_field": "exchange",
        "venue_ids": ["SSE"],
        "population_label": "sse_trading_calendar",
        # 2026-08-31 授权换源: baostock -> calendar_rule (前一次 2026-08-30 tushare ->
        # baostock 已被此次取代)。baostock 被自身风控在并发探测中拉黑, 且实测三个备选
        # 源都结构性给不了未来交易日; calendar_rule 改按规则推导 (周一~周五 − 法定节
        # 假日, backend/config/market_holidays.yaml), 不再向任何供应商取数。详见
        # formal_boundaries.py _FORMAL_BOUNDARIES["trade_cal"] 与
        # services/data_sources/sources/calendar_rule.py 模块 docstring。
        "method": "calendar_rule_weekday_minus_holidays",
        "unit": "calendar_day_status",
    }

    monkeypatch.setattr(
        sr,
        "_publish_trade_cal_accepted_generation",
        lambda _spec: {
            "domain": "trade_cal",
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "publication": "accepted_calendar_generation",
        },
    )
    for name in (
        "eligible_end_date",
        "trading_days",
        "_write_batch",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    result = sr.run_domain("trade_cal", registry=registry)
    assert result["publication"] == "accepted_calendar_generation"
    assert result["failed_batches"] == 0


def test_v2_contract_still_blocks_live_db_write_gate():
    class _Cursor:
        def fetchall(self):
            return [(0, "tushare_raw", str(margin_acceptance._FROZEN_LIVE_DB))]

    class _LiveConnection:
        def execute(self, sql, *_args, **_kwargs):
            assert sql == "PRAGMA database_list"
            return _Cursor()

    from types import SimpleNamespace

    with pytest.raises(MarginAcceptanceError, match="v2 live writes are frozen"):
        margin_acceptance._block_frozen_live_write(
            _LiveConnection(),
            contract=SimpleNamespace(contract_version="2"),
        )


def test_v3_contract_lifts_live_db_write_freeze_gate():
    class _Cursor:
        def fetchall(self):
            pytest.fail("v3 must not probe live DB identity for freeze")

    class _LiveConnection:
        def execute(self, sql, *_args, **_kwargs):
            return _Cursor()

    from types import SimpleNamespace

    # Gate returns without raising; writers still validate batch/contract elsewhere.
    margin_acceptance._block_frozen_live_write(
        _LiveConnection(),
        contract=SimpleNamespace(contract_version="3"),
    )


@pytest.mark.parametrize("entrypoint", ["run", "drain"])
def test_programmatic_margin_entrypoints_block_before_calendar_provider_or_db(
    monkeypatch, entrypoint
):
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(sr.ExecutionPolicyError, match="margin.*scope_blocked"):
        if entrypoint == "run":
            sr.run_domain("margin", registry=_registry())
        else:
            sr.drain_domain("margin", registry=_registry())


@pytest.mark.parametrize(
    "args",
    [
        _args(),
        _args(drain=True),
    ],
    ids=["explicit-domain", "drain"],
)
def test_cli_entrypoints_block_before_calendar_lock_auth_provider_or_db(
    monkeypatch, capsys, args
):
    """**显式点名**一个 disabled 域时, 必须在日历/锁/授权/provider/DB 之前 typed 拒绝。

    2026-09-12 去掉了原本的第三个参数化 id ``all-due``: all-due 不再是"点名",
    disabled 域在选域处就被排除, 所以它走不到 execution_blocked —— 那条语义已由
    test_all_due_skips_disabled_domains_instead_of_blocking 单独钉住(见下)。
    留在这里的两个 id 才是本用例真正守的东西: 点名一个 disabled 域 = 拒绝, 且不得触碰任何副作用。
    """
    import services.writer_lock as writer_lock_module

    monkeypatch.setattr(sr, "_parse_cli_args", lambda: args)
    monkeypatch.setattr(sr, "load_registry", _registry)
    monkeypatch.setattr(sr, "_calendar_preflight", _forbidden("calendar"))
    monkeypatch.setattr(
        sr,
        "_preflight_explicit_operation_windows",
        _forbidden("operation-window calendar"),
    )
    monkeypatch.setattr(sr, "_authorization_preflight", _forbidden("authorization"))
    monkeypatch.setattr(sr, "_adapter", _forbidden("adapter"))
    monkeypatch.setattr(sr, "_target_conn", _forbidden("target db"))
    monkeypatch.setattr(writer_lock_module, "writer_lock", _forbidden("writer lock"))

    assert sr.main() == 6
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "execution_blocked",
        "domain": "margin",
        "mode": "disabled",
        "reason": "scope_blocked",
        "error": "domain=margin execution disabled: scope_blocked",
    }


def test_all_due_skips_disabled_domains_instead_of_blocking(monkeypatch, capsys):
    """``--all-due`` 遇到 disabled 域要**跳过它**, 不是拒绝整批。

    这是上面那个参数化里被删掉的 ``all-due`` id 的替代, 也是 2026-09-07→09-12 五天全链死锁
    (daily_update 每次 exit 4、四阶段一步未启动) 的直接回归钉子。
    fixture 里 margin = {mode: disabled, reason: scope_blocked} 且未声明 sync_policy ——
    改判前它会被选进 all-due 再被 preflight 判死; 改判后集合只剩 daily, 整批照常往下走。

    断言用 ``_selected_domains`` + 两个 preflight 函数, 而不是跑 ``main()``:
    main() 会继续往日历/锁/授权走, 那些不是本用例要守的东西 (它们各有自己的用例)。
    """
    registry = _registry()
    args = _args(all_due=True)

    assert sr._selected_domains(args, registry) == ["daily"]
    # 不抛 = 整批放行; 抛了就说明 disabled 域又漏进了 all-due
    sr.preflight_execution_policies(registry, sr._selected_domains(args, registry))
    sr.preflight_formal_population_scopes(
        registry, sr._selected_domains(args, registry)
    )

    # 反向: 显式点名那个 disabled 域仍然必须 typed 拒绝 (两条语义不可互相替代)
    with pytest.raises(sr.ExecutionPolicyError, match="margin.*scope_blocked"):
        sr.preflight_execution_policies(registry, ["margin"])


def test_main_unlocked_cannot_bypass_execution_policy(monkeypatch):
    monkeypatch.setattr(sr, "_calendar_preflight", _forbidden("calendar"))
    monkeypatch.setattr(sr, "run_domain", _forbidden("run_domain"))

    with pytest.raises(sr.ExecutionPolicyError, match="margin.*scope_blocked"):
        sr._main_unlocked(_args(), _registry(), ["margin"])


def test_automatic_domain_inventory_matches_all_due_and_fails_closed():
    """all-due 集合按**两个**轴排除: on_demand 与 disabled。

    2026-09-12 改判: 本用例原先断言 ``["daily", "margin"]`` —— 而 fixture 里的 margin 正是
    ``{mode: disabled, reason: scope_blocked}``。也就是它当时钉住的是**缺陷本身**:
    一个 disabled 域被选进 all-due, 随后必然被 preflight_execution_policies 判死。
    那个组合让 daily_update 从 2026-09-07 起连续 5 天 exit 4、四阶段一步未启动
    (16 个域按 check_tushare_sunset 检查 8 的官方建议标了 disabled)。
    现在 disabled 在选域处就被排除, 所以 margin 不再出现在集合里。
    """
    registry = _registry()
    registry["domains"]["manual_repair"] = {
        "sync_policy": "on_demand",
        "execution_policy": {"mode": "enabled", "reason": "manual_only"},
    }
    # margin 是 disabled(scope_blocked) 且未声明 sync_policy —— 两个轴里第二个把它排除
    assert sr.automatic_domains(registry) == ["daily"]

    # 第一个轴单独也成立: 把 margin 改回 enabled 再贴 on_demand, 仍然不进集合
    registry["domains"]["margin"]["execution_policy"] = {
        "mode": "enabled",
        "reason": "active",
    }
    assert sr.automatic_domains(registry) == ["daily", "margin"]
    registry["domains"]["margin"]["sync_policy"] = "on_demand"
    assert sr.automatic_domains(registry) == ["daily"]

    # 策略形状非法时 fail-closed: 不许悄悄当成 enabled 选进来。
    # match 用的是 **str(exc)** 里真实出现的文本 —— 不是 "invalid_execution_policy":
    # 那个串是异常的 .reason **属性**值, 只有 pipeline.preflight 会拿它拼
    # f"sync_execution_blocked:{exc.domain}:{exc.reason}", 它并不出现在 str(exc) 里。
    # (先前我按 .reason 写 match, 于是"抛对了却匹配不上"——判据与被判对象错位。)
    registry["domains"]["margin"].pop("sync_policy")
    registry["domains"]["margin"]["execution_policy"] = {"mode": "paused", "reason": "x"}
    with pytest.raises(
        sr.ExecutionPolicyError, match="unsupported execution policy mode"
    ) as caught:
        sr.automatic_domains(registry)
    # 同时钉住 preflight 拼消息用的那两个属性, 否则 pipeline 侧三条 fail-closed 用例
    # 依赖的 "sync_execution_blocked:<domain>:<reason>" 形状没有任何测试覆盖
    assert caught.value.domain == "margin"

    registry["domains"]["margin"]["execution_policy"] = {
        "mode": "enabled",
        "reason": "active",
    }
    registry["domains"]["broken"] = None
    with pytest.raises(ValueError, match="domain entry.*broken.*mapping"):
        sr.automatic_domains(registry)


def test_live_registry_all_due_set_passes_pipeline_preflight():
    """live registry 的 all-due 集合必须能过 pipeline preflight —— 纯 YAML, 零 DB。

    这条是 2026-09-07→09-12 那次 5 天全链死锁的回归钉子, 也是「配置允许全链启动」这个
    验收判据本身。为什么非要用**真** registry: 所有既有的 preflight 测试都 monkeypatch 掉
    ``load_registry``, 于是「按台账门的建议给某个域标 disabled」这个动作从来没有被任何测试
    覆盖过 —— 同形态因此在 48 天内复发两次 (a84e0867 2026-07-21 margin / 01f8f41a 2026-09-07
    十六个 freeze 域)。07-21 那次的证据是手工跑一次 ``PASS domains=42``, 没钉成测试。

    断言的是**不变量**不是状态: 不检查集合里有几个域、也不检查具体哪些域
    (那会随域迁移/换源漂移, 见 feedback-test-must-carry-its-own-fixture 里
    「别把运行时测量值钉成常量」), 只断言「选出来的每一个域都是 enabled, 且两个 preflight 不抛」。
    """
    registry = sr.load_registry()
    domains = sr.automatic_domains(registry)
    assert domains, "all-due 集合不该为空 —— 空集合会让这条断言按构造永远通过"

    sr.preflight_execution_policies(registry, domains)
    sr.preflight_formal_population_scopes(registry, domains)

    for domain in domains:
        policy = sr.execution_policy_for_spec(sr.domain_spec(registry, domain))
        assert policy.mode == "enabled", (
            f"{domain} 进了 all-due 却是 {policy.mode}({policy.reason}) —— "
            "preflight 会 hard_fail 且四阶段一步不启动"
        )


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        pytest.param("missing", "missing execution_policy", id="missing-key"),
        (None, "execution_policy must be a mapping"),
        ({"mode": "disabled"}, "missing execution_policy keys: reason"),
        (
            {"mode": "disabled", "reason": "scope_blocked", "extra": True},
            "unknown execution_policy keys: extra",
        ),
        (
            {"mode": "paused", "reason": "scope_blocked"},
            "unsupported execution policy mode='paused'",
        ),
        (
            {"mode": "disabled", "reason": "Scope Blocked"},
            "execution policy reason contains malformed value",
        ),
    ],
)
def test_execution_policy_is_strict_and_typed(raw, match):
    spec = {"domain": "example"}
    if raw != "missing":
        spec["execution_policy"] = raw
    with pytest.raises(sr.ExecutionPolicyError, match=match):
        sr.execution_policy_for_spec(spec)


def test_explicit_empty_registry_does_not_fall_back_to_live_registry(monkeypatch):
    monkeypatch.setattr(sr, "load_registry", _forbidden("live registry fallback"))

    with pytest.raises(KeyError):
        sr.run_domain("margin", registry={})


def _enabled_formal_margin_registry(*, population_scope=None) -> dict:
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["execution_policy"] = {"mode": "enabled", "reason": "active"}
    if population_scope is not None:
        spec["population_scope"] = population_scope
    else:
        spec.pop("population_scope", None)
    return {"defaults": {}, "domains": {"margin": spec}}


def test_enabled_formal_dataset_missing_population_scope_blocks_before_runtime(
    monkeypatch,
):
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="population scope invalid.*missing population_scope",
    ) as caught:
        sr.run_domain("margin", registry=_enabled_formal_margin_registry())
    assert caught.value.reason == "invalid_population_scope"


def test_enabled_margin_cannot_delete_contract_and_fall_into_legacy_runner(monkeypatch):
    registry = _enabled_formal_margin_registry(population_scope={})
    registry["domains"]["margin"].pop("dataset_contract")
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="dataset contract invalid.*missing or mismatched dataset_contract",
    ) as caught:
        sr.run_domain("margin", registry=registry)
    assert caught.value.reason == "invalid_dataset_contract"


def test_margin_v3_catchup_requires_explicit_start_end(monkeypatch):
    for name in ("_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))
    registry = _enabled_formal_margin_registry(
        population_scope={
            "kind": "external_aggregate",
            "venue_field": "exchange_id",
            "venue_ids": ["SSE", "SZSE"],
            "population_label": "sse_szse_venue_reported_margin",
            "method": "tushare_margin_exchange_summary_sse_szse",
            "unit": "provider_declared_fields",
        }
    )
    margin = registry["domains"]["margin"]
    margin["dataset_contract"] = {
        **margin["dataset_contract"],
        "contract_version": "3",
        "coverage_start": "20260717",
    }
    margin["split_by"] = {"param": "exchange_id", "values": ["SSE", "SZSE"]}
    margin["batch_completeness"] = {
        **margin["batch_completeness"],
        "required_groups": ["SSE", "SZSE"],
        "required_groups_since": {},
    }
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )

    with pytest.raises(sr.SyncWindowError, match="requires explicit --start/--end"):
        sr.run_domain("margin", registry=registry)


def test_margin_v3_refuses_backfill_mass_replay():
    """守: margin 的日历追赶窗口校验器拒绝 --backfill/--resume(不许整段历史
    重放)——与本文件旁边 test_margin_catchup.py 里
    test_catchup_window_refuses_pre_coverage_start /
    test_catchup_window_refuses_beyond_eligible_end 守的是同一个函数
    `_require_authorized_margin_catchup_window`, 只是边界条件不同。

    原来经 sr.run_domain("margin", ..., backfill=True) 走完整入口测这条, 但
    run_domain 顶部的 `_require_execution_enabled(spec)` 现在(margin 全局
    disabled, tushare_sunset_freeze)会抢先抛 ExecutionPolicyError, 走不到
    margin 分支里那句 `_require_authorized_margin_catchup_window`。这不代表
    "拒绝 --backfill"失效了——disabled 时任何窗口(含 --backfill)都在更早、更
    强的关卡被拒。但这条测试原本要精确验证的是"窗口校验器自身"这一层, 所以
    改成跟 sibling 用例一样直接调用该函数, 不再经过 run_domain 顶层的
    execution_policy 网关, 也不依赖 live registry 当前是 enabled 还是 disabled
    (该函数本身不读 execution_policy, 纯窗口校验, 与冻结状态正交)。

    "run_domain 在 enabled 时是否真的把 backfill 参数传到这个校验器"这条wiring
    事实, 由旁边 test_margin_v3_catchup_requires_explicit_start_end (同一
    margin 分支, 同一 `_enabled_formal_margin_registry` helper, 同一校验函数,
    只是断言另一个必填参数)已经证明过, 不会因为这次改动丢失覆盖。
    "disabled 时 backfill 连同其他任何操作一起在 side effect 之前被拒"由
    test_programmatic_margin_entrypoints_block_before_calendar_provider_or_db
    单独覆盖, 也不受影响。
    """
    with pytest.raises(sr.SyncWindowError, match="refuses --backfill"):
        sr._require_authorized_margin_catchup_window(
            backfill=True,
            resume=False,
            start="20260717",
            end="20260722",
            max_dates=None,
            eligible_end="20260722",
            coverage_start="20260717",
        )


def test_enabled_margin_rejects_bse_in_accepted_population_scope(monkeypatch):
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))
    scope = {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE", "BSE"],
        "population_label": "venue_reported_margin_population",
        "method": "tushare_margin_exchange_summary",
        "unit": "provider_declared_fields",
    }

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="venue_ids must be exactly",
    ) as caught:
        sr.run_domain(
            "margin",
            registry=_enabled_formal_margin_registry(population_scope=scope),
        )

    assert caught.value.reason == "invalid_population_scope"


def test_enabled_margin_rejects_bse_transport_even_with_corrected_scope(monkeypatch):
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))
    # Injected registry keeping v2 BSE transport must fail closed when enabled.
    registry = _enabled_formal_margin_registry(
        population_scope={
            "kind": "external_aggregate",
            "venue_field": "exchange_id",
            "venue_ids": ["SSE", "SZSE"],
            "population_label": "sse_szse_venue_reported_margin",
            "method": "tushare_margin_exchange_summary_sse_szse",
            "unit": "provider_declared_fields",
        }
    )
    margin = registry["domains"]["margin"]
    margin["dataset_contract"] = {
        **margin["dataset_contract"],
        "contract_version": "3",
        "coverage_start": "20260717",
    }
    # Force wrong transport even on v3 metadata.
    margin["split_by"] = {"param": "exchange_id", "values": ["SSE", "SZSE", "BSE"]}
    margin["batch_completeness"] = {
        **margin["batch_completeness"],
        "required_groups": ["SSE", "SZSE"],
        "required_groups_since": {"BSE": "20230213"},
    }
    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="forbids BSE in batch_completeness",
    ) as caught:
        sr.run_domain(
            "margin",
            registry=registry,
            start="20260717",
            end="20260717",
        )

    assert caught.value.reason == "invalid_population_scope"


def test_formal_domain_without_consumer_still_blocks_as_not_propagated(monkeypatch):
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["domain"] = "orphan_formal"
    spec["execution_policy"] = {"mode": "enabled", "reason": "active"}
    spec["dataset_contract"] = {
        **spec["dataset_contract"],
        "dataset_id": "tier0.market_data.orphan_formal_daily",
        "schema_id": "tier0.market_data.orphan_formal_daily.canonical",
        "canonical_table": "canonical_orphan_formal_daily",
    }
    registry = {"defaults": {}, "domains": {"orphan_formal": spec}}
    for name in ("eligible_end_date", "_adapter", "_target_conn", "_smartmoney_conn"):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="no formal execution consumer is registered",
    ) as caught:
        sr.run_domain("orphan_formal", registry=registry)

    assert caught.value.reason == "execution_contract_not_propagated"


def test_future_non_margin_formal_dataset_cannot_bypass_population_gate(monkeypatch):
    spec = sr.domain_spec(sr.load_registry(), "margin")
    spec["execution_policy"] = {"mode": "enabled", "reason": "active"}
    spec["dataset_contract"] = {
        **spec["dataset_contract"],
        "dataset_id": "tier0.market_data.future_formal_daily",
        "schema_id": "tier0.market_data.future_formal_daily.canonical",
        "canonical_table": "canonical_future_formal_daily",
    }
    spec.pop("population_scope", None)
    registry = {"defaults": {}, "domains": {"future_formal": spec}}
    for name in (
        "eligible_end_date",
        "trading_days",
        "apply_fetch_socket_timeout",
        "_adapter",
        "_target_conn",
        "_smartmoney_conn",
    ):
        monkeypatch.setattr(sr, name, _forbidden(name))

    with pytest.raises(
        sr.PopulationScopeExecutionError,
        match="population scope invalid.*missing population_scope",
    ) as caught:
        sr.run_domain("future_formal", registry=registry)

    assert caught.value.domain == "future_formal"
    assert caught.value.reason == "invalid_population_scope"


def test_enabled_policy_resolves_without_side_effects():
    policy = sr.execution_policy_for_spec(
        {
            "domain": "daily",
            "execution_policy": {"mode": "enabled", "reason": "active"},
        }
    )

    assert policy == sr.DomainExecutionPolicy(mode="enabled", reason="active")
