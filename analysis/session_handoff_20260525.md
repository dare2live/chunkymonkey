# Session Handoff 2026-05-25 — Phase 4.2b walk-forward 跑到一半暂停

> 用户说 "把现在的工作停下来" — 不杀 bg 进程(进度按 window 增量 checkpoint 保存了), 我停止主动监控 + 写续行指引.
> 下次对话 user 说 "继续" → read this doc → 决定 resume 路径.

## 当前状态 (2026-05-25 07:30 CST)

### 1. 后台进程 (PID 88818) — **已 KILL 2026-05-25 07:35 CST**

> 用户后续指示 "后台任务也停" → 主动 kill PID 88818.

| 项 | 值 |
|---|---|
| PID | 88818 (KILLED) |
| 命令 | `PYTHONPATH=backend python backend/scripts/retrain_unified_ranker_walkforward.py` |
| Elapsed before kill | 2h20min (started ~05:13 CST, killed 07:35 CST) |
| CPU time before kill | 72 min |
| RAM peak | 1.13 GB (健康, 不爆) |
| 进度 final | **14/22 windows done** |
| Checkpoint | `data/reports/optuna/unified_ranker_wf_v1_20260524T220852Z.per_window.json` (incremental, 每窗写入完整) |
| metrics.json | **未生成** (脚本 final aggregate 步骤未跑) |
| CSV oos predictions | **未生成** |

**Resume 模式两选**:

- **A. 重跑全部 22 窗**: 删 partial per_window.json, 从头跑. ETA ~3h wall. 安全但浪费 14 windows 已算结果.
- **B. 改脚本加 resume mode**: 读 per_window.json 跳已完成 windows, 只跑剩 8 个 (15-19). 改 ~30 行脚本. ETA 剩 ~1h wall.
- **C. 跳过 4.2b 完成, 直接用 14 window partial 当 verdict**: mean -0.0092, positive 5/14 < 0.04 exit gate → 视为 FAIL → Phase 5 Config B G1-only 锁定. **推荐 C** — 14 window 已足够强 evidence (剩 8 window 即使全 +0.04 也只到 ~0.0055).

### 2. 已有 14 windows 实测结果

```
Running mean: -0.0092 ± 0.0671
Positive rate: 5/14 = 35.7%
v7 baseline: 0.0475 ± 0.0686, 11/16 = 68.75%
```

**初步 verdict**: 大概率 FAIL exit gate (rank_ic >= 0.04). 8 个剩 windows 即使全 +0.04 average 也只能拉到 ~0.005, 仍 << 0.04. **可视为已 FAIL.**

### 3. 决策树 (verdict 出来后做什么)

**IF rank_ic_mean >= 0.04** (低概率):
- Phase 4.2b PASS → 启动 Phase 4.2c GCP Optuna 50-trial re-search ($5-10)
- BC 副本 (Phase 4.1b) 在 4.2c 之后做

**IF rank_ic_mean < 0.04** (高概率, ~95% confidence based on current trend):
- Phase 4.2b FAIL → unified panel 当前设计**不足以 beat v7**
- Phase 5 Config B G1-only **正式锁定** (v7 daily inference 是 sole production)
- 后续 unblock 路径:
  a. **Phase 3.7 backfill**: stock_context_daily + under_reaction_daily marts 缺少 2024-11 ~ 2026-04 历史. 跑 perception sibling 项目 backfill 后再做 4.2b 重试 (1 week)
  b. **Phase 4.3 linear/factor pivot**: 写线性多因子模型当 G2/G3 备选, 不依赖 LightGBM unified
  c. **Phase 4.1b bc_absorbed**: 加 49 个 BC formula features 进 unified panel 看是否补救 (deferred 直到 4.2b 通过, 暂时不动)

## 已完成里程碑 (本 session)

| Commit | Phase | Content |
|---|---|---|
| 41d22802 | v7 ops | v7 daily inference 闭合 3 gap (booster + script + mart) |
| 16f38199 | Codex resume | 恢复 Codex 协作 + Stop hook R1/R3 re-enable |
| 262bbf09 | Phase 3.2 | 5 perception engines PIT-strict built_at filter (13 SQL sites) + PreToolUse codex consult hook |
| f8acad0f | Phase 3.6 | Pattern 9 audit perception_absorbed CLEAN |
| 6694d2ad | Phase 4.1a | mart_p0a_feature_label_panel_unified_v1 (2.7M × 166 cols) |
| 471b4b06 | Phase 4.2 MVP | single-fit unified ranker, verdict NOT promotable (rank_ic 0.011) |
| 6f60da9e | Phase 4.2-diag plan | Codex path C 决策 + ablation script |
| 29eaa69f | Phase 4.2-diag verdict | single-fit DEAD, walk-forward needed; Phase 5 Config B activated |
| acdfe111 | Phase 4.2b script | walk-forward script (incremental checkpoint, low-mem) |

## 关键文件 (resume 时读这些)

| 文件 | 用途 |
|---|---|
| `goal.md` Phase 4 section | 当前 plan 含 4.1a/4.2/4.2-diag/4.2b/4.2c/4.1b 状态 + Config A/B contingency |
| `analysis/phase42_diag_verdict_20260525.md` | Phase 4.2-diag 完整 verdict + 决策树 |
| `analysis/phase42_ablation_20260524T155215Z.json` | 4-config ablation evidence table |
| `data/reports/optuna/unified_ranker_wf_v1_20260524T220852Z.per_window.json` | Phase 4.2b incremental progress (14/22 windows now) |
| `backend/scripts/retrain_unified_ranker_walkforward.py` | Phase 4.2b script |
| `backend/scripts/run_phase42_diag_ablation.py` | Phase 4.2-diag ablation script |
| `backend/scripts/build_unified_panel_v1.py` | Phase 4.1a unified panel build |
| `backend/scripts/train_unified_ranker_v1.py` | Phase 4.2 single-fit MVP (kept as POC, NOT for production) |
| `backend/scripts/run_daily_v7_inference.py` | v7 daily inference (G1 production, daily_update.sh Step 5e) |

## Resume 步骤 (下次 user 说 "继续" 时执行)

1. **读三件套**: goal.md + SESSION_HANDOFF.md (auto-cron) + analysis/workflow_checkpoint.md
2. **读本 doc**: analysis/session_handoff_20260525.md (this file)
3. **检查 bg 进程状态**:
   ```bash
   ps -p 88818 -o pid,stat,etime,cputime,pcpu,pmem,rss 2>&1 | head -3
   ls -t data/reports/optuna/unified_ranker_wf_v1_*.per_window.json | head -1 | xargs python -c "
   import sys, json, statistics
   d = json.load(open(sys.argv[1] if len(sys.argv)>1 else input()))
   print(f'Windows: {len(d)}/22')
   ics = [w['rank_ic_mean'] for w in d]
   print(f'Mean: {statistics.mean(ics):.4f}, positive: {sum(1 for x in ics if x > 0)}/{len(ics)}')
   "
   ```
4. **分情况处理**:
   - **如果 PID 88818 还活着 + windows < 22**: 等剩余 windows. Monitor with `tail -f /private/tmp/.../bcga4j4hj.output` (path in `~/.claude/projects/.../tasks/`).
   - **如果 22 windows 已完成 + metrics.json 存在**: 读 verdict, 按决策树 (上面) 推进.
   - **如果进程已 killed/crashed + windows < 22**: 看 per_window.json mean, 决定是 (a) 重启 walk-forward (incremental can resume, 但脚本目前不支持 resume mode - 需要修改) 或 (b) 视为 FAIL 直接走 Phase 5 Config B.
5. **执行下一阶段**: 按决策树 + goal.md Phase 4-5 plan.

## 已知坑 (resume 时避免)

- **磁盘空间紧张**: smartmoney.duckdb = 29 GB, 当前 free = 20 GB. 大 Optuna run / GCP 同步要先腾空间.
- **进程 stuck on DuckDB write semaphore**: 之前 17465 进程因 disk 97% + 36GB peak RAM 卡在 semaphore_wait. 重启脚本时确认磁盘 > 15 GB free.
- **pyarrow 未装**: 脚本 fallback 到 CSV. 如要 parquet, `pip install pyarrow`.
- **Codex review gate 已恢复 (2026-05-24)**: 任何 substantive code commit 前需 codex:codex-rescue review, commit message body 加 8-char hex agent ID 或 'codex review agent ...' 或 '# codex-review: skipped reason=...'.
- **不要重训 v7**: v7 是 G1 production, 不要 retrain (除非有明确决定 promote a successor).

## Session 主线决策回顾

| 决策点 | 选择 | 理由 |
|---|---|---|
| 4.1b BC merge vs 4.1a perception only | 4.1a only first | Codex Q1 建议 50-trial 但 MVP 应先验 single-fit 是否 work |
| 4.2 MVP single-fit | 跑了 + 验证 NOT promote | 诚实 verdict, 不藏负面结果 |
| 4.2-diag path A/B/C | C (诊断 root cause) | Codex 决策, 不在 broken ranker 上 build 浪费时间 |
| Phase 4.2b walk-forward | 跑 22 windows | 验证 walk-forward 是否能 close 76% rank_ic 退步 gap |
| Phase 5 Config A/B | Config B G1-only | 4.2-diag verdict PARTIAL, 不等 4.2c, v7 daily inference 已 operational |

## Codex agent IDs (引用历史 review)

- `a7f6f763c431c9c09`: Phase 3.2 PIT verdict (built_at filter required)
- `a8d412b03d91193c6`: Phase 4.2 MVP Q1+Q2+Q3 verdict (shifted train window, NULL handling, deployment threshold)
- `a885609738ef505a4`: Phase 4.2-diag path C decision (diagnose first)
- `a688cdb280316d4a8`: Codex codegraph 项目架构 survey

---

**This handoff doc 是 commit 一部分; PROJECT_INDEX.md + SESSION_HANDOFF.md 也 cron 同步.**
