"""qfq builder (2026-09-08 self-computed-factor rewrite;
2026-09-24 cut_qfq_fresh_file_swap 建到新文件后原子换名) — 全自带 fixture, 不碰宿主生产库。

覆盖: 单位换算 (vol×100/amount×1000) · 锚点=该股自己最后一行 (非全局 max) · 除权日 ratio
正确 · 血统三列(batch_id/ingested_at/factor_as_of)+新增两列(hfq_factor/config_hash)非空 ·
cross_check 五条判据均可红可绿 (第 5 条 duplicate_grain_n 09-24 新增) · A1-A9 (新文件+换名
+围栏+写锁+索引/物理序) —— 见 sandbox/churn_fix_20260919/build_qfq_fresh_file_swap.md §4.3。
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from conftest import duck_mem

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py"
DB_COMPACT_SCRIPT = REPO / "backend" / "scripts" / "db_compact.py"


@pytest.fixture(autouse=True)
def _isolate_writer_lock(tmp_path, monkeypatch):
    """M2 写锁隔离 (blocking: 5 个 qfq 用例此前拿宿主机真实项目写锁, 见
    sandbox/churn_fix_20260919/build_qfq_fresh_file_swap.md 找茬报告)。

    main() 的建库路径经 `with writer_lock(...)`, 不设 CHUNKYMONKEY_WRITER_LOCK_PATH 时
    锁文件是 tempfile.gettempdir()/chunkymonkey-pipeline-writer.lock —— 和生产
    daily_update/pipeline.run 用的是同一个文件 (本机实测确有其文件, 说明是真在用的
    生产状态, 不是假设)。测试不许读写宿主运行时状态 (feedback-test-must-carry-its-
    own-fixture)。本 fixture 把整个模块每条用例的写锁钉死在各自 tmp_path 下, 并清掉
    可能从上级环境继承来的 lease/fd, 结束时哨兵断言宿主默认锁文件 mtime/内容原样
    未动。个别用例 (如 test_check_only_does_not_take_writer_lock/w5/w5b) 自己会再
    setenv 到同一 tmp_path 下的另一个文件名, 这里的设置只是先手, 不影响它们。
    """
    from services.writer_lock import (
        WRITER_LEASE_ENV,
        WRITER_LOCK_FD_ENV,
        WRITER_LOCK_PATH,
        WRITER_LOCK_PATH_ENV,
    )

    before_exists = WRITER_LOCK_PATH.exists()
    before_bytes = WRITER_LOCK_PATH.read_bytes() if before_exists else None
    before_mtime_ns = WRITER_LOCK_PATH.stat().st_mtime_ns if before_exists else None

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "_autouse_isolated_writer.lock"))
    monkeypatch.delenv(WRITER_LEASE_ENV, raising=False)
    monkeypatch.delenv(WRITER_LOCK_FD_ENV, raising=False)

    yield

    after_exists = WRITER_LOCK_PATH.exists()
    assert after_exists == before_exists, "本模块任何用例都不该新建/删除宿主默认写锁文件"
    if after_exists:
        assert WRITER_LOCK_PATH.read_bytes() == before_bytes, "本模块任何用例都不该改动宿主默认写锁文件的内容"
        assert WRITER_LOCK_PATH.stat().st_mtime_ns == before_mtime_ns, "本模块任何用例都不该改动宿主默认写锁文件的 mtime"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_price_kline_qfq_tushare", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
STOCK_A = [
    ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
    ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, 10.0, 5.0, 1100, 11500),
    # 除权日: prev_close(=10.5)/pre_close(=5.25) = ratio 2.0 (1:1 送转量级)
    ("000001.SZ", date(2024, 1, 4), 5.3, 6.1, 5.2, 6.0, 5.25, 14.3, 2000, 11800),
    ("000001.SZ", date(2024, 1, 5), 6.0, 6.3, 5.9, 6.2, 6.0, 3.33, 1500, 9200),
    # 该股自己最后一行 (锚点)
    ("000001.SZ", date(2024, 1, 8), 6.2, 6.7, 6.1, 6.6, 6.2, 6.45, 1800, 11700),
]
# 更早收盘的第二只股 (最后一行早于 A) —— 用来证锚点是"各自最后一行"而非全局 max 日期。
STOCK_B = [
    ("000002.SZ", date(2024, 1, 2), 19.8, 20.3, 19.7, 20.0, 19.5, 0.0, 500, 20000),
    ("000002.SZ", date(2024, 1, 3), 20.0, 20.4, 19.9, 20.0, 20.0, 0.0, 600, 12000),
    ("000002.SZ", date(2024, 1, 4), 20.0, 21.2, 19.9, 21.0, 20.0, 5.0, 700, 14500),
]


def _write_raw_db(raw_db_path: Path, rows: list[tuple]) -> None:
    raw = duck_mem()
    try:
        raw.execute(
            """
            CREATE TABLE canonical_nominal_ohlcv_daily (
                ts_code TEXT, trade_date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                pre_close DOUBLE, pct_chg DOUBLE, vol DOUBLE, amount DOUBLE
            )
            """
        )
        raw.executemany(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        raw.execute(f"ATTACH '{raw_db_path}' AS disk")
        raw.execute(
            "CREATE TABLE disk.canonical_nominal_ohlcv_daily AS "
            "SELECT * FROM canonical_nominal_ohlcv_daily"
        )
        raw.execute("DETACH disk")
    finally:
        raw.close()


def _build(tmp_path: Path, monkeypatch, rows: list[tuple], *, name: str = "raw"):
    """Wire mod.TUSHARE_DB to a fresh on-disk fixture, build_full into a fresh market db.

    Returns (mod, raw_db_path, market_db_path).
    """
    from services.duck_adapter import connect as duck_connect

    mod = _load_module()
    raw_db = tmp_path / f"{name}.duckdb"
    _write_raw_db(raw_db, rows)
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    mdb = tmp_path / f"{name}_market.duckdb"
    market = duck_connect(str(mdb), read_only=False)
    try:
        mod.build_full(market)
    finally:
        market.close()
    return mod, raw_db, mdb


def _reopen(mod, mdb: Path):
    """Fresh market connection with tr re-attached (sees any post-build mutation to raw)."""
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(str(mdb), read_only=False)
    conn.execute(f"ATTACH IF NOT EXISTS '{mod.TUSHARE_DB}' AS tr (READ_ONLY)")
    return conn


def _build_live_with_secondary_tables(tmp_path: Path, monkeypatch, rows: list[tuple], *, name: str = "prodlike"):
    """构造一份"生产形状"的 live 库并干净关闭: TARGET (经 build_full) +
    dim_schema_version (带 PK) + mart_data_deletion_record (带 2 个索引) +
    v_price_kline_qfq 视图。main()/TUSHARE_DB/MARKET_DB 均已 monkeypatch 好。

    返回 (mod, raw_db_path, live_db_path)。
    """
    from services import market_schema
    from services.duck_adapter import connect as duck_connect

    mod = _load_module()
    raw_db = tmp_path / f"{name}_raw.duckdb"
    _write_raw_db(raw_db, rows)
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))

    live = tmp_path / f"{name}_market.duckdb"
    conn = duck_connect(str(live), read_only=False)
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{raw_db}' AS tr (READ_ONLY)")
        mod.build_full(conn)
        conn.executescript(market_schema.ANALYSIS_KLINE_QFQ_VIEW_DDL)
        conn.execute(
            """
            CREATE TABLE dim_schema_version (
                table_name        TEXT PRIMARY KEY,
                expected_version  TEXT NOT NULL,
                actual_version    TEXT,
                rebuilt_at        TIMESTAMP,
                notes             TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO dim_schema_version VALUES "
            "('price_kline_qfq_tushare','v2',NULL,NULL,NULL), ('other_table','v1',NULL,NULL,NULL)"
        )
        conn.execute(
            """
            CREATE TABLE mart_data_deletion_record (
                record_id TEXT PRIMARY KEY,
                deletion_run_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                delete_scope TEXT NOT NULL,
                key_column TEXT,
                key_value TEXT,
                deleted_rows BIGINT DEFAULT 0,
                deleted_files BIGINT DEFAULT 0,
                deleted_bytes BIGINT DEFAULT 0,
                reason TEXT NOT NULL,
                verification_json TEXT,
                deleted_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX idx_data_deletion_run ON mart_data_deletion_record(deletion_run_id)")
        conn.execute("CREATE INDEX idx_data_deletion_table ON mart_data_deletion_record(table_name, delete_scope)")
        conn.execute(
            "INSERT INTO mart_data_deletion_record VALUES "
            "('r1','run1','t1','scope1',NULL,NULL,0,0,0,'why',NULL,'2024-01-01')"
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()

    monkeypatch.setattr(mod, "MARKET_DB", str(live))
    return mod, raw_db, live


# --------------------------------------------------------------------------- A1-ish: CLI surface


def test_help_lists_expected_flags_only(capsys) -> None:
    mod = _load_module()
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--from-accepted" in out
    assert "--check-only" in out
    assert "--full" in out
    assert "--allow-legacy-fill" not in out
    assert "--incremental" not in out


def test_removed_flags_are_rejected() -> None:
    mod = _load_module()
    for bad in (["--allow-legacy-fill"], ["--incremental"]):
        with pytest.raises(SystemExit) as exc:
            mod.main(bad)
        assert exc.value.code == 2


def test_incremental_and_legacy_fill_machinery_removed() -> None:
    """全量是唯一路径 —— 增量/rewrite 判定/legacy nominal union 整段删除, 不留死代码。"""
    mod = _load_module()
    for name in (
        "build_incremental",
        "build_detail",
        "nominal_source_cte",
        "_NOMINAL_SOURCE_CTE",
        "_has_lineage_columns",
        "_table_exists",
    ):
        assert not hasattr(mod, name), f"{name} should have been deleted"


def test_factor_source_is_self_computed_not_raw_adj_factor() -> None:
    """因子来自 adjust_factor.hfq_sql (自算), 生成的 SQL 里不再出现 raw_tushare_adj_factor。"""
    mod = _load_module()
    cfg = mod.load_config()
    sql = mod.build_select_sql(cfg, batch_id="qfq:test", ingested_at="2026-01-01T00:00:00Z")
    assert "raw_tushare_adj_factor" not in sql
    assert "canonical_nominal_ohlcv_daily" in sql
    assert "hfq_factor" in sql
    assert "ratio_status" in sql


# --------------------------------------------------------------------------- build_full correctness


def test_unit_conversion_and_lineage_columns_all_non_null(tmp_path, monkeypatch) -> None:
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        # 单位换算: vol(手)×100=股, amount(千元)×1000=元 — 抽查 B 的第一行 (无除权, 好手算)。
        row = conn.execute(
            f"SELECT volume, amount FROM {mod.TARGET} WHERE code='000002' AND date='2024-01-02'"
        ).fetchone()
        assert row[0] == pytest.approx(500 * 100.0)
        assert row[1] == pytest.approx(20000 * 1000.0)

        # 血统三列 + 新增两列全表非空。
        nulls = conn.execute(
            f"SELECT count(*) FROM {mod.TARGET} WHERE batch_id IS NULL OR ingested_at IS NULL "
            "OR factor_as_of IS NULL OR hfq_factor IS NULL OR config_hash IS NULL"
        ).fetchone()[0]
        assert nulls == 0

        cfg = mod.load_config()
        chash = conn.execute(f"SELECT DISTINCT config_hash FROM {mod.TARGET}").fetchall()
        assert [r[0] for r in chash] == [cfg.config_hash]
    finally:
        conn.close()


def test_anchor_is_stock_own_last_row_not_global_max_date(tmp_path, monkeypatch) -> None:
    """回归旧 bug: 锚点曾是"因子表全局 per-code 最新行"(可能晚于该股自己最后一条 K 线),
    600069.SH 等 6 股末行因此偏离 nominal 最高 89.7%。新版锚点必须是该股自己最后一行 ——
    B 的最后一行 (2024-01-04) 早于 A 的最后一行 (2024-01-08); B 的 factor_as_of 必须钉在
    B 自己的 2024-01-04, 不是 A 的 2024-01-08 (全局 max)。
    """
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        a_anchor = conn.execute(
            f"SELECT factor_as_of FROM {mod.TARGET} WHERE code='000001' ORDER BY date DESC LIMIT 1"
        ).fetchone()[0]
        b_anchor = conn.execute(
            f"SELECT DISTINCT factor_as_of FROM {mod.TARGET} WHERE code='000002'"
        ).fetchall()
        assert str(a_anchor)[:10] == "2024-01-08"
        assert {str(r[0])[:10] for r in b_anchor} == {"2024-01-04"}

        # 每股末行 close 必须精确等于该股自己 nominal close (锚点定义的直接推论)。
        b_last_close = conn.execute(
            f"SELECT close FROM {mod.TARGET} WHERE code='000002' AND date='2024-01-04'"
        ).fetchone()[0]
        assert abs(float(b_last_close) - 21.0) < 1e-9
    finally:
        conn.close()


def test_ex_rights_day_ratio_and_qfq_price_correct(tmp_path, monkeypatch) -> None:
    """A 在 2024-01-04 有除权事件: prev_close(10.5)/pre_close(5.25)=2.0。
    hfq_factor 累乘: d1=1,d2=1,d3=2,d4=2,d5(锚)=2。qfq[t]=nominal[t]×hfq[t]/2。
    """
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A)
    conn = _reopen(mod, mdb)
    try:
        rows = {
            str(r[0]): (float(r[1]), float(r[2]), str(r[3]))
            for r in conn.execute(
                f"SELECT date, close, hfq_factor, ratio_status FROM {mod.TARGET} "
                "WHERE code='000001' ORDER BY date"
            ).fetchall()
        }
        assert rows["2024-01-02"][2] == "first_day"
        assert rows["2024-01-03"][2] == "no_event"
        assert rows["2024-01-04"][2] == "adjusted"
        assert rows["2024-01-04"][1] == pytest.approx(2.0)

        expected_qfq_close = {
            "2024-01-02": 10.0 * 1.0 / 2.0,
            "2024-01-03": 10.5 * 1.0 / 2.0,
            "2024-01-04": 6.0 * 2.0 / 2.0,
            "2024-01-05": 6.2 * 2.0 / 2.0,
            "2024-01-08": 6.6 * 2.0 / 2.0,
        }
        for d, expected in expected_qfq_close.items():
            assert rows[d][0] == pytest.approx(expected, abs=1e-9), d

        # open 也同一因子重标定 (不是只有 close) — 抽查除权日。
        open_d3 = conn.execute(
            f"SELECT open FROM {mod.TARGET} WHERE code='000001' AND date='2024-01-04'"
        ).fetchone()[0]
        assert float(open_d3) == pytest.approx(5.3 * 2.0 / 2.0, abs=1e-9)
    finally:
        conn.close()


def test_missing_pre_close_propagates_to_null_not_dropped_or_zero(tmp_path, monkeypatch) -> None:
    """红线3: pre_close 缺失 → 该股此后 hfq_factor/价格列全 NULL, 行不删、不填 0。"""
    broken = [
        ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
        ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, None, 5.0, 1100, 11500),
        ("000001.SZ", date(2024, 1, 4), 5.3, 6.1, 5.2, 6.0, 5.25, 14.3, 2000, 11800),
    ]
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, broken, name="broken")
    conn = _reopen(mod, mdb)
    try:
        n_rows = conn.execute(f"SELECT count(*) FROM {mod.TARGET} WHERE code='000001'").fetchone()[0]
        assert n_rows == 3  # 行保留, 不删

        d2 = conn.execute(
            f"SELECT close, hfq_factor, ratio_status FROM {mod.TARGET} "
            "WHERE code='000001' AND date='2024-01-03'"
        ).fetchone()
        assert d2[0] is None  # 价格列 NULL, 不填 0
        assert d2[1] is None
        assert d2[2] == "missing_input"

        d3 = conn.execute(
            f"SELECT close, hfq_factor FROM {mod.TARGET} WHERE code='000001' AND date='2024-01-04'"
        ).fetchone()
        assert d3[0] is None  # poison 向后传播, 即便 d3 自己 pre_close 正常
        assert d3[1] is None
    finally:
        conn.close()


# --------------------------------------------------------------------------- cross_check: 四条判据双向验证


def test_cross_check_all_four_pass_on_clean_fixture(tmp_path, monkeypatch) -> None:
    """绿: 干净 fixture 上四条判据全部过。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["set_missing_in_qfq"] == 0
        assert cc["set_extra_in_qfq"] == 0
        assert cc["anchor_close_mismatch_n"] == 0
        assert cc["null_factor_n"] == 0
        assert cc["qfq_max_date"] == cc["canonical_max_date"] == "2024-01-08"
    finally:
        conn.close()


def test_cross_check_set_equality_red_when_canonical_row_deleted_after_build(
    tmp_path, monkeypatch
) -> None:
    """红 (spec 指定注入法): qfq 已建好后, canonical 少了一天 → qfq 比 canonical 多出一条,
    集合不再相等 —— 这是"比集合不比 COUNT"要抓的那类退化 (行数可能凑巧没变, 成员变了)。
    """
    from services.duck_adapter import connect as duck_connect

    mod, raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "DELETE FROM canonical_nominal_ohlcv_daily WHERE ts_code='000002.SZ' AND trade_date=DATE '2024-01-02'"
        )
    finally:
        raw_w.close()

    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["set_extra_in_qfq"] >= 1
        assert cc["set_missing_in_qfq"] == 0
    finally:
        conn.close()


def test_cross_check_anchor_mismatch_red_when_last_row_corrupted(tmp_path, monkeypatch) -> None:
    """红: 直接改坏 qfq 表里某股末行 close (模拟锚点算错/写坏) → 判据 #2 抓到。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        conn.execute(
            f"UPDATE {mod.TARGET} SET close = close + 1.0 "
            "WHERE code='000001' AND date='2024-01-08'"
        )
        cc = mod.cross_check(conn)
        assert cc["anchor_close_mismatch_n"] >= 1
    finally:
        conn.close()


def test_cross_check_null_factor_red_when_pre_close_missing(tmp_path, monkeypatch) -> None:
    """红 (spec 指定注入法): 把某股某天 pre_close 置 NULL → hfq_factor NULL 计数 > 0。"""
    broken = [
        ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
        ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, None, 5.0, 1100, 11500),
    ]
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, broken, name="nullfactor")
    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["null_factor_n"] >= 1
    finally:
        conn.close()


def test_cross_check_max_date_red_when_canonical_gets_new_later_row(tmp_path, monkeypatch) -> None:
    """红: qfq 建完后 canonical 又推进一天 (未重建 qfq) → qfq_max_date 落后于 canonical。"""
    from services.duck_adapter import connect as duck_connect

    mod, raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    raw_w = duck_connect(str(raw_db), read_only=False)
    try:
        raw_w.execute(
            "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
            "('000001.SZ', DATE '2024-01-09', 6.6,6.8,6.5,6.7,6.6,1.5,1000,9000)"
        )
    finally:
        raw_w.close()

    conn = _reopen(mod, mdb)
    try:
        cc = mod.cross_check(conn)
        assert cc["qfq_max_date"] != cc["canonical_max_date"]
        assert cc["canonical_max_date"] == "2024-01-09"
        assert cc["qfq_max_date"] == "2024-01-08"
    finally:
        conn.close()


# --------------------------------------------------------------------------- main() end-to-end


def test_main_check_only_does_not_rebuild(tmp_path, monkeypatch, capsys) -> None:
    """--check-only 只对账, 不 DROP+CTAS —— 表内容 (batch_id) 应保持建表时的原值。"""
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        batch_before = conn.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        conn.close()

    monkeypatch.setattr(mod, "MARKET_DB", str(mdb))
    rc = mod.main(["--check-only"])
    assert rc == 0

    conn = _reopen(mod, mdb)
    try:
        batch_after = conn.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        conn.close()
    assert batch_before == batch_after


def test_main_full_rebuild_from_scratch_returns_zero(tmp_path, monkeypatch) -> None:
    """main(['--from-accepted','--full']) 端到端: 空 market db → 建表 → verdict PASS → rc 0.

    MARKET_DB 重定向到 tmp_path (非生产路径)。点状压缩 (compact_market_after_ctas) 已删
    (cut_db_compaction 2026-09-19) —— 死块回收交给日更 store 阶段统一处理, 本测试只验证
    build+cross_check 端到端返回 0, 不再涉及任何 compact 分支。
    """
    mod = _load_module()
    raw_db = tmp_path / "raw.duckdb"
    _write_raw_db(raw_db, STOCK_A + STOCK_B)
    monkeypatch.setattr(mod, "TUSHARE_DB", str(raw_db))
    monkeypatch.setattr(mod, "MARKET_DB", str(tmp_path / "market.duckdb"))

    rc = mod.main(["--from-accepted", "--full"])
    assert rc == 0


# --------------------------------------------------------------------------- A1-A9 / W5:
# 建到新文件 + 校验 + 原子换名 (cut_qfq_fresh_file_swap, 2026-09-24)。


def test_a1_success_swaps_to_fresh_inode_with_zero_free_blocks(tmp_path, monkeypatch) -> None:
    """A1: main() 成功后, live 的 free_blocks==0 且 inode 变了 (真换了文件, 不是原地改)。"""
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    ino_before = live.stat().st_ino

    rc = mod.main([])
    assert rc == 0
    assert live.stat().st_ino != ino_before

    conn = duck_connect(str(live), read_only=True)
    try:
        fb = conn.execute(
            "SELECT free_blocks FROM pragma_database_size() WHERE database_name = ?",
            [live.stem],
        ).fetchone()[0]
        assert fb == 0
    finally:
        conn.close()

    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()
    assert not build.with_name(build.name + ".wal").exists()


def test_a2_cross_check_review_leaves_production_untouched(tmp_path, monkeypatch) -> None:
    """A2: cross_check REVIEW 时生产文件不变 (batch_id 相同、inode 相同), build/.wal 不存在, rc=2。

    退化必须是"即便重新全量建一遍也依然红"的那种 (main() 每次都是全新 build, 不是在旧表上
    打补丁) —— canonical 缺一天/改坏 qfq 表这两种旧用例的注入法都会被下一次全量重建自愈,
    测不出 main() 端到端的 REVIEW 分支。改用源头缺陷: pre_close 缺失 → hfq_factor 永远
    NULL, 任何一次全量重建都还是红 (与 test_cross_check_null_factor_red_when_pre_close_missing
    同一个真实故障形态)。
    """
    from services.duck_adapter import connect as duck_connect

    broken = [
        ("000001.SZ", date(2024, 1, 2), 9.9, 10.1, 9.8, 10.0, 9.8, 0.0, 1000, 9900),
        ("000001.SZ", date(2024, 1, 3), 10.0, 10.6, 9.9, 10.5, None, 5.0, 1100, 11500),
        ("000001.SZ", date(2024, 1, 4), 5.3, 6.1, 5.2, 6.0, 5.25, 14.3, 2000, 11800),
    ]
    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, broken, name="a2")
    before = duck_connect(str(live), read_only=True)
    try:
        batch_before = before.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        before.close()
    ino_before = live.stat().st_ino

    rc = mod.main([])
    assert rc == 2

    assert live.stat().st_ino == ino_before  # 生产文件一字未动
    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()
    assert not build.with_name(build.name + ".wal").exists()

    after = duck_connect(str(live), read_only=True)
    try:
        batch_after = after.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
    finally:
        after.close()
    assert batch_after == batch_before


def test_a3_stale_live_wal_refuses_swap(tmp_path, monkeypatch) -> None:
    """A3: 生产 .wal 残留时拒绝换名 (经 SwapRefused(stale_live_wal)), rc=3, live 不变, build 不存在。"""
    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    ino_before = live.stat().st_ino
    (live.with_name(live.name + ".wal")).write_bytes(b"stale-live-wal-from-crashed-writer")

    rc = mod.main([])
    assert rc == 3

    assert live.stat().st_ino == ino_before
    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()


def test_a4_secondary_tables_and_view_carried_over_byte_for_byte(tmp_path, monkeypatch) -> None:
    """A4: 小账表原样带过去 (行集合相等 + dim_schema_version 仍有 PK), 视图可查。"""
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B, name="a4")
    before = duck_connect(str(live), read_only=True)
    try:
        # tuple() 而非直接比较 Row 包装对象 —— 不同连接返回的 Row 实例即便字段值全等,
        # 也不保证 __eq__ 语义, 实测过 (直接比较 Row 会假红)。
        schema_before = [tuple(r) for r in before.execute("SELECT * FROM dim_schema_version ORDER BY table_name").fetchall()]
        deletion_before = [tuple(r) for r in before.execute("SELECT * FROM mart_data_deletion_record ORDER BY record_id").fetchall()]
    finally:
        before.close()

    rc = mod.main([])
    assert rc == 0

    after = duck_connect(str(live), read_only=True)
    try:
        schema_after = [tuple(r) for r in after.execute("SELECT * FROM dim_schema_version ORDER BY table_name").fetchall()]
        deletion_after = [tuple(r) for r in after.execute("SELECT * FROM mart_data_deletion_record ORDER BY record_id").fetchall()]
        assert schema_after == schema_before
        assert deletion_after == deletion_before

        pk_n = after.execute(
            "SELECT count(*) FROM duckdb_constraints() "
            "WHERE table_name='dim_schema_version' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0]
        assert pk_n == 1

        view_rows = after.execute("SELECT count(*) FROM v_price_kline_qfq").fetchone()[0]
        assert view_rows > 0
    finally:
        after.close()


def test_a5_target_has_no_index_and_is_physically_clustered(tmp_path, monkeypatch) -> None:
    """A5: TARGET 上 duckdb_indexes() 为空, 物理序 = (code,date)。

    前提自检 (spec 原话: fixture 必须先验证"无 ORDER BY 时确实乱序", 否则用例测不到它声称
    的东西): 用同一份 SELECT 但不加 ORDER BY 建一张探测表, 断言它天然不是 (code,date) 有序。
    """
    # B 先于 A 插入 (与两者的 code 字典序相反): 证明"不加 ORDER BY"时输出确实跟着扫描/
    # 插入序走, 不天然按 code 字典序排列 —— 若用 STOCK_A + STOCK_B (插入序恰好=字典序)
    # 这条前提自检会通不过 (实测过, disorder_n_probe==0), 测不出 ORDER BY 的效果。
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_B + STOCK_A, name="a5")
    conn = _reopen(mod, mdb)
    try:
        cfg = mod.load_config()
        raw_sql = mod.build_select_sql(cfg, batch_id="probe", ingested_at="2024-01-01T00:00:00Z")
        conn.execute(f"CREATE TEMP TABLE _unordered_probe AS {raw_sql}")
        disorder_n_probe = conn.execute(
            "SELECT count(*) FROM ("
            "  SELECT rowid, lag(rowid) OVER (ORDER BY code, date) p FROM _unordered_probe"
            ") WHERE p > rowid"
        ).fetchone()[0]
        assert disorder_n_probe > 0, "fixture 天然已按 (code,date) 有序, 测不出 ORDER BY 的效果, 换个 fixture"

        idx_n = conn.execute(
            f"SELECT count(*) FROM duckdb_indexes() WHERE table_name = '{mod.TARGET}'"
        ).fetchone()[0]
        assert idx_n == 0

        disorder_n_target = conn.execute(
            f"SELECT count(*) FROM ("
            f"  SELECT rowid, lag(rowid) OVER (ORDER BY code, date) p FROM {mod.TARGET}"
            f") WHERE p > rowid"
        ).fetchone()[0]
        assert disorder_n_target == 0
    finally:
        conn.close()


def test_a6_cross_check_duplicate_grain_red_when_grain_repeated(tmp_path, monkeypatch) -> None:
    """A6: cross_check 第 5 条 —— (code,date) 重复行数 == 0, 重复即 REVIEW。

    旧 4 条对重复是盲的 (它们的 qfq_set 用了 DISTINCT, 重复后集合看着仍然相等) ——
    这条断言顺带证明这一点: 只看旧 4 条会误判为 PASS。
    """
    mod, _raw_db, mdb = _build(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    conn = _reopen(mod, mdb)
    try:
        conn.execute(f"INSERT INTO {mod.TARGET} SELECT * FROM {mod.TARGET} LIMIT 1")
        cc = mod.cross_check(conn)
        assert cc["duplicate_grain_n"] >= 1
        old_four_pass = (
            cc["set_missing_in_qfq"] == 0
            and cc["set_extra_in_qfq"] == 0
            and cc["anchor_close_mismatch_n"] == 0
            and cc["null_factor_n"] == 0
        )
        assert old_four_pass, "本用例要证明的正是: 旧 4 条对重复行是盲的 (仍然全绿)"
        assert not mod._cross_check_ok(cc)
    finally:
        conn.close()


def test_a7_no_direct_rename_replace_calls_static() -> None:
    """A7 (静态部分): 换名只经 swap_in_fresh_file —— 两个脚本里不许再出现
    直接的 os.replace(...) / .rename(...) / os.rename(...) 字面调用。"""
    for path in (SCRIPT, DB_COMPACT_SCRIPT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            attr = node.func.attr
            is_os_replace = attr == "replace" and (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            )
            is_any_rename = attr == "rename"  # Path.rename(...) / os.rename(...) 都命中这个 attr
            if is_os_replace or is_any_rename:
                pytest.fail(
                    f"{path.name}:{node.lineno}: 发现直接换名调用 .{attr}(...), "
                    "换名必须经 services.duckdb_file_swap.swap_in_fresh_file"
                )


def test_a7_old_reader_keeps_old_snapshot_new_reader_sees_new_value(tmp_path, monkeypatch) -> None:
    """A7 (动态部分): 换名是 os.replace 而非原地改写 —— 这是 build_qfq_fresh_file_swap.md
    找茬报告点名缺失的隔离用例本体 (静态部分见 test_a7_no_direct_rename_replace_calls_static,
    只扫代码里没有裸 os.replace/rename; 两者合起来才是完整的 A7): 在 main() 之前对 live 开一个
    read_only 连接 R 读到旧 batch_id; main() 成功换名后, R 再查同一张表仍是旧值、不抛异常
    (证明它仍在读 os.replace 前打开的那份旧 inode 快照); 另开一个子进程新连接读到的是新
    batch_id (实测过: 同进程内重新 connect 同一路径会命中 duckdb 按路径缓存的实例, 测不出
    "新进程看见新文件"这件事, 必须真开子进程)。
    """
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    monkeypatch.setattr(mod, "_default_batch_id", lambda ingested_at: "qfq:a7_new_batch_marker:test")

    reader = duck_connect(str(live), read_only=True)
    try:
        old_batch_id = reader.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
        assert old_batch_id != "qfq:a7_new_batch_marker:test"  # 前提自检: 旧值确实不同于新值

        rc = mod.main([])
        assert rc == 0

        # 旧连接换名后继续存活: 同一查询不抛、值不变 —— 仍是旧 inode 的快照。
        still_old = reader.execute(f"SELECT DISTINCT batch_id FROM {mod.TARGET}").fetchone()[0]
        assert still_old == old_batch_id
    finally:
        reader.close()

    # 子进程新开只读连接必须看到新值 (证明 live 路径现在真的指向换名后的新文件)。
    code = (
        "import duckdb; "
        f"conn = duckdb.connect({str(live)!r}, read_only=True); "
        f"print(conn.execute('SELECT DISTINCT batch_id FROM {mod.TARGET}').fetchone()[0]); "
        "conn.close()"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "qfq:a7_new_batch_marker:test"


def test_a9_no_create_index_literal_static() -> None:
    """A9 (静态): build_full 与 main 两个函数体内 (docstring 除外, 历史沿革文字会提到
    "删掉了 CREATE INDEX" 这几个字, 不算数) 不再出现 CREATE INDEX 字面量 (2026-09-24
    起索引已删, CTAS 改 ORDER BY code,date 聚簇——spec §3 实测计划器从不选它, 只贡献
    空洞)。build_select_sql 不含 raw_tushare_adj_factor 已由
    test_factor_source_is_self_computed_not_raw_adj_factor 运行时覆盖, 这里补静态扫描。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("build_full", "main"):
            body = list(node.body)
            if ast.get_docstring(node) and body and isinstance(body[0], ast.Expr):
                body = body[1:]  # 跳过函数自己的 docstring, 只扫真代码
            for stmt in body:
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str):
                        assert "create index" not in n.value.lower(), (
                            f"{node.name}():{n.lineno} 发现 CREATE INDEX 字面量: {n.value!r}"
                        )


def test_a8_check_only_allows_concurrent_readonly_connection(tmp_path, monkeypatch) -> None:
    """A8: --check-only 用 read_only=True 打开 live, 允许另一个只读连接并存 (今天
    read_write 会互斥)。"""
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)

    reader = duck_connect(str(live), read_only=True)
    try:
        rc = mod.main(["--check-only"])
        assert rc == 0
    finally:
        reader.close()


def test_check_only_does_not_take_writer_lock(tmp_path, monkeypatch) -> None:
    """M2: --check-only 只读, 不取写锁 —— 即便另一个 owner 正持有写锁, --check-only 仍能跑。"""
    from services.writer_lock import WRITER_LOCK_PATH_ENV
    from services.writer_lock import writer_lock as real_writer_lock

    mod, _raw_db, _live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "checkonly_writer.lock"))

    with real_writer_lock("other-owner-holding-the-window"):
        rc = mod.main(["--check-only"])
    assert rc == 0


def test_w5_writer_lock_busy_blocks_build_and_leaves_production_untouched(tmp_path, monkeypatch) -> None:
    """W5: 另一个 owner 持有项目写锁时, main() 的建库路径必须让路 —— rc=4, live 不变,
    build 不存在。变异 (去掉 `with writer_lock` 包裹) 应转红 (见收尾变异记录)。"""
    from services.writer_lock import WRITER_LOCK_PATH_ENV
    from services.writer_lock import writer_lock as real_writer_lock

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    ino_before = live.stat().st_ino
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "w5_writer.lock"))

    with real_writer_lock("other-owner-holding-the-window"):
        rc = mod.main([])
    assert rc == 4

    assert live.stat().st_ino == ino_before
    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()


def test_w5b_writer_lock_busy_does_not_delete_other_writers_residual_build(
    tmp_path, monkeypatch
) -> None:
    """回归: 残留 build 清理必须发生在拿到写锁之后, 不是之前 —— 否则排不到队的进程
    会删掉正持锁写者尚未关闭连接的在建 build 文件 (两个进程同时跑本脚本时的竞态)。

    用一个预先放好的 `build` 文件模拟"另一个持锁写者正在建库、build 文件已存在但
    连接尚未关闭"; 本进程锁忙 (rc=4) 时若在拿锁前就跑清理, 这个文件会被误删。
    变异: 把残留清理挪回 `with writer_lock` 之外 (锁获取之前) -> 本用例转红。
    """
    from services.writer_lock import WRITER_LOCK_PATH_ENV
    from services.writer_lock import writer_lock as real_writer_lock

    mod, _raw_db, live = _build_live_with_secondary_tables(tmp_path, monkeypatch, STOCK_A + STOCK_B)
    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "w5b_writer.lock"))

    build = live.with_name(live.stem + "_build.duckdb")
    build.write_bytes(b"another-writers-in-progress-build-file")

    with real_writer_lock("other-owner-holding-the-window"):
        rc = mod.main([])
    assert rc == 4

    assert build.exists(), "锁忙时不该走到清理步骤, 别人的在建 build 文件不该被删"
    assert build.read_bytes() == b"another-writers-in-progress-build-file"


# --------------------------------------------------------------------------- T1-T3:
# 返修 cut_qfq_fresh_file_swap 第三轮 (sandbox/churn_fix_20260919/fix_qfq_r3.md) —
# 主循环实测三个变异当前存活的隔离用例, 全部自带 tmp fixture, 写锁走 autouse fixture。


def test_t1_free_blocks_nonzero_blocks_swap_even_when_cross_check_passes(
    tmp_path, monkeypatch
) -> None:
    """T1: free_blocks 判据必须独立于 cross_check —— 即便集合/锚点/因子/日期/重复行
    五条全过, build 文件里只要还有非零 free_blocks (=CHECKPOINT 后仍有空洞), 就必须
    SKIP 换名 (rc=2)、live 一字不动、build/.wal 均不留痕。

    取舍 (实测过再选的, 不是猜的): 先试过"真建一张大表再 DROP"这条路径 —— 单独验证
    发现 DuckDB 对一个全新文件、单连接内 DROP TABLE 紧跟 CHECKPOINT 会把释放的块整体
    回收 (free_blocks 量到 0, 文件本身也缩小), 生产上观察到的空洞需要跨多次 checkpoint
    的碎片化历史 (spec §1.1/E1 的场景), 在单元测试里为了复现这个历史而反复 CHECKPOINT
    换取真空洞不现实也脆。于是改用 spec 明确给出的另一条路: 直接给 `DuckConn.execute`
    打类级猴子补丁, 只拦截 main() 里那一条形状唯一的 SQL
    (`SELECT free_blocks FROM pragma_database_size() WHERE database_name = ?`),
    伪造返回非零值, 其余所有 execute 调用原样透传给真实实现 (含 build_full/cross_check/
    CHECKPOINT 本身), 不影响它们的真实结果。
    """
    from services.duck_adapter import DuckConn

    mod, _raw_db, live = _build_live_with_secondary_tables(
        tmp_path, monkeypatch, STOCK_A + STOCK_B, name="t1"
    )
    ino_before = live.stat().st_ino

    real_execute = DuckConn.execute

    def _lying_execute(self, sql, params=None):
        if "free_blocks" in sql and "pragma_database_size" in sql:

            class _FakeFreeBlocksRow:
                def fetchone(self_inner):
                    return (1,)

            return _FakeFreeBlocksRow()
        return real_execute(self, sql, params)

    monkeypatch.setattr(DuckConn, "execute", _lying_execute)

    rc = mod.main([])
    assert rc == 2

    assert live.stat().st_ino == ino_before
    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()
    assert not build.with_name(build.name + ".wal").exists()


def test_t2_live_fingerprint_recorded_before_build_not_at_swap_time(
    tmp_path, monkeypatch, capsys
) -> None:
    """T2: `expected` 指纹必须在开始建 build **之前**记下——建库进行中若 live 被另一个
    连接写过并干净关闭 (指纹因而变化), 必须 REFUSE (rc=3), live 上那次并发写入的行原样
    还在 (证明 main() 没有覆盖/丢失它), build 文件已被清理。

    2026-09-25 附注: `SwapRefused` 的这条拒绝消息实际经 `file=sys.stderr` 打印 (与
    db_compact.py 的 FAIL/LOCK_BUSY 同一风格), 不是 spec 字面写的 stdout —— 判为
    spec 措辞的小误差 (不是功能性误报), 断言改成对 stdout+stderr 合并文本找
    `live_changed_during_build`, 不因流的选择而对错误地判红/判绿。
    """
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(
        tmp_path, monkeypatch, STOCK_A + STOCK_B, name="t2"
    )
    real_build_full = mod.build_full

    def _build_full_then_mutate_live(conn, **kwargs):
        detail = real_build_full(conn, **kwargs)
        # 建库进行中, 另一个连接往 live 写一行并干净关闭 (含 CHECKPOINT) —— 让 live
        # 指纹在 main() 记下 expected 之后、真正换名之前发生变化。
        other = duck_connect(str(live), read_only=False)
        try:
            other.execute(
                "INSERT INTO mart_data_deletion_record VALUES "
                "('t2_row','t2_run','t1','scope1',NULL,NULL,0,0,0,"
                "'concurrent-write-during-build',NULL,'2024-06-01')"
            )
            other.execute("CHECKPOINT")
        finally:
            other.close()
        return detail

    monkeypatch.setattr(mod, "build_full", _build_full_then_mutate_live)

    rc = mod.main([])
    assert rc == 3

    captured = capsys.readouterr()
    assert "live_changed_during_build" in (captured.out + captured.err)

    after = duck_connect(str(live), read_only=True)
    try:
        row = after.execute(
            "SELECT record_id FROM mart_data_deletion_record WHERE record_id = 't2_row'"
        ).fetchone()
        assert row is not None, "建库期间并发写入 live 的那一行必须还在"
    finally:
        after.close()

    build = live.with_name(live.stem + "_build.duckdb")
    assert not build.exists()
    assert not build.with_name(build.name + ".wal").exists()


def test_t3_non_target_table_index_carried_over_target_stays_index_free(
    tmp_path, monkeypatch
) -> None:
    """T3: `_copy_secondary_tables` 把非目标表 (mart_data_deletion_record) 上的普通索引
    原样迁移 (index_name+sql 逐字相同); 目标表 price_kline_qfq_tushare 索引数仍为 0
    (A5 已从"物理聚簇"角度覆盖, 这里从"非目标表索引不丢"角度补, 证明两者互不冲突)。
    """
    from services.duck_adapter import connect as duck_connect

    mod, _raw_db, live = _build_live_with_secondary_tables(
        tmp_path, monkeypatch, STOCK_A + STOCK_B, name="t3"
    )

    before = duck_connect(str(live), read_only=True)
    try:
        idx_before = {
            r[0]: r[1]
            for r in before.execute(
                "SELECT index_name, sql FROM duckdb_indexes() "
                "WHERE table_name = 'mart_data_deletion_record'"
            ).fetchall()
        }
    finally:
        before.close()
    assert idx_before, "前提自检: fixture 必须先建好非目标表索引, 否则测不出迁移这件事"

    rc = mod.main([])
    assert rc == 0

    after = duck_connect(str(live), read_only=True)
    try:
        idx_after = {
            r[0]: r[1]
            for r in after.execute(
                "SELECT index_name, sql FROM duckdb_indexes() "
                "WHERE table_name = 'mart_data_deletion_record'"
            ).fetchall()
        }
        target_idx_n = after.execute(
            f"SELECT count(*) FROM duckdb_indexes() WHERE table_name = '{mod.TARGET}'"
        ).fetchone()[0]
    finally:
        after.close()

    assert idx_after == idx_before
    assert target_idx_n == 0
