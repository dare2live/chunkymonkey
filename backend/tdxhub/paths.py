"""Path helpers that are safe to import without optional data packages."""

from __future__ import annotations

from pathlib import Path


def get_config_path(config: str = "config.json") -> str:
    """Return a path under the per-user tdxhub config directory."""

    filename = Path.home() / ".tdxhub" / config
    filename.parent.mkdir(parents=True, exist_ok=True)
    return str(filename)
