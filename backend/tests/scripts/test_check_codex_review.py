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


def test_skip_reason_is_not_counted_as_approval(tmp_path, monkeypatch) -> None:
    """2026-08-10 裁决：缺 APPROVE 不再阻断，但 skip 文本仍不得被识别为「已审查」。

    阻不阻断是一回事，语义是另一回事：正则若被放宽到匹配
    `codex-review: skipped`，就把「明确跳过」洗成了「审过了」—— 那是语义污染，
    与本次降级无关，必须守住。
    """
    body = "codex-review: skipped reason=docs-only cleanup\n"
    assert _run(tmp_path, monkeypatch, body, ["backend/config/x.yaml"]) == 0
    assert gate.has_approved_codex_review(body) is False


def test_generic_codex_or_agent_reference_is_not_approval(tmp_path, monkeypatch) -> None:
    body = "codex review agent acf91c1f\n"
    assert _run(tmp_path, monkeypatch, body, ["backend/config/x.yaml"]) == 0
    assert gate.has_approved_codex_review(body) is False


def test_request_changes_still_blocks(tmp_path, monkeypatch) -> None:
    """降级后唯一保留的阻断：显式否定裁决。

    它有信息量 —— 没人会「忘记」写下 REQUEST_CHANGES，写下它就意味着确有未消除
    的异议。放行它才是真风险。
    """
    body = "some slice\n\nCodex-Reviewed: REQUEST_CHANGES\n"
    assert _run(tmp_path, monkeypatch, body, ["backend/config/x.yaml"]) == 1
    # 连 L1 也不放行：否定裁决优先于 tier 分类
    assert _run(tmp_path, monkeypatch, body, ["goal.md"]) == 1


def test_non_risky_text_change_does_not_require_review(tmp_path, monkeypatch) -> None:
    assert _run(tmp_path, monkeypatch, "notes only\n", ["notes.txt"]) == 0


def test_l1_docs_skip_rule10_without_approval(tmp_path, monkeypatch) -> None:
    """WP1: machine L1 (docs/analysis only) does not require Codex-Reviewed."""
    assert _run(tmp_path, monkeypatch, "docs board update evidence\n", ["goal.md", "docs/README.md"]) == 0


def test_l1_mixed_with_service_stays_l3_but_no_longer_blocks(tmp_path, monkeypatch) -> None:
    """混入 service 文件仍被机器分类为 L3（fail-closed 分类未变），只是不再阻断。

    tier 分类是客观事实（读 staged 路径），必须保持 fail-closed；被降级的只有
    「必须自写一行 APPROVE」这个无法验证的通过条件。
    """
    staged = ["goal.md", "backend/services/calendar.py"]
    assert _run(tmp_path, monkeypatch, "mixed slice evidence\n", staged) == 0
    assert gate.commit_tier_for_staged(staged) != "L1"


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
