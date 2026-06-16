# PROJECT_INDEX.md — Chunky Monkey v2 项目地图 (context-only briefing)

> 用于防止对话压缩 / context 丢失导致重复发现项目结构 / 误解数据资产.
> 内容是**项目地图**, 不是规则 — Codex 规则在 `AGENTS.md`; 当前阶段计划在薄入口 `goal.md`; 历史状态/已完成证据在 `analysis/project_state_ledger.md`; `SESSION_HANDOFF.md` 是生成恢复快照; durable contract 在 `docs/README.md` 指向的 active docs; `CLAUDE.md` 是 legacy Claude-specific history.
> 2026-06-05 起，旧 GCP / GCS / phase5 monitor / cost tracker 条目只作历史证据，不是可恢复执行面。当前长任务/花钱任务必须走 `backend/config/experiment_jobs.yaml` + `scripts/chunkyctl jobs`，`local` active，`modal` planned/blocked。
>
> **目标**: 新接手 (无论 Claude 还是人) 读完此文档**不用看代码 / 不用查 DB** 就能理解:
> 项目业务 / 架构 / 技术路线 / 数据资产 / 当前进度 / 已知坑 / 常用操作.

最后更新: **2026-06-06** (TuShare no-persist exact-flow probe wiring + need_027 probe diagnostics hardening + storage retention owner/consumer policy contract + data-source capability router contract + need_027 candidate validation metadata + provider-neutral experiment job contract + execution-surface audit + retired GCP execution surface removal + architect-controller skill install + verify-verifier rule + Moth complexity path normalization + local complexity baseline refresh + data-health dry-run read-only fix + Moth evidence path sync + design-review preflight machine gate + Moth registry instruction-source sync + after-close data refresh + controller-agent preflight hard gate + retention dry-run inventory + storage payload cap recalibration + DB manifest attach policy + DB boundary static gate + holder replay safety + Codex instruction-source boundary + DuckDB capacity audit + need_027 exact-flow probe gate + stage-opt supply/readiness/schema contract + stage-opt signal-date K-line coverage evidence + stage-opt source-aware density diagnostics + stage-opt source freshness/window diagnostics + iFinD MCP research-only routing recheck).

## [INDEX] 最近增量 (只留 7 天, 历史在 analysis/project_index_changelog_archive_20260611.md + ledger)

- **2026-06-16 重启 (清探索污染 + 立方法论 owner)**: 用户决议清掉本轮无锁方案的 alpha 探索污染重新开始。**精准删除** (保数据底座+基础设施改进): DB 清 experiment_store 留档行 / drop feature_store L2 探索面板(8.17M)/缓存+0行表; 删本轮 16 个 alpha 探索 runner + episode 引擎 + 49 个 analysis 验证结果 json + 11 探索设计稿 + 探索方法论 doc + 4 探索 config + consumer_alpha family + 6 探索 moth 断言。**保留**: sync 限流修复 / tushare catalog(241接口) / mio 收编 / G2-G3 治理 / 全数据底座(raw/dim/K线/财报/行业/serving)。**立权威方法论** `docs/alpha_discovery_methodology.md` (用户口述监督式范式: 裸K线扫主升浪>60% / MACD episode>30% = ground truth → 入场点 PIT 因子逐层叠 → 分层 → train≤2025-06/OOS→2026-06 → Modal; 高积分高价值因子优先 hk_hold/stk_holdertrade/moneyflow_dc 等)。cyq 实测与 tushare qfq 同复权坐标可用(C0 FAIL=审计比错基准非数据错), 本地 2023+ 用 2018 需回填。 **耦合检查工具** `moth coupling` (引擎全局子命令) (用户: 删除暴露 表↔代码↔配置↔DB↔文档↔测试 耦合): --impact <name> 删前看 fan-in 爆炸半径 / 默认扫孤儿引用 (pytest --co 真实 collection 崩 + moth 文件悬空) → moth 断言 coupling-no-orphan-refs。CI 修复: 删 experiment 脚本漏删的 2 孤儿测试 (collection 崩根因)。方法论并入 MASTER §5 (docs 11→10)。 CI 第3处: ci.yml 硬编码测试清单/family 断言悬空 (修+耦合工具 T5)。CI 第4处: 误删 formula_search_spaces/candidates config (被保留优化层 plan_validator/features 消费, 非探索) → 恢复; consumer_alpha_matrix/phaseD_search_space 真探索仍删。本地全量 CI offline 91/91 passed。

- **2026-06-12 晚批: 调度手动化 + chain7 善后修复 + Opus 工具线 (sherpa/moth)**: (1) **自动调度退役** (用户决议): daily-update/concept-snapshot launchd bootout (plist 归档 backend/scripts/launchd/), nightly-data-audit 保留作提醒线; `routers/ops_manual_run.py` (/api/v3/ops/jobs*, job 注册表范式) + 工作台手动按钮, 7 单测+实弹冒烟。(2) **chain7 善后三修复**: `_by_ts_code_batches` ATTACH market 根治 fina_mainbz 0 行 bug; `_warn_if_clamped` 日历 clamp 显式告警; registry 手术 9 处 (top_list/top_inst data_start 20180102 范围决策 / daily+adj_factor min_rows 3000 防 2019 年白跑 / moneyflow_ind_dc 50 / dc 系 page_limit 5000 防截断), 防回退测试 5 个钉死决策值。(3) **Opus 工具线**: moth assertion-pack 引擎 (声称-实况对账, `.moth/assertions/claims.yaml` 8 断言) + `moth assert` 快速子命令; **sherpa v0.1 新项目** (~/Documents/M/sherpa, github dare2live/sherpa) — `sherpa takeover` 接手对账器 (.sherpa/takeover.yaml 6 节) + `sherpa gates` 实验 go/no-go (lhb_exit/lf_v0 两包, 门柱进 YAML 防口头放宽)。(4) 概念域单源化 (东财 dc 系唯一, THS 出局, E7 快照退役) + 总指挥作战图 `analysis/master_plan_20260612.md` + Opus 硬化纲领 `analysis/opus_handoff_hardening_20260612.md` (17 项 P0-P2)。(5) 手动 daily_update 实弹: PK 修复后链路全程零 degraded; Step 2.95 drain 网关晚高峰 ~3 调用/分钟僵慢 115 分钟后定点终止 (step_degraded 按设计降级, gap 扫描可恢复), 误配失败单重试风暴 = min_rows 修复的实证现场。(6) **日历扩展执行**: dim_trading_calendar 969→5,343 行 (2005-01-04 起), 七项验证 PASS; claims/realdb 双断言收紧防回缩。(7) **chain9 发射** (20:58): 探底确认 top_list 2018-2020 可得 (44/67/52 行) + dc_member 地板=2024-12 月末 (10 发双确认); LHB 四件套→2022 段→dc_member 重拉→drain 转正→7 域补丁→fina_mainbz, 链尾 sherpa gates+moth assert 自验。(8) **CM_TDX_SERVERS 活池改独占** (xdxr 全军超时根因: 死池拖尾被请求级轮转抹平排头优势, 32 路并发冷启动烧光超时预算; 测试 29/29)。(8.5) **分页相同页守卫**: 实测网关同时无视 limit/offset 永远返全日 (top_inst 1231 行四参数同返), 三态处置 (超 limit 单页收齐/相同页非整倍取首页/整倍数 fail-closed 拒收) — chain9 实弹 86 天 x50 调用风暴止血 + dc_member 重拉连带保护, 病理单测 4 个 (含行序漂移反序回归 — 三判官修订: 位置比较改整页集合签名)。(9) LF V0 + LHB 退出预注册冻结 (analysis/prereg_*_20260612.md, 数值线按成本锚立法, 门柱进 git)。(10) architect-controller skill 融合立法者协议 (立法→控制→执行三层, ~/.claude 与 ~/.codex 同步)。
- **2026-06-13 晨批: chain9 收官 + LHB gate 转 GO**: top_list/daily/adj_factor 实验窗 728/728/728 日齐 (top_list min 20200102); **dc_member 重拉 23.1M 行** (旧截断库 2.7M 的 8.6 倍, 整 5000 签名清零, moth 弹仓 9/9 全绿); top_inst 被 drain 救活 1.87M 行 (2018-2026)。**首批类型推断陷阱根治** (三案同病: suspend_timing/level/bz_profit 首批 NULL/小整数→INT32 拒真实数据): _write_batch ConversionException→按真实 dtype 单调加宽重试, pandas 3.x dtype='str' 兼容, 33/33 测试。lhb_exit G4 按实测分年剖面修法留痕 (float null 实验窗 4.3-7.8% vs 2023+ 段 1.9%), prereg 修订 1 跑前冻结 null-float 处置 → **sherpa gates lhb_exit GO 6/6, 第一个 alpha 判决实验数据面就绪**。ths_hot 20240312 双夜实证上游真缺结案。**概念事件 raw 源真根因**: 带点全名整体引号成单标识符 ('traw.raw_tushare_dc_member' 查无此表) — raw 路径从未跑通, X 轨曾误归因写锁; 修分段引号+回归测试。daily_basic 2020-2022 段获批回填 (LHB 混淆臂市值桶消费方出现, chain9b)。**LHB 退出实验脚本落地** `backend/scripts/experiment_lhb_exit.py` (--check-prereg 常量与冻结 prereg 文档逐字对账 / sherpa gate 硬门 / 混淆臂 ntile(5) circ_mv 五分位 + ±1pp 带确定性匹配 / bootstrap seed=20260612 n=10000 / 判决 JSON 落 analysis/, --out-dir 防测试污染, 0 样本 INVALID exit 3 fail-closed); 端到端合成测试双根因修通: (a) venv 为 --system-site-packages 而 duckdb 在 ~/Library/Python user-site, 测试覆盖 HOME 即切断解析 → subprocess env 必须继承 os.environ; (b) 旧 fixture 仅 3 股, ntile(5) 每股独占分位 → 物理 0 对照除零 — 重做 10 股 fixture (事件/对照同 q3), 实测合成 net=+10.0pp, J2 1/7 → REJECT 判负路径可红可达, 2/2 测试通过。gate 增 g5-dailybasic-circmv (728 日, 等 chain9b)。**chain9 死因对账 + chain9b 发射** (06:22): 6a/6b/7 三步死于首批类型推断 ConversionException, 而加宽修复 commit 05:59:35 比三步晚 10 分钟 — 复跑即自愈; dc_member 凹陷日鉴别 = 仅 6 日且全是残缺拉取非 vendor 缺口 (20250106/20251128 行数恰 8000 = 旧硬截断签名, 同月邻日概念数 418-559); `scripts/backfill_history_chain9b.sh` 工单: daily_basic 2020-2022 (~689 日 LHB g5 关键路径) → gate GO 即跑 LHB 判决 → 凹陷日定点重拉 x6 → top_inst drain → 加宽自愈三域 → 概念事件重建 + G4 重标定基线输出; ths_hot 20240312 vendor 真缺结案不再烧调用。**LF V0 实验脚本落地** `backend/scripts/experiment_lf_v0.py` (prereg 逐字对账 --check-prereg / 事件=概念内涨停龙头>=2 去连发 [t-4,t-1] / follower 盲点6 t+1 open<up_limit 过滤 / 对照=同日 ±1pp 带非成员 (prereg 文面无 tradability 条款不加) / PIT 双锚 t-1 臂留存>=50% 一级 NO-GO / 极端热日 0.9 分位分桶报告 / cluster bootstrap 按事件重采样 seed=20260612 / J3<30 → INCONCLUSIVE 非判负); 合成测试 2/2 + 红色实证 (关 cooldown → n_events 翻倍 net 1.993≠5.0, 三断言全抓); fixture 钉死去连发/盲点6/带匹配三过滤 (D1 出带股给上涨路径, 漏进对照即偏离 5.0)。**kline watermark 滞后 bug 根治** (P1 遗留): 调度手动化时旧 cron_daily phase_watermarks 孤儿化 — daily_update Step 1 SLA 检查器只读水位从不写, 刷新器 (refresh_source_watermarks.py, 从真实表派生) 无人调 → kline_daily 水位卡 06-03 而 price_kline_tdxhub 实际 06-12; 修法 = daily_update.sh 新增 Step 2.97 (数据步后 panel 前, 失败 step_degraded 送达) + 接线防回退测试 (位置序断言 drain<refresh<panel); 实跑刷新 12 域, kline 水位对齐 06-12。检查器与刷新器是两个器官, 链迁移时只迁了一个 = "新守门孤儿" 反例的镜像变体。**E7 快照对账结案 + 物理摘除**: 退役前 observed vs reconstructed 单日对账发现证人自身腐坏 — 20260610/11 两份快照 dc_member 均 8000 行整截断 (真实日量 ~90k/1016 概念, 快照仅 66 概念), 由此 diff 的 270 行 observed 事件全是伪影; 处置 = concept_snapshot job 条目删除 (留可点按钮=误导) + snapshot_concept_daily.py git rm + 快照目录隔离 data/archive/residue_quarantine_20260613/; 270 行 observed 清除**待用户批准** (惰性无害, LF/gates 均只读 reconstructed); _full/ (Serenity posts/金股 parquet) 与 irm_qa/ 非 E7 产物不动。**LF V0 概念归属 carry-forward 平滑 (prereg 修订 2)**: 修复 6 凹陷日后成员级 flicker 仍 85.1%, drop 最高日 20260518 (35,120) 不在凹陷日清单 — dc_member 有成员级薄日, 逐日裸快照结构性不可用; 修法 = build_concept_events raw 路径 smooth_window=3 (union[t-2..t], PIT 干净) + experiment_lf_v0 MEMBER_WINDOW=3 同语义; G4 数值线不放宽; lf_v0 g1 awk 自吞 bug 修复 (起始行命中结束模式 range 单行恒 0); 概念事件测试 10/10 (薄日幻影抑制 + 真 drop 滞后 + 裸 diff 红色对照)。**主升浪 ground truth S1 复现成立** (用户指令"尽快开始验证"): `backend/scripts/rally_ground_truth_scan.py` 三角法定位原文两处未记录隐含约束 — 事件=穿越非状态 (状态口径 3.6x 锚) + 同股 60 日冷却 (扫描 0..120 唯一吻合, events 31,551 vs 锚 31,577 = 99.92%); TRUE 读法 B 锁定 (3,247 vs 锚 3,012 +7.8%, base rate 10.3% vs 9.5%); 口径已显式化入常量, 比原文可审计; 原型灭失教训 → 产物落 analysis/ 入 git。计划 owner = analysis/zhushenglang_rebuild_plan_20260613.md。**S1 落库 + S2 复用验证 (06-13)**: `--land` 写 `fact_rally_ground_truth` (31,531 突破事件 + gain/dd/peak_offset 连续结局 + is_true_rally 读法B, 含 FAKE/NEUTRAL 供 S3 定二分目标, event_date=突破日 t PIT 锚, 落库防 /tmp 灭失); S2 = 事件 JOIN 现成 `fact_feature_panel` (79 列 PIT, 奥卡姆零重造), 实测 2023+ 段 100% 命中 (26,789 事件/2,784 TRUE 带 70+ 特征), 2022 段无特征 (面板起 2023-01) → S3 训练窗 2023-2026; **S3 LightGBM walk-forward 本地秒级不需 modal** (modal 留 CYQ 全市场特征扩展判正后增量轮)。**S3 判决 = REJECT (异常高泄漏警报)** `backend/scripts/experiment_zhushenglang_s3.py` + prereg `analysis/prereg_zhushenglang_s3_20260613.md`: embargo>=180 交易日 walk-forward (防重叠标签泄漏) 3 折; J1 信号强 (top-decile precision 3.18x base/mean AUC 0.779/3-3 折 PASS) + J3 PASS, 但 J2 FAIL (AUC 0.779 > 预注册 0.75 §4.2 异常高红线); 标签置换对照 0.530 落 [0.45,0.55] = 管道层无泄漏但抓不到特征级前瞻污染; 纪律不挪门柱 (禁事后抬红线=谄媚死); 判负处置 = 特征族 ablation 定位 AUC 驱动 + 驱动 PIT 审计 (真动量 edge vs 泄漏特征), 不接 live/不调参续命 (`zhushenglang_s3_verdict_20260613.json(已删·重启清理)`); 2 测试 (prereg 一致性 + 泄漏防线常量冻结)。**公式 mart 半部署收尾**: 06-11 latest-snapshot PIT 修复后 fitness/buy_signal 两表首次重建 (1,015 + 7,930 行, built_at 2026-06-13), 停滞 05-12 的 stale mart 清零。**网关日配额耗尽** (今日 ~10k+ 调用): dc_member 12 薄日 (含 20260518-26 连 6 日段, 行数<邻域中位 80% 筛法) 重拉与 LF V0 判决顺延至配额重置后; lf_v0 当前 g4-artifact 2.49/日已 PASS (平滑后), churn 790/日 vs 240 门 — 240 标定基线来自旧截断面板自身带病, 薄日重拉后复测再裁。**LF V0 判决 = REJECT (第二个 alpha 判决, 假设证伪)**: `backend/scripts/experiment_lf_v0.py` 跑出 net -0.025pp CI[-0.057,0.008] (阈值+0.55, FAIL) / J2 2-3 期正需 3/3 (FAIL) / J3 17151≥30 (PASS) / PIT 双锚 net_t1≈net 无不稳定伪影 — 概念内涨停龙头后 follower 相对同日同涨幅带非成员零超额, theme/LF 假设证伪; 判负处置 theme/LF 封档+bank/sentiment.py 列退役+产能转 D/LHB (`lf_v0_verdict_20260613.json(已删·重启清理)`)。**性能根治**: 验证器 intractable (~50k 事件×89 follower×5512 全扫排序×2 臂, 36min 不出, 睡眠期误判断电) → 五招向量化 (members 预载 dict 并集/fwd5 memo/bootstrap 预算累加/对照两指针带外扩替全扫排序/detect 两锚共享) → 8 分钟出, 两指针 tie-break 经 1000-case 差分测试与朴素逐字等价守门 (`_band_controls` vs `_controls_naive`, 防"测试绿判决偏")。**LF V0 g4-churn 重标定 (prereg 修订 3) + 薄日修复**: chain9c 薄日重拉 11/12 (仅 20250106 vendor 真空), 平滑重建后 BORN+DEAD 幻影 4612→79 (g4-artifact PASS 2.49<5)。g4-churn 旧门 240 标定在截断面板 (27-354 概念) 失真; measured 干净全面板 (1016 概念/89k 成员行) 真实成员 churn 中位 468/日 (扣 day1 初始载入; 扣全部 8 薄日及邻日仍 453 = 东财真实重分类率 median 0.77%, 非伪影)。重标定为中位数门 < 900 (对残留多日薄段尖峰稳健, flicker 态 1273 仍抓); binding 幻影守门 = 冻结的 g4-artifact, binding 成员稳定性 = 实验 PIT 双锚。sherpa gates lf_v0 GO 7/7。**统一泄漏检测模块 `backend/services/leakage_detect.py` + CLI `leakage_probe.py`** (用户 4 段指令收口: 专用/事前/整合教训/单模块分阶段/防误报): S3 follow_net_return 标签泄漏暴露既有 audit_panel_leakage (查面板构建 SQL) 抓不到"消费方把标签当特征"这层。模块 4 阶段适时单用或 run_all 全面: STAGE1 panel_build (编排既有 audit_panel_leakage 不重造) / STAGE2 feature_consumer (标签契约命中 + 名模式 + 单特征 AUC>0.7 探针, S3 盲区核心) / STAGE3 model_output (§4.2 异常红线 AUC>0.75/RankIC>0.3/sharpe>5/年化>100%/相对+50%) / STAGE4 split_discipline (embargo>=label_horizon + 时间切非随机)。**复核筛选器非神谕** (用户点的): 三信号分级 (契约命中权威/AUC经验强/名模式高误报) + 每 flag 带 false_positive_check (forward_pe 类合法特征→REVIEW 不 HIGH); LEAKAGE_LESSONS 登记表整合 §4.1/4.2/4.5+mythos+audit 的 10 类泄漏→对应阶段检测。接进 experiment_zhushenglang_s3 做训练前自愈闸; 6 模块测试 + 实证抓 S3 三泄漏列不误报 ret/vol。**强制使用三道流程闸** (用户: 从流程确保强制, mythos #14 工具不接入口=孤儿): `backend/config/leakage_consumers.yaml` 消费者注册表 (真相源, 新训练/打分脚本必登记) + `leakage_probe --gate` 遍历逐消费者事前探针 (任一 HIGH exit1, 确定性抽样 30k 行 9.3s) + 接 safe_commit Step 3.6 (改消费者/注册表/builder 契约触发) + moth 弹仓 leakage-consumer-gate (doctor/chain 每次全面检测, 14/14 PASS)。gate 不信脚本手写 EXCLUDE (漂移源) 用注册表 panel_labels 权威标签, 单特征 AUC 层兜新混入列。**地基: index 族落库** (用户"先打地基", D3 KPI 真相源缺口): registry 加 `index_daily_benchmark` (7 基准指数全史, by_code_list 新 batch_mode 固定代码清单循环避全市场) + `index_member_all` (31 申万一级成分史, 按 l1 循环避无参整 5000 截断反例); `sync_runner` 加 `by_code_list` batch_mode (code_param+code_list+fixed_params)。实测落库: **raw_tushare_index_daily 35,128 行 (HS300 000300.SH 5,206 行全史 2005-2026 = KPI 超额真相源, 终结 akshare 主从倒挂)** + raw_tushare_index_member_all 5,847 行 (in_date/out_date 原生 PIT, 申万退役真因=只有快照无历史的正解, JOIN 须 in_date<=t AND out_date>t)。落库前单发实测字段/grain + 无参全拉发现整 5000 截断 (top_inst/dc_member 同型) 改 l1 循环。**CYQ 筹码买卖点深挖** (用户: 深挖筹码分布/胜率+买卖点): 四角度 (winner_rate/筹码形态/C0除权鲁棒/规则事件研究) t-1 PIT+泄漏gate+剔除权窗。最强信号 **px_pctile** (价在筹码成本分布相对分位) RankIC -0.042/IR -0.39 集中 TOP decile (D9 获利盘无套牢支撑 fwd20 -0.47% vs rest +1.20%) = 高位退出/止盈卖点, 独立动量; 但 SELL 净 18-23bp 扣 T+1 成本归零, BUY 假设证伪 (站上均成本是卖向非买向 4 年净 -0.76pp), winner_rate 弱且跨 regime 不稳 → **CYQ = ensemble 弱辅助/风险过滤 + LHB GO 退出组件卖点闸候选, 非独立触发**。C0 三级协议: 比率型直接可用 (Mann-Whitney p=0.66, audit 8.11pp 是 38 股小样本巧合)/price-vs-cost 剔除权窗±3 (窗内 3.3x 伪放大仅 2.28% 样本)/绝对价位才需 modal (当前不触发)。`index_dailybasic` registry 注册 (regime, by_code_list, 落 15k 行/5 指数, 2 缺待补)。**异常核查协议** (用户: 异常不应直接排除而应核查): 异常高信号触发四步核查 (ablation→PIT 溯源→剔除重跑→shuffle), 核实泄漏才修/核实真 edge 保留+新冻结红线重跑, 绝不因看着高丢真实增强 (两反向都防: 泄漏当 alpha 上线 vs 真 edge 误杀)。**L0 live 泄漏体检结果**: v7 (124 特征)/p0b lambdamart-v6 (133)/S3 (62) 全 CLEAN — live 当前未被标签泄漏污染 (S3 是手写 EXCLUDE 偷懒个例); 留 L0 跟进: sniper 用 sector_ret_20d 但 p0b 当 fallback 污染排除的不一致。**信道限流定层 + 配额熔断**: 对照本地官方文档镜像 (`/Users/dp/Documents/M/stock/tushare/`) 定层 — "今日请求已达上限请明天再试" 是代理 jiaoch.site 账户级反刷量墙 (代理商自述"请求过多视为攻击"), 非 TuShare 官方积分配额 (官方 doc_id=290 表一: 5000+ 常规数据无日上限/10000+ 特色数据 300次每分钟; 当日烧配额 8 接口全是常规数据; 官方按接口计而实测三接口同时撞墙=全局计数器; 报错串官方镜像 0 命中)。`sync_runner` 加 QuotaExhaustedError 熔断 (`_is_quota_wall` 识别 → 命中即停链不逐日续戳, run_domain/drain/main 三处停链 + 退出码 2, 11 单测含红色对照); 根治建议 = 官方直连 5000 积分 (¥500/年) 常规数据全无日上限, 详 analysis/data_acquisition_v2_design_20260612.md 限流定层节。**modal smoke 同路径覆写隐患修复** (D 轨遗留): 旧 smoke() 直接覆写 {DATA}/kline_qfq.parquet (真数据输入) 且输出落真 cyq_local/ — 真数据上传后跑一次 smoke = 输入变 1 只合成股; 修法 = cyq_replay_batch 加 input_rel/out_rel 参数 (默认不变), smoke 全程走 smoke/ 隔离前缀; **下次用 modal 前需 modal deploy 重新部署** (D 轨冻结中, 不主动 deploy)。
- **2026-06-13 凌晨批: 残留大清理 (5 线审计 11 agents + critic 裁决) + 数据获取 v2 设计定稿**: (1) **直删 (git rm 全可恢复)**: ops/ 旧名三件套 / configs/launchd 退役 plist x3 + forecast_eps plist / configs/cron 裸 cron 安装包 / 重复安装器 install_launchd_all.sh / 孤儿脚本 x5 (codex 三件套+upgrade_akshare+pre_edit_check) / 死脚本 x4 / .pre-commit-config.yaml 幽灵配置; 未跟踪数据隔离区 data/archive/residue_quarantine_20260612/ (stock.db 0B/kline_delta/0B study db/Users 幽灵树)。(2) **死代码块**: objective.compute_score / expand_to_daily_grid 空壳 / AkshareHolderSource 占位类 / _EM_ENDPOINT 死常量 / check_codex_review 不可达 50 行 / snapshot_concept_daily ths_member 周一分支 (单源化决议贯彻)。(3) **消费方同步**: install_all.sh 只装 nightly-data-audit / chunkyctl 清单 / session_status+delivery_readiness 文案改手动时代 / data_routes 主从倒挂措辞对齐 §4.3 / settings-view 旧名+死链 / start.command 旧名 / GCP 过期注释 x3 / .gitignore 死条目 x11。(4) **预存红清零**: Rule 10 阻塞契约 7 测试改钉 2026-06-12 非阻塞决议 (scripts 套件 300 绿)。(5) 保留有据: 双前端裂脑 (ops 按钮在退役 vanilla, v3 真活体 0 按钮 — 随前端 v5 实施包决策) / 146 孤儿研究脚本批量归档 (核验员驳回直接执行, 普查有漏报, 排 P2) / phase5 双副本 157M (C-user)。(6) 数据获取 v2 + v2.1 判官修订定稿, 用户批准 tushare 官方转主信道 (S1 待 token)。
- **2026-06-12 下午批: 接管对账 + 拆分回归二轮整改 + C0 判决落账**: (1) **拆分回归收口**: COPY FROM DATABASE 不搬 PK/约束/索引 (315 约束→1), 首轮断线 session 只补 4 表; 本轮全仓静态扫描证伪"写入面仅 4 表" (实测 165 张无 PK upsert 目标), 按恒等式"凡 upsert 目标必有 PK"以旧库 DDL 逐表事务恢复 164 张 (107.8s, 0 失败; **RENAME 前必须先 DROP 表上索引** 否则 Dependency Error), 终态约束表 169/索引 348/343 表/24G, 冒烟 6/6 PASS; 残余 fact_fundamental_quarterly = 旧库本就无 PK 既有缺陷。validation v2 六件套 = 行数+抽样值+约束计数+索引计数+upsert 冒烟+写入面全仓扫描对账, owner=`analysis/db_split_runbook_20260612.md`。(2) **chain4-8 下落定案**: nohup 链 07:02-12:09 自然跑完非断死 (物证 /tmp/w1_chain7.log); 三处伤 = 6 域 drain 撞 PK 回归 (今晚 Step 2.95 应自愈) / top_inst 548k 行未转正 / fina_mainbz 秒崩 (sync_runner by_ts_code 的 get_conn 不带 market attach, 确定性 bug 待修)。**top_list 等 2005-2022 历史被 dim_trading_calendar (起点 2023-01-03) 静默 clamp 全军未落** — 窗口决策待用户, LHB 退出实验 gate 因此阻塞。(3) **C0 FAIL 落账**: 三判官全败 (J1 0.8972/J2 8.53pp/J3 0.072), 筹码轴 5 combo 预注册冻结, 口径结论 (疑似未复权, J3 实现缺陷未确证) 写回 sync_registry cyq_perf pit_anchor; 产能转 LF V0 (事件面板仅 1 天需增厚) + LHB 退出。(4) watermark/built_at 时间戳 UTC 误读再现一例 (§8.25 同族), 读库先对时区口径。
- **2026-06-12 上午批: modal 计算面实弹 + 34G 真相 + /goal app bug 诊断 (session 切 CLI 交接)**: (1) **modal 打通**: `backend/compute/modal_app.py` deploy 常驻 (chunkymonkey-compute: cyq_replay_batch/all + smoke), 冒烟实弹返回 'pipeline OK 300 rows' — image/volume/嵌套remote/回传全链验证; 算法与 C0 同源强制; 首个真单 = C0 PASS 后全市场 CYQ 复算 (T3 共享缓存), 数据 push 等 raw 锁。(2) **34G 真相三层**: 47% 空洞碎片 (已回收 16.8G) / 新库 66% cells 是 panel 历史变体 (G4 第二刀可再省 8-12G) / 现役 ~7G; 旧库统计失真实证 (gpcw est 47M vs 真 16.4M)。(3) 本地卡顿元凶 = Claude.app UI 渲染 105% CPU 非计算任务 (实测)。(4) **/goal 仅 CLI 可用**: mac app UI 认识但执行层静默失效 (空闲发送被吞不达模型, 忙碌发送降级纯文本), 替身 `~/.claude/commands/goal-app.md` 已建走 prompt 通道; 待报 Anthropic feedback。(5) 后台续跑交接: chain7 (drain 补 top_list 2005-2020 历史, 4h+) → chain8 (C0 审计自动接力); 17:00 daily_update 新体系首次定时实弹待观察。
- **2026-06-12 34G 拆分执行完成 (用户决议 P0)**: `db_split_execute.py` 逐表重建 — smartmoney **36.1G → 19.3G (紧缩 16.8G)**, 343 表 4 分钟拷完。三次失败教训全记录: 整库 COPY FROM DATABASE 在 205 列宽表 OOM 两次 (8G RAM 不可控) → 逐表模式 (峰值=单表) + threads=2 + 关插入序 + 溢盘; validation hash 用 ROW(t.*) 全表崩 → `hash(t)` 整行 struct 写法。validation 全 PASS (343 表行数 + 7 关键表全行 hash + 视图名集合亲核一致) 后原子换名, manifest 零改动; 旧库 `smartmoney_v1_retired_20260612.duckdb` 保留至 **2026-06-26 人工删除** (届时磁盘回 60G; 当前余 26G)。消费链回归: data-status 正常 + 56 测试过。G4 panel 收敛 = 第二刀 (再省 ~5-8G)。**同步推进矩阵入 goal.md**: A 存储 / B 数据 gateway / C 本地实验 / D modal (判正后扫参主场) / E 消费侧 五轨并行。Codex review 强制解除落地 (safe_commit + hook 非阻塞)。
- **2026-06-12 Alpha 组合实验矩阵定稿 (16 设计 → 4 run_first / 9 run_later / 3 reject)**: workflow 三视角 (筹码×资金流 / 产业链×概念事件 / 反直觉) + judge 三轴评审 (PIT×正交×判决力), 图纸 = `analysis/alpha_combo_matrix_20260612.md`。**run_first**: C0 cyq_perf 黑箱口径审计 9.2 (前置 gate 判 5 个下游生死, 三数值判据预注册) / 出货预警退出组件 winner_rate×elg 8.6 (hologram 期望值最高单项 +10pp) / LHB 上榜即退出镜像 8.0 (已证伪入场信号的退出端复活) / **chain_leader_follower 7.8 (唯一 T0 今天数据全就绪)**。执行序 T0-T3 四波 + 条件分支, 依赖全 watermark 实测口径; judge 用 data-status 红线拒掉谎报已落库的设计 (治理工具上岗实证)。被拒 3 个全给了搭车归宿不浪费。
- **2026-06-12 筹码胜率注册 + 34G 库先行处置**: (1) `cyq_perf` 实弹注册 (用户点名): winner_rate + 5 档筹码成本分位 + weight_avg, 全市场 5512 行/日 2018 起, chain6 排队 (尾挂概念事件 reconstructed 首跑); stk_factor_pro 截断疑点暂缓 (1878≠5512)。(2) smartmoney 345 表实测盘点: 2 个 hash cache 死表 (0 引用) 存档 985MB → DROP; panel 家族 8 变体 30M 行 = G4 收敛实体有真实消费者不可直接清; 拆分六步 runbook = `analysis/db_split_runbook_20260612.md` (执行卡 Codex review gate, 预期回收 10-15G)。(3) **样本契约机制实弹生效**: chain4 回填 adj_factor 首批自动存样本入 fixtures/domain_samples (根因 A 机制上线次日即工作)。(4) alpha 组合矩阵 workflow 后台跑 (3 设计视角×judge)。data/archive 入 gitignore。
- **2026-06-12 复查问题系统性根治 (按根因不按表象, 4 Phase)**: **A 字段语义契约**: sync_runner 首批自动存域真实样本入 `backend/tests/fixtures/domain_samples/` (git 追踪, 幂等不覆盖) — 让"fixture 抽象命名与实现一致地错"物理不可能; dc_member 样本已回补 (方向反事故物证)。**B 时区读层**: source_watermarks UTC 警示头注 + mythos (存储 UTC 不动, 改存储才是迎合表象)。**C 测试红清零**: suite 3503 passed / 4 显式 skip / 0 fail — calendar_gate 21 红清零 (亲核推翻 agent 判级: 真违规仅 1 个 = snapshot_concept_daily 无交易日防护→加日历 gate fail-open + 18+1 处 built_at/产物名假阳性逐行显式标注, 不扩 token 防误放行); widget 5 红 = harness 漏加载 format-utils (生产无恙, 多文件 require 修); rg 4 红 = 环境 shim → skipif 带 reason + 脚本显式 EnvironmentError; 顺手修自引入的裸 duckdb.connect 契约违规 (连接契约 gate 自抓自修)。**D/E 流程纪律入 CLAUDE.md**: §10 side-agent 入库边界 (禁改控制面文件, 主会话 review 收编) + §8.25 降级期 commit 标记 `model-context: degraded` (恢复后定向复查)。红海清零后, 任何新红即新问题 — 信号价值恢复。
- **2026-06-12 Fable-5 复查降级期 (Opus) 工作 — 1 真 CRITICAL + 1 复查自身乌龙 + 1 小修**: (1) **概念事件字段方向反 (真 CRITICAL)**: dc_member 实测 ts_code=BK*.DC 概念板块/con_code=股票, build_concept_events 原码反向会把 5521 只股票当概念; raw+snapshot 双路径改正 + 真实形态防回退测试 (fixture 抽象命名与实现一致地错 = "测试 pass ≠ 产物没说谎"再添一例); post-fix-audit 表 0 行无残留。(2) **复查自身乌龙 (教训入 mythos + source_watermarks 头注)**: 把 queue/watermark 的 UTC 时间戳当北京时读, 虚构"上午轮"与"record 静默失效"并一度把正确的验收结论改错 — 真相: 两轮回填闭环完美 (第一轮 18:43 止 29 败 record open → 脚本幂等第二轮 20:14 止 830 批全成 4.22M 行 → resolve + watermark, 缺口行数 148,249 精确自洽), **29 缺口日已补齐**。(3) run_domain `ok` 双标小修: 改严格 len(failed)==0 (旧宽松口径日志 'ok': True 掩盖 29 败), 与 record 判定统一, monkeypatch 测试锁定; source_watermarks 加 UTC 口径警示头注。(4) 其余复查项核验通过: calendar_gate allowlist / modal 三源 / data-status 分类 / fetch_irm_qa。
- **2026-06-11 数据迁移状态仪表盘 (data-status) + 宪法 v2 状态落实**: `backend/scripts/data_migration_status.py` (入口 `scripts/chunkyctl data-status`) — registry 驱动读 watermark+failure_queue+日历, 一眼看清每域 OK/STALE/NEVER_SYNCED/未决失败 (把"数据基建做好了么"从手敲 SQL 变可执行答案; 真相源唯一, NEVER_SYNCED 不臆造已落库)。日历新鲜度用 canonical latest_completed_trade_date (VARCHAR/未来日 bug 修正)。当前态: 21 注册域 / 7 已落库 (moneyflow 4.22M/stk_limit 5.76M/dc_member 2.71M 等, 总 12.9M 行) / 14 待回填 (chain4/5 在跑+W-B 待注册) / 2 open 失败 (moneyflow_ind_dc/limit_cpt_list, 下次 drain 自动). 3 单测. **宪法 v2 状态落实** (Stop hook 抓 stale): goal.md "草案待确认"→"已生效", docs/PROJECT_CONSTITUTION.md 即 live v2, 草案加"已采纳"头注。
- **2026-06-11 互动易/e互动 问答直抓落地 (产业链 L2 关系边, irm_qa premium 无权限的替代)**: `backend/scripts/fetch_irm_qa.py` — 深交所 `POST irm.cninfo.com.cn/newircs/index/search` (JSON, updateDate 降序流, epoch-ms 字段) + 上证 `POST sns.sseinfo.com/ajax/feeds.do` (HTML 片段, ask_ico/answer_ico 块, 相对时间还原分钟精度) 双源全局流收割 + 本地按代码过滤; 落 `data/concept_snapshots/irm_qa/irm_qa_<YYYYMM>.parquet` (按提问月分文件, 幂等 merge 原子覆盖, 字段 code/question/answer/q_time/a_time/source/fetched_at)。**实测坑**: SZ 服务端 per-code 过滤参数全失效 (stockCode/keyWord/"code,secid" 组合均 totalRecord=0) → 全局流+本地过滤反而对多代码列表请求更少。礼貌限频 2s/请求。首跑实测: sz 27页/565行 + sh 40页/772行 (--days 1, 0 malformed), 19 单测; 真数据抽样核验 (真投资者问答, 唯一 except=httpx.TransportError 有界重试无吞错)。消费方: 链谱边弱监督 (Serenity W5)。**注**: 此条推翻同日早些"互动易未实证"判断 (newircs/index/search 实为浅层 JSON)。
- **2026-06-11 测试红 triage + 本会话 calendar_gate 回归修复**: 全量 triage (3471 测试 99.1% pass): A 环境性 4 (缺 rg) / B 前端 widget 5 (WidgetFormatUtils 导入链) / C 真 bug 0 / D 本会话 2 (已修). 修本会话 2 处 calendar_gate: sync_runner drain today 排除 + build_feature_map 时间戳加 Phase ψ.5 allowlist (stash 实证 21→19 确属本会话). modal 转正对齐 (用户"该用就用": yaml/test/goal 三源收敛, active=可派发非必派发, dry_run 默认 True 防误花). **backlog**: 19 个 HEAD 预存 calendar_gate 红 (bestchoice/ensemble/v7 脚本 wall-clock end_date) 归 P2, 需逐文件判 legit-violation vs false-positive.
- **2026-06-11 概念增删事件流 detector (fact_concept_event)**: `build_concept_events.py` 相邻交易日成分集合 diff → concept_born/dead/member_add/drop 四事件; 真相源 = 概念成分快照本身 (raw_tushare_dc_member 历史回填=reconstructed / parquet 自养=observed), 纯集合运算无中间表; event_date=后一快照日 (变更首次可观测 = PIT 锚), as_of_mode 显式区分 PIT 强弱; 增量 watermark 续传。6 单测 (增删/诞生消失/event_date PIT/增量过滤/as_of 标注/单日空). 用途 = 题材生命周期标记 + 链谱边弱监督 (Serenity W5). 待 dc_member 历史落库 (chain2 回填中) 跑 measured 实验。互动易 L2 关系边: ~~接口未实证暂不进生产~~ → 2026-06-11 晚已实证落地 `fetch_irm_qa.py` (newircs/index/search 实为浅层 JSON, 见本节顶部条目)。
- **2026-06-11 深夜批: runner 分页 + doctor 告警巡检 + 前端急救三件套**: (1) `sync_runner._fetch_paged` offset 分页 (registry `page_limit` 字段驱动; 中间页败=整批 None 防部分写伪完整; 50 页防御上限) → top_inst 注册落地 (1000/页, chain4 队列); stk_surv 实测 trade_date 参数 0 行 — 参数语义未核证暂缓 (0 行不下结论纪律)。(2) `chunkyctl doctor` 新增 `alert_flags` 节: /tmp/chunkymonkey_ALERT_*.flag 巡检入 doctor report (约定变代码), 2 单测。(3) **前端急救三件套** (毕业自 mock 静默回退 CRITICAL): `frontend_config.yaml`+`GET /api/v3/config` (阈值唯一真相源, 拉不到=中性灰不许内置回退) / fetchJson 失败台账 `SOURCE_STATUS`+页面降级 badge+dataReady 带 degraded / stability 硬编码 0.80 改真 null 显 '—'; 5 个 jsx 样板迁移 (picks 0.58+红黄绿/stock-view 0.5/lab stability)。preview 实测: config API 200 + 68 源台账全 ok + 0 console error + 页面正常渲染。剩余硬编码阈值 (lab 0.6/0.65 装饰色, stock-view.js cutoffs) 列下批。前端入口 = `/v3/Chunky Monkey v3.html` (uvicorn main:app 8000; preview 配置 .claude/launch.json 本地不入 git)。
- **2026-06-11 数据平台 P0 第一批: 失败分级 + 日历 gap drain (对抗复审 12 条, 9 修 3 排期)**: (1) `daily_update.sh` 29 处吞错点 (26 处 `|| log WARN` + tdxhub sync_exit 丢弃 + SLA 倒挂 + [gate] 漏网) 全部接 `step_degraded` — 写 `/tmp/chunkymonkey_ALERT_daily_update_degraded.flag` + 链尾汇总 + osascript 通知, 链首清 flag。(2) `sync_runner.drain_domain` + `--drain` CLI: **日历 gap 扫描取代队列重放** (failure_queue 按错误类型聚合不存日期; 真相源 = trade_cal + raw 表本身, 终败/漏跑/历史空洞三类一网打尽); 完整日口径 = 行数 >= min_rows (防 vendor 截断批被当完整, 复审 HIGH), 截断最新优先, today 锚定 Asia/Shanghai, 非按日域 fallback 增量 run_domain (防 drain-only 接线静默停更, 复审 HIGH)。(3) daily_update Step 2.95 接线: drain 即增量 (昨日数据 17:00 落库恰对齐 JOIN t-1)。证据: 16 单测 passed + 实弹撞写锁显式 error + exit 1。(4) **SLA 防线 registry 驱动** (`update_watermark_sla.py`): sync:* 条目从 sync_registry.yaml 自动生成 (注册即入防线), 新增 4 个显式状态 NO_PROBE_RULE/NO_QUERY_MAPPING/DB_LOCKED_UNVERIFIED/NEVER_SYNCED 消灭"查不到=静默OK"; 实弹 dry-run 当场抓到 lhb_daily stale 13d + xdxr stale 17d (分红季!) 两条真腐烂 + 3 个映射盲区域。剩余排期: dead_dates 降频 / doctor flag 检查。(5) **旧源退役批 6 域注册+排队回填** (用户决策"能在 tushare 获取就从 tushare 获取"): top_list(龙虎榜2005起)/report_rc(盈预)/ths_hot(热榜)/moneyflow_hsgt(北向)/dividend(分红除权对照)/adj_factor(复权因子盘前9:20当日可用) — chain4 排 chain3 后自动接力。**实测抓住 3 个截断陷阱**: top_inst 单日 1000 行整=隐性单页上限 (暂缓等分页), ths_hot 全量 2000 整=多榜合计截断 (fixed_params 锁热股单榜 444 行完整) + 同日多 rank_time 快照 grain 必须加 rank_time, dc_hot 参数未核证暂缓。runner 新增 date_param 通用支持 (dividend 用 ex_date / report_rc 用 report_date); allow_empty 域 gap 不可判定 → drain_inapplicable 走 watermark 增量 (顺手闭环 suspend_d 不收敛)。20 单测 passed。**主源定位纠偏 (用户原话二次强调)**: tushare 主源不是兜底 — 复权链方向 = adj_factor/dividend **转正**, tdxhub xdxr 降备援交叉验证, 旧源故障的响应是加速切换不是修旧路径; 权威表述移入 CLAUDE.md §4.3, 禁"兜底/对照"措辞。**第三次递进 → 全量默认 tushare**: 存量 44 表 (tdxhub 12/aif10 9/akshare 22/停用 1) 全部迁移对象, 地图+波次 = `analysis/tushare_full_migration_map_20260611.md` (W-B 16 接口 catalog 核证积分全够; 真例外仅 F10 文本类与本地 CYQ; 估值分位改 daily_basic 自算更 PIT); K 线三件套 daily+daily_basic+adj_factor 已注册, chain5 排队 (daily_basic 的 float_share/free_share = CYQ 流通股本输入替代本地推算); data_product_contract 旧条款 "no global primary" 已改写。
- **2026-06-11 功能地图工具 (FEATURE_MAP.md 机器事实层)**: `backend/scripts/build_feature_map.py` (入口 `scripts/chunkyctl map`, `--check` 漂移门) 从 4 真相源机器枚举: chunkyctl 子命令/launchd plist/@router 路由/sync_registry 域/**产表→writer 映射 (单 writer 契约执法视图: 302 表中 121 张多 writer)**/codegraph 依赖热点 (双过滤抗按名解析假边: 唯一定义名 + caller 实际 import 目标模块 — 实测剔除 Path.resolve 272 条假边)。保鲜: safe_commit Step 2.6 漂移才重生成并同 commit (时间戳行不算漂移, 防噪音 commit); 生成失败 optional 级不挡 commit 但可见。**职责切分红线: FEATURE_MAP=机器可枚举事实, PROJECT_INDEX=人工判断层 (坑/权重/状态), 互不重复**。6 单测 + chunkyctl 39 回归 = 45 passed, 生成 ~1s。
- **2026-06-11 Serenity 方法论锻造 + TuShare 模块化 catalog + 金股验证**: (1) @aleabitoreddit 全量 6463 贴文 (镜像站后端 API 直拉) 提炼方法论全集 (B1-B12 信念/W1-W3 工作流/证据三档, 全带原话日期) + 集成设计 (拆四块: chain-research skill / industry_chain.yaml 链谱维表 / 解禁质押减持 veto / chain_diffusion 第 8 引擎条件激活; **明确反对建第四套书与照搬选股逻辑**; 链谱后视污染 freeze-date forward-only 红线) + 不可迁移批判 — analysis/serenity_20260611/。(2) `backend/scripts/build_tushare_catalog.py` 生成 `backend/config/tushare_api_catalog.json` (239 接口结构化: 积分/限频/字段/起始, probed_* 增量回写设计) = sync_registry 上游字典, 镜像更新可重跑。(3) 券商金股 measured (29 月): 共识>=3 月均超额 +1.41%/累计 +69% vs 市场 +18%, 但小桶噪音 (月中位 8 只) — 定位候选池增强因子非独立策略, 归 v7-F4 与 report_rc 同族 W5 设计 — analysis/broker_gold_validation_20260611.md。
- **2026-06-11 产业链 L1 数据底座: fina_mainbz 主营构成接入 + by_ts_code 批模式**: Serenity A股化三层方案 L1 — `fina_mainbz` (按产品分项收入/利润/成本, 立讯 002475 实测 4 产品项) 注册进 sync_registry, 首期 20251231 年报全市场 ~5300 股排 chain3; sync_runner 新增 `by_ts_code` 模式 (股票清单真相源=K线近45日活跃 code, 非 dim 表) + `fixed_params` 透传。**PIT 教科书陷阱已写进 registry**: end_date 是报告期非披露日, 可用时点必须 JOIN ann_date/disclosure_date。用途: 概念标签→收入加权产业链节点 (蹭概念 <5% vs 真卡点 >50%, 一个 JOIN 算纯度)。互动易 irm_qa_sz/sh 实测 premium 无权限 → 走交易所公开接口直抓 (L2 关系边); vip 全市场版无权限且重复访问触发网关风控 357s 封禁 → 拉黑 (权限错立即放弃, 重复打 = 风控)。
- **2026-06-11 sync_registry 范式落地 + 首个生产域回填 (Task 1 writer/watermark/failure_queue 三 gate)**: 架构稿 §3 实施 — `backend/config/sync_registry.yaml` (一条目=一域, 7 个 W1 域注册: moneyflow/limit_list_d/stk_limit/stock_st/suspend_d/moneyflow_mkt_dc/trade_cal, 每条带 pit_anchor/available_after/data_start/freshness_sla/min_rows 机器可读契约) + `services/data_sources/sync_runner.py` (通用同步器: 交易日历切批 by_trade_date/by_date_range/full_refresh, 0行退避重试, MERGE on grain 幂等, 列演进, 复用 source_watermarks 服务) + `data/tushare_raw.duckdb` 新库 (manifest 注册, 与主库写锁解耦 — DB 管理决策: 新域不进 34GB 单体)。adapter 加 `fetch_raw(api_name)` sync 专用入口 (raw 镜像不加工)。首跑实测: moneyflow_mkt_dc 全史 762 行落库 + watermark 写入 + 7 单测 green (幂等/0行重试/列演进/grain 缺失防御)。**实战坑 2 个**: (1) data_start 必须按"单日参数口径"实测, 范围查询会掩盖真实起点 (mkt_dc 单日 20230103 空, 范围查却有) (2) 部分接口 trade_date 单日参数语义失效只认 start/end → by_date_range 模式。
- **2026-06-11 K线 denormal 停牌行 + audit 两处口径修正 (audit 链转绿)**: nightly audit critical 双拆解 — (1) **真 bug**: tdx 协议对停牌日写 float32 denormal (~5.9e-39) 而非 0, denormal>0 绕过 tradability `volume<=0` 停牌检查 = 停牌股被判可交易 (实测 137 行/44 股/2026-04-23 起, 06-04 仍在产生)。三层修复: 写入路径 `_denormal_to_zero` 清洗 + 存量 137 行 UPDATE 归零 + `is_suspended` 物理下限 1.0 (不足 1 手=停牌) 防御, 防回退测试 3 case。(2) **audit 误报两处**: vwap/close ratio 检查未乘 factor, 近期除权股固有偏离 1/factor 假突破 1.5 阈值 (实测 002245 1.508→1.015), 已乘 factor 对齐 + 停牌行排除; fwd_cost_after 1.0 阈值误判真实牛市连板为坏数据 (实证 300085 自 2024-09-13 八个精确 20.0% 连板 10日+453%, 无复权断层), 改为物理复利极限 block (1.3^n-1)/1.0 观察线 warn, warn 不再走非零 exit (防告警疲劳淹没真 critical)。验证: audit ok/ok/warn + launchd 跑 OK flag 清除 + 84 tests passed。防回退: 探活/检查类阈值必须先问"物理上可能吗", 真实市场极值≠数据错误。
- **2026-06-11 tdxhub 断流根因修复 + 数据源主力反转决策**: 根因不是服务器全死, 是 **Surge 代理在本地接管全部 TCP** (实证: 物理不可达地址 192.0.2.1 任意端口 connect 0.00s "成功") — connect 永远秒成功骗过 server 选择, 真实数据链路只有部分服务器活 (协议层握手实测 9/35 可达, DEFAULT 5 台全活但运行时用的 health 排序池全是死 IP)。修复: 应用层握手扫描出可达池 → `CM_TDX_SERVERS` env (iter_tdx_servers 最高优先级) 写入 daily-update launchd plist。**防回退: 代理环境下 TCP connect 测试无意义, server 探活必须协议层握手**。同日用户决策: **数据源主力反转 — tushare 主力 / tdxhub 备用 / miaoxiang 第三 / akshare 最后** (理由: 真 alpha 来自新接入数据域且全在 tushare; tdxhub 屡断)。probe 239 接口终版: **171 ok / 19 no_permission / 37 error / 12 empty**。
- **2026-06-11 Fable-5 复查修正降级期 3 个真问题 (模型曾回落 Opus, verifier 也降级)**: (1) **PIT 泄漏补漏**: `build_stock_formula_buy_signal_daily.load_today_rows` 原修复只改了 picture as-of, 但寻优统计仍 JOIN legacy in-sample 表 `mart_per_stock_strategy_optimal` (sharpe/win_rate 经 6 因子 score→tier 流入 live selector) — 组间 scope 缝隙; 改读 `mart_per_stock_stage_strategy_optimal_pit` 的 cutoff<=signal_date as-of + oos_*, 防回退测试断言不 JOIN legacy + 未来 cutoff 不可见。(2) **modal 烧钱隐患**: `modal_adapter.submit_job` dry_run 默认 False + modal SDK 本地已装 → 完整 plan 忘传 dry_run 即触发付费调用; 改 dry_run 默认 **True** (fail-safe, 显式 dry_run=False 才 spawn) + modal>=1.5.0 入 requirements (此前装了未声明) + 防回退测试。(3) **§4 unknown 当 0 偷偷参与**: `northbound_alpha` stale 返回 0.0 被 compose 当合格类稀释 composite/虚增 n_classes_eligible; 改返回 score=NaN 让 compose 完全排除该类, compose 级防回退测试断言 n_eligible 不含 + composite 不稀释。受影响 79 passed。
- **2026-06-11 体检 confirmed 系统性修复 (修复工厂 7 组并行+对抗验证)**: (1) **PIT-live-fallback CRITICAL**: `build_daily_position_recommendations.py` 候选 SQL 删除 legacy in-sample 表 `mart_per_stock_strategy_optimal` (24442 keys 0 oos_*, 22741 只在 legacy = 全程 in-sample 假数据驱动 live T+1 仓位), scoring 只读 oos_*, `oos_sharpe IS NOT NULL` 守门, PIT 无覆盖即 score=unknown。(2) **PIT-fitness-snapshot**: `rebuild_stage_formula_fitness.py` + `build_stock_formula_buy_signal_daily.py` 把 `snapshot_date=MAX(...)` latest-snapshot 改 as-of (`snapshot_date<=决策日`), 历史日不注入未来画像。(3) **hsgt-stale-guard**: `northbound_alpha.py`+`build_institution_score_daily.py` 对 `fact_hsgt_daily` (停 2024-08) 加 staleness guard (阈值走 `institution_alpha.yaml`), 超期因子置 unknown 不静默用 2 年前数据。(4) **security**: `prediction_outcome.py` model_id SQL 注入改参数化+白名单; `main.py`+`start.command` 默认绑 127.0.0.1 (env 可切 0.0.0.0)+CORS 收敛 loopback。(5) **daily-update-wiring**: `daily_update.sh` 补三件套增量 rebuild。(6) **champion+plan_validator**: `formula_local_optuna_batch.py` 修 plan gate no-op (enforce_optuna_plan 缩进 bug 致零调用); `champion_registry.yaml`+reader 单一真相源; `registry.py` 成本读 paper_sim_config 收敛双源 (印花税 10→5bps); 收尾: `slippage.py` TradingCostConfig 改 **default-free** (旧默认 stamp=10bps 是减半前失真值, 裸构造=第二真相源已禁), 防回退测试断言裸构造 TypeError + yaml 派生 round_trip=27.282bps (与 cost_after/v5 panel 日志 0.002728 交叉一致)。(7) **modal-adapter**: `services/compute/modal_adapter.py` 复用 local plan gate, `experiment_jobs.yaml` modal planned→active ($30/mo, ~/.modal.toml), test_experiment_jobs 3 断言同步新现实。每组带防回退测试, 受影响 suite 实测 140 passed。
- **2026-06-11 行业分类区分度 measured 决策**: 申万 vs 通达信 ANOVA eta² (40 月度截面, forward 20d, shuffle baseline 扣类目数效应): 申万L2 净区分度 0.137 (稳定性 2.04) > 通达信L2 0.118 > 申万L1 0.110 > 通达信L3 0.068 > 通达信L1 0.070。结论: 申万L2 为主行业口径 (区分度+稳定双第一; L2>L1 甜点; 通达信L3 过细=过拟合)。当前快照验证 (两口径同偏差, 相对排序可靠); 生产需从 TuShare index_member_all 拉历史成分 PIT 化 (申万退役真因=只有快照无历史)。证据 analysis/industry_discrimination_*.json。
- **2026-06-11 TuShare vendor-gateway 接入 + need_027 gate PASS**: `.venv` + tushare 1.4.29; adapter `_pro_api()` 支持 `TUSHARE_HTTP_URL` 代理网关 (token/URL 只在 .env, 不进 git); probe `TUSHARE_TOKEN_ENV_VARS` 改 import adapter 真相源; `_source_preflight` find_spec 加防护; 3 个防回退测试; live gate PASS (tushare 3/3, selected_source=tushare); 代理有间歇空响应, writer 必须 0 行当失败重试; 剩余 production gates: pit_key/freshness_sla/writer/watermark/failure_queue。
- **2026-06-11 v5 panel PIT 防线显式化 (CRITICAL)**: v3 panel 表加宽后 (含 inst_path_a latest-snapshot 泄漏列, v3 DDL 自注 "latest, NOT PIT"), v5 的 `v3.*` 透传通道失去"v5 表无此列"的被动阻挡; `feature_join_v5.py` 现已显式 `v3.* EXCLUDE (inst_quality_wavg/max, inst_total_holding_ratio, inst_holder_cnt, top_inst_holding_ratio)` 兑现注释承诺, 防回退测试断言 5 列必须在 EXCLUDE 清单。v5 表误加的 5 列已 DROP (0 值写入)。遗留: v3 表自身泄漏列的物理清理在 implementation_plan P2。
- **2026-06-11 调度层 cron→launchd 迁移 + 失败告警**: cron 无 FDA 静默失败 (K线断流 4+ 交易日); python3.13 (有 FDA) 做 plist 入口 spawn bash 全链通; `scripts/launchd_job_wrapper.py` 失败写 ALERT flag + macOS 通知; crontab 两条退役。防回退: plist 入口不许改回 bash; 定时任务必须走 wrapper。

## 30 秒速览 — 这是什么项目

**Chunky Monkey v2** = A 股**自动选股 + 实盘模拟**系统. 用户(私人投资者)用它筛 5 只股票 / 月度轮换.

**用户目标 (硬指标, 一切优先级以此为锚)**:
- 年化 ≥ **+30%**
- max_drawdown ≥ **-20%**
- 超额 vs HS300 > 0

**数据基础**: 6,618 股 A 股 K 线 (2022-01 起) + 70K+ 财报 + 35K 机构事件 + 53K 龙虎榜 + 68K 高管增减持 + 大盘 regime + 4 阶段技术形态分类.

**架构主线 (alpha pipeline)**:
```
原始数据 → 公式信号 + PIT 因子 → Optuna 调参 (walk-forward) → mart 表
       → paper_sim selector (按 ensemble score 排名)
       → simulate_trade (T+1 入场, 含 tx_cost + 涨跌停)
       → NAV 曲线 → KPI 验证 (6 类 20+ 指标)
```

**当前最强发现** (实测严格 walk-forward OOS, 7.5h 跑批; 2026-06-11 E0 双口径收敛:
本区历史数字与 §10 实表矛盾, **统一以 §10 表为唯一口径**, 三评委一致点名该矛盾已污染下游引用):
- `reversal_1m_deep × stage=1`: avg OOS sharpe **+0.392** / win **58.1%** (§10 表, 地基旗舰)
- `reversal_1m_mild × stage=1.5`: avg OOS sharpe **+0.342** / win **51.9%** (§10 表, 次选)
- 整体 momentum 公式 (MACD/turtle/dynamic_ma) **全失效** (OOS sharpe ~0 或负)

**距离用户目标**: 单股 OOS sharpe 0.32 → 5 股组合 + 月度轮换 paper_sim 真实期望约 **+15-25% 年化** (推算未实测). 缺 **+5-15pp** 才达 +30% 标准.

**下一步**: 引入更多 alpha 源 (机构跟随主 alpha PIT 重建 / case-based 历史相似 / 板块强度) — 见 §11 "16 项遗漏审计".

## 维护责任 (Rule 9.5 沉淀)

**每次完成一个 phase / commit / 数据 backfill 后, 都要更新本文档**. 具体 checkpoints:
- 新加数据表 → 加进 §2 (数据资产)
- 新加 service 模块 / script 入口 → 加进 §3-4
- 新加 yaml config → 加进 §6
- 解决了已知坑 → §8 标 [PASS] + 短说明
- 跑出新 OOS 数据 → 加进 §10
- 踩了新坑 → §11 + CLAUDE.md Rule 9
- 加 §14 增量日志 (本 session 做了啥)

不维护 = 下次 session 又要重新摸索 = 用户最大抱怨

---

## 0. 用户终极目标 (锚)

> "短期内资产最大幅度增值不缩水"

3 个 PASS 标准:
1. 年化 ≥ +30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0

基线: 2023-01-03 起, 100 万初始, HS300 benchmark.

---

## Pipeline 数据流图 (端到端架构)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 0. 原始数据层 (data sources)                                         │
│   - akshare (K 线 / 财报 / 龙虎榜)  - tdxhub (qfq 复权 K 线)         │
│   - aif10 (估值 / 一致预期)         - tdx F10 (机构持仓)             │
│   - 内部模拟器 (event_simulator)                                     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. raw_ 层 (smartmoney.duckdb): 70K 财报 / 53K 龙虎榜 / 35K 机构事件  │
│    market.duckdb: 6M K 线 / 158K xdxr 事件                           │
└──────────────────────────────────────────────────────────────────────┘
        │ sync (POST /api/inst/update/smart) — 含 watermark
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. fact_ 层 (PIT 时序事实表):                                        │
│    - fact_stock_technical_stage (2.4M, Stan Weinstein 4 stage)       │
│    - fact_signal_context (3.3M, vol_r20/price_pos/drawdown_60d/stage)│
│    - fact_technical_trigger (公式信号触发, 含 strength)              │
│    - fact_risk_factors (4.8M, Phase ψ.β.1 PIT mom/sharpe/vol)        │
│    - fact_financial_pit_daily (3.7M, Phase ψ.β.2 PE/PB/ROE/yoy)      │
│    - fact_capital_flow_pit_daily (858K, Phase ψ.β.3 lhb/exec/holder) │
│    - fact_regime_state (775, 大盘 bull/bear/sideways)                │
└──────────────────────────────────────────────────────────────────────┘
        │ Optuna 调参 (R1 walk-forward, expanding_monthly / train_end_forward)
        │ governance 守门 (sharpe>5/win>0.95/avg>0.5 reject)
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. mart_ 业务层 (调参 / 寻优结果):                                   │
│    - mart_per_formula_stage_optimal (426 OOS 行,                     │
│         per formula × stage × train_end_date, 最强 setup ↓)          │
│    - mart_per_stock_stage_strategy_optimal (per-stock × stage 旧表)  │
│    - mart_formula_horizon_evidence (per formula × hp 全市场)         │
│    - mart_stock_trend (主 alpha 88 列, 但 ⚠ latest 快照无 PIT)       │
│    - fact_optuna_governance_log (reject 审计)                        │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. paper_sim selector (3 mode):                                      │
│    - "backtest" 单公式排名 (按 mart_per_formula_stage.oos_sharpe)    │
│    - "ensemble" 10 alpha zscore 加权 + regime gate (Phase ψ.β.4)     │
│    - "production" 走 mart_daily_position_recommendation (实盘)        │
│    选 top 5 + 流动性过滤 (vol_60d ≤ 40% / amount_20d ≥ 5000万)       │
└──────────────────────────────────────────────────────────────────────┘
        │ T+1 VWAP 入场
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. simulate_trade (services/backtest/realistic_engine.py):           │
│    - T+1 入场 (buy_offset=1, 一字涨停延迟 1 次)                      │
│    - 5 出场触发: stop_loss > target_arm > trailing > hp_expired      │
│         > stage_deterioration                                        │
│    - 含 tx_cost (佣金 0.025% + 印花税 0.05% + 滑点 0.1%)              │
│    - 含涨跌停 reject_buy (一字涨停不买) / 退市暂停过滤                │
└──────────────────────────────────────────────────────────────────────┘
        │ 每日 NAV 更新, swap 决策, 跨日 trailing arm
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. paper_sim 输出 + KPI:                                             │
│    - fact_paper_sim_nav (NAV 时序)                                   │
│    - fact_paper_sim_position (持仓快照)                              │
│    - fact_paper_sim_trade (BUY/SELL/SWAP_OUT/SWAP_IN)                │
│    - mart_paper_sim_kpi (6 类 KPI: A 用户标准 / B anti-churn         │
│         / C robustness / D ablation / E sensitivity / F reality)     │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ 决策: 6 类 KPI 全过 → 上线 / 一类不过 → 不上线
┌──────────────────────────────────────────────────────────────────────┐
│ 7. 实盘上线 (待 — 还没满足用户 +30%/-20%/超额 HS300)                  │
└──────────────────────────────────────────────────────────────────────┘
```

## 1. 三个 DuckDB 数据库

> 权威清单 = `backend/config/database_manifest.yaml` (含 retention_class 生命周期分类, 见 db_management_design §13)。
| DB | 路径 | 用途 | retention_class |
|---|---|---|---|
| `smartmoney.duckdb` | `data/smartmoney.duckdb` | **2.5G / 85表** (2026-06-14 地基-reset: 删整个模型/特征/寻优层144表, 只留基础数据+纯K线中间+档案展示+治理; 26.6→2.5G; 参数寻优重做; 退役实验知识→config/experiments/retired_experiments.yaml) | production_control(地基) |
| `market.duckdb` | `data/market.duckdb` | K 线 + 行情 (`v_price_kline_qfq`) | canonical_source |
| `tushare_raw.duckdb` | `data/tushare_raw.duckdb` | TuShare raw 镜像 (raw_tushare_*), sync_runner 独占写, 写锁隔离 | canonical_source (mirror) |
| `alpha158.duckdb` | (planned, 旧panel 2026-06-14 删) | qlib Alpha158 K线因子库; 旧 panel(418万行/3.5G, PIT不可信)删, 验证Alpha158时干净重算+pit_guard核证 (manifest planned; daily_update Step2c重建循环已切) | rebuildable_feature |
| `etf.duckdb` | `data/etf.duckdb` | ETF 专用 | governed_source |
| `experiment_store.duckdb` | active (S0 建, 执行器接入) | alpha 验证实验输出 (verdict/IC scan/lineage/pit_audit), 与 live 隔离; 写入器=experiment_consumer_alpha_validation.py | transient_experiment |
| `data/scratch/*.duckdb` | (约定) | 测试/探索一次性库, 用完即删, gitignore | disposable_scratch |

**约束** (AGENTS.md / engineering governance DuckDB 段):
- 永远走 `services.duck_adapter.connect` / `services.db.get_conn`
- 单写锁, 一次 ATTACH, 不要直接 `duckdb.connect()`
- raw `duckdb.connect` 允许清单现在 config-owned (`backend/config/duckdb_connect_policy.yaml`) 用于跟踪历史 call sites；新增生产 raw connect line 由 `backend/scripts/check_rule_compliance.py` 默认阻断，确需例外必须有同行/上一行 evidence 注释并进入 review。
- 新增 `data/*.duckdb` / `.duckdb` 文件名字面量默认阻断；DB 路径应进入 `backend/config/database_manifest.yaml` 或专属 config。

---

## 2. 数据资产 — 6 大维度 (完整盘点)

> ⚠ Claude 容易误以为"项目主要数据是 K 线". 错. 6 大维度全有.

### 2.1 大盘 / 指数

| 表 / 字段 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `v_price_kline_qfq` (market.duckdb) 含指数 K 线 | 5.97M 行 / 6,618 股 / 2022-01 → 2026-05 | 实时 | tdxhub 备援源视图; 只 2022+ 且 2022-12-30 复权 glitch; 回测主源已切 ↓ |
| `price_kline_qfq_tushare` (market.duckdb) **回测前复权主源** | 856万行 / 5755 股 / **2019-01 → 2026-06** | build_price_kline_qfq_tushare.py | 2026-06-15 §4.3 消费链切换: raw_tushare_daily×adj_factor 前复权(rebased, 单位对齐tdxhub); 与tdxhub重叠期收益对账 avg 0.03%一致(max差=tdxhub 2022-12-30 glitch, tushare正确); load_kline 已 repoint; 解锁2020+多regime回测; data_layers=L1k |
| `fact_feature_panel` (**feature_store.duckdb** L2) | **817万行 / 5427股 / 2019-01-30→2026-06-12** | build_feature_panel.py | 2026-06-15 L2 重建 (root: L2-bypass — 实验内联直读L0算因子撞写锁/不可复用): 5因子 PIT 宽表 mom_60/reversal_20/vol_20(market) / mf_trend_20(L0 moneyflow) / roe_dt_asof(L0 fina); 独立库=写锁隔离(build写feature_store, daily_update写smartmoney); 探索读此L2不读L0 (moth feature-layer-l2-bypass-ratchet + feature-panel-l2-built 守); data_layers=L2_feature; **executemany 慢 ~30min/8M行(PK逐行约束)→ 加因子前优化Arrow批插(prereg开放项B)** |
| `fact_segment_panel` (**feature_store.duckdb** L2) **F0 形态/分层面板** | **715万行 / 5595股 / 2020-01→2026-06** | build_segment_panel.py (+config segment_panel.yaml) | 2026-06-16 F0: 直读 price_kline_qfq_tushare(绕 tdxhub 视图复权坑)复用 classify_technical_stage 物化 PIT 形态轴 — stage(Weinstein5态)+range_pos(位置)+dif/dea/macd_hist/macd_above_zero(MACD零轴)+board; forward 不入表(profiling即时算防 outcome-as-feature); Arrow批插写0.5s/71K。**F0 裁定**(form_survey_20260616.md): 5态作单一标签 RankIC≈0(死亡条款触发)。**F1 诊断**(measure_form_separation.py + f1_form_redesign_20260616.md): range_pos→fwd10 全样本 RankIC **-0.043(反转)**, regime 翻转(中盘高波-0.054→大盘低波+0.011) — 正交轴+vol-regime条件化方向验证(George-Hwang JF背书); IC≠利润待含成本backtest; data_layers=L2_feature |
| `fact_rally_ground_truth` (**smartmoney.duckdb** L1) **D1 主升浪 ground truth 标签y** | **43,202 突破事件 / 4,345 TRUE主升浪(10.1%) / 5516股 / 2019-04→2025-05** | rally_ground_truth_scan.py --end 2025-05-31 --land (从git找回+改读clean tushare) | 2026-06-16 监督式 episode-first D1: 突破=close>前60日high, 主升浪=突破后60-180天≥50%涨幅 AND max_dd>-20%(读法B+60日冷却, S1复现99.92%); 买点=event_date=t(PIT锚, 特征<=t/label=t+1..180后验); TRUE中位涨幅75.8%(对原锚75.5%)峰位62日; **D2(analyze_episode_forms.py)买点形态弱判别(低位lift1.16/stage~1.0); D3(analyze_episode_trajectory多窗量价/analyze_episode_richfactors资金筹码/experiment_rally_multivariate多因子LightGBM)单因子+浅多因子全弱: OOS AUC 0.51≈shuffle null 0.48/top-decile 1.14x → 主升浪买点在此深度难预测, 待Optuna+Modal系统化搜(DSR-gated)重检**; data_layers=L1_foundation |
| `raw_tushare_moneyflow` (tushare_raw) | **738万行 / 5620股 / 2020-01-02→2026-06-12 [DONE]** | sync_runner --domain moneyflow --backfill | 2026-06-15 用户"拉齐2020"回补完成: data_start 20220104→20200101 + min_rows 4000→3000(2020 universe~3740股); .venv/bin/python + source .env (env PATH双前提) |
| `raw_tushare_moneyflow_dc` (tushare_raw) **东财个股资金流** | **384万行 / 6219股 / 2023-09→2026-06 / 665日 [DONE]** | sync_runner --domain moneyflow_dc --backfill | 2026-06-16 用户"全拉初评有用数据": net_amount/net_amount_rate 东财口径(补 order-size moneyflow); 实测起点~2023-10(东财个股近年才有, data_start 20230901, 前置~20空日 ok:false 但真数据完整); rate=分钟级150/200无日上限 |
| `raw_tushare_index_member_all` (tushare_raw) **申万行业 PIT** + `v_sw_industry_pit` 视图 | **7787行 (5847当前Y + 1940历史剔除N) / out_date填1940 / 同股多区间1609** | sync_runner --domain index_member_all(_hist) + build_sw_industry_view.py | 2026-06-15/16 **行业迁移 S1+S2**: S1 原只拉 is_new='Y' → out_date 100% NULL = latest-snapshot leakage; 加 `index_member_all_hist` 域 (is_new='N' 补真 PIT 区间)。**S2** 建 `v_sw_industry_pit` as-of 视图。**S3** [DONE 2026-06-16] live serving 切申万: build_sw_industry_view.py 加建 smartmoney `dim_stock_sw_industry` 当前快照(5847股, tdx_l* 列名=位置别名/值申万, L1_foundation); industry.py INDUSTRY_TABLE→dim_stock_sw_industry; signals_v2 7 处 JOIN 走 {INDUSTRY_TABLE} 常量(no-hardcode); resolve_industry ref_date 缺陷标注(serving=当前, as-of走视图)。验证 59测试pass/moth32/load_industry_map返申万。**S4** [DONE] 删 STALE 孤儿 mart_stock_industry_pit+quality (4消费者全guard降级/移声明/residue0)。**S6** 初次双轨: 申万5847>通达信5624股, taxonomy不同非系统错位(迁移sound)。**S7** 通达信降tdxhub热备不物删(§4.3)。**迁移功能完成** (serving+探索+KPI全申万PIT)。剩跟进: S6完整1周/S8 index_classify/申万readiness面板重建 (owner=analysis/industry_migration_tdx_to_sw_20260615.md; 06-11 ANOVA 已定 申万L2 主口径)。taxonomy 桶 13→31 历史不可比 (§4.5) |
| `fact_regime_state` | 775 行 / 2023-02 → 2026-04 [PASS] | 历史可用 | trade_date / regime_id / regime_label (bull/bear/sideways) / regime_prob_json / transition_signal |
| `dim_market_segment` | dim 表 | 静态 | 市场分段 |

### 2.2 行业 / 板块

| 表 | 数据量 | freshness | 用途 |
|---|---|---|---|
| `dim_stock_sw_industry` | dim | 静态 | 申万行业映射 |
| `dim_stock_tdx_industry_history` | dim history | PIT | 通达信行业 PIT 映射 |
| `fact_stock_industry_context` | 个股行业上下文 | 取决于跑批 | 衔接 sector_momentum 到个股 |
| **`mart_sector_momentum`** | **⚠ 只 41 行 / 2026-04-17 → 2026-05-13** | ⚠ **没历史, 不能历史回测** | sector_name/code/level, ma20/60, macd, momentum_score, return_1m/3m/6m/12m, excess_1m |
| `mart_industry_pit_quality` | ? | PIT | 行业质量 |
| `mart_stock_industry_pit` | ? | PIT | 个股行业 PIT 评分 |
| `mart_institution_industry_stat` | ? | — | 机构 × 行业统计 |
| `research_inst_industry_performance` | 6,564 行 | — | 机构 × 行业 win_rate_10d/30d/60d/120d, avg_gain_10d/30d/60d/120d |

### 2.3 机构跟随 (项目主 alpha, **权重 0.40**)

| 表 | 内容 |
|---|---|
| **`mart_stock_trend` (主 alpha, 88 列)** | inst_count_t0/t1/t2 / inst_cap_t0/t1/t2 / inst_trend / cap_trend / latest_events / external_attention_signal / **stock_gate** / turtle_setup_state |
| `fact_institution_follow_backtest` | cohort × params Grid 回测 (**已 train/holdout 切分** — split='train'/'holdout', cohort_scheme='institution_L2_pit_20240930') |
| `fact_institution_event` / `fact_jgdy_event` | 机构调研事件 |
| `mart_institution_industry_stat` | 行业级机构统计 |

### 2.4 基本面 / 质量

| 表 | 内容 |
|---|---|
| **`fact_stock_archetype` (22K 行 / 53 列)** | snapshot_date / **net_profit_positive_8q** / **operating_cashflow_positive_8q** / revenue_yoy_positive_4q / profit_yoy_positive_4q / eps_yoy_positive_4q / **high_quality_hits** / growth_hits / cycle_flags |
| `fact_financial_derived` / `fact_fundamental_quarterly` | 财务衍生 / 季度 |
| `fact_stock_fundamental_stage_daily` | 基本面阶段 daily |
| `fact_stock_quality_features` | 质量特征 |
| `raw_aif10_financial_history` / `raw_gpcw_detail` / `raw_tdx_gpcw_wide` | 财务原始 |
| `raw_aif10_valuation_quantile.percentile_fifty` | 估值 10Y 分位 (strategy_ensemble 在用) |
| `raw_aif10_forecast_consensus.compre_rating_num` | 一致预期评分 (strategy_ensemble 在用) |
| `raw_aif10_peer_valuation` | 同业估值 |
| **`raw_tushare_forecast`** (业绩预告, 2026-06-14 接入) | **PEAD 预期差事件因子** (alpha 验证程序 S1 第一个基本面接口): type(预增/预减/扭亏/首亏) + p_change_min/max(净利变动幅度) + net_profit_min/max + ann_date(PIT 锚, 早于正式财报). grain=[ts_code,end_date,ann_date]; 实测 17042 行 (2023-2026) |
| **`raw_tushare_income`** (正式利润表, 2026-06-14 接入) | 96 列全套利润表 (total_revenue/revenue/oper_cost/各费用/operate_profit/n_income/ebit/ebitda...) = 质量/成长因子料 (PEAD 后段慢信号). grain=[ts_code,end_date,f_ann_date,update_flag] (uf=0原始/1订正双推送), PIT 锚 f_ann_date 取 uf=1; by_trade_date date_param=ann_date; 实测 4月 10578 行/5305 股. **express/fina_indicator 已注册** (express=express_vip by_period [sync_runner 加 by_period 分支+单测]; fina_indicator=by_ts_code 2023-2026窗口避100条截断), 回填排队 income 后 (单写锁) |
| **`raw_tushare_balancesheet_advrecv`** (预收账款/合同负债, 2026-06-16 注册) | 用户提议"预收账款"前瞻需求因子: adv_receipts + contract_liab (2020 后迁入) + total_assets. PIT 锚 ann_date; by_period (V0 取每期最新修订). **当前落库 7 期非连续止 2020Q3 = 不可用** (allow_empty=true 旧配置静默吃间歇空响应 + 配额墙截断双因); 已配 allow_empty=false + min_rows_per_batch=1000, 待配额恢复重拉连续季报. debate 裁决档C: 修源前禁入 panel |

### 2.5 资金流 / 事件

| 表 | 内容 |
|---|---|
| `fact_hsgt_daily` | 北向资金 daily |
| `raw_lhb_daily` / `fact_lhb_event` | 龙虎榜 |
| `raw_fund_flow_daily` | 主力资金流 daily |
| `fact_executive_trade_event` | 高管增减持 |
| `fact_shareholder_trade` / `fact_shareholder_trade_tdx_b` | 股东交易 |
| `fact_holder_event` / `fact_top10_holder_period` / `fact_holder_count_period` | 持股人结构 |
| `fact_dzjy_event` | 大宗交易 (旧源) |
| **`raw_tushare_block_trade`** (大宗交易, 2026-06-16 注册) | 用户提议: 机构折价/大单方向, stage 内 alpha 增强候选 (moneyflow 抓不到的机构维度). grain=[ts_code,trade_date,price,vol] (同股同日多笔全留), PIT 锚 trade_date (盘后披露, 决策用 t-1); by_trade_date 2023+. **表未建** (配额墙), 配置就绪待拉. debate 裁决档B: 做事件 confirmation 不做连续因子 |
| `raw_capital_*` (allotment/dividend/repurchase/unlock) | 配股/分红/回购/解禁 |
| `raw_institution_surveys` | 机构调研 raw |
| `raw_qfii_holding_quarterly` | QFII 季度持仓 |

### 2.6 技术 / 形态 / 信号

| 表 | 内容 |
|---|---|
| **`fact_signal_context`** | stock × date / vol_r20 / amt_r20 / amount_20d_avg / price_pos_60d / price_pos_120d / drawdown_60d / **technical_stage** (1/1.5/2/3/4) / built_at |
| **`fact_stock_technical_stage`** | Stan Weinstein 4 stage (1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌) |
| `fact_stock_stage_features` | 阶段特征 |
| `fact_stock_turtle_features` | 海龟特征 |
| **`fact_technical_trigger`** | 公式信号触发 (stock × date × formula_id × variant × strength × state × reason_codes_json) |
| `fact_stock_archetype` (53 列) | 形态原型 (跟基本面共用此表) |
| `fact_setup_snapshot` | ⚠ **0 行 / 未启用** |

### 2.7 Phase ψ 治理 / 调参产物

| 表 | 用途 |
|---|---|
| **`mart_per_stock_stage_strategy_optimal`** | per-stock × variant × stage Optuna 寻优 (Phase ψ R1 后含 OOS 列, 但稀疏信号下大量 governance reject) |
| **`mart_per_formula_stage_optimal`** (Phase ψ.α B) | per-formula × stage × train_end_date 严格 walk-forward 寻优 (反转因子用此表) |
| `mart_formula_horizon_evidence` | per (formula × hp) 全市场合并真实历史涨跌 (无 Optuna 调参, 最干净) |
| `mart_stage_formula_fitness` | cohort fitness (fund × tech × formula × hp) |
| `mart_stock_formula_optuna_v2` | 旧 per-stock × formula × hp 全宇宙 (337K 行) |
| `fact_optuna_governance_log` | Phase ψ governance reject 审计 |
| `mart_market_perception_daily` | Market Perception P1 daily snapshot: regime_score / breadth_state / volatility_state / sentiment_phase, PIT cutoff and built_at |

---

## 3. Service 模块 (231 个 .py 文件, 21 个子包)

### 3.1 调参 / 寻优 (Phase ψ)

| 模块 | 文件 | 作用 |
|---|---|---|
| `services/optimization/` | config.py | yaml loader (governance/walk_forward/search_space/composite/constraints/execution/output) |
| | governance.py | enforce_pre_optimize / enforce_pre_insert (50≤n_trials≤500, sharpe ≤ 5, win ≤ 0.95) |
| | walk_forward.py | split_dispatch (none/holdout/expanding/expanding_monthly/**train_end_forward**) + assert_no_temporal_leak + list_month_ends |
| | oos_aggregator.py | aggregate_oos_metrics (multi-window OOS trades 合并) |
| | composite.py | CompositeWeights.from_config() (7 个权重 ∑=1.0) |
| | constraints.py | HardConstraints (max_dd, streak, worst_loss, min_traded) |
| | objectives.py | 8 个 metric (sharpe/calmar/sortino/pain/ulcer/tail/stability/cvar) |
| | ddl.py | mart_per_stock_*_optimal / mart_per_formula_stage_optimal / fact_optuna_governance_log DDL; log_governance_violations(**manage_txn**: False=与业务表同事务原子提交防 orphan governance, 06-14 D0 发现) |
| `services/backtest/` | optimize.py | optimize_stock_strategy (R1 expanding_monthly 主流程) |
| | realistic_engine.py | simulate_trade (T+1 入场, intraday stop/target, 含 tx_cost) |
| | search_space.py | 5 维 SearchSpace.from_config() (hp/stop/target/trailing/buy_offset) |
| | objective.py | make_objective Optuna 目标函数工厂 |
| | filters.py | is_index_code 等 |

### 3.2 公式 (formula_engine, 4+3 = 7 公式)

| 公式 | 文件 | 类型 |
|---|---|---|
| macd_golden_cross | macd_golden_cross.py | 动量 (DIF 上穿 DEA, variant=above/below_zero, **裸金叉无量能**) |
| turtle_breakout_20/55 | turtle_breakout.py | 动量 (突破 + **量能 > MA20 × 1.3**) |
| dynamic_ma_iterative_cross | dynamic_ma_iterative.py | 动量 (用户 MQL, 4 均线 + 加权重心 + **1 轮迭代过滤假突破**) |
| **reversal_1m_mild** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 4-15% + 60 日低波 + 量比正常) |
| **reversal_1m_deep** (Phase ψ.α) | reversal_short_term.py | **反转** (20 日跌 10-30%) — **主 alpha (sharpe 1.1 horizon / 0.39 walk-forward)** |
| **reversal_1w** (Phase ψ.α) | reversal_short_term.py | **反转** (5 日跌 2-10%) |
| technical_stage (4 stage) | technical_stage.py | classify_technical_stage(closes, volumes) — Stan Weinstein |

### 3.3 多 Alpha Ensemble (strategy_ensemble.py)

**5 alpha 源 + 加权综合** (paper_sim 目前**没用**, 这是设计意图):

| Alpha | weight | 数据源 | 类别 |
|---|---|---|---|
| **institution_follow** | **0.40** | `mart_stock_trend.action_score` | 资金流 (主 alpha) |
| valuation_pct_low | 0.20 | `raw_aif10_valuation_quantile.percentile_fifty` | 基本面价值 |
| forecast_consensus | 0.15 | `raw_aif10_forecast_consensus.compre_rating_num` | sell-side analyst |
| momentum_120d | 0.10 | `fact_risk_factors.mom_120d` | 技术 |
| risk_adjusted_sharpe | 0.15 | `fact_risk_factors.sharpe_60d` | 风险调整 |

### 3.4 Paper Sim v2 (Phase ψ)

| 模块 | 作用 |
|---|---|
| `services/paper_sim/config.py` | yaml loader (portfolio / selection / exit / swap / tx_cost / risk / validation / data) |
| | selector.py | backtest mode 查 mart_per_formula_stage_optimal (Phase ψ.α B), 0 selection leakage; **Phase ψ.β.5 L2**: ensemble mode 可按 vol_60d 缩放 stop/target/trailing per-stock (`_vol_aware_params`, config flag `selection.vol_aware.enabled`); **Phase ψ.γ.2 L3**: ensemble mode 可 JOIN mart_per_stock_stage_strategy_optimal (24K 行 9-dim OOS) 用 per-stock × stage params 覆盖 default (`_load_per_stock_stage_optimal`, config flag `selection.per_stock_stage.enabled`). 优先级: per_stock_stage > vol_aware > default_holding. |
| | driver.py | walk-forward 主循环 + VWAP 成交 + swap 决策 |
| | exit_rules.py | 5 触发优先级 (stop > target_arm > trailing > hp_expired > stage_deterioration) |
| | swap_rules.py | compute_fulfillment / candidate_can_close_gap / evaluate_swap |
| | sizer.py | wilson_kelly position sizing |
| | tx_cost.py | 佣金 + 印花税 + 滑点 |
| | reporter.py | 6 类 KPI (A 用户标准 / B anti-churn / C robustness / D ablation / E sensitivity / F reality_check) |
| | ddl.py | 4 张 paper_sim 专表 (nav / position / trade / kpi) |

### 3.5 候选 / 推荐 / 选股

| 模块 | 作用 |
|---|---|
| `services/buy_signal/` | classify_tier + factor_aggregator + scoring + reasoning + configs + ddl — **6 因子综合 score, 输出 mart_stock_formula_buy_signal_daily** |
| `services/selection/` | logger / outcome / feedback / summary — 选股事件追踪 |
| `services/portfolio_walk_forward/` | metrics.py (CAGR / sharpe / max_dd / calmar / monthly_win_rate), liquidity, ... |
| `services/portfolio_sizer/` | profiles.py 不同风格 sizing |
| `services/trade_plan/builder.py` | 交易计划生成 |
| `services/candle_pattern/` | features (6 维 + 1 突破强度) / evaluator / search_space (4 维 Optuna 阈值) |
| `services/market_perception/` | Market Perception P1: `compute_regime_for_date/range`, PIT-strict market context features written to `mart_market_perception_daily` |

### 3.6 机构 / 行业 / 阶段

| 模块 | 作用 |
|---|---|
| `services/institution_l2_metrics.py` | institution_l2_score_cte (train_best/holdout pair CTE) |
| `services/institution_read.py` / `institution_scoring_read.py` / `institution_write.py` | 机构数据 R/W |
| `services/industry_context_engine.py` | sector_momentum 衔接到个股 fact_stock_industry_context |
| `services/industry.py` / `industry_pit.py` / `industry_overview_read.py` | 行业 PIT + UI 读取 |
| `services/stock_stage_engine.py` | 阶段特征中间事实层 |
| `services/stock_turtle_engine.py` | 海龟形态特征 |

### 3.7 数据源 / 客户端 / sync

| 模块 | 作用 |
|---|---|
| `services/data_sources/` | base / clients_registry / data_routes / fallback / registry — 数据源中央 |
| `services/akshare_client.py` / `tdx_*_client.py` / `block_client.py` / `capital_client.py` / `lhb_client.py` / `xdxr_client.py` / etc. | 各种数据源 client |
| `services/kline_source.py` / `market_db.py` | K 线源 + market DB 入口 |
| `services/duck_adapter.py` / `db.py` / `db_health.py` | DuckDB 安全包装 |
| `services/source_watermarks.py` / `source_policy.py` | sync watermark + policy |

### 3.8 其他

- `services/sentiment/` — **情绪因子框架** (factor_registry + bin_assigner + window_calculator + survey_builder). 未集成到主选股
- `services/external_attention.py` — 关注度因子 (`external_attention_score` 已写入 mart_stock_trend)
- `services/event_simulator.py` / `event_engine.py` — 事件模拟引擎 (用于机构跟随 backtest)
- `services/shareholder_plan_*` (3 文件) — 股东计划相关 alpha
- `services/feature_registry.py` / `feature_labels.py` / `feature_retention.py` — 特征工程
- `services/data_lineage/` — 数据血缘
- `services/ml_lifecycle/` — drift / registry
- `services/etf_*` — ETF 子系统 (独立, 不影响个股 alpha)
- `services/trading_config/` — 真实执行模型 (buy_pricing / sell_pricing / slippage / filters / execution_model)

---

## 4. Scripts 入口 (135 个)

> 机器枚举的完整入口/产表/依赖清单 → `FEATURE_MAP.md` (`scripts/chunkyctl map` 重生成,
> 勿手改)。本节只保留人工策展 (哪些重要/怎么用/坑在哪), 计数以 FEATURE_MAP 为准。

按主题分组:

| 主题 | 数量 | 例子 |
|---|---|---|
| `build_*` | 49 | build_formula_signals_history, build_signal_context, build_stock_formula_buy_signal_daily, build_daily_position_recommendations, build_picture_daily, build_stage_formula_fitness, build_architecture_inventory |
| `formula_*` | 1 | **formula_limit_up_pullback.py** (涨停回调十字星选股, S/A/B 三档, YAML 配置 `config/formula_limit_up_pullback.yaml`) |
| `run_*` | 17 | run_paper_sim_v2 (我们主用), run_follow_backtest (机构跟随), run_optuna_*, run_portfolio_mvp |
| `validate_*` | 10 | validate_exclusion_rules 等 |
| `audit_*` | 5 | **audit_end_to_end.py** (23 项检查) |
| `backfill_*` | 5 | 各种回填 |
| `optimize_*` | 4 | **optimize_per_stock_stage_strategy.py** (Phase ψ R1), **optimize_per_formula_stage.py** (Phase ψ.α B), **optimize_ensemble_full.py** (Phase ψ.γ.1, **20 维 ensemble Optuna**: 13 alpha weights + 2 regime + 3 sigma + hp + max_vol, constrained sharpe, holdout train/test, mart_ensemble_optimal 入库) |
| `rebuild_*` | 2 | rebuild_stage_formula_fitness |
| `replay_*` | 2 | replay_paper_history_signflip |
| `evaluate_*` / `train_*` | 4+2 | 各种评估 + 训练 |
| `cron_*` | — | cron_daily.py (HTTP wrapper for sync) |

### 4.1 主流水线 (顺序严格)

```
1. optimize_per_stock_stage_strategy.py    Optuna 9-dim per (stock × variant × stage)  ~16 min
   或 optimize_per_formula_stage.py        Phase ψ.α B 全局 walk-forward          ~28 min
2. rebuild_stage_formula_fitness.py        fitness 聚合                          ~1s
3. build_stock_formula_buy_signal_daily    buy_signal × technical_trigger        快
4. build_daily_position_recommendations    最终推荐 + 价格                       快
5. audit_end_to_end.py                     23 项检查 (0 FAIL 才算通过)           ~1 min
6. portfolio_backtest.py / run_paper_sim_v2.py   walk-forward NAV + KPI         30 min
```

---

## 5. Routers / API (17 个)

| Router | 主功能 |
|---|---|
| `routers/recommendation.py` | 选股推荐 API |
| `routers/screening.py` | 筛选 |
| `routers/signals.py` | 信号 |
| `routers/institution.py` | 机构数据 |
| `routers/market.py` | 行情 |
| `routers/etf.py` | ETF |
| `routers/updater.py` | sync 入口 (POST /api/inst/update/smart) |
| `routers/workbench.py` | 工作台 |
| `routers/strategy_preset.py` | 策略预设 |
| `routers/v3_*` | v3 系列 (meta / paper / picture / portfolio_builder / selection / views) |

---

## 6. Config 文件 (yaml)

| 文件 | 控制什么 |
|---|---|
| `backend/config/optuna_config.yaml` | Optuna 治理 (Phase ψ Rule 7/8) — governance/walk_forward/search_space/composite/constraints/execution/output |
| `backend/config/paper_sim_config.yaml` | Paper Sim v2 hyperparam |
| `backend/config/paper_sim_momentum.yaml` / `paper_sim_reversal.yaml` / `paper_sim_reversal_deep_only.yaml` | Phase ψ.α ablation 切换 |
| `backend/config/paper_sim_ensemble.yaml` | **Phase ψ.β.4** ensemble 模式 (13 alpha + regime + vol_aware + per_stock_stage) |
| `backend/config/field_dictionary.yaml` | **Phase ψ.γ.dict.1** 字段字典 (3 DB × 12 核心表 × 100+ 字段 + 单位 + PIT key + outlier cap + JOIN 模板) — 防 VWAP unit bug 类故障 |
| `backend/config/recommendation_universe.yaml` | 选股宇宙 |
| `backend/config/db_partition_tiers.yaml` | **DB 多库分区 tier** (源/特征/服务/实验) + 原子写簇 (关联性检查); 驱动 `backend/scripts/db_partition_migrate.py` (保真迁移引擎: 原 DDL 含 PK + INSERT SELECT, 非 CTAS; dry-run 默认 + 前后验证[行数/EXCEPT/约束/索引] + 绝不 DROP 源; D1a experiment_store 25 表迁验 PASS [暂缓 repoint, live 耦合重]; **D2-minimal feature_store 2 表 fact_feature_panel+validation 迁验 PASS** [解决 build_feature_panel vs daily_update 写锁竞争, repoint 待定]) — owner=analysis/db_management_design_20260614.md |
| `backend/scripts/db_compact.py` | **整库保真缩盘** (删行后回收盘): ATTACH-copy 逐表原 DDL 含 PK + INSERT + 重建索引 + 视图按定义重建 (依赖容忍重试), **绝不 CTAS** (避 06-12 约束 315→1); dry-run 默认; 验证前 DETACH src (information_schema/约束/索引跨 attach 库会双计) + 逐表行数对账全等才换名, 旧库留 `_precompact_bak`。2026-06-14 实测 smartmoney 26.6G→17.5G (-34%, 333表/4视图/821约束/333索引全等) — owner=db_management_design §13.4 |
| `backend/scripts/db_dead_table_audit.py` | **死表守门** (0行 AND 0字面引用才判死, 保守防误删); 大表过时判定走 lifecycle 分析非本工具 — owner=db_management_design §12 |
| `backend/scripts/db_lifecycle_delete.py` | **生命周期删除执行器** (可复用): 读删除 manifest, 4 道闸 — (1) live守护 word-boundary grep daily_update脚本集+serving/ensemble/routers, 命中REFUSE (`--force` 跳过用于有意删 live 层如地基-reset); (2) action=archive 先 COPY parquet 再删 (drop 则不归档); (3) mart_data_deletion_record 留痕; (4) 残留扫描悬挂视图 + view 处理 + 周期 CHECKPOINT 防 catalog stale。dry-run默认。2026-06-14 地基-reset 删 144 表/视图 — owner=db_management_design §13.6 |
| `backend/config/data_layers.yaml` + `backend/scripts/data_layer_audit.py` + `backend/services/schema_layer_filter.py` | **数据层级框架** (2026-06-14 地基-reset 后立, owner=docs/data_management_framework.md): 8层声明式注册表(L0_source/L1_foundation/L1k_kline/display/infra/L2_feature/L3_model/L4_experiment), 144表全声明layer=单一真相源根治"层级隐式→反复推导+耦合"; audit `--check` 未声明=FAIL强制新表声明; schema_layer_filter 让 schema-init 只建活层表(梳理"删表后启动空重建"recreation loop, 接 schema_core/marts/migrations); moth 断言 data-layer-integrity/minimal-module-main-routers/no-new-godfile 自动执法。**2026-06-15 扩 feature_store 纳管**: audit `_live_tables` 从只扫 smartmoney → 扫 MANAGED_DBS=(smartmoney,feature_store), 否则 L2 分区(fact_feature_panel)静默不受层级执法; fact_feature_panel 声明 L2_feature, L2 层 status partial_rebuild |
| `backend/scripts/check_legacy_flow_integrity.py` | **老流程污染防回潮 gate** (2026-06-14 工具化 reset 6 教训, owner=framework §6): C1 daily_update 无缺失脚本调用(删层必删caller, 防静默degraded假活)/C2 无 wiped 表孤儿引用(238处实测)/C3 append-only(*_history/*_snapshot)必 storage_retention 声明(防无界膨胀=DB巨大根因)。覆盖 schema_layer_filter 之外的污染面(daily_update/散落DDL/config)。moth `legacy-flow-no-pollution` 守; **重构验收 gate**: 重构前红=问题实锤, 老daily_update退役+清孤儿+加retention 转绿。进度 (owner=analysis/refactor_execution_plan_20260614.md): 2026-06-14 A2 完成→**C3 append_only_retention PASS** (3表 retention 声明: dim_stock_tdx_industry_history/raw_profit_forecast_snapshot_daily/raw_tdx_industry_file_snapshot); A3d gate 精度修 (grep 加 -w 词边界防 substring 假阳性[fact_shareholder_plan 误匹配活表 _tdx_f10] + -I/--exclude-dir 跳 __pycache__ 二进制, 238→179); A3a 删 2 真孤儿 config (model_search/champion_registry)→C2 残 149; **A1 daily_update 重写 855→457 行 (删 Step4-8 model/paper_sim/champion + 19 缺失脚本调用, 保留 sync/L1k macd + 加 retention plan/data-health report; DRY 实跑通过)→C1 PASS**; A3b 退役 7 死 serving router (v3_market_perception bundled fallback / recommendation / institution / screening / v3_meta / v3_views / v3_perception_legacy + main.py 注册, app import OK 124 routes)→**C2 149→70**; A3c schema_versions 删 23 wiped version 条目 (版本注册表非 DDL; import/summary 验证 220→195) + 7 config 18 处 wiped ref 加 @archived 标记 (gate 认可豁免; 表均核实 wiped+DB 不存在; yaml 全 valid)→**C2 70→29**; 余 updater_* 死 feature 步骤(29, 子系统被 data_sources/etf live import 故外科清非整体退役) 待。A5 bloat: 删 phase5.duckdb 57M+phase5_exports 101M 死 model 工件 + manifest 去 phase5 分区; archive/ 3.4G reset 回滚网保留待重建 KPI 验证后用户定。**2026-06-15 C2 gate 修(重建表识别)**: `_live_tables`(复用 data_layer_audit, managed-DB live 集) 排除已重建为 live 的 wiped 层表 — fact_feature_panel 重建后 layer 仍 L2 但已 live, 不再误判孤儿(否则其 manifest/config 引用刷爆 stale 41>29); C2 stale 28<=29 ratchet PASS |
| `backend/scripts/check_strategy_validation_integrity.py` | **策略验证完整性 gate** (2026-06-15 P0 制度先行, 8-lens 对抗复审根因 R1/R2 + 判断法典 C-WinReturn 反哺; owner=docs/strategy_validation_contract.md 判断法典节): 4 检查 — anomaly_symmetric(C-R1: experiment_harness 有 tradability_verdict 对称门)/promotion_needs_money(C-R1: record_verdict 拒无含成本证据转正)/kpi_joint_codex(C-WinReturn: kpi_verdict 联合年化+max_dd+胜率×盈亏比)/engine_execution_aware(C-R2: 单一引擎含涨跌停/非对称成本/容量/T+1 open)。验证器纪律 mythos §13: 引擎检查取单文件全维满足非多文件并集 (防旧 portfolio_backtest 残留 marker 污染)。**P0 gate=P1 引擎验收尺**: engine 检查在引擎重建前 FAIL=预期红色规格。moth `validation-r1-symmetric-gate`/`validation-promotion-needs-money`/`validation-winreturn-codex` 守 |
| `backend/scripts/audit_panel_leakage.py` + `backend/config/leakage_consumers.yaml` | **泄漏审计 + 消费者注册表** (2026-06-15 post-reset 去硬编码): audit_panel_leakage 原硬编码 default 目标 (mart_p0a_v4[wiped]/build_feature_panel_duck.py[已删]) → **改 config 驱动** (读 leakage_consumers.yaml `audit_panels`, 空=PASS 无幻影审计; CLI `--panel` 仍可显式覆盖), 修"能不硬编码就不硬编码"违纪 + 解 Step3.5 幻影 BLOCK。leakage_consumers post-reset 对账: `consumers: []`(3 历史消费者脚本+面板 reset 全删)/`audit_panels: []`(旧SQL面板已wipe; fact_feature_panel 是 Python builder+code/date schema, SQL-JOIN 审计不适用)。experiment-discipline moth 门加识别 phaseD_signal_eval.evaluate_signal(共享 harness 满足留档+anomaly)+check_split_discipline(leakage门)。待: dim_stock_tdx_industry 非PIT(通达信)→tushare申万PIT 行业迁移 |
| `backend/scripts/build_experiment_store.py` + `data/experiment_store.duckdb` | **S0 实验台留档基建** (alpha验证程序, owner=alpha_validation_program_spec §8): 隔离 L4 库 (与 live 写锁/数据隔离防污染) 4 留档表 — fact_experiment_verdict(verdict/prereg_hash/judges) / fact_consumer_alpha_ic_scan(data_snapshot×consumer×metric PIT as-of) / pipeline_artifact_lineage(input/output hash 防回溯泄漏) / experiment_pit_audit_log(每步PIT校验); manifest active。**实验三段纪律固化 (2026-06-15 用户)**: `services/experiment_store.py` 共享留档写入器 (每实验 import+调 record_ic_cells/record_verdict/record_pit_check/record_artifact, 路径走 manifest, 防散落JSON) + `services/experiment_harness.py` (leakage_gate 事前 pit_guard 行为门不过BLOCK / anomaly_verdict 事后 §4.2 红线标 pending_ablation 不直接用/弃) + moth `experiment-discipline-tooled` 强制每个算 OOS IC 的 experiment_*.py 三段全走 (缺任一 FAIL); 5 实验全 retrofit, IC cells 留档 16→101。**R1/R2 制度化加固 (2026-06-15 P0)**: experiment_harness 加 `tradability_verdict` (C-R1 对称门: IC>0 但含成本净收益≤0→IC_POSITIVE_BUT_UNTRADABLE, 补 anomaly 单边盲点) + `kpi_verdict` (C-WinReturn 联合门: 年化 AND max_dd AND 月胜率 AND 胜率×盈亏比期望, 胜率=诊断量); experiment_store.record_verdict 加 C-R1 转正 guard (`confirmed_by_owner=1` 无含成本证据 raise). **C-LEAK 转正门 + leakage 门去自批 (2026-06-15 用户拷问"自批skip=门是摆设")**: record_verdict 加 `_has_leakage_clean` guard (confirmed_by_owner=1 须带 leakage-clean 证据[judges 含 leakage_gate/pit_audit 显 clean] 否则 C-LEAK BLOCK — commit-skip 够不到的转正门强制) + phaseD_signal_eval 把 gate 带入 judges; safe_commit Step3.5/3.6 **移除 SKIP_LEAKAGE_AUDIT 自批逃生**改硬 exit (误报=修 verifier 非 skip, verifier-only commit 不触发门=无死锁); 防御纵深=commit硬门+转正门+CI(终极). moth `validation-promotion-needs-leakage-clean`/`leakage-gate-no-self-bypass`; red→green 测试 (money但无leakage→C-LEAK raise). **P2 阶梯 R1 加固 (2026-06-15)**: experiment_harness 加 `block_bootstrap_return_null` (N1 armory: 含成本持有期收益块自助 -> P(累计<=0), 与 rank 显著性正交的绝对收益 null); Gate2 (experiment_ablation_gate2) 两级转正 (N3: REAL_EDGE->STAT_EDGE_CONFIRMED 排序统计显著非 money, confirmed_by_owner=0, money 转正须 tier2) + cohort/top-K 绝对 forward 报告 (N1); cell-scan (experiment_layered_segment_ic) 加 DSR 多重比较去偏 (N17: n_trials=实际cell数, n_eff=n_days/horizon 重叠校正 N15)。25 单测 (test_experiment_harness_codex + test_portfolio_execbacktest). owner=docs/strategy_validation_contract.md 判断法典 |
| `backend/scripts/experiment_consumer_alpha_validation.py` + `backend/config/experiments/consumer_alpha_matrix.yaml` | **S0 consumer_alpha 验证执行器** (config 驱动, reset 后重建; 复用对象 optimization/walk_forward runner 已删故新建非复活 god-dispatcher): 读 (数据x消费者) 矩阵 yaml (6 候选→7 cell, 映射铁律 event/fundamental/chip/infra→feature_ic, technical→formula_signal) + `experiment_jobs.yaml` `consumer_alpha_validation` family 契约 → gate-before-run (plan().blocked_reasons) → 枚举 cell → S0 dry 空矩阵 (不写假IC) → 写 verdict/lineage/pit_audit 留档 + verdict JSON 落 analysis/。死亡条款守: 矩阵轴走config(判断死, moth `consumer-alpha-axes-in-config-not-code`)/prereg_hash+`--check-prereg`(谄媚死)/PIT每步落档(泄漏死)/dry不造假(估计死)。IC计算留 S3。`backend/services/experiment_jobs.py` 契约loader 同恢复 (337L薄/纯yaml校验/误删, 修4处悬空import) |
| `backend/config/experiments/formula_candidates.yaml` + `l0_bare_kline_baseline_spec_20260614.md(已删·重启清理)` | **L0 裸K线基准 + 公式候选库** (用户 2026-06-14: 公式全保留为 config 备选省重建 + 裸K线寻优最佳OOS参数作基准 + **不要过拟合**): 9 公式索引 (全 ohlcv_only, 信号参数 yaml 全幸存, 评估器 macd live/其余 recoverable@639e0dfb~1), active 子集 4 (防过拟合池子小) / 其余 candidate 待解锁; L0 spec 定义=walk-forward OOS 寻优最佳参数标尺, 防过拟合第一约束 (OOS选参/DSR/pre-reg/限维度/诚实报弱, 复用幸存 optuna_config.yaml 治理; moth `optuna-require-walk-forward`/`optuna-realistic-sharpe-cap`/`l0-baseline-pool-bounded` 固化); 待重建 walk_forward OOS 引擎+治理层 (reset 删) |
| `backend/services/portfolio_walk_forward/oos_ic.py` | **L0 walk-forward OOS RankIC 核心** (Tier-1 引擎心脏, reset 删 runner 后重建): 纯函数无 DB 耦合 — forward_returns(PIT前向收益,只用未来不回看)/cross_sectional_ic(单日截面 spearman, numpy rank 不依赖 scipy, 样本<3→None)/expanding_monthly_windows(R1: min_train6月/forward1月/min_total12月)/oos_rank_ic(只用 OOS test 聚合日度IC→oos_rank_ic+ic_ir, embargo_days 切窗末跨界天[对抗审计修死闸], ic_ir 无偏 ddof=1, 无足够窗→None标unknown)。防过拟合: 选参只看 OOS 不看 train; unknown 不当 0。两层引擎共享窗口+标注原语。14 单测 (red→green PIT + 审计回归 embargo/完整窗) 入 CI |
| `backend/services/formula_engine/features.py` | **裸K线公式→连续PIT特征提取器** (L0 Tier-1): active 4 公式从核心机制派生连续特征 (MACD柱/MA距离/Donchian通道位置/反转), param 驱动读 formula_*.yaml; feature[i] 只用 bars[:i+1] (PIT); warmup→None |
| `backend/services/portfolio_walk_forward/pit_guard.py` | **PIT 行为门** (防泄露固化, 黄金标准前瞻检测): feature[i] 对追加未来 bar 不变否则=lookahead泄漏; 公式无关, 抓任何 rolling/EMA/未来引用 bug; red→green 测验它能抓植入泄漏 |
| `backend/scripts/experiment_l0_baseline.py` | **L0 裸K线基准驱动** (Tier-1 RankIC): v_price_kline_qfq→PIT特征→前向收益→walk-forward OOS RankIC→experiment_store (consumer_id=L0_baseline_<formula>)。**防泄露 3 门固化内联** (门1 PIT行为/门2 切分纪律 check_split_discipline/门3 异常红线 check_metric_anomaly, 任一失败BLOCK, moth `l0-leakage-gates-wired` 反孤儿守)。默认参数=测量; **`--search` 寻参模式** (#17 已实现): 经 plan_validator 闸+search_formula 网格寻 best-OOS-params+DSR, 写 L0_search_*。pre-reg d80e8ce 冻结+grill 后 RUN; **标尺=reversal +0.064 (lookback=20)**, 寻参佐证默认近最优 |
| `backend/scripts/experiment_multifactor_explore.py` | **多因子组合探索 runner** (2026-06-15 /loop, owner=prereg_multifactor_exploration_20260615.md(已删·重启清理)): 读 **L2 fact_feature_panel** (不读L0 raw, 写锁隔离) 同日截面 z-score 加权 signed 合成信号 → 复用 phaseD_signal_eval.evaluate_signal (含成本 execution-aware backtest R1 裁决 + trailing + 留档)。模式 single (逐因子 sign+1 含成本基线, 最诚实无拟合) / composite (子集加权, --auto-sign 用 train 窗 IC 符号定向, holdout 不碰)。事前 check_split_discipline 门 (embargo>=horizon)。**首测实证 (R1 铁证)**: reversal_20 single 2024+ OOS RankIC **+0.0608 (CLEAN, 真有排序力)** 但含成本年化 **-31.26%**/max_dd -59% → tradability_verdict=**IC_POSITIVE_BUT_UNTRADABLE** (验证空间⟂盈利空间, R1 对称门工作); 长高reversal篮=买最深超跌恰最易闷杀。消费 L2 无 in-panel label 列 (label=K线外部前向收益), 无标签透传面, leakage 控制=panel-PIT+同日截面+embargo+anomaly_verdict |
| `backend/services/portfolio_execbacktest.py` + `backend/config/backtest_execution.yaml` + `backend/tests/test_portfolio_execbacktest.py` | **Tier-2 execution-aware 回测引擎 (2026-06-15 P1 重建; 旧 portfolio_returnbacktest[clean但 R2 缺陷:close 无条件成交]已删, 旧 portfolio_backtest.py[5-07]退役标P2)**: 根因 R2 "信号!=可交易头寸"修复 — T+1 **open** 入场(非close, N14) + 涨停一字板剔篮/跌停顺延(N8/N12) + **非对称成本栈**(卖方印花, N13, config 镜像 paper_sim_momentum tx_cost) + 停牌冻结(缺价不剔篮不归零, N11) + 容量诊断(参与度 vs ADV + 大单溢价, N10, 不编造冲击系数守 measured) + **仓位 policy**(equal/rank/inverse_vol + 空槽留现金 = 连续 exposure 雏形, N4/N6); 联合 metrics(年化/max_dd/sharpe/calmar/月胜率/段胜率/盈亏比/正期望, C-WinReturn)。微结构真相源=backtest_execution.yaml(涨跌停镜像 universe_rules/dim_price_limit_rules, 成本镜像 paper_sim_momentum, 防双真相源)。14 单测手算证伪门(T+1 open/一字板剔篮/非对称成本/停牌冻结/容量/sizing/联合metrics/config加载/**trailing多窗**)。**trailing_metrics (2026-06-15 用户)**: 分 近3/6/12/18/24月/3年/5年/全期 窗口报 年化+月胜率+max_dd, 看策略趋势衰减 (全期均值掩盖: mf_trend 全期+2.53% 但近3m -27%/近24m +14.6% = 近期失效); harness evaluate_signal 自动打印趋势表+入 json。moth `validation-engine-execution-aware`/`validation-integrity-gate-green` 守; gate check_strategy_validation_integrity 4/4 PASS。**P3 实弹重裁决 (owner=analysis/p3_execution_aware_verdict_20260615.md)**: 全市场 Stage1.5 含成本年化 -14.06%/max_dd -57.3%, 小盘×高换手(IC最高)-34.69%/max_dd -80.6%, 两者 IC_POSITIVE_BUT_UNTRADABLE+KPI_FAIL (旧引擎 net-2.8% 偏乐观 ~11pp); 裸K线 reversal long-only A股结构性不可交易, Phase D 转慢衰减绝对源 |
| `backend/scripts/experiment_moneyflow_trend_alpha.py` + `phaseD_moneyflow_trend_alpha_20260615.json(已删·重启清理)` | **Phase D 第一刀: 慢衰减绝对源验证模板** (2026-06-15, owner=p3_execution_aware_verdict §4): moneyflow 大单净流入趋势 (trailing-20 净流入占总流比, PIT t-1, 月度 top20) 走全套法典 (leakage_gate→IC necessary 快筛→execution-aware backtest→tradability_verdict+kpi_verdict 裁决)。**结果方法论验证成功 (KPI_FAIL 但 R1=TRADABLE)**: IC快筛+0.0099 且含成本 net **+2.53%>0** (第一个 TRADABLE 信号, 对比 reversal IC_POSITIVE_BUT_UNTRADABLE net-14%), 成本拖累仅 11% (月度低换手 vs reversal 34.5%), 正期望+0.067 → R1+R2 重定向真金白银印证 (慢衰减→低换手→成本可活, 选吸筹非下跌刀). alpha 偏弱 (年化+2.53%<<30%, max_dd-31%) → 单信号不够, 待强化 (财务质量/筹码 / mf_trend 作 Regime 第四轴择时 / 多源分层). = Phase D 后续信号验证模板。**共享 harness `backend/services/phaseD_signal_eval.py::evaluate_signal`** (2026-06-15 同逻辑3次重构): 每信号只构建 signal_by_code(PIT)+fwd → harness 做 IC快筛/含成本backtest/裁决/留档; 复用为 Optuna 目标函数内层 backtest (R1 目标=含成本绝对收益非IC)。moneyflow 实验已重构调它 (回归一致 +2.53%)。组件: mf_trend TRADABLE+2.53% / winner_rate_trend(`experiment_chip_winner_rate_alpha.py`)NO_EDGE-20.6%。**第四轴 Regime/Timing 验证 (`experiment_phaseD_regime_timing.py` + harness `regime_ok` 门, owner=phaseD_regime_timing_20260615.json)**: 市场代理MA60趋势门(risk-off持现金) → mf_trend max_dd **-31.2%→-22.5%**(削8.7pp) + 年化 +2.53%→+3.12% + Calmar 0.08→0.14 = R1'在对的时候在场'实证, 第四轴动 binding 约束。**Optuna 搜参 (`experiment_phaseD_optuna_search.py` + `backend/config/experiments/phaseD_search_space.yaml`, owner=phaseD_optuna_regime_mf_20260615.json)**: optuna TPE 搜 mf_window×regime_ma×top_k×rebal×sizing (576组合), **R1 目标=含成本年化+max_dd突破惩罚 (非 IC)**; grill 门 enforce_phaseD_search_nonempty (防空烧) + train(≤2024)/holdout(2025) disjoint(N20) + DSR(n_trials)去偏; load-once+按window缓存信号→每trial~1s本地3分钟(Modal不需要)。smoke(4trials)holdout 已 +8.94%/max_dd-12.2%(<KPI) |
| `backend/services/optimization/` (deflated_sharpe/plan_validator/formula_param_search) + `backend/config/experiments/formula_search_spaces.yaml` | **L0 寻参治理层** (reset 后最小重建, 只 L0 RankIC 寻参所需非复活策略机器): deflated_sharpe(Bailey-LdP DSR 多重比较去过拟合, stdlib 替 scipy=erf+Acklam) + plan_validator(搜索空间非空闸, 防 29/34 白跑反例, 空→raise) + formula_param_search(网格穷举寻参, 目标 OOS RankIC, 只读 OOS, DSR deflate, 受 plan_validator 闸); search_spaces 小网格(每公式3-9组合=防过拟合)。moth `l0-search-governance-wired` 反孤儿守。寻参 RUN=task#17 (pre-reg+grill, 大计算 Modal) |
| `backend/config/experiments/retired_experiments.yaml` | **退役实验知识库** (实验模块 config 子目录): 模型/寻优层删全表时把"用了什么(字段族/年限/工具/结论)"留这替代留全表 (用户 2026-06-14: challenger 只留摘要不留全表); 参数寻优重做的历史参照; 14 子系统 (公式工厂/p0a-p0b/multidim/synergy/drift/paper_sim/stage-opt/horizon/market_perception/特征搜索/research_chains 等) |
| `backend/config/pipeline_performance_policy.yaml` | step budget 预算 |
| `backend/config/data_sources.yaml` | 数据源 |
| `backend/config/storage_retention.yaml` | 保留期 |
| `backend/config/pricing_label_policy.yaml` | 定价标签 |
| `backend/config/feature_registry.yaml` | 特征注册 |
| `backend/config/tdx_data_need_coverage.yaml` | TDX 数据需求/source priority/迁移建议 catalog，供 `audit_tdx_data_need_coverage.py` 物化到治理表 |

---

## 常用命令 cheatsheet (复制即可跑)

### 安装 (新人首次)
```bash
git clone https://github.com/dare2live/chunkymonkey.git
cd chunkymonkey
python3 -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
pip install pre-commit && pre-commit install   # 强制 PROJECT_INDEX 同步检查
```

### 数据 backfill / Optuna / paper_sim 运行手册

> **2026-06-14 地基-reset 移除**: 模型/特征/寻优/paper_sim 层 (build_signal_context /
> backfill_risk_factors / optimize_per_formula_stage / run_paper_sim_v2 等) 已删, 参数寻优从零重做。
> 数据获取 (raw/dim 同步) 走 `sync_runner` (sync_registry.yaml); 重建路线见 `goal.md` 重建路线 +
> `alpha_validation_program_spec_20260614.md(已删·重启清理)`。地基同步: `scripts/daily_update.sh` (手动)。

### 数据查询 (常用诊断)
```bash
# 查 mart 表最强 setup
duckdb data/smartmoney.duckdb -c "
SELECT formula_id, stage_filter, COUNT(*) AS n,
       ROUND(AVG(oos_sharpe),3) AS avg_sh,
       ROUND(AVG(oos_win_rate)*100,1) AS win
  FROM mart_per_formula_stage_optimal
 GROUP BY 1, 2 ORDER BY avg_sh DESC LIMIT 10"

# 查 PIT 数据 freshness
duckdb data/smartmoney.duckdb -c "
SELECT 'risk_factors' AS t, MIN(calc_date), MAX(calc_date), COUNT(*) FROM fact_risk_factors
UNION SELECT 'financial', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_financial_pit_daily
UNION SELECT 'capital_flow', MIN(trade_date), MAX(trade_date), COUNT(*) FROM fact_capital_flow_pit_daily
UNION SELECT 'signal_context', MIN(date), MAX(date), COUNT(*) FROM fact_signal_context"
```

### 测试 / 验证
```bash
# 全部单测 (paper_sim + optuna + backtest + ...)
cd backend && PYTHONPATH=. pytest tests/ -q

# 地基模块测试 (db/层级/同步)
cd backend && PYTHONPATH=. pytest tests/test_db.py tests/scripts/test_db_compact.py tests/test_source_watermarks.py -q
```

### Pre-commit 测试 (避免 hook reject)
```bash
# 改完代码后 staged
git add backend/services/your_file.py

# 测 hook (会告诉你需不需要改 PROJECT_INDEX)
python3 backend/scripts/check_project_index_sync.py; echo "exit=$?"

# 如果 exit=1 → 改 PROJECT_INDEX.md 加进 §14, 然后 git add PROJECT_INDEX.md
# 如果 exit=0 → 可以 commit
```

## 7. CLAUDE.md 规则栈 (现 9 条)

```
Rule 1: Think Before Coding         — 列假设, 不确定就问, push back
Rule 2: Simplicity First            — 最少代码, 不 speculative
Rule 3: Surgical Changes            — 只改必须改的
Rule 4: Goal-Driven Execution       — 定义成功, 循环验证
Rule 5: Root Cause Over Patches     — 不打补丁, 找根因
Rule 6: Measured, Not Estimated     — 不估算, 必须实测
Rule 7: Anti-Look-Ahead / Leakage   — 普适, 时间维度诚实
Rule 8: Optuna 治理                 — Rule 7 在调参层落地, config-driven
Rule 9: 真金白银 / 第一性原理       — 用户视角严苛门槛
```

---

## 8. 已知坑 / 未启用 / 需要修

| 项 | 状态 |
|---|---|
| **vendor rank 字段 = 分页伪 rank** | [陷阱-永久] `moneyflow_ind_dc.rank` 是每 50 行循环的分页序号 (三评委独立复现 vs 自算全量 rank spearman 仅 0.07-0.084)。**一切 vendor rank/序号类字段必须自算全量截面 rank**, 禁止直接当因子 (E9 纪律件, 2026-06-11) |
| `mart_sector_momentum` 只 41 行 (2026-04 起) | [BLOCKED] 没历史回测能力, **需 rebuild 全期** |
| `fact_setup_snapshot` 0 行 | [BLOCKED] 未启用 |
| **paper_sim 选股 走 strategy_ensemble** | [PASS] Phase ψ.β.4: ensemble mode + `paper_sim_ensemble.yaml` 10 alpha |
| **5 alpha 主源数据 PIT 时序** | [PASS] β.1 fact_risk_factors / β.2 fact_financial_pit_daily / β.3 fact_capital_flow_pit_daily backfill 完成 (跨 2023-01 → 2026-05) |
| **fact_institution_event 主 alpha** | ⚠ 只 1 年 (2025-04 起), 无法做 800 天 backfill — β.3 改用 lhb+exec+holder 替代 |
| **mart_stock_trend.action_score (机构跟随主 alpha)** | [BLOCKED] 仍是 latest 快照 — 未做 PIT 重建 (依赖 fact_institution_event 1 年限制) |
| **aif10 估值/一致预期** | [BLOCKED] 全 latest 快照, 无 PIT, β.2 改用 fact_financial_derived 替代 |
| **case-based / k-NN 历史相似回测** | [BLOCKED] 未建. 数据基础已有 (fact_signal_context + archetype) |
| **`fact_regime_state` 在 paper_sim** | [PASS] Phase ψ.β.4: ensemble selector regime_gate (bear 0.3x / sideways 0.7x / bull 1.0x) |
| sentiment/ 包未集成 | ⚠ 8 文件框架, 未对接 |
| 大盘指数 K 线 在 paper_sim 当 benchmark | [PASS] 已用作 excess vs HS300 |
| **fact_signal_context 早期数据缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2024-03 起, 66% valid_stage) |
| **fact_stock_technical_stage 早期缺** | [PASS] Phase ψ.β.4.5 backfill 完成 (2023-09-12 起, 2.4M 行) |
| **mart_per_formula_stage_optimal train_end 范围** | ⏳ 正在重跑 (1260 任务, 5 worker, 含 7 公式 × stage × 35 train_end) |
| **Optuna 跑批 8h 慢** | [PASS] Phase ψ.β.perf 修 hotspot: _idx O(1) cache + backtest_signals_with_trades 避免重跑 simulate_trade. 重跑预估 3-4h |
| `fact_stock_archetype` (基本面质量) 只 2026-04 几天 | ⚠ 未 backfill 历史 (待后续 audit) |
| `fact_financial_derived.revenue_yoy` 对部分股 (如 000001) null | ⚠ derived 表本身 sparse, 不影响其他股 |

---

## 9. 关键术语速查

| 术语 | 含义 |
|---|---|
| **IS** | In-Sample, 调参用的数据 |
| **OOS** | Out-of-Sample, 调参后**没看过**的数据上的表现 (实盘只能 OOS) |
| **R1** | 严格 walk-forward — 用户指定标准 |
| **expanding_monthly** | R1 严格模式: 每月底切, 累积 train + 当月 OOS |
| **train_end_forward** | Phase ψ.α B: train < d, test = [d, d+forward_days], 写多行支持 paper_sim point-in-time 选 |
| **leakage** (selection) | t 时选股用了 t+ 才能算的指标 (例 mart.sharpe 全期合并) |
| **leakage** (look-ahead) | 特征用了未来 K 线 |
| **CAGR** | (final/initial)^(252/n_days) - 1 — 复利年化 (不是单笔 × N) |
| **technical_stage** | 1=底部 / 1.5=突破中 / 2=上升 / 3=顶部 / 4=下跌 (Stan Weinstein) |
| **mart_** | 业务表 (报表 / 聚合) |
| **fact_** | 事实表 (实际发生) |
| **raw_** | 原始数据源 |
| **dim_** | 维度表 (静态 / 缓变) |

---

## 10. 已实测数据点 (Phase ψ.α 跑出的诚实 OOS)

### 反转因子 (B 严格 walk-forward, 34 个月窗 avg):

| formula × stage | avg OOS sharpe | avg win | avg single ret | max sharpe |
|---|---|---|---|---|
| reversal_1w × stage=3 | +0.393 | 50.4% | +3.94% | +1.255 |
| reversal_1m_deep × stage=3 | +0.393 | 51.6% | +5.49% | +0.898 |
| **reversal_1m_deep × stage=1** (底部深跌反转) | **+0.392** | **58.1%** | **+5.22%** | +0.905 |
| reversal_1m_deep × stage=4 | +0.356 | 46.2% | +4.77% | +0.889 |
| reversal_1m_mild × stage=1.5 | +0.342 | 51.9% | +4.49% | +1.372 |
| ... 9 行 ... | | | | |
| reversal_1w × stage=1 | -0.171 | 34.9% | +2.61% | +0.612 |

### Momentum 公式 (per-stock × stage R1, sparser):
全 12 组合 OOS sharpe 全负 (-0.02 ~ -0.63), avg win ≈ 39% — **per-stock 粒度不适合**, 应该改 per-formula 全局重测.

### Horizon Evidence (无 Optuna, 最干净, per formula × hp):
- reversal_1m_deep × 20d: win 61.8% / sharpe **+1.10** (但**这是合并跨全期, 不是 forward OOS**)

---

## 11. 我 (Claude) 容易踩的坑 (Rule 9.5 沉淀)

| 坑 | 教训 |
|---|---|
| "项目主要数据是 K 线" | **全错**. 6 大数据维度都有. 下结论前先 grep 所有 fact_/mart_/raw_ 表 |
| "momentum 公式失效 → 项目无 alpha" | 错. 项目还有机构跟随 (0.40 主 alpha) + 估值 + 一致预期 + 情绪 + 行业 + 大盘 regime |
| "MACD 是裸的" | 错. 跑 Optuna 时叠加 4 维 K 线形态过滤, 不是裸金叉 |
| "上升趋势 (stage=2) 反转完全无效" | 错. 是**粗糙公式**判 stage=2 回调失败, stage=2 回调本身是合理买点, 需要更精细 |
| "估算 2 min 跑完" → 实际 28 min | Rule 9.5: 不实测就估算 = 失败. 估时间也要小样本先测 |
| **paper_sim selector 用 mart_per_stock_*_optimal sharpe 排名** | 这是 selection leakage. 修正: walk-forward selector (Phase ψ.α B 已修, 但只对 reversal). 整体业务应走 ensemble |
| "对话压缩后还在用旧 context" | 修正: 每次启动**先读这个文档 + CLAUDE.md** |

---

## 11.5 已知遗漏 / 待办清单 (按 ROI 优先级)

> 这是用户反复 push back 后系统 audit 的结果. 每项含: 用户期望 / 现状 / 优先级 / 估时.
> Claude 应该在每个 phase 结束自动 review 这个列表, 不让任何一项静默 drop.

### P0 — 必修 (影响主目标达成)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 1 | **数据 sync 同步** | 数据更新到最新交易日 | `mart_data_source_watermark` 停在 2026-05-06, 其他 2026-05-13. 没主动跑 sync | 1 h |
| 2 | **goal.md 维护** | Phase ψ.β 系列进度记录在 goal.md | goal.md 没动过 Phase ψ.β 内容 | 1 h |
| 4 | **mart_sector_momentum 历史 backfill** | 板块强度可历史回测 | 只 41 行 (2026-04 起), 板块 alpha 不可用 | 半天 |
| 11 | **swap 策略最终评估** | 反转 setup 下 swap 是否需要? | swap_v1 跑 -44% 后中断, 反转下没验证 | paper_sim ablation 一部分 |

### P1 — 高 ROI (alpha 增强)

| # | 项 | 用户期望 | 现状 | 估时 |
|---|---|---|---|---|
| 5 | **mart_stock_trend.action_score PIT 重建** | 机构跟随主 alpha (0.40 权重) 历史可用 | β.3 改方向用 lhb/exec/holder 替代; 主 action_score 还是 latest 快照 | 3-5 天 (受 fact_institution_event 只 1 年限制) |
| 6 | **case-based / k-NN 历史相似回测** | "结合历史相似形态胜率" 选股 | 列为 R-γ, 未开工. 数据基础 fact_signal_context + archetype 已有 | 1-2 周 |
| 10 | **大盘 regime gate paper_sim 验证** | regime 择时是否生效? | yaml 配置加了但 paper_sim 还没验证 (反转 ablation 没用 ensemble mode) | paper_sim ablation 一部分 |

### P2 — 中 ROI (alpha 拓展)

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 3 | fact_stock_archetype 历史 backfill | 只 2026-04 几天 | 半天 |
| 7 | sentiment/ 关注度 alpha 集成 | 8 文件框架, 未对接 | 1 天 |
| 8 | 量价相关因子 (vol-price correlation) | 调研提过, 未建 | 半天 |
| 9 | fact_financial_derived.revenue_yoy sparse | 部分股 null (如 000001 银行) | 修 derived 表本身, 半天 |

### P3 — 工程 / 审计

| # | 项 | 现状 | 估时 |
|---|---|---|---|
| 12 | swap_uplift_estimate vs 反事实验证 | Phase ψ Batch 4c todo | 半天 |
| 13 | qfq 复权 PIT leakage | "业界接受不修", 但 Rule 9.1 严格说要处理 | 1-2 天 |
| 14 | 行业分类 PIT 系统验证 | 没核 SQL 用 history 还是 latest | 半天 |
| 15 | codex 分支整理 | 保留作 backup (用户原话), 不删 | 0 |
| 16 | dev 手册 / goal.md / PROJECT_INDEX 职责划分 | 没明文, 内容可能冗余 | 半天 |
| 17 | **283 历史 Rule violations 渐进清理** (Phase ψ.γ.discipline 扫出) | Rule 5 silent except 138 / Rule 7 date 112 / stock 22 / Rule 6 alpha weight 6 (strategy_ensemble.py) / threshold 3 / sigma 1 / multiplier 1. 多数 Rule 5 可能合理 (best-effort cleanup), Rule 6 6 个是 strategy_ensemble.py 真违规需要 yaml-back. | 1-2 天 (按 rule 分批清理 + 误判加 evidence 注释) |

### 处理原则

- 每跑完一个 phase / commit 后, **检查这个列表是否有项可以划掉**
- 新踩坑 / 新 audit 发现的项加进来
- 不静默 drop — 即使 "暂不修" 也要写明理由
- P0 不修, 用户目标基本不可能达成

## Performance Profile (跑批时间预期)

| 任务 | 数据量 | 实测时长 | 备注 |
|---|---|---|---|
| build_signal_context backfill | 3.3M K 线 → 2.7M context | **5.7 min** | calc 1 min + 写库 4.7 min |
| build_stage_formula_fitness (含 technical_stage) | 5.2M K 线 → 2.4M stage | **4 min** | classify 22s + 写库 3.5 min |
| backfill_risk_factors_history | 5.5M K 线 → 4.8M risk PIT | **12 min** | SQL 窗口 8.6s + 写库 11.5 min |
| backfill_financial_pit | 70K 财报 + K 线 → 3.7M PIT | **10 min** | ASOF JOIN 4s + 写库 10 min |
| backfill_capital_flow_pit | 53K lhb + 68K exec + holder → 858K | **2.4 min** | SQL 3s + 写库 2 min |
| optimize_per_formula_stage (反转 3 公式) | 455 任务 × 100 trials | **28 min** | 8 workers |
| **optimize_per_formula_stage (全 7 公式)** | **1260 任务 × 100 trials** | **7.5 h** ⚠ | 后期 5 worker tail (用户问"卡了吗") |
| paper_sim_v2 walk-forward 单 variant 800 天 | 4-5K 候选 / 天 | 30 min | swap_v1 含 |
| paper_sim_v2 ablation (baseline + swap_v1) | 2 variants × 800 天 | 60 min | |

### 已修 hotspot (Phase ψ.β.perf, commit 192bcb4d)

| Hotspot | 修法 | 预期加速 |
|---|---|---|
| `realistic_engine._idx` linear search | 加 `_BAR_DATE_IDX_CACHE` dict cache | **2-5×** |
| `objective.py` + `optimize.py` 重跑 simulate_trade | 新增 `backtest_signals_with_trades` 返回 (summary, trades) | **1.5-2×** |
| `objective.py` 自己做 linear search | 改用 `_idx` (含 dict cache) | **1.2-1.5×** |

**预期重跑 1260 任务 Optuna 从 7.5h 降到 ~3h**.

### 已知尚未优化

| 项 | 影响 |
|---|---|
| `dynamic_ma_iterative` 公式原始 10 轮迭代 / 当前默认 2 轮 Python loop | 慢公式之一, 可 numpy 向量化 → 3-5× |
| backfill 写库阶段 (单事务 INSERT) | 平均 150 us/row, 4.8M 行 11 min. COPY FROM Parquet 可 5-10× |
| Optuna pool tail effect (5 worker idle / 2 worker 慢任务) | 改 chunksize 或调度策略, 拉平 worker 负载 |

## 12. 当前 Phase / 进度

| Phase | 内容 | 状态 |
|---|---|---|
| Phase β-η+++++++ | 前期工作 (公式 / Optuna / fitness / sizer / etc.) | 大量已完成, 见 goal.md |
| **Phase ψ** | Optuna 治理 + R1 + Rule 7/8 + paper_sim VWAP 修正 | [PASS] commit `34e83d75` (main + codex) |
| **Phase ψ.α** | 反转因子 + per-formula 全局 + B 严格 walk-forward + Rule 9 + PROJECT_INDEX | [PASS] commit `545cb3d9` (feature/reversal-factor) |
| **Phase ψ.β.1** | fact_risk_factors PIT backfill (4.8M 行 / 6,567 股 / 810 天) | [PASS] commit `5a3b5ea8` |
| **Phase ψ.β.2** | fact_financial_pit_daily PIT (3.69M 行) — PE/PB/ROE/yoy/inst_holding_pct | [PASS] commit `baf815b6` (β.2+β.3) |
| **Phase ψ.β.3** | fact_capital_flow_pit_daily (858K 行) — lhb/exec/holder PIT | [PASS] commit `baf815b6` |
| **Phase ψ.β.4** | paper_sim ensemble selector + 10 alpha yaml + regime_gate | [PASS] commit `1af98eca` |
| **Phase ψ.β.4.5** | backfill fact_stock_technical_stage + fact_signal_context 历史 | [PASS] 数据已落, 待 commit |
| **Phase ψ.β.4.6** | ensemble quality_filter (vol_60d / allowed_stages) | [PASS] commit `192bcb4d` |
| **Phase ψ.β.perf** | hotspot fix: _idx O(1) cache + backtest_signals_with_trades | [PASS] commit `192bcb4d`, 161 测过 |
| **Phase ψ.β.5** (in-progress) | optimize_per_formula 重跑 7 公式 × 35 train_end = 1260 任务 | ⏳ 5 worker 67% CPU, 1000/1260 |
| **Phase ψ.β.6** (next) | paper_sim ablation 完整 800 天 (reversal / momentum / ensemble) | ⏸ 等 ψ.β.5 |
| **Phase ψ.β.7** (next) | audit + 修 残留漏洞 (mart_stock_trend PIT / sector_momentum 全期 / case-based 等) | ⏸ |

git 状态 (commit chain):
```
main:                       34e83d75  (Phase ψ Optuna 治理)
feature/reversal-factor:    192bcb4d  (head, 含 β.1-β.4.6 + perf, 6 commits ahead)
  ← 192bcb4d  Phase ψ.β.perf
  ← 1af98eca  Phase ψ.β.4 ensemble selector
  ← baf815b6  Phase ψ.β.2+β.3 financial + capital_flow PIT
  ← 5a3b5ea8  Phase ψ.β.1 risk_factors PIT
  ← 545cb3d9  Phase ψ.α reversal + Rule 9 + PROJECT_INDEX
  ← 34e83d75  Phase ψ
```

worktree 残留: `/Users/dp/.codex/worktrees/a980/stock` 链接到外部 `/Users/dp/Documents/M/stock/.git`, 不归本项目处理.

---

## 13. 写本文档的源数据 (供刷新)

```sql
-- 项目自己维护的架构 inventory (smartmoney.duckdb)
SELECT * FROM mart_architecture_inventory_summary ORDER BY built_at DESC LIMIT 1;
SELECT * FROM mart_architecture_inventory_asset WHERE run_id = ?;
SELECT * FROM mart_data_health;
SELECT * FROM mart_data_source_watermark;
```

---

## 14. Session 增量更新日志 (已归档)

> 246 条历史增量已移至 `analysis/project_index_changelog_archive_20260611.md` (2026-06-11 文档治理)。
> 新增历史叙事写 `analysis/project_state_ledger.md`; 本文件只维护上方活索引与最近 7 天增量。
