# codegraph + 审计基础设施整合规格

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。

版本: 0.1 (设计草稿) | 日期: 2026-05-19 | 状态: 只读设计, 未实施

## A. codegraph 0.6.8 现有能力盘点

### A1. 理论能力 (基于 0.6.8 文档推断)
| 能力名 | 说明 | 输出类型 |
|--------|------|----------|
| call graph | 函数/方法调用链, 用于从修改点枚举 caller/callee | 节点和边 |
| import graph | 模块依赖树, 用于观察包、模块和脚本间依赖 | 模块图 |
| def-use | 变量/函数定义-使用追踪, 用于追踪参数和过滤条件传播 | 定义点和使用点 |
| hotspot | 高耦合/高 fan-in/fan-out 节点, 用于定位架构压力点 | 排名列表 |
| cycle detection | 循环依赖检测, 用于发现 import 或调用闭环 | cycle 列表 |
| dead code | 未被调用的节点, 用于发现废弃函数、因子和入口 | 候选节点列表 |

设计备注:
- 本节基于 codegraph 0.6.8 能力边界推断。
- 实施前必须以本机 `codegraph --help` 和实际 CLI 输出为准。
- 当前规格不假设这些能力已经接入 hooks、scripts 或 skills。
- 当前规格只描述整合方式, 不直接实施。

### A2. 项目实际跑过的 query (commit aa94bbab, 2026-05-19)
规模: 812 files / 12,507 nodes / 43,010 edges

性能 hotspot 发现 (5 项):
| ID | 描述 | 位置 (推断) | 严重度 |
|----|------|------------|--------|
| P-1 | trade_date TEXT->DATE 类型错误, 每次 query 隐式转换 | db.py / fact 表 DDL | HIGH |
| P-2 | Optuna n_jobs 并发未限制 (已 fix) | optuna_runner.py 等 | MEDIUM |
| P-3 | build_feature_panel mega script, 单函数过大 | backend/scripts/ | HIGH |
| P-4 | 21 处 executemany 在 Python for-loop 内调用 | 多处 | MEDIUM |
| P-5 | 130 处 CTE 重复定义 | SQL 文件 | LOW |

性能 finding 解释:
- P-1 是类型和执行计划问题, codegraph 主要用于定位 DDL 与调用路径。
- P-2 是并发配置问题, codegraph 主要用于枚举 Optuna runner 影响面。
- P-3 是脚本结构问题, 适合转成 god-module 阈值告警。
- P-4 用 grep 即可低成本覆盖, 不必依赖图分析。
- P-5 更接近 SQL 模板重复问题, 后续可由 SQL lint 或规范化器处理。

架构 finding (5 项):
| ID | 描述 | 严重度 |
|----|------|--------|
| HIGH-1 | db.py 2478 行 god module, 所有 DB 操作集中 | CRITICAL |
| HIGH-2 | 高 fan-in 模块 (多处被依赖) | HIGH |
| HIGH-4 | 另一高 fan-in 模块 | HIGH |
| ATTACH-41 | 41 个文件使用 DuckDB ATTACH, 耦合集中 | MEDIUM |

架构 finding 解释:
- HIGH-1 是最明确的自动化阈值候选。
- HIGH-2 和 HIGH-4 需要保留原始 codegraph 节点名才能做精确追踪。
- ATTACH-41 可先通过文本扫描建立基准。
- 当前 finding 仍偏一次性诊断, 尚未进入可复跑审计链路。
- 本规格目标是把一次性 finding 固化为低误伤、可重复的设计入口。

### A3. 未充分利用的能力
- dead code 扫描: 从未跑过, 项目演进快, 可能有废弃因子函数
- cycle detection: 只报了高 fan-in, 未明确跑 cycle query
- def-use 追踪: 未用于 PIT 变量泄漏路径分析
- diff-mode (只扫变更文件): 从未在 pre-commit 中使用
- 跨文件 SQL 参数追踪: 未用 codegraph 追踪 date 参数的传递路径
- caller 枚举: 尚未系统用于 fix 后 downstream stale cleanup
- hotspot baseline diff: 尚未形成“本次 vs 上次”的架构趋势报告
- god-module 阈值化: db.py 行数和 fan-in 只在人工审计中出现
- ATTACH 耦合计数: 已有 finding, 但没有稳定脚本或阈值
- import cycle gate: 当前 commit-time gate 不覆盖 dependency cycle

未充分利用的原因:
- codegraph query 当前偏人工探索, 没有稳定接到既有审计入口。
- hooks 关注 rule compliance, 不关注图结构变化。
- 部分能力需要确认 CLI flag 和 JSON 输出格式。
- 部分检查误伤成本较高, 不适合初版 block commit。
- 项目已有多套审计入口, 新能力应桥接而非替代。

## B. 现有项目审计能力矩阵
| 审计场景 | pit-audit | parallel-grid-runner | data-integrity-audit | post-fix-audit | check_*.py scripts | hooks | Rule 9/10 |
|---------|-----------|---------------------|---------------------|----------------|-------------------|-------|-----------|
| 1. PIT leakage 检测 | 完全 | 无 | 部分 | 部分 | 无 | 无 | 部分 |
| 2. 性能 hotspot 发现 | 无 | 无 | 无 | 无 | 无 | 无 | 无 |
| 3. 数据完整性/sync gap | 部分 | 无 | 完全 | 无 | 部分 | 无 | 无 |
| 4. fix 后 downstream stale cleanup | 无 | 无 | 无 | 完全 | 无 | 无 | 部分 |
| 5. 数据源治理/网络约束 | 无 | 无 | 部分 | 无 | 无 | 无 | 无 |
| 6. Optuna leakage/DuckDB lock | 部分 | 完全 | 无 | 无 | 无 | 无 | 无 |
| 7. commit-time self-check | 无 | 无 | 无 | 无 | 部分 | 完全 | 完全 |
| 8. 架构 god-module/高 fan-in | 无 | 无 | 无 | 无 | 无 | 无 | 无 |
| 9. dependency cycle | 无 | 无 | 无 | 无 | 无 | 无 | 无 |

Gap 汇总:
- 场景 2 (性能 hotspot): 完全无自动化覆盖, 依赖手动 codegraph 跑
- 场景 8 (god-module/fan-in): 完全无覆盖
- 场景 9 (cycle): 完全无覆盖
- 场景 5 (数据源治理): 仅 data-integrity-audit 有部分覆盖, 无网络约束检测
- check_*.py scripts 覆盖 commit discipline 和少量质量检查, 不覆盖架构图
- audit_*.py scripts 数量较多, 但当前没有统一消费 codegraph 输出
- hooks 已有硬阻断能力, 新增 codegraph 检查应先 WARN

重叠分析:
- PIT leakage (场景 1): pit-audit + Rule 9 write-time gate 双重覆盖, 重叠可接受
- commit-time check (场景 7): hooks + Rule 9 commit-time + check_rule_compliance.py 三重覆盖, 轻度冗余
- data-integrity-audit 与部分 audit_*.py scripts 目标重叠, 但粒度不同
- post-fix-audit 与 stale reference 审计可能有交集, 但前者偏流程, 后者偏扫描
- codegraph 不应替换现有审计, 只补足调用链、依赖、fan-in、cycle 这类图问题

本轮只读确认的 check scripts:
- backend/scripts/check_codex_review.py
- backend/scripts/check_commit_message.py
- backend/scripts/check_deflated_sharpe.py
- backend/scripts/check_no_emoji.py
- backend/scripts/check_project_index_sync.py
- backend/scripts/check_rule_compliance.py
- backend/scripts/check_sina_tdxhub_overlap.py

本轮只读确认的 audit scripts:
- backend/scripts/audit_data_completeness.py
- backend/scripts/audit_delivery_readiness.py
- backend/scripts/audit_end_to_end.py
- backend/scripts/audit_event_timestamp.py
- backend/scripts/audit_lgbm_feature_importance.py
- backend/scripts/audit_live_dashboard.py
- backend/scripts/audit_p0a_panel.py
- backend/scripts/audit_paper_sim_diagnostics.py
- backend/scripts/audit_pit_coverage.py
- backend/scripts/audit_pit_integrity.py
- backend/scripts/audit_registry_feature_pit.py
- backend/scripts/audit_sim_run_ledger.py
- backend/scripts/audit_stale_references.py
- backend/scripts/audit_survivorship.py
- backend/scripts/audit_survivorship_gate.py
- backend/scripts/audit_tdx_data_need_coverage.py
- backend/scripts/audit_tdx_f10_source_date_sections.py
- backend/scripts/audit_tradeability.py
- backend/scripts/audit_universe_coverage.py

本轮只读确认的 hook 状态:
- .git/hooks/pre-commit 当前先检测 MERGE_HEAD, merge commit 直接 skip
- .git/hooks/pre-commit 当前执行 check_project_index_sync.py
- .git/hooks/pre-commit 当前执行 check_rule_compliance.py
- .git/hooks/pre-commit 当前执行 check_no_emoji.py
- .git/hooks/pre-commit 当前没有 ruff 调用
- .git/hooks/pre-commit 当前没有 codegraph 调用
- .git/hooks/commit-msg 当前执行 check_commit_message.py
- .git/hooks/commit-msg 当前执行 check_codex_review.py
- .git/hooks/commit-msg 当前没有 dependency 或架构检查

本轮只读确认的 docs 状态:
- docs/ 已存在, 无需 mkdir
- docs/ 已有架构、数据源、PIT、alpha、MSAF、回测整合等设计文档
- docs/codegraph_audit_integration_spec.md 在读取时未出现在 docs/ 列表中
- 本次变更只新增该规格文件, 不修改既有 docs 文件

## C. 整合方案 (6 个具体整合点)

### C1. pit-audit Step 3 + codegraph def-use
整合描述: pit-audit Step 3 (SQL 扫描) 补充 codegraph def-use query, 追踪含 LEFT JOIN 的 SQL 函数的调用链, 确认所有调用路径都经过 built_at/notice_date 过滤节点。

现状: pit-audit Step 3 只做静态 grep (rg 'LEFT JOIN'), 不追踪调用链深度。

整合后: 新增子步骤 3b: 对 grep 命中的函数, 用 codegraph query --def-use <function_name> 输出调用链, 人工或脚本确认过滤节点存在。

细化要求:
- Step 3b 只针对 LEFT JOIN 命中函数。
- def-use 结果只作为辅助证据, 不替代人工 PIT 判断。
- 检查 built_at/notice_date 过滤是否在 SQL 执行前发生。
- 检查调用方是否绕过 PIT-safe wrapper。
- 对动态 SQL 或无法解析路径标记 manual review。
- Step 3b 不要求每次 PIT 审计都全量 sync。

验收标准:
- pit-audit SKILL.md 中出现 Step 3b。
- 输出能贴入 PIT 审计报告。
- 发现无法解析路径时给出 manual review 清单。

工作量: pit-audit SKILL.md 新增 Step 3b 说明 (~10 lines)
优先级: 中

### C2. post-fix-audit Step 4 + codegraph call graph fan-out
整合描述: post-fix-audit Step 4 (识别 downstream stale) 补充 codegraph call graph 自动枚举受影响模块。

现状: Step 4 依赖人工判断 'what else calls this function'。

整合后: Step 4 新增: codegraph query --callers <fixed_function> 输出所有调用方列表, 作为 stale cleanup checklist。

约束: 只在被修改函数为 db.py 级别高 fan-in 节点时触发 (否则 overhead 不值得)

细化要求:
- caller 列表按 production、script、test、manual review 分类。
- 每个 caller 标记 unaffected、needs rerun、needs cleanup 或 manual review。
- 对 db.py、feature builder、Optuna runner 等高 fan-in 节点强制附调用图摘要。
- 如果 codegraph CLI 无 --callers, 降级为 import graph 加文本搜索。
- 测试文件不进入 production stale cleanup, 但要单独列出。

验收标准:
- post-fix-audit SKILL.md 新增条件子步骤。
- 子步骤明确触发条件和跳过条件。
- 输出格式能直接成为 cleanup checklist。

工作量: post-fix-audit SKILL.md 新增条件子步骤 (~8 lines)
优先级: 中

### C3. 新 audit script: audit_god_modules.py
描述: 将 codegraph HIGH-1 发现固化为可重复运行的阈值告警脚本。

入口逻辑 (伪代码):
```text
1. 读取 codegraph 上次 sync 的 JSON 输出 (或重新 query)
2. 对每个 node: if fan_in > 30 or line_count > 800: WARN
3. 检查 db.py 是否仍超 2000 行
4. 检查 ATTACH 引用文件数是否超阈值 (当前基准: 41)
5. 输出 Markdown 报告
```

路径: backend/scripts/audit_god_modules.py (待实施)
依赖: codegraph 0.6.8 JSON 输出格式 (需确认 CLI flag)
优先级: 低 (backlog)

设计要点:
- 初版只 WARN, 不 block CI。
- 输出 Markdown, 便于进入 Codex review 或架构审计报告。
- 阈值必须与 baseline 比较, 不能只输出绝对数。
- db.py 行数阈值建议先设 2000 行 WARN。
- line_count 阈值建议先设 800 行 WARN。
- fan_in 阈值建议先设 30 WARN。
- ATTACH 文件数阈值以当前基准 41 为起点。
- 如果 ATTACH 数量上升, 输出新增文件列表。
- 如果 db.py 行数下降, 报告中记录改善, 不要求立即拆分。

报告草案:
```text
God Module Audit
status: WARN
baseline: 2026-05-19
findings:
- db.py line_count: 2478 > 2000
- attach_file_count: 41
- fan_in_over_threshold: N
suggested_review:
- 是否需要拆分 db.py 的 read/write/query namespace
- 是否需要统一 DuckDB ATTACH 入口
```

### C4. pre-commit hook 扩展: codegraph diff-check
描述: 在现有 pre-commit (ruff 后) 新增 codegraph diff-mode 扫描, 只扫 staged changed files。

本轮读取到的现状修正:
- 当前 .git/hooks/pre-commit 没有 ruff 步骤。
- 当前顺序是 merge skip, project index sync, rule compliance, no-emoji。
- 因此实际插入点应是 no-emoji 之后。
- 如果后续恢复 ruff, codegraph diff-check 可放在 ruff 后。
- 该规格不直接编辑 .git/hooks/pre-commit。

逻辑:
```text
1. git diff --cached --name-only --diff-filter=ACM | grep '\.py$' -> changed_files
2. if changed_files: codegraph query --diff <changed_files> --check cycle,fan-in
3. if new cycle or fan_in_delta > 10: WARN (不 block, 只报告)
```

约束:
- 不全量 sync (会慢), 只 diff-mode
- 结果只 WARN 不 block (避免 false positive 卡 commit)
- 需确认 codegraph 0.6.8 是否支持 --diff 模式 (若不支持则降级为: 检查 changed file 的 import 数量变化)
- pre-commit 已有 MERGE_HEAD skip, C4 应复用该 skip 语义
- 如果 staged Python 文件为空, 直接跳过
- 如果 codegraph 命令不存在, 初版 WARN 后跳过, 不 block

降级方案:
- 对 changed_files 运行 import 文本扫描。
- 统计每个文件 import 行数。
- 若单文件 import 行数增加超过 10, WARN。
- 若新增相对 import 指向 backend.db 或大型脚本, WARN。
- 若检测到显式循环 import 文本模式, WARN。

验收标准:
- pre-commit 增加约 15 行 shell。
- hook 仍保留 MERGE_HEAD skip。
- hook 不执行 codegraph 全量 sync。
- hook 不因 codegraph 缺失失败。
- hook 输出包含 codegraph diff-check 字样。
- 初版只 WARN, 不 exit 1。

工作量: .git/hooks/pre-commit 新增 ~15 lines
优先级: 高

### C5. data-integrity-audit 新增 executemany loop 检测步骤
描述: 针对 P-4 (21 处 executemany in Python loop), data-integrity-audit 新增 Step 5b: 扫描 Python 文件中 for-loop 内包含 execute/executemany 的模式。

不需要 codegraph, 纯 grep 实现:
```text
rg -n 'for .+ in .+:' -A5 backend/ | grep -B3 'executemany\|\.execute('
```

输出: 命中行数 + 文件列表, 与 P-4 基准 (21 处) 比较, 若增加则 WARN。

设计边界:
- 该步骤不判断所有 loop execute 都有问题。
- 该步骤只发现可能的批量写入性能热点。
- 该步骤不进入 pre-commit。
- 该步骤适合放在 data-integrity-audit 的性能附加检查中。
- 该步骤可以先以文本扫描实现, 后续再替换为 AST。

验收标准:
- data-integrity-audit SKILL.md 新增 Step 5b。
- Step 5b 记录基准 21。
- Step 5b 明确 WARN 条件是“新增命中”。
- Step 5b 不要求开发者立即重构所有历史命中。

工作量: data-integrity-audit SKILL.md 新增 Step 5b (~12 lines)
优先级: 低

### C6. 新 skill: codegraph-architecture-audit (桥接 skill)
描述: 形式化 codegraph + 现有 skill 的桥接, 5 步流程。

触发条件:
- 大型重构 PR (changed files > 20)
- god-module 被修改 (db.py / build_feature_panel 相关)
- 季度性架构 review
- 数据库访问层大改
- Optuna runner 或 parallel grid runner 大改
- 涉及 PIT 过滤路径的大范围 SQL 改动

5 步流程:
- Step 1. sync: codegraph sync (全量, 仅在触发条件满足时跑)
- Step 2. hotspot query: codegraph query --hotspot --top 10 -> 与上次基准比较
- Step 3. cross-ref: 对 hotspot 节点, 触发 pit-audit Step 3b (C1) 和 post-fix-audit Step 4 (C2)
- Step 4. god-module threshold: 运行 audit_god_modules.py (C3), 比较基准
- Step 5. report: 输出 Markdown diff (本次 vs 上次基准), 附 ETA 给 Codex review

不引外部 skill, 只用 codegraph 0.6.8 CLI + 现有 4 skills + C3 script。
存放路径: /Users/dp/.claude/skills/codegraph-architecture-audit/SKILL.md (设计草稿, 未实施)
优先级: 高 (spec 草稿先写)

桥接原则:
- codegraph-architecture-audit 不替代 pit-audit。
- codegraph-architecture-audit 不替代 post-fix-audit。
- codegraph-architecture-audit 不替代 data-integrity-audit。
- codegraph-architecture-audit 只负责把图分析结果分发到正确审计流程。
- 若 codegraph 输出不可用, skill 应降级为手动 rg 和 import 扫描。
- 若大型重构 PR 未触发全量 sync, review 中应记录原因。
- 所有发现先 WARN, 只有既有 Rule 9/10 gate 继续 block。

报告格式草案:
```text
codegraph architecture audit
date: 2026-05-19
baseline: aa94bbab
mode: full sync
summary:
- files:
- nodes:
- edges:
- hotspot_delta:
- cycle_delta:
required_follow_up:
- pit-audit Step 3b:
- post-fix-audit Step 4:
- god-module threshold:
eta_for_review:
- query:
- manual review:
- cleanup:
```

实施注意:
- skill 文档应先写成操作说明, 不绑定不存在的 CLI flag。
- 所有命令都应带“如果命令不可用则降级”的分支。
- baseline 文件存放位置需要另行确认。
- 该 skill 适合季度 review 和大 PR, 不适合每次小改都跑。
- 若 report 中出现 CRITICAL, 仍应由人工决定是否拆任务。

## D. 实施优先级与 ETA

### 高优先级 (本 sprint, ETA 2h)
| 项目 | 说明 | 估时 |
|------|------|------|
| C4 pre-commit hook 扩展 | .git/hooks/pre-commit 新增 codegraph diff-check | 0.5h |
| C6 codegraph-architecture-audit skill spec | 写 SKILL.md 草稿 (不含实施) | 1.5h |

前置确认: codegraph 0.6.8 是否支持 --diff 模式 (影响 C4 实施路径)

高优先级理由:
- C4 能把 dependency/cycle/fan-in 风险提前到 commit-time。
- C4 初版只 WARN, 对开发流阻断最小。
- C6 能把一次性 codegraph 分析变成可重复流程。
- C6 不需要立即改现有脚本, 风险低。

建议执行顺序:
1. 跑 `codegraph --help` 确认 diff 和 output flag。
2. 若支持 diff, 实施 C4 的 WARN-only hook。
3. 若不支持 diff, 实施 import delta 降级方案。
4. 写 codegraph-architecture-audit SKILL.md 草稿。
5. 用一次大型变更或季度 review 试跑 C6。

### 中优先级 (下 sprint, ETA 4h)
| 项目 | 说明 | 估时 |
|------|------|------|
| C1 pit-audit Step 3b | codegraph def-use 追踪 LEFT JOIN 调用链 | 1.5h |
| C2 post-fix-audit Step 4 扩展 | codegraph fan-out 枚举 downstream | 1h |
| C6 实施 | codegraph-architecture-audit SKILL.md 完整实施 | 1.5h |

中优先级理由:
- C1 能改善 PIT leakage 的路径级证据质量。
- C1 需要审计人员理解 def-use 输出, 不适合仓促自动化。
- C2 能降低 fix 后漏清 downstream 的概率。
- C2 只在高 fan-in 节点触发, 控制成本。
- C6 完整实施依赖 C1、C2 和 C3 的接口稳定。

建议验收:
1. 找一个含 LEFT JOIN 的真实函数试跑 C1。
2. 找一个 db.py 中高 fan-in 函数试跑 C2。
3. 对输出结果做 manual review。
4. 将误伤和漏报写入 skill 文档。

### 低优先级 (backlog, ETA 3h)
| 项目 | 说明 | 估时 |
|------|------|------|
| C3 audit_god_modules.py | 需确认 codegraph JSON 输出格式 | 1.5h |
| C5 executemany grep 步骤 | data-integrity-audit SKILL.md 新增 | 0.5h |
| P-1 trade_date 修复验证 | 确认 TEXT->DATE 已全量修复 | 1h |

低优先级理由:
- C3 依赖 JSON 输出格式, 需要先确认 CLI。
- C3 初版价值主要是报告, 不是阻断。
- C5 可以很快实现, 但误伤需要人工解释。
- P-1 更像具体性能修复验证, 不属于 codegraph 桥接本身。

## E. 风险与未确认项
| 未确认项 | 影响 | 建议 |
|---------|------|------|
| codegraph 0.6.8 是否支持 --diff 模式 | 影响 C4 实施路径 | 跑 codegraph --help 确认 |
| codegraph JSON 输出格式 | 影响 C3 脚本读取方式 | 跑 codegraph query --output json 确认 |
| pre-commit hook 现有执行时间 | C4 若 diff-check 慢则需 skip | 实测 hook 总耗时 |
| db.py 当前行数 | HIGH-1 是否已改善 | wc -l backend/db.py |

扩展风险:
- CLI flag 与设计假设不一致, 导致 C1/C2/C4/C6 需要降级。
- codegraph sync 成本过高, 导致开发者绕过流程。
- WARN 信息过多, 最终被忽略。
- fan-in 阈值过低, 对工具类模块产生大量误报。
- fan-in 阈值过高, 无法提前暴露 god-module 风险。
- def-use 对动态 SQL 和字符串拼接识别不足。
- SQL 参数路径跨 Python 和 DuckDB 时, codegraph 可能只能覆盖 Python 层。
- hooks 已经承担多个检查, 新增检查必须严格控制耗时。
- .git/hooks 是本地文件, 不一定自动随 repo 分发。

缓解策略:
- 初期所有 codegraph 新检查均 WARN-only。
- 对每个 WARN 保留 baseline 和 delta。
- 对大型审计使用全量 sync, 对 commit-time 使用 diff 或降级扫描。
- 对无法解析的动态路径明确标记 manual review。
- 对脚本输出使用 Markdown, 方便进入 review 记录。
- 对高成本步骤设置触发条件, 避免每次小改运行。
- 对 CLI 行为不确定处先写设计, 不提前写死实现。

## F. 不采纳项 (已 reject)
- mattpocock/skills: 外部 skill, 用户已 reject
- complexity-optimizer: 外部 skill, 用户已 reject
- codegraph 全量 sync 加入 pre-commit: 耗时过长, 降级为 diff-mode
- 归档旧数据源而非删除: 违反项目原则 (废弃数据彻底删除)
- 在初版中让 codegraph hook block commit: 误伤风险高, 先 WARN-only
- 在未确认 JSON 输出前实现 audit_god_modules.py: 容易绑定错误格式
- 在本规格中直接修改 pit-audit/post-fix-audit/data-integrity-audit: 本文档只做设计
- 在本规格中引入外部 skill: 与约束不符
- 在本规格中运行 git add 或 git commit: 与约束不符
- 在本规格中修改 .git/hooks: 本次只写设计文档

---

约束确认:
- 中文正文, 无 emoji
- 不 commit (不运行 git add 或 git commit)
- 不引外部 skill
- 只写 docs/codegraph_audit_integration_spec.md 一个文件
- docs/ 已存在, 本次无需 mkdir
- 本文件为设计草稿, 不代表任何 C1-C6 已实施
