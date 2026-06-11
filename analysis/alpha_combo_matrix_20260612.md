# Alpha 组合实验矩阵 (tushare × iFind) — 三视角设计 + Judge 定稿

> 2026-06-12 | workflow: 3 设计视角 (筹码×资金流 / 产业链×概念事件 / 反直觉) 各出 4-6 combo,
> judge 按 PIT 可行性 × 与 46 维量价正交性 × 判决力 三轴评审。16 设计 → 4 run_first / 9 run_later / 3 reject。
> 纪律: 每实验跑前预注册判据冻结阈值; 判负按处置条款归档不复跑不放宽; 验证期 0 真金白银。
> judge 亮点: 用 data-status 红线拒掉一个谎报已落库的设计 (ths_hot 实测 NEVER_SYNCED) — 治理工具上岗。

## 评审榜 (按分排序)

### [run_first] 9.2 — C0 cyq_perf 黑箱口径审计 (前置 gate)

PIT 满分 (无 forward 决策, 纯口径审计); 判决力满分 — 三个预注册数值判据 (Spearman>=0.95 / median|Δ|<=5pp / 极值一致率>=99%) 一次定 5 个下游 combo (A组C1-C4 + C组C4) 生死, 且结论写回 sync_registry pit_anchor 成机器可读契约; 复权口径不核证则 C2/C3 全样本方向性污染 — 这正是 4.5 反例 'audit 必须验证运行时口径' 的正解。成本秒级。唯一弱点: 依赖 chain5 daily/daily_basic(未复权 close + float_share) + chain6 cyq_perf 部分回填, 今天还跑不了。

**修法/前置**: 与 C组C4 的第 0 步 (10 个高送转案例核证 winner_rate 除权路径) 合并去重 — C0 的除权日 Δ 突变分布直接作为 C组C4 的开跑前置, 不要跑两遍。

### [run_first] 8.6 — C2 出货预警退出组件 — 持仓 winner_rate 高位 × elg 极端流出/流入

正交性最高的一支: 退出端与 46 维入场量价基线天然正交 (hologram 盲点2 — 全项目期望值最高单项 +10pp mean, 实现度 0; 盲点4 — 主力数据强项实证在退出端); CYQ 入场前科不适用于持仓监控用途 (hologram 原文 '真价值被自己定位到持仓监控/出货预警')。PIT 锚干净且自指了最易写错的一行 (t close 成交 = 泄漏, 必须 t+1 open); '纯浮盈回吐基线' 对照臂正面拆解 winner_rate-浮盈共线。判决力强: 双指标 + 降级路径预注册, 4 臂重放一次出结论。strategy_portfolio 三评委一致 '失败残值标杆, 独立交付组件'。扣分: 阈值小网格 walk-forward 有轻微 '再试试' 尾巴, 须预注册网格边界后冻结。

**修法/前置**: 开跑前把网格 (winner_rate 分位 / 连续 N / 极端分位) 三参数的取值集合写进预注册文档冻结, 防判决后追加网格。依赖 chain6 cyq_perf + C0 PASS; moneyflow/triggers/paper_sim 已就绪。

### [run_first] 8 — C1 — LHB 上榜即退出: 已证伪入场信号的镜像

与已证伪方向 (LHB 入场 lagging) 是镜像而非同构 — lagging 本身就是出货标记, 有明确新机制且 hologram 盲点4 补法原文点名 '(b) 退出端 — 出货识别恰恰是 LHB 的强项, 入场不是', 从未有人跑过。PIT 设计抓住了真正的命门: 同日同涨幅±1pp+市值桶匹配对照, 否则把涨停均值回归当 LHB 效应 — 这个混淆臂不做结论全废, 设计里是强制项。判决力: Δ阈值 + 逐年同号 + 判负处置 (除名退 regime 温度计) 全预注册。top_list watermark 实测已在 sync (20260611), 2020+ 部分窗设计自带回填截断纪律。

**修法/前置**: top_inst 无 watermark (chain4 分页队列) — 机构席位 side 分桶臂改为可选副表, 主判决不等它; 开跑 gate = SELECT min(trade_date) 实测覆盖 >= 20200101, 不引用文档。

### [run_first] 7.8 — C3 chain_leader_follower — 概念内龙头确认→低位跟随股 T+1 扩散

16 个 combo 中唯一今天数据全就绪的主判决: dc_member 2.71M / limit_list_d / stk_limit 5.76M / kline 全有 watermark 实测在库。它判决的是项目唯一明确的未验证大额剩余 lift (theme/LF +5-10pp, hologram A.3), 判负即封掉 bank/sentiment.py 死代码和套三复审的最大单点 — 判决价值与成本比极高。stk_limit 盘前锚做 t+1 open 可成交过滤直接回应盲点6 (突破×T+1 高开互斥, 从未量化)。e1 倒U 警示已内置 (极端热日单独分桶)。扣分: '阈值进 search space 不拍死' 在判决实验里是尾巴风险 — V0 必须固定阈值出 go/no-go, 扫参只许在判正后做。

**修法/前置**: V0 龙头定义冻结为 limit_list_d 涨停单一口径 (量比/新高臂留 V1), 消灭判决阶段的自由度; dc_index 热度分桶臂在 dc_index 落库前用成分等权自算指数替代或延后。

### [run_later] 7.4 — C2 member_add_confirm — 成分新增事件的数据商确认效应

统计功效好 (数千 ADD 事件), momentum-matched 对照被正确定位为生死设计而非可选项 ('数据商选股即后视动量' 是本实验唯一致命混淆); 判负有体面归宿 (链谱边弱监督) 不留尾巴; 概念域 0% 覆盖 = 正交性真实。降为 run_later 仅因 dc_index 无 watermark (热概念条件分桶依赖), 且 fact_concept_event 实测 0 行需先 build (B-C3 顺手产出)。reconstructed 成分回溯修订的 1/2/3 日滞后敏感性三跑是对的。

**修法/前置**: 无条件版 (不分热/冷) 可在 dc_index 落库前先跑出主表; DROP 副表与 C组C1 的退出端结论交叉引用。

### [run_later] 7.2 — C4 — 除权日筹码强制重画: dividend × cyq_perf 填权速度准实验

三组 CYQ 实验里机制最新颖的一个: 不做无条件入场 filter (已证伪), 改在日历事件强制重画筹码时读它 — 与前科真正脱钩; 安慰剂臂 (小额现金分红应无区分, 否则是 artifact) 是 16 个设计里最干净的证伪装置; 分组窗与收益窗显式不重叠。dividend 已落库 (watermark 20260611 实测)。扣分: winner_rate 复权语义核证与 C0 完全重叠 (应去重); 高送转 selection 修正靠分层+市值中性, 残余混淆比 A 组对照臂设计弱一档。

**修法/前置**: 第 0 步删掉, 改为硬依赖 C0 的除权日 Δ 突变分布结论; C0 FAIL 即本 combo 自动冻结, 不要自带一套口径核证。依赖 chain6 + chain5。

### [run_later] 7 — C1 深套盘 × 温和特大单流入 — reversal 回调增强 gate

站在 E1 实测地基上 (倒U +10.9pp 双公式复现, 原文核对一致), 增量假设边界清晰; 与已证伪的 CYQ 入场 filter 同构风险被两道闸处理: 前科限定突破宇宙 (反转宇宙未测) + 残差生死关前置 (strategy_portfolio 套一 L1 原文预注册, 核对属实); 自己点破了 V0 close 基准在回调日的 gap 美化问题。判决力: 任一不成立即退回纯 E1, 无尾巴。扣分: 入场端 winner_rate 先验仍偏空 (评委已 '期望打对折'), 正交性是本实验要证的而非先验给的; 排序应在退出端 (A-C2) 之后。

**修法/前置**: 4 臂对照里 (c) 仅 winner_rate gate 臂必须先于 (d) 交互臂报告 — 若 (c) 即为负, 交互臂正结果按多重比较打折预注册。依赖 chain6 + C0 PASS + 残差关。

### [run_later] 6.8 — C5 — 卖方覆盖密度突变双臂判决 (report_rc)

正交性有三评委背书 (套二原文 'D 里 alpha 期望最高, 与 46 维量价正交', 核对属实); 双臂对决 + 三种结局预注册 = 一次扫描裁决 D 蓝图核心假设, 判决结构好; 反指先验比正向更符合自家 lagging 证据链, 方向选择诚实。扣分: 行量 unknown (registry 原文 '先探 3 个月再排期') + create_time 历史缺失会把判决窗收缩到不知多短 — 判决力实际兑现度是 16 个里不确定性最大的之一; report_rc watermark 实测仅首跑增量, 2010+ 深度未回填。

**修法/前置**: 把 '探量 + create_time 完整段起点测定' 拆成独立第 0 步并设硬 gate: create_time 完整段 < 3 年即降级为方向参考实验, 不出判决, 防止跑完才发现窗口不够硬撑结论。依赖 chain4 report_rc 深度回填。

### [run_later] 6.6 — C4 底部筹码重构 — 集中度 × weight_avg 搬家 × 横盘 × 温和流入

唯一正先验筹码因子 (+4.4pp, hologram 原文核对) 的放大而非新赌注, 方向选择对; '分红→成本下移→假搬家' 这个本 combo 特有的 artifact 被双修法对照处理, PIT 意识到位; 2^3 ablation + 集中度单轴 <+2pp 整组证伪 = 判决干净。扣分: 三条件交集 × 横盘宇宙的样本量未做先验核证 (E1 纪律是 n>3,200/桶), 交集桶 n 不足时 +4pp 判据无功效 — 这是设计里唯一没堵的洞; 四数据域依赖 (chain5+chain6+moneyflow+dividend) 排期最靠后。

**修法/前置**: 加第 0 步: 先 COUNT 三条件交集日均信号量, n 不足预注册降为两条件版 (砍 weight_avg 轴, 它是三轴中机制最弱的) 而非放宽分位边界。

### [run_later] 6.2 — C3 套牢压力真空 — close vs cost_85pct × 60日新高突破

是用户自己的 CYQ spec P0 假说 (写完 0 实现) 的判决, 有存在正当性; overhead 机制与已证伪的 winner_rate filter 不同字段不同机制, 且 '对 (1-winner_rate) 残差无增量则并轴' 把同构风险变成了显式判据 — 这是教科书级的处理。但它是 16 个里离已证伪结论最近的一个: 前科 (FAKE winner_rate 反而更高) 恰恰出自突破宇宙, 本 combo 又回到突破宇宙赌筹码轴; 8 年窗 + 分年 fold 防牛市 beta 是对的但先验仍最弱。口径错配风险自评准确 (显式 C0 前置)。

**修法/前置**: 排在 A 组最后跑; 若 A-C1 (反转宇宙) 的 winner_rate 残差增量已判死, 本 combo 的开跑门槛自动提高为 '残差判据预注册更严 (+1pp)', 防止筹码入场轴靠换字段无限续命。依赖 chain5+chain6+C0。

### [run_later] 6 — C4 chain_updown_transmission — 上游强势→中游滞后窗口

PIT 结构设计是 16 个里最严谨的之一: 2021 定版静态映射早于回测窗 = 零后视, 链谱 freeze_date append-only forward-only, fina_indicator 未注册就明说不用 (宁缺毋滥纪律的正面示范); 数据已就绪 (industry_sw watermark 2026-06-11 + kline 实测在库); 申万 L2 0.137 是已 measured 的最优颗粒。判决定位正确: 历史段是给细链谱花工时前的廉价生死判决。扣分: 上中下游映射 YAML 需人工编制 — 不是今天可直接跑, 且映射编制本身有自由度 (哪些 L2 算 '同链') 会污染判决, 须定版出处逐条可查; 传导效应在 A 股从未被本项目实证, 先验中性偏空。

**修法/前置**: 映射 YAML 编制时同步写一个 '反链' 安慰剂组 (随机配对非链 L2), 传导差值必须同时赢过非链对照与随机链对照才算数, 堵住映射编制自由度这个洞。

### [run_later] 5.8 — C5 concept_overheat_veto — 概念热度过热的左尾/退出 gate

落点正确 (退出端, 盲点2/4) + e1 倒U 同构证据真实 + 复用 e1_v0 代码路径零新框架 = 成本极低; 验收指标 Δ左尾/Δmax_dd 而非 Δ胜率, 直接治盲点1; ths_hot 22:30 → JOIN t+1 的锚处理是对 registry 原文的正确引用。扣分: ths_hot 与 dc_index 双双无 watermark (实测 NEVER_SYNCED), 是 run_later 里数据依赖最远的; 2024+ 单牛市窗口自评诚实但判决只剩 go/no-go, 信息量打折。诚实声明 '排队中落库即跑' — 与 C组C3 形成对照, 同数据不同诚信。

**修法/前置**: 无, 等 chain4 ths_hot + dc_index 落库即跑; 与被拒的 C组C3 (衰减率) 合并排期 — 若本 combo 判 ths_hot 域非零, 衰减率作为后续臂一次补跑。

### [run_later] 5 — C2 — 龙虎榜冷却期二审

LHB 入场已三判死刑 (V7 -2.8pp + precision 6%<base 9.5%), 这是 '与已证伪方向同构' 红线上的边缘案例 — 救它的是真实的新机制 (上榜池=9% 爆发型母集标记 + 时间隔离后用 91% 慢牛逻辑入场 = 两个已验证结论的未试组合) 和内置负对照臂 (1-5 日立即追必须显著差, 否则实验自身有 bug — 这个自检设计值得表扬); 冷却定义只含 trailing 信息的 PIT 处理干净。但先验偏空 + 项目纪律 '不留恋', 给它的只能是末位排期和一次机会。

**修法/前置**: 硬性串行 gate: 仅当 C组C1 (退出镜像) 实测显示上榜事件对 forward 分布有任何非零信息时才开跑; C组C1 全平则本 combo 不跑直接归档, 省下这次 SQL。

### [reject] 4 — C1 concept_birth_half_band — 概念诞生事件分桶

判决力先天不足: 母体 66 概念 × 1.4 年, BORN 事件 = 窗口内新出现的概念, 量级几乎注定击穿其自己预注册的 <30 fallback 线 — 一个大概率触发 fallback 的实验不该占独立排期, 这正是 '不产生再试试尾巴' 要拒的形态。设计本身无 PIT 硬伤 (event_date 锚 + 滞后敏感性都对), 正交性也真实, 但 n 不够一切白搭; fact_concept_event 实测 0 行。

**修法/前置**: 可救: 按其自己的 fallback 条款执行 — 并入 B组C2 同一 panel 作 BORN 副表 (零额外成本), 先 COUNT 核证, n>=30 才允许独立成章; 不单独立项。

### [reject] 3.5 — C3 — ths_hot 热度衰减速率退出

数据声明与实测矛盾: 声称 'raw_tushare_ths_hot 已注册已落库', 但 mart_data_source_watermark 22 行实读无 sync:ths_hot (NEVER_SYNCED), goal.md 亦列其在待迁移队列 — 触犯本项目 2026-06-11 刚立的 '不臆造已落库' 红线 (data-status 工具的存在理由), 评审纪律上必须拒, 否则红线形同虚设。叠加: 自评 '5 个 combo 里证据最薄' + 2024+ 单 regime + 与 B组C5 同域重叠 (同等未落库但声明诚实)。设计本身 (衰减率 vs 水平双假设 + censoring + 缺榜日剔除) 是好的, 死在数据声明造假。

**修法/前置**: 可救: 更正数据声明为 'chain4 排队, 开跑 gate = sync:ths_hot watermark + 日榜 444 行完整性核证'; 重新排期时挂在 B组C5 之后 — B组C5 判 ths_hot 域非零才跑衰减率臂, 两实验共用一次落库一套完整性过滤。

### [reject] 3 — C6 mainbz_purity_rank — 收入加权节点纯度

三轴里判决力轴直接不及格: 12 周 forward-only 记账 + 单期年报 + ~6 周已累积窗, 自评 '统计功效最弱' '永不直接出生产 gate' — 无论结果如何都不产生一次性生死判决, 不满足本次评审的判决力门槛。数据面: fina_mainbz 无 watermark (chain3 排队但落库未核证), bz_item→概念映射依赖 iFind 人工核证 = 不可直接跑。其自己的定位 ('搭车判决 + 廉价期权, 不单独占研究排期') 其实已经承认了不该作为独立 combo 入列。

**修法/前置**: 可救且应救: 砍掉独立排期, 只保留第 (3) 条搭车臂 — 纯度作为 B组C3 follower 选择的一个 ablation 维度 (同窗口零额外成本); 12 周 forward 记账作为后台 cron 记录, 不占评审与排期名额。

## 执行序 (judge 定稿, 依赖全部 watermark 实测口径)

T0 (今天即可, 全部依赖 watermark 实测已落库): [1] 先跑 build_concept_events.py (dc_member 2.71M 已就绪, fact_concept_event 实测 0 行) — 它是 B组C3/C2 的共享前置; [2] B组C3 chain_leader_follower V0 (dc_member+limit_list_d+stk_limit+kline 全在库, 龙头定义冻结涨停单一口径); 并行可启动 B组C4 的链映射 YAML 编制 (industry_sw+kline 已就绪, 但 YAML 定版是人工前置)。注意: tushare_raw.duckdb 当前被 chain4 adj_factor 回填写锁占用 (PID 21403 实测), 所有读取走 read_only 并避开写锁窗口。 || T1 (chain4 落库节点 — top_list 深度/adj_factor/report_rc/ths_hot/top_inst): [3] C组C1 LHB 上榜即退出 — 开跑 gate = top_list min(trade_date) 实测 <= 20200101 (watermark 已在 sync 但 2005 深度回填中), top_inst 臂可延后; [4] C组C5 第 0 步 report_rc 探量 + create_time 完整段测定 (只探不判)。 || T2 (chain5 K线三件套 + chain6 cyq_perf 部分回填落库): [5] A组C0 黑箱口径审计立即跑 — 50 股×250 日部分回填即可, 不等全史; 它是 A组C1-C4 + C组C4 共 5 个 combo 的唯一生死开关, C0 任一判据 FAIL 则筹码轴全冻结, 研究产能全部转入 T0/T1 已开的非筹码轴。 || T3 (C0 PASS 后, 按期望值排序串行): [6] A组C2 出货预警退出组件 (期望值最高, moneyflow 已就绪+chain6) → [7] A组C1 深套盘×温和流入 (残差生死关前置) → [8] C组C4 除权重画 (第 0 步已由 C0 覆盖, 去重) → [9] A组C4 底部筹码重构 (先 COUNT 交集 n) → [10] A组C3 套牢真空 (A 组最后, 若 A组C1 残差判死则门槛自动加严)。 || 条件分支: B组C2 等 dc_index 落库 (无条件版可提前); B组C5 等 ths_hot+dc_index 落库, 判非零后才带上被拒 C组C3 的衰减率臂补跑; C组C2 冷却期二审仅在 C组C1 显示上榜事件含非零信息时开跑, 否则不跑归档; 被拒的 B组C1 并入 B组C2 作 BORN 副表; 被拒的 B组C6 只保留 B组C3 的纯度 ablation 搭车臂。全程纪律: 每个判决实验跑前预注册判据冻结阈值, 判负即按各自处置条款归档, 不复跑不放宽。
