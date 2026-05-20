"""SMTP notification driver."""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .base import BaseDriver, load_yaml_config, repo_root


log = logging.getLogger(__name__)


class EmailDriver(BaseDriver):
    """Send notifications through SMTP.

    ``dry_run`` defaults to true, so the driver logs without opening SMTP
    unless explicitly configured otherwise.
    """

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None) -> None:
        if config is None:
            path = Path(config_path) if config_path else repo_root() / "configs" / "notification" / "email.yaml"
            config = load_yaml_config(path)
        super().__init__(config)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def dry_run(self) -> bool:
        return bool(self.config.get("dry_run", True))

    def _subject_with_prefix(self, subject: str) -> str:
        prefix = f"[CM-Daily-{datetime.now().strftime('%Y%m%d')}]"
        if subject.startswith(prefix):
            return subject
        return f"{prefix} {subject}"

    def send(self, subject: str, body: str, severity: str) -> bool:
        subject = self._subject_with_prefix(subject)
        if not self.enabled:
            log.info("email notification disabled: %s", subject)
            return False
        if self.dry_run:
            log.info("email dry run severity=%s subject=%s", severity, subject)
            return True

        smtp_host = str(self.config.get("smtp_host") or "")
        smtp_port = int(self.config.get("smtp_port") or 587)
        from_addr = str(self.config.get("from_addr") or "")
        to_addrs = self.config.get("to_addrs") or []
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        app_password = str(self.config.get("app_password") or "")

        if not smtp_host or not from_addr or not to_addrs:
            log.error("email config missing smtp_host/from_addr/to_addrs")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(str(addr) for addr in to_addrs)
        msg["X-CM-Severity"] = severity
        msg.set_content(body)

        smtp_cls = smtplib.SMTP_SSL if smtp_port == 465 else smtplib.SMTP
        with smtp_cls(smtp_host, smtp_port, timeout=10) as smtp:
            if smtp_port != 465:
                smtp.starttls()
            if app_password:
                smtp.login(from_addr, app_password)
            smtp.send_message(msg)
        return True
