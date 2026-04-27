# aif10_scraper

东方财富妙想 F10 (`aif10.eastmoney.com` / `datacenter.eastmoney.com/securities`) 全量解析项目.

类 akshare 风格, 但只覆盖一个数据源 (妙想 F10), 设计目标:

1. **全量抓取能力**: 16 个一级模块的全部 reportName + 字段一对一解析
2. **可选 / 选择性抓取**: 按模块 / 按报告期 / 按股票
3. **压力测试**: 探测单 IP 并发 + QPS 上限
4. **数据库导入**: 一表一 reportName, 自动生成 SQL DDL

**非目标**: 应用层集成 (跟 chunky-monkey 项目的 fact_feature_panel 集成另算, 见 `docs/model-data-optimization-discussion.md` §6).

## 关键发现 (2026-04-27 实测)

**妙想 F10 天然支持全市场批量分页**, 不需要逐 SECUCODE 过滤:

```python
result = call_hsf10('RPT_STOCKVALUATIONTANTILE', page=1, page_size=500)
# pages=187 count=93285 (全市场 × 全历史)
```

187 页 × 0.3s = 60 秒拿到全市场所有股票全历史估值分位. 比 Phase 1 datacenter-web 逐股快 4-27 倍.

## 模块概览 (注册表 72 项 / 全部 batch_friendly)

`registry.py` 收录 72 个 reportName, 全部支持不带 SECUCODE 的全市场分页. 见 `docs/eastmoney-aif10-spec.md`.

| 模块 | reportName | 注册 | DDL 生成 |
|---|---:|---:|---:|
| trading 操盘必读 | 8 | ✓ | ✓ |
| shareholder 股东研究 | 11 | ✓ | ✓ |
| business 经营分析 | 3 | ✓ | ✓ |
| themes 核心题材 | 3 | ✓ | ✓ |
| events 公司大事 | 5 | ✓ | ✓ |
| profile 公司概况 | 5 | ✓ | ✓ |
| peer 同行比较 | 6 | ✓ | ✓ |
| forecast 盈利预测 | 5 | ✓ | ✓ |
| financial 财务分析 | 11 | ✓ | ✓ (v0/v1 混合) |
| dividend 分红融资 | 9 | ✓ | ✓ |
| share_capital 股本 | 1 | ✓ | ✓ |
| executives 高管 | 2 | ✓ | ✓ |
| capital_ops 资本运作 | 2 | ✓ | ✓ |
| related 关联 | 1 | ✓ | ✓ |

资讯/公告/研报全文在独立子域 (`np-anotice-pc` / `np-cnotice-pc` / `np-creport-pc`), 暂未实现.

## 用法

```python
from aif10_scraper import fetch_report, generate_ddl

# 1. 拉一个 reportName 全量
r = fetch_report("RPT_F10_EH_FREEHOLDERS", mode="concurrent", concurrency=20)
print(f"{r['total_rows']} 行 / {r['elapsed_s']}s")

# 2. 自动从样本生成 CREATE TABLE
ddl = generate_ddl("RPT_F10_EH_FREEHOLDERS", r["rows"][:200], dialect="duckdb")
print(ddl)

# 3. 入库 (DuckDB 例)
import duckdb
con = duckdb.connect("aif10.db")
con.execute(ddl)
con.executemany(
    f"INSERT INTO rpt_f10_eh_freeholders VALUES ({','.join(['?']*len(r['rows'][0]))})",
    [tuple(row.values()) for row in r["rows"]],
)
```

CLI 一键生成全部 DDL:

```bash
# 单个
python scripts/generate_ddl.py RPT_F10_EH_FREEHOLDERS

# 整个 module
python scripts/generate_ddl.py --module shareholder

# 全部 (~2 min)
python scripts/generate_ddl.py --all

# 验证生成的 DDL 在 DuckDB 里都能跑
python scripts/validate_schema.py
```

## 项目结构

```
aif10_scraper/
├── aif10_scraper/
│   ├── client.py            # HTTP client (Session + retry + timeout + UA + Referer)
│   ├── registry.py          # ⭐ 16 模块 reportName 注册表 (72 项)
│   ├── batch.py             # 顺序 + 异步并发分页 (concurrency=20 甜蜜点)
│   └── orm/                 # SQL DDL 自动生成 (从样本 → CREATE TABLE)
│       ├── type_infer.py    # 字段类型推断 (BOOLEAN/INT/DOUBLE/DATE/TIMESTAMP/VARCHAR/JSON)
│       └── ddl.py           # DuckDB / SQLite / Postgres 三方言
├── scripts/
│   ├── generate_ddl.py      # CLI: 拉样本 → schema/<name>.sql
│   └── validate_schema.py   # 用 DuckDB :memory: 校验 DDL 都能跑
├── schema/                  # 自动生成的 DDL (一 reportName 一文件)
├── stress/concurrency_test.py
├── examples/01_quick_start.py
└── STRESS_TEST_RESULTS.md   # 单 IP 并发实测: 26 QPS, 13K 行/秒
```

## 与 chunky-monkey 项目的关系

- **本项目**: 独立解析层, 不依赖 chunky-monkey
- **chunky-monkey/backend/services/eastmoney_skill/**: 应用层, 引用本项目 (Phase 2.5+)
- 跑通后可发 GitHub 独立 PyPI 包 (远期)
