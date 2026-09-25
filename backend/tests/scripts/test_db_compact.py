"""db_compact 保真缩盘工具测试 — 守 DETACH-src 回归 + 保真 (DDL/PK/索引/视图/行数)。

核心回归: 验证前必须 DETACH src, 否则 information_schema/duckdb_constraints/duckdb_indexes
跨 attach 库双计 (新+旧=2x) → 对账假失败 return 5。本测试断言 run() return 0 即守住该回归。
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

db_compact = importlib.import_module("backend.scripts.db_compact") if False else None
# 脚本以路径方式导入 (backend/scripts 非包)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "db_compact", REPO / "backend" / "scripts" / "db_compact.py"
)
db_compact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_compact)


def _build_src(path: Path) -> tuple[int, int]:
    """建迷你源库: 2 表 (一带 PK), 1 索引, 1 视图。返回 (a 行数, b 行数)。"""
    c = duck_connect(str(path), read_only=False)
    try:
        c.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, v DOUBLE)")
        c.execute("INSERT INTO a SELECT i, i*1.5 FROM range(100) t(i)")
        c.execute("CREATE TABLE b (k VARCHAR, n INTEGER)")
        c.execute("INSERT INTO b SELECT 'x'||i, i FROM range(50) t(i)")
        c.execute("CREATE INDEX idx_b_k ON b(k)")
        c.execute("CREATE VIEW v_ab AS SELECT a.id, b.n FROM a JOIN b ON a.id = b.n")
        c.execute("CHECKPOINT")
    finally:
        c.close()
    return 100, 50


def test_compact_preserves_structure_and_swaps(tmp_path, monkeypatch):
    src = tmp_path / "testdb.duckdb"
    na, nb = _build_src(src)

    # 让 _db_path 把 alias 映射到我们的临时源库
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb", execute=True)
    assert rc == 0, "缩盘 run 应 return 0 (return 5 = DETACH-src 回归致对账双计假失败)"

    # 换名后: src 路径 = 缩后库。cut_db_compaction (2026-09-19): drop_bak 默认改为
    # True, 校验通过 + 换名成功后自动删 bak (--keep-bak / drop_bak=False 才保留)。
    bak = src.with_name("testdb_precompact_bak.duckdb")
    assert not bak.exists(), "默认 drop_bak=True, 校验通过后 bak 不应残留"
    assert src.exists(), "缩后库应换名回原路径"

    # 保真核对: 表/视图/约束/索引/行数 全保留
    c = duck_connect(str(src), read_only=True)
    try:
        tabs = c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchone()[0]
        views = c.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW'"
        ).fetchone()[0]
        cons = c.execute("SELECT count(*) FROM duckdb_constraints()").fetchone()[0]
        idx = c.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0]
        ra = c.execute('SELECT count(*) FROM a').fetchone()[0]
        rb = c.execute('SELECT count(*) FROM b').fetchone()[0]
        rv = c.execute('SELECT count(*) FROM v_ab').fetchone()[0]
    finally:
        c.close()

    assert tabs == 2, f"表数应保留 2, got {tabs}"
    assert views == 1, f"视图应重建保留 1, got {views}"
    assert cons >= 1, f"PK 约束应保留 (>=1), got {cons}"
    assert idx >= 1, f"索引应重建保留 (>=1), got {idx}"
    assert (ra, rb) == (na, nb), f"行数应全等, got a={ra} b={rb}"
    assert rv > 0, "视图应可查 (重建成功)"


def test_compact_dry_run_no_swap(tmp_path, monkeypatch):
    src = tmp_path / "testdb2.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb2", execute=False)
    assert rc == 0
    # dry-run 不产生新库/不换名
    assert not src.with_name("testdb2_compact.duckdb").exists()
    assert not src.with_name("testdb2_precompact_bak.duckdb").exists()


def test_compact_keep_bak_true_preserves_bak(tmp_path, monkeypatch):
    """A7: drop_bak=False (CLI --keep-bak) -> 校验通过换名后 bak 保留。"""
    src = tmp_path / "testdb3.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdb3", execute=True, drop_bak=False)
    assert rc == 0

    bak = src.with_name("testdb3_precompact_bak.duckdb")
    assert bak.exists(), "drop_bak=False 应保留 _precompact_bak"
    assert src.exists()


class _MutateOnClose:
    """代理: 真实读连接 close() 时, 在源文件上追加一行——用来在"读基线"与"整库
    ATTACH-copy"两个阶段之间制造一次真实的行数不齐, 而不 monkeypatch run() 内部逻辑
    本身 (A7 第三种隔离用例要的是真验证失败, 不是伪造 ok 标志位)。
    """

    def __init__(self, conn, path: Path):
        self._conn = conn
        self._path = path

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        self._conn.close()
        extra = duck_connect(str(self._path), read_only=False)
        try:
            extra.execute("INSERT INTO a VALUES (999999, 1.0)")
            extra.execute("CHECKPOINT")
        finally:
            extra.close()


def test_compact_validation_failure_no_rename_no_bak(tmp_path, monkeypatch):
    """A7 / M11: 校验不过 (基线读取后源库又多了一行, 行数对不齐) -> 不换名、不产生 bak、
    且 new(含 .wal) 已被清掉 (M11: 建过 new 的失败路径都要清, 不留给下一次运行去撞
    "已存在" 早退——这条以前的行为是"留给人排查", 09-25 审查裁决改为必须自动清)。
    """
    src = tmp_path / "testdb4.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    orig_connect = db_compact.duck_connect
    state = {"patched_first_call": False}

    def _connect(path, read_only=False):
        conn = orig_connect(path, read_only=read_only)
        if read_only and not state["patched_first_call"]:
            state["patched_first_call"] = True
            return _MutateOnClose(conn, Path(path))
        return conn

    monkeypatch.setattr(db_compact, "duck_connect", _connect)

    rc = db_compact.run("testdb4", execute=True)
    assert rc == 5, "基线读完后源库又多一行 -> ATTACH-copy 阶段行数对不齐, 应 return 5"

    bak = src.with_name("testdb4_precompact_bak.duckdb")
    assert not bak.exists(), "校验不过不该产生 bak"
    assert src.exists(), "校验不过不该换名, 原路径的 src 应仍在"
    new = src.with_name("testdb4_compact.duckdb")
    assert not new.exists(), "M11: 对账不齐必须清掉 new, 不留给下一次运行去撞已存在"
    assert not new.with_name(new.name + ".wal").exists()


def test_w3_stale_src_wal_refuses_before_building_new_file(tmp_path, monkeypatch):
    """W3 (cut_qfq_fresh_file_swap M3): src 旁有残留 .wal -> rc=7, 这是最开头的检查,
    不是被 swap 的 stale_live_wal 兜住后返回 8; 不产生新库文件, src 指纹不变。

    变异: 删掉开头这条检查 -> 红 (rc 不再恰好是 7 —— 本用例断言 rc 恰为 7, 不是"非 0")。
    """
    src = tmp_path / "testdbw3.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    (src.with_name(src.name + ".wal")).write_bytes(b"stale-wal-from-crash")
    fp_before = db_compact.file_fingerprint(src)

    rc = db_compact.run("testdbw3", execute=True)
    assert rc == 7, "src.wal 残留必须在最开头就拒绝, rc=7"

    new = src.with_name("testdbw3_compact.duckdb")
    assert not new.exists(), "开头就该拒绝, 不该走到建新库这一步"
    assert db_compact.file_fingerprint(src) == fp_before


def test_w4_src_changed_during_compaction_refuses_swap(tmp_path, monkeypatch):
    """W4 (cut_qfq_fresh_file_swap M3): 对账通过后、真正换名前 src 被另一个写者改了
    (写入并 checkpoint) -> 换名必须被拒 (指纹不符), rc=8, src 那次改动没丢, 新库文件已删。

    变异: 把 expected 传 None 或跳过比对 -> 红 (换名会悄悄吃掉那行改动, 不再是"没丢")。
    """
    src = tmp_path / "testdbw4.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    real_swap = db_compact.swap_in_fresh_file

    def _swap_after_late_mutation(build, live, *, expected, keep_bak=None):
        # 模拟"验证已经通过之后, 真正换名前的那个瞬间"另一个写者写入并干净关闭。
        extra = duck_connect(str(live), read_only=False)
        try:
            extra.execute("INSERT INTO a VALUES (888888, 1.0)")
            extra.execute("CHECKPOINT")
        finally:
            extra.close()
        return real_swap(build, live, expected=expected, keep_bak=keep_bak)

    monkeypatch.setattr(db_compact, "swap_in_fresh_file", _swap_after_late_mutation)

    rc = db_compact.run("testdbw4", execute=True)
    assert rc == 8, "对账通过后 src 被改, 换名必须被拒"

    new = src.with_name("testdbw4_compact.duckdb")
    assert not new.exists(), "换名被拒后应删掉 new"

    c = duck_connect(str(src), read_only=True)
    try:
        n = c.execute("SELECT count(*) FROM a WHERE id = 888888").fetchone()[0]
    finally:
        c.close()
    assert n == 1, "src 那次改动不该丢 —— 换名被拒, src 原样保留 (含新行)"


def test_execute_uses_configured_min_free_disk_gb(tmp_path, monkeypatch):
    """磁盘余量门读 backend/config/db_compaction.yaml 的 min_free_disk_gb, 不硬编码。"""
    src = tmp_path / "testdb5.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    class _Cfg:
        min_free_disk_gb = 10_000_000.0  # 荒谬地高 -> 磁盘余量门必定触发

    monkeypatch.setattr(db_compact, "load_db_compaction_config", lambda: _Cfg())

    rc = db_compact.run("testdb5", execute=True)
    assert rc == 6, "磁盘余量门应读配置 min_free_disk_gb (硬编码 10 时本用例测不出来)"


# ═══════════════════════ 追加 2026-09-25 (M11, ruling.md K1/M11): R-a..R-d ═══════════════════════
#
# R-a/R-b 用真实子进程崩溃复现 ruling.md 附录 B 的两种残留 WAL 形态 (不是伪造 .wal 字节,
# 是让 DuckDB 自己写出一份真 WAL 再跳过所有关闭钩子) —— 与 test_w3 用假字节文件的隔离用例
# 互补: test_w3 测"文件存在就拒绝"的机制, R-a/R-b 测"真实崩溃产物确实会被同一机制挡住"。


def _crash_leaving_wal(src: Path, setup_sql: list[str], crash_sql: list[str]) -> None:
    """先用干净连接按 setup_sql 建库并 CHECKPOINT (基线已提交到主文件); 再用真实子进程
    disable_checkpoint_on_shutdown 后跑 crash_sql, `os._exit(0)` 跳过所有关闭钩子——
    DuckDB 已经把 crash_sql 的改动写进 .wal (auto-commit 逐句提交), 但从未 checkpoint
    回主文件, 磁盘上留下真实的 `<db>.wal`。"""
    conn = duck_connect(str(src), read_only=False)
    try:
        for stmt in setup_sql:
            conn.execute(stmt)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    stmts_literal = repr(crash_sql)
    code = (
        "import duckdb, os\n"
        f"conn = duckdb.connect({str(src)!r})\n"
        "conn.execute('PRAGMA disable_checkpoint_on_shutdown')\n"
        f"for stmt in {stmts_literal}:\n"
        "    conn.execute(stmt)\n"
        "os._exit(0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"子进程崩溃复现脚本本身失败: {result.stderr}"


def test_ra_append_only_residual_wal_refuses_before_building_new_file(tmp_path, monkeypatch):
    """R-a: 追加型残留 WAL (10 万行 CHECKPOINT 后子进程再 INSERT 500 行崩溃) ->
    db_compact.run(execute=True) 必须在建 new 之前就查到它, 返回 7; src 指纹不变、
    .wal 仍在、无新库文件 (旧版行为: rc=0 且重开后 500 行静默重复, ruling.md 附录 B 已实测)。

    变异: 删掉开头 src.wal 检查 -> 红 (rc 不再是 7)。
    """
    src = tmp_path / "testdbra.duckdb"
    _crash_leaving_wal(
        src,
        setup_sql=[
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v DOUBLE)",
            "INSERT INTO t SELECT i, i*1.0 FROM range(100000) x(i)",
        ],
        crash_sql=["INSERT INTO t SELECT i, i*1.0 FROM range(100000, 100500) x(i)"],
    )
    src_wal = src.with_name(src.name + ".wal")
    assert src_wal.exists(), "前提自检: 子进程确实留下了真实 .wal"

    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)
    fp_before = db_compact.file_fingerprint(src)

    rc = db_compact.run("testdbra", execute=True)
    assert rc == 7

    assert db_compact.file_fingerprint(src) == fp_before
    assert src_wal.exists()
    assert not src.with_name("testdbra_compact.duckdb").exists()


def test_rb_delete_update_residual_wal_refuses_and_src_stays_openable(tmp_path, monkeypatch):
    """R-b: 删改型残留 WAL (DELETE 一半 + INSERT + UPDATE 后崩溃) -> 同样返回 7, 且
    src 仍可正常打开、行数等于崩溃前(WAL 里)已提交的状态 (WAL 在只读打开时于内存回放,
    ruling.md T-实测同型)。

    变异: 删掉开头 src.wal 检查 -> 红。
    """
    src = tmp_path / "testdbrb.duckdb"
    _crash_leaving_wal(
        src,
        setup_sql=[
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v DOUBLE)",
            "INSERT INTO t SELECT i, i*1.0 FROM range(1000) x(i)",
        ],
        crash_sql=[
            "DELETE FROM t WHERE id < 500",
            "INSERT INTO t SELECT i, i*1.0 FROM range(1000, 1200) x(i)",
            "UPDATE t SET v = v + 1.0 WHERE id >= 500 AND id < 600",
        ],
    )
    src_wal = src.with_name(src.name + ".wal")
    assert src_wal.exists(), "前提自检: 子进程确实留下了真实 .wal"

    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)
    fp_before = db_compact.file_fingerprint(src)

    rc = db_compact.run("testdbrb", execute=True)
    assert rc == 7

    assert db_compact.file_fingerprint(src) == fp_before
    assert src_wal.exists()
    assert not src.with_name("testdbrb_compact.duckdb").exists()

    c = duck_connect(str(src), read_only=True)
    try:
        n = c.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        c.close()
    assert n == 1000 - 500 + 200, "崩溃前已提交 (WAL 里) 的 DELETE+INSERT 状态不该丢也不该多"


def test_rc_clean_compaction_leaves_no_wal_no_new_no_bak(tmp_path, monkeypatch):
    """R-c: 无 WAL 的正常压缩 -> rc 0, 重开 count(*)==count(DISTINCT id), 目录里无 .wal、
    无新库残留、无 bak (默认 drop_bak=True)。"""
    src = tmp_path / "testdbrc.duckdb"
    na, _nb = _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    rc = db_compact.run("testdbrc", execute=True)
    assert rc == 0

    c = duck_connect(str(src), read_only=True)
    try:
        n_all = c.execute("SELECT count(*) FROM a").fetchone()[0]
        n_distinct = c.execute("SELECT count(DISTINCT id) FROM a").fetchone()[0]
    finally:
        c.close()
    assert n_all == n_distinct == na

    assert not src.with_name(src.name + ".wal").exists()
    assert not src.with_name("testdbrc_compact.duckdb").exists()
    assert not src.with_name("testdbrc_precompact_bak.duckdb").exists()


def test_rd_reconciliation_failure_then_clean_retry_succeeds_no_more_rc4(tmp_path, monkeypatch):
    """R-d: 对账失败(基线读完后源库又多一行) -> rc=5, 目录里无新库文件; 紧接着(同一个
    monkeypatch 过的 _db_path, 不再注入行数漂移)再跑一次正常压缩 -> rc 0 (不再出现
    旧版的 rc=4——那条早退路径已删, 上次失败已把 new 清干净)。

    变异: 注释掉 rc=5 分支里的 `_discard_new(new, new_wal)` -> 见下方 test 的姊妹用例
    `test_rd_isolated_...`(专测"开头残留清理"这一条独立防线, 隔离掉 rc=5 分支自己的
    清理), 两条一起覆盖 M11 要求的两处清理点。
    """
    src = tmp_path / "testdbrd.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    orig_connect = db_compact.duck_connect
    state = {"patched_first_call": False}

    def _connect(path, read_only=False):
        conn = orig_connect(path, read_only=read_only)
        if read_only and not state["patched_first_call"]:
            state["patched_first_call"] = True
            return _MutateOnClose(conn, Path(path))
        return conn

    monkeypatch.setattr(db_compact, "duck_connect", _connect)
    rc1 = db_compact.run("testdbrd", execute=True)
    assert rc1 == 5

    new = src.with_name("testdbrd_compact.duckdb")
    assert not new.exists(), "M11: rc=5 失败路径必须清掉 new"

    monkeypatch.setattr(db_compact, "duck_connect", orig_connect)  # 不再注入行数漂移
    rc2 = db_compact.run("testdbrd", execute=True)
    assert rc2 == 0, "M11: 不再出现 rc=4 —— 干净重试应当成功"


def test_rd_isolated_residual_new_from_prior_crash_is_cleaned_before_rebuild(tmp_path, monkeypatch):
    """R-d 的隔离变体, 专测「开头遇到残留 new 视为上次失败, 先清再继续」这一条独立防线
    (与上面 rc=5 分支自己的清理是两处不同代码, 各自隔离测): 直接在磁盘上放一个陈旧的
    `new` 文件 (模拟被 kill -9 / 断电中断、连 except 都没跑到的极端崩溃, 不经过 run()
    自己的任何失败分支), 不 monkeypatch 制造行数漂移 —— 唯一满足的门控条件是"new 残留"。

    变异: 把开头 `if new.exists() or new_wal.exists(): _discard_new(...)` 整段删掉 ->
    红 (旧版行为: return 4, 永久卡死)。
    """
    src = tmp_path / "testdbrd2.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    stale_new = src.with_name("testdbrd2_compact.duckdb")
    stale_new.write_bytes(b"leftover-from-a-kill-9-crash-not-a-real-duckdb-file")

    rc = db_compact.run("testdbrd2", execute=True)
    assert rc == 0, "M11: 开头遇到残留 new 应先清再重建, 不再 return 4"
    assert src.exists()


class _RaiseOnDetach:
    """代理: 真实读写连接, 但在 `DETACH src` 这句上抛异常——模拟 M11「任何异常」这
    一类失败路径 (与 rc=5 的行数对不齐、rc=8 的 SwapRefused 是两个不同触发点, 隔离测)。
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.strip().upper() == "DETACH SRC":
            raise RuntimeError("boom-injected-for-m11-any-exception-path-test")
        return self._conn.execute(sql, *args, **kwargs)


def test_m11_any_exception_after_new_built_still_cleans_up_new(tmp_path, monkeypatch):
    """M11「任何异常」这一类失败路径的隔离用例 (其它全满足, 只有这一条——建库过程中途
    抛出一个跟行数对账、跟换名都无关的异常): 建 new 期间 (DETACH 那一步) 注入
    RuntimeError, 断言异常照常往外抛 (不吞), 但 new/new.wal 已被清掉——不是只有
    rc=5/SwapRefused 两条命名路径才清, "任何异常"也必须清。

    变异: 把 `except Exception: _discard_new(new, new_wal); raise` 整段删掉 -> 红
    (new 残留在磁盘上)。
    """
    src = tmp_path / "testdbm11exc.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    orig_connect = db_compact.duck_connect

    def _connect(path, read_only=False):
        conn = orig_connect(path, read_only=read_only)
        if not read_only:
            return _RaiseOnDetach(conn)
        return conn

    monkeypatch.setattr(db_compact, "duck_connect", _connect)

    with pytest.raises(RuntimeError, match="boom-injected"):
        db_compact.run("testdbm11exc", execute=True)

    new = src.with_name("testdbm11exc_compact.duckdb")
    assert not new.exists(), "M11: 任何异常都必须清掉 new"
    assert not new.with_name(new.name + ".wal").exists()
    assert src.exists()


# ═══════════════════════ 追加 2026-09-25 (K1/M11 复审 blocking 返修): M11-a/M11-b ═══════════════════════
#
# 审查裁决 sandbox/review_20260924/ruling.md 判 M11 有两条失败路径漏清 new: (a) 磁盘余量门
# 排在"开头残留清理"之前, 残留自己占的盘会被误判成"真没盘", 永久卡在 rc=6; (b) 换名阶段
# 只捕获 SwapRefused, os.replace/os.link 抛出的其它异常 (如 ENOSPC) 会跳过清理直接外抛。
# 下面两条各自隔离对应的门控条件 (其它全满足只违反这一条)。


def test_m11a_leftover_new_freed_before_disk_gate_reevaluated(tmp_path, monkeypatch):
    """M11-a: 开头发现残留 new 时必须先清掉它再重新量磁盘余量, 不能拿清理前的旧值去
    过 min_free_disk_gb 门——旧值里还算着残留文件占的盘, 会把"上次崩溃留下的垃圾"
    误判成"真的没盘"(compact_rc6.py repro), 永久卡在 rc=6, 残留也从未被删。

    用 monkeypatch shutil.disk_usage 模拟"清理前 free 不够 (10G) / 清理后 free 够
    (100G)": 只有磁盘门读的是清理后的新值, 本用例才能拿到 rc=0 且残留已删。

    变异: 把磁盘余量门挪回残留清理之前 (旧顺序) -> 红 (rc 变 6, 且残留仍在磁盘上, 因为
    旧代码那条清理分支在早退之后从未被执行到)。
    """
    src = tmp_path / "testdbm11a.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    stale_new = src.with_name("testdbm11a_compact.duckdb")
    stale_new.write_bytes(b"leftover-from-a-kill-9-crash-eating-disk-space")

    class _Cfg:
        min_free_disk_gb = 50.0

    monkeypatch.setattr(db_compact, "load_db_compaction_config", lambda: _Cfg())

    class _Usage:
        def __init__(self, free_bytes):
            self.free = free_bytes

    calls = {"n": 0}

    def _fake_disk_usage(path):
        calls["n"] += 1
        # 第一次量 (清理前, 残留还占着盘): 不够。第二次量 (清理之后): 够。
        return _Usage(10e9) if calls["n"] == 1 else _Usage(100e9)

    monkeypatch.setattr(db_compact.shutil, "disk_usage", _fake_disk_usage)

    rc = db_compact.run("testdbm11a", execute=True)

    assert calls["n"] >= 2, "前提自检: 残留存在时磁盘余量必须被重新量一次 (不是只量一次就定死)"
    assert rc == 0, "残留清掉之后应重新量磁盘、用新值过门, 不该被清理前的旧值卡在 rc=6"
    assert not stale_new.exists(), "残留必须已被清掉"


def test_m11b_swap_oserror_besides_swaprefused_still_cleans_up_new(tmp_path, monkeypatch):
    """M11-b: 换名阶段除了 SwapRefused 之外的异常 (os.replace/os.link 抛出的 OSError,
    如磁盘满 ENOSPC) 同样必须清掉 new/.wal 再往外抛——旧代码只 except SwapRefused,
    这类异常会跳过清理直接外抛, 让 new 永久残留 (compact_swap_oserror.py repro:
    注入 os.replace OSError)。

    变异: 删掉 SwapRefused 处理之后新增的 `except Exception: _discard_new(...); raise`
    整段 -> 红 (new 残留在磁盘上, 异常仍照常外抛但清理动作消失)。
    """
    src = tmp_path / "testdbm11b.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    def _raise_oserror(build, live, *, expected, keep_bak=None):
        raise OSError(28, "No space left on device")  # ENOSPC, 模拟注入在 os.replace 上

    monkeypatch.setattr(db_compact, "swap_in_fresh_file", _raise_oserror)

    with pytest.raises(OSError):
        db_compact.run("testdbm11b", execute=True)

    new = src.with_name("testdbm11b_compact.duckdb")
    assert not new.exists(), "M11: SwapRefused 之外的异常也必须清掉 new"
    assert not new.with_name(new.name + ".wal").exists()
    assert src.exists(), "src 本身不该被动过 (真正的 os.replace 从未跑到, 已被 monkeypatch 拦截)"


# ═══════════════ 追加 2026-09-25 (返修 blocking review, sandbox/churn_fix_20260919): M11-lock ═══════════════
#
# 找茬报告: db_compact 的 CLI 入口 `main()` 不取 writer_lock —— 两个并发 `--execute`
# (人手动跑 + 日更同时跑) 会一个把另一个建到一半的 new 换名进 src、丢表, 前一个还各自
# 报一次成功。M11 那批测试全部经 `db_compact.run()` 直调, 不经 `main()`, 测不到这条。


def test_m11lock_writer_lock_busy_refuses_cli_and_leaves_planted_new_untouched(tmp_path, monkeypatch):
    """M11-lock: 另一个 owner 正持有项目写锁时, `main()` 必须立即拒绝 (rc=4)、不碰任何
    文件——包括一个模拟"上次崩溃留下"的残留 new (本该由 run() 内部清理, 但锁忙时连
    run() 都不该进去, 这个文件更不该被碰)。

    变异: 去掉 main() 里的 `with writer_lock("db_compact")` 包裹 -> 红 (main() 会照常
    往下跑 run(), 锁忙时也能"成功"压缩, 不再是隔离互斥)。
    """
    from services.writer_lock import WRITER_LOCK_PATH_ENV
    from services.writer_lock import writer_lock as real_writer_lock

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "m11lock_writer.lock"))

    src = tmp_path / "lockbusy_testdb.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)
    stale_new = src.with_name("lockbusy_testdb_compact.duckdb")
    stale_new.write_bytes(b"planted-residual-should-be-left-alone-while-lock-busy")
    stale_bytes = stale_new.read_bytes()

    monkeypatch.setattr(sys, "argv", ["db_compact.py", "--db", "lockbusy_testdb", "--execute"])

    with real_writer_lock("other-owner-holding-the-window"):
        with pytest.raises(SystemExit) as exc_info:
            db_compact.main()

    assert exc_info.value.code == 4, "锁忙必须以 rc=4 退出, 不能悄悄往下跑 run()"
    assert stale_new.exists() and stale_new.read_bytes() == stale_bytes, (
        "锁忙时 main() 不该碰任何文件, 包括本该由 run() 清理的残留 new"
    )
    assert src.exists()


def test_m11lock_dry_run_does_not_take_writer_lock(tmp_path, monkeypatch):
    """M11-lock 隔离对照: dry-run (不带 --execute) 只读, 不取写锁 —— 即便另一个 owner
    正持有写锁, dry-run 仍能跑通 (与 build_price_kline_qfq_tushare.py 的 --check-only
    同一原则)。与上一条互补: 上一条测"忙时 --execute 必须被挡", 本条测"忙时 dry-run
    不该被误伤"。"""
    from services.writer_lock import WRITER_LOCK_PATH_ENV
    from services.writer_lock import writer_lock as real_writer_lock

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "m11lock_dryrun_writer.lock"))

    src = tmp_path / "lockbusy_dryrun_testdb.duckdb"
    _build_src(src)
    monkeypatch.setattr(db_compact, "_db_path", lambda alias: src)

    monkeypatch.setattr(sys, "argv", ["db_compact.py", "--db", "lockbusy_dryrun_testdb"])

    with real_writer_lock("other-owner-holding-the-window"):
        with pytest.raises(SystemExit) as exc_info:
            db_compact.main()

    assert exc_info.value.code == 0, "dry-run 不取写锁, 即便另一个 owner 持锁也不该被挡"
