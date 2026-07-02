# ChunkyMonkey edge 前端 v1

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

## 页面

| 路由 | 内容 |
|---|---|
| `/#/institutions` | C1 机构档案: 排名表 (服务端 order_by 排序, low_sample 灰显) + 建仓信号流 (入池跟随) |
| `/#/institutions/:holder` | 档案详情: KPI 卡 + 维度热力图 (行业/年份/类型 × 超额中位/胜率/样本数) + episode 时间线 |
| `/#/paper` | C2 实盘模拟: 组合概览 + nav 曲线 vs HS300 + 持仓表(平仓) + 手动入池 + 更新数据(mark) |
| `/#/workbench` `/#/market` | 占位 |

## 结构约定

- **widget 独立小功能原则**: 每卡片独立 useFetch (独立 loading / 失败态 / 空态), 一个卡片挂了不拖垮页面; 动作 (入池/平仓/mark) 后经事件总线广播 `paper` topic, 相关卡片各自重取。
- TS interface 真相源 = 后端真实返回: GET 端点 2026-07-02 经 TestClient 实测 JSON; POST/DELETE 返回结构读 `backend/routers/paper_portfolio.py` + `backend/services/paper_portfolio.py` (未实弹)。详见 `src/api/types.ts` 头注。
- 涨跌配色 A 股约定: 红涨绿跌。

## 已知缺口

- 持仓表 open 仓无 "现价/浮盈" — 后端无逐股现价端点 (`GET /paper/portfolio` 仅返回成本), 组合级市值走 nav 快照; closed 仓盈亏为毛盈亏 (positions 返回不含 entry_fee/exit_fee)。
- 后端未启动时各卡片显示独立失败态 (`后端不可达`) + 重试按钮, 页面骨架不崩。
