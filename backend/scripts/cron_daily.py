"""每日单入口编排 (W5).

Phase 拆分:
  1) sync       — 调 /api/inst/update/all (HTTP, 让 backend DAG 跑); 跳过若 --skip-sync
  2) lineage    — refresh_all_lineage_state(); 同步 registry 声明到 mart_lineage
  3) watermarks — 刷新源域水位 mart_data_source_watermark
  4) topk       — lifecycle champion 每日 TopK; 可选 shadow TopK
  5) health     — scripts/data_health_snapshot.py main(); 写 mart_data_health
  6) drift      — scripts/compute_feature_drift.py main(); 写 mart_feature_drift
  7) audit      — backend/scripts/audit_stale_references.py 走一遍 (CI gate)

每个 phase 失败后:
  - 默认继续下一 phase (best-effort)
  - --strict: 任一失败立刻退出
  - 退出码: 0 全成功; 1 普通失败; 2 critical (drift critical / 红色等级)

用法:
  # 完整一日
  python3 backend/scripts/cron_daily.py
  # 跳过 sync (sync 已经手动跑过)
  python3 backend/scripts/cron_daily.py --skip-sync
  # 只跑生产推荐与监控
  python3 backend/scripts/cron_daily.py --only watermarks,topk,health,drift
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron-daily")

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

DEFAULT_API = os.environ.get("CM_API", "http://127.0.0.1:8000")


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on", "running"}


def _phase_status_from_rc(phase: str, rc) -> dict:
    """Normalize child-script return codes into cron phase status.

    data_health_snapshot returns 1 for red tables. compute_feature_drift
    returns 2 for critical drift and 1 when drift could not be computed.
    """
    try:
        rc_int = int(rc or 0)
    except Exception:
        rc_int = 1
    if rc_int == 0:
        return {"phase": phase, "status": "ok", "rc": 0}
    if phase == "health" or (phase == "drift" and rc_int >= 2):
        return {"phase": phase, "status": "critical", "rc": rc_int}
    return {"phase": phase, "status": "warn", "rc": rc_int}


def _phase_exit_severity(result: dict) -> int:
    """0=success/skipped, 1=ordinary issue, 2=critical issue."""
    status = str(result.get("status") or "").lower()
    if status in {"ok", "skipped"}:
        return 0
    if status == "critical":
        return 2
    return 1


def _sync_status_from_backend(status: Optional[dict]) -> tuple[str, Optional[str]]:
    if not isinstance(status, dict):
        return "failed", "backend 未返回有效 status"
    steps = status.get("steps") or []
    if not isinstance(steps, list):
        return "ok", None
    bad = [
        s for s in steps
        if isinstance(s, dict) and (s.get("status") or "") in {"failed", "blocked", "stopped"}
    ]
    if bad:
        labels = [
            f"{s.get('step_id') or s.get('step_name')}: {s.get('status')}"
            for s in bad[:5]
        ]
        return "failed", "; ".join(labels)
    partial = [
        s for s in steps
        if isinstance(s, dict) and (s.get("status") or "") == "partial"
    ]
    if partial:
        labels = [
            f"{s.get('step_id') or s.get('step_name')}: partial"
            for s in partial[:5]
        ]
        return "warn", "; ".join(labels)
    return "ok", None


# ─────────────────────────────────────────────────────────────────────
# Phase 实现
# ─────────────────────────────────────────────────────────────────────


def phase_sync(*, api: str, timeout_s: int = 3600) -> dict:
    """Phase 1: HTTP POST /api/inst/update/all + 轮询 status 直到 done.

    依赖 backend server 运行 (start.command 启动). 若 server down,
    跳过 (返回 status='skipped' 不报错).
    """
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        return {"phase": "sync", "status": "failed", "reason": "urllib missing"}

    # 探测 server
    try:
        with urllib.request.urlopen(f"{api}/api/inst/update/status", timeout=3) as r:
            r.read()
    except Exception as exc:
        log.warning(f"[sync] backend server 不可达 ({api}): {exc}")
        return {"phase": "sync", "status": "skipped", "reason": str(exc)}

    # 触发更新
    try:
        req = urllib.request.Request(f"{api}/api/inst/update/all", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        if not result.get("ok", True):
            log.warning(f"[sync] backend 拒绝触发: {result}")
            return {"phase": "sync", "status": "rejected", "reason": result.get("message")}
    except urllib.error.HTTPError as exc:
        log.error(f"[sync] HTTP {exc.code}: {exc.reason}")
        return {"phase": "sync", "status": "failed", "reason": f"HTTP {exc.code}"}
    except Exception as exc:
        log.error(f"[sync] trigger failed: {exc}")
        return {"phase": "sync", "status": "failed", "reason": str(exc)}

    # 轮询 status
    t0 = time.time()
    last_summary = None
    while time.time() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(f"{api}/api/inst/update/status", timeout=10) as r:
                status = json.loads(r.read())
            running = _coerce_bool(status.get("running", status.get("is_running", False)))
            last_summary = status
            if not running:
                break
            time.sleep(15)
        except Exception as exc:
            log.warning(f"[sync] poll failed: {exc} (重试)")
            time.sleep(30)
    else:
        return {"phase": "sync", "status": "timeout", "elapsed_s": time.time() - t0}

    elapsed = time.time() - t0
    final_status, final_reason = _sync_status_from_backend(last_summary)
    log.info(f"[sync] done in {elapsed:.0f}s status={final_status}")
    return {
        "phase": "sync", "status": final_status,
        "elapsed_s": elapsed,
        "last_summary": last_summary,
        "reason": final_reason,
    }


def phase_lineage() -> dict:
    """Phase 2: 同步 lineage registry 声明到 mart_lineage."""
    try:
        from services.data_lineage.run import refresh_all_lineage_state
        n = refresh_all_lineage_state()
        log.info(f"[lineage] refreshed {n} lineage entries")
        return {"phase": "lineage", "status": "ok", "rows": n}
    except Exception as exc:
        log.exception("[lineage] failed")
        return {"phase": "lineage", "status": "failed", "reason": str(exc)}


def _run_with_clean_argv(fn, fake_argv: list[str]):
    """子任务内部 argparse 会读 sys.argv. 暂时替换 sys.argv 防 cron_daily 自己的
    flag 透传过去 (此前导致 drift/health 静默 SystemExit rc=2)."""
    saved = sys.argv
    try:
        sys.argv = fake_argv
        return fn()
    finally:
        sys.argv = saved


def phase_health() -> dict:
    """Phase 3: data_health_snapshot 写一份每日快照."""
    try:
        from scripts.data_health_snapshot import main as health_main
        rc = _run_with_clean_argv(health_main, ["data_health_snapshot.py"])  # 0/1/2
        return _phase_status_from_rc("health", rc)
    except SystemExit as exc:
        return _phase_status_from_rc("health", exc.code)
    except Exception as exc:
        log.exception("[health] failed")
        return {"phase": "health", "status": "failed", "reason": str(exc)}


def phase_drift() -> dict:
    """Phase 4: 算特征 drift, 写 mart_feature_drift."""
    try:
        from scripts.compute_feature_drift import main as drift_main
        rc = _run_with_clean_argv(drift_main, ["compute_feature_drift.py"])
        return _phase_status_from_rc("drift", rc)
    except SystemExit as exc:
        return _phase_status_from_rc("drift", exc.code)
    except Exception as exc:
        log.exception("[drift] failed")
        return {"phase": "drift", "status": "failed", "reason": str(exc)}


def phase_watermarks() -> dict:
    """Phase 3: 源域水位 / fallback 状态."""
    try:
        from services.db import get_conn
        from services.source_watermarks import refresh_known_source_watermarks

        conn = get_conn()
        try:
            items = refresh_known_source_watermarks(conn)
        finally:
            conn.close()
        fallback_active = sum(1 for item in items if item.get("fallback_active"))
        failures = sum(1 for item in items if int(item.get("consecutive_failures") or 0) > 0)
        status = "warn" if failures else "ok"
        return {
            "phase": "watermarks",
            "status": status,
            "domains": len(items),
            "fallback_active": fallback_active,
            "failures": failures,
        }
    except Exception as exc:
        log.exception("[watermarks] failed")
        return {"phase": "watermarks", "status": "failed", "reason": str(exc)}


def phase_topk(*, top_k: int, shadow_model_id: str | None = None) -> dict:
    """Phase 4: 只写 lifecycle champion 到正式推荐; shadow 必须显式传模型."""
    try:
        from scripts.run_daily_topk import main as topk_main

        _run_with_clean_argv(
            topk_main,
            ["run_daily_topk.py", "--top-k", str(top_k), "--track-id", "primary", "--is-primary"],
        )
        shadow_status = "skipped"
        if shadow_model_id:
            _run_with_clean_argv(
                topk_main,
                [
                    "run_daily_topk.py",
                    "--model-id", shadow_model_id,
                    "--mode", "shadow",
                    "--top-k", str(top_k),
                    "--track-id", f"shadow_{shadow_model_id}",
                ],
            )
            shadow_status = "ok"
        return {
            "phase": "topk",
            "status": "ok",
            "top_k": top_k,
            "shadow": shadow_status,
            "shadow_model_id": shadow_model_id,
        }
    except SystemExit as exc:
        return {"phase": "topk", "status": "failed", "rc": exc.code}
    except Exception as exc:
        log.exception("[topk] failed")
        return {"phase": "topk", "status": "failed", "reason": str(exc)}


def phase_audit() -> dict:
    """Phase 5: stale references audit. CI gate."""
    try:
        result = subprocess.run(
            [sys.executable, str(BACKEND / "scripts" / "audit_stale_references.py")],
            capture_output=True, text=True, timeout=120,
        )
        ok = result.returncode == 0
        # audit_stale_references.py 目前 0=clean/warn-only, 1=critical.
        return {
            "phase": "audit",
            "status": "ok" if ok else "critical",
            "rc": result.returncode,
            "stdout_tail": result.stdout.splitlines()[-3:] if result.stdout else [],
        }
    except Exception as exc:
        log.exception("[audit] failed")
        return {"phase": "audit", "status": "failed", "reason": str(exc)}


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

ALL_PHASES = ["sync", "lineage", "watermarks", "topk", "health", "drift", "audit"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--skip-sync", action="store_true", help="不调 /update/all")
    parser.add_argument("--skip-topk", action="store_true", help="不生成每日推荐")
    parser.add_argument("--top-k", type=int, default=50, help="每日 champion TopK 数量")
    parser.add_argument("--shadow-model-id", default=None, help="显式影子模型 ID; 不传则不写 shadow TopK")
    parser.add_argument("--only", help="逗号分隔: sync,lineage,watermarks,topk,health,drift,audit")
    parser.add_argument("--strict", action="store_true", help="任一 phase 失败即退出")
    parser.add_argument("--sync-timeout", type=int, default=3600)
    args = parser.parse_args()

    selected = ALL_PHASES if not args.only else [p.strip() for p in args.only.split(",")]
    if args.skip_sync and "sync" in selected:
        selected = [p for p in selected if p != "sync"]
    if args.skip_topk and "topk" in selected:
        selected = [p for p in selected if p != "topk"]

    log.info(f"=== cron_daily 开始 phases={selected} ===")
    t0 = time.time()
    results = []
    exit_severity = 0

    for phase in selected:
        log.info(f"--- phase: {phase} ---")
        if phase == "sync":
            r = phase_sync(api=args.api, timeout_s=args.sync_timeout)
        elif phase == "lineage":
            r = phase_lineage()
        elif phase == "watermarks":
            r = phase_watermarks()
        elif phase == "topk":
            r = phase_topk(top_k=args.top_k, shadow_model_id=args.shadow_model_id)
        elif phase == "health":
            r = phase_health()
        elif phase == "drift":
            r = phase_drift()
        elif phase == "audit":
            r = phase_audit()
        else:
            log.warning(f"unknown phase: {phase}")
            continue

        results.append(r)
        log.info(f"--- {phase}: {r.get('status')} ---")
        phase_severity = _phase_exit_severity(r)
        exit_severity = max(exit_severity, phase_severity)
        if phase_severity:
            if args.strict:
                log.error(f"strict 模式 — phase {phase} 状态 {r.get('status')}, 立刻退出")
                break

    elapsed = time.time() - t0
    log.info(f"=== cron_daily 结束 ({elapsed:.0f}s) ===")
    log.info(json.dumps({"phases": results, "elapsed_s": elapsed}, indent=2, ensure_ascii=False))

    return 2 if exit_severity >= 2 else (1 if exit_severity else 0)


if __name__ == "__main__":
    raise SystemExit(main())
