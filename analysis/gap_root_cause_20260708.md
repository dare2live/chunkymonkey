# 数据缺口根因深查 (2026-07-08)

## 背景

用户在完整审计尾部22项WARN收口([continuity_gate_tail_closure_20260708.md](continuity_gate_tail_closure_20260708.md))后追问"这些缺口的根因都要找到并修复"——不满足于"回填了这几个日期",要求找出每个缺口
为什么会发生、并系统性修掉。8-agent workflow(4域并行深查 + 收敛综合 + 3角度对抗复核)先做了
第一轮诊断,随后我在实现阶段又抓到2个workflow本身没抓到的独立根因(margin`known_group_gaps`
墓碑过期 / report_rc-forecast-share_float 的 by_trade_date 结构性排除周末数据)。本文档是最终
收口记录。

## 4域(margin/margin_detail/stk_surv/report_rc)workflow 诊断结论

| 域 | 根因判定 | 证据 |
|---|---|---|
| **margin** | vendor 查询形态 bug: 裸调 `pro.margin(trade_date=d)` 无过滤条件时偶发(~0.5%交易日, 全2026年4-5例)漏返 BSE/SZSE, 显式按 `exchange_id` 三分片查询 100% 拿全 | 824个可比交易日仅4-5天中招; 2023-2025三年完全干净; 显式分片对历史"永久缺失"日重新实测均能拿全 |
| **margin_detail** | 单点瞬态异常(1/1090历史交易日), 非分页非披露滞后, `min_rows_per_batch:800` 阈值远低于真实基线(~3472)测不出腰斩 | 12个历史抽样日逐日精确吻合(0例), 全历史1090天中位数扫描仅1天异常 |
| **stk_surv** | **系统性 page_limit 截断**(本轮最严重): 单次调用硬顶400行, 无论 limit 传多大服务端都截在400, 业务活跃日丢22%~87% | 7个历史高峰日100%命中截断(offset分页复测证实); 实测硬顶(limit=100/200正确响应, limit>=500均截400) |
| **report_rc** | 常态性小幅背景噪音(vendor侧持续低幅修订, 数月不收敛)+ 独立的**大规模结构性缺口**(见下) | 12个历史抽样10个有<7%小额差(正常噪音), 1个抽样(20260328)100%缺失揭示更大问题 |

## 2个 workflow 未抓到、实现阶段深挖出的独立根因

### 根因5: margin 的 `known_group_gaps` 墓碑本身"过期"

2026-07-05 R4 workflow 逐日实测 vendor 确认 20260403/0430/0618/0626/0703 五天"源端当天确实只回
SSE"并写入 `known_group_gaps` 墓碑(声明为永久性缺失,豁免检测)。3天后(2026-07-08)复核同一个
20260703,vendor 已经补发了 BSE+SZSE——**真相是披露滞后,不是永久缺失**,当时的墓碑判断本身
就是错的。教训:**任何"实测某时刻源端确实没有"的墓碑都只是那一刻的快照,不是永久真相**,尤其
对有滞后可能的域,墓碑要定期复核,不能一次实测就当终身结论。这次修复:margin 改用 `split_by`
机制(见下)后不再需要任何墓碑——查询形态本身修对了,不用"记住哪天不行"。

### 根因6: report_rc / forecast / share_float 的 `by_trade_date` 结构性排除周末数据 (本轮最大发现)

三域的 `date_param` 分别是 `report_date`(研报发布日)/ `ann_date`(公告日)—— 都是**真实日历
概念**,分析师/上市公司完全可能在周六周日发布。但三域的 `batch_mode` 声明是 `by_trade_date`,
只在**交易日历**上枚举日期去查询——这是纯粹的枚举范围错配(config 声明与字段语义不匹配),
不是"周末没有交易"这件事本身(那是废话),而是"域配置用了错的批次策略"这个真 bug。

**实测证据**(直接调用 vendor API, 非估算):

| 域 | 采样周六/周日 | 实测行数 |
|---|---|---|
| report_rc | 20260328(周六) | 602 行(本地此前 0 行, 100%缺失) |
| report_rc | 20260321(周六)/20260322(周日) | 193 / 387 行 |
| forecast | 20260321/20250315/20240622(周六) | 1 / 1 / 4 行 |
| share_float | 20260321/20250315/20240622(周六) | 12 / 21 / 102 行 |
| dividend | 20260321/20260322/20250315/20240622 | 全部 0 行(验证无影响, 见下) |

由于 `by_trade_date` 从建域(2019, forecast 2008)起就从未在批次枚举里出现过周末日期,这不是
"偶尔漏一天"的缺口,是**结构性地把约 2/7 的日历时间永久排除在采集范围外**,持续了 7+ 年
(forecast 甚至 18 年)。这比 stk_surv 的 page_limit 截断影响面更大(page_limit 只影响"活跃日
的部分行", 这个 bug 是"整整 2/7 的天从未被查询过")。

**修复**: 三域 `batch_mode` 从 `by_trade_date` 改为项目里已有的 `by_ann_date`(为 org_holding/qfii
等"公告日含周末"型域设计的全日历日枚举机制, `sync_runner.py::_calendar_days()` 早有注释"实测
18%公告落周末"——早就知道这类问题存在, 只是没类比到 report_rc/forecast/share_float 这三个同样
用"发布日历日"当 date_param 的域上)。`date_param` 沿用不变, `by_ann_date` 天然读取
`spec.get("date_param", "ann_date")`, 无需其余改动。

**`dividend`(date_param=ex_date)验证排除**: 除权除息日在交易所机制上就只设在交易日(价格调整
必须在有交易的那天生效), 实测4个周末样本全部 0 行, 确认此域不受影响, 保持 `by_trade_date` 不变
——**同一个 date_param 语义"是不是可能落在非交易日"必须逐域实测, 不能因为"这三个域都中招"就
假设第四个域也中招**, 一次性排除靠的是测出来的 0, 不是猜。

**用户拷问("周末也不是交易日啊")**: 用户当场指出我最初的描述把"by_trade_date 不含周末"(定义
本身如此, 非 bug)和"date_param 语义与 batch_mode 枚举范围不匹配"(真正的 bug)混为一谈——这是
应该澄清的表达问题, 已当场订正。用户又追问"研报全文吗"——确认 report_rc 只是结构化元数据
(标题/机构/分析师/评级/目标价/盈利预测数值), 无正文字段; 且当前 `services/pipeline/acquire.py`
对 report_rc 的唯一引用是一句"未来档B若需景气度可能用"的注释, **0 个活跃下游消费方**。据实
汇报后用户仍决定"继续抓取"——这次回填是为 raw 层完整镜像 vendor 事实做地基投入, 不是修复一个
正在运行的功能缺陷(诚实反映现状, 不夸大紧迫性)。

### 根因7: forecast 的 2026-07-05"归入 income 同类保留"判断本身是错的(用户二次拷问揪出)

用户看到 forecast 全历史回填要拉 2008-2026(18年)后追问"2008至今?为啥这么久,不是说过跟K线
的时间周期一样么"——精确点中 2026-07-05"K线边界结构性孤立数据审计"([r4_completion_20260704.md](r4_completion_20260704.md)
第162-201行)当时把 forecast 和 income 归为同一类"保留全历史"域的判断可能站不住脚。

**核实过程**: 用2-agent独立调查(互相不知道对方结论)+ 对抗综合的方式重新裁决(与2026-07-05
原审计同等严谨度)。**双方独立结论一致**: forecast 的 pit_anchor("ann_date...JOIN t-1 保守
(PEAD事件锚)")与 report_rc 的 pit_anchor("report_date...JOIN t-1")结构几乎逐字相同, 而
income 的保留理由明确是"财报事实自包含(非'对某日价格的反应'型数据)"——这个理由从未真正适用于
forecast(PEAD=Post-Earnings-Announcement-Drift, 定义就是"事件公告后价格如何反应", 本质与
report_rc同类, 与income不同类)。更关键的证据(调查者B用git历史考古挖出): forecast/report_rc
**曾是同一批次(commit 00cb09e2/2212a5cf, "档案L3④")引入的姐妹消费函数**
(`load_forecast()`/`load_analyst_reports()`, 同一套 `DataAccess.get(ann_date<=asof)` PIT
loader 模式), 后随2026-06-28纯数据平台重建**同一次一起被删除**——不是"forecast 从未被消费过"
(income那种真自包含), 是"2026-07-05这次审计漏查了历史消费代码, 单凭'当前0消费方'就套用了
income的类比"。

**结论**: forecast 应与 report_rc 同批处理——已物删 `ann_date < 20190102` 的历史数据, `data_start`
改为 `20190102`, 停止原计划的 2008 起点全历史回填, 改为从对齐后的 `20190102` 起（回填量级大幅
缩小, 与 report_rc/share_float 同规模而非多10年）。

**残留待办(不在本轮范围内, 已告知用户)**: 同一次调查发现 `express`(同型pit_anchor, 0消费方,
证据链比forecast弱一档, 未找到历史loader直接证据)和 `moneyflow_hsgt`(pit_anchor同结构但grain
是逐日流水非稀疏事件, 需独立核查) **也可能有同款问题**, 但两者不在本轮"这些缺口"的原始范围内
(margin/margin_detail/stk_surv/report_rc/forecast/share_float), 是这次连带查出的新发现——用户
随后明确要求"要修复", 追加第二轮独立裁决(见下)。

### 根因7 补记: express / moneyflow_hsgt 独立复核 — 结论是"不该套用", 不是"漏了没修"

用户要求处理后, 再次用 2-agent独立调查(互不知情)+对抗综合裁决 express/moneyflow_hsgt。
**这次两位独立调查者本身产生分歧**(A 判两域都该物删, B 判两域都不该), 综合裁决采纳了 B 的
更严谨论证, 结论是**两域现状均不改动**:

- **express — 搁置为 unknown, 不物删**: 证据链核实后发现比 forecast 更弱, 但弱的方向和"该不该
  删"无关而是"能不能判断"——`git log --all -S "raw_tushare_express"` 全历史只有一条注册 commit
  (从未存在过 `load_express()` 或任何消费函数), 而 forecast 有实锤的历史消费函数
  `load_forecast()`(证明JOIN语义曾真实运作过又被边界打断才有资格判"结构性孤立")。**关键教训**:
  "从未有过消费方"和"曾有真实消费又证实结构性对不上"是两种不同证据强度, 前者只能证明"暂无
  消费方"(unknown), 不能反推"结构性孤立"(物删依据)——A 一开始把"更彻底的0消费"包装成"更干净
  的删除理由"是倒因为果, 已被对抗复核推翻。
- **moneyflow_hsgt — 明确驳回物删, 维持 data_start=20141117**: 关键区分点(grain 语义, 非
  pit_anchor文字): report_rc/forecast 是**稀疏事件公告表**(只在事件发生那天有行, PIT设计要求
  按 ann_date 精确等值 JOIN 某一天K线, 早于K线边界的行**永久**匹配不到, 这是结构性缺陷)。
  moneyflow_hsgt 是 **grain=[trade_date] 的逐日全量流水表**(每交易日必有一行, 描述"当日北向
  净流入"这一独立完整事实)——2014-2018 这段是完整连续的历史时间序列, 不存在"某些行结构性匹配
  不到"的问题, 只是"暂无消费方"(同express同一类误判风险)。删除会主动销毁一段完整、未来K线
  历史窗口若回补后立刻可用的数据, 没有任何真实收益。

两域已在 `sync_registry.yaml` 加注释记录本次复核结论(防止未来单看 pit_anchor 文字相似性又
被重新套用同一个错误)。**这次对抗分歧本身就是价值所在**: 如果没有独立第二视角挑战, 很可能把
report_rc/forecast 的处置模式机械套到这两个域上, 误删掉本该保留的历史数据。

## 对抗复核发现(3角度, 均已采纳)

- **cost lens**: 提出方案(手工触发"最近N天重核"命令)必须先测调用量预算(不能拍N值), N应
  分域给(margin已确定性修复不需要N, report_rc长尾滞后N再大也补不全)。**采纳: 未实现该"重核"
  命令**(见下"明确排除"), 故此项不适用, 但方法论采纳。
- **completeness lens**(最关键发现): 这是同一根因(缺 page_limit 导致静默截断)第7-8次复现
  (dc_member/dc_index/top_inst/index_dailybasic/forecast/stk_limit/block_trade/stk_surv),
  **从未做过系统性横向扫描**; 且发现 `gap_tolerance` 被复用去抑制 row_dip 检测本身是个盲区
  (stk_surv 的截断 bug 差点被这个不相关的标签连带掩盖多年)。**采纳**: (1) 已拆分
  `gap_tolerance`/`row_dip_tolerance` 两个独立字段并加所有权契约测试(见下); (2) 已 spawn 一个
  独立任务(task_e215d934)对剩余21个未声明page_limit的by_trade_date域做系统性横向扫描, 不在
  本轮范围内一次做完(避免任务无限扩大, 但明确记录残余风险不是"已全部解决")。
- **constraint lens**: 原方案"diff不一致则自动触发drain重拉"被判定违反用户"现阶段只手工点击
  更新"的既定约束(侦测和写入合并在一次点击里自动连锁执行, 本质是包了壳的自动化)。**采纳:
  不实现该"重核"机制**, 本轮修复全部通过人工发起的一次性 `sync_runner --domain X --backfill`
  完成, 不引入任何新的自动/半自动重跑逻辑。

## 架构审查(用户要求"该重写就重写", architect-controller skill)

用户指出"打补丁不如重写"的担忧, 具体反例: `gap_tolerance` 字段被挪用去抑制 `row_dip`,
导致 stk_surv 的真实 bug 被连带掩盖。用架构师五问逐项核实(而非凭印象判断):

- **实际字段普查**: 47域基础字段(source/api/grain/batch_mode/data_start/pit_anchor/
  available_after/allow_empty_batch/universe_filter等12个)统一干净; 长尾13个窄字段
  (page_limit/gap_tolerance/row_dip_tolerance/known_empty_days/known_group_gaps/dead_groups/
  data_start_reviewed/cross_check_domain/split_by/freshness_group_col/code_param+code_list/
  increment_mode/freshness_no_probe)每个只用在1-11个域, 不构成大范围滥用。
- **跨文件读取核实**: 除 `check_continuity_integrity.py`/`sync_runner.py` 外还有第三个消费方
  `update_watermark_sla.py`(读 `freshness_no_probe`)和 `continuity_guard.py`(也读
  `gap_tolerance`, 与检测脚本共享同一"日历稀疏"判断, 合理共享非误用)。三个文件的字段交集只有
  `known_empty_days`(两边共享同一个"此日源端真空"的客观事实, 合理), **除今天这一例外没有
  发现第二处跨边界误用**。
- **裁决: 窄范围 REVISE, 非全部重写**——不把13个字段重构成嵌套结构(要动全部47域YAML+所有
  解析代码+40个现有测试, 对"目前只复发一次"的问题是过度工程); 不重写 sync_runner.py 取数机制
  (从未被误用, 无证据支持); **补一张字段所有权契约测试**
  (`test_check_continuity_integrity_field_ownership.py`, AST静态扫描, 区分"事实型字段"可多个
  check共享 vs "判断型字段"必须且只能1个owner), 用红绿注入验证它真能抓住今天这个bug的同型
  复现, 把"只能靠人工代码审查才发现"的问题变成自动门。

## 修复清单

| 域 | 代码改动 | 数据改动 |
|---|---|---|
| margin | 新增通用 `split_by: {param, values}` 机制(sync_runner.py), 每交易日展开为N次显式过滤调用 | 回填4个历史"漏返"日期(0403/0430/0618/0626), 撤销过期的 known_group_gaps 墓碑 |
| margin_detail | `min_rows_per_batch` 800→2000(锚定2026真实基线0.6x, 与row_dip判定同口径) | 无(此前已回填0703, 阈值提高是防未来复发) |
| stk_surv | 新增 `page_limit: 200`(实测硬顶400, 200分页安全边际) | **全历史重新分页回填**(1191批, 774,855行, 0失败) |
| report_rc | `batch_mode` by_trade_date→by_ann_date | **全历史重新枚举回填**(2745批, 1,604,458行, 0失败) + 补1个新发现真缺口20260328(602行) |
| forecast | `batch_mode` by_trade_date→by_ann_date; `data_start` 20080101→20190102(K线边界修正, 见根因7) | 物删2019年前53,030行 + 从20190102重新回填(2745批, 47,321行, 0失败) |
| share_float | `batch_mode` by_trade_date→by_ann_date | **全历史重新枚举回填**(2745批, 1,584.7万行, 0失败但10批命中vendor二级分页上限) + 10个日期手工补齐(见下"新发现") |
| express / moneyflow_hsgt | 无(复核后判定不适用K线边界处置, 见根因7补记) | 无, 仅 registry 加注释存档复核结论 |
| (机制) | `check_continuity_integrity.py` 拆分 `gap_tolerance`(仅calendar_gaps) / `row_dip_tolerance`(仅cross_section)两个独立字段 + 字段所有权契约测试 | 6域(limit_list_d/suspend_d/margin/kpl_list/report_rc/block_trade)迁移到 row_dip_tolerance; stk_surv 修复后复核确认后同样加上 |

### 新发现: share_float 存在 vendor 侧二级分页上限(offset≈102000)

全历史回填过程中10个交易日(2020-09~2021-10窗口, 均为IPO解禁高峰期)在 offset=102000 处稳定
报错"查询数据失败"——实测确认这是**跟 limit 大小无关的绝对 offset 上限**(limit=1000/2000/3000/6000
在 offset=102000 时全部失败, offset=99000 时全部成功), 是 vendor 网关的硬性分页深度限制, 非我方
`page_limit`配置问题(与stk_surv的"单次调用硬顶行数"是不同类型的限制)。**处置**: 手工分页拉到
安全边界(offset<=99000)并写入, 10个日期共补齐 599,926 行(去重前raw抓取99万行, 去重后59.99万行
写入 — 抽样核实原始响应本身对同一 grain key 有显著重复率, 非全新增数据, 已按grain正确MERGE去重)。
**已知限制**(未来若此vendor接口改进或需要完整覆盖, 需人工复核): 这10个日期理论上可能还有少量
超出offset=102000边界的记录未能拉取, 影响面小(相对全表1584万行是万分之四量级)。

## 第二轮: 23域page_limit横向扫描 (2026-07-09, 用户委托的task_e215d934执行)

completeness lens 指出的"其余未验证域可能藏同型bug"由 24-agent workflow(23域并行探针+收敛)
执行完毕, 每域用真实DB查询(本地历史最高单日行数日期)+真实vendor API调用(裸调 vs 分页对比)
逐个核证。**结论: 2个真截断 + 20个安全 + 1个孤立残留**:

| 域 | 判定 | 实测证据 | 处置 |
|---|---|---|---|
| **report_rc** | 真截断(第9例) | 裸调硬顶3000(20210428裸调=3000, 分页3736, 缺19.7%), 本地多日恰卡整数3000 | `page_limit:1500` + **第三轮全历史回填**(2745批/1,663,492行+重试1个HTTP瞬态失败批后+4019行; 之前卡3000的日期现全部>3700) |
| **ths_hot** | 真截断(第10例, 本轮最严重) | 裸调硬顶2000(20251028裸调=2000, 分页8769, **缺77.2%**), 原以为的"442行封顶"是低量日表面现象 | `page_limit:1000` + 全历史回填(607批/435,373行; 唯一失败批=20240312已有墓碑的已知源端空洞, 非真失败; 之前卡2000的日期现2720~8769) |
| top_list/hm_detail/cyq_perf | **假警报** | 扫描agent报"本地少30~317行", 复核后全部=universe_filter合法排除(过滤后vendor与本地精确一致: 80=80/1448=1448/5194=5194) — 与本轮早前dividend/block_trade裸行数假警报同族教训(核对必须按项目自己的过滤/去重口径, 不能拿vendor原始行数直比) | 无需动作 |
| limit_list_d | 孤立截断残留(1/700+天) | 20250407(全球暴跌日, 真实2836只跌停)本地恰卡整数2500=同步当时网关截断残留, vendor现裸调已返全量, 全历史仅此1天 | 重拉补齐(2834行) + 防御性`page_limit:1000`(与stk_limit同型场景: 极端行情日行数暴涨) |
| 其余18域 | 安全 | 裸调=分页精确相等(如moneyflow 5198=5198/daily 5515=5515), 或行数量级天然远低于任何已知上限 | 无需动作 |

**注**: report_rc是同一个域在本轮被抓到**两个独立根因**(第一轮: by_trade_date排除周末; 第二轮:
page_limit截断)——第一轮修复后的160万行回填本身在高峰日也被截断过, 第三轮回填才是真·完整态。
一个域的"修好了"判断必须每个根因独立验证, 不能因为"刚全量回填过"就假设它完整。

## 明确排除的方案

- **任何形式的自动重跑/自动drain**: 用户"现阶段只手工点击更新"约束下, 不实现"diff不一致自动
  触发回填"类机制(constraint lens 明确判定这违反约束)。
- **重构13个registry字段为嵌套结构**: architect-controller裁决判定证据不支持(只有1例误用,
  已用字段所有权契约测试堵住), 全量重构收益不成比例。

## 验证

全量测试(不含 realdb)584 passed, 6 deselected(无关)。新增测试: `split_by` 机制3个(含
red-green)、`min_rows_per_batch`阈值3个(含red-green)、`row_dip_tolerance`拆分相关更新、
字段所有权契约3个(含红绿注入验证)、`by_ann_date`周末覆盖3个(含red-green)。

6域全部通过 `check_continuity_integrity.py` 单域核查: margin/margin_detail/report_rc/
forecast/share_float 全部 PASS(0 warn/0 fail); stk_surv 仅剩 1 项已知的、2026-07-05 就
审过的日历稀疏 WARN(与本轮修复无关, `gap_tolerance:annotate` 覆盖范围)。全部6域的周末/
历史缺口样本均已实测确认修复生效(vendor与本地精确吻合, 非估算)。

**最终数据规模**: margin(回填4日)/ margin_detail(阈值调整无需回填)/ stk_surv(1191批,
774,855行) / report_rc(2745批, 1,604,458行) / forecast(2745批, 47,321行, K线边界修正后
规模从原计划的6600+日历日大幅收窄) / share_float(2745批, 1584.7万行 + 10日补拉599,926行)。

## 第三轮: 8维度全地基审计 + READY_WITH_FIXES 九项修复 (2026-07-09~10, 用户"全方位检查+设置目标持续推进")

15-agent workflow(8维度并行审计 + HIGH发现对抗复核 + 收敛裁决)对全部47域做了含本战役新学维度的
全面检查: batch_mode×date_param语义匹配 / 非by_trade_date域page_limit盲区 / 墓碑新鲜度 /
min_rows校准 / 四地基 / PIT锚一致性 / vendor抽样(正确口径) / 治理门健康。

**裁决: READY_WITH_FIXES** — 干净面: 四地基主体全过(K线1822交易日0缺口/grain 0重复/universe
纯度100%/日历前瞻119天), 治理门7/7绿, vendor抽样8/8精确一致, 无NOT_READY级损坏。需先修9项
(4 HIGH + 5 MEDIUM), 全部已执行:

| # | 修复 | 执行 |
|---|---|---|
| H1 | stk_surv 周末调研结构性漏采(与report_rc同型, 自建域起; vendor实测财报季周末≥400行/天) | batch_mode→by_ann_date + 全日历回填(2646批/829,417行, **周末新增54,063行**, 实测周六20231028=524行/周日20250427=1803行远超裸调400硬顶) |
| H2 | ths_hot 周末热榜漏~10-15万行(vendor实测周六394-560行, 含热基死榜停更前周末历史) | batch_mode→by_ann_date + date_param显式声明 + 全日历回填(执行中) |
| H3 | block_trade 20250917 墓碑过期掩盖真缺口(vendor已补发至210行原始) | 撤墓碑+回填178行(grain去重+白名单后, 与审计预期精确一致) |
| H4 | margin_detail min_rows=2000 使594个2019-2021真实完整日成drain永久幻影缺口 | sync_runner 新增**时代分段阈值机制**(min_rows_since/min_rows_before, drain与run_domain同口径) + 4个red-green单测(含"旧行为下幻影缺口最小复现"对照组) |
| M5 | balancesheet/fina_indicator 硬编end_date=20260612(H1财报季新公告结构性拉不到且零告警) | 去硬编, runner动态注入 |
| M6 | 墓碑9抽4翻转(44%): sw_daily 20260707/moneyflow 20231122/ths_hot组20260703/block_trade 20250917 全部过期 | 4条全撤销(留带日期的撤销记录注释); 沉淀规则"距今<5交易日的空洞禁立墓碑" |
| M7 | block_trade available_after 双配置矛盾(sync_registry t+1 vs data_access.yaml eod, 21 entity中唯一方向性冲突) | data_access.yaml对齐t+1; spec.py缺省"eod"→"t+1"(不安全缺省方向修正) |
| M8 | index_daily 隐藏8000上限实锤(裸调恰返8000且头部被砍) / stock_basic 5535逼近6000且是universe身份真相源 | 各声明page_limit(3000, offset分页均实弹验证) + min_rows校准(100→3200) |
| M9 | fina_indicator pit_anchor "取update_flag=1"对87%行不可执行(API漂移后全NULL) | 改纯ann_date口径 + 清陈旧grain注释 |

**LOW攒批一并落**: min_rows 校准8域(dc_member 1000→7000/dc_index→400/trade_cal→8000/
moneyflow_ind_dc→600+era-aware/limit_cpt_list→12/stock_st→100/hm_detail→60/ths_hot→250/
index_dailybasic→1100), index_dailybasic补pit_anchor+available_after(47域唯一双缺),
index_daily_benchmark补available_after, stk_surv date_param澄清注释, ths_hot dead_groups机制
断言更正(data_type是输出列名, market才是真输入参数 — 当年"参数被忽略"的裁决测的是不存在的参数),
adj_factor"唯一盘前"措辞更正, kpl_list pit_anchor文字同步。

**被复核推翻的假警报**(防重查): dc_member "近60日恒定87856"证伪(真实波动30k-89k, vendor核证
为源端体量); block_trade "泄漏方向"反转(实测当日23:38已可用, 错的是过保守的t+1声明方向而非泄漏)。

### 执行期间新增3个独立发现(全部当场修复)

1. **ths_hot 切换事故(烧~150次API后抓获)**: 切by_ann_date时漏声明date_param, 该分支默认值是
   "ann_date"(非by_trade_date的"trade_date") → 发给vendor的参数变成未知参数被忽略, 每页返回
   相同全量→分页永不收敛烧满50页/天防御上限。教训: **切batch_mode必须核对新分支全部默认值差异**,
   stk_surv同批切换没炸纯因它本来就显式声明了date_param。
2. **by_ann_date分支"显式--start丢第一天"残留bug**: 单日验证暴露2天范围只跑1批 — by_trade_date
   分支2026-07-06修过的同款判据bug在by_ann_date分支原样存在(当时只修了一处, 同型判据散落两处
   未一并修)。已修+实测验证(2批/788行/两天各394行落库)。
3. **裸python PATH陷阱(机器重启触发, mythos同型第N例)**: 重启后裸`python`从PATH消失+PATH顺序
   变化使python3解析到系统3.9 → safe_commit.sh/chunkyctl全部治理门command-not-found假红(8个
   沙箱测试失败)。根治: 两脚本加PY解析器(优先项目venv→python3→python), 替换全部24处裸调用。

**验证**: 全量测试587 passed+1 skipped(8个PATH失败测试全部恢复); stk_surv周末数据实测落库;
ths_hot回填修正后单日验证精确落库; 6域continuity check全绿。
