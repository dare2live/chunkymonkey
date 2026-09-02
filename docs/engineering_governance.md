# Engineering Governance

> 状态：live
> 生效：2026-07-16
> 作用：本项目唯一工程执行手册。架构语义归 `MASTER_TOPLEVEL_DESIGN.md`，当前计划归 `goal.md`，研究发布归 `strategy_validation_contract.md`。

## 1. 权威与启动

新会话按以下顺序建立 live truth：

1. `AGENTS.md`；
2. `goal.md`；
3. `scripts/chunkyctl agent-boot`（一页聚合 git status + Moth 摘要 + CodeGraph 状态 +
   board 现查投影（零文件，`agent_board_projection.py`）；只读，非执法输入）；
4. 按任务读取本目录中的唯一 owner 文档；
5. 用只读 DB/API/命令验证易漂移事实。

需要完整工具证据时仍可单独跑 `git status --short --branch`、`moth snapshot --repo .`、
`codegraph status .`；agent-boot 只是聚合入口，不替代任何 gate 的原始输出。

历史查询用 `scripts/chunkyctl history --grep`（git log 是原件，永不断档）/ `--eras`（时期导航）。旧 session handoff / workflow checkpoint 体系已退役；恢复状态必须重新读取 git、Moth、CodeGraph 和 live data。`CLAUDE.md` 是 legacy Claude 文件，Codex 默认不读。

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

### 3.1 什么时候**不该**开刀

Grill Gate 问「这一刀值不值」，这一节问更前面的问题：**它算不算一刀**。

**ops 残差默认不是刀。** 软等时钟、continuity 非 READY、日更 DONE 但 degraded —— 这些是
**观测**，不是待办。只有满足三个触发之一，才允许为它开 foundation/product 代码刀：

1. owner 新下的 block；
2. 能**指名道姓**的消费方（谁在用、哪一步用不了）；
3. 轴①（数据正确性）本身的 gate 失败。

开刀前必须引用其中一条。引用不出来还开，就是拿「清清单」冒充推进 —— 与 §9.1 的
「禁止为了清清单而清残留」是同一条法的两端：那边说残留不都该清，这边说清残留不都算干活。

**判断死 guard**：如果「下一个动作」既不是「用产品」、也不是「观测 ops 时钟」、也不是
「owner 排期的研究」，那它默认是**假推进**。写下来是因为假推进最难自我察觉 —— 它看起来
一直很忙。

**重构时区分「物理护栏」与「历史习惯」。** 物理护栏是违反了会亏钱或出错的约束（PIT /
`available_at` / fail-closed Tier0 / A 股时钟与停牌涨跌停 / 单 writer / `manual_only` /
commit-green ≠ 数据 READY / 各项禁令 / §15 刀级合并），**重构中一条都不能烧**；历史习惯是
当时那么写的交互与措辞，**该改就改**。把两者混为一谈会导致两种错误：要么因为「一直是这样」
而不敢动该动的，要么以「重构」为名烧掉真护栏。分不清时，判据是：**违反它，坏的是数据/钱，
还是只是不好看？**

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

代码提交适格与实时数据就绪是两个状态机。**2026-08-11（P1 门重新分布，§13.1）把这条分离做彻底了**：live continuity 不再在 commit 路径上评估，改由 `daily_update` 的 `system_health` 自检拥有（`chunkyctl gates --run-system-health` 是等价手动入口）。原因是它从来就不是关于「这次 diff 对不对」的判断 —— 之前只是「顺手在 commit 时查一下」，代价是每次 L3 提交都要全库扫描，且制造过「数据坏了所以修复代码也难提交」的张力。

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
- **数据集契约本身**（landing 不过滤 / 三类 population scope / 一次执行一份快照贯穿全链 /
  一个 writer / 同库写串行 / `stage→validate→publish→accepted`）见 `MASTER §5.1` `§6.1`，
  本节不再复述。仍在本节的例外：**历史上某天 t 的退市/ST 归属不得被 t 之后的状态变化重写** ——
  MASTER §5.1 覆盖的是「退市不得反删过去交易日」与「ST 不作为池排除名单」，与这条时间不可变性不是同一条；
- 0 行、空响应、权限页、字段缺失、超时、连接失败分别分类；不得用 0 行冒充成功；
- historical decision 的 as-of / 禁 0-fallback 已升为系统语义，见 `MASTER §6.1`；
- 修 writer/schema/PIT 后必须检查旧表、cache、JSON、watermark、报告、前端和后台进程残留；
- **DuckDB 的 `DROP`/`DELETE`/`UPDATE` 不释放文件块，单独 `CHECKPOINT` 也不缩小文件** —— 它只刷 WAL/catalog。
  批量 DROP（lifecycle/purge）以及增量路径上的整表 DELETE/UPDATE 之后必须显式跑
  `python backend/scripts/db_compact.py --db <alias> --execute`（ATTACH-copy 重写 + row/constraint/index
  parity 校验），核对 parity 通过再删 `*_precompact_bak.duckdb`。**「我 CHECKPOINT 过了」不等于空间已回收**
  —— 这个误解会让库只增不减。`file_size` 棘轮（9→12→15GB）看不见死块；要测
  `pragma_database_size` 的 `free_blocks/total_blocks`。  `build_price_kline_qfq_tushare`
  全量重建后必 compact，增量在死块占比 ≥10% 时也 compact（逃生 `--no-compact`）；
  禁止假设「增量不是 DROP+CTAS 所以没有死块」—— 实测一次全表 UPDATE 就把 market
  从 0.03% 打回 ~25%。`feature_store` 的 `institution_profile.rebuild_all` /
  `rally_gt.rebuild` 是 DROP+CREATE+CHECKPOINT 同型，重建后必须 compact
  （`services.duckdb_compact.maybe_compact_alias`）；其余批次仍需手动补跑。`tushare_raw` 是 Tier0 写面，随时可能被写入，
  compact 前需显式 owner 判断，不进日常回收流程，但 moth 仍应告警死块占比。

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

### 9.1 残留分类：先问「为什么还在」，再决定动不动

**禁止为了清清单而清残留。** 看到一条残留，先回答一个问题：**日常更新会不会再产生同一个
错误？** 答案决定它属于哪一类，也决定它该不该被动。

| Class | 含义 | 处置 |
|---|---|---|
| **A 流程债** | 日常更新**会再产生**同样的错误 → 路径/边界本身错了 | **必须改**（改的是产生它的那条路径，不是清掉这一次的产物） |
| **B 诚实状态** | 它本身就是正确的告警或信任门（`UNTRUSTED` / `WARN` / typed `EMPTY`） | **不该消**。消掉它等于把「我不知道」改写成「没问题」 |
| **C 历史堆债** | 不会复发，只占空间 | 可选 retention；**与运行正确性无关** |
| **D 假残留** | 已经修好了，或被误算进清单 | **从清单里拿掉**，不是再修一遍 |

由此，「100% usable」的定义是 **无开放的 class-A**，而不是「清单全空」。class-B 留着是
**做对了**；把 class-B 洗绿是本文件反复禁止的那种假绿。

这套分类是跨文档复用的判断轴：任何一次「还剩 N 项残留」的陈述，若没有按 A/B/C/D 分开，
那个 N 就没有意义 —— 它把「会复发的缺陷」和「正确的告警」加在了一起。

## 10. 删除与目录整理

`data/archive/<subdir>`（子目录名由各 lifecycle/purge manifest 的 `archive_dir` 决定，集合随
manifest 增减、**不是固定枚举**）是治理性删除留下的**冷 parquet 证据保险丝，不是杂物目录**。
禁止批量清空或当作「占空间的历史堆债」处理；只能逐目录、凭「该批删除已无审计/回溯需求」的
证据清理。做磁盘清理的人最容易在这里一键删掉几百 MB 的删除审计证据。

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
- historical evidence：commit message（Q/Fix/Evidence/Residual），检索 `chunkyctl history`；必要的不可复现实测数据进 `data/`。

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

裁决分两层且互不传递（双向）—— 已升为系统语义，见 `MASTER §6.1`。

`daily_update` 的 store 阶段跑 **system_health 运行时自检**（owner =
`backend/config/governance_gates.yaml` 的 `runtime_checks`，见 §13.1）：continuity、
residual_hygiene、grain_uniqueness、cutover_effective。FAIL =
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
- goal 只写当前 objective/blocker/plan；历史进 commit message。

验收命令：

```bash
PYTHONPATH=backend python backend/scripts/check_doc_governance.py
PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check
```

## 13. Rule 10 与交付

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
reviewer）；本地 hook 天然做不到。同批裁决：`check_commit_message.py` 的关键词组同属自述型，一并降为提示（subject 长度仍阻断
—— 长度是客观事实）。**2026-08-11 再进一步**：关键词表整体换成 **Q / Fix / Evidence / Residual
四段结构**自检。理由是关键词有两个毛病 —— 词表必然烂（某时点的产物，项目换了说法就失效而
没人回来改），且贴个词就能过（它检验字符串出现，不是「说清楚了」）。结构检查改问「这四件事
你说了吗」，尤其逼出最后一问「留了什么坑」。**它同样验证不了真假**，所以仍是 warn-only 的
清单而非验证；shell 与 hook 两条路径共用同一实现，不各维护一份词表。安全性不受影响：PIT / leakage / continuity / lineage / population /
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
L1 = docs/ sandbox/ data/board/ light gates; L2 = tests/routers/frontend + Rule 10;
L3 = writer/PIT/schema/config/deletion = current full gate set.

### 13.1 门的分布：按「谁受害、何时受害」分组（2026-08-11 P1 落地）

tier 剪枝管**跑不跑**，分组管**跑红了会怎样**，两维正交。owner =
`backend/config/governance_gates.yaml`（唯一存放处；`gate_policy.py --check`
把它与 `classify_commit_tier` 和 `safe_commit.sh` 的 fail-closed 兜底串三处对账）。

| 组 | 判据 | commit 路径 | 归属 |
|---|---|---|---|
| `diff_correctness` | 这次 diff 本身错 | **阻断** | staged_worktree_parity / moth_invariants / rule_compliance / ci_pytest / sandbox_isolation / serve_read_layer / calendar_usage / population_contract / lineage_drift / dead_references / no_emoji / config_refs / rule10 |
| `system_health` | 数据 / 策略 / 钱受害 | **不跑** | grain_uniqueness / continuity → `daily_update` store 阶段自检 |
| `scaffold` | 下一个开发者受害 | **warn-only** | project_index_sync / feature_map / moth / doc_drift / doc_governance / doc_runtime_state / commit_msg |

判据来自实证而非偏好（2026-08-10 审计，`chunkyctl history --since 2026-08-10 --full`）：脚手架门本轮 3 次
阻断系统修复提交（文档没同步挡住代码 bug 修复），而 cutover 声明是否仍生效 ——
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

**两条执法路径必须同源。** `git commit` 直调时走 `configs/git-hooks/`（入 git、可审查、
经 `git config core.hooksPath configs/git-hooks` 生效，**新克隆需执行一次**）；走
`safe_commit.sh` 时走上表。二者的**后果都由分组决定**，取自同一份
`governance_gates.yaml`。2026-08-11 实测反例：P1 把 `project_index_sync` 降为 warn-only，
而当时的 hook 只存在于各自机器的 `.git/hooks/`（不入版本、不被审查、新克隆根本没有），
仍旧硬阻断 —— 同一道门在两条路径上给出相反后果，且其中一条无人能看见。

```bash
git config core.hooksPath configs/git-hooks  # 新克隆一次性设置
scripts/chunkyctl status                     # L2 运行时状态单一现查入口 (零文件)
scripts/chunkyctl gates                      # 人读分组表
scripts/chunkyctl gates --check              # 三处门名对账
scripts/chunkyctl gates --run-system-health  # 手动跑运行时自检组
scripts/chunkyctl scaffold-fix               # 脚手架批量收口
```

### 13.2 L2 状态零手写（P2）

**运行时状态只许现查，不许人写。** 唯一入口 `scripts/chunkyctl status`
（`backend/services/project_status.py`；零文件、不缓存、退出码恒 0 —— 它报事实不做裁决，
红绿仍归 continuity / watermark SLA / cutover_effective 各自的门）。

执法 = `check_doc_runtime_state`（scaffold 组）：扫活文档里的紧凑 8 位日期，未在
`backend/config/doc_runtime_state.yaml` 声明的一律报出；失效的豁免也报。它**不做语义
猜测** —— 默认禁止 + 显式豁免，因为写豁免这个动作本身就强制作者回答「这是契约常量
还是运行时状态」。本仓约定历史叙述写 `2026-07-24`（带连字符）、状态写紧凑格式，故只
扫紧凑格式即可避开历史叙述。

判据仍是 `docs/README.md` 那一句：**这个值会不会因为系统正常跑一次日更就变？**

不 `--no-verify` 绕门，不 amend 已 push commit，不在未授权时 push。交付报告必须区分 `FIXED/PARTIAL/BLOCKED`，列 residual、验收命令和 owner。

## 14. 编排与墙钟政策（delivery tax；不触碰真相门）

T0 基线（2026-07-20 实测，非估算；证据=`chunkyctl history --since 2026-07-20 --full`）：L1 commit 门 1.6s、
L2 17.0s（moth assert 7.3s + staged-snapshot cold codegraph 3.8s）、L3 27.1s、
`agent-boot` 11.6s（moth snapshot ≈7s）、CI 单次 ≈1min。结论：机械门不是墙钟
瓶颈；瓶颈在编排仪式（每 slice 同步等 CI、每 micro-commit 一次 Rule 10、
父 agent 串行单 worker）。本节只裁编排节奏，**不放宽任何
accept/PIT/calendar/fail-closed/cutover/E 门 / Rule 10 / ≤40d 语义**。

### 14.1 刀级合并（binding；防 micro-commit 复辟）

**刀** = 一个逻辑单元（例：单域 S7 formal|sunset、单 CLI 面、单 E0 域 land
路径）。刀内允许多文件、一次 stage、**一次 Rule 10**、**一次
`safe_commit`**。禁止把同一刀拆成「docs commit → 小改 commit → 测 commit」各
审一次。验收信号：`commits/knife ≤ 1.5`；commit message 写明刀边界。

- **异步 CI（pipelining，不是放松）**：L2/L3 本地先绿同一 **blocking** pytest 面
  （`backend/config/ci_pytest_surface.yaml` via `run_ci_pytest.py --tier blocking`，亦是
  `safe_commit` `ci_pytest` 门）再 push；push 后**禁止**同步 `gh run watch` /
  空等 CI；可开下一刀。刀收口前回读该刀 CI verdict（或上一刀已结束 run）；红 =
  fix-forward 最高优先并暂停派新刀。同步等待只保留给改 CI/gate 机械本身的切片。
  `nightly_paths` 不进 commit 门（`--tier nightly|all` 手动/未来 schedule）。
- **并行 subagents**：仅当写集不相交且不碰共享真相文件时，父可并行派
  disjoint 刀；派前用 `moth coupling --repo . --impact <name>`（或
  `chunkyctl pre-knife`）证明非重叠。**必须串行的共享面**：
  `goal.md`/`PROJECT_INDEX.md`/`AGENTS.md`/docs owner 三文档/
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

### 14.2 刀前 impact 审计（L3 mandatory）

动 `backend/services/`、YAML/SQL 契约、删表/删配置前，**固定一次**：

```bash
scripts/chunkyctl pre-knife <name>
# 等价：moth coupling --repo . --impact <name>
#       codegraph explore "<name> callers"
```

再配最窄 pytest red-first。一次规划、一次绿；禁止「连崩多层 CI / 多 commit
才绿」。刀后若动 PIT/schema/writer，仍走 `$post-fix-audit`（本清单不替代）。

### 14.3 其它节奏

- **owner 文档读取**：每任务读一次 `docs/README.md` 指到的 owner 文档；同任务
  内后续刀不重读 MASTER 全文，引用具体条款即可。
- **显式不做（Occam，有测量背书）**：`agent-boot --fast`（只省 ~7s/session，
  moth 状态是 boot 的价值本体）；L2 门集手术（17s 非痛点，动 `commit_tiers.yaml`
  = L3+审查）；CI concurrency/cancel 机械；T0 墙钟基线已一次实测入账（见下方实测行）。
- CI 对 L1 docs/board-only push 不再起跑：`ci.yml` `paths-ignore` 镜像
  `commit_tiers.yaml` L1 面（policy owner），子集关系由
  `backend/tests/scripts/test_ci_paths_policy.py` 机器守护。

## 15. 判据与真相源的自我验证（2026-08-21/22 两天八次实证）

**这一节的每条都来自真实犯下并被实测拦下的错误（8 次）。**
其中 **3 次的对象是我们自己刚建好的判据**——见 §15.4，那是本节最该记住的部分。 形态只有两类：
**(A) 拿一个没被验证过的东西当判断基准**；**(B) 拿一个不是真相的东西当真相源**。
两类都不会报错——它们产出的是**看起来合理的错结论**，所以只能靠事前的机械检查挡。

### 15.1 A 类：基准/参照自身必须先被验证

| 实证 | 错在哪 | 代价 |
|---|---|---|
| `dc_member` 底线照 `histmin=7,919` 校准 | 那天本身是一次分页截断（库 7,919 行/35 板块 vs vendor 40,000 行/257 板块） | **事故被固化成基准**，门从此拦不住同类缺陷 |
| 据「`dc_member` 板块数应等于 `dc_index`」判某日缺 342 个板块 | vendor 自己三个接口历史覆盖就不同（`dc_index` 559 / `dc_daily` 983 / `dc_member` 217） | 差点把**供应商固有差异**当成我们的缺陷 |
| 看近 5 日 `moneyflow` 与 `daily` 差额恒 0，就想设「必须为 0」的门 | 全历史只有 21.8% 恒 0（2020-2024 恒 0 率为 **0**，差额达 -23） | 该门会**天天报红** |
| `audit_min_rows_baseline.py` 只读 `min_rows_per_batch` | 漏了 `min_rows_since`/`min_rows_before` 时代分段 | 把两个**已根治**的域误报成缺陷（还写进了给用户的报告） |
| 对账门只按 `count(*)` 逐日比行数 | 完整性不是行数问题 | 基准 `{A,B,C}` vs 本域 `{A,B,X}`，少一只多一只互相抵消，**判 pass** |

**执行检查（做判据之前，逐条过）：**

1. **基准日健康性**：拿某天/某段当基准前，先验它本身正常——`该日值 / 邻域中位 ≥ ratio`。
   工具：`backend/scripts/audit_min_rows_baseline.py`。低比值**只是粗筛不是判决**，
   最终必须向 vendor 核证（反例：`dc_member 2025-10-29` 比值 0.32，但 vendor 全量就是 20,748 行）。
2. **跨源对账前先证明该源本就该一致**：两个接口/两个域历史上是否恒等，要**分层核证**后才能设门。
   落地形式 = `completeness_ref.verified_since` + `evidence`，缺 `verified_since` 直接判违规。
3. **禁止从近期样本推全历史**：任何「差额恒定 / 比例稳定 / 一直如此」的结论，先按年/月分层跑一遍。
4. **判据要读全配置**：写审计工具时，把该域**所有**相关声明字段读全（时代分段、墓碑、
   豁免、容差）。漏一个维度 = 制造假阳性，而假阳性会淹没真信号。

### 15.2 B 类：先确认你查的是不是真相面

**同一天内这一条害了三次**（查 K 线缺口两次、比 universe 口径一次），且波及一个调查 agent。

```
raw_tushare_daily        role=fill / write=forbidden   ← 停更遗留表, 其 max(trade_date) 只反映它何时停更
canonical_nominal_ohlcv_daily / accepted_partition     ← 实际真相面
```

当时实测两者相差一个多月; 北交所更是「遗留表 0 只 vs 真相面数百只」——
拿遗留表比 universe 口径会得出**完全相反**的结论。当前各面实际水位查
`scripts/chunkyctl status`，不要从本文读数字。

**执行检查：**

1. **查任何 `raw_*` 表之前，先看 `backend/config/legacy_raw_plane.yaml`**：
   `role=fill/compatibility/retired` 或 `write: forbidden` 的表**不是真相源**，
   它的 `max(trade_date)` 只反映它何时停更。
2. **门/脚本的输出必须指向真正被审计的对象**：`_result(..., audited=...)`
   （`check_continuity_integrity.py`）。指错表 = 把每一个照着输出去排查的人送进沟里。
3. **门自己的基准表也要过这一关**：对账门初版把基准写成 `raw_tushare_daily`（`write: forbidden`
   的停更表）。后果不是报错而是**静默失效**——基准没有最近那些天，`LEFT JOIN` 一行都比不出来，
   这些天却照样计进「对账通过」的天数。换成真相面后立刻暴露：北交所进入各表的时点差半年以上
   （`canonical` 2026-01-16 / `daily_basic` 2026-07-17 / `moneyflow` 2026-08-03），而旧基准恰恰是
   唯一永远不含北交所的表，把这段真实覆盖差完整掩盖成 100% pass。
4. **判「接口/路由不存在」前穷尽路径形态**：实测反例——扶摇 Parquet 批量下载被我判成
   「未上线」，实际是少了子路径（`/api/dump/market-dumps/daily-k/download-url` 才对）。
   一次 404 只证明**那一个路径**不通。

### 15.4 新建的判据是**高危对象**，不是成果

8 次里有 3 次，出问题的是我们**自己刚建好、刚跑绿、刚提交**的判据：

| 建成到暴露 | 那道判据 | 它错在哪 |
|---|---|---|
| 3 天 | 同日行数对账门 | 名字叫「行数对账」，而完整性从来不是行数问题（集合可以不等而行数相等） |
| 3 天 | 同一道门的基准表 | 基准指向停更遗留表，最近 25 天**空对账**却计入「通过」天数 |
| 数小时 | `audit_min_rows_baseline.py` | 漏读时代分段字段，把两个已根治的域报成缺陷 |

**为什么新判据格外危险**：它刚跑绿过，作者对它有信心；它的绿是「没报错」而不是
「查过并通过」——而这两者在代码里长得一模一样。前 5 次是「用了别人留下的坏基准」，
这 3 次是「自己造了个看起来在工作的东西」，后者更难自查，因为没有人会去质疑刚写完的代码。

**交付一道门/判据之前，必须逐条回答（答不出就是没做完）**：

1. **基准是真相面吗？** 查 `backend/config/legacy_raw_plane.yaml` 的 `role` / `write` ——
   `fill` / `compatibility` / `retired` / `write: forbidden` 的表**不能当基准**。
2. **它声称覆盖的范围，每一项都真的比对了吗？** 拿它报的「N 天通过」去反查：
   基准侧在这 N 天里有没有数据？`LEFT JOIN` 后没有行 = 没比 = 不该算通过。
3. **它比的是聚合量还是集合？** 聚合相等能否掩盖集合不等？（行数、总和、计数都是聚合）
   —— 这条与 §15 开头的 `feedback-verify-content-not-just-row-count` 同源。
4. **反向验证**：造一个它**应该**抓到的缺陷，看它抓不抓得到；再把核心逻辑临时改回旧行为，
   看测试转不转红。两个方向都要跑，只跑正向等于没验。
5. **它的适用边界在哪？** 判据往往在某个样本量/占比之外失效
   （实例：全历史 dip 的 CV 分层在塌陷占比 >5% 时自我掩盖，边界写在 `_dip_severity.py`）。

### 15.3 为什么这些必须成文

六次里没有一次是「粗心」——每次都有看似充分的理由，且**结论自洽**。
拦下它们的全是**实测**，不是复查思路。所以这一节不是提醒「要小心」，
而是把「必须先跑哪一步」写成清单：基准要先验、跨源要先证、样本要先分层、
配置要读全、真相面要先查登记、路径要穷尽。

配套的机器执法已落地：`completeness_ref.verified_since`（缺则违规）、
`audit_min_rows_baseline.py`（基准健康性）、`_result(audited=)`（输出指向）。

### 15.5 判据可能**问错了问题**（2026-09-01 换源实证）

前面几类讲的是「判据的基准是假的」「判据没真的查」。这一类不同：判据在认真工作、
基准也是真的，但它**问的不是你以为它在问的那个问题**。

**实证**：`daily` 域授权换源（tushare → 通达信）时，`security_day_reader` 拒读全部
既有分区，报 `accepted_partition_contract_drift`。追下去发现 `config_hash` 的
payload 里含 `source` 与 `api`——于是「换个供应商取同样的 OHLCV」被算成了「契约变更」。

这个判据本意是问「**数据语义变了吗**」（字段/单位/粒度/总体变了，旧数据就不能用新契约解释）。
但因为 `source` 参与了指纹，它实际问的是「**取数地址变了吗**」。两个问题平时高度重合
——同一个源持续供数时，地址不变语义也不变——所以它多年没暴露。**换源那天，重合消失，
判据当场给出与它意图相反的答案**：数据一字未变，却被判为不可读；影响面实测
`daily` 1,858 个分区 + `stock_st` 1,128 个。

更讽刺的是同一份代码里就写着答案：`formal_boundaries.py` 开篇是
「**Transport axis only.** Business tiers must not own these seams.」
传输轴与语义轴的分离**已经是明文架构原则**，只是 hash 计算没有遵守它。

**修法**：`source`/`api` 移出 `config_payload`；语义仍被 `schema_hash`（字段/类型/单位）
\+ `grain` + `partition_by` + `population_scope` + `availability` + 表名 + `coverage_start`
完整覆盖——任何真实语义变化都会动到其中至少一项。registry 与 DOMAIN 的 source 一致性
另由 `_expected_transport` 独立守卫，不依赖 hash。既有 2,986 个分区一次性重打戳
（只改元数据一字段、不动数据行、有备份可回滚）。

**可复用的自检**（设计或修改任何指纹/校验和/相等断言时逐条答）：
1. 这个判据**想**回答什么问题？用一句话写下来。
2. 它**实际**比较的每一个字段，都属于那个问题吗？逐字段问「这一项变了，
   我想防的那件坏事就真的发生了吗？」——答不出「是」的字段就不该在里面。
3. 有没有**两个问题平时重合**的情况？（同源供数时"地址"与"语义"同步不变）
   重合会让错误的判据长期显得正确，**直到第一次不重合**——那通常正是最需要它准确的时刻。
4. 仓库里有没有**已经写明的分层原则**（如 transport vs semantic），而这个判据违反了它？
   代码注释里的架构声明与实际实现不一致时，**不一致本身就是缺陷**，不是文档过时。

同一天的第二个同类实证（判据挂错真相源）：`test_formal_boundaries` 曾断言
「仍指向 tushare 的 formal 域必须是 `retired_readonly`」。`margin` 标签正是
`retired_readonly`，断言因此通过——但实测它**仍在活跃调 tushare**
（`canonical_margin_exchange_daily.built_at` 三天前还在更新，`margin_acceptance.py`
硬编码 `source="tushare"`，走 2026-07-23 解冻的 on_demand 追赶）。
`runtime_state` 是**会过期的文档标签**，拿它当事实等于用未经验证的东西做判据。
改断言为「必须在日落台账 `tushare_sunset.yaml` 里有裁决」——台账是人写的裁决记录，
不会因为某条通道被悄悄解冻而失真。**改判据后第一次跑就抓出了 `margin` 这个真缺口。**

### 15.6 同一个事实存两处、然后断言相等（2026-09-02 两处活体故障）

上一节的手术（`source`/`api` 移出 `config_hash`）本身是对的，但它的收尾把病带进了第二层：
契约指纹算法一变，`accepted_partition` / `canonical_*` 的戳跟着当前契约重打（它们表达
「这批数据符合哪个契约」），而 `ingest_batch.{contract_hash, config_hash, source_name}` 是
**落地那一刻的证据封印**——`payload_hash` 从它们连同逐行签名一起 sha256 派生，改它等于重新
封印历史（实测 47,604 行同步尝试当场 `LANDING_HASH_MISMATCH`，已回滚）。于是两侧各自都对，
错的是「两侧必须相等」这条断言本身。它散落在 N 处运行时代码里，09-01 只修了 `margin_state`
一处，漏掉的两处次日成为活体故障：

- `calendar_reader._load_and_verify_batch` 把指针戳与批次戳逐键比相等 → `open_calendar_truth()`
  永久 `BLOCKED fields=['config_hash','contract_hash']`，连带 `resolve_traded_on_observation_date` /
  `evaluate_observation_population_readiness` 一起断；而 `calendar_builder` 不走 reader，
  `dim_trading_calendar` 照常生成——**故障是静默的**。
- `margin_acceptance.prove_current_landed_margin_batch` 把 LANDED 检查点的戳与现算契约比相等 →
  最新一个仍处 LANDED 的检查点被判「stale checkpoint」，该日追赶路径卡死。

补掉第一处后故障往下挪一层（`calendar_acceptance` 六元组 `CONTRACT_DRIFT`），再补一处又挪到
封印重算（它拿**现算契约**当身份重算 `payload_hash`）——一处一处补到不炸为止，正是这类漂移
的典型修法，也正是要禁止的修法。

**先给操作数分类，再决定谁能跟谁比**（这是判据，不是清单）：

| 存放点 | 性质 | 指纹算法变了之后 |
|---|---|---|
| 现算契约 `contract.*` | 活 | 新值 |
| `accepted_partition.*` 指针戳 | 重打 | 跟着活契约 |
| `canonical_*.config_hash` | 重打 | 跟着活契约 |
| `ingest_batch.{contract_hash, config_hash}` | **冻结**（封印派生） | 停在落地时刻 |
| `ingest_batch.source_name` | **冻结**（传输轴血缘） | 与现 `contract.source` 无关 |
| `ingest_batch.{contract_version, writer_id, dataset_id, partition_value}` | 声明身份（人定的字符串，不是算法输出） | 不变 |

规则一句话：**算法派生的指纹与传输轴标签是冻结证据，只能用来重算封印，不能与任何「现在」
的值比相等**；可比的是声明身份、内容（`canonical_hash` / `row_count`）与时间链。
「批次是不是被动过」由封印自洽（按批次**自己**的戳重算 `payload_hash`）回答，
「指针是不是当前契约的」由指针 vs 活契约回答——这两问合起来覆盖了原来那条等式想防的一切，
且在换算法、换源那天不会给出与意图相反的答案。

**执法**（两层，缺一不可）：
- 行为测试自带 fixture 造「批次戳陈旧但指针/契约一致」的状态跑真路径：
  `tests/services/test_calendar_frozen_landing_stamp.py`（含业主点名的「只换 source_name」用例）、
  `test_margin_frozen_landing_stamp.py`、`test_accept_frozen_landing_stamp.py`。修复前全部红，
  且各带负向控制（指针漂移仍 BLOCK / 封印不重算仍 BLOCK / 声明身份仍比对）证明不是放松成恒绿。
- 静态门 `backend/scripts/check_frozen_stamp_compare.py` 抓三种真实出现过的写法形状
  （直接比 / 元组比 / SQL `ib.contract_hash = ?`），修复前对真实代码报 13 处，修复后 0；
  它**诚实列出盲区**（dict 键循环、下标取列、拿现算契约重算封印、跨函数传递），这些靠上面的
  行为测试兜底。经 `tests/scripts/test_check_frozen_stamp_compare.py` 接进 `ci_pytest` 门。

对新 `backend/sync/` 包的约束（反向绞杀替换 transport 层时不许把病带过去）：落地封印与
验收指针必须是**两个类型**——`LandingSeal`（落地时刻、只进封印重算、无 `__eq__` 跨型比较）
与 `AcceptedStamp`（跟随活契约）；跨型能比的只有声明身份字段。任何「同一语义事实存两处、
再断言相等」的结构在设计评审时按本节判。
