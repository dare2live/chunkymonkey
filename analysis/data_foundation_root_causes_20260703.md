# 数据四地基根因分析 — 审计 38 条问题的六个系统性根因 (2026-07-03)

> owner: 主会话。输入: data_foundation_audit_20260703.json (10 confirmed/28 low_medium/79 clean)。
> 用户定调: "这些问题属于数据地基的问题, 四地基还有缺陷, 顺根因继续深挖"。
> 方法: 把 38 条按共同机制聚类 → 每个根因 = 地基的一个结构性缺口, 修根因而非逐条打补丁 (流程根治>单点绕过)。

## 根因 1: grain 声明是"猜的"不是"验的" — M1 采集最重缺口

**实例**: report_rc 漏 quarter (CRITICAL) / block_trade 漏 buyer,seller (CRITICAL) / moneyflow_ind_dc 漏
content_type (12 组 dup 已传导进 mart) / fina_indicator 依赖 API 已删的 update_flag (域坏死) /
hm_detail 差点漏 hm_orgs (注册时实弹抓到 — 唯一被 SOP 拦住的)。

**机制链**: 注册时单日抽样查重 → 样本不足以暴露低频多行模式 (多年度研报/双榜板块/多席位) →
**2026-06-22 上线的批内 drop_duplicates(grain) 把错误 grain 从良性 (多行共存) 升级为恶性 (静默销毁)**。
一个保护机制建立在未验证假设上 = 假设错误被机制放大。

**根治**:
- (机械门) grain 唯一性进持续审计: 全域每日 `GROUP BY grain HAVING COUNT(*)>1` 扫描, >0 = FAIL —
  grain 错误在"良性期"就被抓, 而不是等去重上线后变销毁;
- (SOP) 新域注册查重必须 ≥3 样本日含**高活跃日** (大涨日/财报季 — 低频多行模式的高发场景);
- (协议) drop_duplicates 丢行数必须计数上报进 sync 结果 (丢行>0 且非纯重复 = WARN), 禁静默。

## 根因 2: allow_empty 混淆"合法空"与"故障空" — M1 采集协议缺口

**实例**: top_inst 16 缺日 (源端全有数据, 本地被吞; 含一条假 resolved) / block_trade 20250917 空洞
(同机制第二例证)。

**机制链**: 0 行两种语义 (今天真没有 vs 网关抽风) 协议层不可区分 → allow_empty=true 一刀切全当合法 →
drain 对 allow_empty 域 drain_inapplicable → **永不自愈**。

**根治**:
- (机械门) 同族交叉参照: top_inst 空日但 top_list>阈值 = 可疑, 进 failure_queue 非静默接受
  (实测支撑: top_inst 8.5 年从无 <286 行日, 阈值可放心设);
- (协议) allow_empty 域的"空日"一律记录到 known_empty 候补清单, 周期性 live 探针复核 (空是真空吗);
- 事件类低频域 (block_trade/suspend) 用邻日行数模型做 sanity。

## 根因 3: dim/派生层无生产刷新契约 — M2 清洗地基缺口

**实例**: dim_trading_calendar 断链 (唯一写方=一次性迁移脚本, 且已成破坏性死路径; 123 交易日倒计时) /
v_dc_industry_pit 假 PIT (无 out_date 列却叫 _pit) / dim_stock_segment_daily 无 built_at。

**机制链**: sync_registry 对 raw 域管辖很严 (47 域全注册), 但 **dim/派生层没有等效的"刷新责任人"注册** —
data_layers 声明 layer 却不声明"谁每天喂它"。一次性脚本建的表天然成为孤儿。

**根治**:
- (代码件) raw_tushare_trade_cal → dim_trading_calendar 增量 builder 挂日常链 (最紧急, 倒计时中);
- (机械门) check_orphan_tables: 每个非静态表必须有活的写路径 (check_dead_references 的反向:
  有表无 writer = FAIL);
- (声明) data_layers 表级加 refresh_via 字段 (builder 函数名 / sync 域名 / static), 审计对账。

## 根因 4: SLA 门测"最近动过没"不测"该到的到了没" — 治理地基缺口

**实例**: 日历 horizon 检查是注释未实现 (静默停摆模式下门永绿) / fina_indicator 坏死 13 天 doctor 不红 /
stk_factor_pro 停 11 天零痕迹 (by_ts_code 无 drain, SLA 声明与机制矛盾) / ths_hot 热基子榜断流 4 个月
(表级 MAX(trade_date) 探不到分组断流)。

**机制链**: 新鲜度体系 = watermark 时间戳检查, 缺**语义化前瞻检查**: 日历要 horizon (未来余量),
多榜共表要分组新鲜度, by_ts_code 域要按期覆盖。"最近写过" != "内容完整且够用"。

**根治**:
- (机械门) 日历 horizon 门: max(dim.trade_date) − today < 60 交易日 = FAIL (落实 registry 那条注释);
- (机械门) 多值域分组新鲜度: registry 加 freshness_group_col (如 ths_hot data_type), SLA 按组算;
- (对账) registry 每条可执行声明 (data_start/page_limit/SLA) 生成机械验证 — 声明-实测对账进 doctor。

## 根因 5: mart 增量协议缺"迟到数据"语义 — M2→M3 界面缺口

**实例**: pulse 行定格 (当日行带 NULL 入库后永不回补; t+1 域 margin/龙虎榜的列在早跑日永久缺失)。

**根治**: (协议) 所有 mart build_latest 标准化"近 N 日重插"半边 (DELETE+重插最近 3 交易日, 幂等且与
全量逐 bit 一致) — pulse 先修, 写进加工件模板供后续 builder 沿用。

## 根因 6: 声明与实现漂移无对账 — 治理地基缺口

**实例**: data_start 声明 vs 实际覆盖错位 5 域 (dividend 2005→实 2023 等) / income 深史稀疏
(2008-2021 仅 5-15% 覆盖, 用 2019-2021 财务面的回测会静默偏样本) / fixed_params 死配置 (ths_hot
声明单榜实落 9 榜) / crontab stale 头注引用已删的 configs/cron。

**根治**: (对账) declared-vs-actual 审计: 每域 data_start vs 实测 MIN(trade_date) 偏差>90 天 = WARN
并回写 registry 注释; income 类深史稀疏在 registry 加 coverage_note 防回测误用; stale crontab 清理。

## 执行批次

| 批 | 内容 | 状态 |
|---|---|---|
| R0 止血 | 4 个 grain 修 + 8 段重拉队列 | [DONE] grain 已修, 重拉跑批中 |
| R1 机制件 | 日历 builder+horizon 门 / grain 持续审计门 / allow_empty 交叉门 / pulse 迟到回补 | 本批 (代码+单测) |
| R2 同型全扫 | 六根因各自的全库未爆实例扫描 (audit 只抽查了部分面) | 重拉完成后 (需读库) |
| R3 下游重建 | pulse mart 重建 (12 dup 清) + 机构画像三表重算 (top_inst 口径统一后) | 重拉完成后 |
| R4 声明对账 | check_orphan_tables / declared-vs-actual / 分组新鲜度 / medium-low 28 条逐项 | R2 结果合并 |
