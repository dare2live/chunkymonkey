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
    "landing_exemptions": [],
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
        landing_exemptions=frozenset(),
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
    del bad["landing_exemptions"]
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


def test_load_fails_closed_on_landing_exemptions_not_list(tmp_path):
    bad = {**_VALID_MIN, "landing_exemptions": {}}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="landing_exemptions must be a list"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_exemption_wrong_key_set(tmp_path):
    bad = {**_VALID_MIN, "landing_exemptions": [{"db": "smartmoney", "table": "t"}]}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="键集合必须恰为"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_exemption_unknown_db_alias(tmp_path):
    bad = {
        **_VALID_MIN,
        "landing_exemptions": [{"db": "no_such_alias_xyz", "table": "t", "why": "x"}],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="unknown database alias"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_exemption_bad_table_name(tmp_path):
    bad = {
        **_VALID_MIN,
        "landing_exemptions": [{"db": "smartmoney", "table": "1bad-name", "why": "x"}],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="table must match"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_exemption_empty_why(tmp_path):
    bad = {
        **_VALID_MIN,
        "landing_exemptions": [{"db": "smartmoney", "table": "raw_x", "why": "  "}],
    }
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="why must be a non-empty string"):
        cosr.load_scan_config(p)


def test_load_fails_closed_on_duplicate_exemption(tmp_path):
    entry = {"db": "smartmoney", "table": "raw_x", "why": "x"}
    bad = {**_VALID_MIN, "landing_exemptions": [dict(entry), dict(entry)]}
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(cosr.OutOfScopeScanConfigError, match="duplicate \\(db, table\\)"):
        cosr.load_scan_config(p)


def test_load_accepts_valid_exemption_against_real_manifest(tmp_path):
    ok = {
        **_VALID_MIN,
        "landing_exemptions": [{"db": "smartmoney", "table": "raw_x", "why": "test exemption"}],
    }
    p = _write_yaml(tmp_path, ok)
    cfg = cosr.load_scan_config(p)
    assert cfg.landing_exemptions == frozenset({("smartmoney", "raw_x")})


# ── R1. 发现式枚举: 3 张表各 1 个代码列 → 枚举恰 3 项 (集合相等, 不是 >=) ──────────

def test_discovery_enumeration_exact_set_not_at_least():
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE a (ts_code VARCHAR, name VARCHAR)")
        c.execute("CREATE TABLE b (con_code VARCHAR)")
        c.execute("CREATE TABLE c (code VARCHAR)")
        c.execute("CREATE TABLE d (irrelevant VARCHAR)")
        c.execute("CREATE VIEW v AS SELECT ts_code FROM a")
        c.execute("CREATE TABLE _lock_probe (ts_code VARCHAR)")
        discovered = cosr._discover_columns(c, ("ts_code", "con_code", "code"))
        assert discovered == {"a": ["ts_code"], "b": ["con_code"], "c": ["code"]}
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


# ── R4. 豁免只免它自己的表, 派生表不豁免 ──────────────────────────────────────────

def _config_with_exemption(db_alias: str, table: str):
    base = _min_config()
    return cosr.ScanConfig(
        security_code_columns=base.security_code_columns,
        classes=base.classes,
        landing_exemptions=frozenset({(db_alias, table)}),
    )


def test_exempted_landing_table_reports_observed_not_fail_with_visible_value():
    config = _config_with_exemption("d", "landing_x")
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE landing_x (ts_code VARCHAR)")
        c.execute("INSERT INTO landing_x VALUES ('900901.SH')")
        rows = cosr.run_scan(config, lambda alias: c, ["d"])
        assert rows[0]["status"] == "observed"
        assert rows[0]["value"] == 1  # 报数, 不是 warn-nothing
        assert cosr.overall_status(rows) == "PASS"  # observed 不算 FAIL
        assert cosr.exit_code_for(cosr.overall_status(rows)) == 0
    finally:
        pass


def test_derived_table_of_exempt_domain_still_fails():
    """豁免只覆盖登记的那张表本身——同域的另一张(派生)表命中同样的行, 必须照判 FAIL。"""
    config = _config_with_exemption("d", "landing_x")  # 豁免 landing_x, 不豁免 derived_x
    c = duck_connect(":memory:")
    try:
        c.execute("CREATE TABLE derived_x (ts_code VARCHAR)")
        c.execute("INSERT INTO derived_x VALUES ('900901.SH')")
        rows = cosr.run_scan(config, lambda alias: c, ["d"])
        assert rows[0]["status"] == "FAIL"
        assert cosr.overall_status(rows) == "FAIL"
    finally:
        pass


# ── R5. 列名集合坏了 (枚举归零) → UNVERIFIED, 不许悄悄 PASS ───────────────────────

def test_broken_column_name_set_is_unverified_not_pass():
    broken = cosr.ScanConfig(
        security_code_columns=("nonexistent_col_xyz",),
        classes=_min_config().classes,
        landing_exemptions=frozenset(),
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
        {"status": "observed"},
    ]
    assert cosr.overall_status(rows) == "FAIL"
    assert cosr.exit_code_for(cosr.overall_status(rows)) == 1


def test_overall_status_all_pass_and_observed_is_pass():
    rows = [{"status": "PASS"}, {"status": "observed"}]
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
