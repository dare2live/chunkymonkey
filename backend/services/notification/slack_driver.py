"""Slack webhook notification driver."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from .base import BaseDriver, load_yaml_config, repo_root


log = logging.getLogger(__name__)


try:
    import requests  # type: ignore
except Exception:
    class _RequestsFallback:
        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int = 10) -> Any:
            payload = __import__("json").dumps(json).encode("utf-8")
            req = urllib_request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout) as response:
                return type("Response", (), {"status_code": response.status})()

    requests = _RequestsFallback()


class SlackDriver(BaseDriver):
    """Send notifications to a Slack incoming webhook."""

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None) -> None:
        if config is None:
            path = Path(config_path) if config_path else repo_root() / "configs" / "notification" / "slack.yaml"
            config = load_yaml_config(path)
        super().__init__(config)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def send(self, subject: str, body: str, severity: str) -> bool:
        if not self.enabled:
            log.info("slack notification disabled: %s", subject)
            return False
        webhook_url = str(self.config.get("webhook_url") or "")
        if not webhook_url or webhook_url == "YOUR_WEBHOOK_URL":
            log.error("slack webhook_url is not configured")
            return False
        payload = {
            "subject": subject,
            "severity": severity,
            "body": body,
            "text": f"[{severity.upper()}] {subject}\n{body}",
        }
        response = requests.post(webhook_url, json=payload, timeout=10)
        status_code = int(getattr(response, "status_code", 0))
        if 200 <= status_code < 300:
            return True
        log.error("slack webhook failed status=%s payload=%s", status_code, json.dumps(payload, ensure_ascii=False))
        return False
