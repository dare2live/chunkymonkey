# ChunkyMonkey Goal

> 当前阶段契约 only。完成项 → `analysis/project_state_ledger.md`;运行态 →
> `SESSION_HANDOFF.md`。保持 < 165 行;item 完成就移 ledger 只留当前决策/blocker。

## Document Contract

| Document | Owns | Startup use |
|---|---|---|
| `docs/MASTER_TOPLEVEL_DESIGN.md` | 综合顶层设计骨架 (数据→因子→策略→验证→KPI) — **注: 策略/edge 段为未来蓝图, 当前未实现** | 先读它有全局 |
| `goal.md` | 当前阶段目标 / 优先级 / genesis 法 / 路线 | Read first |
| `analysis/project_state_ledger.md` | 完成项 / 历史状态 / 证据 | `rg`/`tail` 查, 不全读 |
| `analysis/data_platform_architecture_20260628.md` | **纯数据平台架构 (重建后真相源)** | 数据平台权威 |
| `SESSION_HANDOFF.md` | 运行态恢复快照 | Context-only, 以 live gate 为准 |
| `docs/data_management_framework.md` | 数据层级框架 + 三原则 + 自动执法 | 数据管理权威 |
| `docs/chunkyctl_session_quickstart.md` | 启动流程 | 启动契约 |
| `docs/README.md` | docs 地图 + 权属 | 文档权威图 |

## 创世层 (项目法, 重建的验收标尺)

**为何存在**: 把 A 股公开数据 (K线/财报/筹码/资金) 转成真金白银的 KPI 级回报 —— 不是论文, 不是数字游戏 (用户原话: "真金白银投入的")。

**死亡条款** (≤3, 陌生人可判):
1. **感知死** — forward 预测从不回填对账 / 异常高回测 (RankIC>0.3, sharpe>5, 年化>100%) 不查 leakage 就上线。
2. **判断死** — 阈值/权重/策略组合 hardcode 进代码而非 config。
3. **谄媚死** — 报喜不报忧 (0 STRONG_BUY / Gate FAIL 不先讲) / 只调对你舒服的方向。

**判断法典种子** (人话 → 机器话, 自动执法):
| 人话 | 机器话 (moth/gate) |
|---|---|
| 每张表必声明所属 layer | `moth data-layer-integrity` |
| 不留 god-file / 万物互引 | `moth minimal-module-main-routers` + `no-new-godfile` |
| 删的层不被启动重建 | `services/schema_layer_filter` + schema DDL 只留 KEEP 集 |
| 文档不准漂移 | `moth doc-drift` |
| 数字 measured 可复现 | `check_rule_compliance` |
| 数据源唯一 tushare (+aif10 十大流通股东) | §4.3; sync_registry 唯一采集契约 |

## North-Star KPI (唯一 owner: 本文件) — **未来目标 (策略层重建后才适用)**

| 指标 | 角色 | 目标 | 口径 |
|---|---|---|---|
| 年化收益 | **目标量** | >= +30% | 含成本 OOS paper_sim, 100 万初始 |
| 最大回撤 | 特征化输出 | 不设硬上限 | 报为"拿到最高胜率/收益所需承受的回撤", 与年化/胜率成对呈现 |
| 超额 vs HS300 | 目标量 | > 0 | 真实基准 (小盘 cohort 对标中证1000/2000) |
| 月胜率 | 诊断量 | >= 55% | walk-forward OOS 月度; 单独不放行 |
| 胜率×盈亏比期望 | 诊断量 | > 0 | positive_expectancy |

**C-WinReturn**: 胜率是诊断量, 收益率+max_dd 是目标量; 全 AND 单项不放行。仓位管理是一等设计轴。
**当前 KPI = `unknown` (N/A)** —— 策略/edge 层 2026-06-28 重建中清空, 无 live 策略可测; edge 在干净平台上重建后才有数字。不许引用历史旧数字。

## 项目现状: 纯数据平台 (2026-06-28 重建定型)

**用户决议 (2026-06-28)**: 项目降为**纯净数据平台** —— 只留 ① 原始数据 (tushare 唯一 + aif10 十大流通股东) ② 优化后的四地基 ③ 数据平台代码 (采集/清洗/SERVE/治理)。**所有加工中间变量 + 策略/serving/edge/workbench 层全部退役** (代码 ~245 文件 git rm 可逆 + 数据 ~40 表 archive parquet 留底), 待未来在干净平台上**从零重建** edge。

**平台分层 (当前真相源)**:
- **M1 采集** (`services/data_sources/` + aif10 client): vendor → L0 raw (raw_tushare_* 40表 + aif10 域 [holders/qfii/org_holding/估值]), 零计算。lhb/surveys 已切 tushare (top_list/top_inst/stk_surv, 批2)。
- **M2 清洗** (`market_*`/`pipeline/clean`): L0 → L1 qfq (v_price_kline_qfq tushare-only, PIT 复权)。
- **M4 SERVE** (`services/data_access/`): 唯一取数 + PIT asof + 口径锁 + provenance (raw entity 读路)。
- **四地基**: ① 主键+PIT锚 ② 读写边界=库分区 (§9 reference.duckdb 拆库 DONE) ③ 可扩展分层 (data_layers L0-L4) ④ 单一真相源 (tushare+aif10, leakage洞=0)。
- **编排** (`pipeline/`): acquire/clean/store/run/stage_runner; daily 门链。
- **血缘** (`services/lineage/`): acquire+consume DAG (impact/provenance/dead CLI)。
- **治理** (`audit`/`data_audit`/`data_quality`/moth/`check_*`/`storage_retention`/`sandbox_guard`): 11 治理 mart + 全套门。

**库现状 (2026-07-02 批7 收敛后实测)**: tushare_raw 7.3G (40 raw + 2 PIT行业视图) · market 737M (price_kline_qfq_tushare 831万 → v_price_kline_qfq) · reference (4 dim) · smartmoney 320M (30 表: aif10 域 + dim + 治理 mart) · etf/feature_store 空壳 · experiment_store (verdict 契约)。

**验证 (2026-07-02 批7 收敛后)**: 全量 pytest 413 passed (10 既存债, stash 基线一致) + CI offline 188 + moth 30/0/0 + data_layer_audit PASS (untagged=0, **stale_tag 首次归零**) + check_dead_references 0 + lineage graph 208节点无死表。

**数据纯化 (批0-7, 2026-06-28~07-02) 已收敛**: 非 tushare/aif10 残表全物删 (ETF 子系统/机构旧表/top10/旧K线管线, archive 留底) + 死代码/config/断言/死闸清零 + 控制面重写 (PROJECT_INDEX 708→300 判断层) + 磁盘 15G→9.5G。4轮 sweep 验 dry, 代码/config/DB 层 is_dry=True。

## 下一步 (用户定方向)

数据平台已纯净收敛, **edge 重建已开工** (2026-07-02): 路线唯一 owner = `analysis/master_implementation_plan_20260702.md` (用户已批: A 档案API→B 基础件[分层/形态/两融]∥C 前端React+Vite→D 主升浪[holdout预算立法]→E 整合)。已落地: **W1 机构画像引擎** (mart_inst_profile 9.4万, feature_store L2) + **W2 实盘模拟通用件** (手动, /api/v3/paper/*, 各策略共用)。机构跟随设计 = analysis/institution_follow_strategy_design_20260702.md。原则不变:
- 北极星目标仍是**主升浪猎手** (episode-first 结果倒推: 找赢家 episode → 反推 PIT 入场/持有/出场特征 → 含成本 OOS 裁决)。详见 `docs/MASTER_TOPLEVEL_DESIGN.md` §5 (蓝图, 未实现) + `analysis/zhushenglang_hunter_plan_20260617.md` (历史方案, 重建参考)。
- **D1 Ground Truth 已 archive** (rally/macd episode GT parquet 在 `data/archive/purge_processed/`); edge 重启时从 raw K线**重新生成** (不复用旧 GT)。
- 重建 edge 必守创世层死亡条款 + 四地基不变量 + 含成本可交易裁决 (R1/R2): IC≠可赚钱; 回测须 execution-aware (涨跌停/T+1 open/非对称成本/容量)。
- archive 的策略代码 (git 史) + 数据 (parquet) 可作重建参考, 但**重建非复活** (旧码逐行核 + 单测证伪门)。

## Operating Reminders

- 主动用全套工具/skill (不等点名): `mio` (思维真相源) · `chunkymonkey-ops` (操作手册) · `architect-controller` · moth · codegraph · workflow (并发)。
- 第一性原理真相源: K线=可交易性 / 日历=日期 / config-table-service owner=业务规则; 数据源 tushare 唯一 (+aif10 十大流通股东)。
- 删确定死的路径直接删, 不留注释/隐藏flag/兼容垫片; 删表必删重建路径 (schema DDL); 不 big-bang 硬删紧耦合层 (按 layer 增量, moth 守)。
- commit 走 `bash scripts/safe_commit.sh`; 大改动 (数据语义/策略/资金路径) 走对抗复审。
- 历史详情 (reset 前 Strategy Portfolio / 旧 board / 重建删除清单 / live gate) 见 ledger + archive; 不在本文件保留。
