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

> 哲学 owner = `mio` skill (真相源原教旨派生面 measured-not-estimated)。本节只留项目机械红线 + self-check (非-mio/Codex 路径提交前拦截)。

- 禁止: "差不多" / "估计" / "假设" / "按当前速度跑" / "线性外推". 变量名含
  `_estimate / predicted_ / assumed_` 停下问: 真来自数据还是拍脑袋?
- 必能答: 哪些历史 row + 时间窗 + 哪个 fact 测出. 测不出 → 标 `unknown`, 不公式糊弄.
- Self-check: (1) 从哪行 SQL 跑? (2) 涵盖几行/几天真实历史? (3) 换 `unknown` 决策会不一样吗?
  (4) 用户能复现吗? 不能干净答 = estimate not measured, 不许提交.

### 1.3 真金白银 — 实盘投入

> 哲学 owner = `mio` skill 核心视角#8 (真金白银/后果优先: "不是数字游戏是真金白银投入的" / "上线后亏钱我睡得着吗" / 误用警告=裁决闸非探索挡箭牌)。本节留项目机械红线触发语 (资金安全, 项目+mio 双写冗余防非-mio session 失守)。

- 异常漂亮 = 警报不是兴奋: "leakage 影响估计 < 10%" → 0 leakage; "回测 +312%" → 立刻怀疑 leakage; 高后果项漏容忍 = 0.
- "先跑试试" → 跑前想清"出数能直接决策吗", 不能别跑.
- 目标穿透 (KPI owner = `goal.md`): 任何分数 improve 必须能回答"含 selection bias /
  leakage 吗? 真实 forward 期望多少?". 不能穿透 = 噪音.

### 1.4 失败先承认

> owner = `mio` skill 核心视角#6 (失败先承认/诚实面对负面证据两面)。项目应用: 反转 OOS 不及格 → 立刻换方向不留恋 ("不合格就是不合格"); 根因找不到 → 明说不伪装"已解决".

## 2. 接手前 First Actions

0. **invoke `chunkymonkey-ops` skill** (操作手册: 红线/坑库/工具调度/纪律/文档地图) + 读
   `docs/MASTER_TOPLEVEL_DESIGN.md` (全局架构骨架) — 先有全局再动手.
1. 读 `goal.md` + `docs/chunkyctl_session_quickstart.md` (启动契约);
   `SESSION_HANDOFF.md` 仅 context-only 快照, live gates 优先于它.
2. `git status --short` 看脏 worktree. 不擅自 revert 别人 (Codex / 上一 session) 的改动.
3. 优先 `rg` / `codegraph` / targeted test / read-only DuckDB inspection — 别盲猜.
4. 任务前 commit 当前 state, 防 race condition.

## 3. 编码风格

Owner: `AGENTS.md` Engineering Rules. 速查: 数值/阈值/路径/日期/表名走 yaml;
同逻辑 2 次抽公共 3 次必重构; 可 hardcode 仅数学常数/边界/测试 fixture/SQL LIMIT (写注释);
用户要全量真实数据不"快速验证小样本"; 数据告诉什么就报什么, 不报喜不报忧.

**探索沙盒 (2026-06-17 用户根治, owner=`sandbox/README.md` + docs/engineering_governance §Exploration Sandbox)**:
ephemeral 探索 (一次性 runner / findings 草稿 / 中间结果 / scratch 数据) **只住 `sandbox/`**
(`bash scripts/sandbox.sh new <exp>`), 绝不进 `backend/scripts/`、`analysis/`、`docs/`、主 DB —
否则探索散进主代码=反复污染 (本次清 ~100 文件的根因)。`sandbox/` gitignored 用完直接删
(`sandbox.sh wipe-all`); 唯一跨删存活 = 裁决写 `experiment_store.duckdb` (record_verdict); 真 edge
才干净重写进 `backend/services/` + 单测。moth `exploration-isolated-in-sandbox` 拦探索 runner 漏进 backend/scripts.
**promotion 纪律 (2026-06-21 立, 4+次隔离失守根治; 反例: 一条跑偏弧的产物[主库表/builder/控制面KPI/裁决]在方法确认前就 promote 进主项目, wipe sandbox 后主项目仍残留)**: 隔离=脚本进不去**不够**, **产物也不许漏**。探索弧期间(方法未确认)产物全留 sandbox — 派生数据→`sandbox/<exp>/scratch.duckdb`(不往主库建表), findings→`sandbox/<exp>/notes.md`(不往控制面文档写 KPI 结果), runner→sandbox(不进 backend/scripts); 唯一跨 sandbox 写 = `record_verdict`(confirmed_by_owner=0)。**promotion 是方法确认后单独 gated 步**: 真 edge 才重写进 backend/services+单测→主库→控制面引用→`record_verdict(confirmed_by_owner=1)`(带含成本+leakage证据)。**控制面文档只引 confirmed_by_owner=1 结论, 不嵌探索期(=0)结果**。机械门 `check_sandbox_isolation.py` (C1 backend引用sandbox=FAIL / C2 控制面嵌未promote=WARN / C3 探索runner漏主脚本=FAIL), wired into `sandbox.sh check` + `safe_commit` Step 3.8。

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
- tushare (vendor gateway): 171/239 接口实测可用; 间歇空响应/读超时 — writer 必须
  把 0 行当失败重试; 单页上限必须实测防静默截断 (top_inst 1000 整反例).
  **代理 2026-06-17 切 tinyshare** (旧 jiaoch.site 账户级反刷量墙弃用; tinyshare 自带网关, `import tinyshare as ts; ts.set_token(授权码); ts.pro_api()`, 授权码进 .env TUSHARE_TOKEN).
  **限流 (tinyshare, 用户 2026-06-17): 单接口 120 次/分钟, 多接口 200 次/分钟, 并发上限 2** — 瞬态限流退避几分钟即恢复 (非当日墙不停链, sync_runner 已实现); 旧 tushare 是 150/200.

### 4.4 Root Cause — 严禁忍

禁止: `try/except: pass` · `--skip-step` · `if env: bypass` · 钉死日期规避上游 bug.
必做: 找首次写坏路径修源头; 症状修复 (DELETE 坏行) != 根因修复, 两者都做;
找不到根因明说 + 加防御; 暂时绕过必须 TODO + 关联 commit, 不能伪装"已解决".

### 4.5 反例汇总

> leakage / 真金白银红线类反例留此 (最该 session 看见的); **操作类坑** (K线/DuckDB/sync/迁移/分层等)
> 详见 `chunkymonkey-ops` skill §2 坑库 + `analysis/project_state_ledger.md` (机器版全清单)。§8.2 #4 沉淀新坑两处都加。

- **in-sample fit 假象**: `mart_per_stock_stage_strategy_optimal` 全期 in-sample + `selector ORDER BY sharpe DESC` → paper_sim "+312%" 假象. 修: walk_forward.expanding_monthly + selector ORDER BY `COALESCE(oos_sharpe, sharpe)` + `governance.enforce_pre_insert` 拒 `walk_forward_mode='none'`.
- **systemic leakage**: v3.2 `stage_opt_per_stock` MAX(oos_sharpe) GROUP BY stock_code 给每 signal_date 用未来 Optuna (Codex acf48d35 critical, commit 5cc47987).
- **chain leakage (relative 红线)**: v3 102 features RankIC 0.0353 = relative +75% (未触 absolute 但触 relative), chain 含 inst latest-snapshot + sector 99.978% fallback leakage (Codex CRITICAL: PIT/leakage/真金白银 不折中, §11 存档条款).
- **含成本红线**: portfolio_backtest +45.4% 当最终决策 (不含 tx_cost/T+1); live 必须含成本 paper_sim, 加成本骤降.
- **selection bias**: `max_stocks=200` 按 code 排序 → 只取 00 深主板, 创业/科创/沪主 0 只参与 Optuna; 审计只查 DB 层 (5206 PASS) 没查 runner 实际加载数. 改: 全量 universe + 运行时 `validate_loaded_stocks` (板块覆盖+80%).
- **公式估算 != measured**: `swap_uplift_estimate` 公式估 → 实测 swap 拉低年化 33pp. 改真实 K 线 forward 反事实. (同类: vol-aware stop/ensemble weights/regime gate hardcode → 全进 Optuna search space + walk-forward OOS。)
- **行业 taxonomy 切源 = 桶定义变, 历史不可比 (2026-06-15, owner=analysis/industry_migration_tdx_to_sw_20260615.md)**: 切行业分类源 (通达信 13/56/76 → 申万 31/131/337) **不是 1:1 映射**, sector-relative 特征 (*_tdx_l1_rel) 的 PARTITION BY 桶数变 → 跨切换点历史特征/RankIC **不可比**, sector_momentum 板块集合变须全量重算。**禁跨 taxonomy 版本 partition/拼接**; 切换打 `taxonomy_version` 戳分段。另: 申万 index_member_all 默认只拉 is_new='Y' (当前成分) → out_date 100% NULL = **latest-snapshot leakage 变体** (行业切换历史丢失), 必须并拉 is_new='N' (历史剔除区间, out_date 填) 才是真 PIT (实测探针: Y 给当前 out_date空 / N 给历史 out_date填)。
- **universe 污染 = 实验直扫 K线无过滤 (2026-06-17, owner=services/universe.py)**: 旧 GT/yushen/rally 实验**直扫 price_kline 全部股**(含北交所 92x/83x + ST + 退市)做回测/选股 → 旧 fact_rally_ground_truth 含北交所 3.1% + 白名单内未滤 ST。根因: universe 过滤是可选 helper 非强制, 实验图省事不调。修 (用户决议升交易日历级硬真相源): `assert_universe_clean()` 硬门 (排除股进任何 GT/回测/选股集 = raise, 就像非交易日不能下单) + 三道门 (代码 `check_universe_filter` 拦内联白名单前缀绕过 / 数据 moth GT-0排除股 / 运行时 builder 调硬门) + PIT ST 日历 (raw_tushare_stock_st, 非 dim_active 当前名)。**任何股票集落地前必过 services.universe**, 内联 `('60','00','30','68')` = 第二真相源被门拦。
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

### 8.25 模型降级期标记 (2026-06-12, 反例: 两次降级期缺陷均靠事后全量复查抓)

会话模型从 Fable 5 降级 (如 Opus) 期间的每个 commit, message 加一行
`model-context: degraded`。恢复 Fable 5 后第一件事: `git log --grep="model-context: degraded"`
列降级期 commits 定向复查 — 重点抓字段语义/验收引用/时区口径这类"测试绿也挡不住"的
实测类错误 (反例: dc_member 方向反 7 测全绿 / UTC 误读虚构事故)。

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

**Side-agent 入库边界 (2026-06-12, 反例: agent 改 INDEX 把两条目嫁接成 Frankenstein)**:
side-agent 产出只许两种形态 — analysis/ 草稿 或 代码+测试; **禁改 PROJECT_INDEX.md /
goal.md / docs/ / CLAUDE.md** (控制面文件主会话独占)。主会话必须 review 后收编
(字段语义/真数据抽查, 不只看测试绿 — 反例: agent 判级 19 文件错 3 个 built_at 类,
亲核才翻案), INDEX 条目由主会话补写。

## 11. 协作与 commit 所有权

**2026-06-12 用户决议: Codex review 强制解除** (safe_commit Step 4.5 与
check_codex_review hook 均已非阻塞化, 信息性提示保留)。现行质量闸:
单测 + self-check 5 项 (§8.3) + 重大改动 (数据语义/策略/资金路径) 对抗复审 workflow
(本 session 实证: 三次对抗复审抓 3 HIGH + 字段方向反 CRITICAL, 有效性已验)。
历史条款存档: 若 Codex 复用, CRITICAL 涉 PIT/leakage/真金白银仍"完全接受+立刻修",
不许折中 (反例: 2026-05-15 折中致 chain leakage RankIC +60% 假象)。

## 11.5 Skills

| Skill | 触发场景 | 强制/建议 |
|---|---|---|
| **`mio`** | 任何判断/分析/架构/取舍的实质任务**开工前** (用户主管思路真相源: 真金白银/后果优先·真相源原教旨·失败先承认·measured-not-estimated·对抗同盟·执行前 grill·跨边界写确认) | **强制 — 实质工作前 invoke** |
| **`chunkymonkey-ops`** | 接手 / 任何实质工作前 (操作手册: 红线/坑库/工具调度/纪律/文档地图) | **强制 — 接手即 invoke** |
| `chunkymonkey-governance` / `engineering-discipline` (grill) | 执行计划前 (跑批 / Optuna / 新模块设计) | 强制 — 不 grill 不执行 |
| `plan_validator.enforce_optuna_plan()` | 跑批前 (代码级) | 强制 (2026-05-26 反例: 29/34 公式无 search space 白跑) |
| `/diagnose` | 硬 bug / 异常结果 / 性能回退 | 强制 — 不走诊断循环不猜 |
| `/lessons` | 改代码前查相关教训 | 建议 |
| 内置 `/tdd` `/handoff` `/to-issues` `architect-controller` | 对应场景 | 建议 |

执行前 grill 三问 (有效性≠可行性) 哲学 owner = `mio` skill 协议#8 + `chunkymonkey-governance`/`engineering-discipline`; 代码级强制 = `plan_validator.enforce_optuna_plan()`。三问: 跑完产出什么谁消费? 每步前提验证了? 成本 vs 产出合理?

## 12. 用户偏好 / 沟通

> 协作哲学 owner = `mio` skill (不报喜不报忧=谄媚死反模式; 接任务先 push back 简化=核心视角#4)。本节留项目沟通机械触发。

- 中文回复. 简洁实用. 表格 > 段落. 数字优先.
- 不报喜不报忧 — 0 STRONG_BUY / 数据滞后 / 测试 fail / Gate FAIL 先讲.
- 先讲业务结果 (年化/max_dd/超额), 技术次之.

## 附录

- 数据表 / 模块 / 命名陷阱 / 运行环境坑 → `PROJECT_INDEX.md` 活索引部分.
- KPI (年化>=30% / max_dd>=-20% / 超额 HS300>0 / 月胜率>=55%) owner = `goal.md`.
- 测试基线 / live gate 状态 → 跑 `scripts/chunkyctl doctor --fast`, 不引用文档里的旧数字.
