"""前置 gate (非四阶段之一): TuShare 授权硬门 + watermark SLA 新鲜度检查。

(experiment job 契约 sanity 步 2026-06-28 退役: services.experiment_jobs 随策略/compute 层删除,
 纯数据平台无 compute backend 契约。)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from services.data_sources.sources.tushare import (
    AUTH_FAILURE_REASONS,
    TuShareAuthorizationError,
    TuShareSource,
    probe_authorization,
)

from .context import PipelineContext


class PipelinePreflightError(RuntimeError):
    """本地数据地基不满足安全执行条件，四阶段必须保持未启动。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"pipeline preflight blocked: {reason}")


def ensure_pipeline_sync_ready(ctx: PipelineContext) -> None:
    """Preflight the exact all-due domain set before auth, DB, or provider work."""

    if ctx.skip_sync:
        ctx.log("--- Sync execution policy: SKIP (--skip-sync) ---")
        return
    from services.data_sources.sync_runner import (
        ExecutionPolicyError,
        PopulationScopeExecutionError,
        automatic_domains,
        load_registry,
        preflight_execution_policies,
        preflight_formal_population_scopes,
    )

    try:
        registry = load_registry()
        domains = automatic_domains(registry)
        preflight_execution_policies(registry, domains)
        preflight_formal_population_scopes(registry, domains)
    except (ExecutionPolicyError, PopulationScopeExecutionError) as exc:
        raise PipelinePreflightError(
            f"sync_execution_blocked:{exc.domain}:{exc.reason}"
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelinePreflightError("sync_registry_invalid") from exc
    ctx.log(f"--- Sync execution policy: PASS domains={len(domains)} ---")


def run_watermark_sla_check(ctx: PipelineContext, *, output_rel: str) -> int:
    """复用唯一 SLA 脚本生成指定时点证据；同日重跑前移除旧文件以 fail closed。"""
    import json
    import subprocess
    import sys as _sys

    from .context import REPO

    output_path = REPO / output_rel
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    args = [
        "backend/scripts/update_watermark_sla.py",
        "--json-output",
        output_rel,
    ]
    if ctx.dry:
        args.insert(1, "--dry-run")
    proc = subprocess.run(
        [_sys.executable] + args,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds(),
    )
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or ""))
        ctx._log_fh.flush()
    if proc.returncode in (0, 2):
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            n_alerts = payload["n_alerts"]
            sources = payload["sources"]
            if (
                isinstance(n_alerts, bool)
                or not isinstance(n_alerts, int)
                or n_alerts < 0
                or not isinstance(sources, list)
                or (proc.returncode == 2) != (n_alerts > 0)
            ):
                raise ValueError("exit code / n_alerts / sources contract mismatch")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            output_path.unlink(missing_ok=True)
            ctx.log(
                f"watermark SLA checker returned invalid artifact {output_rel}: "
                f"{type(exc).__name__}"
            )
            return 1
    return proc.returncode


def _auth_expiry_warning_days(ctx: PipelineContext) -> int:
    if ctx.auth_expiry_warning_days is not None:
        return ctx.auth_expiry_warning_days
    from services.data_sources.sync_runner import load_registry
    registry = load_registry()
    # 授权探测硬编码 tushare adapter (下方 TuShareSource()), 语义上就是 tushare 源专属参数
    # (2026-08-30 从 defaults 移入 sources.tushare)；legacy 兜底读 defaults 保持旧式最小
    # registry (无 sources 段, 单测常用) 可用。
    value = ((registry.get("sources") or {}).get("tushare") or {}).get("auth_expiry_warn_days")
    if value is None:
        value = (registry.get("defaults") or {}).get("auth_expiry_warn_days")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sync_registry.sources.tushare.auth_expiry_warn_days 必须是非负整数")
    return value


def _auth_probe_timeout_seconds() -> float:
    from services.data_sources.sync_runner import load_registry

    registry = load_registry()
    value = ((registry.get("sources") or {}).get("tushare") or {}).get("auth_probe_timeout_seconds")
    if value is None:
        value = (registry.get("defaults") or {}).get("auth_probe_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("sync_registry.sources.tushare.auth_probe_timeout_seconds 必须是正数")
    return float(value)


def tushare_dependent_domains(registry: dict[str, Any] | None) -> list[str] | None:
    """Registry domains ``run_acquire`` could touch this run whose source is tushare.

    2026-09-10 tushare 授权到期不续期整改: 探针曾对**每次** pipeline 运行硬性无条件触发
    (preflight.run_preflight / acquire.run_acquire 各调一次), 与"这次到底要不要 tushare"
    无关 —— 到期当天会连累已切非 tushare 的域 (tdxhub 日线 / fuyao stock_basic /
    stock_st_derive / calendar_rule / miaoxiang top_list,top_inst) 一起熄火。

    先例 ``sync_runner._cli_skips_provider_authorization`` 已解过同一问题 (CLI 单次
    --domain/--all-due 调用), 本函数是它在 pipeline 全链场景下的对应: pipeline 没有
    CLI args 可读 (daily_update 隐式跑全部 "enabled" 域, 不是某一个 --domain), 所以直接
    扫 registry 全表, 按 execution_policy.mode=="enabled" 圈出**本次可能被 run_acquire
    触碰到的域全集** —— 这一步刻意不去复刻 acquire.py 的调用图 (drain 走
    automatic_domains, formal on-demand daily/stock_st 是另一条路径, margin/trade_cal
    又是各自的 on_demand 分支), 因为那份清单会随 acquire.py 演进悄悄腐烂 (漏一个新加的
    on_demand 域 = 静默判错); "enabled" 是唯一不随调用图变化的稳定信号，且宁可判多
    (fail-safe 天然更保守，不会更危险)。

    返回 ``None`` 表示"判不出" (registry 缺失/畸形/某域 execution_policy 或 source 解析
    失败) —— 调用方必须把 ``None`` 当"就当需要 tushare"处理 (fail-safe: 有歧义永远走探测,
    不走跳过；与 ``_cli_skips_provider_authorization`` 同一条铁律的镜像)。
    """
    if registry is None:
        return None
    domains = registry.get("domains")
    if not isinstance(domains, dict) or not domains:
        return None
    from services.data_sources.sync_runner import domain_spec, execution_policy_for_spec

    try:
        tushare_domains = []
        for name in domains:
            spec = domain_spec(registry, name)
            policy = execution_policy_for_spec(spec)
            if policy.mode == "enabled" and spec.get("source") == "tushare":
                tushare_domains.append(str(name))
    except Exception:
        return None
    return tushare_domains


def ensure_tushare_authorized(ctx: PipelineContext) -> dict[str, Any] | None:
    """Run the single account probe for sync-capable flows and cache its sanitized result."""
    if ctx.skip_sync:
        ctx.log("--- Authorization: SKIP (--skip-sync) ---")
        return None
    if ctx.tushare_auth_status is not None:
        return ctx.tushare_auth_status
    if ctx.tushare_auth_blocked_reason is not None:
        # 本次 run 内已经探测过且被拒 — 结论不会变, 复用不重打网络探针。
        raise TuShareAuthorizationError(ctx.tushare_auth_blocked_reason)

    from services.data_sources.sync_runner import load_registry
    try:
        registry_for_scope = load_registry()
    except Exception:
        registry_for_scope = None
    due_tushare_domains = tushare_dependent_domains(registry_for_scope)
    if due_tushare_domains is not None and not due_tushare_domains:
        ctx.log("--- Authorization: SKIP (本次无 tushare 源域 due, registry 全表核实) ---")
        return None

    warning_days = _auth_expiry_warning_days(ctx)
    try:
        status = probe_authorization(
            TuShareSource(), timeout_seconds=_auth_probe_timeout_seconds()
        )
    except TuShareAuthorizationError as exc:
        ctx.tushare_auth_blocked_reason = exc.reason
        raise
    ctx.tushare_auth_status = status
    opened_at = status["opened_at"]
    expires_at = status["expires_at"]
    # `remaining_weeks` 是**照抄供应商的 `week` 字段**, 不是算出来的 —— 2026-08-11 实测
    # 它报 4 而 limitDate 说次日到期, 差 4 周。名字里的 "remaining" 会让人读完日志以为
    # 还有一个月, 而真相是明天就断。真相源是 limitDate(绝对时刻), 故这里现算天数;
    # 供应商那个字段照原样标注来源, 不再让它冒充剩余量。裁决本来就只看 expires_at。
    now = datetime.now(expires_at.tzinfo)
    remaining_days = (expires_at - now).days
    ctx.log(
        "--- Authorization: OK "
        f"opened_at={opened_at.isoformat()} expires_at={expires_at.isoformat()} "
        f"remaining_days={remaining_days} (vendor week field={status['remaining_weeks']}, "
        "非剩余量) ---"
    )
    if expires_at - now <= timedelta(days=warning_days):
        ctx.degraded(
            "tushare authorization expires soon: "
            f"expires_at={expires_at.isoformat()} warning_days={warning_days}"
        )
    return status


def _calendar_gate(ctx: PipelineContext):
    import subprocess
    import sys as _sys

    from .context import REPO

    cmd = [
        _sys.executable,
        "backend/scripts/check_continuity_integrity.py",
        "--only",
        "calendar_horizon",
        "--domain",
        "trade_cal",
        "--strict",
        "--json",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds(),
    )
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or ""))
        ctx._log_fh.flush()
    return proc


def _authorization_reason(output: str) -> str:
    """只从去敏 reason allowlist 取值，不把 provider 原文带进父日志/异常。"""
    return next((reason for reason in AUTH_FAILURE_REASONS if reason in output), "auth_denied")


def _repair_calendar_foundation(ctx: PipelineContext) -> None:
    """唯一 bootstrap：同一 writer lease 内 raw full-refresh → dim builder。"""
    import subprocess
    import sys as _sys

    from services.calendar_builder import build_latest

    from .context import REPO

    ensure_tushare_authorized(ctx)
    cmd = [
        _sys.executable,
        "-m",
        "services.data_sources.sync_runner",
        "--domain",
        "trade_cal",
    ]
    ctx.log("--- Calendar foundation: REPAIR raw trade_cal -> dim ---")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds(),
    )
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or ""))
        ctx._log_fh.flush()
    if proc.returncode == 3:
        raise TuShareAuthorizationError(_authorization_reason((proc.stdout or "") + (proc.stderr or "")))
    if proc.returncode != 0:
        raise PipelinePreflightError("calendar_raw_refresh_failed")
    try:
        result = build_latest()
    except Exception as exc:
        ctx.log(f"calendar builder failed: {type(exc).__name__}")
        raise PipelinePreflightError("calendar_dim_rebuild_failed") from exc
    ctx.log(f"calendar builder: inserted={result['inserted']} dim_max={result['dim_max']}")


def ensure_calendar_ready(ctx: PipelineContext, *, allow_repair: bool = False) -> None:
    """严格验证今日 raw/dim 一致与 horizon；仅 controller 写窗口可自修一次。"""
    proc = _calendar_gate(ctx)
    if proc.returncode == 0:
        ctx.log("--- Calendar foundation: PASS (today raw/dim + horizon) ---")
        return
    if not allow_repair:
        raise PipelinePreflightError("calendar_not_ready")
    _repair_calendar_foundation(ctx)
    second = _calendar_gate(ctx)
    if second.returncode != 0:
        raise PipelinePreflightError("calendar_repair_not_closed")
    ctx.log("--- Calendar foundation: REPAIRED + PASS ---")


def run_preflight(ctx: PipelineContext) -> None:
    ensure_pipeline_sync_ready(ctx)
    # 日历是所有 eligible-end/gap 判断的真相源；先验伪本地地基，再调用外部 provider。
    ensure_calendar_ready(ctx, allow_repair=not ctx.dry and not ctx.skip_sync)
    try:
        ensure_tushare_authorized(ctx)
    except TuShareAuthorizationError as exc:
        # 2026-09-10 tushare 到期不续期整改: 授权阻断只降级 tushare 源域, 不再熄火整链——
        # watermark SLA 检查(与 tushare 无关)和四阶段(许多域已切非 tushare)照常继续。
        # 消息刻意不含 "AUTH BLOCK" / "PREFLIGHT BLOCK" 等 run_outcome._HARD_RE 关键字,
        # 否则仍会被判 hard_fail exit 3 —— 那正是本次要拆掉的硬门。
        ctx.degraded(
            f"tushare authorization_blocked: {exc.reason} "
            "(tushare 源域本次跳过; watermark SLA 检查与四阶段照常继续)"
        )
    ctx.log("--- Preflight: watermark SLA 新鲜度检查 ---")

    # Step 1a: before/readiness 证据。alert 可能由本次 acquire 修复，最终 verdict 留给 Store 重算。
    output_rel = f"data/audit/watermark_sla_before_{ctx.date}.json"
    returncode = run_watermark_sla_check(ctx, output_rel=output_rel)
    if returncode == 2:
        ctx.log(
            f"Preflight watermark SLA alert recorded in {output_rel}; "
            "final verdict deferred to post-acquire Store check"
        )
    elif returncode != 0:
        # 检查器本身 crash 比检查出 alert 更该送达 (旧版严重度倒挂只 log)
        ctx.degraded(f"watermark SLA 检查器 crash (exit {returncode}) — SLA 体系失明")
    # 1b: K线 continuity 由 SLA(1a) + acquire 的 sync_runner 日历 gap 扫描覆盖 (builder reset 删)
