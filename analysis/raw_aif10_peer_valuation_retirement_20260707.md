# raw_aif10_peer_valuation (同行估值排名) 整表退役 (2026-07-07)

## 起因

用户追问"同行估值是啥,你看看本项目里解析好的tushare字段,我记得是券商盈利预测之类的呢"——
经查确认 `raw_aif10_peer_valuation` 与用户记忆的"券商盈利预测"(实为 tushare `report_rc` 表)
是两张完全不同的表, 前者是"个股 PE/PB/PEG 相对同行业排名"(无预测成分), 后者才是分析师盈利预测。

排查过程中进一步用户追问"那把同行估值这个表删除吧并清除残留", 拍板整表退役。

## 证据

1. **实为年度快照非季度** (2026-07-07 已修正 SLA 时发现): 对 vendor 接口
   `RPT_PCF10_INDUSTRY_CVALUE` 显式传 `REPORT_DATE` 过滤探测, 2024/2025 两年全部 3/6/9 月
   季末均返回 0 行, 仅 12-31 年末有数据(2024-12-31=16行/2025-12-31=5611行)。
2. **唯一消费方已死**: `chunkyctl lineage impact raw_aif10_peer_valuation` 显示 consumer_count=3,
   但全是 `data_layers.yaml`(登记)+`aif10_capability_client.py`/`clients_registry.py`(同步基础设施
   自身), 无下游业务消费方。深挖历史文档(`analysis/non_tushare_source_inventory_20260619.md`
   等)发现历史上唯一真实消费方是 `v3_picture` router("aif10 valuation_quantile(3消费者
   v3_picture serving)/peer_valuation ... 是 LIVE") —— 但 `v3_picture` 已随 2026-06-28
   "纯数据平台重建"整体退役(`backend/main.py:110` 注释确认: "策略/serving routers 全退役
   [signals/dossier/v3_picture/...]"), `test_v3_picture.py` 测试文件也已是死代码(测的路由
   `main.py` 已不挂载)。`acquire.py`/`aif10_capability_client.py` 里"LIVE, v3_picture 消费"
   的注释是 2026-06-28 重建后未同步更新的残留断言。

## 执行 (2026-07-07)

1. `backend/services/aif10_capability_client.py` — 从 `CAPABILITY_CONFIG` 删 `peer_valuation`
   条目, 删 `sync_peer_valuation()` 函数, 更新模块 docstring。
2. `backend/services/pipeline/acquire.py` — `_sync_aif10_capabilities()` 的同步循环去掉
   `"peer_valuation"`, 修正 stale 的 "LIVE, v3_picture 消费" 注释。
3. `backend/services/data_sources/sources/aif10.py` — 删 `CAPABILITY_TO_REPORT` 里的
   `peer_valuation` 映射条目 (该文件本身 0 消费方, 未一并处理, 见下"附带发现")。
4. `backend/services/data_sources/clients_registry.py` — 从 `aif10_capability_client` 的
   `ClientSpec.writes` 删 `TableWriteSpec("raw_aif10_peer_valuation", ...)`。
5. `backend/config/data_layers.yaml` — 删 `table_health_overrides` 和 `tables:` 两处登记。
6. `analysis/lifecycle_delete_manifest_raw_aif10_peer_valuation_20260707.yaml` — 物理执行
   `db_lifecycle_delete.py --execute`(archive 到 parquet + `mart_data_deletion_record` 留痕)。

## 附带发现 (未处理, 留给用户后续决定)

- **`raw_aif10_valuation_quantile`(估值分位)同样已无 live 消费方** —— 与 peer_valuation 共享
  同一个已死的 `v3_picture` 消费方, 但用户这次只问了"同行估值", 未一并退役, 仅在代码注释里
  如实标注"暂保留写入(无害), 未来 SERVE 层重建估值维度可复用"。是否也退役留用户决定。
- **`backend/services/data_sources/sources/aif10.py` 整个文件 0 消费方**(`CAPABILITY_TO_REPORT`
  这份 12-capability 菜单从未被任何代码 import), 疑似是更早期 P0.3 fallback 设计的残留
  (`aif10_capability_client.py` 注释: "P0.3 fallback 暂不接")。本次只删了其中 `peer_valuation`
  一行, 未处理整文件退役, 留作后续盘点候选。
- **`test_v3_picture.py`** 是测试已退役路由的死代码, 建议随下次治理清理批一并处理。

## 替代表调研结论

实测确认 `raw_tushare_daily_basic`(tushare原生, 本项目已采集)自带每股每日 `pe`/`pe_ttm`/
`pb`/`ps`/`ps_ttm` 估值倍数(**无** `peg`, 需另配 `fina_indicator` 的增长率自算)。叠加项目
已有的行业分类(申万 `v_sw_industry_pit` PIT 历史视图 / 东财 `dim_stock_dc_industry` 当前
快照), 理论上可用一条 `PARTITION BY industry_code ORDER BY pe` 之类的 SQL **自算**"个股
估值在同行业内的排名/分位", 完全不需要 aif10。

**但这是"能自建"非"现成同等表"** —— 本项目目前**没有**与 aif10 peer_valuation 字段
(STOCK_PE_RANK/STOCK_PEG_RANK/STOCK_PB_RANK 等)一一对应的现成表, 需要新写一个 L1/L2
计算 view/表才能替代。鉴于该功能当前 0 live 消费方(唯一消费方 v3_picture 已退役, 见上),
不建议现在就投入建这个计算层——等未来 SERVE 层重建估值维度、有真实下游需求时再建, 到
时候直接基于 tushare 原生 daily_basic + 申万 PIT 行业算, 无需重新接回 aif10。
