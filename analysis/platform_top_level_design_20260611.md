# 平台顶层设计 — 数据可靠性 × 解耦 × 数据驱动前端

> **[状态校正 2026-06-26 doc治理]** 本文 durable 设计契约多数仍有效 (可靠性/解耦/数据驱动前端三轴), 但**4 不变量真相源已升级为 `analysis/data_module_toplevel_design_20260622.md` (创世宪法 v2.0)** — 统一主键+PIT锚 / 读写边界=库分区 / 可扩展分层 / 单概念单真相源。M5 血缘中枢 (字典+总指挥) 已建 (`backend/services/lineage/`, commit 766a12ce)。读本文取设计意图, 取现状/4不变量以 0622 宪法 + goal.md 为准。

> 2026-06-11 应用户三痛点而立: "数据获取与存储每天出问题 (数据是一切的基础)" /
> "耦合性受够了, 牵一发动全身" / "前端不是数据驱动决策, 决策驱动流程"。
> 性质: durable 设计契约。live 状态/进度在 `goal.md`; 历史背景在
> `architecture_reform_context.md`; 数据产品细则在 `data_product_contract.md` (本文不取代它)。
> 证据基线: 2026-06-11 三路只读审计 (前端 / 管线 / 耦合), 关键数字均经主会话复核。

## 0. 三个痛点, 一个根因

三个痛点是同一病的三个症状: **系统从"脚本集合"长成了"平台", 但没有统一的运行时契约。**

- 数据层: 6 条摄入路径 4 种失败处理范式 — 只有 sync_runner 一条是"注册表驱动 +
  watermark + 失败入队"; 其余各自为政, 失败靠 `|| log WARN` 吞掉继续跑。
- 模块层: 模块间用 Python import 通信 (205 个文件 import `services.db`, 119 个文件
  依赖 `duck_adapter`), 改一处全链震动 — "牵一发动全身"的字面机制。
- 前端层: 业务阈值硬编码在 JSX 里 (10+ 处), API 失败静默回退 mock — 决策逻辑
  散布在最远离数据的地方。

### 平台五原则 (宪法 v2 在平台层的落点)

| # | 原则 | 一句话 |
|---|---|---|
| P1 | 注册表驱动 | 行为=数据不是代码: 数据域/策略/前端卡片都是注册表条目, 加东西=加条目 |
| P2 | 单一执行器 | 一类工作恰一个 runner; 禁止第 N 个脚本带第 N 种失败处理 |
| P3 | 表即边界 | 模块间只通过"带 grain + as_of + freshness SLA 的表"通信; 每表恰一个 writer |
| P4 | 失败必送达 | 任何路径失败 → ALERT flag + 通知; `\|\| log WARN` 继续跑 = 违宪 (第五条) |
| P5 | 前端零决策 | 前端只渲染决策表 + 配置 API; 0 业务阈值, 0 静默降级 |

---

## A. 数据平台 (痛点: 数据是一切的基础)

### A.1 现状: 路径 × 机制矩阵 (2026-06-11 审计 + 主会话复核)

| 路径 | 触发 | watermark | 失败处理 | 告警送达 | SLA audit |
|---|---|---|---|---|---|
| sync_runner (tushare 16 域) | registry/手动 | YES | 重试+failure_queue | wrapper | 部分 |
| daily_update 主链 Step 2d-2l (9 子步) | launchd 17:00 | 7 源无 | **`\|\| log WARN` 吞错继续** | 仅整链失败 | 部分 |
| concept-snapshot | launchd 17:40 | 无 (parquet 文件级) | wrapper 报错 | wrapper | 无 |
| aif10/akshare 手工路径 | daily_update 内 | 无登记 | WARN-only | 无 | 无 |
| backfill_* 脚本族 | 手动 | 各异 | **4 处 `except Exception: pass`** | 无 | 无 |
| nightly_data_audit | launchd 02:00 | — | — | wrapper | 8 表有, 5 表缺 |

实证 (复核过的源码位置):
- WARN-only 吞错: `scripts/daily_update.sh:201-257` 等 12+ 处 `|| log "WARN: ..."` —
  子步失败整链照常 exit 0。这正是 external_attention 14 天断流无人知的根因链第一环。
- failure_queue 只进不出: `mart_data_source_failure_queue` 有 writer
  (`sync_runner.py:103`) 和展示 reader (`workbench_data_source_read.py`), **无重放消费者**。
- 异常吞错: `backfill_sector_momentum_history.py:278` / `backfill_risk_factors_history.py:217`
  / `backfill_capital_flow_pit.py:270` / `backfill_financial_pit.py:213` 均为
  `except Exception: pass`。
- 当日实弹 (chain1 moneyflow 回填): 28 次终败 (14 read-timeout / 12 zero-rows /
  2 并发上限) 全部正确入队 — 但因无 drain, 这些日期若不手工补就是永久空洞。
- 可观测性缺口: runner 成功静默 (只在失败时打日志), 看进度只能看 DB mtime。

### A.2 目标架构: 全域收敛到"注册表 + 单一执行器 + 四道防线"

```
sync_registry.yaml (P1: 域定义 = 数据)
   每域: source / mode / grain / pit_key / freshness_sla / retry / alert_level
        ↓
sync_runner (P2: 唯一执行器)
   防线1 协议层探活 (源健康, 不信 TCP connect)
   防线2 watermark + failure_queue + **drain** (断点续传 + 失败重放)
        ↓ 落库
data_audit generic checker (防线3: 从 registry 自动生成 freshness SLA 检查, 不再手写名单)
        ↓
launchd wrapper (防线4: 失败 → ALERT flag + 通知送达)
```

关键收敛动作: **`daily_update.sh` 从"288 行编排逻辑"退化为薄壳** — 按 registry
顺序调 runner → 跑 audit → 失败分级上报。编排=数据 (registry 里的 DAG 顺序字段),
不是 bash 里的 if。

失败语义分级 (取代一刀切 WARN):

| 级别 | 含义 | 行为 |
|---|---|---|
| `fatal` | 下游决策表依赖 (K线/日历/复权) | 整链停 + ALERT |
| `degraded` | 卫星数据 (LHB/调研/外部关注) | 继续跑 + 当日 ALERT flag + audit 记 stale |
| `optional` | 实验性域 | 继续跑 + failure_queue, 周报汇总 |

每域级别写进 registry, 不写进 bash。"degraded 也必须送达"是和旧 WARN-only 的本质区别。

### A.3 立即修复队列 (按再断流风险排序, 不给伪概率)

| 优先 | 修复 | 落点 |
|---|---|---|
| P0-1 | daily_update 12+ 子步接失败分级 (fatal/degraded/optional), degraded 也写 ALERT flag | `scripts/daily_update.sh` + registry 加 `alert_level` |
| P0-2 | 7 个无 watermark 源补登记 (LHB/institution_survey/external_attention/profit_forecast/tdx_industry 等) | `source_watermarks.py` 登记 + 接 audit |
| P1-3 | `failure_queue_drain`: 每日链尾自动重放 status=open 的失败, 重放仍败才升级告警 | sync_runner 子命令 |
| P1-4 | data_audit SLA 检查改 registry 驱动自动生成, 顺手补上缺的 5 表 | `data_audit_rules.yaml` → 派生 |
| P1-5 | 4 处 `except Exception: pass` 改 raise / 入队 | backfill_* 4 文件 |
| P2-6 | runner 进度心跳 (每 50 批 INFO 一行: 域/进度/失败计数) | sync_runner |
| P2-7 | concept-snapshot parquet 加 `.metadata.json` (行数/日期/源版本), 长期迁 runner 范式 | snapshot_concept_daily |

### A.4 存储分层 (既有方向, 此处定为契约)

| 层 | 库 | 写者 | 规则 |
|---|---|---|---|
| Raw landing | `tushare_raw.duckdb` (已拆) + 未来 per-source raw 库 | 仅 sync_runner | 长回填写锁不挡主库; MERGE on grain 幂等 |
| 主库 | `smartmoney.duckdb` | 各表唯一 writer (P3) | 从 raw 短窗 merge; 清理即拆分 (manifest 既有方向) |
| Artifact | parquet / json + manifest 登记 | 产出脚本 | 不可变, 只追加, 验证产物永不覆写 |

DB 锁实证 (当日): 回填进程持写锁期间主会话连 read_only 都进不去 — raw 层独立库
就是为此; 主库写窗口必须短。

---

## B. 解耦 (痛点: 牵一发动全身)

### B.1 耦合热点 Top 8 (量化证据, 主会话复核标 ✓)

| # | 热点 | 证据 | 切缝 |
|---|---|---|---|
| 1 | `services/db.py` 门面 | 205 文件导入 ✓ | 保留门面但冻结接口: 显式 re-export 白名单, 新代码直连子模块 |
| 2 | `duck_adapter` | 119 文件依赖 ✓ | 视为稳定基础设施, 接口冻结 + 契约测试; 不拆 (拆它才是牵一发动全身) |
| 3 | `data_quality.py` | 4286 行 ✓ 四合一 (规则+SQL生成+执行+报告) | 拆 RuleRegistry(声明式 yaml)/Generator/Executor/Reporter 四层, 规则新增不再触代码改动 |
| 4 | `schema_versions.py` | 481 行 71 导入 | 按数据域拆注册表, 域内 schema 变更不再全局广播 |
| 5 | 60 个 config yaml ✓ | 同参数多 yaml 风险 (TradingCostConfig 双源前科) | 参数 owner 制: 一参数一 yaml 一 owner, 新增前 `rg` 查重入 pre-commit |
| 6 | `pipeline_manifest` | 63 导入 | reader/writer 分离 + 加载时版本快照 |
| 7 | connection 字符串散落 | ~92 定义点 | 全部走 database_manifest 解析器, 禁裸路径 |
| 8 | Config 类双源残余 | 72f1436c 前科 | 枚举全部 *Config 类做一次双源审计 (一次性) |

### B.2 切缝三原则 (比"拆模块"更便宜的解耦)

1. **共享表 > 共享代码**: 两模块要同一份数据 → 通过表 (带 grain/as_of/SLA), 不通过
   import 对方函数。六层契约 D1-D6 的物理实现就是表链, 不是调用链。
2. **单 writer**: 每张 fact/mart 恰一个 writer 模块, manifest 登记; 第二个 writer
   出现 = 设计错误。读者随便加 (read_only)。
3. **接口冻结 > 重构**: 高扇入模块 (db/duck_adapter) 的正解不是拆而是**冻结+契约
   测试** — 205 个依赖者要的是"它不变", 不是"它更优雅"。重构预算花在低扇入高变更
   的 God module (data_quality) 上。

### B.3 连锁修复实证 (为什么值得做)

- denormal 停牌修复 → panel v5 PIT 列过滤补洞: 同根源跨两模块, 因为 K 线消费者
  没有统一入口 (各自 JOIN raw 表) — 表契约 + 单 writer 后此类传染消失。
- TradingCostConfig 双源消灭历经 6+ commit — 双源的修复成本是预防成本的数量级倍数,
  这就是 #5 参数 owner 制进 pre-commit 的理由。

---

## C. 数据驱动前端 (痛点: 决策驱动流程)

### C.1 现状: 违反的是已成文契约

`data_product_contract.md` 已规定 "UI may show unknown/proxy/stale, but must not
dress them as production facts"。审计实证 (file:line):

- **静默 mock 回退**: `design/v3-data-live.jsx:27-34` `fetchJson` 捕获一切错误返回
  null → 页面无感知地用 `v3-data.jsx` (文件头自注 "mock data") 顶上。用户看到的
  推荐可能是假数据, 无任何 badge。
- **数据缺失被硬编码化**: `v3-data-live.jsx:117` `stability: 0.80` — mart 没有这列,
  前端编了一个, 把数据缺陷化妆成事实。
- **决策阈值散布前端** (10+ 处): `v3-page-picks.jsx:78` (`winrate>=0.58` 模型健康) /
  `:329` (机构胜率红黄绿 0.6/0.5) / `v3-page-lab.jsx:163` / `stock-view.js:356`
  (Top20/Top50 分割) / `:345-372` (过滤规则全前端定义) …改一个门槛要改 JSX。
- **双版本静默择源**: `v3-data-live.jsx:244-258` SELECTION_BOARD 新旧 API fallback,
  用户不知道看的是哪个版本。

### C.2 目标: 前端 = 决策表渲染器

```
决策表 (mart_*, 后端唯一决策出口)
  + 展示配置 (frontend_config: 阈值/排序/分割线, yaml 真相源)
        ↓ 薄 API (无业务逻辑, 透传 + as_of + freshness)
前端 (渲染 + 交互; 0 阈值, 0 mock, 0 双源择优)
```

- 每个数据卡片自带 `as_of` + freshness 状态 (来自 registry SLA) — 新鲜度是
  first-class 展示要素, 不是开发者控制台里的 warn。
- fallback 政策: 生产模式禁 mock; 数据不可得 → 显式 `stale` / `unknown` badge
  (即执行 data_product_contract 既有条款); mock 仅 dev flag 显式开启。
- 阈值出口: `GET /api/v3/config` ← `backend/config/frontend_config.yaml` (新增,
  owner=前端展示参数)。前端启动拉一次, hardcode 全部改 `CONFIG.get(...)`。

### C.3 分阶段切口 (不重写框架 — 奥卡姆)

| 阶段 | 动作 | 量级 |
|---|---|---|
| 急救 | ① `fetchJson` 失败改派发 error 事件 + 页面 badge, 禁静默 null; ② `/api/v3/config` + frontend_config.yaml, 10+ 硬编码阈值迁出; ③ `stability` 改返回 NULL 显示 "—" | 天级 |
| 框架 | ConfigAdapter (仿 signal-adapter 模式) + dataReady 带 per-source `{status, source, as_of}` metadata | 周级 |
| 透明化 | 每卡片 freshness badge 接 registry SLA; SELECTION_BOARD 双源择一退役旧源 | 周级 |

React CDN 零工具链架构**保留** — 痛点不在框架, 在决策逻辑放错了层。重写前端
= 发散, 被宪法第九条 gate 拦。

---

## D. 路线图合流与验收

- 数据平台 P0-1/P0-2 并入 Task #1 (TuShare 生产化五 gate) 同一工作流 — 都是
  "watermark/freshness/failure-queue" 同族。
- 前端急救三件套独立小批次, 不与数据平台改动同 commit。
- B 线解耦不设专项工程: #3 data_quality 拆分挂在下次触碰它的需求上顺势做 (P3 原则
  新代码即日生效, 存量按"碰到才改"消化, 防大爆炸重构)。
- **验收口径 (宪法第八条)**: 每条 P0/P1 修复必须附"断流不可能性论证" — 该路径
  在四道防线上各指出一个具体机制 (探活/watermark/audit/送达), 缺一道就没修完。
  不接受"概率从 X% 降到 Y%"这类伪精度叙事。

## E. 本设计的自限 (防发散)

- 不重写前端框架; 不拆 duck_adapter; 不一次性迁移所有旧路径 (逐域, 每域单独验收)。
- 新抽象只允许两个: failure 分级 (registry 字段) + frontend_config.yaml。其余全是
  收敛既有机制。
- 本文件是设计契约, 不是状态账本 — 进度记 goal.md, 完成证据记 project_state_ledger。
