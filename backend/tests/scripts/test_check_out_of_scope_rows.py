"""check_out_of_scope_rows 单测 (S3, 2026-09-12)。

结构照 test_check_db_invariants.py: importlib 装载脚本, 用真实 services.duck_adapter
连接 (内存库 / tmp 文件库) 注入, 不 mock 被测的枚举/扫描逻辑本身。全部 fixture 自带
(CLAUDE.md 反馈 feedback-test-must-carry-its-own-fixture): 本文件不打开任何
data/*.duckdb ——`run_scan`/`main` 的库连接一律经 `conn_for` 注入或 `--db-override`
指向 tmp_path 下现造的库, 一个生产库都不碰。

真实 backend/config/out_of_scope_scan.yaml 只在"真文件能加载 + 正则命中已知向量"
这条测试里读一次 (纯文本 YAML 解析, 不连接任何 .duckdb); `main()` 端到端测试用
--db-override 把 database_manifest.yaml 里**每一个**真实别名都指向 tmp_path 下新建的
空库, 从不解析出任何真实物理路径。

importlib 装载后必须把模块注册进 sys.modules 再 exec——脚本用了 @dataclass(frozen=True),
Python 3.13 的 dataclass 处理会反查 sys.modules[cls.__module__], 不注册会在 import 时
直接 AttributeError (与被测逻辑无关的环境噪音, 已实测确认)。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect as duck_connect  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_out_of_scope_rows", REPO / "backend" / "scripts" / "check_out_of_scope_rows.py"
)
cosr = importlib.util.module_from_spec(_spec)
sys.modules["check_out_of_scope_rows"] = cosr  # dataclass 反查 sys.modules 需要, 见头注
_spec.loader.exec_module(cosr)


_VALID_MIN: dict = {
    "version": 1,
    "security_code_columns": ["ts_code", "con_code"],
    "classes": {
        "b_share": {
            "ruling": "test ruling",
            "code_patterns": [
                {"prefix": "900", "suffix": "SH"},
                {"prefix": "200", "suffix": "SZ"},
            ],
        }
    },
    "databases_without_security_code_columns": [],
}


def _write_yaml(tmp_path: Path, obj: dict, name: str = "scan.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(obj, allow_unicode=True), encoding="utf-8")
    return p


def _min_config():
    """真正解析出的 ScanConfig, 不依赖磁盘临时文件 (供不测 loader 本身的用例用)。"""
    return cosr.ScanConfig(
        security_code_columns=("ts_code", "con_code", "code"),
        classes=(
            cosr.OutOfScopeClass(
                name="b_share",
                ruling="test",
                code_patterns=(
                    cosr.CodePattern(prefix="900", suffix="SH"),
                    cosr.CodePattern(prefix="200", suffix="SZ"),
                ),
                regex=r"^(900\d{3}(\.SH)?|200\d{3}(\.SZ)?)$",
            ),
        ),
        declared_no_code_columns=frozenset(),
    )


# ── 0. 真文件: 仓内 out_of_scope_scan.yaml 能被 loader 加载 + 正则命中已知向量 ─────

def test_production_yaml_loads_and_regex_matches_known_vectors():
    cfg = cosr.load_scan_config()
    assert {c.name for c in cfg.classes} == {"b_share"}
    assert set(cfg.security_code_columns) >= {"ts_code", "stock_code", "con_code", "code"}
    b_share = next(c for c in cfg.classes if c.name == "b_share")

    con = duckdb.connect(":memory:")
    positives = ["900001.SH", "200011.SZ", "900001", "200011"]
    negatives = ["600000.SH", "002280.SZ", "9000011", "200123.SH"]
    for code in positives:
        assert con.execute("SELECT regexp_matches(?, ?)", [code, b_share.regex]).fetchone()[0], code
    for code in negatives:
        assert not con.execute("SELECT regexp_matches(?, ?)", [code, b_share.regex]).fetchone()[0], code
    con.close()


# ── 1. loader fail-closed: 每个门控条件一个隔离用例 ──────────────────────────────

def test_load_fails_closed_on_missing_file(tmp_path):
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="missing"):
        cosr.load_scan_config(tmp_path / "nope.yaml")


def test_load_fails_closed_on_unreadable_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("classes: [this is not\n  valid: yaml: at all", encoding="utf-8")
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="unreadable"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_non_mapping_root(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="root must be a mapping"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_extra_top_level_key(tmp_path):
    bad = {**_VALID_MIN, "extra_key": 1}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="顶层键"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_missing_top_level_key(tmp_path):
    bad = dict(_VALID_MIN)
    del bad["databases_without_security_code_columns"]
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="顶层键"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_wrong_version(tmp_path):
    bad = {**_VALID_MIN, "version": 2}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="version must be 1"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_empty_security_code_columns(tmp_path):
    bad = {**_VALID_MIN, "security_code_columns": []}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="security_code_columns"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_bad_column_name_pattern(tmp_path):
    bad = {**_VALID_MIN, "security_code_columns": ["Ts_Code"]}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match=r"\^\[a-z\]"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_duplicate_column_name(tmp_path):
    bad = {**_VALID_MIN, "security_code_columns": ["ts_code", "ts_code"]}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="duplicate entry"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_empty_classes(tmp_path):
    """classes 为空 = §4.4 死亡条件 (范围外类别名单整体消失), 不是"今天没有范围外行"。"""
    bad = {**_VALID_MIN, "classes": {}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="classes must be a non-empty mapping"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_bad_class_name(tmp_path):
    bad = {**_VALID_MIN, "classes": {"B-Share": _VALID_MIN["classes"]["b_share"]}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="classes 键必须匹配"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_class_wrong_key_set(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": [{"prefix": "900", "suffix": "SH"}], "extra": 1}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="键集合必须恰为"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_empty_ruling(tmp_path):
    bad_class = {"ruling": "   ", "code_patterns": [{"prefix": "900", "suffix": "SH"}]}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="ruling must be a non-empty string"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_empty_code_patterns(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": []}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="code_patterns must be a non-empty list"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_pattern_wrong_key_set(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": [{"prefix": "900", "suffix": "SH", "extra": 1}]}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="code_patterns\\[0\\] 键集合"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_prefix_too_long(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": [{"prefix": "9000", "suffix": "SH"}]}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="prefix must match"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_prefix_too_short(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": [{"prefix": "9", "suffix": "SH"}]}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="prefix must match"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_invalid_suffix(tmp_path):
    bad_class = {"ruling": "x", "code_patterns": [{"prefix": "900", "suffix": "SS"}]}
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="suffix must be one of"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_duplicate_pattern(tmp_path):
    bad_class = {
        "ruling": "x",
        "code_patterns": [{"prefix": "900", "suffix": "SH"}, {"prefix": "900", "suffix": "SH"}],
    }
    bad = {**_VALID_MIN, "classes": {"b_share": bad_class}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="duplicate \\(prefix, suffix\\)"):
        cosr.load_scan_config(p)


# ── S2-A4 (spec_bshare_b2.md §5.2, 2026-09-19): databases_without_security_code_columns
# loader —— 键集不对 / db 不在 manifest / why 空 / 重复 db / 顶层多出旧 landing_exemptions
# 键, 各自 OutOfScopeScanConfigError; 每条隔离 (其它全满足只违反它一条)。────────────


def test_load_fails_closed_on_declared_no_code_columns_not_list(tmp_path):
    bad = {**_VALID_MIN, "databases_without_security_code_columns": {}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(
        cosr.OutOfScopeScanConfigError, match="databases_without_security_code_columns must be a list"
    ):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_declared_wrong_key_set(tmp_path):
    bad = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [{"db": "experiment_store", "table": "t"}],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="键集合必须恰为"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_declared_extra_key(tmp_path):
    """S2-A4 isolation: {db, why} both present (correctly formed) but with a
    stray extra key alongside them must still be rejected — a subset check
    (require {db, why} present, permit more) would wrongly accept this,
    unlike ``test_load_fails_closed_on_declared_wrong_key_set`` above whose
    fixture is missing ``why`` entirely and so is caught either way.
    Mirrors ``test_l9_extra_key_rejected`` in test_vendor_scope.py."""
    bad = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [
            {"db": "experiment_store", "why": "test declaration", "extra": "z"}
        ],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="键集合必须恰为"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_declared_unknown_db_alias(tmp_path):
    bad = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [
            {"db": "no_such_alias_xyz", "why": "x"}
        ],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="unknown database alias"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_declared_empty_why(tmp_path):
    bad = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [
            {"db": "experiment_store", "why": "  "}
        ],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="why must be a non-empty string"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_duplicate_declared_db(tmp_path):
    entry = {"db": "experiment_store", "why": "x"}
    bad = {**_VALID_MIN, "databases_without_security_code_columns": [dict(entry), dict(entry)]}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="duplicate db"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_stale_landing_exemptions_top_level_key(tmp_path):
    """S2-A4 control: the retired ``landing_exemptions`` key must now be
    rejected as an unknown top-level key, not silently ignored."""
    bad = {**_VALID_MIN, "landing_exemptions": []}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="顶层键"):
        cosr.load_scan_config(p)


def test_load_accepts_valid_declared_no_code_columns_against_real_manifest(tmp_path):
    ok = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [
            {"db": "experiment_store", "why": "test declaration"}
        ],
    }
    p = _write_yaml(tmp_path, ok)
    cfg = cosr.load_scan_config(p)
    assert cfg.declared_no_code_columns == frozenset({"experiment_store"})


def test_main_returns_2_when_declared_no_code_columns_malformed(tmp_path, monkeypatch):
    """S2-A4: main() surfaces the loader's fail-closed behavior as exit 2,
    same as the pre-existing classes-empty/missing-file death conditions."""
    bad = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [{"db": "no_such_alias_xyz", "why": "x"}],
    }
    p = _write_yaml(tmp_path, bad)
    monkeypatch.setattr(cosr, "CONFIG_PATH", p)
    assert cosr.main(["--json"]) == 2


# ── R1. 发现式枚举: 3 张表各 1 个代码列 → 枚举恰 3 项 (集合相等, 不是 >=) ──────────

def test_discovery_enumeration_exact_set_not_at_least():
    """返修 (blocking finding, cut_lineage_drift 收尾, 2026-09-19): `_` 前缀曾经被整表
    跳过发现式枚举 (与 services/lineage/builder.py::_live_tables_by_db 同一豁免), 但
    `_lock_probe`/`_ep_*` 早已没有 creator, 前缀已从"瞬态锁探针的巧合命名"退化成
    "谁都能借来永久隐身"的洞——本刀已在 builder.py 与 data_layer_audit.py 关闭它,
    这里补齐同一道 out_of_scope_rows 门。`_lock_probe` 现在必须和其它带代码列的表
    一样出现在发现集合里; VIEW 与不相关列的表仍然不在 (集合相等, 不是 >=)。"""
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE a (ts_code VARCHAR, name VARCHAR)")
        c.execute("CREATE TABLE b (con_code VARCHAR)")
        c.execute("CREATE TABLE c (code VARCHAR)")
        c.execute("CREATE TABLE d (irrelevant VARCHAR)")
        c.execute("CREATE VIEW v AS SELECT ts_code FROM a")
        c.execute("CREATE TABLE _lock_probe (ts_code VARCHAR)")
        discovered = cosr._discover_columns(c, ("ts_code", "con_code", "code"))
        assert discovered == {
            "a": ["ts_code"], "b": ["con_code"], "c": ["code"], "_lock_probe": ["ts_code"],
        }
    finally:
        c.close()


def test_underscore_prefixed_table_no_longer_exempt_from_scan():
    """隔离用例 (其它全满足, 只违反"表名以 `_` 开头"这一个条件): 库可达、列名匹配、
    命中真实范围外码——唯一特殊之处是表名带 `_` 前缀。返修前这类表会被
    `_count_base_tables` 与 `_discover_columns` 双双过滤掉, 范围外行永久不会被扫到;
    返修后必须和普通表一样正常 FAIL。变异 (把两处过滤加回去) 会让本用例先在
    `rows[0]` 处 IndexError (discovered 变空, run_scan 不再产出任何行), 或者
    (若只加回其中一处) 断言值不等——两种红都证明前缀豁免已关闭。"""
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE _scratch (ts_code VARCHAR)")
        c.execute("INSERT INTO _scratch VALUES ('900901.SH')")
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["d"])
        assert len(rows) == 1
        assert rows[0]["table"] == "_scratch"
        assert rows[0]["status"] == "FAIL" and rows[0]["value"] == 1
        assert cosr.overall_status(rows) == "FAIL"
    finally:
        pass


def test_count_base_tables_counts_underscore_prefixed_tables():
    """`_count_base_tables` 单测隔离: 只有一张 `_` 前缀表时, 计数必须是 1 不是 0——
    否则它会跟 `_discover_columns` 的过滤"互相配合"制造假象 (两处都少算, 永远一致,
    `scan_database` 的 `total_tables > 0 and not discovered` 死机制就测不出坏列名
    这一真正想守的情况)。变异: 把 `_` 前缀过滤加回 `_count_base_tables` → 断言红
    (计数变回 0)。"""
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE _scratch (irrelevant VARCHAR)")
        assert cosr._count_base_tables(c) == 1
    finally:
        c.close()


def test_run_scan_one_row_per_discovered_column_per_class():
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE a (ts_code VARCHAR)")
        c.execute("CREATE TABLE b (con_code VARCHAR)")
        c.execute("CREATE TABLE c (code VARCHAR)")
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["fixturedb"])
        assert {(r["table"], r["column"]) for r in rows} == {
            ("a", "ts_code"), ("b", "con_code"), ("c", "code"),
        }
        assert all(r["status"] == "PASS" for r in rows)
    finally:
        pass  # run_scan already closed the connection


# ── R2. 库被真实写锁占用 → UNVERIFIED, 从不 exit 0 ────────────────────────────────

@pytest.fixture
def _rw_lock_holder(tmp_path):
    """在独立子进程里对给定 duckdb 文件开 read_write 连接并一直持有 (哨兵文件同步,
    不用 sleep(N) 掐时间, 规避子进程调度抖动) —— 与
    test_check_lineage_catalog_drift.py 的同名 fixture 同一手法, 证明真实文件锁语义,
    不是 mock RuntimeError。"""
    held: dict[str, subprocess.Popen] = {}

    def _hold(db_path: Path) -> None:
        held_flag = tmp_path / "lock_held.flag"
        release_flag = tmp_path / "lock_release.flag"
        held_flag.unlink(missing_ok=True)
        release_flag.unlink(missing_ok=True)
        script = (
            "import duckdb, time, os\n"
            f"conn = duckdb.connect(r'{db_path}', read_only=False)\n"
            "conn.execute('CREATE TABLE t (x INTEGER)')\n"
            f"open(r'{held_flag}', 'w').close()\n"
            f"while not os.path.exists(r'{release_flag}'):\n"
            "    time.sleep(0.05)\n"
            "conn.close()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        held["proc"] = proc
        held["release_flag"] = release_flag
        deadline = time.time() + 10
        while not held_flag.exists():
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError("lock holder subprocess never signalled in time")
            if proc.poll() is not None:
                raise RuntimeError(f"lock holder subprocess died early: {proc.stdout.read()}")
            time.sleep(0.05)

    yield _hold

    proc = held.get("proc")
    if proc is not None and proc.poll() is None:
        held["release_flag"].write_text("release", encoding="utf-8")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_locked_database_is_unverified_and_never_exit_zero(tmp_path, _rw_lock_holder, monkeypatch):
    db_path = tmp_path / "locked.duckdb"
    _rw_lock_holder(db_path)
    monkeypatch.setenv("CHUNKYMONKEY_AUDIT_LOCK_TIMEOUT", "1")

    from services.duck_adapter import audit_connect

    def conn_for(alias):
        return audit_connect(str(db_path))

    rows = cosr.run_scan(_min_config(), conn_for, ["lockeddb"])
    assert len(rows) == 1
    assert rows[0]["status"] == "UNVERIFIED"
    assert rows[0]["db"] == "lockeddb"
    overall = cosr.overall_status(rows)
    assert overall == "UNVERIFIED"
    assert cosr.exit_code_for(overall) == 3
    assert cosr.exit_code_for(overall) != 0


# ── R3. 命中判定: 带后缀 / 裸码 都 FAIL; "包含 900" 子串不误判 ───────────────────

def test_scan_fails_on_suffixed_code():
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE t (ts_code VARCHAR)")
        c.execute("INSERT INTO t VALUES ('900901.SH')")
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["d"])
        assert len(rows) == 1
        assert rows[0]["status"] == "FAIL" and rows[0]["checked"] == 1 and rows[0]["value"] == 1
    finally:
        pass


def test_scan_fails_on_bare_code_without_suffix():
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE t (ts_code VARCHAR)")
        c.execute("INSERT INTO t VALUES ('200001')")
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["d"])
        assert rows[0]["status"] == "FAIL" and rows[0]["value"] == 1
    finally:
        pass


def test_scan_passes_on_lookalike_substring_not_false_positive():
    """600900.SH 含子串 "900" 但前缀不是 900 —— 不能被误判命中。"""
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE t (ts_code VARCHAR)")
        c.execute("INSERT INTO t VALUES ('600900.SH')")
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["d"])
        assert rows[0]["status"] == "PASS" and rows[0]["value"] == 0 and rows[0]["checked"] == 1
    finally:
        pass


# ── S2-A1/A2/A3 (spec_bshare_b2.md §5.2, 2026-09-19): databases_without_security_code_columns
# 三分支 —— 取代旧 R4 (landing_exemptions/observed, 已删)。每条隔离用例只违反一个门控
# 条件, 其它分支条件全部满足。──────────────────────────────────────────────────────


def _config_with_declared(db_alias: str):
    base = _min_config()
    return cosr.ScanConfig(
        security_code_columns=base.security_code_columns,
        classes=base.classes,
        declared_no_code_columns=frozenset({db_alias}),
    )


def test_declared_db_with_no_code_columns_scan_passes():
    """S2-A1: 声明库 (4 张表均无代码列) → 恰一行 PASS,
    reason=declared_no_security_code_columns, checked==4, overall PASS, exit 0。"""
    config = _config_with_declared("d")
    c = duck_connect(":memory:")
    try:
        for i in range(4):
            c.execute(f"CREATE TABLE t{i} (run_id VARCHAR)")
        rows = cosr.run_scan(config, lambda alias: c, ["d"])
        assert rows == [
            {
                "db": "d", "table": None, "column": None, "class": None,
                "checked": 4, "value": 0, "status": "PASS",
                "reason": "declared_no_security_code_columns",
            }
        ]
        overall = cosr.overall_status(rows)
        assert overall == "PASS"
        assert cosr.exit_code_for(overall) == 0
    finally:
        pass


def test_undeclared_db_with_no_code_columns_stays_unverified():
    """S2-A2: 同样"4 张表均无代码列"的夹具, 但该库**未在**
    databases_without_security_code_columns 里声明 → 必须仍是 UNVERIFIED
    enumeration_empty (现有行为不变) —— 隔离出"声明"这个门控条件本身。"""
    config = _min_config()  # declared_no_code_columns == frozenset() — 未声明任何库
    c = duck_connect(":memory:")
    try:
        for i in range(4):
            c.execute(f"CREATE TABLE t{i} (run_id VARCHAR)")
        rows = cosr.run_scan(config, lambda alias: c, ["d"])
        assert len(rows) == 1
        assert rows[0]["status"] == "UNVERIFIED" and rows[0]["reason"] == "enumeration_empty"
        assert cosr.overall_status(rows) == "UNVERIFIED"
    finally:
        pass


def test_declared_db_with_stale_declaration_still_scans_and_fails():
    """S2-A3: 声明库里多建一张带 ts_code 的表 (声明已过期——库其实长出了代码列) →
    正常逐列扫的行 (含命中/不命中) + 一行 FAIL reason 以 stale_declaration 开头,
    overall FAIL, exit 1。声明命中不能跳过枚举 (mutation target)。"""
    config = _config_with_declared("d")
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE t0 (run_id VARCHAR)")  # 无代码列, 声明原本描述的对象
        c.execute("CREATE TABLE t1 (ts_code VARCHAR)")  # 声明过期的证据: 长出了代码列
        c.execute("INSERT INTO t1 VALUES ('600000.SH')")  # 不命中 B 股, 但列存在即过期
        rows = cosr.run_scan(config, lambda alias: c, ["d"])
        scan_rows = [r for r in rows if r["table"] == "t1"]
        assert len(scan_rows) == 1 and scan_rows[0]["status"] == "PASS"
        stale_rows = [r for r in rows if r["table"] is None]
        assert len(stale_rows) == 1
        assert stale_rows[0]["status"] == "FAIL"
        assert stale_rows[0]["reason"].startswith("stale_declaration")
        assert stale_rows[0]["value"] == 1  # 1 个代码列被发现 (t1.ts_code)
        assert cosr.overall_status(rows) == "FAIL"
        assert cosr.exit_code_for(cosr.overall_status(rows)) == 1
    finally:
        pass


# ── R5. 列名集合坏了 (枚举归零) → UNVERIFIED, 不许悄悄 PASS ───────────────────────

def test_broken_column_name_set_is_unverified_not_pass():
    broken = cosr.ScanConfig(
        security_code_columns=("nonexistent_col_xyz",),
        classes=_min_config().classes,
        declared_no_code_columns=frozenset(),
    )
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE a (ts_code VARCHAR)")
        rows = cosr.run_scan(broken, lambda alias: c, ["d"])
        assert len(rows) == 1
        assert rows[0]["status"] == "UNVERIFIED" and rows[0]["reason"] == "enumeration_empty"
        assert cosr.overall_status(rows) == "UNVERIFIED"
    finally:
        pass


def test_empty_database_zero_tables_is_not_unverified():
    """与上一条对照: 库里本来就一张表都没有时, 枚举为空是正常状态, 不是"列名集合坏了"。"""
    c = duck_connect(":memory:")
    try:
        rows = cosr.run_scan(_min_config(), lambda alias: c, ["d"])
        assert rows == []
        assert cosr.overall_status(rows) == "PASS"
    finally:
        pass


# ── R6. classes 清空 → main() 退出 2 (配置错, 死亡条件) ──────────────────────────

def test_main_returns_2_when_classes_empty(tmp_path, monkeypatch):
    bad = {**_VALID_MIN, "classes": {}}
    p = _write_yaml(tmp_path, bad)
    monkeypatch.setattr(cosr, "CONFIG_PATH", p)
    assert cosr.main(["--json"]) == 2


def test_main_returns_2_when_registry_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cosr, "CONFIG_PATH", tmp_path / "does_not_exist.yaml")
    assert cosr.main([]) == 2


# ── R7. governance_gates.yaml 接线 + degraded_msg 分类 ───────────────────────────

def test_registered_as_system_health_runtime_check_with_null_from_gate():
    from services import governance_gates as gg

    reg = gg.load_registry()
    spec = next((c for c in reg.runtime_checks if c.id == "out_of_scope_rows"), None)
    assert spec is not None, "out_of_scope_rows 没有接进 governance_gates.yaml runtime_checks"
    assert spec.from_gate is None
    assert spec.script == "backend/scripts/check_out_of_scope_rows.py"
    assert isinstance(spec.skip_when_dry, bool)


def test_degraded_msg_classifies_as_integrity_observe():
    from services import governance_gates as gg
    from services.pipeline.run_outcome import classify_msg

    reg = gg.load_registry()
    spec = next(c for c in reg.runtime_checks if c.id == "out_of_scope_rows")
    msg = spec.rendered_degraded_msg(date="20260912")
    assert "{date}" not in msg
    assert classify_msg(msg) == "integrity"


# ── overall_status / exit_code_for 优先级 ────────────────────────────────────────

def test_overall_status_fail_beats_unverified():
    rows = [
        {"status": "FAIL"},
        {"status": "UNVERIFIED"},
        {"status": "PASS"},
    ]
    assert cosr.overall_status(rows) == "FAIL"
    assert cosr.exit_code_for(cosr.overall_status(rows)) == 1


def test_overall_status_all_pass_is_pass():
    """2026-09-19 刀 B2: 旧 observed 三态已删, 只剩 PASS/FAIL/UNVERIFIED (§2.4)。"""
    rows = [{"status": "PASS"}, {"status": "PASS"}]
    assert cosr.overall_status(rows) == "PASS"
    assert cosr.exit_code_for(cosr.overall_status(rows)) == 0


def test_overall_status_unverified_only():
    rows = [{"status": "PASS"}, {"status": "UNVERIFIED"}]
    assert cosr.overall_status(rows) == "UNVERIFIED"
    assert cosr.exit_code_for(cosr.overall_status(rows)) == 3


# ── write_alert_flag 自愈 ─────────────────────────────────────────────────────────

def test_alert_flag_written_on_fail_and_healed_on_pass(tmp_path):
    flag = tmp_path / "alert.flag"
    fail_rows = [{"status": "FAIL", "db": "d", "table": "t", "column": "ts_code",
                  "class": "b_share", "checked": 1, "value": 1}]
    cosr.write_alert_flag(flag, "FAIL", fail_rows)
    assert flag.exists()
    assert "FAIL" in flag.read_text()

    cosr.write_alert_flag(flag, "PASS", [])
    assert not flag.exists()


# ── main() 端到端: --db-override 覆盖真实 manifest 的每一个别名, 从不解析真实路径 ──

def test_main_end_to_end_all_overridden_empty_dbs_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cosr, "CONFIG_PATH", _write_yaml(tmp_path, _VALID_MIN, "scan.yaml"))

    from services.database_manifest import get_database_manifest

    manifest = get_database_manifest()
    override_args: list[str] = []
    for alias in manifest.databases:
        p = tmp_path / f"{alias}.duckdb"
        duckdb.connect(str(p)).close()
        override_args += ["--db-override", f"{alias}={p}"]

    json_out = tmp_path / "out.json"
    flag = tmp_path / "alert.flag"
    rc = cosr.main(["--json-out", str(json_out), "--alert-flag", str(flag), *override_args])
    assert rc == 0, json_out.read_text() if json_out.exists() else "no json-out"
    assert not flag.exists()
    payload = json.loads(json_out.read_text())
    assert payload["overall"] == "PASS"
    # 每个别名开的都是全新空库 (0 张表), 不应贡献任何行
    assert payload["rows"] == []


def test_main_end_to_end_detects_injected_out_of_scope_row(tmp_path, monkeypatch):
    """全部别名仍 override 到 tmp 空库, 只在其中一个里建一张带命中行的表, 验证
    main() 的完整 CLI 路径 (--json / --json-out / --alert-flag / 退出码) 都传导正确,
    且从未解析出任何真实 database_manifest 路径。"""
    monkeypatch.setattr(cosr, "CONFIG_PATH", _write_yaml(tmp_path, _VALID_MIN, "scan.yaml"))

    from services.database_manifest import get_database_manifest

    manifest = get_database_manifest()
    override_args: list[str] = []
    target_alias = sorted(manifest.databases)[0]
    for alias in manifest.databases:
        p = tmp_path / f"{alias}.duckdb"
        con = duckdb.connect(str(p))
        if alias == target_alias:
            con.execute("CREATE TABLE raw_hit (ts_code VARCHAR)")
            con.execute("INSERT INTO raw_hit VALUES ('900901.SH')")
        con.close()
        override_args += ["--db-override", f"{alias}={p}"]

    json_out = tmp_path / "out.json"
    flag = tmp_path / "alert.flag"
    rc = cosr.main(["--json-out", str(json_out), "--alert-flag", str(flag), *override_args])
    assert rc == 1
    assert flag.exists()
    payload = json.loads(json_out.read_text())
    assert payload["overall"] == "FAIL"
    hit = next(r for r in payload["rows"] if r["db"] == target_alias)
    assert hit["status"] == "FAIL" and hit["value"] == 1 and hit["table"] == "raw_hit"


def test_main_end_to_end_declared_db_with_no_code_columns_mixed_with_empty_dbs(tmp_path, monkeypatch):
    """S2-A6: 真实 manifest 全部 7 个别名 --db-override; 只有声明库(有表, 无代码列)可以
    PASS, 其余仍是全新空库 (0 张表); summary 里不再有 observed 键 (mutation target:
    保留 observed 计数 -> 这条键集断言先红); write_alert_flag 在整体 PASS 时删除旗标
    (先写一个旧旗标验证自愈)。"""
    from services.database_manifest import get_database_manifest

    manifest = get_database_manifest()
    target_alias = sorted(manifest.databases)[0]
    cfg = {
        **_VALID_MIN,
        "databases_without_security_code_columns": [
            {"db": target_alias, "why": "test: no code columns by design"}
        ],
    }
    monkeypatch.setattr(cosr, "CONFIG_PATH", _write_yaml(tmp_path, cfg, "scan.yaml"))

    override_args: list[str] = []
    for alias in manifest.databases:
        p = tmp_path / f"{alias}.duckdb"
        con = duckdb.connect(str(p))
        if alias == target_alias:
            con.execute("CREATE TABLE run_id_only (run_id VARCHAR)")
        con.close()
        override_args += ["--db-override", f"{alias}={p}"]

    json_out = tmp_path / "out.json"
    flag = tmp_path / "alert.flag"
    flag.write_text("stale alert from a previous non-PASS run", encoding="utf-8")
    rc = cosr.main(["--json-out", str(json_out), "--alert-flag", str(flag), *override_args])
    assert rc == 0
    assert not flag.exists()
    payload = json.loads(json_out.read_text())
    assert payload["overall"] == "PASS"
    assert set(payload["summary"]) == {"PASS", "FAIL", "UNVERIFIED"}
    declared_rows = [r for r in payload["rows"] if r["db"] == target_alias]
    assert len(declared_rows) == 1
    assert declared_rows[0]["status"] == "PASS"
    assert declared_rows[0]["reason"] == "declared_no_security_code_columns"


# ── 与 vendor_scope (S1) 的一致性: 过渡期允许重复, 不允许分叉 ──────────────────
# out_of_scope_scan.yaml 是 vendor_scope.yaml v2 落地前的过渡文件, B 股代码段在两份文件里
# 各声明了一遍。重复本身可以接受(见 out_of_scope_scan.yaml 头注对"范围外类别 vs 股票池过滤"
# 的隔离论证), **分叉不可以** —— 没有门守着的两份判据必然漂移, 而漂移后果不对称:
# 扫描器少一个代码段 = 有范围外行也报 PASS(静默漏)。
#
# 两个 dataclass 的形状**不同**(实测, 不是推断):
#   S3  ScanConfig.classes             = tuple[OutOfScopeClass, ...], 元素带 .name
#       ScanConfig.security_code_columns = tuple[str, ...]
#   S1  VendorScope.out_of_scope_classes = Mapping[str, OutOfScopeClass] (元素无 .name)
#       VendorScope.security_code_columns = frozenset[str]
# 所以下面按 name 手工对齐, 不假设两边同形。

from services.data_sources.vendor_scope import load_vendor_scope  # noqa: E402


def test_code_patterns_identical_to_vendor_scope():
    """两份配置声明的范围外代码段必须逐字相等 —— 允许重复, 不允许分叉。

    失败时的正确处置是**同时改两边**(或完成合并、删掉过渡文件), 不是放宽这条断言。
    """
    scan = cosr.load_scan_config()          # 读真 out_of_scope_scan.yaml
    scope = load_vendor_scope()             # 读真 vendor_scope.yaml, 不用桩

    mine = {c.name: sorted((p.prefix, p.suffix) for p in c.code_patterns) for c in scan.classes}
    theirs = {
        name: sorted((p.prefix, p.suffix) for p in cls.code_patterns)
        for name, cls in scope.out_of_scope_classes.items()
    }
    assert set(mine) == set(theirs), (
        "两份配置的范围外类别集合不一致: "
        f"out_of_scope_scan={sorted(mine)} vendor_scope={sorted(theirs)}"
    )
    for name in sorted(mine):
        assert mine[name] == theirs[name], (
            f"类别 {name} 的 code_patterns 在两份配置里分叉了: "
            f"out_of_scope_scan={mine[name]} vendor_scope={theirs[name]}"
        )


def test_vendor_scope_code_columns_are_subset_of_scanned_columns():
    """vendor_scope 的 security_code_columns 必须全部落在扫描器认的列名集合里。

    这两个集合**故意不相等**, 方向也不对称, 所以断的是子集不是相等:
      - vendor_scope.security_code_columns 判「哪些 (source, api) 必须登记处置」,
        它面对的是 sync_registry 的 grain / universe_filter_col —— 声明面;
      - out_of_scope_scan.security_code_columns 判「扫库时认哪些列名」,
        它面对的是库里实际长出来的列 —— 天然是超集 (今天多 secucode / symbol)。
    扫描器多认几个列名只是多扫(安全); 反过来, 若 vendor_scope 出现一个扫描器不认的列名,
    就意味着某个登记域的代码列**根本不会被扫到**, 那里有范围外行也报 PASS —— 静默漏。

    这也是为什么合并两份文件时**不能**把 security_code_columns 改读 vendor_scope 那份
    (out_of_scope_scan.yaml 头注已按此更正): 那会让扫描丢掉 secucode / symbol。
    """
    scan = cosr.load_scan_config()
    scope = load_vendor_scope()
    missing = sorted(set(scope.security_code_columns) - set(scan.security_code_columns))
    assert not missing, (
        "vendor_scope 声明了扫描器不认的代码列名, 这些列里的范围外行永远扫不出来: "
        f"{missing}; 把它们加进 out_of_scope_scan.yaml 的 security_code_columns"
    )


# ── S2-A5 (spec_bshare_b2.md §5.2, 2026-09-19): 真文件 ────────────────────────────


def test_real_config_declared_no_code_columns_is_experiment_store():
    cfg = cosr.load_scan_config()
    assert cfg.declared_no_code_columns == frozenset({"experiment_store"})


def test_real_config_regex_byte_identical_to_out_of_scope_codes_class_regex():
    """cfg.classes[0].regex == class_regex(...) 逐字节 —— out_of_scope_codes 是这两个
    formula 抄本(check_out_of_scope_rows 自己的 + vendor_scope.py 的 code_exclude)共同的
    唯一来源 (S1-A1 已经反向验过 vendor_scope 那边, 这里补 check_out_of_scope_rows 这边)。
    """
    from services.data_sources.out_of_scope_codes import class_regex

    cfg = cosr.load_scan_config()
    b_share = next(c for c in cfg.classes if c.name == "b_share")
    assert b_share.regex == class_regex(b_share.code_patterns)


