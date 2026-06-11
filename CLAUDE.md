# CLAUDE.md — Claude 会话专属规则

> 本文件只保留 Claude 会话专属规则与项目红线速查。通用工程规则 owner = `AGENTS.md` +
> `docs/` (authority 顺序见 `docs/README.md`)。Codex 默认不读本文件。
> 压缩后第一件事: 重读 `PROJECT_INDEX.md` 活索引部分防 context 失真。
> 2026-06-11 文档治理: 本文件从 615 行瘦身; 被移出内容的 owner 标注在各节。

## 1. 思维原则

### 1.0 第一性原理 + 奥卡姆剃刀

Owner: `docs/PROJECT_CONSTITUTION.md` 第一/二/三条 (真相源唯一 / 能删必删 / 模块+表+配置)。

写新模块 / 改架构 / 加表前 5 问速查:
1. 真相源是什么? (K 线 / 交易日历 / 交易所公告 — 不是中间派生表)
2. 这层 / 这张表 / 这个函数能删吗? 删了谁受影响?
3. 已有模块做过这事吗? (`rg` 先搜再写)
4. 规则写在哪? 必须 YAML, 不是代码 hardcode
5. 谁来保证数据新鲜? 没有自动 sync + audit = 不允许依赖

### 1.1 Think Before Coding

- 没有隐藏假设, 说出来 + 列 tradeoff. 不确定就问. 最少代码解决问题, 不写 speculative
  feature, 单次代码不抽象成框架. 只改必须改的, 不"顺手优化". 成功 = 用户能 verify 的可测试结果.

### 1.2 Measured Not Estimated

任何参数/阈值/模型预测/策略效果必须真实历史测过, 不能公式估.

- 禁止: "差不多" / "估计" / "假设" / "按当前速度跑" / "线性外推". 变量名含
  `_estimate / predicted_ / assumed_` 停下问: 真来自数据还是拍脑袋?
- 必能答: 哪些历史 row + 时间窗 + 哪个 fact 测出. 测不出 → 标 `unknown`, 不公式糊弄.
- Self-check: (1) 从哪行 SQL 跑? (2) 涵盖几行/几天真实历史? (3) 换 `unknown` 决策会不一样吗?
  (4) 用户能复现吗? 不能干净答 = estimate not measured, 不许提交.

### 1.3 真金白银 — 实盘投入

每行代码当"上线后亏钱我能睡着吗"评估. 用户原话: **"不是数字游戏, 是真金白银投入的"**.

- "leakage 影响估计 < 10%" → 0 leakage. "回测 +312%" → 立刻怀疑 leakage 不兴奋.
- "先跑试试" → 跑前想清"出数能直接决策吗", 不能别跑.
- 目标穿透 (KPI owner = `goal.md`): 任何分数 improve 必须能回答"含 selection bias /
  leakage 吗? 真实 forward 期望多少?". 不能穿透 = 噪音.

### 1.4 失败先承认

反转 OOS 不及格 → 立刻换方向, 不留恋. "不合格就是不合格" (用户原话).

## 2. 接手前 First Actions

1. 读 `goal.md` + `docs/chunkyctl_session_quickstart.md` (启动契约);
   `SESSION_HANDOFF.md` 仅 context-only 快照, live gates 优先于它.
2. `git status --short` 看脏 worktree. 不擅自 revert 别人 (Codex / 上一 session) 的改动.
3. 优先 `rg` / `codegraph` / targeted test / read-only DuckDB inspection — 别盲猜.
4. 任务前 commit 当前 state, 防 race condition.

## 3. 编码风格

Owner: `AGENTS.md` Engineering Rules. 速查: 数值/阈值/路径/日期/表名走 yaml;
同逻辑 2 次抽公共 3 次必重构; 可 hardcode 仅数学常数/边界/测试 fixture/SQL LIMIT (写注释);
用户要全量真实数据不"快速验证小样本"; 数据告诉什么就报什么, 不报喜不报忧.

## 4. 数据 / 策略安全 (核心红线)

### 4.1 PIT / Anti-Leakage

Durable owner: `docs/strategy_validation_contract.md`. 时刻 t 的决策只能用 <= t 信息.

速查 (场景 → 正解): 调参 → walk_forward.expanding_monthly; 排名 → OOS rolling 指标;
特征 → `bars[:sig_i+1]`; label → purged k-fold + embargo >= 1x forward 期;
JOIN → 永远带 `AND x.built_at <= t` / `as_of_date`; 宇宙 → `dim_index_member_history.as_of_date`;
复权 → PIT 复权; 生存者 → 含已退市 + 入场时点宇宙.

提交前 self-check: (1) 这数字在历史 t 当时能算出来吗? (2) 输入字段
`built_at/as_of_date` 都 <= t? (3) train/test 按时间还是 random? (random = leakage)
(4) 有 selection bias? (5) 跨期 label 有 purge + embargo? (6) 数字异常好看了吗 (见 4.2)?
不能干净答 = leakage, 不许提交.

### 4.2 异常高数字 = leakage 警报

- Absolute 红线: RankIC > 0.3 / sharpe > 5 / win_rate > 0.95 / 年化 > 100% / 胜率 100%
  → 立刻怀疑 leakage, 不是兴奋. 真实 forward 期望永远比回测低.
- Relative 红线: 相对 baseline 提升 >= +50% (e.g. RankIC 0.02 → 0.035 是 +75%) → 也算异常,
  必须 ablation 验证每 col 群 PIT 干净度.
- 干净参考: v3.2 P0b 实测 RankIC 0.0108-0.0203 (跨 5/10/20 horizon) = 干净 PIT 下诚实数字.

### 4.3 数据源全量默认 tushare (用户三次递进决策, 2026-06-11)

- **现有的和将来准备接入的所有数据, 默认改接 tushare.** 新数据需求不再做源选型: 查
  tushare catalog → 单日实弹核证字段/grain/单页上限 → 注册 sync_registry。tushare 没有的
  能力 (TDX F10 文本类/本地 CYQ 计算) 才落旧源, 且必须在 need contract 记录例外+理由。
  存量非 tushare 路径 (44 表清单见 analysis/tushare_full_migration_map_20260611.md) 一律
  是迁移对象: 双轨核对 → 按角色处置。消费侧方向 = tushare 转正、旧源降备援 — 不许把
  tushare 表述/设计成"兜底/对照", 那是主从倒挂 (被用户纠偏反例).
- **备用源是热备不是废弃 (用户原话: fallback 也可能会用到)**: tdxhub/miaoxiang 域切换后
  保持健康 — 坏了照修、SLA 照测 (阈值可放宽到备源档但不许静音); 只有 akshare 等淘汰源
  才在双轨核对后物理退役. fallback 链顺序随主源切换同步更新 (tushare 主 → tdxhub 备).
- tdxhub / miaoxiang: 数据质量 100% 可信 (角色 = 备用源). 缺失 = 自己 sync 路径 bug, 优先重拉.
- akshare: 不稳定 (限频/接口变), 正被 TuShare 替换 (见 goal.md need_027).
- tushare (vendor gateway): 171/239 接口实测可用; 间歇空响应/读超时/并发上限 2 — writer 必须
  把 0 行当失败重试; 单页上限必须实测防静默截断 (top_inst 1000 整反例).

### 4.4 Root Cause — 严禁忍

禁止: `try/except: pass` · `--skip-step` · `if env: bypass` · 钉死日期规避上游 bug.
必做: 找首次写坏路径修源头; 症状修复 (DELETE 坏行) != 根因修复, 两者都做;
找不到根因明说 + 加防御; 暂时绕过必须 TODO + 关联 commit, 不能伪装"已解决".

### 4.5 反例汇总

- **K 线含盘中数据** → 不要 `--end 2026-05-12` 钉死, 改 sync 入口 `latest_completed_trade_date` + lint 防回退.
- **DuckDB DELETE FATAL** → DROP+REBUILD index 清状态不够, 找首次写坏路径 + health check.
- **`fact_shareholder_plan.announce_date` 47% NULL** → 不放松 audit 阈值, 查 ingest 路径 (commit 69371838: 写 7034 placeholder 行 + DELETE 历史 + 防回退).
- **`mart_per_stock_stage_strategy_optimal.sharpe`** 全期 in-sample fit + `selector ORDER BY sharpe DESC` → paper_sim "+312%" 假象. 修法: walk_forward.expanding_monthly + selector ORDER BY `COALESCE(oos_sharpe, sharpe)` + `governance.enforce_pre_insert` 拒 `walk_forward_mode='none'`.
- **v3.2 `stage_opt_per_stock`** MAX(oos_sharpe) GROUP BY stock_code 给每 signal_date 用未来 Optuna → systemic leakage (Codex acf48d35 review Q1 critical, commit 5cc47987).
- **v3 102 features RankIC 0.0353** → relative +75% 没触发 absolute 但触发 relative, chain 含 inst_path_a (latest snapshot) + sector (99.978% fallback) leakage. 详 [[feedback-codex-critical-no-compromise]].
- **`swap_uplift_estimate` 公式估算** → 实测 swap 拉低年化 33pp. 改真实 K 线 forward 反事实.
- **vol-aware stop/target/trailing hardcode "业界常用"** → 丢 Optuna search space, walk-forward 拼 OOS 入 mart.
- **ensemble 13 weights 业务直觉写 yaml** → 全丢 Optuna.
- **regime_gate `bear/sideways/bull` 拍脑袋** → 历史 regime sensitivity sweep.
- **portfolio_backtest +45.4% 当最终决策** → 不含 tx_cost/T+1, paper_sim 加成本骤降. live 必须用含成本 paper_sim.
- **`max_stocks=200` 按 code 排序** → 只取 00 深主板, 创业板/科创板/沪主板 0 只参与 Optuna. 审计只查 DB 层 (5206 stocks PASS), 没查 runner 实际加载数. 改: 全量 universe + 运行时 `validate_loaded_stocks` (板块覆盖 + 80% 比例).
- **参数作用域错配** → `limit_up_pct` 是 per-stock 属性 (板块决定) 但放进 global Optuna search space. 改: per-stock 属性运行时自动取, 不进 search space.
- **cron 静默失败 (2026-06-11)** → daily_update cron 无 FDA 每天 'Operation not permitted', K 线断流 4+ 交易日无人知晓. 修法: launchd + python (有 FDA) 入口 + `scripts/launchd_job_wrapper.py` 失败告警送达. 防回退: 定时任务必须走 wrapper, 不许裸 cron; "失败必须有送达用户的 alert"是最低线.
- **审计必须验证运行时状态, 不只是前置条件** (4.6 并入): preflight 查的是"数据存在",
  运行时验证查的是"数据被正确使用". 反例: DB 有 5206 stocks → preflight PASS, 但 runner
  只加载 200 只 → 没人查. 两者缺一不可.

## 5. Optuna 治理

Owner: `docs/strategy_validation_contract.md` "Optuna Governance" 节 (3 守门点 / R1
expanding_monthly 标准 / OOS 列约定 / 3 道防线 / `fact_optuna_governance_log` 审计).
速查: 必走 `services.optimization` 中央层, 不裸调 `study.optimize`; 阈值全走
`backend/config/optuna_config.yaml`; selector/scoring 只读 `oos_*` 列.

## 6. 文档纪律

Owner: `docs/README.md` (lifecycle + authority) + `goal.md` Document Contract.

任务完成 != 代码写完: 以可运行结果 / 接口真实返回 / 关键样例真实数据抽查 / 测试通过为完成
依据. py_compile / lint pass 不算完成. 单分数 improve 不算 delivery — 要 audit script +
evidence artifact 同时同意. 之前 validation artifacts 不允许覆盖或删除, 加新证据 + 刷新 summary.

doc 自维护 (用户原话 "每次修改 claude.md / memory 时直接做优化更新"): 编辑本文件 /
memory 时顺手清过期/冗余/结构问题; 早期方向不删, 加 "状态: 已偏离 / deprecated" 头注.

## 7. Git + Commit

- 高频 commit + push (用户原话: 防 terminal 挂掉丢工作): 每完成一个子任务立即 commit +
  push, 不攒 batch; 没测完用 `wip:` 前缀. 单分支 `main`. 每次 commit 后 `codegraph sync`.
- 必用 `bash scripts/safe_commit.sh "message"`, 禁止裸 `git commit` 被 hook reject 后重试.
- Hook 矩阵 (误判改对应脚本 PATTERNS, 不要 `--no-verify` 跳): project-index-sync /
  rule-compliance (magic 数字无 `# evidence:` 注释) / commit-msg self-check 关键词 /
  no-emoji (PASS/FAIL 走文字).
- Codegraph + Complexity 双扫: owner = `docs/engineering_governance.md`. 速查: substantial
  change (新 service / LOC>50 / 文件>5 / 拆模块 / 改 SQL JOIN) 前 `codegraph query/context`
  看影响面, 改完 `codegraph sync .` + complexity 扫描; 豁免: 纯 doc / typo / 单行 config.

## 8. Self-Check

### 8.1 Write-time (数字字面量进代码前)

1. 这数字 measured from where? Optuna study → `# measured: optuna study <id>`; backtest →
   `# evidence: backtest <commit-hash>`; yaml → `# from yaml: <section>`; 都没有 → 停手.
2. 不接受: "看着合理" "业界常用" "我估计" "先用这个试" "跑出来不好再调".
3. yaml-back 默认, 业务代码只读 yaml. 可 hardcode (写注释): 数学常数/边界/测试 fixture/SQL LIMIT.

### 8.2 Commit-time (`git commit` 前逐项)

1. **PROJECT_INDEX.md 同步了?** — 更新对应活索引节 (数据表/service/script/yaml/反例);
   历史叙事写 `analysis/project_state_ledger.md`, 不进 INDEX changelog.
2. **测试加了?** — 核心逻辑单测; perf 改有 benchmark 防回退测试.
3. **commit message 含数字证据?** — "实测 4.8M 行 / 12 min / sample 验证", 不是 "fixed".
4. **反例表加了?** — 这次踩的新坑沉淀到 §4.5.
5. **真金白银 self-check** (策略 commit): 含 leakage/估算/假设? 穿透 forward 期望?
6. **改了 CLAUDE.md / memory 顺手优化了吗?** (见 §6)
7. **本次 fix 产生 stale artifact 都清了吗?** commit 含 fix/bug/leakage/cleanup/drop/remove/
   revert 关键词 OR 改 SQL/schema/_META_FIELDS/kill process → 强制走 post-fix audit 5 步:
   列 touched artifacts → 想 downstream stale → DB residue check → process/cache/tmp 清单 →
   execute cleanup + verify 0 residue.
8. **goal.md / SESSION_HANDOFF 同步了?** — delivery/validation/next action 变化即更新.
9. **Codegraph + Complexity 跑过了?** — substantial change 必走 §7 双扫.

不能逐项 yes = 别 commit. 重做.

### 8.3 Codex 不可用时的 self-审 fallback

commit message 必写 5 项: (1) PIT: 用未来信息了? (2) OOS: walk-forward 还是 in-sample?
(3) 单测: 正常+边界+异常? (4) 真金白银: leakage/估算/假设? (5) 反例: 对照 §4.5 了?
并标注 `codex-review: skipped reason=<原因>`.

## 9. 计算任务 (GCP 已退役)

GCP 执行面 2026-06-05 退役 (本节旧内容引用的 gcp/ 脚本与 yaml 已物理删除)。计算任务唯一
契约 = `backend/config/experiment_jobs.yaml` + `scripts/chunkyctl jobs --family <f> ...`。
Backend 状态: `local` active; `modal` 已配 token (2026-06-11, ~/.modal.toml, $30/月额度),
跑批前仍需 reviewed adapter + artifact-manifest 契约 (见 `docs/implementation_plan.md`)。
Long-run checkpoint reuse 规则 owner = `AGENTS.md`.

## 10. 并发

Owner: `AGENTS.md` Parallel Execution + `docs/engineering_governance.md`. 速查: 同文件
Edit / 同 DuckDB 表写 / 同 Optuna study / commit 序列必须串行; read-only audit / 独立
ablation 可并发 (DB 连接显式 `read_only=True`); 并发完成必须 main 串行汇总.

## 11. 协作与 commit 所有权

以 `goal.md` controller rule + `AGENTS.md` 为准: Codex 会话按 goal.md (Codex owns
direction/commits); Claude 会话走 `scripts/safe_commit.sh` 现行契约 — message 满足
`Codex-Reviewed:` 或 `codex-review: skipped reason=<...>` + §8.3 fallback 5 项.
豁免: 纯 markdown / 改名 / typo / hook+config 改 (非业务逻辑).
Codex 标 CRITICAL 涉及 PIT/leakage/真金白银 → 只能"完全接受+立刻修+test verified",
不许折中 (反例: 2026-05-15 折中导致 chain leakage RankIC +60% 假象).

## 11.5 Skills

| Skill | 触发场景 | 强制/建议 |
|---|---|---|
| `chunkymonkey-governance` / `engineering-discipline` (grill) | 执行计划前 (跑批 / Optuna / 新模块设计) | 强制 — 不 grill 不执行 |
| `plan_validator.enforce_optuna_plan()` | 跑批前 (代码级) | 强制 (2026-05-26 反例: 29/34 公式无 search space 白跑) |
| `/diagnose` | 硬 bug / 异常结果 / 性能回退 | 强制 — 不走诊断循环不猜 |
| `/lessons` | 改代码前查相关教训 | 建议 |
| 内置 `/tdd` `/handoff` `/to-issues` `architect-controller` | 对应场景 | 建议 |

执行前三问 (self-check): 跑完产出什么谁消费? 每步前提验证了? 成本 vs 产出合理?

## 12. 用户偏好 / 沟通

- 中文回复. 简洁实用. 表格 > 段落. 数字优先.
- 不报喜不报忧 — 0 STRONG_BUY / 数据滞后 / 测试 fail / Gate FAIL 先讲.
- 先讲业务结果 (年化/max_dd/超额), 技术次之.
- 接任务先 push back 看更简单方案, 别上来就实现.

## 附录

- 数据表 / 模块 / 命名陷阱 / 运行环境坑 → `PROJECT_INDEX.md` 活索引部分.
- KPI (年化>=30% / max_dd>=-20% / 超额 HS300>0 / 月胜率>=55%) owner = `goal.md`.
- 测试基线 / live gate 状态 → 跑 `scripts/chunkyctl doctor --fast`, 不引用文档里的旧数字.
