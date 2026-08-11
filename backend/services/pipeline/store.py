"""④ 存储 (Store) — 水位刷新 / 健康报告 / 告警送达。

旧 daily_update.sh: Step 2.97 (watermark refresh) + Step 5 (data-health report + 告警 + degraded 汇总)。
必在全部数据步之后跑 (否则水位/报告反映不出新数据)。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .context import REPO, PipelineContext
from .preflight import run_watermark_sla_check
from .run_outcome import (
    OUTCOME_HARD_FAIL,
    OUTCOME_INTEGRITY,
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

    # Step 2.98: system_health 组运行时自检 (goal.md「治理体系重构」P1.2)。
    #   continuity / residual_hygiene 原本就在这里；grain_uniqueness / cutover_effective
    #   是从 commit 路径归位过来的 —— 它们查的是库里现有数据与 config 声明的生效性，
    #   与「有没有人恰好提交代码」无关。清单 owner =
    #   backend/config/governance_gates.yaml，本函数不许再手写第二份。
    run_system_health_checks(ctx)

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


def run_system_health_checks(ctx: PipelineContext) -> list[dict[str, Any]]:
    """按 governance_gates.yaml 的登记跑 system_health 组；FAIL = degraded + 续跑。

    「门装在受害发生的时刻」——这些检查读的是 live 数据与 config 的实际生效性，
    受害时刻是每次跑日更，所以它们属于这里，不属于 commit 路径。执行器注入
    ``ctx.run_script``：子进程继承 writer lease 与日志，失败即记 degraded（不静默）。

    登记表不可用 = 自检整组没跑，本身就是降级事实，必须记录而不是当成通过。
    """
    from services.governance_gates import (
        GatePolicyError,
        RuntimeCheckSpec,
        load_registry,
        run_runtime_checks,
    )

    ctx.log("--- system_health 运行时自检 (owner: backend/config/governance_gates.yaml) ---")
    try:
        registry = load_registry()
    except GatePolicyError as exc:
        ctx.degraded(
            f"system_health 自检整组未执行 — 门登记表不可用 ({exc}); "
            "continuity/grain/cutover 本次无人查"
        )
        return []

    def _run(spec: RuntimeCheckSpec, args: list[str]) -> int:
        # run_script 失败时已按 degraded_msg 记账；此处只把成败转成退出码。
        ok = ctx.run_script(
            spec.script, list(args), degraded_msg=spec.rendered_degraded_msg(date=ctx.date)
        )
        return 0 if ok else 1

    rows = run_runtime_checks(_run, date=ctx.date, dry=ctx.dry, registry=registry)
    failed = [r["id"] for r in rows if r["status"] == "fail"]
    skipped = [r["id"] for r in rows if r["status"] == "skipped_dry"]
    ctx.log(
        f"system_health: {len(rows) - len(failed) - len(skipped)} pass / "
        f"{len(failed)} fail / {len(skipped)} skipped(dry)"
        + (f" — FAIL: {', '.join(failed)}" if failed else "")
    )
    return rows


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
    # CX-1: delta manifest + stage timings + observational latency budgets.
    from .delta_manifest import finalize_manifest_for_report

    delta_manifest = finalize_manifest_for_report(
        getattr(ctx, "delta_manifest", None),
        stage_timing_s=getattr(ctx, "stage_timing_s", None),
    )
    output["delta_manifest"] = delta_manifest
    output["stage_timing_s"] = dict(delta_manifest.get("stage_timing_s") or {})
    output["latency_budgets"] = dict(delta_manifest.get("latency_budgets") or {})
    output["budget_status"] = dict(delta_manifest.get("budget_status") or {})
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
    soft_waiting_clock / integrity_observe → non-macos channels only
      (one observation banner separately)
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

    # Soft / integrity: observation banner is the single macOS surface.
    dispatch_outcome = (
        OUTCOME_INTEGRITY if outcome == OUTCOME_INTEGRITY else OUTCOME_SOFT_WAITING
    )
    dispatch_cmd = [
        sys.executable,
        "-m",
        "backend.services.notification.dispatcher",
        "--report",
        str(report_json),
        "--outcome",
        dispatch_outcome,
    ]
    ctx.log(
        f"Alerts present (active={','.join(active)}); "
        f"dispatching non-macos ({dispatch_outcome} → observation banner owns macOS)"
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


def _soft_banner_marker(ctx: PipelineContext) -> Path:
    """Per-day marker persisting the last soft-outcome signature already notified.

    Lives beside DEGRADED_FLAG so tests that isolate the runtime dir isolate it too.
    """
    from .context import DEGRADED_FLAG

    return DEGRADED_FLAG.parent / f"chunkymonkey_soft_banner_{ctx.date}.marker"


def _soft_banner_signature(output: dict[str, Any]) -> str:
    """Stable content fingerprint of a soft outcome (timestamp-free).

    Two runs whose degraded reasons + SLA-stale sources are identical produce the
    same signature, so an idle re-click with no real change is coalesced. A genuine
    new/changed degradation produces a different signature and re-notifies.
    Uses raw ``run_outcome_classified`` msgs (not DEGRADED_FLAG lines) precisely
    because those carry no per-run timestamp prefix.
    """
    classified = output.get("run_outcome_classified") or []
    msgs = sorted(str(c.get("msg", "")) for c in classified if isinstance(c, dict))
    stale = sorted(str(s) for s in ((output.get("sla_summary") or {}).get("stale_sources") or []))
    payload = json.dumps(
        {
            "date": output.get("date"),
            "outcome": output.get("run_outcome"),
            "reason": output.get("run_outcome_reason"),
            "msgs": msgs,
            "sla_stale": stale,
            "sla_warn": bool(output.get("sla_warn") or (output.get("alert_flags") or {}).get("sla_warn")),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _outcome_summary_banner(ctx: PipelineContext, output: dict[str, Any]) -> None:
    """At most one macOS observation banner PER DISTINCT soft outcome.

    Coalesce (plan §C2 / 通知 truth): an idle re-click that produces the *same*
    soft signature must not re-spawn an identical banner. Success clears the marker
    so a later soft change re-notifies once. hard_fail → wrapper owns the banner.
    """
    from .context import DEGRADED_FLAG

    marker = _soft_banner_marker(ctx)
    outcome = str(output.get("run_outcome") or "")
    if outcome == OUTCOME_SUCCESS:
        ctx.log("degraded: 0 步")
        marker.unlink(missing_ok=True)  # 恢复正常 → 下次软观测重新提醒一次
        return
    if outcome == OUTCOME_HARD_FAIL:
        # Wrapper owns the single FAIL notification (rc∈{2,3,4,5}).
        ctx.log(
            f"run_outcome=hard_fail — skip store observation banner "
            f"(wrapper FAIL owns macOS; msgs={len(ctx.degraded_msgs)})"
        )
        return
    if outcome not in {OUTCOME_SOFT_WAITING, OUTCOME_INTEGRITY}:
        return

    msgs = (
        DEGRADED_FLAG.read_text().strip().splitlines()
        if DEGRADED_FLAG.exists()
        else list(ctx.degraded_msgs)
    )
    n = len(msgs) or int(output.get("degraded_total") or 0)
    summary_tag = (
        "INTEGRITY_OBSERVE" if outcome == OUTCOME_INTEGRITY else "SOFT_WAITING"
    )
    ctx.log(f"{summary_tag} SUMMARY: 本次 {n} 步观测 (明细 {DEGRADED_FLAG}):")
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

    # Coalesce identical soft/integrity outcomes (idempotent re-click de-spam).
    signature = _soft_banner_signature(output)
    try:
        already = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    except OSError:
        already = ""
    if already == signature:
        ctx.log(
            f"run_outcome={outcome} — 与上次观测一致, 合并通知 (不重复弹窗; "
            f"signature={signature}; 明细见 data/reports/daily_{ctx.date}.json)"
        )
        return

    label = output.get("run_outcome_label") or (
        "完整性观测（非时钟）"
        if outcome == OUTCOME_INTEGRITY
        else "等时钟 / 软观测"
    )
    title = (
        "ChunkyMonkey integrity_observe"
        if outcome == OUTCOME_INTEGRITY
        else "ChunkyMonkey soft_waiting_clock"
    )
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "daily_update {label} · {n} 项{sla_hint}, '
                f'见 data/reports/daily_{ctx.date}.json" '
                f'with title "{title}"',
            ],
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        marker.write_text(signature, encoding="utf-8")
    except OSError:  # noqa: BLE001 — marker 写失败不影响链; 最坏下次软态多弹一条, 不吞真状态
        pass
