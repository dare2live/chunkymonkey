from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "check_codex_review.py"
    spec = importlib.util.spec_from_file_location("check_codex_review", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_message(tmp_path: Path, text: str) -> Path:
    msg_path = tmp_path / "COMMIT_EDITMSG"
    msg_path.write_text(text, encoding="utf-8")
    return msg_path


def test_accepts_canonical_codex_review_trailer(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "get_staged_files", lambda: ["backend/scripts/audit_docs_graph.py"])

    rc = module.main(str(_write_message(tmp_path, "test audit\nCodex-Reviewed: APPROVE_WITH_NOTES")))

    assert rc == 0


def test_rejects_request_changes_trailer(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "get_staged_files", lambda: ["backend/scripts/audit_docs_graph.py"])

    rc = module.main(str(_write_message(tmp_path, "test audit\nCodex-Reviewed: REQUEST_CHANGES")))

    assert rc == 1


def test_accepts_uncommented_skip_reason(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "get_staged_files", lambda: ["backend/config/test_tool_registry.yaml"])

    rc = module.main(str(_write_message(tmp_path, "test audit\ncodex-review: skipped reason=docs-only rename")))

    assert rc == 0


def test_rejects_short_skip_reason_for_code_change(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "get_staged_files", lambda: ["backend/tests/scripts/test_safe_commit.py"])

    rc = module.main(str(_write_message(tmp_path, "test audit\ncodex-review: skipped reason=typo")))

    assert rc == 1


def test_commit_msg_minimal_does_not_bypass_rule10(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "get_staged_files", lambda: ["backend/scripts/check_codex_review.py"])

    rc = module.main(str(_write_message(tmp_path, "test audit\n# commit-msg: minimal")))

    assert rc == 1
