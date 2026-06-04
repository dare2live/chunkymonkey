from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_rule_compliance.py"
SPEC = importlib.util.spec_from_file_location("check_rule_compliance", SCRIPT_PATH)
check_rule_compliance = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = check_rule_compliance
SPEC.loader.exec_module(check_rule_compliance)


def _set_staged_diff(monkeypatch, staged):
    monkeypatch.setattr(check_rule_compliance, "get_staged_diff", lambda: staged)


def test_new_raw_duckdb_connect_outside_policy_is_blocked(monkeypatch, capsys) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [(12, "conn = duckdb.connect(str(db_path), read_only=True)")],
            )
        ],
    )

    assert check_rule_compliance.main() == 1
    assert "DB boundary raw duckdb.connect" in capsys.readouterr().err


def test_added_raw_duckdb_connect_in_legacy_policy_path_is_blocked(monkeypatch, capsys) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/build_stage_opt_pit.py",
                [(98, "conn = duckdb.connect(str(SMART_DB))")],
            )
        ],
    )

    assert check_rule_compliance.main() == 1
    assert "DB boundary raw duckdb.connect" in capsys.readouterr().err


def test_raw_duckdb_connect_evidence_comment_allows_explicit_exception(monkeypatch) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [
                    (
                        12,
                        "conn = duckdb.connect(str(db_path), read_only=True)  # rule-compliance: ok evidence=legacy migration guard",
                    )
                ],
            )
        ],
    )

    assert check_rule_compliance.main() == 0


def test_duckdb_alias_connect_is_blocked(monkeypatch, tmp_path: Path, capsys) -> None:
    file_path = tmp_path / "backend/scripts/new_pipeline.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "import duckdb as ddb\n\nconn = ddb.connect(':memory:')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_rule_compliance, "REPO_ROOT", tmp_path)
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [(3, "conn = ddb.connect(':memory:')")],
            )
        ],
    )

    assert check_rule_compliance.main() == 1
    assert "DB boundary raw duckdb.connect" in capsys.readouterr().err


def test_new_hardcoded_duckdb_path_literal_is_blocked(monkeypatch, capsys) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [(7, 'SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"')],
            )
        ],
    )

    assert check_rule_compliance.main() == 1
    assert "DB boundary hardcoded duckdb path" in capsys.readouterr().err


def test_new_unmanifested_duckdb_filename_literal_is_blocked(monkeypatch, capsys) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [(8, 'TMP_DB = DATA_DIR / "scratch.duckdb"')],
            )
        ],
    )

    assert check_rule_compliance.main() == 1
    assert "DB boundary hardcoded duckdb path" in capsys.readouterr().err


def test_db_boundary_evidence_comment_allows_explicit_exception(monkeypatch) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/scripts/new_pipeline.py",
                [
                    (
                        7,
                        'SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"  # rule-compliance: ok evidence=legacy migration guard',
                    )
                ],
            )
        ],
    )

    assert check_rule_compliance.main() == 0


def test_tests_are_exempt_from_db_boundary_rules(monkeypatch) -> None:
    _set_staged_diff(
        monkeypatch,
        [
            (
                "backend/tests/test_fixture.py",
                [
                    (6, 'fixture_db = tmp_path / "data" / "smartmoney.duckdb"'),
                    (7, "conn = duckdb.connect(str(fixture_db))"),
                ],
            )
        ],
    )

    assert check_rule_compliance.main() == 0
