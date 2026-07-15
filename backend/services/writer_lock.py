"""受支持的 pipeline/sync 入口 advisory lock，含受控 parent→child lease 复入。

本锁不是对仓库内所有历史脚本的魔法全局拦截；公开 controller 入口为 pipeline.run、
pipeline.stage_runner、sync_runner 与手动 API 启动的 daily_update。直接执行内部 writer 脚本
不在此契约内，运维面由 manual-only automation gate 禁止将其注册为后台任务。
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


WRITER_LEASE_ENV = "CHUNKYMONKEY_WRITER_LEASE"
WRITER_LOCK_FD_ENV = "CHUNKYMONKEY_WRITER_LOCK_FD"
AUTH_VERIFIED_LEASE_ENV = "CHUNKYMONKEY_AUTH_VERIFIED_LEASE"
WRITER_LOCK_PATH_ENV = "CHUNKYMONKEY_WRITER_LOCK_PATH"
WRITER_LOCK_PATH = Path(tempfile.gettempdir()) / "chunkymonkey-pipeline-writer.lock"


class WriterLockBusyError(RuntimeError):
    """另一个真实 writer 正持有项目写窗口。"""


@dataclass(frozen=True)
class WriterLease:
    lease_id: str | None
    owner: str
    inherited: bool
    path: Path | None
    lock_fd: int | None


@dataclass(frozen=True)
class WriterLockStatus:
    busy: bool
    owner: str | None
    owner_pid: int | None
    path: Path


def _lock_path() -> Path:
    return Path(os.environ.get(WRITER_LOCK_PATH_ENV) or WRITER_LOCK_PATH)


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _holder_message(path: Path) -> str:
    meta = _read_metadata(path)
    return (
        f"pipeline writer busy: owner={meta.get('owner', 'unknown')} "
        f"pid={meta.get('owner_pid', 'unknown')} path={path}"
    )


def writer_lock_status() -> WriterLockStatus:
    """只读探测真实 flock 状态；metadata 仅在 busy 时作为 holder 提示。"""
    path = _lock_path()
    if not path.exists():
        return WriterLockStatus(False, None, None, path)
    try:
        handle = path.open("r+", encoding="utf-8")
    except OSError:
        meta = _read_metadata(path)
        return WriterLockStatus(
            True,
            str(meta.get("owner")) if meta.get("owner") else None,
            int(meta["owner_pid"]) if isinstance(meta.get("owner_pid"), int) else None,
            path,
        )
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            meta = _read_metadata(path)
            return WriterLockStatus(
                True,
                str(meta.get("owner")) if meta.get("owner") else None,
                int(meta["owner_pid"]) if isinstance(meta.get("owner_pid"), int) else None,
                path,
            )
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return WriterLockStatus(False, None, None, path)
    finally:
        handle.close()


@contextmanager
def writer_lock(
    owner: str,
    *,
    enabled: bool = True,
    inherited_lease: str | None = None,
) -> Iterator[WriterLease]:
    """非阻塞获取唯一 writer；只允许直接父进程签发的匹配 lease 复入。

    任意环境变量值本身不构成授权：只有锁文件 metadata 的 lease 相同，且
    `owner_pid == os.getppid()`，子进程才可继承父 writer 的已持有窗口。
    """
    if not enabled:
        yield WriterLease(None, owner, False, None, None)
        return

    path = _lock_path()
    candidate = inherited_lease or os.environ.get(WRITER_LEASE_ENV)
    inherited_fd_raw = os.environ.get(WRITER_LOCK_FD_ENV)
    if candidate:
        meta = _read_metadata(path)
        try:
            inherited_fd = int(inherited_fd_raw or "")
            fd_stat = os.fstat(inherited_fd)
            path_stat = path.stat()
            same_file = (fd_stat.st_dev, fd_stat.st_ino) == (path_stat.st_dev, path_stat.st_ino)
            # A genuine inherited descriptor shares the parent's open-file description, so
            # reasserting the flock succeeds.  A forged descriptor opened separately cannot
            # acquire while the parent holds the lock.
            fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError, TypeError, ValueError):
            inherited_fd = -1
            same_file = False
        if (
            same_file
            and secrets.compare_digest(str(meta.get("lease") or ""), candidate)
            and meta.get("owner_pid") == os.getppid()
            and writer_lock_status().busy
        ):
            # Do not close/unlock this descriptor here.  It is the actual inherited lock
            # reference; keeping it open means a child remains protected if its parent dies.
            yield WriterLease(candidate, owner, True, path, inherited_fd)
            return

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterLockBusyError(_holder_message(path)) from exc

        lease_id = secrets.token_urlsafe(24)
        metadata = {
            "owner": owner,
            "owner_pid": os.getpid(),
            "lease": lease_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        json.dump(metadata, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        try:
            yield WriterLease(lease_id, owner, False, path, handle.fileno())
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
