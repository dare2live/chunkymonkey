# tushare L2/L3 档案维度候选调研 (2026-06-22)

> owner: 主会话 (ultracode 4-agent 调研 wf_36c8b3ea + 主会话裁决)。
> 背景: 股票档案三层已全维度完成 (L1 价格形态 / L2 每日盘面 / L3 属性背景 ③④⑤⑥)。本文档调研
> tushare 还有哪些数据值得加入 L2/L3, 供后续逐维扩展决策。
> **方法论红线**: 全部按 **dossier 描述性维度 / stage-conditional 确认门** 接入, **不得直接做全市场
> 无条件截面换仓** (撞 R1; 实证 5 单因子 4 个含成本 UNTRADABLE)。技术因子类误用风险最高, 必须包在
> 主升浪/突破 episode gate 内, 转正前走含成本 backtest 绝对收益 (C-R1) 不看 IC。

##  Top 5 最值得做 (ROI × PIT 干净度 × 主升浪贡献)

| # | api | 价值 | 在库? | PIT | caveat |
|---|---|---|---|---|---|
| 1 | **`forecast` 业绩预告** | PEAD 前瞻催化 (早正式财报 1-4 月) = "为什么涨且可持续"; dossier L3 最大空白 | **已在库 (零拉取)** | 干净, 原生 ann_date | 多 revision 必取 ann_date<=t 最新版防未来修订; 幅度区间取中值 |
| 2 | **`stk_holdertrade` 股东增减持** | 内部人撤退/背书最硬信号; 鱼尾减持 veto + 鱼头增持背书 | 需先同步 | 干净, 原生 ann_date | 注册前实弹核证字段/grain/单页; **`in_de` 方向必实测** (防 dc_member 方向反类反例) |
| 3 | **`moneyflow` 个股资金订单分档** | elg(特大单)净流入 + sm(散户)净流出 = 主力吸筹未派发 (鱼身); 翻转=鱼尾派发 | **已在库未用** | t-1 盘后 | **vendor 混用红线**: 与 moneyflow_dc(东财)两套口径择一为主, 勿混同因子; vol手/amount万元单位坑 |
| 4 | **`stk_factor_pro` 261列技术因子** | 一接口全套内生技术 (adx/updays/多头排列=延续; rsi/kdj/wr/bias 顶背离=反转), 三复权省自算 | **已在库未用** | t-1 盘后 | **过拟合**: 须 Optuna 选子集禁全塞; 后复权喂特征/前复权展示勿混 |
| 5 | **`margin_detail` 个股两融** | Δ融资余额=杠杆资金天天有 (龙虎榜仅上榜日), 鱼身持续涌入=延续 / 见顶+融券骤增=鱼尾 | 需先同步 | 干净, trade_date | 仅两融标的(~2000只 universe 子集); 金额单位**元**(≠moneyflow万元)单位坑; 注册前实弹单页 |

**立即可做 (零拉取, 已在库未用)**: `forecast` / `moneyflow` / `stk_factor_pro` / `express` / `dividend`(复权链表已有) — 只需写 loader + dossier 接线。**先做 forecast** (PEAD 前瞻是 dossier 最大空白 + 干净 ann_date)。

## L2 候选 (每日内生量价/资金/筹码, PIT t-1 盘后)

| pri | api | 名称 | 在库 | 主升浪用途 | 陷阱 |
|---|---|---|---|---|---|
| 高 | `moneyflow` | 资金订单分档(大中小特大单) | [在库]已在库未用 | elg净流入+sm净流出=吸筹; 翻转=派发 | [警]vendor混用=口径leakage(勿混moneyflow_dc); 单位手/万元 |
| 高 | `stk_factor_pro` | 261列技术因子 | [在库]已在库未用 | adx/多头排列延续; rsi/kdj顶背离 | [警]过拟合须选子集; 后/前复权勿混 |
| 高 | `margin_detail` | 个股两融 | [需同步]需同步 | Δ融资=杠杆延续; 融券骤增=鱼尾 | 仅两融标的; 单位元≠万元; 实弹单页 |
| 中 | `bak_daily` | 备用行情(量比/内外盘/振幅) | [需同步]需同步 | 量比延续标尺; 内外盘主动买卖 | [警]tushare自产派生非交易所原始; 分页截断险 |
| 中 | `stk_nineturn` | 神奇九转(DeMark TD) | [需同步]需同步 | 上九转(+9)=逃顶 gate (用户GS公式核心之一) | [警]data_start 20230101历史短(OOS~2.5年); 单因子≠edge |
| 中 | `limit_step` | 连板天梯 | [需同步]需同步 | 题材主升强度: 梯队抬升=延续/断板=鱼尾 | 仅连板股极窄; 与 limit_list_d 重叠可自算 |
| 中 | `kpl_list` | 开盘啦榜单(封单/竞价/连板) | [需同步]需同步 | 封单越封越死=延续/萎缩=鱼尾 | 题材子集; 5000积分门; 与 limit_list_d 信息重叠去重 |
| 低 | `stk_auction_o` | 开盘集合竞价 | [需同步]需同步 | 竞价量能=抢筹先验 | [警]盘后更新不可当t日盘前gate只能t-1; 已含在open边际低 |
| 低 | `hsgt_top10` | 沪深股通十大成交股 | [需同步]需同步 | 北向重点延续 | [警]须探活是否随hk_hold同停(2025-07收紧); 极稀疏 |
| 低 | `hk_hold` | 个股北向持股 | [需同步]需同步 | (理论北向加仓=延续) | [高危]**已停披露(2025-08-15起0行)=dead-forward, 不接** ([[project-northbound-holdings-discontinued]]) |

## L3 候选 (慢变外生属性/事件, PIT ann_date / 事件日)

| pri | api | 名称 | 在库 | 主升浪用途 | 陷阱 |
|---|---|---|---|---|---|
| 高 | `forecast` | 业绩预告(PEAD前瞻) | [在库]已在库未用 | 鱼头: 预增/扭亏=最强前瞻催化 | 干净ann_date; 多revision取最新版; 幅度取中值 |
| 高 | `stk_holdertrade` | 股东增减持 | [需同步]需同步 | 鱼尾高位减持veto; 鱼头增持背书 | 干净ann_date; 实弹核证+`in_de`方向实测; 是已发生非计划 |
| 中 | `express` | 业绩快报(PEAD二段) | [在库]已在库未用 | 预告→快报→财报三段确认链, 实数比预告精确 | 用行内ann_date非period; 仅部分公司发稀疏 |
| 中 | `repurchase` | 股票回购 | [需同步]需同步 | 鱼头: 注销式回购=低估+增厚EPS, 与增持互补 | ann_date锚; proc分预案(预期)vs实施(已发生); 须市值归一 |
| 中 | `pledge_stat` | 股权质押率 | [需同步]需同步 | 鱼尾风控: 高质押=平仓盘悬顶+控制权险 | [警]核证ann_date vs end_date真实披露日; 季度慢变当风控分层 |
| 中 | `disclosure_date` | 财报披露计划 | [需同步]需同步 | timing gate + **校准其他财报PIT锚的真相源** | [在库]罕见天然前瞻PIT(已知未来披露窗); 本身非alpha是PIT工程辅助 |
| 中 | `dividend` | 分红送转(高送转) | [在库]已在库(复权链) | 鱼头: 高送转预案=填权炒作题材 | [警]必用ann_date锚严禁ex_date; 取预案首次公告防重复 |
| 低 | `fina_audit` | 财务审计意见 | [需同步]需同步 | 排雷: 非标审计=造假/持续经营硬警报 | 年频极稀疏当负向排除门; 2000积分 |
| 低 | `anns_d` | 全量公告文本兜底 | [需同步]需同步 | 捕捞无结构化接口的事件 | [高危]曾触发网关357s风控; 噪声大须NLP; 仅结构化不够才考虑 |

## 诚实分级: 描述性档案维度 vs 撞 R1 无条件截面

| 类别 | 候选 | 安全用法(条件化) | 误用风险 |
|---|---|---|---|
| 前瞻事件催化(鱼头) | forecast/stk_holdertrade/express/repurchase/dividend | 事件驱动低频=天然低换手, R1友好 | 低(事件稀疏) |
| 内生延续/反转(鱼身/鱼尾) | moneyflow/stk_factor_pro/bak_daily/margin/nineturn | episode内当确认/出场gate | [警]**高**: 全市场月度截面换仓=撞R1(必包stage gate内) |
| 情绪强度(题材) | limit_step/kpl_list | 题材episode内强度分层 | 稀疏; R2涨停一字板买不进险高 |
| 风控负向门 | pledge_stat/fina_audit | 命中即剔除/降权单边门 | 当连续因子用=误用 |
| PIT工程辅助 | disclosure_date | 校准财报锚, 本身非alpha | 当因子用=误用 |
| dead/高风险 | hk_hold(停)/hsgt_top10(疑停)/anns_d(风控) | hk_hold仅历史归因; anns_d末位兜底 | hk_hold决策面≈0 |

## 决策建议 (主升浪猎手优先级)

1. **零成本先做**: `forecast` 业绩预告 (L3, dossier 最大空白=前瞻预期链, ann_date 干净, 已在库) → 鱼头催化解释。
2. **高价值需同步**: `stk_holdertrade` 股东增减持 (L3, in_de 现成方向, 内部人信号最硬) — 注册前单日实弹核证 + in_de 方向实测。
3. **内生确认补强**: `moneyflow` 订单分档 + `stk_factor_pro` 261因子 (L2, 已在库未用) — 但**必包 stage gate 内**, 转正走含成本 C-R1, 误用风险最高。
4. **GS 公式相关**: `stk_nineturn` 神奇九转 (用户 GS 主图公式核心之一: 动态均线+神奇九转) — data_start 短需注意 OOS。
5. **暂不接**: `hk_hold`(停披露 dead-forward) / `anns_d`(网关风控未解)。

相关: `backend/config/sync_registry.yaml` (注册) · `backend/services/dossier.py` (loader 接线) · `docs/stock_dossier_master_design.md` §1.5 (L2/L3 判据) · `docs/strategy_validation_contract.md` (C-R1/R2 含成本裁决)。
