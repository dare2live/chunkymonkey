from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    index_status: str
    worktree_status: str
    raw_status: str


def parse_git_status_short(text: str) -> dict[str, Any]:
    """Parse `git status --short` into a compact JSON summary."""
    entries: list[GitStatusEntry] = []
    counts: dict[str, int] = {
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "modified": 0,
        "deleted": 0,
        "added": 0,
        "renamed": 0,
        "copied": 0,
        "unmerged": 0,
    }
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        raw_status = raw_line[:2]
        path = raw_line[3:].strip() if len(raw_line) > 3 else ""
        if raw_status == "??":
            index_status = "?"
            worktree_status = "?"
        else:
            index_status = raw_status[0]
            worktree_status = raw_status[1]
        entry = GitStatusEntry(
            path=path,
            index_status=index_status,
            worktree_status=worktree_status,
            raw_status=raw_status,
        )
        entries.append(entry)
        status_chars = {index_status, worktree_status}
        if index_status not in {" ", "?"}:
            counts["staged"] += 1
        if worktree_status not in {" ", "?"}:
            counts["unstaged"] += 1
        if "?" in status_chars:
            counts["untracked"] += 1
        if "M" in status_chars:
            counts["modified"] += 1
        if "D" in status_chars:
            counts["deleted"] += 1
        if "A" in status_chars:
            counts["added"] += 1
        if "R" in status_chars:
            counts["renamed"] += 1
        if "C" in status_chars:
            counts["copied"] += 1
        if status_chars & {"U"} or raw_status in {"AA", "DD", "AU", "UD", "UA", "DU"}:
            counts["unmerged"] += 1
    return {
        "clean": not entries,
        "total": len(entries),
        "counts": {key: value for key, value in counts.items() if value},
        "entries": [asdict(entry) for entry in sorted(entries, key=lambda item: item.path)],
    }
