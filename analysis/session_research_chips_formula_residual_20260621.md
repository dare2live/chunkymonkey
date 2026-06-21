# Session 研究归档 — 筹码精细化 / 公式×主升浪 / GS公式 / 残留审计 (2026-06-21)

> 本次 session 多个研究/裁决的归档。控制面文档(goal/master/INDEX)只引 confirmed 结论, 探索细节在此。

## 1. 筹码维度精细化 — 三份研报评估 (用户连发, 筹码做深方向)

| 来源 | 方法 | 数据需求 | 结果 | 项目可做? |
|---|---|---|---|---|
| 长江《筹码分布因子》 | L1快照→60×6矩阵→75衍生特征(分盈亏均值/标准差/偏度/峰度/熵)→TCN | [否] L1快照(项目无, need_027 BLOCKED) | RankIC 9.05%/超额21.76% | 仅借鉴分盈亏统计量思路 |
| baogaobox | 动态筹码(成交×换手加权)→集中度/盈利TG/分散度 | [否] 分钟级/L2/逐笔 | TG年化30.34%/TGRatio样本外31.59% | [否] 无逐笔 |
| **华泰《筹码分层AI因子》(06-02最新)** | **VWAP三角分布换手递推**重建筹码→筹码龄+投资者类型分层→CNN+GRU | [可] **日度VWAP+换手率即可** | 筹码龄RankIC 12.3%/超额32.5% | **[可] 分布重建+分层日度可做** |

**关键发现 (对项目最有价值)**: 华泰 **VWAP中心三角分布换手递推法**只需日度数据 — 当日筹码三角分布落 VWAP 附近(宽度~当日振幅), 历史筹码换手率衰减留存, 叠加归一化 = 完整筹码分布曲线。项目 `daily`(amount/vol→VWAP, high/low→振幅) + `daily_basic`(换手率) **全有**。比现 chips.py 用的 cyq_perf(只5分位 winner_rate/cost_5/50/95)精细得多, 还能拆筹码龄分层(套牢多久=解套压力) + 投资者类型分层(moneyflow order-size桶 elg/lg/md/sm 分配, 粗但能做)。

**真金白银警告**: 三份都是**无条件截面 alpha 因子**(长江自承"小市值低波动敞口") = 项目验证过撞 **R1 墙**那类(无条件截面+小市值→含成本OOS缩水)。RankIC 12.3% 是含成本前截面多头, 别被迷惑。CNN+GRU/TCN 端到端=黑箱(违人话可解释 + 难stage-conditional)。

**裁决/建议**:
- [可] 做: 筹码分布重建(VWAP三角+换手递推, 日度) → chips.py 升级; 筹码龄分层+分盈亏统计量 → L2 筹码描述增强; 选股层在**主升浪候选池内**用筹码分层因子排名(stage-conditional)+含成本OOS。
- [否] 不做: CNN+GRU端到端(黑箱+无条件截面); 当无条件全市场alpha(撞R1); 裸信RankIC。
- POC: sandbox 筹码分布重建(VWAP三角+换手递推 vs cyq_perf 对照)。

## 2. 公式 × 主升浪综合可行性裁决 (Workflow wf_9e910dda, 用户两个想法)

| 想法 | 裁决 | 关键 |
|---|---|---|
| A: 公式买卖点→主升浪买卖点 | 买点 PROCEED / **卖点 BLOCK** | 买点已有产线接线(fact_technical_trigger signal_date_pit); 9公式仅2/9(activity/gs)有内生exit且全机械退出(均线/通道), 直接当主升浪卖点=过早离场吃不到鱼身 |
| B: 公式买点+主升浪延迟卖点(鱼身延续) | **REVISE** | 判定器**必须**用 live `technical_stage`(已注册signal_date_pit), **绝不能**用 rally_stage 鱼身标签(含peak=leakage死); 反转族/MACD below_zero 不能套(均值回归方向相反) |

**方法论纠正 (用户原话: "先知道答案反推时也可以借助公式")**: 修正 Workflow 初版把"公式信号正推 vs episode结果倒推"对立化的教条。正确理解 = **episode(谁是赢家)是真相源, 公式是标注/识别赢家买卖点的工具**(候选/确认腿)。公式触发率≫episode真起点, 不能裸拿公式当episode端点, 但可用公式在赢家身上标出进出点。owner=[[feedback-formula-as-episode-tool]]。

**9公式现状** (wf_9e910dda): MACD金叉实现最完整; ma_base_breakout/limit_up_pullback/reversal 实现已reset删仅L0特征版存活; **仅2/9(activity/gs)有config内生exit**; 深度寻优从未跑完; 无含成本OOS裁决。

## 3. GS 公式 = 用户核心实战公式 (通达信主图, 中际旭创验证)

用户实战主图公式"GS", **K线红=买/绿=卖, 日周月通用, 实战准**。整合(逐字复刻见用户消息):
- **动态均线迭代**(X_1~X_36): MA3/7/13/27均值+EMA5基准 → CROSS触发后 X_9=IF(金叉)×0.98/(死叉)×1.02/else X_4, 迭代X_12...X_36逼近 = 动态支撑压力线 X_36 + 金叉死叉买卖点(X_44/X_45) = 项目 `dynamic_ma_iterative`
- **明暗盘强弱**(X_47/X_48): X_36≥基准强势红/<弱势绿, STICKLINE染色 (注: X_8=CROSS(X_3,X_4) AND X_6=死叉信号, **非**我之前暗盘公式误当的"路径权重"=再证砍暗盘伪公式对)
- **多均线**(5/10/20/90/145, MA145=ma_base_breakout站稳线) + **真换手率**(SUM(VOL,N)/CAPITAL×100, 3/5/10/20日)
- **神奇九转**(GS_: C>REF(C,4)计数到9 = TD Sequential顶底) = 项目 `formula_gs`
- 底部"柳暗花明": 明暗盘+看多(今日/三日/五日看多+明盘/暗盘)
- 中际旭创(300308)主升浪 522→1368 实测: 底部红箭头买入+全程红柱+九转标顶底 = 准。

**用途 (方法论纠正后)**: GS 作 episode 买卖点**标注工具** — 在主升浪赢家(fact_rally_ground_truth)上标 GS 红/绿信号点, 验证 GS 能否套住起涨/卖出。

## 4. 残留审计清单 (Workflow wf_9e0eebb2, 26残留; 用户痛点"旧方案没清干净误导")

**P0 已修 (commit 56e79b14)**: main.py 根路由 `/` 曾重定向旧v3 React界面(/v3/Chunky Monkey v3.html) = 用户痛点"打开看到旧前端". 收口: 根路由改指 dossier(/api/dossier/view) + 删/v3挂载 + design/46文件归档(claude_design活资产→docs/design_specs/claude_design_v5, 废弃原型→docs/archive/design_pre_reset_v3) + title更新.

**待批次处置** (对抗核验后裁决):
- **confirmed_delete (真孤儿, import即崩/0引用)**: `backend/services/perf/`整包(import已删prepared_signal_set即崩) · `feature_labels.py` · `ui_labels.py` · `pipeline_lock.py`(同步清schema_versions.py:158+data_layers.yaml:119) · updater.py build_profiles/build_industry_stat/calc_inst_scores三步(mart全MISSING, web /update/all能复活wiped表违reset收口)
- **migrate_tushare (默认tushare红线)**: **dim_trading_calendar: akshare→raw_tushare_trade_cal(最高优先级PIT安全, tushare已fresh未接!)** · **fact_top10_holder_period: tdx_f10→top10_floatholders(用户问的机构切源, M4, sync_registry未注册)** · institution_surveys→stk_surv · profit_forecast→tushare forecast · aif10 3表→daily_basic+report_rc · etf→fund_daily
- **must_keep_live (勿删)**: dim_trading_calendar(迁前不断) · fact_top10_holder_period(dossier holderCard在用) · aif10 3表(v3_picture live) · inst_institutions(240机构白名单人工资产) · holders_event.py(单测+fact_holder_event唯一重建路径)
- **同步清**: data_audit_rules.yaml:37 mart_institution_score_daily(监控不存在表→假freshness告警)

## 5. 其他 (反例/坑, 落 ledger)
- **前端入口残留误导**: reset切前端时旧v3挂载没退役→根路由打开旧前端误导; "只有一个前端"误判=只查static/漏main.py根路由挂载(审计要查路由挂载非只查文件)。
- **report_date格式不统一**: fact_top10_holder_period report_date 600519='2026-03-31'带横线/000513='20260331'无横线→字符串比较漏数据; loader REPLACE规范化修(根治待切tushare统一)。
- **机构维度接错源**: 图省事用在库tdx_f10(smartmoney)非tushare(违默认tushare红线), 用户即纠; 新接数据先查源是否tushare。
- **L3 sector_context蓝图就绪** (wf_05c60317): 新建sector_context.py纯函数(L2个股vs板块复用rs.py + L3板块regime/资金轮动/概念4函数), 5 SQL草稿PIT正确, 申万切换方案; 数据已补齐待落地。
