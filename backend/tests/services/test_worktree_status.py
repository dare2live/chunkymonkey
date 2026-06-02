from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "services" / "worktree_status.py"
SPEC = importlib.util.spec_from_file_location("worktree_status", SCRIPT_PATH)
worktree_status = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = worktree_status
SPEC.loader.exec_module(worktree_status)


def test_parse_git_status_short_groups_dirty_worktree() -> None:
    status = worktree_status.parse_git_status_short(
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
