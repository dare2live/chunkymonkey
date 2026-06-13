"""governance_log 写入原子性单测 — 防 orphan governance (2026-06-14 D0 扫描发现).

背景: optimize_per_stock_stage_strategy.py / optimize_per_formula_stage.py 原先把
log_governance_violations 写在业务表 BEGIN TRANSACTION 之前 = 独立提交 → 业务写回滚而
governance 已落 = 审计与结果不一致 (orphan governance, late-rejection 不可见 = PIT 审计盲区)。
修法: log_governance_violations 加 manage_txn 参数; 调用方传 manage_txn=False 让 governance
写与业务写同事务原子提交/回滚。本测试锁住该机制 (中断模拟 = ROLLBACK 后无 orphan)。
"""
from __future__ import annotations

import pytest

from services.duck_adapter import connect as duck_connect
from services.optimization.ddl import ensure_optuna_tables, log_governance_violations
from services.optimization.config import get_optuna_config


def _violation(stock_code: str = "000001") -> dict:
    return {
        "stock_code": stock_code,
        "formula_id": "f_test",
        "formula_variant": "v0",
        "stage_filter": "stage2",
        "reason": "sharpe>5 absolute red line",
        "record_json": "{}",
    }


@pytest.fixture()
def conn(tmp_path):
    c = duck_connect(str(tmp_path / "test_gov.duckdb"), read_only=False)
    ensure_optuna_tables(c)
    yield c
    c.close()


def _gov_count(conn) -> int:
    table = get_optuna_config().output.governance_log_table
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_manage_txn_false_rollback_leaves_no_orphan(conn):
    """调用方事务 ROLLBACK (模拟业务写中断) → governance 写一并回滚, 无 orphan。"""
    conn.execute("BEGIN TRANSACTION")
    log_governance_violations(conn, "run_rollback", [_violation()], manage_txn=False)
    # 模拟业务写失败 → 调用方 ROLLBACK
    conn.execute("ROLLBACK")
    assert _gov_count(conn) == 0, "ROLLBACK 后 governance 行应一并回滚 (无 orphan)"


def test_manage_txn_false_commit_persists(conn):
    """调用方事务 COMMIT → governance 写随业务写一同落库。"""
    conn.execute("BEGIN TRANSACTION")
    log_governance_violations(conn, "run_commit", [_violation()], manage_txn=False)
    conn.execute("COMMIT")
    assert _gov_count(conn) == 1, "COMMIT 后 governance 行应持久化"


def test_manage_txn_true_self_commits_backward_compat(conn):
    """默认 manage_txn=True 仍自起事务自提交 (向后兼容, 不破坏旧独立调用)。"""
    n = log_governance_violations(conn, "run_default", [_violation()])
    assert n == 1
    assert _gov_count(conn) == 1, "默认路径应自提交"


def test_empty_violations_noop(conn):
    """空 violations 直接返回 0, 不碰事务 (manage_txn=False 下不应误发 COMMIT/ROLLBACK)。"""
    conn.execute("BEGIN TRANSACTION")
    n = log_governance_violations(conn, "run_empty", [], manage_txn=False)
    assert n == 0
    # 事务仍开着, 可正常 COMMIT (空 violations 没动事务边界)
    conn.execute("COMMIT")
    assert _gov_count(conn) == 0
