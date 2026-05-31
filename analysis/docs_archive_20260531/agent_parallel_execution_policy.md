# Concurrent Agents Execution Policy

Last updated: 2026-05-27

This policy defines which ChunkyMonkey work can be split across concurrent agents and which work must stay serialized under one controller. The goal is to keep parallel work useful without corrupting files, DuckDB state, GCP resources, or backfill outputs.

## Controller Rule

One primary controller owns final merge responsibility for every multi-agent session.

Default operating mode for ChunkyMonkey architecture work is controller-led:
the controller acts as architect, dispatcher, reviewer, and release gate. Agents
perform bounded execution only after the controller has decided the direction,
truth-source boundary, risk class, write scope, and validation requirements.
Agent output is never final by itself; it is reviewed evidence or a patch
candidate.

The controller must:
- decide the next objective and which parts should not be delegated;
- assign non-overlapping scopes before agents start;
- keep write ownership explicit by file, database, cloud resource, or result table;
- review all outputs before accepting them into the main project state;
- resolve conflicts manually instead of letting agents overwrite each other;
- run the final CodeGraph/complexity/tests/gates required by the accepted write set;
- update shared project state only after reconciling the final state.

No subagent should assume that its local finding or patch is final until the controller has merged it.

## Codex Agent Pattern

When Codex sub-agents are available, use them deliberately:

- Keep direction-setting, architecture review, final acceptance, commits, shared docs, GCP/Optuna control, and DuckDB write windows in the controller.
- Delegate concrete sidecar execution that is useful but not blocking the controller's next critical decision.
- Prefer read-only explorer agents for architecture review, complexity triage, data-source inventory, lineage mapping, failure root-cause analysis, and independent test-risk review.
- Use worker agents only with explicit disjoint ownership, such as `backend/routers/foo.py` plus its tests, or one isolated script plus one isolated test file.
- Every delegated prompt must state scope, write permissions, forbidden actions, and required output format.
- Shared files such as `goal.md`, `SESSION_HANDOFF.md`, `AGENTS.md`, `CLAUDE.md`, `PROJECT_INDEX.md`, and implementation plans are merged by the controller in one serialized pass.
- Controller records material agent findings in the current plan or handoff before ending the session.
- Controller may reject, rewrite, or partially accept agent output when it violates project truth sources, introduces unnecessary abstraction, lacks evidence, or fails gates.

## Work Allowed In Parallel

These tasks are safe to run concurrently when each agent has a clear scope and does not write to the same target:

- Documentation reading and summarization, including design docs, handoff files, audit notes, specs, and historical plans.
- DuckDB read-only inventory, including schema inspection, table counts, row-range checks, watermark checks, and lineage coverage audits, as long as every connection is opened read-only and no DDL/DML/backfill runs.
- Code-path audits, including static tracing of routers, services, scripts, tests, CLI entry points, and dependency boundaries.
- Tests and validation, including targeted pytest runs, dry-runs, report verification, and output comparison, provided they do not share a write target or mutate the same generated artifact.

Parallel agents must report scope, commands, files read, and any discovered write risks back to the controller.

For CodeGraph and complexity work, agents may run read-only `codegraph status`, `codegraph context`, `codegraph query`, and complexity scans. If an agent changes Python code, the controller must still run post-edit `codegraph sync .`, targeted tests, and the paired complexity scan before accepting the change.

## Work That Must Not Run In Parallel

These tasks are serialized unless the controller explicitly creates disjoint ownership boundaries:

- Writing the same file, including docs, configs, scripts, generated reports, checkpoints, and handoff files.
- Writing to the same DuckDB database, even when touching different tables, unless a single owner coordinates the write window.
- Starting a new GCP job, submitting training, launching remote materialization, or creating a new cloud batch.
- Stopping or starting a VM, changing GCP active-job markers, or changing cloud cost/monitoring guard state.
- Querying GCP state, GCS, billing/cost tracker, VM status, SSH, or monitor probes unless the controller has declared the controlled-use GCP scope and `CHUNKYMONKEY_GCP_EXPLICIT_OK=1` is set.
- Backfilling the same result table, including paper-sim KPI tables, lineage tables, prediction/result marts, and cache metadata tables.

If any task crosses from read-only inspection into mutation, it must stop and return to the controller for serialization.

## Conflict Avoidance

Before writing, every agent must check current repository status and reread the target file or state it is about to modify. Existing user or peer changes must be preserved.

When two agents need related changes, prefer this order:
1. run all read-only investigation in parallel;
2. have agents return proposed edits or findings;
3. let the controller apply the final write set in one serialized pass;
4. run verification after the write set is merged.

The default assumption is that parallelism is for discovery and validation. Final project state changes are controlled, reviewed, and merged by the primary controller.
