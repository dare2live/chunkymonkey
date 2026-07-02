# 总体实施方案 — edge 重建 + 主升浪猎手 + 机构跟随 (2026-07-02 顶层定稿 v1)

> owner: 主会话 (控制面)。状态: 顶层方案待用户 review, 各 Phase 动手前再细化 grill。
> 输入: 用户 2026-07-02 定调 — ①主升浪验证方法论 (结果倒推+逐层叠加+分层细分, 原话见 §2) ②形态识别=独立模块
> ③股票分层前置为基础加工 ④机构档案 API+edge 界面继续 ⑤"结合后续所有工作顶层设计实施方案和顺序和细节"。
> 关系文档: goal.md (KPI/死亡条款) · data_platform_architecture_20260628 (地基) · institution_follow_strategy_design_20260702 (机构跟随)
> · edge_layer_framework_design_20260628 (5界面, 需按本方案复核) · docs/MASTER_TOPLEVEL_DESIGN (蓝图)。

## 0. 路线图总览 (顺序 + 依赖)

```
[已完成] W1 机构画像引擎 (mart_inst_profile 9.4万) + W2 实盘模拟通用件 (手动, /api/v3/paper/*)
    │
A. 机构档案 API (SERVE 暴露画像+信号流)          ← 半天, 无依赖, 立即
    │
B. 基础前置件 (Type A 平台层, 每日数据获取后跑)   ← 用户裁决"前置在所有策略之前"
 ├─ B1 股票分层模块 [DONE 2026-07-02] (dim_stock_segment_daily 833万行, process 步)
 ├─ B2 形态识别模块 [代码 DONE 2026-07-02] (正交5轴重建, 旧实现对抗审查 14 缺陷修正; 全量 rebuild 验收中)
 ├─ B3 两融采集域 [DONE 2026-07-02] (margin_detail 464.9万行 1816/1816 零缺日 + margin 汇总)
 └─ B4 市场感知引擎 [DONE 2026-07-02] (mart_sector_pulse_daily 33.8万行两链 + market 844行, smoke 过)
    │
C. edge 前端 v1 [骨架 DONE 2026-07-02: React+Vite 档案/实盘模拟页 build 绿]
 ├─ C1 骨架 + 机构档案页 (画像热力图/episode时间线/维度表现) [A 档案API DONE]
 ├─ C2 实盘模拟页 (入池/组合/nav vs HS300)
 ├─ C3 工作台 (数据管线状态, 复用 stage_status)
 └─ C4 市场感知页 (资金热力/RS轮动/悄悄流入榜/情绪温度/退潮预警 — 选股台的上游漏斗)
    │
D. 主升浪猎手 (D1-D4, 依赖 B1+B2+B4[板块上下文因子])              ← 用户方法论主战场
 ├─ D1 GT 重生成 (train 窗 ≤2025-06; archive 5 parquet 定义参照; holdout 纪律立法 §2.1)
 ├─ D2 逐层特征消融 (裸K → 日更量价 → 事件类; 机构 episode 特征在事件层交汇 §4)
 ├─ D3 细分策略 (行业/市值/板块 cell 内, 样本量护栏)
 └─ D4 OOS 验收 (2025-06→今 holdout, 预注册判据 + 使用预算)
    │
E. 整合: D 产出策略 → 选股台界面 → 用户手选入池 W2 实盘模拟 → KPI 对 goal.md
```

**并行度**: A→C1/C2 一条线 (前端), B1/B2/B3→D 一条线 (研究地基), 两线独立可交替推进。

## 1. 架构裁决 (用户两问)

### 1.1 形态识别 = 独立功能模块 — **同意, 但必须切开 A/B 两半**

| 半 | 内容 | asset_class | 位置 |
|---|---|---|---|
| **形态识别 (serving 半)** | 截至 t 的 K线 → t 日形态标签 (Weinstein stage 1/1.5/2/3/4, 长底/突破/上升通道...) — **只用 ≤t 信息, 给定参数结果唯一** | **Type A** (确定性 PIT 重排) | `services/technical_states/` 重建, L1k 层, 平台常驻, 每日 process 步跑 |
| **主升浪 GT (研究半)** | 用**未来顶部信息**回头标注历史起涨点 ("已知答案") | **Type B** (含前瞻 — 天生只能用于研究, 永不 serving) | D1 产出, feature_store L2, edge 隔离 |

> 这个切分是防泄漏的结构保证: serving/实盘只许碰 Type A 形态标签; GT 只活在训练研究里。
> 旧 technical_states (reset 前已建) 随 2026-06-28 重建退役 — 重建=透明重写+单测, git 史 (a078351e 前) 作定义参照非复活。

### 1.2 股票分层前置 — **同意, 归 M3 加工层 (每日数据获取之后)**

用户原话"属于数据变量加工的功能…基础工作, 在每天数据获取之后" — 正确, 且是 Type A:
- 产物: `dim_stock_segment_daily` (stock × date × 市值段/流动性段/申万行业/东财概念TopN/板块) — 分段阈值进 `config/segments.yaml` (判断死红线)
- 挂 pipeline **process 阶段** (现该阶段只有 dc_industry view, 正好充实)
- 消费方: 所有策略的分层 cell 定义 + 画像维度 + edge 界面筛选器 — **单一计算点** (不许每个策略自己算市值段)

## 2. 主升浪方法论评估 (用户思路, 诚实把关)

**用户方法论** (原话浓缩): ≤2025-06 用已知答案识别每股主升浪 → 第一层裸K参数 → 逐层叠加日更参数 (量/换手/两融/筹码分布/筹码胜率) 看关联是 alpha 还是 beta → 再叠加非周期事件 (龙虎榜/增减持/调研/盈利预测) 看是否推手 → 2025-06→今 OOS 验证 → 迭代 → 整合, 且按行业/板块/市值/概念分层细分, 不找通用策略。

**评估: 方向正确** — 本质 = 监督式 episode-first 结果倒推 + 时间切分 + 逐层消融 + 条件化分层, 与 goal.md D1-D4 / MASTER §5 既定方法论完全一致 (且细化了叠加顺序)。业界对应 event study + feature ablation + regime conditioning, 是正路。**三个必须立法的把关点**:

### 2.1 Holdout 使用预算 (最大方法论风险)
"根据测试结果做优化迭代"若迭代发生在 2025-06→今 数据上, **holdout 就烧掉了** (第二次使用起它退化为验证集, 最终数字系统性乐观)。立法:
- 迭代优化只在 train 窗内做 (walk-forward / purged k-fold);
- holdout 触碰**预算 ≤3 次**, 每次触碰前**预注册判据** (先写"什么算过"再看数字), 触碰记录进 experiment_store;
- 最终整合用的参数必须在最后一次 holdout 触碰**之前**冻结。

### 2.2 alpha vs beta 判定标准预定义
每层叠加"看有什么关联"之前先冻结判定: ①超额口径 (vs 分层内基准, 非裸收益 — E2 教训: 2024-25 人人是股神) ②含成本可交易 (R1: IC≠可赚钱; R2: 涨跌停/T+1 execution-aware) ③每 cell 样本量护栏 (episode<N 不出结论)。

### 2.3 分层×参数组合爆炸控制
行业31×市值4×板块…cell 数爆炸 → 多重比较偏差 (总有 cell 碰巧显著)。控制: 分层从粗到细 (先市值×大类行业 ~12 cell), 显著 cell 再细分; 跨 cell 一致性检验 (真信号在相邻 cell 方向一致, 孤立显著 cell = 噪声嫌疑)。

## 3. 机构跟随 × 主升浪 关系 (用户问"是不是就是主升浪猎手")

**不是同一个策略, 是方法论同构的两个策略 + 一个交汇点**:

| | 机构跟随 (已 W1/W2) | 主升浪猎手 (D) |
|---|---|---|
| episode 定义 | 机构建仓→退出 (名册事件) | 股价起涨→顶部 (价格事件) |
| 信号源 | 披露公告 (page_update 锚) | 形态+多层特征 |
| 产品形态 | 档案+用户选跟随 | 选股台+细分策略 |

**交汇点 (真协同)**: fact_inst_episode (37万) 直接作为主升浪 D2 **事件层特征**之一 — "明星机构 (PIT 评级) 新进/增持"是否是主升浪推手, 与龙虎榜/增减持/调研/盈利预测并列消融。机构跟随不必等 D, D 也复用机构件, 互不阻塞。

## 4. 各 Phase 细节 (验收标准)

### A. 机构档案 API (半天)
- data_access.yaml 加 entity: `inst_profile` / `inst_profile_dim` / `inst_episode` (feature_store, SERVE 暴露)
- router `/api/v3/inst/profiles` (排名列表, low_sample 过滤) / `/api/v3/inst/profiles/{holder}` (档案: 总体+维度+episode 时间线) / `/api/v3/inst/signals` (最新期新进/增持事件流, 供"跟随"入口)
- 验收: TestClient 真数据实测 + serve_read_layer 0 违规

### B1 股票分层 (1天)
- `services/segments.py` + `config/segments.yaml` (市值段: 微/小/中/大 分位阈值; 流动性段; 行业=申万L1 PIT; 概念=东财TopN)
- 产物 dim_stock_segment_daily (smartmoney L1, data_layers 声明) + pipeline process 步 + 单测
- 验收: 全市场当日标签覆盖 ≥99% 活跃股 + 与 v_sw_industry_pit 抽查一致

### B2 形态识别 (2-3天)
- `services/technical_states/` 重建: Weinstein 4-stage + 项目已验证形态族 (git 史 + analysis/f1_form_redesign_20260616 + form_survey 参照)
- Type A 红线: 只用 ≤t K线; 参数进 config; 单测含"未来数据不可达"结构断言
- 产物 fact_stock_form_daily (L1k) + process 步
- 验收: 与 archive 旧 fact_rally_stage 重叠期抽查对齐 (定义一致性) + 全量跑通

### B3 两融采集域 (半天)
- sync_registry 加 margin_detail 域 (tushare, 实弹核证 grain/单页上限/data_start — 加源 SOP)

### C. edge 前端 v1 (3-5天, 可与 B/D 并行)
- 技术栈: React+Vite (用户拍板换现代框架); widget 独立小功能原则 (每卡片独立取数/渲染/失败, 禁长链条 init)
- C1 机构档案页: 排名表 (排序/过滤 low_sample) + 档案详情 (维度热力图 + episode 时间线叠股价) + 跟随入口→W2 入池
- C2 实盘模拟页: 持仓表 + nav 曲线 vs HS300 + 入池/出池操作 (API 已备)
- C3 工作台: 数据管线状态卡 (stage_status) + 手动更新按钮
- 验收: preview 实测 + 前端契约测试 (卡片↔API 对应)

### D. 主升浪猎手 (按用户方法论, 每步细化再 grill)
- D1: GT 定义冻结 (参照 archive 5 parquet + 旧定义"底→顶>60%+长底+多头排列") → train 窗生成 → **holdout 纪律 (§2.1) 同步立法进 config+experiment_store 门**
- D2: 逐层消融 — L0 裸K (B2 形态参数) → L1 日更 (量/换手/两融/cyq_perf 筹码) → **L1.5 板块上下文 (B4 pulse: 所在板块资金流/RS/广度/悄悄流入 as-of, 用户确认反哺层)** → L2 事件 (龙虎榜/stk_holdertrade 增减持/stk_surv 调研/forecast 盈利预测/**fact_inst_episode 机构信号**) ; 每层 §2.2 判定
- D3: 分层细分 (§2.3 从粗到细) — 探索在 sandbox, 结论 record_verdict, promote 走确认
- D4: holdout 验收 (预注册判据, 预算内)
- 大规模计算 (若逐层消融×分层需要): Optuna+Modal 届时启用 (有 search space 有消费方才上, grill)

### E. 整合
- D 产出细分策略 → 选股台界面 (第5界面) → 每日候选 → 用户手选入池 W2 → 实盘模拟 KPI 对 goal.md (年化≥30%/max_dd≥-20%/超额>0/月胜率≥55%)

## 5. 待拍板点
1. Phase 顺序: A→(B∥C)→D→E, B/C 两线交替 — 同意?
2. B2 形态族范围: 先 Weinstein 4-stage + 长底/突破 最小集, 够 D1 用即扩 — 同意?
3. Holdout 预算 ≤3 次 + 预注册判据 (§2.1) — 同意立法?
4. edge 技术栈 React+Vite (换现代框架的具体选择) — 同意?
