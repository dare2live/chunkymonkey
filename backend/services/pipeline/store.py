"""④ 存储 (Store) — 水位刷新 / 健康报告 / 告警送达。

旧 daily_update.sh: Step 2.97 (watermark refresh) + Step 5 (data-health report + 告警 + degraded 汇总)。
必在全部数据步之后跑 (否则水位/报告反映不出新数据)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .context import REPO, PipelineContext


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

    # Step 5: data-health 报告 + 告警送达
    _write_report_and_alert(ctx)

    # degraded 汇总送达 (链 exit 0 不经 wrapper 告警, 必须自己送)
    _degraded_summary(ctx)


def _write_report_and_alert(ctx: PipelineContext) -> None:
    ctx.log("--- Report (data-health) ---")
    (REPO / "data/reports").mkdir(parents=True, exist_ok=True)
    (REPO / "data/audit").mkdir(parents=True, exist_ok=True)
    report_json = REPO / f"data/reports/daily_{ctx.date}.json"
    sla_report = REPO / f"data/audit/watermark_sla_{ctx.date}.json"

    # 2026-07-10 修报告脱钩（历史证据=analysis/gap_root_cause_20260708.md 第四轮节）:
    # 原四阶段硬编码 "OK" 不读任何真实状态 — 任何 degraded 甚至整段阶段未跑, 报告照写 OK。
    # 改为整体口径: degraded_msgs 非空 = DEGRADED_PARTIAL(逐阶段归因由 manifest 的
    # pipeline.stage.* check_pass/check_fail 承担, 报告不重复造第二套归因逻辑, 只保证
    # "有问题时报告绝不显示全 OK"这条底线)。
    _has_degraded = len(ctx.degraded_msgs) > 0
    output = {
        "date": ctx.date,
        "dry_run": int(ctx.dry),
        "log": str(ctx.log_path),
        "scope": "data_foundation (L0/L1/L1k/snapshot)",
        "phase_status": {
            "preflight": "OK" if sla_report.exists() else "ERR",
            "chain": "DEGRADED_PARTIAL" if _has_degraded else "OK",
            "detail": "逐阶段状态见 smartmoney.mart_pipeline_run_manifest pipeline.stage.* 行",
        },
        "degraded_total": len(ctx.degraded_msgs),
        "degraded_msgs": list(ctx.degraded_msgs)[:20],
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

    # regime verdict 旧块已删 (2026-06-23 重设计): services.strategies.regime.regime_state 在地基-reset
    # 时删除 (只剩 .pyc), 旧 daily_update report 的 regime 字段自 reset 起恒为 {error} = 死代码。
    # regime 旧 owner (dossier regime.py / market_perception.regime_engine) 已随重建全删; 未来 regime =
    # 市场感知引擎职责 (market_pulse, master plan B4), 不在本数据底座管线职责内。

    output["alert_flags"] = alert_flags
    output.update(alert_flags)
    report_json.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    ctx.log(f"Report written: {report_json}")

    # 告警送达 (SLA stale = 数据断流, 必须送达)
    active = [k for k in ("sla_warn",) if bool(output.get(k))]
    if active:
        ctx.log(f"Alerts present (active={','.join(active)}); dispatching notification")
        proc = subprocess.run(
            [sys.executable, "-m", "backend.services.notification.dispatcher", "--report", str(report_json)],
            cwd=str(REPO), capture_output=True, text=True, env=ctx._subprocess_env(),
            pass_fds=ctx._subprocess_pass_fds())
        if ctx._log_fh:
            ctx._log_fh.write((proc.stdout or "") + (proc.stderr or "")); ctx._log_fh.flush()
        if proc.returncode != 0:
            ctx.degraded("notification dispatch failed")
    else:
        ctx.log("No notification alerts")


def _degraded_summary(ctx: PipelineContext) -> None:
    from .context import DEGRADED_FLAG
    if DEGRADED_FLAG.exists():
        msgs = DEGRADED_FLAG.read_text().strip().splitlines()
        n = len(msgs)
        ctx.log(f"DEGRADED SUMMARY: 本次 {n} 步降级 (明细 {DEGRADED_FLAG}):")
        for m in msgs:
            ctx.log(f"  {m}")
        try:
            subprocess.run(["osascript", "-e",
                            f'display notification "daily_update {n} 步降级, 详见 ALERT flag" '
                            f'with title "ChunkyMonkey degraded"'], capture_output=True)
        except Exception:  # noqa: BLE001
            pass
    else:
        ctx.log("degraded: 0 步")
