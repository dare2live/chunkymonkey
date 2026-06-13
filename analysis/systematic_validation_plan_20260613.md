# 系统性验证计划 — alpha/特征/live 可信度逐层机器可检 (2026-06-13)

> 状态: live 计划 (用户 2026-06-13 指令 "制定计划放 goal.md 系统性的验证")。
> goal.md 放精简骨架 + 指针, 本文件是完整 owner。证据来源: 3 alpha 判决 (LHB GO / LF REJECT /
> S3 REJECT-泄漏) + workflow 三路审计 (S3 ablation / 验证盘点 / live 爆炸半径) + tushare 数据盘点。

## 核心命题

"项目的 alpha/特征/live 到底可不可信" 必须**从地基往上逐层用机器可检 gate 回答**, 不是一堆零散
实验。地基 = 喂模型/live 的特征面板 PIT 干净度 — **S3 已实证 fact_feature_panel 喂了
follow_net_return 标签泄漏 (AUC 0.78 假象), 而同源 mart_p0a_v4/v5 直喂三条 live 决策链**
(sniper / lambdamart-v6 / v7 forward picks)。任一层 gate 未 PASS 即 block 其上所有层; L0 红时
在线推荐视为受污染。横切铁律: 判据跑前冻结 (谄媚死); 异常高数字一律回 L0 查泄漏不放宽红线;
真相源唯一 = builder `MODEL_INPUT_EXCLUDED_COLS`, 全消费者 import 不手抄。

## 分层 (自底向上, 上层 gate 依赖下层 PASS)

| 层 | 目标 | 机器可检 gate | 不过 block | 工具 |
|---|---|---|---|---|
| **L0 特征面板 PIT** | 喂模型/live 的面板不含任一前瞻列, 排除清单单一真相源不可手抄漂移 | (a) 每个消费者实际 feature_cols ∩ builder MODEL_INPUT_EXCLUDED_COLS == ∅ (set 检非目测); (b) 全列 lineage 无 latest-snapshot/未来 JOIN/fallback>1%; (c) S3 重跑对齐 EXCLUDE 后 AUC 落 shuffle 带 (~0.50) | L2/L3/L4 全部 + "放宽红线"动作 | `leakage_detect` STAGE1/2 + 既有 audit_panel_leakage |
| **L1 标签真实性+时间安全** | 标签 PIT 定义清晰、purge+embargo 足、特征/标签边界 schema 层不可混 | (a) 标签列"t 当时算不出"(正确属性) 且被 L0 排除; (b) 所有评估 embargo>=forward horizon, 时间切; (c) corr(被当特征列, label) 无 >0.2 异常 | L2 | `leakage_detect` STAGE4 |
| **L2 实验复验** | 干净地基上重跑被污染实验 (首推 S3), 冻结判据出诚实 alpha 上限 | (a) 判据=跑前冻结值 (check_prereg 机器校验); (b) 声称 edge 经 ablation 边际贡献可量化非负; (c) 异常高触发即回 L0 | L3 | prereg + ablation + `leakage_detect` STAGE3 |
| **L3 Live OOS 背书** | 每日 live 链 (sniper/v4_bc/v7) 模型在 L0 干净面板训练, selector 只读 oos_* | (a) 三链训练面板全 L0 PASS; (b) selector/scoring 静态扫只读 oos_*, 0 处 in-sample 排序; (c) champion 路径死/活判定 | L4 | 静态扫 + L0 gate |
| **L4 含成本 paper_sim+forward** | 含 tx_cost/T+1 验是否真达 KPI (年化>=30%/max_dd>=-20%/超额>0/月胜率>=55%) | (a) all_kpi_pass=True (当前 2026-05-16 = False); (b) forward 兑现落 paper_sim 区间; (c) 滚动监控+送达告警 | 真金白银上线 | paper_sim_v2 + forward 监控 |

## immediate_next (此刻就能跑, 不依赖重训练)

**L0 第一步 live 泄漏体检**: 对三个 live 消费者提取实际 feature_cols, 与 builder
MODEL_INPUT_EXCLUDED_COLS (经 v4/v5 列名映射) 做 set 相等检查 —
- `run_daily_v7_inference.py:94` 手写 EXCLUDE 读 mart_p0a_v5 喂 live forward picks **不引 builder 契约**
  = S3 同型漏排在 live 链上的活体嫌疑, 最高优先核。
- `build_sniper_score_daily.py:276` 直读 lhb_inst_buy_30d/sector_ret_20d; `run_p0b_lambdamart_v6.py:160`
  `SELECT *` 训练面板。
- 工具: `leakage_probe --stage feature-consumer --panel mart_p0a_feature_label_panel_v5 --label-col <fwd> --label-contract build_feature_panel_duck.py` (单特征 AUC 层兜住手写漏排)。

## 横切研究层 (用户 2026-06-13 问: tushare 对 alpha 增强是否充分调研 — 答: 没有, 立为一层)

**T 层 tushare 域 alpha 贡献系统研究** (与 L2 并行, 不阻塞主链): 22 域逐域 IC/ablation —
每个 tushare 域 (moneyflow/top_inst/dc 系/daily_basic/limit/cyq/report_rc...) 是否携带**增量**
alpha (相对已有特征正交)。现状零散: LHB(top_list)=GO 实证有、LF(dc_member 概念)=REJECT、金股
+1.41%/月, 但无系统性per-域研究。判据冻结 + leakage_detect 事前闸 (防又拿前瞻列论证)。

## 数据地基现状 (绕过失真 watermark 直查真实表, 2026-06-13)

tushare 22 域 ~1.1 亿行基本抓完 (daily/adj_factor 2019+/daily_basic 2020+/dc 系 2025+/
top_list+top_inst 2018+/moneyflow+stk_limit+stock_st 2022+)。**真 gap**: (1) fina_mainbz 只
20251231 单期 (by_ts_code 回填只拿最新期, 历史缺); (2) watermark 记"最后操作"非"表真值"
(daily_basic/top_inst/dc_member 显示失真) = 监控 bug 待修; (3) cyq_perf 在库但 C0 判口径疑未复权
(筹码轴冻结)。这些进 L0/L1 数据层核查, 不阻塞 L0 live 泄漏体检。

## 死亡条款

- 感知死: L0 live 泄漏体检 7 天内不跑 = 计划空转 (live 可能正带泄漏决策)。
- 判断死: 任一层 gate 改判据于看到结果后 = 谄媚死作废。
- 横切: 异常高数字 (AUC>0.75/RankIC>0.3/相对+50%) 回 L0 不放宽红线 (S3 教训)。
