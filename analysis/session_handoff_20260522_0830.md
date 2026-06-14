# Session 交接备忘 — 2026-05-22 08:30 CST

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


> 用户 push back: "暂停一下, 把当前工作做个交接, 新起 terminal 无缝衔接".
> 本 doc 是给新 session 的完整 context, 配合 goal.md + SESSION_HANDOFF.md 一起读.

## 0. 新 session 立刻读什么 (3 件套)

1. `goal.md` 顶部 (Perception 推进 5 步 + GCP 监控原则 + 物理边界)
2. `SESSION_HANDOFF.md` (cron 每 5 min 自动更新)
3. **本 doc** (即时手写交接, 包含 background process 状态)

## 1. Background processes 状态 (新 session 不要 kill, 它们要继续跑)

| pid | 命令 | 状态 | 角色 |
|---|---|---|---|
| **50468** (本地 Mac) | `bash scripts/post_retrain_chain.sh` | 跑中, 5min poll | 等 final fit EXITED → export → pull → vm_stop → post_retrain_pipeline |
| **3627** (GCP VM) | `python retrain_lambdamart_v6.py --use-checkpoint-best ...` | 跑中, elapsed 24:32 (08:32 CST) | Final fit, window 20/34 done, ~14 剩 |

**新 session check 这两个还活着**:
```bash
ps -ef | grep post_retrain_chain | grep -v grep    # 本地
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command "ps -p \$(cat ~/chunkymonkey/data/reports/stability_retrain/current.pid) -o pid,etime,cmd --no-headers"   # 远端
```

## 2. GCP retrain 当前状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_stability_20260521T055800Z` |
| Mode | **Final fit** (`--use-checkpoint-best` skip Optuna, 用 best.json 跑 full data train + write predictions) |
| Best params (from best.json) | trial_number=130, best_value=0.4009, max_depth=7, num_leaves=80, lr=0.041 |
| Progress | window 20/34, ~70-80s/window |
| 预计完成 | ~08:50-09:00 CST (再 15-20 min) |
| Spot preempt 历史 | 2 次 (22:44 + 23:52 CST), 4 次 auto-resume |
| Cost | $5.91 / 58.1% of $15 budget, 估 final fit 结束 +$0.2-0.3 |

**完成后 chain 自动接管**:
- export prediction parquet → GCS
- gcloud cp 拉本地 `data/phase5_exports/lgbm_phase5_stability_20260521T055800Z/`
- vm_stop
- `bash scripts/post_retrain_pipeline.sh` (paper_sim + Phase4 gate + registry)
- 读 verdict + macOS notify

## 3. 监控指令 (新 session 用)

| 目的 | 命令 |
|---|---|
| Chain 进度 | `tail -20 /tmp/post_retrain_chain.log` |
| Chain 触发 done flag | `cat data/reports/gcp_auto_resume_done.json 2>/dev/null` (auto-resume done) / 或看 chain log "FINAL VERDICT" |
| Final fit 远端进度 | `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command "tail -15 /tmp/final_fit.log"` |
| Cost | `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/cost_tracker.sh \| tail -8` |
| 任务状态 | `TaskList` |
| /goal command | 跑 `/goal` (已设 slash command) |

## 4. 任务清单 (8 个 task)

| # | Task | 状态 | 备注 |
|---|---|---|---|
| 20 | 综合规划 4 项目协同 | [DONE] | doc 完成 |
| 21 | GCP A Resume retrain | [RUN] in_progress | 实际进入 final fit 阶段 |
| 22 | cleanup + goal.md + /goal prompt | [DONE] | |
| 23 | X1 Perception sourcing 摸底 | [DONE] | 4 调研均跑完 |
| 24 | GCP auto-resume monitor | [DONE] | 已完成使命, 被 chain 替代 |
| 25 | Stage B P4 hsgt/dzjy PIT 化 | [HOLD] blocked | 复杂度上调 (hsgt 2 年没 sync), 留下次启动 |
| 26 | Stage E Project D 股票图谱 MVP | [RUN] 阶段 1 done | 阶段 2 UI 留下次 |
| 27 | retrain 完跑 post_retrain | [RUN] in_progress | chain pid 50468 跑中 |
| (28 新) | **Stage X2.1: tdx_industry daily snapshot** | [DONE] **刚完成** | history 表从 7 → 8 dates, daily_update Step 2j 已加 |

## 5. 数据 sourcing gap 状态 (Perception P3+P5 阻塞链)

| Gap | 状态 | 下一步 |
|---|---|---|
| 概念 PIT 历史 | [DONE] X2.1 启动累积 (8 dates, 每天 +1) | 等时间 (1+ 年才完整 PIT) |
| F10 业务暴露度 | [NO] 妙想 `rpt_f10_op_businessanalysis` 表存在但 ChunkyMonkey 没 import | X2.3 待启动 (1-2 周 parser) |
| 产业链关系图谱 | [NO] 完全没有 | X2.4 依赖 X2.3 |
| hsgt 北上 daily sync | [NO] 卡 2024-08-16 | X2.2 / Task #25 (2-3 天重写) |
| dzjy 大宗 daily sync | [NO] 仅 548 rows 4 天 | 同上 |

## 6. 关键 commits (本 session)

| Commit | 内容 |
|---|---|
| `9907f3c2` | market_perception/utils.py + finite_float 集中 |
| `c5e2c0fe` | 删 3 跑题 doc + goal.md Perception plan |
| `67b38088` | Stage E 阶段 1 + auto-resume monitor |
| `74d0bc77` | post_retrain_chain.sh |
| `3a8a8844` | **X2.1: tdx_industry daily snapshot 加 daily_update Step 2j** (最新, 刚 commit) |

## 7. 4 项目协同图 (战略 anchor)

```
                   Project A: 主项目 (LambdaMART + paper_sim)
                     ↑ verdict 待出 (final fit 跑完 + Phase4 gate)
                     │
        ┌────────────┴────────────┐
        │                          │
   Project D: 股票图谱            Project B: BestChoice
   (主项目内, 阶段 1 API done)    (sibling, Phase 0 freeze done)
   不接 ranker                    等主项目 verdict 启动 Phase 1
        │                          │
        └──── 共享底层 ────────────┘
                     │
                     ▼
            Project C: Market Perception
            (sibling /stock/perception/)
            9 模块 P1-P7 MVP done
            P3 主题扩 + P5 ChainDiffusion 完整版待 (依赖 sourcing)
```

## 8. 新 session 推荐第一句话

直接跑 `/goal` (slash command 已设, prompt 在 `~/.claude/commands/goal.md` 或某全局位置).

`/goal` 会自动: 读 goal.md → 检查 GCP → 报状态 → 给下一步选项.

如果不用 slash command, 直接说: **"看看 GCP 状态和 4 项目推进, 按 goal.md 走"**.

## 9. 警报: 如果 chain monitor 死了

| 症状 | 应对 |
|---|---|
| `ps -ef \| grep post_retrain_chain` 无 | 看 `/tmp/post_retrain_chain.log` 最后状态, 决定: 手动跑 post_retrain_pipeline.sh / 还是重启 chain |
| VM 又 preempt (TERMINATED) | `bash gcp/vm_start.sh` + SSH 重启 final fit (`--use-checkpoint-best`) |
| Final fit 死了 (pid 3627 not found) | SSH check `tail /tmp/final_fit.log` 看错误, 可能 OOM 或被 OS killed |
| Cost 触及 100% | 不会触发 auto-stop (alert-only 设计), 但要 manual stop VM |

## 10. 物理边界硬约束 (4 次重申, 别破)

- **Perception 严格不接入主项目 LambdaMART panel / ranker / paper_sim / champion**
- **BestChoice 物理隔离 sibling repo**, 走 mart 表 challenger import
- **Project D 仅 UI 查询层**, 不接 ranker
- 数据层共享 (主项目 DuckDB), 但各项目写各自 mart 表
