# SESSION HANDOFF — Auto-updated by cron

> 此文件由 `scripts/session_snapshot.sh` 每 5 min cron 自动更新.
> Claude session start 时 read 此文件即可无缝衔接, 不需要用户 paste context.
> Mac 重启 / terminal 崩 后, 启动 Claude → 自动 read → 立即知道当前状态 + next action.
> 业务 pipeline 进度另见 `analysis/workflow_checkpoint.md` (pull/audit/paper_sim/KPI/gate/decision).

## 中断恢复用法 (用户必读)

### 1. Mac 重启 / terminal 崩 后:
```
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state + prompt 模板
claude                              # SessionStart hook 自动 inject 本 handoff
```

### 2. 用户输入哪句话给 Claude:
- **方案 A** (SessionStart hook 配好, 推荐): 不用输入, hook 自动 inject 本 handoff, Claude 看到立即继续 next_action
- **方案 B** (hook fail / 想显式 trigger): 输入 `继续, 看 SESSION_HANDOFF.md 按 next_action 推进`
- **方案 C** (复杂多步流程): 输入 `从 analysis/workflow_checkpoint.md 推断当前 pipeline step, 按 next_recovery_command 继续`

### 3. 一次性 install 全部 resilience:
```
bash scripts/install_resilience.sh   # SessionStart hook + cron + launchd 全装
bash scripts/install_resilience.sh --status   # check 装好没
```

**Snapshot 时间**: 2026-05-20 10:16:53 CST

## 主线 retrain 状态

| 项 | 值 |
|---|---|
| Model ID | `lgbm_phase5_gcp_20260520T010718` |
| VM 状态 | RUNNING |
| VM 上次启动 | 2026-05-19T18:56:38.590-07:00 |
| VM 上次停止 | 2026-05-19T18:51:59.254-07:00 |
| F2 checkpoint best_value |  |
| F2 checkpoint best_trial |  |
| F2 updated_at |  |
| F2 path | `` |

## 后台 process

| 项 | 状态 |
|---|---|
| Local monitor | alive PID=68231 elapsed= |
| Codex companion threads | 0 running |

0

## GCP 成本

| 项 | 值 |
|---|---|
| 月预算用 | 39.9% |
| 剩余 spot 小时 | 20.1 h |

## Git 状态

| 项 | 值 |
|---|---|
| Branch | main |
| HEAD | `b23f6ffb perf(updater): batch fix N+1 真问题 2 处 (criteria #8 70→75%)` |
| 最近 24h commits | 40 |
| 未 commit 文件 | 15 |

### 最近 10 commits

```
b23f6ffb perf(updater): batch fix N+1 真问题 2 处 (criteria #8 70→75%)
bdb0b843 feat: 中断恢复 1 命令入口 + 用户 prompt 模板 (session resilience 优化)
4eece3bd doc: goal.md 更新 — retrain v1 preempted + v2 in-flight (F1+F2) + criteria 7/9 推进
61c81eaa feat: notification framework (criteria 7 P0a, Codex a92b87c4) — email + macos + slack drivers
d81975e6 feat: criteria 7 UI/UX P0a + criteria 9 lineage_url 集成 paper_sim KPI + silent except 修
320ffdbb feat: GCP reliability F4 + F5 + session resilience monitor log cap (Claude general a9cddbf3 + 用户 push)
edc2bce5 feat: session 无缝衔接 framework (Mac 重启/terminal 崩/Claude session 中断 proof)
3bbf7667 feat: GCP retrain reliability F1+F2 (Optuna SQLite + 每 trial checkpoint) — 防 spot preempt 浪费
713368cf doc: GCP retrain reliability root cause + 5 fix + 3 resume option (Codex bocq8b60j)
52877e88 feat + doc: goal.md 加 criteria 7-9 (Codex a9e53d93) + P-1 trade_date Phase A 实施 (Codex ae706482)
```

## NEXT ACTION (auto-computed)

**15 uncommitted files — git status 看 + bash scripts/safe_commit.sh**

## Resilience 配置 (verified)

| 机制 | 状态 |
|---|---|
| F1 Optuna SQLite storage | deployed (`sqlite:///data/reports/optuna/$MODEL_ID.db` resume on preempt) |
| F2 per-trial checkpoint | deployed (`data/reports/optuna/$MODEL_ID.best.json` atomic write) |
| nohup + setsid + disown | retrain detached, SSH 断不影响 |
| monitor MAX_DURATION_HOURS=24 | Mac sleep proof |
| cron session_snapshot.sh | 5min auto update, 不依赖 Claude session 活 |
| SessionStart hook (~/.claude/settings.json) | 启动时 auto-read SESSION_HANDOFF.md |
| Stop hook session_rule_audit | 防 multi-agent / continuous-mode 违规 |

## 一旦中断如何无缝衔接

1. **Mac 重启 / terminal 崩 后**: 启动 terminal → `cd /Users/dp/Documents/M/stock/chunkymonkey` → 启动 `claude`
2. Claude SessionStart hook 自动 cat `SESSION_HANDOFF.md` 注入 context
3. Claude 看到: 当前 retrain model_id / VM status / F2 best_value / monitor 状态 / next action
4. Claude 按 NEXT ACTION 执行 (restart monitor / pull predictions / resume retrain / commit / etc)
5. 用户 0 需要 paste 长 summary

如果 F2 checkpoint 存在 + retrain 被 preempt, F1 SQLite resume:
  ```bash
  gcloud compute instances start chunkymonkey-optuna --zone=us-central1-a
  # SSH 跑同样命令 — Optuna load_if_exists=True 自动 resume 已完成 trials
  gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command '
    cd ~/chunkymonkey && source .venv/bin/activate
    setsid nohup bash -c "
      python backend/scripts/retrain_lambdamart_v6.py --model-id lgbm_phase5_gcp_20260520T010718 \
        --start-date 2023-01-03 --end-date 2026-05-19 --n-trials 50 --min-train-months 6 \
        --study-storage sqlite:///data/reports/optuna/lgbm_phase5_gcp_20260520T010718.db \
        --study-name lgbm_phase5_gcp_20260520T010718 \
        --checkpoint-path data/reports/optuna/lgbm_phase5_gcp_20260520T010718.best.json \
        --top-k 20 > logs/retrain_lgbm_phase5_gcp_20260520T010718.log 2>&1
      RC=$?
      if [ $RC -eq 0 ]; then sudo shutdown -h +5; else sudo shutdown -h +60; fi
    " < /dev/null > /dev/null 2>&1 &
    disown
  '
  ```

---

## minhold=15 PIT/leakage verdict (2026-05-20 上午, user 接受)

User push: "跌到 70-80% 收益率也很高了啊, 回撤也不大, 作为备选吧, 确定不是 leakage 没有未来函数就行"

**Code-level PIT audit verdict — 0 未来函数, 0 leakage**:

| 检查 | Evidence | 结论 |
|---|---|---|
| walk_forward expanding_monthly | run_p0b_lambdamart_v6.py:223 split_expanding_monthly, train<test 严 | ✓ |
| assert_pit_strict 守门 | line 89-122 (Fix 1 int64 + legacy 全验 train.max()<test.min()) | ✓ |
| 预测 walk_forward_mode='expanding_monthly' | model 标记 OOS | ✓ |
| paper_sim T+1 | driver.py:94 注释明确 mirror T+1 设计 | ✓ |
| exit_rules current_close | T 当天 close (非 future), 实盘 T+1 open 退出微 5bps slippage gap | ✓ |
| 4 absolute leakage 红线 | sharpe<5/ann<100%/win<95%/uplift<50% | ✓ |
| minhold15 vs baseline +60% | 相对 leakage warn, **alpha 来源 = exit mechanism (强制持≥15d 过滤 stop_hit 假回调), 非 model feature leakage** | ✓ 机制清楚 |

**结论**: minhold=15 作为 **prod-candidate 备选 alpha 增强**.
实盘 honest expectation: ann ~70-80% (扣 frictions), dd -20.4%, sharpe 2.12 production-grade.
