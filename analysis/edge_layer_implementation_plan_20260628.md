# Edge 5界面分阶段实现计划 v1 (2026-06-28)

> 配套: `edge_layer_framework_design_20260628.md` (设计/盲点/教训门) — 本文 = 落地分几步怎么建。
> 状态: 实现计划草案, 待用户 review 后逐 Phase 动手 (用户决议: "全部分阶段规划好再动手")。
> 来源: 框架设计 + Explore agent 前端资产盘点 (10911 行 JS 逐模块复用/丢弃判断) + SERVE entity 现状核查。

## 0. 勘误 + 技术栈裁决

**框架文档 §0 "edge 0 代码" 不准确, 勘误**: 前端 **FastAPI(后端) + 原生 JS SPA(前端, 无 React/Vue)** 壳子完好 —
index.html(560行) + assets/js/ (10911 行: 14 view 模块 + 22 widgets) + main.css(版本 3.7.3)。
2026-06-28 重建 git rm 删的是 **9 个后端 serving router** (signals/dossier/market/stock_graph/v3_paper/
v3_picture/v3_portfolio_builder/v3_selection/workbench) → **前端血管断了 ~60-70% API 调用指向已删 router**, app 能启动但前端打开大面积 404。**断的是后端血管, 不是前端壳子。**

**技术栈裁决 (奥卡姆)**: 沿用现有 **FastAPI + 原生 JS SPA, 不引入 React/Vue**。理由: 70% 前端框架完整 +
widget 库 (returns-chart/topk-strip/institution-scorecard/stock-list/multidim-badge) 正好对口 5 界面;
从零选框架 = 多一个数量级工程量 + 违奥卡姆。**待用户确认此裁决。**

**复用边界铁律 (mio 真金白银 + 结果倒推)**: 复用的是 **UI 框架 / 纯展示组件**, **数据契约全部按新架构重建**。
旧 widget 绑的 `/api/rec/*`(模型信号正推) / `/api/signals/*`(旧策略配置) / `/api/inst/scoring/*`(旧机构评分)
全是**污染期契约**, 重接 SERVE + verdict 脊柱 + episode-first。**复用 UI ≠ 复用旧数据逻辑。**

## 1. 现有前端资产复用/丢弃映射 (盘点结论)

| 类别 | 数量 | 模块 | 处置 |
|---|---|---|---|
| (KEEP) **直接复用** | 14 | app-nav/cache/list-state, style-tokens, viz-primitives, security-identity, format-utils, multidim-badge, returns-chart, type-summary, stock-summary, stock-list-rows, stock-list-controls | 纯展示/通用, 无 API 绑定, 立即用 |
| (MOD) **改造复用** | 9 | app.js, stock-view, workbench-view, signal-adapter, signal-params, screening-panel, topk-strip, institution-scorecard, workbench-health | UI 框架好, 但绑死已删 router/污染期契约 → 重接 SERVE |
| (DROP) **丢弃** | 4 | cohort-card, backtest-panel, model-monitor, (signal-adapter 旧 SignalConfig 逻辑) | 绑死已退役策略/模型正推, 重建不需要 |
| (DROP) **延期(可选)** | 7 | etf-*.js (workbench/list/opportunity/sector-rotation/strategy-compare/analysis/grid-optimizer) | ETF 模块整条链已删, 不在 5 界面范围; 需显式决策才重建 |

**现存活后端 router** (4): `ops_manual_run`(数据链手动跑) + `strategy_preset`(预设CRUD) + `v3_config`(配置下发) + `perception`(市场感知微服务, guarded fallback)。

**SERVE 现支持 entity** (21): kline_qfq / moneyflow / moneyflow_dc / valuation / index_daily / cyq / stk_limit / share_float / block_trade / report_rc / fundamentals / forecast / sw_daily / sw_industry / top_list / top_inst / holders_tdx / dc_member / dc_index / limit_list_d / institution_survey。
**5 界面需新建的 SERVE entity**: org_holding(机构持仓深史) + 机构跟随收益(B) + 公式命中(B) + 形态正交轴(A) + sim持仓(B)。

## 2. 贯穿脊柱 (Phase 1 先建, 所有界面依赖)

5 界面共享一个裁决/证据层, 不是各页独立看板 (反谄媚死):

| 脊柱件 | 前端 | 后端 | 说明 |
|---|---|---|---|
| **统一 as_of 上下文** | 全局 store (改 app-cache) | 读平台 watermark | 禁各页自取 latest 打架 |
| **数据可得性矩阵** | 新 widget | 新端点读 data_layers health + sync watermark | 每维度标 live/已停披露/季报滞后/浅史/needs_review |
| **裁决等级徽章** | 改 multidim-badge | record_verdict 读 | UNKNOWN < STAT_EDGE < MONEY_CONFIRMED, 可点穿证据 |
| **provenance badge** | 新 widget | SERVE DataResult.provenance (已返) | vendor + PIT 锚 + 滞后天数 |

## 3. 分阶段 (依赖驱动 + 工作量)

### Phase 0 — 清债 + 前端瘦身 (先做, 不依赖新建)
| 项 | 动作 |
|---|---|
| 修 bug#1 | holdings.py 走 SERVE + 去死引用 `mart_current_relationship` (已删表) |
| 修 bug#2 | institution_survey SERVE entity repoint tushare `stk_surv`(36万行) + 退役 tdxhub raw_institution_surveys(撞 tushare 唯一红线) |
| 删丢弃模块 | git rm cohort-card / backtest-panel / model-monitor; etf-* 标延期(注释不删) |
| 清断裂调用 | app.js/stock-view/workbench-view 里指向已删 router 的 fetch → 优雅降级(诚实空态)或移除 |
| **验收** | app 启动无报错 + 前端无 404 风暴(空态诚实) + check_dead_references 绿 + moth 绿 |
| 工作量 | 低-中 |

### Phase 1 — 贯穿脊柱 + 工作台 (最低工作量, 不依赖 edge)
- **脊柱**: 建 §2 四件 (as_of 上下文 / 可得性矩阵 / 裁决徽章 / provenance badge)。
- **工作台前端**: data-view((KEEP)) + strategy-view((KEEP)) + settings-view((KEEP)) + workbench-view((MOD)) + workbench-health((MOD))。
- **工作台后端**: ops_manual_run(活) + strategy_preset(活) + v3_config(活) + **新建** health/audit 端点 (四道防线状态/每域 data_start+滞后/可买入率体检)。
- **参数管理 (判断死最后防线)**: 面板真读写 config yaml 非 hardcode; 阈值人话(J1: "均线斜率>6.5%" 非 `0.0655`) + 边界联动(J2: 调一个报受影响股 delta)。
- **盲点门**: 改策略参数→强制重走 walk-forward OOS 再生效 (Phase 4 有 edge 后接通; 此处先留接口 + 参数变更审计链 谁改/版本/回滚)。
- **验收**: 手动跑数据链 + 四道防线状态可见 + 改参落 yaml + 数据新鲜度一等公民。工作量 中。

### Phase 2 — 股票档案 (认识侧, 读现有数据)
- **范式**: 维度解读器 (DimensionInterpreter, 每维度独立模块 4 接口, 聚合器按需调**不建大宽表**)。
- **前端**: stock-view((MOD)) + stock-list-rows/controls((KEEP)) + topk-strip((MOD)) + multidim-badge((KEEP)) + returns-chart((KEEP)) + stock-summary((KEEP)) + security-identity((KEEP))。
- **维度 × SERVE 现状**:

| 维度 | SERVE entity | 处置 |
|---|---|---|
| K线/估值分位 | kline_qfq(KEEP) valuation(KEEP) | 估值分位自算=**A** |
| 机构持有 | holders_tdx(KEEP) top_inst(KEEP) | **新建** org_holding entity (aif10 深史, available_date PIT) |
| 筹码 | cyq(KEEP) | winner_rate **needs_review** 标注(C0 FAIL) |
| 行业/概念 | sw_industry(KEEP) dc_member(KEEP) | 申万行业 + 东财概念 |
| 财报/预期 | fundamentals(KEEP) forecast(KEEP) report_rc(KEEP) | — |
| **当前形态** | (无) | **新建** 正交轴 cell(位置×趋势×横盘, **A**, type_a_leak 门), 非 Weinstein 5-stage |
| **命中哪些公式** | (无) | 依赖 Phase 4 edge → 先占位 |
| **机构历史收益** | (无) | 依赖 Phase 3 → 先占位 |

- **门**: type_a_leak(形态/估值分位禁混 forward/score 列); 机构收益维度 as-of(Phase3 来)。
- **验收**: 单股看到 K线/估值分位/机构持有/筹码/行业/概念/财报, 每维度带 provenance+freshness, 形态正交轴。工作量 高。

### Phase 3 — 机构档案 = 机构跟随策略 (机构跟随收益 as-of)
- **用户口径 (已拍板)**: 跟随收益 = **机构建仓公告日 T+1 买入, 扫描到退出公告日 T+1 卖出, 含成本**。
- **前端**: institution-scorecard((MOD) 重建评分数据) + type-summary((KEEP))。
- **后端新建 (B, edge 隔离库 experiment_store)**:
  - 跟随收益引擎: org_holding_aif10(2019+深, 非公募分桶) / top10_floatholders(2005+) / qfii / raw_lhb_daily(龙虎榜席位) / surveys → **as-of 持仓 × forward 价格 × 含成本**。
  - "第二次行为标准" (非首次出现就跟, 二次加仓/连续确认)。
  - 容量/拥挤度 (公告公开后跟随者多→滑点摊薄)。
- **门 (真金白银)**: as-of leakage (披露日<=t × t后价格, 禁 latest-snapshot — inst_path_a 2026-05-15 BLOCK 重演点); PIT 锚=available_date/ann_date 非报告期; 含成本 OOS 裁决 (record_verdict)。
- **(MOD) 诚实标注**: "退出"也按公告日→季报滞后~4月, 信号延迟吃 alpha(参北向个股 follower 零超额先例); 跟随收益数字出来前必含成本 OOS, 异常高(年化>100%/sharpe>5)冻结查 leakage。工作量 高。

### Phase 4 — 选股台 = 各公式选股 (episode-first)
- **mio 红线**: 公式=episode 标注/确认工具**非策略**; **stage-conditional**(阶段内排名)非全市场无条件截面(撞 R1 墙 含成本 -14~-35%); 判据=**含成本绝对收益**非 IC。
- **前端**: stock-view((MOD)) + screening-panel((MOD)) + signal-params((MOD) 改读 yaml) + topk-strip((MOD))。
- **后端新建 (B, edge)**:
  - episode-first 验证 pipeline (D1 赢家 episode→D2 PIT 特征+分层→D3 公式→D4 含成本 OOS 裁决)。
  - 公式**只进 episode 验证有效的** (旧库 MACD/海龟/神奇九转 18-20 个 70-86% 污染期假设, **不复活**)。
  - 每公式卡挂**含成本 OOS 成绩单** (record_verdict) + "适配哪个形态/阶段" stage-conditional 上下文。
  - 单一资金口径 (东财 moneyflow_dc, 禁混 tushare net_mf) + universe 硬门 + 板块涨停阈值(主板10%/创业科创20%)。
- **门**: assert_universe_clean(全universe板块≥80%) + R1(含成本绝对收益 null) + R2(execution-aware) + selection-bias。工作量 高(依赖 edge D1-D4 重建)。

### Phase 5 — 首页 sim (最后, edge MONEY_CONFIRMED)
- **约束 (已定)**: 持仓≤3股 / 可现金 / 仓位≤80% / 100万起步; **策略本身"再讨论"**(单独深聊)。
- **前端**: **新建**(不存在) — 复用 returns-chart((KEEP)) 画净值。
- **后端新建 (B, edge)**:
  - **含成本 + execution-aware 回测引擎**: 印花税卖方非对称 + 佣金 + 滑点 + T+1 open 入场(非 close 假成交) + 涨跌停一字板剔篮 + 停牌缺价不静默剔出 + 容量冲击。
  - **forward 对账** (预测 vs 实际兑现逐日) = 命脉非展示 (感知死)。
  - **可插拔 strategy adapter** (选股来源: edge 候选池/公式/机构跟随/聚合, 不锁死)。
  - **regime 门** (当前市场态→仓位动态, MASTER §5 第四轴: long-only 赚钱主来源=对的时候在场)。
- **门 (死亡条款全压)**: 含成本+execution-aware+全universe; forward 对账强制; 年化>100%/sharpe>5 自动冻结查 leakage; KPI(超额 HS300)裁决 MONEY_CONFIRMED 前**只占位诚实空态**, 禁回填污染期旧数字(+312%/+34.88%)。工作量 高。

## 4. 新建表/entity 登记纪律 (复用本 session 立的门)
每 Phase 新建派生表/entity 过 **4 真相源** (sync_registry 采集 / data_layers A-B+health / data_access SERVE entity / lineage producer-consumer) + check_dead_references 硬门 + moth no-new-godfile + asset_class A/B 正确(B 落隔离库 feature_store/experiment_store 禁写平台表)。

## 5. 风险/盲点
- **edge 是真瓶颈**: Phase 3/4/5 都依赖 edge(机构跟随收益/episode 公式/含成本回测), 这些是 0 代码从零建, 非前端工程。前端壳子复用省的是 Phase 0-2 的工程, 决策侧(3/4/5)省不掉。
- **复用陷阱**: (MOD) 改造类 widget 绑死污染期数据契约, 改造时若图省事沿用旧数据结构 = 把污染期假设带进新界面。每个改造 widget 必重新定义数据契约接 SERVE。
- **首页 sim 策略未定**: 用户"再讨论"; Phase 5 做可插拔 adapter 占位, 策略本身需单独设计会话 + episode-first 验证。
- **机构跟随披露滞后**: 用户口径 PIT 锚对, 但季报滞后~4月可能让 alpha 缩水甚至归零, Phase 3 必含成本 OOS 验真有 edge 再当信号。

## 6. 每 Phase Definition of Done
完成 = 可运行结果 + 真实数据抽查 + 测试通过 + 该 Phase 的门全绿 + provenance/verdict 贯穿 + INDEX/goal 同步。单界面"打开能看"不算完成, 数据契约干净(A/B 正确 + PIT + 无 leakage)才算。

## 7. 待用户确认
1. 技术栈裁决: 沿用 FastAPI + 原生 JS SPA (§0) — 确认/否决?
2. Phase 0-5 顺序 + 是否先从 Phase 0(清债)起手?
3. etf-* 模块: 延期 / 彻底删 / 重建?
4. 首页 sim 策略单独会话深聊 (本计划只占位)。
