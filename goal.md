# ChunkyMonkey Goal

> 状态：live controller board
> 手写：objective / 已裁决 / 禁令 / 下一步。**运行时状态现查 `scripts/chunkyctl status`**（零文件；非执法输入）。
> 完成证据：`scripts/chunkyctl history --grep <关键词>`（git log 即原件）；时期导航 `--eras`。交接：`chunkyctl history --grep "account-switch"`。
> **执行方案（仅两份；abolished 主方案/支线）**：底座 `goal.md「下一步」执行 backlog` · 策略 `goal.md「下一步」执行 backlog + strategy_validation_contract.md §3.2/§3.3`（**已排期** S1 RX-E/RX-F；`RX_AUTH=RX-20260824-EF`。S5 Optuna **已排期** `PHASE_N_AUTH=OP-20260824-S5`，yaml `phase_n_optuna` 在 S1/S2 交付前保持空；StrategyRelease 仍禁）。
> **清理台账**：`chunkyctl history --grep "文档收敛"`。Owner 立法仍只认 `docs/README.md` 三份 contracts。
> **活契约引用（非第二 backlog）**：`docs/MASTER_TOPLEVEL_DESIGN.md §11 (FND-GATE 十维)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.5 (变量积木分层)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.6 (物理分层裁决)` · `docs/engineering_governance.md §3.1 (何时不该开刀)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)` · `docs/MASTER_TOPLEVEL_DESIGN.md §5.7 (披露域增量策略)` · `chunkyctl history --grep "ST 白名单"`。

## 当前 objective

**轨道 = foundation solidify CLOSED**（母体 = transport strangler S1–S7 + brick 分层 + E0 + DB 分层）。
逐项完成史查 `chunkyctl history --grep <关键词>`；FND-GATE 十维实时裁决跑
`check_foundation_done.py` —— **本节不再复述已闭合项**（复述必然滞后，本轮实证过）。

**换源 strangler（判据已换；对账段收口）**
产品只回答三问：市场在哪、谁在买、价量结构成不成立。研究定位 = **信号条件有效性分层**，不是多因子建模：公式发出的买卖信号，按信号日可见的观测量分桶，看每桶前向收益与胜率；不加权、不回归、不寻优（S5 仍禁）。**「全量因子」= 分层字典不是模型输入** —— `factor_family_inventory.yaml` 是这本字典的目录，不退役。系统四层：信号事件流 → 观测维度字典（信号日可见） → 前向收益（`next_tradable_open`） → 分层胜率表；L4 纯计算，不依赖外部源。
新输入优先扶摇 / 妙想 F10 / 通达信 hub adapter，进同一 `landing→accept→canonical`。**切换判据不是 identity**：accepted 是二手供应商面不是真相源，跨源口径必然不齐；判据 = 产品要不要 + 源能不能给 + 口径写进字段字典 + 自证式验收（schema 漂移 / 行哈希 / grain 去重 / 分区物理约束 / landing 与 canonical 非空），与 TuShare 数值无关；同名指标口径不同就并列成两个切片维度，不择一。TuShare 日更在该域 cutover 前不停；不再注册新 TuShare 域；三源无等价则**明确留 TuShare**。禁 tdxhub 日线 qfq 当成交 SSOT，禁复活已物删 client / 表名。

**当前 blocker**
- **TuShare 授权 `2026-09-10` 到期（剩 10 天，硬停不是降级）**：`tushare.py` 在 `expires_at <= now` 时直接 `raise TuShareAuthorizationError("auth_expired")`。**曾经卡的不只是 TuShare 域** —— `authorization_preflight()` 写死 `adapter_factory("tushare")`，纯扶摇 / 纯通达信回填也一起挂；**该耦合已解开**：`_cli_skips_provider_authorization` 让「所选域全非 tushare」的 CLI 跳过 tushare 授权探活（fail-safe by construction：判不出就不跳），`require_live_adapter` 改按域校验（`5d7f53d11`）。故换源工作本身不再被到期日绑架。**仍会硬断的是「三源无等价、明确留 TuShare」的那批域**，到期前要么续期、要么逐域找到替代 —— 这是真 blocker，不因上述解耦而消失。
- 其余无阻塞（2026-08-28 `chunkyctl status` 实测）：日更链正常 —— 日线 / ST T+1、两融 / 持股变动公告 T+2、十大流通股东 T+0，49 源 0 连续失败 0 fallback。唯一实质滞后是 `org_holding_detail_period`（期轴，`status` 自注「不构成 SLA 判定」），**已归「下一步」K4，不另立 blocker**。`cutover 声明 vs 实际` 的 `tier12_consumer` WARN 是 config 写明的逐日回落、发布一期即自愈，**不是待修项**。滞后数与裁决一律现查 `scripts/chunkyctl status`，禁在本文件写死（2026-08-10 两份手写文档互相矛盾且同时落后两周）。

已裁决硬事实（勿回滚）：
- accepted daily / ST 起点 **`20190102`** / **`20220104`** 是契约常量；**当前 frontier 是运行时状态，
  现查 `chunkyctl status`，禁止在本文件写死**（2026-08-10 两份手写文档互相矛盾且同时落后两周）
- Phase F ladder measured **reject** / `claimable=false`（可 checkpoint；**≠** Release）
- Delivery-OS：eng_gov §14（一刀 = Rule10 + safe_commit；异步 CI；L3 pre-knife；不放宽 PIT / ≤40d）
- A→H = 后置研究地图；**E/F remeasure scheduled**（`RX_AUTH=RX-20260824-EF` 与 `backend/config/strategy_lab.yaml` `authorizations.formal_rx` 对锁）。**S5 Optuna 已排期** `PHASE_N_AUTH=OP-20260824-S5`（执行前提 = S1/S2 同 protocol ExperimentVerdict；yaml 第二钥未开、runner 未实现）。StrategyRelease 仍禁；F9 按字面锁 `RX_AUTH=` + `Optuna` + `StrategyRelease`
- **对账 ≠ 切换**：八刀 recon 是只读诊断器 —— `primary_cut` 在 **11 个文件共 21 处硬编码 False**（2026-08-31 正则精确统计；`assignment_gap_recon` 独占 7，`moneyflow_recon` 4，`cyq_recon` 2，其余 8 文件各 1）。**`claimable` 不是 recon 装置的一部分** —— 它是策略侧另一套语义、另有 30 个文件在用（`institution_follow_*` / `main_rally_*` / `formula_challenge` / `strategy_lab`），换源不要动它，其中 4 项子结论 `identity=true`（妙想龙虎榜 / 席位 / 大宗、扶摇沪深 codeset）仍为 False；扶摇与通达信日 K `ohlc_mismatch=0` 仍写 `kline_daily_primary_untouched=true`。**扩样本不会让它变 true**，切换装置须另建（见「下一步」K1）
- **消费面去供应商化**：`data_access.yaml` entity **禁直指 `raw_*`**（现存直指项列白名单，只减不增），改经 `v_<domain>_<grain>` 视图解析，跨源字段名差异在视图 `AS` 里吸收（先例 = `v_sw_industry_pit`）。**先有视图接缝，再谈 `capability` / `primary`**
- **扶摇凭证**落 `~/Library/Application Support/hithink-finance/credentials.env`（0600，不进仓库；`resolve_api_key()` 三处来源之一）。实测能力边界（2026-08-28 逐端点二分实测，非推断）：**涨停池 `20200623` 起** —— 早于 TuShare `limit_list_d`（2023-01）与 `limit_cpt_list`（2024-01）两年半，**这是扶摇唯一的历史深度增量**；**跌停池 / 炸板池只有 `20250813` 起**（三池窗口各不相同；此前把涨停池窗口推广到三池、写成「三池 2020-07」是错的）；**竞价强弱基准 `20230919` 起**（此前写「2022 起」错误，其窗口极宽但 2023-09-19 前全为 0 行）；热股榜**仅 1 年**；异动分析**仅当日**；**三端点表达「窗口外」的方式不一致** —— 涨停池与竞价返回 0 行，跌停 / 炸板抛 `code=1002 Invalid parameter format`（不是参数错，同格式在窗口内正常返回），**按统一规则判边界必判错**；`meta/tickers/list` 与 `prices/snapshot` **无 ST 字段**。ST 历史 PIT 身份三源皆无 —— `stock_st` 留 TuShare 的理由是「只有它有历史」，不是「识别不了」。
- **扶摇日 K**（`/api/a-share/prices/historical`，2026-08-30 实测）：参数 `{thscode, interval:'1d', start, end, adjust}`，`start`/`end` 是**毫秒时间戳**（`shanghai_midnight_ms()`；`date_ms` 也是上海午夜，用 UTC 解会偏一天）。三条硬约束必须写死在 adapter、不留给调用方：**① `adjust` 缺省是 `forward`（前复权）**，必须显式传 `none` 才是不复权 —— 不传不报错、价格量级看着合理，是最隐蔽的坑（分红事件精确验证：600000.SH 差值 3.311 = 期间 8 笔现金分红之和）；**② 入库必须过 `low ≤ turnover/volume ≤ high` 自洽校验** —— 实测 5 只深市票在 `2025-11-27`/`2025-12-01` 两天越界（反推均价差 10~1000 倍，而同日 OHLC 与 TuShare 一致，且 TuShare 侧自洽、全市场 0 只偏离），**这是扶摇自身的量纲 bug，静默入库即成交量错 10~1000 倍**；**③ 单次跨度上限 3653 天**（超出报 `code=1003 must span at most 10 years`），全历史须分段。单位 `volume = tushare.vol × 100`（股 vs 手）、`turnover = amount × 1000`（元 vs 千元）。`adjust=none` 下 OHLC 与 TuShare **25,226 对 bit-identical**；代码表是 TuShare universe 的**严格超集**（5563 vs 5550，多出 13 只为待上市新股），含**北交所 343 只（全 `920xxx.BJ`）**

- **交易日历已换源 baostock（`81805a995`；换源 strangler 首个真 cutover，过程查 `chunkyctl history`）**：
  accepted truth 已切且逐行零差异；**但 serve projection（`raw_tushare_trade_cal` → `dim_trading_calendar`）
  仍冻结在 `built_at 2026-07-16` 且无人再写** —— 「truth 已切 / projection 冻结」属 K1 范畴，切 daily 前须一并收口。
- **日 K 三源裁决（2026-08-30 逐源实测，业主 08-31 拍板；勿回滚）**：**通达信 = 日 K 主源**，
  扶摇 = 备源并接三池 / 竞价，baostock = 日历源 + 第三备援。判据是实测出来的三项全满足：
  通达信**有全历史 + 有北交所 + 与 TuShare 逐位一致 + 免费无授权**（`920000.BJ` `2026-08-28`
  OHLC `13.59/13.87/13.44/13.81` 与 TuShare 逐位相同，amount 换算后一致；`protocol_market()`
  早已支持 `.BJ → market 2`）。**baostock 不当日 K 主源的唯一原因是无北交所** —— 它是免费数据
  服务不是券商，此前把它的缺失当成「三源共同的硬门槛」是错的，**三家券商源实测全部有北交所**
  （扶摇 343 只 / 妙想 `.BJ` 后缀 / 通达信 market 2）。通达信的代价 = 依赖公网主机可用性
  （协议免费但主机表会变，失败已能自动驱逐重学）。
- **妙想能力边界（2026-08-30 实测）**：**没有日 K —— 产品边界不是数据缺口**（F10 基本面库，行情在东财另一条产品线），
  不要再在这条线上找。能接：财务三表（带 `NOTICE_DATE`，PIT 安全）/ 两融（`2010-03-31` 起）/ 十大股东 /
  龙虎榜（**无独立 `NOTICE_DATE`，PIT 锚只能用 `TRADE_DATE`**）/ 大宗 / 陆股通，且全部原生支持 `.BJ`。
  **关键澄清**：`dc_index` / `moneyflow_ind_dc` / `moneyflow_mkt_dc` 名字带「dc」像东财直连，实为 **TuShare 代理的东财接口**，到期一样断。
- **通达信握手代价已根治（`c8e428769`）**：主机记忆从进程内 dict 改为跨进程落盘，冷启动 42.9s → 0.35s；
  不硬编码主机、握手失败即驱逐。连带教训：**持久化会让测试污染从进程内升级为跨进程存活**（已用 conftest autouse fixture 隔离）。

启动：`scripts/chunkyctl agent-boot`；运行时状态：`scripts/chunkyctl status`（现查，零文件）。

## 下一步

**执行 backlog 就在本节**（2026-08-11 起：原两份 `analysis/` 执行计划已并入这里 —— 规则段进
owner contract，进度段在此。**不再有第二个说「下一步」的地方**）。

*底座*（exit 已 MET；「100% usable」= 无 class-A，判据 eng_gov §9.1）
- **A3** Type-B fact publish 短滞后（moneyflow / limit / index / dc）—— 同跑 catchup
- **A4** org 中间历史季洞 —— **DEFER**：仅显式 backfill 刀，日常增量路径不变
- **A5** cyq 消费口径（历史段 FAIL）—— **DEFER**：消费前换算或弃用，非采集轴问题

*策略*（**已排期** S1；开门条件见 `strategy_validation_contract.md` §3.2；`RX_AUTH=RX-20260824-EF`）
- **S0** Strategy Lab 本地框架 —— 重冻两份 development snapshot（分区严格早于 holdout `20250601`）后才 `framework_ready`
- **S1→S2** RX-E/RX-F remeasure **已跑**：均为诚实 reject/inconclusive、`claimable=false`（**≠** Release）；Optuna runner / StrategyRelease 仍禁。
- **S-spec** 三包 `StrategySpec` 骨架（画像 ≠ `institution_follow_v1` 跟随纸面 ≠ E B0/B4 动量消融；rally = setup 纸面、full-episode 未实现；公式 = 五条 frozen hash）。跟随 spec 纸面已接 `stk_holdertrade` 公告事件；E/F JSON 仍是消融。`local_smoke` 可加载 spec；`claimable=false`；非 Release
- **S3** 公式挑战 **合成烟测 + 单名 live pointer（已测）**（下一 open + 一名一仓/T+1；`claimable=false`）。全宇宙 B5 / 吸收 BestChoice / Optuna runner / StrategyRelease **未做**
- **S4** Release + 纸面执行 · **S5** Optuna（**已排期** `PHASE_N_AUTH=OP-20260824-S5`；须 S1/S2 交付后才写入 yaml `phase_n_optuna` 并实现 runner；现禁开 runner）
- 默认序 S1→S2→S3→S4，S5 最后

*三源换源*（按域选最佳再切 `primary`；停更 `raw_tushare_daily` 不当尺子）
- **刀 1–8 只读对账已收口，不再加刀**（逐刀结论查 `chunkyctl history --grep "feat(source)"`）。留存硬事实：通达信主机表 = 客户端 `connect.cfg [HQHOST]`（只读、不跑 `bestip`）；两融尺子 = accepted 交易所汇总（个股加总非身份）；筹码 = `turnover_overlay_v1` 非持仓观测；资金流三名分列禁跨源加总；公式 = `next_tradable_open`，主升浪 = `setup_signal_only`，跟随 PIT = `notice_available_at`；禁 gpcw 复活、不注册新 TuShare 域。
- 参考 easy_tdx（学习不抄 OS）：标准 HQ 与 MAC 协议主机隔离、握手/非空载荷后才 failover、复权必须显式 `--adjust`；不抄缠论/回测 UI，qfq 仍禁成交 SSOT

*三源换源 — 执行段*（**刀 1–8 = 只读诊断已收口，不再加刀**；判据见 objective 自证式非 identity。并发按**文件重叠**判非逻辑依赖 —— `AGENTS.md` 法条 = parallel agents only when moth proves non-overlap。实测两处硬冲突：**K2 与 K6 同改 `sources/tdxhub.py`**、**K3 与 K5 同改 `sync_registry.yaml`**（K3 删域 / K5 加扶摇域）。故按 wave 走、每 wave 两 agent：**W1 = K1 + K4**（K1 先决，其余刀的接缝靠它）· **W2 = K2 + K3** · **W3 = K5 + K6**。**并发只到编辑与本地验证为止，提交必须串行** —— `safe_commit` 重生 `data/lineage/graph.json`（全仓唯一文件）并跑 `staged_worktree_parity`，同时 commit 必撞；要真并行提交先各自 `git worktree` 隔离再串行合入）
- **K2** 资金流两条腿并行（三源均无 TuShare 日终主力净流入的等价物）：tdxhub 补 MAC 协议客户端 + `capital_flow` 命令（与标准 HQ 主机隔离、握手 + 非空载荷后才 failover、不跑 `bestip`）· 同时查东财 datacenter 资金流 reportName。两边都拿到就并列进分层字典，不择一
- **K3** 退役 6 个零消费域：`daily_info` / `dc_daily` / `hm_detail` / `hm_list` / `kpl_list` / `ths_hot` —— 全仓只被采集器、`backend/services/foundation_obs_serve.py`、recon 脚本触及，无业务读取方。`kpl_list` 由扶摇涨停池升级替代。**游资概念留路线图**，重建在妙想席位（`RPT_OPERATEDEPT_TRADE` + 龙虎榜席位，后者 identity 已验），不保留 `hm_*`（`hm_list` 起点太晚、`hm_detail` 无读取方）。**两条执行前提（2026-08-28 实测）**：① `dc_daily` 禁子串匹配 —— 同前缀的 `dc_index` / `moneyflow_ind_dc` / `moneyflow_mkt_dc` 是活域（`dc_daily` 词边界匹配后只剩 `sync_registry` / `pipeline_latency_budgets` / `delta_manifest` / `foundation_obs_serve`，确为零消费）；② `ths_hot` 在 `check_continuity_integrity.py` 有**活登记**（`CROSS_SECTION_GROUP_COLS` 分组检测 + dead_groups 墓碑），退役须同源清掉，否则复刻本文件 A2 那个「墓碑指向活域→watermark 每轮被清→唯一无监控者」的盲区
- **K4** `org_holding` 两个缺口：① PIT 规则 2026-08-27 才改对，canonical 历史全部由旧「法定截止日冒充已知日」逻辑写入（accepted 分区日期清一色落在 0430 / 0831 / 1031），须按新逻辑 re-accept，且落后源端一个报告期未补；② 已 accept 但无消费方（`institution_profile.py` 未 ATTACH 该库），补消费链或明确暂缓 —— **落库通过 ≠ 被消费**
- **K5** 扶摇接入（key 已具备；四域已注册为 `on_demand` 但 **DB 里一行都没有 —— 注册 ≠ 落库**，2026-08-28 实测 `%fuyao%` 表不存在）：涨停池回填 **`20200623` 起**（含封单额 / 涨停时间 / 连板数 / `is_st`）· 跌停 / 炸板池只能从 **`20250813`** 起 · 竞价强弱基准 **`20230919`** 起 · dump 全历史日 K 与通达信交叉 · 基金 28 端点评估为跟随策略「资金方侧」第二条腿（现有跟随只有上市公司披露侧）
- **K5 落库前必修的两处（2026-08-28 验收实测）**：① ~~`on_demand` 显式窗口死门~~ —— **已修 `a222ab6f8`**（条件 `batch_mode == by_ts_code` 与 registry 交集为空、门从未触发；已改 `!= full_refresh` 并加红测试。诊断全文见该 commit）。
  ② **注册即隐身** —— 四域 `freshness_no_probe` + 表不存在 ⇒ 连续性门三项全 `skipped_missing_table`，**当前零可观测性**，没有任何机制会告诉你它们是空的。落库后要么给 SLA，要么把 `no_probe` 理由写成「历史静态快照、不再增量」—— 判据是「它明天停更了，哪一行代码会告诉我」。
- **K6** 通达信全历史（**它已是日 K 主源**，见「已裁决硬事实」日 K 三源裁决条；**链路已实测通，`live_unprobed` 这条 Residual 作废**）：链路与握手代价均已实测收口（见「已裁决硬事实」）。协议翻页取全历史未复权日 K + `xdxr`，验证 10 年深度的翻页稳定性（现只验过 10 个交易日窗口）；`block` 板块 / 概念作为第三套命名空间并列，不与 SW / DC / THS 合并。qfq / hfq 仍禁当成交 SSOT

*治理缺口*（2026-08-29 实测，均为「治理件存在、执法路径不存在」同型）
- ~~「80 个测试文件不被任何 CI 面覆盖」~~ **该结论已于 2026-08-29 自我证伪，勿再引用**：
  实测 236 个测试文件**全部**被 blocking(156) / nightly(0) / optional(80) 三面之一覆盖，**真正未覆盖 = 0**。原误判源于统计脚本把 `ci_test_optional` 的条目当纯字符串处理 —— 该段条目是 `{path, reason}` 字典形式，匹配失败后整段 80 条被误判为「未覆盖」，而 80 这个数字恰好等于该段条目数，巧合掩盖了 bug。
  **真实情况是治理比误判更好**：`ci_test_optional` 每条都带 `reason`，如 `test_sync_runner_drain.py` 注明「2026-07-20 CI-tax 审计发现的既有 gap；未经离线 CI 安全性甄别故不静默添加，须在 live-dependency review 后逐片显式提升」—— 是显式待办分类，不是遗漏。
  **残留的真问题**（比原误判小得多）：`safe_commit` 的 ci_pytest 门只跑 blocking 不跑 optional，所以 optional 里的 80 个文件不阻断提交 —— 本轮删一个模块级全局名波及其中 9 个文件却全绿放行，即为此。修法按那条 reason 自己写的：逐片甄别后提升，不批量塞。
- **`check_serve_derive_closed_loop.py` 全仓零引用**（不在 `governance_gates.yaml`、无任何调用链），而它对应的法条 `MASTER_TOPLEVEL_DESIGN.md §5.8 派生新鲜度闭环法` 仍挂在本文件「活契约引用」里 —— **法条在，执法者一次没跑过**。要么接进门体系，要么连同法条引用一起退役。

**护栏**（长期有效，非进度）：formal frontier 与 drain soft 窗分立叙述 · PIT + ≤40d ·
§14 不放宽 · serve = 沪深A 含 ST · 禁为清单洗绿（class-B 诚实状态**留着就是做对了**）。

## 治理体系重构（2026-08-10 立项；L1/L2/L3 层已落地，收尾中）

### 三条原则

1. **能机器生成的绝不人写** —— 状态 100% 可生成；人手写状态，从写下那刻起就在烂。
2. **一个事实一个存放处，存在最靠近使用它的地方** —— 阈值被代码读→YAML；判据人和门共用→文档。**文档解释「为什么」，配置持有「是什么」。**
3. **门装在受害发生的时刻，不装在最方便检查的时刻。**

### 目标态：按变化频率分四层（不按主题分）

| 层 | 内容 | 变化频率 | 载体 |
|---|---|---|---|
| **L0 宪法** | 不变量 / 判据 / 边界 | 几月 | 极少数文档 |
| **L1 契约** | 阈值 / 窗口 / 白名单 / 注册表 | 几周 | YAML，被代码读 + schema 校验 |
| **L2 状态** | 前沿 / 覆盖 / 门的实际裁决 | 每次运行 | **命令现查，零文件，禁人写** |
| **L3 历史** | 做了什么 + 为什么 | 只追加 | **git commit，无独立文件** |

推论：**`analysis/` 终点是 0 份**（已实证全仓 `open()/read_text()` 指向 analysis 零命中，无一是运行时依赖）；**ledger 退役**（commit message 已含 Q/Fix/Evidence/Residual，ledger 是 git log 的人工副本，必然滞后 —— 实证断档 77 个 commit 而 git 一条没丢；检索用 `git log --grep`，永不断档）。

### 剩余计划（完成项查 `chunkyctl history --grep 治理`，本节只朝前看）

**已闭合**：P1 门重新分布 · P4.1 孤儿法条归位 · P2 状态零手写（含 board 现查）· P3.2/P3.3 历史归 git（ledger 退役）· P3.4 主体（`analysis/` 55→3）。四层里 L1 契约 / L2 状态 / L3 历史均已到位。

**A/B 已闭合**（治理收尾 + 门体系残留）：`analysis/` 归零并退役 · commit message 改结构自检 ·
三份 owner 重新划界（AGENTS §4 十五条里 12 条曾是 MASTER 的近逐字副本）· `check_no_emoji` 登记为第 20 门 ·
`test_safe_commit` 从长期 25 红修到 18 绿并转正 CI · B3 查出 moth 门的 `elif` 在 warn-only 下短路掉
`moth coupling`（warn-only 退化成 warn-nothing 的实例，已修 + 参数化守卫锁定）。

**C 已闭合**（数据线）：daily 与 stock_st 两个 `on_demand` 域的尾部断流已补齐（两域前沿现已追平交易日锚，
滞后 0；行数单调无截断，现查 `chunkyctl status`）· qfq/form 已跟进 · tier12 补发 5 期，`stock_row_count ==
universe_membership_size == 5205`（无静默填充）→ **tier12_consumer 转 PASS**（ACCEPTED_CUTOVER）·
org_holding 采集窗=报告期末（东财随公告更新），PIT/accept=同股同期定期报告公告日（禁用法定截止冒充已知日）；法定截止后本地缺 = completeness_miss（年报/中报/一季报/三季报各自截止日）。

**迁移原则**：不追求一步到位，但新增任何文档前先问「这是 L0/L1/L2/L3 哪一层」—— 是 L2 就不许写，是 L3 就写进 commit message。

## 禁令

- 静默 cutover / 无证据回翻 `cutover_allowed=false`；Optuna；E 松门；StrategyRelease
- margin thaw；mass backfill；plugin bus；第二 DB；agent 自降 commit tier
- **org_holding（及同类 by-period 域）在每次 manual/`daily_update` 上做全市场单期 ~830k mass re-pull / 无界翻页 refresh** — 只允许 check latest plannable vs local：**缺 raw 则拉一期并按公告日/first-seen accept**（禁止写未来分区；法定截止不是 known-at）；**已有 raw 则 page-1 count probe，源端领先才 grain MERGE**（禁止 DELETE+全期重拉）
- 随手重写 accepted canonical / 日历契约 / PIT-availability / `stage→validate→publish` / cutover 证据链；dual-write 迁移窗口；把「残破感」当 greenfield 重写许可证
- 后台 subagent 若再出现「仅 2 行 transcript、tool 无 result」：改用本会话直接做或 `shell` 子代理（见交接文档）
- S7 14 sync_orphan **blanket pre-accept as standby**；假 S7 COMPAT

## 已裁决（稳定）

六层分层表（Tier 0A 市场数据 → Tier 4 决策/产品）正文见 `docs/MASTER_TOPLEVEL_DESIGN.md` §表 —— 本文件不留副本。

依赖只向下。Ops 观察但不拥有业务事实。多源=契约可换 adapter（目标态）；首策略包=`institution_follow`；边做边测。Tier0 未闭合前禁止寻优、生产候选、自动跑批。

架构硬决定摘要：积木=`module+data+config+contract+evidence`；landing 保留供应商响应；日历与 universe 同级硬门；名义 OHLCV=成交真相；一数据集一 writer；`manual_only`；静态 PASS≠`live_readiness`。完整条文见 `docs/MASTER_TOPLEVEL_DESIGN.md`。

**Formal daily/ST acquire · 手动 sync 时钟 · 披露域增量** 三条裁决的正文已归位 `docs/MASTER_TOPLEVEL_DESIGN.md` §5.1/§5.7 —— 本文件不再留副本（副本必然与 owner 漂移）。要点仍成立：全市场按 `trade_date` 拉、禁 exclude-then-fetch、沪深A 含 ST/*ST、`stock_st` 是 membership 证据不是 denylist、org 采集按已结束报告期缺则拉一期（PIT=公告日 JOIN，禁用法定截止冒充已知日）、禁 mass 与 by-date invent。

**Gate pytest 分层**：owner = `backend/config/ci_pytest_surface.yaml`（`blocking` / `nightly` / optional 三面 + 每条 optional 带 reason），L2/L3 与 CI 同跑 `--tier blocking`。分层理由与当初的取舍查 `chunkyctl history --grep "gate redesign"`。

**S7 sync_orphan standby（owner Q2）**：**NO** blanket pre-accept of 14 orphans（无 consumer / 无 contract / 大宗成本 / 假 readiness）。保持 ssot 墙；`legacy_raw_plane.yaml` **publication_watchlist** = 未来策略需要时的 publication 候选（非自动队列）；薄门：sync_orphan 进 DataAccess → `check_legacy_raw_plane` FAIL。**禁假 COMPAT**。

**Product 系统 + Agent-OS 演进裁决（owner，针对 Fable5 提案）**：后续演进 = **strangler + 聚焦**，非 greenfield 重写。仅三把杠杆：(1) 单一读 SSOT 经 resolver（禁旁路直读）；(2) 本地 L2/L3 pytest = CI test-list 唯一 SSOT；(3) god-seam strangler，按 blast radius 分步收编，不整体推倒。

## 禁止误报（交付判据）

切片循环见 `docs/engineering_governance.md` §5（坏例先红 → 最小实现 → 绿 → 窄回归 → 挑战 verifier → stale 审计 → `FIXED|PARTIAL|BLOCKED`）。

交易所汇总 ≠ 沪深池。accepted 行数 ≠ 业务正确。continuity 非 READY ≠ 代码不可提交。measured reject ≠ StrategyRelease。函数存在 / WARN / fixture 绿 ≠ 交付。**投影 ≠ 执法输入**（board 已改现查、零文件）。
