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
