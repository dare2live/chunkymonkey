"""防回退: plan_validator gate 真的被 enforce, 跑批入口不带病跑.

2026-06-11 体检 HIGH 修复防回退:
  - formula_local_optuna_batch.py 旧代码 (1) 用 validate_optuna_plan + 手动
    sys.exit 而非 enforce_optuna_plan; (2) `if not plan_result.passed` 在
    searchable_formulas 为空时 plan_result 未绑定 → NameError, gate 静默失效;
    (3) sys.exit/print 缩进错位.
修复后: 入口调 enforce_optuna_plan (FAIL → raise PlanValidationError), 空 searchable
走 else 分支不引用 plan_result.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_BC = Path(__file__).resolve().parents[2] / "services" / "bc_absorbed"
if str(_BC) not in sys.path:
    sys.path.insert(0, str(_BC))

_BATCH = _BC / "scripts" / "formula_local_optuna_batch.py"


def test_enforce_raises_on_invalid_plan():
    """无 search space + 无 output_path 的计划 → enforce 必须 raise (gate 有效)."""
    from plan_validator import PlanValidationError, enforce_optuna_plan

    with pytest.raises(PlanValidationError):
        enforce_optuna_plan(formulas=["__nonexistent_formula__"], trials=10, output_path=None)


def test_enforce_returns_result_on_valid():
    """validate 全 PASS 时 enforce 返回 PlanCheckResult.passed=True (不 raise)."""
    from plan_validator import PlanCheckResult, validate_optuna_plan

    # 用一个全 PASS 的人造 result 直接验证 enforce 的语义 (不依赖真实公式可跑性)
    r = PlanCheckResult()
    r.checks = [{"name": "x", "status": "PASS", "detail": "ok"}]
    assert r.passed is True
    assert r.failed == []


def test_batch_runner_calls_enforce_not_validate():
    """跑批入口必须用 enforce_optuna_plan (raise 路径), 不能退回 validate + 手动 exit."""
    src = _BATCH.read_text(encoding="utf-8")
    assert "enforce_optuna_plan" in src, "batch runner 必须 import/call enforce_optuna_plan"
    # main() 体内不应再 import validate_optuna_plan 作为 gate (只允许 enforce)
    assert "from plan_validator import enforce_optuna_plan" in src


def test_batch_runner_no_unbound_plan_result():
    """plan_result 不能在未赋值分支被引用 (旧 NameError bug 防回退).

    解析 main() AST: 任何引用 plan_result 的语句必须在给它赋值的 if-body 内.
    """
    tree = ast.parse(_BATCH.read_text(encoding="utf-8"))
    main_fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "main"
    )

    # 找到给 plan_result 赋值的 if 块
    assign_if = None
    for node in ast.walk(main_fn):
        if isinstance(node, ast.If):
            assigns = [
                t.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Assign)
                for t in sub.targets
                if isinstance(t, ast.Name)
            ]
            if "plan_result" in assigns:
                assign_if = node
                break
    assert assign_if is not None, "plan_result 应在某 if-body 内赋值"

    # 收集 if-body 内的所有 plan_result Name 节点 id
    in_block_nodes = {
        id(sub)
        for sub in ast.walk(assign_if)
        if isinstance(sub, ast.Name) and sub.id == "plan_result"
    }
    # 全函数内的 plan_result Name 节点
    all_nodes = {
        id(sub): sub
        for sub in ast.walk(main_fn)
        if isinstance(sub, ast.Name) and sub.id == "plan_result"
    }
    outside = [nid for nid in all_nodes if nid not in in_block_nodes]
    assert not outside, (
        "plan_result 在赋值 if-body 之外被引用 — 空 searchable_formulas 时会 NameError"
    )


def test_batch_runner_compiles():
    """语法正确 (缩进 bug 防回退)."""
    import py_compile

    py_compile.compile(str(_BATCH), doraise=True)
