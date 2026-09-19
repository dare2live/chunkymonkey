"""``restamp_stock_st_contract.py`` — v1->v2 plan/execute + ``--to-v1`` rollback.

对应刀 1 spec 附录 A6/A7。全部用内存 DuckDB / 独立临时文件, 不打开
``data/*.duckdb``, 不联网 (project rule: 测试不 mock 掉 calendar/universe/
population 门, 但这里根本不涉及它们 —— 这是纯 land→accept 之后的机械 SQL 重打
测试)。

夹具按契约升版**之前**的 v1 形状手工建表 (``ensure_security_day_schema`` 现在
只会按当前 v2 契约建表, 造不出 v1 形状) —— 列集合/NOT NULL 集合/两个 source_name
的 ingest_batch 行都照 spec §4.6 手写, 不复用任何"现成的 v1 建表函数"(那个函数
已经不存在, 也不该假装它还在)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.restamp_stock_st_contract import (
    RestampMismatchError,
    execute,
    execute_to_v1,
    format_plan,
    format_plan_to_v1,
    main,
    plan,
    plan_to_v1,
)
from services.data_sources.accepted_schema import create_accepted_evidence_tables
from services.data_sources.nominal_ohlcv_contract_versions import load_rollback_target
from services.data_sources.stock_st_schema import CANONICAL_TABLE, DATASET_ID

_ROLLBACK_YAML = (
    Path(__file__).resolve().parents[2] / "config" / "stock_st_contract_versions.yaml"
)
_V1 = load_rollback_target("1", path=_ROLLBACK_YAML)

_NOW = datetime(2026, 8, 28, 9, 30, tzinfo=timezone.utc)


def _v1_shaped_conn() -> duckdb.DuckDBPyConnection:
    """一个内存连接, 手工建出契约升版**之前**的 v1 形状: name/type/type_name 全
    NOT NULL, 无 st_origin 列。"""

    conn = duckdb.connect(":memory:")
    create_accepted_evidence_tables(conn)
    conn.execute(
        f"""
        CREATE TABLE {CANONICAL_TABLE} (
            trade_date DATE NOT NULL,
            ts_code VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            type VARCHAR NOT NULL,
            type_name VARCHAR NOT NULL,
            available_at TIMESTAMP WITH TIME ZONE NOT NULL,
            ingest_batch_id VARCHAR NOT NULL,
            source_row_hash VARCHAR NOT NULL,
            contract_version VARCHAR NOT NULL,
            config_hash VARCHAR NOT NULL,
            built_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (trade_date, ts_code)
        )
        """
    )
    return conn


def _insert_ingest_batch(
    conn, *, batch_id: str, partition: str, source_name: str, row_count: int
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_batch VALUES (
            ?, ?, ?, ?, ?, 'services.data_sources.stock_st_acceptance', ?, ?, 'ACCEPTED',
            '{}', '[]', 1, 1, 0, ?, ?, 'payload-hash', 'canonical-hash',
            ?, ?, ?, ?, ?, NULL, NULL
        )
        """,
        [
            batch_id,
            DATASET_ID,
            _V1.contract_version,
            _V1.contract_hash,
            _V1.config_hash,
            partition,
            source_name,
            row_count,
            row_count,
            _NOW,
            _NOW,
            _NOW,
            _NOW,
            _NOW,
        ],
    )


def _insert_accepted_pointer(conn, *, partition: str, batch_id: str, row_count: int) -> None:
    conn.execute(
        """
        INSERT INTO accepted_partition VALUES (
            ?, ?, ?, ?, ?, ?, ?, 'content-hash-fixture', ?, ?, ?
        )
        """,
        [
            DATASET_ID,
            partition,
            batch_id,
            _V1.contract_version,
            _V1.contract_hash,
            _V1.config_hash,
            row_count,
            _NOW,
            _NOW,
            _NOW,
        ],
    )


def _insert_canonical_row(
    conn, *, trade_date: str, ts_code: str, name: str, batch_id: str
) -> None:
    conn.execute(
        f"""
        INSERT INTO {CANONICAL_TABLE} VALUES (
            ?, ?, ?, 'ST', '风险警示板', ?, ?, 'row-hash-fixture', ?, ?, ?
        )
        """,
        [
            trade_date,
            ts_code,
            name,
            _NOW,
            batch_id,
            _V1.contract_version,
            _V1.config_hash,
            _NOW,
        ],
    )


def _two_source_v1_fixture() -> duckdb.DuckDBPyConnection:
    """两个分区, 各自的 source_name 不同 (tushare / stock_st_derive) —— 回填映射
    必须按 ingest_batch.source_name 分别落到不同的 st_origin 标签上。"""

    conn = _v1_shaped_conn()
    _insert_ingest_batch(
        conn, batch_id="b-tushare", partition="20220104", source_name="tushare", row_count=1
    )
    _insert_accepted_pointer(conn, partition="20220104", batch_id="b-tushare", row_count=1)
    _insert_canonical_row(
        conn, trade_date="2022-01-04", ts_code="000005.SZ", name="ST星源", batch_id="b-tushare"
    )

    _insert_ingest_batch(
        conn,
        batch_id="b-derive",
        partition="20260917",
        source_name="stock_st_derive",
        row_count=1,
    )
    _insert_accepted_pointer(conn, partition="20260917", batch_id="b-derive", row_count=1)
    _insert_canonical_row(
        conn, trade_date="2026-09-17", ts_code="000001.SZ", name="ST甲", batch_id="b-derive"
    )
    return conn


# ---------------------------------------------------------------------------
# A6: plan + execute backfills st_origin by ingest_batch.source_name
# ---------------------------------------------------------------------------


def test_plan_computes_add_column_and_backfill_shape():
    conn = _two_source_v1_fixture()
    try:
        p = plan(conn)
        assert p.add_columns == (("st_origin", "VARCHAR"),)
        assert p.drop_not_null == ("name",)
        assert p.set_not_null == ("st_origin",)
        assert p.unmapped_sources == ()
        assert p.executable is True
        assert p.is_noop is False
        rendered = format_plan(p)
        assert "st_origin" in rendered
    finally:
        conn.close()


def test_execute_backfills_st_origin_and_preserves_content_and_ingest_batch():
    conn = _two_source_v1_fixture()
    try:
        p = plan(conn)
        content_before = dict(p.content_hash_before)
        ingest_before = p.ingest_batch_before

        execute(conn, p)

        rows = conn.execute(
            f"SELECT ts_code, st_origin FROM {CANONICAL_TABLE} ORDER BY ts_code"
        ).fetchall()
        by_code = {r[0]: r[1] for r in rows}
        assert by_code["000005.SZ"] == "provider_tushare_stock_st"
        assert by_code["000001.SZ"] == "derived_name_prefix"

        # content_hash 未动 (重打只改戳不改内容)
        after_content = {
            str(r[0]): str(r[1])
            for r in conn.execute(
                "SELECT partition_value, content_hash FROM accepted_partition WHERE dataset_id = ?",
                [DATASET_ID],
            ).fetchall()
        }
        assert after_content == content_before

        # ingest_batch 戳组合分布未动
        ingest_after = tuple(
            tuple(r)
            for r in conn.execute(
                """SELECT contract_version, contract_hash, config_hash, source_name,
                          status, COUNT(*)
                     FROM ingest_batch WHERE dataset_id = ?
                    GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5""",
                [DATASET_ID],
            ).fetchall()
        )
        assert ingest_after == ingest_before

        # 表形状 == 契约 (st_origin 现在 NOT NULL, name 现在可空)
        cols = {
            str(r[0]): str(r[1]).upper()
            for r in conn.execute(f"DESCRIBE {CANONICAL_TABLE}").fetchall()
        }
        assert "st_origin" in cols
        not_null = {
            str(r[1][0])
            for r in conn.execute(
                "SELECT constraint_type, constraint_column_names FROM duckdb_constraints() "
                "WHERE table_name = ?",
                [CANONICAL_TABLE],
            ).fetchall()
            if str(r[0]).upper() == "NOT NULL" and r[1] and len(r[1]) == 1
        }
        assert "name" not in not_null
        assert "st_origin" in not_null

        # 重跑一次 -> no-op
        p2 = plan(conn)
        assert p2.is_noop is True
    finally:
        conn.close()


def test_replan_after_execute_is_noop_and_does_not_reclobber_baostock_rows():
    """表上已有 st_origin 且混有 provider_baostock_isst 行时, plan.is_noop 且不
    进回填分支 —— 重跑脚本不会把水库来源的行错标成名称路径。"""

    conn = _two_source_v1_fixture()
    try:
        p = plan(conn)
        execute(conn, p)

        # 模拟一行后来由双水库派生器直接写入的 baostock 来源行 (st_origin 由适配器
        # 逐行给出, 不经过这份历史回填映射)。
        conn.execute(
            f"UPDATE {CANONICAL_TABLE} SET st_origin = 'provider_baostock_isst' "
            "WHERE ts_code = '000001.SZ'"
        )

        p2 = plan(conn)
        assert p2.add_columns == ()
        assert p2.is_noop is True

        execute(conn, p2)
        still = conn.execute(
            f"SELECT st_origin FROM {CANONICAL_TABLE} WHERE ts_code = '000001.SZ'"
        ).fetchone()[0]
        assert still == "provider_baostock_isst"  # 不回填/不覆盖
    finally:
        conn.close()


def test_unmapped_source_name_is_not_executable_and_cli_exits_2(tmp_path):
    conn = _v1_shaped_conn()
    _insert_ingest_batch(conn, batch_id="b-zzz", partition="20220104", source_name="zzz", row_count=1)
    _insert_accepted_pointer(conn, partition="20220104", batch_id="b-zzz", row_count=1)
    _insert_canonical_row(
        conn, trade_date="2022-01-04", ts_code="000005.SZ", name="ST星源", batch_id="b-zzz"
    )
    p = plan(conn)
    assert p.unmapped_sources == ("zzz",)
    assert p.executable is False
    rendered = format_plan(p)
    assert "zzz" in rendered
    with pytest.raises(RestampMismatchError, match="zzz"):
        execute(conn, p)
    conn.close()

    db_file = tmp_path / "copy.duckdb"
    conn2 = duckdb.connect(str(db_file))
    create_accepted_evidence_tables(conn2)
    conn2.execute(
        f"""
        CREATE TABLE {CANONICAL_TABLE} (
            trade_date DATE NOT NULL, ts_code VARCHAR NOT NULL, name VARCHAR NOT NULL,
            type VARCHAR NOT NULL, type_name VARCHAR NOT NULL,
            available_at TIMESTAMP WITH TIME ZONE NOT NULL, ingest_batch_id VARCHAR NOT NULL,
            source_row_hash VARCHAR NOT NULL, contract_version VARCHAR NOT NULL,
            config_hash VARCHAR NOT NULL, built_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (trade_date, ts_code)
        )
        """
    )
    _insert_ingest_batch(conn2, batch_id="b-zzz", partition="20220104", source_name="zzz", row_count=1)
    _insert_accepted_pointer(conn2, partition="20220104", batch_id="b-zzz", row_count=1)
    _insert_canonical_row(
        conn2, trade_date="2022-01-04", ts_code="000005.SZ", name="ST星源", batch_id="b-zzz"
    )
    conn2.close()

    rc = main(["--db-override", str(db_file), "--execute"])
    assert rc == 2


# ---------------------------------------------------------------------------
# A7: rollback to v1
# ---------------------------------------------------------------------------


def test_plan_to_v1_and_execute_to_v1_restores_v1_stamps():
    conn = _two_source_v1_fixture()
    try:
        p = plan(conn)
        execute(conn, p)

        rp = plan_to_v1(conn)
        assert rp.drop_columns == ("st_origin",)
        assert rp.restore_not_null == ("name",)
        assert rp.executable is True
        assert rp.null_row_count == 0

        execute_to_v1(conn, rp)

        pointer_rows = conn.execute(
            "SELECT DISTINCT contract_version, contract_hash, config_hash "
            "FROM accepted_partition WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchall()
        assert pointer_rows == [(_V1.contract_version, _V1.contract_hash, _V1.config_hash)]

        canonical_rows = conn.execute(
            f"SELECT DISTINCT contract_version, config_hash FROM {CANONICAL_TABLE}"
        ).fetchall()
        assert canonical_rows == [(_V1.contract_version, _V1.config_hash)]

        cols = {
            str(r[0]) for r in conn.execute(f"DESCRIBE {CANONICAL_TABLE}").fetchall()
        }
        assert "st_origin" not in cols
    finally:
        conn.close()


def test_plan_to_v1_blocked_once_a_null_name_row_exists():
    conn = _two_source_v1_fixture()
    try:
        p = plan(conn)
        execute(conn, p)

        # 模拟一行 baostock 来源的行: name NULL, st_origin 已存在 (v2 表上合法)。
        conn.execute(
            f"""
            INSERT INTO {CANONICAL_TABLE}
                (trade_date, ts_code, name, type, type_name, st_origin,
                 available_at, ingest_batch_id, source_row_hash, contract_version,
                 config_hash, built_at)
            VALUES (?, ?, NULL, 'ST', '风险警示板', 'provider_baostock_isst',
                    ?, 'b-derive', 'row-hash-fixture-2', ?, ?, ?)
            """,
            ["2026-09-16", "000002.SZ", _NOW, p.target_contract_version, p.target_config_hash, _NOW],
        )

        rp = plan_to_v1(conn)
        assert rp.null_row_count == 1
        assert rp.executable is False
        rendered = format_plan_to_v1(rp)
        assert "必须先删除含 NULL 的分区" in rendered

        with pytest.raises(RestampMismatchError, match="必须先删除含 NULL 的分区"):
            execute_to_v1(conn, rp)
    finally:
        conn.close()
