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

**Snapshot 时间**: 2026-06-03 09:18:39 CST

## 当前切片

- `assets/js/stock-view.js` 里的 `buildStockIndex()` 把筛选选项收集、`screeningMap` / `turtleMap` 计数、覆盖股票集合与股票索引收成一次遍历，并对空输入做兜底；`renderFilterBar()` / `renderTopkSummary()` 直接复用这个索引，不再分别扫 `byStock`。`backend/tests/contract/test_stock_view.py` 新增 helper 行为回归，`backend/tests/contract/test_workbench_frontend_contract.py` 补 export / wiring contract。验证：`node --check assets/js/stock-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_stock_view.py` 3 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/stock-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步，`git diff --check` PASS；但全仓 broad scan 仍为 WARN / 80 high findings，残余继续集中在 `assets/js/app.js` / `assets/js/settings-view.js` / `assets/js/signal-adapter.js` / `assets/js/stock-view.js` 的历史 heuristic 行。

## 上一切片

- `assets/js/data-view.js` 里的 `buildAssetHealthIndex()` / `buildAuditResultsModel()` / `buildRoutesTableModel()` 现都改成直线型 `for...of` 收口，`buildRoutesTableModel()` 还把 route 过滤字段收成一次性 `buildRouteSearchText()` 搜索串，避免每条 route 再跑一层 `some()` 回调；`backend/tests/contract/test_data_view.py` 新增 `protocol` / `raw_table` filter 回归，锁住多字段过滤语义。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_data_view.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/data-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步；但全仓 broad scan 仍为 WARN / 80 high findings，主要残余还在 `assets/js/app.js` / `assets/js/settings-view.js` / `assets/js/stock-view.js` / `assets/js/signal-adapter.js` 的历史热点。

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
| HEAD | `2d932cc0 refactor: stock-view index consolidation | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 3 passed, targeted complexity PASS, codegraph sync PASS` |
| 最近 24h commits | 275 |
| 未 commit 文件 | 2 |

### 最近 10 commits

```
2d932cc0 refactor: stock-view index consolidation | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 3 passed, targeted complexity PASS, codegraph sync PASS
eeff4015 refactor: data-view route search cleanup | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 6 passed, targeted complexity PASS, codegraph sync PASS | post-fix-audit cleanup verified 无残留
5bcea950 docs: refresh session handoff snapshot after signal-adapter grouping cleanup | # commit-msg: minimal
e834f18c refactor: signal-adapter grouping cleanup | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: node --check PASS, audit PASS, pytest 4 passed, targeted complexity PASS, codegraph sync PASS | post-fix-audit cleanup verified 无残留
690dd3ed docs: refresh session handoff snapshot after app navigation helper extraction | # commit-msg: minimal
74e25438 refactor: app navigation helper extraction | Codex-Reviewed: APPROVE | test pass: node --check PASS, audit PASS, pytest 2 passed, complexity no obvious hotspots, codegraph sync PASS
3aaea862 docs: refresh session handoff snapshot after chunkyctl action suffix helper extraction | # commit-msg: minimal
d3ecc21e refactor: chunkyctl action detail suffix helper | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: py_compile PASS, pytest 28 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
d220d960 docs: refresh session handoff snapshot after need summary helper regression coverage | # commit-msg: minimal
c7543045 test: add helper regression coverage for need summary | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: py_compile PASS, pytest 24 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
40cb6c4b docs: refresh session handoff snapshot after blocked need summary helper extraction | # commit-msg: minimal
947abce1 refactor: blocked need summary helper | Codex-Reviewed: APPROVE_WITH_NOTES | test pass: py_compile PASS, pytest 22 passed, audit PASS, complexity no new HIGH, codegraph sync PASS | # commit-msg: minimal
b6eb97be docs: refresh session handoff snapshot after need_027 source-registration helper extraction | # commit-msg: minimal
```

## NEXT ACTION (auto-computed)

**continue complexity hotspot triage — app.js / settings-view.js / signal-adapter.js / stock-view.js**

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
3. Claude 看到: 当前主线状态 / local artifacts / next action
4. Claude 按 NEXT ACTION 执行本地工作 (audit / compare / commit / etc)
5. 用户 0 需要 paste 长 summary

GCP controlled-use (2026-05-21 用户澄清):
- 可用于大计算、寻优、长 replay、主项目与 BestChoice 综合寻优。
- 启动前说明 scope、wall time/成本、输入快照、输出路径、artifact 保存与 stop/rollback。
- 脚本层仍要求 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`, 防误触。
