# launchd cron — Nightly Data Audit

`com.chunkymonkey.nightly-data-audit.plist` 每天 02:00 跑 `backend/scripts/nightly_data_audit.py` (governance v1 `periodic_audit`).

## Install

```bash
cp configs/launchd/com.chunkymonkey.nightly-data-audit.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.chunkymonkey.nightly-data-audit.plist
```

## Verify

```bash
# Loaded check
launchctl list | grep chunkymonkey

# Force trigger immediate run (skip schedule)
launchctl start com.chunkymonkey.nightly-data-audit

# Check audit JSON output
cat data/audit/nightly_data_audit_latest.json | python3 -m json.tool | head -50

# Check launchd log (stdout / stderr)
tail data/audit/launchd_stdout.log
tail data/audit/launchd_stderr.log
```

## Failure response (按 governance.yaml `periodic_audit.failure_response`)

| Severity | Response |
|---|---|
| `warn` | write audit report, continue read-only dashboards only |
| `block` | stop label panel / model training / paper_sim / P3 holdout / champion promotion |

当前实施: alert_path = `data/audit/nightly_data_audit_latest.json` + launchd stderr.
未来增强 (Phase 2+): slack / email alert + 自动 block downstream cron.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.chunkymonkey.nightly-data-audit.plist
rm ~/Library/LaunchAgents/com.chunkymonkey.nightly-data-audit.plist
```
