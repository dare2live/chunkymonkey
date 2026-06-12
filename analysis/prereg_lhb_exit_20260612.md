# 预注册冻结 — LHB 上榜即退出 (C组C1 主判决)

> 状态: **FROZEN 2026-06-12** (跑前冻结; 修改判据 = 挪门柱, 必须带理由 commit 且禁止在看到结果后)
> judge 残篇结构性条款 (镜像论/混淆臂强制/逐年同号/top_inst 臂可选) 见 alpha_combo_matrix_20260612.md;
> 数值线由总指挥按成本与风险锚立法。

## 创世层

- 为什么存在: 判决"龙虎榜上榜 = 出货标记"能否改善持仓退出质量 — LHB 入场已被证伪
  (lagging), 镜像方向 (出货识别恰是 LHB 强项) 从未有人跑过 (hologram 盲点4 补法点名)。
- 死线: (1) **混淆臂强制**: 不做同日同涨幅+市值桶匹配对照就出的任何结论全废
  (judge 原文: 否则把涨停均值回归当 LHB 效应); (2) PIT: 榜单 t 盘后公布, 行为最早 t+1 open;
  (3) 判负按处置条款除名归档, 不复跑不放宽。

## 假设 (单一, 可证伪)

持仓股在 t 日登上龙虎榜后, t+1 开盘退出, 相对"继续持有 20 个交易日"能显著减少后续回撤/
负收益 — 且该效应在扣除"高涨幅自身的均值回归"(混淆臂) 后仍然存在。

## 冻结定义 (判断法典, 人话+机器话)

| 条款 | 人话 | 机器话 |
|---|---|---|
| 上榜事件 | 个股出现在当日 top_list (任意 reason) | `event := raw_tushare_top_list[trade_date=t].ts_code` (按 ts_code 去重, 多 reason 算一次) |
| 退出反事实 | 上榜组: t+1 open 卖 vs 持有至 t+21 open 卖 的收益差 (留住的损失/收益) | `exit_gain = -(qfq_open[t+21]/qfq_open[t+1] - 1)` 正值 = 退出占优; 成本不计 (两方案同次数交易, 仅时点差) |
| 混淆臂对照 | 同日、日涨幅 ±1pp 带、流通市值同五分位桶、**未上榜**的股票, 同口径 exit_gain | float_values 五分位 + pct_change 带匹配, 每事件股抽 3 对照 |
| 净效应 | 上榜组 exit_gain − 对照组 exit_gain (扣除均值回归后的 LHB 残余) | `net = mean(exit_gain_event) - mean(exit_gain_control)` |
| 实验窗 | 2020-01-02..2026-05-29 (gate 解锁段 + 既有段, 含牛/熊/震荡完整周期) | top_list min<=20200101 由 sherpa gates lhb_exit G2 把守 |
| top_inst 臂 | 机构席位方向分桶 = 可选副表, **主判决不等它** | top_inst 转正后补跑, 不进三判官 |

## 修订 1 (2026-06-13, 跑前冻结 — 数据落地后实验前, 判据 J1-J3 未动)

实测: 实验窗 float_values null 率 4.3-7.8%/年 (2023+ 段仅 1.9%, 原 G4 按彼标定失真)。
**null-float 事件处置 (冻结)**: 混淆臂需要市值桶, float_values 为 NULL 的上榜事件
**从主判决样本中排除** (保持混淆臂匹配完整性), 排除计数必须在判决表披露;
附表报告被排除事件的 raw exit_gain 仅作敏感性披露, 不进 J1-J3。

## 三判官 (全部满足 = GO; 任一不满足 = NO-GO)

```yaml
# prereg_lhb_exit verdict constants — 实验脚本常量必须与本块逐字一致 (验收项)
J1_net_exit_gain:
  rule: "净效应 net >= +1.0pp (20 日窗) 且 bootstrap 95% CI 下界 > 0"
  threshold_pp: 1.0      # 锚 = 持仓单次 20 日窗的风险改善须 >= ~4x 双边成本量级才值得改写退出逻辑, 非数据反推
J2_yearly_sign:
  rule: "按自然年 2020..2025 (6 年) + 2026YTD, net 同号为正 >= 5/7 年 (逐年同号, judge 原文条款)"
J3_confound_dominance:
  rule: "上榜组 exit_gain 均值 > 对照组 exit_gain 均值的 1.5 倍 — LHB 信息须显著强于纯均值回归, 否则信号本质是'追高了就该走'而非'上榜了该走'"
```

## 死亡条款 (实验自身)

- 感知死: 判决 7 天内未入 ledger = 作废重跑。
- 判断死: G2/G3 数据 gate 未 PASS 强行开跑 = 判决无效 (sherpa gates lhb_exit 是硬门)。
- 谄媚死: 看到结果后讨论"换 10 日窗/换年份切法就显著了" = 触发本条; 窗口与切法已冻结。

## 判负处置 (预注册)

LHB 上榜从退出信号候选除名; 降级归宿 = lmt_market_temp 情绪温度计输入 (regime 层);
C组C2 (LHB 冷却期二审) 取消 (串行 gate 原文: 仅当 C-C1 显示非零信息才开跑);
hm_detail/hm_list 注册取消 (0 call)。

## 七问对账

为什么存在/死线 = 创世层 | 目标 = 三判官判决表 + 逐年分解表 | 拍板 = 用户 (判正后是否
进 B 主书退出组件 ablation) | 环境 = 本地 SQL | 什么算好 = yaml 块 | 预算 = 本地 CPU
小时级 + 0 增量 API + 总指挥注意力 ~3h | 缺口 = top_inst 臂数据 (可选, 不阻塞)

## 开跑前置 (机器可检)

`sherpa gates --repo . lhb_exit` 全 PASS (G1 日历地板 / G2 top_list 2020-2022 728 日 /
G3 daily+adj_factor 同段 / G4 字段质量) — 当前 G2/G3 等 chain9 回填, 完成后复检。
