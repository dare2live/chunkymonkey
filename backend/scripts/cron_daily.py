"""每日单入口编排 (W5).

Phase 拆分:
  1) sync       — 调 /api/update/all (HTTP, 让 backend DAG 跑); 跳过若 --skip-sync
  2) lineage    — refresh_all_lineage_state(); 同步 registry 声明到 mart_lineage
  3) health     — scripts/data_health_snapshot.py main(); 写 mart_data_health
  4) drift      — scripts/compute_feature_drift.py main(); 写 mart_feature_drift
  5) audit      — backend/scripts/audit_stale_references.py 走一遍 (CI gate)

每个 phase 失败后:
  - 默认继续下一 phase (best-effort)
  - --strict: 任一失败立刻退出
  - 退出码: 0 全成功; 1 普通失败; 2 critical (drift critical / 红色等级)

用法:
  # 完整一日
  python3 backend/scripts/cron_daily.py
  # 跳过 sync (sync 已经手动跑过)
  python3 backend/scripts/cron_daily.py --skip-sync
  # 只跑后置 (健康 + drift)
  python3 backend/scripts/cron_daily.py --only health,drift
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


# ─────────────────────────────────────────────────────────────────────
# Phase 实现
# ─────────────────────────────────────────────────────────────────────


def phase_sync(*, api: str, timeout_s: int = 3600) -> dict:
    """Phase 1: HTTP POST /api/update/all + 轮询 status 直到 done.

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
        with urllib.request.urlopen(f"{api}/api/update/status", timeout=3) as r:
            r.read()
    except Exception as exc:
        log.warning(f"[sync] backend server 不可达 ({api}): {exc}")
        return {"phase": "sync", "status": "skipped", "reason": str(exc)}

    # 触发更新
    try:
        req = urllib.request.Request(f"{api}/api/update/all", method="POST", data=b"")
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
            with urllib.request.urlopen(f"{api}/api/update/status", timeout=10) as r:
                status = json.loads(r.read())
            running = status.get("is_running", False)
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
    log.info(f"[sync] done in {elapsed:.0f}s")
    return {
        "phase": "sync", "status": "ok",
        "elapsed_s": elapsed,
        "last_summary": last_summary,
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
        return {"phase": "health", "status": "ok" if rc == 0 else "warn", "rc": rc}
    except SystemExit as exc:
        return {"phase": "health", "status": "ok" if exc.code == 0 else "warn", "rc": exc.code}
    except Exception as exc:
        log.exception("[health] failed")
        return {"phase": "health", "status": "failed", "reason": str(exc)}


def phase_drift() -> dict:
    """Phase 4: 算特征 drift, 写 mart_feature_drift."""
    try:
        from scripts.compute_feature_drift import main as drift_main
        rc = _run_with_clean_argv(drift_main, ["compute_feature_drift.py"])
        return {"phase": "drift", "status": "ok" if rc == 0 else "warn", "rc": rc}
    except SystemExit as exc:
        return {"phase": "drift", "status": "ok" if exc.code == 0 else "warn", "rc": exc.code}
    except Exception as exc:
        log.exception("[drift] failed")
        return {"phase": "drift", "status": "failed", "reason": str(exc)}


def phase_audit() -> dict:
    """Phase 5: stale references audit. CI gate."""
    try:
        result = subprocess.run(
            [sys.executable, str(BACKEND / "scripts" / "audit_stale_references.py")],
            capture_output=True, text=True, timeout=120,
        )
        ok = result.returncode == 0
        # audit 退出码: 0=clean / 1=warn-only / 2=critical
        return {
            "phase": "audit",
            "status": "ok" if ok else ("warn" if result.returncode == 1 else "failed"),
            "rc": result.returncode,
            "stdout_tail": result.stdout.splitlines()[-3:] if result.stdout else [],
        }
    except Exception as exc:
        log.exception("[audit] failed")
        return {"phase": "audit", "status": "failed", "reason": str(exc)}


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────

ALL_PHASES = ["sync", "lineage", "health", "drift", "audit"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--skip-sync", action="store_true", help="不调 /update/all")
    parser.add_argument("--only", help="逗号分隔: sync,lineage,health,drift,audit")
    parser.add_argument("--strict", action="store_true", help="任一 phase 失败即退出")
    parser.add_argument("--sync-timeout", type=int, default=3600)
    args = parser.parse_args()

    selected = ALL_PHASES if not args.only else [p.strip() for p in args.only.split(",")]
    if args.skip_sync and "sync" in selected:
        selected = [p for p in selected if p != "sync"]

    log.info(f"=== cron_daily 开始 phases={selected} ===")
    t0 = time.time()
    results = []
    has_critical = False

    for phase in selected:
        log.info(f"--- phase: {phase} ---")
        if phase == "sync":
            r = phase_sync(api=args.api, timeout_s=args.sync_timeout)
        elif phase == "lineage":
            r = phase_lineage()
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
        if r.get("status") == "failed":
            has_critical = True
            if args.strict:
                log.error(f"strict 模式 — phase {phase} 失败, 立刻退出")
                break

    elapsed = time.time() - t0
    log.info(f"=== cron_daily 结束 ({elapsed:.0f}s) ===")
    log.info(json.dumps({"phases": results, "elapsed_s": elapsed}, indent=2, ensure_ascii=False))

    return 2 if has_critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
