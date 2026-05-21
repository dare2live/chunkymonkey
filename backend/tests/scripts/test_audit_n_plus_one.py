from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_n_plus_one.py"
SPEC = importlib.util.spec_from_file_location("audit_n_plus_one", SCRIPT_PATH)
audit_n_plus_one = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit_n_plus_one
SPEC.loader.exec_module(audit_n_plus_one)


def _scan(tmp_path: Path, code: str):
    path = tmp_path / "sample.py"
    path.write_text(dedent(code), encoding="utf-8")
    return audit_n_plus_one.scan_files([path], repo_root=tmp_path)


def test_pattern_1_detects_sql_execute_in_for_loop(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(conn, rows):
            for row in rows:
                conn.execute("INSERT INTO t VALUES (?)", [row])
        """,
    )

    assert any(f.pattern == "SQL_EXECUTE_IN_FOR_LOOP" and f.severity == "HIGH" for f in findings)


def test_pattern_2_detects_read_sql_in_for_loop(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(ids):
            for stock_id in ids:
                frame = pd.read_sql("SELECT * FROM t WHERE id = ?", conn, params=[stock_id])
                yield frame
        """,
    )

    assert any(f.pattern == "PER_ROW_IO_IN_FOR_LOOP" and f.severity == "MEDIUM" for f in findings)


def test_pattern_3_detects_duckdb_connect_in_for_loop(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(paths):
            for path in paths:
                conn = duckdb.connect(path)
                yield conn
        """,
    )

    assert any(f.pattern == "DB_CONNECT_IN_FOR_LOOP" and f.severity == "HIGH" for f in findings)


def test_pattern_4_detects_iterrows_with_sql(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(df, conn):
            for _, row in df.iterrows():
                conn.execute("INSERT INTO t VALUES (?)", [row["x"]])
        """,
    )

    assert any(f.pattern == "ITERROWS_WITH_IO" and f.severity == "MEDIUM" for f in findings)


def test_clean_file_produces_zero_findings(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def transform(rows):
            values = []
            for row in rows:
                values.append(row * 2)
            return values
        """,
    )

    assert findings == []


def test_iter_python_files_excludes_tests_by_default(tmp_path: Path) -> None:
    source = tmp_path / "backend" / "services" / "sample.py"
    test = tmp_path / "backend" / "tests" / "test_sample.py"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("def source():\n    pass\n", encoding="utf-8")
    test.write_text("def test_sample():\n    pass\n", encoding="utf-8")

    files = audit_n_plus_one.iter_python_files([tmp_path / "backend"])

    assert files == [source]


def test_iter_python_files_can_include_tests(tmp_path: Path) -> None:
    source = tmp_path / "backend" / "services" / "sample.py"
    test = tmp_path / "backend" / "tests" / "test_sample.py"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("def source():\n    pass\n", encoding="utf-8")
    test.write_text("def test_sample():\n    pass\n", encoding="utf-8")

    files = audit_n_plus_one.iter_python_files([tmp_path / "backend"], include_tests=True)

    assert files == [source, test]


def test_chunked_executemany_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(conn, rows):
            for i in range(0, len(rows), 500):
                conn.executemany("INSERT INTO t VALUES (?)", rows[i:i + 500])
        """,
    )

    assert findings == []


def test_chunked_executemany_loop_with_named_batch_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def load(conn, rows):
            BATCH = 500
            for i in range(0, len(rows), BATCH):
                conn.executemany("INSERT INTO t VALUES (?)", rows[i:i + BATCH])
        """,
    )

    assert findings == []


def test_chunked_in_delete_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def cleanup(conn, extra_codes):
            for i in range(0, len(extra_codes), 200):
                batch = extra_codes[i:i + 200]
                placeholders = ",".join("?" * len(batch))
                conn.execute(f"DELETE FROM t WHERE stock_code IN ({placeholders})", batch)
        """,
    )

    assert findings == []


def test_ddl_split_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def ensure_schema(conn, DDL):
            for stmt in DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
        """,
    )

    assert findings == []


def test_sql_script_split_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def execute_script(conn, sql):
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
        """,
    )

    assert findings == []


def test_ddl_strip_split_alias_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        def ensure_schema(conn, DDL):
            for stmt in DDL.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(s)
        """,
    )

    assert findings == []


def test_schema_migration_loop_is_not_n_plus_one(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """
        REGISTRY_SCHEMA_MIGRATIONS = ["ALTER TABLE t ADD COLUMN a TEXT"]

        def ensure_schema(conn):
            for statement in REGISTRY_SCHEMA_MIGRATIONS:
                conn.execute(statement)
        """,
    )

    assert findings == []


def test_p4_baseline_matches_current_verified_audit_floor() -> None:
    assert audit_n_plus_one.P4_BASELINE_FINDINGS == 19
