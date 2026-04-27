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

## 模块概览 (16 个)

参考 `docs/eastmoney-aif10-spec.md` 的完整 spec.

| 模块 | reportName 数 | 状态 |
|---|---|---|
| 1. 操盘必读 | 8 | ⬜ TODO |
| 2. 股东研究 | 11 | ⬜ TODO |
| 3. 经营分析 | 3 | ⬜ TODO |
| 4. 核心题材 | 4 | ⬜ TODO |
| 5. 资讯公告 | 4 (含全文) | ⬜ TODO |
| 6. 公司大事 | 8 | ⬜ TODO |
| 7. 公司概况 | 5 | ⬜ TODO |
| 8. 同行比较 | 6 | ⬜ TODO |
| 9. 盈利预测 | 5 | ⬜ TODO |
| 10. 研究报告 | 1 (PDF/全文) | ⬜ TODO |
| 11. 财务分析 | 12 | ⬜ TODO |
| 12. 分红融资 | 7 | ⬜ TODO |
| 13. 股本结构 | 3 | ⬜ TODO |
| 14. 公司高管 | 2 | ⬜ TODO |
| 15. 资本运作 | 2 | ⬜ TODO |
| 16. 关联个股 | 5 | ⬜ TODO |

总计约 **86 个 reportName** + 4 类全文接口 (公告 / 资讯 / 研报 / 信息流).

## 项目结构

```
aif10_scraper/
├── aif10_scraper/
│   ├── client.py            # HTTP client (Session + retry + timeout + UA + Referer)
│   ├── registry.py          # ⭐ 16 模块 reportName 注册表 (核心)
│   ├── batch.py             # 异步批量分页 + 并发 runner
│   ├── schema_check.py      # 字段缺失/新增告警
│   ├── reports/             # 16 个模块对应的业务封装
│   │   ├── trading.py       # 操盘必读
│   │   ├── shareholder.py   # 股东研究
│   │   ├── financial.py     # 财务分析
│   │   └── ...
│   ├── orm/                 # SQL DDL 自动生成 (一 reportName 一张表)
│   └── utils/
│       ├── secucode.py
│       └── unit_norm.py
├── stress/                  # 压力测试
│   ├── concurrency_test.py  # 1/2/5/10/20 并发 QPS 上限探测
│   └── batch_full_scan.py   # 全市场单模块全量拉
├── tests/
└── examples/
    ├── 01_single_stock.py   # 单股 16 模块全 scan
    ├── 02_market_full.py    # 全市场单模块批量
    └── 03_db_export.py      # 导出 DuckDB / Parquet
```

## 与 chunky-monkey 项目的关系

- **本项目**: 独立解析层, 不依赖 chunky-monkey
- **chunky-monkey/backend/services/eastmoney_skill/**: 应用层, 引用本项目 (Phase 2.5+)
- 跑通后可发 GitHub 独立 PyPI 包 (远期)
