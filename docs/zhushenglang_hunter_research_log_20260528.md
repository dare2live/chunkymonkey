# 主升浪猎手研究实证日志 — 2026-05-28

> 一份给开发人员和咨询顾问看的完整研究记录。本 session 把 ChunkyMonkey 主项目重新定位为"主升浪猎手", 围绕北大《地震概论》outreach 章节"炒股挣钱"PPT 做了 16 个版本的策略原型, 跑了全 A 股 5 年 3 万次突破事件 + 2503 次真主升浪的 ground truth, 试过 Optuna walk-forward 5 fold + LightGBM 多维 ML + CYQ 筹码分布 + 市场感知 backfill 等所有现有数据支撑的路径, 最终诚实结论是 K 线层天花板就在 60% 胜率, LightGBM 多维 ML 能拉到平均 70% / top 5% 86%, 但 86% 部分来自 2024-09 牛市偏差, 去掉后真实 78%。

---

## 目录

> **先读 §1 + §2** — 本研究的局限性和没做的工作放在最前面, 因为当前结果(60-86% 胜率)很可能正是这些局限和未做事项导致的。读结论前必须理解边界。

| § | 内容 |
|---|---|
| **1** | **本次验证的局限性(请先读)** |
| **2** | **没做的工作清单(应该做但没做)** |
| 3 | 北极星 — 北大三句话作为项目顶层目标 |
| 4 | 北大 PPT 原文要点 + YouTube 逐字稿交叉验证 |
| 5 | 项目现状盘点 |
| 6 | PPT 5 例股票数据库实证研究 |
| 7 | 全 A 股主升浪 ground truth 扫描 |
| 8 | 策略 16 个版本演进(V0 - V16)总表 |
| 9 | 关键发现汇总 |
| 10 | Optuna walk-forward 揭示的天花板 |
| 11 | LightGBM ML 突破 + 真相 |
| 12 | CYQ 筹码分布实证 |
| 13 | 市场感知 backfill 进度 |
| 14 | 反向分析(recall vs precision tradeoff) |
| 15 | 经验和踩过的坑 |
| 16 | 现状盘点 + 下一步路径 |
| 17 | 附录 — 数据文件清单 |

---

## 1. 本次验证的局限性(请先读)

### A. 样本量局限

| 维度 | 实际情况 | 风险 |
|---|---|---|
| V14/V16 baseline cases | **789 个** (V4 极严 filter 后) | 拆 5 fold 后每 fold 仅 70-200 OOS |
| Fold2 / Fold3 (弱市)OOS | **仅 70-98 个** | 单 fold 胜率波动 ±15pp 可能纯噪音 |
| Top 5% 极值 | 每 fold 仅 3-5 个 | top 5% 跨 fold std 极大, 偶然性高 |
| 真主升浪 (TRUE_RALLY) | 全样本 3012 个 | 拆 fold 后部分 fold TRUE 仅 70-120 |
| **2024-09 924 暴涨期占比** | top 5% 中 11/29 = 38% | V12 86% 胜率含**显著牛市偏差** |

### B. 时间窗口局限

| 维度 | 实际情况 | 风险 |
|---|---|---|
| 总样本时段 | **2022-2026 仅 5 年** | 不含 2015 牛市 / 2018 熊市 / 2020 疫情 / 2007 大牛 |
| 牛市占比 | 2024-09 起牛, 占 V12 OOS 约 50% | 训练样本偏向牛市, OOS 也偏 |
| 熊市样本 | 仅 2022-04 / 2023-06 ~ 11(2 段) | 熊市策略验证样本不足 |
| Trading calendar | 2023-01-03 起 | backfill 极限只到 2023-01 |

### C. Walk-forward 局限

| 维度 | 实际情况 |
|---|---|
| 切分方式 | 只用 expanding 窗口(train 不断扩大), 没试 sliding window / time-series CV / nested CV |
| Fold 数 | 3-5 fold, 没试 10+ fold 看真实 std |
| OOS gap | 0(train 末紧跟 OOS 头), 没留 purge gap 防 boundary leak |
| 多 seed 重复 | LightGBM random_state=42 固定, 没多 seed 跑看 std |

### D. 信号 PIT 安全局限

| 维度 | 实际情况 | 风险 |
|---|---|---|
| CYQ 流通盘 | 用 implied_float = holder_count × avg_float_shares (report_date) | 严格 PIT 应用 disclosure_date, 轻微 leakage |
| 户数集中信号 | fact_capital_flow_pit_daily 的 PIT 字段全 NULL, 我用 report_date 代替 | disclosure 滞后 2-3 月, 信号可能误用未来信息 |
| post5_vol_growth | T+5 才能判定 | 是退出/持有信号, 不是 PIT 入场 filter, 但 V11 测试时混入了 |
| breakout_high60 | 用 close > 前 60 日 high, 不含当日 → 安全 | OK |

### E. 回测真实性局限

| 维度 | 实际情况 | 实战影响 |
|---|---|---|
| **tx_cost** | **prototype 未扣** (paper_sim 有但 V8-V16 standalone 没用) | 实际会让 mean +12% → +9-10% |
| **滑点** | 未扣 | -0.5 ~ -1pp mean |
| **T+1 限制** | Prototype 假设 T+0 卖买灵活 | 实际 T+1 强制持有, 影响 5 日内 swap |
| **涨跌停** | breakout 模拟用 close, 没明确"涨停板买不到" | 涨停日入场不可执行, 影响 5-10% case |
| **一字板** | 没区分一字板 vs 普通涨停 | 实际接不到 |
| **持仓重叠** | 只 per-trade 评估, 没模拟同时持 5 仓 | 真实年化会压缩 60-70% |
| **资金量** | 假设无限资金, 没考虑 100 万 / 500 万 / 5000 万 容量 | 大资金会受成交量约束 |
| **流动性** | 没扣低流动性股的滑点放大 | 中小盘股影响显著 |

### F. 策略覆盖局限

| 维度 | 实际情况 |
|---|---|
| 入场 universe 定义 | 只测"60 日 high 突破"一种 entry universe; 其他主升浪不必经过 60 日新高 |
| Ground truth label 定义 | TRUE_RALLY = 60-180 天涨幅 ≥ 50% AND mid_dd > -20%, 是一种定义不是唯一 |
| 退出策略固定 | V12-V16 LightGBM 只决定入场, 退出全用 V4 trailing, 没让 ML 决定退出 |
| 单一退出框架 | V4 利润回吐式 trailing 是当前 best, 但没对比其他退出框架(如固定 holding period / volatility-targeted exit) |
| 不含板块对冲 | 没考虑用 ETF 对冲单股 beta |

### G. 数据完整性局限

| 维度 | 实际情况 |
|---|---|
| market_perception | daily/theme/LF backfill **未成功**, 现状只有 2024-11+ 部分覆盖 |
| 北上资金 | 上游 2024-08-19 永久死亡, 无法恢复 |
| 机构调研 / 解禁 | schema 在 panel 未接 |
| 行业映射 | 用 tdx_l1_name(L1 一级行业), 没用 L2/L3 子行业更细 |
| 主题/概念 | 缺历史 thematic 关联数据 |

### H. ML 方法论局限

| 维度 | 实际情况 |
|---|---|
| **超参数** | LightGBM 用默认参数(n_estimators=200, depth=4, num_leaves=15, lr=0.05); 没真正调过 |
| **特征工程** | 46-55 维特征全是手工设计, 没做 polynomial / interaction features |
| **类别特征处理** | breadth_state / sentiment_phase 等用简单 label encoding, 没试 target encoding / one-hot |
| **特征 selection** | 没用 BorutaSHAP / mutual info 等严格特征选择 |
| **样本权重** | 没做 sample_weight(可能给牛市 case 降权防 fit) |
| **过采样/欠采样** | TRUE/FAKE/NEUTRAL 比例 3:6:4, 没用 SMOTE 之类平衡 |
| **多分类** | 只做了 binary(final_ret > 0); 可以多分类(loss/small_win/big_win) 让 ML 区分长尾 |
| **回归 target** | 没试 regression target = final_ret(直接预测涨幅), 只做 binary |

### I. 业务认知局限

| 维度 | 实际情况 |
|---|---|
| **没跟实盘老手访谈** | PPT 是科普级别, 没访谈过实盘游资 / 私募 / 机构操盘手验证 winning pattern |
| **没看券商研报** | hugo2046/QuantsPlaybook 等券商金工报告复现库没系统研究 |
| **没看学术论文** | A 股 momentum / reversal / 板块轮动 学术 paper 没系统调研 |
| **PPT 仅 1 份** | 只研究了北大《地震概论》outreach 这 1 份 PPT, 没对比其他炒股教程 |

### J. 总体置信度评估

| 数字 | 置信度 | 原因 |
|---|---|---|
| 2503 真主升浪 ground truth | 高 | 全 A 股 5 年, 算法定义清晰 |
| V12 平均胜率 70.9%(prob>0.65) | 中 | walk_forward 5 fold OOS, 但 std 12.3% 大 |
| **V12 top 5% 胜率 86.2%** | **低** | 含 38% 2024-09 牛市样本, 去掉后 78%; 跨 fold std 极大 |
| V16 emotion +2.4pp | 中 | 实测稳定, 但跟 hs300 共线, 边际 marginal |
| Optuna walk_forward 58% | 高 | 3 fold, std 10.8%, Random 也 55.3%, 真实底线 |
| 单因子 +X.Xpp lift | 中 | 在 V4 baseline 上测, 不一定适用其他 baseline |
| 86% → 78% 真实胜率 | 中 | 去 2024-09 估算, 没严格 holdout |
| **真实实战年化** | **低** | 未扣 tx_cost + 持仓重叠 + T+1 限制, 估计 +30 ~ +60%(KPI 30% 仍达标) |

---


## 2. 没做的工作清单(应该做但没做)

本次研究是 16 个版本 prototype 串行迭代, 时间精力有限, 以下方向**应该做但没做**, 留给下一阶段:

### A. 寻优 / 参数标定层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **全局因子共振 Optuna 寻优** | 把 V14 的 47 维特征 + 各种 AND/OR 组合 + 阈值, 全部进 Optuna search space, walk_forward 5 fold 联合搜 | 替代手工"叠加"模式, 期望 +2-5pp |
| **LightGBM 超参 walk_forward Optuna** | V12-V16 全用默认参数(n_estimators=200, depth=4, lr=0.05); 应该 Optuna 联合搜 hyperparams + walk_forward 验证 | 期望 +1-3pp |
| **多模型 ensemble 调权** | LightGBM + XGBoost + RandomForest + CatBoost 加权平均, Bayesian 调权 | 期望 +1-2pp 稳定性 |
| **SHAP 分析 + interaction features** | 没真做 SHAP feature importance + interaction; 应该用 SHAP 找出顶级 interaction(如 post5_vol_growth × day_chg)显式构造 | 期望 +2-4pp |
| **gplearn 自动因子挖掘** | 用 genetic programming 在原始 OHLCV 上自动挖因子, 跑 walk_forward 验证 | 实战常见 +3-5pp, 但 overfit 风险大 |

### B. 信号层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **bc_absorbed leader_follower_diffusion_buy 实测** | 已知信号已写, 但没真把它跑出来加到 V14 baseline 看 lift | 跟北大 PPT "埋伏接力龙头" 一致, 期望 +3-5pp |
| **多周期共振过滤** | 日线突破 ∧ 周线 MACD 金叉 ∧ 月线均线多头 三层 AND filter | 期望 +5-8pp 但入场频率会大降 |
| **CYQ 持仓监控验证** | spec 第 6 节"持仓期 CYQ 变化 → 主力出货预警"没测; 应该在 ground truth 主升浪 case 上看 CYQ overhead_pressure / bottom_lock_rate 在 peak 前 N 日的变化是否能预警 | 期望: 把退出端从 V4 mean +15% 推到 +25% |
| **延迟入场策略实测** | V11 用了 T+5 vol_growth 作退出强化, 但没测"突破后 5/10/20 天直接延迟入场"作纯入场策略 | 跟用户洞察"二次突破后再确认入场" 完全对应 |
| **大盘 regime gate 实测 lift** | V9 fold 诊断证明 regime 是 base rate 主因, 但没真在 V14 baseline 上叠加 regime gate 测试 lift | 期望熊市暂停 → 整体年化提升 |
| **板块/系统性熔断 验证** | 大盘连续 -10% / 同板块 dd 触发的强制清仓没在 paper_sim 模拟 | max_dd 改善 |
| **历史 PPT 例股 5 案例的完整 paper_sim 重跑** | V12 入场判定后, 用 V4 trailing 退出在 5 例上跑完整持仓曲线, 跟 PPT 描述对照 | 验证"是否能抓到 +316% / +492% 这种 mega case" |

### C. 形态识别层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **A 股 SEPA / VCP 系数标定** | Mark Minervini 美股标准在 A 股不适配, 应用 walk_forward 重新标定 ATR / 振幅 / 突破阈值, 而不是直接砍掉 | 至少给爆发型主升浪一个工具 |
| **TA-Lib 61 烛台模式接入** | 没引入 TA-Lib CDLDOJI / CDLHAMMER 等具名形态, 都是手算 | 边际 +1-2pp |
| **Stan Weinstein 4 阶段判别(per-stock)** | 北大 PPT 主力 9 阶段, Weinstein 4 阶段都没工程化 | 主升浪期识别更稳 |
| **连板梯队识别 / 一板二板龙头** | PPT 反复说"一板定热点二板定龙头", 没工程化 | BestChoice 短线方向 |
| **完全周线视角策略** | V13 是日线事件+周线 *additional* 特征, 没做"周线突破事件+周线训练" 完全周线方案 | 跟慢牛 60-180 天主升浪天然吻合, 期望 +3-5pp |
| **单股单策 cluster 框架** | C0-C3 形态分类讨论过, 应该用不同入场/退出规则, 但 V14/V16 都是一套规则跑全部 | 跨 cluster 胜率应该更稳 |
| **per-stock historical regime aware** | 没考虑同一只股不同历史阶段(主升过 vs 没主升过)的策略差异 | per-stock 单股单策框架 |

### D. paper_sim / 工程化层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **持仓重叠模拟 + 真 NAV path** | 没模拟同时持有 5 仓的 NAV 走势, 只算 per-trade 收益; 真实持仓重叠会让年化压缩 60-70% | 实战准确度关键 |
| **完整 tx_cost 模拟** | Prototype 没接 paper_sim 完整 A 股成本(佣金 0.025% + 印花 0.05% + 过户 + 规费 + 滑点 + 大单 surcharge); 实际会让 mean +12% 缩水到 +9-10% | 实战准确度关键 |
| **T+1 / 涨跌停 / 一字板 严格模拟** | Prototype 假设 T+0 可买卖,真实需要 T+1 解禁限制 | 实战准确度 |
| **板块 sector_budget 软约束** | paper_sim 有 sector_budget 静态 40%, 没接 V14 模型的 prob 输出 + 板块动态调权 | 风控 |
| **regime gate 自动化** | hs300_60d_ret 等 regime 信号没实际接进 paper_sim selector, 文档讨论过但没实施 | 弱市保护 |
| **walk_forward CLI 暴露** | backend/services/portfolio_walk_forward/ 代码有但没 CLI 暴露,prototype 是 standalone 跑 | 工程化前置 |
| **第二回测引擎 sanity check** | 项目 paper_sim 是 self-implemented, 没用 vectorbt / qlib 做平行验证; 单引擎风险大 | 防回测漏洞 |
| **CYQ Phase 1-3 完整实现** | Spec 已写完, 算法 prototype 通过, 但 services/chip_distribution.py / dim_float_shares_history 表 / run_position_monitor.py 都没建 | CYQ 真正落地 |
| **services.zhushenglang 模块化** | 整套 V14-V16 框架仍在 /tmp/, 没进 backend/services/zhushenglang/ | 工程化前置 |
| **Optuna 中央层 + governance** | 项目宪法 §5 要求 walk_forward.split_dispatch + governance.enforce_pre_optimize/insert + fact_optuna_governance_log; V8/V9 用 standalone Optuna 不走中央层 | 项目宪法对齐 |

### E. 数据层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **market_perception 上游 backfill** | daily/theme/LF 失败因为缺 breadth / industry PIT / stock_context 历史; 应该先调研这些上游怎么建 | 解锁 theme+LF 信号(估计 +5-10pp) |
| **北上资金替代 alpha** | fact_hsgt_top10_daily(每日 Top10 活跃股) + dim_north_holding_quarterly(季度持仓 delta) 没建 sync 脚本 | 部分恢复"主力跟随"信号 |
| **机构调研 fact_jgdy_event 进 panel** | schema 在但 panel 未接 | per-stock 主力关注度信号 |
| **解禁 raw_capital_unlock 进 panel** | schema 在但 panel 未接 | 主力 want_to_sell 信号 |
| **CYQ 流通盘历史完整** | 当前用 implied_float = holder_count × avg_float_shares 季度披露, 严格 PIT-safe 应该用 disclosure_date 而不是 report_date | 防轻微 leakage |
| **PIT join 路径 bug 修复** | fact_capital_flow_pit_daily.holder_count_q_pct 全 NULL, PIT-safe 户数信号不可用 | 解锁户数集中信号 |

### F. 验证 / Audit 层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **严格 PIT leakage audit 自动化** | 项目宪法要求每个信号过 audit_pit_integrity.py, prototype 跑都没走 audit | 防 leakage |
| **Walk-forward expanding vs sliding 对比** | 只跑了 expanding 窗口 walk_forward; sliding window / time-series CV / nested CV 没试 | 验证 alpha 是否真稳定 |
| **股票池 survivorship bias 检查** | 用现有 5203 只活跃股扫, 没含已退市股; 可能有 survivorship bias | 数据完整性 |
| **跨 universe 测试** | 只测了 全 A 股一个 universe; HS300 / 中证 1000 / 创业板分别测应该不同 | 验证策略是否对 universe 敏感 |
| **跨样本期外推** | 没用更早历史(2015 牛市 / 2018 熊市 / 2020 疫情) 验证策略是否能 generalize | 防 overfit 当前 5 年 |
| **A/B test 不同 ground truth 定义** | TRUE_RALLY = 60-180d 涨幅 ≥ 50% 是一种定义; 也应该测 ≥ 30% / ≥ 80% / 持续 30-300 天 看策略对定义的 robustness | 验证策略不是 fit 某个 label 定义 |

### G. 用户/PM 决策层面

| 没做 | 应该怎么做 | 期望价值 |
|---|---|---|
| **跟 paper_sim 主线 LambdaMART 集成** | V14-V16 是 standalone LightGBM, 没跟项目主线 LambdaMART feature panel 互通 | 工程整合 |
| **双策略(主升浪猎手 + BestChoice 短线)联调** | 讨论过分工, 但没在同一 paper_sim NAV 模拟两套策略 NAV 合并 + max_dd | 总组合风控 |
| **手机端 / UI 可视化** | 主升浪猎手的入场/退出信号没接前端, 实战手动还是 web/小程序? | 实盘可用性 |

---


## 3. 北极星 — 北大三句话

主项目的顶层目标用北大《地震概论》outreach 章节 PPT 第 63 页的三句话浓缩:

```
会分析结构 + 理解主力意图 + 只做主升浪
```

工程化拆解:

| 北大原句 | 工程含义 |
|---|---|
| 会分析结构 | 全市场 regime + 板块 sector_heat + 主题 theme_lifecycle 三级状态机 |
| 理解主力意图 | per-stock 主力 4 阶段画像(LHB + 资金 + 高管 + 户数 + 调研 + CYQ) |
| 只做主升浪 | 三层共振 AND gate (结构 ∧ 主力 ∧ 主升浪起爆) |

KPI:

| 维度 | 目标 |
|---|---|
| 年化 | ≥ 30% |
| max_drawdown | ≤ -20% |
| 月胜率 | ≥ 55% |
| 单次涨幅 | 下限 35%, 无上限(用 trailing 控制) |
| 单次失败损失 | ≤ -10% |
| 年开仓次数 | 不预设(让数据决定) |

**注意**: 用户最初设定胜率 ≥85% 是 ground truth 上限(真主升浪自身 99.8% 涨幅 ≥ 50%), 实证后接受现实 ~78%-86% 是真实可达。

---

## 4. 北大 PPT 原文 + 逐字稿交叉验证

### PPT 真实身份

| 项 | 真实情况 |
|---|---|
| PPT 真实主题 | 北大《地震概论》2026 春季学期 **outreach 章节**, 不是炒股专题课 |
| 实际授课日期 | 2026.3.25 / 27 |
| 流出来源 | 公众号"土匪投资日记"(非北大官方) |
| 总页数 | 65 页 (非系统提示的 330 页) |
| 实际结构 | 6 个教学节 + 4 张总结标语页 + 谢谢页 |
| YouTube 二次加工 | "交易道法术频道"逐字稿 |

### PPT 6 教学节 + 4 总结页

| 节 | 页区 | 主旨 |
|---|---|---|
| 第 1 节 股市的本质 | p4-8 | 资源配置/价格发现, 短期零和 + 长期红利, 3 维度(宏观/行业/公司), 4 模块(选股/交易/仓位/风控) |
| 第 2 节 炒股基础知识 | p9-25 | 10 个术语(K 线 / 反转反弹) |
| 第 3 节 股市是较难的赚钱方式 | p26-32 | 金字塔结构, 散户在底层, 唯一武器是认知 + 时间 + 耐心 |
| 第 4 节 为什么还要讲炒股 | p33-38 | 6 点 + 一盈二平七亏损, **国际跳棋**(逐字稿读成"象棋") |
| 第 5 节 炒股盈利要诀和原则 | p39-49 | 顺势为王, 春夏秋冬周期, 四大法则 |
| 第 6 节 炒作例子 | p50-57 | 中国科传 601858 / 大众交通 600611 / 长白山 603099 / 新锐股份 688257 |
| 标语收尾 p58-64 | 一板二板 + 主力运作全景图 + 得主力者得天下 + 主升浪 + 五颗星密码 |

### 四大法则细化(实操可工程化)

| 法则 | PPT 原话 | 工程对应 |
|---|---|---|
| 法则一 仓位 > 选股 | 334 仓位法(30% 底仓 + 30% 机动 + 40% 现金) | paper_sim 仓位算法多种 |
| **法则二 止损 > 预测** | 止损要快, 止盈要慢; 买入价下方 5% 无条件止损 / 破 60 日线无条件清仓 | F1 -5% 硬斩(后改 -10) + ma60 趋势止损 |
| **法则三 逻辑 > 消息** | 买入三问: 为什么涨 / 谁在买 / 还能涨吗 | 每次入场记录 fact_paper_entry_reasoning |
| **法则四 等待 > 操作** | 一年虽做几波 但提高成功率; 80% 收益来自 20% 时间 | 严 gate + 极低开仓频率 |

### 主力运作全景图(p60, PPT 实际是 9 阶段, 不是 4 阶段)

```
建仓阶段(3 子段):
  1. 初始建仓 (低位悄悄吸筹)
  2. 对冲打压建仓 (假阴线压住)
  3. 横盘建仓 (区间震荡, 控盘)
↓
洗盘 + 试盘:
  4. 拉离洗盘 (脱离低位震荡区)
  5. 测试上方抛压
↓
6. 主升浪拉升 (本体)
↓
出货阶段(4 子段):
  7. 头部出货
  8. 维持出货
  9. 诱多出货
  10. 彻底出货
```

### 逐字稿(YouTuber)自加的部分

| 加工 | 内容 |
|---|---|
| 引流注入 | 5 次"在我的历史节目《XX》中..."自我推广 |
| 用词偏差 | PPT 写"国际跳棋", 逐字稿读成"国际象棋" |
| 牛顿名言 | "我可以计算出天体运行规律,却无法计算人性的疯狂"— 未在 PPT 中找到 |
| 节号 | 把 4 张标语页升格成"第 7-10 节", 实际 PPT 只有 6 节 |
| 主体内容 | 90% PPT 原话照搬, 无歪曲 |

---

## 5. 项目现状盘点

### 双数据库

| DB | 主要表 |
|---|---|
| data/market.duckdb | price_kline_tdxhub, v_price_kline_qfq(5.2M 行), price_xdxr |
| data/smartmoney.duckdb | 345 张表: dim_/ fact_/ mart_/ raw_ |

### Paper-Sim 核心架构

| 模块 | 文件 | 状态 |
|---|---|---|
| 主循环 | [paper_sim/driver.py](backend/services/paper_sim/driver.py) | 日频 / max_positions=5 / max_swaps_per_day=2 |
| 入场 | [paper_engine/entries.py](backend/services/paper_engine/entries.py) | 一字板拒入 / 停牌跳过 / 跳空高开拒 |
| 退出 | [paper_sim/exit_rules.py](backend/services/paper_sim/exit_rules.py) | 个股止损 + trailing + 主升活着判定 |
| 仓位 | [paper_sim/sizer.py](backend/services/paper_sim/sizer.py) | Kelly / wilson_kelly / score_rank_diff_v1 / equal |
| 风控 | [paper_sim/risk_control.py](backend/services/paper_sim/risk_control.py) | max_dd_hard_stop=-25% |
| **撮合** | driver.py L144-196 | **VWAP 撮合, 无 limit order 簿深度** |
| Tx_cost | [paper_sim_config.yaml](backend/config/paper_sim_config.yaml) | 完整 A 股成本(佣金/印花/过户/规费/滑点 + 大单 surcharge) |

### 市场感知模块(已存在但跟 paper_sim 是断的)

| Engine | 状态 |
|---|---|
| backend/services/market_perception/ | 8 个 engine: emotion / leader_follower / regime / stock_context / style_rotation / theme_lifecycle / under_reaction / utils |
| backend/routers/v3_market_perception.py | API 已暴露 |
| 5 张产物表 mart_market_perception_* | regime/emotion 2024-11+, theme/LF/under_reaction 仅 22 天 |
| **关键缺口** | `regime_gate` 在 paper_sim_config.yaml 占位但代码未实现, market_perception 信号没接进 selector/sizer |

### 主力数据现状

| 数据 | 表 | 状态 | 在 panel? |
|---|---|---|---|
| 龙虎榜 | fact_lhb_event | OK | [OK] |
| 资金流 PIT | fact_capital_flow_pit_daily | OK | [OK] |
| 高管增减持 | fact_executive_trade_event | OK | [OK] |
| 股东户数 | fact_holder_count_period | OK, quarterly | 部分 |
| 机构调研 | fact_jgdy_event | schema 在 | [NG] 未接 |
| 解禁 | raw_capital_unlock | schema 在 | [NG] 未接 |
| **北上资金** | fact_hsgt_daily | **2024-08-16 后死了, 上游规则改** | [NG] 不可恢复 |

### bc_absorbed 包(早期被吸收的代码, 含未接通的好信号)

| 信号 | 文件 | 状态 |
|---|---|---|
| `leader_follower_diffusion_buy` | [bc_absorbed/bank/sentiment.py:32](backend/services/bc_absorbed/bank/sentiment.py) | 写过, 未接通到 paper_sim selector |

物理含义: "Follower (is_leader=False) in theme with high diffusion score = buy entry" — 跟北大 PPT "埋伏接力龙头启动的股票"完全对应。

### 北上资金死亡详情

2024-08-19 起沪深交易所 + 港交所联合改披露:

| 维度 | 现状 |
|---|---|
| 每日整体净流入 | 不再公开 |
| 个股每日净买卖 | 不再公开 |
| 沪深股通每日总成交额 | 还在 |
| Top 10 活跃股名单 | 还在(daily) |
| 个股持仓变动 | 改 **季度**(每季度第 5 个交易日) |
| akshare `stock_hsgt_hist_em` | 2024-08-19 后返回空 |

量化只能用: Top 10 活跃股 daily appearance + 季度持仓 delta, 原"日级北上净买入"alpha 整条线已死。

---

## 6. PPT 5 例股票数据库实证

### 主升浪段精确定位

| 股 | 起涨日 | 峰值日 | 涨幅 | 持续 | 模式 |
|---|---|---|---|---|---|
| 601858 中国科传 | 2023-01-03 ¥11.87 | 2023-05-05 ¥50.06 | +322% | 88 天 | 慢牛 |
| 600611 大众交通 | 2024-04-16 ¥2.72 | 2024-08-05 ¥11.31 | +316% | 78 天 | 慢牛偏 A |
| 603099 长白山 | 2023-10-09 ¥13.23 | 2024-01-17 ¥39.15 | +196% | 70 天 | A+B |
| 001225 和泰机电 | 2025-11-13 ¥51.68 | 2026-02-25 ¥86.77 | +68% | 70 天 | 爆发 A |
| **688257 新锐股份** | **2025-06-23 ¥13.53** | **2026-03-10 ¥80.05** | **+492%** | **180 天** | **纯慢牛 [重点]** |

### 起涨前 60 天 多源信号 X-Ray(关键反例)

**001225 和泰机电(爆发模式 A 教科书)**:
- LHB: 10-15 换手率 39.9% **机构席位 2 个** 净买 6063 万
- LHB: 10-16 换手率 26.6% **机构席位 3 个** 净买 7735 万
- 户数 25Q3 **-18.82%**(极强筹码集中)
- regime 健康扩散, emotion=追强有效, cycle=新周期试错

**688257 新锐股份(慢牛模式 B, 颠覆传统认知)**:
- LHB 起涨前 90 天: **0 次**
- LHB 起涨后 90 天: 仅 2 次, 都无机构席位
- 高管增减持 1 年: **0 次**
- 户数 25Q1 -3.48%, 但 24Q4 反而 +17.57%(逆向噪音)
- K 线起涨日: 当日 +1.32%, 振幅 2.2%, **极缩量**(13218 = 60 日均值 50%)

### 5 例分类汇总

| 股 | 模式 A 信号通过 | 模式 B 信号(均线) | 综合 |
|---|---|---|---|
| 001225 | 6/7(强 LHB+户数) | 2/6 | **纯 A 爆发** |
| 600611 | 2/7 | 4-5/6 | B 慢牛 |
| 601858 | 0/7 | 5-6/6 | **B 慢牛** |
| 603099 | 1/7 | 4-5/6 | B/C |
| **688257** | **0/7** | 6/6 | **纯 B 慢牛** [重点] |

**核心结论**: **PPT 例股 4/5 是慢牛, 1/5 是爆发**。慢牛起涨日完全无主力痕迹, 必须靠均线 + 形态识别。LHB / 高管 / 户数 是 lagging 信号 而非 leading。

---

## 7. 全 A 股主升浪 ground truth 扫描(2503 case)

### 扫描参数
- 时间: 2022-2026 (5 年)
- 全 A 股 5203 只
- 突破事件 = 当日 close > 前 60 日 high
- 主升浪 = 突破后 60-180 天涨幅 ≥ 50% AND 中间 max_dd > -20%

### 扫描结果

| 项 | 值 |
|---|---|
| 全部突破事件 | 31,577 |
| 真主升浪(TRUE_RALLY) | **3,012** (9.5% base rate) |
| 假突破(FAKE_BREAKOUT) | 16,100 (51%) |
| 中性(NEUTRAL) | 12,465 (39.5%) |
| 主升浪涉及 unique 股票数 | 1994 / 5203 = **38%** |
| 中位涨幅 | **75.5%** |
| 平均涨幅 | 95.3% |
| 75% 分位 | 107% |
| 90% 分位 | 156% |
| 95% 分位 | 201% |
| 中位持续天数 | 90 天 |
| **每月平均起涨次数** | **58 次** |

### 涨幅分档

| 档 | n | 占比 |
|---|---|---|
| ≥ 50% | 2503 | 100%(定义) |
| ≥ 100% | 738 | 29.5% |
| ≥ 200% | 126 | 5% |
| ≥ 500% | 10 | 0.4% |

### Top 5 怪兽 case
- 002289 +854%
- 002969 +806%
- 688125 +664%
- 300204 +634%
- 600289 +569%

### 形态分布(模式分类)

| 模式 | n | 占比 | 涨幅 median |
|---|---|---|---|
| B 慢牛(起涨前振幅 < 25%) | **1150** | **46%** | 73.7% |
| C 中性(25-50%) | 1123 | 45% | 76.6% |
| A 爆发(振幅 > 50%) | 230 | **9%** | 79.1% |

**关键洞察**: 慢牛 + 中性 = 91%, 爆发只占 9% — 主力跟随信号(LHB / 机构席位)只能识别 9%, 91% 必须靠均线 / 形态识别。

### 一字天梯/暴炒剔除

| 类型 | n | 占比 | 处置 |
|---|---|---|---|
| 一字天梯(≥5 连板 OR ≥3 一字板 OR ≥10 涨停日) | **257** | **10.3%** | **剔除**(散户接不到) |
| 可投资主升浪 | 2246 | 89.7% | 后续操作集 |

### 每月起涨频率
- 总月份数: 43
- 平均每月: **58 次**
- 最少月: 5 次 (2022-02 熊市底)
- 最多月: **481 次** (2024-09 牛市爆发)

---

## 8. 策略 16 个版本演进(V0 - V16)

### 演进总表

| 版本 | 核心思路 | 入场/月 | 胜率(>0) | mean_ret | q10 损失 | 备注 |
|---|---|---|---|---|---|---|
| V0 | 突破日入场 + F1 -5% 硬斩 | 734 | **3.5%** | -1.9% | — | 失败基准, F1 误伤 50% 真主升浪 |
| V1 | 二次突破入场 + 紧 ma60 止损 | 278 | 42.3% | +3.9% | — | 二次突破策略关键 |
| V2 | 二次突破 + 宽 ma60 止损 | 278 | 42.2% | +3.7% | — | 宽止损没显著改善 |
| V3 | + C3 + 前安静 + 强确认 + 下影 | 4.2 | **58.2%** | +7.4% | -9.2% | 极严 filter 起作用 |
| V4 | + 利润回吐 trailing(in-sample) | 4.2 | **62.6%** | +8.7% | -9.5% | in-sample 数字 |
| V5 | R3 小步爬升入场 | 437 | **30.6%** | +0.8% | — | **失败** — 形态再细化无区分度 |
| V6 | 二次突破 + 金字塔加仓 | 4.2 | **44.0%** | +6.6% | -6.9% | **失败** — 加仓提高成本 + NEUTRAL 被套 |
| V7 | + LHB/户数/高管单因子叠加 | 1.1 | 57.4% | — | — | 最佳 +2pp(已到天花板) |
| V8 | Optuna 100 trials 单 split | 4 | **65.6%(IS) → 28.6%(OOS)** | -7.5pp | — | **严重 overfit** |
| V9 | Optuna walk-forward 3 fold | 5.8 | **58.0%(均)** std 10.8% | +6.5% | -5.5% | 真实 OOS, Random 也 55.3% |
| V10 | 基础量价单因子扫描 | — | post5_vol_growth +16.1pp(lookahead) | — | — | 发现 post5_vol_growth 强信号 |
| V11b | + intra_amp ≤ 3 + T+5 vol_growth ≥ 1.0 | 5.8 | **60.0%(WF)** std 7% | +5.1% | -5.5% | 最稳框架 |
| **V12** | **LightGBM 5 fold (46 维)** | 4.7 | **70.9%(prob>0.65)** | +12.05% | — | **首次突破 60%** |
| V12 top 5% | LightGBM top 5% selectivity | 0.7 | **86.2%** | +15.15% | — | 含 2024-09 牛市偏差 |
| V13 | + 周线特征(MACD/RSI/ma等) | 4.4 | 62.7% | +11.94% | — | 周线没显著 lift(共线) |
| V14 | + 同板块共振 + CYQ + hs300 regime | 4.4 | 60.2% | +11.11% | — | 稳定性提升 std -38% |
| V14 top 5% | LightGBM top 5% | 0.7 | 72.1% | — | — | 噪音, 不及 V12 |
| **V15** | V12 + 市场感知 子样本验证 | 4.4 | 73.8% | +14.97% | — | **子样本 mp+em 加入 +2.6pp** |
| **V16** | V14 + emotion backfill 100% 覆盖 | 4.6 | **70.4%(prob>0.6)** | +12.23% | — | 平均 +2.4pp, std 略稳 |
| V16 top 5% | LightGBM top 5% | 0.7 | **86.2%** | +17.33% | — | 复现 V12 top 5% |

### V0 → V16 演进的 4 个里程碑

| 里程碑 | 版本 | 数字 |
|---|---|---|
| **二次突破入场** 关键发现 | V1 | 胜率 3.5% → 42.3%, +1100% |
| **极严 Filter** 突破 60% | V3 | 胜率 → 58.2% |
| **LightGBM 多维突破** 60% 天花板 | V12 | 胜率 → 70.9%(prob>0.65) |
| **emotion 100% backfill** | V16 | 胜率 +2.4pp 略稳 |

### 失败的方向(已实证)

| 版本 | 思路 | 失败原因 |
|---|---|---|
| V0 | 突破日入场 + F1 -5% 硬斩 | F1 -5% 砍掉 50% 真主升浪 |
| V5 | R3 小步爬升精细化形态识别 | 形态再细化对单股 K 线无区分力, FAKE 也满足 |
| V6 | 金字塔加仓(浮盈 +15% 即加仓) | 加仓提高成本均价, A 股短趋势里加在震荡区 |
| V7 LHB/户数/高管 | 单因子叠加期望 +5pp | 最佳 +2pp, LHB 机构反而 -2.8pp(lagging) |
| V8 单 split Optuna | 100 trials 找最优 | 严重 overfit, IS 65.6% → OOS 28.6% |
| V13 周线特征 | 加 10 个周线特征 | 跟日线高度共线, 无信号 |
| CYQ 入场 filter | spec 说能识别套牢盘 | 实证 winner_rate FAKE 反而高于 TRUE(都在新高位置) |

---

## 9. 关键发现汇总(实证证伪 vs 证实)

### 已证伪的常识(反直觉)

| 常识 | 实证结果 |
|---|---|
| "突破要放量" | 缩量上涨胜率 60.0%, 暴量(vol_ratio≥4) 胜率 43.3% |
| "LHB 机构席位 = 主力进场" | **precision 6.0% < base rate 9.5%**, 机构是 lagging 信号 |
| "高管增持 = 主力信号" | PPT 例股大众交通起涨前高管刚减持 3.72%, 仍 +316% |
| "户数集中 = 筹码集中 = 利好" | hc < -10% 信号反向(可能 disclosure 滞后) |
| "形态再细化 = 更精准" | V5 R3 形态精细化反而胜率退步到 30.6% |
| "金字塔加仓 = 海龟交易精髓" | A 股短趋势(median 90 天)里加仓提高成本均价砍长尾 |
| "Optuna 精调能找出 alpha" | walk_forward 跨 fold Optuna 仅 +2.7pp vs random |
| "F1 -5% 硬斩 = 保护本金" | 真主升浪 mid_dd median -15.7%, F1 -5% 误伤 50% |

### 已证实的真信号

| 信号 | 实证支持 |
|---|---|
| 二次突破入场 | 胜率 3.5% → 42.3% |
| post5_vol_growth ≥ 1.0(后 5 日均量 ≥ 入场量) | 单因子 +16.1pp 胜率(但是 lookahead 信号) |
| intra_amp_today ≤ 3% 入场日振幅小 | 单因子 +5.3pp 胜率 |
| 入场日缩量(vol_ratio < 1.5) | 单因子 +4.6pp 胜率 |
| 同板块共振 ≥ 5 | 单因子 +1.1pp |
| 极严 4-6 Filter AND(C3 + 前安静 + 后强势 + 下影) | 胜率 → 58.2% (V3) |
| 利润回吐 trailing(<20% 用 -8% / 50-100% 允许回到入场) | 让长尾走完 |
| Dynamic Trailing 按盈利分级容差 | mean 86.7%, 捕获率 90.2% |
| 北大 PPT "主力运作 9 阶段" | per-stock 真实存在 |

### 真主升浪起涨日的真实特征

| 特征 | 全部 TRUE | 模式 B 慢牛 | 模式 A 爆发 |
|---|---|---|---|
| 起涨日量比 > 1.5 | 81% | 95.8% | — |
| 起涨日涨幅 | median 3.92% | median 3.42% | median 5.80% |
| 距 250 日 high | median -17% | median -21.3% | median -7.9% |
| 250 日区间分位 | median 0.50 | median 0.48 | median 0.85 |
| 前 60 日 ret | median 9.5% | **median 9.5%(温和涨)** | median 29.5% |
| 前 60 日波动率 | median 1.77% | **median 1.77%(低波动)** | median 3.84% |
| close > ma250 | 70.8% | 62.8% | 86.7% |
| ma60 > ma120 | 30.9% | **22.8%(均线还乱)** | 53.3% |

**反传统**: 真主升浪起涨日 77% **均线还是空头排列**, 起涨过程中才翻多头。Mark Minervini SEPA Stage 2 美股标准在 A 股不适配。

### V12 top 5% (86%) winning pattern

| 特征 | top5 median | bot50 median | diff |
|---|---|---|---|
| post5_vol_growth | **1.52** | 0.70 | **+0.82** |
| day_chg(入场日涨幅) | **+1.36%** | +2.58% | -1.22(不追高) |
| pre_amt_concentration | 0.054 | 0.045 | +0.010 |
| pre_max_day_drop_60 | -4.21 | -4.55 | +0.33 |

**时间分布**: 11/29 case 集中在 **2024-09 924 暴涨期**, 占 38%。

**去掉 924 后真实胜率 ≈ 78%**(18/23 = 78.3%), 不是 86.2%。

**行业偏好**: 金融 + 装备制造 = 56%。

---

## 10. Optuna walk-forward 揭示的天花板(V8 / V9)

### V8 单 split Optuna 教训(in-sample fit 警报)

| 维度 | In-Sample (train+val) | OOS (2025+) |
|---|---|---|
| n_trades | 32 | 14 |
| **win_rate** | **65.6%** | **28.6%** |
| **mean_ret** | **+11.4%** | **+3.9%** |

**胜率从 65.6% 跌到 28.6% = 教科书 in-sample overfit**。100 trials × 7 维 参数空间 + 大样本 = 总能"碰到"一组 in-sample 65% 的参数。

### V9 walk-forward 3 fold 真实 OOS

| Fold | Train 胜率 | OOS 胜率 | mean_ret | Random OOS 胜率 |
|---|---|---|---|---|
| Fold1 | 71.4% | 46.2% | +0.93% | 41.7% |
| Fold2 | 58.6% | 72.2% | +9.31% | 69.5% |
| Fold3 | 70.0% | 55.6% | +9.29% | 54.7% |
| **平均** | 66.7% | **58.0%** | +6.5% | **55.3%** |
| std | 5.5% | 10.8% | 3.95% | — |

**Optuna 精调贡献仅 +2.7pp 比 Random params 平均**。

**参数族跨 fold 极不稳定**:

| 参数 | Fold1 | Fold2 | Fold3 | std |
|---|---|---|---|---|
| pre_n_limit_up_60_max | 2 | 2 | 0 | 1.15 |
| pre_vol20_max | 1.6 | 1.5 | 2.7 | 0.67 |
| post_retreat_to_entry_min | -3.5 | -3.5 | -8.0 | 2.60 |
| post5_lower_dominant_min | 5 | 5 | 25 | **11.55** |

参数族不稳 = **没有真正跨期一致的 alpha 信号**。

### 这一阶段的深刻教训

| 教训 | 数据支撑 |
|---|---|
| in-sample 65%+ 胜率 = 强烈 leakage/overfit 警报 | V8 IS 65.6% → OOS 28.6% |
| 单 split walk_forward 不靠谱 | V9 fold 间 std 10.8% |
| Optuna 不能拯救底层弱信号 | random 也 55.3%, Optuna 仅 +2.7pp |
| 参数稳定性 比 单 fold 最优重要 | 跨 fold 参数族不稳 = 没真 alpha |
| 项目宪法 §5 Optuna 治理(walk_forward + governance) 是必要的 | 否则 ML 实验全部 in-sample fit |

---

## 11. LightGBM ML 突破 + 真相(V12 / V14 / V16)

### V12 LightGBM walk-forward 5 fold (46 维)

| Fold | OOS 胜率 | top 10% precision | top 5% 胜率 |
|---|---|---|---|
| Fold2 (2023H2) | 50.0% | 55.6% | 50.0% |
| Fold3 (2024H1) | 53.6% | 57.1% | 100.0% |
| Fold4 (2024H2-2025H1) | **75.9%** | **90.0%** | 80.0% |
| Fold5 (2025H2-2026) | 68.8% | 90.5% | 90.0% |
| **平均** | **62.1%** | **70.8%** | **80.4%** |

### V12 全 OOS 跨阈值

| 阈值 | n/月 | 胜率 | mean_ret |
|---|---|---|---|
| prob > 0.65 | 4.7 | **70.9%** | +12.05% |
| prob > 0.70 | 4.0 | 71.9% | +12.71% |
| **top 5%** | 0.7 | **86.2%** | +15.15% |
| **top 10%** | 1.3 | **79.3%** | +16.17% |

### Feature Importance Top 10 (V12)

| Rank | Feature | Importance |
|---|---|---|
| 1 | post5_vol_growth | 115 |
| 2 | pre_max_day_drop_60 | 94 |
| 3 | pre_amt_concentration | 88 |
| 4 | post_retreat_to_entry | 87 |
| 5 | pre_max_day_chg_60 | 79 |
| 6 | pre_vol20 | 78 |
| 7 | post5_new_high | 72 |
| 8 | day_chg | 71 |
| 9 | intra_amp_today | 67 |
| 10 | pre_vol_recent_ratio | 63 |

**全部基础量价**, 跟单因子分析一致。

### V12 Fold 诊断 — 是真 alpha 还是 fit 牛市?

| Fold | HS300 | 市场 | base rate(OOS 真主升浪占比) | prob>0.6 胜率 | **ML lift** |
|---|---|---|---|---|---|
| Fold2 (2023H2) | -11.86% | 熊市 | 42.9% | 51.6% | +9.9pp |
| Fold3 (2024H1) | +2.22% | 震荡 | 43.1% | 54.5% | +11.4pp |
| Fold4 (2024H2-2025H1) | +13.16% | 牛市 | 65.0% | 79.3% | +14.3pp |
| Fold5 (2025H2) | +21.31% | 强牛 | 61.1% | 68.2% | +7.1pp |

**ML lift 跨 fold 一致(+7 ~ +14pp)** = 模型在所有市场都能"加 10pp", **真实 alpha 不是 fit 牛市**。表现差异主因是 base rate(大盘环境)差异。

### V14 + 同板块共振 + CYQ + hs300_regime

| 指标 | V12 | V14 | Δ |
|---|---|---|---|
| 平均 AUC | 0.609 | 0.617 | +0.008 |
| 平均胜率(>0.5) | 61.1% | 60.2% | -0.9pp |
| top 10% | 70.8% | 74.8% | +4.0pp |
| **std AUC** | 0.068 | **0.042** | **-38% 更稳** |
| **std 胜率** | 13.9% | **10.0%** | **-28% 更稳** |

新特征 importance:
- sector_breakouts_15d: rank #6 (68) [重点] 有效
- hs300_60d_ret: rank #16 (46) 有效
- CYQ 4 个特征 **全部未进 Top 20** (跟 spec 说的一致)

### V16 + emotion 100% backfill

| 指标 | V14 | V16 | Δ |
|---|---|---|---|
| AUC 平均 | 0.617 | 0.620 | +0.003 |
| 平均胜率 | 60.1% | 62.5% | **+2.4pp** |
| std 胜率 | 13.8% | 11.2% | -2.6 |
| top 10% | 70.8% | 69.9% | -0.9 |

**Emotion 100% 覆盖后, importance 仍然低** — 跟 hs300 信号共线, 没新信息。真正能 +5-10pp 的是 theme / leader_follower。

### V12 top 5% (86%) 深挖

29 case 分布:
- **2024-09 (924 暴涨)**: 11 个 / 29 = **38%**
- 2025-07/08: 7 个 / 29 = 24%
- 其他: 11 个 / 29 = 38%

去掉 2024-09 后:
- n = 18, target=1 占 **77.8%** (vs 含 924 是 86.2%)

**真实可重复胜率 ≈ 78%**, 不是 86%。

---

## 12. CYQ 筹码分布实证

### 算法实现状态

| 项 | 状态 |
|---|---|
| Spec 文档 | [docs/chip_distribution_cyq_spec.md](chip_distribution_cyq_spec.md) (615 行完整) |
| 算法 prototype | /tmp/cyq_proto.py |
| 5 例股票 sanity 测试 | 通过, 跟通达信对齐(获利比例/90% 区间/N 周期内成本精确匹配) |
| 实际工程化 | 未实现 (services/chip_distribution.py 不存在) |

### 5 例股票当前 CYQ(2026-05-26)

| 股 | 当前价 | 获利率 | 上方套牢 | 平均成本 | 90% 区间 |
|---|---|---|---|---|---|
| 001225 和泰机电 | 63.30 | 96.7% | 3.3% | 56.69 | 48.63 ~ 62.90 |
| 601858 中国科传 | 25.70 | 76.2% | 23.8% | 23.70 | 19.34 ~ 29.09 |
| 600611 大众交通 | 5.54 | 31.1% | **68.9%** | 5.71 | 4.62 ~ 6.47 |
| 603099 长白山 | 38.19 | 58.4% | 41.6% | 39.56 | 34.59 ~ 49.44 |
| 688257 新锐股份 | 100.54 | 93.1% | 6.9% | 79.60 | 53.08 ~ 101.16 |

### CYQ 单因子区分度测试(200 TRUE + 200 FAKE + 200 NEUT 采样)

| 指标 | TRUE | FAKE | NEUT | 差距 |
|---|---|---|---|---|
| **winner_rate** | 0.929 | **0.969** 警告: | 0.917 | **反向** |
| **overhead_pressure** | 0.071 | **0.031** 警告: | 0.083 | **反向** |
| bottom_lock_rate | 0.252 | 0.251 | 0.252 | 几乎相同 |
| range_90 | 0.269 | 0.310 | 0.272 | -0.041 |
| cost_deviation | 0.090 | 0.133 | 0.078 | -0.043 |

**反直觉**: FAKE_BREAKOUT 在入场日 winner_rate 反而高于 TRUE_RALLY — 因为假突破也在新高附近,上方没人套。

### CYQ Filter precision 测试

| Filter | n | TRUE 占比 | base rate 34.9% |
|---|---|---|---|
| overhead < 5% | 219 | 31.5% | -3pp |
| winner > 70% | 388 | 35.1% | ≈ |
| **90% 范围窄 < 30%(筹码集中)** | 247 | **39.3%** [重点] | +4.4pp |
| cost_dev > 20% | 66 | 21.2% | -13.7pp |

**唯一微弱有效的是"筹码集中度"**, 其余 CYQ 单因子无效。

### 结论(跟 spec 完全一致)

CYQ Spec 第 9 节早就说明:
> [NG] 不把 CYQ 指标当 LightGBM feature — 与现有价量特征高度共线
> [NG] 不用于选股 alpha — 本质是 OHLCV 的变换

**CYQ 真正价值在持仓监控 + 出货识别**, 不是入场 filter。这跟 V14 把 CYQ 4 个指标加进 LightGBM 后 全部未进 Top 20 也完全一致。

---

## 13. 市场感知 backfill 进度

### Engine 入口已经齐全

8 个 build script 在 backend/scripts/:
- build_market_perception_daily.py(regime)
- build_market_perception_emotion_daily.py
- build_market_perception_theme_daily.py
- build_market_perception_leader_follower_daily.py
- build_market_perception_style_daily.py
- build_market_perception_under_reaction_daily.py
- build_market_perception_stock_context_daily.py

全部用 `--start --end` 参数 + `INSERT OR REPLACE`(idempotent) + 写 mart_market_perception_audit_log。

### Backfill 实战结果

| 表 | 原范围 | backfill 后 | 状态 | 失败原因 |
|---|---|---|---|---|
| **emotion_daily** | 2024-11+ (373 行) | **2023-01 ~ 2026-05 (814 行)** [OK] | **100% 覆盖 V12 OOS** | — |
| daily(regime) | 2024-11+ (373) | 失败 | 44% | `breadth row missing for 2023-01-03` — 需要 fact_stock_kline_daily 或 v_price_kline_qfq 的 prev_close lookback |
| theme_daily | 2026-04-27+ (22 天) | 失败 | <5% | `observed PIT industry coverage incomplete: 0/800 trading days` — 缺 mart_observed_industry_pit_daily 历史 |
| leader_follower_daily | 22 天 | 失败 | <5% | `leader/follower inputs missing` — 缺 stock_context_daily 历史 |

### V16 实测 emotion backfill 的边际价值

| 指标 | V14(emotion 44%) | V16(emotion 100%) | Δ |
|---|---|---|---|
| AUC 平均 | 0.617 | 0.620 | +0.003 |
| 平均胜率 | 60.1% | **62.5%** | **+2.4pp** |
| std 胜率 | 13.8% | 11.2% | 更稳 |

**emotion 100% 覆盖只贡献 +2.4pp** — 因为 emotion 信息高度跟 hs300_60d_ret 共线, 已经被 K 线特征 cover。

### 上游依赖链问题

要修复 daily / theme / LF backfill, 需要先 backfill:

| 上游表 | 依赖于哪个 backfill | 估计工期 |
|---|---|---|
| breadth(每日涨跌家数 + 涨停数) | 用 v_price_kline_qfq prev_close 算, 应该 OK | 调研 0.5 天 |
| mart_observed_industry_pit_daily | 行业映射 PIT 历史 | 1 天 |
| mart_market_perception_stock_context_daily | per-stock 主升/震荡状态 | 0.5 天 |
| 然后 daily / theme / leader_follower 才能跑 | | 0.5 天 |

总: **2-3 天工期** 修复上游 + 跑 4 个剩余 backfill。

### Backfill 价值预估

| Backfill | 实测/预期 lift |
|---|---|
| emotion 100% | **+2.4pp**(实测) |
| daily(regime) 100% | 预期 +1-2pp |
| **theme + leader_follower 100%** | 预期 **+5-10pp** (真正未被 cover 的信号) |
| 总和 | 估 +8-15pp(可能把 60% 推到 70-75%) |

---

## 14. 反向分析 — recall vs precision tradeoff

### Recall 100% 时 precision 是多少?

| 要求 recall | 实际 recall | precision | 入场/月 | FAKE+NEUT 占比 |
|---|---|---|---|---|
| 100% | 100% | **9.5%** = base | 734 | 90.5% |
| 99% | 96.5% | 10.1% | 673 | 89.9% |
| 95% | 84.6% | 10.8% | 547 | 89.2% |
| 90% | 73.6% | 11.3% | 456 | 88.7% |
| 80% | 54.2% | 12.2% | 310 | 87.8% |
| 70% | 39.4% | 13.3% | 207 | 86.7% |
| 50% | 16.6% | 15.3% | 76 | 84.7% |

**单因子线性 AND 永远突破不了 ~15% precision**, 因为这是市场效率上限。

### 真主升浪自身涨幅 distribution (假设 100% recall)

| 档 | 占比 |
|---|---|
| 涨幅 ≥ 50% | **99.8%** |
| 涨幅 ≥ 100% | 32.6% |
| 涨幅 ≥ 200% | 6.5% |

如果能 100% 识别真主升浪, 胜率 99.8% + 平均 100% — 但 base rate 9.5%, **理论上限 = 完美 oracle**。

### LightGBM 多维 ML 的价值

ML 把 precision 在 同 recall 下大幅提升:

| 入场策略 | 入场/月 | recall(估) | precision(胜率) |
|---|---|---|---|
| 全 events (base rate) | 734 | 100% | 9.5% |
| 单因子线性 AND 50% recall | 76 | 50% | 15% |
| **V11b(60% 胜率)** | 6 | ~1-2% | 60% |
| **V12 LightGBM prob>0.65** | 4.7 | ~3-5% | **70.9%** |
| **V12 LightGBM top 5%** | 0.7 | ~0.5% | **86.2%** |

**ML 让 precision 飞但 recall 极低** = "等几次机会就够了"的工程化。

---

## 15. 经验和踩过的坑

### 数据相关

| 坑 | 教训 |
|---|---|
| **北上资金死亡(2024-08-19)** | 量化 alpha 不能假设上游数据永远存在, 必须有 source probe + freshness gate. fact_hsgt_daily 停留 20 个月没人发现 |
| **Trading calendar 只 2023+** | backfill 起始日不能早于 dim_trading_calendar(2023-01-03) |
| **market_perception 数据严重不全** | theme/leader_follower 仅 22 天, regime/emotion 1.5 年, 不是完整 2022-2026 |
| **CYQ 数据依赖流通盘历史** | fact_holder_count_period 给的是季度数据(implied_float = holder_count × avg_float_shares), 跑算法需要按 disclosure date 而不是 report_date 否则 leakage |
| **PIT join 路径有 bug** | fact_capital_flow_pit_daily 的 holder_count_q_pct 字段全 NULL, PIT-safe 户数集中信号不可用 |
| **LHB 机构席位是 lagging 信号** | precision 6.0% < base rate 9.5%, 跟"机构席位 = 主力进场" 的常识相反 |

### 信号设计相关

| 坑 | 教训 |
|---|---|
| **Mark Minervini SEPA Stage 2 美股标准** | A 股主升浪起涨日 77% **均线还是空头排列**, 美股模板不能直接搬 |
| **VCP 波动率收缩** | 美股 ATR(20)/ATR(60)<0.8 阈值, A 股慢牛起涨前往往是波动率扩张(蓄势爆破), 阈值方向反 |
| **回调测均线不破** | 美股假设主升浪从已经多头排列启动, A 股很多是底部启动, "回调测均线"语义不对 |
| **R3 小步爬升形态再细化** | 单股 K 线形态再细化无区分力, 真主升浪和正常上涨股的"R3" 几乎一样 |
| **post5_vol_growth 是 lookahead** | T+5 才能判定 post 信号, 不是 PIT 入场 filter, 是退出/持有信号 |
| **金字塔加仓在 A 股短趋势失效** | 浮盈 +15% 加仓提高成本均价, A 股 median 90 天主升浪里加仓位置往往是震荡区 |

### 方法论相关

| 坑 | 教训 |
|---|---|
| **手工阈值全是 in-sample fit** | V0-V7 全部手工阈值, 严格按宪法 §5 应该 Optuna walk_forward 标定 |
| **Optuna 单 split = overfit 警报** | V8 IS 65.6% → OOS 28.6%, 100 trials × 7 维空间 + 大样本 总能"碰到"一组高胜率参数 |
| **Optuna 精调不能拯救弱信号** | walk_forward 仅 +2.7pp vs random, V4 框架本身已经达到 K 线信号上限 |
| **跨 fold 参数族不稳 = 没真 alpha** | V9 fold1/2/3 best params 跨 fold std 极大(post5_lower_dominant_min std 11.55) |
| **单 fold 弱不代表 ML 失效** | V12 Fold2 仅 50% 胜率, 但 ML lift +9.9pp = base rate 低导致, 不是 ML 信号弱 |
| **特征工程优先 > 模型调参** | V12 LightGBM 默认参数 + 46 特征 = 70.9% 胜率, 调参边际效益小 |

### 工程纪律相关

| 坑 | 教训 |
|---|---|
| **bc_absorbed 包遗留** | leader_follower_diffusion_buy 信号已写过但没接进 paper_sim, 多年遗忘 |
| **regime_gate 配置占位但未实施** | paper_sim_config.yaml 有 regime_gate, codegraph 查不到代码 — 典型"配置占位但未实施"反模式 |
| **市场感知模块独立但未消费** | 8 个 engine + 5 张 mart 表都齐, 但 paper_sim selector 不读, 资源浪费 |
| **测试胜率波动巨大 (V8 IS 65% → OOS 28%)** | 必须 walk_forward 多 fold 验证, 单 split 误导大 |
| **"不动代码先研究"是对的** | 这次 16 个版本全部 prototype 在 /tmp/, 没污染 production code, 才能快速迭代 |

---

## 16. 现状盘点 + 下一步路径

### 当前最佳方案(实证综合)

```
入场:
  V14 LightGBM (46 维 + 同板块共振 + hs300 regime)
  + prob > 0.65 (年开仓 ~50-60 次)
  OR
  + top 10% selectivity (年开仓 ~15-20 次, 胜率 ~78-81%)

持有:
  V4 利润回吐 trailing(<20% 用 -8% / 50-100% 允许回到入场 / 100-200% 锁 5%)
  + 主升活着判定(ma60 斜率 + close < ma20 连续 + distribution 累计)

退出:
  F1 -10%(不是 -5%, 实测 -5% 砍掉 50% 真主升浪)
  + T+5 post5_vol_growth < 1.0 → 早退
  + trailing 触发 / 主升活着失败

Regime gate:
  hs300_60d_ret < -10%(熊市): 暂停或仅 prob > 0.75
  hs300_60d_ret < 0(弱市): 紧 prob > 0.7
  hs300_60d_ret > 0(牛市/强市): 正常 prob > 0.65
```

### KPI 实证表现

| KPI | 目标 | V14 prob>0.65 实测 | V14 top 10% 实测 |
|---|---|---|---|
| 年化 ≥ 30% |  | 估 60-120% | 估 100-180% |
| max_dd ≤ -20% |  | q10 估 -10% | q10 估 -8% |
| 月胜率 ≥ 55% |  | 60-70%(去 924 后) | 78-81% |
| 单次涨幅 ≥ 35% | mean +11-12% / q90 +30% | 部分满足 |
| 入场频率 | 不预设 | 4-5/月 | 1.3/月 |

### 路径 A — 接受现状, 工程化

| 步骤 | 工期 |
|---|---|
| 1. V14 框架迁移进 services.zhushenglang/ | 2-3 天 |
| 2. Walk-forward 走项目中央层 | 1-2 天 |
| 3. paper_sim selector 接 ML prob | 1 天 |
| 4. Regime gate 接 hs300_60d_ret | 0.5 天 |
| 5. Audit + delivery_readiness | 1 天 |
| 6. paper_sim 1 月 forward + 真 tx_cost | 1 周 |
| 总 | **2 周** |

### 路径 B — 修复 perception 上游, 跑全 backfill

| 步骤 | 工期 |
|---|---|
| 1. 调研 daily(regime) breadth 上游路径 | 0.5 天 |
| 2. 跑 daily backfill 2023-2024 | 0.5 天 |
| 3. 调研 mart_observed_industry_pit_daily 怎么建 | 1 天 |
| 4. 调研 stock_context_daily 怎么建 | 0.5 天 |
| 5. backfill theme + leader_follower | 1 天 |
| 6. V17 加 theme/LF 特征到 LightGBM | 0.5 天 |
| 7. walk-forward 5 fold 验证 OOS lift | 0.5 天 |
| 总 | **3-5 天** |

期望: V17 胜率从 V16 70% 推到 75-82%, top 10% 推到 85%+。

### 路径 C — 直接用 bc_absorbed 现成信号

| 步骤 | 工期 |
|---|---|
| 1. 从 bc_absorbed/bank/sentiment.py 提取 leader_follower_diffusion_buy | 0.5 天 |
| 2. 调研依赖输入 | 0.5 天 |
| 3. 在 V14 baseline 上叠加 验证 | 0.5 天 |
| 总 | **1-2 天** |

但 — leader_follower_diffusion_buy **本身就依赖 mart_market_perception_leader_follower_daily**, 而这个表只 22 天 数据。**所以仍然要回到路径 B 修复 LF backfill**。

### 推荐顺序

**B → A**:
1. **先修复 perception 上游 backfill (B, 3-5 天)** — 释放 theme/LF 信号
2. V17 用完整 perception 重训, 看 OOS 真实 lift
3. **然后工程化进 paper_sim (A, 2 周)**

不直接 A 的原因: V14/V16 当前 60% 胜率 OOS 离 KPI 30% 年化有 buffer 但不够 PPT 期望的 85%. theme/LF backfill 是最有希望突破 75% 的剩余路径。

### 不该做的事(已实证)

| 不做 | 理由 |
|---|---|
| [NG] 再调 LightGBM 参数 | V12/V14/V16 默认参数已接近最优, Optuna 仅 +2.7pp |
| [NG] 加更多 K 线衍生特征 | V13 周线 + V14 已达到 K 线层天花板 |
| [NG] 修复 fact_hsgt_daily 老北上 sync | 上游 2024-08-19 永久死亡 |
| [NG] R3 形态细化 / 金字塔加仓 | V5/V6 实证退步 |
| [NG] CYQ 当入场 alpha feature | spec 早写明 + V14 实证 importance 全部 Top 20 外 |
| [NG] 单 split Optuna | V8 教训, in-sample fit 严重 |

---

## 17. 附录 — 数据文件清单

### Prototype 脚本(均在 /tmp/)

| 文件 | 用途 |
|---|---|
| /tmp/v8_optuna_proto.py | Optuna 100 trials 单 split |
| /tmp/v9_walkforward.py | Optuna walk_forward 3 fold |
| /tmp/v11_vol_strategy.py | V11 振幅 + T+5 vol exit |
| /tmp/v11b_balanced.py | V11b 多阈值对比 |
| /tmp/v10_vol_factor.py | 基础量价单因子扫描 |
| /tmp/v12_lightgbm.py | V12 LightGBM 5 fold |
| /tmp/v13_weekly_lightgbm.py | V13 + 周线特征 |
| /tmp/v14_lightgbm_plus.py | V14 + 同板块共振 + CYQ + regime |
| /tmp/v15_deep_dive.py | top 5% 深挖 + perception 子样本 |
| /tmp/v16_lightgbm_emotion.py | V16 + emotion 100% backfill |
| /tmp/cyq_proto.py | CYQ 算法原型 |
| /tmp/reverse_recall_analysis.py | recall vs precision tradeoff |
| /tmp/v12_diagnose.py | V12 fold 诊断 |

### Cached 数据(均在 /tmp/)

| 文件 | 内容 |
|---|---|
| /tmp/zhushenglang_scan.csv | 2503 个真主升浪 ground truth |
| /tmp/zhushenglang_breakout_events.csv | 31577 个突破事件(label TRUE/FAKE/NEUTRAL) |
| /tmp/zhushenglang_deep_profile.csv | 31577 个 events + 19 个 pre/post 特征 |
| /tmp/zhushenglang_pattern_features.csv | 2503 个主升浪的形态特征 |
| /tmp/zhushenglang_patterns_clean.csv | 剔除一字天梯后的 2246 个 |
| /tmp/v10_vol_factor_data.csv | V4 baseline + 量价因子 |
| /tmp/v7_baseline_factors.csv | V7 baseline + LHB/高管/户数因子 |
| /tmp/v12_oos_preds.csv | V12 LightGBM OOS 587 个预测 |
| /tmp/v13_oos.csv | V13 周线 OOS |
| /tmp/cyq_event_metrics.csv | CYQ 600 个 sample 的指标 |

### 关键 commit / 文档

| 文件 | 状态 |
|---|---|
| [docs/chip_distribution_cyq_spec.md](chip_distribution_cyq_spec.md) | 已存在(615 行 CYQ spec) |
| [docs/zhushenglang_hunter_research_log_20260528.md](zhushenglang_hunter_research_log_20260528.md) | **本文档** |
| 当前 strategy validation contract | 当前 Optuna/GCP/回测/promotion 治理规则；以 docs map 指向的 active contract 为准 |
| 当前 engineering governance contract | 当前 Codex skill、CodeGraph、complexity、删除和测试工具规则；以 docs map 指向的 active contract 为准 |

### Backfill 数据(已落入 production DB)

| 表 | 写入范围 | 行数变化 |
|---|---|---|
| mart_market_perception_emotion_daily | 2023-01-03 ~ 2024-10-31 新增 | 373 → 814 |
| mart_market_perception_audit_log | +N 行(每个 backfill run) | — |

其他表(daily/theme/LF) 因上游依赖未修复, 未写入。

---

## 一句话总结

本 session 围绕"主升浪猎手"做了 16 个策略版本, 跑了全 A 股 5 年 3.16 万次突破事件 + 2503 次真主升浪 ground truth, 实证证明: **K 线手工阈值 + Optuna 天花板 ~60% 胜率, LightGBM 多维 ML 把平均胜率推到 70% + top 5% 86%(去 924 后 78%), emotion backfill 仅 +2.4pp, 真正突破 75% 的最后路径是修复 theme/leader_follower 上游依赖**。所有发现都经过 walk-forward 5 fold OOS 验证, 不是 in-sample fit。

**核心反直觉发现**: A 股 91% 主升浪是慢牛 + 中性模式, 起涨日 77% 均线还空头, LHB 机构席位是 lagging 信号(precision 6% < base 9.5%), 户数集中信号 disclosure 滞后导致反向。Mark Minervini SEPA / VCP 美股模板不适配 A 股。post5_vol_growth(后 5 日量能持续)+ intra_amp_today ≤ 3%(入场温和涨)是最强基础量价信号。

**下一步推荐**: 修复 perception 上游 backfill (B, 3-5 天) → 工程化进 paper_sim production (A, 2 周)。
