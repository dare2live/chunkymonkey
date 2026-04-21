"""启动 FastAPI，触发智能更新，然后抓取评分样本做审计.

用途:
  python3 scripts/sef/smart_update_and_scoring.py --port 8012

流程:
  1) 启动 uvicorn 后台进程
  2) 等待 /health 返回 OK
  3) 调 GET /signals/update/smart-plan 看待执行清单
  4) 调 POST /update/smart 触发智能更新
  5) 每 5 秒轮询 /update/status 直到完成
  6) 拉 /signals/today 样本做评分验证
  7) 关闭 uvicorn
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request as urlreq
from urllib.error import URLError, HTTPError

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"


def _http(method: str, url: str, body: Any = None, timeout: int = 30) -> dict:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urlreq.Request(url, data=data, headers=headers, method=method)
    with urlreq.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _wait_for_ready(base: str, max_seconds: int = 90) -> bool:
    for _ in range(max_seconds):
        try:
            r = _http("GET", f"{base}/health", timeout=3)
            if r.get("status") == "ok" or r.get("ok") or "status" in r:
                return True
        except (URLError, HTTPError, json.JSONDecodeError, ConnectionResetError):
            pass
        time.sleep(1)
    return False


def _wait_for_done(base: str, max_seconds: int = 1800) -> dict:
    last: dict = {}
    for _ in range(max_seconds // 5):
        try:
            status = _http("GET", f"{base}/api/inst/update/status", timeout=10)
        except Exception:  # noqa: BLE001
            time.sleep(5)
            continue
        last = status
        if not status.get("running", False):
            return last
        time.sleep(5)
    return last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--skip-update", action="store_true", help="只拉评分，不跑智能更新")
    parser.add_argument("--signals-sample", type=int, default=20)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    report: dict[str, Any] = {"base": base, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(_BACKEND))
    log_path = Path(f"/tmp/sef_uvicorn_{args.port}.log")
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--log-level",
            "warning",
        ],
        cwd=str(_BACKEND),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    report["uvicorn_pid"] = proc.pid
    report["uvicorn_log"] = str(log_path)

    try:
        if not _wait_for_ready(base, max_seconds=90):
            report["error"] = "uvicorn did not become ready"
            raise RuntimeError("uvicorn did not become ready")

        if not args.skip_update:
            plan = _http("GET", f"{base}/api/inst/update/smart-plan")
            report["plan"] = plan.get("plan", {})
            steps_to_run = plan.get("plan", {}).get("steps") or []
            if not steps_to_run:
                report["update"] = {"noop": True, "message": "data is up-to-date"}
            else:
                resp = _http("POST", f"{base}/api/inst/update/smart", body={})
                report["update_kickoff"] = resp
                status = _wait_for_done(base, max_seconds=3600)
                report["update_final_status"] = status

        # 评分样本
        try:
            signals = _http("GET", f"{base}/api/signals/today?limit={args.signals_sample}")
        except Exception as e:  # noqa: BLE001
            signals = {"error": str(e)}
        report["signals_today"] = signals

        # audit 快照
        try:
            audit_resp = _http("GET", f"{base}/api/inst/update/audit", timeout=60)
        except Exception as e:  # noqa: BLE001
            audit_resp = {"error": str(e)}
        report["audit"] = audit_resp

    finally:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
