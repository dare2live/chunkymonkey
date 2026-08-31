"""坑 5 的回归锁: baostock 进程间会话锁 (2026-08-31 账号被拉黑事故)。

事故: 一个 agent 起 5 进程并发探 baostock 吞吐, 十几秒内触发服务端风控
``10001011 黑名单用户``, 该 IP 此后所有查询 login failed。根因是 baostock
按账号 (本项目匿名登录 => 按来源 IP) 维持**单一会话**, 多进程互踢 + 反复重连被风控。

坑 3 的 thread-id 检查对多进程完全无效 (每进程各有一份 ``_owner_thread_id``),
所以补了 ``_acquire_process_lock``。本文件锁死它的三条行为:
拿不到锁必须**拒绝**而不是排队; 锁机制自身故障必须**降级放行**; logout 必须**无条件**释放。

所有用例把锁路径指到 ``tmp_path``, 绝不碰真实 ``data/scratch/``
(项目在"测试写脏工作站文件"上栽过多次)。
"""
from __future__ import annotations

import pytest

import services.data_sources.sources.baostock as bao


@pytest.fixture(autouse=True)
def _isolate_lock_path(monkeypatch, tmp_path):
    monkeypatch.setenv(bao._SESSION_LOCK_PATH_ENV, str(tmp_path / "baostock_session.lock"))


def test_lock_path_honours_env(tmp_path, monkeypatch):
    target = tmp_path / "custom.lock"
    monkeypatch.setenv(bao._SESSION_LOCK_PATH_ENV, str(target))
    assert bao._session_lock_path() == target


def test_first_acquire_succeeds_and_close_releases():
    h1 = bao._acquire_process_lock()
    assert h1 is not None
    h1.close()
    h2 = bao._acquire_process_lock()      # 释放后可再抢
    assert h2 is not None
    h2.close()


def test_second_acquire_while_held_raises_not_queues():
    """并发场景的核心断言: 拒绝, 不排队 —— 排队只会把并发变成拥塞并再次触发风控。"""
    held = bao._acquire_process_lock()
    assert held is not None
    try:
        with pytest.raises(bao.BaostockConcurrencyError) as exc:
            bao._acquire_process_lock()
        msg = str(exc.value)
        assert "single" in msg and "blacklisted" in msg      # 消息要讲清后果
    finally:
        held.close()


def test_lock_machinery_failure_degrades_open(monkeypatch, tmp_path):
    """锁文件建不出来时放行 (返回 None) 而不是抛 —— 防护不该变成新的故障点。"""
    monkeypatch.setenv(bao._SESSION_LOCK_PATH_ENV, str(tmp_path / "nodir" / "x.lock"))

    def _boom(*a, **k):
        raise PermissionError("simulated: cannot create lock dir")

    monkeypatch.setattr(bao.Path, "mkdir", _boom)
    assert bao._acquire_process_lock() is None


def test_logout_releases_lock_even_when_login_failed(monkeypatch):
    """锁在 login **之前**抢下; login 失败时 _logged_in 仍为 False,
    早退会把锁泄漏到整个进程生命周期 —— 必须无条件释放。"""
    src = bao.BaostockSource()
    src._lock_handle = bao._acquire_process_lock()
    assert src._lock_handle is not None
    assert src._logged_in is False        # 模拟 login 失败后的状态
    src.logout()
    assert src._lock_handle is None
    again = bao._acquire_process_lock()   # 已释放, 能再抢到
    assert again is not None
    again.close()


def test_release_is_idempotent():
    src = bao.BaostockSource()
    src._release_process_lock()           # 从未持锁
    src._lock_handle = bao._acquire_process_lock()
    src._release_process_lock()
    src._release_process_lock()           # 重复释放不抛
    assert src._lock_handle is None
