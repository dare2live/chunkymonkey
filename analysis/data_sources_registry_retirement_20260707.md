# data_sources 多源注册表框架精简收口 (2026-07-07)

## 背景

`sources/aif10.py` 退役后, 用户要求"研究解决方案"处理 `sources/tushare.py` 所在的整套多源
注册表框架(`base.py`/`registry.py`)。workflow 调研 + 对抗验证确认: 这套框架不是像
`aif10.py` 那样"整包死"——`sync_runner.py` 有一条真实活线(`get_registry().get_source
("tushare")` → `TuShareSource.fetch_raw()`), 但注册表设计的"多源 fallback/优先级/健康检查/
能力清单"这套机制本身全部 0 调用。用户选"B: 精简收口"。

## 证据摘要 (workflow 全文见 session transcript)

- `registry.py::resolve()`/`list_sources()`/`healthcheck_all()`: 全仓库 0 调用 (AST 扫描确认)。
- `registry.py::get_registry()`: 仅 `sync_runner.py:60` 一处调用, 且只用来做"按名字查找"
  (`.get_source(name)`), 未用到 fallback/优先级逻辑。
- `base.py::BaseDataSource/Capability/Health/register_source`: 0 外部调用。
- `sources/tushare.py::fetch()`(capability式) + `healthcheck()`: 0 调用。
- `sources/tushare.py::fetch_raw()`: **活**, 是全部47个 sync 域的真实拉数路径
  (`sync_runner.py:398/812/1064`)。
- 起源: commit `68fb2a0d`(2026-04-27, P1骨架, 明确多源+UI 设计) → 唯一 UI 消费方路由
  在 `b35066a8`(2026-06-24, "物删旧 updater UI 簇 22 文件")一并删除, 框架本体("registry.py"/
  "base.py")当时未被清理, 只是失去了消费方, 靠 sync_runner 那条侧门线继续被 import。

## 执行 (2026-07-07, 用户拍板"B: 精简收口")

1. **`sources/tushare.py`**: 重写(249行→91行)。删 `fetch()`(capability式, 含
   `_add_normalized_order_flow_columns`/`_net_amount`/`_amount_delta`/`_first_number` 等
   仅供其使用的归一化辅助函数一并删除)、`healthcheck()`、`capabilities` 属性、
   `@register_source`装饰器、`BaseDataSource`继承。只留 `fetch_raw()` + 其依赖的
   `_env_token()`/`_pro_api()`/`_compact_params()`/`_to_records()`。
2. **`registry.py` + `base.py`**: 整文件 `git rm`(合计约340行)。
3. **`__init__.py`**: 重写, 去掉 registry 转发/re-export, 只留说明性 docstring(包边界仍需
   这个文件存在, 但内容不再需要做任何转发——`from services.data_sources import sync_runner`
   这种子模块导入方式不依赖 `__init__.py` 内容, 已核实全仓库唯一的外部导入方式正是这种)。
4. **`sync_runner.py::_adapter()`**: 改为直接 `import TuShareSource` 并模块级单例缓存
   (`_TUSHARE_SOURCE` 全局变量, 懒加载), 保留原有的"未知 source 报错"行为语义
   (`KeyError`, 消息更新说明"精简后只剩 tushare")。

## 验证

- 全量测试 619 passed(框架简化前) → 617 passed(简化后, 减少的2个是
  `test_calendar_gate.py` 一个参数化测试扫描目录内 `.py` 文件数量减少导致的用例数变化,
  非真实回归, 已用 git stash 前后 diff 逐条核对确认)。
- `chunkyctl doctor --fast` / `moth assert` 保持全绿。
- **实测活链路**: 直接调用 `sync_runner._adapter('tushare').fetch_raw('daily', ...)` 走完整
  真实 tushare API 请求, 返回正确数据, 确认精简后的直连路径功能完全等价。

## 本次未动 (留待用户另行决定)

调研过程中意外发现: `index.html` + `assets/js/settings-view.js` + `assets/js/data-view.js`
不是孤立的"旧 data_sources UI 死重", 而是一整套**更大的老 vanilla-JS workbench 前端**的一
部分——`backend/tests/contract/test_workbench_frontend_contract.py` 会读取
`index.html`/`app.js`/`data-view.js`/`workbench-view.js`/`stock-view.js`/
`widgets/stock-list-controls.js` 等一串文件, 校验脚本引用顺序和入口注册关系。删除
`index.html`/`data-view.js`/`settings-view.js` 会连带打断这一整套跟本次 data_sources
精简无关的老前端契约测试。**已超出本次批准范围, 本次完全未触碰这几个文件**, 是否要
整体清理这套老前端(连同其契约测试)需要用户单独立项调研决定。
