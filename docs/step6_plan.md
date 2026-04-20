# Step 6 · 信号整合到股票视图（SignalAdapter 架构）

> 2026-04-20 规划 · 落地 C6c 已完成（SignalAdapter 适配层）
> 后续 C6d-g + C7 待推进

---

## 1. 核心设计原则

> **信号是股票的属性，不是独立入口**。HANDOFF 说"主角是机构，股票是机构行为的载体"，但用户每天的决策对象是**挑股票**——信号/机构是股票的属性，不是平级 tab。

最终导航（C6g 完成后）：
- **股票**（主入口，列表 + 抽屉）
- **机构**（track record 列表 + 抽屉）
- **工作台**（运维 + 参数调试 + 系统健康验证）

信号 tab 下线，所有信号数据通过 SignalAdapter 透传到股票视图。

---

## 2. 架构：3 层分离

```
  ┌────────────────────────────────────────────────┐
  │  UI 层（股票列表 / 抽屉 / 机构视图 / 工作台）    │
  │  只消费标准化展示对象，不认识后端字段路径        │
  └────────────────────────────────────────────────┘
                         ↑
  ┌────────────────────────────────────────────────┐
  │  适配层 · SignalAdapter（assets/js/signal-adapter.js）│
  │  标准化转换 + 事件总线 + 参数历史快照              │
  └────────────────────────────────────────────────┘
                         ↑
  ┌────────────────────────────────────────────────┐
  │  数据层 · signals_v2 后端 API                    │
  │  /today /cohort /config /backtest /events/stats  │
  └────────────────────────────────────────────────┘
```

### 适配层职责
1. 封装所有 `/api/signals/*` 调用（UI 不直接 fetch）
2. `eventToView(raw)` 把后端字段映射为 `{id, stockCode, action, longEV, ruleChecks[], ...}`；**后端加字段不影响 UI**
3. `aggregateByStock(events)` 按股票分组（follow 优先排序）
4. 事件总线 `on / emit`；`updateConfig()` 会 emit `config:changed` 让订阅者刷新
5. 参数变更自动存 `localStorage.cm_config_history`（最近 20 次）

---

## 3. 3 个未来场景（0 前端改动即可扩展）

### 场景 A · 用户调参数（max_premium_pct 15 → 10）
```
用户在工作台保存参数
  → SignalAdapter.updateConfig() 写后端 + emit 'config:changed'
  → 订阅者并行响应：
    · 股票列表 fetchSignals() 重渲染（某些股票从可跟变不跟）
    · cohort 卡 fetchCohort() 重渲染数字 + 季度分布
    · 当前打开的股票抽屉 自动重渲染 ruleChecks
```
**前端 0 改动**。

### 场景 B · 后端加 D9「机构席位集中度」硬规则
```
修改：
  · signals_v2._apply_hard_rules  加检查逻辑
  · signals_v2._build_rule_breakdown.checks[]  新加 D9 对象
  · DEFAULT_CONFIG  加 key (例 max_inst_seat_concentration)
  重启后端 = 完成。

前端：
  · 列表：不展示细维度（稳定 7 列）
  · 股票抽屉"信号证据链"Tab：遍历 `ruleChecks[]` 自动渲染 D9 方块
  · 工作台参数面板：从 /api/signals/config 返回的 keys 动态渲染（已是这样）
```
**前端 0 改动**。

### 场景 C · 对比历史参数评分
```
SignalAdapter.getConfigHistory() 返回最近 20 次 {ts, before, patch}
工作台「参数历史」折叠区展示时间轴：
  每次参数变更对应当时的 cohort follow 数 / edge
  支持一键回滚（POST config 原 before 对象）
```

---

## 4. UI 设计契约

### 股票列表（稳定不变）· 7 列
```
┌─ 股票 ─┬─ 当期信号 ─┬─ 共识 ──┬─ 长期 EV ─┬─ 溢价 ─┬─ 最近事件 ─┬─ ★ ─┐
│代码名称│badge大color│机构/事件│ 最佳值+样本│ 平均   │ 日期距今  │ 自选│
│行业    │            │共识条   │            │        │           │ 详情│
└────────┴────────────┴─────────┴────────────┴────────┴───────────┴─────┘
```

- **不展示 D1-D8 分列**（空间有限 + 维度会变）
- 点行展开右侧抽屉

### 股票抽屉 · 3 Tab（灵活适应维度变化）
```
[股票代码 名称 · 行业]                              [☆ 自选] [× 关闭]
─────────────────────────────────────────────────
KPIs: 总机构 · 共识 · 平均溢价 · 最佳 EV · 最近事件
─────────────────────────────────────────────────
Tab: 机构持仓 | 事件时间线 | 信号证据链

● 机构持仓 —  /api/inst/profiles/detail/{inst_id} 跨调
● 事件时间线 — adapter.fetchSignals 过滤该 stock_code
● 信号证据链 — 遍历 event.ruleChecks[] 自动渲染（数据驱动）
```

### 机构视图（不大改，但统一）
- 列表保持 C5 的 56px 行高
- 抽屉沿用 signals-view 的 institution_track_record 组件（C6f 拆出单独 widget）

### 工作台 · 新增 3 个折叠区
位于已有「数据管线 / 审计 / 批量管理 / 模块开关 / Qlib / 自选 / 排除 / 重算」之间：
- **信号参数**（16 键 app_settings 动态渲染 + 保存即 emit config:changed）
- **Cohort 反馈闭环**（当前 signals-view 的 cohort 卡 + 季度分布胶囊）
- **历史回测**（signals-view 的 backtest 面板，按需手动触发）
- **参数历史**（读 localStorage.cm_config_history 时间轴）
- **参数快照回滚**（一键恢复某次历史参数 + 警告）

---

## 5. 实施清单（剩余 C6d-g + C7）

### C6d · 股票视图重建（80 min）
- `index.html` 加回 `<section id="view-stocks">`；导航改 4 tab（信号/机构/股票/工作台，临时）
- 新建 `assets/js/stock-view.js`
  - `window.StockView = { load, reload }`
  - 订阅 `SignalAdapter.on('config:changed', reload)`
  - 列表 7 列，渲染 `SignalAdapter.fetchSignals` 的 byStock 结果
  - 胶囊筛选：动作 / 行业 / 机构类型 / 机构数 / 仅看自选
- `app.js` showView dispatcher 映射 stocks → StockView.load

### C6e · 股票抽屉 3 Tab（80 min）
在 stock-view.js 内实现（或单独 `stock-drawer.js`）
- 抽屉结构：头部 KPIs + Tab 切换
- Tab1 机构持仓：复用 `/api/inst/profiles/detail/{id}` 格式，但按 stock_code 过滤
- Tab2 事件时间线：`adapter.fetchSignals()` 结果按 stock_code 过滤 + 按 notice_date 排序
- Tab3 信号证据链：遍历最近 follow/watch 事件的 `ruleChecks` 数组，用 C3 的 `.chip-tint-*` 渲染

### C6f · signals-view.js 拆分为 3 个工作台 widget（60 min）
- 拆 `assets/js/widgets/signal-params.js`（~150 行）
- 拆 `assets/js/widgets/cohort-card.js`（~80 行）
- 拆 `assets/js/widgets/backtest-panel.js`（~100 行）
- 每个 widget 订阅 adapter + 暴露 render 方法给工作台按需调用

### C6g · 删 signals-view.js 剩余 + 信号 tab 下线（40 min）
- `index.html` 删 `<section id="view-signals-v2">`
- 导航 4 tab → **3 tab**：股票 / 机构 / 工作台
- 默认入口改股票（`showView('stocks')`）
- app.js / signals-view.js 残余删除
- `CM_ASSET_VERSION` bump 3.0.0（大版本架构迭代）

### C7 · 收尾（30 min）
- 抽屉样式统一（机构抽屉 + 股票抽屉共用同一组 CSS class）
- 参数历史折叠区实现（读 localStorage）
- legacy `.btn-*` CSS 清理（`.chip` 已全面接管）
- preview 全站验证

**总预估 ~4.5 小时**。

---

## 6. 验证清单（每个 commit 做一次）

- [ ] 参数保存后股票列表自动刷新（不手动 F5）
- [ ] 参数保存后 cohort 卡自动刷新
- [ ] 股票抽屉"信号证据链" Tab 展示硬规则体检（7 维点灯）
- [ ] 假装后端加一个 D9 维度（测试：`ruleChecks` 多一项，前端自动多一个方块）
- [ ] `localStorage.cm_config_history` 有记录且可读
- [ ] 3 tab 导航干净，信号 tab 彻底下线
- [ ] 所有按钮都是 `.chip` 样式，无 legacy `.btn-*`
- [ ] 日志面板白底，等宽字体
- [ ] 列表行高 56px 统一

---

## 7. 风险 / 回滚

每个 commit 独立推 main。若 C6d-g 任一步出问题：
- `git revert <commit>` 回到上一步
- 最坏情况回到 C6c（adapter 已就位但 UI 未改），等同于当前状态

worktree 清理建议：C6g 完成后可以删除 `sharp-hermann-b28418` worktree。

---

## 8. 与 HANDOFF 的关系

HANDOFF.md 保留为"长期交接文档"（用户偏好 / 架构 / 用户强调的原则）。
本文档 `step6_plan.md` 只记录 Step 6 这一次大重构的计划和实施清单，C7 完成后可以归并进 HANDOFF 的"最近 commits"章节然后删除本文件。

—— 完
