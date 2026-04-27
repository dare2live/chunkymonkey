# 架构重设计方案 — 数据源 registry + 页面重新分区

**起始**: 2026-04-27
**目标**: 从第一性原理重新设计数据层 + UI 层, 让数据源接入、数据管理、策略调参、系统设置各司其职.
**状态**: 设计稿, **未实现**, 待 review.

---

## 0. TL;DR

1. **后端建一个 `data_sources/` registry**: 每个 client (mootdx / akshare / aif10-scraper / eastmoney 等) 自己注册自己, 声明能拉什么数据 + 优先级 + 健康状态. 有了它, 任何"数据需求 X" 都能按 `tdxhub > 妙想 > akshare` 优先级自动 failover, 而不是像现在散在各 client 里硬编码.
2. **前端拆 4 个独立页面**, 把现在塞在工作台的内容分开:
   - **数据** (新): 数据源 + 数据更新 + 字段映射. 可视化配置.
   - **工作台** (精简): 只保留事实计算 + 集市构建 DAG.
   - **策略** (新): 信号参数 / Cohort / 回测 / 选股, 升级到独立页.
   - **系统设置** (新): 数据源参数、危险操作、主题等.
3. **妙想以外部依赖方式接入**: `pip install` 我们的 [`dare2live/aif10-scraper`](https://github.com/dare2live/aif10-scraper), 在 registry 里注册一个 source, 走标准 failover 链路.
4. **分阶段迁移**: 先 backend registry (P1) → 再前端数据页 (P2) → 再工作台精简 (P3) → 策略页 (P4) → 系统设置 (P5). **每阶段独立可上线**, 不要一次大爆炸.

---

## 1. 现状盘点 (Phase B 调研结论)

### 1.1 后端数据源现状

12 个 client 散落在 `backend/services/`:

| Client | 数据来源 | 主要拉的数据 | 失败处理 |
|---|---|---|---|
| `akshare_client.py` (kline_source.py) | 东财/新浪/腾讯 | 日 K 线 / 月 K 线 / 交易日历 | ✅ 3 源 failover (硬编码) |
| `financial_client.py` | mootdx + akshare | 财务快照 / 历史 8 期 | ThreadPool 并发, 无 failover |
| `tdx_industry_client.py` | tdxhub | 申万行业 | mootdx circuit breaker |
| `tdx_affair_client.py` | tdxhub | 公司公告 / gpcw | 无 failover |
| `block_client.py` | tdxhub | 板块 | 无 |
| `xdxr_client.py` | tdxhub | 除权除息 | 无 |
| `capital_client.py` | 东财 datacenter-web | 股票回购 / 历年分红 | 无 |
| `lhb_client.py` | 东财 push2his | 龙虎榜 | 无, 反爬卡死会 hang |
| `qfii_client.py` | 东财 datacenter-web | QFII 持仓 | 无 |
| `margin_client.py` | 东财 datacenter-web | 融资融券 | 无 |
| `institution_survey_client.py` | 东财 datacenter-web | 机构调研 | 无 |
| `financial_indicator_client.py` | akshare | 扩展财务指标 | 无 |

`updater.py` 是 1200+ 行的 DAG 编排器, 19 个 step, 4 个 group (data/calc/mart/manual). 每个 step 内部 `lazy import` 各 client.

### 1.2 现有 failover 痕迹

只有 K 线有 failover (`assets/.../kline_source.py:86-179`):

```
东财 → 新浪 → 腾讯, 失败原因记 diagnostics
```

mootdx 有 circuit breaker (180s 冷却), 但是**全局共享**, 任何 client 触发都会让其他 client 跟着断电.

其他 11 个数据源**没有 failover**. 如果 push2his 反爬, lhb_client 直接 hang. 如果 datacenter-web 4xx, qfii_client 直接报错回 500.

### 1.3 前端现状

**一级 nav**: 股东挖掘 / ETF研究 (两个 board)

**二级 nav** (7 页):

| 页面 | 入口 | JS | 功能 |
|---|---|---|---|
| 股票 | `#view-stocks` | `stock-view.js` | 股东事件、信号、海龟 |
| 机构 | `#view-research` | `app.js:664` | 机构 track record + 管理 |
| 模型监控 | `#view-model-monitor` | `app.js:6490` | 多维评分 |
| **工作台** | `#view-dashboard` | `app.js:203` | **运维 + 策略参数 + 系统设置 全塞这里** |
| ETF 工作台 / 机会 / 列表 | `#etftab-*` | `app.js:5616+` | ETF 业务 |

**工作台塞了什么** (现状, 4 层 + 2 组, 单页 ~2000px):

```
Layer 1: 健康摘要带 (5 张卡片)
Layer 2: 主操作面板 (智能更新按钮 + 数据源状态 + 实时日志)
Layer 3: 管线步骤栅格 (19 个 step 卡片, 可单步触发)
Layer 4: 数据质量审计 (折叠)

组 1 - 策略参数:
  - 信号参数 widget
  - Cohort 反馈闭环 widget
  - 历史回测 widget
  - 选股扫描 widget

组 2 - 系统设置:
  - 全量重算派生数据 (危险按钮)
```

**没有独立的策略参数页, 没有独立的系统设置页, 没有独立的数据页.**

---

## 2. 痛点 + 根因

| 痛点 | 现状 | 根因 |
|---|---|---|
| 1. 数据源散落 | 12 个 client, 调用方各自 import | 没有统一注册中心 |
| 2. 优先级硬编码 | K 线 failover 写死 `[东财, 新浪, 腾讯]`, 其他 11 个没 failover | 没有 priority 概念 |
| 3. 状态不可见 | 数据源连通性只测 3 个 URL (股东/K线/行业), 没说"现在哪个源在用" | 没有 source-level telemetry |
| 4. 接新源代价大 | 加一个数据源要改 updater.py + 加 client + 加 connectivity 测试 | 没有插件化注册 |
| 5. 工作台过载 | 4 层 + 2 组, ~2000px, 健康摘要被埋没 | 一个页面承担 4 种职责 |
| 6. 策略调参分散 | 4 个 widget 各自加载 + 各自保存, 没有预设 / 切换 | 没有"策略包" 概念 |
| 7. 系统设置基本空缺 | 只有"全量重算"一个按钮 | 没规划 |
| 8. 数据/计算混在一起 | 拉数据 + 跑事实 + 跑集市 全是工作台的"管线", 用户看不出分工 | 没有职责分层的 UI |

---

## 3. 设计原则 (来自 CLAUDE.md 项目规则 + 第一性)

1. **机构是主角**, 数据源是后台基础设施 → UI 上"数据源"应当是被运维, 不是被分析的对象
2. **数据分层 raw → dim → fact → mart**: UI 应当尊重这个分层 (数据页管 raw + dim, 工作台管 fact + mart)
3. **单点计算、多处复用**: registry 是单点, 任何"我要 K 线" 不绕开它
4. **三可原则** (可见 / 可追溯 / 可复核): 数据源 + 字段 + 来源都得在 UI 暴露
5. **退役原则**: 接新源 = 标记旧源为 deprecated, 不能两套并存
6. **新功能必删旧功能**: 工作台精简后, 老的策略参数 widget 直接迁走, 不留兼容
7. **可视化配置**: UI 直接改 JSON / DB 配置, 不要再去改 .py 文件
8. **模块化**: 每个数据源是一个独立模块 (一个 .py 文件 + 一份 manifest), 加新源不动 updater.py

---

## 4. 后端设计: 数据源 registry

### 4.1 概念模型

```
            ┌──────────────────────────────┐
            │  DataSourceRegistry (单例)    │
            │  - sources: dict[str, Source] │
            │  - capability_index           │
            └──────────────────────────────┘
                          ▲
                          │ register
        ┌──────┬──────┬──────┬──────┬──────┐
       tdxhub  aif10  ak-em  ak-sina ak-tx  custom
       (源)   (外部)   (源)   (源)   (源)
```

每个 Source 自我描述:

```python
# backend/services/data_sources/sources/tdxhub.py
@register_source
class TdxhubSource(BaseDataSource):
    name = "tdxhub"
    display_name = "通达信 (mootdx)"
    priority = 10                        # 数字越小优先级越高
    capabilities = [
        Capability("kline_daily",   freshness="t-0",  cost="low"),
        Capability("financial_gpcw", freshness="t-1", cost="low"),
        Capability("industry_sw",   freshness="static", cost="low"),
        Capability("blocks",        freshness="static", cost="low"),
        Capability("xdxr",          freshness="t-1", cost="low"),
    ]

    def fetch(self, capability: str, **kwargs) -> Result:
        ...

    def healthcheck(self) -> Health:
        # ping 通达信服务器, 返回 ok / degraded / down + last_check_ts
        ...
```

```python
# backend/services/data_sources/sources/aif10.py
# 走 pip install aif10-scraper, 包装一层 Adapter
@register_source
class Aif10Source(BaseDataSource):
    name = "aif10"
    display_name = "妙想 F10 (datacenter)"
    priority = 20                        # tdxhub 之后, akshare 之前
    capabilities = [
        Capability("top_free_holders",  freshness="quarterly"),
        Capability("holder_count",      freshness="quarterly"),
        Capability("valuation_quantile", freshness="daily"),
        Capability("peer_ranking",      freshness="quarterly"),
        Capability("forecast_consensus", freshness="weekly"),
        # ... 72 个 reportName 映射成业务 capability
    ]

    def fetch(self, capability, **kwargs):
        from aif10_scraper import fetch_report
        report_name = CAPABILITY_TO_REPORT[capability]
        return fetch_report(report_name, **kwargs)
```

```python
# backend/services/data_sources/sources/akshare_em.py / akshare_sina.py / akshare_tx.py
@register_source
class AkshareEastMoneySource(BaseDataSource):
    name = "akshare_em"
    display_name = "akshare (东财源)"
    priority = 30
    capabilities = [
        Capability("kline_daily", ...),
        Capability("fund_etf_spot_ths", ...),
        # ... akshare 但只 tdxhub + 妙想 没覆盖的
    ]
```

### 4.2 调用方法

调用方不直接 import client, 走 registry:

```python
from services.data_sources import resolve

# 我要日 K 线, 不关心走哪个源
df = resolve("kline_daily", code="600519", since="2024-01-01")

# 默认按 priority 顺序尝试: tdxhub → aif10 → akshare_em → akshare_sina → akshare_tx
# 第一个成功的返回, 失败累积到 telemetry
```

也支持显式指定源 (调试用):

```python
df = resolve("kline_daily", code="600519", source="akshare_em")
```

### 4.3 优先级 = tdxhub > 妙想 > akshare 怎么落地

不是全局一刀切, 而是**每个 capability 独立优先级**:

| capability | tdxhub 能拉 | 妙想能拉 | akshare 能拉 | 默认顺序 |
|---|---|---|---|---|
| kline_daily | ✅ | ❌ | ✅ (3 源) | tdxhub → ak_em → ak_sina → ak_tx |
| financial_gpcw | ✅ (8 期) | ✅ (200 期) | ❌ | tdxhub → 妙想 (历史更深时) |
| industry_sw | ✅ | ❌ | ✅ | tdxhub → akshare |
| top_free_holders | ❌ | ✅ | ✅ (慢) | 妙想 → akshare |
| valuation_quantile | ❌ | ✅ | ❌ | 妙想 (独家) |
| peer_ranking | ❌ | ✅ | ❌ | 妙想 (独家) |
| holder_count | ❌ | ✅ | ✅ (旧) | 妙想 → akshare |
| money_flow | ❌ | ❌ | ✅ (push2 反爬) | akshare (唯一, 加 retry) |

也就是说: **声明式 manifest**, 每个 source 在自己的 `capabilities` 列表里写"我能干什么", registry 自动按 priority 聚合出每 capability 的 fallback chain.

### 4.4 健康监控

每个 Source 实现 `healthcheck()` → 返回:

```python
@dataclass
class Health:
    state: Literal["ok", "degraded", "down"]
    last_check_ts: datetime
    last_success_ts: datetime
    consecutive_failures: int
    avg_latency_ms: float
    notes: str  # "Surge 代理冲突" / "rate-limit 5xx" / 等
```

UI 上显示 chip: 🟢 / 🟡 / 🔴 + tooltip.

### 4.5 schema 版本

每个 source 拉回来的数据写入 raw_*** 表时, 表头多一列 `_source` + `_source_version`:

```sql
-- 现状: raw_holdings 没区分源
-- 设计后:
ALTER TABLE raw_holdings ADD COLUMN _source VARCHAR;
ALTER TABLE raw_holdings ADD COLUMN _source_version VARCHAR;
-- _source = 'aif10', _source_version = '0.1.0' 之类
```

UI 数据页可以筛"哪些行是哪个源拉的", 也能侦测同一行被多源回写时的差异.

---

## 5. 前端设计: 页面重新分区

### 5.1 一级 nav 不变

```
[股东挖掘]  [ETF研究]
```

### 5.2 二级 nav 调整

**当前** (业务 4 + 工作台 1, 工作台肿胀):
```
股票 / 机构 / 模型监控 / 工作台 / [ETF 工作台 / 机会 / 列表]
```

**提议** (业务 3 + 运维 4, 各司其职):
```
业务区:    股票 / 机构 / 模型监控
运维区:    数据 / 工作台 / 策略 / 系统
ETF 区:    ETF 工作台 / 机会 / 列表  (保持原样)
```

视觉上业务区在左, 运维区在右, 中间一条分隔线.

### 5.3 数据 (Data) — 新页面

**一句话定位**: 管 raw 和 dim 层, 别管 fact 和 mart.

布局 (上中下 3 段):

```
┌────────────────────────────────────────────────────┐
│ 数据源面板 (顶部)                                    │
│ ┌──────────┬──────────┬──────────┬────────────┐    │
│ │ tdxhub   │ aif10    │ ak-em    │ ak-sina    │    │
│ │ 🟢 ok    │ 🟢 ok    │ 🟡 slow  │ 🔴 down   │    │
│ │ 优先 10  │ 优先 20  │ 优先 30  │ 优先 40    │    │
│ │ 7 类数据 │ 12 类数据│ 9 类数据 │ 1 类数据   │    │
│ │ [详情] ▼ │ [详情] ▼ │ [详情] ▼ │ [详情] ▼   │    │
│ └──────────┴──────────┴──────────┴────────────┘    │
├────────────────────────────────────────────────────┤
│ 数据 → 数据源 映射 (中部)                             │
│ ┌──────────────────────────────────────────────┐  │
│ │ capability        当前源     fallback         │  │
│ │ ─────────────────────────────────────────────│  │
│ │ kline_daily       tdxhub  → ak-em → ak-sina  │  │
│ │ financial_gpcw    tdxhub  → aif10            │  │
│ │ top_free_holders  aif10                      │  │
│ │ valuation_quantile aif10  (独家)              │  │
│ │ holder_count      aif10  → ak-em             │  │
│ │ money_flow        ak-em  (无替代)             │  │
│ │ ...                                           │  │
│ │ [+ 添加映射]                                  │  │
│ └──────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────┤
│ 数据更新调度 (底部)                                   │
│  ┌──────────────────┐  ┌─────────────────────────┐│
│  │ 智能更新           │  │ 实时日志                 ││
│  │ [运行]             │  │ ...                      ││
│  │ ─────────────     │  │                          ││
│  │ ☑ K 线追加         │  │                          ││
│  │ ☑ 十大股东        │  │                          ││
│  │ ☑ 财报              │  │                          ││
│  │ ☐ 龙虎榜 (跳过)   │  │                          ││
│  │ ☐ 机构调研         │  │                          ││
│  │ ─────────────     │  │                          ││
│  │ 总计 5/8 步         │  │                          ││
│  └──────────────────┘  └─────────────────────────┘│
└────────────────────────────────────────────────────┘
```

**数据源面板** 每张卡片点 [详情] 展开:
- 健康状态历史 (近 24h chip 条)
- 平均延迟
- 累计调用 / 失败次数
- 当前提供的 capabilities 列表 (12 个 chip)
- 配置项: timeout / retry / rate_limit / 优先级 (可拖拽改)

**数据 → 数据源映射** 每行点开可:
- 改 fallback 顺序 (拖拽)
- 锁定到某个源 (调试)
- 看最近 1000 次成功率 / 谁拉的 / 多大量

**数据更新调度**:
- 复选框选要跑的 step
- 基于 capability 自动推断要哪些数据源
- 显示估计时长 (基于历史)
- 跑的时候右边日志实时滚动
- 跑完弹审计快照

### 5.4 工作台 (Workbench) — 精简版

**一句话定位**: 只管 fact + mart, 把 raw 拉取从这里赶走.

```
┌────────────────────────────────────────────┐
│ 健康摘要 (保留 5 张卡)                        │
├────────────────────────────────────────────┤
│ 计算 DAG (可视化, 不再用 chip 列表)             │
│                                             │
│   [sync_raw]    ← 灰色, 链接到 数据 页         │
│       ↓                                      │
│   [match_inst] ✅                            │
│       ↓                                      │
│   [gen_events] ✅                            │
│       ↓                                      │
│   [calc_returns] ⏳                          │
│       ↓                                      │
│   [build_profile] ⏸                         │
│       ↓                                      │
│   [calc_stock_score] ⏸                      │
│                                             │
│  点节点 = 看详情 / 单步触发 / 看 schema_version │
├────────────────────────────────────────────┤
│ 危险操作: 全量重算派生数据                       │
│  → 这个按钮**移到 系统设置 页**, 工作台不再有   │
└────────────────────────────────────────────┘
```

工作台职责: 看现在 fact / mart 状态 + 触发计算. 跟 raw / dim 数据源完全脱钩.

### 5.5 策略 (Strategy) — 新页面

**一句话定位**: 信号参数 + 回测 + Cohort + 选股, 升级到独立页, 加预设管理.

```
┌──────────────────────────────────────────────┐
│ 顶部: 当前预设 [稳健型 ▼]  [保存] [新建] [删除]  │
├──────────────────────────────────────────────┤
│ Tab 切换:                                       │
│  [信号参数] [Cohort 闭环] [回测] [选股]          │
├──────────────────────────────────────────────┤
│                                                │
│   选中 Tab 内容区                               │
│   (现有 widget 直接搬过来作 Tab content)        │
│                                                │
│   eg. 信号参数 Tab:                              │
│     - follow / watch / skip 阈值                │
│     - cooldown                                  │
│     - 改完即时刷新股票列表                        │
│                                                │
│   eg. 回测 Tab:                                  │
│     - 时间范围                                   │
│     - 选股池                                     │
│     - [跑回测]                                   │
│     - 结果图 + 交易明细                           │
│                                                │
└──────────────────────────────────────────────┘
```

**预设管理** 是新增功能:
- 一个预设 = (信号参数 + Cohort 阈值 + 回测窗口) 的快照
- 用户保存"激进型 / 稳健型 / 试验型", 切换一秒到位
- 后端用 `dim_strategy_preset` 存

### 5.6 系统设置 (Settings) — 新页面

**一句话定位**: 工程级配置 + 危险操作.

```
┌──────────────────────────────────────────────┐
│ 数据源配置 (从 数据 页镜像)                     │
│   - 全局 timeout 默认                          │
│   - 全局 retry 默认                            │
│   - 代理设置 (Surge 警告)                       │
├──────────────────────────────────────────────┤
│ 派生层版本                                      │
│   - fact_institution_event   schema_version v3 │
│   - fact_setup_snapshot      schema_version v2 │
│   - mart_*                   schema_version v1 │
│   [清空所有派生层]                              │
├──────────────────────────────────────────────┤
│ 危险操作                                        │
│   ⚠ 全量重算派生数据  (从工作台搬来)             │
│   ⚠ 清空 raw_holdings 重拉                     │
│   ⚠ 清空所有 dim_* 重建                        │
├──────────────────────────────────────────────┤
│ 主题 / 偏好                                     │
│   - 暗色 / 亮色 (新)                            │
│   - 是否自动刷新                                 │
│   - 默认页面                                     │
├──────────────────────────────────────────────┤
│ 关于                                           │
│   - 版本: v2.x.x                               │
│   - 后端 Python: 3.x                           │
│   - DuckDB: x.x.x                              │
│   - 启动时间: ...                              │
└──────────────────────────────────────────────┘
```

---

## 6. 妙想接入路径

### 6.1 安装

```bash
# 安装独立仓
pip install git+https://github.com/dare2live/aif10-scraper.git
```

或者本地开发:

```bash
pip install -e ../aif10_scraper_standalone
```

`requirements.txt` 加一行.

### 6.2 适配层

`backend/services/data_sources/sources/aif10.py` ~50 行:

```python
from aif10_scraper import fetch_report, REPORTS, generate_ddl
from ..base import BaseDataSource, Capability, register_source

# capability 名 → reportName 映射 (我们用得到的子集)
CAPABILITY_TO_REPORT = {
    "top_free_holders":     "RPT_F10_EH_FREEHOLDERS",
    "holder_count":         "RPT_F10_EH_HOLDERNUM",
    "valuation_quantile":   "RPT_STOCKVALUATIONTANTILE",
    "peer_ranking":         "RPT_PCF10_INDUSTRY_CVALUE",
    "forecast_consensus":   "RPT_HSF10_RES_ORGRATING",
    "financial_history":    "RPT_F10_FINANCE_MAINFINADATA",
    # ... 按需加
}

@register_source
class Aif10Source(BaseDataSource):
    name = "aif10"
    display_name = "妙想 F10"
    priority = 20

    @property
    def capabilities(self):
        return [
            Capability(name=cap, freshness=...)
            for cap in CAPABILITY_TO_REPORT.keys()
        ]

    def fetch(self, capability, **kwargs):
        report_name = CAPABILITY_TO_REPORT[capability]
        result = fetch_report(report_name, mode="auto", **kwargs)
        return result["rows"]

    def healthcheck(self):
        # ping 一次 client.get_v1 拿 1 页, 看有没有 200
        ...
```

### 6.3 前端展示

数据页里 `aif10` 卡片:
```
🟢 妙想 F10 (datacenter)         优先 20
12 类数据    平均 0.3s/页    24h 调用 1547 次
[详情] [查看 GitHub] [查看 schema]
```

点开详情看到 12 个 capability 的字段映射. 每个 capability 引用 `aif10_scraper/schema/<reportName>.sql` 自动生成的 DDL.

---

## 7. 迁移路线 (Phased)

每阶段独立发布, 不走大爆炸.

### P1 - 后端 registry 骨架 (~2-3 天)

- 新建 `backend/services/data_sources/`
  - `base.py`: `BaseDataSource` / `Capability` / `Health` / `register_source`
  - `registry.py`: 单例 `_registry` + `resolve()`
- 创建 6 个 source 适配 (tdxhub / aif10 / akshare_em / akshare_sina / akshare_tx / em_datacenter)
- 各 client 内部不变, 只在 source 层 wrap 一层
- 加一个 `/api/data_sources/list` endpoint
- **不动 updater.py**, 但是新代码可以走 registry

✅ 验收: `curl /api/data_sources/list` 返回 6 条, 每条带 health.

### P2 - 数据页 UI (~3-4 天)

- 新建二级 nav "数据"
- 把现有"数据源连通性 + 数据更新按钮"从工作台搬过来
- 加数据源面板 (4 张卡 + 详情展开)
- 加数据 → 源映射表
- 数据更新调度 (复选框选 step)
- 实时日志右栏

✅ 验收: 用户在数据页能看清"我项目里 8 类数据各自走哪个源, 谁挂了"

### P3 - 工作台精简 (~1-2 天)

- 把策略参数 widget 全部移走 (P4 接手)
- 把"全量重算"按钮移走 (P5 接手)
- 把数据相关 (sync_raw 等) 标灰, 链接跳数据页
- 把管线 chip 改成 DAG SVG
- 工作台只保留: 健康摘要 (5 卡) + DAG + 单步触发

✅ 验收: 工作台高度 < 1000px, 用户一眼看出"现在 fact / mart 健康吗".

### P4 - 策略页 + 预设 (~3-4 天)

- 新建二级 nav "策略"
- 4 个 widget (信号参数 / Cohort / 回测 / 选股) 转 Tab
- 顶部加预设选择器
- 后端建 `dim_strategy_preset` 表
- 老 widget 从工作台移除

✅ 验收: 用户切预设一秒到位, 信号参数 / 阈值都按预设刷新.

### P5 - 系统设置页 (~2 天)

- 新建二级 nav "系统"
- 集中"全量重算" + 派生层版本管理 + 数据源参数 + 主题
- 把所有"危险操作" 集中在这里

✅ 验收: 跨页面找运维按钮的次数从 3 次降到 1 次.

### P6 (远期) - 可视化配置完善 (~5+ 天)

- 数据 → 源映射改可拖拽 (现在 P2 是只读)
- 数据源 manifest 可在 UI 里编辑 (现在是 .py 改)
- 预设导入 / 导出 JSON

---

## 8. 待决策点 (需要你拍板)

| 编号 | 决策 | 候选 | 我的倾向 |
|---|---|---|---|
| D1 | 是否让 ETF 区也接入数据源 registry? | (a) 是, ETF 也走 registry / (b) 否, ETF 自治 | (a) 一致性更好 |
| D2 | 一级 nav 改 3 大块 (业务/运维/ETF) 还是保持现在 2 个 board? | (a) 改 / (b) 保持 | (b) 不动 nav, 二级里分组 |
| D3 | 妙想接入是 git+url 还是发 PyPI? | (a) git+ / (b) PyPI | (a) 现在 private, 先 git |
| D4 | aif10-scraper 仓改 public 还是保持 private? | (a) public / (b) private | (b) 先 private, 稳定后 public |
| D5 | data_sources 的 capability 是写代码还是写 yaml? | (a) Python class / (b) YAML manifest | (a) 类型安全, 不引入新依赖 |
| D6 | 失败 telemetry 进 DuckDB 还是只内存? | (a) DuckDB 表 / (b) 内存 ring buffer | (a) 长期可分析 |
| D7 | 数据页和现工作台共用健康摘要带, 还是各自不同? | (a) 共用 / (b) 不同 | (b) 数据页关心 raw, 工作台关心 fact, 不同维度 |
| D8 | 策略预设是用户级还是全局? | (a) 全局 1 套 / (b) 每用户独立 | (a) 这是个人项目, 全局够 |

---

## 9. 不在本次范围

- 业务页面 (股票 / 机构 / 模型监控) 内部布局不动
- ETF 三页内部布局不动
- 模型评分公式不动
- 交易 / 下单 / 风控逻辑 (项目目前没有)
- 多用户认证 / 权限 (项目目前是单机)

---

## 10. 风险

| 风险 | 应对 |
|---|---|
| registry 抽象错了, 不同源差异太大塞不进去 | P1 先做 6 个源的样本, 跑通再扩展 |
| 工作台用户不适应 (现有用户就你一个, 但仍有学习成本) | 工作台保留时跳到数据页有"以前在这里"提示 (1 个版本后撤) |
| 妙想 git+ 安装在 launchd/cron 环境跑可能装不上 | start.command 检查时 `pip install --upgrade aif10-scraper`, 同 akshare |
| 12 个 client 改造工作量大 | P1 只 wrap 不改 client 内部, 改造量小 |
| 派生层 schema_version 现在不存在, 加上要 migrate 历史表 | P5 配合数据库迁移脚本, 默认所有现有表 v1 |

---

## 11. 下一步

请你 review 本文档, 重点反馈:

1. 总体结构 (4 个新页面 + registry) 是不是你想要的?
2. D1-D8 待决策点的选择
3. P1 - P5 的优先级顺序对不对? 有没有想先做的?
4. 还有什么没想到的?

review 完, 我们按你定的顺序逐个 phase 实现. 每个 phase 完了我提交 + 等你验证再开下个.
