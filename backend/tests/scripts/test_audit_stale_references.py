from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_stale_references.py"
SPEC = importlib.util.spec_from_file_location("audit_stale_references", SCRIPT_PATH)
audit_stale_references = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_stale_references
SPEC.loader.exec_module(audit_stale_references)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_grep_classifies_code_test_doc_comment_and_retirement_action(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit_stale_references, "REPO", tmp_path)
    monkeypatch.setattr(
        audit_stale_references,
        "RETIREMENT_METADATA_PATHS",
        ("backend/services/data_deprecation.py",),
    )
    retired_token = "dim_" + "stock"

    files = [
        _write(tmp_path / "backend/app.py", f"SELECT * FROM {retired_token}\n# {retired_token} retired\n"),
        _write(tmp_path / "backend/tests/test_app.py", f"{retired_token}\n"),
        _write(tmp_path / "docs/note.md", f"{retired_token}\n"),
        _write(tmp_path / "backend/services/data_deprecation.py", f"{retired_token}\n"),
    ]

    hits = audit_stale_references.grep(re.compile(rf"\b{retired_token}\b"), files)

    assert [(hit.file, hit.kind) for hit in hits] == [
        ("backend/app.py", "code"),
        ("backend/app.py", "comment"),
        ("backend/tests/test_app.py", "test"),
        ("docs/note.md", "doc"),
        ("backend/services/data_deprecation.py", "retirement_action"),
    ]


def test_tier3_known_retired_scan_deduplicates_and_preserves_severity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit_stale_references, "REPO", tmp_path)
    monkeypatch.setattr(audit_stale_references, "SELF_EXCLUDE_PATHS", ())
    monkeypatch.setattr(
        audit_stale_references,
        "KNOWN_RETIRED",
        [
            {
                "name": "old_table",
                "kind": "db_table",
                "replaced_by": "new_table",
                "search": [r"\bold_table\b", r"old_table"],
            }
        ],
    )
    file_path = _write(tmp_path / "backend/app.py", "SELECT * FROM old_table\n")

    [finding] = audit_stale_references.tier3_known_retired_scan([file_path])

    assert finding.severity == "critical"
    assert len(finding.hits) == 1
    assert finding.hits[0].file == "backend/app.py"


def test_tier5_commented_out_code_skips_allowlisted_and_chinese_comments(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(audit_stale_references, "REPO", tmp_path)
    monkeypatch.setattr(audit_stale_references, "SELF_EXCLUDE_PATHS", ())
    file_path = _write(
        tmp_path / "backend/app.py",
        "\n".join(
            [
                "# old_call()",
                "# TODO remove later",
                "# 这是说明性注释",
                "# entry_target = 100 × (1 + 0.03)",
                "# from yaml: configs/data_governance.yaml schema_contracts.price_kline",
                "# n_total=5, n_30d=3, win_rate=75%",
                "# Insert test stocks",
                "# Neckline = high between two lows",
                "# day_of_month (1-31)",
                "# i=2: a[1]=2<3 True; a[2]=3>3 False -> not cross",
                "# VALUE = 1",
            ]
        ),
    )

    hits = audit_stale_references.tier5_commented_out_code([file_path])

    assert [(hit.line, hit.kind, hit.text) for hit in hits] == [
        (1, "dead_code", "# old_call()"),
        (11, "dead_code", "# VALUE = 1"),
    ]


def test_tier6_dead_branches_detects_false_branches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit_stale_references, "REPO", tmp_path)
    monkeypatch.setattr(audit_stale_references, "SELF_EXCLUDE_PATHS", ())
    file_path = _write(tmp_path / "backend/app.py", "if False:\n    pass\nwhile False:\n    pass\n")

    hits = audit_stale_references.tier6_dead_branches([file_path])

    assert [(hit.line, hit.text) for hit in hits] == [(1, "if False:"), (3, "while False:")]


def test_tier7_retired_files_detects_header_marker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit_stale_references, "REPO", tmp_path)
    monkeypatch.setattr(audit_stale_references, "SELF_EXCLUDE_PATHS", ())
    file_path = _write(tmp_path / "docs/old.md", "# This file is deprecated\nbody\n")

    audit_stale_references.tier5_commented_out_code([file_path])
    hits = audit_stale_references.tier7_retired_files([file_path])

    assert [(hit.file, hit.line, hit.kind) for hit in hits] == [("docs/old.md", 1, "retired_file")]


def test_read_cache_can_be_cleared_for_fresh_snapshots(tmp_path: Path) -> None:
    file_path = _write(tmp_path / "backend/app.py", "old\n")

    assert audit_stale_references._read_lines(file_path) == ("old",)
    file_path.write_text("new\n", encoding="utf-8")
    assert audit_stale_references._read_lines(file_path) == ("old",)

    audit_stale_references._clear_read_cache()

    assert audit_stale_references._read_lines(file_path) == ("new",)


def test_phase0_line_helper_classifies_legacy_sql_runtime_hit() -> None:
    hits = audit_stale_references._phase0_hits_for_line(
        "chunkymonkey",
        "chunkymonkey/backend/app.py",
        "runtime",
        7,
        "import sqlite3",
    )

    assert any(hit.category == "sqlite_runtime" and hit.marker == "sqlite3" for hit in hits)


def test_full_report_persists_all_stale_audit_tiers() -> None:
    hit = audit_stale_references.Hit(
        file="backend/app.py",
        line=3,
        text="# old_call()",
        kind="dead_code",
    )
    dead_branch = audit_stale_references.Hit(
        file="backend/app.py",
        line=7,
        text="if False:",
        kind="dead_branch",
    )
    retired_file = audit_stale_references.Hit(
        file="docs/old.md",
        line=1,
        text="This file is deprecated",
        kind="retired_file",
    )

    report = audit_stale_references._build_full_report(
        scanned_files=1,
        phase0={"summary": {}},
        tier1={},
        tier3=[],
        tier4={},
        tier5=[hit],
        tier6=[dead_branch],
        tier7=[retired_file],
    )

    assert report["summary"]["tier5_commented_out_code_hits"] == 1
    assert report["summary"]["tier6_dead_branch_hits"] == 1
    assert report["summary"]["tier7_retired_file_hits"] == 1
    assert report["tier5_commented_out_code"][0]["kind"] == "dead_code"
    assert report["tier6_dead_branches"][0]["text"] == "if False:"
    assert report["tier7_retired_files"][0]["file"] == "docs/old.md"
