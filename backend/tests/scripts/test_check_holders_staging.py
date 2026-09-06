"""check_holders_staging 单测 (ingest_holders_raw Phase A, 2026-09).

覆盖任务卡钉死的断言:
  - 生产库连接必须 read_only=True — 传可写连接必须拒绝 (机制性: 查
    ``duckdb_databases().readonly``, 不是"调用方保证过了"式的口头约定)。
  - 六项检查各自是独立函数, 每项都有一个"该 FAIL 时确实 FAIL"的靶
    (第 5/6 项没有"必须=0"式判据, 分别验证它的例外判据 [负 gap] 和
    数值正确性)。
  - staging schema 缺表/缺列 → 明确 RuntimeError, 不是裸 DuckDB 报错。
  - ATTACH 静默失败 (duck_adapter 出错只 warning 不 raise) 时,
    ``build_connection`` 必须自己兜底拒绝, 不能悄悄漏掉一整个数据库。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from scripts import check_holders_staging as vhs  # noqa: E402
from scripts import ingest_holders_raw as ihr  # noqa: E402

RAW_FIELD_NAMES = [name for name, _ in vhs.RAW_FIELDS]
_RAW_ROW_COLS = RAW_FIELD_NAMES + ["fetch_id", "row_ordinal"]


# ── fixture builders ─────────────────────────────────────────────────────────


def _new_staging(path: Path) -> duckdb.DuckDBPyConnection:
    """staging 表由**生产者自己的 DDL** 建, 不手搓。

    2026-09-06: 手搓过一版 (page_count/row_count/fetched_at/request_json), 生产者
    实际写的是 n_rows/requested_at/elapsed_s —— fixture 和被测对象各说各话, 22 个
    用例一起红。调 ``ingest_holders_raw.ensure_staging_tables`` 后, 两边共用同一份
    DDL, 这类漂移在结构上不可能再发生。
    """

    conn = duckdb.connect(str(path))
    ihr.ensure_staging_tables(conn)
    return conn


def _new_prod(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
          stock_code VARCHAR, report_date VARCHAR, holder_set VARCHAR, holder_rank INTEGER,
          row_seq INTEGER, holder_name VARCHAR, hold_ratio_float DOUBLE, notice_date VARCHAR,
          is_exit_row BOOLEAN, holder_name_norm VARCHAR, share_class VARCHAR, shares_approx BIGINT,
          change_status VARCHAR, hold_change_num DOUBLE, holder_type VARCHAR
        )
        """
    )
    return conn


def _new_feat(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE mart_inst_profile (holder VARCHAR, holder_type VARCHAR)")
    return conn


def _insert_fetch(conn, **kw) -> None:
    defaults = dict(
        fetch_id="f1",
        stock_code="600519",
        requested_at="2026-09-06T00:00:00Z",
        status="ok",
        n_rows=1,
        truncated=False,
        error=None,
        elapsed_s=0.5,
    )
    defaults.update(kw)
    cols = list(defaults)
    conn.execute(
        f"INSERT INTO raw_fetch ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        [defaults[c] for c in cols],
    )


def _insert_raw_row(conn, **kw) -> None:
    """One raw_rows row. Sensible defaults for an ordinary in-holdorg institution row."""

    defaults = {name: None for name in RAW_FIELD_NAMES}
    defaults.update(
        {
            "SECURITY_CODE": "600519",
            "END_DATE": "2026-03-31 00:00:00",
            "HOLDER_NAME": "Holder A",
            "HOLDER_CODE": "CODEA",
            "IS_HOLDORG": "1",
            "IS_REPORT": "1",
            "UPDATE_DATE": "2026-04-15 00:00:00",
            "NOTICE_DATE": "2026-04-15 00:00:00",
            "HOLDER_RANK": 1,
            "HOLD_NUM": 1000,
            "HOLD_RATIO": 5.0,
            "SHARES_TYPE": "A股",
            "SECURITY_NAME_ABBR": "TEST",
        }
    )
    defaults.update(kw)
    defaults["fetch_id"] = kw.get("fetch_id", "f1")
    defaults["row_ordinal"] = kw.get("row_ordinal", 1)
    conn.execute(
        f"INSERT INTO raw_rows ({', '.join(_RAW_ROW_COLS)}) VALUES ({', '.join(['?'] * len(_RAW_ROW_COLS))})",
        [defaults[c] for c in _RAW_ROW_COLS],
    )


def _insert_canonical_row(conn, **kw) -> None:
    defaults = dict(
        stock_code="600519",
        report_date="20260331",
        holder_set="free",
        holder_rank=1,
        row_seq=1,
        holder_name="Holder A",
        hold_ratio_float=5.0,
        notice_date="20260415",
        is_exit_row=False,
        holder_name_norm="Holder A",
        share_class="A",
        shares_approx=1000,
        change_status="新进",
        hold_change_num=1000.0,
        holder_type=None,
    )
    defaults.update(kw)
    cols = list(defaults)
    conn.execute(
        f"INSERT INTO canonical_top10_float_holders_period ({', '.join(cols)}) "
        f"VALUES ({', '.join(['?'] * len(cols))})",
        [defaults[c] for c in cols],
    )


def _golden(tmp_path: Path) -> tuple[Path, Path, Path]:
    """一只股两期, 无异常: 全部六项应 PASS/INFO(非负 gap), 无 FAIL。"""

    staging = tmp_path / "staging.duckdb"
    prod = tmp_path / "prod.duckdb"
    feat = tmp_path / "feat.duckdb"

    sc = _new_staging(staging)
    _insert_fetch(sc)
    _insert_raw_row(sc, row_ordinal=1, END_DATE="2026-03-31 00:00:00", HOLDER_NAME="Holder A")
    _insert_raw_row(sc, row_ordinal=2, END_DATE="2026-06-30 00:00:00", HOLDER_NAME="Holder A",
                     UPDATE_DATE="2026-07-15 00:00:00", NOTICE_DATE="2026-07-15 00:00:00")
    sc.close()

    pc = _new_prod(prod)
    _insert_canonical_row(pc, report_date="20260331")
    _insert_canonical_row(pc, report_date="20260630", notice_date="20260715")
    pc.close()

    fc = _new_feat(feat)
    fc.execute("INSERT INTO mart_inst_profile VALUES ('Holder A', NULL)")
    fc.close()
    return staging, prod, feat


# ── read-only guard ──────────────────────────────────────────────────────────


def test_require_all_read_only_rejects_writable_attachment(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = duckdb.connect(str(staging), read_only=True)
    conn.execute(f"ATTACH '{prod}' AS prod (READ_WRITE)")
    conn.execute(f"ATTACH '{feat}' AS feat (READ_ONLY)")
    with pytest.raises(PermissionError, match="writable"):
        vhs.require_all_read_only(conn)
    conn.close()


def test_require_all_read_only_accepts_all_readonly_connection(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    status = vhs.require_all_read_only(conn)
    assert status["prod"] is True
    assert status["feat"] is True
    conn.close()


def test_build_connection_raises_if_attach_silently_fails(tmp_path):
    """duck_adapter 对 ATTACH 失败只 warning 不 raise; build_connection 必须自己兜底."""

    staging, prod, _feat = _golden(tmp_path)
    missing_feat = tmp_path / "does_not_exist.duckdb"
    with pytest.raises(RuntimeError, match="feat"):
        vhs.build_connection(staging, prod, missing_feat)


def test_require_staging_schema_missing_table_raises(tmp_path):
    staging = tmp_path / "empty_staging.duckdb"
    duckdb.connect(str(staging)).close()
    _prod = _new_prod(tmp_path / "prod.duckdb")
    _prod.close()
    _feat = _new_feat(tmp_path / "feat.duckdb")
    _feat.close()
    with pytest.raises(RuntimeError, match="raw_fetch"):
        vhs.build_connection(staging, tmp_path / "prod.duckdb", tmp_path / "feat.duckdb")


def test_require_staging_schema_missing_raw_fetch_column_raises(tmp_path):
    """raw_fetch 少一列就必须报出**那一列的名字**, 不是笼统"schema 不对"。"""

    staging = tmp_path / "bad_fetch_staging.duckdb"
    conn = duckdb.connect(str(staging))
    ihr.ensure_staging_tables(conn)
    conn.execute("ALTER TABLE raw_fetch DROP COLUMN n_rows")
    conn.close()
    _new_prod(tmp_path / "prod.duckdb").close()
    _new_feat(tmp_path / "feat.duckdb").close()
    with pytest.raises(RuntimeError, match="n_rows"):
        vhs.build_connection(staging, tmp_path / "prod.duckdb", tmp_path / "feat.duckdb")


def test_require_staging_schema_missing_raw_rows_column_raises(tmp_path):
    staging = tmp_path / "bad_staging.duckdb"
    conn = duckdb.connect(str(staging))
    ihr.ensure_staging_tables(conn)
    # raw_fetch 保持合法, 只把 raw_rows 打残 —— 否则 raw_fetch 先报错, 这个用例
    # 就再也测不到它想测的那条路径 (2026-09-06 实际发生过)。
    conn.execute("DROP TABLE raw_rows")
    conn.execute("CREATE TABLE raw_rows (fetch_id VARCHAR, row_ordinal INTEGER, SECURITY_CODE VARCHAR)")
    conn.close()
    _new_prod(tmp_path / "prod.duckdb").close()
    _new_feat(tmp_path / "feat.duckdb").close()
    with pytest.raises(RuntimeError, match="raw_rows"):
        vhs.build_connection(staging, tmp_path / "prod.duckdb", tmp_path / "feat.duckdb")


# ── 1. fetch completeness ────────────────────────────────────────────────────


def test_fetch_completeness_pass_on_clean_fixture(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_fetch_completeness(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["truncated_count"] == 0
    assert r["observed"]["ok_count"] == 1
    conn.close()


def test_fetch_completeness_fails_on_truncated(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_fetch(sc, stock_code="000001", fetch_id="f2", truncated=True, n_rows=5)
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_fetch_completeness(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["truncated_count"] == 1
    assert r["observed"]["truncated_stocks"][0]["stock_code"] == "000001"
    conn.close()


def test_fetch_completeness_reports_errors_without_failing(tmp_path):
    """error 逐股清单是'报数', 不是判据 — truncated=0 时整体仍 PASS."""

    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_fetch(sc, stock_code="000002", fetch_id="f2", status="error", error="timeout")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_fetch_completeness(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["error_count"] == 1
    assert r["observed"]["error_stocks"] == [{"stock_code": "000002", "fetch_id": "f2", "error": "timeout"}]
    conn.close()


# ── 2. derivation rules ──────────────────────────────────────────────────────


def test_derivation_rules_pass_on_clean_fixture(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_derivation_rules(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["total_violations"] == 0
    conn.close()


def test_derivation_rules_fails_on_is_report_season_end_mismatch(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=9, END_DATE="2026-05-15 00:00:00", IS_REPORT="1")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_derivation_rules(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["rules"]["is_report_matches_season_end"]["mismatch"] == 1
    conn.close()


def test_derivation_rules_fails_on_is_holdorg_mismatch(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=9, HOLDER_CODE=None, IS_HOLDORG="1")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_derivation_rules(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["rules"]["is_holdorg_matches_holder_code_present"]["violations"] == 1
    conn.close()


def test_derivation_rules_fails_on_multiple_holder_code_old(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=9, HOLDER_CODE="CODEA", HOLDER_CODE_OLD="OLD1", NOTICE_DATE="2026-08-01 00:00:00", UPDATE_DATE="2026-08-01 00:00:00", END_DATE="2026-06-30 00:00:00")
    _insert_raw_row(sc, row_ordinal=10, HOLDER_CODE="CODEA", HOLDER_CODE_OLD="OLD2", NOTICE_DATE="2026-08-01 00:00:00", UPDATE_DATE="2026-08-01 00:00:00", END_DATE="2026-06-30 00:00:00")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_derivation_rules(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["rules"]["holder_code_old_at_most_one_per_holder_code"]["violations"] == 1
    conn.close()


def test_derivation_rules_fails_on_notice_after_update(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=9, NOTICE_DATE="2026-05-01 00:00:00", UPDATE_DATE="2026-04-01 00:00:00")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_derivation_rules(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["rules"]["notice_date_not_after_update_date"]["violation"] == 1
    conn.close()


# ── 3. raw covers canonical ──────────────────────────────────────────────────


def test_raw_covers_canonical_pass_on_clean_fixture(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_raw_covers_canonical(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["missing_count"] == 0
    conn.close()


def test_raw_covers_canonical_fails_on_missing_raw_row(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    pc = duckdb.connect(str(prod))
    _insert_canonical_row(pc, holder_name="GHOST", holder_rank=2)
    pc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_raw_covers_canonical(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["missing_count"] == 1
    assert r["observed"]["missing_stocks_sample"] == ["600519"]
    conn.close()


def test_raw_covers_canonical_dedupes_known_canonical_duplicate_copies(tmp_path):
    """canonical 里逐字重复的副本不该造成假 FAIL — DISTINCT 内容键天然吸收它."""

    staging, prod, feat = _golden(tmp_path)
    pc = duckdb.connect(str(prod))
    # 再插入一条与 20260331/Holder A 内容键完全相同的"重复副本" (只差 row_seq/元数据)。
    _insert_canonical_row(pc, report_date="20260331", row_seq=2)
    pc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_raw_covers_canonical(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["missing_count"] == 0
    conn.close()


# ── 4. raw internal duplicates ───────────────────────────────────────────────


def test_raw_internal_duplicates_pass_on_clean_fixture(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_raw_internal_duplicates(conn)
    assert r["status"] == "PASS"
    assert r["observed"]["duplicate_groups"] == 0
    conn.close()


def test_raw_internal_duplicates_fails_on_verbatim_duplicate(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    # 逐字复制第一行 (只改 row_ordinal, 模拟翻页把同一行取了两遍)。
    _insert_raw_row(sc, row_ordinal=99, END_DATE="2026-03-31 00:00:00", HOLDER_NAME="Holder A")
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_raw_internal_duplicates(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["duplicate_groups"] == 1
    assert r["observed"]["extra_rows"] == 1
    conn.close()


# ── 5. exit gap ───────────────────────────────────────────────────────────────


def test_exit_gap_positive_gap_is_info_not_fail(tmp_path):
    """已知生产库退出行系统性偏少 → 正 gap 是预期证据缺口, 不判 FAIL."""

    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    # Holder B 只在第一期出现, 第二期消失 → raw 能派生出一条退出行; canonical 没有它。
    _insert_raw_row(sc, row_ordinal=50, END_DATE="2026-03-31 00:00:00", HOLDER_NAME="Holder B",
                     HOLDER_CODE=None, IS_HOLDORG="0", HOLDER_RANK=2)
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_exit_gap(conn)
    assert r["status"] == "INFO"
    assert r["observed"]["negative_gap_periods"] == []
    by_period = {row["report_date"]: row for row in r["observed"]["by_report_date"]}
    assert by_period["20260630"]["gap"] == 1
    conn.close()


def test_exit_gap_negative_gap_fails(tmp_path):
    """canonical 声称的退出行数超过 raw 能派生出的数量 = 矛盾, 判 FAIL."""

    staging, prod, feat = _golden(tmp_path)
    pc = duckdb.connect(str(prod))
    _insert_canonical_row(
        pc, report_date="20260630", holder_name="PHANTOM", holder_rank=9,
        is_exit_row=True, notice_date="20260715",
    )
    pc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_exit_gap(conn)
    assert r["status"] == "FAIL"
    assert r["observed"]["negative_gap_periods"] == ["20260630"]
    conn.close()


# ── 6. identity experiment ────────────────────────────────────────────────────


def test_identity_experiment_counts_collapsing_variants(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    # 同一 HOLDER_CODE 下出现两个不同名字变体 (模拟机构改名/曾用名)。
    _insert_raw_row(sc, row_ordinal=60, END_DATE="2026-06-30 00:00:00", HOLDER_NAME="Holder A New Name",
                     HOLDER_CODE="CODEA", UPDATE_DATE="2026-07-15 00:00:00", NOTICE_DATE="2026-07-15 00:00:00")
    sc.close()
    pc = duckdb.connect(str(prod))
    _insert_canonical_row(pc, report_date="20260630", holder_name="Holder A New Name", notice_date="20260715")
    pc.close()
    fc = duckdb.connect(str(feat))
    fc.execute("INSERT INTO mart_inst_profile VALUES ('Holder A New Name', NULL)")
    fc.close()

    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_identity_experiment(conn)
    assert r["status"] == "INFO"
    assert r["observed"]["ambiguous_holder_code_groups"] == 1
    # canonical 里 'Holder A' + 'Holder A New Name' 两个变体都在受影响集合里。
    assert r["observed"]["canonical_holder_name_variants_collapsing"] == 2
    # mart_inst_profile 原有 'Holder A' + 新增 'Holder A New Name' 都受影响。
    assert r["observed"]["mart_inst_profile_holders_affected"] == 2
    conn.close()


def test_identity_experiment_morgan_stanley_same_code(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=70, HOLDER_NAME="摩根士丹利中国", HOLDER_CODE="MS01", HOLDER_RANK=3)
    _insert_raw_row(sc, row_ordinal=71, HOLDER_NAME="Morgan Stanley & Co", HOLDER_CODE="MS01", HOLDER_RANK=4)
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_identity_experiment(conn)
    assert r["status"] == "INFO"
    assert r["observed"]["morgan_stanley_same_code"] is True
    names = {v["holder_name"] for v in r["observed"]["morgan_stanley_variants"]}
    assert {"摩根士丹利中国", "Morgan Stanley & Co"} <= names
    conn.close()


def test_identity_experiment_morgan_stanley_different_codes(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    sc = duckdb.connect(str(staging))
    _insert_raw_row(sc, row_ordinal=70, HOLDER_NAME="摩根士丹利中国", HOLDER_CODE="MS01", HOLDER_RANK=3)
    _insert_raw_row(sc, row_ordinal=71, HOLDER_NAME="摩根士丹利华鑫", HOLDER_CODE="MS02", HOLDER_RANK=4)
    sc.close()
    conn = vhs.build_connection(staging, prod, feat)
    r = vhs.check_identity_experiment(conn)
    assert r["observed"]["morgan_stanley_same_code"] is False
    conn.close()


# ── orchestration ─────────────────────────────────────────────────────────────


def test_run_all_returns_six_named_checks_and_overall_pass(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = vhs.build_connection(staging, prod, feat)
    results = vhs.run_all(conn)
    assert [r["name"] for r in results] == [
        "fetch_completeness",
        "derivation_rules_zero_exception",
        "raw_covers_canonical_content_keys",
        "raw_internal_duplicates",
        "exit_hole_by_report_date",
        "identity_experiment",
    ]
    for r in results:
        assert "observed" in r and "expected" in r
    assert vhs.overall_status(results) == "PASS"
    conn.close()


def test_run_all_refuses_writable_prod_connection(tmp_path):
    staging, prod, feat = _golden(tmp_path)
    conn = duckdb.connect(str(staging), read_only=True)
    conn.execute(f"ATTACH '{prod}' AS prod (READ_WRITE)")
    conn.execute(f"ATTACH '{feat}' AS feat (READ_ONLY)")
    with pytest.raises(PermissionError):
        vhs.run_all(conn)
    conn.close()


def test_main_json_exit_code_reflects_overall_status(tmp_path, capsys):
    staging, prod, feat = _golden(tmp_path)
    rc = vhs.main([
        "--staging", str(staging),
        "--prod-db", str(prod),
        "--feature-store-db", str(feat),
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    assert payload["status"] == "PASS"
    assert len(payload["checks"]) == 6
    for check in payload["checks"]:
        assert "observed" in check and "expected" in check

    # break it: add a truncated fetch → overall FAIL → exit 1
    sc = duckdb.connect(str(staging))
    _insert_fetch(sc, stock_code="000003", fetch_id="f3", truncated=True)
    sc.close()
    rc2 = vhs.main([
        "--staging", str(staging),
        "--prod-db", str(prod),
        "--feature-store-db", str(feat),
        "--json",
    ])
    assert rc2 == 1
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["status"] == "FAIL"


# ── 生产者/验证器之间的列契约 ─────────────────────────────────────────────────


def test_verifier_required_columns_are_a_subset_of_producer_ddl(tmp_path):
    """验证器要的每一列, 生产者都必须真的写。

    这两个脚本是一对接口的两端 (ingest_holders_raw 写 -> check_holders_staging 读),
    此前只靠"我在两边都写对了"维持, 结果 n_rows/page_count 各说各话。这里用机器比,
    任一端改列名而另一端没跟, 当场红。
    """

    conn = duckdb.connect(str(tmp_path / "contract.duckdb"))
    ihr.ensure_staging_tables(conn)
    fetch_cols = {r[1] for r in conn.execute("PRAGMA table_info('raw_fetch')").fetchall()}
    rows_cols = {r[1] for r in conn.execute("PRAGMA table_info('raw_rows')").fetchall()}
    conn.close()

    assert vhs.REQUIRED_RAW_FETCH_COLUMNS <= fetch_cols, (
        "验证器要的 raw_fetch 列生产者没写: "
        f"{sorted(vhs.REQUIRED_RAW_FETCH_COLUMNS - fetch_cols)}"
    )
    assert vhs.REQUIRED_RAW_ROWS_COLUMNS <= rows_cols, (
        "验证器要的 raw_rows 列生产者没写: "
        f"{sorted(vhs.REQUIRED_RAW_ROWS_COLUMNS - rows_cols)}"
    )
