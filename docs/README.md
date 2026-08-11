# ChunkyMonkey Docs

> 状态：live
> 作用：权威文档地图与生命周期规则。这里不保存历史清单或 session 状态。

## 权威顺序

| 顺序 | 文件 | 唯一职责 |
|---:|---|---|
| 1 | `../AGENTS.md` | Codex 在本仓库的操作边界、技能调度和交付纪律 |
| 2 | `../goal.md` | 当前 objective、优先级、blocker 和下一步 |
| 3 | `MASTER_TOPLEVEL_DESIGN.md` | 项目目的、Tier0-Tier4、模块/数据/配置/契约/证据和迁移路线 |
| 4 | `strategy_validation_contract.md` | 研究、PIT、消融、策略发布和纸面执行 |
| 5 | `engineering_governance.md` | 启动、工具、测试、并行、删除、文档和提交规则 |

辅助材料：

| 文件 | 角色 |
|---|---|
| `../PROJECT_INDEX.md` | 短项目导航与当前资产判断，不是规则 owner |
| `../FEATURE_MAP.md` | 机器生成的入口/数据域/writer 地图；可重建，不手改 |
| `../analysis/FOUNDATION_EXECUTION_PLAN.md` | 数据底座执行 backlog（evidence-only；`goal.md` 指向） |
| `../analysis/STRATEGY_EXECUTION_PLAN.md` | 后续策略执行 backlog（RX 前 BLOCKED；evidence-only） |
| `../analysis/DOC_CLEANUP_20260723.md` | 2026-07-23 文档收敛台账（kept/deleted） |

`CLAUDE.md` 是 legacy compatibility pointer，Codex 默认不读。旧 session handoff / workflow checkpoint 体系已经退役；新会话从 git、Moth、CodeGraph 和 live data 重建状态。跨 Cursor 账号续作时另读 owner 明示的 `../analysis/account_switch_handoff_20260720.md`（入 git，非旧 checkpoint 体系）。

**执行方案仅两份**（底座 / 策略）；禁止再写平行「主方案 / 支线方案 / 第三 bible」。立法仍只认上表三份 owner contracts。

## 文档生命周期

| 内容 | 去向 |
|---|---|
| 当前目标、阻断、下一步 | `goal.md` |
| 稳定架构/工程/验证规则 | 上表唯一 owner；优先修改，不新建平行文档 |
| 已完成工作与不可复现实证 | **commit message**（Q/Fix/Evidence/Residual）；检索 `scripts/chunkyctl history --grep`。时期叙事 = `era/*` annotated tag（`--eras`） |
| 可机器重建的地图/报告 | 生成器输出目录，标明 generated |
| 普通过程记录、旧计划、过期设计 | 删除；git history 已保留 |
| 一次性探索 | `sandbox/`，结束即清理 |
| **运行时状态**（frontier / watermark / 覆盖区间与天数 / 表行数 / DB 体积 / 窗口末端） | **不进任何人工维护文档**；现查 **`scripts/chunkyctl status`**（L2 单一入口，零文件；`--json` 给 agent）。执法由 `check_doc_runtime_state` 机械对账 |

禁止：

- 为同一主题建立 `v2/final/latest/revised` 平行文档；
- 把过期内容移到新的 archive-of-archive；
- 用“历史参考/暂留”让整份 stale 文档继续充当 active owner；
- 在 README 维护已删除文件的长列表；
- 把聊天结论当成唯一设计记录；
- **在人工维护的文档里写死会随运行而变的值**。判据一句话：**这个值会不会因为系统正常跑一次日更就变？** 会变 = 运行时状态，只能指向真相源；不会变 = 不变量（契约起点 `data_start`、已发生的休市日、故意冻结的 `holdout_start`），写死是对的。
  2026-08-10 实证：`goal.md` 写 `accepted daily→20260721`、`PROJECT_INDEX.md` 写 `→20260720`，而 `accepted_partition` 实测 `→20260804` —— 两份手写文档互相矛盾且同时落后实况两周，而 `check_doc_drift` 报 `stale_count=0`（它只查悬空链接与代码路径，不查语义与时效）。
  2026-08-11 追加实证（**为什么必须机器执法**）：上一条之后已做过一次人工清理，两周后 `PROJECT_INDEX.md` 仍残留 `→20260720`、`至 20260721`，且同一份文档里 `23/46 ssot` 与 `20/46 ssot` 自相矛盾（实跑真值 `ssot=20`）。**靠人扫必漏** → `check_doc_runtime_state` + `backend/config/doc_runtime_state.yaml`（默认禁止紧凑日期 + 显式豁免须写明为何是常量；豁免失效会自报）。同时修正上一条留下的坏指针：`BOARD.md` 本身第 6 行就写明「数据前沿请查 accepted 分区表，勿据此判断」，把 frontier 指向它等于指向一个声明自己没有该值的文件 —— 现统一指向 `scripts/chunkyctl status`。

## 修改规则

1. 先确定唯一 owner；
2. 用 CodeGraph、`rg` 和 `moth coupling` 查 fan-in；
3. 把仍有效内容并入 owner；
4. 更新代码注释、Moth、AGENTS、skills 和生成器引用；
5. 真删旧文件，不留 stub；
6. 运行文档门和 `git diff --check`。

```bash
PYTHONPATH=backend python backend/scripts/check_doc_governance.py
PYTHONPATH=backend python backend/scripts/check_doc_drift.py --check
moth assert --repo .
```

验收要求：`fails=0`、`warns=0`、无 dangling link、无 retired CLI 活引用、无第二 owner。
