# Engineering Governance

> 状态：live
> 生效：2026-07-16
> 作用：本项目唯一工程执行手册。架构语义归 `MASTER_TOPLEVEL_DESIGN.md`，当前计划归 `goal.md`，研究发布归 `strategy_validation_contract.md`。

## 1. 权威与启动

新会话按以下顺序建立 live truth：

1. `AGENTS.md`；
2. `goal.md`；
3. `scripts/chunkyctl agent-boot`（一页聚合 git status + Moth 摘要 + CodeGraph 状态 +
   生成板 `BOARD.md`/`data/board/agent_context.json`；只读投影，非执法输入）；
4. 按任务读取本目录中的唯一 owner 文档；
5. 用只读 DB/API/命令验证易漂移事实。

需要完整工具证据时仍可单独跑 `git status --short --branch`、`moth snapshot --repo .`、
`codegraph status .`；agent-boot 只是聚合入口，不替代任何 gate 的原始输出。

`analysis/project_state_ledger.md` 只用 `rg`/`tail` 查询历史。旧 session handoff / workflow checkpoint 体系已退役；恢复状态必须重新读取 git、Moth、CodeGraph 和 live data。`CLAUDE.md` 是 legacy Claude 文件，Codex 默认不读。

## 2. 必用 skills

| 场景 | Skill |
|---|---|
| 判断、架构、取舍 | `$mio` |
| 广泛架构、模糊拆解、多 agent 总控 | `$architect-controller` |
| ChunkyMonkey 非平凡执行、数据/策略/PIT/删除/门禁 | `$chunkymonkey-governance` |
| Debug、TDD、交接 | `$chunkymonkey-debug-delivery` |
| `.py/.yaml/.sql` 交付或提交前 Rule 10 | `$chunkymonkey-review-gate` |
| Codex 本地配置、skills、hooks、插件、启动项 | `$codex-local-ops` |

Skill 输出是方法约束，不替代 live repo evidence。子 agent 输出是候选证据，controller 负责裁决。

## 3. 开工前 Grill Gate

任何大重构、跑批、寻优、迁移或付费操作先回答：

1. 产出是什么，谁消费，不做会怎样；
2. 输入快照、数据覆盖、参数、依赖和退出条件是否已经验证；
3. 最便宜的证伪方式是什么；
4. 失败/中断如何回滚，已完成 checkpoint 如何复用；
5. 哪个 gate 能把“能运行但无价值”拦住。

无法回答则不执行。provider 任务只允许经 Grill Gate 后从明确手工入口启动；禁止自动、
隐式或后台启动，也不把不存在的 backend 写成 active config。

## 4. CodeGraph、Moth 与复杂度

非平凡审计或改动：

```bash
codegraph status .
codegraph explore "<task or symbols>"
```

索引陈旧或代码修改后：

```bash
codegraph sync .
```

Moth 用于共享工具状态和证据路径：

```bash
moth snapshot --repo .
moth assert --repo .
moth coupling --repo . --impact <name>
```

Moth 0.3.0 会从进程 cwd 解析 profile 的相对 `repo_path`。因此必须先 `cd` 到待验仓库或
staged snapshot，再统一使用 `--repo .`；禁止留在主工作树中用绝对 `--repo /tmp/...`
代验，否则 assertion、evidence path 和 CodeGraph 可能静默回到主工作树。`safe_commit.sh`
已把这一 cwd 约束做成 exact-index gate。

Moth PASS 只有在 verifier 自身能红、没有 warning 被 regex 洗成 PASS 时才是证据。业务门仍由项目脚本/配置/表拥有，不能搬进 Moth 重写第二套规则。

代码提交适格与实时数据就绪是两个状态机。**2026-08-11（P1 门重新分布，§14.1）把这条分离做彻底了**：live continuity 不再在 commit 路径上评估，改由 `daily_update` 的 `system_health` 自检拥有（`chunkyctl gates --run-system-health` 是等价手动入口）。原因是它从来就不是关于「这次 diff 对不对」的判断 —— 之前只是「顺手在 commit 时查一下」，代价是每次 L3 提交都要全库扫描，且制造过「数据坏了所以修复代码也难提交」的张力。

自检必须校验 live continuity 的 JSON、扫描面、交易日锚、计数和退出码一致性，并原样报告 `READY / DEGRADED / UNVERIFIED / BLOCKED`；空扫描、skipped、库不可达或 WARN 都不得冒充 READY。有效的供应商/数据库数据 FAIL 阻断数据更新、下游消费和发布。任何非 READY 状态下，提交都不等于 Tier0 就绪 —— 现在这句话更硬：**commit 根本不再产生任何 readiness 声明**。continuity 直接检查、故障队列、ALERT、日更管线以及 doctor 对现存 flag 的读面继续保持非绿，直到真实数据修复并单独复验。GitHub CI 只跑离线 contract/unit 门（`requirements-ci.txt` + `check_universe_filter --skip-live-readiness`）；live continuity / DuckDB readiness 仍只在本地评估，不得把缺库 IOException 写成 CI 红灯。

复杂度扫描是线索，不是判决。只有在真实数据规模、调用路径和最小复现证明后才修改性能热点。

## 5. 测试工具有效性与边做边测

切片交付强制循环（与 `goal.md` 一致）：

```text
坏例先红 → 最小实现 → 红变绿 → 窄回归 → 挑战 verifier →（若动 PIT/schema/writer）stale 审计
→ 标 FIXED | PARTIAL | BLOCKED
```

禁止：先实现再补自洽测；mock 掉被测的 calendar/universe/population 门让 pipeline 绿；把 Moth/doctor
静态 PASS 或 grep 符号存在当成 Tier0 READY；绿了不做 shadow/stale 就切消费者。

引用任何测试绿之前，记录：

- 精确命令和命中的文件；
- scope 是 unit、contract、integration 还是明确 opt-in 的 realdb/network/perf；
- fixture 是否使用当前真相源和真实 schema shape；
- 是否会写共享 DB/输出；
- 是否能用坏例变红，是否只是自洽/过度 mock；
- universe 坏例是否同时覆盖 BSE/新老三板、t 日 ST、t 日已不合格、未来才退市，且未来状态不得
  改写过去结果；exchange aggregate 与逐证券 project universe 必须用不相等反例证明没有混称；
- 是否把 warn/proxy/empty selection 当 PASS。

当前真相源：交易日在日历；正式日级 universe 用 t 日名义 K 线 + t 日 ST + venue/board policy；
90 日 K 线窗口只可用于 legacy 当前枚举。分类必须带 namespace/version；`dim_active_a_stock` 只可作
身份/名称/cache，不是历史可交易性真相。

先跑最窄测试，再按 blast radius 扩大。默认测试使用 DuckDB memory fixture；真实 DB 测试必须显式只读或串行写窗口。
Phase 出口或提交前再放大到相关联合回归 / 全量 backend / `moth assert` / `codegraph sync`。

## 6. 数据与数据库纪律

- 审计默认 `read_only=True`；大表先聚合、抽样或 LIMIT；
- landing 不做 universe/business filter；过滤发生在 canonical/serve，并保留 reason；
- 每个正式数据集声明 `raw_evidence`、`external_aggregate` 或 `project_universe_pit` population scope；
  缺 policy id/version/hash 或用 external aggregate 冒充项目股票池时 fail closed；
- 交易日历与 universe policy 从同一次执行快照派生并贯穿 fetch/validate/accept/audit/consumer；
  禁止 runner 与下游各自重读 YAML、内联前缀或用今天的退市/ST 状态清洗历史；
- 每个发布数据集一个 writer；其他模块只读公开契约；
- 同一 DuckDB/表/输出目录的写入必须串行；
- 先 stage/validate，再在一个可证明的事务边界内发布数据与 accepted partition；
- 0 行、空响应、权限页、字段缺失、超时、连接失败分别分类；不得用 0 行冒充成功；
- historical decision 必须显式 as-of/available-at；缺失传播 `NULL/unknown`，禁止 latest/0/demo fallback；
- 修 writer/schema/PIT 后必须检查旧表、cache、JSON、watermark、报告、前端和后台进程残留。

## 7. 配置与 hardcoding

| 内容 | Owner |
|---|---|
| 稳定阈值、窗口、优先级、启停、资源政策 | typed YAML/config |
| 观测事实、分类成员、运行状态、血缘、验收、实验结果 | 数据表或不可变 artifact |
| 验证、访问顺序、fallback、公开 API | service/module |
| fixture、数学常数、schema/enum、DDL、小型实现细节 | Python 可接受 |

配置必须有类型校验和未知键拒绝。禁止 YAML 编程语言、动态代码拓扑、计划中的假 backend 和状态回写。一个规则只能有一个 owner，不得同时复制在 Python、YAML、SQL 和文档。

## 8. 并行与 controller

默认并行只读调查、独立测试和不冲突的文件切片。Controller 保留：

- 产品/架构/真相源裁决；
- 共享 docs、AGENTS、goal、Moth profile；
- DuckDB 写窗口、运行任务、提交和最终验收；
- agent patch 的逐文件复核。

不得并行：同一文件/配置、同一表/库/输出目录、同一实验 study、最终 merge/commit。Agent 必须知道工作树有其他改动，不得 revert、stage 或 commit 他人内容。

## 9. Dirty worktree

每次修改前后运行：

```bash
git status --short --branch
moth snapshot --repo .
```

先删除有证据的生成残留（`.DS_Store`、源码树内的 `__pycache__`、`.pytest_cache`、`.pyc`）。清理命令必须显式 prune `.venv/`、`node_modules/` 和其他依赖/runtime 目录；TinyShare 的 SDK 本体就是版本化 `.pyc`，把全仓 `*.pyc` 当缓存会在 metadata 仍存在时破坏采集入口。其他未知文件不删、不还原。按 owner 切片审查并显式列出文件；禁止 `git add .`。

如果有他人/前序未提交改动：

1. 冻结其文件清单；
2. 新工作使用不相交写入面；
3. gate 报告区分 pre-existing 与本次引入；
4. 只有 owner 和证据清楚后才分别 stage/commit。

## 10. 删除与目录整理

删除前必须同时检查：

```bash
moth coupling --repo . --impact <name>
codegraph explore "<name> callers consumers owners"
rg -n "<name>" backend scripts docs analysis .moth
```

并检查 service/router、config、治理脚本 SQL 字符串、tests、docs/Moth/skills 五类消费者。确认替代 owner、历史证据迁移和最窄回归测试后才真删；禁止 renamed-dead、注释墓碑、空 stub 和 archive-of-archive。

项目文档只分三类：

- live authority：`AGENTS.md`、`goal.md` 和 `docs/README.md` 列出的 owner；
- generated/read model：`FEATURE_MAP.md` 等可重建地图；
- historical evidence：`analysis/project_state_ledger.md` 或必要的不可复现实证。

过期计划和普通叙述由 git history 保留，不另建 archive 目录。

## 11. 数据更新与自动化

ChunkyMonkey 数据更新模式为 `manual_only`。禁止安装或保留项目数据 cron/launchd/隐藏后台触发器。受支持入口：

```bash
scripts/chunkyctl agent-boot
scripts/chunkyctl doctor --fast
scripts/chunkyctl sync --domain DOMAIN [--drain --max-dates N]
scripts/chunkyctl sync --domain DOMAIN --backfill --start YYYYMMDD --end YYYYMMDD
bash scripts/daily_update.sh --date YYYYMMDD
```

`agent-boot` 是只读会话启动聚合（git/Moth/CodeGraph/生成板一页），不触碰数据、
不安装调度，也不产生任何 readiness 声明。

单域修洞、回放和 canary 只走 `chunkyctl sync`；它加载项目 provider 环境并复用生产
runner 的授权、交易日历和 writer lock，不是第二套采集逻辑，也不会安装调度。全链验证仍走
`daily_update.sh`，单域成功不能替代下游 Tier0 blocking、SLA 和 consumer gate。

显式回放边界必须在 provider adapter、目标数据库和 writer I/O 前经过与默认/drain 相同的
eligibility resolver；未来 partition fail closed。`--drain` 不得混用 start/end/backfill/resume，
on-demand by-security 必须同时给出 start/end，full-refresh 不接受日期边界。formal transport 的
batch mode、date parameter、write mode 与分片列表也必须在 calendar、writer lock 和授权探针前
完成静态合同验证。历史 `--end` 只限制本次 operation window，输出状态和 accepted-state
projection 仍保留 live eligibility frontier。该入口是手动命令，不创建调度器。

一次执行只能从一个 registry snapshot 生成一个 immutable contract 对象；accept/recover、state、
reconcile/projection、pipeline 与 continuity/SLA 必须透传同一对象。测试应使用 `is` 证明 identity，
不能用 dataclass 值相等制造“同一合同”的假绿。

accepted-state/readiness/reconcile 必须从同一个 immutable evidence snapshot 消费正式事实。快照读取
按物理 surface 做 set-based 查询，主库查询数不得随 partition 数增长；schema inventory 查询故障、
表/列缺失和 0 行必须保持不同 failure taxonomy。声明为单分区的快照不得夹带其他分区；公开
reconcile、accepted-state 和 readiness API 不接受 snapshot、proof、旧 state 或其他绕过参数，
只能从传入连接现场加载。快照复用只允许发生在同一调用栈的私有 helper 内。权威 proof 由 state
owner 逐分区裁决并一次复用已验证的
trading-session index，使坏分区不能污染好分区。规模门同时检查固定 query count、calendar
operation count 和坏例 turning red，不能只用小样本耗时作证。

readiness 属于 state 与 reconcile 之上的 orchestration，必须单向依赖二者；state/proof 层不得
反向 import reconcile。拆文件后要检查静态依赖图，不得用 function-local import 掩盖循环。

裁决分两层：单域 canary 只由该域的 accepted partition、reconcile、projection 与 failure
证据裁决；full pipeline、消费者切换和发布仍受全局 continuity/SLA/failure 阻断。全局告警
不能洗绿单域，也不能反向抹掉已经闭合的单域证据。

`daily_update` 的 store 阶段跑 **system_health 运行时自检**（owner =
`backend/config/governance_gates.yaml` 的 `runtime_checks`，见 §14.1）：continuity、
residual_hygiene、grain_uniqueness、cutover_effective、lineage_catalog。FAIL =
degraded + 续跑 + 写 flag，绝不静默；`--dry` 只跳过声明了 `skip_when_dry` 的重扫描项。
手动等价入口 `scripts/chunkyctl gates --run-system-health`。

执行前验证交易日历、授权、writer lease、目标 partition、源 availability 和成本；执行后查真实表内容、accepted state/watermark、failure queue 和 ALERT。进程 exit 0 不是数据齐全证明。

脚本存在不等于正在自动运行；判断自动化必须查 launchd/cron/launchctl/installer/registry 的真实 fan-in。

## 12. 文档门

活文档必须满足：

- 只有一个 owner；本地 Markdown 链接和命令真实存在；
- 不引用 retired CLI 或已删代码作为现行步骤；
- `check_doc_governance` 同时 `fails=0` 且 `warns=0`；
- `check_doc_drift` 无 stale；
- 生成地图区分 active/retired，不从帮助文字猜生命周期；
- goal 只写当前 objective/blocker/plan；历史移 ledger。

验收命令：

```bash
PYTHONPATH=backend python backend/scripts/check_doc_governance.py
PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check
```

## 13. Agent-OS 双轨政策与仪式影子期（WP6）

Agent-OS 核心面（WP0–WP4）已落地：tiered `safe_commit`、生成板、
`chunkyctl agent-boot`、AGENTS 瘦身。旧仪式与新表面**影子并行**，切换是真相
裁决，不是 DX 快捷键。

| 项 | 政策 |
|---|---|
| 影子期起点 | WP4 land = `be8efc6f` / 2026-07-20 |
| 影子期长度 | **10 个工作 session 或 14 天（先到者）** |
| 新表面（现行） | `agent-boot`；`BOARD.md`/`agent_context.json`；L1/L2/L3 tiers；AGENTS ≤100 |
| 旧路径（影子保留） | 手拼 `git`+`moth snapshot`+`codegraph status` boot；手抄 goal 状态段 |
| 仪式 cutover 条件 | 门覆盖 parity 机器 diff 为空（每门在其触发面仍可红）**且**影子期无真相回归 |
| 任一回归 | 影子期重置；必要时回退 L3 全门 |
| **不在本政策内** | B-pit/C **数据面** `cutover_allowed`（继续冻结；与仪式切换无关） |

WP5 shared DuckDB memory fixture pack：**Occam 跳过**——无 WP1 基线证明测试建库
是显著耗时热点；需要时再开，不预建。

轨道关闭（agent-OS track CLOSED）仍要求停机检查单全绿（见 ledger / goal 残余），
含：影子期结束 + 旧仪式真删 + T0 墙钟实测入账 + 一次真实 L2/T2 路径证据。
A→H 冻结已由 owner 于 2026-07-20 解除（核心 WP0–WP4 闭合即恢复 A→H 刀；
影子期/仪式 cutover 残余照常，与 A→H 互不阻塞）。

## 14. Rule 10 与交付

**Rule 10 是纪律，不是闸门（2026-08-10 裁决）。** commit-msg 门只阻断显式的
`Codex-Reviewed: REQUEST_CHANGES`（否定裁决有信息量 —— 没人会「忘记」写它）；
缺 `APPROVE` 只提示不阻断。理由：该门**判定「审查是否发生」的唯一依据**，是提交
者自己写的 message 里一行正则匹配 —— 它无法验证审查是否发生、审查者是谁、是否
独立。（精确化：该门确实也读 staged 文件与 tier 分类，但那只决定**是否需要**审查，
从不用于验证审查本身；被降级的是后者。）按
本文件自己的判据（committer 自写 justification + 无复核 = 摆设），把它当红线有
三个坏结果：挡不住不做审查的人（补一行字即过）；只挡住不愿假称审过的诚实提交
者；并制造「L3 都经过独立审查」的虚假保证 —— 比不检查更糟。

**通用原则：一件事若无法机器验证，就不要用机器门假装验证它 —— 写进规则，别写
进闸。** 真要强制独立审查，enforcement 必须落在提交者够不到的地方（CI / PR 侧
reviewer）；本地 hook 天然做不到。同批裁决：`check_commit_message.py` 的
GROUP A/B/D 关键词同属自述型，一并降为提示（subject 长度仍阻断 —— 长度是客观
事实）。安全性不受影响：PIT / leakage / continuity / lineage / population /
calendar 等门读的是代码与数据，提交者无法用措辞影响它们。

**未竟事项（不得当作已解决）**：本次只做了「拆除」没做「替代」。旧门虽假，至少
给诚实提交者一个约束信号；补位方案（CI 侧强制 reviewer —— 落在提交者够不到的
地方）尚未落地。在它落地前，L3 独立审查**完全依赖执行者自觉**遵守下面的纪律。

**程序缺陷记录（2026-08-10）**：拆除该门的那一刀（`337e9346a`）由提交者自审自
merge，理由是「用被拆的门卡拆门本身没有意义」—— 独立审查指出这是**循环论证**，
且提交者对该改动存在利益冲突（它让提交者自己更易提交），成立。事后已补做独立
审查，verdict `REQUEST_CHANGES`，三条 finding 中两条采纳并已修正（措辞精确化、
本节未竟事项）。教训：**改变审查规则本身的改动，最需要独立审查，不能以「与被改
的规则冲突」为由豁免** —— 正确做法是先审后改，或改完立即补审并记录。

下面的流程仍是**必须遵守的交付纪律**，只是不再由 commit-msg 门代为强制。

`.py/.yaml/.sql` 或高风险文档/删除切片完成后：

1. 查看 scoped diff；
2. 用 `$chunkymonkey-review-gate` 做独立审查；
3. 修复 blocking finding；
4. 跑目标测试、Moth、CodeGraph sync、`git diff --check`；
5. 显式 stage 文件列表；
6. 需要本地提交时使用：

```bash
SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<message>"
```

`safe_commit.sh` machine-classifies staged paths into L1/L2/L3
(`backend/scripts/classify_commit_tier.py` + `backend/config/commit_tiers.yaml`).
Agents cannot self-downgrade; unknown/deletion/bad policy → L3 full gates.
L1 = docs/analysis/sandbox light gates; L2 = tests/routers/frontend + Rule 10;
L3 = writer/PIT/schema/config/deletion = current full gate set.

### 14.1 门的分布：按「谁受害、何时受害」分组（2026-08-11 P1 落地）

tier 剪枝管**跑不跑**，分组管**跑红了会怎样**，两维正交。owner =
`backend/config/governance_gates.yaml`（唯一存放处；`gate_policy.py --check`
把它与 `classify_commit_tier` 和 `safe_commit.sh` 的 fail-closed 兜底串三处对账）。

| 组 | 判据 | commit 路径 | 归属 |
|---|---|---|---|
| `diff_correctness` | 这次 diff 本身错 | **阻断** | rule_compliance / ci_pytest / sandbox_isolation / serve_read_layer / calendar_usage / population_contract / lineage_drift / dead_references / config_refs / rule10 |
| `system_health` | 数据 / 策略 / 钱受害 | **不跑** | grain_uniqueness / continuity → `daily_update` store 阶段自检 |
| `scaffold` | 下一个开发者受害 | **warn-only** | project_index_sync / feature_map / agent_board / moth / doc_drift / doc_governance / commit_msg |

判据来自实证而非偏好（2026-08-10 审计，见 ledger 同日条目）：脚手架门本轮 3 次
阻断系统修复提交（文档没同步挡住代码 bug 修复），而 b_pit cutover 是否仍生效 ——
一个纯运行时事实 —— 被装在 commit 路径上，于是「有没有人恰好提交相关代码」决定
了它何时被查，系统跑了 13 次没人查。**受害时刻在运行时的检查，就不该装在 commit
时刻。**

约束（不可放宽）：

- 分组只改变**后果**，从不删除检查。`system_health` 组的每道门必须挂在
  `runtime_checks` 上，否则 `load_registry()` 直接抛错（拒绝「从 commit 摘掉却
  没人接手」）。
- fail-closed：策略文件不可读 → 所有门按 `diff_correctness` 阻断（= 改动前行为）。
- warn-only 不是放水：scaffold 未闭合项在 commit 尾部一次性列全，配套批量收口
  入口 `scripts/chunkyctl scaffold-fix`。
- always-on 的 `ci_surface_drift`（Step 3.35）不参与分组，任何 tier 都阻断。
- 静态 PASS 仍不升级 live readiness：commit 通过 ≠ Tier0 数据就绪。

```bash
scripts/chunkyctl gates                      # 人读分组表
scripts/chunkyctl gates --check              # 三处门名对账
scripts/chunkyctl gates --run-system-health  # 手动跑运行时自检组
scripts/chunkyctl scaffold-fix               # 脚手架批量收口
```

不 `--no-verify` 绕门，不 amend 已 push commit，不在未授权时 push。交付报告必须区分 `FIXED/PARTIAL/BLOCKED`，列 residual、验收命令和 owner。

## 15. 编排与墙钟政策（delivery tax；不触碰真相门）

T0 基线（2026-07-20 实测，非估算；证据=ledger 同日条目）：L1 commit 门 1.6s、
L2 17.0s（moth assert 7.3s + staged-snapshot cold codegraph 3.8s）、L3 27.1s、
`agent-boot` 11.6s（moth snapshot ≈7s）、CI 单次 ≈1min。结论：机械门不是墙钟
瓶颈；瓶颈在编排仪式（每 slice 同步等 CI、每 micro-commit 一次 Rule 10、
父 agent 串行单 worker）。本节只裁编排节奏，**不放宽任何
accept/PIT/calendar/fail-closed/cutover/E 门 / Rule 10 / ≤40d 语义**。

### 15.1 刀级合并（binding；防 micro-commit 复辟）

**刀** = 一个逻辑单元（例：单域 S7 formal|sunset、单 CLI 面、单 E0 域 land
路径）。刀内允许多文件、一次 stage、**一次 Rule 10**、**一次
`safe_commit`**。禁止把同一刀拆成「docs commit → 小改 commit → 测 commit」各
审一次。验收信号：`commits/knife ≤ 1.5`；ledger 条目写明刀边界。

- **异步 CI（pipelining，不是放松）**：L2/L3 本地先绿同一 **blocking** pytest 面
  （`backend/config/ci_pytest_surface.yaml` via `run_ci_pytest.py --tier blocking`，亦是
  `safe_commit` `ci_pytest` 门）再 push；push 后**禁止**同步 `gh run watch` /
  空等 CI；可开下一刀。刀收口前回读该刀 CI verdict（或上一刀已结束 run）；红 =
  fix-forward 最高优先并暂停派新刀。同步等待只保留给改 CI/gate 机械本身的切片。
  `nightly_paths` 不进 commit 门（`--tier nightly|all` 手动/未来 schedule）。
- **并行 subagents**：仅当写集不相交且不碰共享真相文件时，父可并行派
  disjoint 刀；派前用 `moth coupling --repo . --impact <name>`（或
  `chunkyctl pre-knife`）证明非重叠。**必须串行的共享面**：
  `goal.md`/`BOARD.md`/ledger/`PROJECT_INDEX.md`/`AGENTS.md`/docs owner 三文档/
  `.moth/`/`commit_tiers.yaml`/`safe_commit.sh`/`ci.yml`；同一 DuckDB 的写；
  provider 采集 job；git stage/commit/push 窗口；Rule 10 verdict 与最终验收
  （controller-owned）。机器话：两把刀的 `git diff --name-only` 预期集合相交，
  或任一方触本清单 → 串行。
- **Rule 10 节奏**：独立审查按**刀**一次，覆盖该刀合并 diff；不按
  micro-commit 重复开审。审查仍 blocking（L2/L3 trailer 语义不变），只是粒度
  归刀。
- **薄 enforcement（非 Delivery-OS 重写）**：`chunkyctl agent-boot` 投影提醒本
  节；`chunkyctl pre-knife <name>` 固化刀前审计；不新增第二套 commit OS，不软化
  L3/Rule10/PIT/≤40d。

### 15.2 刀前 impact 审计（L3 mandatory）

动 `backend/services/`、YAML/SQL 契约、删表/删配置前，**固定一次**：

```bash
scripts/chunkyctl pre-knife <name>
# 等价：moth coupling --repo . --impact <name>
#       codegraph explore "<name> callers"
```

再配最窄 pytest red-first。一次规划、一次绿；禁止「连崩多层 CI / 多 commit
才绿」。刀后若动 PIT/schema/writer，仍走 `$post-fix-audit`（本清单不替代）。

### 15.3 其它节奏

- **owner 文档读取**：每任务读一次 `docs/README.md` 指到的 owner 文档；同任务
  内后续刀不重读 MASTER 全文，引用具体条款即可。
- **显式不做（Occam，有测量背书）**：`agent-boot --fast`（只省 ~7s/session，
  moth 状态是 boot 的价值本体）；L2 门集手术（17s 非痛点，动 `commit_tiers.yaml`
  = L3+审查+影子期 parity 风险）；CI concurrency/cancel 机械；T0 自动测量 hook
  （一次 ledger 实测入账即闭合 WP6 该残余，复测按需手跑）。
- CI 对 L1 docs/board-only push 不再起跑：`ci.yml` `paths-ignore` 镜像
  `commit_tiers.yaml` L1 面（policy owner），子集关系由
  `backend/tests/scripts/test_ci_paths_policy.py` 机器守护。
