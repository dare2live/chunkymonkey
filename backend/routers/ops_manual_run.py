"""手动任务触发 router — 2026-06-12 用户决议: 自动调度退役, 更新类任务改前端按钮手动触发.

设计:
- 一个 job 注册条目 = 一个可手动触发的链 (argv + 日志/flag 路径), 新任务加条目零端点代码
  (与 sync_registry 同范式, 防每任务一个端点的发散)。
- 触发 = detached spawn (start_new_session): API 进程重启不杀链; 告警链复用
  manual_job_wrapper (失败写 /tmp/chunkymonkey_ALERT_<job>.flag + macOS 通知, 成功自清)。
- 状态 = 受支持 pipeline/sync writer flock 真相 + pgrep 诊断提示 + wrapper flag + 链日志尾部,
  全部只读, 不碰 DuckDB
  (状态接口任何时刻可调, 不与运行中的链抢锁)。
- current_activity = 从 log_tail 解析的可读「正在: …」摘要 (UI 可观测性; 非业务真相)。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from services.writer_lock import writer_lock_status

router = APIRouter()

_REPO = Path(__file__).resolve().parents[2]
_PY = _REPO / ".venv" / "bin" / "python"
_WRAPPER = _REPO / "scripts" / "manual_job_wrapper.py"

_CHUNKYCTL = _REPO / "scripts" / "chunkyctl"

# Parameterized S1/S2 (Capability E residual). Narrow whitelist — not a second DAG.
_LAND_ACCEPT_DOMAINS = frozenset({"daily", "stock_st"})
_LAND_ACCEPT_MODES = frozenset(
    {"land_only", "land_then_accept", "accept_from_landing"}
)
_LAND_ACCEPT_DATE_RE = re.compile(r"^\d{8}$")
_LAND_ACCEPT_MAX_WINDOW_DAYS = 40  # rule-compliance: ok evidence=≤40d hard gate mirrors sync CLI

# job 注册表: 名称 -> 定义 (argv 不含解释器/wrapper 前缀; pattern 用于 pgrep 活性判定)
MANUAL_JOBS: dict[str, dict[str, Any]] = {
    "daily_update": {
        "argv": ["/bin/bash", str(_REPO / "scripts" / "daily_update.sh")],
        "pattern": "scripts/daily_update.sh",
        "log": "/tmp/chunkymonkey_daily_update.log",
        "extra_flags": ["/tmp/chunkymonkey_ALERT_daily_update_degraded.flag"],
        "label": "数据底座五段手动链 (preflight/获取/清洗/加工/存储)",
    },
    # Capability E: 独立阶段 = chunkyctl pipeline / derive (caller-only; 非第二编排器)
    # sync_land_accept argv is built per request (see run_land_accept); placeholder only.
    "sync_land_accept": {
        "argv": [str(_CHUNKYCTL), "sync", "--domain", "daily", "--land-only",
                 "--start", "19700101", "--end", "19700101"],
        "pattern": "chunkyctl sync --domain",
        "log": "/tmp/chunkymonkey_sync_land_accept.log",
        "extra_flags": [],
        "label": "S1/S2 参数化: land-only / land-then-accept / accept-from-landing",
        "parameterized": True,
    },
    "pipeline_acquire": {
        "argv": [str(_CHUNKYCTL), "pipeline", "acquire"],
        "pattern": "services.pipeline.stage_runner acquire",
        "log": "/tmp/chunkymonkey_pipeline_acquire.log",
        "extra_flags": [],
        "label": "单阶段: 获取 acquire (含日历/授权预检)",
    },
    "pipeline_clean": {
        "argv": [str(_CHUNKYCTL), "pipeline", "clean"],
        "pattern": "services.pipeline.stage_runner clean",
        "log": "/tmp/chunkymonkey_pipeline_clean.log",
        "extra_flags": [],
        "label": "单阶段: 清洗 clean (qfq + data_audit)",
    },
    "pipeline_process": {
        "argv": [str(_CHUNKYCTL), "pipeline", "process"],
        "pattern": "services.pipeline.stage_runner process",
        "log": "/tmp/chunkymonkey_pipeline_process.log",
        "extra_flags": [],
        "label": "单阶段: 加工 process (DC / pulse 等)",
    },
    "pipeline_store": {
        "argv": [str(_CHUNKYCTL), "pipeline", "store"],
        "pattern": "services.pipeline.stage_runner store",
        "log": "/tmp/chunkymonkey_pipeline_store.log",
        "extra_flags": [],
        "label": "单阶段: 存储/治理 store",
    },
    "derive_qfq": {
        "argv": [str(_CHUNKYCTL), "derive", "qfq"],
        "pattern": "derive_cli.py qfq",
        "log": "/tmp/chunkymonkey_derive_qfq.log",
        "extra_flags": [],
        "label": "独立派生: derive qfq (accepted-only)",
    },
    # concept_snapshot (E7) 2026-06-13 物理摘除: 实测 20260610/11 两份快照均 8000 行整
    # 截断 (真实日量 ~90k), observed 事件全是伪影; dc 系历史 tushare 可随时回拉, 快照
    # 是冗余中间层 — 留可点按钮 = 误导 (产物入 fact_concept_event 污染 LF 实验面)。
    # tdx_pool_refresh 2026-07-10 摘除（历史证据=analysis/gap_root_cause_20260708.md
    # 第四轮节): 其 argv 指向的 refresh_tdx_server_pool.py 已随 tdx 全源退役物删, 按钮点击会
    # spawn 注定失败的进程并经 manual_job_wrapper 写 ALERT flag 制造误导告警。check_dead_
    # references 六道扫描仍不覆盖 Python Path 拼接 — tdx 退役残留漏进 SERVE 层的实例。
}

# Workbench step cards (Capability E). Align real CLI/APIs only — no invented DAG.
# runnable=False nodes stay visible with disabled_reason (S1/S2 need domain+dates).
PIPELINE_NODE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "preflight",
        "label": "预检",
        "description": "日历 / sync policy / 授权 — 嵌在 daily_update 与 acquire 内",
        "job": None,
        "runnable": False,
        "disabled_reason": "无独立 safe job；跑「数据更新」或「获取 acquire」时内嵌执行",
    },
    {
        "id": "acquire",
        "label": "获取 acquire",
        "description": "chunkyctl pipeline acquire（含 formal daily/ST catchup + registry drain）",
        "job": "pipeline_acquire",
        "runnable": True,
        "disabled_reason": None,
    },
    {
        "id": "land_accept",
        "label": "S1 land / S2 accept",
        "description": "按域 land-only / accept-from-landing / land-then-accept（参数化）",
        "job": "sync_land_accept",
        "runnable": True,
        "parameterized": True,
        "disabled_reason": None,
        "params_schema": {
            "domains": ["daily", "stock_st"],
            "modes": ["land_only", "land_then_accept", "accept_from_landing"],
            "requires": {
                "land_only": ["domain", "start", "end"],
                "land_then_accept": ["domain", "start", "end"],
                "accept_from_landing": ["domain", "batch_id"],
            },
            "endpoint": "POST /api/v3/ops/pipeline/land-accept/run",
        },
    },
    {
        "id": "clean",
        "label": "清洗 clean",
        "description": "chunkyctl pipeline clean（qfq 派生面 + post-sync data_audit）",
        "job": "pipeline_clean",
        "runnable": True,
        "disabled_reason": None,
    },
    {
        "id": "derive",
        "label": "派生 derive",
        "description": "chunkyctl derive qfq（accepted-only；form 重建仍走 CLI）",
        "job": "derive_qfq",
        "runnable": True,
        "disabled_reason": None,
    },
    {
        "id": "process",
        "label": "加工 process",
        "description": "chunkyctl pipeline process（下游加工 / pulse 等）",
        "job": "pipeline_process",
        "runnable": True,
        "disabled_reason": None,
    },
    {
        "id": "store",
        "label": "存储 store",
        "description": "chunkyctl pipeline store（治理 / continuity / 水位侧）",
        "job": "pipeline_store",
        "runnable": True,
        "disabled_reason": None,
    },
)

# tail 行数: 前端状态卡只展示链尾近况, 全文走日志文件本身
_LOG_TAIL_LINES = 40  # rule-compliance: ok evidence=display-only UI tail, 非业务阈值
# wrapper flag 目录 (测试 monkeypatch 此值隔离, 不碰生产 /tmp flag)
_FLAG_DIR = Path("/tmp")
# 运行中日志无新行超过此秒数 → UI 标注「可能 stdout 缓冲 / 长同步中」
_STALE_LOG_S = 90  # rule-compliance: ok evidence=UI hint only

_PHASE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("preflight", "预检 preflight", re.compile(r"Preflight|Sync execution policy|Calendar foundation|Authorization", re.I)),
    ("acquire", "① 获取 ACQUIRE", re.compile(r"①\s*获取|ACQUIRE|=== .*获取")),
    ("clean", "② 清洗/派生 CLEAN", re.compile(r"②\s*清洗|CLEAN|derive|land_then_accept|accept_", re.I)),
    ("process", "③ 加工 PROCESS", re.compile(r"③\s*加工|PROCESS")),
    ("store", "④ 存储 STORE", re.compile(r"④\s*存储|=== .*STORE|post-acquire Store", re.I)),
    ("fail", "失败 / 阻断", re.compile(r"PREFLIGHT BLOCK|FAIL rc=|DEGRADED:|scope_blocked", re.I)),
)


def _job_or_404(job: str) -> dict[str, Any]:
    spec = MANUAL_JOBS.get(job)
    if spec is None:
        raise HTTPException(404, f"未注册的手动任务 '{job}' (可用: {sorted(MANUAL_JOBS)})")
    return spec


def _is_running(spec: dict[str, Any]) -> bool:
    r = subprocess.run(
        ["pgrep", "-f", spec["pattern"]], capture_output=True, check=False
    )
    return r.returncode == 0


def _spawn(job: str, spec: dict[str, Any], *, argv_override: list[str] | None = None) -> int:
    body_argv = argv_override if argv_override is not None else list(spec["argv"])
    argv = [str(_PY), str(_WRAPPER), job, *body_argv]
    stdout_path = Path(f"/tmp/chunkymonkey_{job}.stdout.log")
    env = os.environ.copy()
    # 链式 Python print 默认块缓冲 → 前端长时间只见「更新中」; 强制行缓冲
    env.setdefault("PYTHONUNBUFFERED", "1")
    with stdout_path.open("ab") as fh:
        proc = subprocess.Popen(
            argv,
            cwd=str(_REPO),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    return proc.pid


def _compact_yyyymmdd(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def build_land_accept_argv(payload: dict[str, Any]) -> list[str]:
    """Validate UI/API params → chunkyctl sync argv (fail-closed whitelist)."""

    domain = str(payload.get("domain") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    if domain not in _LAND_ACCEPT_DOMAINS:
        raise HTTPException(
            400,
            f"domain must be one of {sorted(_LAND_ACCEPT_DOMAINS)}; got {domain!r}",
        )
    if mode not in _LAND_ACCEPT_MODES:
        raise HTTPException(
            400,
            f"mode must be one of {sorted(_LAND_ACCEPT_MODES)}; got {mode!r}",
        )
    argv = [str(_CHUNKYCTL), "sync", "--domain", domain]
    if mode == "accept_from_landing":
        batch_id = str(payload.get("batch_id") or "").strip()
        if not batch_id:
            raise HTTPException(400, "accept_from_landing requires batch_id")
        argv.extend(["--accept-from-landing", "--batch-id", batch_id])
        return argv

    start = _compact_yyyymmdd(payload.get("start"))
    end = _compact_yyyymmdd(payload.get("end"))
    if not _LAND_ACCEPT_DATE_RE.match(start) or not _LAND_ACCEPT_DATE_RE.match(end):
        raise HTTPException(400, "start/end must be YYYYMMDD")
    if start > end:
        raise HTTPException(400, "start must be <= end")
    # Calendar-day span guard (trading-day ≤40 enforced inside sync_runner).
    from datetime import datetime as _dt

    span = (_dt.strptime(end, "%Y%m%d") - _dt.strptime(start, "%Y%m%d")).days + 1
    if span > _LAND_ACCEPT_MAX_WINDOW_DAYS:
        raise HTTPException(
            400,
            f"window {start}..{end} spans {span} calendar days; "
            f"max {_LAND_ACCEPT_MAX_WINDOW_DAYS} (use CLI for explicit backfill knives)",
        )
    flag = "--land-only" if mode == "land_only" else "--land-then-accept"
    argv.extend([flag, "--start", start, "--end", end])
    if payload.get("from_local_raw"):
        argv.append("--from-local-raw")
    return argv


def _alert_summary(flag_path: Path) -> str | None:
    if not flag_path.exists():
        return None
    text = flag_path.read_text(errors="ignore").strip()
    if not text:
        return flag_path.name
    for line in text.splitlines():
        if "PREFLIGHT BLOCK" in line or "DEGRADED:" in line or "FAIL rc=" in line:
            return line.strip()
    return text.splitlines()[0].strip()[:240]


def _slice_current_run(tail: list[str]) -> list[str]:
    """Keep lines after the latest daily_update / stage banner when present."""
    start = 0
    for i, line in enumerate(tail):
        if "=== ChunkyMonkey daily update" in line or "=== ChunkyMonkey pipeline stage" in line:
            start = i
    return tail[start:]


def _derive_current_activity(
    *,
    tail: list[str],
    mtime: float | None,
    writer_busy: bool,
    process_hint: bool,
    owner: str | None,
    owner_pid: int | None,
    alert_summary: str | None,
) -> dict[str, Any]:
    active = bool(writer_busy or process_hint)
    run_lines = _slice_current_run(tail)
    progress = ""
    for line in reversed(run_lines):
        s = line.strip()
        if s:
            progress = s
            break
    phase_id = "idle"
    phase_label = "空闲"
    # Walk newest→oldest; first matching line wins (so ACQUIRE beats earlier preflight).
    for line in reversed(run_lines):
        for pid, label, pat in _PHASE_PATTERNS:
            if pid == "fail" and active:
                continue  # prior FAIL residue must not mask live phase
            if pat.search(line):
                phase_id, phase_label = pid, label
                break
        if phase_id != "idle":
            break
    if active and phase_id == "idle":
        phase_id, phase_label = "running", "运行中"

    age_s: float | None = None
    if mtime is not None:
        age_s = max(0.0, time.time() - float(mtime))
    stale = bool(active and age_s is not None and age_s >= _STALE_LOG_S)

    if active:
        summary = f"正在: {phase_label}"
        if owner:
            summary += f" · writer={owner}"
        if owner_pid is not None:
            summary += f" pid={owner_pid}"
        if stale:
            summary += f" · 日志已 {int(age_s or 0)}s 无新行（进程仍在，可能长同步 / stdout 缓冲）"
    elif alert_summary:
        summary = f"告警: {alert_summary}"
        phase_id = "alert"
        phase_label = "告警残留"
    elif progress:
        summary = f"最近: {progress[:160]}"
    else:
        summary = "空闲 · 尚无日志"

    return {
        "phase": phase_id,
        "phase_label": phase_label,
        "summary": summary,
        "progress_line": progress[:320] if progress else None,
        "log_age_s": round(age_s, 1) if age_s is not None else None,
        "stale_log": stale,
        "blocking_reason": alert_summary,
    }


def _status_payload(job: str, spec: dict[str, Any]) -> dict[str, Any]:
    lock = writer_lock_status()
    process_hint = _is_running(spec)
    log = Path(spec["log"])
    fail_flag = _FLAG_DIR / f"chunkymonkey_ALERT_{job}.flag"
    flags = {fail_flag.name: fail_flag.exists()}
    for extra in spec["extra_flags"]:
        p = Path(extra)
        flags[p.name] = p.exists()
    tail: list[str] = []
    mtime = None
    if log.exists():
        tail = log.read_text(errors="ignore").splitlines()[-_LOG_TAIL_LINES:]
        mtime = log.stat().st_mtime
    alert_text = _alert_summary(fail_flag)
    activity = _derive_current_activity(
        tail=tail,
        mtime=mtime,
        writer_busy=lock.busy,
        process_hint=process_hint,
        owner=lock.owner,
        owner_pid=lock.owner_pid,
        alert_summary=alert_text,
    )
    return {
        "job": job,
        "label": spec["label"],
        "running": lock.busy,
        "writer_busy": lock.busy,
        "owner": lock.owner,
        "owner_pid": lock.owner_pid,
        "process_hint_running": process_hint,
        "alert_flags": flags,
        "alert_summary": alert_text,
        "log_path": str(log),
        "log_tail": tail,
        "log_mtime": mtime,
        "current_activity": activity,
    }


@router.get("/jobs")
def list_jobs():
    """全部可手动触发任务 + 实时状态 (前端按钮面板数据源)."""
    return {"jobs": [_status_payload(j, s) for j, s in MANUAL_JOBS.items()]}


@router.get("/pipeline/nodes")
def pipeline_nodes():
    """Capability E: workbench step-card catalog + live status for runnable nodes.

    Parameterized S1/S2 uses POST /pipeline/land-accept/run (not bare /jobs/.../run).
    Primary full-chain remains daily_update — not replaced by this catalog.
    """
    nodes: list[dict[str, Any]] = []
    for spec in PIPELINE_NODE_CATALOG:
        entry = dict(spec)
        job = spec.get("job")
        if job and job in MANUAL_JOBS:
            entry["status"] = _status_payload(job, MANUAL_JOBS[job])
        else:
            entry["status"] = None
        nodes.append(entry)
    return {
        "primary_job": "daily_update",
        "nodes": nodes,
    }


@router.post("/pipeline/land-accept/run")
def run_land_accept(payload: dict[str, Any] = Body(...)):
    """Capability E parameterized S1/S2 — whitelist domain/mode/dates only."""

    job = "sync_land_accept"
    spec = _job_or_404(job)
    lock = writer_lock_status()
    if lock.busy:
        raise HTTPException(
            409,
            f"writer busy: owner={lock.owner or 'unknown'} "
            f"pid={lock.owner_pid or 'unknown'}",
        )
    argv = build_land_accept_argv(payload or {})
    pid = _spawn(job, spec, argv_override=argv)
    return {
        "job": job,
        "accepted": True,
        "pid": pid,
        "argv": argv,
        "mode": str((payload or {}).get("mode") or ""),
        "domain": str((payload or {}).get("domain") or ""),
    }


@router.get("/jobs/{job}")
def job_status(job: str):
    return _status_payload(job, _job_or_404(job))


@router.post("/jobs/{job}/run")
def run_job(job: str):
    spec = _job_or_404(job)
    if spec.get("parameterized"):
        raise HTTPException(
            400,
            f"job={job} is parameterized; use POST /api/v3/ops/pipeline/land-accept/run",
        )
    lock = writer_lock_status()
    if lock.busy:
        raise HTTPException(
            409,
            f"writer busy: owner={lock.owner or 'unknown'} "
            f"pid={lock.owner_pid or 'unknown'}",
        )
    pid = _spawn(job, spec)
    # Detached child owns the atomic flock acquisition.  This endpoint can only truthfully
    # acknowledge enqueue/acceptance: another click may race between the read-only status
    # probe and child startup, in which case one child exits with writer_busy and its wrapper
    # persists the failure.  Never claim "started" before that lock handshake exists.
    return {"job": job, "accepted": True, "pid": pid}
