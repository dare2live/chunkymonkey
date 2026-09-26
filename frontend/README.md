# ChunkyMonkey 前端

只读观察面：展示数据底座与研究、档案的已发布结果，不给买卖结论。

设计 owner：`frontend/DESIGN.md`。
架构与工程纪律只认项目根 `../CLAUDE.md` 与 `../goal.md`。

站点：`frontend/app/`（多页静态站，无构建、无 npm）。
`start.command` 起后端后根路径重定向到 `/app/`。

改界面：先改 `DESIGN.md`，再改对应 `frontend/app/<space>/<tab>.html` 与共享
`css/site.css`、`js/core.js`、`js/live.js`、`js/lab.js`（实验室现查）。
