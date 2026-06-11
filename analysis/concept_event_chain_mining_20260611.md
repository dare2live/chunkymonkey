# 概念增删事件流 → 粗颗粒产业链挖掘: 可行性评估 + 设计

> 2026-06-11 | 用户思路: "tushare 里的同花顺、东财、通达信好像是每天都会增加删除概念
> (国产替代、去日本化等), 是否可以用于粗颗粒度的产业链挖掘?"
> 结论先行: **可行, 且与已有链谱设计正交互补 — 但它是"题材确认器"不是"产业链发现器",
> 方向性 (上下游) 必须靠 fina_mainbz + iFind + 互动易补。**

## 1. 数据可行性 (catalog 实测口径)

四家概念源能力矩阵 (积分要求全部 <= 本账号 10000):

| 源 | 接口 | 历史成分 | 积分 | 状态 |
|---|---|---|---|---|
| 同花顺 THS | `ths_index` / `ths_member` / `ths_daily` | **无历史** — member 仅当前快照 | 6000 | 自养快照已启动 (launchd 17:40 每日) + chain2 全量排队中 |
| 东财 DC | `dc_index` / `dc_member` | **按 trade_date 循环取历史** | 6000 | sync_registry 已注册, chain2 回填排队中 |
| 通达信 TDX | `tdx_index` / `tdx_member` / `tdx_daily` | **按日期循环取历史** | 6000 | 未注册 — 本评估新增建议 |
| 开盘啦 KPL | `kpl_concept_cons` | 按代码+日期循环取历史 | 5000 | 未注册 — 题材更替最快的源 |
| 涨停题材 | `limit_cpt_list` | 2024-01 起 | 8000 | sync_registry 已注册 |

关键不对称: **DC/TDX/KPL 可重建历史事件流 (可回测), THS 只能从自养日 forward**。
这决定了实验设计: 历史检验用 DC/TDX/KPL, THS 流作为第 4 票从今天开始积累。

## 2. 事件流设计 (最小模块)

### 2.1 表 (一张, grain 幂等)

`fact_concept_event(event_date, source, concept_code, concept_name, event_type, con_code, as_of_mode)`

- `event_type`: `concept_born` / `concept_dead` / `member_add` / `member_drop`
- `as_of_mode`: `observed` (自养快照 diff, 真 PIT) / `reconstructed` (历史回填 diff,
  假设数据商当日发布 — PIT 弱假设, 必须显式标注, 回测结果须做 1-3 日滞后敏感性)
- 生成器: 相邻交易日成分集合 diff, 纯 SQL/pandas, 无新服务 — 挂 sync_runner 落库后的
  post-step 即可

### 2.2 三个可行用途 (按证据强度排序)

1. **主题生命周期时间戳 (Serenity B11 对接)** — 概念诞生日 = 数据商把市场叙事
   工程化的客观时刻。它**滞后**题材启动 (确认效应), 所以定位是 phase 标记器:
   诞生前 = 萌芽期 (只有 L1/L2 链谱+资金流能看见), 诞生日起 = 确认期/加速期。
   measured 实验 (chain2 落库后): 概念诞生后 5/10/20 日成分超额收益分布 + 诞生时
   已涨幅度分桶 — 验证"诞生即追高"还是"诞生后仍有半段"。
2. **链谱边弱监督 (industry_chain.yaml 喂料)** — `member_add(股票X, 概念C)` = 数据商
   确认 X 与主题 C 关联。四源投票降噪: 仅 >=2 源同向才入候选边。这直接填补链谱
   "成员关系靠人工维护"的缺口, 人工只剩审核。
3. **共现图 → 粗颗粒链谱** — 两股票长期共属概念集合的 Jaccard 相似度建图, 社区检测出
   "题材簇"。诚实边界: 这是**题材近邻度, 不含上下游方向**。方向性三件套另有分工:
   fina_mainbz 主营收入 (节点定位) + iFind 产业链工作流 (层级结构) + 互动易 (关系边)。

### 2.3 不做什么

- 不用概念事件直接做买卖信号 (单源滞后 + 数据商口味噪音)
- 不让"事件类概念" (去日本化/国产替代这类政策叙事) 进共现图权重 — 它们成分宽泛,
  会把无产业关联的股票连成假边; 用 `ths_index.type` / 板块层级字段过滤或降权
- 不在验证期给任何概念因子真金白银权重 (宪法: 0 真金白银验证期)

## 3. iFind MCP 接入验证记录 (2026-06-11)

| 项 | 结果 |
|---|---|
| token 存放 | `.env` `IFIND_MCP_TOKEN` (gitignored, 实测 `git check-ignore` PASS); 绝不入 git/config/报告 |
| stock-mcp | initialize HTTP/2 200 + session 建立, tools/list 9 工具 (与 06-05 smoke 一致) |
| index-mcp | `sector_data` 实弹返回真数据: "换芯"概念 20260611, 9 成分, 5日均 -3.28% |
| MCP 注册 | `~/.claude.json` local scope (非 git): `ifind-stock` / `ifind-index` / `ifind-news`, 下个会话生效 |
| 定位 | 不变 (goal.md): research-only — 产业链语义发现 / 概念板块 PIT 快照 / 新闻公告证据; 不做生产 writer |

生产警示 (实测):
1. 语义检索非确定性 — 查"国产替代"被就近匹配到"换芯"板块。链谱工作流必须先用
   `sector_data` 解析板块代码, 再用代码做确定性查询。
2. 配额按 trial 口径预算 (2000 总请求 / 2rps), 只许人工触发的研究会话消费,
   **不进任何 cron/launchd**。
3. 经 Surge 代理 — 探活必须协议层 (initialize 握手), 不信 TCP connect (宪法既有教训)。

## 4. 行动项

| # | 动作 | 前置 |
|---|---|---|
| 1 | `tdx_index`/`tdx_member`/`kpl_concept_cons` 三域加入 sync_registry (历史按日回填) | chain1/2 队列完成后追加, 避免 gateway 并发上限 2 互踩 |
| 2 | `fact_concept_event` 生成器 + observed/reconstructed 双模式 | 概念域落库 |
| 3 | measured 实验: 概念诞生后 N 日超额 + member_add 股 forward 分布 | #2 |
| 4 | 四源投票边 → industry_chain.yaml 候选区 (人工审核入谱) | #2, Serenity 集成 W5 |
