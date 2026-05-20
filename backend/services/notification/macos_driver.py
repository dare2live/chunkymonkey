"""macOS desktop notification driver."""
from __future__ import annotations

import logging
import subprocess
from typing import Any

from .base import BaseDriver


log = logging.getLogger(__name__)


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MacOSDriver(BaseDriver):
    """Send a local macOS notification through osascript."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config or {"enabled": True})

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def send(self, subject: str, body: str, severity: str) -> bool:
        if not self.enabled:
            log.info("macOS notification disabled: %s", subject)
            return False
        script = (
            f'display notification "{_applescript_string(body)}" '
            f'with title "{_applescript_string(subject)}" '
            f'subtitle "{_applescript_string(severity.upper())}"'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.error("macOS notification failed: %s", result.stderr.strip())
            return False
        return True
