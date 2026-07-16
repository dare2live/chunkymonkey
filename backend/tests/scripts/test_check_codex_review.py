from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import check_codex_review as gate


def _run(tmp_path: Path, monkeypatch, message: str, staged: list[str]) -> int:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(message, encoding="utf-8")
    monkeypatch.setattr(gate, "get_staged_files", lambda: staged)
    return gate.main(str(msg))


def test_approve_is_required_for_risky_python(tmp_path, monkeypatch) -> None:
    assert _run(tmp_path, monkeypatch, "test audit\nCodex-Reviewed: APPROVE\n", ["backend/x.py"]) == 0


def test_request_changes_always_blocks(tmp_path, monkeypatch) -> None:
    assert _run(tmp_path, monkeypatch, "Codex-Reviewed: REQUEST_CHANGES\n", ["goal.md"]) == 1


def test_request_changes_cannot_be_overridden_by_later_approval(tmp_path, monkeypatch) -> None:
    assert _run(
        tmp_path,
        monkeypatch,
        "Codex-Reviewed: REQUEST_CHANGES\nCodex-Reviewed: APPROVE\n",
        ["goal.md"],
    ) == 1


def test_later_request_changes_overrides_earlier_approval(tmp_path, monkeypatch) -> None:
    assert _run(
        tmp_path,
        monkeypatch,
        "Codex-Reviewed: APPROVE\nCodex-Reviewed: REQUEST_CHANGES\n",
        ["goal.md"],
    ) == 1


def test_request_changes_cannot_be_overridden_by_skip_text(tmp_path, monkeypatch) -> None:
    assert _run(
        tmp_path,
        monkeypatch,
        "Codex-Reviewed: REQUEST_CHANGES\ncodex-review: skipped reason=cleanup\n",
        ["goal.md"],
    ) == 1


def test_skip_reason_does_not_bypass(tmp_path, monkeypatch) -> None:
    assert _run(
        tmp_path,
        monkeypatch,
        "codex-review: skipped reason=docs-only cleanup\n",
        ["docs/README.md"],
    ) == 1


def test_generic_codex_or_agent_reference_does_not_bypass(tmp_path, monkeypatch) -> None:
    assert _run(tmp_path, monkeypatch, "codex review agent acf91c1f\n", ["backend/config/x.yaml"]) == 1


def test_non_risky_text_change_does_not_require_review(tmp_path, monkeypatch) -> None:
    assert _run(tmp_path, monkeypatch, "notes only\n", ["notes.txt"]) == 0


def test_risky_set_matches_safe_commit_contract() -> None:
    assert gate.needs_codex_review([
        "goal.md",
        "backend/config/x.yml",
        "query.sql",
        "scripts/x.sh",
        "data/lineage/graph.json",
        ".gitignore",
    ])


def test_staged_file_scan_failure_blocks_instead_of_looking_empty(
    tmp_path, monkeypatch, capsys
) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("Codex-Reviewed: APPROVE\n", encoding="utf-8")
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=128, stdout="", stderr="fatal: bad index"
        ),
    )

    assert gate.main(str(msg)) == 2
    assert "cannot prove the staged scope" in capsys.readouterr().err
