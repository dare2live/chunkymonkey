from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_tooling_gate.py"
SPEC = importlib.util.spec_from_file_location("audit_tooling_gate", SCRIPT_PATH)
audit_tooling_gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_tooling_gate
SPEC.loader.exec_module(audit_tooling_gate)


def test_parse_codegraph_status_outputs_pending_json() -> None:
    text = """
\x1b[1mCodeGraph Status\x1b[0m
\x1b[36mProject:\x1b[0m /repo

\x1b[1mIndex Statistics:\x1b[0m
  Files:     1,041
  Nodes:     16,666
  Edges:     300,838
  DB Size:   171.47 MB

\x1b[1mNodes by Kind:\x1b[0m
  function        6,897
  class           449

\x1b[1mFiles by Language:\x1b[0m
  python          995
  javascript      22

\x1b[1mPending Changes:\x1b[0m
  Added:     44 files
  Modified:  2 files
"""

    status = audit_tooling_gate.parse_codegraph_status(text)

    assert status["project"] == "/repo"
    assert status["index"]["files"] == 1041
    assert status["index"]["db_size"] == "171.47 MB"
    assert status["nodes_by_kind"] == {"function": 6897, "class": 449}
    assert status["files_by_language"]["python"] == 995
    assert status["pending"]["added"] == 44
    assert status["pending"]["modified"] == 2
    assert status["pending"]["total"] == 46
    assert status["pending"]["sync_required"] is True


def test_parse_codegraph_status_does_not_count_clean_marker_as_pending() -> None:
    status = audit_tooling_gate.parse_codegraph_status("Project: /repo\nPending Changes:\n  No pending changes\n")

    assert status["pending"]["total"] == 0
    assert status["pending"]["sync_required"] is False


def test_parse_git_status_short_groups_dirty_worktree() -> None:
    status = audit_tooling_gate.parse_git_status_short(
        "\n".join(
            [
                " M backend/app.py",
                "A  backend/new.py",
                " D docs/old.md",
                "?? docs/new.md",
                "R  docs/a.md -> docs/b.md",
            ]
        )
    )

    assert status["clean"] is False
    assert status["total"] == 5
    assert status["counts"] == {
        "staged": 2,
        "unstaged": 2,
        "untracked": 1,
        "modified": 1,
        "deleted": 1,
        "added": 1,
        "renamed": 1,
    }
    assert status["entries"][0]["path"] == "backend/app.py"


def test_parse_complexity_markdown_and_diff_baseline() -> None:
    baseline_text = """
# Complexity Hotspots

## HIGH nested-loop
- Location: `scripts/old.py:10`
- Finding: Nested loop may create O(n^2) or worse behavior.
- Suggestion: batch it

## MEDIUM membership-in-loop
- Location: `scripts/stable.py:20`
- Finding: Repeated list membership checks may be slow.
- Suggestion: use a set
"""
    current_text = """
# Complexity Hotspots

## HIGH nested-loop
- Location: `scripts/new.py:12`
- Finding: Nested loop may create O(n^2) or worse behavior.
- Suggestion: batch it

## MEDIUM membership-in-loop
- Location: `scripts/stable.py:20`
- Finding: Repeated list membership checks may be slow.
- Suggestion: use a set
"""

    baseline = audit_tooling_gate.parse_complexity_markdown(baseline_text)
    current = audit_tooling_gate.parse_complexity_markdown(current_text)
    diff = audit_tooling_gate.diff_complexity_findings(current, baseline)

    assert diff["baseline_count"] == 2
    assert diff["current_count"] == 2
    assert diff["new_count"] == 1
    assert diff["resolved_count"] == 1
    assert diff["unchanged_count"] == 1
    assert diff["new_high_count"] == 1
    assert diff["new_findings"][0]["path"] == "scripts/new.py"
    assert diff["resolved_findings"][0]["path"] == "scripts/old.py"


def test_complexity_diff_without_loaded_baseline_does_not_claim_new_high() -> None:
    current = audit_tooling_gate.parse_complexity_markdown(
        """
# Complexity Hotspots

## HIGH nested-loop
- Location: `scripts/current.py:12`
- Finding: Nested loop may create O(n^2) or worse behavior.
- Suggestion: batch it
"""
    )

    diff = audit_tooling_gate.complexity_diff_report(
        current,
        [],
        baseline_status="not_configured",
    )

    assert diff["status"] == "baseline_unavailable"
    assert diff["new_high_count"] == 0
    assert diff["new_findings"] == []
    assert diff["unclassified_high_count"] == 1


def test_build_report_warns_when_baseline_missing_and_fails_on_pending(tmp_path: Path) -> None:
    report = audit_tooling_gate.build_tooling_gate_report(
        repo=tmp_path,
        git_status_text="",
        codegraph_status_text="""
Project: /repo
Pending Changes:
  Added: 1 file
""",
        complexity_markdown="""
## HIGH nested-loop
- Location: `scripts/new.py:12`
- Finding: Nested loop may create O(n^2) or worse behavior.
- Suggestion: batch it
""",
        baseline_path=tmp_path / "missing-baseline.json",
    )

    assert report["verdict"] == "FAIL"
    assert report["codegraph"]["pending"]["sync_required"] is True
    assert report["complexity"]["baseline"]["status"] == "missing"
    assert report["complexity"]["diff"]["status"] == "baseline_unavailable"
    assert report["complexity"]["diff"]["new_high_count"] == 0
    assert report["complexity"]["diff"]["unclassified_high_count"] == 1


def test_build_report_uses_loaded_baseline_for_new_high_verdict(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "severity": "MEDIUM",
                        "kind": "membership-in-loop",
                        "path": "scripts/stable.py",
                        "line": 20,
                        "finding": "Repeated list membership checks may be slow.",
                        "suggestion": "use a set",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = audit_tooling_gate.build_tooling_gate_report(
        repo=tmp_path,
        git_status_text=" M backend/app.py\n",
        codegraph_status_text="Project: /repo\nPending Changes:\n  No pending changes\n",
        complexity_markdown="""
## MEDIUM membership-in-loop
- Location: `scripts/stable.py:20`
- Finding: Repeated list membership checks may be slow.
- Suggestion: use a set

## HIGH nested-loop
- Location: `scripts/new.py:12`
- Finding: Nested loop may create O(n^2) or worse behavior.
- Suggestion: batch it
""",
        baseline_path=baseline,
    )

    assert report["verdict"] == "FAIL"
    assert report["git_status"]["total"] == 1
    assert report["complexity"]["baseline"]["status"] == "loaded"
    assert report["complexity"]["diff"]["status"] == "compared"
    assert report["complexity"]["diff"]["new_high_count"] == 1
    assert report["complexity"]["severity_counts"] == {"HIGH": 1, "MEDIUM": 1}
