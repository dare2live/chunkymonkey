# Codex Round 28 — 中国量化社区顶级策略借鉴

Source: agent a7289d129b04c68a8, 2026-05-17.

## 10 顶级 A 股策略类型 + 我们覆盖

| 策略 | 社区年化 | 我们已有? | gap |
|---|---|---|---|
| 小市值轮动 | 40-130% | 部分 (mc_decile) | 涨停过滤 + 弱转强 待补 |
| 多因子 ML 排序 | 12-25% | ✓ 已是主路径 | LightGBM walk-forward |
| 低波/交易摩擦 | 8-20% | 部分 (vol features) | 标准化 turnover/换手波动 待 |
| 短/中反转 | 10-25% | ✓ (ret_20d 等) | residual reversal 待补 |
| **PEAD/业绩超预期** | **15-20%** | 部分 | **SUE 严格 PIT 待建** |
| **资金流: 主力+两融+北向** | 10-25% | 部分 (LHB) | 主力/两融/北向 待补 |
| 行业/ETF 轮动 | 8-25% | ✓ (sector) | regime trigger 待补 |
| 高股息/红利质量 | 8-20% | 部分 (estimated_z) | OCF/ROE 显式 |
| **负面事件过滤** | risk control | 缺 | **解禁/质押/户数 hard filter** |

## Top 20 共识因子 vs 我们已有 (本表关键 borrow 点)

| 因子 | 我们已有 | 借鉴 |
|---|---|---|
| Size (流通市值) | ✓ mc_decile | ok |
| 20D 反转 | ✓ alpha158 ret | ok |
| 残差反转 | 部分 | 补行业内中性化 |
| **SUE / 业绩超预期 / 盈利预测上修** | 部分 (yjyg snapshot) | **P0 严格 PIT 化** |
| **北向持股变化** | 缺 | P0 (注意 2024-08 后口径变化) |
| **主力资金净流入** | 缺 | P0 (akshare stock_individual_fund_flow) |
| 两融余额变化 | 暂停 (rz_balance research gap) | 待 |
| 解禁比例/ADV | 部分 (max_unlock_ratio_180d 痕迹) | **P0 hard filter** |
| 股权质押比例 | 缺 | P0 (akshare stock_gpzy_pledge_ratio_em) |
| 股东户数 | 部分 (cf_holder_concentration) | 完善公告日 PIT |
| 调研热度 | ✓ | ok |

## 5 个最高 ROI 借鉴 (task #77-79)

| # | 借鉴 | 预期 | task |
|---|---|---|---|
| 1 | SUE/业绩预告/盈利预测上修 PIT | 年化 +5-12pp | #77 |
| 2 | 资金流三件套 主力+两融+北向 | 年化 +3-10pp | 待加 task |
| 3 | 解禁/质押/户数 负面 filter | max_dd -3 to -8pp | #78 |
| 4 | Regime-aware cash/ETF 防守 | max_dd 向 -20% | #79 |
| 5 | 执行模型 升级 (VWAP + ADV 冲击) | 减假 alpha 关键 | 待加 task |

## 实盘 vs 回测 gap 主因 (我们 paper_sim 已 handle 大部)

| 主因 | 我们 handle 状态 |
|---|---|
| 未来函数 report_date 替代公告日 | ✓ (PIT enforce) |
| 最新复权泄漏 | ✓ (PIT 复权) |
| 幸存者偏差 | ✓ (KEEP universe + dim_all_ever_listed) |
| 涨跌停/停牌不可成交 | ✓ (Codex C-C mask) |
| 开盘价成交 | 部分 (用 VWAP, 但 8s 延迟未模拟) |
| 买卖价差 | 部分 (TX cost model 含) |
| T+1 无法日内 | ✓ (T+1 entry) |
| 数据商口径变化 | ⚠ (sync gap 当前问题) |
| 过拟合 Optuna | ✓ (walk-forward + Deflated SR) |
| 费用低估 | ✓ (tx_cost yaml 详细) |

主要 gap: 数据 sync 当前问题 + 开盘价 8s 延迟模拟未做.

## 仓位管理 / sizing 社区共识

我们应做 (按优先):
1. **Rank Tilt + vol haircut + 单票 25% cap + 15-30% cash + 行业 cap** (Codex 推荐 next step)
2. **不直接上 Kelly** (小样本不稳)
3. **动态持仓数** (市场弱 5→2-3 或空仓)

我们已实施: score_rank_diff_v1 sizer (类似 Rank Tilt). 差行业 cap + 动态持仓数.

## 风险控制 best practices (P0 立即应用)

1. **次新过滤** (上市 < 120 天)
2. **解禁黑名单** (60/180 天未来解禁市值/ADV 高)
3. **质押高风险** (质押比例 + 控股股东质押)
4. **市场状态空仓** (HS300/ZZ1000 跌破 MA + 宽度恶化)
5. **组合 DD hard stop -20%** (已有)
6. **hard stop 冻结期 10-20 天** (已有)
7. **ETF/货币 ETF 防守** (511880/债券/黄金) — 新加
8. **单票 25% / 行业 40% cap** (部分)
9. **ADV 容量限制** (单笔 < ADV20 × 1-3%)
