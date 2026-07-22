"""dispatcher --skip-macos coalesces soft-degrade banners."""
from __future__ import annotations

import json
from pathlib import Path

from services.notification import dispatcher as disp


def test_skip_macos_disables_macos_driver(tmp_path, monkeypatch):
    report = tmp_path / "daily_20260722.json"
    report.write_text(
        json.dumps(
            {
                "date": "20260722",
                "sla_warn": True,
                "alert_flags": {"sla_warn": True},
                "sla_summary": {"n_alerts": 1, "stale_sources": ["tushare"]},
            }
        ),
        encoding="utf-8",
    )
    sent: list[tuple[str, str, str]] = []

    class FakeMacOS:
        def __init__(self, config=None):
            self.config = config or {"enabled": True}

        @property
        def enabled(self):
            return bool(self.config.get("enabled", True))

        def send(self, subject, body, severity):
            sent.append((subject, body, severity))
            return True

    class FakeEmail:
        def __init__(self, config=None):
            self.enabled = True

        def send(self, subject, body, severity):
            sent.append(("email:" + subject, body, severity))
            return True

    monkeypatch.setattr(disp, "MacOSDriver", FakeMacOS)
    monkeypatch.setattr(disp, "EmailDriver", FakeEmail)
    monkeypatch.setattr(
        disp,
        "load_trigger_config",
        lambda _=None: {
            "default_channels": ["email", "macos"],
            "conditions": {
                "sla_warn": {"enabled": True, "severity": "warn", "channels": ["email", "macos"]},
                "kpi_anomaly": {"enabled": False},
                "leakage_red": {"enabled": False},
            },
        },
    )

    assert disp.dispatch_report(report, macos_config={"enabled": False}) is True
    assert any(s[0].startswith("email:") for s in sent)
    assert not any(not s[0].startswith("email:") for s in sent)
