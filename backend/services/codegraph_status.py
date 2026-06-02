from __future__ import annotations

import re
from typing import Any


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STATUS_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]+):\s*(?P<value>.+)$")
TABLE_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*)\s+(?P<value>[\d,]+)$")
PENDING_RE = re.compile(r"^(?P<kind>Added|Modified|Deleted|Renamed):\s*(?P<count>[\d,]+)\s+files?$")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _parse_int(text: str) -> int:
    return int(text.replace(",", ""))


def _section_name(line: str) -> str | None:
    sections = {
        "Index Statistics:": "index",
        "Nodes by Kind:": "nodes_by_kind",
        "Files by Language:": "files_by_language",
        "Pending Changes:": "pending",
    }
    return sections.get(line)


def _status_key(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def parse_codegraph_status(text: str) -> dict[str, Any]:
    """Parse `codegraph status` text into stable JSON-friendly fields."""
    status: dict[str, Any] = {
        "project": None,
        "index": {},
        "nodes_by_kind": {},
        "files_by_language": {},
        "pending": {"total": 0, "sync_required": False},
    }
    section: str | None = None
    for raw_line in _strip_ansi(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        next_section = _section_name(line)
        if next_section:
            section = next_section
            continue
        if line.startswith("Project:"):
            status["project"] = line.split(":", 1)[1].strip()
            continue
        if section == "index":
            match = STATUS_VALUE_RE.match(line)
            if not match:
                continue
            key = _status_key(match.group("key"))
            value = match.group("value").strip()
            status["index"][key] = _parse_int(value) if value.replace(",", "").isdigit() else value
            continue
        if section in {"nodes_by_kind", "files_by_language"}:
            match = TABLE_VALUE_RE.match(line)
            if match:
                status[section][match.group("key")] = _parse_int(match.group("value"))
            continue
        if section == "pending":
            match = PENDING_RE.match(line)
            if match:
                status["pending"][match.group("kind").lower()] = _parse_int(match.group("count"))
            elif "No pending" in line:
                status["pending"]["none"] = True
    total = sum(
        value
        for key, value in status["pending"].items()
        if key not in {"total", "sync_required", "none"} and isinstance(value, int)
    )
    status["pending"]["total"] = total
    status["pending"]["sync_required"] = total > 0
    return status
