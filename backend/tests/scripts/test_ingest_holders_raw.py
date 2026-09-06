"""ingest_holders_raw.py Phase A 取数刀的钉死断言 (mock client, 0 次真网络请求).

见 backend/scripts/ingest_holders_raw.py 模块 docstring: Phase A 只取数落
staging + 只读验证, 不写生产库、不动 schema。本文件只测「取」这一半的判据:
limit/resume/单股异常隔离/truncated 不被吞/staging 路径守卫/raw_rows 列集合。
"""
from __future__ import annotations

import pytest

from conftest import duck_mem
from scripts import ingest_holders_raw as hr
from services.data_sources.holders_top10_schema import RAW_FIELDS


class FakeClient:
    """替身 aif10 client — 只实现 get_v1 形状, 从不碰网络."""

    def __init__(self, pages: dict | None = None, errors: dict | None = None):
        self.pages = pages or {}
        self.errors = errors or {}
        self.call_count = 0

    def get_v1(
        self,
        report_name,
        *,
        page=1,
        page_size=500,
        sort_columns="",
        sort_types="",
        columns="ALL",
        secucode=None,
        extra_filters=None,
        filter_expr=None,
        extra_params=None,
    ):
        self.call_count += 1
        if secucode in self.errors:
            raise self.errors[secucode]
        return self.pages.get(secucode, {"pages": 0, "count": 0, "data": []})


def _row(secucode: str, code: str, rank: int, holder_name: str = "某机构") -> dict:
    """真实字段命名形态的供应商行 (mythos 防字段方向反), 只填测试用得到的键."""
    return {
        "SECUCODE": secucode,
        "SECURITY_CODE": code,
        "END_DATE": "2026-06-30 00:00:00",
        "HOLDER_NAME": holder_name,
        "HOLDER_RANK": rank,
        "HOLD_NUM": 1000 + rank,
        "HOLD_RATIO": 1.23,
        "IS_HOLDORG": "1",
        "HOLDER_CODE": None,
        "UPDATE_DATE": "2026-07-10 00:00:00",
    }


def _page(rows: list[dict], *, count: int | None = None, pages: int = 1) -> dict:
    return {"pages": pages, "count": count if count is not None else len(rows), "data": rows}


CODES = ["600001", "600002", "600003"]


def _secu(code: str) -> str:
    return hr._secucode(code)


# ── 断言 1: fetch --limit 3 -> raw_fetch 恰 3 行, raw_rows = 三股行数之和 ──
def test_fetch_writes_one_fetch_row_per_stock_and_sums_row_counts():
    conn = duck_mem()
    pages = {
        _secu(CODES[0]): _page([_row(_secu(CODES[0]), CODES[0], 1), _row(_secu(CODES[0]), CODES[0], 2)]),
        _secu(CODES[1]): _page([_row(_secu(CODES[1]), CODES[1], r) for r in (1, 2, 3)]),
        _secu(CODES[2]): _page([_row(_secu(CODES[2]), CODES[2], 1)]),
    }
    client = FakeClient(pages=pages)

    summary = hr.run_fetch(conn, symbols=CODES, client=client, resume=False)

    assert summary["ok"] == 3
    assert summary["error"] == 0
    n_fetch = conn.execute("SELECT COUNT(*) FROM raw_fetch").fetchone()[0]
    assert n_fetch == 3
    n_rows = conn.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]
    assert n_rows == 2 + 3 + 1


# ── 断言 2: --resume 重跑, 已 ok 的股 0 次网络请求 ──────────────────────
def test_resume_skips_already_ok_stocks_with_zero_network_calls():
    conn = duck_mem()
    pages = {_secu(c): _page([_row(_secu(c), c, 1)]) for c in CODES}
    first_client = FakeClient(pages=pages)
    first = hr.run_fetch(conn, symbols=CODES, client=first_client, resume=False)
    assert first["ok"] == 3
    assert first_client.call_count > 0

    second_client = FakeClient(pages=pages)
    second = hr.run_fetch(conn, symbols=CODES, client=second_client, resume=True)

    assert second_client.call_count == 0
    assert second["resume_skipped"] == 3
    assert second["ok"] == 0


# ── 断言 3: 某股异常 -> 该股 error, 其余仍 ok, 不中断整批 ───────────────
def test_one_stock_error_does_not_abort_the_batch():
    conn = duck_mem()
    pages = {_secu(c): _page([_row(_secu(c), c, 1)]) for c in CODES}
    errors = {_secu("600002"): RuntimeError("boom")}
    client = FakeClient(pages=pages, errors=errors)

    summary = hr.run_fetch(conn, symbols=CODES, client=client, resume=False)

    assert summary["ok"] == 2
    assert summary["error"] == 1
    bad = conn.execute(
        "SELECT status, error, n_rows FROM raw_fetch WHERE stock_code = ?", ["600002"]
    ).fetchone()
    assert bad[0] == "error"
    assert "boom" in bad[1]
    assert bad[2] == 0
    good_codes = [
        r[0]
        for r in conn.execute(
            "SELECT stock_code FROM raw_fetch WHERE status = 'ok' ORDER BY stock_code"
        ).fetchall()
    ]
    assert good_codes == ["600001", "600003"]
    # 出错的股不留 raw_rows 残行
    n_bad_rows = conn.execute(
        "SELECT COUNT(*) FROM raw_rows WHERE fetch_id = ?", ["600002"]
    ).fetchone()[0]
    assert n_bad_rows == 0


# ── 断言 4: truncated=True 不许被吞 ─────────────────────────────────────
def test_truncated_flag_is_surfaced_not_swallowed():
    conn = duck_mem()
    code = "600001"
    secu = _secu(code)
    # count 远大于单页 landed 行数, pages=1 (无后续页可翻) -> PaginationLandResult.truncated=True
    # (aif10_scraper.pagination.assess_pagination_land 的判据: landed+tol < expected)
    pages = {secu: _page([_row(secu, code, 1)], count=1000, pages=1)}
    client = FakeClient(pages=pages)

    summary = hr.run_fetch(conn, symbols=[code], client=client, resume=False)

    assert summary["truncated"] == 1
    row = conn.execute(
        "SELECT truncated FROM raw_fetch WHERE stock_code = ?", [code]
    ).fetchone()
    assert row[0] is True


# ── 断言 5: staging 路径必须在 data/scratch/ 下 ─────────────────────────
def test_staging_path_under_scratch_is_accepted():
    candidate = hr.default_staging_path("unit_test_probe_accept")
    assert hr.validate_staging_path(candidate) == candidate.resolve()


def test_staging_path_pointing_at_production_db_is_refused():
    from services.db_connection import DB_DIR

    production_path = DB_DIR / "smartmoney.duckdb"
    with pytest.raises(hr.StagingPathError):
        hr.validate_staging_path(production_path)


def test_cli_fetch_refuses_production_db_path_and_exits_nonzero(capsys):
    from services.db_connection import DB_DIR

    production_path = DB_DIR / "smartmoney.duckdb"
    rc = hr.main(["fetch", "--db-path", str(production_path), "--symbols", "600001"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "data/scratch" in err or str(DB_DIR / "scratch") in err


# ── 断言 6: raw_rows 列集合 == RAW_FIELDS 名字集合 + (fetch_id, row_ordinal) ──
def test_raw_rows_column_set_matches_raw_fields_plus_carrier_keys():
    conn = duck_mem()
    hr.ensure_staging_tables(conn)

    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'raw_rows'"
        ).fetchall()
    }
    expected = {name for name, _ in RAW_FIELDS} | {"fetch_id", "row_ordinal"}
    assert cols == expected
