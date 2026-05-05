"""每日单入口编排 (W5).

Phase 拆分:
  1) sync       — 默认调 /api/inst/update/smart (HTTP, 每日生产增量); 跳过若 --skip-sync
  2) lineage    — refresh_all_lineage_state(); 同步 registry 声明到 mart_lineage
  3) watermarks — 刷新源域水位 mart_data_source_watermark
  4) topk       — lifecycle champion 每日 TopK; 可选 shadow TopK
  5) health     — scripts/data_health_snapshot.py main(); 写 mart_data_health
  6) drift      — scripts/compute_feature_drift.py main(); 写 mart_feature_drift
  7) audit      — backend/scripts/audit_stale_references.py 走一遍 (CI gate)

每个 phase 失败后:
  - 默认继续下一 phase (best-effort)
  - sync 超时或被拒绝时会请求 backend stop, 并阻断后续 DuckDB phase
  - --strict: 任一失败立刻退出
  - 退出码: 0 全成功; 1 普通失败; 2 critical (drift critical / 红色等级)

用法:
  # 完整一日
  python3 backend/scripts/cron_daily.py
  # 手动全量回填 (非每日默认)
  python3 backend/scripts/cron_daily.py --full-sync
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
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cron-daily")

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

DEFAULT_API = os.environ.get("CM_API", "http://127.0.0.1:8000")
SYNC_BLOCKING_STATUSES = {"rejected", "timeout", "stale_running"}
CRON_LOCK_NAME = "cron_daily"


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
    if result.get("phase") == "sync" and status in SYNC_BLOCKING_STATUSES:
        return 2
    if status == "critical":
        return 2
    return 1


def _sync_failure_blocks_followups(result: dict) -> bool:
    """Whether it is unsafe to run local DB phases after sync."""
    if result.get("phase") != "sync":
        return False
    return str(result.get("status") or "").lower() in SYNC_BLOCKING_STATUSES


def _request_sync_stop(api: str, *, reason: str, timeout: int = 5) -> dict:
    try:
        import urllib.request
    except ImportError:
        return {"ok": False, "reason": "urllib missing"}
    try:
        payload = json.dumps({"reason": reason}).encode("utf-8")
        req = urllib.request.Request(
            f"{api}/api/inst/update/stop",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        return {"ok": True, "response": body.decode("utf-8", errors="replace")[:500]}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _parse_status_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _seconds_since_status_time(value: Optional[str], *, now: Optional[datetime] = None) -> Optional[float]:
    parsed = _parse_status_datetime(value)
    if not parsed:
        return None
    if now is not None:
        current = now
    elif parsed.tzinfo:
        current = datetime.now(parsed.tzinfo)
    else:
        current = datetime.now()
    try:
        return max(0.0, (current - parsed).total_seconds())
    except TypeError:
        return max(0.0, (datetime.now() - parsed.replace(tzinfo=None)).total_seconds())


def _stale_running_reason(status: Optional[dict], *, stale_after_s: int, now: Optional[datetime] = None) -> Optional[str]:
    if not isinstance(status, dict) or stale_after_s <= 0:
        return None
    if not _coerce_bool(status.get("running", status.get("is_running", False))):
        return None
    ctx = status.get("run_context") or {}
    heartbeat_at = ctx.get("heartbeat_at") or ctx.get("started_at")
    age = _seconds_since_status_time(heartbeat_at, now=now)
    if age is None:
        return None
    if age > stale_after_s:
        step_id = ctx.get("step_id") or ctx.get("step_name") or "unknown"
        return f"backend update heartbeat stale for {age:.0f}s at step {step_id}"
    return None


def _fetch_update_status(api: str, *, timeout: int = 3) -> dict:
    import urllib.request

    with urllib.request.urlopen(f"{api}/api/inst/update/status", timeout=timeout) as r:
        return json.loads(r.read())


def _wait_backend_idle(api: str, *, timeout_s: int, poll_s: float = 2.0) -> dict:
    deadline = time.time() + max(0, timeout_s)
    last_status = None
    while time.time() <= deadline:
        try:
            last_status = _fetch_update_status(api, timeout=3)
            if not _coerce_bool(last_status.get("running", last_status.get("is_running", False))):
                return {"ok": True, "status": last_status}
        except Exception as exc:
            last_status = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        time.sleep(max(0.1, poll_s))
    return {"ok": False, "status": last_status, "reason": f"backend still running after {timeout_s}s grace"}


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


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


# ─────────────────────────────────────────────────────────────────────
# Phase 实现
# ─────────────────────────────────────────────────────────────────────


def phase_sync(
    *,
    api: str,
    timeout_s: int = 3600,
    full_sync: bool = False,
    critical_sync_only: bool = True,
    stale_heartbeat_s: int = 300,
    stop_grace_s: int = 30,
    poll_interval_s: float = 5.0,
) -> dict:
    """Phase 1: HTTP POST backend update endpoint + 轮询 status 直到 done.

    依赖 backend server 运行 (start.command 启动). 若 server down,
    跳过 (返回 status='skipped' 不报错).
    """
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        return {"phase": "sync", "status": "failed", "reason": "urllib missing"}

    # 探测 server / 清理 stale updater
    try:
        initial_status = _fetch_update_status(api, timeout=3)
    except Exception as exc:
        log.warning(f"[sync] backend server 不可达 ({api}): {exc}")
        return {"phase": "sync", "status": "skipped", "reason": str(exc)}

    stale_reason = _stale_running_reason(initial_status, stale_after_s=stale_heartbeat_s)
    if stale_reason:
        stop_result = _request_sync_stop(api, reason=f"cron_daily stale sync guard: {stale_reason}")
        idle_result = _wait_backend_idle(api, timeout_s=stop_grace_s)
        if not idle_result.get("ok"):
            log.error("[sync] stale backend update still running: %s", idle_result)
            return {
                "phase": "sync",
                "status": "stale_running",
                "reason": stale_reason,
                "stop_result": stop_result,
                "idle_result": idle_result,
                "last_summary": initial_status,
            }
        log.warning("[sync] cleared stale backend update before starting daily sync: %s", stale_reason)
    elif _coerce_bool(initial_status.get("running", initial_status.get("is_running", False))):
        return {
            "phase": "sync",
            "status": "rejected",
            "reason": "backend update already running",
            "last_summary": initial_status,
        }

    trigger_path = "/api/inst/update/all" if full_sync else "/api/inst/update/smart"
    if not full_sync and critical_sync_only:
        trigger_path += "?critical_only=true"

    # 触发更新
    try:
        req = urllib.request.Request(f"{api}{trigger_path}", method="POST", data=b"")
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
            status = _fetch_update_status(api, timeout=10)
            running = _coerce_bool(status.get("running", status.get("is_running", False)))
            last_summary = status
            if not running:
                break
            time.sleep(max(1.0, poll_interval_s))
        except Exception as exc:
            log.warning(f"[sync] poll failed: {exc} (重试)")
            time.sleep(max(2.0, poll_interval_s * 2))
    else:
        elapsed = time.time() - t0
        stop_result = _request_sync_stop(api, reason=f"cron_daily sync timeout after {elapsed:.0f}s")
        log.error("[sync] timeout after %.0fs; stop_result=%s", elapsed, stop_result)
        return {
            "phase": "sync",
            "status": "timeout",
            "elapsed_s": elapsed,
            "trigger_path": trigger_path,
            "stop_result": stop_result,
        }

    elapsed = time.time() - t0
    final_status, final_reason = _sync_status_from_backend(last_summary)
    log.info(f"[sync] done in {elapsed:.0f}s status={final_status}")
    return {
        "phase": "sync", "status": final_status,
        "elapsed_s": elapsed,
        "trigger_path": trigger_path,
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


def phase_topk(
    *,
    top_k: int,
    shadow_model_id: str | None = None,
    include_risk_summary: bool = False,
) -> dict:
    """Phase 4: 只写 lifecycle champion 到正式推荐; shadow 必须显式传模型."""
    try:
        from scripts.run_daily_topk import main as topk_main

        _run_with_clean_argv(
            topk_main,
            [
                "run_daily_topk.py",
                "--top-k", str(top_k),
                "--track-id", "primary",
                "--is-primary",
                "--quiet-preview",
                *(["--skip-risk-summary"] if not include_risk_summary else []),
            ],
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
                    "--quiet-preview",
                    *(["--skip-risk-summary"] if not include_risk_summary else []),
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


def _record_cron_manifest(
    *,
    run_id: str,
    started_at: str,
    elapsed_s: float,
    results: list[dict],
    exit_severity: int,
    lock_summary: Optional[dict] = None,
) -> None:
    try:
        from services.db import get_conn
        from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso

        status = "success" if exit_severity == 0 else ("critical" if exit_severity >= 2 else "warn")
        conn = get_conn()
        try:
            record_pipeline_run(
                conn,
                run_id=run_id,
                pipeline_name="cron_daily",
                status=status,
                started_at=started_at,
                ended_at=utc_now_iso(),
                duration_s=elapsed_s,
                commit_sha=git_commit_sha(REPO),
                input_tables=[
                    "dim_active_a_stock",
                    "fact_feature_panel",
                    "mart_model_lifecycle",
                    "mart_multidim_model",
                ],
                output_tables=[
                    "mart_pipeline_lock",
                    "mart_data_source_watermark",
                    "mart_data_source_failure_queue",
                    "mart_daily_recommendation",
                    "mart_daily_topk_view_cache",
                    "mart_data_health",
                    "mart_feature_drift",
                    "mart_feature_drift_histogram",
                ],
                model_id=None,
                feature_group="daily_production",
                gate_result=status,
                blockers=[
                    f"{r.get('phase')}:{r.get('status')}"
                    for r in results
                    if _phase_exit_severity(r) > 0
                ],
                perf_summary={
                    "phases": _json_safe(results),
                    "exit_severity": exit_severity,
                    "pipeline_lock": _json_safe(lock_summary),
                },
            )
        finally:
            conn.close()
    except Exception as exc:
        log.warning("[manifest] cron_daily record failed: %s", exc)


def _acquire_cron_pipeline_lock(*, run_id: str, stale_after_s: int, metadata: Optional[dict] = None) -> dict:
    from services.db import get_conn
    from services.pipeline_lock import acquire_pipeline_lock
    from services.schema_versions import record_actual_version

    conn = get_conn(timeout=30)
    try:
        summary = acquire_pipeline_lock(
            conn,
            lock_name=CRON_LOCK_NAME,
            owner_run_id=run_id,
            phase="startup",
            stale_after_s=stale_after_s,
            metadata=metadata,
        )
        record_actual_version(conn, "mart_pipeline_lock")
        conn.commit()
        return summary
    finally:
        conn.close()


def _heartbeat_cron_pipeline_lock(*, run_id: str, phase: str) -> None:
    try:
        from services.db import get_conn
        from services.pipeline_lock import heartbeat_pipeline_lock

        conn = get_conn(timeout=30)
        try:
            heartbeat_pipeline_lock(conn, lock_name=CRON_LOCK_NAME, owner_run_id=run_id, phase=phase, commit=True)
        finally:
            conn.close()
    except Exception as exc:
        log.warning("[lock] heartbeat failed for phase=%s: %s", phase, exc)


def _release_cron_pipeline_lock(*, run_id: str, status: str) -> dict:
    try:
        from services.db import get_conn
        from services.pipeline_lock import release_pipeline_lock

        conn = get_conn(timeout=30)
        try:
            released = release_pipeline_lock(
                conn,
                lock_name=CRON_LOCK_NAME,
                owner_run_id=run_id,
                status=status,
                commit=True,
            )
            return {"released": released, "status": status}
        finally:
            conn.close()
    except Exception as exc:
        log.warning("[lock] release failed: %s", exc)
        return {"released": False, "status": status, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--skip-sync", action="store_true", help="不调 backend update")
    parser.add_argument("--full-sync", action="store_true", help="非每日默认: 调 /update/all 跑完整 DAG")
    parser.add_argument("--include-non-critical-sync", action="store_true", help="每日 smart sync 也执行 dashboard/research 派生 step")
    parser.add_argument("--sync-poll-interval", type=float, default=5.0, help="sync status 轮询间隔秒数")
    parser.add_argument("--skip-topk", action="store_true", help="不生成每日推荐")
    parser.add_argument("--top-k", type=int, default=50, help="每日 champion TopK 数量")
    parser.add_argument("--include-topk-risk-summary", action="store_true", help="每日 TopK 同步刷新风险摘要")
    parser.add_argument("--shadow-model-id", default=None, help="显式影子模型 ID; 不传则不写 shadow TopK")
    parser.add_argument("--only", help="逗号分隔: sync,lineage,watermarks,topk,health,drift,audit")
    parser.add_argument("--strict", action="store_true", help="任一 phase 失败即退出")
    parser.add_argument("--sync-timeout", type=int, default=3600)
    parser.add_argument("--stale-heartbeat-seconds", type=int, default=300, help="backend update heartbeat 超过该秒数视为 stale")
    parser.add_argument("--stop-grace-seconds", type=int, default=30, help="发现 stale updater 后等待停止的秒数")
    parser.add_argument("--pipeline-lock-stale-seconds", type=int, default=600, help="cron_daily pipeline lock heartbeat 超过该秒数视为 stale")
    args = parser.parse_args()

    selected = ALL_PHASES if not args.only else [p.strip() for p in args.only.split(",")]
    if args.skip_sync and "sync" in selected:
        selected = [p for p in selected if p != "sync"]
    if args.skip_topk and "topk" in selected:
        selected = [p for p in selected if p != "topk"]

    log.info(f"=== cron_daily 开始 phases={selected} ===")
    t0 = time.time()
    started_at = datetime.utcnow().isoformat(timespec="seconds")
    run_id = f"cron_daily_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    results = []
    exit_severity = 0
    lock_summary = None

    try:
        lock_summary = _acquire_cron_pipeline_lock(
            run_id=run_id,
            stale_after_s=args.pipeline_lock_stale_seconds,
            metadata={"phases": selected, "full_sync": args.full_sync},
        )
    except Exception as exc:
        active_lock = _json_safe(getattr(exc, "active_lock", None))
        r = {
            "phase": "pipeline_lock",
            "status": "critical",
            "reason": str(exc),
            "active_lock": active_lock,
        }
        results.append(r)
        elapsed = time.time() - t0
        _record_cron_manifest(
            run_id=run_id,
            started_at=started_at,
            elapsed_s=elapsed,
            results=results,
            exit_severity=2,
            lock_summary={"acquired": False, "error": str(exc), "active_lock": active_lock},
        )
        log.error("[lock] cron_daily pipeline lock unavailable: %s", exc)
        return 2

    try:
        for phase in selected:
            log.info(f"--- phase: {phase} ---")
            _heartbeat_cron_pipeline_lock(run_id=run_id, phase=phase)
            if lock_summary is not None:
                lock_summary["last_phase"] = phase
            phase_t0 = time.time()
            if phase == "sync":
                r = phase_sync(
                    api=args.api,
                    timeout_s=args.sync_timeout,
                    full_sync=args.full_sync,
                    critical_sync_only=not args.include_non_critical_sync,
                    stale_heartbeat_s=args.stale_heartbeat_seconds,
                    stop_grace_s=args.stop_grace_seconds,
                    poll_interval_s=args.sync_poll_interval,
                )
            elif phase == "lineage":
                r = phase_lineage()
            elif phase == "watermarks":
                r = phase_watermarks()
            elif phase == "topk":
                r = phase_topk(
                    top_k=args.top_k,
                    shadow_model_id=args.shadow_model_id,
                    include_risk_summary=args.include_topk_risk_summary,
                )
            elif phase == "health":
                r = phase_health()
            elif phase == "drift":
                r = phase_drift()
            elif phase == "audit":
                r = phase_audit()
            else:
                log.warning(f"unknown phase: {phase}")
                continue

            r.setdefault("phase_elapsed_s", round(time.time() - phase_t0, 3))
            results.append(r)
            log.info(f"--- {phase}: {r.get('status')} ---")
            phase_severity = _phase_exit_severity(r)
            exit_severity = max(exit_severity, phase_severity)
            if phase_severity:
                if _sync_failure_blocks_followups(r):
                    log.error(
                        "sync 状态 %s，跳过剩余 phase，避免后台更新仍在运行时争抢 DuckDB 锁",
                        r.get("status"),
                    )
                    break
                if args.strict:
                    log.error(f"strict 模式 — phase {phase} 状态 {r.get('status')}, 立刻退出")
                    break
    finally:
        release_summary = _release_cron_pipeline_lock(
            run_id=run_id,
            status="released_success" if exit_severity == 0 else ("released_critical" if exit_severity >= 2 else "released_warn"),
        )
        if lock_summary is not None:
            lock_summary = {**lock_summary, "release": release_summary}

    elapsed = time.time() - t0
    _record_cron_manifest(
        run_id=run_id,
        started_at=started_at,
        elapsed_s=elapsed,
        results=results,
        exit_severity=exit_severity,
        lock_summary=lock_summary,
    )
    log.info(f"=== cron_daily 结束 ({elapsed:.0f}s) ===")
    log.info(json.dumps({"phases": results, "elapsed_s": elapsed}, indent=2, ensure_ascii=False))

    return 2 if exit_severity >= 2 else (1 if exit_severity else 0)


if __name__ == "__main__":
    raise SystemExit(main())
