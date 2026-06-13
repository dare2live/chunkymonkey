# 总指挥统一作战图 — 策略需求 × 数据解锁 (2026-06-12, 06-13 对齐更新)

> 状态: live — §1 统一图 / §4 tushare 用尽地图 / §7 决策落账 仍 live; §0/§3 战术段已执行完 (见下)。
> **当前操作框架 = `systematic_validation_plan_20260613.md` (L0-L4 分层验证)**; 本图保留为排兵历史 +
> 数据动作清单。状态变化回写 goal.md。

> Owner: 总指挥排兵入口 (用户 2026-06-12 授权)。引用源: strategy_portfolio_20260611.md (12 周路线)
> + alpha_combo_matrix_20260612.md (16 combo)。

## 0. 现状一句话判定 (06-13 更新)

**06-12 的"三条路全堵"已全部疏通并出判决; 策略验证从"被数据堵"转入"有诚实结论, 转攻地基 + 组合"。**

- **三判决落定** (06-13): LHB 上榜即退出 = **GO** (+2.428pp/20日, 7/7 年); LF V0 概念传导 = **REJECT**
  (净超额≈0, theme/LF 证伪); 主升浪 S3 ML = **REJECT-泄漏** (follow_net_return 标签泄入, 修后 0 真 edge)。
- **泄漏治理立** (06-13): S3 暴露特征级泄漏 → `services/leakage_detect.py` 4 阶段模块 + 三道强制闸
  (注册表/--gate/safe_commit+moth) + L0-L4 系统性验证计划。live 三消费者体检全 CLEAN。
- **干净特征 alpha 实测** (06-13): 62 干净特征 0 泄漏, 有诚实弱 alpha (rz_balance 杠杆情绪 IC_IR 0.80→
  1.37@90d / lhb 反转 / 中短反转簇), 但无强单因子, 单独不够 KPI — 需多因子组合 optuna + 新特征增量。
- **筹码轴 5 combo 仍冻结** (C0 FAIL 未翻案)。
- **地基缺口 (本轮转攻)**: index_daily + index_member_all 未落库 (KPI 超额 HS300 真相源, 现走 akshare
  主从倒挂; 申万 L2 行业 PIT) = North-Star KPI 量不出的根因。Opus 硬化纲领 ~6/23 完成 (cm_takeover_audit /
  experiment_gates / 语义探针弹仓 / A4 守门有效性审计等 P0/P1 待补)。
- 北极星 KPI 四指标仍 = unknown (无可信含成本 artifact); 含成本 paper_sim 最新 2026-05-16 all_kpi=False。

## 1. 统一图: 策略需求 → 实验 gate → 数据动作

| 策略消费方 | 实验/里程碑 | gate (可脚本核验) | 数据动作 | 触发方式 | 状态 |
|---|---|---|---|---|---|
| LHB 退出 (C组C1, run_first 8.0) | 上榜即退出主判决 | top_list min<=20200101 + daily/adj_factor 2020-2022 段 728 日齐 | 日历扩展 + chain9 显式回填 (728+1456 调用 ≈3.1h) | 今晚 chain9 | 解锁中 |
| LF V0 (B组C3, run_first 7.8) | 龙头-跟随主判决 | X 轨 G1-G6 (分页修复→重拉→重建→伪影判据) | dc_member 加 page_limit + snapshot 分页修复 + 347 日重拉 + build_concept_events 重建 | 今晚 chain9 (重拉) + 代码批 | 解锁中 |
| B组C2 member_add_confirm | 事件确认主表 | 同 LF V0 G1-G4 (共享事件面板) | 同上 | 同上 | 跟随 |
| D 排序层 W2 | D-A0 基线复跑 (锚点 0.0108-0.0203) | 落回历史带, 否则先修管线全 ablation 顺延 | 无新数据 (本地) | 周末/W2 第一位 | 可立即 |
| W3 判决实验 (moneyflow 截面信息含量) | D-A1 ablation 2023+ 窗版 | 预注册窗口口径按 2023+ 重新冻结 (2018 原文被 clamp) | moneyflow 2022 段 242 调用 (chain9 搭车) + 2018 段待裁 | 今晚搭车 | 半解锁 |
| B组C4 链谱 | industry_chain.yaml 人工编制 + 反链安慰剂 | YAML 定版 | 无 (industry_sw+kline 在库); iFind 喂料走 POC | 人工, 可立即 | 可立即 |
| B组C5 ths_hot 过热 veto 单臂 | 22:30 落盘 -> JOIN t+1 | ths_hot 403,362 行已在库 (评审时 NEVER_SYNCED 已过时) | 无 | 可立即 | 可立即 |
| 筹码轴 5 combo | 冻结 (C0 FAIL) | 复活 = 新立预注册实验 (本地 qfq CYQ 复算替代黑箱, 不解冻原 combo) | modal cyq_replay (已 deploy) + push 脚本 + smoke 覆写隐患修复 | W2-W3, 用户裁决后 | 冻结待裁 |
| 套三题材扩散 | W9 三道门复审 | 门① dc_member 历史>=2022-01 (现库 2025+, vendor 下限 unknown) | chain9 顺带探底 dc_member 2020/2022 历史可得性 (4 发) | 今晚搭车 | 养数据 |
| C组C5 卖方覆盖 | create_time 完整段测定 | 完整段<3 年降级 | 库内已落段先测 (本地) | W2 搭车 | 半可执行 |

**单点解锁项 = dim_trading_calendar 扩展** (一刀解 LHB gate / W3 全期 / 2018 段 / Tier-A 四条线)。
执行决策 (总指挥裁定, 依据 C 轨影响面全扫): 扩到 **20050104** (registry 最早 data_start, 影响面
扫描结论 = 除 drain 外全部消费点安全, 无隐性重算); **同批做 registry data_start 范围对齐手术**
防 drain 开闸自动烧配额 (C 轨 critical): 未批准历史段的域 data_start 临时抬至已批准范围,
注释保留原值与升级路径。

## 2. 调度新政 (用户 2026-06-12 16:05 决议: 自动更新退役, 一律前端按钮手动)

已落地:
- launchd 退役: com.chunkymonkey.daily-update / concept-snapshot 已 bootout, plist 归档
  `backend/scripts/launchd/` (恢复 = launchctl bootstrap)。nightly-data-audit (只读审计+告警)
  **保留** — 手动时代它是 "该点按钮了" 的提醒线, 不是更新任务。
- 后端: `backend/routers/ops_manual_run.py` (`/api/v3/ops/jobs*`) — job 注册表范式
  (新任务加条目零端点代码), detached spawn + 复用 launchd_job_wrapper 告警链 (失败 flag +
  macOS 通知)。7 单测 + main.app 实弹冒烟 200。
- 前端: 工作台新增 "每日更新 (手动)" 卡片 (跑每日更新链 / 概念快照 两按钮 + 30s 轮询状态),
  contract 70/70 过。

新政例外处置 (06-12 晚定案):
- **E7 概念快照自养 → 退役** (§7.1: dc 系历史 tushare 可随时回拉, 快照是冗余中间层;
  "攒一天少一天" 只对已出局的 THS 系成立)。退役前做 observed vs reconstructed 单日对账 sanity。
- B组C6 "12 周 forward 记账 cron" 假设失效 → 改为 forward 记账脚本挂 daily_update 链内
  (手动跑时顺带), 预注册文档按此改写。

## 3. 06-12 今晚执行序 (已全部执行完, 存档)

1-6 全部完成 (06-12 晚~06-13): daily_update 实弹 + 代码批 + 日历扩展 5343 行 + chain9/9b/9c 回填
(top_list/daily/adj_factor 实验窗 728 日齐 / dc_member 重拉 23.1M / 类型推断加宽根治 / 薄日重拉 +
carry-forward 平滑) + 概念快照 E7 退役 (证人腐坏摘除) + build_concept_events 平滑重建。详 ledger
2026-06-12~13 各条。**当前执行序改由 `systematic_validation_plan_20260613.md` + goal.md 验证计划节驱动**:
地基 (index_member_all/index_daily 落库) → tushare 域 alpha 贡献研究 (T 层) 并行 → 组合 optuna。

## 4. tushare 用尽地图 (T 轨 239 接口全盘点定稿, 2026-06-12)

已定原则: 不为接而接, 每接口必须标策略消费方; 注册纪律 = 单日实弹核证字段/grain/单页上限
(top_inst 1000 整 / dc_member 5000 整两个反例) → 注册 → 排 chain → probed_* 回写 catalog
(治理缺口: '171 ok' 名单未持久化, 哪 19 个无权限不可查 — 本轮起逐接口回写)。

**按消费方的未注册接口排程**:

| 波次 | 接口 | 消费方 | 备注 |
|---|---|---|---|
| chain9 (今晚) | 无新接口 — 纯历史回填 | LHB gate + W3 判决窗 | top_list/top_inst 2018-2022 + daily/adj_factor 2019-2022 + moneyflow 2022 + dc_member 重拉, ~9.7k calls 一夜窗 |
| chain9.5 (尾挂) | 无新接口 — 7 域转正补丁 | 数据面收口 | suspend_d ~1080 + dc_index ~250 + top_inst 补日 + ths_hot/hsgt 单发 + moneyflow_ind_dc 纯配置 (min_rows 200→50, 实测 86 行/日), 共 ~1350 calls |
| chain9.5 后小批 | **index_daily + index_member_all** (2000x2 积分) | KPI 超额 HS300 主源 (现走 akshare = 主源倒挂) + 申万 L2 PIT 行业中性化 | 高优, 无分页险 |
| chain11 | dc_daily (6000, 2020 起) + kpl_list/kpl_concept_cons (5000x2) | LF/题材: 概念行情史替代 dc_index 停摆臂; kpl 是唯一可能早于 2025 的题材标签源 | 单日实弹核证后回填 |
| chain12 | income/balancesheet/cashflow + **disclosure_date** + fina_indicator; margin 两支 + index_dailybasic (regime); hk_hold (northbound 断头域接管) | D 排序层基本面族 + B 书 regime | disclosure_date 是 fina_mainbz PIT 锚配套必需, 同批走 |
| 条件触发 | hm_detail (10000 积分=额度边界) + hm_list | LHB 判正后唯一增量维度 (席位质量) | 硬性串行: C-C1 判负则 0 call |

**暂缓项重估定稿**: stk_factor_pro 维持暂缓 (截断疑点未解 + 0 消费方 + 技术因子本地可派生,
为接而接); **cyq_chips 价值上升** — ~60 call 小样本逐价位对照 (20 股 x 3 日, vs modal 本地
复算) 是确证 "未复权坐标" 假说、救活 5 个 frozen combo (含期望值最高的 A-C2 +10pp) 的唯一
钥匙, 已列用户决策清单。

**chain9 跑前 grill 抓住的白跑级缺陷**: daily/adj_factor min_rows_per_batch=4000 按 2026 年
5512 只标定, 2019 年仅 ~3700 只 → 2019-2020H1 ~360 日 x2 域全部误判 below_min_rows 灌爆
failure_queue。跑前必须降到 3000 (带注释, 年份感知 v2 另议)。dc_member 历史地板定案:
vendor 2025-01-02 前无数据 (E8 探底 + 本日复核一致), 套三门① (>=2022-01) dc 系永远满足
不了 — 出路 = kpl_concept_cons 探史或继续养数据下季度再审。

## 5. iFind 接入设计 (research-only, 永不越界)

- 红线 (有既有决议背书): 不做行情/精确资金流生产替代; 不做 tushare 已有域的"兜底/对照"常态源;
  latest-only 截面 (search_stocks/get_stock_summary) 严禁进回测路径。
- PIT 分级: A 带历史窗 (search_news/notice, get_stock_performance) research 可用 / B latest
  快照须 daily 自养 (sector_data 成分维度, get_stock_info) / C 报告期陷阱 (get_stock_financials
  无公告日锚) / D 横切: NL 语义检索非确定性 ("国产替代"→"换芯" 实证), 任何工具不能直接做
  可复现回测特征。
- **POC-1 (首选, 0 写库)**: fact_concept_event 事件真实性核验 — 干净事件面板重建后, 抽 20 条
  member_add, sector_data 解析板块码 + search_notice/news 找业务关联公告; 预注册判据
  >=16/20 → dc_member 语义可信; <16/20 → LF V0 强制加纯度过滤臂 (恰是 B组C6 搭车臂)。
  配额 ~40-60 次, trial 2000 内。挂在事件面板重建之后。
- **POC-2 (搭车)**: top_list 回填落库后, get_stock_performance 抽 10 股×日做独立第二源
  核对 (回填验收臂, 非替代)。
- 升级契约 (POC 判正后): parquet 自养范式 (同 irm_qa), 不入 sync_registry (registry 是
  headless writer 契约, iFinD 是 MCP-only 人工触发源, 强行入会制造 NEVER_SYNCED 假告警)。
- 配额/价格: 旧笔记 "trial 2000/personal 40 CNY" 声称已收回 (用户页面未见收费, §7.3) —
  POC 期间以真实限频错误实测边界, 不做预算前置。按钮触发 `claude -p` 路径属设计假设, POC 前实测。

## 6. 本周末-W2 实验排程 (数据线并行不互抢)

| 序 | 任务 | 依赖 | 产出 |
|---|---|---|---|
| 1 | D-A0 基线复跑 (锚点 0.0108-0.0203) | 无 (本地) | 测量仪校准, 失败则一切 ablation 顺延 |
| 2 | LF V0 + LHB 退出预注册重新成文 (16 combo 底稿未持久化, 数值判据必须跑前冻结) | 无 | 两份预注册冻结文档 |
| 3 | industry_chain.yaml 人工编制 + 反链安慰剂组 | 无 | B组C4 前置 |
| 4 | B组C5 ths_hot 过热 veto 单臂 | ths_hot 已在库 | veto 信号 V0 |
| 5 | LHB 退出主判决 | chain9 完成 + gate 脚本 PASS + 预注册 | go/no-go 判决表 |
| 6 | build_concept_events 重建 + LF V0 | dc_member 重拉 G2-G4 PASS + 预注册 | 事件面板 + LF 判决 |
| 7 | W2 既定: moneyflow audit / E3 双源对账 / E5 regime gate v0 / index_member_all 落库 | 部分依赖 chain9 | W2 里程碑 |
| 8 | iFind POC-1 | 任务 6 | 语义可信度判决 |

## 7. 决策落账 (2026-06-12 晚, 用户三决议 + 授权)

1. **概念域单源化 (用户决议: "概念之类不用同花顺, 不用全用, 容易混")**: 概念/板块轴主源 =
   东财 dc 系唯一 (dc_member/dc_index/dc_daily); THS 概念族 (ths_member/ths_index/ths_daily)
   全部出局, kpl 系降为候补 (除非某 gate 明确需要 2025 前题材史且用户单独点头)。
   连带: **E7 概念快照自养退役** — dc 系历史可随时回拉 (catalog 实锤), 快照是冗余中间层;
   退役前用 06-10→06-11 的 observed 快照 diff 与 reconstructed 重建结果做一次对账 sanity。
   已落地的 ths_hot 403k 行 (热股榜, 非概念成分) 保留做 B组C5 单臂实验 (零新增成本), 不再扩展。
2. **modal 使用授权 (用户: "你来安排")**: 总指挥裁定 — (a) 先修 smoke 同路径覆写隐患
   (b) cyq_chips ~60 call 小样本逐价位对照 (确证"未复权坐标"假说, 预注册判据先成文)
   (c) 假说确证则写坐标换算层 + modal 全市场 CYQ 复算做供给 (push 脚本 + 预注册新实验,
   不解冻原 5 combo 而是新立项), $30 额度内, dry_run 治理不变。
3. **iFind 配额/价格声称收回**: 此前 "trial 2000/personal 40 CNY" 来自 06-05 探测笔记,
   用户在页面未见收费 — 该声称降级为 unknown, POC 期间用真实限频错误实测边界, 不做预算决策。
4. **top_list 2005-2017 全史** (chain10): LHB 判正后条件触发, 维持。
5. **cyq_perf 2018-2022 补样**: 并入决策 2 的口径对照结论后处置。

## 8. 防回退与风险

- 日历扩展防回退: realdb 断言收紧 min<=2005-01-04; refresh 源后续切 raw_tushare_trade_cal
  (akshare cutoff hardcode 是 2023 起点根因, 不阻塞本次)。
- drain 开闸风险: registry data_start 手术与日历扩展同 commit, 任何域未批准段不进 drain expected。
- dc_member 重拉验收: G2 (5 日概念数跳变<=10% + 754 概念中位覆盖) 不过不许重建事件;
  G4 (BORN+DEAD 日均<5, ADD+DROP 日均<=240) 不过不许开 LF V0。
- 2020 段 below_min_rows suspect 预报: daily/adj_factor 2020 截面 ~3800 < min_rows 4000,
  属预期 suspect 非失败, 回填窗内不重试风暴。
- 手动时代最大新风险 = "没人按按钮": nightly-data-audit 保留 + doctor alert_flags 巡检
  + 前端状态卡, 三线提醒。
