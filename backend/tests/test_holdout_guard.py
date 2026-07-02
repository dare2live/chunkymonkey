"""holdout_guard 单测 — master plan §2.1 机械门 (内存库, conftest.duck_mem)。

覆盖: 正常路径 / 无预注册 touch raise / 预算耗尽 raise / assert 门越界 raise /
预注册消耗后不可复用 / 预算全局跨 experiment / 空判据拒注册。
政策数字 (holdout_start / touch_budget) 全部从 holdout_policy.yaml 读, 测试不 hardcode 第二真相源。
"""
from __future__ import annotations

from datetime import date

import pytest

from conftest import duck_mem
from services import holdout_guard as hg


@pytest.fixture()
def conn():
    c = duck_mem()
    yield c
    c.close()


@pytest.fixture()
def policy():
    return hg.load_policy()


# ---------- 正常路径 ----------

def test_register_then_touch_normal_path(conn, policy):
    budget = int(policy["touch_budget"])
    tid = hg.register_criteria("exp_d4_oos", "预注册判据: 超额>0 且 max_dd>-20% 算过", conn=conn)
    assert isinstance(tid, str) and len(tid) == 32

    # 注册后未消耗: touched_at NULL
    row = conn.execute(
        "SELECT experiment, criteria, criteria_sha256, registered_at, touched_at "
        "FROM holdout_touch_log WHERE touch_id=?", (tid,)).fetchone()
    assert row["experiment"] == "exp_d4_oos"
    assert "超额>0" in row["criteria"]
    assert len(row["criteria_sha256"]) == 64
    assert row["registered_at"] is not None
    assert row["touched_at"] is None

    remaining = hg.touch_holdout("exp_d4_oos", conn=conn)
    assert remaining == budget - 1

    # 消耗后 touched_at 落
    touched_at = conn.execute(
        "SELECT touched_at FROM holdout_touch_log WHERE touch_id=?", (tid,)).fetchone()[0]
    assert touched_at is not None


def test_touch_consumes_oldest_prereg_and_decrements(conn, policy):
    budget = int(policy["touch_budget"])
    hg.register_criteria("exp_a", "判据1", conn=conn)
    hg.register_criteria("exp_a", "判据2", conn=conn)
    assert hg.touch_holdout("exp_a", conn=conn) == budget - 1
    assert hg.touch_holdout("exp_a", conn=conn) == budget - 2
    n_unconsumed = conn.execute(
        "SELECT count(*) FROM holdout_touch_log WHERE touched_at IS NULL").fetchone()[0]
    assert n_unconsumed == 0


# ---------- 无预注册 touch = raise ----------

def test_touch_without_preregistration_raises(conn):
    with pytest.raises(hg.HoldoutPreregistrationMissing):
        hg.touch_holdout("exp_never_registered", conn=conn)


def test_prereg_consumed_not_reusable(conn):
    hg.register_criteria("exp_b", "判据只够一次", conn=conn)
    hg.touch_holdout("exp_b", conn=conn)
    with pytest.raises(hg.HoldoutPreregistrationMissing):
        hg.touch_holdout("exp_b", conn=conn)  # 已消耗, 无未消耗行


def test_other_experiment_prereg_not_borrowable(conn):
    hg.register_criteria("exp_c", "exp_c 的判据", conn=conn)
    with pytest.raises(hg.HoldoutPreregistrationMissing):
        hg.touch_holdout("exp_d", conn=conn)  # 预注册按 experiment 匹配, 不许借


# ---------- 预算耗尽 = raise (且全局跨 experiment) ----------

def test_budget_exhausted_raises(conn, policy):
    budget = int(policy["touch_budget"])
    for k in range(budget):
        hg.register_criteria("exp_burn", f"判据{k}", conn=conn)
        remaining = hg.touch_holdout("exp_burn", conn=conn)
    assert remaining == 0
    hg.register_criteria("exp_burn", "第 budget+1 次", conn=conn)
    with pytest.raises(hg.HoldoutBudgetExhausted):
        hg.touch_holdout("exp_burn", conn=conn)


def test_budget_is_global_across_experiments(conn, policy):
    budget = int(policy["touch_budget"])
    for k in range(budget):
        exp = f"exp_global_{k}"  # 每次换名 — 换名不能绕预算
        hg.register_criteria(exp, f"判据{k}", conn=conn)
        hg.touch_holdout(exp, conn=conn)
    hg.register_criteria("exp_global_new", "换名再来", conn=conn)
    with pytest.raises(hg.HoldoutBudgetExhausted):
        hg.touch_holdout("exp_global_new", conn=conn)


# ---------- assert 门: 越界日期 raise ----------

def test_assert_gate_raises_beyond_holdout_start(policy):
    hs = str(policy["holdout_start"])  # "20250601"
    with pytest.raises(hg.HoldoutBoundaryViolation):
        hg.assert_holdout_untouched("20250602")
    with pytest.raises(hg.HoldoutBoundaryViolation):
        hg.assert_holdout_untouched("2026-07-01")
    with pytest.raises(hg.HoldoutBoundaryViolation):
        hg.assert_holdout_untouched(date(2025, 12, 31))
    assert hs == "20250601"  # 政策锚 (变更需同步 master plan §2.1)


def test_assert_gate_passes_within_train_window():
    hg.assert_holdout_untouched("20250531")
    hg.assert_holdout_untouched("2025-05-31")
    hg.assert_holdout_untouched(date(2024, 12, 31))
    hg.assert_holdout_untouched("20250601")  # 切分日本身 = train 右边界, 允许


def test_assert_gate_rejects_garbage_date():
    with pytest.raises(ValueError):
        hg.assert_holdout_untouched("not-a-date")


# ---------- 注册输入校验 ----------

def test_register_empty_criteria_raises(conn):
    with pytest.raises(ValueError):
        hg.register_criteria("exp_e", "", conn=conn)
    with pytest.raises(ValueError):
        hg.register_criteria("exp_e", "   ", conn=conn)
    with pytest.raises(ValueError):
        hg.register_criteria("", "判据", conn=conn)
