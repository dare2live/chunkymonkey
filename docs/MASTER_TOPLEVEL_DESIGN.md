# ChunkyMonkey 综合顶层设计 (Master Top-Level Design)

> 2026-06-14 立。本会话全部设计 + 用户对公式/数据的核心想法 + 重构方案的**系统性综合**。
> 定位: 项目最高层设计真相源, 串起 数据→因子→策略→验证→KPI 全链 + 纪律/工具 + 路线。
> 各层细节 owner 见文末文档体系; 本文件只给**骨架 + 决策 + 为何**, 不复制细节 (防双真相源)。
> 固化: 本设计的操作知识 (坑/工具调度/纪律) 沉淀进项目专用 skill `chunkymonkey-ops` (后续开发持续用)。

---

## 0. 北极星 (一句话)
把 A 股公开数据 (K线/财报/筹码/资金) 转成**真金白银的 KPI 级回报** —— 不是论文不是数字游戏。
KPI (owner=goal.md): 年化≥30% / max_dd≥-20% / 超额HS300>0 / 月胜率≥55% (含成本 OOS paper_sim, 2023起)。当前=unknown。

## 1. 创世法 (不可变, owner=goal.md)
**死亡条款** (陌生人可判): ① 感知死=forward不回填对账/异常高回测(RankIC>0.3/sharpe>5/年化>100%)不查leakage上线;
② 判断死=阈值/权重/策略组合 hardcode 进代码非 config; ③ 谄媚死=报喜不报忧/只调舒服方向。
**判断法典** (人话→机器话): 每表声明layer→`moth data-layer-integrity`; 不留god-file→`minimal-module-*`;
删层不被重建→`schema_layer_filter`+`legacy-flow-no-pollution`; 数字measured可复现→`check_rule_compliance`+leakage gate。

## 2. 总架构 (8层数据 × 4流程域, **data_layers.yaml 唯一驱动**)
```
真相源: data_layers.yaml (表→layer, 85活/56wiped) ── 流程/config 读它, 不自带表清单 (反转, 防重建循环)
─ 数据层 ───────────────────────────────────────────────────────
 L0_source     tushare_raw + market K线          ← sync_runner (registry 驱动)
 L1_foundation dim/财报PIT/十大股东/LHB/机构      ← from L0
 L1k_kline_int technical_stage/macd (纯OHLCV)     ← from v_price_kline_qfq
 display       档案展示 (UI serving)              ← from L1
 infra         watermark/audit/gate/deletion
─ 实验层 (不进 daily_update, 走 alpha 验证程序) ──────────────────
 L2_feature / L3_model / L4_experiment           ← experiment_store, S0-S4, 参数寻优重做
─ 4 流程域 (边界清晰, 各碰各 layer) ──────────────────────────────
 daily-update(L0/L1/L1k/snapshot+retention) · alpha-validation(L2-L4) · serving(display) · infra(治理)
```
核心反转 (重构): daily_update 不再硬编步骤 (自当真相源致删层留死调用); 读 data_layers active 表自动跳 wiped。

## 3. 数据层 (tushare 主源; 验证优先于抓取)
- **主源 tushare** (171/239 可用, 已抓 29); tdxhub/miaoxiang 备援; akshare 淘汰中。
- **数据菜单** (owner=tushare_alpha_potential_menu): 137 A股未抓接口评估, **无 high edge** (诚实)。真缺口因子族:
  现金流质量(cashflow)/机构大宗折价(block_trade)/北向per-stock(ccass/hsgt_top10)/事件日历/杠杆(margin)。
- **用户 focus (技术类: 资金流/筹码)**: 个股资金流 moneyflow_dc(低PIT) / 筹码 cyq_chips(重建winner_rate) /
  北向 hsgt_top10+ccass / 大宗 block_trade / 龙虎 hm_detail(10000档, 高PIT须t-1)。
- **口径铁律**: 行业/概念资金流必须 flow vendor = membership vendor (东财链自洽 dc_member+moneyflow_ind_dc;
  申万只做中性化; **禁同花顺第三套**)。混用=§4.5 sector leakage 同源。
- **原则**: 不为没证明的数据建保鲜管道 (architect rule6); 验出 alpha 才抓 (超 L0 标尺 +0.064 才转正)。

## 4. 因子层 (因子来源 = 验证矩阵的列)
- **裸K线 L0** (已建): reversal/macd/ma/turtle, 标尺 reversal OOS RankIC **+0.064**。
- **Alpha158** (旧panel已删PIT不可信, 验证时干净重算): 64 OHLCV 因子 = 判"裸K线还有没有油水" (超+0.064则K线有料, 不超则增量须来自新数据)。
- **tushare 新数据因子** (验证后接): 资金流/筹码/财务 (menu P0/P1)。

## 5. 策略层 (用户核心想法: **条件化, 非万能公式**)
> L0 市场级 reversal 仅 +0.064, **因把所有形态平均了** (低位用reversal/上升用动量互相抵消)。正解=条件化。
**策略立方体** cell = (Segment 形态 × Feature 因子 × Policy 公式), owner=conditional_stage_strategy_design + multidim_strategy_architecture:
- **Segment 形态** (用户): 横盘/低位/上升通道/下跌通道/高位 = `technical_stage` (Weinstein 5阶段) + MACD零轴/历史分位(PIT expanding)细分轴。
- **Policy 公式**: 20公式+主升浪猎手, 找"哪个公式适配哪个形态"。
- **Feature 因子**: 换手/筹码/资金流/Alpha158 在 cell 内增 alpha (主辅: 主公式出仓, 因子只调制)。
- **第一实验** (最便宜): per-stage L0 IC — reversal 在低位是否远超 +0.064 → 证条件化 + 出(公式×形态)适配矩阵 = 形态维解锁证据。
- **治理**: 维度爆炸用 DSR/PBO 压 (n_trials 如实计全 cell); **逐维解锁** (先证形态维优于基线再加因子维); 数据驱动 regime 聚类作补充。
- **宇宙**: `universe.py` 排除 ST/退市/三板/北交所 (前缀非60/00/30/68 + ST名 + 退市no-trade)。

## 6. 验证层 (可靠性阶梯, 防过拟合第一约束; owner=model_validation_reliability_design)
> 漂亮回测 = 真本事 or 运气? 用一道**阶梯** (多 null 逐层证伪), 非单一 p。一个过不了随机性检验的策略不投一分钱。
```
Gate0 PIT-clean (3门固化: PIT行为门/embargo切分/异常红线; pit_guard)
Gate1 walk-forward OOS RankIC > L0 标尺 +0.064 (oos_ic)
Gate2 MC 截面置换: RankIC > shuffle null 95% (新建; 测真横截面技能)
Gate3 DSR (Bailey-LdP, 已建): 选参 best 在 N trials 下显著 (多重比较去偏)
Gate4 PBO (CSCV, 恢复): IS-best 在 OOS 仍 best
Gate5 MC 块自助 Sharpe + 回撤压力 + 成本MC (Tier-2 backtest)
   → 全过才进实盘候选
```
纠错: 文章"打乱收益测夏普"无效 (夏普置换不变); 正解 feature↔label 截面置换 + 信号重生于 bootstrap 路径。
MC/PBO 是过滤器非圣杯 (过了仍可能 PIT/生存者偏差失效)。Tier-1 RankIC 快筛 + Tier-2 策略 sharpe 终验 (两者都算)。

## 7. 实验台 (alpha 验证程序 S0-S4, owner=alpha_validation_program_spec)
S0 实验台✓(experiment_store 4留档表 + consumer_alpha family + 执行器) → S1 数据✓ → S2 harness →
S3 逐数据验证 → S4 判决。留档链: pre-reg(冻结判据 prereg_hash) → verdict JSON → DB → ledger。隔离 live 防污染。

## 8. 纪律与工具调度 (固化进 skill `chunkymonkey-ops`)
| 工具 | 何时用 | 守什么 |
|---|---|---|
| **moth** `moth assert --repo .` | commit前/接手/重大改动 | claims-vs-reality 弹仓 (layer/godfile/leakage gate/legacy-flow/防过拟合) |
| **codegraph** sync/query | substantial change (新service/LOC>50/拆模块/改JOIN) | 耦合/依赖/影响面 |
| **gate 脚本** | commit + 重构验收 | data_layer_audit / legacy-flow-no-pollution / check_rule_compliance |
| **grill** (chunkymonkey-governance skill + plan_grill_gate hook) | 跑批/Optuna寻优/新模块前 | 跑了有没有用 (search space非空/前提验/成本) |
| **Workflow** (ultracode) | 全面 audit/对抗验证/fan-out | 多视角证伪 (本会话: 对抗泄漏审计抓 embargo 死闸) |
| **safe_commit.sh** | 每次 commit | hook 矩阵 (INDEX同步/rule/no-emoji/self-check/post-fix-audit) |
| **subagent** (Explore/general) | 跨文件 audit/research | 主对话只带结论 |
高频 commit+push; 高风险动作(删/force)先问; codex 复审非阻塞但 CRITICAL leakage 完全接受。

## 9. 重构 (老流程污染清除, owner=system_refactor_architecture)
教训 (DB 9.1G 根因): 删层必删caller / 删schema留caller=静默degraded / append-only无retention=膨胀 /
孤儿config引用(238处) / 散落DDL绕schema门(alpha158循环根) / daily_update自当真相源。
重构 (gate红→绿验收): 退役老daily_update → 清238孤儿引用 → 3表加retention → 散落DDL包layer-gate → bloat回收。

## 10. 路线图 (当前态 → KPI)
```
[✓] 地基reset (85表/2.5G) + 8层框架 + genesis法
[✓] S0 实验台 + L0 Tier-1 标尺 (reversal +0.064, 对抗审计清) + 寻参治理层(DSR)
[✓] 数据菜单评估 + 可靠性/条件化策略 设计 + 重构架构设计 + 教训工具化(gate)
[ ] 重构执行 (老流程退役, gate 转绿) ← 当前决策点
[ ] per-stage L0 IC (证条件化, 解锁形态维)
[ ] Tier-2 backtest 引擎 + MC/PBO 补全 (可靠性阶梯)
[ ] 逐数据 alpha 验证 (cashflow/block_trade/资金流/筹码, 超双标尺才入)
[ ] 策略立方体逐维解锁 → 含成本 paper_sim → KPI 达标 → 实盘候选
```

## 11. 文档体系 (防"到处指引到处找"; 整合后)
- **本文件** = 顶层骨架 (读它先有全局)。**goal.md** = 当前阶段+genesis法+KPI。**PROJECT_INDEX.md** = 活索引(表/模块/脚本)。
- **owner 细节 doc** (本文件引用, 不复制): data_management_framework(数据层) / strategy_validation_contract(PIT/Optuna) /
  PROJECT_CONSTITUTION(三原则) / 各 analysis 设计 doc (alpha验证/L0/可靠性/条件化策略/重构架构/数据菜单)。
- **skill `chunkymonkey-ops`** = 操作知识固化 (坑/工具调度/纪律/反例), 后续开发持续 invoke。
- **退役**: 36 个已标"已偏离" analysis + 过时 docs (architecture_reform_context 等) → 移 _retired_/ 或删 (防污染)。
- **CLAUDE.md** = 瘦身到会话红线 + 指针到本文件/skill (不重复细节)。
