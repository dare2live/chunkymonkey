# aif10_scraper — vendored

来源: https://github.com/dare2live/aif10-scraper.git (业主自己的仓)
并入日期: 2026-09-08
并入时上游 commit: `e162d7b990c8115d40358b5b5eaaf73f68350dd6`
许可: MIT, Copyright (c) 2026 Jim Morrison (LICENSE 随包拷入)

## 并入范围 = 整包

实测导入闭包(真跑一次 import 取 sys.modules 差集) **8 模块 / 1,975 行**, **恰好等于整包** ——
没有可裁的部分, 全部并入。

    aif10_scraper/__init__.py          45
    aif10_scraper/batch.py            421
    aif10_scraper/client.py           207
    aif10_scraper/orm/__init__.py      21
    aif10_scraper/orm/ddl.py          158
    aif10_scraper/orm/type_infer.py   186
    aif10_scraper/pagination.py       270
    aif10_scraper/registry.py         667

## 为什么在仓内

原来靠 `sibling_repos.ensure_import_path("miaoxiang")` 插 sys.path, 8 个消费文件里散着
18 行 import 与 8 处 ensure_import_path 调用。那个布局的三个实测问题(与 marketdb 同,
详见 backend/marketdb/VENDOR.md):没钉版本 / 不受 22 道门管 / 不在 CI 可达范围。

另外它此前是 `pip install -e` 装的, 于是"包在哪"有两个答案(editable 指向 sibling,
PYTHONPATH 指向仓内)。并入同批卸掉 editable 安装, 只留一个答案。

## 消费方

backend/services/{holders_aif10,org_holding_aif10,org_holding_fetch,qfii_client}.py /
backend/services/data_sources/{moneyflow_recon.py,sources/miaoxiang.py} /
backend/scripts/{ingest_holders_raw,recon_assignment_gaps,recon_fina_margin}.py

## 与上游的差异（重新同步上游时必须先读这节）

并入当天改了 7 处 `except ...: pass`（rule_compliance 门抓出来的）。**这些是行为改动，
不是排版**，上游没有对应 commit：

**4 处 `except KeyError: pass` → 抛**（`batch.py`，包住 `get_report(report_name)`）
上游的写法是：registry 里查不到这个 report 就静默把 `spec` 留成 `None`，于是
`sort_columns` / `sort_types` 落空，请求**不带排序**发出去。东财 v1 是分页接口，
没有稳定排序时同一行可能落在两页、另一行一页不落；下游按 grain 去重后，表现是
「行数对得上、内容少了」，不报错。撞本项目红线「缺失只能传播为缺失」。
实测本仓在用的 3 个 report（`RPT_DMSK_HOLDERS` / `RPT_F10_EH_FREEHOLDERS` /
`RPT_MAIN_ORGHOLDDETAIL`）全部已注册，这条分支今天走不到 —— 走到就该响。

**3 处 `except Exception: pass` → 记 warning**（`batch.py` 1 处、`pagination.py` 2 处，
包住 `progress_callback`）
这三处吞的是显示层回调，行已经收在结果里，不影响完整性，所以保持「不中断取数」的
语义不变；只是从「吞掉且无痕」改成「吞掉并记一行 warning」。`pagination.py` 因此新增
了模块级 logger。

**2 处白星符号（U+2B50）→ `[重点]`**（`registry.py` 的 `subname`）
本项目全局禁 emoji（no_emoji 门）。`subname` 只在 `orm/ddl.py` 生成一行 SQL 注释时用到，
不参与任何匹配，改字面量无行为影响；上游拿它标「重点报表」的语义用 `[重点]` 保留。
（这里写码点不写字符本身：no_emoji 门问的是「staged 内容里有没有」，不问是不是在描述它。）

## 上游未并入的证据（一手源，指向 commit 不指向工作树）

上游仓有三份文件承载本包的实测出处，**没有**并入本仓（并入范围只含 package）。它们
在上游 GitHub 上永久可解析，所以这里只留指针；同时列出「本仓无法自己重新得到」的部分。

| 文件 | 上游 commit | 内容 | 本仓是否已有等价物 |
|---|---|---|---|
| `600519_F10_data_source_report.md` | `8326cb9` | 881 行，F10 十四个一级模块 / 50+ 二级子栏目逐栏目 DevTools 抓包；附录 A 是完整接口清单 | 主体已升级为 `registry.py` 的 74 条 `ReportSpec`（比 markdown 表格更强的机器可读形式）。**缺 4 个**，见下 |
| `STRESS_TEST_RESULTS.md` | `a1501e3` | 92 行，2026-04-27 单 IP 并发实测曲线 | **无等价物**，数字已抄进 `batch.py` 头注与 `sync_registry.yaml` 的 `sources.miaoxiang` 注释 |
| `docs/p6_probe.json` | `a1501e3` | 684 行，600519.SH 上 20 条 report 的探测记录 | 端点身份 20/20 全在 `registry.py`；100 页上限本仓 2026-07-24 独立复测过（`pagination_integrity.py`）；字段快照本来就该现场重探（`orm/type_infer.py` 是运行时推断） |

**附录 A 有而 `registry.py` 没有的 4 个 reportName**（要用先重新探测再登记，不照抄一行
表格发明 `ReportSpec` —— `key` / `date_field` / `frequency` / `sort_columns` 报告里都没给）：

    RPTA_DATA_IF_INDICATOR
    RPTA_DATA_IF_LINECHART
    RPT_CUSTOM_DMSK_TREND
    RPT_NORTH_ORG_HOLDDETAIL_NEW

另有 `stress/concurrency_test.py`（109 行，压测 harness）也未并入 —— 注意它是压测**前**
写的，验收标准写着「最高 QPS 不触发 rc=100」，而 `STRESS_TEST_RESULTS.md` 的结论是**根本
没有限流**。`batch.py` 原头注抄的是那个假设不是结论，并入时已改。

## 并入后的自查（重新同步上游时也跑一遍）

```
grep -rnE '(^|[^/A-Za-z])(docs|tests|scripts|stress)/[A-Za-z0-9_./-]+\.(py|md|json)' \
  backend/aif10_scraper | grep -v 'backend/' | grep -v 上游
```

输出必须为空：每条路径引用要么带 `backend/` 前缀且文件真存在，要么在同一行明标
`上游@<SHA>`。这是手跑的规则不是门 —— 门分不清「历史提及」与「路径声明」，硬做会假阳性
（红线 13：无法机器验证的写进规则，不写进闸）。
