# check_continuity_integrity.py 尾部收口 (2026-07-08)

## 背景

"把这些问题都解决了" 的 22 项 WARN 收口任务, 涵盖两类: (1) 3 张表 declared_drift 反复重报同一
已核实结论; (2) 12 域行数骤降 WARN, 需逐个判定是真数据缺口还是自然事件驱动高方差。

## 1. declared_drift 抑制机制

`check_declared_vs_actual()` 此前对 `drift > DECLARED_DRIFT_CAL_DAYS` 无条件报
`warn_declared_drift`, 即便 `sync_registry.yaml` 里已经有详细 `coverage_note` 说明这是源端
数据质量问题(非采集缺陷)且用户/前次 R4 workflow 已明确"不需要改动"——每次跑 gate 都重报同一件
已结案的事, WARN 队列该是"未核实"清单不该堆积噪音。

**修复**: 仿照既有 `gap_tolerance` 字段的解析模式, 新增 `data_start_reviewed: bool` 字段
(`load_domain_specs()` L91-95 同型解析), `check_declared_vs_actual()` 内 `data_start_reviewed=true`
时 declared_drift 分支降 pass(保留 detail 说明"已人工核实"); 同一现象的 sparse_history 分支也一并
降 pass(否则压掉 drift 后同一件事从另一分支重新冒出——两者是同一份 coverage_note 覆盖的同一现象)。

应用到 3 张已有 coverage_note 的表: `balancesheet` / `fina_indicator` / `stk_holdernumber`。
全量跑 WARN 数 22→19。2 个 red-green 单测(`test_declared_drift_reviewed_flag_suppresses_warn_red_green`
/ `test_declared_drift_reviewed_also_suppresses_sparse_history_relabel`)。

## 2. 12 域行数骤降逐个核实 (measured, 非估算)

对每个域用**真实 tushare API 调用**(非静态读代码/信任旧结论)逐日核对 vendor 与本地行数, 且严格按
该域声明的 `grain` 做去重比较(不是裸行数比对——踩过的坑见下)。

### 真缺口 (4 个, 已回填修复)

| 域 | 日期 | 缺口 | 根因 |
|---|---|---|---|
| margin | 20260703 | 本地仅 SSE 1行, vendor 实为 SSE+BSE+SZSE 3行 | 此前 `known_group_gaps` 把 07-05 复核时"vendor 当时确实只回 SSE"的观察当永久墓碑, 但 3 天后源端已补发 BSE/SZSE(真披露滞后非永久缺失)——墓碑随时间推移变成过期的假声明, 已撤销该条目 + 回填 |
| margin_detail | 20260703 | 本地 1652 行 vs 应有 3472(vendor 原始4374行经 universe_filter 后) | 52% 静默截断, `min_rows_per_batch: 800` 阈值远低于实际量级(~3472), 无法拦截"非零但仍严重不完整"的批次——continuity_integrity 的动态中位数骤降检测是此类问题的正确兜底层, drain 的静态阈值天然测不到 |
| stk_surv | 20260701/20260703 | 本地 229/74 vs 应有 248/188 | 同型披露滞后, allow_empty_batch=true 域 drain 走 watermark 增量不做历史 gap 扫描, 只有本次逐日 vendor 核对才抓到 |
| report_rc | 20260611/0612/0618/0702/0706 | 本地各偏低 (90 vs 197 等) | 同型, 21 日"骤降窗口"里混了 5 个真缺口 + 16 个真自然递减(研报季节性回落), 不能因为"整体看像自然衰减"就放弃逐日核实 |

### 假警报 (2 个, 方法论教训)

`dividend`(20260622等4日)、`block_trade`(20260521等2日): 裸行数比对显示本地约为 vendor 的一半,
但按域声明的 `grain` (dividend=`[ts_code,end_date,div_proc]`, block_trade=`[ts_code,trade_date,
price,vol,buyer,seller]`) 去重后 vendor 与本地**完全一致 0 缺失**——vendor 原始响应本身对同一
grain key 返回重复行(dividend 同一分红方案下 `ann_date` 不同的两次公告；block_trade 未知原因的
镜像行), 项目自己的写入管线早已正确按 grain 去重, 只是我最初核对时用了裸行数(未按 grain 去重)
才产生"缺一半"的假象。**教训(与 [[feedback-verify-content-not-just-row-count]] 同族)**: 核对
vendor-vs-本地完整性必须按该域声明的 grain 做去重比较, 不能裸数行数——这是本次审计里第二次踩到
"聚合数字骗人, 必须下钻到实际内容"这一类盲点的具体变体。

### 确认为真·自然高方差 (6 个, 域级 `gap_tolerance: annotate`)

`limit_list_d` / `suspend_d` / `margin`(残留3日) / `kpl_list` / `report_rc`(残留16日) /
`block_trade`(残留5日): 逐日/逐样本 grain-aware 核对 vendor 后确认本地数据完全正确, 骤降是
涨跌停/停牌/大宗交易笔数这类事件本身随市场情绪日日高方差的真实反映, 非数据缺陷。复用既有
`gap_tolerance` 字段(此前只喂给 `check_calendar_gaps`), `check_cross_section()` 的 row_dip
分支新增同一字段判断降 pass——两个检测面本是同一份人工判断("此域天然事件驱动稀疏/高方差"),
不该因为检测切面不同就要求重复审核。

### 已有真·历史异常, 随时间自然滚出窗口 (1 个, 无需动作)

`cyq_perf` 20260615 单日源端仅回 1 行(前后日 5190+ 行), 2026-07-05 R4 workflow 已确认是真实
源端异常且已回填(无法追溯修正那一天源端本身的行为)。该日期已随 60 交易日滚动窗口自然滚出
当前检测范围, 目前无需 `known_empty_days` 墓碑; `check_cross_section()` 已新增
`known_empty_days` 排除逻辑(与 red-green 单测同批加入), 该日期若未来重新落入窗口边界前提前
补墓碑即可。

### 顺带收口的历史遗留 TODO

`block_trade` registry 注释里 2026-07-05 遗留的"20250918 本地179行远低于1001行, 疑似另有历史
采集问题, 待独立跟进"——本次复核确认 179 行 = 1001 原始行套用 universe_filter 白名单过滤后的
正确结果(vendor 实测 grain-aware 去重后同为 179, 0 缺失), 并非遗留 bug, 已在注释中收口。

## 代码改动

- `backend/scripts/check_continuity_integrity.py`: `load_domain_specs()` 新增
  `data_start_reviewed` 字段解析; `check_declared_vs_actual()` 按该字段抑制 declared_drift+
  sparse_history; `check_cross_section()` 新增 `known_empty_days` 排除 + `gap_tolerance`
  抑制 row_dip。
- `backend/tests/scripts/test_check_continuity_integrity.py`: 新增 4 个 red-green 单测。
- `backend/config/sync_registry.yaml`: 3 表加 `data_start_reviewed: true`; 7 域加
  `gap_tolerance: annotate`(limit_list_d/suspend_d/margin/kpl_list/report_rc/block_trade,
  加上已有的 stk_surv/dividend/forecast/share_float 共 11 域); margin 撤销 20260703 的过期
  `known_group_gaps` 墓碑; block_trade 收口历史 TODO 注释。
- 数据侧: `margin`/`margin_detail`/`stk_surv`/`report_rc` 共 9 个交易日的真实回填
  (`sync_runner --domain X --start/--end`)。

## 验证

`check_continuity_integrity.py` 全量: WARN 22→7(剩余全部是既有、有意保留的 `gap_tolerance:
annotate` 型 calendar_gaps informational WARN, 非本次任务目标, 设计上就该保持可见但非阻塞)。
单测 27/27 passed(含 4 个新增 red-green)。
