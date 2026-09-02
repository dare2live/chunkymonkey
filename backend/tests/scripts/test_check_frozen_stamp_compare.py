"""check_frozen_stamp_compare 单测 (2026-09-02).

它守的病: ingest_batch.{contract_hash, config_hash, source_name} 是落地那一刻的证据
封印 (算法派生指纹 + 传输轴标签), 契约指纹算法一变或换源就必然与"现在"的契约/指针值
不再相等; 但代码里散落着把这条必然为假的等式当不变量断言的写法。本门是静态 AST lint,
抓三种写法形状 (S1 direct / S2 tuple / S3 sql), 并诚实留了盲区 (B1-B4, 由行为测试兜底)。

全部 fixture 都是内联 Python 源码字符串 (或 tmp_path 现造的文件树), 不读宿主环境的任何
真实文件, 除了最后一个测试 (test_real_services_tree_has_no_frozen_stamp_comparisons) ——
那个测试故意扫真实的 backend/services/ 树, 是这道门在 ci_pytest 下的commit-time 门牙。

注意: 撰写本文件时 backend/services/ 下确实还有本门要抓的活体违规 (另一位工程师正在修),
所以最后一个测试此刻**预期失败**——不 skip / 不 xfail / 不削弱断言, 就让它红, 红的内容
(findings 列表) 本身就是待修清单。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "check_frozen_stamp_compare",
    REPO / "backend" / "scripts" / "check_frozen_stamp_compare.py",
)
cfsc = importlib.util.module_from_spec(_spec)
# 3.13 dataclass 解析 cls.__module__ 时要求它已在 sys.modules (否则
# `_is_type` 在 `sys.modules.get(cls.__module__).__dict__` 上炸 AttributeError) —
# 必须在 exec_module 之前注册, Finding 这个 @dataclass 才能装饰成功。
sys.modules[_spec.name] = cfsc
_spec.loader.exec_module(cfsc)


# ============================================================================
# S1: direct_compare — 冻结落地戳字面量 (subscript) 与活契约/handoff 形参/指针字面量比相等
# ============================================================================

S1_CASES = [
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, contract):
                return batch["config_hash"] == contract.config_hash
            '''
        ),
        id="eq_config_hash_vs_contract",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, contract):
                return str(batch["contract_hash"]) != contract.contract_hash
            '''
        ),
        id="ne_contract_hash_vs_contract",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, contract_hash):
                return str(batch["contract_hash"]) != contract_hash
            '''
        ),
        id="handoff_param_name",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, domain):
                return str(batch["source_name"]) != domain.source
            '''
        ),
        id="source_name_vs_domain_source",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, contract):
                return contract.config_hash != str(batch["config_hash"])
            '''
        ),
        id="reversed_operands",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(landed, pointer):
                return str(landed["config_hash"]) != pointer["config_hash"]
            '''
        ),
        id="frozen_vs_pointer_subscript",
    ),
    pytest.param(
        textwrap.dedent(
            '''\
            def f(batch, contract):
                if (str(batch["contract_version"]) != contract.contract_version or str(batch["config_hash"]) != contract.config_hash):
                    pass
            '''
        ),
        id="chained_boolean_only_config_hash_fires",
    ),
]


@pytest.mark.parametrize("source", S1_CASES)
def test_s1_direct_shapes_each_yield_one_finding(source: str) -> None:
    findings = cfsc.scan_source(source, "fixture.py")
    assert len(findings) == 1, [f.render() for f in findings]
    finding = findings[0]
    assert finding.kind == "direct_compare"
    # 每个 fixture 的比较式都落在函数体的第 2 行 (def 占第 1 行)。
    assert finding.line == 2


# ============================================================================
# S2: tuple_compare — calendar_acceptance 的 expected/actual 元组写法
# ============================================================================


def test_s2_tuple_shape_yields_tuple_finding() -> None:
    source = textwrap.dedent(
        '''\
        def f(batch_id, contract, batch):
            expected_wiring = (batch_id, str(contract.contract_version), str(contract.contract_hash), str(contract.config_hash))
            actual_wiring = (str(batch["partition_value"]), str(batch["contract_version"]), str(batch["contract_hash"]), str(batch["config_hash"]))
            if actual_wiring != expected_wiring:
                raise ValueError
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert len(findings) == 1, [f.render() for f in findings]
    finding = findings[0]
    assert finding.kind == "tuple_compare"
    assert finding.line == 4  # the `if actual_wiring != expected_wiring:` line


def test_s2_tuple_shape_reassignment_clears_tag() -> None:
    """在比较前把 actual_wiring 重新赋成非元组值, 追踪到的 'frozen' 标签应被清掉。"""
    source = textwrap.dedent(
        '''\
        def f(batch_id, contract, batch):
            expected_wiring = (batch_id, str(contract.contract_version), str(contract.contract_hash), str(contract.config_hash))
            actual_wiring = (str(batch["partition_value"]), str(batch["contract_version"]), str(batch["contract_hash"]), str(batch["config_hash"]))
            actual_wiring = 1
            if actual_wiring != expected_wiring:
                raise ValueError
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert findings == []


# ============================================================================
# S3: sql_compare — SQL 文本里拿 ingest_batch 的冻结列与参数比相等
# ============================================================================


def test_s3_sql_fstring_ingest_and_accepted_alias() -> None:
    source = textwrap.dedent(
        '''\
        def f():
            query = f"SELECT ap.batch_id FROM {ACCEPTED_TABLE} ap JOIN {INGEST_BATCH_TABLE} ib ON ib.batch_id = ap.batch_id WHERE ib.contract_hash = ? AND ib.config_hash = ?"
            return query
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert len(findings) >= 1, "expected at least one sql_compare finding"
    assert all(f.kind == "sql_compare" for f in findings)


def test_s3_sql_plain_string_ingest_batch_table() -> None:
    source = textwrap.dedent(
        '''\
        def f():
            return "SELECT batch_id FROM ingest_batch WHERE dataset_id = ? AND contract_hash = ?"
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert len(findings) == 1, [f.render() for f in findings]
    assert findings[0].kind == "sql_compare"


def test_s3_sql_accepted_partition_table_is_allowed() -> None:
    """指针表 (accepted_partition) 已重打过, 与参数比相等不是病。"""
    source = textwrap.dedent(
        '''\
        def f():
            return "SELECT batch_id FROM accepted_partition WHERE dataset_id = ? AND contract_hash = ?"
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert findings == []


def test_s3_sql_mutation_is_not_a_comparison() -> None:
    source = textwrap.dedent(
        '''\
        def f():
            return "UPDATE ingest_batch SET contract_hash = ? WHERE batch_id = ?"
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert findings == []


def test_s3_sql_fstring_ingest_alias_not_compared_is_allowed() -> None:
    """f-string 里 join 了 ingest_batch 记成 ib, 但只拿 accepted_partition (ap) 的列去比;
    ib 自己的冻结列没参与任何 `= ?` 比较, 不该报。"""
    source = textwrap.dedent(
        '''\
        def f():
            return f"SELECT ap.contract_hash FROM {INGEST_BATCH_TABLE} ib JOIN accepted_partition ap ON ap.batch_id = ib.batch_id WHERE ap.contract_hash = ?"
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert findings == []


# ============================================================================
# 允许的形状 (判据文档里明确"不在判据内") — 一个源里全放, 断言零 finding
# ============================================================================


def test_allowed_shapes_are_not_reported() -> None:
    source = textwrap.dedent(
        '''\
        def f1(pointer, contract):
            return pointer["config_hash"] != contract.config_hash

        def f2(contract, fresh):
            return contract.contract_hash != fresh.contract_hash

        def f3(batch, contract):
            return str(batch["contract_version"]) != contract.contract_version

        def f4(batch, contract):
            return str(batch["writer_id"]) != contract.writer_id

        def f5(batch):
            return _hash(contract_hash=batch["contract_hash"], config_hash=batch["config_hash"])

        def f6(batch, SOURCE):
            if batch.source != SOURCE:
                pass

        def f7(r, contract):
            if r[0] != contract.contract_hash:
                pass

        def f8(row, contract):
            if row["config_hash"] != contract.config_hash:
                pass
        '''
    )
    findings = cfsc.scan_source(source, "fixture.py")
    assert findings == [], [f.render() for f in findings]


# ============================================================================
# iter_python_files — 跳过 tests/ 和 __pycache__/
# ============================================================================


def test_iter_python_files_skips_tests_and_pycache(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    (pkg).mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    tests_dir = pkg / "tests"
    tests_dir.mkdir()
    (tests_dir / "b.py").write_text("x = 2\n", encoding="utf-8")
    pycache_dir = pkg / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "c.py").write_text("x = 3\n", encoding="utf-8")

    found = list(cfsc.iter_python_files(tmp_path))
    assert found == [pkg / "a.py"]


# ============================================================================
# main — 退出码 + --json
# ============================================================================


_DIRTY_SOURCE = textwrap.dedent(
    '''\
    def f(batch, contract):
        return str(batch["contract_hash"]) != contract.contract_hash
    '''
)
_CLEAN_SOURCE = "def f():\n    return 1\n"


def test_main_exit_codes_and_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # 1. dirty root → 1, prints the finding
    dirty_root = tmp_path / "dirty"
    dirty_root.mkdir()
    (dirty_root / "m.py").write_text(_DIRTY_SOURCE, encoding="utf-8")

    rc = cfsc.main(["--root", str(dirty_root)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "direct_compare" in out
    assert "1 finding(s)" in out

    # 2. clean root → 0, PASS
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    (clean_root / "m.py").write_text(_CLEAN_SOURCE, encoding="utf-8")

    rc = cfsc.main(["--root", str(clean_root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 finding(s)" in out
    assert "PASS" in out

    # 3. nonexistent root → 2
    missing_root = tmp_path / "does-not-exist"
    rc = cfsc.main(["--root", str(missing_root)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "ERROR" in captured.err

    # 4. --json → machine-readable list with path/line/kind/detail keys
    rc = cfsc.main(["--root", str(dirty_root), "--json"])
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert set(payload[0].keys()) == {"path", "line", "kind", "detail"}
    assert payload[0]["kind"] == "direct_compare"


# ============================================================================
# 真实仓库 — commit-time 门牙
# ============================================================================


def test_real_services_tree_has_no_frozen_stamp_comparisons() -> None:
    """跑在真实 backend/services/ 树上的门牙 (ci_pytest_surface 收录的那道)。

    2026-09-02 撰写本测试时, backend/services/ 下已知还留有本门要抓的活体违规
    (docstring 里点名的 calendar_reader / calendar_acceptance / margin_acceptance
    三处, 另一位工程师正在修) —— 这个测试此刻**预期失败**, 不 skip / 不 xfail /
    不削弱断言。失败信息里列出的 findings 就是待修清单。
    """
    findings = cfsc.scan_tree(cfsc.REPO / "backend" / "services", display_root=cfsc.REPO)
    assert findings == [], "\n".join(f.render() for f in findings)
