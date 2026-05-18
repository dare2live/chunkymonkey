# ChunkyMonkey 指挥管理体系 (Orchestration Master Plan)

> 用户 push back 2026-05-18: "你要不要先设计一个指挥管理体系和方案, 怎么管理、调度、使用 agents 和 codex, 怎么使用谷歌云的资源".
> 不再 reactive ad-hoc 修. 顶层指挥体系 + 调度 + 监控 + 成本管理.

## 0. 体系总览

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 0: User (终极决策)                                          │
│   - 终极目标定义 / push back 标准 / 项目方向                       │
│   - "不报喜不报忧" / "PIT critical" / "真金白银"                   │
└────────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────────┐
│ Layer 1: Claude main session (主指挥, ONE per session)            │
│   职责: 接 user 指令 → 拆任务 → 调度 → 监控 → 同步 → 综合 → 报告    │
│   工具: Bash / Read / Edit / Write / Agent / Task                │
│   不做: 大规模设计 / 长时 compute / 重复 audit (派下层)             │
└────────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────────┐
│ Layer 2a: Codex agents (设计/调研/重构)        │ Layer 2b: Claude sub-agents (探索/计划)
│   - codex:codex-rescue subagent              │   - Explore (大代码库 grep)
│   - 跑本地 Mac (无 VM 依赖)                    │   - Plan (复杂多步实施 plan)
│   - 适合: 长 doc / 调研 / spec / refactor      │   - general-purpose (兜底)
│   - 并行: 可同时跑 3-5 个 (background)         │   - 跑本地 Mac
│   - 调度: 派完不阻塞 main                      │   - 适合: 探索 / 计划 / 兜底
│   - 监控: codex_monitor.sh 每 15min            │
└────────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────────┐
│ Layer 3: 计算资源 (Mac mini local + GCP VM)                       │
│   本地 Mac (8C 8GB):                                              │
│     - Claude session 自己                                          │
│     - Codex companion 跑本地                                       │
│     - 单测 / 小 SQL / commit / codegraph sync                      │
│     - paper_sim quick test                                         │
│     - 数据 audit (read-only)                                       │
│   GCP n2-standard-32 spot (us-central1, $0.376/h):                │
│     - Optuna 大规模 walk-forward (32 cores)                        │
│     - Akshare 数据 backfill (国内网络 block, VM 通)                │
│     - tdxhub 大批量历史拉取                                         │
│     - 多策略 parallel walk-forward (4-8 jobs)                       │
│   GCS (us-central1, $0.020/GB·月):                                │
│     - 永久数据 (smartmoney 21GB + market 1.5GB + alpha158 1.86GB)  │
│     - VM 间数据共享 (intra-region 免费)                             │
└────────────────────────────────────────────────────────────────┘
```

## 1. Agent 调度规则 (派谁做什么)

### 1.1 决策树: 派 Codex / 派 Claude sub-agent / Claude 自己做

```
任务来了
  │
  ├── 是否需要 compute (Optuna / paper_sim / panel build)?
  │     YES → GCP VM start, 跑完 stop, 不要派 agent (本身 batch)
  │     NO  → 下一步
  │
  ├── 是否需要长 doc / 调研 / spec / refactor?
  │     YES → 派 Codex (background, --fresh --model gpt-5.5 --effort xhigh)
  │     NO  → 下一步
  │
  ├── 是否需要大代码库探索 (> 3 grep)?
  │     YES → Claude sub-agent Explore
  │     NO  → 下一步
  │
  ├── 是否多步骤复杂 plan (> 5 步)?
  │     YES → Claude sub-agent Plan
  │     NO  → 下一步
  │
  └── Claude main 自己做 (1-3 文件改 / 单测 / commit / status check)
```

### 1.2 Codex 适合做的 7 类 (CLAUDE.md §10.0)

1. 架构 / 设计文档 (1000+ 行, DDL + SQL + decorator + test 模板)
2. 第三方工具调研 (license / ROI / 风险 verdict)
3. 数据 integrity / sync 修复
4. PIT-strict 设计 (新 mart schema + ASOF JOIN + audit)
5. SQL 性能 / 重构
6. factor spec / feature 设计
7. negative finding / 第二意见

### 1.3 Codex 派任务模板 (5 项)

```
1. 明确背景: 项目当前状态 + stack + 数据 inventory
2. 明确任务: 设计 X / 调研 Y / 修 Z + 输出格式 (表格 + 数字 + grep 路径)
3. 明确约束: 中文 / 无 emoji / PIT-strict / 单分支 main / license 政策
4. 明确反例: 项目踩过的坑 (Rule 5/7 反例) — 让 Codex 不重蹈
5. 明确禁忌: TODO 折中 / "估计影响小" / 单测跳
```

### 1.4 并行规则

- **Codex 并行**: `run_in_background=true`, 不同主题, 各跑各 thread, 完成 task-notification
- **Claude sub-agent 并行**: 同消息内多 Agent calls (max 5), 必须确认 file 无 conflict
- **混合并行**: Claude main 派 Codex + 自己跑 Claude sub-agent + 自己干小活, **三层都并行**

### 1.5 监控规则 (CLAUDE.md §10.0.4)

- **派 Codex 后**: 不被动等 task-notification (companion 可能 misreport)
- **每完成 main 任务后**: check `codex-companion status --json`
- **idle > 30 min**: cancel + 检查 doc/code 是否已 deliver
- **自动化**: launchd cron `scripts/codex_monitor.sh` 每 15 min
- **安装**: `cp configs/launchd/com.chunkymonkey.codex-monitor.plist ~/Library/LaunchAgents/ && launchctl load ...`

## 2. GCP 资源管理 (CLAUDE.md §10.0.2)

### 2.1 资源 inventory

| 资源 | 配置 | 单价 | 月费 24/7 | stop 后 |
|---|---|---|---:|---|
| VM n2-standard-32 spot | 32 vCPU + 128GB RAM | $0.376/h | $275 | $0 (compute) |
| Disk pd-standard 100GB | | $0.04/GB·月 | $4 | $4 (一直收) |
| GCS multi-region | smart+market+alpha158+delta ~25GB | $0.020/GB·月 | $0.50 | $0.50 (永久) |
| Egress (出 region) | scp + GCS download 偶发 | $0.12/GB | 看流量 | 看流量 |

用户预算: **$10/月 credit**.

### 2.2 任务 → 资源决策树

```
任务来了
  │
  ├── 是否需要 32+ cores 并行 compute?
  │     YES → GCP VM (Optuna grid / 多策略 walk-forward)
  │     NO  → 下一步
  │
  ├── 是否需要 国内 network unblocked?
  │     YES → GCP VM (akshare backfill / tdxhub 大批量)
  │     NO  → 下一步
  │
  ├── 是否 单测 / quick audit / commit / codegraph?
  │     YES → 本地 Mac
  │     NO  → 下一步
  │
  └── Default: 本地 Mac, 不要上 VM
```

### 2.3 VM 生命周期 (强制)

```
任务批前:
  bash gcp/vm_start.sh
  -- 启动 + 等 SSH ready (5-10 秒)

跑 batch:
  gcloud compute ssh ... --command='...'

任务批完:
  bash gcp/vm_stop.sh
  -- 立即 stop, compute $0/h
  -- 数据 keep on disk (后续 start 不丢)
```

### 2.4 月预算管理

| 月用量 | 预算 | 监控 |
|---|---:|---|
| < $5 | OK | 默认 |
| $5-8 | 60-80% credit, 留心 | 每周 1 次 `gcloud billing` check |
| $8-10 | 80-100%, 紧急 stop | 立即 audit 哪些任务长跑 |
| > $10 | 超预算 | 全 stop + 反省 task 是否必要 |

**当前用量 (2026-05-18)**: ~$5.4 (13h compute + scp + GCS)

### 2.5 数据 lifecycle

| 数据 | 位置 | 生命周期 | 备份 |
|---|---|---|---|
| Code | git main | 永久 | github |
| Codegraph index | `.codegraph/` 本地 | session-level | git ignored |
| 训练数据 (smartmoney.duckdb) | 本地 + GCS | 永久 | GCS multi-region |
| K-line (market.duckdb) | 本地 + GCS | 永久 | GCS |
| alpha158 panel | 本地 + GCS | 永久 | GCS |
| Optuna trials (mart_p1_optuna_trials) | DB | 永久 | GCS DB backup |
| Wave 1/2/N output (jsonl/parquet) | VM disk → GCS → 本地 merge | session | GCS |
| paper_sim KPI | DB | 永久 | GCS DB backup |

### 2.6 GCP 自动化

- `gcp/vm_start.sh` — 启动 + SSH wait (IAP tunnel)
- `gcp/vm_stop.sh` — 检查 active 任务后 stop (--force 跳 check)
- `gcp/setup_all.sh` — 4-arg setup (project / bucket / region / email)
- 未来: cron 定期 idle check + auto-stop (类似 codex_monitor)

## 3. Commit / push / codegraph 工作流 (CLAUDE.md §10.0.3 + 10.0.4)

### 3.1 流程模板

```bash
# 1. 写代码 / 改文件
vim foo.py

# 2. 检查 (可选)
PYTHONPATH=backend python -m pytest backend/tests/test_foo.py -q

# 3. Stage + commit + push + sync (一气呵成, 用 safe_commit.sh)
git add foo.py
bash scripts/safe_commit.sh "fix: xxx

# self-审 ...
# rule-compliance: ok evidence=..."
```

### 3.2 safe_commit.sh 5 步 pre-flight

1. git status — list staged
2. `check_project_index_sync.py` — PROJECT_INDEX 同步
3. `check_rule_compliance.py` — rule 6 evidence
4. commit-msg keyword check (GROUP A + B, minimal marker)
5. git commit + push + `codegraph sync`

任一 fail → abort + 提示修法.

### 3.3 反 pattern (踩过)

- 改 5 文件攒一次 commit → terminal 崩 = 全丢
- Python 长跑 1 小时 crash → 没 commit = 全丢
- Codegraph stale → query "未定义" 误判
- commit reject 重试同 message → 浪费 5-10 min × N

## 4. 任务分类 + 调度策略 (4 类)

### 4.1 类 A: 设计 / 调研 / 文档 (Codex 主)

- 派 Codex `--model gpt-5.5 --effort xhigh --fresh`
- 并行多个 (最多 5)
- 等 task-notification + 主动 check 每 30 min
- 输出 doc → commit + push + codegraph sync

### 4.2 类 B: 代码 implementation (Codex 主 / Claude main 辅)

- 派 Codex 写代码 (Codex 适合 mid-size 重构)
- Claude main 收 + review + test + commit
- 大功能 split 多 Codex 并行 (e.g. Phase 1.1 / 1.2 / 1.3 parallel)
- 小改 Claude main 自己做

### 4.3 类 C: compute (GCP VM 主)

- start VM
- 派任务 (nohup 后台)
- 跑批期间 Claude main 干别的
- 等完 / Monitor 触发 → stop VM
- 数据 → GCS → local merge

### 4.4 类 D: 自动化 / 维护 (本地 cron + launchd)

- codex_monitor.sh 每 15 min auto-cancel idle
- nightly_data_audit.py 每天 2 AM
- 未来: daily_data_update.sh 全自动化 (用户终极交付要求)

## 5. 项目交付标准跟踪 (goal.md 同步)

6 项交付标准 (用户 2026-05-17 定义):

| # | 类别 | 标准 | 工具 |
|---|---|---|---|
| 1 | 数据管理 | sync gap auto-alert + watermark 实填 + PIT 严格 | nightly_data_audit + scripts/preflight_panel_build.py |
| 2 | 策略模型管理 | MSAF 3 类全上线 + ensemble + regime gate | Phase 1-3 implementation |
| 3 | backtester gate | PBO/DSR/conservative/IS-OOS 4 gate enforce | docs/backtester_mcp_integration_20260517.md 实施 |
| 4 | 全自动化 daily update | 1 click or zero, 不需要大模型维护 | scripts/daily_update.sh (待写) |
| 5 | GCP 成本控制 | ≤ $10/月 credit | vm_start.sh / vm_stop.sh + codex_monitor |
| 6 | 实盘 GO/NO-GO | 5 年 backtest 中位 ≥ 25%, 单年 ≥ 0%, Sharpe ≥ 2.0, PBO ≤ 0.2 | Phase 4 validation gate |

## 6. 不再 ad-hoc 撞墙修

每次 user push back → 立刻固化 (CLAUDE.md rule + memory + script):

| user push back | 固化路径 |
|---|---|
| "充分利用 Codex" | CLAUDE.md §10.0 + memory feedback_codex_proactive_dispatch |
| "你和 codex 多轮次沟通" | CLAUDE.md §10.0.1 + memory feedback_multi_agent_collab |
| "GCP 当重点固化" | CLAUDE.md §10.0.2 + gcp/vm_start.sh + vm_stop.sh + memory feedback_gcp_cost_control |
| "提高 commit 频率" | CLAUDE.md §10.0.3 + 流程模板 |
| "Codex idle 浪费" | CLAUDE.md §10.0.4 + scripts/codex_monitor.sh + launchd plist |
| "commit retry 浪费" | scripts/safe_commit.sh + pre-flight |
| "顶层指挥体系" | 本 ORCHESTRATION.md + 5 个 section |

## 7. 持续优化 (随时更新本 doc)

不固定. 每次 user push back / Claude 反省 / Codex 反例 — 加 section / update flow.
