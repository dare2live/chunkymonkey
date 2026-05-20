from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.gen_report import render_daily_markdown
from services.notification import dispatcher
from services.notification.email_driver import EmailDriver
from services.notification.macos_driver import MacOSDriver
from services.notification.slack_driver import SlackDriver


def test_email_dry_run():
    driver = EmailDriver(
        config={
            "enabled": True,
            "smtp_host": "YOUR_SMTP_HOST",
            "smtp_port": 587,
            "from_addr": "YOUR_FROM_ADDR",
            "to_addrs": ["YOUR_TO_ADDR"],
            "app_password": "YOUR_APP_PASSWORD",
            "dry_run": True,
        }
    )
    with patch("services.notification.email_driver.smtplib.SMTP") as smtp, patch(
        "services.notification.email_driver.smtplib.SMTP_SSL"
    ) as smtp_ssl:
        assert driver.send("Daily", "Body", "warn") is True
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()


def test_macos_notification():
    run_result = Mock(returncode=0, stderr="")
    with patch("services.notification.macos_driver.subprocess.run", return_value=run_result) as run:
        driver = MacOSDriver(config={"enabled": True})
        assert driver.send("Daily", "Body", "warn") is True

    args = run.call_args.args[0]
    assert args[0] == "osascript"
    assert args[1] == "-e"
    assert "display notification" in args[2]
    assert 'with title "Daily"' in args[2]
    assert 'subtitle "WARN"' in args[2]


def test_slack_driver():
    response = Mock(status_code=200)
    with patch("services.notification.slack_driver.requests.post", return_value=response) as post:
        driver = SlackDriver(config={"enabled": True, "webhook_url": "https://hooks.example.test/services/test"})
        assert driver.send("Daily", "Body", "critical") is True

    post.assert_called_once()
    assert post.call_args.args[0] == "https://hooks.example.test/services/test"
    payload = post.call_args.kwargs["json"]
    assert payload["subject"] == "Daily"
    assert payload["severity"] == "critical"
    assert "Body" in payload["text"]


def test_dispatcher_routes(tmp_path: Path):
    report = tmp_path / "daily_20260520.json"
    report.write_text(
        json.dumps({"date": "20260520", "alert_flags": {"sla_warn": True, "kpi_anomaly": False, "leakage_red": False}}),
        encoding="utf-8",
    )
    trigger_config = {
        "default_channels": ["email", "macos"],
        "conditions": {
            "sla_warn": {"enabled": True, "severity": "warn", "channels": ["email", "macos"]},
            "kpi_anomaly": {"enabled": True, "severity": "warn", "channels": ["email"]},
            "leakage_red": {"enabled": True, "severity": "critical", "channels": ["email", "macos", "slack"]},
        },
    }

    with patch.object(dispatcher, "EmailDriver") as email_cls, patch.object(
        dispatcher, "MacOSDriver"
    ) as macos_cls, patch.object(dispatcher, "SlackDriver") as slack_cls:
        email_cls.return_value.enabled = True
        email_cls.return_value.send.return_value = True
        macos_cls.return_value.enabled = True
        macos_cls.return_value.send.return_value = True
        slack_cls.return_value.enabled = True

        assert dispatcher.dispatch_report(
            report,
            trigger_config=trigger_config,
            email_config={"enabled": True, "dry_run": True},
            slack_config={"enabled": True, "webhook_url": "https://hooks.example.test/services/test"},
        ) is True

    email_cls.return_value.send.assert_called_once()
    macos_cls.return_value.send.assert_called_once()
    slack_cls.assert_not_called()


def test_markdown_render(tmp_path: Path):
    report = tmp_path / "daily_20260520.json"
    report.write_text(
        json.dumps(
            {
                "date": "20260520",
                "top_recommendations": [
                    {"rank_in_date": 1, "stock_code": "000001", "stock_name": "Sample", "pred_score": 0.92}
                ],
            }
        ),
        encoding="utf-8",
    )

    markdown = render_daily_markdown(report, connect_db=False)

    assert "## Today Top-5 Recommendations" in markdown
    assert "## Current Holdings" in markdown
    assert "## Data Sync Status" in markdown
    assert "## Today KPI" in markdown
