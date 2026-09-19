"""feature_store / DuckDB compact helper — 日更 store 阶段统一压缩钩子 (cut_db_compaction 2026-09-19)。

取代已删的点状 maybe_compact_alias (原 3 个调用方: build_price_kline_qfq_tushare.
compact_market_after_ctas / institution_profile.rebuild_all / rally_gt.rebuild)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services.duck_adapter import connect as duck_connect

_REPO = Path(__file__).resolve().parents[3]


def _load_db_compact_module():
    """独立加载一份 db_compact.py 模块实例, 可安全就地打补丁 _db_path 不影响其它测试。"""
    spec = importlib.util.spec_from_file_location(
        "db_compact_for_test", _REPO / "backend" / "scripts" / "db_compact.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── A1 变体: 隔离用例 (每条只违反一个条件) ───────────────────────────────────────


def test_compact_if_bloated_skips_below_threshold(tmp_path, monkeypatch):
    from services import duckdb_compact as dc

    db_file = tmp_path / "x.duckdb"
    db_file.write_bytes(b"stub")

    def _boom(*_a, **_k):
        raise AssertionError("compact must not run below threshold")

    stub = type("M", (), {"_db_path": staticmethod(lambda alias: db_file), "run": staticmethod(_boom)})
    monkeypatch.setattr(dc, "_load_db_compact", lambda: stub)
    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 3.0)

    result = dc.compact_if_bloated("x", trigger_free_block_pct=5.0)
    assert result["attempted"] is False
    assert result["returncode"] is None
    assert result["free_pct_before"] == 3.0
    assert result["size_before_bytes"] == result["size_after_bytes"] == db_file.stat().st_size


def test_compact_if_bloated_runs_at_or_above_threshold(tmp_path, monkeypatch):
    from services import duckdb_compact as dc

    db_file = tmp_path / "x.duckdb"
    db_file.write_bytes(b"stub")
    calls = {"n": 0}

    def _run(alias, execute=False, drop_bak=False):
        calls["n"] += 1
        assert execute is True
        assert drop_bak is True
        return 0

    stub = type("M", (), {"_db_path": staticmethod(lambda alias: db_file), "run": staticmethod(_run)})
    monkeypatch.setattr(dc, "_load_db_compact", lambda: stub)
    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 62.5)

    result = dc.compact_if_bloated("x", trigger_free_block_pct=5.0)
    assert result["attempted"] is True
    assert result["returncode"] == 0
    assert calls["n"] == 1


def test_compact_if_bloated_missing_db_is_not_attempted(tmp_path, monkeypatch):
    """红线3: 库文件不存在 -> 缺失传播为缺失 (attempted=False, free_pct_before=None), 不当失败处理。"""
    from services import duckdb_compact as dc

    missing = tmp_path / "does_not_exist.duckdb"

    def _boom(*_a, **_k):
        raise AssertionError("compact must not run on a missing db")

    stub = type("M", (), {"_db_path": staticmethod(lambda alias: missing), "run": staticmethod(_boom)})
    monkeypatch.setattr(dc, "_load_db_compact", lambda: stub)

    result = dc.compact_if_bloated("x", trigger_free_block_pct=5.0)
    assert result["attempted"] is False
    assert result["free_pct_before"] is None
    assert result["returncode"] is None
    assert result["size_before_bytes"] is None
    assert result["size_after_bytes"] is None


def test_compact_if_bloated_defaults_threshold_from_config(tmp_path, monkeypatch):
    """trigger_free_block_pct=None -> 读 backend/config/db_compaction.yaml, 不硬编码。"""
    from services import duckdb_compact as dc

    db_file = tmp_path / "x.duckdb"
    db_file.write_bytes(b"stub")

    class _Cfg:
        trigger_free_block_pct = 40.0

    monkeypatch.setattr(
        "services.db_compaction_rules.load_db_compaction_config", lambda: _Cfg()
    )
    stub = type(
        "M",
        (),
        {"_db_path": staticmethod(lambda alias: db_file), "run": staticmethod(lambda *a, **k: 0)},
    )
    monkeypatch.setattr(dc, "_load_db_compact", lambda: stub)
    monkeypatch.setattr(dc, "free_block_pct", lambda _alias: 30.0)  # 高于真配置 5.0, 低于桩配置 40.0

    result = dc.compact_if_bloated("x")  # 不传阈值 -> 必须读桩配置的 40.0, 不是真配置的 5.0
    assert result["trigger_free_block_pct"] == 40.0
    assert result["attempted"] is False, "30.0 < 桩配置阈值 40.0, 不该压缩; 若读到硬编码/真配置的 5.0 会误判为该压缩"


# ── A3: 真实夹具库 (不碰生产) —— 空闲块占比高于阈值才压缩, 默认删 bak ──────────────


def _build_bloated_fixture(path: Path, *, n: int = 200_000, keep: int = 5_000) -> None:
    """插入 n 行不可压缩内容 (md5 拼接防字典/RLE 压缩) 再删掉大半, 制造真实死块。"""
    conn = duck_connect(str(path), read_only=False)
    try:
        conn.execute("CREATE TABLE t (id INTEGER, v VARCHAR)")
        conn.execute(
            f"INSERT INTO t SELECT i, md5(i::VARCHAR) || md5((i+1)::VARCHAR) || md5((i+2)::VARCHAR) "
            f"FROM range({n}) t(i)"
        )
        conn.execute("CHECKPOINT")
        conn.execute(f"DELETE FROM t WHERE id < {n - keep}")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


def test_compact_if_bloated_real_fixture_above_threshold_compacts_and_drops_bak(tmp_path, monkeypatch):
    from services import duckdb_compact as dc

    src = tmp_path / "bloatdb.duckdb"
    _build_bloated_fixture(src)

    db_compact_mod = _load_db_compact_module()
    db_compact_mod._db_path = lambda alias: src
    monkeypatch.setattr(dc, "_load_db_compact", lambda: db_compact_mod)

    pct_before = dc.free_block_pct("bloatdb")
    assert pct_before is not None and pct_before >= 5.0, (
        f"夹具库未能制造出 >=5% 死块 (实测 {pct_before}); 调大 fixture 规模"
    )

    result = dc.compact_if_bloated("bloatdb", trigger_free_block_pct=5.0)
    assert result["attempted"] is True
    assert result["returncode"] == 0

    bak = src.with_name("bloatdb_precompact_bak.duckdb")
    assert not bak.exists(), "默认 drop_bak=True, 压缩后 bak 不应残留"
    assert src.exists()

    pct_after = dc.free_block_pct("bloatdb")
    assert pct_after is not None and pct_after < pct_before


def test_compact_if_bloated_real_fixture_below_threshold_leaves_file_untouched(tmp_path, monkeypatch):
    from services import duckdb_compact as dc

    src = tmp_path / "cleandb.duckdb"
    # 小库、无删除 -> 0 free_blocks, 稳低于任何非零阈值。
    conn = duck_connect(str(src), read_only=False)
    try:
        conn.execute("CREATE TABLE t (id INTEGER, v VARCHAR)")
        conn.execute("INSERT INTO t SELECT i, 'x' FROM range(1000) t(i)")
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    db_compact_mod = _load_db_compact_module()
    db_compact_mod._db_path = lambda alias: src
    monkeypatch.setattr(dc, "_load_db_compact", lambda: db_compact_mod)

    pct = dc.free_block_pct("cleandb")
    assert pct is not None and pct < 5.0

    size_before = src.stat().st_size
    mtime_before = src.stat().st_mtime_ns
    result = dc.compact_if_bloated("cleandb", trigger_free_block_pct=5.0)
    assert result["attempted"] is False
    assert result["returncode"] is None

    size_after = src.stat().st_size
    mtime_after = src.stat().st_mtime_ns
    assert size_before == size_after, "低于阈值不应压缩, 文件字节应不变"
    assert mtime_before == mtime_after, "低于阈值不应触碰源文件"
    assert not src.with_name("cleandb_precompact_bak.duckdb").exists()


# ── A8: 点状压缩已删 (静态断言, 覆盖三个原调用方) ─────────────────────────────────


def test_point_compaction_removed_from_former_call_sites():
    inst = (_REPO / "backend" / "services" / "institution_profile.py").read_text(encoding="utf-8")
    rally = (_REPO / "backend" / "services" / "rally_gt.py").read_text(encoding="utf-8")
    qfq = (_REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py").read_text(encoding="utf-8")

    for name, text in (
        ("institution_profile.py", inst),
        ("rally_gt.py", rally),
        ("build_price_kline_qfq_tushare.py", qfq),
    ):
        assert "maybe_compact_alias" not in text, f"{name} 不该再调用已删的点状压缩钩子"
        assert "duckdb_compact" not in text, f"{name} 不该再 import duckdb_compact"
        assert "COMPACT_FREE_PCT" not in text, f"{name} 不该再有点状压缩阈值字面量"
    assert "compact_market_after_ctas" not in qfq, "build_price_kline_qfq_tushare.py 不该再有点状 compact 函数"


def test_maybe_compact_alias_no_longer_exists():
    from services import duckdb_compact as dc

    assert not hasattr(dc, "maybe_compact_alias"), (
        "maybe_compact_alias 已无调用方 (institution_profile/rally_gt 已删调用), "
        "只保留日更步骤要用的 compact_if_bloated"
    )
