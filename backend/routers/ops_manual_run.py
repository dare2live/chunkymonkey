"""手动任务触发 router — 2026-06-12 用户决议: 自动调度退役, 更新类任务改前端按钮手动触发.

设计:
- 一个 job 注册条目 = 一个可手动触发的链 (argv + 日志/flag 路径), 新任务加条目零端点代码
  (与 sync_registry 同范式, 防每任务一个端点的发散)。
- 触发 = detached spawn (start_new_session): API 进程重启不杀链; 告警链复用
  launchd_job_wrapper (失败写 /tmp/chunkymonkey_ALERT_<job>.flag + macOS 通知, 成功自清)。
- 状态 = 进程活性 (pgrep) + wrapper flag + 链日志尾部, 全部只读, 不碰 DuckDB
  (状态接口任何时刻可调, 不与运行中的链抢锁)。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

_REPO = Path(__file__).resolve().parents[2]
_PY = _REPO / ".venv" / "bin" / "python"
_WRAPPER = _REPO / "scripts" / "launchd_job_wrapper.py"

# job 注册表: 名称 -> 定义 (argv 不含解释器/wrapper 前缀; pattern 用于 pgrep 活性判定)
MANUAL_JOBS: dict[str, dict[str, Any]] = {
    "daily_update": {
        "argv": ["/bin/bash", str(_REPO / "scripts" / "daily_update.sh")],
        "pattern": "scripts/daily_update.sh",
        "log": "/tmp/chunkymonkey_daily_update.log",
        "extra_flags": ["/tmp/chunkymonkey_ALERT_daily_update_degraded.flag"],
        "label": "每日数据更新链 (K线/因子/信号/审计 全链, 约 40-60 分钟)",
    },
    # concept_snapshot (E7) 2026-06-13 物理摘除: 实测 20260610/11 两份快照均 8000 行整
    # 截断 (真实日量 ~90k), observed 事件全是伪影; dc 系历史 tushare 可随时回拉, 快照
    # 是冗余中间层 — 留可点按钮 = 误导 (产物入 fact_concept_event 污染 LF 实验面)。
    "tdx_pool_refresh": {
        "argv": [
            str(_PY),
            str(_REPO / "backend" / "scripts" / "refresh_tdx_server_pool.py"),
        ],
        "pattern": "refresh_tdx_server_pool.py",
        "log": "/tmp/chunkymonkey_tdx_pool_refresh.log",
        "extra_flags": [],
        "label": "TDX 服务器活池刷新 (协议层扫描全候选写回 .env, ~1 分钟; 活池独占后的定期保养)",
    },
}

# tail 行数: 前端状态卡只展示链尾近况, 全文走日志文件本身
_LOG_TAIL_LINES = 15  # rule-compliance: ok evidence=display-only UI tail, 非业务阈值
# wrapper flag 目录 (测试 monkeypatch 此值隔离, 不碰生产 /tmp flag)
_FLAG_DIR = Path("/tmp")


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


def _spawn(job: str, spec: dict[str, Any]) -> int:
    argv = [str(_PY), str(_WRAPPER), job, *spec["argv"]]
    stdout_path = Path(f"/tmp/chunkymonkey_{job}.stdout.log")
    with stdout_path.open("ab") as fh:
        proc = subprocess.Popen(
            argv,
            cwd=str(_REPO),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def _status_payload(job: str, spec: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "job": job,
        "label": spec["label"],
        "running": _is_running(spec),
        "alert_flags": flags,
        "log_path": str(log),
        "log_tail": tail,
        "log_mtime": mtime,
    }


@router.get("/jobs")
def list_jobs():
    """全部可手动触发任务 + 实时状态 (前端按钮面板数据源)."""
    return {"jobs": [_status_payload(j, s) for j, s in MANUAL_JOBS.items()]}


@router.get("/jobs/{job}")
def job_status(job: str):
    return _status_payload(job, _job_or_404(job))


@router.post("/jobs/{job}/run")
def run_job(job: str):
    spec = _job_or_404(job)
    if _is_running(spec):
        raise HTTPException(409, f"{job} 已在运行, 拒绝重复触发")
    pid = _spawn(job, spec)
    return {"job": job, "started": True, "pid": pid}
