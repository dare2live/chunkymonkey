"""④ 存储 (Store) — 水位刷新 / 健康报告 / 告警送达。

旧 daily_update.sh: Step 2.97 (watermark refresh) + Step 5 (data-health report + 告警 + degraded 汇总)。
必在全部数据步之后跑 (否则水位/报告反映不出新数据)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .context import REPO, PipelineContext
from .preflight import run_watermark_sla_check
from .run_outcome import (
    OUTCOME_HARD_FAIL,
    OUTCOME_SOFT_WAITING,
    OUTCOME_SUCCESS,
    derive_run_outcome,
)


def run_store(ctx: PipelineContext) -> None:
    ctx.log("=== ④ 存储 STORE (水位/连续性/报告/告警) ===")

    # Step 2.97: 源域水位刷新 (从真实表派生, 写 mart_data_source_watermark) — 必在全部数据步之后
    if not ctx.skip_sync and not ctx.dry:
        ctx.run_script("backend/scripts/refresh_source_watermarks.py",
                       degraded_msg="watermark refresh 失败 — SLA 体系将持续误报 stale")

    # Step 2.98: 数据连续性/完整性常驻审查 (R1 根因2/4/6 机械门 2026-07-03: 中间空洞/横截面骤降/
    #   子榜断流/声明-实测漂移/by_ts_code 断流)。FAIL = degraded + 专属 flag (非 FAIL 自愈清 flag,
    #   与 /tmp/chunkymonkey_ALERT_*.flag 告警链同模式); read_only 全库扫描, skip_sync 也跑 (库存审查)。
    if not ctx.dry:
        ctx.run_script(
            "backend/scripts/check_continuity_integrity.py",
            ["--alert-flag", "/tmp/chunkymonkey_ALERT_continuity.flag",
             "--json-out", f"data/audit/continuity_{ctx.date}.json"],
            degraded_msg=("continuity/integrity 审查 FAIL — 库存数据缺日/断流/横截面异常 "
                          f"(详 data/audit/continuity_{ctx.date}.json + ALERT_continuity.flag)"))

    # Step 2.99: acquisition 后重算同一 SLA 投影。preflight 仅保留 before/readiness 证据，
    # 最终报告和告警只消费此 post-acquire artifact，避免已修复分区仍被旧报告误报。
    sla_output_rel = f"data/audit/watermark_sla_{ctx.date}.json"
    sla_returncode = run_watermark_sla_check(ctx, output_rel=sla_output_rel)
    if sla_returncode == 2:
        ctx.degraded(f"post-acquire watermark SLA alert (见 {sla_output_rel})")
    elif sla_returncode != 0:
        ctx.degraded(
            f"post-acquire watermark SLA 检查器 crash (exit {sla_returncode}) — 最终 SLA 失明"
        )

    # Step 5: data-health 报告 + outcome-keyed 告警送达
    write_report_and_alert(ctx)


def write_report_and_alert(
    ctx: PipelineContext,
    *,
    hard_exit_code: int | None = None,
) -> dict[str, Any]:
    """Write daily_*.json (incl. typed run_outcome) and dispatch by outcome.

    Callable from store (full chain) and from run.py early hard exits so
    wrapper/UI always have a machine-readable truth object.
    """
    ctx.log("--- Report (data-health + run_outcome) ---")
    (REPO / "data/reports").mkdir(parents=True, exist_ok=True)
    (REPO / "data/audit").mkdir(parents=True, exist_ok=True)
    report_json = REPO / f"data/reports/daily_{ctx.date}.json"
    preflight_sla_report = REPO / f"data/audit/watermark_sla_before_{ctx.date}.json"
    sla_report = REPO / f"data/audit/watermark_sla_{ctx.date}.json"

    _has_degraded = len(ctx.degraded_msgs) > 0
    outcome_info = derive_run_outcome(
        ctx.degraded_msgs,
        hard_exit_code=hard_exit_code,
    )
    output: dict[str, Any] = {
        "date": ctx.date,
        "dry_run": int(ctx.dry),
        "log": str(ctx.log_path),
        "scope": "data_foundation (L0/L1/L1k/snapshot)",
        "phase_status": {
            "preflight": "OK" if preflight_sla_report.exists() else "ERR",
            "post_acquire_sla": "OK" if sla_report.exists() else "ERR",
            "chain": "DEGRADED_PARTIAL" if _has_degraded else "OK",
            "detail": "逐阶段状态见 smartmoney.mart_pipeline_run_manifest pipeline.stage.* 行",
        },
        "sla_evidence": {
            "preflight": str(preflight_sla_report),
            "post_acquire": str(sla_report),
        },
        "degraded_total": len(ctx.degraded_msgs),
        "degraded_msgs": list(ctx.degraded_msgs)[:20],
        # Typed SSOT — exit/wrapper/notify/UI render this (plan §C2).
        "run_outcome": outcome_info["run_outcome"],
        "run_outcome_label": outcome_info["run_outcome_label"],
        "run_outcome_reason": outcome_info["run_outcome_reason"],
        "run_outcome_exit_code": outcome_info["exit_code"],
        "run_outcome_classified": outcome_info["classified"][:20],
    }
    alert_flags = {"sla_warn": False}

    if sla_report.exists():
        try:
            sla = json.loads(sla_report.read_text())
            output["sla_summary"] = {
                "n_updates": sla.get("n_updates", 0),
                "n_alerts": sla.get("n_alerts", 0),
                "stale_sources": [s["source_name"] for s in sla.get("sources", []) if s.get("alert")],
            }
            alert_flags["sla_warn"] = bool(output["sla_summary"]["n_alerts"])
        except Exception as e:  # noqa: BLE001
            output["sla_summary"] = {"error": str(e)}

    output["alert_flags"] = alert_flags
    output.update(alert_flags)
    report_json.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    ctx.log(
        f"Report written: {report_json} "
        f"run_outcome={output['run_outcome']} "
        f"reason={output['run_outcome_reason']}"
    )

    _dispatch_by_outcome(ctx, report_json, output)
    _outcome_summary_banner(ctx, output)
    return output


def _dispatch_by_outcome(
    ctx: PipelineContext,
    report_json: Path,
    output: dict[str, Any],
) -> None:
    """Outcome-keyed notification: no --skip-macos heuristic.

    success → silent
    soft_waiting_clock → non-macos channels only (one observation banner separately)
    hard_fail → wrapper owns the single FAIL macOS banner (no store spam)
    """
    outcome = str(output.get("run_outcome") or "")
    if outcome == OUTCOME_SUCCESS:
        ctx.log("run_outcome=success — notification silent")
        return
    if outcome == OUTCOME_HARD_FAIL:
        ctx.log("run_outcome=hard_fail — store skips dispatch (wrapper FAIL owns notify)")
        return

    active = [k for k in ("sla_warn",) if bool(output.get(k))]
    if not active:
        ctx.log("No notification alerts (sla_warn off)")
        return

    # Soft waiting: observation banner is the single macOS surface.
    dispatch_cmd = [
        sys.executable,
        "-m",
        "backend.services.notification.dispatcher",
        "--report",
        str(report_json),
        "--outcome",
        OUTCOME_SOFT_WAITING,
    ]
    ctx.log(
        f"Alerts present (active={','.join(active)}); "
        "dispatching non-macos (soft_waiting_clock → observation banner owns macOS)"
    )

    proc = subprocess.run(
        dispatch_cmd,
        cwd=str(REPO), capture_output=True, text=True, env=ctx._subprocess_env(),
        pass_fds=ctx._subprocess_pass_fds())
    if ctx._log_fh:
        ctx._log_fh.write((proc.stdout or "") + (proc.stderr or ""))
        ctx._log_fh.flush()
    if proc.returncode != 0:
        ctx.degraded("notification dispatch failed")


def _outcome_summary_banner(ctx: PipelineContext, output: dict[str, Any]) -> None:
    """At most one macOS observation banner for soft_waiting_clock; hard via wrapper."""
    from .context import DEGRADED_FLAG

    outcome = str(output.get("run_outcome") or "")
    if outcome == OUTCOME_SUCCESS:
        ctx.log("degraded: 0 步")
        return
    if outcome == OUTCOME_HARD_FAIL:
        # Wrapper owns the single FAIL notification (rc∈{2,3,4,5}).
        ctx.log(
            f"run_outcome=hard_fail — skip store observation banner "
            f"(wrapper FAIL owns macOS; msgs={len(ctx.degraded_msgs)})"
        )
        return
    if outcome != OUTCOME_SOFT_WAITING:
        return

    msgs = (
        DEGRADED_FLAG.read_text().strip().splitlines()
        if DEGRADED_FLAG.exists()
        else list(ctx.degraded_msgs)
    )
    n = len(msgs) or int(output.get("degraded_total") or 0)
    ctx.log(f"SOFT_WAITING SUMMARY: 本次 {n} 步软观测 (明细 {DEGRADED_FLAG}):")
    for m in msgs:
        ctx.log(f"  {m}")
    sla_hint = ""
    try:
        if output.get("sla_warn") or (output.get("alert_flags") or {}).get("sla_warn"):
            stale = (output.get("sla_summary") or {}).get("stale_sources") or []
            sla_hint = (
                f"; SLA stale={','.join(str(s) for s in stale[:4])}"
                if stale
                else "; SLA warn"
            )
    except Exception:  # noqa: BLE001
        pass
    label = output.get("run_outcome_label") or "等时钟 / 软观测"
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "daily_update {label} · {n} 项{sla_hint}, '
                f'见 data/reports/daily_{ctx.date}.json" '
                f'with title "ChunkyMonkey soft_waiting_clock"',
            ],
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        pass
