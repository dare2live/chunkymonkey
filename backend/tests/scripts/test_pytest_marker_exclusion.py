"""钉住 pytest.ini 的排除规则与「测试文件」定义。

排除 realdb / perf / network / gcp / slow 五类测试
唯一靠 pytest.ini 的 addopts `-m` 子句; 而「测试文件」这个概念本身唯一靠
pytest.ini 的 `python_files`。这条测试是唯一钉住它们的地方:

1. addopts 的 `-m` 子句必须排除这五个 marker
   (K2a: 删掉这个子句 -> test_addopts_excludes_optional_markers 变红)。
2. 曾经靠旧登记表的排除名单排除的三个文件, 各自必须真的带着对应 marker,
   否则 addopts 排除不了它们, 它们会漏进默认收集面
   (K2b: 去掉 backend/tests/test_system_routes.py 的 pytestmark ->
    test_optional_files_carry_exclusion_marker[system_routes] 变红)。
3. `python_files` 必须钉死为 `test_*.py`, 与 Step 3.4 的 git ls-files pathspec
   同一条表达式 —— 否则「pytest 认的测试文件」与「git 索引里要跑的文件」两个定义
   可能对不上 (K2c: 删掉这一行 -> test_python_files_pins_test_prefix 变红)。

用 configparser 读 pytest.ini、用 ast 读三个文件的语法树 —— 不 import 它们:
test_system_routes.py 导入期会拉起 FastAPI app, test_real_data_consistency.py /
test_perf_p1_trade_date.py 的模块顶层会碰生产 DuckDB 路径, 只看标记声明本身
就够了, 不需要真的执行它们。
"""
from __future__ import annotations

import ast
import configparser
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PYTEST_INI = REPO / "pytest.ini"

REQUIRED_EXCLUDED_MARKERS = ("realdb", "perf", "network", "gcp", "slow")

# (parametrize id, 文件路径, 该文件必须带着的 marker 名字)
OPTIONAL_FILES = (
    (
        "system_routes",
        REPO / "backend" / "tests" / "test_system_routes.py",
        ("realdb",),
    ),
    (
        "real_data_consistency",
        REPO / "backend" / "tests" / "realdb" / "test_real_data_consistency.py",
        ("realdb",),
    ),
    (
        "perf_p1_trade_date",
        REPO / "backend" / "tests" / "scripts" / "test_perf_p1_trade_date.py",
        ("perf", "slow"),
    ),
)

_M_CLAUSE = re.compile(r'-m\s+"([^"]*)"')


def _read_pytest_ini() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(PYTEST_INI, encoding="utf-8")
    return config


def _addopts_minus_clause() -> str:
    addopts = _read_pytest_ini().get("pytest", "addopts")
    match = _M_CLAUSE.search(addopts)
    # 没匹配到就是空子句, 不是异常 —— 交给下面的断言正常判红,
    # 不许在这里抛看不出「addopts 断言」的无关异常。
    return match.group(1) if match else ""


def test_addopts_excludes_optional_markers() -> None:
    clause = _addopts_minus_clause()
    for name in REQUIRED_EXCLUDED_MARKERS:
        assert f"not {name}" in clause, (
            f"pytest.ini addopts 的 -m 子句必须排除 {name}, 实测子句: {clause!r}"
        )


def test_python_files_pins_test_prefix() -> None:
    """K2c: `python_files` 必须钉死为 `test_*.py`, 与 Step 3.4 的
    `git ls-files -- ':(glob)backend/tests/**/test_*.py'` 是同一条表达式 ——
    否则 pytest 自己认的「测试文件」定义可能悄悄漂到跟 git 索引不一致的地方
    (例如放行 `*_test.py`, 而那个命名今天在仓库里不存在, 一旦出现就成了一个
    git 索引里没有、pytest 却会收集的文件, 或反过来)。
    """
    config = _read_pytest_ini()
    assert config.has_option("pytest", "python_files"), (
        "pytest.ini 缺 python_files —— 与 Step 3.4 的 git ls-files pathspec 脱钩"
    )
    value = config.get("pytest", "python_files").strip()
    assert value == "test_*.py", (
        f"pytest.ini 的 python_files 必须钉死为 test_*.py, 实测: {value!r}"
    )


def _collect_mark_names(tree: ast.Module) -> set[str]:
    """收集模块级 `pytestmark = ...` 赋值与全部函数装饰器里出现的 pytest.mark.<name>。"""
    names: set[str] = set()

    def _record(node: ast.AST) -> None:
        target = node.func if isinstance(node, ast.Call) else node
        # pytest.mark.foo -> Attribute(value=Attribute(value=Name('pytest'), attr='mark'), attr='foo')
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "pytest"
        ):
            names.add(target.attr)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            for sub in ast.walk(node.value):
                _record(sub)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                _record(dec)

    return names


@pytest.mark.parametrize(
    "path,required",
    [(p, r) for _, p, r in OPTIONAL_FILES],
    ids=[name for name, _, _ in OPTIONAL_FILES],
)
def test_optional_files_carry_exclusion_marker(
    path: Path, required: tuple[str, ...]
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    marks = _collect_mark_names(tree)
    missing = [m for m in required if m not in marks]
    assert not missing, (
        f"{path.relative_to(REPO)} 缺少排除 marker {missing} "
        f"(已声明: {sorted(marks)}) —— pytest.ini addopts 排除不了它, "
        "它会漏进默认收集面。"
    )
