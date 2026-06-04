# SESSION HANDOFF — Manual resume snapshot

> 此文件由 `scripts/session_snapshot.sh` / `scripts/cm_resume.sh` 按需手动刷新.
> Codex app/CLI 不再通过 cron 或 SessionStart hook 自动注入本 handoff，避免 stale state 被静默加载.
> 新会话应先按 `docs/chunkyctl_session_quickstart.md` 做启动检查，再把本文件当 context-only 状态快照.
> 业务 pipeline 进度另见 `analysis/workflow_checkpoint.md` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
```
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
```

### 2. 新 Codex 会话输入哪句话:
- **推荐**: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
- **简短恢复**: `继续，看 SESSION_HANDOFF.md 和 analysis/workflow_checkpoint.md，按 next_action 推进。`
- **复杂 pipeline**: `从 analysis/workflow_checkpoint.md 推断当前 pipeline step，按 next_recovery_command 继续。`

### 3. 自动注入状态:
```
bash scripts/install_resilience.sh --status
```
默认不再安装 cron snapshot / SessionStart auto-inject；如需恢复旧自动化，必须显式设置脚本里的 legacy opt-in。

**Snapshot 时间**: 2026-06-04 13:50:35 CST

## 主线状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_v9b_20260523T083000Z` |
| VM 状态 | ? |
| VM 上次启动 |  |
| VM 上次停止 |  |
| F2 checkpoint best_value | 0.3094819825339931 |
| F2 checkpoint best_trial | 32 |
| F2 updated_at | 2026-05-23T12:24:52+00:00 |
| F2 path | `data/reports/optuna/lgbm_phase5_v9b_20260523T083000Z.best.json` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | dead |
| Codex companion threads | 0 running |

0

## GCP 成本

| 项 | 值 |
|---|---|
| 月预算用 | 25.2% |
| 剩余 spot 小时 | 105.9 h |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `97c7f903 chore: exclude resume-generated checkpoints from snapshot dirtiness` |
| 最近 24h commits | 12 |
| 未 commit 文件 | 0 |

### 最近 10 commits

```
97c7f903 chore: exclude resume-generated checkpoints from snapshot dirtiness
1fa32d2d chore: align session snapshot next action
43b85626 docs: align data health startup contract
348c9b9f docs: refresh handoff after stage context production rebuild
b9e72d13 docs: record stage context production rebuild evidence
5a8273a0 docs: refresh handoff after technical-stage residual policy
ebd18209 fix: govern technical-stage residual classification
6c16bc47 docs: refresh session handoff after stage-opt gate update
9644d65d chore: expose stage-opt attrition and skill dispatch
7e1f14cb chore: capture Codex local ops and Moth profile rules
```

## NEXT ACTION (auto-computed)

**run startup checks first — scripts/chunkyctl doctor --fast; prioritize data_health blocking_yellow, then stage-opt structural blocker / need_027 blocked-gap triage**

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 14:11 CST)

- Treat `CLAUDE.md` as legacy Claude-only history. Codex policy comes from `AGENTS.md`, current docs, Codex skills, Moth evidence paths, and live tooling output unless the user explicitly asks for historical migration.
- `chunkyctl preflight` now exposes `controller_agent_gate` and `instruction_sources`; broad audit/research/architecture/data/debug/review/spec/triage work should dispatch bounded sidecar agents or record the concrete skip reason.
- Latest live forecast slice: `raw_profit_forecast_snapshot_daily` now has `2026-06-04` / `2,377` stocks; `mart_forecast_upside_live` now has `2026-06-04` / `2,305` stocks. `data_health_snapshot.py --dry-run --format text` improved to `green=316 / yellow=26 / red=0 / blocking_yellow=9`.
- Root cause fixed: `scripts/daily_update.sh` now refreshes `compute_forecast_upside_live.py` after raw forecast ingest using the same `FORECAST_SNAPSHOT_DATE`; `backend/tests/test_daily_update_model_refresh.py` locks this contract. The mart remains live shadow only, not training/backtest input.
- Next data-health write path should be sliced, not mixed: holder/F10, GPCW derived audits, capital sync, and feature-panel tail refresh. Re-run `doctor --fast` or `data_health_snapshot.py --dry-run --format text` after each writer slice.

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 16:45 CST)

- 2026-06-04 盘后刷新已完成。`data_health_snapshot.py --dry-run --format text` 当前为 `green=326 / yellow=16 / red=0 / blocking_yellow=0`；`scripts/chunkyctl doctor --fast` 仍为 `WARN`，但 `data_health.blocking_yellow_tables=[]`、`red_tables=[]`。下一会话不要再按 14:11 的 `blocking_yellow=9` 循环。
- K-line / feature-panel 已到 2026-06-04：本地 `build_price_kline_tdxhub.py --skip-existing --target-date 2026-06-04` 写入 `10,388` 行，`5,200` 股成功 / `000638` 失败；canonical K-line 为 `2022-01-01 -> 2026-06-04` / `5,203` codes。`sync_hs300_benchmark_kline.py` OK，`build_feature_panel_duck.py --mode incremental` 后 `fact_feature_panel` 为 `4,161,982` rows / `2023-01-03 -> 2026-06-04`，`feature_panel_prune_20260604_after_close` 显示两张 feature 表 `missing_signal_count=0` / `pruned_count=0`。
- holder/F10 / GPCW / capital 本轮 blocking 切片已处理：GPCW profile/PIT audit 已刷新；capital latest 更新到 `2026-06-04T15:00:54.088424`；F10 raw 到 2026-06-04，canonical holder replay 有 2026-06-04 行，9 个 holder/plan/trade replay 索引已恢复；`mart_shareholder_plan_initial_event` 重建为 `9,677` rows / `built_at=2026-06-04T08:33:04+00:00`。
- 本轮代码修复点：`ingest_holders_tdxhub.py` 的 replace raw replay 改成 raw-key temp table 批量删除旧 facts、跳过 holder-key 逐行 delete，并直接插入 `availability_source`，避免 DuckDB indexed delete fatal 和无索引逐行 UPDATE。相关测试 `backend/tests/test_ingest_holders_tdxhub.py` PASS。
- Moth 已提交并推送：`ed19610 feat: preserve profile instruction sources`。GitHub repo `dare2live/moth` 为 PUBLIC，本机 `/Users/dp/.local/bin/moth` 指向 repo `.venv/bin/moth`，`moth snapshot/profile` 能输出 `instruction_sources.ignored_by_default=["CLAUDE.md"]`。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 17:15 CST)

- `chunkyctl preflight` 的 controller/agent gate 已从文档提醒升级为机器门禁：广义 audit/research/architecture/data/debug/review/spec/triage 或 3+ 独立 scope 缺少 `--agent-dispatch` 证据时会返回 `controller_agent_dispatch_missing` 并 FAIL；`--agent-skip-reason` 只能作为 WARN 例外。后续新会话不要只在文字上承诺“总指挥模式”，要先让 preflight 看到 agent 调度证据。
- Rule 10 reviewer 指出 holder replace replay 还会留下旧 `fact_controlling_shareholder`。已修：replace replay 先解析成功 raw，再批量删除 holder/plan/trade/controlling 旧 facts；解析失败不会先删旧 facts。另补 `availability_source` 直接插入分支测试。核心 holder/chunkyctl/capital tests 当前通过。
- DB 容量只读并行审计已记录到 `analysis/db_capacity_audit_20260604.md`。`data/smartmoney.duckdb` 约 `33.6 GiB` / `34G`；未发现 `no2`、session snapshot 循环写爆，或能解释容量的 `.bak/.gz/.zst` 压缩备份副本。主因更像多版本宽面板全量并存、rank/cache 表 key 重叠、347 个索引/存储元数据开销、小表 repeated rewrite row-group bloat，以及 `formula_engine` reason JSON 总量 WARN。最可疑冗余组是 `mart_p0a_feature_label_panel` legacy/v3/v4/v5/unified，以及 `fact_feature_panel_candidate` / `fact_feature_panel_tdx_keep_challenger` / `mart_feature_rank_matrix_cache_*` 同 key 重叠。不要从这条证据直接删表或 VACUUM；下一步应单独做 retention/index/compact 方案，先备份、分类 owner、证明 consumer。

## POST-SNAPSHOT CONTROLLER NOTE (2026-06-04 18:45 CST)

- `scripts/chunkyctl preflight` wrapper 已补 `--agent-dispatch` / `--agent-skip-reason` 转发，项目推荐入口现在能真正把 agent evidence 传给 Python gate；缺失 evidence 的 broad work 仍会 FAIL。新增 wrapper 回归测试覆盖 positional scope、flag task、skip reason。
- DB retention 第一片已实现为 dry-run inventory：`backend/config/storage_retention.yaml` 新增 `table_inventory`，`backend/services/storage_retention.py` 输出 `table_inventory` / `table_inventory_count`，`backend/scripts/plan_storage_retention.py` 在非 execute 模式默认 read-only 打开 DuckDB。生产库只读 dry-run 当前为 `candidate_count=0`、`table_inventory_count=12`、`protected_artifact_table_count=7`、`compaction.recommended=false`；它只分类，不删除，不 VACUUM。
- sidecar 复核后的业务优先级：当前本地 P0 是收干净 controller tooling / retention dry-run 切片；下一业务 P1-A 是 `need_027` exact-flow blocked-gap triage。`data_health` 仍是 warning-only，`stage-opt` 是 P1 supply contract，`storage_payload` 为低优先级 WARN。

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (`sqlite:///data/reports/optuna/$MODEL_ID.db` resume on preempt) |
| F2 per-trial checkpoint | deployed (`data/reports/optuna/$MODEL_ID.best.json` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| manual session_snapshot.sh | active; run via `bash scripts/cm_resume.sh` |
| cron session_snapshot.sh | disabled by default for Codex app/CLI |
| SessionStart handoff auto-inject | disabled by default for Codex app/CLI |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → `cd /Users/dp/Documents/M/stock/chunkymonkey`
2. 运行 `bash scripts/cm_resume.sh` 刷新本 handoff 和 snapshot
3. 新 Codex 会话输入: `请按照 docs/chunkyctl_session_quickstart.md 接手本项目，先完成启动检查，再看 SESSION_HANDOFF.md 的 next_action。`
4. Codex 先跑 live checks，再按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`, 防误触。
