"""mart_data_deletion_record 记账: 目标表有无主键都必须能写, 且同一 record_id 重记是替换不是追加。

2026-09-11 实测: tushare_raw / market / reference / feature_store 四个库里这张表没有任何约束,
只有 smartmoney 有 record_id 主键; 旧实现 INSERT OR REPLACE 在无主键表上 Binder Error。
"""
from __future__ import annotations

import json

from services.data_deletion import record_data_deletion
from services.duck_adapter import connect

_NO_PK_DDL = """
CREATE TABLE mart_data_deletion_record (
    record_id TEXT, deletion_run_id TEXT, table_name TEXT, delete_scope TEXT,
    key_column TEXT, key_value TEXT, deleted_rows BIGINT, deleted_files BIGINT,
    deleted_bytes BIGINT, reason TEXT, verification_json TEXT, deleted_at TEXT
)
"""


def _rows(conn):
    return conn.execute(
        "SELECT record_id, deleted_rows, verification_json FROM mart_data_deletion_record ORDER BY record_id"
    ).fetchall()


def _record(conn, deleted_rows, verification, scope="rows_replaced_by_partition_reland"):
    record_data_deletion(
        conn,
        deletion_run_id="r1",
        table_name="raw_demo",
        delete_scope=scope,
        reason="test",
        key_column="trade_date",
        key_value="20230103..20230104",
        deleted_rows=deleted_rows,
        verification=verification,
    )


def test_record_on_table_without_primary_key_replaces_same_record_id():
    conn = connect(":memory:")
    conn.execute(_NO_PK_DDL)
    _record(conn, 10, {"n": 1})
    _record(conn, 12, {"n": 2})
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0][1] == 12
    assert json.loads(rows[0][2]) == {"n": 2}


def test_record_on_fresh_table_with_primary_key_replaces_same_record_id():
    conn = connect(":memory:")
    _record(conn, 10, {"n": 1})
    _record(conn, 12, {"n": 2})
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0][1] == 12


def test_different_scope_is_a_separate_record():
    conn = connect(":memory:")
    conn.execute(_NO_PK_DDL)
    _record(conn, 10, {})
    _record(conn, 0, {}, scope="rows_replaced_verified")
    assert len(_rows(conn)) == 2
