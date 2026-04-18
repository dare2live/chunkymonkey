# tdxhub 优化 & 全量数据接入计划

> 目标：将 dare2live/tdxhub (原 mootdx fork) 打造成通达信数据的**统一接入层**，覆盖 tdxpy(pytdx) 全部能力，
> 同时解决性能、兼容性、服务器发现等问题。本项目(chunky-monkey-v2)按需从中取数据。

---

## 实施进度

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1: 基础设施 | ✅ 已完成 | 服务器合并117台、capabilities模块、pyproject更新、README重写 |
| Phase 2: 本项目接入新数据 | ✅ 基础完成 | 服务器统一、Affair gpcw 入库、仓库更名 tdxhub；待接入 holdings/scoring |
| Phase 3: 扩展数据接入 | ✅ 已完成 | xdxr、TDX 板块、批量实时行情已接入；共享 Quotes 连接池已覆盖 batch quotes、财务快照、ETF 列表、指数 K 线、日 K 线诊断链 |
| Phase 4: 文档治理 + 代码质量 | ✅ 已完成 | 文档站、异常层级、公开 API 签名、依赖分组与 focused tests 已收口 |
| Phase 5: 长期优化 | ✅ 已完成 | Tick / 扩展市场能力已补公开示例，bestip 已支持原生 asyncio 与 connect.cfg 导入 |

---

## 〇、现状总结

### 依赖链
```
pytdx (rainx, 已归档 2020)
  └─ tdxpy 0.2.7 (bopo 维护, 2024)
       └─ mootdx 0.11.7 (bopo 原作, 已停更 2+ 年)
            └─ dare2live/tdxhub 0.12.0 fork (你维护, 原 mootdx)
```

### fork 已完成的修复（含本次 Phase 1）
1. 更新 TDX 服务器列表 → **117 台**（合并 tdxpy 104 + mootdx 原始 38，去重）
2. 修复 `valid_server` 支持字符串 `"ip:port"` 格式
3. 修复 StdQuotes/ExtQuotes 构造函数 server 参数链路
4. 安装依赖切到 `mini-racer`，导入模块仍为 `py_mini_racer`（解决 M 芯片问题，移入可选依赖）
5. **新增 `capabilities.py` 模块** — 29 个 API 方法结构化注册，`summary()` 打印全景表
6. **README 完整重写** — 全部 API 文档化，含代码示例、字段说明、协议限制
7. Python 版本提升至 3.9+，依赖从 `^` 锁定改为 `>=` 下限

### 本项目目前只使用了 3 个功能
| 功能 | 文件 | mootdx API |
|------|------|------------|
| 日K线 | `akshare_client.py` | `client.bars()` |
| 基础财务快照 | `financial_client.py` | `client.finance()` |
| ETF/股票列表 | `etf_engine.py` | `client.stocks()` |

### tdxpy 完整能力（20+ 方法未使用）
mootdx 已经封装了大部分 tdxpy 方法，但本项目远未充分利用。

---

## 一、服务器列表：动态发现 + 全量覆盖

### 1.1 背景发现

**实测结果**：tdxpy 内置 104 台 + mootdx 38 台，合并去重 117 台，2026-04-13 全部存活。
mootdx 只保留 14 台是过度裁剪。

**通达信没有官方动态发现 API**。服务器列表来自客户端 `connect.cfg` 二进制文件。
但是，可以通过 TCP 连接测试 + 延迟排序实现动态健康检查。

### 1.2 实施方案

#### Phase 1: 合并全量服务器池 ✅ 已完成

`mootdx/consts.py` 已直接内联 117 台去重服务器，按券商分组注释。
`mootdx/server.py` 不再从 `tdxpy.constants` 导入 hq_hosts，避免重复。

#### Phase 2: 添加运行时健康检查 + 延迟排序（待实施）

新增 `mootdx/server_pool.py`：

```python
class ServerPool:
    """TDX 服务器池：健康检查 + 延迟排序 + 自动故障转移"""
    
    def __init__(self, hosts: list, check_interval: int = 300):
        self._all_hosts = hosts
        self._ranked: list[tuple[str, int, float]] = []  # (ip, port, latency_ms)
        self._check_interval = check_interval
        self._last_check = 0
    
    def get_best(self, n: int = 5) -> list[tuple[str, int]]:
        """返回延迟最低的 N 台服务器"""
        if time.time() - self._last_check > self._check_interval:
            self._refresh()
        return [(ip, port) for ip, port, _ in self._ranked[:n]]
    
    def _refresh(self):
        """并发 TCP 延迟测试，更新排名"""
        # 用 ThreadPoolExecutor 并发测试所有服务器
        # 按延迟排序，标记不可达的
        ...
    
    def mark_failed(self, server: tuple):
        """标记某个服务器失败，短期降权"""
        ...
```

#### Phase 3: 支持外部服务器源

```python
# 支持从最新版通达信客户端的 connect.cfg 导入
def import_from_connect_cfg(cfg_path: str) -> list:
    """解析通达信 connect.cfg 二进制文件，提取服务器列表"""
    ...

# 支持环境变量自定义
# CM_TDX_SERVERS=ip1:port1,ip2:port2
```

---

## 二、做成「全量数据接入层」

### 2.1 设计原则

```
dare2live/tdxhub (你维护, 原 mootdx)
  ├─ quotes.py      → StdQuotes  (A股行情全部能力)
  ├─ ext_quotes.py  → ExtQuotes  (扩展市场全部能力)
  ├─ affair.py      → Affair     (专业财务数据)
  ├─ reader.py      → Reader     (本地文件)
  ├─ server_pool.py → ServerPool (服务器管理)
  └─ pool.py        → ConnectionPool (连接池)
```

**关键改造**：mootdx 已经封装了 tdxpy 的大部分方法，但有些能力被隐藏或遗漏。
目标是确保 tdxpy 的每个公开方法都能通过 mootdx 方便地调用。

### 2.2 tdxpy 完整 API 与 mootdx 覆盖对照

#### StdQuotes (TdxHq_API) — 20 个方法

| # | tdxpy 方法 | mootdx 已封装? | mootdx 方法名 | 你项目使用? |
|---|-----------|---------------|--------------|------------|
| 1 | `get_security_bars` | ✅ | `bars()` | ✅ K线 |
| 2 | `get_index_bars` | ✅ | `index_bars()` / `index()` | ❌ |
| 3 | `get_security_quotes` | ✅ | `quotes()` | ❌ |
| 4 | `get_security_count` | ✅ | `stock_count()` | ❌ |
| 5 | `get_security_list` | ✅ | `stocks()` | ✅ ETF列表 |
| 6 | `get_minute_time_data` | ✅ | `minute()` | ❌ |
| 7 | `get_history_minute_time_data` | ✅ | `minutes()` | ❌ |
| 8 | `get_transaction_data` | ✅ | `transaction()` | ❌ |
| 9 | `get_history_transaction_data` | ✅ | `transactions()` | ❌ |
| 10 | `get_company_info_category` | ✅ | `F10C()` | ❌ |
| 11 | `get_company_info_content` | ✅ | `F10()` | ❌ |
| 12 | `get_xdxr_info` | ✅ | `xdxr()` | ❌ |
| 13 | `get_finance_info` | ✅ | `finance()` | ✅ 财务快照 |
| 14 | `get_and_parse_block_info` | ✅ | `block()` | ❌ |
| 15 | `get_k_data` | ✅ | `k()` | ❌ |
| 16 | `get_block_info_meta` | ❌ | — | — |
| 17 | `get_block_info` | ❌ | — | — |
| 18 | `get_report_file` | ❌ | — | — |
| 19 | `get_report_file_by_size` | ❌ | — | — |
| 20 | `get_traffic_stats` | ✅ | `traffic()` | ❌ |

**结论**: mootdx 已覆盖 16/20 个核心方法。缺的 4 个是底层方法(block meta/raw、report file)，
一般不需要直接调用。**mootdx 已经是完整的封装层。**

#### Affair (财务数据) — 已封装但项目未使用

| 功能 | mootdx 方法 | 说明 |
|------|------------|------|
| 财务文件列表 | `Affair.files()` | 列出 gpcw19960630.zip ~ gpcw20260331.zip |
| 下载财务文件 | `Affair.fetch()` | 下载 zip 到本地 |
| 解析财务文件 | `Affair.parse()` | 解析为 DataFrame，500+ 字段 |

**这是最大的未利用金矿。** 每个 gpcw 文件包含当期所有 A 股的 500+ 字段财务数据。

#### ExtQuotes — 扩展市场（期货/港股/外汇）

mootdx 已封装。你的项目暂不需要，但能力已在。

### 2.3 需要在 mootdx 中新增的能力

| 新增项 | 优先级 | 说明 |
|--------|--------|------|
| **ConnectionPool** | P0 | 连接池复用，避免每次请求新建连接 |
| **ServerPool** | P0 | 动态服务器排名 + 健康检查 |
| **batch_finance()** | P1 | 批量财务查询（复用同一连接） |
| **batch_quotes()** | P1 | 批量实时行情（单次80只） |
| **Affair 字段映射表** | P1 | gpcw 500+ 字段的中文名映射 |
| **async 原生支持** | P3 | 用 asyncio 替代同步 socket（长期） |

---

## 三、从 mootdx 获取的新数据 → 增强本项目

### 3.1 Affair 专业财务数据（P0，收益最大）

gpcw 文件中的关键数据，按你项目的需求分类：

#### 🔥 机构持股明细（字段 238-265）→ 增强 holdings.py + scoring.py

| 字段编号 | 内容 | 价值 |
|---------|------|------|
| 238 | 总股本 | 对照验证 |
| 239 | 流通A/B/H股 | 流通盘 |
| 240 | 股东人数 | 筹码集中度 |
| 241-265 | **机构持股明细** | **核心数据** |

机构持股明细按类型细分：
- QFII 机构数 + 持股量
- 券商机构数 + 持股量
- 基金机构数 + 持股量
- 社保机构数 + 持股量
- 保险机构数 + 持股量
- 私募机构数 + 持股量
- 信托机构数 + 持股量
- 一般法人持股量
- 特殊法人持股量

**这正是你「机构事件研究系统」的核心数据源之一！** 目前你从东财/AKShare 获取持仓数据，
gpcw 数据是另一个独立来源，可以交叉验证。

#### 龙虎榜 / 融券 / 陆股通（序列数据）

| 序列 | 内容 | 增强目标 |
|------|------|---------|
| 2 | 龙虎榜买入/卖出总金额 | external_attention.py |
| 3 | 融资余额、融券余量 | 市场情绪指标 |
| 4 | 大宗交易成交均价/额 | event_engine.py |
| 5 | 增减持 | holdings.py |
| 6 | **陆股通持股量** | 北向资金数据 |
| 8-9 | 龙虎榜机构买卖方 | external_attention.py |
| 10 | 近3月机构调研次数 | external_attention.py |

#### 一致预期数据

| 序列 | 内容 | 增强目标 |
|------|------|---------|
| 1 | 近6月买入/增持评级家数 | stock_forecast_engine.py |
| 3 | 一致预期目标价 | stock_forecast_engine.py |
| 5-7 | T/T+1/T+2 EPS 预期 | stock_forecast_engine.py |
| 8-10 | T/T+1/T+2 净利润预期 | stock_forecast_engine.py |

#### 增强财务分析

| 字段范围 | 内容 | 增强目标 |
|---------|------|---------|
| 153-170 | 偿债能力（流动比率/速动比率等） | financial_client.py |
| 171-182 | 营运能力（周转率等） | financial_client.py |
| 183-191 | 发展能力（增长率等） | quality_feature_engine.py |
| 230-237 | 单季度指标 | financial_client.py |

### 3.2 除权除息数据 `xdxr()`（P1）

```python
client.xdxr(symbol='600036')
# 返回：分红、送转、增发、回购等 14 种事件
# 字段：fenhong, peigujia, songzhuangu, peigu, suogu, 
#       panqianliutong, panhouliutong, qianzonggb, houzonggb
```

**价值**: 你的 `return_engine.py` 计算收益时需要除权信息。
当前依赖 akshare，mootdx 的 xdxr 是更快的本地数据源。

### 3.3 板块/概念数据 `block()`（P2）

```python
client.block(tofile='block_gn.dat')  # 概念板块成分股
client.block(tofile='block.dat')     # 行业板块
client.block(tofile='block_fg.dat')  # 风格板块（大盘/中盘/小盘）
client.block(tofile='block_zs.dat')  # 指数成分
```

**价值**: 补充 `industry.py`，与申万分类交叉验证；
获取实时概念热点板块。

### 3.4 分笔成交 Tick 数据（P3）

```python
client.transaction(symbol='600036')           # 今日分笔
client.transactions(symbol='600036', date='20260413')  # 历史分笔
```

**价值**: 构建更精细的量价特征，增强 qlib 模型训练。
但数据量大，需要存储规划。

---

## 四、PyMiniRacer / M 芯片问题

### 现状
- fork 安装依赖已切到 `mini-racer`（bpcreech 维护），但运行时导入模块名仍为 `py_mini_racer`
- `mini-racer` 0.14.1 (2026-01) 明确支持 macOS aarch64 (M 芯片)
- 兼容性表：macOS ≥ 10.9 的 x86_64 + aarch64 都有预编译 wheel

### 需要的改动
1. **更新 README**：标注 M 芯片问题已解决
2. **将 mini-racer 移入可选依赖**：本项目不使用选股公式功能，不需要它

```toml
# pyproject.toml
[tool.poetry.group.racer.dependencies]
mini-racer = {version = ">=0.12.0", optional = true}
```

---

## 五、性能优化

### 5.1 连接池（P0）

**问题**: 每次 `bars()`/`finance()` 都新建 TCP 连接 → 开销巨大。

**方案**: 在 mootdx 中添加 `ConnectionPool`：

```python
# mootdx/pool.py
import queue
import threading

class ConnectionPool:
    def __init__(self, server_pool: ServerPool, max_size: int = 5, timeout: int = 5):
        self._pool = queue.Queue(max_size)
        self._server_pool = server_pool
        self._timeout = timeout
        self._lock = threading.Lock()
    
    def acquire(self) -> TdxHq_API:
        try:
            client = self._pool.get_nowait()
            if not client.client._closed:
                return client
        except queue.Empty:
            pass
        # 新建连接
        servers = self._server_pool.get_best(3)
        for ip, port in servers:
            client = TdxHq_API()
            if client.connect(ip, port, time_out=self._timeout):
                return client
        raise ConnectionError("所有服务器连接失败")
    
    def release(self, client):
        try:
            self._pool.put_nowait(client)
        except queue.Full:
            client.close()
    
    def __enter__(self):
        return self.acquire()
    
    def __exit__(self, *args):
        ...
```

### 5.2 统一服务器列表（P0）

**问题**: 当前三处重复维护服务器列表：
1. `mootdx/consts.py` — 14 台
2. `akshare_client.py` `_TDX_SERVER_CANDIDATES` — 14 台
3. `financial_client.py` `_FINANCIAL_TDX_SERVERS` — 7 台

**方案**: 全部改为从 mootdx 的 `ServerPool` 获取。

### 5.3 批量操作（P1）

```python
# 当前: 逐股票查询，每只一次 TCP 往返
for code in codes:
    client.finance(symbol=code)

# 优化后: 复用同一连接，批量查询
with pool.acquire() as client:
    for code in codes:
        client.finance(symbol=code)
    # 或者批量实时行情（单次最多 80 只）
    client.quotes([(1, '600036'), (0, '000001'), ...])
```

---

## 六、代码质量改进

### 6.1 依赖清理

当前 mootdx 依赖大而全，部分对你的使用场景无用：

| 依赖 | 用途 | 建议 |
|------|------|------|
| `tqdm` | CLI 进度条 | 移入 `[cli]` extras |
| `tenacity` | 重试 | 保留（你项目自己也有断路器，可考虑统一） |
| `prettytable` | CLI 表格 | 移入 `[cli]` extras |
| `mini-racer` | 选股公式 JS 引擎 | 移入 `[formula]` extras |
| `tdxpy` | 底层协议 | 核心依赖，保留 |
| `pandas` | 数据处理 | 核心依赖，保留 |

### 6.2 异常处理规范化

```python
# 当前: 大量 bare except
except Exception:
    pass

# 改为: 细化异常层级
class MootdxError(Exception): ...
class ConnectionError(MootdxError): ...
class ServerTimeoutError(MootdxError): ...
class EmptyDataError(MootdxError): ...
class ParseError(MootdxError): ...
```

### 6.3 类型标注

给所有公开 API 添加类型标注：

```python
def bars(self, symbol: str = '000001', frequency: int = 9, 
         start: int = 0, offset: int = 800) -> pd.DataFrame | None:
    ...

def finance(self, symbol: str = '000001') -> pd.DataFrame | None:
    ...
```

### 6.4 Python 版本支持

当前 `pyproject.toml` 声明支持 Python 3.6-3.10，严重过时：
- 去掉 3.6-3.8（已 EOL）
- 添加 3.11-3.13 测试
- 用 f-string、`|` 联合类型等现代语法

---

## 七、分阶段实施路线

### Phase 1: 基础设施 ✅ 已完成（2026-04-13）

**目标**: 服务器管理 + 能力目录 + 依赖清理

| 序号 | 改动 | 文件 | 状态 |
|------|------|------|------|
| 1.1 | 合并全量服务器列表 (14→117台) | `mootdx/consts.py` | ✅ |
| 1.2 | 新增 API 能力目录模块 | `mootdx/capabilities.py` (新) | ✅ |
| 1.3 | server.py 不再重复导入 tdxpy hosts | `mootdx/server.py` | ✅ |
| 1.4 | mini-racer 移入可选依赖 | `pyproject.toml` | ✅ |
| 1.5 | Python 版本 3.9+, 依赖降低锁定 | `pyproject.toml` | ✅ |
| 1.6 | README 完整重写 (全部 API 文档化) | `README.md` | ✅ |
| 1.7 | 版本号 0.11.7→0.12.0 | `__init__.py`, `pyproject.toml` | ✅ |

**验证通过**: 
- `HQ_HOSTS` 117 台，全部来自 `consts.py`
- `capabilities.summary()` 输出 29 个 API 方法
- `Quotes.factory().bars('600036')` 连接正常返回数据

### Phase 2: 本项目接入新数据源 ✅ 基础设施已完成（2026-04-13）

**目标**: 接入 Affair 专业财务数据 + 统一服务器

| 序号 | 改动 | 文件 | 状态 |
|------|------|------|------|
| 2.0 | GitHub 仓库更名 mootdx → tdxhub | `dare2live/tdxhub` | ✅ |
| 2.1 | 服务器列表统一 (14台→117台) | `akshare_client.py` | ✅ 从 mootdx.consts.HQ_HOSTS 导入 |
| 2.1b | 服务器列表统一 (7台→117台) | `financial_client.py` | ✅ 同上 |
| 2.1c | 消除硬编码服务器 | `akshare_client.py` fetch_index_kline | ✅ 改用 _iter_tdx_servers() |
| 2.2 | 新增 Affair 数据同步 | `services/tdx_affair_client.py` (新) | ✅ sync_gpcw_files() |
| 2.3 | 建表 raw_gpcw_detail (92列) | `tdx_affair_client.py` | ✅ 含机构持股+财务+业绩预告 |
| 2.4 | 接入机构持股明细 | `holdings.py` / `scoring.py` | 🔲 待接入 |
| 2.5 | 接入一致预期数据 | `stock_forecast_engine.py` | 🔲 待接入 |
| 2.6 | requirements.txt 更新 | `backend/requirements.txt` | ✅ 指向 tdxhub |
| 2.7 | sync_gpcw 加入定期更新 | `routers/updater.py` | 🔲 待接入 |

**已验证**:
- 4 个季度 gpcw 文件正确下载、解析、入库（17,981 行）
- 机构持股明细完整：基金/险资/社保/QFII/私募/信托/银行/法人 各类型
- 招行(600036) ROE=11.8, EPS=5.7, 基金1397家, 股东47.9万户
- 处理了 gpcw 重复列名问题（净资产收益率等9个字段，取 iloc[0]）
- 服务器 fallback 机制：mootdx 未安装时降级到内置 5 台后备列表

#### 2.A 已完成工作详情

**仓库改名 (2.0)**
- GitHub API 改名 `dare2live/mootdx` → `dare2live/tdxhub`
- 本地目录 `/Users/dp/Documents/M/mootdx` → `/Users/dp/Documents/M/tdxhub`
- git remote 更新；GitHub 自动设置旧 URL 重定向
- Python 包名保持 `import mootdx` 不变（兼容性）

**服务器列表统一 (2.1/2.1b/2.1c)**
- `akshare_client.py`: 删除 14 台硬编码 `_TDX_SERVER_CANDIDATES`，
  改为 `from mootdx.consts import HQ_HOSTS`，提取 `(host, port)` 二元组
- `financial_client.py`: 删除 7 台硬编码 `_FINANCIAL_TDX_SERVERS`，同上
- `fetch_index_kline()`: 消除硬编码 `119.147.212.81:7709`，
  改用 `_iter_tdx_servers()` 遍历前 5 台，首个成功即返回
- 两个文件均有 `try/except ImportError` 降级逻辑：mootdx 缺失时使用 5 台内置后备

**tdx_affair_client.py (2.2/2.3)**

新建 `backend/services/tdx_affair_client.py`，提供：

| 函数 | 功能 |
|------|------|
| `sync_gpcw_files(conn, quarters=4)` | 下载最近 N 季度 gpcw → 解析 → INSERT OR REPLACE 到 raw_gpcw_detail |
| `get_latest_institutional_holdings(conn, stock_codes)` | 查询最新一期机构持股明细 |
| `get_gpcw_financial_snapshot(conn, stock_code, limit=8)` | 查询某只股票多期财务数据 |

raw_gpcw_detail 表设计（92 列）：

| 分类 | 字段数 | 说明 |
|------|--------|------|
| 主键 | 2 | stock_code, report_date |
| 每股指标 | 8 | eps, nav_per_share, roe, ocf_per_share 等 |
| 利润表 | 4 | revenue, operating_profit, net_profit, net_profit_deducted |
| 现金流 | 3 | operating/investing/financing_cashflow |
| 资产负债 | 7 | total_assets, total_liabilities, cash, inventory 等 |
| 股本结构 | 4 | total_shares, float_a_shares, free_float_shares, restricted |
| 股东 | 4 | holder_count, top1_holder, top10_holder, top10_float_holder |
| **机构持股** | **21** | inst_total + 10 类机构 (count+shares) + national_team |
| 业绩预告 | 5 | forecast_profit_yoy_low/high, forecast_profit_low/high, announce_date |
| 业绩快报 | 4 | express_net_profit, eps, roe_diluted, net_profit_deducted |
| TTM/年度 | 4 | revenue_ttm, net_profit_ttm, ocf_ttm, total_revenue_ttm |
| 其它 | 2 | employee_count, report_announce_date |
| 时间戳 | 1 | ingested_at |

gpcw 重复列名处理：gpcw 585 列中有 9 对重名（如"净资产收益率"出现两次），
`_safe_float()` 检测到 pandas Series 时取 `iloc[0]`（主报表值）。

#### 2.B 待做：机构持股接入 scoring（2.4）

**设计思路**：
- `holdings.py` 当前从东财/AKShare 获取机构持仓，gpcw 是独立的验证源
- gpcw 的优势：按机构类型细分（基金/险资/社保/QFII/私募/信托/银行/法人）
- 可以在 `scoring.py` 中新增维度：
  - 机构类型多样性（inst_type_diversity）：参与的机构类型越多越好
  - 基金重仓度（fund_concentration）：基金持股占自由流通股比例
  - 险资/社保配置信号（long_money_signal）：险资+社保持股变化
  - 股东集中度（holder_concentration）：top10 持股 / 总股本

**实施要点**：
- gpcw 数据按季度更新，不适合做高频特征，适合做「底仓质量」评分
- 与现有东财持仓数据交叉验证：两个源的机构总数应大致吻合
- 新特征加入 scoring 权重时需对比有/无该特征的评分效果

#### 2.C 待做：一致预期接入 forecast（2.5）

**设计思路**：
- gpcw 包含业绩预告字段（净利润同比增幅上/下限、净利润上/下限、公告日期）
- 可增强 `stock_forecast_engine.py`：
  - 业绩预告超预期信号（forecast_profit_yoy_high > 某阈值）
  - 业绩确定性（forecast_profit_yoy_high - low 差值越小越确定）
  - 快报 vs 预告一致性

**注意**：gpcw 不含卖方分析师一致预期（EPS/目标价），那是付费数据。
gpcw 的"业绩预告"是公司自己发布的盈利预计，两者不同。

#### 2.D 待做：加入 updater.py 定期更新（2.7）

gpcw 文件按季度发布（3/6/9/12月末），但实际上是滚动更新的：
同一个 gpcw20251231.zip 随着上市公司陆续发布年报会不断膨胀。

**建议**：
- 在 updater.py 的每日更新流程末尾加入 `sync_gpcw_files(conn, quarters=2)`
- 只拉最近 2 个季度（最新季和上一季），已有数据用 INSERT OR REPLACE 覆盖
- 每次约 10MB 下载 + 10s 解析，对每日跑批影响很小

#### 2.E 优化建议：围绕 gpcw 的增值方案

**P0 — 直接可用，收益最大**

1. **gpcw 机构持股 vs 东财持仓交叉验证**
   - gpcw 来源：通达信数据中心 → 证交所公开持仓数据
   - 东财来源：上市公司公告 → 东方财富解析
   - 两个源应该基本一致，不一致的股票值得重点关注（数据质量问题 or 持仓变动信号）
   - 可以 `holdings.py` 中新增 cross_validate_institutional_holdings() 比对

2. **gpcw 股东人数变化 → 筹码集中度信号**
   - holder_count 季度对比：减少 = 筹码集中（通常偏多）
   - 与当前 scoring 中的价格动量信号形成互补，属于基本面维度
   - 实施简单：只需对比最近两期 raw_gpcw_detail 的 holder_count

3. **gpcw 业绩预告惊喜因子**
   - forecast_profit_yoy_high > 50% 且 forecast_profit_yoy_low > 20% → 强正面预告
   - 结合公告日期(forecast_announce_date)做事件研究：预告后股价反应
   - 可以扩展 event_engine.py 或作为 scoring 的一个独立因子

**P1 — 需要更多开发**

4. **gpcw 财务质量因子补充 quality_feature_engine**
   - 当前 quality_feature_engine.py 的数据来源是 akshare 财报
   - gpcw 多了几个有用字段：
     - `capital_reserve_per_share` 资本公积（高资本公积 → 有送转能力）
     - `undistributed_profit_per_share` 未分配利润
     - `ocf_per_share` vs `eps` 经营现金流/EPS 比值（>1 质量好）
     - `restricted_a_shares` / `total_shares` 限售股占比（解禁压力）

5. **gpcw 替代部分 akshare 财务数据拉取**
   - 当前 financial_client.py 用 akshare/sina 接口批量拉取历史财报
   - gpcw 每季度一个文件覆盖全 A 股，无需逐股拉取
   - 但 gpcw 只有最新一期快照，不如 akshare 有多期历史
   - **折中方案**：首次全量用 akshare，增量用 gpcw（每日更新最快）

6. **国家队持股追踪**
   - `national_team_shares_wan` 是独有数据（东财持仓不直接区分国家队）
   - "国家队"包括汇金公司、证金公司、外管局等
   - 对于判断市场底部/顶部有参考价值
   - 可以建立 national_team_trend 时间序列，观察增减持趋势

### Phase 3: 扩展数据接入（~2 个工作日）

**目标**: 除权除息 + 板块 + 实时行情

| 序号 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 3.1 | 除权除息数据同步 | `services/xdxr_client.py` (新) | xdxr 数据入库，已接入 `sync_market_data` |
| 3.2 | 板块概念数据同步 | `services/block_client.py` (新) | 概念/行业板块成分股，已接入 `sync_industry` |
| 3.3 | 批量实时行情 | `services/quote_snapshot_client.py`, `services/setup_tracker.py` | 已补充 tdxhub `quotes()` 批量查询，并接入 setup 当前价刷新 |
| 3.4 | 连接池接入 | `services/tdx_source.py`, `services/quote_snapshot_client.py`, `akshare_client.py`, `financial_client.py`, `services/xdxr_client.py`, `services/block_client.py` | 共享 Quotes 连接池已覆盖 batch quotes、财务快照、ETF 列表、指数 K 线、日 K 线诊断链、xdxr、block |

**验证**:
- 复权数据与 akshare 一致
- 板块数据能正确查询
- K线获取性能提升（A/B 对比）

### Phase 4: 文档治理 + 代码质量（~1 个工作日）

**目标**: 文档站去上游化 + 异常处理 + 类型标注 + 测试

| 序号 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 4.1 | 文档站入口纠偏 | `mkdocs.yml`, `docs/index.md`, `docs/setup.md`, `docs/quick.md` | 已完成，统一为 tdxhub 当前 fork 语境 |
| 4.2 | FAQ / API / CLI 文档清理 | `docs/faq`, `docs/api`, `docs/cli` | 已完成两批清理，补齐 FAQ、CLI 与 API 当前行为 |
| 4.3 | 异常类层级 | `mootdx/exceptions.py` | 已完成，统一基类/依赖异常/校验异常，并补 focused tests |
| 4.4 | 公开 API 类型标注 | `mootdx/quotes.py` | 已完成：Quotes / BaseQuotes / StdQuotes / ExtQuotes 主公开面签名已补齐 |
| 4.5 | 依赖分组 | `pyproject.toml` | 已完成：`cli` / `racer` extras 已落地，README 与安装页已同步 |
| 4.6 | 基础测试 | `tests/` | 已补 holiday / exception / quotes util / connect.cfg focused tests |

### Phase 5: 长期优化（已收口）

| 序号 | 改动 | 说明 |
|------|------|------|
| 5.1 | 分笔 Tick 数据接入 | 已完成：标准市场 `transaction()` / `transactions()` 示例已补齐 |
| 5.2 | asyncio 原生化 | 已完成：`bestip(sync=False)` 改为 `asyncio.open_connection` 原生 TCP 探测 |
| 5.3 | connect.cfg 解析器 | 已完成：支持从通达信客户端 `connect.cfg` 导入 `HQHOST` / `DSHOST` |
| 5.4 | 扩展市场 | 已完成：ExtQuotes 主公开面签名与公开示例已补齐 |

---

## 八、附录

### A. 实测服务器延迟排名 Top 20（2026-04-13）

| 延迟 | IP | 来源 |
|------|----|------|
| 0.2ms | 114.80.63.35:7709 | tdxpy:云行情上海电信Z2 |
| 0.2ms | 202.96.138.90:7709 | tdxpy:华林 |
| 0.2ms | 218.108.47.69:7709 | tdxpy:杭州华数主站J2 |
| 0.3ms | 121.14.104.66:7709 | tdxpy:安信 |
| 0.3ms | 14.17.75.71:7709 | tdxpy:深圳电信主站Z1 |
| 0.3ms | 202.108.253.139:80 | tdxpy:北京联通主站Z80 |
| 0.4ms | 101.227.73.20:7709 | tdxpy:华泰(上海电信) |
| 0.4ms | 60.191.117.167:7709 | tdxpy:杭州电信主站J1 |
| 0.5ms | 122.192.35.44:7709 | tdxpy:华泰(南京联通) |
| 0.5ms | 202.108.253.131:7709 | tdxpy:北京联通主站Z2 |
| 0.7ms | 116.205.163.254:7709 | mootdx:广州双线主站5 |
| 1.1ms | 116.205.171.132:7709 | mootdx:广州双线主站6 |
| 5.2ms | 183.57.72.23:7709 | tdxpy:广发 |
| 5.5ms | 124.70.199.56:7709 | mootdx:上海双线主站6 |
| 5.5ms | 180.153.39.51:7709 | tdxpy:上海电信主站Z3 |
| 5.6ms | 124.70.133.119:7709 | mootdx:上海双线主站12 |
| 5.6ms | 220.178.55.86:7709 | tdxpy:华林 |
| 5.6ms | 58.63.254.191:7709 | tdxpy:海通 |
| 5.7ms | 183.57.72.22:7709 | tdxpy:广发 |
| 5.8ms | 58.58.33.123:7709 | tdxpy:青岛电信主站W1 |

### B. gpcw 文件字段编号速查

```
每股指标:        1-7     (EPS/ROE/BVPS...)
资产负债表:      8-73    (总资产/净资产/流动资产...)
利润表:          74-97   (营收/净利/三费...)
现金流量表:      98-133  (经营/投资/筹资现金流...)
偿债能力:        153-170 (流动比率/速动比率...)
营运能力:        171-182 (周转率...)
发展能力:        183-191 (增长率...)
获利能力:        193-229 (毛利率/ROE/EBIT...)
单季度指标:      230-237 (单季营收/利润...)
股本股东:        238-265 (股东数/机构持股明细...)
业绩预告快报:    286-294
一致预期:        见序列数据
龙虎榜/融券等:   见序列数据
```

### C. 关键限制

| 约束 | 值 |
|------|-----|
| K线每次最多 | 800 条 |
| 分笔每次最多 | 2000 条 |
| 批量行情每次最多 | ~80 只 |
| 服务器列表来源 | 静态（无动态发现） |
| 扩展行情端口 | 7720（7727 已大部分失效） |
| 财务数据服务器 | 120.76.152.87:7709 |
