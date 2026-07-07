# aif10_capability_client 模块 + raw_aif10_valuation_quantile + sources/aif10.py + test_v3_picture.py 退役 (2026-07-07)

## 背景

前一批(同日更早)已退役 `raw_aif10_peer_valuation`, 收尾时留了 3 处"顺带发现"给用户决定:
`raw_aif10_valuation_quantile`(同源死消费方)/`sources/aif10.py`(疑似P0.3残留)/`test_v3_picture.py`
(测已删路由)。用户要求"先深入研究再决定怎么处理", 跑了一轮 workflow(3 独立深挖 + 2 对抗
verify), 全部 3 项均判定"确认死代码/孤儿", 用户拍板"A"(即每项的最彻底处理选项)。

## 证据摘要 (workflow 全文见 session transcript)

### 1. `raw_aif10_valuation_quantile` (93,658 行)

- `chunkyctl lineage impact` : `serve_entity: null`, 3 个"consumer"全是同步基础设施自身。
- 唯一历史消费方 `v3_picture` 路由已确认死(`main.py:110` 注释 + `git log --diff-filter=D`
  确认 commit `a078351e` 整批删除 209 行含 `backend/routers/v3_picture.py`)。
- **额外证据(比 peer_valuation 更早的独立定罪)**: 2026-05-29 的
  `analysis/docs_archive_20260531/market_perception_data_onboarding_spec_20260529.md` 早在
  v3_picture 存活期就写明"`raw_aif10_valuation_quantile` 无 date 列接历史 = leakage", 从未
  被特征/回测管线接入; PIT-safe 替代(`pe_ttm_z_1y`/`pb_z_1y` rolling z-score, 见
  `fact_financial_pit_daily`)当时就已存在并在用。即这张表从建成起就没有真正服务过决策链。

### 2. `backend/services/data_sources/sources/aif10.py`

- P0.3 时期设计的多源注册框架(`@register_source` 装饰器 + `CAPABILITY_TO_REPORT` 12-capability
  菜单), import 时确实会执行注册动作(非纯静态死代码), 但注册目标(`source: aif10`)从未在
  `sync_registry.yaml` 声明过 —— 47/47 域全是 `source: tushare`。
- 唯一真实调用点 `sync_runner.py::_adapter(source_name)` 只解析过 `"tushare"`, 从未传入
  `"aif10"`。实际 aif10 数据管线(`holders_aif10.py`/`qfii_client.py`/`org_holding_aif10.py`/
  `aif10_capability_client.py`)全部直接 `import aif10_scraper`/`AIF10Client`, 零个 import
  这个注册框架文件。`aif10_capability_client.py` 自己的注释("走 aif10_scraper 主源, P0.3
  fallback 暂不接")印证了这个框架从建成起就没被启用过。

### 3. `backend/tests/test_v3_picture.py`

- `main.py` 当前挂载路由中确认无 `v3_picture`; 路由源文件 `backend/routers/v3_picture.py` +
  服务层 `backend/services/picture/` 均已不在磁盘(同批 commit `a078351e` 删除)。
- 该测试默认被 pytest marker 排除(不影响日常 CI), 但手动强制跑(`-m realdb`)后 10 项里
  3 项是真 `AssertionError`(期待 200 实收 404), 另 7 项是"200或404都算过"的形同虚设写法。
- 全仓库搜索确认这是孤立单文件, 没有更大范围的 v3_picture 死代码簇需要一并清理(排除本节
  末尾提到的 `storage_retention.yaml` 独立小残留)。

## 执行 (2026-07-07, 用户拍板 "A")

1. **`raw_aif10_valuation_quantile` 表**: 物删(archive+drop, 见配套 manifest)。
2. **`aif10_capability_client.py` 模块**: 移除 `valuation_quantile` 后 `CAPABILITY_CONFIG`
   变空(唯二 capability 已全部退役), 违反能删必删 —— 整模块 `git rm`(非仅清空字典)。
3. **`backend/services/pipeline/acquire.py`**: 删 Step 2i3(`_sync_aif10_capabilities` 调用)
   + 删 `_sync_aif10_capabilities()` 函数本身(清空后是空 for 循环的 no-op)。
4. **`backend/services/data_sources/sources/aif10.py`**: 整文件 `git rm`;
   `backend/services/data_sources/__init__.py` 的 `from .sources import aif10, tushare` 改为
   只 import `tushare`(该子模块仍活跃, 未一并处理, 见"本次未动"一节)。
5. **`backend/services/data_sources/clients_registry.py`**: 删整个
   `ClientSpec(client_id="aif10_capability_client", ...)` 条目。
6. **`backend/config/data_layers.yaml`**: 删 `tables:` 里 `raw_aif10_valuation_quantile` 登记。
7. **`backend/tests/test_v3_picture.py`**: 整文件 `git rm`。
8. **`backend/tests/test_acquire_active_stock_refresh.py`**: 删对已不存在的
   `_sync_aif10_capabilities` 的 monkeypatch 行。
9. `CLAUDE.md` §4.3 / `PROJECT_INDEX.md`: 更新 aif10 sanctioned-source 范围声明(收窄为
   holder+QFII+机构持仓明细, 去掉估值分位/同行估值)。

## 本次未动 (留待未来, 非本次范围)

- `backend/services/data_sources/sources/tushare.py` + 整个 `register_source`/`Registry` 框架
  本身仍保留(该子模块的活跃度未在本次调研范围内核实, 且用户只点名了 aif10.py, 不擅自扩大)。
- `backend/config/storage_retention.yaml` 里 `mart_stock_picture_daily` 的保留策略残留(对应表
  已物删), 是同根因(v3_picture死亡)的独立、更小配置残留, workflow 顺带发现但列为"非本次3项
  之一", 需用户另行拍板。

## 结果

- 全量测试: 621 → 619 passed(删 2 个测试, 均属已删测试文件本身)。
