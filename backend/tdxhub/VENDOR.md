# tdxhub — vendored

来源: https://github.com/dare2live/tdxhub.git (业主自己的仓)
并入日期: 2026-09-08
并入时上游 commit: `e2516d57e3935c5c1ee373f9a463b8587e2b4e2c`
许可: MIT, Copyright (c) 2017 mootdx (LICENSE 随包拷入)

血缘: 通达信客户端反汇编 -> tdxpy -> mootdx -> tdxhub -> 本仓。协议层(私有浮点编码、
setup 三帧、各 parser)是对客户端行为的逐字转写, 不是规范实现。

## 并入范围 = 整包减一个文件

`tdxhub/` 下 88 个模块里并入 87 个, 只减 `utils/demjson.py`(6,320 行)。
判据是字符串级的**全仓无人引用**: 上游 package / scripts / tests / docs 与本仓一起 grep,
除了它自己和一行 changelog 之外零命中 —— 是从 mootdx 带下来的遗留 JSON 库。
后来又减 `utils/holiday.py`(见下「与上游的差异」)。

### 为什么不按使用闭包裁

试过, **当天连错三次, 每次都静默**:

1. **真跑一次 import 取 `sys.modules` 差集** -> 55 模块 / 6,554 行。漏掉 `reader.py` 方法体里
   的 6 个惰性 import(`contrib.compat` / `lc_min_bar_reader` / `min_bar_reader` /
   `tools.customize` / `parse` / `exhq_daily_bar_reader`)。
2. **AST 遍历补惰性 import** -> 63 模块 / 7,207 行。漏掉 `__init__.py` 的
   `__getattr__` 里 `importlib.import_module('tdxhub.capabilities')` —— 模块名是字符串,
   AST 里根本不是 import 节点。
3. **再查一遍** -> 漏掉 `protocol/reader/__init__.py` 的 `__getattr__` 懒加载(暴露
   `TdxDailyBarReader` 等 5 个类), 而 `contrib/compat.py` 顶层就 import 了它 ——
   也就是说第 1 步"补进来"的模块自己又指回被裁掉的模块。

每一次的失败模式都一样: 少一个模块, import 那一刻才炸, 而那一刻可能是半年后某条
没有测试覆盖的代码路径。7,207 行换一个错了三次的边界不值。所以边界不靠推断, 靠
`backend/tests/services/test_vendored_packages.py::test_vendored_tdxhub_is_import_complete`
—— 逐个真 import, 少一个就红。

## 为什么在仓内

原来靠 `sibling_repos.ensure_import_path("tdxhub")` 插 `sys.path`。实测问题:

**同一台机器上有两份, 拿到哪份取决于代码路径, 两条路径都不报错。** 用户 site 里躺着
一份 `pip install` 的 tdxhub, 比 sibling **落后 22 个 commit**: 裸 `import tdxhub` 拿旧的,
走 `ensure_import_path` 拿新的。具体后果 —— 2026-09-08 在上游修的
`get_volume(0)` 零值 bug(`e2516d5`), 在裸 import 那条路径上**从未执行过**。

并入 `backend/tdxhub` 并卸掉那份 pip 安装后, 结构上只剩一份, 由
`test_vendored_packages.py` 钉住。三个第三方包(marketdb / aif10_scraper / tdxhub)全部
并入后 `sibling_repos.py` / `sibling_repos.yaml` 没有生产消费方, 一并退役。

## 没有带过来的部分

上游仓不只是一个包: 还有 `scripts/`(7 个, 1,210 行)、`tests/`(137 个文件)、`docs/`
(25 个文件含 mkdocs 站点)、`.github/` CI、`pyproject.toml`/`poetry.lock`、Dockerfile。
这些**没有**并入 —— 把一个带自己 CI 与文档站的项目整仓塞进来是嵌套不是合并。

上游仓 `~/Documents/M/stock/tdxhub` **原样留在磁盘上, 本次一个字节都没动**, 只是不再
在任何 import path 上(`sibling_repos` 机制退役 + 那份 pip 安装卸掉)。归档或删除它是
不可逆动作, 且它带着自己的 git 历史 / docs 站 / 137 个测试, 留给业主拍板。

本仓这份的对账锚点是上面那个 commit SHA, 不是"sibling 目录里现在长什么样" —— 上游
之后再提交, 这份不会自动跟, 也不该自动跟。重新同步先读下面「与上游的差异」。

**待办(不在本次范围)**: `tdxhub.holders`(2,786 行, 已并入)是上游按「十大流通股东最新
增量主源」写的, 配套 harness 是上游 `scripts/holders_universe_{fetch,consolidate}.py` 与
`holders_e2e_verify.py`。要用它得走本仓的 DB 边界 / PIT / universe 门, 是一次真接入,
不是拷文件。

## 消费方

`backend/services/data_sources/sources/tdxhub.py`(唯一 import 面: `config` / `consts.HQ_HOSTS` /
`server.parse_connect_cfg` / `quotes.Quotes` / `reader.Reader`) 与
`backend/services/data_sources/tdxhub_mac.py`(`protocol.parser.setup_commands`)。
其余模块经这两处间接触达。

## 与上游的差异（重新同步上游时必须先读这节）

并入当天改了 6 类, **都是行为改动不是排版**, 上游没有对应 commit。

**删 `k()` / `ohlc()` / `get_k_data()`**（`quotes.py`）
`get_k_data` 用**本机当前时间**到目标日期的天数算 K 线 offset, 再按「非交易日大概是
全年的 1/3」这两个常数(2.8 / 3.5)把日历天折成交易天。撞两条红线: 交易日只从
`services.calendar` 取, 且不许拿比例猜节假日。本仓的未复权日 K 走
`tdxhub_kline_recon.fetch_unadjusted_bars`(count 按协议算的精确值), 整包内也只有
`ohlc -> k -> get_k_data` 这一条链且链头无人调用, 所以整条删。连带的四个只服务这条链
的辅助函数(`_parse_date` / `_date_distance` / `_record_date` / `_normalise_k_records`)一并删。

**删 `utils/holiday.py`**
一个用 `datetime.now().date()` 猜节假日的模块, 上游包内零 importer。在本项目里,
非 `services.calendar` 的节假日判断是最危险的一类残留 —— 不是"留着也没事", 是
"总有一天有人会 import 它"。

**删 `utils/demjson.py`**（见上「并入范围」）

**`ex_get_transaction_data.py`: 不再拿本机今天补日期**
TDX 分笔报文只带 `HH:MM:SS`。上游用 `datetime.combine(date.today(), ...)` 补齐, 于是每条
历史分笔都被盖上运行当天的日期 —— 拿不知道的东西编了个值, 撞「缺失只能传播为缺失」。
改成 `None`, `hour`/`minute`/`second` 三字段原样保留。本仓的分笔走 std 路径
(`get_history_transaction_data`, 日期是请求入参), 不经这个扩展市场 parser。

**行情方法去掉 symbol / date 的字面量默认值**（`quotes.py`, 约 20 处签名）
上游有 `symbol: str = '000001'`、`date: str = '20170209'`、`date: str = '20191023'`、
`symbol: str = ''` 这类默认值: 调用方漏传一个参数, 拿回来的是**另一只股票、另一天**的
数据且不报错。全部改成必填; 必填参数排在有默认值的参数之后时用 `*` 变成关键字必填
(`minutes` / `quote` / `transaction` / ExtQuotes 的几个)。
`__main__.py` 里 click 的 `-s/--symbol` 默认值**保留** —— 那是 `--help` 里看得见的交互
默认值, 且本仓不调 `python -m tdxhub`; 两处形状一样但风险不同(库调用方看不见默认值,
CLI 用户看得见), 所以处置不同。三处加了 `# rule-compliance: ok evidence=` 注释。

**5 处 `except ...: pass` 改掉**
- `protocol/parser/base.py`: 删一个 `try: import cython ... except ImportError: pass` 块。
  它只在 `cython.compiled` 为真时定义 `def buffer(x): return x`, 而这个 `buffer` 在整包里
  零处调用(Python 3 也没有内建 `buffer`) —— Python 2 时代遗留, 删掉在任何解释器下都
  无行为变化。
- `quotes.py` 3 处: `isoformat()` 与 `to_dict('records')` 与 "ip:port" 形态试探。语义
  (转不出来就原样往下走)不变, 但 `except Exception` 收窄到真会抛的那几类, 并各记一行
  debug —— 原来是吞掉且无痕。
- `holders.py` 2 处: `client.close()` 失败。语义(关不掉也要继续轮换/收尾)不变, 改成记
  一行 debug; 连接关不掉往往是对端断了, 但也可能是句柄泄漏, 不记就永远看不见。
  同批补了 `from tdxhub.logger import logger` —— 这个模块原本没有 logger,
  改动本身若不补 import 会在运行时 NameError, 而 import 测试查不出来(调用在函数体内)。

**`version.py` / `protocol/version.py`: `print` 加 `__main__` 守卫**
原本是模块顶层的裸 `print(__version__)` —— import 这个模块就往 stdout 吐一行, 会污染
任何解析 stdout 的调用方。`python -m tdxhub.version` 的行为不变。
