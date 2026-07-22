# 沪深A 白名单含 ST（owner clarification 2026-07-22）

> Status: evidence-only
> Label: **FIXED**（population denylist 误伤 ST）
> Peer: `foundation_acquire_all_due_unblock_20260722.md` semantic guard（勿 revert）

## 白话裁决

| 概念 | 正确语义 |
|---|---|
| 产品/感知 serve 白名单 | **沪深A = 60/00/30/68，含 ST/*ST** |
| 排除 | 新三板/老三板、退市（观察日无名义 K）、**B股**、**北交所 BJ** — **不是** ST 标签 |
| `stock_st` 域 | **ST membership 证据**（谁在何时是 ST）| 与 universe 成员资格正交 |
| `stock_st` `zero_rows` / `pending_publish` | **域发布窗 / publish timing** | 不得误读为「白名单不要 ST」 |

## 证伪（改前）

- Serve 前缀过滤（`universe_serve_filter`）**本来就不踢 ST** —— 只看 board prefix。
- 正式人口 `resolve_traded_on_observation_date` **错误**地把 accepted `stock_st` 成员从 `ts_codes` **剔除**（`excluded_st_count`）。
- Legacy `get_active_universe(include_st=False)` 默认也按名称踢 ST。
- `universe_rules.yaml` / MASTER §5.1 文言写「∩ ¬ST」——与产品意图冲突。

## 改后契约

- Population = 开市 ∩ 名义K ∩ board 白名单；**ST 留在池内**；`st_member_count` = 池内 ST 计数（证据，非剔除）。
- 仍加载 accepted `stock_st`（PIT 证据 / readiness）；零行仍 fail-closed = **证据不可用**，不是 denylist。
- `get_active_universe` 默认 `include_st=True`；策略可显式 `False` 收窄。
- Policy version **3 → 4**。

## 与 acquire `--all-due` 的边界

`foundation_acquire_all_due_unblock_20260722.md` 解的是编排形状（drain 先于 formal；sibling 不绑架）。  
本刀解的是 **白名单语义**：勿把 `stock_st` 失败当成「踢 ST」。两刀互补，勿互相 revert。

## Owner docs

- `docs/MASTER_TOPLEVEL_DESIGN.md` §5.1
- `backend/config/universe_rules.yaml`
- `goal.md` Formal daily/ST 段
