# ChunkyMonkey 前端设计方案

> 状态：live · 本文件是前端面的唯一设计 owner。
> 归属（依 `docs/README.md` 生命周期表）：架构与契约语义归 `docs/MASTER_TOPLEVEL_DESIGN.md`，验证纪律归
> `docs/strategy_validation_contract.md`，工程纪律归 `docs/engineering_governance.md`；本文只拥有
> **界面信息架构、视觉系统、页面↔端点映射、展示诚实性约定**，不转述任何 Tier 语义。
> 站点根：`frontend/app/`（无构建步；FastAPI 挂 `/app/`）。Kimi 的单页 HTML 只是设计样稿，
> 不是产品形态 —— 每个标签是独立网页，共享视觉与导航。
> 后端静态挂载见 `backend/main.py`。

## 1. 设计原点

项目两件大事：**数据底座**（获取→清洗→加工→入库→完整性/连续性检查）与**策略验证**（在其上做研究）。
前端因此分三个空间，而不是市面产品那种满屏平铺：

| 空间 | 定位 | 气质 |
|---|---|---|
| FOUNDATION · 底座 | 管理向：数据的整体、局部、流程、健康 | 工程台账，如实、可下钻 |
| LAB · 实验室 | 研究向：实验、消融、发布门、快照封存 | 判决文书，克制 |
| INSIGHT · 洞察 | 应用向：底座与研究的只读消费投影 | 简单直观，设计巧妙 |

每个标签是**独立 HTML 页**，URL 为 `/app/<space>/<tab>.html`。页头空间钮 + 标签栏是共享铬
（`js/core.js`），不是把整站塞进一个 hash 路由。跨页跳转靠 `data-nav` + 可选
`data-code` / `data-holder` / `data-chain` / `data-domain`，落到真实 query：

| 深链 | URL |
|---|---|
| 个股档案 | `/app/insight/dossier.html?code=600519` |
| 机构席位展开 | `/app/insight/inst.html?holder=<name>` |
| 板块下钻 | `/app/insight/sector.html?chain=sw_industry&code=...` |
| 域详情 | `/app/foundation/domain.html?domain=moneyflow` |

## 2. 视觉系统

**马卡龙奶油色系**，整体平静、祥和、灵动；同类内容用色阶区分。

- 纸面：`--paper` / `--paper-2` 奶油底；墨色 `--ink` / `--ink-2` / `--ink-3`。
- **红涨绿跌**（全站统一，资金相关一律遵守）：
  - 流入/上涨红阶 `--in-1..4`（浅→深）+ 文字 `--in-tx`；
  - 流出/下跌绿阶 `--out-1..4` + 文字 `--out-tx`。
  - 色阶映射数值强度（如热力格按 |净流| 分四档；连板梯队按名次深浅）。
- 状态色：`--ok` 绿 / `--soft` 黄（stale、delegated）/ `--unk` 灰（unknown、empty）/ `--hole` 洞红 / `--hard` 硬红。
- 字体：无衬线正文 + 等宽（`--mono`）用于数值、代码、as-of 标注。
- 变动/研究标签用同族胶囊（`.chg` 红绿色阶；`.rtag` 中性描边），不另起一套 UI 语言。

**铁律：文字不进会被非均匀拉伸的 SVG**（`preserveAspectRatio="none"` 的图）。
曲线图的「窗口累计」等标签一律用 HTML overlay（`.cv-cum`）绝对定位，字形永不变形。

共享样式只放 `frontend/app/css/site.css`。

## 3. 诚实性约定（本面与市面产品的分界线）

1. **加工层下移**：展示变量尽量由后端产出（`cum_values` / `total_series` / `stripe_cum` / `horizons` /
   `usability.tabs` / `change_counts` / `exited`），前端只展示；旧后端缺字段时才允许本地累计兜底，
   并在代码注释标明。
2. **stale 是常态信息，不是异常**：所有消费面展示 as-of 与滞后原因（`stale-banner`），滞后照实标注，不装新鲜。
3. **typed empty**：空态分「条件型空态」（无标的满足条件，空本身即信息）与「能力空态」（域不可用，给 reason）。
   禁止用 filler 填满；禁止把 404/网络失败渲染成空白图。
4. **缺失传播为缺失**：`unknown` 不猜、不补零、不拿旧值冒充；缺日断柱断线。
5. **信任门**：叙事类输出（盘中简报）在能力灯不全绿时输出 `NARRATIVE = NULL` —— NULL 是系统输出，不是加载失败。
6. **披露/研究面常驻合规横幅**：如机构席位 `tier3_research_evidence_only` + conformity 状态 + `cutover_allowed`。
7. **离线烘焙兜底**：后端不可达时，**台账/实验室/市场总览**可展示标注了日期的真实快照，并在页脚说明；
   烘焙内容必须是真值快照，不得编造。个股档案 / 股票列表 / K 线 **不**用烘焙旧股顶上 —— 离线即 typed empty。
8. **量纲**：后端小数（`0.15` = 15%）在本页用「已是百分数」的 `fmtPct` 时必须先 ×100；不要把这套函数抄到会自己 ×100 的运行时。
9. **K 线是 qfq 分析视图**，不是名义成交价；图注必须写 `qfq`，不暗示可按图成交。
10. **研究标签是命名层**：`research_identity` / `seat_research_class` 只展示 tags，禁止把国家队、外资自有、席位净买加总成一个「热钱」。

## 4. 页面 ↔ 端点映射

文件路径 = `/app/<space>/<tab>.html`。下列「标签」与文件名一致。

### FOUNDATION
| 页 | 文件 | 数据源 |
|---|---|---|
| 全量矩阵 | `foundation/matrix.html` | 现查快照（烘焙）→ 域详情 |
| 工作台 | `foundation/ops.html` | `GET /api/v3/ops/jobs/daily_update` · `GET /api/v3/ops/pipeline/nodes` · POST 触发 |
| 运行回放 | `foundation/run.html` | 烘焙 |
| 门与健康 | `foundation/gates.html` | 烘焙 |
| 域详情 | `foundation/domain.html` | 烘焙 + 下钻参数 `?domain=` |

### LAB
| 页 | 文件 |
|---|---|
| 实验总览 | `lab/overview.html` |
| 实验明细 | `lab/experiments.html` |
| 消融详情 | `lab/expdetail.html` |
| 发布门 | `lab/release.html` |
| 快照封存 | `lab/snapshots.html` |

读研究工件与冻结清单；判决永不被 UI 美化。

### INSIGHT
| 页 | 文件 | 端点 |
|---|---|---|
| 市场快照 | `insight/market.html` | `pulse/sentiment` · `pulse/drill` · `pulse/heatmap` · `pulse/strongest` · `pulse/flow_board` |
| 资金流向 | `insight/flows.html` | `pulse/heatmap` · `pulse/flow_board` |
| 退潮预警 | `insight/warnings.html` | `pulse/warnings` |
| 板块下钻 | `insight/sector.html` | `pulse/drill` · `pulse/flow_stripe` |
| 盘中简报 | `insight/briefing.html` | `decision/briefing/daily` |
| 形态选股 | `insight/screener.html` | `screener/options` · `screener/form_stage` |
| 个股档案 | `insight/dossier.html` | 列表 `stock/list`；详情 `stock/{code}/dossier` · `stock/{code}/kline` · `decision/moneyflow/stock/{code}` · `decision/intersection/stock/{code}` |
| 机构席位 | `insight/inst.html` | `inst/profiles` · `inst/profiles/{holder}` |
| 观察账本 | `insight/paper.html` | `paper/portfolio` · `paper/nav` |

**跳转闭环**：板块下钻叶子 → 个股档案；个股档案机构面 → 机构席位 `?holder=` 自动展开（即使该户不在本页前 500 排名表）；机构 episode 行 → 个股档案。任何一面都不许是死路。
点行（含代码/名称格）进档案；雪球是行末独立小链 `.xq`，不得把代码/名称整格包成外链 —— `core.js` 对 `a.xq` 放行，包住名称等于抢走档案入口。

**股票列表契约**：
- 身份行 = `ref.dim_active_a_stock`（身份缓存，**不是**观察日 `traded_on` 宇宙）。标题必须写明。
- 形态标签 = `fact_stock_form_daily` 最新快照日 LEFT JOIN；未覆盖为 NULL，不回填。
- `facets.form_name` / `breakout` 是该快照日的**全市场普查计数**，不随当前 `q`/`tag` 缩放 ——
  chip 是结构阅读，不是筛选直方图。
- attach `reference` 失败 = 能力空态（HTTP 503），不是无名列表。

## 5. 个股档案语义

默认进入列表（`dossier.html` 无 `code`）。`?code=` 六位代码进入详情。头部灯色映射
`usability.tabs.*.status`（ok=绿 ● / stale、delegated=黄 ◐ / empty、unknown=灰 ▨）。

迷你标签（仍在档案页内，不是新的顶层 URL）：

- **形态**：qfq K 线 + 成交量（`stock/{code}/kline`，空则 typed empty）+ 五轴 + observation + `production_read_status`。
- **资金**：delegated 到 decision 端点，七档窗口；流通市值缺则 unknown 不除。
- **股东**：十大流通股东（披露轴，非交易日轴）+ 变动胶囊（新进/增持/减持/不变，股数来自 `hold_change_num`）
  + `change_counts` 汇总条 + **本期退出**独立区（`holders.exited`，period-diff，不与在榜行混排）
  + 连续在榜期数（`approx_periods_present`，近 8 期窗口 heuristic，title 必须写明）
  + 持股收益仅闭合 episode 的 `return_pct`（α 小数）；持有中 = —
  + 研究标签 `.rtag` 来自 `research_identity`（命名层，不加总）
  + 户数变化曲线。
- **机构**：`holders.institution_profile` 覆盖摘要 + 有画像股东行；`episode_only` 不假链。
- **交集**：三链强势交集命中与否；stale 照标。
- **龙虎榜**：`lhb_seats`（席位日，不是十大股东）；无上榜 = typed empty，不补零。席位名可带
  `seat_research_class` 标签与民间名，法定名仍保留。

## 6. 维护规则

1. 改页面结构/端点映射/色彩语义，**先改本文**，再改 `frontend/app/<space>/<tab>.html` 与共享
   `css/site.css`、`js/core.js`、`js/live.js`。无 `dist/`、无 npm、无 React/Vite。
2. 本文不写任何随运行变化的值（前沿、as-of、行数）——运行时状态现查 `scripts/chunkyctl status` 或页面本身。
3. 新增消费面：先在本文登记 **文件路径 + 端点 + 诚实性约定**，再实现成独立网页，不要把新面塞进已有页的 display:none 堆里。
4. 共享铬只通过 `core.js` 绘制。页面之间用真实 URL，不用 hash 伪装成单页应用。
