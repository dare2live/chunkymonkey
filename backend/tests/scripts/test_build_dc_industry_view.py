from __future__ import annotations

import pytest

from backend.scripts import build_dc_industry_view
from services.duck_adapter import connect


def _build_fixture(tmp_path, monkeypatch):
    raw_db = tmp_path / "tushare_raw.duckdb"
    smart_db = tmp_path / "smartmoney.duckdb"
    raw = connect(str(raw_db), read_only=False)
    try:
        raw.execute(
            """
            CREATE TABLE raw_tushare_dc_index (
                trade_date TEXT, idx_type TEXT, ts_code TEXT, name TEXT, level TEXT
            )
            """
        )
        raw.execute(
            """
            CREATE TABLE raw_tushare_dc_member (
                trade_date TEXT, ts_code TEXT, con_code TEXT
            )
            """
        )
        raw.executemany(
            "INSERT INTO raw_tushare_dc_index VALUES (?, ?, ?, ?, ?)",
            [
                ("20260716", "行业板块", "I1A", "一级甲", "东财一级行业"),
                ("20260716", "行业板块", "I2A", "二级甲", "东财二级行业"),
                ("20260716", "行业板块", "I3A", "三级甲", "东财三级行业"),
                ("20260716", "行业板块", "I1B", "一级乙", "东财一级行业"),
                ("20260716", "行业板块", "I2B", "二级乙", "东财二级行业"),
                ("20260716", "行业板块", "I3B", "三级乙", "东财三级行业"),
                ("20260716", "概念板块", "C1", "概念甲", None),
                ("20260716", "概念板块", "C2", "概念乙", None),
            ],
        )
        raw.executemany(
            "INSERT INTO raw_tushare_dc_member VALUES (?, ?, ?)",
            [
                ("20260716", "I1A", "000001.SZ"),
                ("20260716", "I2A", "000001.SZ"),
                ("20260716", "I3A", "000001.SZ"),
                ("20260716", "I1B", "000002.SZ"),
                ("20260716", "I2B", "000002.SZ"),
                ("20260716", "I3B", "000002.SZ"),
                ("20260716", "C1", "000001.SZ"),
                ("20260716", "C2", "000002.SZ"),
            ],
        )
    finally:
        raw.close()

    monkeypatch.setattr(build_dc_industry_view, "TRAW", str(raw_db))
    monkeypatch.setattr(build_dc_industry_view, "SMARTMONEY", str(smart_db))
    monkeypatch.setattr(
        build_dc_industry_view,
        "current_snapshot_quality_floor",
        lambda namespace: (
            {"min_stocks": 2, "min_nodes_by_level": {"L1": 2, "L2": 2, "L3": 2}}
            if namespace == "dc_industry"
            else {"min_memberships": 2, "min_stocks": 2, "min_nodes": 2}
        ),
    )
    build_dc_industry_view.build_current_dims()
    return smart_db, raw_db


def _dims_snapshot(smart_db):
    conn = connect(str(smart_db), read_only=True)
    try:
        industry = [tuple(row) for row in conn.execute(
            "SELECT * FROM dim_stock_dc_industry ORDER BY stock_code"
        ).fetchall()]
        concepts = [tuple(row) for row in conn.execute(
            "SELECT * FROM dim_stock_dc_concept ORDER BY stock_code, concept_code"
        ).fetchall()]
        schemas = {
            table: [tuple(row) for row in conn.execute(
                f"PRAGMA table_info('{table}')"
            ).fetchall()]
            for table in ("dim_stock_dc_industry", "dim_stock_dc_concept")
        }
        indexes = [tuple(row) for row in conn.execute("""
            SELECT index_name, table_name, expressions, is_unique
            FROM duckdb_indexes()
            WHERE table_name IN ('dim_stock_dc_industry', 'dim_stock_dc_concept')
            ORDER BY index_name
        """).fetchall()]
        return industry, concepts, schemas, indexes
    finally:
        conn.close()


class _FailAfterSql:
    def __init__(self, inner, needle: str):
        self.inner = inner
        self.needle = " ".join(needle.split()).upper()

    def execute(self, sql, params=None):
        result = self.inner.execute(sql, params)
        if self.needle in " ".join(str(sql).split()).upper():
            raise RuntimeError("injected post-rename failure")
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_verify_accepts_exact_raw_snapshot_parity_with_duck_adapter_row(tmp_path, monkeypatch):
    _build_fixture(tmp_path, monkeypatch)

    assert build_dc_industry_view.verify() == 0


def test_second_valid_rebuild_is_idempotent_with_unique_indexes(tmp_path, monkeypatch):
    smart_db, _raw_db = _build_fixture(tmp_path, monkeypatch)

    build_dc_industry_view.build_current_dims()

    snapshot = _dims_snapshot(smart_db)
    assert len(snapshot[0]) == 2 and len(snapshot[1]) == 2
    assert {row[0] for row in snapshot[3]} == {
        "idx_dc_concept_grain",
        "idx_dc_industry_stock",
    }


def test_verify_rejects_collapsed_taxonomy_despite_full_stock_coverage(tmp_path, monkeypatch):
    smart_db, _raw_db = _build_fixture(tmp_path, monkeypatch)
    conn = connect(str(smart_db), read_only=False)
    try:
        conn.execute(
            """
            UPDATE dim_stock_dc_industry SET
                tdx_l1='I1A', tdx_l1_name='一级甲',
                tdx_l2='I2A', tdx_l2_name='二级甲',
                tdx_l3='I3A', tdx_l3_name='三级甲'
            """
        )
    finally:
        conn.close()

    assert build_dc_industry_view.verify() == 1


def test_verify_rejects_empty_concept_snapshot(tmp_path, monkeypatch):
    smart_db, _raw_db = _build_fixture(tmp_path, monkeypatch)
    conn = connect(str(smart_db), read_only=False)
    try:
        conn.execute("DELETE FROM dim_stock_dc_concept")
    finally:
        conn.close()

    assert build_dc_industry_view.verify() == 1


def test_verify_rejects_provider_snapshot_collapse_even_when_serving_matches(tmp_path, monkeypatch):
    smart_db, raw_db = _build_fixture(tmp_path, monkeypatch)
    before = _dims_snapshot(smart_db)
    raw = connect(str(raw_db), read_only=False)
    try:
        raw.execute("DELETE FROM raw_tushare_dc_index WHERE ts_code IN ('I1B','I2B','I3B','C2')")
        raw.execute("DELETE FROM raw_tushare_dc_member WHERE con_code = '000002.SZ'")
    finally:
        raw.close()

    with pytest.raises(RuntimeError, match="accepted tables were not published"):
        build_dc_industry_view.build_current_dims()

    assert _dims_snapshot(smart_db) == before
    assert build_dc_industry_view.verify() == 1


def test_verify_rejects_ambiguous_same_level_memberships(tmp_path, monkeypatch):
    smart_db, raw_db = _build_fixture(tmp_path, monkeypatch)
    before = _dims_snapshot(smart_db)
    raw = connect(str(raw_db), read_only=False)
    try:
        raw.execute(
            "INSERT INTO raw_tushare_dc_index VALUES "
            "('20260716', '行业板块', 'I1X', '一级冲突', '东财一级行业')"
        )
        raw.execute(
            "INSERT INTO raw_tushare_dc_member VALUES ('20260716', 'I1X', '000001.SZ')"
        )
    finally:
        raw.close()

    with pytest.raises(RuntimeError, match="accepted tables were not published"):
        build_dc_industry_view.build_current_dims()

    assert _dims_snapshot(smart_db) == before
    assert build_dc_industry_view.verify() == 1


def test_build_rejects_unmapped_industry_level_and_preserves_live_pair(tmp_path, monkeypatch):
    smart_db, raw_db = _build_fixture(tmp_path, monkeypatch)
    before = _dims_snapshot(smart_db)
    raw = connect(str(raw_db), read_only=False)
    try:
        raw.execute(
            "INSERT INTO raw_tushare_dc_index VALUES "
            "('20260716', '行业板块', 'IX', '未知层级', '供应商新层级')"
        )
        raw.execute(
            "INSERT INTO raw_tushare_dc_member VALUES ('20260716', 'IX', '000001.SZ')"
        )
    finally:
        raw.close()

    with pytest.raises(RuntimeError, match="accepted tables were not published"):
        build_dc_industry_view.build_current_dims()

    assert _dims_snapshot(smart_db) == before


def test_source_frontier_mismatch_preserves_live_pair(tmp_path, monkeypatch):
    smart_db, raw_db = _build_fixture(tmp_path, monkeypatch)
    before = _dims_snapshot(smart_db)
    raw = connect(str(raw_db), read_only=False)
    try:
        raw.execute(
            "INSERT INTO raw_tushare_dc_index VALUES "
            "('20260717', '行业板块', 'I1A', '一级甲', '东财一级行业')"
        )
        raw.execute(
            "INSERT INTO raw_tushare_dc_index VALUES "
            "('20260717', '概念板块', 'C1', '概念甲', NULL)"
        )
    finally:
        raw.close()

    with pytest.raises(RuntimeError, match="source frontier mismatch"):
        build_dc_industry_view.build_current_dims()

    assert _dims_snapshot(smart_db) == before


def test_publish_failure_after_first_rename_rolls_back_data_schema_and_indexes(
    tmp_path,
    monkeypatch,
):
    smart_db, raw_db = _build_fixture(tmp_path, monkeypatch)
    before = _dims_snapshot(smart_db)
    inner = connect(
        str(smart_db),
        read_only=False,
        attach={"traw": {"path": str(raw_db), "read_only": True}},
    )
    failing = _FailAfterSql(
        inner,
        f"ALTER TABLE {build_dc_industry_view._DIM_IND_NEXT} "
        f"RENAME TO {build_dc_industry_view.DIM_IND}",
    )
    monkeypatch.setattr(build_dc_industry_view, "connect", lambda *args, **kwargs: failing)

    with pytest.raises(RuntimeError, match="injected post-rename failure"):
        build_dc_industry_view.build_current_dims()

    assert _dims_snapshot(smart_db) == before
