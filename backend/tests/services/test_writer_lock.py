"""受支持的 pipeline/sync 数据写入口 advisory lock 契约。"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from services import writer_lock as wl


def test_writer_lock_rejects_another_writer_and_reports_holder(tmp_path, monkeypatch):
    lock_path = tmp_path / "writer.lock"
    monkeypatch.setenv(wl.WRITER_LOCK_PATH_ENV, str(lock_path))
    held = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    held.write(json.dumps({"owner_pid": 12345, "owner": "other", "lease": "secret"}))
    held.flush()

    with pytest.raises(wl.WriterLockBusyError, match="owner=other.*pid=12345"):
        with wl.writer_lock(owner="sync_runner"):
            pass

    fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    held.close()


def test_writer_lock_disabled_for_read_only_work(tmp_path, monkeypatch):
    lock_path = tmp_path / "writer.lock"
    monkeypatch.setenv(wl.WRITER_LOCK_PATH_ENV, str(lock_path))
    with wl.writer_lock(owner="audit", enabled=False) as lease:
        assert lease.lease_id is None
        assert lease.inherited is False
    assert not lock_path.exists()


def test_writer_lock_status_uses_flock_not_stale_metadata(tmp_path, monkeypatch):
    lock_path = tmp_path / "writer.lock"
    monkeypatch.setenv(wl.WRITER_LOCK_PATH_ENV, str(lock_path))
    lock_path.write_text(
        json.dumps({"owner_pid": 99999, "owner": "stale", "lease": "old"}),
        encoding="utf-8",
    )
    assert wl.writer_lock_status().busy is False

    with wl.writer_lock(owner="live"):
        status = wl.writer_lock_status()
        assert status.busy is True
        assert status.owner == "live" and status.owner_pid == os.getpid()


def test_only_direct_child_with_matching_parent_lease_can_reenter(tmp_path, monkeypatch):
    lock_path = tmp_path / "writer.lock"
    monkeypatch.setenv(wl.WRITER_LOCK_PATH_ENV, str(lock_path))
    child = (
        "from services.writer_lock import writer_lock; "
        "\nwith writer_lock(owner='child') as lease: print(lease.inherited)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "backend"

    with wl.writer_lock(owner="parent") as lease:
        env[wl.WRITER_LEASE_ENV] = str(lease.lease_id)
        env[wl.WRITER_LOCK_FD_ENV] = str(lease.lock_fd)
        ok = subprocess.run(
            [sys.executable, "-c", child],
            cwd=str(Path(__file__).resolve().parents[3]),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            pass_fds=(lease.lock_fd,),
        )
        assert ok.returncode == 0 and ok.stdout.strip() == "True"

        env[wl.WRITER_LEASE_ENV] = "forged-lease"
        denied = subprocess.run(
            [sys.executable, "-c", child],
            cwd=str(Path(__file__).resolve().parents[3]),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert denied.returncode != 0
        assert "pipeline writer busy" in denied.stderr


def test_inherited_child_keeps_real_lock_after_parent_dies(tmp_path):
    """父 controller 异常退出后，仍在写的直接子进程必须继续挡住第二 writer。"""
    lock_path = tmp_path / "writer.lock"
    ready_path = tmp_path / "child.ready"
    pid_path = tmp_path / "child.pid"
    repo = Path(__file__).resolve().parents[3]
    child_code = textwrap.dedent("""
        import os
        import time
        from pathlib import Path
        from services.writer_lock import writer_lock

        with writer_lock(owner="orphan-child"):
            Path(os.environ["READY"]).write_text("ready")
            time.sleep(30)
    """)
    parent_code = textwrap.dedent("""
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path
        from services.writer_lock import WRITER_LEASE_ENV, WRITER_LOCK_FD_ENV, writer_lock

        with writer_lock(owner="crashing-parent") as lease:
            env = os.environ.copy()
            env[WRITER_LEASE_ENV] = str(lease.lease_id)
            env[WRITER_LOCK_FD_ENV] = str(lease.lock_fd)
            child = subprocess.Popen(
                [sys.executable, "-c", os.environ["CHILD_CODE"]],
                env=env,
                cwd=os.environ["REPO"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(lease.lock_fd,),
            )
            Path(os.environ["PID_FILE"]).write_text(str(child.pid))
            deadline = time.time() + 5
            while not Path(os.environ["READY"]).exists() and time.time() < deadline:
                time.sleep(.02)
            os._exit(0)
    """)
    env = os.environ.copy()
    env.update({
        wl.WRITER_LOCK_PATH_ENV: str(lock_path),
        "PYTHONPATH": "backend",
        "READY": str(ready_path),
        "PID_FILE": str(pid_path),
        "CHILD_CODE": child_code,
        "REPO": str(repo),
    })
    subprocess.run(
        [sys.executable, "-c", parent_code], cwd=str(repo), env=env, check=True, timeout=8
    )
    child_pid = int(pid_path.read_text())
    try:
        assert ready_path.exists()
        old = os.environ.get(wl.WRITER_LOCK_PATH_ENV)
        os.environ[wl.WRITER_LOCK_PATH_ENV] = str(lock_path)
        try:
            with pytest.raises(wl.WriterLockBusyError):
                with wl.writer_lock(owner="second-writer"):
                    pass
        finally:
            if old is None:
                os.environ.pop(wl.WRITER_LOCK_PATH_ENV, None)
            else:
                os.environ[wl.WRITER_LOCK_PATH_ENV] = old
    finally:
        os.kill(child_pid, signal.SIGTERM)
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
