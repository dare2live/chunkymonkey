# Endpoint Classification

## Public Product Contracts

### Institution / Stock Research

- `/api/inst/institutions`
- `/api/inst/institutions/search`
- `/api/inst/institutions` `POST`
- `/api/inst/institutions/batch`
- `/api/inst/institutions/{inst_id}` `PUT`
- `/api/inst/institutions/{inst_id}` `DELETE`
- `/api/inst/events`
- `/api/inst/profiles`
- `/api/inst/stock-trends`
- `/api/inst/candidate-setups`
- `/api/inst/setup-tracking/snapshots`
- `/api/inst/setup-validation/report`
- `/api/inst/stock-validation/report`
- `/api/inst/watchlist`
- `/api/inst/watchlist` `POST`
- `/api/inst/stocks/blacklist`
- `/api/inst/stocks/blacklist` `POST`
- `/api/inst/stocks/blacklist/{stock_code}` `DELETE`
- `/api/inst/profiles/detail/{inst_id}`
- `/api/inst/stocks/detail/{stock_code}`
- `/api/inst/profiles/returns-history/{inst_id}`
- `/api/inst/exclusions/categories`
- `/api/inst/scoring/config/{card_type}` `GET/POST/DELETE`
- `/api/inst/scoring/framework/{card_type}`
- `/api/inst/scoring/breakdown/{card_type}/{object_id}`
- `/api/inst/scoring/calculate/{card_type}`

### Data / Ops Used By SPA

- `/api/inst/market/status`
- `/api/inst/update/status`
- `/api/inst/update/stop`
- `/api/inst/update/reset-derived`
- `/api/inst/update/connectivity`
- `/api/inst/update/audit`
- `/api/inst/update/smart`
- `/api/inst/update/step/{step_id}`
- `/api/inst/update/sync`
- `/api/inst/update/calc`
- `/api/inst/update/mart`
- `/api/inst/update/all`
- `/api/inst/lifeboat/run`
- `/api/inst/lifeboat/status`
- `/api/inst/lifeboat/report`
- `/api/screening/industry-overview`
- `/health`
- `/api/settings/modules`

## Internal Routes

- `/api/inst/setup-replay/summary`
- `/api/inst/setup-replay/factors`
- `/api/inst/setup-replay/events`
- `/api/inst/setup-tracking/summary`
- `/api/inst/holdings`
- `/api/inst/industry-stats`
- `/api/inst/stocks/attention/{stock_code}`
- `/api/inst/update/smart-plan`

Rationale:

- 仓内无前端消费者。
- 仍保留分析、调试、运维或直连排查价值。
- 不应作为产品公开契约继续暴露在 openapi 中。

## Compat Routes

- `/api/screening/sector-momentum`
- `/api/screening/dual-confirm`
- `/api/screening/results`
- `/api/screening/detail/{stock_code}`
- `/api/screening/summary`

Rationale:

- 已有显式 `include_in_schema=False`。
- 用于兼容旧入口或隐藏查询，不进入产品契约。

## Retire Candidates

- 当前活跃路由里暂无新的高置信度 `retire` 候选。
- 已在历史审计中出现的 `financial/*` 与 `stock-latest-periods`，在当前仓库里已不再是活跃待办。
- 未来若 internal 路由失去分析/调试价值，再转入 retire 队列，而不是直接从 public 删除后悬空。