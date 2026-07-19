# ChunkyMonkey Project State Ledger

> 状态：historical evidence index，query-only
> 重整：2026-07-16
> 完整逐 commit 细节保存在 git history、数据库审计表和 `data/reports/`。本文件只保留会改变后续判断的事实、裁决和不可复现实证，不再充当 session log。

## 使用规则

- 当前 objective、blocker 和下一步看 `goal.md`；
- 当前架构/研究/工程规则看 `docs/README.md` 指向的 owner；
- 精确表数、行数、水位、命令和工具状态必须 live 重查；
- 本账本中的历史数值仅说明当时证据，不能自动成为当前生产证书；
- 普通过程叙述、旧计划和重复设计已于 2026-07-16 删除，git history 可恢复。

## 项目演变

### 2026-04 至 2026-05 — 原始产品与策略实验期

- 项目最初以机构行为/跟随、股票档案、选股公式和策略实验为主；
- 随后扩展出多个数据源、特征 panel、Optuna、paper/backtest、前端和 provider 执行路径；
- 资产增长快于契约和验收，形成大量表、配置、状态写者和互相引用的流程；
- 历史高收益/胜率结论混有 latest snapshot、PIT、执行、样本和治理问题，后续全部失去生产证书资格。

### 2026-06-11 至 2026-06-16 — 第一次系统审计与架构警报

- 审计确认项目不是缺少治理口号，而是治理分散、测试/门禁可空转、配置和中间表成为第二真相源；
- `dim_all_ever_listed` 快照判断退市曾误伤大量仍有 K 线的股票，确立“K 线/交易日历优先于派生快照”的教训；
- 多个历史回测出现异常好数字，追查出未来信息、latest fallback、全期拟合或执行假设问题；
- 裸 K reversal 虽可出现正 IC，含成本 long-only 结果仍显著为负，确立“IC 只是诊断，含成本绝对收益才是可交易证据”；
- 开始建立 PIT、leakage、执行、配置、数据层和 Rule 10 纪律，但当时仍存在治理工具自膨胀。

### 2026-06-17 至 2026-06-27 — 数据源与真相源收敛

- universe 逐步从快照式 active 表转向证券身份 + K 线/规则判断；`dim_active_a_stock` 降为身份/名称/cache；
- K 线切到 TuShare 路径并修复旧复权错误；当前 qfq builder 对收益分析有价值，但 2026-07-16 审计确认它仍缺 batch/ingested/factor 血缘且不能兼任名义成交价；
- 多个 AkShare/TDXHub/旧机构/旧 ETF 路径被迁移或退役，删除前保留必要 parquet/删除记录；
- 主升浪 episode-first ground truth、负样本、strata、embargo、买入可行性和阶段资产开始形成；
- 历史实测表明主升浪样本不是简单连续涨停模型，起涨可交易性较高，但这只证明“可买”，不证明策略 alpha。

### 2026-06-28 — 地基 reset

- 因策略/特征/serving 污染和表爆炸，项目执行大规模 reset，删除大量旧代码和表，收缩为数据平台；
- 建立 `data_layers.yaml`、DataAccess、lineage、retention、sandbox、dead-reference 等治理面；
- reset 清除了大量错误资产，但也造成新问题：真实产品目的被压扁成“纯数据平台”，部分执行器/门禁/文档被删后仍有残余引用，配置开始描述不存在的未来世界；
- 结论：reset 是必要清障，不是最终架构。

### 2026-07-02 至 2026-07-04 — Edge 资产局部重建

- 重建/保留了 `segments`、`technical_states`、`market_pulse`、`institution_profile`、`rally_gt` 等模块；
- technical state 形成位置/趋势/纯度/量能/波动等多轴状态和 breakout/pattern 逻辑；
- market pulse 同时接入东财概念/行业和申万 L1/L2/L3；
- institution profile 与 rally ground truth 具备可复用研究价值；
- 这些资产当时按功能交付，但尚未形成统一 DatasetSnapshot/Experiment/StrategyRelease/Decision 契约。

### 2026-07-06 至 2026-07-10 — 全面数据与门禁复核

- 多轮全仓死代码/死表/死配置清理暴露：删除供给侧时常漏治理脚本、测试、Moth 和文档消费者；此后将五类消费者纳入删除检查；
- 发现多个门禁存在 pass-by-vacuity、错误扫描面、fixture 与真实 schema 不一致或 WARN 冒充 PASS；
- continuity、分页截断、cross-section 行数、failure queue、UTC/fetched_at、rally strata 等问题得到多轮 red-green 修复；
- 机构 profile 的持仓筛选曾使用 report/open date 而非真实 notice/available time，修复后确认 disclosure availability 是策略硬边界；
- SERVE 读层审计确认 builder 需要读 raw，但普通消费者必须通过公开契约；仅靠目录/白名单并不足以证明边界。

### 2026-07-15 — 手动数据更新链复核

- 用户确认取消自动跑批；live launchd/cron/launchctl 未发现 ChunkyMonkey 数据调度，仓库仍有旧 installer/snapshot 文本残留；
- 交易日历门通过，手动 daily update 暴露 margin 多市场半批、continuity、watermark/failure outcome 分裂等问题；
- 形成当前未提交 Tier0 修复 slice（见 `goal.md` 文件清单）；该 slice 在 2026-07-16 架构 Phase 0 期间冻结，待独立 Rule 10 和真实更新链复核。

### 2026-07-16 — 顶层架构重新裁决

用户重新明确产品目的：

1. Tier0 正确获取与加工交易数据；
2. 识别股票阶段/形态；
3. 感知市场资金活动及行业/概念趋势；
4. 验证机构跟随、主升浪和选股公式在裸 K/状态基础上的增益；
5. 采用“模块 + 数据 + 配置”的积木式管理。

Controller 使用 Mio、架构控制、CodeGraph、Moth、只读 DuckDB 和三路对抗审查后裁决：

- 方向应扩展为 `module + data + config + contract + evidence`；
- 分开 transport 轴（landing/canonical/serve）和业务 Tier0-Tier4；
- 保留大部分数据/算法资产，重建边界和发布契约，不 big-bang rewrite；
- 分类统一身份/时间契约而非统一成一棵树；
- 市场感知表达活动度、不平衡代理、参与广度和价格响应，不声称资金守恒迁移；
- stock state 是版本化描述，未来 label/概率/信号属于研究；
- 策略统一走 B0 裸 K → B1 状态 → B2 市场 → B3/B4/B5 单块消融；
- Tier4 必须由 ExperimentVerdict → StrategyRelease → DecisionBatch → Candidate → paper evidence 闭合。

### 2026-07-16 — Phase 0 控制面与仓库整理

- 活文档收敛为 `AGENTS.md`、`goal.md` 和 `docs/README.md` 指向的三份契约；历史设计、handoff、checkpoint、archive-of-archive 和无消费者过程文件按 fan-in 证据删除；
- 数据更新明确保持 `manual_only`；live crontab/launchctl 无 ChunkyMonkey 数据跑批，旧 session snapshot/install/resume 路径和未注册的本地 SessionStart handoff hook 退役；
- Rule 10 收敛到单一 `check_codex_review.py`，任意顺序的 `REQUEST_CHANGES` 均阻断；safe-commit 的 feature map、lineage 和静态门改为 exact staged snapshot 验证；
- 修复 lineage 投影的结构性假绿：表节点由裸 `table_name` 改为 `db.table`，跨库同名表不再静默合并；裸名 impact 保守汇总全部匹配库，DataAccess entity 别名只连接声明库；
- 旧 storage-retention 空库存可 PASS、legacy-flow 读取失败可放行，属于 verifier 假绿；两套无当前消费者的 dormant runtime/gate 已删除，正式 lifecycle/retention 留作 Tier0 contract 明确缺口；
- 旧 holdout helper 的“单触碰”只是非原子日志且无发布调用方，已收缩为 training boundary guard；single-touch/prereg/freeze 仍是未来 Tier3 发布门，不冒充当前能力；
- taxonomy 从混合 `dc_concept` 链拆为 `dc_industry`、`dc_concept`、`sw_industry` 三 namespace；`content_type` 仅保留源证据，不再兼任身份和 grain；
- BestChoice 删除独立 agent/goal/handoff、App、runners、恢复快照和重复结果，仅保留两份历史实现、两份全量机器证据、smoke 与 fail-closed manifest verifier；
- 新增 stale staged lineage 必须阻断、unstaged/frozen 变化不得污染 staged gate、跨库同名表和双向 Rule 10 verdict 回归；首轮聚焦测试 117 passed，最终证书以提交前 exact staged gates 为准。

### 2026-07-16 — 手工更新实弹与 Phase 0 假绿复核

- 20:03 由受支持入口 `scripts/daily_update.sh --date 20260716` 手工执行；日历门确认当日开市，授权 `opened_at=2026-06-17T10:48:58+08:00`、`expires_at=2026-08-12T15:43:00+08:00`，全链以 4 项 degraded、exit 1 诚实结束；没有 cron/launchd/17:00 自动触发；
- 今日核心行情链有边界地完整：daily/daily_basic 均 5,197 股且集合一致，daily 对 adj_factor/stk_limit 零缺，OHLCV 与 grain 检查无异常，名义行情到 qfq 派生同日贯通；这不等于 Tier0 架构闭合，也没有独立交易所源交叉验证；
- 唯一 continuity FAIL 是 `margin_detail`：完整前沿 20260709 为 3,471 行（SH 1,651 / SZ 1,820）；7 月 10/13/14 的旧数据只有 SH，7 月 15 无 accepted partition。本次重拉继续只返 SH 半批，新完整性门拒写并保留 open failure；不得降阈值、写 known-empty 或删告警凑绿；
- continuity 把全局 20260716 当成所有域应到日，未消费 `available_after=t+1`，因此把真实 4 个缺日写成 5 个；all-due 还把 `unsupported` 域排除出 bad。两项不改变本次 FAIL，但都是冻结 Tier0 verifier 的后续阻断项；
- DC 当前快照实际已写成功，失败来自 verifier 对 `duck_adapter.Row` 使用 slice；回归测试先红后绿，live 复验为 5,204 股、31/128/337 个 L1/L2/L3 桶及 494 个概念；随后用正式 `chunkyctl pipeline process` 独立重跑，segments/technical states 幂等零新增、process 恢复 `check_pass`，原 full-run 告警仍保留作历史证据；
- market pulse 先在 APFS 克隆库演练，再在正式手工链原子重建；正式表 1,061,329 条 sector 日记录、855 个 market 日，覆盖到 20260716，三 namespace、两张唯一索引、零重复 grain、零 shadow residue；
- 源缓存清理曾错误进入 `.venv` 并删除 TinyShare 受管 `.pyc` payload，使 4 个授权测试失败；依据完全一致的 wheel RECORD 从本机同版本环境恢复后 13 项授权测试通过，并将 `.venv`/managed runtime prune 规则写入 AGENTS、工程治理、skills 与 Moth assertion。该事故证明“清缓存”也必须有 ownership 边界，不能把依赖 payload 当生成垃圾。
- BestChoice assertion 改为 module invocation 后暴露 Moth 自污染：首次 verifier 会生成 `__pycache__`，下一次 doc-governance 将其判成冻结包漂移。命令补 `python3 -B -m` 后，连续两次 `moth assert --repo .` 均为 30/30 PASS 且不生成缓存；控制面最终证据还包括 exact staged pytest 724 passed/8 deselected、前端 build、BestChoice 双 smoke、doc/lineage/feature-map/grain/calendar/config/dead-ref/sandbox/SERVE 门和 CodeGraph fresh。
- 独立 Rule 10 终审在 Codex sandbox 复现 `doctor` 的 `crontab -l` `PermissionError` traceback；`_run_command` 原来只捕获 FileNotFound。新增回归先红，再将其他 `OSError` 转为 rc=126 的结构化失败；focused suite 26 passed。受限环境现在诚实返回 automation `FAIL`（无法证明调度面），沙箱外只读复验仍为 `manual_only` PASS；不得把“审计命令无权限”降成无自动化。
- 对抗审查又证伪 market pulse 的三类假绿：迟到窗口 DELETE+重插可在源缩短时少写成功，全量 shadow 只查非空/重复而不保护 accepted state，新 frontier 即使行业/概念各仅 1 行也会自洽发布。新增逐键/逐日回退门：增量要求目标 namespace/date 不缩行、不换键且 market 每个目标日恰一行；全量对全历史 DC 与可映射 SW raw grain 做双向逐键一致。最新 DC moneyflow 还必须与同日 dc_index 行业/概念 catalog 逐键一致，并达到 taxonomy 派生下限 448/450；最新可映射 SW 下限 380。历史 vendor 覆盖不强求逐日矩形；既有 accepted keys/dates 全保留，legacy 修复也只允许错误行业键以同 sector/date 更正 namespace。57 项 market/API 反例通过，2GB APFS 克隆在新门下仍完整重建（1,061,329 sector rows / 1,827 sector days / 855 market rows）。
- DC verifier 原来只比较 serving 自身汇总，builder 又先覆盖两张 live 表再验证，且可把行业 index D 与 member D-1 拼成伪“当前快照”。现在 industry index、concept index、member 三路 MAX 必须同日；两张 shadow 在同一事务内完成 raw 双向逐键差集、质量下限、歧义、grain、NULL/unmapped level 校验后才一起 rename，并创建唯一索引。坏源和第一张 rename 后注入故障均回滚 data/schema/index。12 项反例通过；live 只读复验仍为行业 5,204 股、31/128/337 个 L1/L2/L3 节点，概念 66,718 条 membership、5,203 股、494 个概念，克隆库原子发布通过。
- 独立 reviewer 指出 STORE 日志仍声称执行已退役 retention；日志已改为实际执行的“水位/连续性/报告/告警”。正式 lifecycle/retention contract 仍是 Phase 1 缺口，没有用改名伪称已实现。
- CI 与 `start.command` 仍检查已退役 akshare/TDX fallback 并注入 V8 flags，属于 active control-plane 失同步；现已移除旧 provider 依赖/启动叙述，CI 明确只验证离线控制面，真实采集仍由本机受管 TinyShare + 手工入口负责。

## 2026-07-16 现场数据证据

以下为当日只读审计快照，后续使用前需重查：

- `fact_stock_form_daily` 最新日覆盖约 5,117 只、11 种 form；当天 breakout 为 0；
- `dim_stock_segment_daily` 最新日约 5,198 只、31 个申万 L1，存在少量行业缺失；
- market pulse 同时包含 DC 与 SW namespace；正式手工链已原子重建，SW L1/L2/L3 的 `content_type` 现按真实 level 输出，全量 clone 回归同时证明 accepted 历史未倒退；
- DC 源 `level` 是中文字符串；代码由 `taxonomy.yaml` 映射，不再 numeric cast 或按 SW 名称猜层级，live materialization 与 raw latest snapshot 已逐键复验；
- 东财概念是多对多，单票平均关联多个概念，概念净额求和会重复；
- 旧 `v_dc_industry_pit` 只输出 first-seen，writer 已退役；live DB 残留 view 待 Phase 1 只读核验后删除；
- DC 与 SW 按名称映射存在名称和成员差异；新 writer 已去掉名称 JOIN，跨 namespace 只允许未来 evidence crosswalk；
- `fact_stock_form_daily`、market pulse 表缺 definition/config/input snapshot/availability 等版本证据；
- 当前正式决策/持仓闭环为空或近空，不能声称已有生产策略。

## 保留的关键历史证据索引

这些文件暂保留，因为包含不可简单从 git diff 或数据库当前态重算的审计/实测：

已合并而不再单独保留的证据：continuity 尾部缺口已并入
`gap_root_cause_20260708.md`；legacy-flow 与 SERVE 门的 red→green 反例已固化在对应
gate/tests；2026-07-02 产业链温度计设想已被新 taxonomy 架构取代；execution-aware 的
“IC 不等于可交易收益、成本/T+1/停牌/涨跌停必须入模”已收敛进
`docs/strategy_validation_contract.md`。这些内容不再各占一份 owner-like 文档。

| 文件 | 用途 |
|---|---|
| `comprehensive_data_module_audit_20260706.md` | 全面数据模块审计证据 |
| `data_foundation_root_causes_20260703.md` | 数据地基反复修复的根因证据 |
| `d1_gt_archaeology_20260702.md` | 主升浪 ground-truth 定义考古 |
| `edge_builder_pit_audit_20260708.md` | edge builder PIT 审计与机构 availability 证据 |
| `gap_root_cause_20260708.md` | 近期数据缺口 root-cause 证据 |
| `kline_completeness_crywolf_fix_20260624.md` | K 线完整性 verifier 误报修复证据 |
| `market_pulse_design_20260702.md` | 旧 market pulse 设计；已被新架构取代，仅因 frozen sync config 仍有引用暂留 |
| `rally_buyability_gonogo_20260620.md` | 主升浪标签可买入性实证 |
| `data_sources_registry_retirement_20260707.md` | 冻结 sync runner 仍引用的注册表退役证据；Phase 1 迁移引用后复核删除 |
| `r4_completion_20260704.md` | 冻结 sync config 仍引用的字段/域处置证据；非当前 gate 状态 |
| `tushare_alpha_potential_research_20260617.md` | 冻结 sync config 的历史拉取决策；口径已被新架构取代 |
| `非tushare源_双轨_holders_20260623.md` | 冻结 sync runner 的 holder 源选型反例；非当前实现 owner |
| `miaoxiang_aif10_source_decision_20260624.md` | 不可替代的 holder 主源裁决证据 |

非 Markdown 的关键实测证据也保留在 `analysis/`，不属于 active owner：

| 文件 | 用途 |
|---|---|
| `technical_states_audit_20260702.json` | technical state 真实分布、覆盖与形态审计快照 |
| `technical_state_plan_review_20260621.json` | 旧状态方案的结构化对抗审查证据 |
| `technical_state_theory_research_20260621.json` | 阶段/形态理论来源与定义候选证据 |

这些文件不是 active owner。其结论若与 live code/data 或 2026-07-16 owner contracts 冲突，以 live evidence 和 owner contracts 为准。

## 已删除文档类别（2026-07-16）

- `analysis/docs_archive_20260531/` 的 archive-of-archive；
- 旧 constitution、data framework、data product、quickstart 和 docs/archive；
- 2026-07-02 已吸收的 master/data-platform/technical-state/institution/rally 设计稿；
- 其余重复架构、迁移计划、退役说明、旧 handoff/旧索引日志和普通过程叙述。
- 24 个零 fan-in 的 `analysis/` 探索脚本、CSV 和 chain log；可复现过程不再伪装成长期证据。
- 已被全局 Codex skills 取代的 5 个 `.claude/skills/` 本地副本、失效 scheduled-task lock 和空 worktree 目录。
- 只描述 2026-06 旧 Optuna/feature 分库计划、且唯一消费者也无有效运行面的 `db_partition_tiers.yaml` 与 `db_partition_migrate.py`。
- 无执行器却声明 Modal/未来表为 active 的 `experiment_jobs.yaml` 与 Modal 依赖；研究执行重新以 Strategy/Experiment contracts 为准。
- 指向旧插件版本和已删引擎的 agent/status/pre-edit 工具，以及其 `.claude` 项目 hook；本地 Codex 运维不再由仓库脚本冒充。

仍被现行门禁消费的 `data_module_members.yaml` 和 `data_layers.yaml` 已明确标记为过渡期
`NONCONFORMING` registry；它们不是目标模块/业务 Tier 真相源，待 Phase 1 contract manifest
和数据契约接管边界后迁移、收缩或删除。

删除不等于抹去历史：git history、数据库 deletion/audit records、测试和可重建报告仍是证据。重新引用历史方案前必须先证明它仍适配当前架构。

### 2026-07-17—18 — Phase 1 margin Tier0 tracer canary

- 按顶层设计只落地首个 `margin` tracer：typed `DatasetContract`、不可变 schema/hash、
  provider landing、validate/publish/accept、`AcceptedPartition`、accepted-state Ops projection、
  逐分区 shadow reconcile 和唯一 writer；没有扩到第二域、切换消费者或删除 legacy；
- v2 contract 将 publication eligibility 明确定义为
  `axis=trading_day/rule=next_trading_session_at/at=09:00`；schema hash=
  `8935c9be0741707330b7db9fc775a5ec2e71ab24dbf1095124d3d36dcd48f6ff`、config hash=
  `6dd2428b75b66e750efc6a7b252841422a82eb9dbfb4be2188c572c5e4f412be`、contract hash=
  `d65b6510374fbd2c6d79e9836d0ede7bb41cb232a0cd46d909a7682a11137e8f`；transport/batch mode
  不再承担 availability 语义，默认、显式回放和 drain 共用同一 eligibility/window resolver；
- 正式手工入口 `scripts/chunkyctl sync --domain margin --drain --max-dates 2` 在授权、交易日历和
  writer lock 前置门后接收 `20260715—20260716`：两日各 3 个交易所 fragment/3 行 canonical，
  content hash=`ab6703…0a5`、`f47e04…d76`，两分区均 `PARITY`；同入口幂等复跑
  gap/refill/rows=`0/0/0`，未重拉 provider 数据；
- accepted frontier 与唯一 watermark 均为 `20260716/3`，parser=`margin_accepted_contract_2`，
  open margin failure=0、fallback=false、projection drift 为空；SLA probe 对 margin 为
  `OK/verified`。默认 SLA 报告仍诚实保留 3 个无关全局 alert：两项 no-mapping 与
  `margin_detail` stale；
- Rule 10 对抗反例先后证伪并修复九处假绿：Ops watermark/failure queue 改为同一显式事务，
  registry 缺失/畸形或无 query mapping 改为 fail closed；零 accepted gap 仍重验 shadow parity，
  失败连续返回 `partial` 且持久投影、不重拉 provider；`chunkyctl sync` 禁止 `--all-due` 并强制
  单一 `--domain`；默认增量遇到 frontier 之前的 accepted 内部缺口也必须失败并指向 `--drain`，
  不再用最新水位洗白；四个正式事务的 rollback 失败也不再被 `except: pass` 吞掉，而是保留
  主异常与 traceback，并附加“连接状态未知”的 rollback 证据。原聚焦 contract/integration/pipeline
  回归 453 passed；新增四路径故障注入后 `test_margin_acceptance.py` 55 passed，受管 `.venv`
  完整 backend suite 最终 1080 passed/8 deselected。系统 Python 缺 TinyShare 导致的 4 个 import failure
  由同一代码在项目受管 provider 运行时全绿证伪为错误解释器，不冒充代码修复。Moth assertions
  30/30 PASS，CodeGraph current，文档与生成地图 fresh；
- `20260718` 周六跨日审计又证伪共享门：裸 `t+1` 原先在非交易日直接纳入最近交易日，
  drain 又绕过 `eligible_end_date` 自算 `<today`，且 `empty + not_attempted` 被连带跳过状态覆盖成
  `FRAGMENT_FAILED`。生产探针以 `20260716/SZSE=1` 为控制，`20260717` 实测 SSE=1、SZSE=0、
  BSE=0；两次 v1 尝试均完整落为 immutable `REJECTED`，accepted/legacy/watermark 零变化。
  正式 margin 随后经公共 `--backfill --start 20260715 --end 20260716` 重发 v2 两日/6 行；当前
  accepted pointers 全部指向 v2，旧 v1 accepted/rejected/landing 只保留历史且 current matches=0，
  canonical/legacy 内容 hash 不变、reconcile 仍为 `PARITY`。公共 drain 复验 expected=2、
  gap/refill/rows=0，普通 sync batches/rows/failed=0、eligible_end=`20260716`、零 provider call；
  显式未来 end 和注入未来 drain 都在 adapter/DB/writer 前拒绝，历史 end 仅限制操作窗口而不伪造
  实时 eligibility；
- 最终对抗审查继续用反例推翻入口绿灯：单次执行现在从同一 registry snapshot 只派生一个
  immutable contract 对象，并沿 runner、accept/recover、state、reconcile/projection、pipeline、
  continuity 与 SLA 以对象 identity 透传；publication calendar/cutoff validator 下沉到
  `margin_validation`，消除 state 对 acceptance 私有 helper 的反向依赖。accepted pointer 先做本地
  contract identity fail-fast，再只读一次 calendar snapshot 重证所有 current partition 的 cutoff；
  continuity 同样消费 typed policy，`20260718` 周六反例只要求 `20260715—16`，不再用裸 `t+1`
  误纳尚未发布的 `20260717`。formal transport 对 batch mode/date param/write mode/split param/全生命
  周期 group 集合及重复、小写、空值做副作用前验证；drain 混用 replay flags、on-demand 缺单侧边界、
  full-refresh 带日期边界和所有 future window 也在 calendar/lock/auth/provider/DB 前稳定拒绝。
  最终正式入口普通 sync 返回 batches/rows/failed=`0/0/0`、eligible_end=`20260716`；drain 返回
  expected/gap/refill/rows=`2/0/0/0`。单域 strict continuity 为 PASS（2 pass、1 个因仅两日而
  skipped），SLA dry-run 对 margin 为 `OK/verified`、projection drift 为空，同时保留 3 个无关全局 alert；
- registry 中另有 16 个裸 `available_after=t+1` legacy 域，覆盖 trade-date、ann-date、period 和
  by-security 四类传输面。它们保留迁移前行为，未被 margin 的 trading-day 语义批量重定义；后续须
  逐域声明 typed axis/rule/clock 并版本化，不能再从 `batch_mode` 推断 publication availability；
- 数据更新继续 `manual_only`；只读现场核验无 ChunkyMonkey cron/launchd/launchctl/installer。
  provider 授权开通 `2026-06-17T10:48:58+08:00`、到期 `2026-08-12T15:43:00+08:00`；
- 本 canary 不证明全史闭合：正式 accepted coverage 仅 2 日/6 行，legacy 为 1827 日/4485 行；
  market-pulse 仍读 legacy 且未随本 rollout 重建。全史前还要批量化约 `6 + 13N` 查询路径，
  第二正式域前要先抽 runner 的 outcome-to-loop policy，禁止继续复制 dataset-specific 分支；
  并另行治理 legacy `source_watermarks` standalone helper 的非原子 failure-queue 更新。全局 strict
  continuity 另判 `cyq_perf` 超 SLA，而 SLA 报告仍标 `OK`，属于后续 Ops verifier 口径残留，
  不得用来反向抹掉本 canary 的 accepted/parity 证据，也不能在未修前声称全局 READY。

### 2026-07-18 — margin full-history read preflight

- 将 accepted pointer、batch、landing、canonical 和 legacy comparison 收敛到一个 immutable
  `MarginEvidenceSnapshot`；schema inventory 加五个 set-based surface read 固定为 6 条主库
  `SELECT`，不构造随 N 增长的 `IN`，删除 public reconcile 的逐分区 SQL fallback 和 proof 注入面；
- N=1 与 N=20 的 projection/readiness 均实测 `(6, 6)`；交易日历由一次规范化索引加逐分区
  `bisect` 取 successor，1827 分区对抗测量的 `_compact_date` 调用从 3,341,583 降至 3,655，
  cutoff 循环从约 11.106 秒降至约 0.019 秒；乱序、重复和非法索引构造均 fail closed；
- Rule 10 多轮反例进一步闭合 scope 内容夹带、schema query 与 schema drift 混型、landing
  row-hash/request/ordinal 丢失、自洽 premature publication、坏 B 污染好 A、calendar read 异常逃逸、
  public `_accepted_proof` 绕过和旧 accepted state 与新 snapshot 混代；最终跨连接反例又证明旧
  clean snapshot 可把已污染的新连接洗成 `PARITY`，因此所有 authoritative public reconcile、
  accepted-state/readiness 入口均删除 snapshot/state 注入，只有同一调用栈的私有 helper 可复用现场
  snapshot；正式 schema 缺列仍稳定返回 `SCHEMA_MISMATCH`，真实查询故障返回 `QUERY_ERROR`；
- `data/tushare_raw.duckdb` 以 `read_only=True` 复验：accepted=`20260715/20260716`、canonical=6，
  projection/readiness 各 6 条主库查询，两分区均 `PARITY`，missing/unexpected/reconcile failure 全空；
  reconcile 同样固定 6 条查询；readiness orchestration 上移为独立模块，依赖图收敛为
  `readiness -> reconcile/state`、`reconcile -> legacy/state`，不再靠局部 import 隐藏循环；结构拆分后
  新增大文件门恢复为 3 个既有例外，Moth assertions `30/30 PASS`，完整 backend suite 为
  `1107 passed / 8 deselected`。本切片未写主库、未拉历史、未切消费者、未扩第二域；它只解除受控
  历史 rollout 的读取规模前置阻塞。

### 2026-07-18 — margin history migration grill evidence

- 全史目标快照为 `20190102—20260716` 共 1827 个交易日、legacy 4485 行；按当时只读生产
  分布，`20230213` 起 831/831 日含 BSE，之前 996 日只有 SSE/SZSE。计数是审计快照而非稳定
  配置；active registry 只保存北交所两融业务生效日与两/三市场分段规则。

### 2026-07-18 — margin history execution gate

- 正式 `margin` history 入口现要求显式 start/end/max-dates，registry cap=20；typed
  request/plan/result、oldest-first、accepted+PARITY skip、精确 LANDED 恢复、compare-before-publish、
  首错停与稳定 evidence hash 已进入唯一 `chunkyctl sync` 路径。accepted checkpoint 绑定
  batch/row/content hash，LANDED 绑定 batch/payload hash；冲突只保留 landing，不覆盖 legacy；
- 所有 selected domain 的 provider timeout 都从同一 registry snapshot 静态验证；缺失、类型错、
  非正数、NaN/Inf 与超过平台 `threading.TIMEOUT_MAX` 的值均在 calendar、writer lock、授权、
  adapter 和 target DB 前失败，history 内另保留防御性复核。合法旧测试 fixture 只补同一配置，
  未放宽缺失/非法红例或业务断言；
- 只读全史 dry plan 固定 contract hash=`d65b6510374fbd2c6d79e9836d0ede7bb41cb232a0cd46d909a7682a11137e8f`、
  config hash=`6dd2428b75b66e750efc6a7b252841422a82eb9dbfb4be2188c572c5e4f412be`、
  plan hash=`79f0aed1fc29afb2c20eba383c1e284d47377f7876e7213175812a8557da2376`；
  1827 日中 1825 日待迁移、2 日 skip，`--max-dates 1` 只选 `20190102`，其余 1824 日 deferred；
- 最终同步/保证金广域回归 `383 passed`，完整 backend suite `1215 passed / 8 deselected`；
  Feature Map 连续两次 fresh、Moth `30/30 PASS`、CodeGraph current、`git diff --check` 通过。
  本门验证未调用 provider、未写 live DB、未安装自动任务；下一步只能先跑 `20190102` 单日 canary，
  成功后再跑 `20230213` BSE 分段边界，不能跳到全史或 consumer cutover。

### 2026-07-18 — margin history rollout and universe-scope superseding verdict

- 正式公共入口先后验收 `20190102` 两市场 canary（batch=`margin:20190102:03c4ff2892364cd1ae905ac58473df7a`、
  rows=2、content hash=`7be443ff…ebcfc`）与 `20230213` 三市场 canary
  （batch=`margin:20230213:87a2ad26b3ba4e188693c9142e1ad3d5`、rows=3、content hash=
  `c82b907c…640f`），随后按 cap=20、oldest-first、首错停回放。主库只读复验为 accepted=
  `1823` 日/`4473` 行、canonical=`1823` 日/`4473` 行、legacy=`1827` 日/`4485` 行；消费者与
  frozen v2 contract/config 均未切换；
- 首错 `20260709` batch=`margin:20260709:5332c102407841a4b14896b1b69a5494` 保持 `LANDED`：
  expected/completed/failed fragments=`3/3/0`、landing rows=`3`、payload hash=`7f807b1d…6c47d`。
  candidate hash=`12b5ec4c…d65eb`、legacy hash=`09bb6d40…32ba4`；唯一 issue 为
  `(20260709,BSE,rqmcl)` 的 Tushare `NULL` 对 legacy `0`，未写 canonical、accepted 或 legacy；
- 北交所官网同日 summary JSONP 六个重叠字段与 legacy BSE 行一致，raw SHA256=
  `d12f07e6bd4bda103a4de5c51db9b18957f8fc4708f52dfc54f762db04b10311`；官网 XLS 的
  “融券卖出量（股）”同为 `0.00`，SHA256=`602fce61ea0595deed1bf0a51d8c8d905f0ba63591e6b21dcfedef8a0f3c40fa`。
  这只证明 provider observation 有矛盾，不授权覆盖 frozen v2；
- 用户重申 BSE、新老三板、ST、退市属于与交易日历同级的排除门后，controller 对 registry、代码、
  live DB 和 consumer 做了重新证伪：47 个同步域中 30 个声明 `universe_filter`，仅 6 个
  `by_ts_code` 在请求前走较完整当前 universe，24 个只做前缀过滤；formal margin 无过滤且从
  `20230213` 起硬性要求 BSE。`check_universe_filter.py --all` 对 `*ST` 坏例仍放行并报告
  `CLEAN (1103 files)`，Moth assertion 只检查 gate symbol 存在，均为 false green；
- live 影响：formal canonical 已含 BSE 827 行；market pulse 830 日把 BSE 计入两融总额并有 4 日
  改变沪深日增方向；`raw_tushare_daily` 最新日有 208 只 PIT-ST，855 个 pulse 日中 854 日的
  涨跌广度会因剔除 ST 改变，龙虎榜最新 74 只中有 7 只 ST，SW/DC 成员与下钻也有同类泄漏；
- superseding verdict=`BLOCK v2 rollout / PROCEED population-scope correction`。问题不是再补四日，
  而是 transport completeness 被误升为 business canonical scope。BSE 专项裁决原型未 apply、未提交、
  未重拉 provider，随后从 worktree 删除；旧 batch/landing/canonical 只作不可变错误-scope 证据，
  不得继承为 full-coverage generation 或切消费者。

### 2026-07-18 — system-upgrade checkpoint: universe gate reconstruction

- Formal policy is now factory-owned and semantically rehashed. Its daily rule is
  `traded_on_observation_date`; the 90-day heuristic remains only in legacy current enumeration.
  The production scope binder rejects an exchange-grained aggregate relabelled as a security-grained
  project universe and requires `ts_code`, observation-time partition anchoring and trading-day availability.
- The recent-window resolver was rejected and removed. The accepted design requires one read snapshot containing
  versioned calendar generation, exact-date nominal Kline and exact-date ST proofs, with contract-derived
  availability/completeness and positive provider-envelope evidence. Red-team counterexamples proved that
  self-consistent hashes alone can launder future partitions, permission pages and incomplete calendars; that
  implementation was deliberately withheld from this checkpoint rather than patched into another false green.
- The old margin history request/runtime/writer/CLI modules and their obsolete self-contained tests were
  physically removed. The retained v2 surface is read-only evidence/state/reconcile/projection; the supported
  margin CLI exits `execution_blocked / scope_blocked` before provider or DB access, and the residual acceptance
  mechanic refuses the live `tushare_raw.duckdb` by verified DB identity before schema or DML.
- Static population verification now distinguishes live worktree from Git index, includes untracked production
  source in worktree mode, removes the invalid “any Kline scan is wrong” regex and shape-mutation bad-case count,
  and reports `live_readiness=NOT_EVALUATED`. Full backend regression before final withdrawal was green; focused
  doctor/population/safe-commit gates also pass, while live doctor intentionally returns overall FAIL.
- Status remains `PARTIAL/BLOCKED_FOR_DATA_USE`: only one formal dataset exists and it is the disabled external
  margin aggregate. Accepted calendar/Kline/ST source contracts, DB loader/writers and a read-only/live canary
  remain after the OS upgrade. No provider call or live DB write occurred.

### 2026-07-19 — system-upgrade checkpoint: shared acceptance primitive and calendar rejection

- `ingest_batch` and `accepted_partition` fixed DDL/constraint verification moved from the margin-specific schema
  into one shared accepted-evidence primitive. Margin keeps the same public table constants, four-table creation
  order, error type and atomic rollback behavior. The shared verifier now pins one current DuckDB catalog/schema;
  adversarial attached databases with same-named tables can neither lend missing constraints nor contaminate a
  valid target-table verdict. The new red cases and margin rollback compatibility test are part of mandatory CI.
- A proposed calendar contract was deliberately deleted after Rule 10 `REQUEST_CHANGES`: it called the mutable,
  open-day-only `dim_trading_calendar` an immutable full generation even though the real builder deletes closed
  days and updates the table in place. The proposal also failed to bind full-refresh/write mode, completeness,
  pagination and availability, and its YAML self-declared code topology without proving runtime consumption.
  The accepted design therefore remains unchanged: preserve open and closed provider dates in an immutable
  accepted generation, bind source batch/content/completeness/availability/accepted-time evidence, and treat the
  existing dim table only as a serve projection.
- Controller verification after the correction: shared/margin focused suite `67 passed`; wider accepted/margin/
  existing-calendar regression `377 passed`; the exact CI offline list `364 passed`; full backend suite
  `1180 passed / 8 deselected`. Independent Rule 10 rerun ended `APPROVE`; Moth assertions `30/30 PASS`,
  CodeGraph current, doc governance/drift and `git diff --check` passed, and the rejected calendar files are absent.
  `chunkyctl doctor --fast` intentionally remains overall `FAIL` only because `population_readiness` is
  `NOT_EVALUATED`; static population contract, manual-only automation and current data-health sections pass.
  No provider call, live DB DDL/DML, automation change, consumer cutover or data publication occurred. Tier0
  remains `PARTIAL/BLOCKED_FOR_DATA_USE`, and the restart point is calendar accepted-generation writer/reader
  design rather than another wrapper around the old dim.

### 2026-07-19 — system-upgrade checkpoint: isolated calendar generation prototype

- A second, deliberately non-production checkpoint now preserves the first end-to-end calendar generation
  prototype: typed contract parsing, fixed landing/canonical schema, fragment-preserving Tx-A, atomic Tx-B
  acceptance and a fail-closed trusted reader. The old `trade_cal` execution path is explicitly disabled with
  `accepted_generation_pending`; the legacy `raw_tushare_trade_cal`/open-only `dim_trading_calendar` remain
  compatibility evidence and are not accepted predecessors or publication truth.
- Read-only source and DB audits established the target semantics without promoting old data: SSE source evidence
  currently contains 13,162 natural-day rows from `19901219` through `20261231` (8,797 open and 4,365 closed),
  while the dim is only a 5,343-row open-day projection from `20050104`. No accepted calendar batch or pointer
  exists. The new generation must therefore preserve open and closed days, bind complete pagination and the
  pretrade chain, and be freshly fetched only after its writer and reader pass review.
- The focused calendar/shared/execution-policy suite reached `109 passed`; service tests also exited successfully,
  `py_compile` and `git diff --check` passed. No provider call, live DB DDL/DML, accepted publication, automation
  change or consumer cutover occurred.
- The first checkpoint review found a cross-entry false green: direct `run_domain("trade_cal")` blocked before side
  effects, but full acquire could write holders/QFII/org-holding before its all-due child encountered a disabled
  domain; calendar repair could also issue the account authorization probe first. The exact all-due selection,
  execution-policy gate and formal-population gate are now shared public pure preflight helpers. Full preflight,
  direct acquire and independent acquire stage invoke them before calendar repair, authorization, provider, DB or
  writer work. Three adversarial fan-in tests failed before the fix and now pass; the combined calendar,
  sync-runner and pipeline regression is `264 passed`, and the full backend suite is
  `1270 passed / 8 deselected`. Final reader cleanup hardening separately reached `15 passed`: invalid dates no
  longer fall through silently, cleanup failure blocks a normal return, and rollback failure adds evidence without
  masking an in-flight `CalendarTruthUnavailable`, generic error or `BaseException` cancellation.
- Independent reuse and quality reviews both returned `REVISE`. Blocking findings are: the formal contract still
  binds legacy target/write-mode fields; physical schema and semantic contract hashes are conflated; availability
  is not yet a typed `axis/rule/at` object; runtime writer entrypoints bootstrap DDL before validating input;
  `dataclasses.replace()` can forge a nominal contract; population scope and fixed-schema/inventory verification
  are duplicated; and the second accepted dataset has exposed reusable state/pointer mechanics that need a narrow,
  non-universal boundary. This checkpoint is source preservation only and remains `BLOCKED_FOR_DATA_USE`.
- Exact restart order: make the contract factory-owned and independently re-verifiable; split formal transport and
  publication topology from disabled legacy compatibility fields; keep the schema hash physical and assemble one
  normalized semantic payload from one registry snapshot; reuse `ExternalAggregateScope` and shared fixed-schema
  inventory; move DDL to explicit bootstrap; add forged-contract, missing-schema-with-no-DDL and descending-page
  red cases; then rerun Rule 10, post-fix audit, full backend, Moth and CodeGraph before any fresh provider canary.
- Post-fix read-only residue verification found no calendar landing/canonical tables, no calendar ingest batches and
  no accepted calendar pointers in live `tushare_raw.duckdb`; no calendar/sync pipeline process was running. No DB,
  process, cache or consumer cleanup was therefore authorized or required for this source-only checkpoint.

### 2026-07-19 — 整体方案立法 + Phase A1 calendar factory attestation

- 业主拍板：多源=契约可换 adapter；首策略包=`institution_follow`；边做边测。权威文档已改：`goal.md` A→H、
  `MASTER`/`strategy_validation_contract`/`engineering_governance`/`PROJECT_INDEX` 对齐；doc-governance PASS。
- Phase A1（PARTIAL）：`CalendarGenerationContract` 改为 factory-only（禁 `__init__`/`dataclasses.replace`）；新增
  `verify_calendar_generation_contract` 重算 hash；`calendar_landing._contract` 强制 attestation。对抗测：
  直构/replace/字段篡改变红；`test_calendar_contract|acceptance|reader` = `78 passed`。
- Reader 测试 `_land_and_accept_real_generation` 同步冻结 land/accept 时钟，避免与 fixture `FIRST_ACCEPTED`
  同日墙钟导致 `time_chain` 假红。
- 未做：`DatasetExecutionContract` 传播出口、calendar live accepted、resolver、landing 纯度迁移、provider/DB 写。
  Tier0 仍 `BLOCKED` / `NOT_EVALUATED`。
- CI 根因（run 29667261457）：DuckDB timestamptz 需 `pytz`；dead-references 扫 `services.rally_gt` 时缺
  `pandas` 被误报为死引用。`.github/workflows/ci.yml` 离线依赖补 `pytz pandas`。
  后续 push `0b40e07d` CI run `29686700408` = success。

### 2026-07-19 — Phase A1 DatasetExecutionContract 传播闭合

- A1 **complete**（非 PARTIAL）：`bind_execution_contract` 出口 `verify_execution_contract`；新增
  `formal_execution.propagate_formal_execution_contract` + margin consumer
  `receive_margin_execution_contract`；`require_same_execution_contract` 证明 consumer 收到同一对象（`is`）。
- `sync_runner._require_formal_population_execution` 绑定后强制 handoff；成功则 `run_domain`/`drain_domain`
  走 `_refuse_formal_domain_runtime`（margin=`formal_runtime_retired`），不再假墙
  “bind 后永远 `execution_contract_not_propagated`”。无注册 consumer 的 formal 域仍该 reason。
- margin 仍 `scope_blocked` / live-write frozen；未做 provider fetch、consumer cutover、calendar live accepted。
- 对抗测：`test_formal_execution` + `test_population_scope` verify/identity + `test_sync_execution_policy`
  = `69 passed`。Tier0 仍 `BLOCKED` / `NOT_EVALUATED`；下一刀 A2。

### 2026-07-19 — Phase A2 calendar accepted live-capable path

- A2 **FIXED/complete**（代码路径；非 live 发表）：
  - `CalendarAvailabilityPolicy` typed `axis/rule/at`；禁 naked `availability_rule` 字符串。
  - config/contract hash 绑定 formal publication tables（landing/fragment/canonical），legacy
    `target_table`/`write_mode` 仅 compatibility 字段、不进 publication identity。
  - `land_calendar_batch`/`accept_calendar_batch` 不再隐式 DDL；input 校验先于 schema verify；
    bootstrap 仅 `ensure_calendar_acceptance_schema` / `calendar_runtime.bootstrap_*`。
  - 新增 `calendar_runtime.publish_accepted_calendar_generation`；`refuse_legacy_calendar_raw_write`；
    sync_runner 即使翻 enabled 也禁 legacy raw 落穿。
  - `dim_trading_calendar` 明确 `serve_projection_open_days_only`；dim+raw  alone → reader
    `NOT_EVALUATED`，不得冒充 accepted generation。
- 对抗测：`test_calendar_{contract,acceptance,runtime,reader,schema}` + `test_sync_execution_policy`
  = `120 passed`。未做 provider fetch、live DDL、consumer cutover。Tier0 仍
  `BLOCKED` / `live_readiness=NOT_EVALUATED`（缺 accepted K/ST）。下一刀 A3。

### 2026-07-19 — Phase A3 traded_on_observation_date resolver (PARTIAL)

- A3 **PARTIAL**：新增 `observation_population.py`：
  - trusted loaders：calendar→`open_calendar_truth`；nominal K/ST → fail-closed
    `NOT_EVALUATED`（`accepted_writer_pending`，禁 raw/dim/qfq 冒充）。
  - `resolve_traded_on_observation_date`：开市 ∩ 名义K 成员 − ST − 非白名单 board；
    拒未来 observation、不可见 partition、0 行 K。
  - `evaluate_observation_population_readiness` 接入 `check_universe_filter`：
    `live_readiness` 经真评估（当前 live 常见 `BLOCKED`：calendar schema 不完整 + K/ST
    writer 未建），不再硬编码常量。
- `universe_rules.yaml` policy **v3**：`trading_calendar` =
  `tier0.reference.sse_trading_calendar_generation`（对齐 A2 dataset id）。
- 对抗测：`test_observation_population` + universe/population_scope/check_universe_filter
  相关 = `84 passed`。
- **BLOCKED residual（需后续或授权 canary，非本刀）**：名义 K / ST accepted partition
  writer+schema+live 发表未建；无 provider mass fetch。下一刀 A4 landing 纯度。

### 2026-07-19 — Phase A4 landing purity (FIXED)

- A4 **FIXED**：`sync_runner._prepare_batch_df` 写前不再按 `universe_filter` 删行；仅校验
  filter_col 像证券代码（防配错列）。新增 `universe_serve_filter.apply_universe_serve_filter`
  在 canonical/serve 过滤并记录 policy_id/version/hash + exclusion reasons。
- `batch_integrity.complete_batch_dates` / continuity gap 口径改为全量 landing 人口
  （BJ 计入 min_rows），与 serve 过滤解耦。
- 对抗测：`test_universe_serve_filter` + batch/sync/continuity 相关更新绿。无 provider
  fetch、无 consumer cutover。下一刀 A5。

### 2026-07-19 — Phase A5 formal adapter/landing/canonical boundaries + Phase A exit

- A5 **FIXED**：新增 `formal_boundaries.py` — inventory 声明 margin/trade_cal/daily/stock_st
  三界（adapter=tushare only；landing/canonical writer 路径；runtime_state）。
  sync_runner 对非 `writers_pending` 域硬墙 legacy `_write_batch`；`_adapter` 只许 tushare。
- daily/stock_st 仍 `writers_pending`（临时 legacy 路径，A3 residual）。
- **Phase A 代码出口**：A1–A5 对抗测绿；`live_readiness` 经 loader 评估（live 常见 BLOCKED：
  calendar 未 bootstrap + K/ST writer 未建）。未做 provider mass fetch、consumer cutover；
  margin 仍 scope_blocked/frozen。下一阶段 B。

### 2026-07-19 — Phase A3 residual closed (FIXED) + Phase A honest complete

- A3 **FIXED**：名义 OHLCV / same-day ST accepted writers 落地：
  - shared `security_day_partition` Tx-A/Tx-B（landing→validate→canonical replace→
    `accepted_partition`）；域模块
    `nominal_ohlcv_{schema,contract,acceptance,runtime,reader}` +
    `stock_st_{schema,contract,acceptance,runtime,reader}`。
  - dataset ids 对齐 UniversePolicy：`tier0.market_data.nominal_ohlcv_daily` /
    `tier0.security_identity.stock_st_daily`；population=`raw_evidence`；
    availability=`trading_day/same_day_at`（18:00 / 09:20）。
  - `observation_population` trusted loaders 改读 accepted reader；无 live partition
    仍 fail-closed（`NOT_EVALUATED`/`BLOCKED`），禁 raw/qfq/dim 冒充。
  - `formal_boundaries`：daily/stock_st → `accepted_runtime_ready_canary_pending`；
    legacy `_write_batch` 硬墙。registry sync `execution_policy.disabled` /
    `accepted_partition_pending`。
- 对抗测：`test_nominal_ohlcv_acceptance`（publish/premature/kill-point/reader/
  resolver e2e）+ formal/observation/sync 相关绿。未做 provider mass fetch、
  live DDL bootstrap、consumer cutover。
- **Phase A 代码完整**：A1–A5 均 FIXED。残余仅 data-plane（calendar/K/ST live
  accepted partitions + 授权 canary）。下一阶段 B。

### 2026-07-19 — Fable5 scheme review REVISE absorbed

- Verdict **REVISE**（agent `dc8394ca-3077-41d7-b392-af9f2f7dcde1`）：MASTER/goal
  「当前仅接入 TuShare」与实况矛盾 — `holders_aif10`/`miaoxiang` aif10 为披露域
  live 主源且直写 fact（无 landing/accepted）。adapter-only 裁决保留为**目标态**。
- 方案修订落地：goal/MASTER 拆 **B-ext** / **B-pit**（B-pit 闸在 A3 data-plane）；
  新增 **E0** 披露域 formal 化（E 硬前置）；strategy §8.1 补 NULL `notice_date`
  契约排除 + t 日 universe EOD/`decision_time` 语义；PROJECT_INDEX 登记
  NONCONFORMING 披露源与 `boundary_inventory` 非 readiness 证书。
- Resolver 对抗缺口关闭：accepted ST 零行 fail-closed；`row_count`↔membership
  基数 parity。未开工 institution_follow 生产、未 mass fetch；margin 仍冻结。

### 2026-07-19 — B-ext slice1: market_pulse_scope UNTRUSTED attestation

- **PARTIAL**：新增 `market_pulse_scope.attest_market_pulse_scope` —
  legacy mart 字段 `adv_dec_ratio`=`raw_evidence` UNTRUSTED；
  `rzrqye`/`rzrqye_chg`=`external_aggregate` UNTRUSTED（含 BSE 注记）。
  `refuse_project_universe_claim_for_legacy_pulse` 硬墙。
- 对抗测：`test_market_pulse_scope` 4 passed。不改 mart 列/router payload、无
  consumer cutover、无 provider fetch。下一刀：shadow reconcile + 读面旁路
  trust 字段（仍不切数值）。

### 2026-07-19 — B-ext slice2: pulse shadow reconcile + API trust sidecar

- **PARTIAL（B-ext 未宣称 FIXED）**：`market_pulse_shadow_reconcile` —
  BSE 进 legacy sum → `SCOPE_MISMATCH`；SSE+SZSE-only →
  `EXTERNAL_HONEST_SHADOW`；`cutover_allowed` 恒 false；永不因
  `project_universe_available`  alone 放行。
- `/api/v3/pulse/sentiment` 旁路 `population_scope` + `cutover_allowed=false`；
  `days` 数值不变。
- 对抗测：`test_market_pulse_shadow_reconcile` 5 +
  `test_sentiment_v2_fields` trust 断言。无 mart rewrite、无 cutover、无
  mass fetch。下一：前端/读面消费 trust；B-pit 等 A3 data-plane。

### 2026-07-19 — B-ext slice3: API shadow_reconcile + UI untrusted labels

- **B-ext FIXED（诚实化）**：`/pulse/sentiment` 增加只读 `shadow_reconcile`
 （有 `tr.raw_tushare_margin` 时做 venue 对比；无 attach fail-closed）；
  MarketPage 脉搏/情绪卡标注 UNTRUSTED 与「交易所汇总/raw」，不改数值。
- 对抗：`test_sentiment_v2_fields` shadow BLOCKED + cutover false。
  残余：mart 仍发错误 scope 数（待 B-pit cutover）；生产 shadow 可能
  `margin_raw_not_attached`。

### 2026-07-19 — B-ext slice3: shadow on sentiment + frontend trust + A3 canary BLOCKED

- **B-ext PARTIAL（代码面收口，非 FIXED）**：
  - `/api/v3/pulse/sentiment` 增加 `shadow_reconcile`；生产 pulse conn 无 `tr`
    时尽力 `ATTACH … READ_ONLY`，失败记 `margin_raw_not_attached`（fail-closed，
    不编造 PARITY/cutover）。
  - 前端 PulseBand/SentimentCard 消费 `population_scope.overall_status`（及
    shadow verdict）做 UNTRUSTED 标注；KPI 标签标明 raw / 交易所汇总。
  - 对抗测：`test_market_pulse_shadow_reconcile` + `test_market_pulse_api` =
    20 passed（含 shadow BLOCKED on latest fixture day）。
- **A3 data-plane residual 实测 BLOCKED（未跑 canary）**：
  - registry：`trade_cal`/`daily`/`stock_st` = `execution_policy.mode=disabled`；
    `margin` = `scope_blocked`。
  - live DB：无 calendar/K accepted landing·canonical；`accepted_partition`=
    1823 仅为冻结 margin；`raw_tushare_stock_st` 仍是 legacy raw。
  - 精确 blocker：缺 **authorized** narrow canary（须显式放开
    `execution_policy` 或等价授权）+ 缺 live accepted calendar generation。
    本 session 不 mass backfill、不解冻 margin、不切 B-pit 数值。
- 未做：E0/institution_follow 生产、consumer cutover、provider fetch。
