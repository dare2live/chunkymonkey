# Engineering Governance

> 状态：live
> 生效：2026-07-16
> 作用：本项目唯一工程执行手册。架构语义归 `MASTER_TOPLEVEL_DESIGN.md`，当前计划归 `goal.md`，研究发布归 `strategy_validation_contract.md`。

## 1. 权威与启动

新会话按以下顺序建立 live truth：

1. `AGENTS.md`；
2. `goal.md`；
3. `git status --short --branch`；
4. `moth snapshot --repo .`；
5. `codegraph status .`；
6. 按任务读取本目录中的唯一 owner 文档；
7. 用只读 DB/API/命令验证易漂移事实。

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

代码提交适格与实时数据就绪是两个状态机。`safe_commit.sh` 必须校验 live continuity 的 JSON、扫描面、交易日锚、计数和退出码一致性，并原样报告 `READY / DEGRADED / UNVERIFIED / BLOCKED`；空扫描、skipped、库不可达或 WARN 都不得冒充 READY。verifier 崩溃/坏报告仍阻断提交，但有效的供应商/数据库数据 FAIL 只阻断数据更新、下游消费和发布，不得造成“数据坏了，所以修复代码也无法提交”的死锁。任何非 READY 状态下，提交都不等于 Tier0 就绪；continuity 直接检查、故障队列、ALERT、日更管线以及 doctor 对现存 flag 的读面继续保持非绿，直到真实数据修复并单独复验。

复杂度扫描是线索，不是判决。只有在真实数据规模、调用路径和最小复现证明后才修改性能热点。

## 5. 测试工具有效性

引用任何测试绿之前，记录：

- 精确命令和命中的文件；
- scope 是 unit、contract、integration 还是明确 opt-in 的 realdb/network/perf；
- fixture 是否使用当前真相源和真实 schema shape；
- 是否会写共享 DB/输出；
- 是否能用坏例变红，是否只是自洽/过度 mock；
- 是否把 warn/proxy/empty selection 当 PASS。

当前真相源：交易日在日历；名义 K 线/公司行动是交易事实；universe 规则在 `universe_rules.yaml`；分类必须带 namespace/version；`dim_active_a_stock` 只可作身份/名称/cache，不是历史可交易性真相。

先跑最窄测试，再按 blast radius 扩大。默认测试使用 DuckDB memory fixture；真实 DB 测试必须显式只读或串行写窗口。

## 6. 数据与数据库纪律

- 审计默认 `read_only=True`；大表先聚合、抽样或 LIMIT；
- landing 不做 universe/business filter；过滤发生在 canonical/serve，并保留 reason；
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
scripts/chunkyctl doctor --fast
scripts/chunkyctl sync --domain DOMAIN [--drain --max-dates N]
scripts/chunkyctl sync --domain DOMAIN --backfill --start YYYYMMDD --end YYYYMMDD
bash scripts/daily_update.sh --date YYYYMMDD
```

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

## 13. Rule 10 与交付

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

不 `--no-verify` 绕门，不 amend 已 push commit，不在未授权时 push。交付报告必须区分 `FIXED/PARTIAL/BLOCKED`，列 residual、验收命令和 owner。
