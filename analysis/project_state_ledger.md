# ChunkyMonkey Project State Ledger

> 状态: live — 滚动追加的历史账本 (例外: analysis 默认冻结, 本文件持续追加)

> 滚动规则 (2026-06-11): 本文件只保留当月+上月条目; 月初把更早条目剪切到
> `analysis/ledger_archive_YYYYMM.md`。手动执行, 不写自动工具。

> This ledger preserves completed work, historical status, and evidence notes
> moved out of `goal.md` on 2026-06-05. It is context and evidence, not the
> current operating plan.
>
> Usage: do not read this file start-to-finish during startup. Use `goal.md`
> for the current phase contract, `SESSION_HANDOFF.md` for a generated runtime
> snapshot, and `rg` / `tail` on this ledger only when a task needs specific
> historical evidence.

## 2026-06-13 夜 — CYQ 筹码买卖点深挖 (用户: 深度挖掘筹码分布/胜率 + 买卖点应用)

- **四角度深挖** (winner_rate 买卖向 / 筹码形态 / C0 除权鲁棒 / 买卖点规则事件研究), 全程 t-1 PIT +
  泄漏 gate + 剔除除权窗 + 异常核查不排除:
- **最强信号 = px_pctile** (价在筹码成本分布的相对分位 = (raw_close-cost_5)/(cost_95-cost_5)):
  RankIC -0.042 / **IR -0.39** (个位特征里偏强, shuffle=0 无 leakage), 效应高度集中 TOP decile
  (D9 价站上几乎全部历史持仓成本=获利盘极厚无套牢支撑 fwd20 -0.47% vs rest +1.20%) = **清晰的高位
  退出/止盈卖点信号**, 且 past20 双重排序证**独立于动量** (增量 alpha)。这是用户"辅助判断卖出点位"的答案。
- **诚实证伪 (不夸大)**: 事件研究 SELL 规则 (winner_rate 250日分位>0.9 退出) 净 alpha 仅 18-23bp,
  **扣 T+1 双边成本 ~30-40bp 后归零** → CYQ 不构成扣成本后可独立投入的卖点。BUY 假设**被证伪**
  (低胜率+站上均成本进场 4 年净 -0.76pp, t=-14; "站上均成本"本身是均值回归卖向非买向)。winner_rate
  弱负 (-0.022) 且 2026 翻正跨 regime 不稳。**用户先验"筹码集中+价上方=强势"被数据否定** (该象限最差;
  "发散+价在成本下方"+1.44% 最优)。
- **定位 (诚实)**: CYQ 是 **ensemble 弱辅助/风险过滤特征 (高分位降权) + 退出组件辅助闸**, 非独立买卖点触发。
- **C0 三级协议** (修正 audit 的 8.11pp 框架): 级别1 比率型 (winner_rate/分位) 直接可用 (除权日 |delta|
  6.92 vs 非除权 6.65pp, Mann-Whitney p=0.66 不显著, audit 8.11pp 是 38 股小样本巧合); 级别2 price-vs-cost
  跨坐标型剔除除权窗±3 (窗内 IC -0.134 vs 窗外 -0.040 = 3.3x 伪放大, 剔窗仅去 2.28% 样本) + 配 raw 未复权
  close; 级别3 绝对价位精配才需 modal qfq 重算。**modal 当前不触发** (本轮无级别3 特征)。
- **next**: px_pctile 作 **LHB GO 退出组件的卖点闸**验证 (CYQ TIMING 信号 × LHB GO 入场后持仓退出天然契合,
  把 CYQ 从"证伪的独立信号"转为"退出辅助闸"的唯一有价值落地路径); 带 T+1 成本验证未过不进生产。

## 2026-06-13 晚 — 地基 index 落库 + tushare raw 域挖掘 (用户: 打地基 + 同步挖掘)

- **地基 index 族落库** (KPI 超额 HS300 真相源缺口 D3): registry 加 index_daily_benchmark (7 基准指数全史,
  by_code_list 新 batch_mode) + index_member_all (31 申万一级成分史)。实测 raw_tushare_index_daily 35,128 行
  (HS300 000300.SH 5,206 行全史 2005-2026 = 终结 akshare 主从倒挂) + index_member_all 5,847 行 (in_date/out_date
  原生 PIT = 申万退役真因"只有快照无历史"的正解)。落库前单发实测救坑: 无参全拉整 5000 截断 (dc_member 同型) → l1 循环。
- **tushare raw 域增量 alpha 挖掘** (3 域并行, t-1 PIT, 过泄漏 gate, 异常核查不排除):
  - **daily_basic = 唯一真增量**: turnover_rate (RankIC -0.089 / IC_IR **-0.497** / t-16) + circ_mv 连续 size
    (-0.049/-0.264) + pb (-0.069), **均不在 v5 130-col panel (0 hits 实证), PIT 干净 (same-day -0.094 vs t-1
    -0.089 证 shift 生效), 与反转正交 (spearman +0.22<0.3)** = 真新维度非换皮。turnover_rate IC_IR -0.5 稳定性
    超现面板多数特征 = 本轮最有价值发现。
  - moneyflow / top_inst = **无增量** (net_mf 0.0267 / 机构席位质量四维 ~0, 现有 flow rank / LHB 计数反转换皮)。
  - verify-the-verifier: circ_mv 当年被 v5 DROP 的 caveat 查清 = 派生表 fact_market_cap_decile_daily 的
    Pattern 10 NULL 时间渐变 leak (pre-2025 98.3% NULL), 非 size 因子本身; raw daily_basic circ_mv 全年 0% NULL
    绕开。异常核查抓出 seat_diff_all (-0.0501 但 99.3% 事件值=0 仅 253 天) = 低覆盖伪信号非 alpha; inst_buy_share
    split-half 非平稳衰减 (0.162→0.046) 标注。
- **诚实结论 (回答"tushare 对 alpha 增强")**: raw tushare 仅 daily_basic 风格因子 (换手/size/价值) 有增量;
  flow/LHB 域已饱和。next = turnover_rate+circ_mv 按 t-1 PIT 进面板 → 控现有 62/130 列偏 IC ablation (验增量非
  共线) → optuna 组合 (走 plan_validator 防空 search space); 若偏 IC 仍弱则现有 raw 域见底, 转 CYQ (modal)/链谱新维度。

## 2026-06-13 晚 — 干净特征 forward-IC 探索: 有诚实弱 alpha, 0 泄漏 (充分利用 duckdb)

- **四路 duckdb 探索 (原始IC/horizon/regime/正交, 全过泄漏 gate, 产物 analysis/c1_rankic_*_20260613.*)**:
  62 干净特征 (builder 标签契约排除后) 对 forward_ret_20d 跑 805 日 walk-forward 截面 RankIC。
- **0 泄漏嫌疑**: 全场 |RankIC| 上限 0.107 (<0.15 §4.2 红线), IC_IR 上限 0.80 — 印证 label-excluded
  PIT 保证有效, 反证过去 +312%/RankIC 0.035 那些惊喜确是泄漏。
- **诚实弱 alpha (有, 但需组合)**: 3 簇正交符号稳定信号 — (1) **rz_balance_to_amount20** (融资余额/20d
  成交额, 杠杆情绪) IC_IR 0.80 唯一强正向, 随 horizon 单调增强 0.073@5d→0.138@90d (IC_IR 1.37@90d),
  全 regime 稳定, 但覆盖仅 56.6% (两融标的); (2) **lhb_inst_buy_count_30/60d** 反转/拥挤 IC_IR -0.84
  高度正交; (3) 中短期反转簇 (ret_20/60d/ma_ratio_*, 共线留 1-2); (4) 波动簇 regime-依赖 (须 gate)。
- **判决**: 无强单因子, 但是合法多因子组合底座 (低频 60-90d 持有, 温和超额); 单独撑不起 KPI 年化>=30%,
  冲 KPI 需 (a) 三族组合 optuna OOS 确认 (本地, ~8 因子线性权重 + walk-forward, 不需 modal) (b) CYQ 筹码/
  资金流类新特征补增量 (alpha_combo_matrix run_first combo 方向)。干净量价这口井诚实见底。
- **anomaly 协议跟进**: rz_balance IC_IR 1.37@90d 是全场最高, 虽 <0.15 红线且 horizon-单调+regime-稳定
  (慢因子特征非泄漏特征), 仍按"异常核查不排除"做 PIT 溯源确认 (融资余额是否 T+1 PIT 干净) 再入组合。
- **modal 未触发**: 全程本地 duckdb 秒-分钟级算完; modal 只在 CYQ 全市场筹码特征扩展时才上。

- **彻查 (用户指令: 排除泄露但不放过真实增强; workflow ablation + 主会话亲自复算双证)**: S3 首轮
  AUC 0.779 = **确凿特征级前瞻泄漏, 非真 edge**。罪魁 = 5 个 `follow_net_return_5/10/20/60/90d`
  标签列被当特征 (贡献 top15 gain 51.8%, follow_net_return_90d 单列 29%); 这些列 builder
  build_feature_panel_duck.py 已标 `PIT_LABEL_COLS`/`MODEL_INPUT_EXCLUDED_COLS` (LEAD(exit_price)
  前瞻算的), **是我 S3 脚本手写 EXCLUDE_COLS 漏了它们绕过了 builder 标签契约**。
- **corrected 复算 (修订1: EXCLUDE_COLS 改复用 builder MODEL_INPUT_EXCLUDED_COLS 单一真相源)**:
  62 特征, mean AUC **0.5013** / top-decile precision **0.94x base** (低于 base!) / 三折 0.48-0.55-0.47
  = 纯随机; shuffle 0.51 干净。**verdict=REJECT (无真 edge, 干净)**。
- **双向结论**: (1) 泄漏 100% 坐实并已修 (排除标签族 + 防回退测试断言涵盖 builder 契约);
  (2) **没有放过真实增强** — 去泄漏后现有 fact_feature_panel 因果特征对主升浪预测 0 edge。研究日志
  V12/V16 的 70.9%/86% 极可能是同款 follow/forward 衍生特征泄漏 (北极星头条数字存疑)。
- **系统性教训 (用户提: 做专用事前泄漏工具)**: builder 有标签契约 + audit_panel_leakage.py 查面板
  构建 SQL, 但都**抓不到消费方把标签当特征**这层 (S3 盲区)。→ 需消费方/训练前 leakage_probe:
  (a) 标签契约静态校验 (feature ∩ MODEL_INPUT_EXCLUDED_COLS/forward 名模式) (b) 单特征 OOS AUC
  探针 (>0.7 = 泄漏嫌疑, follow_net_return_90d 单列 0.84 会被抓)。作 L0 事前闸, 见 goal.md 验证计划。

## 2026-06-13 午后 — 主升浪 S1→S2→S3: ground truth 落库 + ML 假设重验 = REJECT (异常高泄漏警报)

- **S1 落库**: `fact_rally_ground_truth` (31,531 突破事件 + 连续结局 + is_true_rally 读法B 3,247 TRUE);
  S1 复现已对账锚 99.92% (见早条)。产物落库防 /tmp 灭失。
- **S2 复用 (奥卡姆零特征重造)**: 事件 JOIN 现成 `fact_feature_panel` (67 PIT 特征), 2023+ 段 100%
  命中 (26,797 事件 / 2,784 TRUE); 2022 段无特征 (面板起 2023-01) 排除 → 训练窗 2023-2026。
- **S3 判决 (prereg FROZEN, `analysis/prereg_zhushenglang_s3_20260613.md`, `experiment_zhushenglang_s3.py`)**:
  LightGBM walk-forward, **embargo>=180 交易日** (label horizon, 防重叠标签泄漏 — 经典陷阱, 原研究
  很可能未做), 3 折 expanding。结果: **J1 信号 PASS 且强** (top-decile precision 3.18x base, mean
  AUC 0.779, 3/3 折正); **J3 PASS** (3/3 折); **J2 非泄漏 FAIL** — mean AUC 0.779 > 预注册 0.75
  "异常高"红线 (§4.2)。标签置换对照 0.530 (落 [0.45,0.55] 带) = **管道层无泄漏**。**verdict=REJECT**。
- **纪律解读 (不挪门柱)**: REJECT 由冻结的 leakage-ceiling 规则触发, 非"无信号"。信号真实 (shuffle 干净
  + 3.18x precision) 但 AUC 超 §4.2 怀疑线。置换对照只抓管道级泄漏, **抓不到特征级前瞻污染** (如 LF V0
  latest-snapshot bug 型)。两可能须 ablation 区分: (a) 真动量 edge (突破股 ret_20d/60d 合法预测续涨,
  则我 0.75 红线太保守 — **但禁止事后抬 (谄媚死)**); (b) fact_feature_panel 某特征前瞻泄漏虚抬 AUC。
- **判负处置 (预注册 + §4.2 相对红线)**: 主升浪 ML 线**不接 live, 不在现有特征上调参续命**; 下一步 =
  特征族 ablation (定位 AUC 驱动) + 驱动特征 PIT 审计 → 若证实泄漏, 修 fact_feature_panel; 若证实真
  edge, 用**新冻结的、properly-anchored 红线**重跑 (不复用本轮被看过的判据)。fold-2 (最新) label 因
  数据截止 2026-05-28 前瞻窗截断而 AUC 0.68 偏低 (披露)。
- **验证器性能**: S3 27k×67 LightGBM walk-forward + 置换对照本地 ~分钟 (modal 非必需, 留 CYQ 特征扩展)。

## 2026-06-13 午 — 第二个 alpha 判决: LF V0 概念龙头-跟随者 = REJECT (假设证伪)

- **判决 (B组C3, prereg FROZEN + 修订1/2/3, 跑于 14:07-14:15)**: `analysis/lf_v0_verdict_20260613.json`。
  n_events=17,151 / n_follower=3,496,309 (2025-2026)。J1 净超额 **-0.025pp** (bootstrap CI95
  [-0.057, 0.008], 阈值 +0.55) FAIL; J2 三期 -0.19/+0.07/+0.015 (2/3 正, 需 3/3) FAIL;
  J3 17,151≥30 PASS; PIT 双锚 net_t1=-0.026≈net (成员锚定不改变零结果 = 无不稳定伪影);
  极端热日 -0.155 / 平日 -0.004 全≈0。**verdict=REJECT**: 概念内涨停龙头出现后 follower 相对
  同日同涨幅带非成员股**零超额** — "theme/LF +5-10pp" (hologram A.3) 假设证伪。
- verify-the-verifier: 无 leakage 警报 (负结果非可疑正结果); 各臂/分期/冷热日全收敛 0; 3.5M 样本
  CI 自然极窄; 两指针对照选择经 1000-case 差分测试与朴素逐字等价 (非选择偏差致负)。
- **prereg 判负处置 (预注册, 防留恋)**: theme/LF 轴封档; bank/sentiment.py 列退役清单 (待主会话
  审后收编, 不本轮删); 套三复审门③ 记 FAIL 输入; 产能转 D 排序层 W2 + LHB 线; 搭车臂 (纯度/BORN
  副表) 一并归档不单独立项。
- **性能根治 (验证器 intractable)**: 朴素版 ~50k 事件 × ~89 follower × 5512 全扫排序 × 2 臂 →
  36min CPU 不出 (睡眠期误判断电); 向量化五招 (members 预载 dict 并集 / fwd5 memo / bootstrap
  预算累加 / 对照两指针带外扩替全扫排序 / detect 两锚共享) → 8 分钟出判决; 两指针 tie-break
  经 1000-case 差分测试守门 (= 朴素逐字等价, 防"测试绿判决偏")。

## 2026-06-13 晨 — 第一个 alpha 判决: LHB 上榜即退出 = GO (三判官全过)

- **判决 (C组C1, prereg FROZEN 2026-06-12 + 修订1, 跑于 06:48-06:52)**: `analysis/lhb_exit_verdict_20260612.json`。
  n=74,111 事件 (2020-01..2026-05, 含牛熊震荡全周期); J1 净效应 **+2.428pp/20日** (bootstrap CI95
  [2.239, 2.61], 阈值 1.0, seed=20260612 n=10000) PASS; J2 **7/7 年同号为正** (2020:+2.91 / 2021:+1.68 /
  2022:+3.68 / 2023:+2.50 / 2024:+2.48 / 2025:+2.18 / 2026YTD:+0.39, 需>=5/7) PASS; J3 事件组 +2.132 vs
  混淆臂对照 **-0.296** — 同日同涨幅带同市值五分位的非上榜股 20 日窗持有反而略优于退出, 而上榜股退出
  显著占优 = LHB 信息强于纯均值回归, 判官设计意图实质满足 PASS。**verdict=GO**。
- 排除披露: window_tail 9,976 / no_controls 14,601 / null_float 仅 2 (修订1 担心的 4-8% null 实为
  top_list 自带 float_values 口径, 几乎无损) / no_price_or_quintile 14。保守性: 退市股 t+21 无价被排除
  → 排除的恰是退出收益最大的灾难案例, 偏差方向对判正不利, 结论稳健。
- 前置数据面: chain9b step1 daily_basic 2020-2022 回填 689 批 2,984,163 行 0 失败 (06:22-06:48, 26 分钟,
  晨间网关高吞吐), g5 独立复核 728/728 整; sherpa gates lhb_exit 7/7 GO 后实验才开跑 (硬门履约)。
- 处置 (prereg 既定): 判正 → 是否进 B 主书退出组件 ablation **待用户拍板**; top_inst 机构席位臂为可选
  副表后补 (8 缺日 vendor 顽固空响应, 不阻塞)。C组C2 冷却期二审解锁 (串行 gate 条款: C1 非零信息成立)。
- 执行自省 (如实): 总指挥在监控中犯时间锚定错误 — 凭臆想时钟把 top_inst drain "重试 2 分钟" 误判为
  "卡死 2 小时" 并过早 kill (06:57)。代价可控 (可选副表 + drain 幂等, 链尾后重发即补), 教训 = 任何
  时长判断必须先 `date` 锚定真实时钟, 不许凭会话体感 (与 UTC 误读虚构事故同族, §8.25 实测类错误)。

## 2026-06-12 晚 — 调度手动化 + 日历扩展 + chain9 发射 (持续推进指令下的当晚执行账)

- 调度新政落地: daily-update/concept-snapshot launchd bootout (plist 归档), ops_manual_run router + 前端按钮 (7 单测+实弹); E7 概念快照退役 (用户决议概念域单源化 = 东财 dc 系唯一, THS 出局; dc 系历史 tushare 可回拉, 快照属冗余中间层)。
- 手动 daily_update 实弹 (18:38-20:54, wrapper OK): PK 修复后全链零 Binder degraded — 二轮整改实弹验证成立。Step 2.95 drain 网关晚高峰 ~3 调用/分钟僵慢 (115 分钟 vs 晨跑 15 分钟基线, CPU 2:56/115min + 连接轮换证实非挂死), 定点终止按设计降级; 误配失败单重试风暴 = min_rows 修复的实证现场。
- 链后核验: K 线 06-12 全市场 5200 行已落 (watermark 元数据滞后 1 天 bug 坐实, 修复排 P1); xdxr Step 2b2 首实弹失败定根因 = xdxr 客户端遍历死 TDX 服务器池, 未接 CM_TDX_SERVERS 活池 override (K 线路径接了所以 4 分钟跑完) — 修复排 P1, 热备源可容忍; 6 域 watermark 转正未发生 (drain 被磨死在前段), 移交 chain9 step5 drain。
- 日历扩展执行: dim_trading_calendar 969→5,343 行 (2005-01-04..2022-12-30 段 4,374 行), 七项验证全 PASS (evidence=analysis/calendar_extension_20260612.py); claims 弹仓 calendar-floor 收紧 == 2005-01-04, realdb 断言收紧 min<=2005-01-04+count>=5000 防回退。
- failure_queue 僵尸单清理 8→6 open (moneyflow_hsgt 20230210 已在表实证 + diag_test_14 案撤, resolve 带对账理由)。
- chain9 发射 (20:58, nohup): 自检 PASS (日历/min_rows/page_limit/写锁) → 探底实弹: top_list 2018/2019/2020 = 44/67/52 行, top_inst 487/786 行 (LHB 回填数据可得性确认); **dc_member 地板夹层定案 = 2024-12-16 零行 / 2024-12-31 有数据** (10 发双确认探针), registry 20250102 最多丢 3-5 交易日维持不改 (E8 基本正确) — 套三门① (>=2022-01) dc 系永不满足, 走养数据或 kpl 探史。链序: LHB 四件套 → 2022 段补窗 → dc_member 分页重拉 → 全域 drain (增量+转正) → 7 域补丁 → fina_mainbz → 链尾 data-status+sherpa gates+moth assert 自验闭环。
- Opus 工具线: moth assertion 引擎+assert 子命令 (dare2live/moth 已推); sherpa v0.1+init (dare2live/sherpa 新私有仓); chunkymonkey 弹仓 claims 8 断言 + gates lhb_exit/lf_v0 两包。

## 2026-06-12 — 接管对账 + 拆分回归二轮整改 (断线 session 善后)

- 背景: 10:04 收尾 commit 后 12:09-14:24 间一个 session 断线, 留 4 孤儿文件; 用户 14:33 拉起新 session 全面接管。六线 workflow (A 存储/B 数据/C 实验/D modal/E 平台/F 考古, 13 agents 对抗核验) + 完备性审查定位全部残留。
- 后台链下落定案: chain4-8 nohup 链没死, 07:02-12:09 自然跑完退出 (本机 0 残活进程, /tmp/w1_chain7.log + w1_chain8.log 物证); 但 chain7 带三处伤: (1) step1 末 6 域 drain 撞拆库丢 PK 回归 Binder 连环失败 (2) step2 top_inst 548,858 行落库未转正 ok:false (3) step3 fina_mainbz 秒崩 (sync_runner by_ts_code 路径 get_conn 不带 market attach, 确定性代码 bug 非环境问题)。
- 时区乌龙又一例: watermark"停在 06-11 23:17"系 UTC 误读, 实为 06-12 07:17 CST chain7 成功记录 (source_watermarks.py 头注明示 UTC 存储)。
- 拆分回归二轮整改: 首轮 (13:52 断线 session) 只补 4 表 PK; 本轮全仓静态扫描证伪"写入面仅 4 表" (实测 165 张无 PK upsert 目标), 按恒等式"凡 upsert 目标必有 PK"以旧库 DDL 逐表事务重建 164 张 (79 张 7.2s + 85 张 100.6s, 0 失败; RENAME 前须先 DROP 表上索引否则 Dependency Error), 终态约束表 5→169 / 索引 348 不变 / 343 表 0 残留 / 冒烟 6/6 PASS / 24G。残余 1 张 fact_fundamental_quarterly = 旧库本就无 PK 的既有缺陷, 不混入本回归。
- chain7 静默 clamp 定案 (critical): registry data_start=20050104 被 dim_trading_calendar 起点 2023-01-03 静默截断, top_list/top_inst/dividend/adj_factor/cyq_perf 的 2005-2022 (cyq 2018-2022) 全军未落零告警; LHB 退出实验 gate (top_list min<=20200101) 因此阻塞。窗口决策 (扩日历 vs 改 data_start) 待用户。
- C0 判决执行: verdict=FAIL 入档 (db3feffa), 筹码轴 5 combo 按预注册条款冻结, 口径结论写回 sync_registry cyq_perf pit_anchor (措辞"疑似未复权": J3 双口径比对实现缺陷 + exdiv median 8.11pp 未过自设 10pp 线, 确证留待非判决性 probe); 产能转 LF V0 + LHB 退出。
- 文档对齐: goal.md 同步矩阵 5 轨道刷新; implementation_plan.md modal stale 行改 active 实况; CLAUDE.md §4.5 新增 COPY FROM DATABASE 与日历 clamp 两反例。
- 遗留 (优先级序): (1) 17:00 daily_update 拆库后首跑观察 (xdxr Step 2b2 首实弹 + kline watermark 滞后 bug 验证) (2) fina_mainbz attach bug 修复 (3) top_inst 5 终败日重试 + 0610/0611 补 (4) suspend_d/dc_index 静默短路根因 (5) 窗口决策 (6) modal smoke 同路径覆写隐患 (7) moneyflow_ind_dc min_rows 误配核证 + failure_queue 僵尸单清理 (8) LF V0 事件面板增厚。

## 2026-06-11 — 文档治理执行 (盘点 agent 清单 E1-E7)

- docs gate FAIL→PASS: implementation_plan_20260611 并入 docs/implementation_plan.md (Active Repair Plan 节) 后归档至 analysis/, docs_count 11→10, unresolved_live_refs 1→0。
- CLAUDE.md 615→210 行: 通用规则外移 (Optuna 治理→strategy_validation_contract.md 新节; 编码/并发/双扫→AGENTS.md+engineering_governance 指针; §9 GCP 整节失效内容删除→experiment_jobs 契约指针); §4.5 反例表 + §8 self-check 原样保留并新增 cron 静默失败反例。
- 本文件 1014→580 行: 2026-05-27 旧执行计划 (442 行) 归档至 ledger_archive_202605.md; 头部加滚动规则 (当月+上月, 月初手动剪切)。
- PROJECT_INDEX.md 5933→753 行 (87% 是 changelog): 顶部增量区+§14 246 条归档至 project_index_changelog_archive_20260611.md; check_project_index_sync.py + safe_commit.sh 指引文案改为'活索引节+历史进 ledger', 拔掉机械再生根因。
- goal.md: 新增 North-Star KPI 表 (四大目标唯一 owner, 当前实测=unknown 如实声明); commit ownership 双轨裁决; workflow_checkpoint 契约层退役 (物理删除=P2)。
- 状态面收敛: session_snapshot.json / workflow_checkpoint.json untrack (生成物不进 git); SESSION_HANDOFF untrack 经评估撤回 (audit source 依赖, 记 P2); 0 引用孤儿 3 件删除 (next_session_prompt_20260527 / formula_combo_search_95pct.json / multi_wave_strategy_design_300616 英文版)。
- session_snapshot.sh resume 节收口为 quickstart 指针 + ALERT flag 检查指引。

## 2026-06-06 — iFinD MCP data-source routing recheck

- A read-only sidecar rechecked the local iFinD MCP mirror under
  `/Users/dp/Documents/M/stock/iFind` and the current ChunkyMonkey data-source
  routing docs. The controller accepted the routing evidence but not a
  production promotion.
- Current decision remains: iFinD MCP is eligible only as a research/probe
  source for sector/theme daily PIT snapshots, industry-chain analyst-assist,
  news/notice evidence snippets, company profile enrichment, and curated EDB
  industry-cycle candidates.
- iFinD MCP must not become a global fallback or replace K-line, tick/order-book,
  high-frequency realtime quotes, F10/holder backbone, historical concept
  membership backfill, or `need_027` exact order-flow. Structured forecast/report
  rows remain unproven unless a separate field/date/PIT/permission probe proves
  them.
- Durable owner: keep the current route in `goal.md` and the completed evidence
  in `analysis/data_source_selection_20260605.md`; future production-shaped
  probes should record query template hash, result limit, source date, quota
  cost, de-dup key, empty behavior, and PIT counterexamples before any writer or
  source catalog promotion.

## 2026-06-06 — Stage-opt reversal freshness/window boundary

- `audit_stage_opt_candidate_supply.py` now reports source freshness and
  audit-window feasibility, including per-source `max_signal_date`,
  `signal_date_count`, formula-level max signal dates, K-line dates after source
  max, and warnings such as `source_max_date_before_kline_max` /
  `source_window_signal_dates_below_min_signals`.
- This closes a diagnostic ambiguity from the short live reversal audit. Local
  read-only verification showed `dim_trading_calendar` reached `2026-06-05`,
  `v_price_kline_qfq` reached `2026-06-04`, but `fact_technical_trigger` and
  `reversal_1m_mild` / `reversal_1m_deep` / `reversal_1w` reached only
  `2026-06-02`.
- Short live reversal audit `2026-06-01..2026-06-05` now recommends
  `candidate_supply_freshness` before formula/source redesign. It reports
  `fact_technical_trigger` formula max dates of `2026-06-02`, K-line dates after
  source max `2026-06-03` and `2026-06-04`, and `signal_date_count=2 <
  min_signals=5`.
- Longer 2026 YTD reversal-only audit proves this is not simply "reversal can
  never meet readiness": `raw_signal_rows=306247`, `unique_keys=33946`,
  `ready_keys=21117`, `ready_coverage_pct=62.21`,
  `signal_kline_coverage_pct=100.0`.
- Controller decision: do not tune reversal thresholds again and do not add a
  reversal state/history table yet. First refresh/rebuild `fact_technical_trigger`
  to the latest trusted K-line date, rerun short + YTD audits, then consider a
  no-persist state/source POC only if freshness-aligned evidence still shows a
  material density blocker.
- Verification: scoped `audit_test_tool_health.py` PASS; targeted
  `backend/tests/scripts/test_audit_stage_opt_candidate_supply.py` passed
  (`22 passed`); related service/script/CLI tests passed (`67 passed`);
  `scripts/chunkyctl audit --run ...` passed, including CodeGraph sync,
  complexity checks, universe filter, compile, Moth snapshot, and pytest. Short
  live reversal audit still recommends `candidate_supply_freshness`. See
  `analysis/stage_opt_reversal_supply_boundary_20260606.md` for the compact
  decision record.

## 2026-06-06 — Stage-opt signal-date K-line coverage evidence

- `audit_stage_opt_candidate_supply.py` now joins `v_price_kline_qfq` at
  `(stock_code, date)` grain for both `fact_technical_trigger` and
  `mart_macd_state_history` rows, then carries `has_kline_bar` into each signal
  row. Candidate readiness remains at
  `(stock_code, formula_id, formula_variant, stage_bin)` key grain, but a key is
  ready only when `kline_signal_rows >= readiness.min_signals_per_key`.
- Partial signal-date K-line gaps are now visible instead of being hidden by
  stock-level coverage. A key with some matched bars but fewer matched signal
  rows than `min_signals` is blocked by `below_min_signals` and, when relevant,
  `missing_signal_kline_bars`; keys with zero matched bars remain
  `no_kline_bars`.
- Recommendation logic now compares pure upstream supply shortfall against
  signal-date K-line blockers. K-line blockers win ties because K-line is the
  lower truth source; this prevents a 5 raw signal / 4 matched K-line case from
  being mislabeled as ordinary upstream formula scarcity.
- Reports now expose `signal_rows_with_bars`, `signal_rows_without_bars`, and
  `signal_kline_coverage_pct` at top level, in the attrition funnel, markdown
  output, `min_signals_sensitivity`, and the `chunkyctl doctor` stage-opt
  summary.
- Verification: scoped test-tool audit
  `stage_opt_candidate_supply_contract_tests` passed with `fail=0`, `warn=0`;
  targeted pytest for stage-opt audit plus `chunkyctl` passed (`56 passed`);
  `py_compile` passed; short live audit `2026-06-01..2026-06-05` returned
  `WARN`, `signal_rows_with_bars=16558`, `signal_rows_without_bars=0`,
  `signal_kline_coverage_pct=100.0`, and
  `blocked_reason_counts={"below_min_signals": 12155}`; full
  `scripts/chunkyctl doctor --fast` still returned `WARN`, with full-history
  `signal_rows_with_bars=7918485`, `signal_rows_without_bars=0`,
  `signal_kline_coverage_pct=100.0`, `below_min_signals=90331`, and
  `stage_focus=upstream_candidate_supply`. CodeGraph synced and up to date;
  Moth showed no new complexity findings (`new_high_count=0`); `git diff
  --check` passed.
- This closes the previous "per-key K-line coverage evidence" gap in the
  stage-opt gate. It does not close the P1 stage-opt blocker: the current live
  blocker is still upstream formula coverage / signal density.

## 2026-06-06 — `need_027` exact-flow probe diagnostics hardening

- `backend/scripts/probe_source_capability.py` now classifies `need_027`
  exact-flow source-probe blockers as controller-readable causes instead of
  collapsing them into `probe_blocked`. Current classifications include
  `tushare_token_missing`, `akshare_remote_disconnected`,
  `missing_exact_flow_columns`, `missing_date_range`,
  `row_count_below_minimum`, and `not_exact_flow_capability`.
- The gate report now includes per-case `controller_blockers` / `next_action`,
  per-source-group blocker and next-action summaries, and a top-level
  `post_probe_gates` object for `field_mapping`, `date_coverage`, `pit_key`,
  `freshness_sla`, `writer`, `watermark`, and
  `failure_queue_resolution`. A source-probe PASS can mark field/date checks
  as pass for the selected source group, but production remains blocked until
  PIT/freshness/writer/watermark/failure-queue gates are separately proven.
- `backend/config/test_tool_registry.yaml` now registers
  `need027_source_probe_gate_contract_tests` as explicit acceptance evidence
  for the gate contract, rather than relying only on the broad
  `backend/tests/scripts` bucket.
- Verification: scoped `audit_test_tool_health.py` for the probe script, probe
  tests, registry, TuShare tests, and need-coverage audit returned `PASS` with
  `registry_coverage_pct=100`; `py_compile` passed; targeted pytest passed
  (`53 passed`); live no-persist gate summary returned `BLOCKED` with
  `6` exact-flow probes, `0` valid, AkShare
  `akshare_remote_disconnected=3`, TuShare `tushare_token_missing=3`, and all
  `post_probe_gates` `not_checked`; CodeGraph synced and is up to date;
  complexity scan still reports only the existing 80 high findings.
- This does not close `need_027`. It makes the blocker actionable: restore
  AkShare source stability or provide a TuShare token, rerun the no-persist
  gate, then continue only through PIT/freshness/writer/watermark/failure-queue
  evidence.

## 2026-06-06 — Stage-opt candidate supply readiness contract hardening

- `backend/config/stage_opt_candidate_supply.yaml` now owns
  `readiness.min_signals_per_key=5`; `audit_stage_opt_candidate_supply.py`
  uses that config value as the default and keeps `--min-signals` as an
  explicit override only. This preserves the current threshold while moving the
  business readiness rule out of argparse code.
- `backend/services/stage_opt_candidate_supply.py` validates readiness as a
  positive integer and includes it in `candidate_supply_contract` reports, so
  downstream `chunkyctl`/doctor consumers can see the config-owned threshold.
- `audit_stage_opt_candidate_supply.py` now reports configured diagnostic
  source-load failures via `source_load_errors` instead of silently treating a
  missing or broken `mart_macd_state_history` read as zero rows. Source-load
  failures make the audit `WARN` even when candidate keys otherwise look ready.
- The same contract now declares source `required_columns` and join
  `source_columns` / `target_columns` / `required_columns`. The audit checks
  those table schemas before row loading and emits `source_schema_checks` /
  `source_schema_errors`; schema failures short-circuit to structural `WARN`
  with `candidate_supply_source_schema`, so missing source or join columns
  cannot be mistaken for ordinary candidate scarcity.
- `backend/scripts/chunkyctl.py` now preserves `source_load_errors` and
  `source_schema_errors` plus `summary.source_load_error_count` and
  `summary.source_schema_error_count` in the stage-opt doctor summary, so the
  controller view cannot hide diagnostic-source or schema failures behind
  `below_min_signals`.
- Verification: `py_compile` passed; scoped test-tool audit
  `stage_opt_candidate_supply_contract_tests` passed with `fail=0`, `warn=0`;
  targeted tests passed (`59 passed`); short live read-only audit for
  `2026-06-01..2026-06-05` returned `WARN`, `min_signals=5`,
  `source_schema_error_count=0`, `source_load_error_count=0`,
  `unique_keys=12155`, `ready_keys=0`, and
  `blocked_reason_counts={"below_min_signals": 12155}`. CodeGraph is synced
  and up to date; complexity scan only reported existing JS hotspots; Rule 10
  reviewer returned `APPROVE_WITH_NOTES`.
- This does not close the P1 stage-opt blocker. It only hardens the gate
  contract and evidence surface. The later signal-date K-line evidence slice
  closed the K-line diagnostic gap; remaining work is upstream formula
  coverage/signal-density repair before strategy/model work resumes.

## 2026-06-06 — TuShare no-persist exact-flow probe wiring for `need_027`

- `backend/services/data_sources/sources/tushare.py` adds a TuShare Pro source
  adapter for `moneyflow`, `moneyflow_dc`, and `moneyflow_ths`. Tokens are
  read only from `TUSHARE_TOKEN`, `TUSHARE_PRO_TOKEN`, or `TS_TOKEN`; probe
  kwargs and reports do not carry token values. Healthcheck verifies token and
  package presence only and does not call the live API.
- `backend/scripts/probe_source_capability.py --need027-exact-flow-gate` now
  treats `individual_fund_flow`, `moneyflow`, `moneyflow_dc`, and
  `moneyflow_ths` as exact-flow candidates, but evaluates them by source group:
  one complete source group can satisfy the source-probe layer, while other
  candidate source failures remain visible as grouped blockers. This preserves
  the business rule that AkShare and token-backed TuShare are alternatives, not
  an all-sources-AND production requirement.
- `backend/config/tdx_data_need_coverage.yaml` adds three TuShare `moneyflow`
  no-persist probe cases for `600519.SH`, `000001.SZ`, and `300750.SZ`. These
  cases are still probe/research scope only; no writer, DB persistence,
  failure-queue resolve, or production promotion is enabled by this slice.
- Focused verification: targeted tests for TuShare adapter, source probe gate,
  and need-coverage audit passed (`52 passed`); scoped
  `audit_test_tool_health.py` passed with `fail=0`, `warn=0`, and
  `registry_coverage_pct=100`.
- Latest live no-persist gate evidence is still `BLOCKED`: `6` exact-flow
  probes, `0` valid, source groups `akshare` and `tushare` both blocked.
  AkShare failed with Eastmoney `RemoteDisconnected`; TuShare failed because no
  env token is configured. `production_eligibility` remains `blocked` and the
  next gate is still writer/watermark/PIT/freshness/failure-queue evidence
  after a stable source probe exists.

## 2026-06-05 — DB retention consumer audit correction

- P1 DB retention consumer proof corrected a bad cleanup assumption. The three
  previously `unknown_pending_codegraph` P0A panels are not no-live-consumer
  deletion candidates:
  `mart_p0a_feature_label_panel_v5` has `run_daily_v7_inference.py`,
  `build_unified_panel_v1.py`, and `feature_join_v5.py` consumers;
  `mart_p0a_feature_label_panel_unified_v1` has unified ranker train,
  walk-forward, and diagnostic consumers; `mart_p0a_feature_label_panel` is
  still the default for P0b LightGBM, P1 ablation, and P0a audit scripts.
- `backend/scripts/audit_storage_retention_consumers.py` now provides the
  read-only static gate for table-inventory consumer proof. It exact-matches
  table tokens so `mart_p0a_feature_label_panel` is not falsely inflated by
  `_v3` / `_v4` / `_v5` references, fails on `unknown_pending_*` consumers,
  and reports runtime references by table.
- Live evidence after the correction: `audit_storage_retention_consumers.py`
  is `PASS` with `audited_tables=11` and `runtime_ref_tables=11`;
  `plan_storage_retention.py` is `candidate_count=0`,
  `table_inventory_count=12`, `policy_contract=PASS`,
  `compaction.recommended=false`. This does not authorize production delete or
  VACUUM; it means the retention contract is explicit and currently protective.
- Next cleanup step changed from "prove no live consumer" to "migrate or retire
  panel consumers first, then rerun consumer audit, copied-DuckDB validation,
  row/schema manifests, and rollback checks."

## 2026-06-05 — DB retention owner/consumer policy contract

- P1 DB retention/modularization advanced from owner-only dry-run inventory to
  a table-level policy contract in `backend/config/storage_retention.yaml`.
  Each inventory entry now declares `db_alias`, truth source, consumers,
  delete gates, rollback evidence, and compaction policy.
- `backend/services/storage_retention.py` now emits `policy_contract` from the
  dry-run planner and blocks `execute_storage_cleanup()` unless the contract
  is `PASS`. Missing consumers, truth source, compaction policy, delete gates,
  or rollback evidence are treated as policy violations.
- Production DB remained read-only for this slice. Live dry-run evidence:
  `candidate_count=0`, `table_inventory_count=12`,
  `policy_contract=FAIL on unknown_consumer_proof`,
  `compaction.recommended=false`. The FAIL is intentional protection: obsolete
  and cache entries with `unknown_pending_*` consumers cannot satisfy cleanup
  readiness.
- Rule 10 sidecar found and controller fixed an initial gap where
  `policy_contract` covered only inventory rows, not executable cleanup
  candidates. The contract now checks that every executable candidate matches an
  inventory contract and still has delete/rollback gates.
- Sidecar storage audit confirmed `data/smartmoney.duckdb` remains the capacity
  driver (`~33.6 GiB` / `34G`), with pressure from multi-version wide panels
  and rank/cache overlap. No large `.duckdb.bak/.gz/.zst` backup set or
  snapshot-loop write explosion was found under `data/`.
- Still not approved: production delete, VACUUM, export/import compact, table
  movement, or feature-store split. The follow-up consumer audit above replaces
  the earlier no-live-consumer hypothesis for the P0A panel family.

## Archived From `goal.md` On 2026-06-05

## 2026-06-05 — documentation control plane split

- `goal.md` was reduced to a compact live controller board: current phase,
  active priorities, latest gate snapshot, implementation plan, and roadmap.
- Completed work and historical status were moved here so new sessions can
  query evidence with `rg`/`tail` instead of reading a long startup document.
- The pre-existing `SESSION_HANDOFF.md` was archived as
  `analysis/session_handoff_20260605_archived.md` before regeneration; the new
  generated handoff is 101 lines and context-only.
- The pre-existing old GCP pipeline checkpoint was archived as
  `analysis/workflow_checkpoint_legacy_gcp_20260604.md` and
  `analysis/workflow_checkpoint_legacy_gcp_20260604.json`; the active
  `analysis/workflow_checkpoint.md` now defaults to an inactive stub unless a
  future pipeline explicitly registers itself.

> Archived note from old `goal.md`: before the 2026-06-05 split, `goal.md`
> was treated as the current target authority. After the split, this ledger is
> context-only evidence; update compact `goal.md` for current decisions.

## 2026-06-04 — Codex controller gate and data-health freshness blockers

- 2026-06-05 10:37 CST 已按总控模式完成同花顺 / 通达信 / TuShare 数据源阶段研究并落证据到 `analysis/data_source_selection_20260605.md`。结论按用途拆分：`need_027` exact-flow 的最小可逆下一步是 TuShare token-backed 只读三股 probe（`600519`、`000001`、`300750`）映射到现有主力/超大/大/中/小单契约；同花顺/iFinD 或同花顺语义 MCP 更适合产业链、题材、热榜、龙头扩散，但必须先做 daily PIT snapshot contract；通达信 MCP/xmtdx 保留低成本行情/板块/资金流实验候选，不能直接升为 `need_027` 生产源。当前 `need_027` 仍 `blocked`，不建 writer、不写库、不 resolve failure_queue，直到字段/日期/稳定性/PIT/freshness/watermark gate 全过。
- 2026-06-05 13:58 CST 已把数据源裁定从“单一主备”修正为 capability-level 并行路由，并补入 `analysis/data_source_selection_20260605.md`：TuShare 和 `tdxhub` 不是互斥替换关系；标准日频事实层可让 TuShare 做主源候选并由 `tdxhub` 交叉校验，但 K 线/xdxr/gpcw/F10/holder 等已稳定链路继续由 `tdxhub` 主供；CYQ 筹码分布按 `docs/chip_distribution_cyq_spec.md` 先走本地 K 线 + 历史流通盘 deterministic 计算，TuShare 5000 档 `cyq_perf` / `cyq_chips` 只作为 benchmark/backfill 候选；`need_027` 仍按 exact order-flow gate 管理，2000 档 `moneyflow`、5000 档 `moneyflow_dc`、6000 档 `moneyflow_ths` 分层 probe，不用 rank/proxy 或 stale raw 代替生产证据。生产配置暂不改，下一步是 capability-router source catalog + token-backed no-persist probe。
- 2026-06-05 16:05 CST 已复核 iFinD MCP 官方价格页公开 bundle 与 TuShare 官方权限表，并把性价比边界写入 `analysis/data_source_selection_20260605.md`：`tdxhub + iFinD trial` 可零成本完成连接、产业链模板和少量 EDB/news/notice 探针；iFinD 个人版 `40 CNY/月` / `399 CNY/年`、`5000 次/月`，适合日均约 `166` 次以内的小批量语义/PIT research snapshots；TuShare 2000 积分按当前个人表约 `200 CNY/年`，`200/min`、`100000/day/api`，更适合结构化批量和 `need_027.moneyflow` exact-flow probe。当前排序调整为 `tdxhub` 生产 backbone -> iFinD 语义研究层 -> TuShare 结构化缺口层；仍不新增 writer、不写 token、不改生产路由。
- 2026-06-05 本轮已核验用户镜像 `/Users/dp/Documents/M/stock/iFind`，并把本地可复现证据补入 `analysis/data_source_selection_20260605.md`：镜像包含 pricing、product-data-scope、产业链/主题/宏观产业案例、finance-data skill 页和前端 bundle；确认 iFinD MCP 的 7 个数据域、32+ 工具、产业链/主题/EDB/news/notice workflow、trial/personal/enterprise 限额，以及 MCP 不支持高频实时行情和盘口数据。Chandrasekhar sidecar 复核结论不冲突：iFinD 进入 `research_only/probe` snapshot source，不作为全局生产替代；首批生产形态只能从 `sector_data` daily PIT snapshot、news/notice evidence snippets、curated EDB series 这类可控 contract 做起，`need_027` exact order-flow 继续 blocked。
- 2026-06-05 14:40 CST 已按用户要求清理 ChunkyMonkey GCP 明确资源：项目 `gen-lang-client-0821344445` / `Chunky Monkey` 中 `chunkymonkey-optuna` VM 已删除，关联 100GB boot disk 已随 VM 删除，`gs://chunkymonkey-data-0517` 及约 `33,948,685,278` bytes 对象已删除；项目壳随后也已删除，当前 lifecycle 为 `DELETE_REQUESTED`。`Gemini API` 项目 `gen-lang-client-0274784341` 的 RUNNING `e2-micro` 通过串口日志确认运行 `shadowsocks-go.service` / `shadowsocks-go-443.service`；14 个 10GB 快照来自 `default-schedule-1` 每日自动 boot-disk snapshot，保留 14 天且源盘删除后默认 `KEEP_AUTO_SNAPSHOTS`。该项目仍非 ChunkyMonkey 明确资源，删除需按用户确认范围执行。
- 2026-06-05 本轮已把项目内 GCP 从“retired but re-enable guard”改为“执行面删除 + provider-neutral job contract”：删除 `gcp/` 旧入口、`scripts/gcp_*` / phase5 monitor / cost tracker / GCS sync 等脚本、`backend/config/gcp_policy.yaml`、旧 guard 测试、两个失效 launchd plist，并清理本机 `~/Library/LaunchAgents` 中对应 label（当前均 absent）。新增 `backend/config/experiment_jobs.yaml`、`backend/services/experiment_jobs.py`、`scripts/chunkyctl jobs`，当前 active backend 只有 `local`，`modal` 为 planned 且会阻断；data_validation / backtest_validation / model_training / parameter_search 必须先声明 job family、required gates 和 artifact contracts，不再通过注释、隐藏 flag 或旧 latch 保留废路径。
- 2026-06-05 15:09 CST 已按用户追问把 `Gemini API` 项目 `gen-lang-client-0274784341` 的 `default-schedule-1` 自动快照策略从 14 天改为 3 天，并把源盘删除行为改为 `APPLY_RETENTION_POLICY`；boot disk 仍绑定该 policy。15:18 CST 已按用户要求删除最旧 11 个自动快照，当前只保留最近 3 个：`20260603055544`、`20260604055544`、`20260605055544`，均为 10GB READY boot-disk snapshots。
- 2026-06-04 盘后数据刷新已完成：本地 `build_price_kline_tdxhub.py --skip-existing --target-date 2026-06-04` 写入 `10,388` 行，`5,200` 股成功 / `1` 股失败（`000638`），canonical K-line 当前为 `2022-01-01 -> 2026-06-04` / `5,203` codes；`sync_hs300_benchmark_kline.py` 也到 `2026-06-04`。`build_feature_panel_duck.py --mode incremental` 把 `fact_feature_panel` 推进到 `2026-06-04`，`4,161,982` rows / `5,203` codes；`prune_feature_panel_to_canonical_kline.py --start-date 2026-01-12 --end-date 2026-06-04` 复验 `missing_signal_count=0` / `pruned_count=0`。
- holder/F10、GPCW、capital、feature-panel tail 本轮 blocking 切片已串行刷新并复验：GPCW profile/PIT audit 刷到 2026-06-04；capital `dim_capital_behavior_latest.updated_at=2026-06-04T15:00:54.088424`；F10 raw 已刷到 2026-06-04，canonical holder facts 有 2026-06-04 replay 行，`idx_t10_*` / `idx_plan_*` / `idx_trade_*` 9 个 replay 相关索引已恢复；`mart_shareholder_plan_initial_event` 重建为 `9,677` rows / `built_at=2026-06-04T08:33:04+00:00`。
- 最新 live gate：`data_health_snapshot.py --dry-run --format text` 为 `green=326 / yellow=16 / red=0 / blocking_yellow=0`；`scripts/chunkyctl doctor --fast` 仍为 `WARN`，但 `data_health.blocking_yellow_tables=[]`、`red_tables=[]`。剩余黄色是 warning-quality 资产（如 `fact_dzjy_event`、`fact_executive_trade_event`、`raw_lhb_daily` 等），不再是当前 startup blocker；后续可按 warning-only writer 收尾，但不要把它们和本轮 blocker 混为一谈。
- 2026-06-05 08:39 CST 复跑 `scripts/chunkyctl doctor --fast` 后，随时间推进 data-health freshness 又回到 `green=321 / yellow=21 / red=0 / blocking_yellow=4`。blocking yellow 为 `fact_financial_pit_daily`、`fact_stock_fundamental_stage_daily`、`mart_feature_drift`、`mart_feature_drift_histogram`；这是 SLA 滚动后的当前 live 状态，不是 `design_review_gate` 代码改动导致。下一轮数据维护应优先按这 4 个 writer 切片复验，再处理 warning-only 资产。
- 2026-06-05 08:50 CST 已按窄写窗口清掉这 4 个 blocking-yellow：`backfill_financial_pit.py --start 2026-06-03 --end 2026-06-04` 写入尾部 `10,364` 行，`fact_financial_pit_daily` 到 `2026-06-04`；`build_stage_formula_fitness.py --start 2025-01-01 --write-start 2026-06-03 --end 2026-06-04 --stage-only` 补 `fact_stock_technical_stage` `10,222` 行；`build_picture_daily.py --date 2026-06-04` 写入 `5,203` 股 × 4 表；`compute_feature_drift.py --refresh-baseline` 写入 27 个 drift 特征并刷新 histogram（命令返回 2 是因为新 snapshot 有 `critical=3`，不是写入失败）。复验：`data_health_snapshot.py --dry-run --format text` 为 `green=325 / yellow=17 / red=0`，`scripts/chunkyctl doctor --fast` 为 `WARN` / `worktree PASS` / `blocking_yellow=0` / `red=[]`。剩余 yellow 是 warning-only writer/SLA debt；下一步不要再追这 4 个旧 blocker。
- 2026-06-05 10:20 CST 按 warning-only P1 本地派生切片继续收敛 data-health：先以 `data_health_snapshot.py --dry-run --format json` 确认 live 状态为 `green=333 / yellow=9 / red=0 / blocking_yellow=0`，再串行重建 `mart_current_relationship`（`build_current_relationship` 写入 `5,000` 行）和 `mart_dual_confirm`（`calc_dual_confirm` 更新 `15,320` 条事件）。复验：`data_health_snapshot.py --dry-run --format json` 为 `green=335 / yellow=7 / red=0 / blocking_yellow=0`；`scripts/chunkyctl doctor --fast` 为 `WARN` / `worktree PASS` / storage payload `PASS` / stage-opt `ready_coverage_pct=74.81` / `need_027` 仍 blocked。剩余 7 个 yellow 是 `fact_dzjy_event`、`fact_jgdy_event`、`raw_executive_trade`、`fact_executive_trade_event`、`fact_institution_event`、`fact_shareholder_trade`、`fact_paper_sim_trade`；它们分别属于外部 AkShare 全表/事件源、derived high fan-out、TDX holder/F10、paper_sim 验证产物，不应为了 freshness 数字盲跑窄窗口或全量 destructive writer。
- `ingest_holders_tdxhub.py --parse-raw-only --replace-facts --limit 5201` 暴露了 DuckDB indexed delete / replay 性能问题：逐股 rowid delete 会触发 DuckDB fatal 或长时间扫描。代码已改为 replace replay 先解析成功 raw，再按 raw key 临时表批量删除旧 holder/plan/trade/controlling facts、跳过 holder-key 逐行 delete，并在生产表有 `availability_source` 时直接插入该列，避免无索引逐行 UPDATE。Rule 10 reviewer 指出的 `fact_controlling_shareholder` stale-row 风险和 `availability_source` 覆盖缺口已补回归；`backend/tests/test_ingest_holders_tdxhub.py` PASS。
- Moth 已同步上游并更新本机安装：`dare2live/moth` 确认为 PUBLIC，最新提交 `dcb809a fix: sync ChunkyMonkey instruction sources` 已推送，`/Users/dp/.local/bin/moth` 指向 repo `.venv/bin/moth` 并能在 registry `moth profile chunkymonkey` 与 repo-local snapshot/profile 路径输出 `instruction_sources.ignored_by_default=["CLAUDE.md"]`。
- 2026-06-05 09:02 CST 已把用户提供的“架构师/总指挥”思维提炼成本地 Codex skill：`/Users/dp/.codex/skills/architect-controller/SKILL.md`，并在 `/Users/dp/.codex/AGENTS.md`、`chunkymonkey-governance` skill、项目 `AGENTS.md`、`docs/chunkyctl_session_quickstart.md` 和 `.moth/profile.yaml` 挂上调用/发现路径。该 skill 的运行协议是 substrate truth source、boundary contract、meta-spec、falsification gate、attention allocation 和 smallest reversible next step；Moth 只暴露 `skill_architect_controller` evidence path，不承载业务规则。
- 2026-06-05 09:36 CST 已用 `$architect-controller` 做本轮恢复和架构检查：Codex app 更新后未提交补丁仍在；旧的 `chunkyctl doctor` / `moth snapshot` / complexity scan 孤儿进程已清掉。`architect-controller` skill 新增 “verify the verifier” 运行规则；`complexity-optimizer` 的单文件扫描 false-negative 已修并用最小反例验证，所以本轮之前由单文件 complexity scan 得出的“clean”证据遇到异常时要复验工具本身。
- Moth complexity baseline 误报根因已改按“本机 baseline stale + Moth path normalization”处理：Moth 不再用顶层目录不相交猜测 baseline 不兼容，loaded baseline 会正常 compare；Moth diff 现在会把 repo 内绝对路径归一为相对路径，避免同源 finding 被误算为新增/已解决。当前本机 ignored baseline `data/reports/tooling/complexity_baseline.json` 已刷新到当前全仓 scanner scope（80 条 `assets` findings），所以 live Moth snapshot 应回到 `complexity.diff.status=compared` / `new_high_count=0`。若 scanner scope 未来改变，先刷新同 scope baseline，再使用 `new_high_count` 做阻断判断。
- `data_health_snapshot.py --dry-run` 写锁根因已修：dry-run 现在通过 `duck_adapter.connect(..., read_only=True)` 打开生产 DB，且不执行 `ensure_asset_deprecation_columns()` DDL；`deprecation_status` / `replacement_table` 改为 optional cols 保持旧 schema 兼容。复验 `--dry-run --format json` 为 `green=325 / yellow=17 / red=0`，不再因写连接或 DDL 抢 DuckDB 锁。
- Codex 规则源边界已重新固化：`CLAUDE.md` 是 legacy Claude-only history，Codex 默认只使用 `AGENTS.md`、当前 docs、Codex skills、Moth evidence paths 和 live tooling output。`chunkyctl preflight` 现在输出 `instruction_sources.ignored_by_default=["CLAUDE.md"]`，`chunkyctl worktree` 把 `CLAUDE.md` 归为 `legacy_context`，不再混入 `controller_state`。
- controller/agent 执行模型已做成机器 gate：广义 audit/research/architecture/data/debug/review/spec/triage 或 3+ 独立 scope 如果没有 `--agent-dispatch` 证据，会返回 `controller_agent_dispatch_missing` 并使 `preflight` FAIL；只有显式 `--agent-skip-reason` 才能作为 WARN 例外。Controller 仍负责方向、scope、最终验收、共享 docs、commit、DB/provider 写窗口；agent 输出只能作为候选证据。
- 第一性原理 / 奥卡姆 / 架构师审查已从文档约束补成 `chunkyctl preflight.design_review_gate` 机器字段：scope 任务、架构/数据/策略/配置/表/阈值类任务会显式要求检查 first_principles、occam、owner、truth_source、failure_mode 和 drift-blocking gate；长期规则仍归 `docs/engineering_governance.md`，Moth 只暴露证据路径和指令源边界。
- DB 容量问题已完成只读并行审计并记录到 `analysis/db_capacity_audit_20260604.md`：`data/smartmoney.duckdb` 约 `33.6 GiB` / `34G`，未发现 `no2`、session snapshot 循环写爆，或能解释容量的 `.bak/.gz/.zst` 压缩备份副本；主因更像多版本宽面板/缓存并存、rank/cache key 重叠、索引/row-group 开销，以及 `formula_engine` reason JSON 总量 WARN。最可疑冗余组是 `mart_p0a_feature_label_panel` legacy/v3/v4/v5/unified 以及 `fact_feature_panel_candidate` / `fact_feature_panel_tdx_keep_challenger` / `mart_feature_rank_matrix_cache_*` 的同 key 重叠。不要直接删表或 VACUUM；下一步应做独立 retention/index/compact 方案，先分 owner 和可复现证据。
- 2026-06-05 本轮只读复核未发现“原表 + 压缩备份 + 多快照”或 `no2` 循环快照导致的 P0 写爆；`smartmoney.duckdb` 容量风险仍来自宽事实/缓存/多版本表和 reason JSON payload。本轮 storage payload 切片只校准 reviewed-column owner/cap，不清表、不压缩、不写生产库：`fact_technical_trigger.reason_codes_json` 与 `mart_macd_state_history.reason_codes_json` 提升为 full-history evidence cap，`mart_stock_picture_daily.institution_top_json` 纳入 bounded picture summary。最新 `audit_storage_payloads.py` 复验为 `PASS`、`323 columns / 0 FAIL / 0 WARN / 13 reviewed`，递归 key/path-marker/单行超 cap 仍会重新 WARN/FAIL。
- DB retention 第一片已收口到 dry-run inventory，不做生产删除：`storage_retention.yaml` 现在把 F10 raw lineage、canonical/current panels、legacy/obsolete candidate panels、tdx_keep challenger panel、rank-matrix cache 表纳入 `table_inventory`；`plan_storage_retention.py` dry-run 默认 read-only 打开生产库。最新只读 dry-run 为 `candidate_count=0`、`table_inventory_count=12`、`protected_artifact_table_count=7`、`compaction.recommended=false`，说明当前进度是“机器可读分类和 owner/action 绑定”，不是清理执行。
- DB 模块化管理第一片已落地为 registry + 连接权限收口，不搬表、不删除、不压缩：新增 `backend/config/database_manifest.yaml` 与 `backend/services/database_manifest.py`，机器记录 `smartmoney` / `market` / `alpha158` / `etf` / `phase5_predictions` / planned `feature_store` 的 alias、path、owner/domain、online/artifact/planned 状态和默认 attach mode；`analytics` 默认路径改由 manifest 解析。`duck_adapter.connect(... attach={"market": path})` 现在默认把附库按 `READ_ONLY` attach，只有显式 `{"path": path, "read_only": false}` 才允许 writable attached DB，避免 writable smartmoney 连接顺手把 market/alpha158/etf 也拿成可写边。只读 sidecar 未发现生产路径必须写 attached DB；可疑路径都是“写 smartmoney、读 market/alpha158”。验证：`chunkyctl preflight` PASS 且记录 agent dispatch，`audit_test_tool_health` PASS / registry coverage 100%，`py_compile` PASS，targeted pytest `24 passed`，`paper_sim/test_ddl.py` 与 `test_candidate_feature_pipeline.py` PASS，target files complexity clean，`scripts/chunkyctl audit --run ...` PASS，CodeGraph 已 sync。
- DB 连接边界第二片已把 `services.db_connection` 默认主库路径接入 `database_manifest.smartmoney`，同时保留 `services.db.DB_DIR` / `DB_PATH` monkeypatch 兼容；这让主业务 DB 的默认入口也跟随 manifest，而不是继续私有硬编码。新增 `test_default_db_path_comes_from_database_manifest` 锁住默认路径。验证：controller preflight PASS 且记录 agent dispatch；`audit_test_tool_health` PASS；`py_compile` PASS；targeted pytest `16 passed`；目标复杂度 clean；`scripts/chunkyctl audit --run ...` PASS；`git diff --check` PASS；CodeGraph 已 sync。没有移动表、删除表、VACUUM 或生产 DB 写入。下一片再选代表脚本逐步移除散落的 `data/*.duckdb` 字面量。
- DB 模块化第三片已收口为“新增阻断”地基，不继续深挖历史迁移：`check_rule_compliance.py` 现在会在 staged diff 上阻断新增生产 `duckdb.connect(...)` / duckdb alias connect 和新增 `data/*.duckdb` / `.duckdb` 文件名字面量；例外仍由 evidence 注释或 `backend/config/database_manifest.yaml` 等配置拥有，不在 hook 里新建业务规则源。新增 `rule_compliance_static_gate_tests` 登记到 test-tool registry。验证：`backend/tests/scripts/test_rule_compliance.py` + `backend/tests/services/test_duckdb_connect_policy.py` 10 passed，DuckDB connection/manifest 相关 16 passed，`scripts/chunkyctl audit --run ...` PASS，CodeGraph sync 已由 audit 执行（提交前新测试文件仍会显示 pending added），`git diff --check` PASS。下一步不应沿 DB literal 批量迁移深入，除非它阻塞更高层框架；优先回到 `need_027` exact-flow、stage-opt upstream supply、storage retention/compact 方案的分层推进。
- `need_027` exact-flow blocked-gap 第一片已落地为专用小批量 probe gate：`backend/config/tdx_data_need_coverage.yaml` 的 `need_027.source_probe_cases` 现在配置 `600519/sh`、`000001/sz`、`300750/sz` 三个 exact `individual_fund_flow` 样本；`backend/scripts/probe_source_capability.py --need027-exact-flow-gate` 默认只读不持久化，case-level `persist_status` 会被拒绝，只有显式 `--persist-status` 才写 failure_queue。gate 要求 exact capability、非空行、日期范围、主力/超大/大/中/小单字段；即使 `--persist-status`，也必须先通过 exact-flow validation 才能 resolve，validation 失败只会保持/open blocker；rank snapshot 会被标为 `ignored_for_need_027_exact_flow_gate`，且 `individual_fund_flow_rank_snapshot` 的 persistence domain 已改为 `stock_fund_flow_rank_snapshot`，不能误 resolve `order_flow_fund_flow`。2026-06-04 20:25 CST live no-persist gate 仍为 `BLOCKED`：3/3 exact 样本均 `RemoteDisconnected`，`success_rate=0.0`，`production_eligibility` 保持 `blocked`，下一步仍需源稳定性恢复后再做 PIT/freshness、writer/watermark、failure_queue resolve。
- stage-opt upstream supply contract 第一片已落地，不调阈值、不写生产库：新增 `backend/config/stage_opt_candidate_supply.yaml` 与 `backend/services/stage_opt_candidate_supply.py`，把 `fact_technical_trigger` / `mart_macd_state_history` 的 source role、grain、eligibility、PIT status、allowed consumers、allowed stage bins 和 research-challenger formula scope override 放到 config-owned contract。`audit_stage_opt_candidate_supply.py` 现在消费该 contract 并输出 `schema_version=1` / `candidate_supply_contract`；`chunkyctl doctor` 透传该 summary。验证：scoped test-tool audit PASS，`py_compile` PASS，`backend/tests/services/test_stage_opt_candidate_supply.py` + stage-opt audit + chunkyctl tests `45 passed`，`backend/tests/test_build_formula_signals.py` `23 passed`，`scripts/chunkyctl audit --run ...` PASS，target files complexity clean，CodeGraph 已 sync。结论不变：stage-opt 仍是 `P1 / upstream_candidate_supply`，当前地基只是把 truth-source / source eligibility / formula scope 边界机器化，下一步再决定是否做 schema/source redesign。
- 2026-06-04 14:11 CST 的 `scripts/chunkyctl doctor --fast` 是 forecast 切片前 baseline：`data_health` 为 `green=314 / yellow=28 / red=0 / blocking_yellow=11`，覆盖 2026-06-03 的 data-health PASS 旧叙述。切片前 blocking yellow 集中在 5 个 writer 切片：`forecast`、`holder/F10`、`GPCW local derived`、`capital`、`feature-panel tail`。
- Forecast 小切片已执行：`ingest_profit_forecast_snapshot.py --snapshot-date 2026-06-04` 写入 `raw_profit_forecast_snapshot_daily` 当日 `2,377` stocks，`compute_forecast_upside_live.py --snapshot-date 2026-06-04` 写入 `mart_forecast_upside_live` 当日 `2,305` stocks；`data_health_snapshot.py --dry-run --format text` 复验为 `green=316 / yellow=26 / red=0 / blocking_yellow=9`。
- Forecast 根因已定位并修复到 daily workflow：`scripts/daily_update.sh` 原本 Step 2l 只跑 raw ingest，未跑 `compute_forecast_upside_live.py`，导致 `mart_forecast_upside_live` stale。现在 Step 2l/2m 用同一个 `FORECAST_SNAPSHOT_DATE` 串行刷新 raw 和 shadow mart；`backend/tests/test_daily_update_model_refresh.py` 已加 static contract 防回退。注意 forecast mart 仍是 live shadow，不可用于历史 training/backtest。
- 下一步不应直接把所有 writer 混跑。当前 blocking-yellow 已清零；后续如果继续数据维护，按 warning-only writer 分片推进（事件类、调研类、LHB、paper_sim 等），每片仍要在 controller 统一写窗口内执行并用 `doctor --fast` / `data_health_snapshot.py --dry-run --format text` 复验。
- 本轮 governance/tooling 验证：`backend/tests/scripts/test_chunkyctl.py` 29 passed，`audit_test_tool_health.py --scope backend/scripts/chunkyctl.py --scope backend/tests/scripts/test_chunkyctl.py` PASS，`scripts/chunkyctl audit --run AGENTS.md backend/scripts/chunkyctl.py backend/tests/scripts/test_chunkyctl.py docs/chunkyctl_session_quickstart.md docs/engineering_governance.md` PASS，CodeGraph index up to date，`git diff --check` PASS。

## 2026-06-03 — technical-stage residual production rebuild complete

- `backend/services/formula_engine/technical_stage.py` 已把 `unknown` 收窄为“数据不足”语义；有足量 MA 慢线历史的 residual 状态按 `backend/config/technical_stage.yaml` 的 governed residual policy 归入 `1/3/4`。`residual_*_stage` 配置不允许 `1.5/2`，所以 Stage 1.5/2 仍只能由显式突破/上升趋势规则产生，不能通过 fallback 绕过 MA 顺序和回撤约束。
- 代码切片已提交：`ebd18209 fix: govern technical-stage residual classification`；验证为 `audit_test_tool_health.py --scope backend/tests/test_formula_engine.py --scope backend/tests/test_build_formula_signals.py` PASS，targeted stage/signal-context tests 14 passed，stage-opt audit tests 10 passed，`scripts/chunkyctl audit --run backend/config/technical_stage.yaml backend/services/formula_engine/technical_stage.py backend/tests/test_formula_engine.py` PASS，CodeGraph synced，目标文件 complexity scan clean，`git diff --check` PASS。
- 生产 DB 已按月/季度窗口本地重建完成：`fact_stock_technical_stage` 现在 `3,973,319` rows，范围 `2023-01-12 -> 2026-06-02`；`fact_signal_context` 现在 `4,093,116` rows，范围 `2023-01-03 -> 2026-06-02`。单次 full-window `fact_stock_technical_stage` 写入会在大批量 `executemany` 阶段中断，已改用月度窗口规避；后续若要再做全量重建，应优先把 stage 写入实现改成批量/临时表路径。
- 最新 full-history `audit_stage_opt_candidate_supply.py --format json` 仍为 `WARN`，但漏斗已明显改善：`filtered_signal_rows=7,918,485`、`unique_keys=358,529`、`ready_keys=268,198`、`ready_coverage_pct=74.81`，`dropped_unknown_stage_rows` 从 `4,433,034` 降到 `1,231,858`，`blocked_keys` 从 `101,824` 降到 `90,331`；剩余 blocker 仍全是 `below_min_signals`，所以主线继续是 `P1 / upstream_candidate_supply`，不是再调 stage fallback。
- 最新 `scripts/chunkyctl doctor --fast` 仍为 `WARN`，但 `data_health PASS`、`universe PASS`、`worktree PASS/0 dirty`。剩余 next actions 是 complexity high findings、storage payload WARN、stage-opt upstream candidate supply、`need_027` exact-flow blocked-gap triage。

## 2026-06-03 — Codex resume automation re-baselined to manual refresh

- Codex app/CLI 的隐藏启动加载项已收口：`~/.codex/hooks.json` 不再启用 `SessionStart -> session_start_handoff.sh`，用户 crontab 不再包含 `scripts/session_snapshot.sh` / `scripts/workflow_checkpoint.sh` 周期任务，`check_pending_work.sh` 也不再在每次 prompt 隐式查询旧 provider/VM 状态。
- 项目恢复路径同步改为手动刷新：中断后先在仓库运行 `bash scripts/cm_resume.sh`，再让新 Codex 会话按 `docs/chunkyctl_session_quickstart.md` 完成 live startup checks。`SESSION_HANDOFF.md` 只作为 context-only snapshot，不能替代 `doctor --fast`、`worktree`、当前 crontab/hooks 实况。
- `scripts/install_resilience.sh` 默认不再安装 legacy cron/launchd 自动化；如确需恢复旧自动化，必须显式设置 `CHUNKYMONKEY_ENABLE_LEGACY_AUTOMATION=1`。这避免 stale handoff 被新 Codex 会话静默加载，也避免 cron 在 macOS TCC/FDA 约束下反复产生失败日志或系统邮件。

## 2026-06-03 — controller/agent parallelism and blocker triage update

- 用户已明确纠正执行模型：Codex 是总指挥，其他 agents 是不同角色助手；默认应并行派 bounded sidecar agents，只有用户明确要求不并行、工具不可用、或下一步和 controller critical path 紧耦合时才不并行。`AGENTS.md` 与 `docs/chunkyctl_session_quickstart.md` 已同步这条 opt-out parallelism 规则；DB-heavy 命令仍要区分 read-only 与 materializing/write 连接，避免只读调查互相抢 DuckDB 写锁。
- 阶段推进原则补充：不要在某个方向过度深入细节；先搭框架地基，再按层完善。当前估算本阶段约完成 70%-75%，剩余约 25%-30%：重点不是继续清单个 DB literal，而是把 `need_027`、stage-opt supply、storage retention/compact、数据治理闭环按框架顺序推进到可持续默认门禁。
- `audit_tdx_data_need_coverage.py` 已新增 `--summary-only` 只读摘要路径，`chunkyctl doctor` 改用该路径读取 `need_coverage`，避免 startup/并行 triage 时为了展示 blocked summary 去 exact-sync 写 `mart_tdx_data_need_coverage` / `dim_data_source_priority` / `mart_data_source_reassignment_proposal`。
- `need_027` controller 复核：单只 `600519/sh` 曾返回 `status=ok` / `row_count=120` / 日期范围 `2025-12-01 -> 2026-06-02`，但 2026-06-03 15:38 CST 的只读 explorer live probe 又返回 `status=blocked` / `RemoteDisconnected`。因此该 blocker 仍是 production blocker：不能按“源已恢复”推进，也不能用 rank snapshot 当 production fallback；下一刀应是专用小批量 exact-flow probe gate，先证明稳定性、PIT/freshness、writer/watermark、failure_queue resolve，再谈生产 writer。
- `stage-opt` explorer 复核后仍是 `P1 / upstream_candidate_supply`：当前 full-history audit 为 `raw_rows=9,159,813`，`filtered_signal_rows=4,717,309`，`unique_keys=315,814`，`ready_keys=213,990`，`ready_coverage_pct=67.76`，`below_min_signals=101,824`，`dropped_unknown_stage_rows=4,433,034`；weakest formulas 为 `ma_base_breakout` / `gs_pullback_confirm` / `volume_base_breakout`。不要继续靠删除弱公式或局部阈值放宽美化 coverage。`audit_stage_opt_candidate_supply.py` 现已补 `attrition_funnel`、`formula_attrition`、`formula_family_attrition`、`blocked_matrix_by_stage_formula`、`top_blocked_stage_formula_cells`、`blocked_matrix_by_registry_family`、`top_blocked_registry_family_cells` 与 advisory `verdict`；`--limit-stocks` 现在从 raw 非指数样本选股，纯 unknown-stage 切片仍会保留 drop 计数并 `WARN`，并输出 `P1 / stage_context_coverage` recommendation，sensitivity 也改成单次 key counter，不再重算完整 attrition 矩阵。`chunkyctl doctor` 会透传 top evidence cells。下一步应基于这些证据决定 upstream candidate contract/schema，而不是再做 knob-tuning。
- `storage_payload` cap recalibration slice 已收口：`fact_technical_trigger.reason_codes_json` 与 `mart_macd_state_history.reason_codes_json` 仍要求单行很小、无 recursive/path hits，但总量 cap 按 full-history evidence 调整；`mart_stock_picture_daily.institution_top_json` 作为有界 daily picture summary 纳入 reviewed 列。当前 storage payload 不是 blocker；后续 storage 治理继续转向 retention/compact owner 设计。

## 2026-06-03 — Codex local ops and Moth profile rules captured

- 新增全局 skill `/Users/dp/.codex/skills/codex-local-ops`，用于 Codex Mac app/CLI 本地启动项、hooks、skills/MCP、plugin sync 429、remote compact 前端错误、`.codex/worktrees`、Terminal system mail、GCP monitor 残留等问题。全局 `/Users/dp/.codex/AGENTS.md` 已要求这类任务先用 `$codex-local-ops`，并把默认并行修正为 opt-out。
- `codex-local-ops` 内置 `scripts/probe_plugin_startup_sync.sh`，用 fake `git` 短时探测 `codex app-server` 启动期 remote 调用。当前验证显示 baseline、`plugin_sharing=false`、`remote_plugin=false`、local marketplace override 仍会 `ls-remote` `openai/plugins.git` 与 `openai/codex-plugin-cc.git`；只有 `plugins=false` 不调用 git，但会禁用插件系统，因此只能作为用户明确接受的最后手段。
- 新增 repo-local Moth profile `.moth/profile.yaml`，并同步 `/Users/dp/Documents/M/moth/profiles/chunkymonkey.yaml`；`backend/services/moth_snapshot.py` 现在会把匹配的 `chunkymonkey` profile 自动解析到 repo-local profile。Moth 只拥有 shared tooling metadata/evidence paths/CodeGraph/complexity/dirty state，`stage_opt`、`need_027`、`storage_payload`、`data_health` 仍由 ChunkyMonkey audit scripts 与 `chunkyctl` 拥有规则。
- `.moth/profile.yaml` 现在把 `/Users/dp/.codex/AGENTS.md`、`$codex-local-ops`、`$chunkymonkey-governance`、`$chunkymonkey-review-gate` 和 `docs/PROJECT_CONSTITUTION.md` 暴露为 evidence paths；`AGENTS.md` / `docs/engineering_governance.md` / `docs/chunkyctl_session_quickstart.md` 已新增 Skill dispatch，防止后续新会话只跑 `doctor` 而不加载对应 skill。Moth 仍只负责定位证据和 shared tooling snapshot，不承载业务 gate 规则。
- `plugin startup_sync` 规则已扩展到 GitHub 429、clone timeout、`early EOF`、`openai/plugins.git`、`openai/codex-plugin-cc.git` 和 `codex review` / helper task 启动时打印的同类 WARN；这些都先按 Codex 本地插件同步问题处理，不先归因到项目 hook/cron/LaunchAgent，也不先删 plugin cache。
- 验证：`quick_validate.py /Users/dp/.codex/skills/codex-local-ops` PASS；`moth profile chunkymonkey --format json` 能看到新增 evidence paths；`PYTHONPATH=backend python -m pytest -q backend/tests/services/test_moth_snapshot.py backend/tests/scripts/test_chunkyctl.py backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 59 passed；`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_audit_stage_opt_candidate_supply.py backend/tests/scripts/test_chunkyctl.py` 37 passed；`scripts/chunkyctl audit --run ... .moth/profile.yaml` PASS；`scripts/chunkyctl worktree --format markdown` unknown=0；`scripts/chunkyctl doctor --fast` 仍为 WARN，但 data_health/test_tool/universe PASS，剩余 WARN 是已知 dirty/complexity/storage/stage-opt/need_027。


## 2026-06-03 — strategy reframe: data-health blockers first

- 这轮复盘的结论是：前端热点收口本身没有错，`stock-view.js` / `data-view.js` / `signal-adapter.js` / `app.js` 的局部 refactor 也都通过了 targeted 校验，但它们正在变成“优化复杂度分数”的局部循环，而不是解决当前项目最重的风险。最新 `scripts/chunkyctl doctor --fast` 已经把 **data_health 刷成 PASS**，`green=342 / yellow=0 / red=0`；`moth` 仍提示 complexity new high findings 80，说明当前剩余问题已经不在 data-health，而在历史复杂度和仍未收口的 blocker 线程。
- 这轮 data-health triage 已按 writer / SLA 补完底层链路：`price_kline_tdxhub`、`fact_financial_pit_daily`、`fact_stock_fundamental_stage_daily`、`fact_feature_panel`、`mart_feature_drift`、`mart_feature_drift_histogram`、`fact_lhb_event`、`mart_daily_recommendation_explanation` 都已经回到当前基准日期或被刷新到可用状态，因此不再把它们当成门禁阻塞项。
- 前端结构优化继续降级为次要任务，除非它直接关联用户可见 bug、数据门禁或 pipeline 失败；下一阶段的 success metric 以 `need_027` blocked-gap triage 和 stage-opt candidate supply 为主，再结合必要的 warning-only / hot-path 收尾，而不是继续压 heuristic hotspot count。
- 执行顺序分层：
  1. **P0 数据健康 / 正确性 blocker**: 直接阻断门禁、数据 freshness、PIT、安全性或会让 `doctor --fast` 继续 WARN 的问题。先修这个。
  2. **P1 框架 / seam**: 能一次性减少 2 个以上后续修复、或者复用到多个热点的共享 helper、shared model、边界抽离。只有它能减少重复劳动时，才先做框架。
  3. **P2 用户可见热点**: 单个页面或模块的高频路径、N+1、重复扫描、明显卡顿。若没有共享 seam，就直接修这个，不要为了“框架正确性”绕路。
  4. **P3 局部清理**: 只影响单个文件、收益有限、且不触及 blocker 的收尾工作。放到最后。
- 具体规则：如果一个“框架改造”只服务一个文件，直接修细节更划算；如果它能同时覆盖 2+ 个热点，或能把同类问题统一进一个 shared helper / model，先做框架。

## 2026-06-03 — data-health blocker triage complete (historical; superseded by 2026-06-04 live snapshot)

- `price_kline_tdxhub` 已补到 `2026-06-02`，`backfill_financial_pit.py --start 2026-05-29` 已把 `fact_financial_pit_daily` 抬到 `2026-06-02`，`build_picture_daily.py` 把 `fact_stock_fundamental_stage_daily` 抬到 `2026-06-02`，`build_feature_panel_duck.py --mode incremental` 把 `fact_feature_panel` 抬到 `2026-06-02`，`compute_feature_drift.py --refresh-baseline` 已刷新 `mart_feature_drift` / `mart_feature_drift_histogram`。
- `update_watermark_sla.py` 已把 `kline_daily` 水位同步到 `2026-06-02`，`scripts/chunkyctl doctor --fast` 现在是 `WARN` 但 `blocking_yellow=0`，`green=340 / yellow=2 / red=0`。剩余黄色只是不阻断门禁的 warning-only 资产：`fact_lhb_event`、`mart_daily_recommendation_explanation`。
- 这意味着 data-health blocker triage 这一轮已经收口，后续优先级可以回到 `need_027` blocked-gap triage、stage-opt candidate supply，以及必要时的 warning-only writer 收尾，不再围着旧的 5 个 blocking yellow table 打转。

## 2026-06-03 — stage-opt supply expansion via bc_absorbed challengers

- `backend/services/formula_engine/bc_absorbed_challengers.py` 新增了 5 个 `bc_absorbed` challenger 的 `FormulaBase` 适配器，并在 `backend/services/formula_engine/bootstrap.py` 中纳入 live `REGISTRY`，让 `build_formula_signals_history.py` 能通过同一条信号管线生成它们的 `fact_technical_trigger` 行。
- 已执行 `PYTHONPATH=backend python backend/scripts/build_formula_signals_history.py --formula gs_raw_buy gs_pullback_confirm ma_base_breakout activity_breakout volume_base_breakout`，本轮回填写入 `704,661` 条信号和 `35` 行 `mart_formula_horizon_evidence`。
- 最新 `backend/scripts/audit_stage_opt_candidate_supply.py --format json` 显示：`live_formula_count=12`，`raw_signal_rows=7,127,177`，`unique_keys=222,509`，`ready_keys=153,433`，`ready_coverage_pct=68.96`，`blocked_reason_counts` 仍以 `below_min_signals=69,076` 为主，但 weakest formulas 已切到 `ma_base_breakout` / `gs_pullback_confirm` / `volume_base_breakout`。
- 这一步把 stage-opt 的上游供给面真正扩宽了，但还没有结束 blocker 线程；下一步仍然是继续按 weakest formulas 和 `need_027` blocked-gap triage 往前推，而不是把它当成终局。
- 2026-06-03 进一步把 live 口径收紧成 18 条公式：`pullback_doji` 与 `monthly_stage2_daily_volume_confirm` 保留为研究候选，不再进入默认 live 回填；`backend/scripts/build_formula_signals_history.py` 现在默认只跑 `bootstrap.LIVE_FORMULA_IDS`，并在默认回填时顺手清掉 held-back 两条的历史 `fact_technical_trigger` / `mart_formula_horizon_evidence` 行。最新 full-history audit 刷成 `raw_signal_rows=5,901,698 / filtered_signal_rows=4,717,309 / unique_keys=315,814 / ready_keys=213,990 / ready_coverage_pct=67.76 / below_min_signals=101,824`，`weakest_formula_ids` 变成 `ma_base_breakout` / `gs_pullback_confirm` / `volume_base_breakout`；这比 64.71% 的扩展前结果更好，但仍低于 68.96% 的旧 baseline，所以 stage-opt 主线仍是 `P1 / upstream_candidate_supply`，只是 live surface 更干净了。

## 2026-06-03 — bestchoice blueprint intake

- 记录一份新的系统蓝图：以 `CatBoost` 特征矩阵 + 贝叶斯后验更新 + `Optuna TPE` 做参数寻优，执行层用 `ATR`、时间止损和分数凯利控制风险，底座是本地 `DuckDB` / `VectorBT`，原始数据湖走 `httpx` -> JSON -> `Parquet`，重回测和大规模寻优先通过 `experiment_jobs` 建模，再接 Modal provider adapter。
- 这个蓝图的推荐推进顺序是：`1)` 先把数据管道和 truth-source 收敛好，`2)` 再跑通本地研究闭环（特征、CatBoost、VectorBT、Bayesian/Optuna），`3)` 最后再接 provider adapter 编排。数据层先行，因为没有稳定真相源，后面的模型和风控都只是漂浮的算术。
- `bestchoice` 相关的公式 / 选股事项先记为后续推进项，不和当前的 `stage-opt` / `need_027` blocker 线程混在一起；等当前 blocker 线自然停住时，再切过来做。

## 2026-06-03 — data-view render hot paths flattening

- `assets/js/data-view.js` 的几个渲染热点继续收口：`renderHealthHeatmap()`、`renderSourcePriority()`、`renderFallbackPanel()`、`renderDriftQueue()`、`renderCapTable()`、`renderStepGrid()` 以及 `startPolling()` 的日志聚合都从 `.map().join()` / `forEach()` 改成了直线型 `for...of` / 字符串拼接，保持输出语义不变，只收紧回调型热路径。
- 验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_data_view.py --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/data-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步。
- 这次只是在前端 cockpit 里继续削掉一个明显的渲染带宽点；全仓 broad scan 仍然有历史 HIGH 残余，后续还是按 `assets/js/data-view.js` / `assets/js/settings-view.js` / `assets/js/signal-adapter.js` / `assets/js/stock-view.js` 的热路径继续收口，而不是把这次当成全局完结。

## 2026-06-03 — data-view render hot paths second pass

- `assets/js/data-view.js` 的第二轮收口把剩余的回调型渲染又压了一层：`buildRouteSearchText()`、`buildSourceCardsModel()`、`buildHealthHeatmapModel()`、`buildSourcePriorityModel()`、`buildLinkOverviewModel()`、`renderLinkOverview()`、`renderSourceCards()`、`renderAuditResults()`、`renderRoutesTable()` 和 `_setUpdateButtonsBusy()` 现在也都收成直线型循环/拼接，去掉了残余的 `.map()` / `.forEach()` 热点。
- 验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_data_view.py --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/data-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步。
- 这轮的意义是把 `data-view` 从“broad scan 常驻高热点文件”往“局部可收口文件”推了一步；全仓 broad scan 仍会保留别的历史 HIGH，后续继续按 `stage-opt / need_027` 主线和剩余前端热路径并行推进。


## 2026-06-03 — stock-view index consolidation

- `assets/js/stock-view.js` 新增 `buildStockIndex()`，把筛选选项收集、`screeningMap` / `turtleMap` 计数、覆盖股票集合与股票索引收成一次遍历，并对空输入做兜底；`renderFilterBar()` / `renderTopkSummary()` 直接复用这个索引，不再分别扫 `byStock`。`backend/tests/contract/test_stock_view.py` 新增 helper 行为回归，`backend/tests/contract/test_workbench_frontend_contract.py` 补 export / wiring contract。验证：`node --check assets/js/stock-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_stock_view.py` 3 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/stock-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步，`git diff --check` PASS；但全仓 broad scan 仍为 WARN / 80 high findings，残余继续集中在 `assets/js/app.js` / `assets/js/settings-view.js` / `assets/js/signal-adapter.js` / `assets/js/stock-view.js` 的历史 heuristic 行，后续继续按热路径收口。


## 2026-06-03 — data-view route search cleanup

- `assets/js/data-view.js` 里的 `buildAssetHealthIndex()` / `buildAuditResultsModel()` / `buildRoutesTableModel()` 现都改成直线型 `for...of` 收口，`buildRoutesTableModel()` 还把 route 过滤字段收成一次性 `buildRouteSearchText()` 搜索串，避免每条 route 再跑一层 `some()` 回调；`backend/tests/contract/test_data_view.py` 新增 `protocol` / `raw_table` filter 回归，锁住多字段过滤语义。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_data_view.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/data-view.js --format markdown` targeted scan 无明显热点，`codegraph sync .` 已同步；但全仓 broad scan 仍为 WARN / 80 high findings，主要残余还在 `assets/js/app.js` / `assets/js/data-view.js` / `assets/js/settings-view.js` / `assets/js/stock-view.js` / `assets/js/signal-adapter.js` 的历史热点，后续继续按热路径收口。

## 2026-06-03 — signal-adapter grouping cleanup

- `assets/js/signal-adapter.js` 里 `eventToView()` 的 institutionType 回退收成 `extractInstitutionType()`，`aggregateByStockViews()` 改成单次 group state + 线性 action 分桶 + 单次公告日排序；`backend/tests/contract/test_signal_adapter.py` 新增 `fetchSignals()` mock 回归，锁住 stock 桶顺序、`topEvent`、`events` / `timelineEvents` 顺序和 fallback 语义。验证：`node --check assets/js/signal-adapter.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_signal_adapter.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_signal_adapter.py backend/tests/contract/test_workbench_frontend_contract.py` 4 passed，`python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey/assets/js/signal-adapter.js --format markdown` 无明显热点；但全仓 broad scan 仍会把 `assets/js/app.js` / `assets/js/data-view.js` / `assets/js/settings-view.js` / `assets/js/stock-view.js` 以及 `signal-adapter.js` 的部分 heuristic 行继续列为高热点，后续继续按热路径收口。

## 2026-06-03 — app navigation helper extraction

- `assets/js/app.js` 里的 group / view / ETF tab / stock tab active-state 切换现统一走 `setActiveState()`，点击绑定统一走 `bindNodeClicks()`，把 3 处顶层 `querySelectorAll(...).forEach(...)` 绑定和多处 `classList.toggle('active')` 收拢成 2 个纯 helper；`backend/tests/contract/test_workbench_frontend_contract.py` 已补 helper presence 与旧直接绑定模式不回流的回归。验证：`node --check assets/js/app.js` PASS，`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py` 2 passed，`analyze_complexity.py` 对 `assets/js/app.js` 无明显热点；最新 `scripts/chunkyctl doctor --fast` 仍为 WARN，complexity high findings 80，主要集中在 `assets/js/data-view.js` 41 / `assets/js/stock-view.js` 13 / `assets/js/settings-view.js` 12 / `assets/js/signal-adapter.js` 9 / `assets/js/app.js` 5。

## 2026-06-03 — chunkyctl action detail suffix helper extraction

- `backend/scripts/chunkyctl.py` 里的 `_stage_opt_candidate_action()` / `_need_coverage_blocked_action()` 现共用 `_format_action_detail_suffix()`，把两处重复的 details 后缀拼装收成一个纯 helper；`stage-opt` / `need_027` 的 next-action 文本和现有回归测试语义保持不变。`backend/tests/scripts/test_chunkyctl.py` 已补 helper 直测。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/scripts/chunkyctl.py --scope backend/tests/scripts/test_chunkyctl.py` PASS，`python -m py_compile backend/scripts/chunkyctl.py backend/tests/scripts/test_chunkyctl.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_chunkyctl.py` 28 passed，`analyze_complexity.py` 对 `backend/scripts/chunkyctl.py` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — audit_tdx_data_need_coverage helper regression expansion

- `backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 现直接覆盖 `_source_registration_summary()` 和 `_blocked_need_summary()` 两个新 helper，补强了 `need_027` 的 source registration / failure queue 字段映射回归；`_summarize_need_gaps()` 的外层集成测试仍保留，输出语义不变。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/scripts/audit_tdx_data_need_coverage.py --scope backend/tests/scripts/test_audit_tdx_data_need_coverage.py` PASS，`python -m py_compile backend/scripts/audit_tdx_data_need_coverage.py backend/tests/scripts/test_audit_tdx_data_need_coverage.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 24 passed，`analyze_complexity.py` 对 `backend/scripts/audit_tdx_data_need_coverage.py` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — audit_tdx_data_need_coverage blocked-need summary helper extraction

- `backend/scripts/audit_tdx_data_need_coverage.py` 里的 `_summarize_need_gaps()` 现把 blocked need 字典拼装收成 `_blocked_need_summary()` 纯 helper，`need_027` 相关的 source registration / failure queue 结构保持不变，`blocked_needs` 输出没有改字段。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/scripts/audit_tdx_data_need_coverage.py --scope backend/tests/scripts/test_audit_tdx_data_need_coverage.py` PASS，`python -m py_compile backend/scripts/audit_tdx_data_need_coverage.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 22 passed，`analyze_complexity.py` 对 `backend/scripts/audit_tdx_data_need_coverage.py` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — audit_tdx_data_need_coverage source-registration helper extraction

- `backend/scripts/audit_tdx_data_need_coverage.py` 里的 `_summarize_need_gaps()` 现把 blocked need 的 source registration 摘成 `_source_registration_summary()` 纯 helper，`preferred/fallback` 的 family、注册状态、capability 列表和 `individual_fund_flow` 支持判断都统一由这个 helper 生成，`need_027` 输出结构不变。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/scripts/audit_tdx_data_need_coverage.py --scope backend/tests/scripts/test_audit_tdx_data_need_coverage.py` PASS，`python -m py_compile backend/scripts/audit_tdx_data_need_coverage.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_audit_tdx_data_need_coverage.py` 22 passed，`analyze_complexity.py` 对 `backend/scripts/audit_tdx_data_need_coverage.py` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — chunkyctl need_027 blocker action helper extraction

- `backend/scripts/chunkyctl.py` 里的 `_next_actions()` 现把 stage-opt / need_coverage 的 next-action 文本组装分别收成 `_stage_opt_candidate_action()` 和 `_need_coverage_blocked_action()`，主循环更短，但输出语义不变；`backend/tests/scripts/test_chunkyctl.py` 仍直接覆盖 stage-opt 推荐和 `need_027` blocked-gap 文本。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/scripts/chunkyctl.py --scope backend/tests/scripts/test_chunkyctl.py` PASS，`python -m py_compile backend/scripts/chunkyctl.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_chunkyctl.py` 27 passed，`analyze_complexity.py` 对 `backend/scripts/chunkyctl.py` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — settings-view data source params model extraction

- `assets/js/settings-view.js` 里的数据源参数卡已收成 `buildDataSourceParamsModel()` 纯 helper，`renderDataSourceParams()` 现在只消费 model；row normalization 把 `sources` 统一成 `rows`、`sourceCount`、`totalCapabilities`、`isEmpty`，并把 `capabilities` 缺失作为空数组兜底，不再直接在 render 里读 raw payload。`backend/tests/contract/test_settings_view.py` 已补 helper 导出与输入归一回归。验证：`node --check assets/js/settings-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_settings_view.py backend/tests/contract/test_workbench_frontend_contract.py` 4 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对 `assets/js/settings-view.js` 无明显热点。

- `assets/js/settings-view.js` 里的 about 区块也已收成 `buildAboutModel()` 纯 helper，`renderAbout()` 现在只消费 model；row normalization 把 `status` / `enabled_modules` 统一成 `backendLabel`、`enabledModules`、`isHealthy`，并把空响应兜底成 `异常`。`backend/tests/contract/test_settings_view.py` 已补 helper 导出与输入归一回归。验证：`node --check assets/js/settings-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_settings_view.py backend/tests/contract/test_workbench_frontend_contract.py` 5 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对 `assets/js/settings-view.js` 无明显热点。

## 2026-06-03 — data-view routes table single-pass cleanup

- `assets/js/stock-view.js` 里的信号证据链继续收口，`renderTabEvidence()` 现在把 follow/watch 事件改成单次扫描，不再先 `filter()` 两遍再拼接；`backend/tests/contract/test_workbench_frontend_contract.py` 已补 `stock-view` 不应回到双 filter 版本的回归。验证：`node --check assets/js/stock-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_signal_adapter.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对 `assets/js/stock-view.js` 无明显热点。

- `assets/js/stock-view.js` 里的股票筛选继续收口，`collectOptions()` / `applyFilters()` 现在直接读 `topEvent.institutionType`，不再重复从 `ruleChecks` 扫 `inst_type`；`backend/tests/contract/test_signal_adapter.py` 已补 `eventToView` 通过 `rule_breakdown.checks` 归一出 `institutionType` 的回归。验证：`node --check assets/js/signal-adapter.js assets/js/stock-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_signal_adapter.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对 `assets/js/stock-view.js` 无明显热点。

- `assets/js/data-view.js` 里的数据视图继续收口，`renderSourceCards()` / `renderRoutesTable()` / `renderDriftQueue()` / `renderStepGrid()` 现在都改成事件委托，不再在每次渲染后 `querySelectorAll(...).forEach(...)` 逐个绑定按钮；`_setUpdateButtonsBusy()` 也改成对 step-grid 容器统一切换 busy 样式。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/data-view.js` 里的 `buildRoutesTableModel()` 已改成单次扫描，`null` 路由会被直接跳过，`backend/tests/contract/test_data_view.py` 已补 `null` 路由不影响 route model 的回归。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — app.js turtle/helper dead-code cleanup

- `assets/js/app.js` 里的 `turtleSystemLabel` / `turtleStateMeta` / `turtleStateTag` / `instLink` / `evTag` 死 helper 已删除，`backend/tests/contract/test_workbench_frontend_contract.py` 已补这组 dead helper 不应回流的回归。验证：`node --check assets/js/app.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_data_view.py` 6 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — data-view fallback/drift/capability single-pass cleanup

- `assets/js/data-view.js` 里的数据视图继续收口，`buildFallbackPanelModel()`、`buildDriftQueueModel()`、`buildCapabilityTableModel()` 现在都改成单次扫描，不再在 `filter()` / `map()` 链里重复遍历；`backend/tests/contract/test_data_view.py` 已补 null / invalid 输入不影响输出形状的回归。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — data-view link overview manual count cleanup

- `assets/js/data-view.js` 里的数据链路总览继续收口，`buildLinkOverviewModel()` 现在把 `manual` 的 keep/watch/drop 计数改成单次扫描，不再重复 `filter()` 三次；`backend/tests/contract/test_data_view.py` 已补 `manual` 无效 decision 不影响计数的回归。验证：`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py` 6 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — widget format utils analysis/workbench-health 扩展

- `assets/js/app.js` 里的 `loadIndustryOverviewSummary` / `resolveStockSummary` 死 helper 已删除，`backend/tests/contract/test_workbench_frontend_contract.py` 已补这两个 dead wrapper 不应回流的回归。验证：`node --check assets/js/app.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_settings_view.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/settings-view.js` 里的 `versions` 死字段已删除，schema versions model 现在只保留层级计数与漂移/对齐分组；`backend/tests/contract/test_settings_view.py` 已补 `versions` 不应再出现在 schema model 的回归。验证：`node --check assets/js/settings-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_settings_view.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/settings-view.js` 里的 `summary` 死字段已删除，schema versions 现在只暴露由版本列表聚合出来的层级计数与漂移分组；`backend/tests/contract/test_settings_view.py` 已补 `summary` 不应再出现在 schema model 的回归。验证：`node --check assets/js/settings-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_settings_view.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/signal-adapter.js` 里的旧 `aggregateByStock()` wrapper 已删除，股票视图现在只消费 `fetchSignals()` 返回的 `byStock` 聚合结果，`backend/tests/contract/test_signal_adapter.py` 也改成锁定 `eventToView` 映射与 dead wrapper 清理；`backend/tests/contract/test_workbench_frontend_contract.py` 继续确保信号适配层契约文件仍在预期加载顺序内。验证：`node --check assets/js/signal-adapter.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_signal_adapter.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/signal-params.js` 里的本地 `fmtWinRate` 已删除，cohort inline 里的胜率展示现在直接复用 `WidgetFormatUtils.formatWinRate()`；`backend/tests/contract/test_widget_format_utils.py` 已补 `SignalParamsWidget` 的共享 formatter / local formatter contract，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 `format-utils.js` 在 `signal-params.js` 之前加载。验证：`node --check assets/js/widgets/signal-params.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/screening-panel.js` 里的本地 `fmt` 已删除，选股扫描卡片现在直接复用 `WidgetFormatUtils.formatNumber()`；`backend/tests/contract/test_widget_format_utils.py` 已补 `ScreeningPanelWidget` 的共享 formatter / local formatter contract，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 `format-utils.js` 在 `screening-panel.js` 之前加载。验证：`node --check assets/js/widgets/screening-panel.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/etf-list.js` 里的本地 `etfNum` 已删除，ETF 全量筛选页现在直接复用 `WidgetFormatUtils.formatNumber()`；`backend/tests/contract/test_widget_format_utils.py` 已补 `ETFListWidget` 的共享 formatter / local formatter contract，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 `format-utils.js` 在 `etf-list.js` 之前加载。验证：`node --check assets/js/widgets/etf-list.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `backtest-panel` / `etf-strategy-compare` 扩展，`backtest-panel` 的 win rate 展示和 `etf-strategy-compare` 的 win rate / 回撤展示现在都复用 `WidgetFormatUtils.formatWinRate()` / `formatPercent()`；`backend/tests/contract/test_widget_format_utils.py` 已补 helper export / widget source contract，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 `format-utils.js` 在这些 widget 之前加载。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/backtest-panel.js assets/js/widgets/etf-strategy-compare.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/etf-opportunity.js` 里的本地 `etfNum` / `scoreNum` / `signedPct` / `pct` 已删除，机会发现页现在直接复用 `WidgetFormatUtils.formatNumber()` / `formatPercent()`；`backend/tests/contract/test_widget_format_utils.py` 已补 `ETFOpportunityWidget` 的共享 formatter / local formatter contract，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 `format-utils.js` 在 `etf-opportunity.js` 之前加载。验证：`node --check assets/js/widgets/etf-opportunity.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `screening-panel` / `cohort-card` / `backtest-panel` 扩展，三个 widget 现在都复用 `WidgetFormatUtils`，并补了 Node `globalThis` 导出回归；`index.html` 也把 `format-utils.js` 提前到这组三个 widget 之前加载。`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 `format-utils.js` 在这些 widget 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/signal-params.js assets/js/widgets/cohort-card.js assets/js/widgets/backtest-panel.js assets/js/widgets/screening-panel.js assets/js/widgets/multidim-badge.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `signal-params` / `cohort-card` / `backtest-panel` 扩展，三个 widget 现在都复用 `WidgetFormatUtils`，并补了 Node `globalThis` 导出回归；`index.html` 也把 `format-utils.js` 提前到这组三个 widget 之前加载。`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 `format-utils.js` 在这些 widget 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/signal-params.js assets/js/widgets/cohort-card.js assets/js/widgets/backtest-panel.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `topk-strip` 扩展，`topk-strip` 的 `fmtScore` 现在也复用 `WidgetFormatUtils`，并补了 Node 导出与源码契约回归，保证它不是只在浏览器里偶然可用；`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 `format-utils.js` 在 `topk-strip` 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/topk-strip.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `institution-scorecard` 扩展，机构评分卡里的 `fmtNum` / `fmtScore` / `fmtGain` 现在都复用 `WidgetFormatUtils`，去掉各自的重复 local formatter 逻辑；`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_institution_scorecard_widget.py` 也补了 format-utils 依赖加载回归，`backend/tests/contract/test_workbench_frontend_contract.py` 继续保证 format-utils 在机构评分卡之前加载。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/institution-scorecard.js assets/js/widgets/etf-analysis.js assets/js/widgets/workbench-health.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_institution_scorecard_widget.py` 4 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `etf-analysis` / `workbench-health` 扩展，`etf-analysis` 里的 `fmtNum` / `fmtSignedPct` / `etfNum` 和 `workbench-health` 里的 `fmt` 现在都复用 `WidgetFormatUtils`，去掉各自的重复 local formatter 逻辑；`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 format-utils 在这些 widget 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/etf-analysis.js assets/js/widgets/workbench-health.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — widget format utils ETF list/workbench 扩展

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 `etf-list` / `etf-workbench` 扩展，两个 widget 现在也统一复用 `WidgetFormatUtils.formatNumber()` / `formatPercent()`，去掉各自的重复 local formatter 逻辑；`backend/tests/contract/test_widget_format_utils.py` 继续负责 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 `format-utils.js` 在这两个 widget 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/etf-list.js assets/js/widgets/etf-workbench.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — widget format utils ETF 扩展

- `assets/js/widgets/format-utils.js` 的共享格式化 helper 继续向 ETF widget 扩展，`etf-sector-rotation` / `etf-opportunity` / `etf-strategy-compare` 现在也统一复用 `WidgetFormatUtils.formatNumber()` / `formatPercent()`，去掉各自的重复 local formatter 逻辑；`index.html` 的加载顺序已经覆盖这些 widget，`backend/tests/contract/test_widget_format_utils.py` 补了 helper export / widget export contract，`backend/tests/contract/test_workbench_frontend_contract.py` 也补了 format-utils 在 ETF widget 之前加载的回归。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/etf-sector-rotation.js assets/js/widgets/etf-opportunity.js assets/js/widgets/etf-strategy-compare.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_widget_format_utils.py backend/tests/contract/test_model_monitor_widget.py backend/tests/contract/test_workbench_frontend_contract.py` 4 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对三个改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — widget format utils shared helper

- `assets/js/widgets/format-utils.js` 新增共享格式化 helper，`model-monitor` 和 `grid-optimizer` 现在统一复用 `WidgetFormatUtils.formatNumber()` / `formatPercent()`，去掉各自的重复 local formatter 逻辑；`index.html` 已把 helper 放在对应 widget 之前加载，`backend/tests/contract/test_model_monitor_widget.py` 也补了 helper export / format contract。验证：`node --check assets/js/widgets/format-utils.js assets/js/widgets/model-monitor.js assets/js/widgets/grid-optimizer.js assets/js/app.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_model_monitor_widget.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`audit_test_tool_health.py` PASS，`analyze_complexity.py` 对两个改动文件无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — dead app.js wrapper cleanup

- `assets/js/app.js` 里的薄 wrapper `loadStocks()` / `loadResearch()` 已删除，`showView()` 直接分发到 `window.StockView.load()/reload()` 与 `loadInstScorecard()`，避免重复入口和维护歧义；`backend/tests/contract/test_workbench_frontend_contract.py` 已补这两条 wrapper 不应回流的回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/app.js --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`node --check assets/js/app.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py` 2 passed，`analyze_complexity.py` 复扫后 `assets/js/app.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — stock report widget removal

- `assets/js/widgets/stock-report.js` 与其专用契约测试已删除，`index.html` / `assets/js/app.js` 里的 stock-report 脚本加载与 `StockReportWidget` 入口也一并清理；`backend/tests/contract/test_workbench_frontend_contract.py` 已补“stock-report 不应回流到 app.js / index”的回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/app.js --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`node --check assets/js/app.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`analyze_complexity.py` 复扫后 `assets/js/app.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — rank matrix cache rows model extraction

- `assets/js/workbench-view.js` 里的 rank matrix cache 页已继续收成页级 model：`buildRankMatrixCacheModel()` 现在把 `summary` / `latest_benchmarks` / `cache_entries` 规范成 `summaryMetrics`、`benchmarkRows`、`cacheEntryRows`、`isEmpty`，`renderRankMatrixCache()` 只消费 model；`backend/tests/contract/test_workbench_frontend_render_smoke.py` 已补这组 model 的稳定性回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`node --check assets/js/workbench-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 23 passed，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — stability context rows model extraction

- `assets/js/workbench-view.js` 里的稳定性上下文已继续收成页级 model：`buildStabilityContextModel()` 现在把 `summaries` / `diagnostics` 进一步规范成 `summaryRows`、`diagnosticRows`、`summaryCount`、`diagnosticCount`、`isEmpty`，`renderStabilityContext()` 只消费 model；`buildResearchModel()` / `buildChampionModel()` 也都直接消费这个 model。`backend/tests/contract/test_workbench_frontend_contract.py` 和 `backend/tests/contract/test_workbench_frontend_render_smoke.py` 已补 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`node --check assets/js/workbench-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 23 passed，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — stability context model extraction

- `assets/js/workbench-view.js` 里的稳定性上下文已收成 `buildStabilityContextModel()` 纯 helper，`buildResearchModel()` / `buildChampionModel()` 现在都直接消费该 model，`renderResearch()` / `renderChampion()` 只显示 `runId` 与归一后的 summaries/diagnostics；`backend/tests/contract/test_workbench_frontend_contract.py` 和 `backend/tests/contract/test_workbench_frontend_render_smoke.py` 已补 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`node --check assets/js/workbench-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 23 passed，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — data-view source cards detail cache

- `assets/js/data-view.js` 里的数据源卡片详情现在预先缓存成 `detailRowsHtml`，`renderSourceCards()` 只挂载 source model，`toggleDetail()` 直接从 `_state.sourceCardModelByName` 读取，避免每次点击都重新 `find/map` 重算 capability rows；`backend/tests/contract/test_data_view.py` 已补 source-cards model 的稳定性回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/data-view.js --scope backend/tests/contract/test_data_view.py` PASS，`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py` 4 passed，`codegraph sync .` 已同步。


## 2026-06-03 — data-view link overview model extraction

- `assets/js/data-view.js` 里的数据链路总览已收成 `buildLinkOverviewModel()` 纯 helper，`renderLinkOverview()` 现在只消费 model；row normalization 把 `summary` / `by_layer` / `tdxValidation` / `sourceHealth` 统一成 `snapshotAt`、`keep`、`watch`、`drop`、`pit`、`sourceLabel`、`nodes`，并把 `statusFromCounts` 的决策也前移到 model 层。`backend/tests/contract/test_data_view.py` 已补 link overview 的稳定性回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/data-view.js --scope backend/tests/contract/test_data_view.py` PASS，`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py` 4 passed，`analyze_complexity.py` 复扫后 `assets/js/data-view.js` / `assets/js/stock-view.js` 无新增明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — stock timeline ordering delegation

- `assets/js/signal-adapter.js` 里的股票事件聚合继续模型化，`aggregateByStockViews()` 现在除了 `events` 还预先产出 `timelineEvents`，把 `renderTabTimeline()` 的日期排序从 UI 渲染路径挪到共享数据层；`assets/js/stock-view.js` 已直接消费 `s.timelineEvents || s.events`。`backend/tests/contract/test_signal_adapter.py` 已补 timeline 顺序回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/signal-adapter.js --scope assets/js/stock-view.js --scope backend/tests/contract/test_signal_adapter.py` PASS，`node --check assets/js/signal-adapter.js assets/js/stock-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_signal_adapter.py` 1 passed，`analyze_complexity.py` 复扫后 `assets/js/signal-adapter.js` / `assets/js/stock-view.js` 无新增明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — data-view cockpit panel model extraction

- `assets/js/data-view.js` 里的数据视图 cockpit 面板继续收口成纯 model：新增 `buildHealthHeatmapModel()`、`buildSourcePriorityModel()`、`buildFallbackPanelModel()`、`buildDriftQueueModel()`、`buildCapabilityTableModel()`，对应的 `renderHealthHeatmap()` / `renderSourcePriority()` / `renderFallbackPanel()` / `renderDriftQueue()` / `renderCapTable()` 现在只消费 model；`backend/tests/contract/test_data_view.py` 已补这组 builder 的稳定性回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/data-view.js --scope backend/tests/contract/test_data_view.py` PASS，`node --check assets/js/data-view.js` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py` 4 passed，`analyze_complexity.py` 复扫后 `assets/js/data-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — pipelines model extraction

- `assets/js/workbench-view.js` 里的 pipelines 页已收成 `buildPipelinesModel()` 纯 helper，`renderPipelines()` 现在只消费 model；row normalization 把 `recent` / `slowest` / `blockers` 统一到 page-level model，并保留 `status_counts`、`latest`、`slowestName`、`slowestDurationS`、`isEmpty`。workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 22 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — research model extraction

- `assets/js/workbench-view.js` 里的研究页已收成 `buildResearchModel()` 纯 helper，`renderResearch()` 现在只消费 model；row normalization 把 `research_schedule` / `model_stability` / `ranker_profiles` / `ranker_policy` / `rank_matrix_cache` / `stability_context` / `stock_horizon_profile` / `shareholder_plan_*` / `temporal_synergy` / `industry_pit` 统一到 page-level model，workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 21 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — data-view audit model cleanup

## 2026-06-03 — today signal cache model extraction

- `assets/js/workbench-view.js` 里的今日信号快照已收成 `buildTodaySignalCacheModel()` 纯 helper，`renderTodaySignalCache()` 现在只消费 model；row normalization 把 `today_signal_cache` 统一成 `status`、`statusTone`、`signalCount`、`freshnessDays`、`sourceMaxNoticeDate`、`currentSourceMaxNoticeDate`、`builtAt`、`error`、`step`、`isEmpty`，并把 dataSources 页头部的信号快照统计也切到 model 口径。`buildDataSourcesModel()` 也开始返回 `signalCacheModel`，workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 20 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — processing monitor model extraction

- `assets/js/workbench-view.js` 里的处理工具监控 / 拒绝原因块已收成 `buildProcessingMonitorModel()` 纯 helper，`renderProcessingMonitorTable()` 现在只消费 model；row normalization 把 `processing_monitor` 统一成 `recentRuns`、`reasonCounts`、`totalRejectedRows`、`runCount`、`recentRunCount`、`reasonCount`、`isEmpty`，并把数据源页头部清洗拒绝统计也切到 model 口径。`buildDataSourcesModel()` 也开始返回 `processingMonitor` model，workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 19 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — asset governance table model extraction

- `assets/js/workbench-view.js` 里的资产用途与质量契约表已收成 `buildAssetGovernanceTableModel()` 纯 helper，`renderAssetGovernanceTable()` 现在只消费 model；row normalization 把 `asset_health.items` 统一成 `rows`、`rowCount`、`isEmpty`，并保留原有 hidden_internal 过滤、quality gate 排序与 80 行上限语义。`buildDataSourcesModel()` 也开始返回 `assetGovernanceTable` model，workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 18 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — tdx server health model extraction

- `assets/js/workbench-view.js` 里的 TDX K 线服务器健康块已收成 `buildTdxServerHealthModel()` 纯 helper，`renderTdxServerHealthTable()` 现在只消费 model；row normalization 把 `servers` / `summary` 统一成 `capabilities`、`totals`、`rows`、`rowCount`、`updatedAt`、`isEmpty`，并兼容 `healthy_count` / `timeout_server_count` 与 success/fail/timeout 汇总口径。`buildDataSourcesModel()` 也开始返回 `tdxServerHealth` model，workbench contract / smoke test 已补上 helper 导出与输入归一回归。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/workbench-view.js --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_workbench_frontend_render_smoke.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_workbench_frontend_render_smoke.py` 17 passed，`node --check assets/js/workbench-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/workbench-view.js` 无明显热点，`codegraph sync .` 已同步。

- `assets/js/data-view.js` 里的审计结果拆分已收成 `buildAuditResultsModel()` 纯 helper，`renderAuditResults()` 只消费 model；`null` 审计行会被安全忽略，issues / okRows / n_error / n_warn / n_ok / n_tables / run_at 都在单一 model 层归一。新增 `backend/tests/contract/test_data_view.py` 锁住 helper 行为，并把 `backend/tests/contract/test_workbench_frontend_contract.py` 里的 data-view 契约收紧到 `buildAuditResultsModel(`。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope assets/js/data-view.js --scope backend/tests/contract/test_data_view.py --scope backend/tests/contract/test_workbench_frontend_contract.py --scope backend/tests/contract/test_settings_view.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_data_view.py backend/tests/contract/test_workbench_frontend_contract.py backend/tests/contract/test_settings_view.py` 4 passed，`node --check assets/js/data-view.js` PASS，`analyze_complexity.py` 复扫后 `assets/js/data-view.js` 无明显热点，`codegraph sync .` 已同步。

## 2026-06-03 — ETF workbench widget extraction

- `assets/js/app.js` 里的 ETF 工作台主实现已抽到 `assets/js/widgets/etf-workbench.js`，`app.js` 现在只保留 `loadEtfWorkbench()` 薄 wrapper + 依赖注入；widget 自带 `mountEtfWorkbench` / `buildWorkbenchHtml` / `etfNum`，把 ETF 工作台总览、数据源与覆盖范围、ETF 结构与功能入口收口到单独模块，`index.html` 已调整为先加载 widget 再加载 `app.js`。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_etf_workbench_widget.py --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_etf_workbench_widget.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`node --check assets/js/widgets/etf-workbench.js assets/js/app.js` PASS，`analyze_complexity.py` 复扫后新 widget 无明显热点，`codegraph sync .` 已同步。
## 2026-06-03 — workbench health widget extraction

- `assets/js/app.js` 里的工作台健康 / 连通性实现已抽到 `assets/js/widgets/workbench-health.js`，`app.js` 现在只保留 `refreshWorkbenchHealthBar()` / `refreshNetwork()` 薄 wrapper + 依赖注入；widget 自带 `refreshWorkbenchHealthBar` / `refreshNetwork` / `normalizeSourceName` / `setSourcePill`，把健康卡、事件成熟度、机构概览、信号概览、管线状态与 network pills 收口到单独模块，`index.html` 已调整为先加载 widget 再加载 `app.js`。验证：`PYTHONPATH=backend python backend/scripts/audit_test_tool_health.py --scope backend/tests/contract/test_workbench_health_widget.py --scope backend/tests/contract/test_workbench_frontend_contract.py` PASS，`PYTHONPATH=backend python -m pytest -q backend/tests/contract/test_workbench_health_widget.py backend/tests/contract/test_workbench_frontend_contract.py` 3 passed，`node --check assets/js/widgets/workbench-health.js assets/js/app.js` PASS，`analyze_complexity.py` 复扫后新 widget 无明显热点，`codegraph sync .` 已同步。
## 2026-05-27 — 架构重构执行计划 (已归档)

> 全文移至 `ledger_archive_202605.md` (2026-06-11 文档治理)。
> 该计划已被 goal.md 取代; execution authority = goal.md + docs/implementation_plan.md。

## 2026-06-14 — Alpha 验证程序 S0 完成 (实验台执行器 config 驱动重建)

地基-reset 后 spec §4「复用实验台」前提已死 (核证: optimization 中央层 / plan_validator /
walk_forward runner / backtest / chunkyctl.py jobs 派发器全被 reset 删, experiment_jobs.py 契约
loader 误删致 4 处 `import services.experiment_jobs` 悬空崩)。故 S0 = config 驱动**新建**最小脚手架,
不复活 god-dispatcher (它派发的引擎本身已删 = 想象的复杂度, architect rule6)。

交付 (全 local 秒级, 零 Optuna/Modal 花费):
- `experiment_jobs.py` 契约 loader 忠实恢复 (337L 薄/纯 yaml 校验/误删) — 修复 4 处悬空 import。
- `consumer_alpha_matrix.yaml` (数据x消费者) 矩阵: 6 候选 (forecast/income/cyq/kpl/holder/sw) +
  映射铁律 (event/fundamental/chip/infra→feature_ic, technical→formula_signal) → 枚举 7 cell。
- `experiment_jobs.yaml` 加 `consumer_alpha_validation` family (required_gates: data_health_snapshot/
  pit_audit/leakage_consumer_scan; artifact_contracts 指向 4 留档表)。
- `experiment_consumer_alpha_validation.py` 执行器: gate-before-run (plan().blocked_reasons) →
  枚举 cell → S0 dry 空矩阵 (不写假 IC, measured-not-estimated) → 写 verdict/lineage/pit_audit
  留档 + verdict JSON 落 analysis/。死亡条款守: 矩阵轴走 config (判断死) / prereg_hash+--check-prereg
  (谄媚死) / PIT 每步落档 (泄漏死) / dry 不造假 (估计死)。
- 验证: dry 骨架实跑 7 cell 枚举正确 (kpl 双消费者路由), 留档表写 verdict 1/pit_audit 4/lineage 1/
  ic_scan 0; 10 单测 + moth `consumer-alpha-axes-in-config-not-code` 断言 + CI offline 64 passed 全绿;
  smoke 数据已清 (实 store S1+ 才落真数据)。
- 下一步 (P1): L0 裸K线基准 — 需重建 OOS walk-forward IC 计算 (reset 删了 walk_forward runner),
  仅 OHLCV 派生基准跑过执行器作标尺。
- 2026-06-19 退役 ensemble 污染孤儿 (FEATURE_MAP code/table lens 审计旁支): `strategy_ensemble.py`
  (P3.11 多策略 ensemble builder) 自创建 (51734fa9) 起从未接入 live 链 — 唯一引用是它自暴露的两个
  FastAPI 端点 (POST /ensemble/run + GET /ensemble/topk, 端点簇外 0 调用); 输出表
  mart_ensemble_signals 已在 06-14 reset 物删 (manifest L270 备案, 5 库确认 0 残留); 4 alpha 源里
  mart_stock_trend(0.40)/fact_risk_factors(0.25) 已被 reset drop, 仅 aif10 两源 (task#37 待退役) 尚存。
  审计法: 多 lens workflow (调用/数据依赖/控制面/姊妹件) + 3 skeptic 对抗证伪 (frontend/调度/隐藏耦合)
  全部 found_live=False conf=high RETIRE; frontend skeptic 纠正了 find lens 假阴性 (项目确有前端
  assets/js+bestchoice, 但 ensemble 端点 0 fetch)。处置: git rm builder + 删 2 端点 + 清 schema_versions
  声明 + test_tool_registry stale 块 + check_universe_filter 2 死 exempt (build_ensemble_v4/feature_join_v5
  皆已 reset 删) + 重生 FEATURE_MAP + INDEX §3.3 与 stale row。验收: moth pass=30 fail=0, main.py 122 路由
  装载 OK, universe gate CLEAN。retired_experiments.yaml ensemble 知识条目刻意保留 (非孤儿)。
- 2026-06-19 universe 身份真相源切 tushare stock_basic + 退役 akshare dim_active (用户: "退役旧表 + 看还有哪些非tushare源"):
  根因双向 bug 实证 — 旧 get_active_universe 不与真股清单交集 → 漏入指数 000300 + 漏掉真股 8 只(akshare快照stale 24天)。
  修: 注册 stock_basic 域(tushare full_refresh list_status=L, 不设universe_filter=身份真相源) → security_master 改读
  raw_tushare_stock_basic 重建 dim(symbol/ts_code后缀权威市场/排北交所, 退役 ak.stock_info_a_code_name + _market_from_code) →
  get_active_universe 加身份交集。实测 dim 5201→5208(+8真股/-1退市000638), universe 000300出局/4978干净/test_universe 16 passed。
  全库非tushare源盘点 owner=analysis/non_tushare_source_inventory_20260619.md (akshare22/tdxhub18/aif10 13 + critic抓出tdxhub财务簇盲区,
  M2-M4 逐簇双轨退役, 不bulk-drop)。akshare stock_info_a_code_name 调用退役(其余 ~10 akshare 备援调用不动)。
- 2026-06-19 非tushare孤儿源 6/6 SAFE_TO_DROP 全退役 (~838k行, 用户"继续退役非tushare源"):
  逐表对抗验证 workflow wf_39200ec2 (11表→SAFE_TO_DROP 6/KEEP_MIGRATE 5/0误判); 关键 mythos§14 — aif10 valuation/peer
  + price_kline 被标 RETIRE 实为 LIVE(喂 v3_picture serving/regime engine)→改 KEEP_MIGRATE 避免 bulk-drop 断服务。
  退役 6 表 (commit 0c2eeb8a/bb245c57/1485c13b/a5991e90): fact_orderbook_snapshot(污染残留) + raw_fund_flow_daily(86k,→tushare moneyflow)
  + raw_aif10_holder_count(742k,→stk_holdernumber) + raw_aif10_financial_history(探针孤儿) + fact_hsgt_daily(2767,HSGT停2024)
  + financial_indicator簇3表(fact/dim/sync_state)。每张 shared-writer 删X留Y精细手术(aif10_capability_client 5→3 /
  build_akshare_panel 6→5 / financial_client caller改stub), KEEP 部分全验完好; scoring/audit dormant 引用 try/except 安全降级。
  全程 data_layer_audit PASS + moth fail=0 + 各簇 test passed。退役日志详 non_tushare_source_inventory_20260619.md §3.5。
  剩 KEEP_MIGRATE_FIRST 5表 (aif10 valuation/peer/forecast + dividend_summary + price_kline) 走 M2/M3/M4 双轨先迁后删。
