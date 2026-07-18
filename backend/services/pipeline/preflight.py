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
    value = (load_registry().get("defaults") or {}).get("auth_expiry_warn_days")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sync_registry.defaults.auth_expiry_warn_days 必须是非负整数")
    return value


def _auth_probe_timeout_seconds() -> float:
    from services.data_sources.sync_runner import load_registry

    value = (load_registry().get("defaults") or {}).get("auth_probe_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("sync_registry.defaults.auth_probe_timeout_seconds 必须是正数")
    return float(value)


def ensure_tushare_authorized(ctx: PipelineContext) -> dict[str, Any] | None:
    """Run the single account probe for sync-capable flows and cache its sanitized result."""
    if ctx.skip_sync:
        ctx.log("--- Authorization: SKIP (--skip-sync) ---")
        return None
    if ctx.tushare_auth_status is not None:
        return ctx.tushare_auth_status

    warning_days = _auth_expiry_warning_days(ctx)
    status = probe_authorization(
        TuShareSource(), timeout_seconds=_auth_probe_timeout_seconds()
    )
    ctx.tushare_auth_status = status
    opened_at = status["opened_at"]
    expires_at = status["expires_at"]
    remaining_weeks = status["remaining_weeks"]
    ctx.log(
        "--- Authorization: OK "
        f"opened_at={opened_at.isoformat()} expires_at={expires_at.isoformat()} "
        f"remaining_weeks={remaining_weeks} ---"
    )
    now = datetime.now(expires_at.tzinfo)
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
    # 日历是所有 eligible-end/gap 判断的真相源；先验伪本地地基，再调用外部 provider。
    ensure_calendar_ready(ctx, allow_repair=not ctx.dry and not ctx.skip_sync)
    ensure_tushare_authorized(ctx)
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
