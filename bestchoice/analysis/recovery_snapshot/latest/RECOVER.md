# Latest Recovery Snapshot

This directory is deleted and recreated on every checkpoint run. Only the latest snapshot is kept.

## What To Tell Codex

请从中断处继续。先运行 `python scripts/workflow_checkpoint.py --brief`，检查 consistency.ready 和 next_action，然后按照 next_action/命令继续；遵守 goal.md 和 agent.md，不写入生产 stock_formula_best.csv。

## Human Recovery

```bash
python scripts/workflow_checkpoint.py --brief
bash analysis/recovery_snapshot/latest/resume.sh
```

## Next Command

```bash
cat analysis/operational_delivery_readiness.md
```

## Verification

```bash
bash analysis/recovery_snapshot/latest/verify.sh
```

## Space Policy

- Only `analysis/recovery_snapshot/latest/` is kept.
- Old snapshots are removed before writing the new one.
- Large CSV and DuckDB artifacts are not copied; `artifact_manifest.json` records their size and timestamp.
