"""共用换名机制 duckdb_file_swap —— 六项围栏隔离用例 (W1) + 正常路径 (W2)。

见 sandbox/churn_fix_20260919/build_qfq_fresh_file_swap.md §W1/W2: 六项检查各配
一个"其它全满足只违反它"的隔离用例; 每条断言逐个变异 (注释掉对应检查) 应转红,
本文件不留变异产物, 施工记录见 sandbox/churn_fix_20260919/ 下的收尾报告。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest

from services.duckdb_file_swap import (
    REASON_BAK_ALREADY_EXISTS,
    REASON_BUILD_NOT_CLOSED,
    REASON_LIVE_CHANGED_DURING_BUILD,
    REASON_LIVE_HAS_ACTIVE_WRITER,
    REASON_STALE_LIVE_WAL,
    REASON_UNEXPECTED_FIRST_BUILD,
    SwapRefused,
    file_fingerprint,
    swap_in_fresh_file,
)


def _make_closed_db(path: Path, *, value: int = 1) -> None:
    """建一个干净关闭 (无 .wal 残留) 的最小 duckdb 文件。"""
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute(f"INSERT INTO t VALUES ({value})")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


def _hold_write_connection(db_path: Path) -> subprocess.Popen:
    """真实子进程持有 db_path 的读写连接; ready 后通过 stdout 通知父进程。"""
    code = textwrap.dedent(
        f"""
        import duckdb, sys
        conn = duckdb.connect({str(db_path)!r}, read_only=False)
        print("ready", flush=True)
        sys.stdin.readline()
        conn.close()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    assert line.strip() == "ready", f"子进程未就绪: {line!r}"
    return proc


def _release_write_connection(proc: subprocess.Popen) -> None:
    proc.stdin.write("done\n")
    proc.stdin.close()
    proc.wait(timeout=10)


# ═══════════════════════════════════════════════ W1: 六项围栏, 各一个隔离用例


def test_w1_build_wal_residual_refuses(tmp_path):
    """build.wal 残留 (build 连接没干净关闭) → 拒绝, live 与 build 均不动。"""
    live = tmp_path / "live.duckdb"
    _make_closed_db(live)
    expected = file_fingerprint(live)

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build)
    (build.with_name(build.name + ".wal")).write_bytes(b"stale-build-wal")

    with pytest.raises(SwapRefused) as exc:
        swap_in_fresh_file(build, live, expected=expected)
    assert exc.value.reason == REASON_BUILD_NOT_CLOSED
    assert file_fingerprint(live) == expected
    assert build.exists()  # 未被换名/删除 (删除是调用方的事, 本函数只拒绝)


def test_w1_live_wal_residual_refuses(tmp_path):
    """live.wal 残留 (会被换名回放进新文件, spec E5b 实测) → 拒绝。"""
    live = tmp_path / "live.duckdb"
    _make_closed_db(live)
    expected = file_fingerprint(live)
    (live.with_name(live.name + ".wal")).write_bytes(b"stale-live-wal")

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build)

    with pytest.raises(SwapRefused) as exc:
        swap_in_fresh_file(build, live, expected=expected)
    assert exc.value.reason == REASON_STALE_LIVE_WAL
    assert file_fingerprint(live) == expected  # .wal 不参与指纹, live 本体未变
    assert build.exists()


def test_w1_live_fingerprint_changed_refuses(tmp_path):
    """live 指纹变 (另一连接在 expected 记完之后写入并 checkpoint) → 拒绝。"""
    live = tmp_path / "live.duckdb"
    _make_closed_db(live)
    expected = file_fingerprint(live)

    other = duckdb.connect(str(live))
    try:
        other.execute("INSERT INTO t VALUES (2)")
        other.execute("CHECKPOINT")
    finally:
        other.close()
    assert file_fingerprint(live) != expected  # 前提自检: 指纹确实变了

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build)

    with pytest.raises(SwapRefused) as exc:
        swap_in_fresh_file(build, live, expected=expected)
    assert exc.value.reason == REASON_LIVE_CHANGED_DURING_BUILD
    assert build.exists()


def test_w1_first_build_with_non_none_expected_refuses(tmp_path):
    """首次建库: live 不存在时 expected 必须是 None, 否则拒绝。"""
    live = tmp_path / "live.duckdb"  # 不创建 —— 首次建库场景
    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build)

    with pytest.raises(SwapRefused) as exc:
        swap_in_fresh_file(build, live, expected=(1, 2, 3))
    assert exc.value.reason == REASON_UNEXPECTED_FIRST_BUILD
    assert not live.exists()
    assert build.exists()


def test_w1_live_has_active_writer_refuses(tmp_path):
    """live 有活跃写者 (真实子进程持有读写连接, 不是同进程未关连接那种弱形态)。"""
    live = tmp_path / "live.duckdb"
    _make_closed_db(live)
    expected = file_fingerprint(live)

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build)

    proc = _hold_write_connection(live)
    try:
        with pytest.raises(SwapRefused) as exc:
            swap_in_fresh_file(build, live, expected=expected)
        assert exc.value.reason == REASON_LIVE_HAS_ACTIVE_WRITER
    finally:
        _release_write_connection(proc)
    assert file_fingerprint(live) == expected
    assert build.exists()


def test_w1_keep_bak_already_exists_refuses(tmp_path):
    """keep_bak 目标已存在 → 拒绝, 不覆盖已有 bak、不换名。"""
    live = tmp_path / "live.duckdb"
    _make_closed_db(live)
    expected = file_fingerprint(live)

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build, value=2)

    bak = tmp_path / "live_precompact_bak.duckdb"
    bak.write_bytes(b"already-here")

    with pytest.raises(SwapRefused) as exc:
        swap_in_fresh_file(build, live, expected=expected, keep_bak=bak)
    assert exc.value.reason == REASON_BAK_ALREADY_EXISTS
    assert bak.read_bytes() == b"already-here"  # 未被覆盖
    assert file_fingerprint(live) == expected
    assert build.exists()


# ═══════════════════════════════════════════════════════════ W2: 正常路径


def test_w2_swap_moves_build_inode_into_live_path(tmp_path):
    live = tmp_path / "live.duckdb"
    _make_closed_db(live, value=1)
    expected = file_fingerprint(live)
    old_live_ino = live.stat().st_ino

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build, value=2)
    build_ino = build.stat().st_ino

    swap_in_fresh_file(build, live, expected=expected)

    assert not build.exists()  # os.replace 后原路径不再有 build 文件
    assert live.stat().st_ino == build_ino  # live 现在就是原 build 的 inode
    assert live.stat().st_ino != old_live_ino

    conn = duckdb.connect(str(live), read_only=True)
    try:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 2
    finally:
        conn.close()


def test_w2_keep_bak_hardlinks_old_live_without_copy(tmp_path):
    live = tmp_path / "live.duckdb"
    _make_closed_db(live, value=1)
    expected = file_fingerprint(live)
    old_live_ino = live.stat().st_ino

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build, value=2)

    bak = tmp_path / "live_precompact_bak.duckdb"
    swap_in_fresh_file(build, live, expected=expected, keep_bak=bak)

    assert bak.exists()
    assert bak.stat().st_ino == old_live_ino  # 硬链接, 不是拷贝
    conn = duckdb.connect(str(bak), read_only=True)
    try:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 1  # 旧内容仍可读
    finally:
        conn.close()


def test_w2_no_keep_bak_leaves_no_bak_file(tmp_path):
    live = tmp_path / "live.duckdb"
    _make_closed_db(live, value=1)
    expected = file_fingerprint(live)

    build = tmp_path / "live_build.duckdb"
    _make_closed_db(build, value=2)

    swap_in_fresh_file(build, live, expected=expected)

    assert not (tmp_path / "live_precompact_bak.duckdb").exists()
