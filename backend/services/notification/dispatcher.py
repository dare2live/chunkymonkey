"""Notification dispatcher for daily alert reports."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from .base import load_yaml_config, repo_root
from .email_driver import EmailDriver
from .macos_driver import MacOSDriver
from .slack_driver import SlackDriver


log = logging.getLogger(__name__)

ALERT_KEYS = ("sla_warn", "kpi_anomaly", "leakage_red")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "warn", "warning", "red", "critical"}


def load_trigger_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else repo_root() / "configs" / "notification" / "triggers.yaml"
    return load_yaml_config(path)


def extract_alerts(report: dict[str, Any], trigger_config: dict[str, Any] | None = None) -> dict[str, bool]:
    flags = report.get("alert_flags") if isinstance(report.get("alert_flags"), dict) else {}
    conditions = (trigger_config or {}).get("conditions") or {}
    alerts: dict[str, bool] = {}
    for key in ALERT_KEYS:
        condition = conditions.get(key) if isinstance(conditions, dict) else None
        if isinstance(condition, dict) and condition.get("enabled") is False:
            alerts[key] = False
            continue
        alerts[key] = _truthy(report.get(key)) or _truthy(flags.get(key))
    return alerts


def _severity_for(active: list[str], trigger_config: dict[str, Any]) -> str:
    rank = {"info": 0, "warn": 1, "warning": 1, "critical": 2, "red": 2}
    chosen = "info"
    conditions = trigger_config.get("conditions") if isinstance(trigger_config.get("conditions"), dict) else {}
    for key in active:
        condition = conditions.get(key) if isinstance(conditions, dict) else {}
        severity = str(condition.get("severity") or ("critical" if key == "leakage_red" else "warn"))
        if rank.get(severity, 0) > rank.get(chosen, 0):
            chosen = "critical" if severity == "red" else severity
    return "warn" if chosen == "warning" else chosen


def _channels_for(active: list[str], trigger_config: dict[str, Any]) -> list[str]:
    channels: list[str] = []
    default_channels = trigger_config.get("default_channels") or ["email", "macos"]
    conditions = trigger_config.get("conditions") if isinstance(trigger_config.get("conditions"), dict) else {}
    for key in active:
        condition = conditions.get(key) if isinstance(conditions, dict) else {}
        configured = condition.get("channels") if isinstance(condition, dict) else None
        for channel in configured or default_channels:
            if channel not in channels:
                channels.append(str(channel))
    if not channels:
        channels = [str(channel) for channel in default_channels]
    return channels


def _load_report(report_path: str | Path) -> dict[str, Any]:
    path = Path(report_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read report {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"report {path} is not a JSON object")
    data["_report_path"] = str(path)
    return data


def _subject(report: dict[str, Any], active: list[str]) -> str:
    report_date = report.get("date") or report.get("run_date") or "unknown"
    return f"ChunkyMonkey daily alerts {report_date}: {', '.join(active)}"


def _body(report: dict[str, Any], active: list[str]) -> str:
    lines = [
        f"Report: {report.get('_report_path', '-')}",
        f"Active alerts: {', '.join(active)}",
    ]
    sla_summary = report.get("sla_summary")
    if isinstance(sla_summary, dict):
        lines.append(f"SLA alerts: {sla_summary.get('n_alerts', 0)}")
        stale_sources = sla_summary.get("stale_sources") or []
        if stale_sources:
            lines.append(f"Stale sources: {', '.join(str(item) for item in stale_sources)}")
    latest_kpi = report.get("latest_kpi")
    if isinstance(latest_kpi, dict):
        lines.append(
            "Latest KPI: "
            f"sim_run_id={latest_kpi.get('sim_run_id', '-')} "
            f"annual_return={latest_kpi.get('annual_return', latest_kpi.get('ann_ret', '-'))} "
            f"max_dd={latest_kpi.get('max_dd', '-')} "
            f"sharpe={latest_kpi.get('sharpe', '-')}"
        )
    return "\n".join(lines)


def _instantiate_drivers(
    channels: list[str],
    *,
    email_config: dict[str, Any] | None = None,
    slack_config: dict[str, Any] | None = None,
    macos_config: dict[str, Any] | None = None,
) -> list[Any]:
    drivers: list[Any] = []
    for channel in channels:
        if channel == "email":
            cfg = email_config if email_config is not None else None
            driver = EmailDriver(config=cfg)
            if driver.enabled:
                drivers.append(driver)
        elif channel == "macos":
            driver = MacOSDriver(config=macos_config)
            if driver.enabled:
                drivers.append(driver)
        elif channel == "slack":
            cfg = slack_config if slack_config is not None else None
            driver = SlackDriver(config=cfg)
            if driver.enabled:
                drivers.append(driver)
        else:
            log.warning("unknown notification channel: %s", channel)
    return drivers


def dispatch_report(
    report_path: str | Path,
    *,
    trigger_config: dict[str, Any] | None = None,
    email_config: dict[str, Any] | None = None,
    slack_config: dict[str, Any] | None = None,
    macos_config: dict[str, Any] | None = None,
) -> bool:
    trigger_config = trigger_config or load_trigger_config()
    report = _load_report(report_path)
    alerts = extract_alerts(report, trigger_config)
    active = [key for key, value in alerts.items() if value]
    if not active:
        log.info("no notification alerts in report=%s", report_path)
        return False

    channels = _channels_for(active, trigger_config)
    severity = _severity_for(active, trigger_config)
    subject = _subject(report, active)
    body = _body(report, active)
    drivers = _instantiate_drivers(
        channels,
        email_config=email_config,
        slack_config=slack_config,
        macos_config=macos_config,
    )
    sent_any = False
    for driver in drivers:
        sent_any = bool(driver.send(subject, body, severity)) or sent_any
    return sent_any


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch ChunkyMonkey notifications")
    parser.add_argument("--report", required=True, help="Daily JSON report path")
    parser.add_argument("--config", default=None, help="Trigger config YAML path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    config = load_trigger_config(args.config)
    dispatch_report(args.report, trigger_config=config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
