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
    from .delta_manifest import empty_manifest

    if ctx.delta_manifest is None:
        ctx.delta_manifest = empty_manifest(run_date=ctx.date)

    if ctx.skip_sync:
        ctx.log("=== ① 获取 ACQUIRE: SKIP (--skip-sync) ===")
        # Margin hard-gate only while the product is enabled; frozen/disabled skips.
        if not ctx.dry and _margin_hard_gate_required():
            _assert_margin_shadow_parity(ctx)
        elif not ctx.dry:
            ctx.log("margin hard-gate SKIP (execution_policy disabled / not in all-due)")
        _finalize_acquire_delta(ctx, drain_results=[], formal_outcomes=[])
        return
    # 独立 stage 入口也必须走与全链相同的授权硬门；全链已探针时复用 ctx 缓存。
    from .preflight import ensure_pipeline_sync_ready, ensure_tushare_authorized
    ensure_pipeline_sync_ready(ctx)
    ensure_tushare_authorized(ctx)
    ctx.log("=== ① 获取 ACQUIRE (纯采集 →L0, 不计算) ===")
    if ctx.dry:
        ctx.log("DRY: 跳过实际 sync (获取阶段全是写操作)")
        _finalize_acquire_delta(ctx, drain_results=[], formal_outcomes=[])
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

    # Step 2j2: 机构持仓明细 aif10 — every-run incremental check (mass refresh banned)
    ctx.step(lambda: _sync_org_holding(ctx), degraded_msg="org_holding aif10 sync 失败")

    # Step 2k (external_attention 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 东财人气/关注度退役, 用户决cut, 无tushare等价=永久丢):
    #   消费侧 scoring 外部关注 boost/池升级/crowding penalty 优雅降级 (external_attention_score→None)。

    # Step 2l (profit_forecast EPS 快照) 已退役 2026-06-27 (通达信全删 M4: akshare 退役, 用户决cut):
    #   raw_profit_forecast_snapshot_daily 0 live 读者 (snapshot 设计防leakage但无消费); 档B 若需景气度走 tushare forecast/report_rc。

    # Step 2.94: registry --all-due drain FIRST (published automatic domains).
    # Structural (2026-07-22 RCA): formal on_demand daily/ST catchup must NOT
    # hard-gate / kidnap published-domain catchup (ths_hot etc.). S3 intent =
    # caller-only orchestrator with per-domain fail-closed siblings — not a
    # fused "today's K/ST empty ⇒ abort whole acquire" dragon.
    drain_results = _sync_registry_drain(ctx)

    # Step 2.95: formal on_demand daily/ST — latest eligible land_then_accept.
    # Per-domain soft (pending) / degraded (hard fail); never aborts drain
    # (drain already ran) and never raises Tier0AcquireError for domain outcomes.
    formal_outcomes = _sync_formal_on_demand_security_days(ctx)

    # Step 2.96: 交易日历 dim 传导 (R1 根因3, 2026-07-03): raw_tushare_trade_cal (2.94 已刷)
    #   → reference.dim_trading_calendar 增量 MERGE。dim 曾无生产 writer (唯一写方=已封存的
    #   一次性迁移脚本), horizon 倒计时中 — 本步是 dim 的唯一日常刷新契约。
    ctx.step(_build_trading_calendar,
             degraded_msg="交易日历 dim 传导失败 — dim_trading_calendar 停止向未来延伸 (horizon 门将 FAIL)")

    # Step 2.97 (2026-07-06 全面数据审计根因根治): raw_tushare_stock_basic (2.95 已刷)
    #   → reference.dim_active_a_stock 全量重写。写函数早就存在且正确, 但此前从未被任何
    #   daily_update 步骤调用, 25 个消费方(universe 身份真相源)读的这张表只能靠人工手动
    #   跑脚本刷新, 实测发现已静默 stale 8 天。本步是 dim_active_a_stock 的唯一日常刷新契约,
    #   与上一步 dim_trading_calendar 同一模式补齐。
    ctx.step(
        lambda: _refresh_active_a_stock_master(ctx),
        degraded_msg="dim_active_a_stock 刷新失败 — universe 身份真相源可能继续 stale",
    )  # rule-compliance: ok evidence=写方本身非universe消费

    _finalize_acquire_delta(ctx, drain_results=drain_results, formal_outcomes=formal_outcomes)


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


def _sync_org_holding(ctx: PipelineContext) -> None:
    """Org holding incremental check: fetch/accept one plannable period or skip.

    Mass ~830k refresh / by-date invent / backfill banned (owner 2026-07-21/23).
    """
    import asyncio
    from pathlib import Path

    from services.duck_adapter import connect as duck_connect
    from services.org_holding_aif10 import (
        org_holding_period_gap_report,
        sync_org_holding_incremental,
    )
    from .context import db_path
    from .delta_manifest import empty_manifest

    conn = duck_connect(db_path("smartmoney"))
    try:
        gap = org_holding_period_gap_report(conn)
        print("org_holding_gap_check: " + json.dumps(gap, ensure_ascii=False, default=str))
        result = asyncio.run(sync_org_holding_incremental(conn))
        print(json.dumps(result, ensure_ascii=False, default=str))
    finally:
        conn.close()

    if ctx.delta_manifest is None:
        ctx.delta_manifest = empty_manifest(run_date=ctx.date)
    summary = dict(ctx.delta_manifest.get("acquire_summary") or {})
    incremental = list(summary.get("incremental") or [])
    incremental.append(
        {
            "domain": "org_holding",
            "action": result.get("action"),
            "status": result.get("status"),
            "report_date": result.get("report_date"),
            "available_date": result.get("available_date"),
            "written": result.get("written") or result.get("count") or 0,
            "next_period": result.get("next_period"),
            "next_period_unlock": result.get("next_period_unlock"),
            "message": result.get("message"),
            "gap": {
                "plannable": (result.get("gap") or {}).get("plannable"),
                "local_has_plannable": (result.get("gap") or {}).get(
                    "local_has_plannable"
                ),
                "accepted_has_plannable": (result.get("gap") or {}).get(
                    "accepted_has_plannable"
                ),
                "missing_count": (result.get("gap") or {}).get("missing_count"),
            },
        }
    )
    summary["incremental"] = incremental
    ctx.delta_manifest["acquire_summary"] = summary

    if str(result.get("status") or "") == "under_populated_accepted":
        ctx.degraded(
            "org_holding under_populated_accepted — thin/canary accept; "
            "mass refresh banned; repair knife required"
        )
    try:
        out = Path(__file__).resolve().parents[3] / "data" / "reports"
        out.mkdir(parents=True, exist_ok=True)
        (out / "org_holding_period_gap_latest.json").write_text(
            json.dumps(
                {
                    "run_date": ctx.date,
                    "gap": gap,
                    "result": {
                        "action": result.get("action"),
                        "status": result.get("status"),
                        "message": result.get("message"),
                    },
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        ctx.log(f"org_holding gap latest write skipped: {exc}")


# _sync_external_attention 已退役 2026-06-27 (通达信全删 M4: akshare external_attention.py 物删, 用户决cut)


def _build_trading_calendar() -> None:
    """raw trade_cal → reference.dim_trading_calendar 增量 (R1 根因3 生产刷新契约)."""
    from services.calendar_builder import build_latest
    print(f"calendar_builder: {build_latest()}")


def _refresh_active_a_stock_master(ctx: PipelineContext) -> None:
    """raw_tushare_stock_basic → reference.dim_active_a_stock 全量重写 (rule-compliance: ok evidence=写方本身非universe消费)
    (2026-07-06 全面数据审计根因根治): 写函数 refresh_active_a_stock_master 一直存在且正确,
    但从未被任何 daily_update 步骤调用过——25 个消费方(universe 身份真相源)读的这张表此前
    只能靠人工手动跑脚本刷新, 实测发现已静默 stale 8 天。stock_basic 域已由上一步
    _sync_registry_drain 的 --all-due 覆盖同步 (full_refresh 批模式), 本步紧随其后重建派生表,
    与 _build_trading_calendar (raw trade_cal → dim_trading_calendar) 同一模式。

    CX-2: capture before/after code sets for delist state sensor (observer only).
    """
    from services.data_access import resolver
    from services.pipeline.state_sensors import load_dim_active_codes
    from services.security_master import refresh_active_a_stock_master

    before: set[str] | None = None
    try:
        ref_ro = resolver.connect_ro("reference")
        try:
            before = load_dim_active_codes(ref_ro)
        finally:
            ref_ro.close()
    except Exception as exc:  # noqa: BLE001 — sensor baseline best-effort
        ctx.log(f"dim_active before-snapshot unavailable: {type(exc).__name__}")
        before = None
    ctx.dim_active_codes_before = before

    n = refresh_active_a_stock_master(None)
    print(f"security_master: refresh_active_a_stock_master rows={n}")

    after: set[str] | None = None
    try:
        ref_ro = resolver.connect_ro("reference")
        try:
            after = load_dim_active_codes(ref_ro)
        finally:
            ref_ro.close()
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"dim_active after-snapshot unavailable: {type(exc).__name__}")
        after = None
    ctx.dim_active_codes_after = after


# stock_st = day-level ST *membership evidence* for HS-A (ST names stay in
# whitelist). Soft/pending here is publish-timing only — never "drop ST from
# product" / exclude-then-fetch. Exclude boards = 三板/退市整理/B/BJ via
# universe_rules, not by skipping this domain. Owner: MASTER §5.1 / goal.md.
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


def _sync_formal_on_demand_security_days(ctx: PipelineContext) -> list[dict]:
    """Pull latest eligible formal daily/ST via modular land_then_accept.

    on_demand domains never enter --all-due. This is the S3 orchestrator bridge:
    one trade_date = eligible_end when missing from accepted_partition.
    Does **not** fill historical holes (log-not-fill / explicit backfill knife).

    Per-domain outcomes (structural 2026-07-22):
    - pending_publish / skip → soft continue (typed state, not pipeline abort)
    - land_then_accept hard fail → ``ctx.degraded`` + continue sibling domains
    - wiring bugs (wrong sync_policy) still raise Tier0AcquireError
    Never raises for ordinary domain catchup outcomes (avoids exit-5 kidnap of
    clean/process after drain has already run, and of siblings within this step).
    """

    from services.data_sources import sync_runner
    from services.duck_adapter import connect

    registry = sync_runner.load_registry()
    planned: list[tuple[str, str, str, str]] = []  # domain, eligible_end, reason, dataset_id
    outcomes: list[dict] = []
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
                outcome = {
                    "domain": domain,
                    "action": "skip",
                    "reason": f"execution_policy_{policy.mode}",
                    "policy_reason": policy.reason,
                }
                ctx.log(
                    f"formal {domain}: SKIP catchup "
                    f"(execution_policy {policy.mode}/{policy.reason})"
                )
                outcomes.append(outcome)
                continue
            eligibility = sync_runner.eligible_end_date(spec, trigger_mode="manual")
            eligible_end = eligibility.eligible_end
            if eligible_end is None:
                outcome = {
                    "domain": domain,
                    "action": "skip",
                    "reason": "no_eligible_end",
                    "eligibility_reason": eligibility.reason,
                }
                ctx.log(
                    f"formal {domain}: SKIP catchup "
                    f"(no eligible_end; reason={eligibility.reason})"
                )
                outcomes.append(outcome)
                continue
            dataset_id = _formal_security_day_dataset_id(domain)
            if _accepted_partition_exists(conn, dataset_id, eligible_end):
                outcome = {
                    "domain": domain,
                    "action": "skip",
                    "reason": "latest_eligible_already_accepted",
                    "eligible_end": eligible_end,
                    "eligibility_reason": eligibility.reason,
                    "dataset_id": dataset_id,
                }
                print(json.dumps(outcome, ensure_ascii=False))
                outcomes.append(outcome)
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
        if result.get("pending_publish"):
            outcome = {
                "domain": domain,
                "action": "pending_publish",
                "eligible_end": eligible_end,
                "eligibility_reason": eligibility_reason,
                "dataset_id": dataset_id,
                "pending_publish_reason": result.get(
                    "pending_publish_reason",
                    "same_day_vendor_vacuum",
                ),
            }
            print(json.dumps(outcome, ensure_ascii=False))
            outcomes.append(outcome)
            continue
        if status != "ok" or failed:
            outcome = {
                "domain": domain,
                "action": "failed",
                "eligible_end": eligible_end,
                "eligibility_reason": eligibility_reason,
                "dataset_id": dataset_id,
                "status": status,
                "failed_batches": failed,
                "error": result.get("error"),
            }
            print(json.dumps(outcome, ensure_ascii=False, default=str))
            outcomes.append(outcome)
            # Domain-local fail-closed: degrade, do not abort siblings / chain.
            ctx.degraded(
                f"formal {domain} land_then_accept failed for {eligible_end}: "
                f"status={status!r} failed_batches={failed} "
                f"error={result.get('error')!r}"
            )
            continue
        print(json.dumps(result, ensure_ascii=False, default=str))
        outcomes.append(
            {
                "domain": domain,
                "action": "accepted",
                "eligible_end": eligible_end,
                "dataset_id": dataset_id,
            }
        )
    return outcomes


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


def _run_drain_subprocess(
    ctx: PipelineContext, cmd: list[str]
) -> tuple[int, str, str]:
    """Run the drain subprocess, **streaming stderr live** into the parent log.

    Root cause fixed (2026-07-22, owner mandate): the previous
    ``subprocess.run(..., capture_output=True)`` buffered ~40 min of drain
    output until the child returned, so the workbench「数据更新」/``current_activity``
    saw one static ``$ ...sync_runner --all-due`` line and looked hung. The drain
    was never stuck — only its per-domain progress was starved.

    stdout stays a single machine-readable JSON list (parsed by the caller for
    per-domain evidence); human/domain progress is Python ``logging`` +
    per-domain lines on **stderr**, which we pump line-by-line to ``ctx._log_fh``
    as it arrives. A reader thread drains stderr while the main thread reads the
    full stdout, avoiding pipe-buffer deadlock. Returns ``(returncode, stdout,
    stderr)`` so the caller keeps its existing auth / JSON-parse contract.
    """
    import subprocess, sys as _sys, threading
    from .context import REPO

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered: stderr progress reaches the log promptly
        env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds(),
    )
    stderr_chunks: list[str] = []

    def _pump_stderr() -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            stderr_chunks.append(line)
            # Dual-write like ctx.log: our own stdout is the wrapper's job log
            # (/tmp/chunkymonkey_<job>.log, read by the workbench current_activity),
            # while _log_fh is the date-suffixed pipeline log. Both must receive
            # per-domain drain progress live so「数据更新」isn't a static line.
            _sys.stdout.write(line)
            _sys.stdout.flush()
            if ctx._log_fh:
                ctx._log_fh.write(line)
                ctx._log_fh.flush()

    pump = threading.Thread(target=_pump_stderr, daemon=True)
    pump.start()
    stdout_data = proc.stdout.read() if proc.stdout else ""
    proc.wait()
    pump.join(timeout=10)
    return int(proc.returncode or 0), stdout_data, "".join(stderr_chunks)


def _sync_registry_drain(ctx: PipelineContext) -> list[dict]:
    """sync_runner --all-due --drain (module 调用, subprocess 隔离)。

    Drain stderr streams live to the parent log (see ``_run_drain_subprocess``);
    stdout is the final per-domain JSON evidence parsed below.
    Returns the per-domain evidence list (possibly empty on soft paths).
    """
    import sys as _sys
    cmd = [_sys.executable, "-m", "services.data_sources.sync_runner",
           "--all-due", "--drain", "--max-dates", "30"]
    ctx.log(f"  $ {' '.join(cmd)}")
    returncode, stdout_data, stderr_data = _run_drain_subprocess(ctx, cmd)
    if ctx._log_fh:
        # stderr already streamed live; append the final stdout JSON evidence.
        ctx._log_fh.write(stdout_data or ""); ctx._log_fh.flush()
    if returncode == 3:
        from services.data_sources.sources.tushare import (
            AUTH_FAILURE_REASONS,
            TuShareAuthorizationError,
        )

        output = (stdout_data or "") + (stderr_data or "")
        reason = next((item for item in AUTH_FAILURE_REASONS if item in output), "auth_denied")
        raise TuShareAuthorizationError(reason)
    try:
        results = json.loads(stdout_data or "")
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
    if returncode != 0:
        ctx.degraded("sync_registry drain 有残余缺口或域错误 (见 log)")
    return results


def _finalize_acquire_delta(
    ctx: PipelineContext,
    *,
    drain_results: list[dict],
    formal_outcomes: list[dict],
) -> None:
    """Attach typed delta_manifest after acquire evidence is known (CX-1/CX-2)."""
    from services.duck_adapter import connect

    from .delta_manifest import (
        build_advanced_partitions,
        decide_dc_action,
        empty_manifest,
        load_latency_budgets,
        plan_process_steps,
        probe_dc_source_frontier,
        read_dc_as_of,
    )
    from .state_sensors import collect_state_changes

    manifest = ctx.delta_manifest or empty_manifest(run_date=ctx.date)
    manifest["run_date"] = ctx.date
    summary = dict(manifest.get("acquire_summary") or {})
    summary["drain"] = list(drain_results or [])
    summary["formal"] = list(formal_outcomes or [])
    manifest["acquire_summary"] = summary

    advanced = build_advanced_partitions(
        formal=list(formal_outcomes or []),
        drain=list(drain_results or []),
    )
    budgets = load_latency_budgets()
    current_frontier = probe_dc_source_frontier()
    previous_frontier = read_dc_as_of()
    dc_decision = decide_dc_action(
        current_frontier=current_frontier,
        previous_frontier=previous_frontier,
        advanced_partitions=advanced,
        provenance_domains=budgets["dc_provenance_domains"],
    )

    # CX-2 state sensors — read-only; never fuse into Tier0 writers.
    stock_st_conn = None
    holders_conn = None
    state_changes: dict = {}
    try:
        if not ctx.dry:
            stock_st_conn = connect(ctx.db("tushare_raw"), read_only=True)
            holders_conn = connect(ctx.db("smartmoney"), read_only=True)
        state_changes = collect_state_changes(
            stock_st_conn=stock_st_conn,
            holders_conn=holders_conn,
            delist_before=ctx.dim_active_codes_before,
            delist_after=ctx.dim_active_codes_after,
            persist_dim_as_of=not ctx.dry,
        )
    except Exception as exc:  # noqa: BLE001 — sensors must not abort acquire
        ctx.log(f"state_sensors unavailable: {type(exc).__name__}: {exc}")
        state_changes = {
            "stock_st": {
                "status": "unavailable",
                "changed": False,
                "reason": str(exc)[:200],
                "tier0_write": False,
            },
            "holders": {
                "status": "unavailable",
                "changed": False,
                "tier0_write": False,
            },
            "delist": {
                "status": "unavailable",
                "changed": False,
                "tier0_write": False,
            },
            "any_changed": False,
            "force_reasons": [],
        }
    finally:
        for conn in (stock_st_conn, holders_conn):
            if conn is None:
                continue
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    delta = dict(manifest.get("delta") or {})
    delta["advanced_partitions"] = advanced
    delta["dc_source_frontier"] = current_frontier
    delta["dc_frontier_advanced"] = dc_decision.get("dc_frontier_advanced")
    delta["late_window_policy"] = "always_run"
    delta["state_changes"] = state_changes
    manifest["delta"] = delta
    manifest["process_plan"] = plan_process_steps(
        dc_decision=dc_decision,
        state_changes=state_changes,
    )
    ctx.delta_manifest = manifest
    incremental = list((manifest.get("acquire_summary") or {}).get("incremental") or [])
    # Stream-truth single line for workbench regex / log tail consumers.
    ctx.log(
        "[delta_manifest] "
        + json.dumps(
            {
                "formal": [
                    {"domain": r.get("domain"), "action": r.get("action")}
                    for r in (formal_outcomes or [])
                ],
                "incremental": [
                    {
                        "domain": r.get("domain"),
                        "action": r.get("action"),
                        "status": r.get("status"),
                        "report_date": r.get("report_date"),
                    }
                    for r in incremental
                ],
                "advanced_n": len(advanced),
                "dc": manifest["process_plan"]["dc_industry_view"],
                "late_window_policy": "always_run",
                "state_changes": {
                    "any_changed": state_changes.get("any_changed"),
                    "stock_st": {
                        "changed": (state_changes.get("stock_st") or {}).get("changed"),
                        "entered_n": (state_changes.get("stock_st") or {}).get(
                            "entered_n"
                        ),
                        "exited_n": (state_changes.get("stock_st") or {}).get("exited_n"),
                    },
                    "holders": {
                        "changed": (state_changes.get("holders") or {}).get("changed"),
                        "ratio_changed_n": (state_changes.get("holders") or {}).get(
                            "ratio_changed_n"
                        ),
                        "rank_changed_n": (state_changes.get("holders") or {}).get(
                            "rank_changed_n"
                        ),
                        "exit_n": (state_changes.get("holders") or {}).get("exit_n"),
                    },
                    "delist": {
                        "changed": (state_changes.get("delist") or {}).get("changed"),
                        "removed_n": (state_changes.get("delist") or {}).get(
                            "removed_n"
                        ),
                    },
                },
            },
            ensure_ascii=False,
        )
    )


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
