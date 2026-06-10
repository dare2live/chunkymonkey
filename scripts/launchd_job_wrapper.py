#!/usr/bin/env python3
"""launchd job 统一入口: 失败必须送达, 决不静默.

根因背景 (2026-06-11): daily_update cron 自 6 月起每天 'Operation not permitted'
静默失败, K 线断流 4+ 交易日无人知晓 (feedback-data-sync-silent-failure 复发).
本 wrapper 是"告警送达"的根因修复: 任何 job 失败 -> macOS 通知 + ALERT flag 文件,
chunkyctl doctor / cm_resume 读 flag 即可在下次 session 第一时间看到.

为什么入口是 python 不是 bash (2026-06-11 实测, 见 PROJECT_INDEX):
launchd 上下文中 bash 读 ~/Documents 下脚本被 TCC 拦 (exit 126), 而本机
python3.13 已有 Full Disk Access; launchd job 的 TCC 身份取自 ProgramArguments[0],
子进程继承 -- 所以 plist 用 .venv/bin/python 跑本文件, 内部再 spawn bash 即可,
无需给 bash/cron 授权.

用法 (plist ProgramArguments):
  <repo>/.venv/bin/python <repo>/scripts/launchd_job_wrapper.py <job_name> <command> [args...]

产物:
  /tmp/chunkymonkey_<job>.log        -- 追加式运行日志 (含 OK/FAIL 边界行)
  /tmp/chunkymonkey_ALERT_<job>.flag -- 仅最近一次失败时存在; 成功自动清除
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%F %T")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: launchd_job_wrapper.py <job_name> <command...>", file=sys.stderr)
        return 2
    job, cmd = sys.argv[1], sys.argv[2:]
    log = Path(f"/tmp/chunkymonkey_{job}.log")
    flag = Path(f"/tmp/chunkymonkey_ALERT_{job}.flag")
    start = _ts()

    with log.open("a") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    end = _ts()

    if rc != 0:
        tail = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-5:])
        flag.write_text(
            f"[{start} -> {end}] FAIL rc={rc} job={job}\n"
            f"cmd: {' '.join(cmd)}\n--- last log lines ---\n{tail}"
        )
        with log.open("a") as fh:
            fh.write(f"[{start} -> {end}] FAIL rc={rc} job={job} (alert flag: {flag})\n")
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'display notification "{job} failed rc={rc} — see {log}" '
                f'with title "ChunkyMonkey job FAIL"',
            ],
            capture_output=True,
        )
    else:
        with log.open("a") as fh:
            fh.write(f"[{start} -> {end}] OK job={job}\n")
        flag.unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
