"""① 获取 (Acquire) — 纯采集: 只下载/同步外部 vendor 数据进 raw/L0, 不计算。

ACQUIRE 阶段步骤:
  LHB / institution_survey / holders_aif10 / aif10 capabilities / QFII / org_holding / sync_runner drain。
  (HS300 benchmark 2026-06-28 退役 akshare 备援步: 主源=tushare raw_tushare_index_daily 000300 走
   sync_runner registry 同步, 旧 akshare→price_kline 备援脚本删; profit_forecast/external_attention/
   xdxr 热备早退役: 通达信全删 + 复权走 tushare adj_factor)
skip_sync=1 跳整个阶段; dry=1 只跑只读不写。
"""
from __future__ import annotations

import json

from .context import PipelineContext


class Tier0AcquireError(RuntimeError):
    """A blocking Tier0 dataset was not proven ready for downstream stages."""


def run_acquire(ctx: PipelineContext) -> None:
    if ctx.skip_sync:
        ctx.log("=== ① 获取 ACQUIRE: SKIP (--skip-sync) ===")
        # Margin hard-gate only while the product is enabled; frozen/disabled skips.
        if not ctx.dry and _margin_hard_gate_required():
            _assert_margin_shadow_parity(ctx)
        elif not ctx.dry:
            ctx.log("margin hard-gate SKIP (execution_policy disabled / not in all-due)")
        return
    # 独立 stage 入口也必须走与全链相同的授权硬门；全链已探针时复用 ctx 缓存。
    from .preflight import ensure_pipeline_sync_ready, ensure_tushare_authorized
    ensure_pipeline_sync_ready(ctx)
    ensure_tushare_authorized(ctx)
    ctx.log("=== ① 获取 ACQUIRE (纯采集 →L0, 不计算) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过实际 sync (获取阶段全是写操作)")
        return

    # HS300 benchmark K线: 主源 = tushare raw_tushare_index_daily 000300 (sync_runner registry 同步,
    #   下方 drain 覆盖)。2026-06-28 删旧 akshare→price_kline 备援步 (akshare 源退役)。
    # xdxr 除权 sync 已移除 (2026-06-28 重建: tdx 热备退役, 复权走 tushare adj_factor)

    # Step 2d LHB sync 已退役 2026-06-29 (批2b: LHB 切 tushare top_list/top_inst, 由 sync_runner 域 drain; lhb_client+raw_lhb_daily 退役物删)

    # Step 2i institution_survey aif10+akshare sync 已退役 2026-06-28 (批2 数据源切 tushare 唯一:
    #   调研走 tushare stk_surv→raw_tushare_stk_surv 由 sync_runner 域 drain, institution_survey_client 退役)

    # Step 2i2: 十大流通股东 aif10 增量 (主源, 替退役中的 tdxhub; 按披露日只拉新披露股)
    ctx.step(lambda: _sync_holders_aif10(ctx), degraded_msg="holders aif10 sync 失败")

    # Step 2i3 (aif10 capability sync) 已删 2026-07-07: peer_valuation(07-07先删)+valuation_quantile
    #   (本批删)是仅剩的两个 capability, 唯一消费方 v3_picture 已随 2026-06-28 重建退役且早在
    #   v3_picture 活着时就因无 date 列(latest-snapshot leakage)从未被特征管线接入, PIT-safe
    #   替代(pe_ttm_z_1y/pb_z_1y rolling z-score)已存在; _sync_aif10_capabilities() 整函数随之删除
    #   (0 消费方的 capability 清空后, 保留一个空 for 循环的 no-op 函数违反能删必删)。

    # Step 2j: QFII 季度持股 (外资维度; 2026-06-24 迁自旧 updater)
    ctx.step(_sync_qfii, degraded_msg="QFII sync 失败")

    # Step 2j2: 机构持仓明细 aif10 (非公募机构分桶; 2026-06-24 aif10 例外扩展, 替退役 tdx F10 控股股东表)
    ctx.step(_sync_org_holding, degraded_msg="org_holding aif10 sync 失败")

    # Step 2k (external_attention 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 东财人气/关注度退役, 用户决cut, 无tushare等价=永久丢):
    #   消费侧 scoring 外部关注 boost/池升级/crowding penalty 优雅降级 (external_attention_score→None)。

    # Step 2l (profit_forecast EPS 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 退役, 用户决cut):
    #   raw_profit_forecast_snapshot_daily 0 live 读者 (snapshot 设计防leakage但无消费); 档B 若需景气度走 tushare forecast/report_rc。

    # Step 2.94: formal on_demand daily/ST — modular land_then_accept for latest
    #   eligible day only (never rides --all-due; never mass history fill).
    _sync_formal_on_demand_security_days(ctx)

    # Step 2.95: sync_registry 域日历 gap 重放 = 增量 + 修洞统一机制 (终败/漏跑/历史空洞)
    _sync_registry_drain(ctx)

    # Step 2.96: 交易日历 dim 传导 (R1 根因3, 2026-07-03): raw_tushare_trade_cal (2.95 已刷)
    #   → reference.dim_trading_calendar 增量 MERGE。dim 曾无生产 writer (唯一写方=已封存的
    #   一次性迁移脚本), horizon 倒计时中 — 本步是 dim 的唯一日常刷新契约。
    ctx.step(_build_trading_calendar,
             degraded_msg="交易日历 dim 传导失败 — dim_trading_calendar 停止向未来延伸 (horizon 门将 FAIL)")

    # Step 2.97 (2026-07-06 全面数据审计根因根治): raw_tushare_stock_basic (2.95 已刷)
    #   → reference.dim_active_a_stock 全量重写。写函数早就存在且正确, 但此前从未被任何
    #   daily_update 步骤调用, 25 个消费方(universe 身份真相源)读的这张表只能靠人工手动
    #   跑脚本刷新, 实测发现已静默 stale 8 天。本步是 dim_active_a_stock 的唯一日常刷新契约,
    #   与上一步 dim_trading_calendar 同一模式补齐。
    ctx.step(_refresh_active_a_stock_master,
             degraded_msg="dim_active_a_stock 刷新失败 — universe 身份真相源可能继续 stale")  # rule-compliance: ok evidence=写方本身非universe消费


# ── 步骤实现 (in-process, 直调 service) ──────────────────────────


# _sync_lhb 已删 2026-06-29 (批2b: lhb_client 退役, LHB 切 tushare top_list/top_inst)


# _sync_institution_survey 已删 2026-06-28 (批2: institution_survey_client[aif10+akshare] 退役, 切 tushare stk_surv)


def _sync_holders_aif10(ctx) -> None:
    """十大流通股东 aif10 增量 (主源). 水位驱动: 扫存量 MAX(披露日) 之后有新披露的股, 无 wall-clock."""
    from services.db import get_conn
    from services.holders_aif10 import sync_holders_aif10_incremental
    conn = get_conn()
    try:
        result = sync_holders_aif10_incremental(conn)
        print(f"holders_aif10: watermark={result.get('watermark')} "
              f"affected={result.get('affected_stocks', 0)} rows={result.get('rows_written', 0)} "
              f"exits={result.get('exit_rows', 0)} errors={result.get('errors', [])[:3]}")
    finally:
        conn.close()


def _sync_qfii() -> None:
    """QFII 季度持股增量 (外资维度). 水位=最近已披露季度末, 已有则跳过."""
    import asyncio
    from services.duck_adapter import connect as duck_connect
    from services.qfii_client import sync_qfii_incremental
    from .context import db_path
    conn = duck_connect(db_path("smartmoney"))
    try:
        import json
        print(json.dumps(asyncio.run(sync_qfii_incremental(conn)), ensure_ascii=False, default=str))
    finally:
        conn.close()


def _sync_org_holding() -> None:
    """机构持仓明细 aif10 — incremental-only on manual update (owner hard lock).

    Binding (2026-07-21): check latest plannable vs local; fetch **only** if
    that period is missing. NEVER full-period ~830k mass re-pull / unbounded
    page crawl for "refresh". NEVER call ``backfill`` from this path.
    Older historical gaps: log-not-fill (mass backfill is a separate explicit knife).
    """
    import asyncio
    import json
    from services.duck_adapter import connect as duck_connect
    from services.org_holding_aif10 import (
        org_holding_period_gap_report,
        sync_org_holding_incremental,
    )
    from .context import db_path

    conn = duck_connect(db_path("smartmoney"))
    try:
        gap = org_holding_period_gap_report(conn)
        print(
            "org_holding_gap_check: "
            + json.dumps(gap, ensure_ascii=False, default=str)
        )
        print(
            json.dumps(
                asyncio.run(sync_org_holding_incremental(conn)),
                ensure_ascii=False,
                default=str,
            )
        )
    finally:
        conn.close()


# _sync_external_attention 已退役 2026-06-27 (通达信全删 M4: akshare external_attention.py 物删, 用户决cut)


def _build_trading_calendar() -> None:
    """raw trade_cal → reference.dim_trading_calendar 增量 (R1 根因3 生产刷新契约)."""
    from services.calendar_builder import build_latest
    print(f"calendar_builder: {build_latest()}")


def _refresh_active_a_stock_master() -> None:
    """raw_tushare_stock_basic → reference.dim_active_a_stock 全量重写 (rule-compliance: ok evidence=写方本身非universe消费)
    (2026-07-06 全面数据审计根因根治): 写函数 refresh_active_a_stock_master 一直存在且正确,
    但从未被任何 daily_update 步骤调用过——25 个消费方(universe 身份真相源)读的这张表此前
    只能靠人工手动跑脚本刷新, 实测发现已静默 stale 8 天。stock_basic 域已由上一步
    _sync_registry_drain 的 --all-due 覆盖同步 (full_refresh 批模式), 本步紧随其后重建派生表,
    与 _build_trading_calendar (raw trade_cal → dim_trading_calendar) 同一模式。"""
    from services.security_master import refresh_active_a_stock_master
    n = refresh_active_a_stock_master(None)
    print(f"security_master: refresh_active_a_stock_master rows={n}")


FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS: tuple[str, ...] = ("daily", "stock_st")


def _formal_security_day_dataset_id(domain: str) -> str:
    if domain == "daily":
        from services.data_sources.nominal_ohlcv_schema import DATASET_ID

        return DATASET_ID
    if domain == "stock_st":
        from services.data_sources.stock_st_schema import DATASET_ID

        return DATASET_ID
    raise Tier0AcquireError(f"unsupported formal security-day domain={domain!r}")


def _accepted_partition_exists(conn, dataset_id: str, partition_value: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 AS ok
        FROM accepted_partition
        WHERE dataset_id = ? AND partition_value = ?
        LIMIT 1
        """,
        [dataset_id, partition_value],
    ).fetchone()
    return row is not None


def _sync_formal_on_demand_security_days(ctx: PipelineContext) -> None:
    """Pull latest eligible formal daily/ST via modular land_then_accept.

    on_demand domains never enter --all-due. This is the S3 orchestrator bridge:
    one trade_date = eligible_end when missing from accepted_partition.
    Does **not** fill historical holes (log-not-fill / explicit backfill knife).
    """

    from services.data_sources import sync_runner
    from services.duck_adapter import connect

    registry = sync_runner.load_registry()
    planned: list[tuple[str, str, str, str]] = []  # domain, eligible_end, reason, dataset_id
    conn = connect(ctx.db("tushare_raw"), read_only=True)
    try:
        for domain in FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS:
            spec = sync_runner.domain_spec(registry, domain)
            if spec.get("sync_policy") != "on_demand":
                raise Tier0AcquireError(
                    f"domain={domain} orchestrator catchup requires sync_policy=on_demand"
                )
            policy = sync_runner.execution_policy_for_spec(spec)
            if policy.mode != "enabled":
                ctx.log(
                    f"formal {domain}: SKIP catchup "
                    f"(execution_policy {policy.mode}/{policy.reason})"
                )
                continue
            eligibility = sync_runner.eligible_end_date(spec, trigger_mode="manual")
            eligible_end = eligibility.eligible_end
            if eligible_end is None:
                ctx.log(
                    f"formal {domain}: SKIP catchup "
                    f"(no eligible_end; reason={eligibility.reason})"
                )
                continue
            dataset_id = _formal_security_day_dataset_id(domain)
            if _accepted_partition_exists(conn, dataset_id, eligible_end):
                print(
                    json.dumps(
                        {
                            "domain": domain,
                            "action": "skip",
                            "reason": "latest_eligible_already_accepted",
                            "eligible_end": eligible_end,
                            "eligibility_reason": eligibility.reason,
                            "dataset_id": dataset_id,
                        },
                        ensure_ascii=False,
                    )
                )
                continue
            planned.append(
                (domain, eligible_end, str(eligibility.reason), dataset_id)
            )
    finally:
        conn.close()

    for domain, eligible_end, eligibility_reason, dataset_id in planned:
        print(
            json.dumps(
                {
                    "domain": domain,
                    "action": "land_then_accept",
                    "eligible_end": eligible_end,
                    "eligibility_reason": eligibility_reason,
                    "dataset_id": dataset_id,
                    "window": "single_day_incremental",
                },
                ensure_ascii=False,
            )
        )
        result = sync_runner.run_domain(
            domain,
            start=eligible_end,
            end=eligible_end,
            registry=registry,
            trigger_mode="manual",
        )
        failed = int(result.get("failed_batches") or 0)
        status = str(result.get("status") or "")
        # Same-day vendor vacuum (pending_publish): soft-skip so registry
        # --all-due drain (ths_hot etc.) is not kidnapped by today's empty
        # formal daily/ST. Non-pending failures stay hard Tier0 blocks.
        if result.get("pending_publish"):
            print(
                json.dumps(
                    {
                        "domain": domain,
                        "action": "pending_publish",
                        "eligible_end": eligible_end,
                        "eligibility_reason": eligibility_reason,
                        "dataset_id": dataset_id,
                        "pending_publish_reason": result.get(
                            "pending_publish_reason",
                            "same_day_vendor_vacuum",
                        ),
                    },
                    ensure_ascii=False,
                )
            )
            continue
        if status != "ok" or failed:
            raise Tier0AcquireError(
                f"formal {domain} land_then_accept failed for {eligible_end}: "
                f"status={status!r} failed_batches={failed} "
                f"error={result.get('error')!r}"
            )
        print(json.dumps(result, ensure_ascii=False, default=str))


def _margin_hard_gate_required(registry: dict | None = None) -> bool:
    """True only when margin is enabled for live acquire gating.

    Frozen ``mode=disabled`` (scope_blocked) must not deadlock daily_update.
    Explicit margin sync remains blocked by sync_runner execution policy.
    """

    from services.data_sources import sync_runner

    reg = registry if registry is not None else sync_runner.load_registry()
    spec = sync_runner.domain_spec(reg, "margin")
    return sync_runner.execution_policy_for_spec(spec).mode == "enabled"


def _require_margin_drain_closed(results: list[dict]) -> None:
    """Fail closed on the single margin drain result when the product is enabled."""

    margin_results = [item for item in results if item.get("domain") == "margin"]
    if len(margin_results) != 1:
        raise Tier0AcquireError(
            f"formal margin result cardinality must be one, got {len(margin_results)}"
        )
    margin = margin_results[0]
    if (
        margin.get("status") not in {"clean", "drained"}
        or margin.get("still_failed")
        or margin.get("truncated")
        or margin.get("today_catchup_failed")
    ):
        raise Tier0AcquireError(
            "formal margin did not close accepted/reconcile gates: "
            f"status={margin.get('status')!r} "
            f"still_failed={margin.get('still_failed')!r} "
            f"truncated={margin.get('truncated')!r}"
        )


def _sync_registry_drain(ctx: PipelineContext) -> None:
    """sync_runner --all-due --drain (module 调用, subprocess 隔离)。"""
    import subprocess, sys as _sys
    from .context import REPO
    cmd = [_sys.executable, "-m", "services.data_sources.sync_runner",
           "--all-due", "--drain", "--max-dates", "30"]
    ctx.log(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds(),
    )
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or "")); ctx._log_fh.flush()
    if proc.returncode == 3:
        from services.data_sources.sources.tushare import (
            AUTH_FAILURE_REASONS,
            TuShareAuthorizationError,
        )

        output = (proc.stdout or "") + (proc.stderr or "")
        reason = next((item for item in AUTH_FAILURE_REASONS if item in output), "auth_denied")
        raise TuShareAuthorizationError(reason)
    try:
        results = json.loads(proc.stdout or "")
    except (TypeError, ValueError) as exc:
        raise Tier0AcquireError(
            "sync_registry did not return parseable per-domain evidence"
            + (
                "; formal margin readiness is unknown"
                if _margin_hard_gate_required()
                else ""
            )
        ) from exc
    if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
        raise Tier0AcquireError("sync_registry per-domain evidence must be a JSON list")
    if _margin_hard_gate_required():
        _require_margin_drain_closed(results)
        _assert_margin_shadow_parity(ctx)
    else:
        ctx.log(
            "margin drain/shadow hard-gate SKIP "
            "(disabled + on_demand; product stays frozen)"
        )
    if proc.returncode != 0:
        ctx.degraded("sync_registry drain 有残余缺口或域错误 (见 log)")


def _assert_margin_shadow_parity(ctx: PipelineContext) -> None:
    """Read-only gate over every current accepted canary partition."""

    from services.data_sources import margin_ingest, sync_runner
    from services.data_sources.margin_readiness import evaluate_margin_readiness
    from services.duck_adapter import connect

    spec = sync_runner.domain_spec(sync_runner.load_registry(), "margin")
    contract = margin_ingest.contract_for_spec(spec)
    if contract is None:
        raise Tier0AcquireError("formal margin registry lost its typed contract")
    eligibility = sync_runner.eligible_end_date(spec)
    expected = (
        sync_runner.trading_days(contract.coverage_start, eligibility.eligible_end)
        if eligibility.eligible_end is not None
        else []
    )
    conn = connect(ctx.db("tushare_raw"), read_only=True)
    try:
        readiness = evaluate_margin_readiness(
            conn,
            expected,
            contract=contract,
            eligible_end=eligibility.eligible_end,
            eligibility_reason=eligibility.reason,
        )
        if not readiness.ready:
            failures = [
                (failure.partition_value, list(failure.issue_codes))
                for failure in readiness.reconcile_failures
            ]
            raise Tier0AcquireError(
                "formal margin readiness failed: "
                f"eligible_end={readiness.eligible_end!r} "
                f"eligibility={readiness.eligibility_reason!r} "
                f"expected={len(readiness.expected)} "
                f"accepted={len(readiness.accepted_state.partitions)} "
                f"missing={list(readiness.missing)} "
                f"unexpected={list(readiness.unexpected)} "
                f"reconcile_failures={failures}"
            )
    finally:
        conn.close()
