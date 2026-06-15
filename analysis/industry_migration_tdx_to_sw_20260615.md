# 行业分类迁移: 通达信 → tushare 申万 PIT (2026-06-15)

> 状态: live (执行中, S1 DONE)。owner=本文件。上承 Workflow wf_2ef22e73 scoping + 用户决议。
> 缘起: 用户"咋还有通达信啊?行业分类用东财吧?" → 实测发现行业分类**仍是通达信 (非PIT)**, 是活的潜在泄漏源。
> 用户裁决: **优先做全套迁移** ("行业都不对, 多因子探索还有啥用, 热点行业都不准, 这不偏离 loop 主线" — 行业是探索地基) +
> 删 STALE 孤儿 mart 走视图 (奥卡姆) + 接受 taxonomy 历史不可比 (真金白银红线已知)。

## 北极星对齐 (为何这是 /loop 前提非偏离)
行业分类错 → sector-relative 特征 / 行业中性化 / 热点行业轮动全错 → 多因子探索建在错地基上 = 空中楼阁。
先把行业地基修对 (PIT + 申万), 探索才有意义。

## 现状 (Workflow 实测, 非假设)
- **通达信仍是活的行业源**: dim_stock_tdx_industry (5624, PK=stock_code, **无PIT标记** updated_at 全量覆盖) + _history (67345, snapshot_date 12个快照窗 04-25~06-12)。同步链活 (daily_update Step2j → tdx_industry_client, tdxhub 源)。东财(dc)是**概念**源不是行业分类。
- **mart_stock_industry_pit (39259)**: 通达信源区间式 PIT 表, 但 **STALE** (built_at 2026-05-07 只吃6/12快照) + builder build_industry_pit.py/services/industry_pit.py **reset已删** (639e0dfb) = 孤儿物化表; 含 5611 行 current_label_fallback = latest-snapshot leakage 段。
- **live 消费面小**: 真要 repoint = **industry.py 单点** (INDUSTRY_TABLE 常量 + load_industry_map + resolve_industry) + signals_v2.py 一处裸 JOIN; 覆盖 workbench/signals/stock_graph 活链。**~17 dead_orphan** (下游 mart MISSING/0行) 不 repoint 走独立退役。
- **latent bug**: resolve_industry 收 ref_date 但忽略它 = 读路径本身无 PIT (换源前就漏)。

## taxonomy 红线 (真金白银, 已入 CLAUDE §4.5)
申万 SW2021 **31/131/337** (官方 31/134/346, 落库 L2缺3/L3缺9 因成分<5不发布) vs 通达信 **13/56/76** — **非 1:1, 不同桶划分**。
后果: sector-relative 特征 (*_tdx_l1_rel) PARTITION BY 桶从 13→31, 跨切换点历史特征/RankIC **不可比**; sector_momentum 板块从 13→31 须全量重算。
**应对**: 切换点打 `taxonomy_version` 戳 (tdx_v / sw2021_v), 特征工程按 version 分段, **禁跨版本 partition/拼接**。幸: 受影响下游当前全 dead_orphan (无活回测消费), 不即时污染; 但复活必须从申万桶重算。

## tushare 申万能力 (实测)
- index_member_all (doc 335): 一行带 L1+L2+L3 全三级码+名 + ts_code + in_date/out_date + is_new。单页2000上限 (by_l1 循环避5000截断)。
- **PIT 关键 (S1 修复点)**: is_new 入参 'Y'(当前)/'N'(历史剔除); **单发探针实测 is_new='N' 给 out_date** (801010: Y 126行 out_date全空 / N 56行 out_date全填 / ''=当前同Y)。原 registry 只拉 Y → out_date 100% NULL = latest-snapshot。
- index_classify (doc 181, 2000积分): 分类树骨架 (代码+名+发布标志+变动原因+成分数), level=L1/L2/L3 × src=SW2021。**未落** (S8 补)。

## 迁移步骤 (S0-S8)
- **S0** [DONE] 决策门: 全套迁移 + 删mart走视图 + 接受 taxonomy 不可比。本文件 = 契约。
- **S1** [DONE 2026-06-15] P0 数据真相: registry 加 `index_member_all_hist` 域 (is_new='N'), 回拉 1940 历史剔除区间 → raw_tushare_index_member_all。**验收 PASS**: out_date_filled 0→1940, is_new Y+N, 同股多区间 0→1609。latest-snapshot leakage 数据层已修。
- **S2** [TODO] 建 as-of 视图 (真相源=raw 表, 奥卡姆不物化中间 mart): WHERE in_date<=t AND (out_date IS NULL OR out_date>t), 按决策日 t 取唯一归属 (dedup: 取 in_date 最大活跃区间; 脏数据→unknown)。列映射 l1_code/name→tdx_l1/name 位 (抽象层 alias)。视图建哪库: smartmoney ATTACH tushare_raw 或跨库。
- **S3** [TODO] repoint industry.py 单点: INDUSTRY_TABLE 常量 + load_industry_map/resolve_industry 读 S2 视图 + **修 latent bug** (load_industry_map/resolve_industry 加 as_of_date 入参真下推到视图 WHERE, 否则换源也还 latest-snapshot)。signals_v2.py 裸 JOIN 一并 repoint。列名常量 tdx_* 保持 (alias 语义=当前生效行业级别, 源已换)。
- **S4** [TODO] 删 STALE mart (用户定): DROP mart_stock_industry_pit + mart_industry_pit_quality; 改 seed_dim_data_asset.py:94 悬空 owner 引用; 删前确认 workbench gate 无硬依赖。
- **S5** [TODO] 改 workbench_industry_pit_read.py: 读 S2 视图派生 readiness (替原 mart)。
- **S6** [TODO] 双轨核对 >=1周: 申万L1 vs tdx_l1 同批活跃股归属一致率, 留 artifact; tdxhub dim sync 链保热备 (§4.3)。
- **S7** [TODO] 退役: 物删 mart 重建残留 (悬空引用); dim sync 链降 tdxhub 热备**不物删** (§4.3 fallback 也用, 申万无T码三级等价); dead_orphan ~17 独立退役线 (非本迁移)。
- **S8** [TODO, P1 并行] 注册+落 index_classify (level×SW2021) 补桶骨架/变动原因; 核 raw_tushare_index_daily 含 801xxx.SI 全31行业日线 (sw_daily, 行业动量/中性化收益序列)。

## 开放决策 (用户已定 #1/#2; #3-5 默认见下)
- #1 taxonomy 历史不可比: **接受** (用户定全套迁移)。
- #2 mart: **删走视图** (用户定)。
- #3 一股多区间决策日去重: 默认取 in_date 最大活跃区间; 同t多命中(脏数据)→unknown 不取最新 (PIT 安全)。
- #4 tdxhub 热备保留: dim sync 保留热备, SLA 放宽 (申万转正后), 保留期限/物删评估留用户后续。
- #5 中性化层级: **2026-06-11 已 measured 决策 申万 L2 为主口径** (ANOVA 净区分度 0.137 > 通达信L2 0.118 > 申万L1 0.110; L2>L1 甜点; 通达信L3 过细=过拟合, 证据 analysis/industry_discrimination_*.json)。故 repoint 默认应切 **SECTOR_LEVEL=2 (申万L2)** 非 L1 — 本迁移正是该 06-11 决策的执行 (当时缺 PIT 历史未落地, S1 已补)。L2=131 桶须 S8 index_classify 补全 + 每桶样本数检查防过细。
