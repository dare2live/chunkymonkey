# ChunkyMonkey 完整实施方案与修复计划 (2026-06-11)

> 依据: 2026-06-10~11 全面体检 (code-review 12 confirmed / 奥卡姆架构批判 / 实盘就绪度穿透 NOT_READY / 11 维工程审计) + TuShare 10000 积分接入实测 (need_027 gate PASS) + 数据源对比 (239 接口) + 30 条历史教训 + 回测模块盘点。
> 设计原则: 用户思路 (最小模块化 + 最小数据表 + 配置文件; 规则/模型/策略分层) 经第一性原理修正后采纳 — 见 §0。
> 状态: 待主体检工作流终版 findings 合并修复细目; 本文档为执行契约骨架。

## 0. 设计原则 (用户思路 + 专业修正)

采纳:
- 六层分层契约: 数据 → 特征 → 规则(信号) → 模型(排序) → 策略(组合) → 执行(回测/实盘)。每层只通过 schema 化的表 + yaml 契约对接, 不允许跨层直查。
- 配置文件承载规则 (项目宪法 §1.0 已确立), 代码只是配置的解释器。
- 最小数据表: 奥卡姆审计证实 344 表中保障覆盖率仅 4%, 表收敛是结构性修复不是清洁工作。

修正 (不被用户思路锁定的部分):
1. **不重写回测框架**。paper_sim v2 (12 模块, 含成本/T+1/涨跌停 mask/Wilson-Kelly/风险控制) 质量已过线, 缺的是统一 Strategy 接口 + 分层 contract + 基准对比, 补齐成本远低于重写 (教训: bestchoice 双拷贝就是重写路线的尸体)。
2. **先修地基再加 alpha**。数据管线断流 (cron 三连断, K 线缺 4+ 交易日) 时做任何因子研究都是在过期数据上浪费算力。Phase 0 是硬前置。
3. **"规则/模型/策略"三层已存在** (10 公式 yaml / LambdaMART v6 / 3 策略 ensemble), 问题不是缺设计而是缺收敛: 5 张每股最优表、8 张推荐表、12 个 paper_sim yaml 变体各算各的 — 方案是收敛到单链路, 不是再建一套。

## Phase 0 — 止血 (本周, P0)

| # | 任务 | 验收 |
|---|---|---|
| 0.1 | cron 三连修复: daily_update (macOS TCC 拒绝) / nightly_audit (PATH 无 python) / forward monitor (.venv 已建好) — 迁移到 launchd + 显式失败告警送达 (失败写标记文件 + macOS 通知; "静默失败"是 2026-05-26 教训复发, 根因是无告警送达而非单次故障) | launchd 任务连续 3 天成功; 人为破坏一次能收到告警 |
| 0.2 | K 线补数 06-05~06-10 (tdxhub 手工补跑) + TuShare daily 对账脚本 (行数 + 收盘价抽样) 进 nightly audit | price_kline_tdxhub max(date) = 最新完整交易日; 对账 diff=0 |
| 0.3 | 427 个未 push commit 推送; 当前 worktree diff 修复 code-review confirmed 关键项后 slice commit (含 TuShare 接入) | git status clean; origin 同步 |
| 0.4 | champion 身份统一 (daily_update.sh:46 vs SESSION_HANDOFF 分裂) — 单一 champion 注册点 (yaml), 两处都读它 | rg 全 repo 只有一处 champion 定义 |
| 0.5 | 证据链补洞: v9b gate 引用的 train_log 行不在 DB (重跑或标记 invalid); p3 acceptance HS300 基准=0 行标记掺水; GO/NO-GO 禁止混用不同 model_id | delivery_readiness 重跑, 每项证据可从 DB/artifact 复核 |

## Phase 1 — TuShare 数据接入 (1-2 周, P0-P1)

架构: **一个通用 tushare sync client** (不是每接口一个脚本):
- 空响应重试 (代理 jiaoch.site 实测有间歇性 15s 空响应 — 0 行一律按失败重试 ≤3 次)
- 限频按 trade_date 批量拉取 (一次调用全市场一天, 省额度)
- watermark 表 + failure queue + freshness SLA 注册 data_audit_rules.yaml
- 表设计最小化: raw_tushare_{api} 薄层 (原始字段 + trade_date + built_at), 需要 PIT 锚点的加 ann_date/end_date; 不建中间派生表, 特征直接从 raw 算

接入顺序 (对比报告 Top 8):
1. moneyflow (2010 起 16 年, 全新域, adapter 已有)
2. cyq_perf + cyq_chips (筹码胜率/成本分位, 2018 起, 18-19 点更新 → JOIN 必须 t-1)
3. stk_limit + stock_st + suspend_d (可执行性真相源, 替代本地 YAML 规则推导, 修实盘 ST±5% 缺口)
4. moneyflow_hsgt + hk_hold (北向域重建, 现停 2024-08)
5. margin_detail (两融, 域空缺)
6. top_inst + top_list 回填 (LHB 历史 3 年 → 20 年, 机构席位补缺)
7. trade_cal + stock_basic (解除 akshare 单点依赖)
8. daily + adj_factor (K 线第二源对账, tdxhub 保持主源)

验收 gate: need_027 post_probe_gates 5 项 required (pit_key / freshness_sla / writer / watermark / failure_queue_resolution) 全部 pass。

主源决策 (已定): 分域 — 新域 TuShare 主源; akshare 全面退役; K 线 tdxhub 主干 + TuShare 对账 (代理单点风险不押命脉)。

## Phase 2 — Alpha 研究框架 (2-4 周, 与 Phase 3 并行)

核心: **单一特征实验流水线**, 杜绝"各算各的":
1. 特征注册制: feature_registry.yaml 每特征声明 {来源表, PIT 锚点列, 数据可用时刻, 计算窗口, 注册日期}; panel build 只接受注册过的特征。
2. ROI gate (教训 L27): 新特征入 panel 前必须过 100K 行 Spearman + coverage + var≈0 检查; fail 不 promote。
3. 评估标准统一: walk-forward OOS RankIC (干净基线 0.0108-0.0203); 相对提升 ≥+50% 自动触发 PIT 复审 (教训 L15)。

研究顺序 (按数据可得性 × 先验强度 × 用户点名):
- **F1 资金流族**: 主力净流入强度/连续性、超大单占比、5 日累计、量价-资金流背离。三口径 (moneyflow/dc/ths) 共识度本身做因子。
- **F2 筹码族**: winner_rate (与胜率诉求同义)、价格距 cost_50/cost_85 分位、筹码集中度 (cost_85-cost_15)/cost_50、获利盘变化率。
- **F3 板块/概念协同** (用户点名"产业链扩散/板块协同/概念协同"): dc_member/ths_member 按日成分 PIT 化 → 板块内涨停传染数、板块资金流领先个股、概念扩散度 (概念内 N 日新高占比)。前置: 板块成分历史回填。
- **F4 龙虎榜/游资**: 机构席位净买额、hm_detail 游资标签跟随。
- **F5 事件族**: 增减持方向、解禁距离、业绩预告/快报 (forecast/express PIT 用 ann_date)。

算力: 本地优先; 全市场 16 年 moneyflow 回填、cyq 8 年回填、Optuna sweep 上 modal (待 token 配好 + experiment_jobs adapter gate)。

## Phase 3 — 回测框架收敛 (与 Phase 2 并行, 不重写)

基于盘点缺口:
1. AbstractStrategy 接口: load_candidates / evaluate_exit / rank_swap 三方法, selector 5 种模式收敛为实现类; legacy latest 路径物理删除 (PIT 风险)。
2. 分层 contract: 每层输入输出 schema 写 yaml (panel→score→candidate→position), build 时校验。
3. 基准对比内建: 每次 replay 自动跑 equal_weight / HS300 / random 三基准, KPI 报告含超额列。
4. 多策略并行: run_id 前缀写表, 一次 batch 跑 N 配置。
5. paper_sim 超参 (kelly/stop/target/trailing) 进 Optuna walk-forward (教训 L8), 12 个 yaml 变体收敛: prod 1 个 + 实验模板 1 个, 其余归档 analysis/。
6. 验证三级标准化: portfolio_backtest (研究粗筛, 标注不含成本) → paper_sim replay (含成本, 决策依据) → paper_sim 日频 (实盘镜像)。数字出口规则: 对外只引用 replay 及以上级别。

## Phase 4 — 模型/策略升级 (4-8 周)

1. LambdaMART v7 = v6 + F1/F2 特征族 (每族独立 ablation, 不打包上车)。
2. ensemble 权重进 Optuna (教训 L9, 现 13 权重拍脑袋)。
3. regime gate 数据驱动 (教训 L10): 加入 moneyflow_mkt_dc 大盘资金流 + 历史 sensitivity sweep。
4. **胜率专项** (用户核心痛点, 月胜率 50-71% 波动): cyq winner_rate 做 selector 硬过滤/软加权 A/B; 止盈止损结构再寻优; 入场时机用筹码成本分位择时。目标: 月胜率稳定 ≥55%, 以 walk-forward OOS 月度胜率分布 (不是均值) 验收。

## Phase 5 — 实盘验证 (Phase 4 通过 gate 后)

1. 可执行性闭环: stock_st/stk_limit/suspend_d 真实数据接入 tradability (替代静态规则), 新股前 5 日规则补上。
2. 滑点校准: 小资金真实成交回执回填成本模型 (现为行业参考值; 54.9x 换手下 1bp 滑点 ≈ 1pp 年化)。
3. paper→live 协议: 盘前信号生成时刻表、人工下单清单格式、成交偏差记录表 (fact_live_execution_delta)。
4. 双周 gate review: 四大目标 (年化≥30% / max_dd≥-20% / 超额>0 / 月胜率≥55%) KPI 自动面板, 全部含成本 OOS 口径。

## 治理瘦身 (贯穿执行, 采纳奥卡姆审计 Top 5)

| # | 目标 | 动作 |
|---|---|---|
| G1 | 状态面 8 → 3 | goal.md (计划) + SESSION_HANDOFF (快照) + ledger (归档, 设上限滚动); 退役 moth 链路 / workflow_checkpoint / session_snapshot.json 独立维护 |
| G2 | 死表族清理 (~34GB 主因) | 孤儿表 0 caller 先删; 版本化 panel 六代→现役一代 (旧表导 hash 摘要进 analysis 后 DROP); cache 表退出 DB |
| G3 | dim_all_ever_listed 退役 | dim_listing_status 等价性测试 → 删 fallback 分支 → DROP; dim_active_a_stock 的 sync-enumeration 职能加 freshness SLA |
| G4 | 每股最优 5 表→1 (保 _pit), 推荐 8 表→1 | 旧名建只读 VIEW 过渡 30 天后 DROP |
| G5 | bestchoice/ (FROZEN) vs bc_absorbed/ 双拷贝 | git tag 归档 frozen 树后删除; bc_absorbed 接 universe hook 后撤销整树豁免 |

## 修复计划合并清单 (P0/P1/P2)

P0 (资金安全/数据正确性): Phase 0 全部 + code-review #1 (find_spec 无防护崩 gate) + preflight veto 语义测试补齐。
P1 (阻断实盘): Phase 1 接入 + 证据链规范 (单 model_id 全链路) + 漂移监控复活 (alpha-decay 触发器移出死 cron)。
P2 (工程债务): 治理瘦身 G1-G5 + code-review 其余 confirmed (env 名单复制 / healthcheck 重实现 / action 双词汇 / install_hint prose / 三层冗余) + 主体检工作流终版 findings (待合并)。

## 决策记录

- 2026-06-11: TuShare 经代理 jiaoch.site 接通, need_027 gate PASS, 主源策略 = 分域 (新域 TuShare / akshare 退役 / K 线 tdxhub 主干 + 对账)。
- 2026-06-11: 回测框架走"收敛补 contract"路线, 否决重写。
- 2026-06-11: modal 为大算力后端 (token 待配), 用前需 experiment_jobs adapter gate。
