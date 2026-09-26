# ChunkyMonkey — 项目规则（唯一一份）

跨项目协作方式在 `~/.claude/CLAUDE.md`，判断视角在 mio skill。本文件只放本项目独有、且没有门在守的硬约束；机器守的规则不在这里——看 `backend/config/governance_gates.yaml` 每道门的 `invariant`。目标看 `goal.md`；进度 `scripts/chunkyctl status`；历史 `scripts/chunkyctl history --grep <词>`（git 即原件）。

## 红线（违反即作废，不论结果多好）
1. PIT：任何观测量必须声明何时可见；决策时点只能读当时可得的数据；未来收益/概率/买卖信号不进状态与分桶维度；历史上某天的退市/ST 归属不得被之后的状态改写；加入未来数据不改变历史输出。
2. 交易日只从 `services.calendar` 取；universe 只从「t 日有名义 K 线 = 在交易」规则得；`dim_active_a_stock` 是身份缓存不是历史可交易性。
3. 缺失只能传播为缺失：NULL/unknown 不填 0、不 latest fallback、不 demo；测不出标 unknown。

## 数据
4. 依赖只向下：派生 ← 接受 ← 证据，每层可从下一层重生成；反向喂数据即错。
5. 能由「已注册域 + 公开规则」推导的，不注册取数域；推导物锁规则有效期，过期 fail 不 warn。
6. 一张表一个 writer；同一 DuckDB 文件的写串行——单写者，并发写真会写坏文件；跨进程读与写互斥，长写任务期间不开库；用新文件替换活库只经 `backend/services/duckdb_file_swap.py` 的 `swap_in_fresh_file`，换文件时原样搬运同住表不算第二个写者；表的约束与索引只由该表 writer 的 DDL 定义；审计默认 `read_only=True`。
7. 库文件按维护单元与更新频率划分，不按加工阶段拆：每个库文件日更后空闲块与死行按构造为 0，靠写法而不是事后压缩；整表重建只走「建到新文件 → 校验 → 原子换名」；land→accept 在同一文件内原子完成；版本是列或分区，不是 `v2` 表名。
8. 分类成员是数据不是 YAML；不同体系名称相同不等于等价；观点类（vendor_view）数据标体系名，不跨体系、不跨层级加总。
9. 展示只消费 accepted / 已发布面，不读 landing、不静默 legacy fill；输入 stale / unknown 时不伪造分数，标 unknown。
10. 先看供应商给了什么轴，再决定增量怎么做；轴不支持的增量形态不靠算力硬凑（禁 by-date invent、禁 count 未变全量重拉）。

## 配置与代码
11. 规则进 typed YAML，未知键 / 悬空引用 fail-closed；YAML 只描述"检查什么、比较什么"，不决定"跑什么、按什么顺序跑"——禁 plugin bus / 通用 DAG / YAML DSL。
12. 运行时状态（前沿 / 水位 / 计数）不写进任何手写文件，只 `scripts/chunkyctl status` 现查。
13. 一件事若无法机器验证，写进规则，别写进闸。
14. 缺 lineage = UNTRUSTED，不是"大概对"。

## 执行
15. `manual_only`：不装 cron / launchd / 隐藏触发器；数据更新只走 `bash scripts/daily_update.sh --date YYYYMMDD` 或 `scripts/chunkyctl sync --domain X`。
16. 提交只走 `SAFE_COMMIT_NO_PUSH=1 scripts/safe_commit.sh "<msg>"`；显式 stage 文件，不 `git add .`；不动他人未提交改动，不 revert / stage / commit 他人工作。
17. 探索只在 `sandbox/`；删东西不留墓碑 / stub / renamed-dead；测试不 mock 掉被测的 calendar / universe / population 门。
18. 清缓存不递归 `.venv/` / `node_modules/`——`.venv` 里有只以版本化 `.pyc` 发布的包，删了即坏。
19. 每段代码为一个目的而写，目的达到即合入：方案阶段写清目的与验收判据（可观测结果、预期数字）、影响面、与现有代码的重复或矛盾、边界情况、关键条件的测试计划；施工后的审查按功能→冲突→重复矛盾→简洁的顺序，只核「验收判据是否达成、有没有方案未声明的冲突」，简洁性只记待办，不按轮数停，挖出方案没覆盖的问题就回头补方案；给业主看的样品只修误导性错数就交。
