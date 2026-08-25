# ChunkyMonkey 前端

过渡期 read / observation surface，不是 Tier4 决策产品。

设计 owner：`frontend/DESIGN.md`。
架构 / 策略 / 工程纪律仍只认 `docs/README.md` 三份 contracts。

站点：`frontend/app/`（多页静态站，无构建、无 npm）。
`start.command` 起后端后根路径重定向到 `/app/`。

改界面：先改 `DESIGN.md`，再改对应 `frontend/app/<space>/<tab>.html` 与共享 CSS/JS。
