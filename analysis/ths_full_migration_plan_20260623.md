# 同花顺 (THS) 全套迁移方案

> 状态: analysis 草稿 (主会话 review 后才促进; 探索沙盒纪律外, 因这是控制面方案文档)
> 日期: 2026-06-23
> 作者: 主升浪猎手 session (4 路 fan-in 调研合成)
> Owner 决策 (已锁): **全项目单一数据供应商 = 同花顺 (THS)**; 删申万 (SW) + 东财 (DC) + 通达信 (TDX) 的行业/概念/资金流。
> 真相源: `backend/config/tushare_api_catalog.json` (一手 HTML 目录解析) + `backend/config/sync_registry.yaml` + fan-in 4 路调研。
> 红线引用: CLAUDE.md §4.1 PIT / §4.3 单一源 + 删源不删数据双轨 (铁律11) / §4.2 异常高数字 / mio 真金白银。

---

## 第 0 节 · TL;DR (一页结论)

| 维度 | 裁决 |
|---|---|
| THS 资金流 (个股/行业/概念) | **[OK] 可行** — 有 trade_date PIT 戳, grain 双向, 单日全市场单次拉完, 直接替 DC 资金流 |
| THS 板块指数行情 (ths_daily) | **[OK] 可行** — 有 trade_date, 替 dc_index/sw_daily 的板块指数行情 |
| THS 板块目录 (ths_index) | **[OK] 可行** — 当前快照目录, 板块元数据 (用于循环拉成分) |
| **THS 个股↔板块归属 (ths_member)** | **[NO] 硬伤 — 需用户拍板** — `in_date/out_date/weight` 全 "(暂无)", **latest-snapshot = PIT 泄漏红线**, 且**无任何历史成分接口**可补 |
| 整体可行性 | **有条件可行**: 资金流/指数行情干净可迁; **成员归属 (行业/概念 membership) 是唯一硬伤**, 决定整套方案成败 |

**一句话**: THS 资金流和指数行情可以干净迁移; 但 **THS 个股行业/概念归属只有"当前快照"、无历史区间、且 tushare 不提供 THS 历史成分接口** —— 这正是项目 2026-06-23 刚把申万退役掉的**同一种 latest-snapshot 泄漏**, 申万至少还有 `index_member_all` 的 `in_date/out_date` 原生 PIT 兜底, **THS 连这个都没有**。这是比申万更弱的 PIT 地基, **必须回报用户拍板** (见第 4 节)。

---

## 第 1 节 · 可行性裁决 (逐接口 + PIT 红线)

### 1.1 六接口规格汇总 (来自一手 catalog)

| 接口 | doc_id | grain | trade_date/PIT | 单页上限 | 历史起点 | 裁决 |
|---|---|---|---|---|---|---|
| `ths_index` 板块目录 | 259 | 一行=一板块指数 (目录元数据) | [WARN] 无日期, 当前快照目录 (含 list_date 但无版本) | 5000 行 (6000 积分), 一次全量勿循环 | 文档未标, 需实弹 | [OK] (元数据用途, 非 PIT 敏感) |
| `ths_member` 板块成分 | 261 | 一行=(板块, 成分股) 对 | **[NO] 无 PIT**: in_date/out_date/weight 全 (暂无); 只有 is_new(Y/N) | 未给行数, 按板块循环, 200次/分 | **N/A (无历史)** | **[NO] 硬伤** |
| `moneyflow_ths` 个股资金流 | 348 | (交易日, 个股), 双向 grain | [OK] trade_date=PIT 戳, 盘后更新 | 6000 行; 全市场~5400<6000 单日一次拉完 | 文档未标, 需实弹 | [OK] |
| `moneyflow_ind_ths` 行业资金流 | 343 | (交易日, THS行业板块) | [OK] trade_date=PIT 戳 | 5000 行; 行业~90<<5000 | 文档未标, 需实弹 | [OK] 资金流本身; [WARN] 归属映射受 ths_member 拖累 |
| `moneyflow_cnt_ths` 概念资金流 | 371 | (交易日, THS概念板块) | [OK] trade_date=PIT 戳 | 5000 行; 概念~400<5000 | 文档未标, 需实弹 | [OK] 资金流本身; [WARN] 归属映射受 ths_member 拖累 |
| `ths_daily` 板块指数行情 | 260 | (交易日, 板块指数) | [OK] trade_date | (调研截断, 需补) | 文档未标, 需实弹 | [OK] |

### 1.2 ths_member 无 PIT = 硬伤 (核心裁决, 第一性原理拆解)

**真相源核对** (catalog `tushare_api_catalog.json:27290-27309`, doc_id=261):
```
weight   "权重(暂无)"
in_date  "纳入日期(暂无)"
out_date "剔除日期(暂无)"
is_new   "是否最新Y是N否"
```
"(暂无)" = tushare 实际不返回这三列。**只有 is_new(Y/N) 当前标志** = 标准的 latest-snapshot。

**为什么这是红线 (不是程度问题)**:
- CLAUDE.md §4.5 反例已白纸黑字: "申万 index_member_all 默认只拉 is_new='Y' (当前成分) → out_date 100% NULL = **latest-snapshot leakage 变体**"。
- sync_registry.yaml:423-424 注释 (申万退役真因): "申万退役真因 = 只有快照无历史 → index_member_all 带 in_date/out_date 是 PIT 原生解"。
- **即项目 2026-06-23 (f523b4d7) 刚做的事, 是从 latest-snapshot 源 (通达信) 切到 PIT-native 源 (申万 index_member_all 含 is_new=N 历史区间)。THS membership 连 is_new=N 历史都没有 → 比通达信/申万都更弱。**

**后果 (真金白银视角)**: 用 THS membership 做回测时, 一只股票"现在属于 AI 概念"会被错误地回填到它**还没被纳入 AI 概念**的历史日期上 → 行业/概念相对强弱、sector_momentum、概念资金流归属全部带未来信息 → 回测虚高、live 崩。这正是 §4.2 "异常高数字=leakage 警报"会触发的那类泄漏。

### 1.3 有没有解? (穷尽真相源后的三条路)

| 路径 | 可行性 | 评估 |
|---|---|---|
| (a) tushare 有 THS 历史成分接口吗? | **[NO] 无** | catalog 全目录无 THS membership-history 接口; ths_member 是唯一 THS 成分接口, 且无 PIT |
| (b) 自建 forward PIT (daily by_trade_date 快照, 同 dc_member 模式) | **[WARN] 部分可行** | 从注册日起**向前**每日快照成分, 用 built_at 戳成 as-of 历史。**但只能覆盖注册日之后**, 注册日之前历史**永久缺失** (无法重建) |
| (c) 接受 current-only + 显式标注 (membership 只服务"当前", 回测禁用) | **[WARN] 降级可接受但伤回测** | 行业/概念归属只用于 live 当前画像 (context), 不进任何回测 JOIN。但项目的 sector-relative 特征 (rally_episode_strata / 概念资金流归属) 需要历史归属 → 这些特征**直接报废** |

**裁决**: (b)+(c) 组合是工程上能做的最好, **但仍有不可弥补的缺口** (注册日前历史 + 回测期归属泄漏)。

> **mio 失败先承认**: 不合格就是不合格。THS membership 的 PIT 地基**弱于刚退役的申万**。在"行业/概念归属"这一维度上, 全套 THS 是一次**地基降级**, 不是平迁。这必须如实回报, 不能用"自建 forward 快照能解"糊过去 —— forward 快照解不了回测期历史。

### 1.4 必须先实弹核证的开放问题 (限流 120/分 下怎么核)

> 限流真相源 = sync_registry.yaml:34-36 = **单接口 120 次/分, 多接口合计 200/分, 并发 2** (用户 2026-06-17/19 决议; 注: 用户口头记忆的 "180/分" 在 config/代码/单测中均无记录, 系统一直是 120; 若要提 180 须先实弹核证 tinyshare 真放宽, 在拿到证据前按 120 设计)。

| # | 开放问题 | 核证方法 (单发, 不跑批; 限流下 1-2 次调用即可) | 阻塞什么 |
|---|---|---|---|
| Q1 | ths_member 是否真的 in_date/out_date 全 NULL? | 单调 `ths_member(ts_code='885800.TI')`, 看返回列实际有没有 in_date 值 | **整套可行性** (若实际有值则硬伤消失) |
| Q2 | ths_member 无参是否返回全表? (文档未保证) | 单调 `ths_member()` 无参, 看行数 vs 按板块循环 | backfill 调用数估算 |
| Q3 | moneyflow_ths 历史起点 (history_start=None) | 单调 `moneyflow_ths(ts_code='000001.SZ', start_date='20200101', end_date='20200110')` 看最早有数据日 | 资金流回填 data_start |
| Q4 | moneyflow_ind_ths / moneyflow_cnt_ths 历史起点 | 同上, 各单调一次取早期单日 | 行业/概念资金流 data_start |
| Q5 | ths_daily 历史起点 + 单页上限 (调研截断) | 单调 ths_daily 取单板块全史 + 看 catalog doc_id=260 完整 limits | 板块指数行情回填 |
| Q6 | ths_index 板块总数 (概念+行业+特色) | 单调 `ths_index()` 无参或分 type, 数行数 | 成分循环 backfill 调用数 (板块数×天数) |
| Q7 | THS 行业 vs 概念是否同一 type 体系区分 | 看 ths_index 的 type 列枚举 (N概念/I行业/...) | 物化 dim 时怎么分行业表 vs 概念表 |

**核证纪律**: 全部单发核证 (≤10 次调用), 远低于 120/分限流; 落 `analysis/非tushare源_双轨_20260623.md` 或本文件附录。**Q1 是 go/no-go 闸** —— 若 Q1 证实有 in_date, 整个硬伤消失, 方案升级为干净平迁。

---

## 第 2 节 · 迁移分阶段步骤 (可逆优先, 删最后)

> 总原则 (mio 改前双审计 + 删源不删数据铁律11): **先注册新源 → backfill → 物化 → repoint 消费方 → watermark/gate → 双轨核对达标 → 才物删旧源**。每阶段独立可 commit, 任一阶段失败可回滚不影响上游。

### 阶段 ① 注册 + backfill 同花顺 6 域

新增 6 个 sync_registry.yaml domains (零域专属代码, sync_runner 表驱动):

| domain | api | target_table | batch_mode | grain | data_start | 备注 |
|---|---|---|---|---|---|---|
| `ths_index` | ths_index | raw_tushare_ths_index | full_refresh | [ts_code] | N/A | 板块目录, 当前快照, 每日 CREATE OR REPLACE |
| `ths_member` | ths_member | raw_tushare_ths_member | by_code_list (循环板块) | [ts_code, con_code, trade_date] | 注册日 (Q2/Q6 后定) | **加 trade_date=built_at 戳成 forward PIT, 同 dc_member 模式** |
| `moneyflow_ths` | moneyflow_ths | raw_tushare_moneyflow_ths | by_trade_date | [ts_code, trade_date] | Q3 实测 | universe_filter:true; 个股资金流 (万元) |
| `moneyflow_ind_ths` | moneyflow_ind_ths | raw_tushare_moneyflow_ind_ths | by_trade_date | [ts_code, trade_date] | Q4 实测 | 行业资金流 (亿元, 口径异于个股) |
| `moneyflow_cnt_ths` | moneyflow_cnt_ths | raw_tushare_moneyflow_cnt_ths | by_trade_date | [ts_code, trade_date] | Q4 实测 | 概念资金流 (亿元) |
| `ths_daily` | ths_daily | raw_tushare_ths_daily | by_trade_date 或 by_code_list | [ts_code, trade_date] | Q5 实测 | 板块指数行情 |

**所有 domain defaults 继承**: rate_limit 120/200/2, zero_row_policy:fail, target_db:tushare_raw, fetch_timeout_seconds:120。

**ths_member forward PIT 实现 (硬伤缓解, 不是消除)**:
- batch_mode 用 by_code_list 按板块循环 (Q2 若证实无参全拉则改 full_refresh)。
- 落表强制写 `trade_date = 当日交易日` + `built_at = now()`, grain 含 trade_date → 同一板块每日一行快照, 区间 = [trade_date, 下次成分变化日)。
- 物化 v_ths_member_pit 视图: as-of t 取 `MAX(trade_date) <= t` 的快照 (与 dc_member as-of 同模式)。
- **缺口标注**: 注册日前历史无法重建, v_ths_member_pit 在 data_start 前返回 NULL (不是猜测), 消费方须容忍 NULL。

**backfill 调用数 / 耗时估算 (@120/分 单接口)**:

> 假设交易日数: 2020-01 至今 ≈ 1330 交易日 (若 Q3/Q4/Q5 证实历史只到 2023/2024 则相应缩短)。

| domain | 调用模式 | 调用数 | @120/分耗时 |
|---|---|---|---|
| moneyflow_ths | 1 次/交易日 (全市场单次<6000) | ~1330 | ~11 分钟 |
| moneyflow_ind_ths | 1 次/交易日 | ~1330 | ~11 分钟 |
| moneyflow_cnt_ths | 1 次/交易日 | ~1330 | ~11 分钟 |
| ths_daily | 若 by_trade_date 1 次/日 | ~1330 | ~11 分钟 |
| ths_index | 1 次 (全量) | ~1 | <1 分钟 |
| ths_member | 按板块循环×天数 (forward 起每日) | 板块数(~490)×天数 | **见下** |

> **ths_member 是 backfill 瓶颈**: 若按"每个板块每个交易日"快照 = 490 板块 × 1330 日 = 65 万次调用 ÷ 120/分 ≈ **90 小时**, 不可接受。
> **缓解策略**: forward PIT 只需"成分变化时补一行"。实务做法 = (a) 初始拉一次全板块成分 (490 次≈4 分钟); (b) 此后**每日只快照一次全板块** (无参或循环), 用 grain MERGE 幂等去重, 只有变化才新增行。即 backfill 阶段不回填历史 (无历史可回), 从注册日起 forward 累积。**注册日前 = 永久空白** (这是硬伤的体现, 非工程可补)。

**单页截断防御** (§4.5 top_inst 1000 整反例 / dc_member 5000 整反例): ths_index/moneyflow_ind/cnt 单页 5000, moneyflow_ths 6000 —— backfill 时若单批返回正好等于上限值 = 疑似截断, 必加 page_limit 哨兵 + min_rows_per_batch sanity。

### 阶段 ② 物化 dim_stock_ths_industry / dim_stock_ths_concept (层级推导)

THS 无独立"级别"字段, 只有 ths_index.type 枚举 (N概念/I行业/R地域/S特色/...)。层级推导方法:

- **行业表 dim_stock_ths_industry**: 从 ths_index 取 type='I' (行业指数) 的板块 → JOIN ths_member 取成分 → 列 = (ts_code 股票, ths_ind_code 板块, ths_ind_name, trade_date)。
  - THS 行业是**单层**还是多层? 调研未确认 THS 是否有 L1/L2/L3。**[需 Q7 实测]** —— 若 THS 行业只有单层, 则不能 1:1 替申万 L1/L2/L3 三列。industry.py 当前依赖 `tdx_l1/l2/l3` 三列别名 (实为申万三级)。
  - **若 THS 单层**: industry.py 的三级 API 需降级或用 THS 概念/特色补充层次 → 影响面大, 须在阶段 ③ 评估。
- **概念表 dim_stock_ths_concept**: type='N' (概念) → 成分 → 多对多 (个股属多概念)。
- **as-of PIT 视图 v_ths_member_pit**: 同 1.3(b), 只服务注册日后 forward 历史。

> **第一性原理警告**: 申万行业 = 官方 3 级 31/131/337 桶 (细粒度, mio 真相源双用途的"天花板"). THS 行业的桶定义/层级数是 THS 自己的体系, 与申万**不可比** (§4.5 taxonomy 切源不可比反例)。切到 THS = 又一次 taxonomy 版本切换, 须打 taxonomy_version 戳, 跨切换点历史特征不可拼接。**这点叠加 PIT 硬伤, 进一步削弱行业维度地基。**

### 阶段 ③ repoint 全部消费方 (逐个列, 删最后)

> 当前行业真相源 = `dim_stock_sw_industry` (申万, f523b4d7 刚切, 列名 tdx_l1/l2/l3 = 申万三级位置别名)。
> 资金流当前 = DC (raw_tushare_moneyflow_dc/moneyflow_ind_dc) + 概念 DC (dc_member/dc_index)。

**3a. 行业归属消费方** (f523b4d7 已把这 6 个 + 6 测试从 tdx 切到 sw, 现需再切到 ths):

| # | 消费方 | 文件 | 改法 |
|---|---|---|---|
| 1 | 行业解析单点 | `backend/services/industry.py` (INDUSTRY_TABLE=dim_stock_sw_industry @27) | 改 INDUSTRY_TABLE → dim_stock_ths_industry; 评估三级别名是否成立 (Q7) |
| 2 | 行业上下文引擎 | `backend/services/industry_context_engine.py` | repoint |
| 3 | 机构 L2 指标 | `backend/services/institution_l2_metrics.py` | repoint |
| 4 | 评分 | `backend/services/scoring.py` | repoint |
| 5 | 板块动量 | `backend/services/sector_momentum.py` | repoint + 板块集合变须全量重算 (taxonomy 变) |
| 6 | 个股图谱读 | `backend/services/stock_graph_read.py` | repoint |
| + | 申万物化步 | `scripts/daily_update.sh` Step2.96c (build_sw_industry_view) | 改建 dim_stock_ths_industry, 退役 build_sw_industry_view |
| + | 申万物化脚本 | `backend/scripts/build_sw_industry_view.py` | 退役, 新建 build_ths_industry_view.py |
| + | watermark 双配置 | `source_watermarks.py` + `update_watermark_sla.py` (industry_sw 域) | industry_sw → industry_ths |
| + | 6 测试 fixture | test_industry_context_engine/institution_l2_metrics/sector_momentum/stock_graph_read/stock_scoring_integration/stock_turtle_engine | fixture 表名 + 列改 ths |

**3b. 机构行业统计** (fan-in 标注, 还在读 TDX 的残留): `backend/routers/updater_institution.py:421-477,532` (INNER JOIN dim_stock_tdx_industry) → repoint 到 ths (或随 build_industry_stat 退役, 见阶段 ⑤)。

**3c. 资金流消费方** (DC → THS):
- 个股资金流: 搜 `raw_tushare_moneyflow_dc` 全部消费方 → 切 raw_tushare_moneyflow_ths (注意口径: THS 大/中/小三档 + _rate, DC 是 net_amount/net_amount_rate, **字段不 1:1**, 消费方须改字段映射)。
- 行业资金流: raw_tushare_moneyflow_ind_dc → raw_tushare_moneyflow_ind_ths (单位 DC vs THS 亿元口径核对)。
- 概念资金流: dc_member/dc_index/moneyflow_ind_dc 概念链 → moneyflow_cnt_ths。

**3d. 口径自洽红线** (§2 坑库 "口径混用=leakage"): **flow vendor 必须 = membership vendor**。切 THS 后, 资金流 (THS) 与归属 (THS) 同源 = 自洽 [OK]。但归属本身有 PIT 硬伤 → 资金流归属到历史板块时仍带泄漏。

### 阶段 ④ watermark / gate / moth 改

| 对象 | 改动 |
|---|---|
| `source_watermarks.py` | industry_sw → industry_ths; 加 moneyflow_ths/ind/cnt + ths_daily 域水位 |
| `update_watermark_sla.py` | 同步 SLA 域名; 删 SW/DC 资金流 SLA 行 |
| `storage_retention.yaml` | 加 raw_tushare_ths_* retention; 删 dim_stock_tdx_industry_history (204行) / raw_tdx_industry_file_snapshot (239行) retention; 评估 SW/DC 表 retention |
| moth 断言 | 加 ths membership/flow claims-vs-reality 断言; 删 SW/DC/TDX 相关断言 |
| moth `exploration-isolated-in-sandbox` | 不受影响 (本迁移是控制面非探索) |
| 口径自洽 gate | 若有 flow-vendor=membership-vendor 检查, 更新为 ths |

### 阶段 ⑤ 删申万 + 东财 + 通达信 (双轨核对达标后物删)

> 铁律11: 删源不删数据需先双轨核对; 但本场景部分是"删数据" (用户决议: 单一 THS 下 SW/DC/TDX 行业概念资金流全是无效数据 → 删)。区分两类:
> - **删源不删数据** (有 THS 等价 + 双轨达标): 资金流类先双轨核对 (具体日+股+字段一致率, 不看汇总 verdict, §4.5 比亚迪反例) → 切 SERVE 主源 → 物删 raw。
> - **直接删数据** (无 THS 等价 OR 用户判定无效): 用户已锁"删 SW/DC/TDX 行业概念资金流" → 走 db_lifecycle_delete + deletion_record。

**5a. 通达信现有残留** (fan-in [通达信残留] 清单, 即便 f523b4d7 已切 SW, 这些仍 dormant/dead/stale_registry, 一并清):

| 对象 | 文件 | 状态 |
|---|---|---|
| _step_sync_industry_with_hooks | updater_institution.py:308-415 | dormant (可经 API 触发) |
| _step_sync_industry + build_industry_stat wrapper | updater.py:92,404-418,464-483 | dormant |
| _step_build_industry_stat_sync (3× INNER JOIN tdx) | updater_institution.py:417-532 | dead_code (读冻结表) |
| STEPS/HARD_DEPS sync_industry/build_industry_stat | updater_plan.py:53,64,87,97,100,104,112,114,115,123 | stale_registry (钉死 DAG) |
| audit smart-plan sync_industry | audit.py:484,1332,1449,1452,1617,1633,1711 | stale_registry |
| tdx_industry_client.py | 全文件 | dead_code |
| tdx_industry_names.py | 全文件 | dead_code |
| clients_registry tdx_industry_client + block_client | clients_registry.py:62-89 | stale_registry (记为活主源, 与§4.3矛盾) |
| data_routes '申万行业'→dim_stock_tdx_industry 误配 | data_routes.py:49-68 | stale (命名误导) |
| schema_core TDX DDL | schema_core.py:487-517 | **必删否则 schema-init 复活 (重建循环)** |
| schema_migrations TDX 索引 + drop plan | schema_migrations.py:298-300,596 | 随表退役 |
| storage_retention TDX 条目 | storage_retention.yaml:204-208,239-246 | 删 |
| block_client.py + stock_detail_read.py (dim_stock_tdx_block) | block_client.py:185-212 / stock_detail_read.py:95-96 | repoint 到 ths 概念 或退役 |

**5b. 申万退役** (f523b4d7 刚建, 现要删): dim_stock_sw_industry + build_sw_industry_view.py + sync_registry sw_daily/index_member_all/index_member_all_hist (行业用途部分) + v_sw_industry_pit。
> **注意**: index_member_all 还兼"KPI 超额 HS300 真相源"用途 (index_daily_benchmark 是另一域)。删 SW **行业**部分前须确认 index_member_all 不被指数基准链消费 —— 若只服务行业则可删, 若兼指数则保留指数部分。

**5c. 东财退役**: raw_tushare_moneyflow_dc / moneyflow_ind_dc / dc_member / dc_index + 各 sync_registry 域 + 消费方 (已在 ③ repoint)。

**物删纪律**: 全走 `db_lifecycle_delete` + `deletion_record`; 删前 schema-init 重建路径同切 (否则重建循环, §2 坑库 #1); 删后 orphan 审计 + 0 residue 验证 (post-fix-audit 5 步)。

---

## 第 3 节 · 无残留 checklist (验收闸)

> 来源: fan-in [全量fan-in] + [通达信残留] 两份清单 + f523b4d7 已切范围。每项迁移必 touch, 作为 commit 前逐项验收。**未逐项 yes 不算迁移完成。**

### 3.1 表 (raw / dim / mart / view)
- [ ] 新建: raw_tushare_ths_index / ths_member / moneyflow_ths / moneyflow_ind_ths / moneyflow_cnt_ths / ths_daily
- [ ] 新建: dim_stock_ths_industry / dim_stock_ths_concept / v_ths_member_pit
- [ ] 物删: dim_stock_tdx_industry / dim_stock_tdx_industry_history / raw_tdx_industry_file_snapshot / dim_stock_tdx_block / dim_tdx_block_catalog
- [ ] 物删 (用户锁删): dim_stock_sw_industry / v_sw_industry_pit
- [ ] 物删: raw_tushare_moneyflow_dc / moneyflow_ind_dc / dc_member / dc_index
- [ ] 评估: index_member_all / sw_daily / raw_tushare_index_member_all (行业 vs 指数基准用途分离)

### 3.2 代码 (service / router / client)
- [ ] repoint: industry.py / industry_context_engine.py / institution_l2_metrics.py / scoring.py / sector_momentum.py / stock_graph_read.py
- [ ] repoint/退役: updater_institution.py:421-477,532 (industry-stat JOIN) / stock_detail_read.py:95-96
- [ ] 退役整模块: tdx_industry_client.py / tdx_industry_names.py / block_client.py / build_sw_industry_view.py
- [ ] 退役 router 步: updater_institution.py:308-415 / updater.py:92,404-483 / updater_institution.py:417-532
- [ ] 新建: build_ths_industry_view.py (或等价物化脚本)
- [ ] 注释翻转: akshare_client.py:9 / api_schemas.py:28 / data_deprecation.py:26,56 (replacement_table → ths)

### 3.3 注册表 / 配置
- [ ] sync_registry.yaml: +6 THS 域; -DC 资金流/概念域; -SW 行业域 (保留指数基准部分待评估)
- [ ] clients_registry.py:62-89: 删 tdx_industry_client + block_client ClientSpec (否则 dim_data_asset/UI 显活)
- [ ] data_routes.py:49-68: 删/改 '申万行业'/'板块概念' 误配路由
- [ ] schema_core.py:487-517: 删 TDX 行业/板块 DDL (防 schema-init 复活)
- [ ] schema_migrations.py:298-300,596: 删 TDX 索引 + drop plan
- [ ] updater_plan.py / audit.py: 删 sync_industry / build_industry_stat 的 STEPS/HARD_DEPS/smart-plan 引用 (解 DAG 钉死)
- [ ] daily_update.sh: 退役 Step2.96c build_sw_industry_view; 接 THS 物化步
- [ ] data_layers.yaml: raw_tdx_industry 相关层声明清理

### 3.4 域 / watermark / retention
- [ ] source_watermarks.py: industry_sw → industry_ths; +THS 资金流域
- [ ] update_watermark_sla.py: 同步 + 删 SW/DC SLA 行
- [ ] storage_retention.yaml: +THS history/snapshot retention; -TDX (204-208,239-246) ; 评估 SW/DC retention
- [ ] mart_data_source_watermark: 删 SW/DC/TDX stale 水位行 (post-fix DB residue check)

### 3.5 doc / test / 控制面
- [ ] PROJECT_INDEX.md 活索引: 表/service/script/yaml/反例 节同步
- [ ] FEATURE_MAP.md: scripts/chunkyctl map 重生
- [ ] goal.md / SESSION_HANDOFF.md: 若 delivery/data 真相源变化
- [ ] 测试 fixture: 6+ 测试表名/列改 ths
- [ ] 新增防回退测试: ths_member forward PIT as-of 视图单测 (注册日前返 NULL 不猜测)
- [ ] §4.5 反例表: 沉淀 "THS membership 无 PIT 硬伤" + "taxonomy 第三次切换不可比"
- [ ] codegraph sync . + complexity 双扫 (substantial change)
- [ ] moth assert --repo . 全绿

### 3.6 双轨核对 artifact
- [ ] analysis/非tushare源_双轨_20260623.md: DC资金流 vs THS资金流 逐日+股+字段一致率 (≥99%, 不看汇总 verdict, 定位 max diff 到具体日+股)
- [ ] deletion_record: 每张物删表登记

---

## 第 4 节 · 风险 / 回报点 (需用户拍板)

### 4.1 [NO] 硬伤 — 必须用户拍板 (go/no-go)

**THS 个股行业/概念归属 (ths_member) 无 PIT 历史 = latest-snapshot 泄漏红线**

| 项 | 内容 |
|---|---|
| 事实 | ths_member 的 in_date/out_date/weight 全 "(暂无)" (catalog doc_id=261 实证); tushare 无任何 THS 历史成分接口 |
| 红线 | CLAUDE.md §4.1 PIT + §4.5 latest-snapshot leakage 变体 (与刚退役的通达信/申万 is_new=Y 同类, 但 THS 连 is_new=N 历史都没有) |
| 工程能做的最好 | forward PIT (注册日起每日快照累积) —— **只覆盖注册日之后**, 注册日前历史永久缺失, 回测期归属仍泄漏 |
| 真金白银后果 | 行业/概念相对强弱、sector_momentum、概念资金流归属、rally_episode_strata 的 sector-relative 特征 → 回测带未来信息, live 期望低于回测 |
| 对比基线 | 申万 index_member_all 有 in_date/out_date 原生 PIT (is_new=N 历史区间) —— **切 THS = 行业/概念维度的 PIT 地基降级** |

**给用户的三个选项**:
1. **接受降级 + 隔离用途**: THS membership 只服务 live 当前画像 (context), **禁进任何回测 JOIN**; 行业/概念历史特征 (sector-relative / 概念资金流归属) **全部报废或冻结**。资金流/指数行情正常迁。 → 项目还能用 THS 资金流, 但丢行业/概念历史 alpha 维度。
2. **行业维度保留申万** (混合方案, 违"单一源"但守 PIT): 资金流/指数行情切 THS, **行业/概念归属保留申万 index_member_all** (唯一有 PIT 的源)。 → 违背"全项目单一供应商"决策, 但守住 PIT 红线。需用户在"单一源" vs "PIT 红线"间裁决。
3. **全套 THS + 接受 forward-only PIT**: 明确接受注册日前无历史 + 回测期归属泄漏, 用 forward 快照从今天起累积, 1-2 年后历史够用再做行业/概念历史 alpha。 → 短期行业/概念历史维度不可用, 长期自建。

> **mio 真金白银裁决倾向 (供参考, 非替用户决策)**: PIT 红线 (§4.1/§4.2) 在项目宪法里是"不折中"级 (§4.5 chain leakage 折中致 RankIC +60% 假象的反例)。"单一供应商"是运维简单性偏好。当**简单性偏好**撞上**资金安全红线**, 红线优先 —— 选项 2 (行业归属保留申万 PIT) 最守红线; 但这是用户的价值权衡, 必须用户拍板, 不由 AI 替决。

### 4.2 [WARN] 次级风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| THS 行业层级数未知 | THS 是否有 L1/L2/L3 三级? industry.py 依赖三级别名 | Q7 实测; 若单层则 industry.py 三级 API 须降级 |
| taxonomy 第三次切换不可比 | TDX→SW (06-16) →SW→THS (本次) = 桶定义又变, 跨切换点历史不可拼 | 打 taxonomy_version 戳分段, 禁跨版本 partition (§4.5) |
| 资金流字段不 1:1 | THS 大/中/小三档 vs DC net_amount; THS 行业亿元 vs 个股万元 | 消费方改字段映射 + 单位口径核对 |
| index_member_all 双用途 | 兼行业 + KPI 超额基准 | 删行业部分前确认基准链不依赖 |
| schema-init 重建循环 | 删 TDX/SW 表不删 schema_core DDL → 复活 | DDL 同删 (3.3 已列) |
| 历史起点未知 | moneyflow_ths/ind/cnt/ths_daily history_start=None | Q3/Q4/Q5 实弹核证, 不假设 2020 |

### 4.3 [OK] 干净可迁部分 (无硬伤)
- THS 三个资金流接口 (moneyflow_ths/ind/cnt): trade_date PIT 戳, grain 双向, 单日全市场单次拉完 → 干净替 DC 资金流。
- ths_daily 板块指数行情: trade_date PIT → 替 dc_index/sw_daily 板块指数行情。
- ths_index 板块目录: 当前快照元数据, 非 PIT 敏感, 用于循环拉成分。

---

## 附录 A · 限流真相源澄清 (供主会话同步)

- 系统记录的限流 = **120/分单接口, 200/分多接口, 并发 2** (sync_registry.yaml:34-36 / sync_runner.py:226 / test_sync_runner_ratelimit.py / CLAUDE.md §4.3 四处一致)。
- 用户口头记忆的 "180/分" 在 config/代码/单测中**均无记录** (仓库内所有 180 均为无关数字: data_start 20180102 / backoff [60,120,180])。
- CLAUDE.md §4.3 写的是 120, 与实际一致, **无 discrepancy, 不需改文档**。
- 若用户确要提到 180 (tinyshare 实际放宽), 是**改值决策非修笔误**: 改 sync_registry.yaml:34 + CLAUDE.md:115 + 单测断言 + 注释, 且**改前须实弹核证 tinyshare 真给 180** (no-hardcode 红线)。**在拿到 180 实测证据前, 本方案所有 backfill 估算按 120 设计。**

## 附录 B · 待实弹核证清单 (Q1-Q7, 第 1.4 节, ≤10 次调用)
Q1 ths_member in_date 是否真 NULL (go/no-go 闸) / Q2 无参全拉 / Q3-Q5 资金流+指数历史起点 / Q6 板块总数 / Q7 行业层级数 + type 体系。全单发, 远低于 120/分限流, 落本文件或 analysis/非tushare源_双轨_20260623.md。
