# ChunkyMonkey 现有观察前端

本目录是过渡期 read/observation surface，不是 Tier4 决策产品。架构、策略发布和数据契约
以 `docs/README.md` 指向的 owner 文档为准。

React 18 + Vite + TypeScript, 无重 UI 框架 (手写单文件深色样式), 图表唯一依赖 echarts。

## 启动

```bash
# 后端 (端口真相源 = backend/main.py: CM_PORT env, 默认 8000)
cd backend && ../.venv/bin/uvicorn main:app --port 8000   # 或项目既有启动方式

# 前端
cd frontend
npm install
npm run dev        # vite dev server, /api 代理到 http://127.0.0.1:${CM_PORT:-8000}
npm run build      # tsc --noEmit + vite build → dist/
```

**注意 (2026-07-04)**: `vite.config.ts` 的 `base: "/app/"` (为生产 build 挂载 FastAPI `/app` 路径而设)
同时影响 `npm run dev` — dev server 首页在 `http://localhost:5173/app/`, 访问根路径 `http://localhost:5173/`
会一直卡 "Awaiting server..." (资源路径带 /app/ 前缀找不到)。开发时务必带 `/app/` 前缀访问。

## 页面

| 路由 | 内容 |
|---|---|
| `/#/institutions` | Tier3 机构披露研究档案 + 披露事件流；不产生 CandidateSignal |
| `/#/institutions/:holder` | 档案详情: KPI 卡 + 维度热力图 (行业/年份/类型 × 超额中位/胜率/样本数) + episode 时间线 |
| `/#/paper` | Legacy NONCONFORMING 手工观察账本；qfq-close 估值，不是订单/成交模拟 |
| `/#/market` | Tier2 市场感知（DC/SW namespace 与资金口径分开） |
| `/#/workbench` | 占位，未实现 |

## 结构约定

- 每卡片独立 useFetch；观察账本动作后经事件总线广播 `paper` topic。
- TS interface 真相源 = 后端真实返回: GET 端点 2026-07-02 经 TestClient 实测 JSON; POST/DELETE 返回结构读 `backend/routers/paper_portfolio.py` + `backend/services/paper_portfolio.py` (未实弹)。详见 `src/api/types.ts` 头注。
- 涨跌配色 A 股约定: 红涨绿跌。

## 已知缺口

- 观察账本缺订单/成交、T+1、停牌、涨跌停与名义可成交价；任何 NAV/收益只是研究观察值。
- 后端未启动时各卡片显示独立失败态 (`后端不可达`) + 重试按钮, 页面骨架不崩。
