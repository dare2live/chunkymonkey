> **[状态更新 2026-07-02]**: 路线/顺序部分已被 `master_implementation_plan_20260702.md` (用户批准) 取代;
> 本文件的 5 界面框架/复用盘点仍是界面设计参照。注意: 写于批0 前, 引用的部分 entity/表已变
> (holders_top10 正名/ETF 全删/strategy_preset 删), 使用前对照 FEATURE_MAP 现状。

# Edge 展示/服务层框架设计 v1 (2026-06-28)

> owner: 主会话 (控制面草案, 待用户拍板后定稿)。状态: 框架设计, 未实现 (edge 层 0 代码)。
> 来源: workflow wzmp67x1v (4-agent 挖掘: 北极星范式/历史教训/平台底料/界面盲点) + 主会话综合 + 对抗盲点叠加。
> 北极星: 主升浪猎手 (episode-first 结果倒推); 这 5 界面是在纯数据平台上**从零重建**的 edge 层, 重建非复活旧码。

## 0. 设计第一性原理 (吸取的教训 → 做成结构门, 不是可选展示)

本项目踩过的所有坑, 在这 5 界面会**集中重演**。框架的核心不是画界面, 是把教训烤成结构性的门:

| 教训 (来源) | 在哪个界面重演 | 做成什么门 |
|---|---|---|
| **R1: IC≠可赚钱** (每日截面 spearman 减掉了 long-only 赚的 cohort 漂移) | 选股台/首页 | 判据=**含成本绝对收益**, IC 仅降级快筛; selector 按含成本 backtest 选不按 IC |
| **R2: 信号≠可交易头寸** (涨跌停一字板/T+1 open/非对称成本/容量) | 选股台/首页 | 回测引擎 execution-aware; **可买入率/容量=一等展示列**非事后系数 |
| **真金白银/感知死** (异常高=leakage 警报; forward 不回填=死) | 首页 sim | 含成本+execution-aware+全universe; **forward 对账**强制; 年化>100%/sharpe>5 自动冻结查 leakage |
| **结果倒推非信号正推** (3 次方法论漂移) | 选股台/机构档案 | 公式=episode 标注工具非策略; 机构信号=episode 验证非裸跟买 |
| **机构 latest-snapshot leakage** (inst_path_a, 2026-05-15 Codex BLOCK) | 股票/机构档案 | 机构历史收益**必 as-of**(披露日<=t 的持仓×t前价格); 禁 latest snapshot |
| **机构持仓 PIT 锚=披露截止日非报告期** (季报滞后~4月) | 机构档案 | PIT 锚=available_date/ann_date, 季中举牌用 ann_date |
| **形态 Weinstein 5-stage 被 measured 否定** (RankIC≈0/方向反) | 股票档案/选股台 | 形态用**正交轴**(位置×趋势×横盘)非 5-stage 单标签 |
| **selection-bias** (max_stocks 按 code 排序只取深主板) | 选股台 | 全 universe + assert_universe_clean 硬门 + 板块覆盖≥80% |
| **谄媚死** (报喜不报忧; 校验了不执行) | 全 5 界面 | **verdict/provenance 贯穿层**: 每个数字可点穿"含没含成本/IC vs 绝对/in-sample vs OOS/STAT vs MONEY_CONFIRMED/滞后几天" |
| **碎登记/死引用/god-file** (本 session F1-F4 根治) | 全 5 界面 (会产大量新派生表) | 每新表过 4 真相源门 + check_dead_references 硬门 + moth no-new-godfile |
| **数据滞后静默失败** (本 session G1-G4) | 工作台/全界面 | 数据新鲜度=界面一等公民; 四道防线状态可见 |

**最高优先铁律 (mio 核心视角#3 地基-上层偏序)**: edge 未经 D1-D4 + 含成本 OOS 裁决 (MONEY_CONFIRMED) 前, **首页 sim 不显示任何"像真的"收益数字**。KPI 当前=unknown/N/A, 界面默认诚实空态, 禁回填历史污染期旧数字 (+312%/+34.88% 等)。

## 1. 架构定位: 5 界面 = edge 层, 经 SERVE 读平台, 分三侧

```
                 ┌─ 运维侧 ──────────────┐
                 │  工作台 (数据更新+参数) │  ← 最独立, 平台已有能力的 UI 封装, 可最先交付
                 └───────────┬───────────┘
纯数据平台 (raw+四地基+SERVE) │
   DataAccess.get(entity,     ├─ 认识侧 ──────────────┐
     codes, as_of)            │  股票档案 · 机构档案    │  ← 读/解读已有数据 (维度解读器)
   → DataResult{rows,         └───────────┬───────────┘
     provenance}              │
                 ┌─ 决策侧 ───┴───────────┐
                 │  选股台 · 首页 sim       │  ← 依赖 edge (episode-first 信号+策略), asset_class=B
                 └───────────────────────┘  过验证门, 落 edge 隔离库 (feature_store/experiment_store)
```

**三条硬契约 (所有界面)**:
1. **取数只经 SERVE** `DataAccess.get(entity, codes, as_of)` — 禁裸 `FROM raw_*` / 裸 `conn.execute` 自写 asof (四地基不变量#1/#4; check_serve_read_layer 门)。
2. **派生产出分 A/B 登记** — 命中公式/形态/机构收益/选股结果/sim持仓 = asset_class **B** (含 forward/label/score/signal) → 落 edge 隔离库 (feature_store/experiment_store, 当前空), 禁写平台表; 纯确定性 PIT 重排 (如估值分位) = **A** 可进平台。每张新表过 4 真相源 (sync_registry/data_layers/data_access/lineage)。
3. **每个数字带 provenance+freshness** — SERVE 已返 DataResult.provenance; 界面强制显示来源/口径/滞后/裁决等级。

## 2. 贯穿脊柱 (反谄媚死的核心, design_specs v5 "状态色只来自机器判决")

5 界面**共享**一个裁决/证据贯穿层, 不是各页独立看板:

- **统一 as_of 上下文**: 平台 watermark 驱动单一 as_of 贯穿全界面 (禁各页自取 latest 互相打架)。
- **数据可得性矩阵**: 每维度标 live/已停披露(北向个股)/季报滞后(机构持仓~4月)/浅史(surveys 2025-04+)/needs_review(cyq winner_rate C0 FAIL) — 喂界面默认态, 缺=诚实 unknown 不假填。
- **裁决等级徽章**: 每个分数/收益挂 `UNKNOWN < STAT_EDGE(IC/统计) < MONEY_CONFIRMED(含成本OOS)` + 可点穿到证据 (含没含成本/in-sample vs OOS/selection-bias)。
- **provenance badge**: 来源 vendor + PIT 锚 + 数据滞后天数。

## 3. 逐界面设计

### 3.1 工作台 (运维侧, 最先交付, 不依赖 edge)
- **职责**: ① 每日数据更新操作面 ② 参数管理 (阈值/权重/策略参数)。
- **数据/能力 (已有)**: `ops_manual_run` router (手动跑数据链, KEEP) + pipeline/ + 治理 mart (data_health/watermark/failure_queue/pipeline_run_manifest) + lineage CLI。
- **核心难点+门**:
  - 更新面**不止"跑"按钮, 还要"跑成功了吗+数据新鲜吗"**: 暴露四道防线状态 (探活/watermark+drain/SLA/ALERT flag) + 每域 data_start/滞后 + 可买入率体检 (本 session 静默失败教训)。
  - 参数管理=**判断死最后防线**: 参数面板真读写 config (yaml) 非 hardcode; 阈值人话 (J1: "均线斜率>6.5%" 非 `ma_slope:0.0655`) + 边界联动 (J2: 调一个报受影响股票 delta)。
  - **盲点**: 改"策略参数"后**强制重走 walk-forward OOS 裁决再生效** (防改完看 in-sample 好数字=选参 peek); 参数变更审计链 (谁改/版本/回滚)。

### 3.2 股票档案 (认识侧, 第二, 大部分读现有数据)
- **职责**: 单股信息合集 — 命中哪些公式 / 当前形态 / 哪些机构持有 / 这些机构的历史收益。
- **范式**: 维度解读器协议 (DimensionInterpreter, stock_dossier_master_design §2) — 每维度独立模块 4 接口 (interpret/series/compare/screen)+config, 档案聚合器**按需调各维度不建大宽表** (违奥卡姆+随维度爆列)。
- **数据源 (经 SERVE)**: K线qfq + 估值分位(自算=A) + 十大流通股东/org_holding(机构持有, available_date PIT) + cyq筹码(winner_rate needs_review) + v_sw_industry_pit(申万行业) + dim_stock_dc_concept(东财概念) + 财报/预期。
- **核心难点+门**:
  - **"这些机构的历史收益"=头号 leakage 雷** (inst_path_a latest-snapshot 重演): 必须 as-of (披露日<=t 的持仓 × t 前价格 forward 现算), 禁拿"机构截至今天的收益"标注历史信号。机构持仓 PIT 锚=披露截止/ann_date 非报告期。
  - **"命中哪些公式"**=公式在该股历史赢家 episode 上的标注成色 (PIT 命中时点), 非今天金叉; "当前命中" 须明确 as_of。
  - **"当前形态"**=正交轴 cell (位置×趋势×横盘), 非 Weinstein 5-stage; 是 Type A 确定性 PIT 重排可常驻但禁混 forward/score 列 (type_a_leak 门)。

### 3.3 机构档案 = 机构跟随策略 (认识+决策侧)
- **职责**: 跟踪机构持仓/调研 → 跟随价值评估 + 跟随信号。
- **数据源**: inst_institutions(240注册) + inst_holdings(2024-12+浅) + org_holding_aif10(2019+深, 非公募分桶) + top10_floatholders(2005-2026) + qfii + raw_lhb_daily(龙虎榜席位) + surveys(调研)。
- **核心难点+门 (信号正推第二陷阱)**:
  - **"机构买→跟买"=裸信号正推**: 正解 (design_specs 14_ia) = 先用持仓深史×K线 forward **含成本**算每个机构的**历史跟随收益** (谁值得跟) → "第二次行为标准" (非首次出现就跟, 要二次加仓/连续确认)。
  - **信号时点=披露日非报告期** (季报滞后~4月); 跟随信号在披露日才可知 → 信号延迟可能让 alpha 缩水甚至归零 (参北向个股 follower 零超额先例)。
  - **跟谁**: 北向个股已停披露(2025-08起0行=dead-forward, 只历史)/概念流 IC≈0(同步非领先)/ths_member 无 PIT(出局) → 只能跟十大流通股东(aif10)/QFII/龙虎榜超级席位/调研。
  - **容量+拥挤**: 信号公开披露后跟随者多→滑点摊薄, "历史收益漂亮"≠跟得进。

### 3.4 选股台 = 各公式选股情况 (决策侧, 依赖 edge)
- **职责**: 项目各"公式"的选股情况 (公式触发哪些股)。
- **核心难点+门 (最大老路诱导陷阱)**:
  - 用户字面"公式触发了哪些股"天然=**信号正推+公式当策略+无条件截面三重违范式**。正解: 公式=episode 标注/确认工具 (在已知赢家 episode 上验哪些公式能套住起涨/卖出), 每个公式卡**必挂含成本 OOS 成绩单** (record_verdict) + **"此公式适配哪个形态/阶段"** 的 stage-conditional 上下文。
  - 必 **stage-conditional** (阶段内排名) 非全市场无条件截面 (撞 R1 墙, 含成本 -14~-35%)。
  - **公式范围**: 旧公式库 (MACD/海龟/神奇九转/动态均线 18-20 个) 代码已 git rm + 70/78/86% 是污染期假设 → **不复活**, 新公式从零重建只进 episode 验证过有效的。
  - 多公式聚合**单一资金口径** (东财 moneyflow_dc 链自洽, 禁混 tushare net_mf); universe 硬门 + 板块涨停阈值适配 (主板10%/创业科创20%)。

### 3.5 首页 = 实盘模拟 (决策侧, 最后, 依赖 edge MONEY_CONFIRMED)
- **职责**: 实盘模拟结果 (持仓≤3股 / 可现金 / 仓位≤80% / 100万起步; **策略本身"再讨论"**)。
- **核心难点+门 (真金白银, 死亡条款全压这)**:
  - sim 一出年化数字就是**真金白银裁决面非展示面**: 含成本 (印花税卖方非对称+佣金+滑点) + execution-aware (涨跌停一字板剔篮+T+1 open 入场非 close 假成交+停牌缺价不静默剔出) + 容量 + 全 universe。
  - **forward 对账** (预测 vs 实际兑现逐日) = 命脉非展示 (感知死: forward 不回填=死); 异常高 (年化>100%/sharpe>5) 自动冻结查 leakage。
  - 100万/≤3股/≤80%仓=**约束 (Execution 轴, MASTER §5 第五轴一等设计)**, 但不替代裁决; 顶部应有 regime 门 (当前市场态→仓位动态调节, MASTER §5 第四轴: long-only 赚钱主来源=在对的时候在场)。
  - 超额基准: 主升浪猎物多小盘→**对标中证1000/2000** 非只 HS300 (防 selection-bias 高估 alpha)。
  - 选股来源待定 (edge 候选池 / 公式 / 机构跟随 / 聚合) → 框架做**可插拔 strategy adapter** 不锁死。

## 4. 复发防护 (复用本 session 立的门, 不重建)

- **碎登记**: 5 界面产大量新派生表→每张过 4 真相源 (sync_registry/data_layers A-B/data_access entity/lineage)。
- **死引用**: check_dead_references 硬门 (safe_commit Step3.97) — 删界面模块必清引用方。
- **god-file**: moth no-new-godfile + minimal-module-main-routers — 每界面 read service 单一职责小模块 (反例: 旧 dossier.py 553行/screening 631行)。
- **谄媚死**: 每数字带 verdict+provenance, 0信号/Gate FAIL/滞后先讲。

## 5. 建议重建顺序 (依赖驱动)

| 序 | 界面 | 依赖 | 可交付性 |
|---|---|---|---|
| 1 | **工作台** | 平台已有 (ops_manual_run/pipeline/治理 mart) | **最先, 不依赖 edge** |
| 2 | **股票档案** | SERVE 维度解读器 (读现有数据) | 早, 大部分维度数据已在库 |
| 3 | **机构档案** | 机构历史收益 edge 计算 (B) | 需先验跟随在含成本+披露延迟下有无 edge |
| 4 | **选股台** | episode-first 公式验证 (edge D1-D4) | 需 edge 重建 |
| 5 | **首页 sim** | edge MONEY_CONFIRMED 真策略 + 含成本回测引擎 | **最后, edge 未裁决前只占位** |

## 6. 现存待修 (本设计揭出的当前 bug, 非新建)

- **holdings.py 裸读绕 SERVE**: `conn.execute` 直建 _cache_stock_latest_rd 从 fact_top10_holder_period 取 + 引用**已删表 mart_current_relationship** (死引用)。check_serve_read_layer 门未拦到 → 需修 (走 SERVE + 去死引用)。
- **institution_survey vendor 冲突**: SERVE entity 仍指 tdxhub raw_institution_surveys (1.45万行/8月), 但 tushare stk_surv 已 sync 36万行 → 撞 tushare 唯一红线, 需 repoint。

## 7. 待用户拍板 (见对话)
重建顺序确认 / 首页 sim 占位策略 / 机构历史收益口径 / 选股台公式范围 / serving 形态 / 超额基准 / institution_survey repoint。
